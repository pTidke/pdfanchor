"""The Excel reader: cells as Excel shows them, with the stored value and formula kept."""

import io
import zipfile
from dataclasses import replace
from datetime import datetime
from pathlib import Path

import pytest

openpyxl = pytest.importorskip("openpyxl")

from ragnaroq import evaluate, readers  # noqa: E402
from ragnaroq.anchors import cite  # noqa: E402
from ragnaroq.config import Config  # noqa: E402
from ragnaroq.readers import ReaderRefused, _zip, numformat, read_bytes  # noqa: E402

DEMO = Path(__file__).parent.parent / "references" / "demo"
INDIAN = '[>=10000000]"₹"##\\,##\\,##\\,##0;[>=100000]"₹"##\\,##\\,##0;"₹"##,##0'


@pytest.mark.parametrize(
    "value, fmt, shown",
    [
        (0.05, "0%", "5%"),
        (0.1234, "0.00%", "12.34%"),
        (1234567, "#,##0", "1,234,567"),
        (150000, INDIAN, "₹1,50,000"),
        (12500000, INDIAN, "₹1,25,00,000"),
        (1234.5, "[$₹-4009] #,##0.00", "₹ 1,234.50"),
        (-42, "#,##0;(#,##0)", "(42)"),
        (datetime(2026, 11, 26), "dd-mmm-yyyy", "26-Nov-2026"),
        (datetime(2026, 11, 26), "d/m/yyyy", "26/11/2026"),
        (7, "General", "7"),
        (2.5, "General", "2.5"),
        ("80C", "@", "80C"),
        (5000, INDIAN, "₹5,000"),
        (datetime(2026, 11, 26, 14, 5), "h:mm", "14:05"),
    ],
)
def test_values_render_as_excel_shows_them(value, fmt, shown):
    assert numformat.render(value, fmt) == shown


def test_an_unknown_format_is_none():
    assert numformat.render(0.001, "0.0E+00;;") is None


def read_xlsx(data: bytes, name="t.xlsx", config=None):
    return read_bytes(data, name, config or Config())


def workbook_bytes(build) -> bytes:
    wb = openpyxl.Workbook()
    build(wb)
    buffer = io.BytesIO()
    wb.save(buffer)
    return buffer.getvalue()


def demo(name):
    return read_xlsx((DEMO / name).read_bytes(), name)


def line_at(result, cell):
    return next(line for line in result.document.lines() if line.anchor.cell == cell)


def test_the_demo_workbooks_score_by_anchor():
    report = evaluate.run_exact(DEMO, readers.read_bytes)
    scored = {f.name: f for f in report.files}
    for name in ("rates.xlsx", "rates-nocache.xlsx"):
        assert scored[name].error is None
        assert scored[name].score.ok, scored[name].score


def test_a_hidden_sheet_and_row_and_column_are_left_out_and_named():
    result = demo("rates.xlsx")
    cells = {cite(line.anchor) for line in result.document.lines()}
    assert "Rates!B9" not in cells and "Rates!A9" not in cells and "Rates!F2" not in cells
    assert not any(a.startswith("Lookup") for a in cells)
    assert [p.name for p in result.document.pages] == ["Rates"]
    flag = next(f for f in result.document.flags if f.rule == "xlsx.hidden")
    assert "Lookup" in flag.message and "row 9" in flag.message and "column F" in flag.message


def test_a_merged_cell_is_one_line_anchored_to_its_range():
    result = demo("rates.xlsx")
    merged = [line for line in result.document.lines() if cite(line.anchor) == "Rates!B1:D1"]
    assert [line.text for line in merged] == ["Values"]


def test_a_formula_without_a_cached_value_gives_no_text():
    line = line_at(demo("rates-nocache.xlsx"), "B2")
    assert line.text == ""
    assert "xlsx.no-cached-value" in {f.rule for f in line.flags}
    assert line.value.formula == "=B1*2"


