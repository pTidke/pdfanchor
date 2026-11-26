"""The PowerPoint reader: exact anchors for slide text, table cells, notes and pictures."""

import io
import struct
import zipfile
import zlib
from dataclasses import replace
from pathlib import Path

import pytest

pytest.importorskip("pptx")

from pptx import Presentation  # noqa: E402
from pptx.chart.data import CategoryChartData  # noqa: E402
from pptx.enum.chart import XL_CHART_TYPE  # noqa: E402
from pptx.opc.constants import RELATIONSHIP_TYPE as RT  # noqa: E402
from pptx.opc.package import Part  # noqa: E402
from pptx.opc.packuri import PackURI  # noqa: E402
from pptx.oxml import parse_xml  # noqa: E402
from pptx.util import Inches  # noqa: E402

from ragnaroq import evaluate, readers  # noqa: E402
from ragnaroq.anchors import PptxAnchor, cite  # noqa: E402
from ragnaroq.config import Config  # noqa: E402
from ragnaroq.readers import ReaderRefused, read_bytes  # noqa: E402

DEMO = Path(__file__).parent.parent / "references" / "demo"
NS = (
    'xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main" '
    'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" '
    'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships" '
    'xmlns:mc="http://schemas.openxmlformats.org/markup-compatibility/2006" '
    'xmlns:c="http://schemas.openxmlformats.org/drawingml/2006/chart" '
    'xmlns:a14="http://schemas.microsoft.com/office/drawing/2010/main" '
    'xmlns:m="http://schemas.openxmlformats.org/officeDocument/2006/math"'
)
TABLE_URI = "http://schemas.openxmlformats.org/drawingml/2006/table"


def png() -> bytes:
    def chunk(kind, body):
        return struct.pack(">I", len(body)) + kind + body + struct.pack(">I", zlib.crc32(kind + body))

    header = struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", header)
        + chunk(b"IDAT", zlib.compress(b"\x00\xff\x00\x00"))
        + chunk(b"IEND", b"")
    )


def para(text="", extra=""):
    runs = f"<a:r><a:t>{text}</a:t></a:r>" if text else ""
    return f"<a:p>{runs}{extra}</a:p>"


def sp(name, *paragraphs, ph=None, ident=50):
    holder = f'<p:ph type="{ph}"/>' if ph else ""
    body = "".join(paragraphs) or "<a:p/>"
    return (
        f'<p:sp {NS}><p:nvSpPr><p:cNvPr id="{ident}" name="{name}"/><p:cNvSpPr/>'
        f"<p:nvPr>{holder}</p:nvPr></p:nvSpPr><p:spPr/>"
        f"<p:txBody><a:bodyPr/>{body}</p:txBody></p:sp>"
    )


def group(name, *children):
    return (
        f'<p:grpSp {NS}><p:nvGrpSpPr><p:cNvPr id="60" name="{name}"/><p:cNvGrpSpPr/><p:nvPr/></p:nvGrpSpPr>'
        "<p:grpSpPr/>" + "".join(children) + "</p:grpSp>"
    )


def alternate(choice, fallback):
    return (
        f'<mc:AlternateContent {NS}><mc:Choice Requires="a14">{choice}</mc:Choice>'
        f"<mc:Fallback>{fallback}</mc:Fallback></mc:AlternateContent>"
    )


def frame(name, uri, inner=""):
    return (
        f'<p:graphicFrame {NS}><p:nvGraphicFramePr><p:cNvPr id="70" name="{name}"/><p:cNvGraphicFramePr/>'
        '<p:nvPr/></p:nvGraphicFramePr><p:xfrm><a:off x="0" y="0"/><a:ext cx="100" cy="100"/></p:xfrm>'
        f'<a:graphic><a:graphicData uri="{uri}">{inner}</a:graphicData></a:graphic></p:graphicFrame>'
    )


