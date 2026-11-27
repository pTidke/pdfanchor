"""Word (.docx). One body page. Every body paragraph takes the next number, empty ones and
paragraphs holding only a picture included, so a number never moves when text is added elsewhere.
Tracked changes are read as accepted, comments, headers and footers are left out, and each is flagged."""

import io
import re

from ..anchors import DocxAnchor, cite
from ..config import Config
from ..model import Cell, Figure, Flag, Table
from . import READER_VERSIONS, ReaderRefused, ReaderUnavailable, ReadResult, _build, _zip

_W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
_A = "{http://schemas.openxmlformats.org/drawingml/2006/main}"
_WP = "{http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing}"
_C = "{http://schemas.openxmlformats.org/drawingml/2006/chart}"
_R = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
_MC = "{http://schemas.openxmlformats.org/markup-compatibility/2006}"
_M = "{http://schemas.openxmlformats.org/officeDocument/2006/math}"
_V = "{urn:schemas-microsoft-com:vml}"
_O = "{urn:schemas-microsoft-com:office:office}"
_RUN_PARTS = (_W + "t", _W + "tab", _W + "br", _W + "cr")
# Text under these is not the paragraph's own: deleted or moved away, a text box, or a fallback copy.
_NOT_OWN = (_W + "del", _W + "moveFrom", _W + "txbxContent", _MC + "Fallback")
_CHANGES = (_W + "ins", _W + "del", _W + "moveFrom", _W + "moveTo")
_EMU_PER_PIXEL = 9525
# Pictures: DrawingML, VML, and an embedded object (whose own picture is its preview).
_PICTURES = (_W + "drawing", _W + "pict", _W + "object")
# Pictures under these are read elsewhere: with a text box's paragraphs, never as a fallback copy.
_ELSEWHERE = (_W + "txbxContent", _MC + "Fallback")
_LENGTH = re.compile(r"(width|height)\s*:\s*([\d.]+)\s*(pt|px|in|cm|mm)?", re.I)
_PIXELS = {"pt": 96 / 72, "px": 1, "in": 96, "cm": 96 / 2.54, "mm": 96 / 25.4}


def _vml_size(style: str) -> tuple[int, int]:
    """A VML shape's width and height in pixels, from its style; 0 when not given."""
    size = {"width": 0, "height": 0}
    for name, number, unit in _LENGTH.findall(style or ""):
        try:
            size[name.lower()] = int(float(number) * _PIXELS[(unit or "px").lower()])
        except ValueError:
            pass
    return size["width"], size["height"]


def _inside(node, root, tags) -> bool:
    """Whether `node` sits under one of `tags` between it and `root`."""
    for ancestor in node.iterancestors():
        if ancestor is root:
            return False
        if ancestor.tag in tags:
            return True
    return False


def _math(paragraph):
    """The paragraph's own equation symbols, `m:t` in document order."""
    return [
        node
        for node in paragraph.iter(_M + "t")
        if node.getparent().tag == _M + "r"
        and (node.text or "").strip()
        and not _inside(node, paragraph, _NOT_OWN)
    ]


def _text(paragraph) -> str:
    """The paragraph's own text, as if each tracked change were accepted: `w:delText` is not
    `w:t`, and moved-away text and text boxes are not read here. An equation's symbols are
    read in place, each followed by a space."""
    parts = []
    for node in paragraph.iter(*_RUN_PARTS, _M + "t"):
        if node.tag == _M + "t":
            if node.getparent().tag == _M + "r" and not _inside(node, paragraph, _NOT_OWN):
                parts.append((node.text or "").strip() + " ")
        elif node.getparent().tag == _W + "r" and not _inside(node, paragraph, _NOT_OWN):
            parts.append((node.text or "") if node.tag == _W + "t" else " ")
    return "".join(parts).strip()


def _boxes(paragraph):
    """The paragraph's text boxes, outermost only; a fallback copy is never one."""
    for box in paragraph.iter(_W + "txbxContent"):
        if not _inside(box, paragraph, (_W + "txbxContent", _MC + "Fallback")):
            yield box


