"""The metadata entry of one document: grounded dates, periods, amounts, references and file
mentions, the structure counts, length, scripts and trust. Values come from lines that are not
excluded; each names the lines it came from."""

import unicodedata
from collections.abc import Callable
from dataclasses import dataclass

from ..export.chunks import citation
from ..model import Document, Flag, Line, Status
from ..structure import SCRIPTS
from . import find
from .model import (
    DocumentMetadata,
    Grounded,
    GroundedDefinition,
    Length,
    ScriptShare,
    StructureCounts,
    Trust,
)
from .paragraphs import Para, locate, paragraphs
from .rules import DOC_LIST_LIMIT, kind_of, table_share
from .shape import Definition, shape

_RANK: dict[Status, int] = {"blocked": 0, "flagged": 1, "accepted": 2}
_Resolve = Callable[[int, int], list[tuple[Line, int, int]]]


@dataclass
class _Segment:
    text: str
    resolve: _Resolve


def _lowest(lines: list[Line]) -> Status:
    return min((line.status for line in lines), key=_RANK.__getitem__, default="accepted")


def _grounded(text: str, value: str | dict, lines: list[Line]) -> Grounded:
    return Grounded(
        value=value,
        text=text,
        line_ids=[line.id for line in lines],
        cite=citation(lines),
        status=_lowest(lines),
    )


def _table_lines(document: Document) -> dict[str, list[Line]]:
    found: dict[str, list[Line]] = {}
    for line in document.lines():
        if line.role == "table" and line.table_id and not line.excluded and line.text.strip():
            found.setdefault(line.table_id, []).append(line)
    return found


def _segments(paras: list[Para], tables: dict[str, list[Line]]) -> list[_Segment]:
    """The strings the finders run over: each heading and body paragraph, and each table cell line."""
    found = []
    for para in paras:
        if para.kind in ("heading", "body"):
            found.append(_Segment(para.text, lambda s, e, para=para: locate(para, s, e)))
        elif para.kind == "table" and para.table:
            for line in tables.get(para.table.id, []):
                found.append(_Segment(line.text, lambda s, e, line=line: [(line, s, e)]))
    return found


def _find(segments: list[_Segment], finder) -> list[Grounded]:
    found = []
    for segment in segments:
        for match in finder(segment.text):
            lines = [line for line, _, _ in segment.resolve(match.start, match.end)]
            if lines:
                found.append(_grounded(match.text, match.value, lines))
    return found


def mentions(document: Document) -> list[Grounded]:
    """File-name candidates, each with `value` "": the folder decides which ones name a file."""
    segments = _segments(paragraphs(document), _table_lines(document))
    return _find(segments, find.file_mentions)


def _row_lines(para: Para, tables: dict[str, list[Line]]) -> dict[tuple[int, int], Line]:
    """The line of each cell of a table, found by reading the lines and cells in order."""
    lines = tables.get(para.table.id, []) if para.table else []
    found, cursor = {}, 0
    for cell in para.table.cells if para.table else []:
        text = (cell.text or "").strip()
        for k in range(cursor, len(lines)):
            if lines[k].text.strip() == text:
                found[(cell.row, cell.column)] = lines[k]
                cursor = k + 1
                break
    return found


def _definition(
    definition: Definition, cells: dict[int, dict[tuple[int, int], Line]]
) -> GroundedDefinition | None:
    para = definition.para
    if para.kind == "table":
        row = para.text.count("\n", 0, definition.start) - 1
        lines = [line for (r, _), line in sorted(cells[id(para)].items()) if r == row]
        text = " ".join(line.text.strip() for line in lines)
    else:
        lines = [line for line, _, _ in locate(para, definition.start, definition.end)]
        text = para.text[definition.start : definition.end]
    if not lines:
        return None
    base = _grounded(text, "", lines)
    return GroundedDefinition(**base.model_dump(), term=definition.term)


def _title(document: Document, paras: list[Para]) -> Grounded:
    heading = next((p for p in paras if p.kind == "heading"), None)
    if heading:
        lines = [line for line, _, _ in locate(heading, 0, len(heading.text))]
        return _grounded(heading.text, "", lines)
    first = next((line for line in document.lines() if not line.excluded and line.text.strip()), None)
    if first:
        return _grounded(first.text.strip(), "", [first])
    return Grounded(text=document.source.filename, line_ids=[], cite="", status="accepted")


def _letters(lines: list[Line]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for line in lines:
        for ch in line.text:
            if ch.isalpha():
                name = unicodedata.name(ch, "").split(" ")[0]
                script = SCRIPTS.get(name, name.title()[:4] or "Zzzz")
                counts[script] = counts.get(script, 0) + 1
    return counts


def _scripts(document: Document) -> list[ScriptShare]:
    lines = list(document.lines())
    kept = _letters([line for line in lines if not line.excluded])
    every = _letters(lines)
    total_kept, total = sum(kept.values()), sum(every.values())
    shares = [ScriptShare(script=s, share=round(n / total_kept, 4), excluded=False) for s, n in kept.items()]
    shares += [
        ScriptShare(script=s, share=round(n / total, 4), excluded=True)
        for s, n in every.items()
        if s not in kept
    ]
    return sorted(shares, key=lambda share: share.script)


def _trust(document: Document) -> Trust:
    lines = [line for line in document.lines() if not line.excluded]
    if not lines:
        return Trust(accepted=0.0, flagged=0.0, blocked=0.0)
    share = {s: round(sum(1 for line in lines if line.status == s) / len(lines), 4) for s in _RANK}
    return Trust(accepted=share["accepted"], flagged=share["flagged"], blocked=share["blocked"])


def describe(document: Document, *, path: str | None = None) -> DocumentMetadata:
    paras = paragraphs(document)
    structure = shape(paras, document)
    tables = _table_lines(document)
    segments = _segments(paras, tables)
    cells = {id(p): _row_lines(p, tables) for p in paras if p.kind == "table"}
    lists = {
        "dates": _find(segments, find.dates),
        "periods": _find(segments, find.periods),
        "amounts": _find(segments, find.amounts),
        "references": _find(segments, find.references),
        "definitions": [d for d in (_definition(d, cells) for d in structure.definitions) if d],
    }
    flags = []
    for name, values in lists.items():
        if len(values) > DOC_LIST_LIMIT:
            lists[name] = values[:DOC_LIST_LIMIT]
            flags.append(
                Flag(
                    rule="metadata.list-limit",
                    severity="review",
                    message=f"{name} holds {len(values)} values; the first {DOC_LIST_LIMIT} are kept.",
                )
            )
    counts = StructureCounts(
        headings=structure.headings,
        heading_depth=structure.heading_depth,
        numbered_paragraphs=len(structure.numbered),
        provisos=len(structure.provisos),
        explanations=len(structure.explanations),
        qa_pairs=len(structure.qa),
        definitions=lists["definitions"],
        tables=structure.tables,
        table_share=round(table_share(structure), 4),
        figures=structure.figures,
        slides=structure.slides,
        records=len(structure.records),
    )
    return DocumentMetadata(
        path=path if path is not None else document.source.filename,
        format=document.source.format,
        sha256=document.source.sha256,
        title=_title(document, paras),
        kind=kind_of(structure, document),
        dates=lists["dates"],
        periods=lists["periods"],
        amounts=lists["amounts"],
        references=lists["references"],
        structure=counts,
        length=Length(chars=structure.chars, parts=max(len(document.pages), 1)),
        scripts=_scripts(document),
        trust=_trust(document),
        flags=flags,
    )
