"""Chunks for a vector index, one JSON object per line (chunk format 0.4.0).

Every chunk has the same shape, so any vector database can load it as it stands: a UUID `id`, the
`text` to show, the `embedding_text` to embed, and a `metadata` object that is flat (strings,
integers, floats and booleans, never null; the four `LIST_KEYS` are non-empty lists of strings,
`[""]` when there is none) with the same keys on every chunk. A citation is in `metadata["cite"]`,
and the full anchors are in `anchors` and, as a string, in `metadata["anchors_json"]`. Index
`embedding_text`; show `text`; filter on `status`, `dates`, `date_first`/`date_last`, `periods`,
`references` and `terms`.

`to_chunks` describes the document, chooses its strategy (or takes the plan it is given), and
turns the strategy's pieces into chunks.
"""

import json
import re
import uuid
from collections import deque
from typing import TYPE_CHECKING

from ..anchors import Anchor, PdfAnchor, XlsxAnchor, cite
from ..config import Config
from ..model import SCHEMA_VERSION, Document, Line, Table
from ..semantic import find
from ..semantic.model import Chunking, DocumentMetadata
from ..semantic.paragraphs import locate
from ..semantic.rules import CHUNK_LIST_LIMIT
from . import Unit, grid_cell, grid_markdown, layout

if TYPE_CHECKING:
    from ..chunking.pieces import Piece
    from ..semantic.paragraphs import Para

NAMESPACE = uuid.uuid5(uuid.NAMESPACE_URL, "https://github.com/pTidke/ragnaroq")

METADATA_KEYS = (
    "schema_version",
    "file_id",
    "path",
    "file_type",
    "file_sha256",
    "kind",
    "status",
    "rules",
    "sources",
    "cite",
    "part",
    "part_index",
    "page_first",
    "page_last",
    "heading",
    "title",
    "chars",
    "anchors_json",
    "image_path",
    "strategy",
    "parent_id",
    "parent_cite",
    "heading_path",
    "dates",
    "date_first",
    "date_last",
    "periods",
    "references",
    "terms",
    "scripts",
    "part_of",
)
LIST_KEYS = ("dates", "periods", "references", "terms")

ANCHORS_JSON_LIMIT = 20_000  # UTF-8 bytes
TRUNCATED = "chunk.anchors-truncated"
CELL = re.compile(r"([A-Z]+)(\d+)")
REMOTE = re.compile(r"^(?:[A-Za-z][A-Za-z0-9+.\-]*:|//)")  # a URL a figure keeps, never fetched


def _dump(anchor: Anchor) -> dict:
    return anchor.model_dump(mode="json", exclude_none=True)


def _number(letters: str) -> int:
    total = 0
    for letter in letters:
        total = total * 26 + ord(letter) - 64
    return total


def _letters(number: int) -> str:
    letters = ""
    while number:
        number, rest = divmod(number - 1, 26)
        letters = chr(65 + rest) + letters
    return letters


def _bounding_range(group: list[Line]) -> XlsxAnchor | None:
    """The range covering a table's cells, or None when the lines are not all table cells of one sheet."""
    anchors = [line.anchor for line in group]
    if not group or not all(isinstance(a, XlsxAnchor) for a in anchors):
        return None
    if not all(line.table_id for line in group) or len({a.sheet for a in anchors}) != 1:
        return None
    cells = [(_number(m[1]), int(m[2])) for a in anchors for m in CELL.finditer(a.cell)]
    if not cells:
        return None
    columns, rows = [c[0] for c in cells], [c[1] for c in cells]
    first, last = (min(columns), min(rows)), (max(columns), max(rows))
    corner = [f"{_letters(column)}{row}" for column, row in (first, last)]
    return XlsxAnchor(sheet=anchors[0].sheet, cell=corner[0] if first == last else ":".join(corner))