def pic(name, blip, alt=None, title=None, ph=None, size=(952500, 476250)):
    attrs = (f' descr="{alt}"' if alt else "") + (f' title="{title}"' if title else "")
    holder = f'<p:ph type="{ph}"/>' if ph else ""
    return (
        f'<p:pic {NS}><p:nvPicPr><p:cNvPr id="80" name="{name}"{attrs}/><p:cNvPicPr/>'
        f"<p:nvPr>{holder}</p:nvPr></p:nvPicPr>"
        f"<p:blipFill>{blip}</p:blipFill>"
        '<p:spPr><a:xfrm><a:off x="0" y="0"/>'
        f'<a:ext cx="{size[0]}" cy="{size[1]}"/></a:xfrm></p:spPr></p:pic>'
    )


def cell_xml(*paragraphs, attrs=""):
    return f"<a:tc{attrs}><a:txBody><a:bodyPr/>{''.join(paragraphs) or '<a:p/>'}</a:txBody><a:tcPr/></a:tc>"


def table(name, rows):
    inner = (
        "<a:tbl><a:tblPr/><a:tblGrid/>"
        + "".join(f"<a:tr h='1'>{''.join(row)}</a:tr>" for row in rows)
        + "</a:tbl>"
    )
    return frame(name, TABLE_URI, inner)


def add(slide, *elements):
    for element in elements:
        slide.shapes._spTree.append(parse_xml(element))


def save(deck) -> bytes:
    buffer = io.BytesIO()
    deck.save(buffer)
    return buffer.getvalue()


def new_deck(*titles):
    """A deck with one 'title only' slide per title."""
    deck = Presentation()
    for text in titles:
        slide = deck.slides.add_slide(deck.slide_layouts[5])
        slide.shapes.title.text = text
    return deck


def read_pptx(data, name="t.pptx", config=None):
    return read_bytes(data, name, config or Config())


def lines(result):
    return [(line.anchor, line.text) for line in result.document.lines()]


def texts(result):
    return [line.text for line in result.document.lines()]


def flags(result):
    return {flag.rule for flag in result.document.flags}


def page_flags(result, number=1):
    return {flag.rule for flag in result.document.pages[number - 1].flags}


def with_image(slide, blob=None):
    """The relationship id of a picture part on the slide."""
    return slide.part.get_or_add_image_part(io.BytesIO(blob or png()))[1]


# --- the plan's tests ----------------------------------------------------------------------------


def test_the_demo_deck_scores_by_anchor():
    report = evaluate.run_exact(DEMO, readers.read_bytes)
    scored = {f.name: f for f in report.files}
    assert scored["deck.pptx"].error is None
    assert scored["deck.pptx"].score.ok, scored["deck.pptx"].score


def test_the_title_is_a_heading_and_each_slide_a_part():
    deck = new_deck("Budget", "Slabs")
    deck.slides.add_slide(deck.slide_layouts[6])  # blank: no title
    result = read_pptx(save(deck))
    pages = result.document.pages
    assert [(p.number, p.kind, p.name) for p in pages] == [
        (1, "slide", "Budget"),
        (2, "slide", "Slabs"),
        (3, "slide", ""),
    ]
    assert result.document.source.pages == 3
    heading = next(result.document.lines())
    assert (heading.text, heading.role) == ("Budget", "heading")
    assert result.document.title == "Budget"


def test_bullets_are_lines_with_their_shape_and_paragraph():
    deck = Presentation()
    slide = deck.slides.add_slide(deck.slide_layouts[1])
    slide.shapes.title.text = "Key changes"
    body = slide.placeholders[1]
    body.text_frame.text = "New slab"
    body.text_frame.add_paragraph().text = "Standard deduction"
    result = read_pptx(save(deck))
    shape = body.name
    assert shape == "Content Placeholder 2"
    assert lines(result)[1:] == [
        (PptxAnchor(slide=1, shape=shape, paragraph=0), "New slab"),
        (PptxAnchor(slide=1, shape=shape, paragraph=1), "Standard deduction"),
    ]
    assert [line.role for line in result.document.lines()] == ["heading", "body", "body"]


def test_speaker_notes_are_lines_marked_as_notes():
    deck = new_deck("Slide")
    deck.slides[0].notes_slide.notes_text_frame.text = "First note"
    deck.slides[0].notes_slide.notes_text_frame.add_paragraph().text = "Second note"
    result = read_pptx(save(deck))
    assert lines(result)[1:] == [
        (PptxAnchor(slide=1, shape="Notes", paragraph=0, notes=True), "First note"),
        (PptxAnchor(slide=1, shape="Notes", paragraph=1, notes=True), "Second note"),
    ]


