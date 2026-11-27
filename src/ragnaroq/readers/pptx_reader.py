"""PowerPoint (.pptx). One page per slide, numbered from 1. Shapes are read in the order of the
slide's shape tree, groups and `mc:AlternateContent` choices included; each text paragraph is a
line. Notes are read, and footers, comments, layouts and masters are left out. Charts, SmartArt and
other embedded objects are not read, and each is flagged."""

import io
import zipfile

from ..anchors import PptxAnchor, cite
from ..config import Config
from ..model import Cell, Figure, Flag, Page, Table
from . import READER_VERSIONS, ReaderRefused, ReaderUnavailable, ReadResult, _build, _zip

_M = "{http://schemas.openxmlformats.org/officeDocument/2006/math}"
_A = "{http://schemas.openxmlformats.org/drawingml/2006/main}"
_P = "{http://schemas.openxmlformats.org/presentationml/2006/main}"
_R = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
_MC = "{http://schemas.openxmlformats.org/markup-compatibility/2006}"
_GRAPHIC_DATA = _A + "graphicData"
_TITLES = ("title", "ctrTitle")
_FOOTERS = ("ftr", "dt", "sldNum")
_EMU_PER_PIXEL = 9525
_LABELS = {"chart": "chart", "diagram": "SmartArt graphic"}
_NOT_READ = {
    "chart": ("chart.not-read", "its numbers are not in the text"),
    "diagram": ("pptx.smartart", "its text is not read"),
}


class _Text(str):
    """A paragraph's text; `equation` says whether some of it came from an equation."""

    equation = False


def _paragraph_text(paragraph) -> _Text:
    """The paragraph's text; a line break or a tab element is a space. An equation is read in
    place from the `m:t` symbols of the first `mc:Choice`, never the fallback copy."""
    parts, equation = [], False
    for child in paragraph:
        if child.tag in (_A + "r", _A + "fld"):
            parts.extend(t.text or "" for t in child.findall(_A + "t"))
        elif child.tag in (_A + "br", _A + "tab"):
            parts.append(" ")
        elif child.tag == _MC + "AlternateContent":
            choice = child.find(_MC + "Choice")
            symbols = (
                [t.text.strip() for t in choice.iter(_M + "t") if (t.text or "").strip()]
                if (choice is not None)
                else []
            )
            if symbols:
                parts.append(" ".join(symbols))
                equation = True
    text = _Text("".join(parts).strip())
    text.equation = equation
    return text


def _paragraphs(body) -> list[_Text]:
    """Every paragraph of a text body, empty ones included, so an index is the paragraph's number."""
    return [_paragraph_text(p) for p in body.findall(_A + "p")] if body is not None else []


def _number(value) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _props(element):
    """The shape's `p:cNvPr` and `p:nvPr`, in whichever `p:nv…Pr` block its kind has."""
    for child in element:
        if child.tag.startswith(_P + "nv") and child.tag.endswith("Pr"):
            return child.find(_P + "cNvPr"), child.find(_P + "nvPr")
    return None, None


def _name(element) -> str:
    props, _ = _props(element)
    return (props.get("name") or "") if props is not None else ""


def _placeholder(element) -> str | None:
    """The placeholder's type; one with no type is an ordinary body placeholder."""
    _, nv = _props(element)
    holder = nv.find(_P + "ph") if nv is not None else None
    return None if holder is None else (holder.get("type") or "obj")


