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


def read(data: bytes, name: str, config: Config) -> ReadResult:
    try:
        text, guessed = _build.decode(data)
    except UnicodeDecodeError as error:
        raise ReaderRefused("csv.invalid", f"{name} is not valid UTF-16 text: {error}") from error
    delimiter = _delimiter(text)
    try:
        rows = list(csv.reader(io.StringIO(text, newline=""), delimiter=delimiter))
    except csv.Error as error:
        raise ReaderRefused("csv.invalid", f"{name} is not valid CSV: {error}") from error
    width = max((len(r) for r in rows), default=0)
    if len(rows) * width > config.max_cells:
        raise ReaderRefused("csv.too-large", f"{name} has more than {config.max_cells} cells.")
    document = _build.start(data, name, "csv", config, {"reader:csv": READER_VERSIONS["csv"]})
    page = document.pages[0]
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
