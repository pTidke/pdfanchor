"""Chunks for a vector index, one JSON object per line.

Every chunk has the same shape, so any vector database can load it as it stands: a UUID `id`, the
`text` to show, the `embedding_text` to embed, and a `metadata` object that is flat (strings,
integers, floats and booleans only, never null) with the same keys on every chunk. A citation is
in `metadata["cite"]`, and the full anchors are in `anchors` and, as a string, in
`metadata["anchors_json"]`. Index `embedding_text`; show `text`; filter on `status`.
"""

import json
import re
import uuid
from collections import deque

from ..anchors import Anchor, PdfAnchor, XlsxAnchor, cite
from ..config import Config
from ..model import SCHEMA_VERSION, Document, Line, Table
from . import Unit, grid_cell, table_markdown, units

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
)

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


def chunk_anchors(lines: list[Line]) -> list[dict]:
    """Where a chunk came from, built from each line's anchor and never from a box.

    A PDF gives one anchor per page, with the box around that page's lines. An xlsx table gives one
    anchor, the range its cells cover. Anything else gives {"from", "to"} per part."""
    anchors: list[dict] = []
    for group in _groups(lines):
        first, last = _span(group)
        if isinstance(first, PdfAnchor):
            boxes = [line.anchor.box for line in group if isinstance(line.anchor, PdfAnchor)]
            anchors.append(
                {
                    "page": first.page,
                    "box": [
                        round(min(b[0] for b in boxes), 2),
                        round(min(b[1] for b in boxes), 2),
                        round(max(b[2] for b in boxes), 2),
                        round(max(b[3] for b in boxes), 2),
                    ],
                    "line_ids": [line.id for line in group],
                }
            )
        elif _bounding_range(group):
            anchors.append(_dump(first))
        else:
            anchors.append({"from": _dump(first), "to": _dump(last)})
    return anchors


def _citation(lines: list[Line]) -> str:
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


