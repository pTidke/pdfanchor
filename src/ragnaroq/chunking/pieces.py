"""What a strategy cuts a document into, and the packers every strategy shares.

A `Piece` is one chunk before it is written: the ranges of paragraphs' text it holds (`parts`), and
the table rows or the figure it holds whole (`units`), with its text, the text to embed, its parent
and its heading path. `export.chunks` turns each piece into a chunk; it finds the lines and the
characters a piece came from by `locate` on its parts and from its units' lines.
"""

from collections.abc import Callable, Hashable
from dataclasses import dataclass, field, replace
from functools import cached_property
from typing import Literal

from ..config import Config
from ..export import Unit, units
from ..export.chunks import _figure_embedding, _table_groups, citation
from ..model import Document
from ..semantic import find
from ..semantic.model import Chunking, DocumentMetadata
from ..semantic.paragraphs import Para
from ..semantic.shape import Shape
from .sentences import pack, sentences

LONG_SENTENCE = "chunk.long-sentence"
BETWEEN_PARAGRAPHS = "\n\n"
DOCUMENT = "document"  # the parent key of a strategy whose parent is the whole document


@dataclass
class Piece:
    """One chunk to be. `parts` are `(para, start, end)` ranges of `para.text`, in order; `units`
    are the table groups and figures it holds whole (their lines, the header lines they repeat and
    their figure, which `locate` cannot find in a table's or an image's paragraph). `parent` is a
    stable key of the parent (`parent_id` is made from it) and `parent_cite` its citation."""

    kind: Literal["text", "table", "image", "definition", "summary"]
    parts: list[tuple[Para, int, int]]
    text: str
    embedding_text: str
    parent: str
    parent_cite: str
    heading_path: tuple[str, ...]
    part_of: tuple[int, int] | None = None
    rules: list[str] = field(default_factory=list)
    terms: list[str] = field(default_factory=list)
    units: list[Unit] = field(default_factory=list)


@dataclass(frozen=True)
class Parent:
    key: str
    cite: str


@dataclass
class StrategyContext:
    """What a strategy reads: the document, its paragraphs (those of a status in `include`; a
    non-PDF table's rows are decided group by group), its shape and metadata, and the plan."""

    document: Document
    paras: list[Para]
    shape: Shape
    meta: DocumentMetadata
    choice: Chunking
    config: Config
    include: tuple[str, ...] = ("accepted", "flagged")

    @property
    def max_chars(self) -> int:
        return self.choice.parameters["max_chars"]

    @property
    def min_chars(self) -> int:
        return self.choice.parameters["min_chars"]

    @cached_property
    def _tables(self) -> dict[tuple[int, str], Unit]:
        return {(u.page, u.table.id): u for u in units(self.document) if u.kind == "table" and u.table}

    def table_unit(self, para: Para) -> Unit | None:
        """The table as 0.3 cut it: its lines (matched to its cells by `_table_groups`) and its grid."""
        return self._tables.get((para.page, para.table.id)) if para.table else None


def document_parent(context: StrategyContext) -> Parent:
    lines = [line for line in context.document.lines() if not line.excluded and line.text.strip()]
    return Parent(DOCUMENT, citation(lines))


def embedding(document: Document, heading_path: tuple[str, ...], body: str) -> str:
    """The text to embed, as 0.3 made it: the title and the nearest heading above the body."""
    context = [part for part in (document.title, heading_path[-1] if heading_path else None) if part]
    if len(context) == 2 and context[0] == context[1]:
        context.pop()
    return "\n".join([" > ".join(context), body]) if context else body


def joined(parts: list[tuple[Para, int, int]]) -> str:
    """The parts' text: ranges of one paragraph joined by a space, paragraphs by a blank line."""
    text, previous = [], None
    for para, start, end in parts:
        if previous is not None:
            text.append(" " if para is previous else BETWEEN_PARAGRAPHS)
        text.append(para.text[start:end])
        previous = para
    return "".join(text)


def text_piece(
    context: StrategyContext,
    parts: list[tuple[Para, int, int]],
    parent: Parent,
    *,
    heading_path: tuple[str, ...] | None = None,
    part_of: tuple[int, int] | None = None,
    rules: list[str] | tuple[str, ...] = (),
    kind: Literal["text", "table", "definition", "summary"] = "text",
) -> Piece:
    """A piece of paragraphs' text, under the heading path of its first part unless given."""
    path = parts[0][0].heading_path if heading_path is None else heading_path
    text = joined(parts)
    return Piece(
        kind,
        list(parts),
        text,
        embedding(context.document, path, text),
        parent.key,
        parent.cite,
        path,
        part_of,
        sorted(set(rules)),
    )


def _protected(text: str, ranges: list[tuple[int, int]]) -> frozenset[int]:
    """The indexes of the sentences that hold a reference, in one pass over both (both are in order)."""
    references = find.references(text)
    held, k = set(), 0
    for index, (start, end) in enumerate(ranges):
        while k < len(references) and references[k].end <= start:
            k += 1
        if k < len(references) and references[k].start < end:
            held.add(index)
    return frozenset(held)