def _groups(lines: list[Line]) -> list[list[Line]]:
    """Lines with an anchor, one list per part (page, sheet or slide), in order."""
    parts: dict[int, list[Line]] = {}
    for line in lines:
        if line.anchor is not None:
            parts.setdefault(line.page, []).append(line)
    return [parts[number] for number in sorted(parts)]


def _span(group: list[Line]) -> tuple[Anchor, Anchor]:
    bound = _bounding_range(group)
    return (bound, bound) if bound else (group[0].anchor, group[-1].anchor)


def _with_chars(anchor: dict, line: Line, chars: dict[str, tuple[int, int]]) -> dict:
    if line.id in chars:
        anchor["from_char"], anchor["to_char"] = chars[line.id]
    return anchor


def chunk_anchors(lines: list[Line], chars: dict[str, tuple[int, int]] | None = None) -> list[dict]:
    """Where a chunk came from, built from each line's anchor and never from a box.

    A PDF gives one anchor per page, with the box around that page's lines. An xlsx table gives one
    anchor, the range its cells cover. Anything else gives {"from", "to"} per part. `chars` names
    the lines the chunk holds only in part, with the `(from, to)` offsets into each one's text: a
    PDF page's anchor lists them under "chars"; a "from" or "to" anchor gets `from_char` and
    `to_char` when its line is one of them."""
    chars = chars or {}
    anchors: list[dict] = []
    for group in _groups(lines):
        first, last = _span(group)
        if isinstance(first, PdfAnchor):
            boxes = [line.anchor.box for line in group if isinstance(line.anchor, PdfAnchor)]
            anchor = {
                "page": first.page,
                "box": [
                    round(min(b[0] for b in boxes), 2),
                    round(min(b[1] for b in boxes), 2),
                    round(max(b[2] for b in boxes), 2),
                    round(max(b[3] for b in boxes), 2),
                ],
                "line_ids": [line.id for line in group],
            }
            partial = {line.id: list(chars[line.id]) for line in group if line.id in chars}
            if partial:
                anchor["chars"] = partial
            anchors.append(anchor)
        elif _bounding_range(group):
            anchors.append(_dump(first))
        else:
            anchors.append(
                {
                    "from": _with_chars(_dump(first), group[0], chars),
                    "to": _with_chars(_dump(last), group[-1], chars),
                }
            )
    return anchors


def citation(lines: list[Line]) -> str:
    """A short citation for the lines, as a chunk carries it."""
    groups = _groups(lines)
    if not groups:
        return ""
    first, last = _span(groups[0])[0], _span(groups[-1])[1]
    if isinstance(first, PdfAnchor):
        pages = sorted({line.page for group in groups for line in group})
        return f"p. {pages[0]}" if len(pages) == 1 else f"pp. {pages[0]}–{pages[-1]}"
    return cite(first) if first == last else f"{cite(first)}–{cite(last)}"


def _json(value) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _limited(anchors: list[dict]) -> tuple[str, bool]:
    """The anchors as JSON text within ANCHORS_JSON_LIMIT UTF-8 bytes, dropping whole anchors from the end."""
    text = _json(anchors)
    if len(text.encode("utf8")) <= ANCHORS_JSON_LIMIT:
        return text, False
    kept, size = 0, 2
    for anchor in anchors:
        cost = len(_json(anchor).encode("utf8")) + (1 if kept else 0)
        if size + cost > ANCHORS_JSON_LIMIT:
            break
        size += cost
        kept += 1
    return _json(anchors[:kept]), True


def _model_label(figure) -> str:
    return f"(model) {figure.model_description.strip()}" if figure.model_description else ""


def _figure_embedding(figure) -> str:
    """The description, then the model's sentence marked "(model)": only the embedding sees it."""
    return "\n".join(part for part in (figure.description.strip(), _model_label(figure)) if part)


def _part(document: Document, number: int) -> str:
    if document.source.format == "pdf":
        return f"p. {number}"
    page = next((page for page in document.pages if page.number == number), None)
    return (page.name if page and page.name else "") or ""