def test_shapes_inside_a_group_are_read():
    deck = new_deck("Slide")
    add(deck.slides[0], group("Group 1", sp("Inner", para("Inside the group"))))
    result = read_pptx(save(deck))
    assert (PptxAnchor(slide=1, shape="Inner"), "Inside the group") in lines(result)


def test_a_picture_without_alt_text_has_no_description_until_ocr():
    deck = new_deck("Slide")
    deck.slides[0].shapes.add_picture(io.BytesIO(png()), Inches(1), Inches(1)).name = "Picture 2"
    deck.slides[0].shapes[-1]._element.nvPicPr.cNvPr.attrib.pop("descr", None)
    result = read_pptx(save(deck))
    (figure,) = result.document.pages[0].figures
    assert figure.anchor == PptxAnchor(slide=1, shape="Picture 2")
    assert (figure.description, figure.description_source) == ("", "none")
    assert result.images[figure.id] == png()
    assert figure.order == 1  # after the title line


def test_a_slide_table_is_a_table_with_cell_anchors():
    deck = new_deck("Slide")
    shape = deck.slides[0].shapes.add_table(2, 2, Inches(1), Inches(1), Inches(4), Inches(1))
    shape.name = "Table 2"
    for r, row in enumerate((("Slab", "Rate"), ("Up to 12 lakh", "Nil"))):
        for c, text in enumerate(row):
            shape.table.cell(r, c).text = text
    result = read_pptx(save(deck))
    assert lines(result)[1:] == [
        (PptxAnchor(slide=1, shape="Table 2", cell=(1, 1)), "Slab"),
        (PptxAnchor(slide=1, shape="Table 2", cell=(1, 2)), "Rate"),
        (PptxAnchor(slide=1, shape="Table 2", cell=(2, 1)), "Up to 12 lakh"),
        (PptxAnchor(slide=1, shape="Table 2", cell=(2, 2)), "Nil"),
    ]
    assert cite(lines(result)[2][0]) == "slide 1, Table 2, row 1, column 2"
    (grid,) = result.document.pages[0].tables
    assert (grid.id, grid.page, grid.rows, grid.columns) == ("p1-t1", 1, 2, 2)
    assert [(c.row, c.column, c.text) for c in grid.cells][3] == (1, 1, "Nil")
    assert all(line.table_id == "p1-t1" for line in list(result.document.lines())[1:])


def test_a_chart_is_flagged_not_read():
    deck = new_deck("Slide")
    data = CategoryChartData()
    data.categories = ["A", "B"]
    data.add_series("S", (1, 2))
    chart = deck.slides[0].shapes.add_chart(XL_CHART_TYPE.COLUMN_CLUSTERED, 0, 0, 100, 100, data)
    chart.name = "Chart 3"
    result = read_pptx(save(deck))
    (flag,) = [f for f in result.document.pages[0].flags if f.rule == "chart.not-read"]
    assert "slide 1, Chart 3" in flag.message
    assert texts(result) == ["Slide"]


# --- R17: PowerPoint text that must never vanish -------------------------------------------------


def test_a_shapes_are_read_in_tree_order_through_groups_at_any_depth():
    deck = new_deck("Slide")
    add(
        deck.slides[0],
        sp("One", para("first")),
        group("G1", sp("Two", para("second")), group("G2", group("G3", sp("Three", para("third"))))),
        sp("Four", para("fourth")),
    )
    result = read_pptx(save(deck))
    assert texts(result) == ["Slide", "first", "second", "third", "fourth"]


def test_a_alternate_content_is_read_from_the_choice_never_the_fallback():
    deck = new_deck("Slide")
    both = alternate(sp("Chosen", para("choice text")), sp("Fallback", para("fallback text")))
    add(
        deck.slides[0],
        both,
        group("G", alternate(sp("Deep", para("deep choice")), sp("F2", para("deep fb")))),
    )
    result = read_pptx(save(deck))
    assert texts(result) == ["Slide", "choice text", "deep choice"]
    assert lines(result)[1][0].shape == "Chosen"


