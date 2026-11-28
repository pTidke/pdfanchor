"""The chunking strategies (spec section 3), each a pure function of a document and its plan,
registered by name in `STRATEGY_FUNCTIONS`. A table in a document of any strategy is cut by
`table_pieces`, a figure by `image_pieces`.

A unit that `numbered-paragraphs`, `sections` or `question-answer` splits repeats its anchor (the
paragraph's number, the section's heading, the question) at the start of every piece after the
first, room being left for it; the anchor is in the piece's `text` only, not in its `parts`."""

from collections.abc import Callable
from dataclasses import dataclass, field, replace
from itertools import count

from ..export.chunks import citation
from ..model import Line
from ..semantic.paragraphs import Para, numbering
from ..semantic.rules import STRATEGIES
from .pieces import (
    BETWEEN_PARAGRAPHS,
    Parent,
    Piece,
    StrategyContext,
    document_parent,
    embedding,
    image_pieces,
    join_small,
    merged,
    pack_paras,
    table_pieces,
    text_piece,
)

TEXT_KINDS = ("heading", "body")


def single(context: StrategyContext) -> list[Piece]:
    """The whole document as one piece: its headings, paragraphs and tables in order. A figure stays
    an image piece of its own, where it stands."""
    parent = document_parent(context)
    made: list[Piece] = []
    parts, held, texts = [], [], []
    at = None
    for para in context.paras:
        if para.kind == "image":
            made.extend(image_pieces(context, para, parent))
            continue
        if para.kind == "table" and para.table is not None:
            unit = context.table_unit(para)
            if unit is None or not unit.lines or unit.status not in context.include:
                continue
            held.append(unit)
        if at is None:
            at = len(made)
        parts.append((para, 0, len(para.text)))
        texts.append(para.text)
    if parts:
        kind = "table" if all(para.kind == "table" for para, _, _ in parts) else "text"
        text = "\n\n".join(texts)
        whole = Piece(
            kind, parts, text, embedding(context.document, (), text), parent.key, parent.cite, (), units=held
        )
        made.insert(at, whole)
    return made


def sentences(context: StrategyContext) -> list[Piece]:
    """Paragraphs joined up to `max_chars` under one heading path (a heading starts a new piece, as
    in 0.3); a paragraph over it split between sentences, one sentence repeated; a piece under
    `min_chars` joined to its neighbour under the same heading when the two fit."""
    parent = document_parent(context)
    made: list[Piece] = []
    run: list[Para] = []

    def flush():
        made.extend(pack_paras(context, run, parent))
        run.clear()

    for para in context.paras:
        if para.kind == "body":
            run.append(para)
        elif para.kind == "heading":
            flush()
        elif para.kind == "table":
            flush()
            made.extend(table_pieces(context, para, parent))
        elif found := image_pieces(context, para, parent):
            flush()
            made.extend(found)
    flush()
    return join_small(context, made, key=lambda piece: (piece.parent, piece.heading_path))


# --- shared by numbered-paragraphs, sections and question-answer -----------------------------------------


def _size(paras: list[Para]) -> int:
    """The length of the paragraphs' text joined by blank lines."""
    return sum(len(para.text) for para in paras) + len(BETWEEN_PARAGRAPHS) * max(len(paras) - 1, 0)


def _whole(paras: list[Para]) -> list[tuple[Para, int, int]]:
    return [(para, 0, len(para.text)) for para in paras]


def _text_only(paras: list[Para]) -> list[Para] | None:
    """The headings and body paragraphs among `paras`, or None when a table or a described figure is
    among them (each is a piece of its own). A figure with no description holds nothing."""
    text = []
    for para in paras:
        if para.kind in TEXT_KINDS:
            text.append(para)
        elif para.kind == "table" or (para.figure is not None and para.figure.description.strip()):
            return None
    return text


def _lines(context: StrategyContext, paras: list[Para]) -> list[Line]:
    """The lines the paragraphs come from, a table's from its cells, for a parent's citation."""
    found: list[Line] = []
    for para in paras:
        unit = context.table_unit(para) if para.kind == "table" else None
        found += unit.lines if unit else [span.line for span in para.spans]
    return [line for line in found if not line.excluded and line.text.strip()]


