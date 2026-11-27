"""The kind of a document, by the first rule that matches (spec section 3)."""

from ..model import Document
from .model import Kind
from .shape import Shape

FAQ_MIN_PAIRS = 3
FAQ_MIN_SHARE = 0.4
NUMBERED_MIN = 3
NUMBERED_WITH_NOTE_MIN = 2
SECTIONED_MIN_DEPTH = 2
SECTIONED_MIN_HEADINGS = 3
SECTIONED_MIN_CHARS = 3000
TABLE_SHARE = 0.6
DOC_LIST_LIMIT = 1000


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