def test_b_a_picture_placeholder_is_a_picture_and_alt_text_falls_back_to_the_title():
    deck = new_deck("Slide")
    slide = deck.slides[0]
    rid = with_image(slide)
    blip = f'<a:blip r:embed="{rid}"/>'
    add(
        slide,
        pic("Picture Placeholder 4", blip, title="Only a title", ph="pic"),
        pic("Plain", blip, alt="The alt", title="The title"),
    )
    result = read_pptx(save(deck))
    first, second = result.document.pages[0].figures
    assert (first.anchor.shape, first.description, first.description_source) == (
        "Picture Placeholder 4",
        "Only a title",
        "alt",
    )
    assert (second.description, second.description_source) == ("The alt", "alt")
    assert (first.width, first.height) == (100, 50)
    assert result.images[first.id] == png() and result.images[second.id] == png()
    assert [f.id for f in (first, second)] == ["p1-f1", "p1-f2"]


def test_b_a_linked_picture_is_not_fetched_and_is_flagged():
    deck = new_deck("Slide")
    slide = deck.slides[0]
    link = slide.part.relate_to("https://example.com/x.png", RT.IMAGE, is_external=True)
    add(slide, pic("Linked", f'<a:blip r:link="{link}"/>', alt="A remote chart"))
    result = read_pptx(save(deck))
    assert "pptx.linked-image" in page_flags(result)
    (figure,) = result.document.pages[0].figures
    assert (figure.description, figure.description_source) == ("A remote chart", "alt")
    assert result.images == {}


def test_b_a_picture_whose_image_part_is_missing_is_flagged():
    deck = new_deck("Slide")
    add(deck.slides[0], pic("Broken", '<a:blip r:embed="rId99"/>', alt="Lost"))
    result = read_pptx(save(deck))
    assert "pptx.image-missing" in page_flags(result)
    assert result.images == {}


def test_c_table_cells_join_their_paragraphs_and_skip_merged_away_cells():
    deck = new_deck("Slide")
    rows = [
        [
            cell_xml(para("Head"), para(""), para("Sub"), attrs=' gridSpan="2"'),
            cell_xml(attrs=' hMerge="1"'),
            cell_xml(para("Z")),
        ],
        [
            cell_xml(para("Tall"), attrs=' rowSpan="2"'),
            cell_xml(para("B2")),
            cell_xml(para("C2")),
        ],
        [cell_xml(attrs=' vMerge="1"'), cell_xml(para("B3")), cell_xml()],
    ]
    add(deck.slides[0], table("Grid", rows))
    result = read_pptx(save(deck))
    assert lines(result)[1:] == [
        (PptxAnchor(slide=1, shape="Grid", cell=(1, 1)), "Head\nSub"),
        (PptxAnchor(slide=1, shape="Grid", cell=(1, 3)), "Z"),
        (PptxAnchor(slide=1, shape="Grid", cell=(2, 1)), "Tall"),
        (PptxAnchor(slide=1, shape="Grid", cell=(2, 2)), "B2"),
        (PptxAnchor(slide=1, shape="Grid", cell=(2, 3)), "C2"),
        (PptxAnchor(slide=1, shape="Grid", cell=(3, 2)), "B3"),
    ]
    (grid,) = result.document.pages[0].tables
    assert (grid.rows, grid.columns) == (3, 3)
    assert all(line.paragraph >= 1 for line in result.document.lines())


def test_d_only_the_notes_body_is_read_not_the_slide_number_or_header():
    deck = new_deck("Slide")
    notes = deck.slides[0].notes_slide
    notes.notes_text_frame.text = "The real note"
    others = [s for s in notes.placeholders if s.placeholder_format.type != 2 and s.has_text_frame]
    for shape in others:
        if shape.placeholder_format.type == 13:  # slide number
            shape.text_frame.text = "7"
    result = read_pptx(save(deck))
    assert texts(result) == ["Slide", "The real note"]
    assert any(s.placeholder_format.type == 13 for s in notes.placeholders)