def _unwrap(parent):
    """Children in order, with content controls and custom XML replaced by what they hold."""
    for child in parent:
        if child.tag == _W + "sdt":
            content = child.find(_W + "sdtContent")
            if content is not None:
                yield from _unwrap(content)
        elif child.tag == _W + "customXml":
            yield from _unwrap(child)
        else:
            yield child


def _children(parent, tag):
    return (child for child in _unwrap(parent) if child.tag == _W + tag)


def _blocks(parent):
    return (child for child in _unwrap(parent) if child.tag in (_W + "p", _W + "tbl"))


def _style_names(document) -> dict[str, str]:
    names = {}
    for style in document.styles.element.findall(_W + "style"):
        name = style.find(_W + "name")
        if name is not None:
            names[style.get(_W + "styleId")] = name.get(_W + "val") or ""
    return names


def _is_heading(style: str) -> bool:
    lowered = style.lower()
    return lowered == "title" or lowered.startswith("heading")


class _Reader:
    def __init__(self, document, config: Config, page):
        self.document = document
        self.config = config
        self.page = page
        self.names = _style_names(document)
        self.images: dict[str, bytes] = {}
        self.limited = False
        self.part = document.part  # whose relationships name the pictures being read
        self.roots = [document.element.body]  # where tracked changes can be

    def style_of(self, paragraph) -> str:
        style = paragraph.find(f"{_W}pPr/{_W}pStyle")
        return self.names.get(style.get(_W + "val"), "") if style is not None else ""

    def full(self) -> bool:
        if len(self.page.lines) < self.config.max_cells:
            return False
        if not self.limited:
            self.limited = True
            self.page.flags.append(
                Flag(
                    rule="docx.line-limit",
                    severity="review",
                    message=f"More than {self.config.max_cells} lines; the rest is not read.",
                )
            )
        return True

    def flag(self, rule: str, message: str) -> None:
        self.page.flags.append(Flag(rule=rule, severity="review", message=message))

    def pictures(self, paragraph, anchor, caption: str | None):
        """One figure per picture in the paragraph, in order: DrawingML, VML, and the preview of
        an embedded object, which is also flagged. Charts are flagged and not read."""
        for node in paragraph.iter(*_PICTURES):
            if _inside(node, paragraph, (*_ELSEWHERE, _W + "object")):
                continue  # a text box's pictures come with its paragraphs, an object's with it
            if node.tag == _W + "object":
                self.flag(
                    "docx.object-not-read",
                    f"An embedded object at {cite(anchor)} is not read; only its preview picture is kept.",
                )
                for inner in node.iter(_W + "drawing", _V + "imagedata"):
                    if _inside(inner, node, _ELSEWHERE):
                        continue
                    if inner.tag == _W + "drawing":
                        self.drawing(inner, anchor, caption)
                    else:
                        self.vml(inner, anchor, caption)
            elif node.tag == _W + "drawing":
                self.drawing(node, anchor, caption)
            else:
                for data in node.iter(_V + "imagedata"):
                    if not _inside(data, node, _ELSEWHERE):
                        self.vml(data, anchor, caption)

    def drawing(self, drawing, anchor, caption):
        if drawing.find(f".//{_A}graphicData/{_C}chart") is not None:
            where = cite(anchor)
            self.flag("chart.not-read", f"A chart at {where} is not read; its numbers are not in the text.")
            return
        blip = drawing.find(f".//{_A}blip")
        if blip is None:
            return  # a shape: its text, if any, is read from its text box
        props = drawing.find(f".//{_WP}docPr")
        alt = _build.alt_text(props.get("descr"), props.get("title")) if props is not None else ""
        extent = drawing.find(f".//{_WP}extent")
        width = int(extent.get("cx", 0)) // _EMU_PER_PIXEL if extent is not None else 0
        height = int(extent.get("cy", 0)) // _EMU_PER_PIXEL if extent is not None else 0
        self.figure(anchor, caption, alt, blip.get(_R + "embed"), blip.get(_R + "link"), (width, height))

    def vml(self, data, anchor, caption):
        """A VML picture: `v:imagedata` names the image; the shape around it has the alt text."""
        shape = data.getparent()
        alt = _build.alt_text(shape.get("alt"), shape.get(_O + "title"), data.get(_O + "title"))
        embed = data.get(_R + "id") or data.get(_O + "relid")
        self.figure(anchor, caption, alt, embed, None, _vml_size(shape.get("style")))

    def figure(self, anchor, caption, alt, embed, link, size):
        """A figure, with its image when the file holds it; a linked picture, or one whose id names
        no image in the file, is a figure with no image, flagged."""
        relationship = self.part.rels.get(embed) if embed else None
        stored = relationship is not None and not relationship.is_external
        figure = Figure(
            id=f"p1-f{len(self.page.figures) + 1}",
            page=1,
            order=len(self.page.lines),
            anchor=anchor,
            width=size[0],
            height=size[1],
            description=caption or alt,
            description_source="caption" if caption else ("alt" if alt else "none"),
        )
        if stored:
            self.images[figure.id] = relationship.target_part.blob
        else:
            linked = link is not None or relationship is not None
            why = "is linked, not stored in the file; it was not fetched"
            message = f"The picture at {cite(anchor)} {why if linked else 'has no image stored in the file'}."
            figure.flags.append(Flag(rule="image.linked-not-fetched", severity="review", message=message))
        self.page.figures.append(figure)

    def line(self, text, anchor, role="body", table_id=None, equation=False):
        if text and not self.full():
            line = _build.add_line(self.page, text, "docx", anchor, role=role, table_id=table_id)
            if equation:
                line.flags.append(
                    Flag(
                        rule="docx.equation",
                        severity="review",
                        message=(
                            "An equation is read as its plain symbols; fractions, powers and layout "
                            "are lost; compare with the page."
                        ),
                    )
                )

    def paragraph(self, element, anchor, role="body", table_id=None, caption=None, own_line=True):
        """The paragraph's pictures and text, then each text box's paragraphs as lines of their own."""
        self.pictures(element, anchor, caption)
        if own_line:
            self.line(_text(element), anchor, role, table_id, equation=bool(_math(element)))
        for box in _boxes(element):
            self.blocks(_blocks(box), anchor, "table" if table_id else "body", table_id)

    def blocks(self, items, anchor, role="body", table_id=None):
        """Paragraphs and nested tables, all as lines of one anchor."""
        for item in items:
            if item.tag == _W + "tbl":
                for row in _children(item, "tr"):
                    for cell in _children(row, "tc"):
                        self.blocks(_blocks(cell), anchor, role, table_id)
            else:
                self.paragraph(item, anchor, role, table_id)

    def table(self, element, number: int):
        table_id = f"p1-t{number}"
        cells, columns, rows = [], 0, 0
        for r, row in enumerate(_children(element, "tr"), 1):
            rows = r
            column = 0
            for cell in _children(row, "tc"):
                span = cell.find(f"{_W}tcPr/{_W}gridSpan")
                start = column + 1
                column += min(max(int(span.get(_W + "val", 1)), 1), 1000) if span is not None else 1
                columns = max(columns, column)
                if self.full():
                    continue
                items = list(_blocks(cell))
                own = [item for item in items if item.tag == _W + "p"]
                text = "\n".join(t for t in (_text(para) for para in own) if t)
                anchor = DocxAnchor(table=number, cell=(r, start))
                first = len(self.page.lines)
                if text:
                    equation = any(_math(para) for para in own)
                    self.line(text, anchor, "table", table_id, equation=equation)
                for item in items:
                    if item.tag == _W + "tbl":
                        self.blocks([item], anchor, "table", table_id)
                    else:
                        self.paragraph(item, anchor, "table", table_id, own_line=False)
                # The cell holds every line anchored to it, in order: its own paragraphs, then its
                # nested tables and text boxes. A cell with only a nested table has a cell too.
                held = [line.text for line in self.page.lines[first:]]
                if held:
                    cells.append(Cell(row=r - 1, column=start - 1, text="\n".join(held), anchor=anchor))
        if cells:
            self.page.tables.append(Table(id=table_id, page=1, rows=rows, columns=columns, cells=cells))

    def body(self):
        paragraph_number = table_number = 0
        items = list(_blocks(self.document.element.body))
        for index, item in enumerate(items):
            if item.tag == _W + "tbl":
                table_number += 1
                self.table(item, table_number)
                continue
            paragraph_number += 1
            caption = None
            following = items[index + 1] if index + 1 < len(items) else None
            if following is not None and following.tag == _W + "p":
                if self.style_of(following).lower().startswith("caption"):
                    caption = _text(following) or None
            role = "heading" if _is_heading(self.style_of(item)) else "body"
            self.paragraph(item, DocxAnchor(paragraph=paragraph_number), role, caption=caption)

    def notes(self, kind: str):
        """Footnotes or endnotes, numbered from 1 by where the body refers to them."""
        part = next(
            (
                rel.target_part
                for rel in self.document.part.rels.values()
                if rel.reltype.endswith(f"/{kind}s")
            ),
            None,
        )
        if part is None:
            return
        from lxml import etree

        root = etree.fromstring(part.blob)
        self.roots.append(root)
        self.part = part  # a note's pictures are named by the notes part
        notes = {
            note.get(_W + "id"): note
            for note in root.findall(_W + kind)
            if note.get(_W + "type") in (None, "normal")
        }
        # A note nothing refers to follows the rest.
        order, seen = [], set()
        for reference in self.document.element.body.iter(_W + kind + "Reference"):
            note_id = reference.get(_W + "id")
            if note_id in notes and note_id not in seen:
                seen.add(note_id)
                order.append(note_id)
        order += [note_id for note_id in notes if note_id not in seen]
        for number, note_id in enumerate(order, 1):
            anchor = DocxAnchor(note=f"{kind} {number}")
            items = list(_blocks(notes[note_id]))
            own = [item for item in items if item.tag == _W + "p"]
            text = " ".join(t for t in (_text(para) for para in own) if t)
            self.line(text, anchor, equation=any(_math(para) for para in own))
            for item in items:
                if item.tag == _W + "tbl":
                    self.blocks([item], anchor)
                else:
                    self.paragraph(item, anchor, own_line=False)

    def has_header_or_footer_text(self) -> bool:
        from lxml import etree

        for rel in self.document.part.rels.values():
            if rel.reltype.endswith(("/header", "/footer")):
                root = etree.fromstring(rel.target_part.blob)
                if any((t.text or "").strip() for t in root.iter(_W + "t")):
                    return True
        return False


