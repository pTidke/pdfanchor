"""Where a piece of text lives in its file, one anchor class per kind of file.

An anchor is what a person needs to find the text again: a page and box for a PDF, a cell for a
spreadsheet, a path for HTML or XML. `cite` writes it short, and `key` makes it comparable.
"""

import json
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class _Anchor(BaseModel):
    model_config = ConfigDict(extra="forbid")


class PdfAnchor(_Anchor):
    type: Literal["pdf"] = "pdf"
    page: int
    box: tuple[float, float, float, float]


class XlsxAnchor(_Anchor):
    type: Literal["xlsx"] = "xlsx"
    sheet: str
    cell: str = Field(description='"C7", or "B2:D2" for a merged range.')


class PptxAnchor(_Anchor):
    """`slide` is 1-based; `paragraph` is 0-based in the shape's text frame. A table cell is
    `cell`, a 1-based (row, column), with paragraph 0."""

    type: Literal["pptx"] = "pptx"
    slide: int
    shape: str
    paragraph: int = 0
    notes: bool = False
    cell: tuple[int, int] | None = None


class DocxAnchor(_Anchor):
    """All numbers are 1-based. A paragraph, a table cell (`table` and `cell` as row, column) or a note."""

    type: Literal["docx"] = "docx"
    paragraph: int | None = None
    table: int | None = None
    cell: tuple[int, int] | None = None
    note: str | None = None

    @model_validator(mode="after")
    def _one_place(self):
        in_table = self.table is not None and self.cell is not None
        stray = (self.table is None) != (self.cell is None)
        if stray or sum((self.paragraph is not None, in_table, self.note is not None)) != 1:
            raise ValueError("a docx anchor names exactly one of: paragraph; table with cell; note")
        return self


class HtmlAnchor(_Anchor):
    type: Literal["html"] = "html"
    path: str


class MdAnchor(_Anchor):
    type: Literal["md"] = "md"
    lines: tuple[int, int] = Field(description="First and last line, 1-based and inclusive.")


class JsonAnchor(_Anchor):
    type: Literal["json"] = "json"
    pointer: str


class XmlAnchor(_Anchor):
    type: Literal["xml"] = "xml"
    path: str


class CsvAnchor(_Anchor):
    type: Literal["csv"] = "csv"
    row: int = Field(description="1-based; the header row counts.")
    column: str


Anchor = Annotated[
    PdfAnchor
    | XlsxAnchor
    | PptxAnchor
    | DocxAnchor
    | HtmlAnchor
    | MdAnchor
    | JsonAnchor
    | XmlAnchor
    | CsvAnchor,
    Field(discriminator="type"),
]


def pdf_anchor(page: int, x0: float, top: float, x1: float, bottom: float) -> PdfAnchor:
    return PdfAnchor(page=page, box=tuple(round(v, 2) for v in (x0, top, x1, bottom)))


def cite(anchor: Anchor) -> str:
    """A short citation for a person to read."""
    match anchor:
        case PdfAnchor():
            return f"p. {anchor.page}"
        case XlsxAnchor():
            return f"{anchor.sheet}!{anchor.cell}"
        case PptxAnchor():
            if anchor.notes:
                return f"slide {anchor.slide} notes"
            if anchor.cell is not None:
                return f"slide {anchor.slide}, {anchor.shape}, row {anchor.cell[0]}, column {anchor.cell[1]}"
            return f"slide {anchor.slide}, {anchor.shape}"
        case DocxAnchor():
            if anchor.note is not None:
                return anchor.note
            if anchor.table is not None and anchor.cell is not None:
                return f"table {anchor.table}, row {anchor.cell[0]}, column {anchor.cell[1]}"
            return f"¶{anchor.paragraph}"
        case HtmlAnchor() | XmlAnchor():
            return anchor.path
        case MdAnchor():
            return f"lines {anchor.lines[0]}–{anchor.lines[1]}"
        case JsonAnchor():
            return anchor.pointer
        case CsvAnchor():
            return f"row {anchor.row}, {anchor.column}"
    raise TypeError(f"not an anchor: {anchor!r}")


def key(anchor: Anchor) -> str:
    """Canonical JSON with sorted keys; two anchors for the same place have the same key."""
    return json.dumps(anchor.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
