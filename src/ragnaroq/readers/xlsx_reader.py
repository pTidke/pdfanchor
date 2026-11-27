"""Excel (.xlsx). One page per visible sheet and one line per non-empty, visible cell, whose
text is the value as Excel shows it ("5%", "₹1,50,000"). The stored value, the formula and
the number format are kept in `line.value`. A merged range is one line anchored to the range."""

import contextlib
import io
import posixpath
import threading
import zipfile
from datetime import date, datetime, time

from defusedxml import DefusedXmlException
from defusedxml import ElementTree as SafeET

from ..anchors import XlsxAnchor
from ..config import Config
from ..model import Cell, CellValue, Figure, Flag, Page, Table
from . import READER_VERSIONS, ReaderRefused, ReaderUnavailable, ReadResult, _build, _zip, numformat


def _stored(value):
    if isinstance(value, (datetime, date, time)):
        return value.isoformat()
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def _formula(cell) -> str | None:
    value = cell.value
    if cell.data_type == "f":
        return value if isinstance(value, str) else getattr(value, "text", None)
    return None


def _hidden_columns(sheet) -> set[int]:
    return {
        index
        for dimension in sheet.column_dimensions.values()
        if dimension.hidden and dimension.min
        for index in range(dimension.min, (dimension.max or dimension.min) + 1)
    }


def _corner(anchor, get_column_letter) -> str:
    start = getattr(anchor, "_from", None)
    return f"{get_column_letter(start.col + 1)}{start.row + 1}" if start is not None else "A1"


def _flag(page: Page, rule: str, message: str) -> None:
    page.flags.append(Flag(rule=rule, severity="review", message=message))


def _alt(anchor) -> str:
    """A picture's alt text (`xdr:cNvPr` @descr, else @title), as openpyxl keeps it on the anchor;
    "" when it does not describe the picture (`_build.alt_text`)."""
    props = getattr(getattr(getattr(anchor, "pic", None), "nvPicPr", None), "cNvPr", None)
    return _build.alt_text(props.descr, props.title) if props is not None else ""


_MAIN = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
_REL = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
_PACKAGE = "{http://schemas.openxmlformats.org/package/2006/relationships}"
_XDR = "{http://schemas.openxmlformats.org/drawingml/2006/spreadsheetDrawing}"
_A = "{http://schemas.openxmlformats.org/drawingml/2006/main}"
_ANCHORS = {_XDR + "twoCellAnchor", _XDR + "oneCellAnchor", _XDR + "absoluteAnchor"}
_PLACING = {_XDR + "from", _XDR + "to", _XDR + "pos", _XDR + "ext", _XDR + "clientData"}
_BROKEN = (KeyError, ValueError, zipfile.BadZipFile, SafeET.ParseError, DefusedXmlException)


def _related(archive, part: str) -> dict[str, tuple[str, str]]:
    """A part's relationships inside the package: id -> (type, the target's name in the archive)."""
    folder, name = posixpath.split(part)
    try:
        root = SafeET.fromstring(archive.read(f"{folder}/_rels/{name}.rels"))
    except KeyError:
        return {}
    found = {}
    for relationship in root.iter(_PACKAGE + "Relationship"):
        target = relationship.get("Target") or ""
        if relationship.get("TargetMode") == "External" or not target:
            continue
        where = target[1:] if target.startswith("/") else posixpath.normpath(posixpath.join(folder, target))
        found[relationship.get("Id")] = (relationship.get("Type") or "", where)
    return found


def _unread(drawing) -> int:
    """Objects in a sheet's drawing that openpyxl drops: shapes, text boxes, groups, connectors,
    and frames that are not charts. Pictures and charts are read."""
    count = 0
    for anchor in drawing:
        if anchor.tag not in _ANCHORS:
            continue
        for child in anchor:
            if child.tag in _PLACING or child.tag == _XDR + "pic":
                continue
            data = child.find(f"{_A}graphic/{_A}graphicData") if child.tag == _XDR + "graphicFrame" else None
            if data is not None and (data.get("uri") or "").endswith("/chart"):
                continue
            count += 1
    return count