class _Reader:
    def __init__(self, config: Config, document):
        self.config = config
        self.document = document
        self.images: dict[str, bytes] = {}
        self.count = 0  # lines over the whole deck
        self.limited = False
        self.footers = False

    # -- the cap ----------------------------------------------------------------------------------

    def full(self) -> bool:
        if self.count < self.config.max_cells:
            return False
        if not self.limited:
            self.limited = True
            self.page.flags.append(
                Flag(
                    rule="pptx.line-limit",
                    severity="review",
                    message=f"More than {self.config.max_cells} lines; the rest is not read.",
                )
            )
        return True

    def line(self, text, anchor, role="body", table_id=None, equation=False, level=0):
        if text and not self.full():
            self.count += 1
            line = _build.add_line(self.page, text, "pptx", anchor, role=role, table_id=table_id, level=level)
            if equation:
                line.flags.append(
                    Flag(
                        rule="pptx.equation",
                        severity="review",
                        message=(
                            "An equation is read as its plain symbols; fractions, powers and layout "
                            "are lost; compare with the slide."
                        ),
                    )
                )

    def flag(self, rule, message):
        self.page.flags.append(Flag(rule=rule, severity="review", message=message))

    # -- shapes -----------------------------------------------------------------------------------

    def shapes(self, parent):
        """Each shape under `parent` in tree order. A group is entered; of an AlternateContent
        only the first `mc:Choice` is, and the fallback copy never is."""
        for child in parent:
            tag = child.tag
            if tag == _P + "grpSp":
                self.image_fill(child)
                self.shapes(child)
            elif tag == _MC + "AlternateContent":
                choice = child.find(_MC + "Choice")
                if choice is not None:
                    self.shapes(choice)
                else:
                    self.flag(
                        "pptx.object-not-read",
                        f"Content at slide {self.number} has no usable choice; it is not read.",
                    )
            elif tag == _P + "sp":
                self.image_fill(child)
                self.text_shape(child)
            elif tag == _P + "pic":
                self.image_fill(child)
                self.picture(child)
            elif tag == _P + "cxnSp":
                self.image_fill(child)
            elif tag == _P + "graphicFrame":
                self.graphic_frame(child)
            elif tag == _P + "contentPart":
                self.flag("pptx.object-not-read", f"An object at slide {self.number} is not read.")

    def image_fill(self, element):
        """A picture used as a shape's fill is not a figure; say that it is not read."""
        properties = element.find(_P + "spPr")
        if properties is None:
            properties = element.find(_P + "grpSpPr")
        if properties is not None and properties.find(_A + "blipFill") is not None:
            self.flag("pptx.image-fill", "An image fill is not read.")

    def text_shape(self, element):
        kind = _placeholder(element)
        paragraphs = _paragraphs(element.find(_P + "txBody"))
        if kind in _FOOTERS:
            self.footers = self.footers or any(paragraphs)
            return
        name = _name(element)
        title = kind in _TITLES
        for index, text in enumerate(paragraphs):
            self.line(
                text,
                PptxAnchor(slide=self.number, shape=name, paragraph=index),
                "heading" if title else "body",
                equation=text.equation,
                level=1 if title else 0,
            )
        if title and self.title is None and any(paragraphs):
            self.title = " ".join(text for text in paragraphs if text)

    def picture(self, element):
        anchor = PptxAnchor(slide=self.number, shape=_name(element))
        props, _ = _props(element)
        alt = _build.alt_text(props.get("descr"), props.get("title")) if props is not None else ""
        blip = element.find(f"{_P}blipFill/{_A}blip")
        embed = blip.get(_R + "embed") if blip is not None else None
        link = blip.get(_R + "link") if blip is not None else None
        relationship = self.slide_part.rels.get(embed) if embed else None
        stored = relationship is not None and not relationship.is_external
        if self.limited:
            return
        extent = element.find(f"{_P}spPr/{_A}xfrm/{_A}ext")
        figure = Figure(
            id=f"p{self.number}-f{len(self.page.figures) + 1}",
            page=self.number,
            order=len(self.page.lines),
            anchor=anchor,
            width=_number(extent.get("cx")) // _EMU_PER_PIXEL if extent is not None else 0,
            height=_number(extent.get("cy")) // _EMU_PER_PIXEL if extent is not None else 0,
            description=alt,
            description_source="alt" if alt else "none",
        )
        if stored:
            self.images[figure.id] = relationship.target_part.blob
        else:  # as in Word: a linked picture, or an id that names no image, is a figure with no image
            linked = link is not None or relationship is not None
            why = "is linked, not stored in the file; it was not fetched"
            message = f"The picture at {cite(anchor)} {why if linked else 'has no image stored in the file'}."
            figure.flags.append(Flag(rule="image.linked-not-fetched", severity="review", message=message))
        self.page.figures.append(figure)

    def graphic_frame(self, element):
        data = element.find(f"{_A}graphic/{_GRAPHIC_DATA}")
        uri = (data.get("uri") or "") if data is not None else ""
        kind = uri.rsplit("/", 1)[-1]
        anchor = PptxAnchor(slide=self.number, shape=_name(element))
        if kind == "table" and data.find(_A + "tbl") is not None:
            self.table(data.find(_A + "tbl"), anchor)
        elif kind in _NOT_READ:
            rule, why = _NOT_READ[kind]
            self.flag(
                rule,
                f"A {_LABELS[kind]} at {cite(anchor)} is not read; {why}.",
            )
        else:
            self.flag("pptx.object-not-read", f"An embedded object at {cite(anchor)} is not read.")

    def table(self, element, anchor):
        number = len(self.page.tables) + 1
        table_id = f"p{self.number}-t{number}"
        cells, columns, rows = [], 0, 0
        for r, row in enumerate(element.findall(_A + "tr"), 1):
            rows = r
            columns = max(columns, len(row.findall(_A + "tc")))
            for c, cell in enumerate(row.findall(_A + "tc"), 1):
                if cell.get("hMerge") in ("1", "true") or cell.get("vMerge") in ("1", "true"):
                    continue  # merged away; the cell it merges into holds the text
                if self.full():
                    continue
                own = [t for t in _paragraphs(cell.find(_A + "txBody")) if t]
                text = "\n".join(own)
                if text:
                    where = anchor.model_copy(update={"cell": (r, c)})
                    equation = any(t.equation for t in own)
                    self.line(text, where, "table", table_id, equation=equation)
                    cells.append(Cell(row=r - 1, column=c - 1, text=text, anchor=where))
        if cells:
            self.page.tables.append(
                Table(id=table_id, page=self.number, rows=rows, columns=columns, cells=cells)
            )

    # -- slides -----------------------------------------------------------------------------------

    def slide(self, number: int, slide):
        self.number = number
        self.slide_part = slide.part
        self.title = None
        self.page = Page(number=number, kind="slide", name="")
        self.document.pages.append(self.page)
        if slide.element.get("show") in ("0", "false"):
            self.flag(
                "pptx.hidden-slide", f"Slide {number} is hidden in the slideshow; it is read all the same."
            )
        if slide.element.cSld.find(f"{_P}bg//{_A}blip") is not None:
            self.flag("pptx.image-fill", "An image fill is not read.")
        self.shapes(slide.element.cSld.spTree)
        if slide.has_notes_slide:
            frame = slide.notes_slide.notes_text_frame
            for index, text in enumerate(_paragraphs(frame._txBody) if frame is not None else []):
                anchor = PptxAnchor(slide=number, shape="Notes", paragraph=index, notes=True)
                self.line(text, anchor, equation=text.equation)
        self.page.name = self.title or ""


