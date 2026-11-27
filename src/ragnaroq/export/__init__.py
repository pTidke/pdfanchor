"""Turn a Document into files a language model or a vector index can use."""

from collections import deque
from collections.abc import Iterator
from dataclasses import dataclass, field

from ..model import Cell, Document, Figure, Line, Table


@dataclass
class Unit:
    """One heading, paragraph or table in reading order. `context` holds lines the unit shows
    but does not hold (a table's header row repeated above a later group of its rows): they
    count for its status and rules, never for its anchors or id."""

    kind: str
    page: int
    lines: list[Line]
    table: Table | None = None
    figure: Figure | None = None
    context: list[Line] = field(default_factory=list)
    markdown: str | None = None  # a group of a table's rows, laid out in the whole table's columns

    @property
    def text(self) -> str:
        if self.figure:
            return ""  # a figure is shown by the Markdown export, not read as text
        if self.markdown is not None:
            return self.markdown
        if self.table:
            return table_markdown(self.table)
        return " ".join(line.text.strip() for line in self.lines if line.text.strip())

    @property
    def status(self) -> str:
        states = {line.status for line in [*self.lines, *self.context]}
        states |= {self.table.status} if self.table else set()
        if self.figure:
            states.add(self.figure.status)
        return "blocked" if "blocked" in states else "flagged" if "flagged" in states else "accepted"

    @property
    def rules(self) -> list[str]:
        flags = [flag for line in [*self.lines, *self.context] for flag in line.flags]
        flags += self.table.flags if self.table else []
        flags += self.figure.flags if self.figure else []
        return sorted({flag.rule for flag in flags})


MAX_GRID = 100_000  # a table with more cells than this is not laid out as a grid


def grid_cell(text: str | None, escape=lambda text: text) -> str:
    """A cell's words as a Markdown grid shows them."""
    return escape(" ".join((text or "").split())).replace("|", "\\|")


def layout(table: Table) -> tuple[list[int], dict[int, int]]:
    """The rows and columns a table's grid shows: the header row (row 0) and every row that holds
    a cell, and every column that holds one. A sheet's table spans its outermost cells, so one
    value in XFD1048576 would otherwise lay out 17 billion empty cells. Returns the rows in order
    and each shown column's place in the grid. A table with no cells keeps its declared shape."""
    if not table.cells:
        return list(range(max(table.rows, 1))), {c: c for c in range(table.columns)}
    rows = sorted({0} | {cell.row for cell in table.cells})
    columns = sorted({cell.column for cell in table.cells})
    return rows, {column: place for place, column in enumerate(columns)}


def grid_markdown(rows: int, columns: int, cells: list[Cell], escape=lambda text: text) -> str:
    """Cells already placed in a grid of `rows` by `columns`, as Markdown; each cell's `row` and
    `column` are its place in the grid. A grid over `MAX_GRID` cells is written cell by cell."""
    if rows * columns > MAX_GRID:  # a grid of that size could take gigabytes
        return "\n".join(
            f"row {cell.row + 1}, column {cell.column + 1}: " + escape(" ".join((cell.text or "").split()))
            for cell in cells
        )
    grid = [["" for _ in range(columns)] for _ in range(rows)]
    for cell in cells:
        grid[cell.row][cell.column] = grid_cell(cell.text, escape)
    lines = ["| " + " | ".join(row) + " |" for row in grid]
    return "\n".join([lines[0], "|" + " --- |" * columns, *lines[1:]])


def table_markdown(table: Table, escape=lambda text: text) -> str:
    """The table as a Markdown grid of the rows and columns that hold a cell (`layout`).
    `escape` is applied to each cell's words (the Markdown file escapes them; chunk text stays
    raw). A table too large for a grid lists its cells, each at its place in the table."""
    rows, columns = layout(table)
    if len(rows) * len(columns) > MAX_GRID:
        return grid_markdown(table.rows, table.columns, table.cells, escape)
    places = {row: place for place, row in enumerate(rows)}
    placed = [
        cell.model_copy(update={"row": places[cell.row], "column": columns[cell.column]})
        for cell in table.cells
    ]
    return grid_markdown(len(rows), len(columns), placed, escape)


def _due(figures: deque[Figure], position: int) -> list[Figure]:
    """Take from `figures` those that belong before the line at `position`."""
    due = []
    while figures and figures[0].order <= position:
        due.append(figures.popleft())
    return due


def units(document: Document) -> Iterator[Unit]:
    """Headings, paragraphs, tables and figures in page order. Excluded lines are skipped.
    A figure comes just before the line at its `order`, or after the last line."""
    for page in document.pages:
        tables = {table.id: table for table in page.tables}
        members: dict[str, list[Line]] = {}  # each table's lines, found once per page
        for line in page.lines:
            if line.table_id:
                members.setdefault(line.table_id, []).append(line)
        figures = deque(sorted(page.figures, key=lambda figure: figure.order))  # stable: ties keep order
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
                    yield Unit("table", page.number, members[line.table_id], tables.get(line.table_id))
                continue
            if current and line.paragraph != current[0].paragraph:
                yield Unit(current[0].role, page.number, current)
                current = []
            current.append(line)
        if current:
            yield Unit(current[0].role, page.number, current)
        for figure in figures:
            yield Unit("image", page.number, [], figure=figure)