def _package(data: bytes) -> tuple[dict[str, int], bool]:
    """From the archive itself, what openpyxl does not keep: for each sheet name, how many drawn
    objects are not read (a drawing that cannot be parsed counts as one), and whether the
    workbook has cell comments."""
    unread: dict[str, int] = {}
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        names = archive.namelist()
        comments = any(name.startswith(("xl/comments", "xl/threadedComments/")) for name in names)
        try:
            book = SafeET.fromstring(archive.read("xl/workbook.xml"))
            sheets = _related(archive, "xl/workbook.xml")
        except _BROKEN:
            return unread, comments
        for sheet in book.iter(_MAIN + "sheet"):
            _, path = sheets.get(sheet.get(_REL + "id"), ("", ""))
            try:
                parts = _related(archive, path).values()
                drawings = [where for kind, where in parts if kind.endswith("/drawing")]
            except _BROKEN:
                drawings = []
            for where in drawings:
                try:
                    count = _unread(SafeET.fromstring(archive.read(where)))
                except _BROKEN:
                    count = 1
                if count:
                    name = sheet.get("name") or ""
                    unread[name] = unread.get(name, 0) + count
    return unread, comments


def _read_sheet(sheet, values, number: int, page: Page, config: Config, images: dict, utils) -> list[str]:
    """Fill `page` from one sheet; returns what was left out as hidden."""
    merged = {str(r).split(":")[0]: str(r) for r in sheet.merged_cells.ranges}
    hidden_rows = sorted(i for i, d in sheet.row_dimensions.items() if d.hidden)
    hidden_columns = _hidden_columns(sheet)
    left_out = [f"{sheet.title}: row {r}" for r in hidden_rows]
    left_out += [f"{sheet.title}: column {utils.get_column_letter(c)}" for c in sorted(hidden_columns)]
    skip_rows = set(hidden_rows)
    table_id, cells, current_row, paragraph = f"p{number}-t1", [], None, 0
    bounds = [10**9, 10**9, 0, 0]
    # Only cells that exist, in order: a sheet declared as A1:XFD1048576 is not walked cell by cell.
    for row, column in sorted(sheet._cells):
        if len(page.lines) >= config.max_cells:
            _flag(
                page,
                "xlsx.cell-limit",
                f"{sheet.title} has more than {config.max_cells} cells; the rest is not read.",
            )
            break
        cell = sheet._cells[(row, column)]
        if row in skip_rows or column in hidden_columns or cell.value is None or cell.value == "":
            continue
        coordinate = cell.coordinate
        formula = _formula(cell)
        stored = values[coordinate].value if formula is not None else cell.value
        flags = []
        if stored is None:
            text = ""
            flags.append(
                ("xlsx.no-cached-value", "The formula has no cached result; open and save it in Excel.")
            )
        else:
            text = numformat.render(stored, cell.number_format)
            if text is None:
                text = str(_stored(stored))
                flags.append(
                    ("xlsx.format-unknown", f'The number format "{cell.number_format}" is not known.')
                )
        if row != current_row:
            current_row, paragraph = row, _build.next_paragraph(page)
        anchor = XlsxAnchor(sheet=sheet.title, cell=merged.get(coordinate, coordinate))
        line = _build.add_line(
            page,
            text,
            "xlsx",
            anchor,
            role="table",
            table_id=table_id,
            paragraph=paragraph,
            value=CellValue(stored=_stored(stored), formula=formula, format=cell.number_format),
        )
        line.flags.extend(Flag(rule=r, severity="review", message=m) for r, m in flags)
        bounds = [min(bounds[0], row), min(bounds[1], column), max(bounds[2], row), max(bounds[3], column)]
        cells.append((row, column, text, anchor))
    if cells:
        top, left, bottom, right = bounds
        page.tables.append(
            Table(
                id=table_id,
                page=number,
                rows=bottom - top + 1,
                columns=right - left + 1,
                cells=[Cell(row=r - top, column=c - left, text=t, anchor=a) for r, c, t, a in cells],
            )
        )
    for k, picture in enumerate(getattr(sheet, "_images", []), 1):
        alt = _alt(picture.anchor)
        figure = Figure(
            id=f"p{number}-f{k}",
            page=number,
            order=len(page.lines),  # after the sheet's cells: a picture has no place among them
            width=int(picture.width),
            height=int(picture.height),
            anchor=XlsxAnchor(sheet=sheet.title, cell=_corner(picture.anchor, utils.get_column_letter)),
            description=alt,
            description_source="alt" if alt else "none",
        )
        images[figure.id] = picture.data
        page.figures.append(figure)
    for chart in getattr(sheet, "_charts", []):
        at = _corner(chart.anchor, utils.get_column_letter)
        _flag(
            page,
            "chart.not-read",
            f"A chart at {sheet.title}!{at} is not read; its numbers are not in the text.",
        )
    return left_out