def _lines_of(piece: "Piece", position: dict[int, int]) -> list[tuple[Line, int, int]]:
    """The lines a piece holds, in reading order, each with the `(from, to)` range of its text the
    piece holds (two ranges of one line become the range that covers both). The lines of its text
    parts are found by `locate`; the lines of its table groups are held whole."""
    held: dict[int, tuple[Line, int, int]] = {}

    def add(line: Line, start: int, end: int):
        if id(line) in held:
            _, low, high = held[id(line)]
            start, end = min(start, low), max(end, high)
        held[id(line)] = (line, start, end)

    for para, start, end in piece.parts:
        for line, low, high in locate(para, start, end):
            add(line, low, high)
    for unit in piece.units:
        for line in unit.lines:
            add(line, 0, len(line.text))
    return sorted(held.values(), key=lambda found: position.get(id(found[0]), len(position)))


def _partial(line: Line, start: int, end: int) -> bool:
    """True when the range leaves out some of the line's words."""
    text = line.text
    return start > len(text) - len(text.lstrip()) or end < len(text.rstrip())


def _found(finder, text: str) -> list[str]:
    """The normalised values the finder finds in the text, in order and without repeats."""
    return list(dict.fromkeys(m.value for m in finder(text) if isinstance(m.value, str) and m.value))


def _listed(values: list[str]) -> list[str]:
    return values[:CHUNK_LIST_LIMIT] or [""]


def _day(iso: str) -> int:
    return int(iso.replace("-", ""))


def _chunk(
    document: Document, path: str, piece: "Piece", strategy: str, position: dict[int, int]
) -> dict | None:
    """A piece as a chunk, or None when it holds no line and no figure (nothing to cite)."""
    figure = next((unit.figure for unit in piece.units if unit.figure), None)
    located = _lines_of(piece, position)
    if not located and not figure:
        return None
    lines = [line for line, _, _ in located]
    context = [line for unit in piece.units for line in unit.context]
    chars = {line.id: (start, end) for line, start, end in located if _partial(line, start, end)}
    text = piece.text
    if figure:
        anchors = [
            {"page": figure.anchor.page, "box": list(figure.anchor.box), "line_ids": []}
            if isinstance(figure.anchor, PdfAnchor)
            else {"from": _dump(figure.anchor), "to": _dump(figure.anchor)}
        ]
        cited, numbers = cite(figure.anchor), [figure.page]
    else:
        anchors, cited = chunk_anchors(lines, chars), citation(lines)
        numbers = sorted({line.page for line in lines})
    states = {line.status for line in lines} | {unit.status for unit in piece.units}
    flags = [flag for line in [*lines, *context] for flag in line.flags]
    rules = {flag.rule for flag in flags} | {rule for unit in piece.units for rule in unit.rules}
    rules |= set(piece.rules)
    anchors_json, cut = _limited(anchors)
    if cut:
        rules.add(TRUNCATED)
    pdf = document.source.format == "pdf"
    sources = {line.source for line in lines} | set(figure.ocr_sources if figure else [])
    scripts = sorted({script for line in [*lines, *context] for script in line.scripts})
    file_id = str(uuid.uuid5(NAMESPACE, path))
    if figure:
        what = figure.id
    else:
        what = ",".join(
            f"{line.id}:{chars[line.id][0]}-{chars[line.id][1]}" if line.id in chars else line.id
            for line in lines
        )
    dates = _found(find.dates, text)
    heading = piece.heading_path[-1] if piece.heading_path else ""
    metadata = {
        "schema_version": SCHEMA_VERSION,
        "file_id": file_id,
        "path": path,
        "file_type": document.source.format,
        "file_sha256": document.source.sha256,
        "kind": piece.kind,
        "status": "blocked" if "blocked" in states else "flagged" if "flagged" in states else "accepted",
        "rules": ",".join(sorted(rules)),
        "sources": ",".join(sorted(sources)),
        "cite": f"{path}, {cited}" if cited else path,
        "part": _part(document, numbers[0]) if numbers else "",
        "part_index": numbers[0] if numbers else 0,
        "page_first": numbers[0] if pdf and numbers else 0,
        "page_last": numbers[-1] if pdf and numbers else 0,
        "heading": heading,
        "title": document.title or "",
        "chars": len(text),
        "anchors_json": anchors_json,
        "image_path": figure.path if figure and not REMOTE.match(figure.path) else "",  # a saved file only
        "strategy": strategy,
        "parent_id": str(uuid.uuid5(NAMESPACE, f"{file_id}|parent|{piece.parent}")),
        "parent_cite": f"{path}, {piece.parent_cite}" if piece.parent_cite else path,
        "heading_path": " › ".join(piece.heading_path),
        "dates": _listed(dates),
        "date_first": _day(min(dates)) if dates else 0,  # ISO dates sort as the days they name
        "date_last": _day(max(dates)) if dates else 0,
        "periods": _listed(_found(find.periods, text)),
        "references": _listed(_found(find.references, text)),
        "terms": _listed(list(dict.fromkeys(piece.terms))),
        "scripts": "+".join(scripts),
        "part_of": f"{piece.part_of[0]}/{piece.part_of[1]}" if piece.part_of else "",
    }
    assert tuple(metadata) == METADATA_KEYS
    return {
        "id": str(uuid.uuid5(NAMESPACE, f"{file_id}|{document.source.sha256}|{what}")),
        "text": text,
        "embedding_text": piece.embedding_text,
        "metadata": metadata,
        "anchors": anchors,
    }


