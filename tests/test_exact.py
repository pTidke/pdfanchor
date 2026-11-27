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
from ragnaroq.policy import EXACT_SOURCES, decide, needs_review

KINDS = {"docx": "body", "xlsx": "sheet", "pptx": "slide", "csv": "body"}


def exact_document(source, texts, kind=None, name=None, figures=(), table=False):
    kind = kind or KINDS.get(source, "body")
    lines = [
        Line(
            id=f"l{i}",
            page=1,
            order=i,
            raw=t,
            text=t,
            source=source,
            table_id="t1" if table else None,
            role="table" if table else "body",
        )
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
        created_at="2026-01-01T00:00:00Z",
        pages=[page],
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
    return Line(
        id=f"o{order}", page=1, order=order, box=box, raw=text, text=text, source="ocr:a", readings=readings
    )


def test_an_ocr_description_follows_the_ocr_lines():
    bad = figure(
        description="Total 1,200",
        description_source="ocr",
        ocr_lines=[ocr_line("Total 1,200", "Total 7,200"), ocr_line("Net 5", "Net 5", 1)],
    )
    good = figure(
        description="Total 1,200",
        description_source="ocr",
        ocr_lines=[ocr_line("Total 1,200", "Total 1,200"), ocr_line("Net 5", "Net 5", 1)],
    )
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
    fig = figure(
        path="images/slide-4-picture-2.png", description="Bar chart", description_source="alt", order=1
    )
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


def test_a_flagged_figure_alone_makes_the_document_need_review():
    fig = figure(description="", description_source="none")
    doc = decided(exact_document("pptx", ["All fine"], figures=[fig]))
    assert {line.status for line in doc.lines()} == {"accepted"}
    assert needs_review(doc) is True
    assert doc.summary["figures"] == 1 and doc.summary["figures_flagged"] == 1
    assert doc.summary["figures_blocked"] == 0


def test_an_alt_text_figure_does_not_need_review():
    fig = figure(description="Bar chart", description_source="alt")
    doc = decided(exact_document("pptx", ["All fine"], figures=[fig]))
    assert needs_review(doc) is False
    assert doc.summary["figures"] == 1 and doc.summary["figures_flagged"] == 0


# --- needs_review (final review C1) -------------------------------------------------------------


def _read(name, data, config=None):
    from ragnaroq import Config, read_bytes

    return read_bytes(data, name, config or Config()).document


def _chart_docx() -> bytes:
    import io

    import pytest

    docx = pytest.importorskip("docx")
    from lxml import etree

    w = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
    wp = "http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing"
    a = "http://schemas.openxmlformats.org/drawingml/2006/main"
    c = "http://schemas.openxmlformats.org/drawingml/2006/chart"
    r = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
    made = docx.Document()
    made.add_paragraph("Collections rose this year, as the chart shows.")
    chart = (
        f'<w:p xmlns:w="{w}" xmlns:wp="{wp}" xmlns:a="{a}" xmlns:c="{c}" xmlns:r="{r}"><w:r><w:drawing>'
        '<wp:inline><wp:extent cx="100" cy="100"/><wp:docPr id="9" name="Chart 1"/><a:graphic>'
        f'<a:graphicData uri="{c}"><c:chart r:id="rId99"/></a:graphicData></a:graphic></wp:inline>'
        "</w:drawing></w:r></w:p>"
    )
    made.element.body[-1].addprevious(etree.fromstring(chart))
    buffer = io.BytesIO()
    made.save(buffer)
    return buffer.getvalue()


def _with_flag(source, rule, where="document", severity="review"):
    from ragnaroq.model import Flag

    doc = decided(exact_document(source, ["All fine"]))
    flag = Flag(rule=rule, severity=severity, message="m")
    (doc.flags if where == "document" else doc.pages[0].flags).append(flag)
    return doc


def _pdf_document(page_flags=(), table=False):
    from ragnaroq.model import Flag

    line = Line(
        id="p1-l1",
        page=1,
        order=0,
        raw="Clean text",
        text="Clean text",
        source="native",
        box=Box(x0=0, top=0, x1=10, bottom=10),
    )
    line.readings = [Reading(source="ocr:echo", text="Clean text")]
    tables = [Table(id="p1-t1", page=1, rows=1, columns=1, cells=[Cell(row=0, column=0, text="x")])]
    page = Page(
        number=1,
        kind="native",
        width=100,
        height=100,
        lines=[line],
        tables=tables if table else [],
        flags=[Flag(rule=r, severity=s, message="m") for r, s in page_flags],
    )
    doc = Document(
        source=Source(sha256="0", bytes=1, filename="f.pdf", pages=1),
        engine=Engine(name="t", version="0", policy_version="0.6", config_sha256="0", components={}),
        created_at="2026-01-01T00:00:00Z",
        pages=[page],
    )
    return decided(doc)


def test_needs_review_truth_table():
    from dataclasses import replace

    from ragnaroq import Config

    small = replace(Config(), max_cells=10)
    deep = b"> " * 150 + b"deep quote\n\nafter the deep part, is this read?\n"
    cases = {
        # Exact tables whose every cell is accepted need no person.
        "clean csv": (_read("r.csv", b"Section,Amount\n80C,150000\n80D,25000\n"), False),
        "json records": (
            _read("d.json", b'[{"section":"80C","limit":150000},{"section":"80D","limit":1}]'),
            False,
        ),
        "html table": (_read("p.html", b"<table><tr><th>A</th></tr><tr><td>1</td></tr></table>"), False),
        "md table": (_read("m.md", b"| A | B |\n|---|---|\n| 1 | 2 |\n"), False),
        "plain md": (_read("plain.md", b"# Title\n\nHello world.\n"), False),
        # A review flag that says something was not read, or read with doubt, needs a person.
        "duplicate key": (_read("dup.json", b'{"a": 1, "a": 2, "b": "x"}'), True),
        "md too deep": (_read("deep.md", b"# Title\n\nIntro.\n\n" + deep), True),
        "html line limit": (_read("lim.html", ("<p>p</p>" * 50).encode(), small), True),
        "md line limit": (_read("lim.md", "\n\n".join(f"p {i}" for i in range(50)).encode(), small), True),
        "docx chart": (_read("chart.docx", _chart_docx()), True),
        "tracked changes": (_with_flag("docx", "docx.tracked-changes"), True),
        "encoding": (_with_flag("csv", "encoding.guessed"), True),
        "pptx smartart": (_with_flag("pptx", "pptx.smartart", "page"), True),
        "xlsx cell limit": (_with_flag("xlsx", "xlsx.cell-limit", "page"), True),
        # Flags that only inform need no person.
        "tiny images": (_with_flag("docx", "image.tiny-skipped"), False),
        "headers": (_with_flag("docx", "docx.headers-footers"), False),
        "footers": (_with_flag("pptx", "pptx.footers"), False),
        # A table that is not accepted still needs a person.
        "flagged table": (decided(exact_document("docx", ["Item", "31* March, 2025"], table=True)), True),
        # PDF: as before, any table, and only blocking page or document flags.
        "pdf clean": (_pdf_document(), False),
        "pdf review page flag": (_pdf_document([("native.image-backed", "review")]), False),
        "pdf blocking page flag": (_pdf_document([("page.failed", "blocking")]), True),
        "pdf table": (_pdf_document(table=True), True),
    }
    got = {name: needs_review(doc) for name, (doc, _) in cases.items()}
    assert got == {name: want for name, (_, want) in cases.items()}


def test_ten_thousand_one_cell_tables_decide_and_export_in_seconds():
    """Final review I3: lines are grouped by table once per page, not scanned once per table."""
    import time

    from ragnaroq import Config
    from ragnaroq.export.chunks import to_chunks

    n = 10_000
    lines = [
        Line(
            id=f"p1-l{i}",
            page=1,
            order=i,
            raw=f"cell {i}",
            text=f"cell {i}",
            source="html",
            table_id=f"p1-t{i}",
            role="table",
            anchor=DocxAnchor(table=i + 1, cell=(1, 1)),
        )
        for i in range(n)
    ]
    tables = [
        Table(id=f"p1-t{i}", page=1, rows=1, columns=1, cells=[Cell(row=0, column=0, text=f"cell {i}")])
        for i in range(n)
    ]
    figures = [figure(order=i) for i in range(0, n, 2)]
    for i, fig in enumerate(figures):
        fig.id = f"f{i}"
    page = Page(number=1, kind="body", lines=lines, tables=tables, figures=figures)
    doc = exact_document("html", [])
    doc.pages = [page]
    started = time.monotonic()
    for _ in range(3):  # a file is decided three times on its way through index
        decide(doc, Policy())
    chunks = to_chunks(doc, Config())
    md = to_markdown(doc)
    assert time.monotonic() - started < 6
    assert {t.status for t in tables} == {"accepted"}
    assert len([c for c in chunks if c["metadata"]["kind"] == "table"]) == n
    assert md.count("| cell ") == n