def cuts(para: Para, max_chars: int) -> list[tuple[int, int, bool]]:
    """`para.text` cut between sentences into `(start, end, long)` ranges without their outer
    whitespace, each starting with the last sentence of the one before (`pack`); a sentence that holds
    a reference is not split until it runs over by half (`REFERENCE_OVERRUN`)."""
    text = para.text
    ranges = sentences(text, para.breaks)
    found = []
    for cut in pack(text, ranges, max_chars, protected=_protected(text, ranges)):
        piece = text[cut.start : cut.end]
        start = cut.start + len(piece) - len(piece.lstrip())
        end = cut.end - (len(piece) - len(piece.rstrip()))
        if start < end:
            found.append((start, end, cut.long))
    return found


def split_paragraph(
    context: StrategyContext, para: Para, parent: Parent, *, max_chars: int | None = None
) -> list[Piece]:
    """A paragraph over the limit as pieces of whole sentences, each marked `part_of` (n, m); a piece
    of a sentence longer than the limit carries `chunk.long-sentence`."""
    found = cuts(para, max_chars or context.max_chars)
    count = len(found)
    return [
        text_piece(
            context,
            [(para, start, end)],
            parent,
            part_of=(n, count) if count > 1 else None,
            rules=[LONG_SENTENCE] if long else [],
        )
        for n, (start, end, long) in enumerate(found, 1)
    ]


def pack_paras(context: StrategyContext, paras: list[Para], parent: Parent) -> list[Piece]:
    """Whole paragraphs joined, in order, into pieces of at most `max_chars` (blank lines counted);
    a paragraph over it is split between sentences and stands alone."""
    limit = context.max_chars
    made: list[Piece] = []
    pending: list[Para] = []
    size = 0

    def flush():
        if pending:
            made.append(text_piece(context, [(p, 0, len(p.text)) for p in pending], parent))
            pending.clear()

    for para in paras:
        if len(para.text) > limit:
            flush()
            made.extend(split_paragraph(context, para, parent))
            continue
        grown = size + len(BETWEEN_PARAGRAPHS) + len(para.text) if pending else len(para.text)
        if pending and grown > limit:
            flush()
            grown = len(para.text)
        pending.append(para)
        size = grown
    flush()
    return made


def _merged(context: StrategyContext, first: Piece, second: Piece) -> Piece:
    text = first.text + BETWEEN_PARAGRAPHS + second.text
    return replace(
        first,
        parts=first.parts + second.parts,
        text=text,
        embedding_text=embedding(context.document, first.heading_path, text),
        part_of=first.part_of or second.part_of,
        rules=sorted({*first.rules, *second.rules}),
        terms=list(dict.fromkeys([*first.terms, *second.terms])),
        units=first.units + second.units,
    )


def join_small(
    context: StrategyContext,
    pieces: list[Piece],
    *,
    key: Callable[[Piece], Hashable] = lambda piece: piece.parent,
) -> list[Piece]:
    """A text piece under `min_chars` joins the next piece, else the one before, when both are text
    pieces with the same `key` (the same parent by default), they are not two pieces of one paragraph,
    and together they fit in `max_chars`; otherwise it stays as it is."""
    least, limit = context.min_chars, context.max_chars

    def small(piece: Piece) -> bool:
        return piece.kind == "text" and len(piece.text) < least

    def fits(first: Piece, second: Piece) -> bool:
        return (
            first.kind == second.kind == "text"
            and bool(first.parts and second.parts)
            and first.parts[-1][0] is not second.parts[0][0]
            and key(first) == key(second)
            and len(first.text) + len(BETWEEN_PARAGRAPHS) + len(second.text) <= limit
        )

    waiting = list(pieces)
    kept: list[Piece] = []
    for i, piece in enumerate(waiting):
        if small(piece) and i + 1 < len(waiting) and fits(piece, waiting[i + 1]):
            waiting[i + 1] = _merged(context, piece, waiting[i + 1])
        elif small(piece) and kept and fits(kept[-1], piece):
            kept[-1] = _merged(context, kept[-1], piece)
        else:
            kept.append(piece)
    return kept


def table_pieces(context: StrategyContext, para: Para, parent: Parent) -> list[Piece]:
    """A table as 0.3 cut it: a table of a file that is not a PDF in groups of rows under its header
    row, each decided alone (`include`); a PDF table whole, its lines not being matched to its cells;
    a table the reader gave no grid for, as its lines' text."""
    if para.table is None:
        return [text_piece(context, [(para, 0, len(para.text))], parent, kind="table")] if para.spans else []
    unit = context.table_unit(para)
    if unit is None or not unit.text:
        return []
    pdf = context.document.source.format == "pdf"
    groups = [unit] if pdf else _table_groups(unit, context.max_chars)
    return [
        Piece(
            "table",
            [(para, 0, len(para.text))],
            group.text,
            embedding(context.document, para.heading_path, group.text),
            parent.key,
            parent.cite,
            para.heading_path,
            units=[group],
        )
        for group in groups
        if group.lines and group.status in context.include
    ]


def image_pieces(context: StrategyContext, para: Para, parent: Parent) -> list[Piece]:
    """A figure with a description as an image piece of its own; the model's sentence, marked
    "(model)", goes only into the text to embed."""
    figure = para.figure
    if figure is None or not figure.description.strip():
        return []
    return [
        Piece(
            "image",
            [(para, 0, len(para.text))],
            figure.description.strip(),
            embedding(context.document, para.heading_path, _figure_embedding(figure)),
            parent.key,
            parent.cite,
            para.heading_path,
            units=[Unit("image", para.page, [], figure=figure)],
        )
    ]