def _packed(
    context: StrategyContext, paras: list[Para], parent: Parent, max_chars: int | None = None
) -> list[Piece]:
    """Runs of text packed by `pack_paras` (in `max_chars`, the plan's unless given), each table cut
    by `table_pieces` and each figure by `image_pieces`, in order."""
    made: list[Piece] = []
    run: list[Para] = []

    def flush():
        made.extend(pack_paras(context, run, parent, max_chars=max_chars))
        run.clear()

    for para in paras:
        if para.kind in TEXT_KINDS:
            run.append(para)
        elif para.kind == "table":
            flush()
            made.extend(table_pieces(context, para, parent))
        elif found := image_pieces(context, para, parent):
            flush()
            made.extend(found)
    flush()
    return made


def _prefixed(context: StrategyContext, piece: Piece, anchor: str) -> Piece:
    """The piece with `anchor` in front of its text (not of its parts: the lines it cites stay its own)."""
    text = anchor + piece.text
    return replace(piece, text=text, embedding_text=embedding(context.document, piece.heading_path, text))


def _counted(made: list[Piece]) -> list[Piece]:
    """The text pieces of one unit numbered `part_of` (n, m), when there are two or more."""
    texts = [i for i, piece in enumerate(made) if piece.kind == "text"]
    for n, i in enumerate(texts, 1):
        made[i] = replace(made[i], part_of=(n, len(texts)) if len(texts) > 1 else None)
    return made


def _headed(
    context: StrategyContext, head: list[Para], body: list[Para], parent: Parent, path: tuple[str, ...]
) -> list[Piece]:
    """A unit too long for one piece: `body` packed between paragraphs, then sentences, with `head`
    (whole paragraphs: a question, or a heading with any headings above it that hold nothing of their
    own) in front of its first piece, and the last of `head` repeated at the start of every other text
    piece, room being left for both. A head over half of `max_chars` is not repeated; it goes in front
    of the first piece when it fits there, else it is a piece of its own (R17 c)."""
    limit = context.max_chars
    width = _size(head) + len(BETWEEN_PARAGRAPHS)
    repeat = width <= limit // 2
    room = limit - width if repeat else limit
    made = join_small(context, _packed(context, body, parent, room), max_chars=room)
    first = made[0] if made and made[0].kind == "text" else None
    if first and (repeat or width + len(first.text) <= limit):
        made[0] = text_piece(
            context, [*_whole(head), *first.parts], parent, heading_path=path, rules=first.rules
        )
    else:  # a table or a figure first, or a head too long to sit beside the first piece
        made[:0] = [replace(p, heading_path=path) for p in pack_paras(context, head, parent)]
    if repeat:
        again = head[-1].text + BETWEEN_PARAGRAPHS
        made[1:] = [_prefixed(context, p, again) if p.kind == "text" else p for p in made[1:]]
    return _counted(made)


@dataclass
class _Section:
    """A heading and what follows it until a heading of the same or a higher level: its own
    paragraphs (those before its first subsection) and its subsections. `start` and `end` are
    indexes into the context's paragraphs; the root section holds what comes before the first
    heading, and has no heading."""

    heading: Para | None
    start: int
    end: int
    own: list[Para] = field(default_factory=list)
    children: list["_Section"] = field(default_factory=list)

    @property
    def path(self) -> tuple[str, ...]:
        return self.heading.heading_path if self.heading else ()


def _outline(paras: list[Para]) -> _Section:
    root = _Section(None, 0, len(paras))
    open_sections = [root]
    for i, para in enumerate(paras):
        if para.kind == "heading":
            while len(open_sections) > 1 and open_sections[-1].heading.level >= para.level:
                open_sections.pop().end = i
            section = _Section(para, i, len(paras))
            open_sections[-1].children.append(section)
            open_sections.append(section)
        else:
            open_sections[-1].own.append(para)
    return root


def _walk(root: _Section) -> list[_Section]:
    """The sections in reading order: each before its subsections."""
    found, waiting = [], [root]
    while waiting:
        section = waiting.pop()
        found.append(section)
        waiting.extend(reversed(section.children))
    return found


