import pytest
from pydantic import TypeAdapter

from ragnaroq.anchors import (
    Anchor,
    CsvAnchor,
    DocxAnchor,
    HtmlAnchor,
    JsonAnchor,
    MdAnchor,
    PdfAnchor,
    PptxAnchor,
    XlsxAnchor,
    XmlAnchor,
    cite,
    key,
)
from ragnaroq.model import Box, CellValue, Figure, Line, Page


@pytest.mark.parametrize(
    "anchor, text",
    [
        (PdfAnchor(page=3, box=(72, 140, 520, 152)), "p. 3"),
        (XlsxAnchor(sheet="Rates", cell="C7"), "Rates!C7"),
        (PptxAnchor(slide=4, shape="Title 1"), "slide 4, Title 1"),
        (PptxAnchor(slide=4, shape="Notes", notes=True), "slide 4 notes"),
        (PptxAnchor(slide=4, shape="Table 2", cell=(2, 1)), "slide 4, Table 2, row 2, column 1"),
        (DocxAnchor(paragraph=18), "¶18"),
        (DocxAnchor(table=2, cell=(3, 1)), "table 2, row 3, column 1"),
        (DocxAnchor(note="footnote 1"), "footnote 1"),
        (HtmlAnchor(path="/html/body/main/p[2]"), "/html/body/main/p[2]"),
        (MdAnchor(lines=(40, 42)), "lines 40–42"),
        (JsonAnchor(pointer="/deductions/80C/limit"), "/deductions/80C/limit"),
        (XmlAnchor(path="/notice/section[2]/para[1]"), "/notice/section[2]/para[1]"),
        (CsvAnchor(row=12, column="Amount"), "row 12, Amount"),
    ],
)
def test_every_anchor_has_a_short_citation(anchor, text):
    assert cite(anchor) == text


def test_an_anchor_round_trips_through_json():
    line = Line(
        id="l1",
        page=1,
        order=0,
        raw="5%",
        text="5%",
        source="xlsx",
        anchor=XlsxAnchor(sheet="Rates", cell="C7"),
        value=CellValue(stored=0.05, format="0%"),
    )
    again = Line.model_validate_json(line.model_dump_json())
    assert again.anchor == XlsxAnchor(sheet="Rates", cell="C7") and again.value.stored == 0.05


def test_the_type_picks_the_anchor_class():
    adapter = TypeAdapter(Anchor)
    assert adapter.validate_python({"type": "csv", "row": 2, "column": "A"}) == CsvAnchor(row=2, column="A")
    assert adapter.validate_python({"type": "md", "lines": [1, 2]}) == MdAnchor(lines=(1, 2))


def test_a_key_is_the_same_for_the_same_place():
    a = PdfAnchor(page=1, box=(1, 2, 3, 4))
    assert key(a) == key(PdfAnchor(page=1, box=(1.0, 2.0, 3.0, 4.0)))
    assert key(a) != key(PdfAnchor(page=2, box=(1, 2, 3, 4)))
    assert key(a) == '{"box":[1.0,2.0,3.0,4.0],"page":1,"type":"pdf"}'


def test_a_line_with_a_box_and_no_anchor_gets_a_pdf_anchor():
    line = Line(
        id="l", page=2, order=0, box=Box(x0=1, top=2, x1=3, bottom=4), raw="a", text="a", source="native"
    )
    assert line.anchor == PdfAnchor(page=2, box=(1, 2, 3, 4))
    assert Line(id="l", page=2, order=0, raw="a", text="a", source="x").anchor is None


def test_a_sheet_page_and_a_figure_round_trip():
    figure = Figure(
        id="f1", page=1, anchor=PptxAnchor(slide=1, shape="Picture 2"), order=3, description_source="alt"
    )
    page = Page(number=1, kind="sheet", name="Rates", figures=[figure])
    again = Page.model_validate_json(page.model_dump_json())
    assert again.figures[0].order == 3 and again.width is None and again.name == "Rates"


@pytest.mark.parametrize(
    "kwargs",
    [
        {},
        {"table": 2},
        {"cell": (1, 1)},
        {"paragraph": 1, "note": "footnote 1"},
        {"paragraph": 1, "table": 1, "cell": (1, 1)},
    ],
)
def test_a_docx_anchor_names_exactly_one_place(kwargs):
    with pytest.raises(ValueError):
        DocxAnchor(**kwargs)


def test_pptx_cells_have_distinct_keys():
    one = PptxAnchor(slide=4, shape="Table 2", cell=(1, 2))
    other = PptxAnchor(slide=4, shape="Table 2", cell=(2, 1))
    assert key(one) != key(other)
    assert key(one) != key(PptxAnchor(slide=4, shape="Table 2"))
