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
from ..semantic.rules import REFERENCE_OVERRUN
from ..semantic.shape import Shape
from .sentences import Cut, pack, sentences

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


def _rebalanced(
    text: str,
    ranges: list[tuple[int, int]],
    protected: frozenset[int],
    found: list[Cut],
    max_chars: int,
    least: int,
    lead: int = 0,
) -> list[Cut]:
    """When the last cut is under `least`, move the boundary before it back by whole sentences until
    it is not, while it stays within `max_chars` and the cut before stays at `least` or more (with
    `lead` characters already in front of a first cut). The repeated sentence follows the boundary,
    under `pack`'s rule. When that cannot be done, the cuts stay as they are (R17 b, c)."""
    if least <= 0 or len(found) < 2 or found[-2].long or found[-1].long:
        return found
    ends = [stop - (len(text[start:stop]) - len(text[start:stop].rstrip())) for start, stop in ranges]
    first_of = {start: i for i, (start, _) in enumerate(ranges)}
    last_of = {stop: i for i, (_, stop) in enumerate(ranges)}
    stretched = int(max_chars * (1 + REFERENCE_OVERRUN))

    def size(first: int, last: int) -> int:
        return ends[last] - ranges[first][0]

    before, tail = found[-2], len(ranges) - 1
    if size(first_of[found[-1].start], tail) >= least:
        return found
    start, stop = first_of[before.start], last_of[before.end]
    shared = len(found) > 2 and not found[-3].long and last_of[found[-3].end] == start
    floor = start + 1 if shared else start  # the cut before keeps a sentence of its own
    extra = lead if len(found) == 2 else 0
    for moved in range(stop - 1, floor - 1, -1):
        if size(start, moved) + extra < least:
            break
        limit = stretched if moved + 1 in protected else max_chars
        again = moved if size(moved, moved + 1) <= limit else moved + 1  # the repeated sentence
        if size(again, tail) > max_chars:
            break
        if size(again, tail) >= least:
            return [
                *found[:-2],
                Cut(before.start, ranges[moved][1], False),
                Cut(ranges[again][0], found[-1].end, False),
            ]
    return found


def cuts(
    para: Para, max_chars: int, *, least: int = 0, room: int | None = None
) -> list[tuple[int, int, bool]] | None:
    """`para.text` cut between sentences into `(start, end, long)` ranges without their outer
    whitespace, each starting with the last sentence of the one before (`pack`); a sentence that holds
    a reference is not split until it runs over by half (`REFERENCE_OVERRUN`). With `least`, a last
    cut under it takes sentences from the cut before (`_rebalanced`). With `room`, the first cut holds
    at most `room` characters, for what goes in front of it; None when its first sentence does not
    fit there."""
    text = para.text
    ranges = sentences(text, para.breaks)
    if not ranges:
        return []
    protected = _protected(text, ranges)
    lead = 0
    if room is None:
        found = pack(text, ranges, max_chars, protected=protected)
    else:
        first = ranges[0][0] + len(text[ranges[0][0] : ranges[0][1]].rstrip())
        if first - ranges[0][0] > room:
            return None
        head = pack(text, ranges, room)[0]  # not stretched: it must fit beside what goes before it
        found, lead = [head], max_chars - room
        last = next(i for i, (_, stop) in enumerate(ranges) if stop == head.end)
        if last + 1 < len(ranges):
            rest = pack(
                text, ranges[last:], max_chars, protected=frozenset(i - last for i in protected if i >= last)
            )
            if rest[0] == Cut(*ranges[last], False):  # only the repeated sentence: it and the next do not fit
                rest = rest[1:]
            found += rest
    found = _rebalanced(text, ranges, protected, found, max_chars, least, lead)
    trimmed = []
    for cut in found:
        piece = text[cut.start : cut.end]
        start = cut.start + len(piece) - len(piece.lstrip())
        end = cut.end - (len(piece) - len(piece.rstrip()))
        if start < end:
            trimmed.append((start, end, cut.long))
    return trimmed