def _key(anchor: Anchor | None) -> str:
    return anchor.model_dump_json() if anchor is not None else ""


def _line_rows(unit: Unit) -> dict[int, list[Line]]:
    """The table's lines by the row of the cell each belongs to (0 is the header row). A line is
    matched to a cell by anchor, in order: two cells may share one (a Markdown row, a JSON header
    and its first value), and several lines one cell (Word's paragraphs and nested tables)."""
    waiting: dict[str, deque[int]] = {}
    for cell in unit.table.cells:
        waiting.setdefault(_key(cell.anchor), deque()).append(cell.row)
    last: dict[str, int] = {}
    rows: dict[int, list[Line]] = {}
    row = 0
    for line in unit.lines:
        key = _key(line.anchor)
        if waiting.get(key):
            row = last[key] = waiting[key].popleft()
        elif key in last:
            row = last[key]
        rows.setdefault(row, []).append(line)  # a line no cell names stays with the line before it
    return rows


def _row_chars(widths: dict[int, int], columns: int) -> int:
    """The length of "| a | b |" in a grid of `columns`, from the lengths of the cells it holds."""
    return 4 + sum(widths.values()) + 3 * (columns - 1) if columns else 2


def _table_groups(unit: Unit, limit: int) -> list[Unit]:
    """A table of a file that is not a PDF, as groups of rows whose Markdown fits in `limit`, each
    with the header row (row 1) above its rows. The header's lines belong to the first group and
    are context to the others; a row that alone is over the limit is a group of its own. Only the
    rows and columns that hold a cell are laid out (`layout`), so the work follows the cells, not
    the span of a sheet; every group is laid out in the whole table's columns."""
    table = unit.table
    if len(unit.text) <= limit:
        return [unit]
    lines = _line_rows(unit)
    shown, places = layout(table)
    columns = len(places)
    cells: dict[int, list] = {}
    for cell in table.cells:
        cells.setdefault(cell.row, []).append(cell.model_copy(update={"column": places[cell.column]}))
    sizes = {}
    for row, members in cells.items():
        # of two cells at one place, the last shows
        sizes[row] = _row_chars({cell.column: len(grid_cell(cell.text)) for cell in members}, columns)
    base = sizes.get(0, _row_chars({}, columns)) + 1 + 1 + 6 * columns  # the header, then "| --- |…"
    groups: deque[list[int]] = deque()
    current, size = [], base
    for row in shown[1:]:
        cost = 1 + sizes.get(row, _row_chars({}, columns))  # a row is empty only in a table with no cells
        if current and size + cost > limit:
            groups.append(current)
            current, size = [], base
        current.append(row)
        size += cost
    groups.append(current)
    header, heads = cells.get(0, []), lines.get(0, [])
    status = table.status if table.flags else "accepted"  # an exact table's status is its lines'
    made: list[Unit] = []
    while groups:
        rows = groups.popleft()
        moved = [c.model_copy(update={"row": i}) for i, row in enumerate(rows, 1) for c in cells.get(row, [])]
        text = grid_markdown(len(rows) + 1, columns, [*header, *moved])
        if len(rows) > 1 and len(text) > limit:  # a grid too large is written cell by cell
            groups.extendleft([row] for row in reversed(rows))
            continue
        grid = Table(
            id=table.id,
            page=table.page,
            rows=len(rows) + 1,
            columns=columns,
            cells=[*header, *moved],
            status=status,
            flags=table.flags,
        )
        own = [line for row in rows for line in lines.get(row, [])]
        if not made:
            own = [*heads, *own]
        if own:  # rows with no text have nothing to show or cite
            made.append(Unit("table", unit.page, own, grid, context=heads if made else [], markdown=text))
    return made


