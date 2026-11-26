"""JSON. A key whose value is an object or array is a heading; a scalar is `key: value` at its
RFC 6901 pointer; an array of objects with the same keys is one table."""

import json
import re
from decimal import Decimal

from ..anchors import JsonAnchor
from ..config import Config
from ..model import Cell, Flag, Table
from . import READER_VERSIONS, ReaderRefused, ReadResult, _build

_TOKENS = re.compile(r'"(?:[^"\\]|\\.)*"|[\[\]{}]', re.DOTALL)


def _pointer(parts: tuple) -> str:
    return "".join("/" + str(p).replace("~", "~0").replace("/", "~1") for p in parts)


def _check_depth(text: str, limit: int) -> None:
    depth = 0
    for token in _TOKENS.finditer(text):
        char = token.group()
        if char in "[{":
            depth += 1
            if depth > limit:
                raise ReaderRefused("json.too-deep", f"JSON is nested more than {limit} levels deep.")
        elif char in "]}":
            depth -= 1


def _shown(value) -> str:
    if value is True:
        return "true"
    if value is False:
        return "false"
    if value is None:
        return "null"
    return str(value)


def _is_table(value) -> bool:
    if not isinstance(value, list) or not value or not all(isinstance(r, dict) for r in value):
        return False
    keys = set(value[0])
    return bool(keys) and all(set(r) == keys for r in value) and not any(
        isinstance(v, dict | list) for r in value for v in r.values()
    )


def read(data: bytes, name: str, config: Config) -> ReadResult:
    try:
        text = data.decode(json.detect_encoding(data)).lstrip("﻿")
    except UnicodeDecodeError as error:
        raise ReaderRefused("json.invalid", f"{name} is not valid text: {error}") from error
    _check_depth(text, config.max_depth)
    duplicates: list[str] = []

    def pairs(items):
        seen = set()
        for k, _ in items:
            if k in seen:
                duplicates.append(k)
            seen.add(k)
        return dict(items)

    try:
        root = json.loads(text, object_pairs_hook=pairs, parse_float=Decimal, parse_constant=str)
    except (json.JSONDecodeError, RecursionError) as error:
        raise ReaderRefused("json.invalid", f"{name} is not valid JSON: {error}") from error

    document = _build.start(data, name, "json", config, {"reader:json": READER_VERSIONS["json"]})
    page = document.pages[0]
    tables = 0

    def add(text, parts, role="body", **kw):
        if len(page.lines) >= config.max_cells:
            raise ReaderRefused("json.too-large", f"{name} has more than {config.max_cells} values.")
        return _build.add_line(page, text, "json", JsonAnchor(pointer=_pointer(parts)), role=role, **kw)

    def table(rows, parts):
        nonlocal tables
        tables += 1
        table_id = f"p1-t{tables}"
        columns = list(rows[0])
        cells = []
        paragraph = max((line.paragraph for line in page.lines), default=0)
        for c, column in enumerate(columns):  # a header has no pointer of its own: it takes its first value's
            add(column, (*parts, 0, column), "table", table_id=table_id, paragraph=paragraph + 1)
            anchor = JsonAnchor(pointer=_pointer((*parts, 0, column)))
            cells.append(Cell(row=0, column=c, text=column, anchor=anchor))
        for r, record in enumerate(rows):
            for c, column in enumerate(columns):
                cell = (*parts, r, column)
                add(_shown(record[column]), cell, "table", table_id=table_id, paragraph=paragraph + 2 + r)
                cells.append(Cell(row=r + 1, column=c, text=_shown(record[column]),
                                  anchor=JsonAnchor(pointer=_pointer(cell))))
        page.tables.append(
            Table(id=table_id, page=1, rows=len(rows) + 1, columns=len(columns), cells=cells)
        )

    # An explicit stack keeps deep, valid JSON from exhausting Python's call stack.
    stack = [(root, (), None)]
    while stack:
        value, parts, key = stack.pop()
        if isinstance(value, dict | list) and key is not None:
            add(str(key), parts, "heading")
        if isinstance(value, dict):
            children = [(v, (*parts, k), k) for k, v in value.items()]
        elif isinstance(value, list):
            if _is_table(value):
                table(value, parts)
                continue
            children = [(v, (*parts, i), None) for i, v in enumerate(value)]
        else:
            add(_shown(value) if key is None else f"{key}: {_shown(value)}", parts)
            continue
        stack.extend(reversed(children))
    if duplicates:
        shown = ", ".join(sorted(set(duplicates))[:5])
        document.flags.append(
            Flag(
                rule="json.duplicate-key",
                severity="review",
                message=f"Repeated keys, the last kept: {shown}.",
            )
        )
    return ReadResult(document)