def _section_parent(context: StrategyContext, section: _Section) -> Parent:
    """A section as a parent: keyed by its heading's first line, cited from its heading to its end,
    subsections included. What comes before the first heading has the document as parent."""
    if section.heading is None:
        return document_parent(context)
    lines = _lines(context, context.paras[section.start : section.end])
    return Parent(f"section:{section.heading.spans[0].line.id}", citation(lines))


# --- numbered-paragraphs ------------------------------------------------------------------------------------


def _units(paras: list[Para], numbered: set[int]) -> list[list[Para]]:
    """Each numbered paragraph with what follows it up to the next (its provisos, explanations and
    any other paragraph, table or figure); what comes before the first is a unit of its own."""
    units: list[list[Para]] = []
    for para in paras:
        if id(para) in numbered or not units:
            units.append([para])
        else:
            units[-1].append(para)
    return units


def _groups(units: list[list[Para]], least: int) -> list[list[list[Para]]]:
    """The units in order, a unit of text under `least` characters joined to the units after it until
    the group is not; the last unit of a section closes the last group."""
    groups, carried, size, plain = [], [], 0, True
    for i, unit in enumerate(units):
        text = _text_only(unit)
        plain = plain and text is not None
        if text:
            size += _size(text) + (len(BETWEEN_PARAGRAPHS) if size else 0)
        carried.append(unit)
        if plain and size < least and i + 1 < len(units):
            continue
        groups.append(carried)
        carried, size, plain = [], 0, True
    return groups


def _numbered_group(
    context: StrategyContext, group: list[list[Para]], parent: Parent, numbered: set[int]
) -> list[Piece]:
    """One group of units: whole when it fits, else packed between paragraphs, then sentences, every
    text piece that does not start with its paragraph's number given it ("4. "), and the pieces that
    hold one numbered paragraph's unit numbered `part_of` (n, m). Pieces of the text before the first
    numbered paragraph keep the numbering `pack_paras` gives them, as in `sentences`."""
    paras = [para for unit in group for para in unit]
    text = _text_only(paras)
    if text is not None and _size(text) <= context.max_chars:
        return [text_piece(context, _whole(text), parent)] if text else []
    units = [unit for unit in group if id(unit[0]) in numbered]
    tokens: dict[int, str] = {}
    for unit in units:
        if token := numbering(unit[0].text):
            tokens.update((id(para), token) for para in unit)
    width = max((len(token) + 1 for token in tokens.values()), default=0)
    if width > context.max_chars // 2:  # a number too long to repeat
        tokens, width = {}, 0
    room = context.max_chars - width
    made = join_small(context, _packed(context, paras, parent, room), max_chars=room)
    heads = {id(unit[0]) for unit in units}
    for i, piece in enumerate(made):
        para, start, _ = piece.parts[0]
        token = tokens.get(id(para))
        if piece.kind == "text" and token and not (start == 0 and id(para) in heads):
            made[i] = _prefixed(context, piece, token + " ")
    holders = [
        [
            i
            for i, piece in enumerate(made)
            if piece.kind == "text" and any(id(p) in held for p, _, _ in piece.parts)
        ]
        for held in ({id(para) for para in unit} for unit in units)
    ]
    for i in {i for found in holders for i in found}:
        made[i] = replace(made[i], part_of=None)
    for found in holders:
        for n, i in enumerate(found if len(found) > 1 else [], 1):
            if made[i].part_of is None:
                made[i] = replace(made[i], part_of=(n, len(found)))
    return made


def numbered_paragraphs(context: StrategyContext) -> list[Piece]:
    """One piece per numbered paragraph, with the paragraphs after it up to the next (its provisos and
    explanations), under its section (its innermost heading) as parent; what comes before a section's
    first numbered paragraph is packed like `sentences`. A paragraph under `min_chars` joins the next
    one in its section (the last one, the one before it, when the two fit); a long one is split
    between sentences, every piece starting with the paragraph's number."""
    numbered = {id(para) for para in context.shape.numbered}
    made: list[Piece] = []
    for section in _walk(_outline(context.paras)):
        if not section.own:
            continue
        parent = _section_parent(context, section)
        groups = _groups(_units(section.own, numbered), context.min_chars)
        last: list[Piece] = []
        for group in groups:
            last = _numbered_group(context, group, parent, numbered)
            made.extend(last)
        if len(groups) > 1 and len(last) == 1 and len(made) > 1:
            made[-2:] = join_small(context, made[-2:])
    return made