class _RawPicture:
    """A picture as the archive holds it. openpyxl's own `Image` opens, and for any format but
    GIF, JPEG and PNG decodes and re-saves, the picture with Pillow; no reader does that. The
    figure stage looks at the bytes, with limits."""

    def __init__(self, source):
        self.data = source.getvalue()
        self.format = "raw"
        self.width = self.height = 0  # measured later, by the figure stage


_HERE = threading.local()


def _install() -> None:
    """Put a dispatcher where openpyxl looks up `Image` for a workbook's pictures, once. It
    makes a raw picture only in a thread that is inside `_raw_pictures`; for every other
    caller in the process it is openpyxl's own `Image`."""
    from openpyxl.reader import drawings

    if getattr(drawings.Image, "_ragnaroq", False):
        return
    original = drawings.Image

    def dispatch(source):
        return _RawPicture(source) if getattr(_HERE, "raw", False) else original(source)

    dispatch._ragnaroq = True
    drawings.Image = dispatch


@contextlib.contextmanager
def _raw_pictures():
    """While this thread loads a workbook, openpyxl takes pictures as raw bytes."""
    _install()
    _HERE.raw = True
    try:
        yield
    finally:
        _HERE.raw = False


def read(data: bytes, name: str, config: Config) -> ReadResult:
    _zip.check(data, config)
    try:
        import openpyxl
        from openpyxl import utils
    except ModuleNotFoundError as error:
        raise ReaderUnavailable("The xlsx reader needs openpyxl: pip install 'ragnaroq[formats]'") from error
    try:
        with _raw_pictures():
            formulas = openpyxl.load_workbook(io.BytesIO(data), data_only=False)
            cached = openpyxl.load_workbook(io.BytesIO(data), data_only=True)
    except Exception as error:  # openpyxl raises many kinds for a broken file
        raise ReaderRefused("xlsx.invalid", f"{name} could not be opened as a workbook: {error}") from error
    document = _build.start(data, name, "xlsx", config, {"reader:xlsx": READER_VERSIONS["xlsx"]})
    unread, comments = _package(data)
    pages, images, hidden = [], {}, []
    for sheet in formulas.worksheets:
        if sheet.sheet_state != "visible":
            hidden.append(f"sheet {sheet.title}")
            continue
        page = Page(number=len(pages) + 1, kind="sheet", name=sheet.title)
        hidden += _read_sheet(sheet, cached[sheet.title], page.number, page, config, images, utils)
        if unread.get(sheet.title):
            _flag(
                page,
                "xlsx.shapes-not-read",
                f"{unread[sheet.title]} shape(s), text box(es) or other drawn object(s) on {sheet.title} "
                "are not read; any text in them is not in the text.",
            )
        pages.append(page)
    for chartsheet in formulas.chartsheets:
        if chartsheet.sheet_state != "visible":
            hidden.append(f"chart sheet {chartsheet.title}")
            continue
        document.flags.append(
            Flag(
                rule="chart.not-read",
                severity="review",
                message=f"The chart sheet {chartsheet.title} is not read; its numbers are not in the text.",
            )
        )
    if comments:
        document.flags.append(
            Flag(rule="xlsx.comments", severity="review", message="The file has comments; they are not read.")
        )
    if pages:
        document.pages = pages
        document.source.pages = len(pages)
    if hidden:
        document.flags.append(
            Flag(
                rule="xlsx.hidden",
                severity="review",
                message="Left out because hidden: " + "; ".join(hidden) + ".",
            )
        )
    return ReadResult(document, images)