def _cut_pieces(
    context: StrategyContext,
    para: Para,
    found: list[tuple[int, int, bool]],
    parent: Parent,
    lead: list[Para] | tuple[Para, ...] = (),
) -> list[Piece]:
    """The pieces of a cut paragraph, each marked `part_of` (n, m), the first holding `lead` (whole
    paragraphs) in front of it; a piece of a sentence longer than the limit carries
    `chunk.long-sentence`."""
    count = len(found)
    made = []
    for n, (start, end, long) in enumerate(found, 1):
        parts = [(p, 0, len(p.text)) for p in lead] if n == 1 else []
        parts.append((para, start, end))
        made.append(
            text_piece(
                context,
                parts,
                parent,
                part_of=(n, count) if count > 1 else None,
                rules=[LONG_SENTENCE] if long else [],
            )
        )
    return made


def split_paragraph(
    context: StrategyContext, para: Para, parent: Parent, *, max_chars: int | None = None
) -> list[Piece]:
    """A paragraph over the limit as pieces of whole sentences, its last piece rebalanced to reach
    `min_chars` where that fits (`cuts`)."""
    found = cuts(para, max_chars or context.max_chars, least=context.min_chars) or []
    return _cut_pieces(context, para, found, parent)


def _joined_split(
    context: StrategyContext, pending: list[Para], size: int, para: Para, parent: Parent, limit: int
) -> list[Piece] | None:
    """Short paragraphs (`size` under `min_chars`) in front of the first piece of `para`, cut to the
    room they leave in `limit` (R17 a). A paragraph that would fit a chunk of its own is cut so only
    when no piece is then left under `min_chars`. None when it cannot be done."""
    least = context.min_chars
    room = limit - size - len(BETWEEN_PARAGRAPHS)
    found = cuts(para, limit, least=least, room=room) if room > 0 else None
    if not found or len(found) < 2:
        return None
    sizes = [size + len(BETWEEN_PARAGRAPHS) + found[0][1] - found[0][0]]
    sizes += [end - start for start, end, _ in found[1:]]
    if len(para.text) <= limit and min(sizes) < least:
        return None
    return _cut_pieces(context, para, found, parent, lead=pending)


def pack_paras(
    context: StrategyContext, paras: list[Para], parent: Parent, *, max_chars: int | None = None
) -> list[Piece]:
    """Whole paragraphs joined, in order, into pieces of at most `max_chars` (blank lines counted;
    the plan's unless given, as when a strategy repeats text in front of each piece); a paragraph
    over it is split between sentences. Short paragraphs (under `min_chars`) that the next paragraph
    does not fit beside go in front of its first piece, cut to the room left (`_joined_split`); when
    that cannot be done they stay a small piece, for `join_small`, and `max_chars` wins (R17)."""
    limit, least = max_chars or context.max_chars, context.min_chars
    made: list[Piece] = []
    pending: list[Para] = []
    size = 0

    def flush():
        if pending:
            made.append(text_piece(context, [(p, 0, len(p.text)) for p in pending], parent))
            pending.clear()

    for para in paras:
        grown = size + len(BETWEEN_PARAGRAPHS) + len(para.text) if pending else len(para.text)
        if grown <= limit:
            pending.append(para)
            size = grown
            continue
        if (
            pending
            and size < least
            and (joined := _joined_split(context, pending, size, para, parent, limit))
        ):
            made.extend(joined)
            pending.clear()
            size = 0
            continue
        flush()
        if len(para.text) > limit:
            made.extend(split_paragraph(context, para, parent, max_chars=limit))
            size = 0
        else:
            pending.append(para)
            size = len(para.text)
    flush()
    return made


def merged(context: StrategyContext, first: Piece, second: Piece) -> Piece:
    """The two pieces as one, the second after the first, under the first's parent and heading path."""
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
    max_chars: int | None = None,
) -> list[Piece]:
    """A text piece under `min_chars` joins the next piece, else the one before, when both are text
    pieces with the same `key` (the same parent by default), they are not two pieces of one paragraph,
    and together they fit in `max_chars` (the plan's unless given); otherwise `max_chars` wins and it
    stays as it is (R17 c). Pieces of a split paragraph are rebalanced before this, by `cuts`, and
    short paragraphs in front of a long one by `pack_paras`."""
    least, limit = context.min_chars, max_chars or context.max_chars

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
            waiting[i + 1] = merged(context, piece, waiting[i + 1])
        elif small(piece) and kept and fits(kept[-1], piece):
            kept[-1] = merged(context, kept[-1], piece)
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