# --- sections -------------------------------------------------------------------------------------------


@dataclass
class _Held:
    """A piece of `sections` with what joining it needs: the paragraphs of its parent section
    (`scope`, indexes into the context's paragraphs) and the units it holds (a section whole, or the
    pieces of a section's own text)."""

    piece: Piece
    scope: tuple[int, int]
    units: frozenset[int]


def _common(first: tuple[str, ...], second: tuple[str, ...]) -> tuple[str, ...]:
    n = 0
    while n < min(len(first), len(second)) and first[n] == second[n]:
        n += 1
    return first[:n]


def _joined_sections(context: StrategyContext, held: list[_Held]) -> list[Piece]:
    """A text piece under `min_chars` joins the next piece, else the one before, when the two fit in
    `max_chars` and that one is a text piece of another unit, with the same parent or lying in the
    small piece's parent section; the piece made has the small piece's parent and the heading path
    the two share. One of several pieces of a split section takes in nothing from outside its parent,
    so that its pieces keep one parent. A piece that holds nothing but headings (a section with nothing
    under its heading) may still join such a piece when it lies in its parent section: the piece made
    keeps that piece's parent, heading path and `part_of`, the headings holding no text of their own."""
    index = {id(para): i for i, para in enumerate(context.paras)}
    least, limit = context.min_chars, context.max_chars

    def small(one: _Held) -> bool:
        return one.piece.kind == "text" and len(one.piece.text) < least

    def bare(one: _Held) -> bool:
        return small(one) and all(para.kind == "heading" for para, _, _ in one.piece.parts)

    def inside(one: _Held, other: _Held) -> bool:
        at = [index[id(para)] for para, _, _ in other.piece.parts]
        return one.scope[0] <= min(at) and max(at) < one.scope[1]

    def room(one: _Held, other: _Held) -> bool:
        return (
            other.piece.kind == "text"
            and not one.units & other.units
            and len(one.piece.text) + len(BETWEEN_PARAGRAPHS) + len(other.piece.text) <= limit
        )

    def fits(one: _Held, other: _Held) -> bool:
        return room(one, other) and (
            other.piece.parent == one.piece.parent or (other.piece.part_of is None and inside(one, other))
        )

    def attaches(one: _Held, other: _Held) -> bool:
        return room(one, other) and inside(one, other)

    def join(first: _Held, second: _Held, under: _Held, small_one: _Held) -> _Held:
        """The two as one piece under `under`'s parent: with the heading path they share when that is
        the small one, else with `under`'s heading path and `part_of`."""
        piece = merged(context, first.piece, second.piece)
        if under is small_one:
            path, part_of = _common(first.piece.heading_path, second.piece.heading_path), piece.part_of
        else:
            path, part_of = under.piece.heading_path, under.piece.part_of
        piece = replace(
            piece,
            parent=under.piece.parent,
            parent_cite=under.piece.parent_cite,
            heading_path=path,
            part_of=part_of,
            embedding_text=embedding(context.document, path, piece.text),
        )
        return _Held(piece, under.scope, first.units | second.units)

    waiting, kept = list(held), []
    for i, one in enumerate(waiting):
        after = waiting[i + 1] if i + 1 < len(waiting) else None
        before = kept[-1] if kept else None
        if small(one) and after and fits(one, after):
            waiting[i + 1] = join(one, after, one, one)
        elif small(one) and before and fits(one, before):
            kept[-1] = join(before, one, one, one)
        elif bare(one) and after and attaches(one, after):
            waiting[i + 1] = join(one, after, after, one)
        elif bare(one) and before and attaches(one, before):
            kept[-1] = join(before, one, before, one)
        else:
            kept.append(one)
    return [one.piece for one in kept]


