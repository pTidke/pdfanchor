from ragnaroq.anchors import DocxAnchor, PptxAnchor, XlsxAnchor
from ragnaroq.config import Policy
from ragnaroq.export import units
from ragnaroq.export.markdown import to_markdown
from ragnaroq.model import (
    Box,
    Cell,
    Document,
    Engine,
    Figure,
    Line,
    Page,
    Reading,
    Source,
    Table,
)
from ragnaroq.policy import EXACT_SOURCES, decide

KINDS = {"docx": "body", "xlsx": "sheet", "pptx": "slide", "csv": "body"}


def exact_document(source, texts, kind=None, name=None, figures=(), table=False):
    kind = kind or KINDS.get(source, "body")
    lines = [
        Line(id=f"l{i}", page=1, order=i, raw=t, text=t, source=source,
             table_id="t1" if table else None, role="table" if table else "body")
        for i, t in enumerate(texts)
    ]
    tables = []
    if table:
        cells = [Cell(row=0, column=i, text=t) for i, t in enumerate(texts)]
        tables = [Table(id="t1", page=1, rows=1, columns=len(texts), cells=cells)]
    page = Page(number=1, kind=kind, name=name, lines=lines, tables=tables, figures=list(figures))
    return Document(
        source=Source(sha256="0", bytes=1, filename=f"f.{source}", pages=1, format=source),
        engine=Engine(name="t", version="0", policy_version="0.6", config_sha256="0", components={}),
        created_at="2026-01-01T00:00:00Z", pages=[page],
    )


def decided(doc):
    decide(doc, Policy())
    return doc


def figure(**kw):
    return Figure(id="f1", page=1, anchor=PptxAnchor(slide=1, shape="Picture 2"), **kw)


def test_exact_text_is_accepted_without_a_second_reader():
    doc = decided(exact_document("docx", ["Rates for TY 2026-27", "The limit is ₹1,50,000."]))
    assert {line.status for line in doc.lines()} == {"accepted"}


def test_exact_text_still_goes_through_the_validators():
    doc = decided(exact_document("xlsx", ["New Delhi, the 31* March, 2025"]))
    assert "date.ordinal-suffix" in {f.rule for line in doc.lines() for f in line.flags}


def test_the_ocr_misreading_checks_do_not_run_on_exact_text():
    doc = decided(exact_document("csv", ["Cat. No. 11320B", "A | B"]))
    assert {line.status for line in doc.lines()} == {"accepted"}


def test_every_exact_source_is_a_format_name():
    assert EXACT_SOURCES == {"docx", "xlsx", "pptx", "html", "md", "json", "xml", "csv"}


def test_exact_table_cells_are_accepted():
    doc = decided(exact_document("docx", ["Item", "Rate"], table=True))
    table = doc.pages[0].tables[0]
    assert {line.status for line in doc.lines()} == {"accepted"}
    assert not any(f.rule == "table.member" for line in doc.lines() for f in line.flags)
    assert table.status == "accepted" and table.flags == []


def test_an_exact_table_follows_its_flagged_line():
    doc = decided(exact_document("docx", ["Item", "31* March, 2025"], table=True))
    assert doc.pages[0].tables[0].status == "flagged"


def test_a_scanned_table_is_still_never_accepted_by_rule():
    doc = exact_document("docx", ["Item", "Rate"], table=True)
    for line in doc.lines():
        line.source = "native"
    decided(doc)
    assert doc.pages[0].tables[0].status == "flagged"
    assert {f.rule for f in doc.pages[0].tables[0].flags} == {"table.structure-unverified"}


def test_a_figure_with_alt_text_is_accepted_and_one_with_no_description_is_flagged():
    alt = figure(description="Bar chart of collections", description_source="alt")
    none = figure(description="", description_source="none")
    decided(exact_document("pptx", ["x"], figures=[alt, none]))
    assert alt.status == "accepted" and alt.flags == []
    assert none.status == "flagged" and [f.rule for f in none.flags] == ["image.no-description"]


def test_a_caption_is_accepted():
    cap = figure(description="Figure 1. Slabs", description_source="caption")
    decided(exact_document("docx", ["x"], figures=[cap]))
    assert cap.status == "accepted"


def ocr_line(text, other=None, order=0):
    readings = [Reading(source="ocr:b", text=other)] if other else []
    box = Box(x0=0, top=order * 10, x1=50, bottom=order * 10 + 9)
    return Line(id=f"o{order}", page=1, order=order, box=box,
                raw=text, text=text, source="ocr:a", readings=readings)


def test_an_ocr_description_follows_the_ocr_lines():
    bad = figure(description="Total 1,200", description_source="ocr",
                 ocr_lines=[ocr_line("Total 1,200", "Total 7,200"), ocr_line("Net 5", "Net 5", 1)])
    good = figure(description="Total 1,200", description_source="ocr",
                  ocr_lines=[ocr_line("Total 1,200", "Total 1,200"), ocr_line("Net 5", "Net 5", 1)])
    decided(exact_document("pptx", ["x"], figures=[bad, good]))
    assert bad.status == "flagged" and bad.flags
    assert good.status == "accepted" and good.flags == []


def test_a_model_description_never_changes_the_status():
    fig = figure(description="", description_source="none", model_description="A bar chart")
    ok = figure(description="Chart", description_source="alt", model_description="Something else")
    decided(exact_document("pptx", ["x"], figures=[fig, ok]))
    assert fig.status == "flagged" and ok.status == "accepted"


def test_the_third_reader_looks_at_a_figure_ocr_line_without_a_box():
    line = ocr_line("Total", "Total")
    line.box = None
    fig = figure(description="Total", description_source="ocr", ocr_lines=[line])
    decided(exact_document("pptx", ["x"], figures=[fig]))
    assert fig.status == "accepted"


def test_figures_are_placed_among_the_lines_by_order():
    first = figure(description="A", description_source="alt", order=1)
    last = figure(description="B", description_source="alt", order=9)
    start = figure(description="C", description_source="alt", order=0)
    doc = exact_document("pptx", ["one", "two"], figures=[first, last, start])
    kinds = [(u.kind, u.figure.description if u.figure else u.text) for u in units(doc)]
    assert [k for k, _ in kinds] == ["image", "body", "image", "body", "image"]
    assert [t for _, t in kinds] == ["C", "one", "A", "two", "B"]


def test_markdown_marks_parts_and_places_figures():
    xlsx = decided(exact_document("xlsx", ["5%"], name="Rates"))
    xlsx.pages[0].lines[0].anchor = XlsxAnchor(sheet="Rates", cell="C7")
    assert "<!-- sheet Rates -->" in to_markdown(xlsx)
    fig = figure(path="images/slide-4-picture-2.png", description="Bar chart",
                 description_source="alt", order=1)
    fig.anchor = PptxAnchor(slide=4, shape="Picture 2")
    pptx = decided(exact_document("pptx", ["Title"], figures=[fig]))
    md = to_markdown(pptx)
    assert "<!-- slide 1 -->" in md
    assert "![Bar chart](images/slide-4-picture-2.png) *(slide 4, Picture 2)*" in md
    assert md.index("Title") < md.index("![Bar chart]")


def test_a_body_marker_and_a_flagged_figure_comment():
    fig = figure(path="a.png", description_source="none")
    fig.anchor = DocxAnchor(paragraph=2)
    md = to_markdown(decided(exact_document("docx", ["x"], name="main", figures=[fig])))
    assert "<!-- body main -->" in md
    assert "<!-- flagged: image.no-description -->" in md


def test_the_policy_version_is_0_6():
    assert Policy().version == "0.6"