def _flags(reader: _Reader) -> list[Flag]:
    document = reader.document
    found = []
    if any(next(root.iter(*_CHANGES), None) is not None for root in reader.roots):
        found.append(
            Flag(
                rule="docx.tracked-changes",
                severity="review",
                message="The file has tracked changes; they are read as accepted.",
            )
        )
    has_part = any(rel.reltype.endswith("/comments") for rel in document.part.rels.values())
    if has_part or document.element.body.find(f".//{_W}commentReference") is not None:
        found.append(
            Flag(rule="docx.comments", severity="review", message="The file has comments; they are not read.")
        )
    if reader.has_header_or_footer_text():
        found.append(
            Flag(
                rule="docx.headers-footers",
                severity="review",
                message="The file has headers or footers; they are not read.",
            )
        )
    return found


def read(data: bytes, name: str, config: Config) -> ReadResult:
    try:
        _zip.check(data, config)
    except ReaderRefused as refusal:
        if refusal.rule != "file.zip-invalid":
            raise
        raise ReaderRefused(
            "docx.invalid", f"{name} could not be opened as a document: {refusal.message}"
        ) from None
    try:
        import docx
    except ModuleNotFoundError as error:
        raise ReaderUnavailable(
            "The docx reader needs python-docx: pip install 'ragnaroq[formats]'"
        ) from error
    document = _build.start(data, name, "docx", config, {"reader:docx": READER_VERSIONS["docx"]})
    try:
        source = docx.Document(io.BytesIO(data))
        reader = _Reader(source, config, document.pages[0])
        reader.body()
        reader.notes("footnote")
        reader.notes("endnote")
        document.flags.extend(_flags(reader))
    except Exception as error:  # python-docx and lxml raise many kinds for a broken file
        raise ReaderRefused("docx.invalid", f"{name} could not be read as a document: {error}") from error
    return ReadResult(document, reader.images)