def sections(context: StrategyContext) -> list[Piece]:
    """One piece per section (a heading and what follows until a heading of the same or a higher
    level), its heading at the start of its text and its heading path with it, under the section it
    was cut from as parent. A section too long for one piece is cut into its own text (split between
    paragraphs, then sentences, every piece starting with its heading) and its subsections, each a
    section in turn. A heading with nothing of its own leads into the next section under the same
    parent: its first subsection (where it stays in the heading path), else the section after it, whose
    first piece it goes in front of. A piece under `min_chars` joins its neighbour under its parent
    (`_joined_sections`)."""
    limit = context.max_chars
    units = count()
    held: list[_Held] = []
    everything = (0, len(context.paras))
    waiting = [(_outline(context.paras), document_parent(context), everything, [])]
    while waiting:
        section, parent, scope, lead = waiting.pop()
        text = _text_only(context.paras[section.start : section.end])
        if text and _size([*lead, *text]) <= limit:
            piece = text_piece(context, _whole([*lead, *text]), parent, heading_path=section.path)
            held.append(_Held(piece, scope, frozenset([next(units)])))
            continue
        own = _section_parent(context, section) if section.heading else parent
        inner = (section.start, section.end) if section.heading else scope
        if section.heading and not section.own and section.children:
            lead = [*lead, section.heading]  # nothing of its own: the heading leads into its first subsection
        else:
            unit = frozenset([next(units)])
            if section.heading:
                made = _headed(context, [*lead, section.heading], section.own, own, section.path)
            else:  # what comes before the first heading, packed like sentences
                made = join_small(context, _packed(context, section.own, own))
            held.extend(_Held(piece, inner, unit) for piece in made)
            lead = []
        planned = []
        for i, child in enumerate(section.children):
            if not child.own and not child.children and i + 1 < len(section.children):
                lead = [*lead, child.heading]  # nothing under it: it leads into the section after it
                continue
            planned.append((child, own, inner, lead))
            lead = []
        waiting.extend(reversed(planned))
    return _joined_sections(context, held)


# --- question-answer ------------------------------------------------------------------------------------


def _pair(context: StrategyContext, question: Para, answers: list[Para]) -> list[Piece]:
    """A question with its answer, its own parent: whole when it fits, else the answer packed between
    paragraphs, then sentences, every piece starting with the question."""
    paras = [question, *answers]
    parent = Parent(f"question:{question.spans[0].line.id}", citation(_lines(context, paras)))
    text = _text_only(paras)
    if text is not None and _size(text) <= context.max_chars:
        return [text_piece(context, _whole(text), parent)]
    return _headed(context, [question], answers, parent, question.heading_path)


def question_answer(context: StrategyContext) -> list[Piece]:
    """One piece per question with its answer (`_pair`); a pair never joins another. Text outside the
    pairs is packed like `sentences`, under the document."""
    document = document_parent(context)
    kept = {id(para) for para in context.paras}
    pairs: dict[int, list[Para]] = {}
    for question, answers in context.shape.qa:
        held = [answer for answer in answers if id(answer) in kept]
        if id(question) in kept and any(answer.kind == "body" for answer in held):
            pairs[id(question)] = held
    answering = {id(answer) for held in pairs.values() for answer in held}
    made: list[Piece] = []
    run: list[Para] = []

    def flush():
        made.extend(join_small(context, pack_paras(context, run, document)))
        run.clear()

    for para in context.paras:
        if id(para) in pairs:
            flush()
            made.extend(_pair(context, para, pairs[id(para)]))
        elif id(para) in answering:
            continue
        elif para.kind == "body":
            run.append(para)
        elif para.kind == "heading":
            flush()
        elif para.kind == "table":
            flush()
            made.extend(table_pieces(context, para, document))
        elif found := image_pieces(context, para, document):
            flush()
            made.extend(found)
    flush()
    return made


STRATEGY_FUNCTIONS: dict[str, Callable[[StrategyContext], list[Piece]]] = {
    "single": single,
    "question-answer": question_answer,
    "numbered-paragraphs": numbered_paragraphs,
    "sections": sections,
    "sentences": sentences,
}


def strategy_of(name: str) -> str:
    """The strategy that cuts a document planned for `name`. Until all eight are built, one not yet
    registered falls back to `sentences` (ruling R4); a name that is not a strategy is refused."""
    if name not in STRATEGIES:
        raise ValueError(f"{name!r} is not a chunking strategy; the strategies are {', '.join(STRATEGIES)}")
    return name if name in STRATEGY_FUNCTIONS else "sentences"


def pieces(context: StrategyContext) -> list[Piece]:
    return STRATEGY_FUNCTIONS[strategy_of(context.choice.strategy)](context)
