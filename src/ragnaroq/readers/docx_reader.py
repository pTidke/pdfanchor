"""Word (.docx). One body page. Every body paragraph takes the next number, empty ones and
paragraphs holding only a picture included, so a number never moves when text is added elsewhere.
Tracked changes are read as accepted, comments are left out, and each is flagged."""

import io

from ..anchors import DocxAnchor, cite
from ..config import Config
from ..model import Cell, Figure, Flag, Table
from . import READER_VERSIONS, ReaderRefused, ReaderUnavailable, ReadResult, _build, _zip

_W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
_A = "{http://schemas.openxmlformats.org/drawingml/2006/main}"
_WP = "{http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing}"
_C = "{http://schemas.openxmlformats.org/drawingml/2006/chart}"
_R = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
_RUN_PARTS = (_W + "t", _W + "tab", _W + "br", _W + "cr")
_EMU_PER_PIXEL = 9525


def _deleted(node) -> bool:
    return any(ancestor.tag == _W + "del" for ancestor in node.iterancestors())


def _text(paragraph) -> str:
    """Every run's text, as if each tracked change were accepted: `w:delText` is not `w:t`."""
    parts = []
    for node in paragraph.iter(*_RUN_PARTS):
        if node.getparent().tag == _W + "r" and not _deleted(node):
            parts.append((node.text or "") if node.tag == _W + "t" else " ")
    return "".join(parts).strip()


def _body_items(parent):
    """Paragraphs and tables in order, looking inside content controls."""
    for child in parent:
        if child.tag in (_W + "p", _W + "tbl"):
            yield child
        elif child.tag == _W + "sdt":
            content = child.find(_W + "sdtContent")
            if content is not None:
                yield from _body_items(content)


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

    def pictures(self, paragraph, anchor, caption: str | None):
        """One figure per picture in the paragraph; charts are flagged and not read."""
        for drawing in paragraph.iter(_W + "drawing"):
            if drawing.find(f".//{_A}graphicData/{_C}chart") is not None:
                self.page.flags.append(
                    Flag(
                        rule="chart.not-read",
                        severity="review",
                        message=f"A chart at {cite(anchor)} is not read; its numbers are not in the text.",
                    )
                )
                continue
            blip = drawing.find(f".//{_A}blip")
            embed = blip.get(_R + "embed") if blip is not None else None
            if embed is None or embed not in self.document.part.related_parts:
                continue
            props = drawing.find(f".//{_WP}docPr")
            alt = ((props.get("descr") or props.get("title")) if props is not None else None) or ""
            extent = drawing.find(f".//{_WP}extent")
            figure = Figure(
                id=f"p1-f{len(self.page.figures) + 1}",
                page=1,
                order=len(self.page.lines),
                anchor=anchor,
                width=int(extent.get("cx", 0)) // _EMU_PER_PIXEL if extent is not None else 0,
                height=int(extent.get("cy", 0)) // _EMU_PER_PIXEL if extent is not None else 0,
                description=caption or alt,
                description_source="caption" if caption else ("alt" if alt else "none"),
            )
            self.images[figure.id] = self.document.part.related_parts[embed].blob
            self.page.figures.append(figure)

    def table(self, element, number: int):
        table_id = f"p1-t{number}"
        cells, columns, rows = [], 0, 0
        for r, row in enumerate(element.findall(_W + "tr"), 1):
            rows = r
            column = 0
            for cell in row.findall(_W + "tc"):
                span = cell.find(f"{_W}tcPr/{_W}gridSpan")
                start = column + 1
                column += int(span.get(_W + "val", 1)) if span is not None else 1
                columns = max(columns, column)
                if self.full():
                    continue
                paragraphs = cell.findall(_W + "p")
                text = "\n".join(t for t in (_text(para) for para in paragraphs) if t)
                anchor = DocxAnchor(table=number, cell=(r, start))
                for para in paragraphs:
                    self.pictures(para, anchor, None)
                if text:
                    _build.add_line(self.page, text, "docx", anchor, role="table", table_id=table_id)
                    cells.append(Cell(row=r - 1, column=start - 1, text=text, anchor=anchor))
        if cells:
            self.page.tables.append(Table(id=table_id, page=1, rows=rows, columns=columns, cells=cells))

    def body(self):
        paragraph_number = table_number = 0
        items = list(_body_items(self.document.element.body))
        for index, item in enumerate(items):
            if item.tag == _W + "tbl":
                table_number += 1
                self.table(item, table_number)
                continue
            paragraph_number += 1
            anchor = DocxAnchor(paragraph=paragraph_number)
            caption = None
            following = items[index + 1] if index + 1 < len(items) else None
            if following is not None and following.tag == _W + "p":
                if self.style_of(following).lower().startswith("caption"):
                    caption = _text(following) or None
            self.pictures(item, anchor, caption)
            text = _text(item)
            if text and not self.full():
                role = "heading" if _is_heading(self.style_of(item)) else "body"
                _build.add_line(self.page, text, "docx", anchor, role=role)

    def footnotes(self):
        part = next(
            (
                rel.target_part
                for rel in self.document.part.rels.values()
                if rel.reltype.endswith("/footnotes")
            ),
            None,
        )
        if part is None:
            return
        from lxml import etree

        root = etree.fromstring(part.blob)
        notes = {
            note.get(_W + "id"): note
            for note in root.findall(_W + "footnote")
            if note.get(_W + "type") in (None, "normal")
        }
        # Word numbers a note by where it is referenced; a note nothing refers to follows the rest.
        order, seen = [], set()
        for reference in self.document.element.body.iter(_W + "footnoteReference"):
            note_id = reference.get(_W + "id")
            if note_id in notes and note_id not in seen:
                seen.add(note_id)
                order.append(note_id)
        order += [note_id for note_id in notes if note_id not in seen]
        for number, note_id in enumerate(order, 1):
            text = " ".join(t for t in (_text(p) for p in notes[note_id].findall(_W + "p")) if t)
            if text and not self.full():
                _build.add_line(self.page, text, "docx", DocxAnchor(note=f"footnote {number}"))


def _flags(document) -> list[Flag]:
    body = document.element.body
    found = []
    if body.find(f".//{_W}ins") is not None or body.find(f".//{_W}del") is not None:
        found.append(
            Flag(
                rule="docx.tracked-changes",
                severity="review",
                message="The file has tracked changes; they are read as accepted.",
            )
        )
    has_part = any(rel.reltype.endswith("/comments") for rel in document.part.rels.values())
    if has_part or body.find(f".//{_W}commentReference") is not None:
        found.append(
            Flag(rule="docx.comments", severity="review", message="The file has comments; they are not read.")
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
        reader.footnotes()
        document.flags.extend(_flags(source))
    except Exception as error:  # python-docx and lxml raise many kinds for a broken file
        raise ReaderRefused("docx.invalid", f"{name} could not be read as a document: {error}") from error
    return ReadResult(document, reader.images)
