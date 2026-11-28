"""The chunking strategies (spec section 3), each a pure function of a document and its plan,
registered by name in `STRATEGY_FUNCTIONS`. A table in a document of any strategy is cut by
`table_pieces`, a figure by `image_pieces`."""

from collections.abc import Callable

from ..semantic.paragraphs import Para
from ..semantic.rules import STRATEGIES
from .pieces import (
    Piece,
    StrategyContext,
    document_parent,
    embedding,
    image_pieces,
    join_small,
    pack_paras,
    table_pieces,
)


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


STRATEGY_FUNCTIONS: dict[str, Callable[[StrategyContext], list[Piece]]] = {
    "single": single,
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