def _chunk(document: Document, path: str, heading: str | None, members: list[Unit]) -> dict:
    figure = members[0].figure
    lines = [line for unit in members for line in unit.lines]
    text = figure.description.strip() if figure else "\n\n".join(unit.text for unit in members if unit.text)
    context = [part for part in (document.title, heading) if part]
    if len(context) == 2 and context[0] == context[1]:
        context.pop()
    if figure:
        anchors = [
            {"page": figure.anchor.page, "box": list(figure.anchor.box), "line_ids": []}
            if isinstance(figure.anchor, PdfAnchor)
            else {"from": _dump(figure.anchor), "to": _dump(figure.anchor)}
        ]
        citation, numbers = cite(figure.anchor), [figure.page]
    else:
        anchors, citation = chunk_anchors(lines), _citation(lines)
        numbers = sorted({line.page for line in lines})
    states = {unit.status for unit in members}
    rules = sorted({rule for unit in members for rule in unit.rules})
    anchors_json, cut = _limited(anchors)
    if cut:
        rules = sorted({*rules, TRUNCATED})
    body = _figure_embedding(figure) if figure else text
    pdf = document.source.format == "pdf"
    sources = {line.source for line in lines} | set(figure.ocr_sources if figure else [])
    kind = "image" if figure else "table" if members[0].kind == "table" else "text"
    file_id = str(uuid.uuid5(NAMESPACE, path))
    what = figure.id if figure else ",".join(line.id for line in lines)
    metadata = {
        "schema_version": SCHEMA_VERSION,
        "file_id": file_id,
        "path": path,
        "file_type": document.source.format,
        "file_sha256": document.source.sha256,
        "kind": kind,
        "status": "blocked" if "blocked" in states else "flagged" if "flagged" in states else "accepted",
        "rules": ",".join(rules),
        "sources": ",".join(sorted(sources)),
        "cite": f"{path}, {citation}" if citation else path,
        "part": _part(document, numbers[0]) if numbers else "",
        "part_index": numbers[0] if numbers else 0,
        "page_first": numbers[0] if pdf and numbers else 0,
        "page_last": numbers[-1] if pdf and numbers else 0,
        "heading": heading or "",
        "title": document.title or "",
        "chars": len(text),
        "anchors_json": anchors_json,
        "image_path": figure.path if figure and not REMOTE.match(figure.path) else "",  # a saved file only
    }
    assert tuple(metadata) == METADATA_KEYS
    return {
        "id": str(uuid.uuid5(NAMESPACE, f"{file_id}|{document.source.sha256}|{what}")),
        "text": text,
        "embedding_text": "\n".join([" > ".join(context), body]) if context else body,
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


def _row_chars(widths: list[int]) -> int:
    """The length of "| a | b |", from the cells' lengths."""
    return 4 + sum(widths) + 3 * (len(widths) - 1) if widths else 2


def _table_groups(unit: Unit, limit: int) -> list[Unit]:
    """A table of a file that is not a PDF, as groups of rows whose Markdown fits in `limit`, each
    with the header row (row 1) above its rows. The header's lines belong to the first group and
    are context to the others; a row that alone is over the limit is a group of its own."""
    table = unit.table
    if len(unit.text) <= limit:
        return [unit]
    lines = _line_rows(unit)
    cells: dict[int, list] = {}
    for cell in table.cells:
        cells.setdefault(cell.row, []).append(cell)
    empty = [0] * table.columns
    sizes = {}
    for row, members in cells.items():
        widths = list(empty)
        for cell in members:
            widths[cell.column] = len(grid_cell(cell.text))
        sizes[row] = _row_chars(widths)
    base = sizes.get(0, _row_chars(empty)) + 1 + len("|" + " --- |" * table.columns)
    groups: deque[list[int]] = deque()
    current, size = [], base
    for row in range(1, table.rows):
        cost = 1 + sizes.get(row, _row_chars(empty))
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
        grid = Table(
            id=table.id,
            page=table.page,
            rows=len(rows) + 1,
            columns=table.columns,
            cells=[*header, *moved],
            status=status,
            flags=table.flags,
        )
        if len(rows) > 1 and len(table_markdown(grid)) > limit:  # a grid too large is written cell by cell
            groups.extendleft([row] for row in reversed(rows))
            continue
        own = [line for row in rows for line in lines.get(row, [])]
        if not made:
            own = [*heads, *own]
        if own:  # rows with no text have nothing to show or cite
            made.append(Unit("table", unit.page, own, grid, context=heads if made else []))
    return made


def to_chunks(
    document: Document,
    config: Config,
    include: tuple[str, ...] = ("accepted", "flagged"),
    *,
    path: str | None = None,
) -> list[dict]:
    path = path or document.source.filename
    chunks: list[dict] = []
    heading: str | None = None
    pending: list[Unit] = []

    def flush():
        nonlocal pending
        if pending:
            chunks.append(_chunk(document, path, heading, pending))
            pending = []

    for unit in units(document):
        if unit.kind == "table" and unit.table is not None and document.source.format != "pdf":
            if unit.text:  # cut into groups of rows, each decided alone; a PDF table stays one chunk
                flush()
                groups = _table_groups(unit, config.chunk_chars)
                chunks += [_chunk(document, path, heading, [g]) for g in groups if g.status in include]
            continue
        if unit.status not in include:
            continue
        if unit.figure:
            if unit.figure.description.strip():
                flush()
                chunks.append(_chunk(document, path, heading, [unit]))
            continue
        if not unit.text:
            continue
        if unit.kind == "heading":
            flush()
            heading = unit.text
        elif unit.kind == "table":
            flush()
            chunks.append(_chunk(document, path, heading, [unit]))
        else:
            size = sum(len(member.text) for member in pending)
            if pending and size + len(unit.text) > config.chunk_chars:
                flush()
            pending.append(unit)
    flush()
    return chunks


def to_jsonl(chunks: list[dict]) -> str:
    return "".join(json.dumps(chunk, ensure_ascii=False) + "\n" for chunk in chunks)
