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

from ..anchors import Anchor, PdfAnchor, XlsxAnchor, cite
from ..config import Config
from ..model import SCHEMA_VERSION, Document, Line
from . import Unit, units

NAMESPACE = uuid.UUID("5d0c2b1e-7a64-4d3b-9f0e-2a8c6f1b4e37")

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

ANCHORS_JSON_LIMIT = 20_000
TRUNCATED = "chunk.anchors-truncated"
CELL = re.compile(r"([A-Z]+)(\d+)")


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


def _limited(anchors: list[dict]) -> tuple[str, bool]:
    """The anchors as JSON text within ANCHORS_JSON_LIMIT characters, dropping whole anchors from the end."""
    text = json.dumps(anchors, ensure_ascii=False, separators=(",", ":"))
    if len(text) <= ANCHORS_JSON_LIMIT:
        return text, False
    kept, size = 0, 2
    for anchor in anchors:
        cost = len(json.dumps(anchor, ensure_ascii=False, separators=(",", ":"))) + (1 if kept else 0)
        if size + cost > ANCHORS_JSON_LIMIT:
            break
        size += cost
        kept += 1
    return json.dumps(anchors[:kept], ensure_ascii=False, separators=(",", ":")), True


def _figure_text(unit: Unit) -> str:
    figure = unit.figure
    parts = [figure.description.strip()]
    if figure.model_description:
        parts.append(f"(model) {figure.model_description.strip()}")
    return "\n".join(part for part in parts if part)


def _part(document: Document, number: int) -> str:
    if document.source.format == "pdf":
        return f"p. {number}"
    page = next((page for page in document.pages if page.number == number), None)
    return (page.name if page and page.name else "") or ""


def _chunk(document: Document, path: str, heading: str | None, members: list[Unit]) -> dict:
    figure = members[0].figure
    lines = [line for unit in members for line in unit.lines]
    text = _figure_text(members[0]) if figure else "\n\n".join(unit.text for unit in members if unit.text)
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
    pdf = document.source.format == "pdf"
    kind = "image" if figure else "table" if members[0].kind == "table" else "text"
    what = figure.id if figure else ",".join(line.id for line in lines)
    identity = f"{document.source.sha256}|{what}"
    metadata = {
        "schema_version": SCHEMA_VERSION,
        "file_id": str(uuid.uuid5(NAMESPACE, path)),
        "path": path,
        "file_type": document.source.format,
        "file_sha256": document.source.sha256,
        "kind": kind,
        "status": "blocked" if "blocked" in states else "flagged" if "flagged" in states else "accepted",
        "rules": ",".join(rules),
        "sources": ",".join(sorted({line.source for line in lines})),
        "cite": f"{path}, {citation}" if citation else path,
        "part": _part(document, numbers[0]) if numbers else "",
        "part_index": numbers[0] if numbers else 0,
        "page_first": numbers[0] if pdf and numbers else 0,
        "page_last": numbers[-1] if pdf and numbers else 0,
        "heading": heading or "",
        "title": document.title or "",
        "chars": len(text),
        "anchors_json": anchors_json,
        "image_path": figure.path if figure else "",
    }
    assert tuple(metadata) == METADATA_KEYS
    return {
        "id": str(uuid.uuid5(NAMESPACE, kind + "|" + identity)),
        "text": text,
        "embedding_text": "\n".join([" > ".join(context), text]) if context else text,
        "metadata": metadata,
        "anchors": anchors,
    }


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
        if unit.status not in include:
            continue
        if unit.figure:
            if _figure_text(unit):
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