def _kept(paras: list["Para"], include: tuple[str, ...], pdf: bool) -> list["Para"]:
    """The paragraphs of a status in `include` (a table of a file that is not a PDF is kept: its
    groups of rows are decided alone), with each heading path made again from the headings kept."""
    kept = [p for p in paras if p.status in include or (p.kind == "table" and p.table and not pdf)]
    stack: list[tuple[int, str]] = []
    for para in kept:
        if para.kind == "heading":
            while stack and stack[-1][0] >= para.level:
                stack.pop()
            stack.append((para.level, para.text))
        para.heading_path = tuple(text for _, text in stack)
    return kept


def to_chunks(
    document: Document,
    config: Config,
    include: tuple[str, ...] = ("accepted", "flagged"),
    *,
    path: str | None = None,
    choice: Chunking | None = None,
    meta: DocumentMetadata | None = None,
) -> list[dict]:
    """The document's chunks, cut by the strategy of `choice`; without one, by the plan in `meta`,
    else by the rules over `meta` (described here when not given). Lines of a status not in
    `include` are left out with the paragraph or group of rows that holds them."""
    # imported here: these modules import this one (citation, _table_groups)
    from ..chunking.pieces import StrategyContext
    from ..chunking.strategies import pieces, strategy_of
    from ..semantic.describe import describe
    from ..semantic.paragraphs import paragraphs
    from ..semantic.rules import choose
    from ..semantic.shape import shape

    path = path or document.source.filename
    if choice is None:
        meta = meta or describe(document, path=path)
        choice = meta.chunking or choose(meta, config)
    elif meta is None:
        meta = describe(document, path=path)
    strategy = strategy_of(choice.strategy)
    paras = paragraphs(document)
    found = shape(paras, document)
    kept = _kept(paras, include, document.source.format == "pdf")
    context = StrategyContext(document, kept, found, meta, choice, config, include)
    position = {id(line): i for i, line in enumerate(document.lines())}
    chunks = [_chunk(document, path, piece, strategy, position) for piece in pieces(context)]
    return [chunk for chunk in chunks if chunk and chunk["metadata"]["status"] in include]


def to_jsonl(chunks: list[dict]) -> str:
    return "".join(json.dumps(chunk, ensure_ascii=False) + "\n" for chunk in chunks)
