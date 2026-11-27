"""Paragraphs with the lines and characters they come from."""

import re
from dataclasses import dataclass, field
from typing import Literal

from ..export import Unit, table_markdown, units
from ..model import Document, Figure, Line, Status, Table
from ..structure import levels

_NUMBER = re.compile(
    r"""(?:
        (?P<dotted>\d{1,3}(?:\.\d{1,3})*\.?)
      | \(\d{1,3}\) | \d{1,3}\)
      | \([a-z]\) | [a-z]\)
      | \([ivxlcdm]{1,6}\) | [ivxlcdm]{1,6}\)
      | [IVXLC]{1,6}\.
    )(?=\s|$)""",
    re.VERBOSE,
)
_LABELLED = re.compile(r"(?:Article|Section|Clause)\s+\d{1,3}(?:\.\d{1,3})*\.?(?![0-9A-Za-z])")
_BULLETS = "•◦▪-*–"


def numbering(text: str) -> str | None:
    """The paragraph's leading number token ("1.", "1.1", "(1)", "1)", "(a)", "(i)", "I.",
    "Article 5", "Section 3.", "Clause 4"), or None."""
    text = text.lstrip()
    if match := _LABELLED.match(text):
        return match.group()
    match = _NUMBER.match(text)
    if match and (not match.group("dotted") or "." in match.group()):
        return match.group()
    return None


def _marked(text: str) -> bool:
    return (len(text) > 1 and text[0] in _BULLETS and text[1].isspace()) or numbering(text) is not None


@dataclass(frozen=True)
class Span:
    """`line.text[start:end]` sits at `offset` in the paragraph's text."""

    line: Line
    start: int
    end: int
    offset: int


@dataclass
class Para:
    kind: Literal["heading", "body", "table", "image"]
    page: int
    text: str
    spans: list[Span] = field(default_factory=list)
    breaks: tuple[int, ...] = ()
    level: int = 0
    heading_path: tuple[str, ...] = ()
    table: Table | None = None
    figure: Figure | None = None
    status: Status = "accepted"


def _spans(unit: Unit) -> tuple[str, list[Span]]:
    spans, parts, offset = [], [], 0
    for line in unit.lines:
        text = line.text
        stripped = text.strip()
        if line.excluded or not stripped:
            continue
        start = len(text) - len(text.lstrip())
        spans.append(Span(line, start, start + len(stripped), offset))
        parts.append(stripped)
        offset += len(stripped) + 1
    return " ".join(parts), spans


def paragraphs(document: Document) -> list[Para]:
    """One Para per unit, in reading order; heading levels are set first."""
    levels(document)
    result: list[Para] = []
    stack: list[tuple[int, str]] = []
    for unit in units(document):
        spans: list[Span] = []
        level = 0
        if unit.kind == "table" and unit.table:
            text = table_markdown(unit.table)
        elif unit.kind == "image":
            text = unit.figure.description if unit.figure else ""
        else:
            text, spans = _spans(unit)
            if not spans:
                continue
        kind = unit.kind if unit.kind in ("heading", "table", "image") else "body"
        if kind == "heading":
            level = max(unit.lines[0].level, 1)
            while stack and stack[-1][0] >= level:
                stack.pop()
            stack.append((level, text))
        breaks = tuple(s.offset for s in spans if kind == "body" and _marked(s.line.text.strip()))
        result.append(
            Para(
                kind,
                unit.page,
                text,
                spans,
                breaks,
                level,
                tuple(t for _, t in stack),
                unit.table,
                unit.figure,
                unit.status,
            )
        )
    return result


def locate(para: Para, start: int, end: int) -> list[tuple[Line, int, int]]:
    """The lines and `(from_char, to_char)` offsets into each line's text that cover
    `para.text[start:end]`; lines the range does not touch are skipped."""
    found = []
    for span in para.spans:
        top = span.offset + (span.end - span.start)
        low, high = max(start, span.offset), min(end, top)
        if low < high:
            found.append((span.line, span.start + low - span.offset, span.start + high - span.offset))
    return found
