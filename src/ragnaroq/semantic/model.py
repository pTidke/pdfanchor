"""What `metadata.json` holds for one document. `schemas/metadata.schema.json` is generated from
these models, and a test keeps the two in sync."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from ..model import Flag, Status

METADATA_VERSION = "0.4.0"


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Grounded(Model):
    """A value and the lines it came from."""

    value: str | dict = Field(
        default="", description='The normalised form, or "" when it cannot be normalised without guessing.'
    )
    text: str = Field(description="The words as they stand in the lines.")
    line_ids: list[str]
    cite: str
    status: Status = Field(description="The lowest status of the lines it came from.")


class GroundedDefinition(Grounded):
    term: str = Field(description="The term as written.")


class Kind(Model):
    value: Literal["spreadsheet", "slides", "data", "faq", "numbered", "sectioned", "prose"]
    because: list[str] = Field(
        description='The counts that decided it, for example "numbered_paragraphs = 14".'
    )


class StructureCounts(Model):
    headings: int = 0
    heading_depth: int = 0
    numbered_paragraphs: int = 0
    provisos: int = 0
    explanations: int = 0
    qa_pairs: int = 0
    definitions: list[GroundedDefinition] = Field(default_factory=list)
    tables: int = 0
    table_share: float = Field(default=0.0, description="Characters in tables over all characters.")
    figures: int = 0
    slides: int = 0
    records: int = 0


class Length(Model):
    chars: int
    parts: int = Field(description="Pages, sheets or slides; 1 for a single-body document.")


class ScriptShare(Model):
    script: str
    share: float = Field(
        description="Share of the characters that carry a script, over all non-empty lines, excluded or "
        "not, so the shares sum to 1."
    )
    excluded: bool = Field(description="Every line carrying the script is excluded.")


class Trust(Model):
    accepted: float
    flagged: float
    blocked: float


class Chunking(Model):
    strategy: str
    chosen_by: Literal["rules", "override"]
    parameters: dict
    reasons: list[str]
    filters: list[str]


class DocumentMetadata(Model):
    schema_version: str = METADATA_VERSION
    path: str
    format: str
    sha256: str
    title: Grounded
    kind: Kind
    dates: list[Grounded]
    periods: list[Grounded]
    amounts: list[Grounded]
    references: list[Grounded]
    files: list[Grounded] = Field(default_factory=list)
    structure: StructureCounts
    length: Length
    scripts: list[ScriptShare]
    trust: Trust
    chunking: Chunking | None = None
    flags: list[Flag] = Field(default_factory=list)
