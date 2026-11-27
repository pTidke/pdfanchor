"""Turn a Document into files a language model or a vector index can use."""

from collections.abc import Iterator
from dataclasses import dataclass

from ..model import Document, Figure, Line, Table


@dataclass
class Unit:
    """One heading, paragraph or table in reading order."""

    kind: str
    page: int
    lines: list[Line]
    table: Table | None = None
    figure: Figure | None = None

    @property
    def text(self) -> str:
        if self.figure:
            return ""  # a figure is shown by the Markdown export, not read as text
        if self.table:
            return table_markdown(self.table)
        return " ".join(line.text.strip() for line in self.lines if line.text.strip())

    @property
    def status(self) -> str:
        states = {line.status for line in self.lines} | ({self.table.status} if self.table else set())
        if self.figure:
            states.add(self.figure.status)
        return "blocked" if "blocked" in states else "flagged" if "flagged" in states else "accepted"

    @property
    def rules(self) -> list[str]:
        flags = [flag for line in self.lines for flag in line.flags]
        flags += self.table.flags if self.table else []
        flags += self.figure.flags if self.figure else []
        return sorted({flag.rule for flag in flags})


MAX_GRID = 100_000  # a table with more cells than this is not laid out as a grid


def table_markdown(table: Table, escape=lambda text: text) -> str:
    """The table as a Markdown grid. `escape` is applied to each cell's words (the Markdown
    file escapes them; chunk text stays raw)."""
    if table.rows * table.columns > MAX_GRID:  # a grid of that size could take gigabytes
        return "\n".join(
            f"row {cell.row + 1}, column {cell.column + 1}: " + escape(" ".join((cell.text or "").split()))
            for cell in table.cells
        )
    grid = [["" for _ in range(table.columns)] for _ in range(table.rows)]
    for cell in table.cells:
        grid[cell.row][cell.column] = escape(" ".join((cell.text or "").split())).replace("|", "\\|")
    rows = ["| " + " | ".join(row) + " |" for row in grid]
    return "\n".join([rows[0], "|" + " --- |" * table.columns, *rows[1:]])


def _due(figures: list[Figure], position: int) -> list[Figure]:
    """Take from `figures` those that belong before the line at `position`."""
    due = []
    while figures and figures[0].order <= position:
        due.append(figures.pop(0))
    return due


def units(document: Document) -> Iterator[Unit]:
    """Headings, paragraphs, tables and figures in page order. Excluded lines are skipped.
    A figure comes just before the line at its `order`, or after the last line."""
    for page in document.pages:
        tables = {table.id: table for table in page.tables}
        figures = sorted(page.figures, key=lambda figure: figure.order)  # stable: ties keep list order
        seen: set[str] = set()
        current: list[Line] = []

        for position, line in enumerate(page.lines):
            for figure in _due(figures, position):
                if current:
                    yield Unit(current[0].role, page.number, current)
                    current = []
                yield Unit("image", page.number, [], figure=figure)
            if line.excluded:
                continue
            if line.table_id:
                if current:
                    yield Unit(current[0].role, page.number, current)
                    current = []
                if line.table_id not in seen:
                    seen.add(line.table_id)
                    members = [m for m in page.lines if m.table_id == line.table_id]
                    yield Unit("table", page.number, members, tables.get(line.table_id))
                continue
            if current and line.paragraph != current[0].paragraph:
                yield Unit(current[0].role, page.number, current)
                current = []
            current.append(line)
        if current:
            yield Unit(current[0].role, page.number, current)
        for figure in figures:
            yield Unit("image", page.number, [], figure=figure)