def test_e_footer_date_and_slide_number_placeholders_are_left_out_and_flagged():
    deck = new_deck("Slide")
    add(
        deck.slides[0],
        sp("Footer Placeholder 3", para("Confidential"), ph="ftr"),
        sp("Date Placeholder 4", para("26 Nov"), ph="dt"),
        sp("Slide Number Placeholder 5", para("9"), ph="sldNum"),
        sp("Body", para("kept")),
    )
    result = read_pptx(save(deck))
    assert texts(result) == ["Slide", "kept"]
    assert "pptx.footers" in flags(result)


def test_e_an_empty_footer_is_not_flagged_and_layout_text_is_never_read():
    deck = new_deck("Slide")
    add(deck.slides[0], sp("Footer Placeholder 3", ph="ftr"))
    layout_shape = deck.slides[0].slide_layout.placeholders[0]
    layout_shape.text_frame.text = "LAYOUT TEXT"
    deck.slide_master.placeholders[0].text_frame.text = "MASTER TEXT"
    result = read_pptx(save(deck))
    assert texts(result) == ["Slide"]
    assert "pptx.footers" not in flags(result)


def test_f_a_smartart_and_other_objects_are_flagged_not_read():
    deck = new_deck("Slide")
    add(
        deck.slides[0],
        frame("Diagram 1", "http://schemas.openxmlformats.org/drawingml/2006/diagram"),
        frame("Object 2", "http://schemas.openxmlformats.org/presentationml/2006/ole"),
        frame("Video 3", "http://example.com/media"),
    )
    result = read_pptx(save(deck))
    found = [f for f in result.document.pages[0].flags]
    assert [f.rule for f in found] == ["pptx.smartart", "pptx.object-not-read", "pptx.object-not-read"]
    assert "slide 1, Diagram 1" in found[0].message
    assert all(f.severity == "review" for f in found)
    assert texts(result) == ["Slide"]


def test_f_a_chart_inside_a_group_is_still_flagged():
    deck = new_deck("Slide")
    chart = frame("Chart 9", "http://schemas.openxmlformats.org/drawingml/2006/chart", "<c:chart/>")
    add(deck.slides[0], group("G", chart))
    result = read_pptx(save(deck))
    assert "chart.not-read" in page_flags(result)


def test_g_a_hidden_slide_is_read_and_flagged():
    deck = new_deck("Shown", "Hidden")
    deck.slides[1]._element.set("show", "0")
    result = read_pptx(save(deck))
    assert texts(result) == ["Shown", "Hidden"]
    assert page_flags(result, 1) == set()
    assert page_flags(result, 2) == {"pptx.hidden-slide"}


def test_h_comments_are_left_out_and_flagged():
    deck = new_deck("Slide")
    slide = deck.slides[0]
    part = Part(
        PackURI("/ppt/comments/comment1.xml"),
        "application/vnd.openxmlformats-officedocument.presentationml.comments+xml",
        slide.part.package,
        b"<p:cmLst xmlns:p='http://schemas.openxmlformats.org/presentationml/2006/main'>"
        b"<p:cm authorId='0'><p:text>Check this rate</p:text></p:cm></p:cmLst>",
    )
    slide.part.relate_to(part, RT.COMMENTS)
    result = read_pptx(save(deck))
    assert texts(result) == ["Slide"]
    assert "pptx.comments" in flags(result)
    assert "pptx.comments" not in flags(read_pptx(save(new_deck("Slide"))))


def test_i_paragraph_numbers_count_empty_paragraphs_and_breaks_and_tabs_become_spaces():
    deck = new_deck("Slide")
    add(
        deck.slides[0],
        sp(
            "Body",
            para("zero"),
            para(""),
            "<a:p><a:r><a:t>one</a:t></a:r><a:br/><a:r><a:t>two</a:t></a:r><a:tab/>"
            "<a:r><a:t>three</a:t></a:r></a:p>",
            "<a:p><a:endParaRPr/></a:p>",
            para("four"),
        ),
    )
    result = read_pptx(save(deck))
    assert lines(result)[1:] == [
        (PptxAnchor(slide=1, shape="Body", paragraph=0), "zero"),
        (PptxAnchor(slide=1, shape="Body", paragraph=2), "one two three"),
        (PptxAnchor(slide=1, shape="Body", paragraph=4), "four"),
    ]


