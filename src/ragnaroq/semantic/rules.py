"""The kind of a document and the chunking strategy for it, each by the first rule that matches
(spec section 3)."""

from typing import TYPE_CHECKING

from ..config import Config
from ..model import Document
from .model import Chunking, DocumentMetadata, Kind
from .shape import Shape

if TYPE_CHECKING:
    from .overrides import Override

SINGLE_MAX_CHARS = 1500

FAQ_MIN_PAIRS = 3
FAQ_MIN_SHARE = 0.4
NUMBERED_MIN = 3
NUMBERED_WITH_NOTE_MIN = 2
SECTIONED_MIN_DEPTH = 2
SECTIONED_MIN_HEADINGS = 3
SECTIONED_MIN_CHARS = 3000
TABLE_SHARE = 0.6
DEFAULT_MIN_CHARS = 200
MAX_CHARS_LIMITS = (200, 4000)
REFERENCE_OVERRUN = 0.5
CHUNK_LIST_LIMIT = 50
DOC_LIST_LIMIT = 1000
ONE_TABLE_SHARE = 0.95
STRATEGIES = (
    "single",
    "question-answer",
    "numbered-paragraphs",
    "sections",
    "table-rows",
    "slides",
    "records",
    "sentences",
)
CHUNKING_VERSION = "1"

# strategy -> (parent, keep_with_parent): fixed, and shown in the plan
_FIXED: dict[str, tuple[str, list[str]]] = {
    "single": ("document", []),
    "question-answer": ("question", ["answer"]),
    "numbered-paragraphs": ("section", ["proviso", "explanation"]),
    "sections": ("section", []),
    "table-rows": ("table", []),
    "slides": ("slide", ["notes"]),
    "records": ("record", []),
    "sentences": ("document", []),
}


def qa_share(shape: Shape) -> float:
    """The share of the body characters that sit in question-answer pairs."""
    if not shape.body_chars:
        return 0.0
    held = 0
    for question, answers in shape.qa:
        held += len(question.text) if question.kind == "body" else 0
        held += sum(len(a.text) for a in answers if a.kind == "body")
    return min(held / shape.body_chars, 1.0)


def table_share(shape: Shape) -> float:
    return shape.table_chars / shape.chars if shape.chars else 0.0


def kind_of(shape: Shape, document: Document) -> Kind:
    fmt = document.source.format
    if fmt in ("xlsx", "csv"):
        return Kind(value="spreadsheet", because=[f"format = {fmt}"])
    if fmt == "pptx":
        return Kind(value="slides", because=["format = pptx"])
    if fmt in ("json", "xml"):
        return Kind(value="data", because=[f"format = {fmt}"])
    pairs, share = len(shape.qa), qa_share(shape)
    if pairs >= FAQ_MIN_PAIRS and share >= FAQ_MIN_SHARE:
        return Kind(value="faq", because=[f"qa_pairs = {pairs}", f"qa_share = {share:.2f}"])
    numbered, notes = len(shape.numbered), len(shape.provisos) + len(shape.explanations)
    if numbered >= NUMBERED_MIN or (notes and numbered >= NUMBERED_WITH_NOTE_MIN):
        because = [f"numbered_paragraphs = {numbered}"]
        if shape.provisos:
            because.append(f"provisos = {len(shape.provisos)}")
        if shape.explanations:
            because.append(f"explanations = {len(shape.explanations)}")
        return Kind(value="numbered", because=because)
    if shape.heading_depth >= SECTIONED_MIN_DEPTH or (
        shape.headings >= SECTIONED_MIN_HEADINGS and shape.chars >= SECTIONED_MIN_CHARS
    ):
        return Kind(
            value="sectioned",
            because=[
                f"heading_depth = {shape.heading_depth}",
                f"headings = {shape.headings}",
                f"chars = {shape.chars}",
            ],
        )
    tables = table_share(shape)
    if tables >= TABLE_SHARE:
        return Kind(value="spreadsheet", because=[f"table_share = {tables:.2f}"])
    return Kind(
        value="prose",
        because=[f"qa_pairs = {pairs}", f"numbered_paragraphs = {numbered}", f"headings = {shape.headings}"],
    )


def _strategy(meta: DocumentMetadata, config: Config) -> tuple[str, list[str]]:
    """The strategy by the spec's table, and the reasons (metadata fields with their values)."""
    chars, structure = meta.length.chars, meta.structure
    kind = f"kind = {meta.kind.value} ({', '.join(meta.kind.because)})"
    if chars < SINGLE_MAX_CHARS and structure.largest_table_chars <= config.chunk_chars:
        return "single", [f"length.chars = {chars} < {SINGLE_MAX_CHARS}"]
    if chars < SINGLE_MAX_CHARS:
        big = f"structure.largest_table_chars = {structure.largest_table_chars} > {config.chunk_chars}"
    else:
        big = f"length.chars = {chars} > {SINGLE_MAX_CHARS}"
    reasons = [kind, big]
    by_kind = {
        "faq": "question-answer",
        "numbered": "numbered-paragraphs",
        "sectioned": "sections",
        "spreadsheet": "table-rows",
        "slides": "slides",
    }
    if meta.kind.value in by_kind:
        return by_kind[meta.kind.value], reasons
    if meta.kind.value == "data":
        if structure.tables == 1 and structure.table_share >= ONE_TABLE_SHARE:
            reasons += [f"structure.tables = 1, structure.table_share = {structure.table_share:.2f}"]
            return "table-rows", reasons
        return "records", reasons
    return "sentences", reasons


def choose(meta: DocumentMetadata, config: Config, override: "Override | None" = None) -> Chunking:
    """The chunking plan for one document: by the rules, or as `override` says."""
    if override:
        strategy, chosen_by = override.strategy, "override"
        reasons = [f"override: {override.pattern}"]
    else:
        (strategy, reasons), chosen_by = _strategy(meta, config), "rules"
    parent, keep = _FIXED[strategy]
    max_chars = override.max_chars if override and override.max_chars is not None else config.chunk_chars
    min_chars = override.min_chars if override and override.min_chars is not None else DEFAULT_MIN_CHARS
    filters = [name for name in ("dates", "periods", "references") if getattr(meta, name)]
    return Chunking(
        strategy=strategy,
        chosen_by=chosen_by,
        parameters={
            "max_chars": max_chars,
            "min_chars": min_chars,
            "parent": parent,
            "keep_with_parent": keep,
        },
        reasons=reasons,
        filters=filters,
    )