def _document_flags(reader: _Reader, names: list[str]) -> list[Flag]:
    found = []
    if reader.footers:
        found.append(
            Flag(
                rule="pptx.footers",
                severity="review",
                message="The file has footers, dates or slide numbers on its slides; they are not read.",
            )
        )
    if any(name.startswith("ppt/comments/") for name in names):
        found.append(
            Flag(rule="pptx.comments", severity="review", message="The file has comments; they are not read.")
        )
    return found


def read(data: bytes, name: str, config: Config) -> ReadResult:
    try:
        _zip.check(data, config)
    except ReaderRefused as refusal:
        if refusal.rule != "file.zip-invalid":
            raise
        raise ReaderRefused(
            "pptx.invalid", f"{name} could not be opened as a presentation: {refusal.message}"
        ) from None
    try:
        import pptx
        from lxml import etree
        from pptx.exc import PackageNotFoundError
    except ModuleNotFoundError as error:
        raise ReaderUnavailable(
            "The pptx reader needs python-pptx: pip install 'ragnaroq[formats]'"
        ) from error
    document = _build.start(data, name, "pptx", config, {"reader:pptx": READER_VERSIONS["pptx"]})
    document.pages = []
    reader = _Reader(config, document)
    try:  # only opening the package can show an invalid file; reading it afterwards is our own code
        deck = pptx.Presentation(io.BytesIO(data))
        slides = list(deck.slides)
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            names = archive.namelist()
    except (zipfile.BadZipFile, KeyError, etree.XMLSyntaxError, PackageNotFoundError, ValueError) as error:
        raise ReaderRefused("pptx.invalid", f"{name} could not be read as a presentation: {error}") from error
    for number, slide in enumerate(slides, 1):
        reader.slide(number, slide)
    document.source.pages = len(document.pages)
    document.flags.extend(_document_flags(reader, names))
    return ReadResult(document, reader.images)
