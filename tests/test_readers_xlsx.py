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
        (-0.001, "#,##0.00", "-0.00"),
        (-0.4, "0", "-0"),
        (-0.4, "#,##0;(#,##0)", "(0)"),
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


def test_decide_keeps_reader_flags_every_time():
    from ragnaroq import policy

    document = demo("rates.xlsx").document
    nocache = demo("rates-nocache.xlsx").document
    for _ in range(2):
        for doc in (document, nocache):
            policy.decide(doc, Config().policy)
        assert "xlsx.format-unknown" in {f.rule for f in line_at(readers.ReadResult(document), "B6").flags}
        assert "xlsx.no-cached-value" in {f.rule for f in line_at(readers.ReadResult(nocache), "B2").flags}


def test_decide_keeps_a_csv_readers_flag():
    from ragnaroq import policy
    from ragnaroq.model import Flag

    document = read_bytes(b"a,b\n1,2\n", "t.csv", Config()).document
    line = next(document.lines())
    line.flags.append(Flag(rule="csv.something", severity="review", message="x"))
    policy.decide(document, Config().policy)
    assert "csv.something" in {f.rule for f in line.flags}


# --- final review I2: what the Excel reader left out without a word -------------------------------


def _png():
    from PIL import Image as PilImage

    png = io.BytesIO()
    PilImage.new("RGB", (200, 100), "blue").save(png, "PNG")
    png.seek(0)
    return png


def _patched(data: bytes, name: str, change) -> bytes:
    """The workbook with one archive member rewritten."""
    out = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(data)) as source, zipfile.ZipFile(out, "w") as target:
        for item in source.infolist():
            content = source.read(item.filename)
            target.writestr(item, change(content) if item.filename == name else content)
    return out.getvalue()


def _with_picture(wb):
    from openpyxl.drawing.image import Image

    ws = wb.active
    ws["A1"] = "Rates"
    ws.add_image(Image(_png()), "C3")


A = "http://schemas.openxmlformats.org/drawingml/2006/main"
TEXT_BOX = (
    "<twoCellAnchor><from><col>5</col><colOff>0</colOff><row>1</row><rowOff>0</rowOff></from>"
    "<to><col>8</col><colOff>0</colOff><row>4</row><rowOff>0</rowOff></to>"
    '<sp macro="" textlink=""><nvSpPr><cNvPr id="3" name="TextBox 2"/><cNvSpPr txBox="1"/></nvSpPr>'
    f'<spPr><a:prstGeom xmlns:a="{A}" prst="rect"/></spPr>'
    f'<txBody><a:bodyPr xmlns:a="{A}"/><a:p xmlns:a="{A}"><a:r><a:t>Note: provisional.</a:t></a:r></a:p>'
    "</txBody></sp><clientData/></twoCellAnchor>"
).encode()


def test_picture_alt_text_is_the_figure_description():
    data = _patched(workbook_bytes(_with_picture), "xl/drawings/drawing1.xml",
                    lambda xml: xml.replace(b'descr="Picture"', b'descr="Logo of the department"'))
    (figure,) = read_xlsx(data).document.pages[0].figures
    assert (figure.description, figure.description_source) == ("Logo of the department", "alt")
    assert figure.status == "accepted"


def test_a_picture_title_is_its_description_when_it_has_no_alt_text():
    data = _patched(workbook_bytes(_with_picture), "xl/drawings/drawing1.xml",
                    lambda xml: xml.replace(b'descr="Picture"', b'title="Department logo"'))
    (figure,) = read_xlsx(data).document.pages[0].figures
    assert (figure.description, figure.description_source) == ("Department logo", "alt")


def test_shapes_and_text_boxes_on_a_sheet_are_flagged():
    data = _patched(workbook_bytes(_with_picture), "xl/drawings/drawing1.xml",
                    lambda xml: xml.replace(b"</wsDr>", TEXT_BOX + b"</wsDr>"))
    openpyxl.load_workbook(io.BytesIO(data))  # still a workbook Excel and openpyxl open
    page = read_xlsx(data).document.pages[0]
    assert [f.rule for f in page.flags] == ["xlsx.shapes-not-read"]
    assert "Sheet" in page.flags[0].message
    assert len(page.figures) == 1  # the picture beside the shape is still read


def test_a_chartsheet_is_flagged_and_cell_comments_are_left_out_and_flagged():
    from openpyxl.chart import BarChart, Reference
    from openpyxl.comments import Comment

    def build(wb):
        ws = wb.active
        ws.title = "Data"
        for r in range(1, 5):
            ws.append([f"Q{r}", r * 100])
        ws["A1"].comment = Comment("Figures are provisional until audited.", "auditor")
        sheet = wb.create_chartsheet("Collections chart")
        chart = BarChart()
        chart.add_data(Reference(ws, min_col=2, min_row=1, max_row=4))
        sheet.add_chart(chart)

    document = read_xlsx(workbook_bytes(build)).document
    rules = {f.rule: f.message for f in document.flags}
    assert "Collections chart" in rules["chart.not-read"]
    assert rules["xlsx.comments"] == "The file has comments; they are not read."
    assert not any("provisional" in line.text for line in document.lines())
    from ragnaroq.policy import needs_review

    assert needs_review(document)


def test_a_plain_workbook_has_none_of_these_flags():
    document = read_xlsx(workbook_bytes(_with_picture)).document
    assert document.flags == [] and document.pages[0].flags == []


# --- final review I6: times round as Excel rounds them ---------------------------------------------


@pytest.mark.parametrize(
    "value, fmt, shown",
    [
        (1 / 24 - 1e-9, "h:mm:ss", "1:00:00"),  # a formula's hour, with floating error below it
        (1 / 24 - 1e-9, "h:mm", "1:00"),
        (0.5 + 0.6 / 86400, "h:mm:ss", "12:00:01"),
        (0.5 + 0.4 / 86400, "h:mm:ss", "12:00:00"),
        (__import__("datetime").time(12, 0, 0, 600000), "h:mm:ss", "12:00:01"),
        (datetime(2026, 11, 26, 9, 59, 59, 700000), "dd-mmm-yyyy h:mm", "26-Nov-2026 10:00"),
        (datetime(2026, 11, 26, 9, 59, 59, 700000), "h:mm:ss", "10:00:00"),
        (datetime(2026, 11, 26, 9, 59, 40), "h:mm", "9:59"),  # minutes are not rounded, only seconds
        (45987.99999999, "dd-mmm-yyyy", "27-Nov-2025"),
        (datetime(2026, 11, 26, 9, 30, 15, 260000), "h:mm:ss.0", "9:30:15.3"),
        (datetime(2026, 11, 26, 9, 30, 15, 994000), "h:mm:ss.00", "9:30:15.99"),
        (datetime(2026, 11, 26, 9, 30, 15, 996000), "h:mm:ss.00", "9:30:16.00"),
        (datetime(2026, 11, 26, 23, 59, 59, 600000), "h:mm AM/PM", "12:00 AM"),
    ],
)
def test_times_round_to_the_second_or_the_shown_fraction_as_excel_does(value, fmt, shown):
    assert numformat.render(value, fmt) == shown


def test_a_sheet_figure_comes_after_the_sheet_lines():
    """Final review m9: a picture is placed after the sheet's table, not before its first cell."""
    from ragnaroq.export import units

    def build(wb):
        _with_picture(wb)
        wb.active.append(["80C", 150000])

    document = read_xlsx(workbook_bytes(build)).document
    page = document.pages[0]
    (figure,) = page.figures
    assert figure.order == len(page.lines) == 3
    assert [unit.kind for unit in units(document)] == ["table", "image"]