def test_the_stored_value_and_formula_are_kept():
    result = demo("rates.xlsx")
    rate = line_at(result, "B2")
    assert (rate.text, rate.value.stored, rate.value.format) == ("5%", 0.05, "0%")
    tax = line_at(result, "B5")
    assert (tax.text, tax.value.stored, tax.value.formula) == ("7500", 7500, "=B2*B3")
    odd = line_at(result, "B6")
    assert odd.text == "1234.5" and "xlsx.format-unknown" in {f.rule for f in odd.flags}
    assert line_at(result, "B4").value.stored == "2026-11-26T00:00:00"


def test_every_cell_is_a_table_line_and_a_row_is_a_paragraph():
    result = demo("rates.xlsx")
    page = result.document.pages[0]
    assert page.kind == "sheet" and len(page.tables) == 1
    assert {line.table_id for line in page.lines} == {page.tables[0].id}
    assert line_at(result, "A2").paragraph == line_at(result, "B2").paragraph
    assert line_at(result, "A2").paragraph != line_at(result, "A3").paragraph


def test_a_sheet_over_the_cell_limit_is_cut_and_flagged():
    def build(wb):
        for r in range(1, 6):
            wb.active.append([r, r * 2, r * 3])

    result = read_xlsx(workbook_bytes(build), config=Config(max_cells=10))
    page = result.document.pages[0]
    assert len(page.lines) == 10
    assert "xlsx.cell-limit" in {f.rule for f in page.flags + result.document.flags}


def test_a_huge_declared_range_of_empty_cells_does_not_hang():
    def build(wb):
        wb.active["A1"] = "x"
        wb.active["XFD1048576"] = "y"

    result = read_xlsx(workbook_bytes(build))
    assert [line.text for line in result.document.lines()] == ["x", "y"]


def test_pictures_become_figures_with_their_bytes_and_charts_are_flagged():
    from openpyxl.chart import BarChart, Reference
    from openpyxl.drawing.image import Image
    from PIL import Image as PilImage

    png = io.BytesIO()
    PilImage.new("RGB", (4, 3), "red").save(png, "PNG")
    png.seek(0)

    def build(wb):
        ws = wb.active
        ws.append(["a", 1])
        ws.add_image(Image(png), "C3")
        chart = BarChart()
        chart.add_data(Reference(ws, min_col=2, min_row=1, max_row=1))
        ws.add_chart(chart, "E5")

    result = read_xlsx(workbook_bytes(build))
    page = result.document.pages[0]
    figure = page.figures[0]
    assert cite(figure.anchor) == "Sheet!C3"
    assert result.images[figure.id].startswith(b"\x89PNG")
    assert [f.rule for f in page.flags] == ["chart.not-read"]
    assert "E5" in page.flags[0].message


def zipped(entries: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, content in entries.items():
            archive.writestr(name, content)
    return buffer.getvalue()


def test_a_zip_bomb_is_refused_before_it_is_opened(monkeypatch):
    data = zipped({"[Content_Types].xml": b"<x/>", "xl/workbook.xml": b"<x/>", "xl/big.bin": bytes(50_000)})
    opened = []
    monkeypatch.setattr(openpyxl, "load_workbook", lambda *a, **k: opened.append(1))
    with pytest.raises(ReaderRefused) as refused:
        read_xlsx(data, config=Config(max_unzipped_bytes=10_000))
    assert refused.value.rule == "file.zip-limits" and not opened


def test_too_many_zip_entries_are_refused():
    data = zipped({f"e{i}": b"" for i in range(20)})
    with pytest.raises(ReaderRefused) as refused:
        _zip.check(data, Config(max_zip_entries=10))
    assert refused.value.rule == "file.zip-limits"


def test_a_broken_workbook_is_refused_not_a_crash():
    data = zipped({"[Content_Types].xml": b"<x/>", "xl/workbook.xml": b"<not a workbook"})
    with pytest.raises(ReaderRefused) as refused:
        read_xlsx(data)
    assert refused.value.rule == "xlsx.invalid"


def test_zip_and_cell_limits_do_not_change_the_pdf_reading_key():
    changed = replace(Config(), max_unzipped_bytes=5, max_zip_entries=3)
    assert Config().reading_sha256() == changed.reading_sha256()
