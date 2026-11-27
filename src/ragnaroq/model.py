"""The output contract. Every piece of text stays anchored to its place on the page.

`schemas/document.schema.json` is generated from these models; a test keeps them in sync.
Coordinates are PDF points with a top-left origin on the displayed page.
"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .anchors import Anchor, PdfAnchor

SCHEMA_VERSION = "0.4.0"

Status = Literal["accepted", "flagged", "blocked"]


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Box(Model):
    x0: float
    top: float
    x1: float
    bottom: float


class Flag(Model):
    """Why a rule did not accept something. `rule` is a stable identifier."""

    rule: str
    severity: Literal["review", "blocking"]
    message: str


class Reading(Model):
    """Another reading of the same place. Its text becomes the line's `text` only through `resolution`."""

    source: str
    text: str
    confidence: float | None = None


class Resolution(Model):
    """How a disputed line was settled by rule.

    `text_from` names the reading whose text `text` now holds."""

    rule: str
    agreed: list[str]
    text_from: str


class CellValue(Model):
    """What a spreadsheet cell holds, apart from how it is shown."""

    stored: str | float | int | bool | None
    formula: str | None = None
    format: str | None = None


class Line(Model):
    id: str
    page: int
    order: int
    box: Box | None = None
    anchor: Anchor | None = Field(
        default=None, description="Where the line lives in its file. A PDF line's anchor is its page and box."
    )
    value: CellValue | None = None
    raw: str = Field(description="Exactly what the reader returned.")
    text: str = Field(
        description=(
            "NFC-normalised `raw`, unless `resolution.text_from` names another reading, "
            "whose text it then holds."
        ),
    )
    source: str = Field(description='"native" or "ocr:<engine>".')
    readings: list[Reading] = Field(default_factory=list)
    font_size: float | None = None
    bold: bool = False
    scripts: list[str] = Field(default_factory=list)
    role: Literal["heading", "body", "table"] = "body"
    level: int = Field(
        default=0, description="Heading level, 1 the highest; 0 when the line is not a heading."
    )
    paragraph: int = 0
    table_id: str | None = None
    status: Status = "flagged"
    flags: list[Flag] = Field(default_factory=list)
    resolution: Resolution | None = None
    excluded: str | None = Field(
        default=None, description="Reason this line is kept as evidence but left out of exports."
    )

    @model_validator(mode="after")
    def _pdf_anchor(self):
        # Documents before 0.3.0 had a box and no anchor.
        if self.anchor is None and self.box is not None:
            b = self.box
            self.anchor = PdfAnchor(page=self.page, box=(b.x0, b.top, b.x1, b.bottom))
        return self


class Cell(Model):
    row: int
    column: int
    text: str | None = Field(description="None means the reader gave no value, not a blank.")
    box: Box | None = None
    anchor: Anchor | None = None


class Table(Model):
    id: str
    page: int
    box: Box | None = None
    rows: int
    columns: int
    cells: list[Cell]
    status: Status = "flagged"
    flags: list[Flag] = Field(default_factory=list)


class Figure(Model):
    id: str
    page: int
    anchor: Anchor
    order: int = Field(default=0, description="How many of the part's lines come before the figure.")
    path: str = ""
    width: int = 0
    height: int = 0
    description: str = ""
    description_source: Literal["caption", "alt", "ocr", "none"] = "none"
    model_description: str | None = None
    ocr_lines: list[Line] = Field(default_factory=list)
    ocr_sources: list[str] = Field(
        default_factory=list,
        description='The OCR reader the description came from, e.g. "ocr:tesseract".',
    )
    status: Status = "flagged"
    flags: list[Flag] = Field(default_factory=list)


class Page(Model):
    number: int
    width: float | None = None
    height: float | None = None
    rotation: int = 0
    kind: Literal["native", "scanned", "blank", "failed", "sheet", "slide", "body"]
    name: str | None = None
    lines: list[Line] = Field(default_factory=list)
    tables: list[Table] = Field(default_factory=list)
    figures: list[Figure] = Field(default_factory=list)
    flags: list[Flag] = Field(default_factory=list)
    metrics: dict[str, float] = Field(default_factory=dict)


class Source(Model):
    sha256: str
    bytes: int
    filename: str
    pages: int
    format: str = "pdf"


class Engine(Model):
    name: str
    version: str
    policy_version: str
    config_sha256: str
    components: dict[str, str]


class Document(Model):
    schema_version: Literal["0.1.0", "0.2.0", "0.3.0", "0.4.0"] = Field(
        default=SCHEMA_VERSION,
        description=(
            'The contract version. A stored "0.1.0" to "0.3.0" document loads, and is restamped "0.4.0".'
        ),
    )
    source: Source
    engine: Engine
    created_at: str
    title: str | None = None
    pages: list[Page]
    flags: list[Flag] = Field(default_factory=list)
    summary: dict[str, int] = Field(default_factory=dict)

    @field_validator("schema_version")
    @classmethod
    def _restamp(cls, value: str) -> str:
        # 0.2.0 to 0.4.0 only added fields with defaults (and Line fills its PDF anchor).
        return SCHEMA_VERSION

    def lines(self):
        return (line for page in self.pages for line in page.lines)