def test_j_ids_follow_the_slide_number_and_each_kind_counts_by_itself():
    deck = new_deck("One", "Two")
    slide = deck.slides[1]
    add(slide, sp("Body", para("text")), table("Grid", [[cell_xml(para("c"))]]))
    slide.shapes.add_picture(io.BytesIO(png()), Inches(1), Inches(1))
    result = read_pptx(save(deck))
    two = result.document.pages[1]
    assert [line.id for line in two.lines] == ["p2-l1", "p2-l2", "p2-l3"]
    assert [t.id for t in two.tables] == ["p2-t1"]
    assert [f.id for f in two.figures] == ["p2-f1"]
    assert result.document.pages[0].lines[0].id == "p1-l1"


def test_k_the_line_cap_counts_the_whole_deck_and_flags_where_it_stops():
    deck = new_deck("One", "Two", "Three")
    result = read_pptx(save(deck), config=replace(Config(), max_cells=2))
    assert texts(result) == ["One", "Two"]
    assert page_flags(result, 1) == set()
    assert page_flags(result, 2) == set()
    assert page_flags(result, 3) == {"pptx.line-limit"}
    assert [p.name for p in result.document.pages] == ["One", "Two", "Three"]


def test_l_a_broken_file_is_refused_as_invalid():
    from ragnaroq.readers import pptx_reader

    good = save(new_deck("Slide"))
    broken_xml = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(good)) as source, zipfile.ZipFile(broken_xml, "w") as target:
        for item in source.infolist():
            data = source.read(item.filename)
            if item.filename == "ppt/slides/slide1.xml":
                data = data.replace(b"<p:cSld", b"<p:cSl")
            target.writestr(item.filename, data)
    no_main = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(good)) as source, zipfile.ZipFile(no_main, "w") as target:
        for item in source.infolist():
            if item.filename != "ppt/presentation.xml":
                target.writestr(item.filename, source.read(item.filename))
    for data in (good[: len(good) // 2], broken_xml.getvalue(), no_main.getvalue()):
        with pytest.raises(ReaderRefused) as refused:
            pptx_reader.read(data, "t.pptx", Config())
        assert refused.value.rule == "pptx.invalid"


def test_l_a_programming_error_is_not_turned_into_a_refusal(monkeypatch):
    from ragnaroq.readers import pptx_reader

    def boom(self, *args, **kwargs):
        raise RuntimeError("bug")

    monkeypatch.setattr(pptx_reader._Reader, "slide", boom)
    with pytest.raises(RuntimeError):
        pptx_reader.read(save(new_deck("Slide")), "t.pptx", Config())


def test_the_zip_limits_apply_before_the_library_opens_the_file():
    with pytest.raises(ReaderRefused) as refused:
        read_pptx(save(new_deck("Slide")), config=replace(Config(), max_zip_entries=2))
    assert refused.value.rule == "file.zip-limits"


def test_a_deck_with_no_slides_reads_as_no_pages():
    result = read_pptx(save(Presentation()))
    assert result.document.source.pages == 0
    assert list(result.document.lines()) == []


# --- R19: fix round 1 ----------------------------------------------------------------------------

EQUATION_FLAG = (
    "An equation is read as its plain symbols; fractions, powers and layout are lost; compare with the slide."
)


def equation(*symbols, fallback="FALLBACK"):
    pieces = "".join(f"<m:r><m:t>{s}</m:t></m:r>" for s in symbols)
    return (
        '<mc:AlternateContent><mc:Choice Requires="a14"><a14:m><m:oMathPara><m:oMath>'
        f"{pieces}</m:oMath></m:oMathPara></a14:m></mc:Choice>"
        f"<mc:Fallback><a:r><a:t>{fallback}</a:t></a:r></mc:Fallback></mc:AlternateContent>"
    )


def test_i1_an_equation_is_read_in_place_and_its_line_is_flagged():
    deck = new_deck("Slide")
    lead = "<a:r><a:t>Tax = </a:t></a:r>"
    add(deck.slides[0], sp("Body", f"<a:p>{lead}{equation('x', '+', 'y')}</a:p>"))
    result = read_pptx(save(deck))
    (line,) = [line for line in result.document.lines() if line.role == "body"]
    assert (line.text, line.anchor) == ("Tax = x + y", PptxAnchor(slide=1, shape="Body", paragraph=0))
    assert [(f.rule, f.message) for f in line.flags] == [("pptx.equation", EQUATION_FLAG)]
    assert line.status == "flagged"
    assert "FALLBACK" not in "".join(texts(result))


def test_i1_a_paragraph_holding_only_an_equation_is_a_line():
    deck = new_deck("Slide")
    add(deck.slides[0], sp("Body", f"<a:p>{equation('E', '=', 'mc')}</a:p>", para("after")))
    result = read_pptx(save(deck))
    assert lines(result)[1:] == [
        (PptxAnchor(slide=1, shape="Body", paragraph=0), "E = mc"),
        (PptxAnchor(slide=1, shape="Body", paragraph=1), "after"),
    ]
    flagged = [line.text for line in result.document.lines() if line.flags]
    assert flagged == ["E = mc"]


def test_i1_an_equation_in_a_table_cell_flags_the_cell_line():
    deck = new_deck("Slide")
    cell = cell_xml(f"<a:p>{equation('a', '+', 'b')}</a:p>")
    add(deck.slides[0], table("Grid", [[cell]]))
    result = read_pptx(save(deck))
    line = list(result.document.lines())[1]
    assert (line.text, [f.rule for f in line.flags]) == ("a + b", ["pptx.equation"])


def test_m1_an_image_fill_or_background_is_flagged_not_a_figure():
    deck = new_deck("Slide")
    slide = deck.slides[0]
    rid = with_image(slide)
    fill = f'<a:blipFill><a:blip r:embed="{rid}"/></a:blipFill>'
    shape = sp("Filled", para("over the fill")).replace("<p:spPr/>", f"<p:spPr>{fill}</p:spPr>")
    add(slide, shape)
    background = parse_xml(
        f'<p:bg {NS}><p:bgPr><a:blipFill><a:blip r:embed="{rid}"/></a:blipFill></p:bgPr></p:bg>'
    )
    slide.element.cSld.insert(0, background)
    result = read_pptx(save(deck))
    found = [f for f in result.document.pages[0].flags if f.rule == "pptx.image-fill"]
    assert [f.message for f in found] == ["An image fill is not read."] * 2
    assert result.document.pages[0].figures == []
    assert "over the fill" in texts(result)


def test_m2_alternate_content_with_no_choice_is_flagged():
    deck = new_deck("Slide")
    add(
        deck.slides[0],
        f"<mc:AlternateContent {NS}><mc:Fallback>{sp('F', para('lost'))}</mc:Fallback></mc:AlternateContent>",
    )
    result = read_pptx(save(deck))
    assert "pptx.object-not-read" in page_flags(result)
    assert "lost" not in texts(result)


def test_m3_a_bad_extent_is_zero_and_a_walk_error_is_not_an_invalid_file(monkeypatch):
    from ragnaroq.readers import pptx_reader

    deck = new_deck("Slide")
    slide = deck.slides[0]
    blip = f'<a:blip r:embed="{with_image(slide)}"/>'
    add(slide, pic("Odd", blip).replace('cx="952500"', 'cx="wide"'))
    (figure,) = read_pptx(save(deck)).document.pages[0].figures
    assert (figure.width, figure.height) == (0, 50)

    def boom(self, *args, **kwargs):
        raise KeyError("bug")

    monkeypatch.setattr(pptx_reader._Reader, "slide", boom)
    with pytest.raises(KeyError):
        pptx_reader.read(save(new_deck("Slide")), "t.pptx", Config())


def test_m4_a_table_inside_a_group_inside_a_group_is_read():
    deck = new_deck("Slide")
    inner = table("Deep", [[cell_xml(para("buried"))]])
    add(deck.slides[0], group("Outer", group("Inner", inner)))
    result = read_pptx(save(deck))
    assert lines(result)[1:] == [(PptxAnchor(slide=1, shape="Deep", cell=(1, 1)), "buried")]
    assert len(result.document.pages[0].tables) == 1
