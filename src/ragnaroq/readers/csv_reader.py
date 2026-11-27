"""CSV. One line per non-empty cell, anchored by row (1-based, the header counts) and the
column's header text, and one table for the file."""

import csv
import io

from ..anchors import CsvAnchor
from ..config import Config
from ..model import Cell, Flag, Table
from . import READER_VERSIONS, ReaderRefused, ReadResult, _build

DELIMITERS = ",;\t|"


def _delimiter(text: str) -> str:
    try:
        return csv.Sniffer().sniff(text[:65536], delimiters=DELIMITERS).delimiter
    except csv.Error:
        return ","


def _rows(text: str, delimiter: str, name: str, limit: int) -> tuple[list[list[str]], int, bool]:
    """The rows, the table's width, and whether the file was cut: whole rows are read while rows
    times width stays within `limit` (a first row wider than it is cut to `limit` cells)."""
    rows, width = [], 0
    try:
        for row in csv.reader(io.StringIO(text, newline=""), delimiter=delimiter):
            wider = max(width, len(row))
            if (len(rows) + 1) * wider > limit:
                if not rows and limit > 0:
                    rows, width = [row[:limit]], min(len(row), limit)
                return rows, width, True
            rows.append(row)
            width = wider
    except csv.Error as error:
        raise ReaderRefused("csv.invalid", f"{name} is not valid CSV: {error}") from error
    return rows, width, False


def read(data: bytes, name: str, config: Config) -> ReadResult:
    try:
        text, guessed = _build.decode(data)
    except UnicodeDecodeError as error:
        raise ReaderRefused("csv.invalid", f"{name} is not valid UTF-16 text: {error}") from error
    delimiter = _delimiter(text)
    rows, width, cut = _rows(text, delimiter, name, config.max_cells)
    document = _build.start(data, name, "csv", config, {"reader:csv": READER_VERSIONS["csv"]})
    page = document.pages[0]
    if cut:
        page.flags.append(
            Flag(
                rule="csv.line-limit",
                severity="review",
                message=f"{name} has more than {config.max_cells} cells; the rest is not read.",
            )
        )
    if guessed:
        document.flags.append(
            Flag(rule="encoding.guessed", severity="review", message="Not UTF-8; read as Windows-1252.")
        )
    if not rows:
        return ReadResult(document)
    header = []
    for i in range(width):
        label = rows[0][i] if i < len(rows[0]) and rows[0][i].strip() else f"column {i + 1}"
        while label in header:
            label += f" ({i + 1})"
        header.append(label)
    cells = []
    for r, row in enumerate(rows, 1):
        paragraph = _build.next_paragraph(page)
        for c in range(width):
            value = row[c] if c < len(row) else ""
            anchor = CsvAnchor(row=r, column=header[c])
            cells.append(Cell(row=r - 1, column=c, text=value, anchor=anchor))
            if value.strip():
                _build.add_line(
                    page, value, "csv", anchor, role="table", table_id="p1-t1", paragraph=paragraph
                )
    page.tables.append(Table(id="p1-t1", page=1, rows=len(rows), columns=width, cells=cells))
    return ReadResult(document)
