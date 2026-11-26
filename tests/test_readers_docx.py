"""The Word reader: exact anchors for paragraphs, table cells and footnotes."""

import io
import struct
import zipfile
import zlib
from pathlib import Path

import pytest

pytest.importorskip("docx")

from ragnaroq import evaluate, readers  # noqa: E402
from ragnaroq.anchors import DocxAnchor, cite  # noqa: E402
from ragnaroq.config import Config  # noqa: E402
from ragnaroq.readers import ReaderRefused, read_bytes  # noqa: E402

DEMO = Path(__file__).parent.parent / "references" / "demo"
W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
NS = (
    f'xmlns:w="{W}" '
    'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships" '
    'xmlns:wp="http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing" '
    'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" '
    'xmlns:pic="http://schemas.openxmlformats.org/drawingml/2006/picture" '
    'xmlns:c="http://schemas.openxmlformats.org/drawingml/2006/chart" '
    'xmlns:wps="http://schemas.microsoft.com/office/word/2010/wordprocessingShape" '
    'xmlns:mc="http://schemas.openxmlformats.org/markup-compatibility/2006" '
    'xmlns:m="http://schemas.openxmlformats.org/officeDocument/2006/math"'
)
REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
STYLES = (
    f'<w:styles xmlns:w="{W}">'
    '<w:style w:type="paragraph" w:styleId="Heading1"><w:name w:val="heading 1"/></w:style>'
    '<w:style w:type="paragraph" w:styleId="Title"><w:name w:val="Title"/></w:style>'
    '<w:style w:type="paragraph" w:styleId="Caption"><w:name w:val="caption"/></w:style>'
    "</w:styles>"
)


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


def docx_bytes(
    body: str,
    *,
    footnotes: str | None = None,
    endnotes: str | None = None,
    header: str | None = None,
    comments: bool = False,
    media: bool = False,
):
    rels = [f'<Relationship Id="rId1" Type="{REL}/styles" Target="styles.xml"/>']
    parts = {
        "[Content_Types].xml": (
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="rels" '
            'ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
            '<Default Extension="xml" ContentType="application/xml"/>'
            '<Default Extension="png" ContentType="image/png"/>'
            '<Override PartName="/word/document.xml" '
            'ContentType="application/vnd.openxmlformats-officedocument'
            '.wordprocessingml.document.main+xml"/>'
            '<Override PartName="/word/styles.xml" ContentType="application/vnd.openxmlformats-officedocument'
            '.wordprocessingml.styles+xml"/>'
            '<Override PartName="/word/footnotes.xml" '
            'ContentType="application/vnd.openxmlformats-officedocument'
            '.wordprocessingml.footnotes+xml"/>'
            '<Override PartName="/word/endnotes.xml" '
            'ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.endnotes+xml"/>'
            '<Override PartName="/word/header1.xml" '
            'ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.header+xml"/>'
            '<Override PartName="/word/comments.xml" '
            'ContentType="application/vnd.openxmlformats-officedocument'
            '.wordprocessingml.comments+xml"/>'
            "</Types>"
        ),
        "_rels/.rels": (
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            f'<Relationship Id="rId1" Type="{REL}/officeDocument" Target="word/document.xml"/>'
            "</Relationships>"
        ),
        "word/styles.xml": STYLES,
        "word/document.xml": f"<w:document {NS}><w:body>{body}</w:body></w:document>",
    }
    if footnotes is not None:
        rels.append(f'<Relationship Id="rId2" Type="{REL}/footnotes" Target="footnotes.xml"/>')
        parts["word/footnotes.xml"] = f"<w:footnotes {NS}>{footnotes}</w:footnotes>"
    if endnotes is not None:
        rels.append(f'<Relationship Id="rId4" Type="{REL}/endnotes" Target="endnotes.xml"/>')
        parts["word/endnotes.xml"] = f"<w:endnotes {NS}>{endnotes}</w:endnotes>"
    if header is not None:
        rels.append(f'<Relationship Id="rId5" Type="{REL}/header" Target="header1.xml"/>')
        parts["word/header1.xml"] = f"<w:hdr {NS}>{header}</w:hdr>"
    if comments:
        rels.append(f'<Relationship Id="rId3" Type="{REL}/comments" Target="comments.xml"/>')
        parts["word/comments.xml"] = (
            f'<w:comments {NS}><w:comment w:id="0" w:author="A"><w:p><w:r><w:t>Note</w:t></w:r></w:p>'
            "</w:comment></w:comments>"
        )
    if media:
        rels.append(f'<Relationship Id="rId9" Type="{REL}/image" Target="media/image1.png"/>')
    parts["word/_rels/document.xml.rels"] = (
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        + "".join(rels)
        + "</Relationships>"
    )
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, content in parts.items():
            archive.writestr(name, content)
        if media:
            archive.writestr("word/media/image1.png", png())
    return buffer.getvalue()


def p(text="", style=None):
    ppr = f'<w:pPr><w:pStyle w:val="{style}"/></w:pPr>' if style else ""
    run = f"<w:r><w:t>{text}</w:t></w:r>" if text else ""
    return f"<w:p>{ppr}{run}</w:p>"


def picture(alt=None, title=None, rid="rId9"):
    attrs = (f' descr="{alt}"' if alt else "") + (f' title="{title}"' if title else "")
    return (
        '<w:p><w:r><w:drawing><wp:inline><wp:extent cx="952500" cy="476250"/>'
        f'<wp:docPr id="1" name="Picture 1"{attrs}/>'
        '<a:graphic><a:graphicData uri="http://schemas.openxmlformats.org/drawingml/2006/picture">'
        f'<pic:pic><pic:blipFill><a:blip r:embed="{rid}"/></pic:blipFill></pic:pic>'
        "</a:graphicData></a:graphic></wp:inline></w:drawing></w:r></w:p>"
    )


def read_docx(data: bytes, name="t.docx", config=None):
    return read_bytes(data, name, config or Config())


def anchors(result):
    return [(line.anchor, line.text) for line in result.document.lines()]


def flags(result):
    return {flag.rule for flag in result.document.flags}


def test_the_demo_document_scores_by_anchor():
    report = evaluate.run_exact(DEMO, readers.read_bytes)
    scored = {f.name: f for f in report.files}
    assert scored["guide.docx"].error is None
    assert scored["guide.docx"].score.ok, scored["guide.docx"].score


def test_headings_come_from_heading_styles():
    result = read_docx(docx_bytes(p("Guide", "Title") + p("Rates", "Heading1") + p("Body text.")))
    roles = [(line.text, line.role) for line in result.document.lines()]
    assert roles == [("Guide", "heading"), ("Rates", "heading"), ("Body text.", "body")]
    assert result.document.title == "Guide"


def test_paragraph_numbers_count_every_body_paragraph():
    table = f"<w:tbl><w:tr><w:tc>{p('in a cell')}</w:tc></w:tr></w:tbl>"
    result = read_docx(docx_bytes(p("one") + p() + p("three") + table + p("after")))
    assert anchors(result) == [
        (DocxAnchor(paragraph=1), "one"),
        (DocxAnchor(paragraph=3), "three"),
        (DocxAnchor(table=1, cell=(1, 1)), "in a cell"),
        (DocxAnchor(paragraph=4), "after"),
    ]


def test_table_cells_are_numbered_by_row_and_column_and_tables_in_order():
    def row(*cells):
        return "<w:tr>" + "".join(f"<w:tc>{p(c)}</w:tc>" for c in cells) + "</w:tr>"

    body = f"<w:tbl>{row('a', 'b')}</w:tbl>{p('between')}<w:tbl>{row('c')}{row('d', 'e')}</w:tbl>"
    result = read_docx(docx_bytes(body))
    assert [(a.table, a.cell, t) for a, t in anchors(result) if a.table] == [
        (1, (1, 1), "a"),
        (1, (1, 2), "b"),
        (2, (1, 1), "c"),
        (2, (2, 1), "d"),
        (2, (2, 2), "e"),
    ]
    assert len(result.document.pages[0].tables) == 2


def test_tracked_changes_read_as_accepted_and_are_flagged():
    body = (
        '<w:p><w:r><w:t xml:space="preserve">Due on </w:t></w:r>'
        '<w:ins w:id="1" w:author="A"><w:r><w:t>31 July</w:t></w:r></w:ins>'
        '<w:del w:id="2" w:author="A"><w:r><w:delText>30 June</w:delText></w:r></w:del></w:p>'
    )
    result = read_docx(docx_bytes(body))
    assert anchors(result) == [(DocxAnchor(paragraph=1), "Due on 31 July")]
    assert "docx.tracked-changes" in flags(result)


def test_comments_are_left_out_and_flagged():
    body = (
        '<w:p><w:commentRangeStart w:id="0"/><w:r><w:t>Rate is 5%.</w:t></w:r>'
        '<w:commentRangeEnd w:id="0"/><w:r><w:commentReference w:id="0"/></w:r></w:p>'
    )
    result = read_docx(docx_bytes(body, comments=True))
    assert [text for _, text in anchors(result)] == ["Rate is 5%."]
    assert "docx.comments" in flags(result)
    assert "docx.tracked-changes" not in flags(result)


def test_a_footnote_is_its_own_line():
    notes = (
        '<w:footnote w:type="separator" w:id="-1"><w:p><w:r><w:separator/></w:r></w:p></w:footnote>'
        '<w:footnote w:type="continuationSeparator" w:id="0"><w:p><w:r><w:continuationSeparator/></w:r></w:p>'
        "</w:footnote>"
        '<w:footnote w:id="2"><w:p><w:r><w:footnoteRef/></w:r><w:r><w:t>Second.</w:t></w:r></w:p>'
        "</w:footnote>"
        '<w:footnote w:id="5"><w:p><w:r><w:footnoteRef/></w:r><w:r><w:t>First.</w:t></w:r></w:p></w:footnote>'
    )
    body = (
        '<w:p><w:r><w:t>A</w:t></w:r><w:r><w:footnoteReference w:id="5"/></w:r></w:p>'
        '<w:p><w:r><w:t>B</w:t></w:r><w:r><w:footnoteReference w:id="2"/></w:r></w:p>'
    )
    result = read_docx(docx_bytes(body, footnotes=notes))
    assert anchors(result)[2:] == [
        (DocxAnchor(note="footnote 1"), "First."),
        (DocxAnchor(note="footnote 2"), "Second."),
    ]


def test_a_picture_becomes_a_figure_with_its_alt_text_and_caption():
    body = p("Intro") + picture(alt="A bar chart") + p("Figure 1: Collections", "Caption")
    result = read_docx(docx_bytes(body, media=True))
    (figure,) = result.document.pages[0].figures
    assert figure.anchor == DocxAnchor(paragraph=2)
    assert figure.description == "Figure 1: Collections"
    assert figure.description_source == "caption"
    assert result.images[figure.id] == png()
    assert (figure.width, figure.height) == (100, 50)


def test_alt_text_title_and_none_describe_a_picture_without_a_caption():
    cases = [
        (picture(alt="Alt words", title="Title words"), "Alt words", "alt"),
        (picture(title="Title words"), "Title words", "alt"),
        (picture(), "", "none"),
    ]
    for body, description, source in cases:
        result = read_docx(docx_bytes(p("x") + body + p("Plain follow-up."), media=True))
        (figure,) = result.document.pages[0].figures
        assert (figure.description, figure.description_source) == (description, source)


def test_a_chart_object_is_flagged_not_read():
    chart = (
        '<w:p><w:r><w:drawing><wp:inline><wp:docPr id="2" name="Chart 1"/>'
        '<a:graphic><a:graphicData uri="http://schemas.openxmlformats.org/drawingml/2006/chart">'
        '<c:chart r:id="rId7"/></a:graphicData></a:graphic></wp:inline></w:drawing></w:r></w:p>'
    )
    result = read_docx(docx_bytes(p("Before") + chart))
    page = result.document.pages[0]
    assert [f.rule for f in page.flags] == ["chart.not-read"]
    assert page.figures == []


def test_a_broken_file_is_refused_as_invalid():
    good = docx_bytes(p("x"))
    for data in (good[: len(good) // 2], docx_bytes(p("x")).replace(b"<w:body>", b"<w:bod")):
        with pytest.raises(ReaderRefused) as refused:
            from ragnaroq.readers import docx_reader

            docx_reader.read(data, "t.docx", Config())
        assert refused.value.rule == "docx.invalid"


def test_the_zip_limits_apply_before_the_library_opens_the_file():
    from dataclasses import replace

    with pytest.raises(ReaderRefused) as refused:
        read_docx(docx_bytes(p("x")), config=replace(Config(), max_zip_entries=2))
    assert refused.value.rule == "file.zip-limits"


# --- R16: structures that must not lose or repeat text -------------------------------------------

MC = "http://schemas.openxmlformats.org/markup-compatibility/2006"


def sdt(inner):
    return f"<w:sdt><w:sdtPr/><w:sdtContent>{inner}</w:sdtContent></w:sdt>"


def tc(inner):
    return f"<w:tc>{inner}</w:tc>"


def tr(*cells):
    return "<w:tr>" + "".join(cells) + "</w:tr>"


def tbl(*rows):
    return "<w:tbl>" + "".join(rows) + "</w:tbl>"


def lines(result):
    return [(cite(a), t) for a, t in anchors(result)]


def test_content_controls_and_custom_xml_are_unwrapped_at_every_level():
    body = (
        p("one")
        + f"<w:customXml>{p('two')}{sdt(p('three'))}</w:customXml>"
        + tbl(sdt(tr(tc(p("a")), sdt(tc(p("b"))))))
        + p("four")
    )
    notes = f'<w:footnote w:id="1">{sdt(p("In a note."))}</w:footnote>'
    body += '<w:p><w:r><w:footnoteReference w:id="1"/></w:r></w:p>'
    result = read_docx(docx_bytes(body, footnotes=notes))
    assert lines(result) == [
        ("¶1", "one"),
        ("¶2", "two"),
        ("¶3", "three"),
        ("table 1, row 1, column 1", "a"),
        ("table 1, row 1, column 2", "b"),
        ("¶4", "four"),
        ("footnote 1", "In a note."),
    ]


def test_a_nested_table_is_read_into_the_outer_cell():
    inner = tbl(tr(tc(p("x")), tc(p("y"))), tr(tc(p("z"))))
    body = tbl(tr(tc(p("outer") + inner + p("tail")), tc(p("b")))) + tbl(tr(tc(p("second"))))
    result = read_docx(docx_bytes(body))
    assert lines(result) == [
        ("table 1, row 1, column 1", "outer\ntail"),
        ("table 1, row 1, column 1", "x"),
        ("table 1, row 1, column 1", "y"),
        ("table 1, row 1, column 1", "z"),
        ("table 1, row 1, column 2", "b"),
        ("table 2, row 1, column 1", "second"),
    ]


def test_tables_in_a_footnote_are_lines_of_that_note():
    notes = f'<w:footnote w:id="1">{p("Intro.")}{tbl(tr(tc(p("c1")), tc(p("c2"))))}</w:footnote>'
    body = '<w:p><w:r><w:t>A</w:t></w:r><w:r><w:footnoteReference w:id="1"/></w:r></w:p>'
    result = read_docx(docx_bytes(body, footnotes=notes))
    assert lines(result)[1:] == [("footnote 1", "Intro."), ("footnote 1", "c1"), ("footnote 1", "c2")]


def test_endnotes_are_read_like_footnotes_with_their_own_numbering():
    foot = (
        '<w:footnote w:type="separator" w:id="-1"><w:p><w:r><w:separator/></w:r></w:p></w:footnote>'
        f'<w:footnote w:id="1">{p("Foot.")}</w:footnote>'
    )
    end = (
        '<w:endnote w:type="separator" w:id="-1"><w:p><w:r><w:separator/></w:r></w:p></w:endnote>'
        '<w:endnote w:type="continuationSeparator" w:id="0"><w:p/></w:endnote>'
        f'<w:endnote w:id="3">{p("Second end.")}</w:endnote>'
        f'<w:endnote w:id="2">{p("First end.")}</w:endnote>'
    )
    body = (
        '<w:p><w:r><w:t>A</w:t></w:r><w:r><w:endnoteReference w:id="2"/></w:r>'
        '<w:r><w:footnoteReference w:id="1"/></w:r></w:p>'
        '<w:p><w:r><w:endnoteReference w:id="3"/></w:r></w:p>'
    )
    result = read_docx(docx_bytes(body, footnotes=foot, endnotes=end))
    assert lines(result)[1:] == [
        ("footnote 1", "Foot."),
        ("endnote 1", "First end."),
        ("endnote 2", "Second end."),
    ]


def test_headers_and_footers_are_left_out_and_flagged():
    result = read_docx(docx_bytes(p("Body."), header=p("Page header")))
    assert [t for _, t in anchors(result)] == ["Body."]
    assert "docx.headers-footers" in flags(result)
    assert "docx.headers-footers" not in flags(read_docx(docx_bytes(p("Body."), header=p())))
    assert "docx.headers-footers" not in flags(read_docx(docx_bytes(p("Body."))))


def test_tracked_moves_read_the_destination_once_and_are_flagged():
    body = (
        '<w:p><w:r><w:t xml:space="preserve">Start </w:t></w:r>'
        '<w:moveFrom w:id="1" w:author="A"><w:r><w:t>moved</w:t></w:r></w:moveFrom>'
        '<w:moveTo w:id="2" w:author="A"><w:r><w:t>kept</w:t></w:r></w:moveTo></w:p>'
    )
    result = read_docx(docx_bytes(body))
    assert lines(result) == [("¶1", "Start kept")]
    assert "docx.tracked-changes" in flags(result)


def test_a_tracked_change_only_in_a_note_is_flagged():
    notes = (
        '<w:footnote w:id="1"><w:p><w:ins w:id="5" w:author="A"><w:r><w:t>New.</w:t></w:r></w:ins></w:p>'
        "</w:footnote>"
    )
    body = '<w:p><w:r><w:footnoteReference w:id="1"/></w:r></w:p>'
    result = read_docx(docx_bytes(body, footnotes=notes))
    assert lines(result) == [("footnote 1", "New.")]
    assert "docx.tracked-changes" in flags(result)


def textbox(*paragraphs):
    return f"<w:txbxContent>{''.join(paragraphs)}</w:txbxContent>"


def test_a_text_box_is_separate_lines_after_its_host_paragraph():
    host = (
        "<w:p><w:r><w:t>host</w:t></w:r><w:r><w:drawing><wp:anchor><a:graphic><a:graphicData>"
        f"<wps:wsp><wps:txbx>{textbox(p('boxed one'), p('boxed two'))}</wps:txbx></wps:wsp>"
        "</a:graphicData></a:graphic></wp:anchor></w:drawing></w:r></w:p>"
    )
    result = read_docx(docx_bytes(host + p("next")))
    assert lines(result) == [("¶1", "host"), ("¶1", "boxed one"), ("¶1", "boxed two"), ("¶2", "next")]


def test_alternate_content_reads_the_choice_and_skips_the_fallback():
    box = textbox(p("boxed"))
    host = (
        f'<w:p xmlns:mc="{MC}"><w:r><w:t>host</w:t></w:r><w:r><mc:AlternateContent>'
        f"<mc:Choice><w:drawing>{box}</w:drawing></mc:Choice>"
        f"<mc:Fallback><w:pict>{box}</w:pict><w:t>fallback text</w:t></mc:Fallback>"
        "</mc:AlternateContent></w:r></w:p>"
    )
    result = read_docx(docx_bytes(host))
    assert lines(result) == [("¶1", "host"), ("¶1", "boxed")]


def math(*symbols):
    return "<m:oMath>" + "".join(f"<m:r><m:t>{s}</m:t></m:r>" for s in symbols) + "</m:oMath>"


def test_an_equation_is_read_in_place_and_its_line_is_flagged():
    message = (
        "An equation is read as its plain symbols; fractions, powers and layout are lost; "
        "compare with the page."
    )
    lead = '<w:r><w:t xml:space="preserve">Tax = </w:t></w:r>'
    body = f"<w:p>{lead}{math('x', '+', 'y')}</w:p><w:p>{math('E', '=', 'mc')}</w:p>" + p("Plain.")
    result = read_docx(docx_bytes(body))
    first, second, plain = result.document.lines()
    assert (first.text, second.text) == ("Tax = x + y", "E = mc")
    assert [(f.rule, f.message) for f in first.flags] == [("docx.equation", message)]
    assert [f.rule for f in second.flags] == ["docx.equation"]
    assert first.status == "flagged" and plain.flags == []


def test_an_equation_under_a_fallback_is_not_read_twice():
    body = (
        f'<w:p><w:r><w:t>Real </w:t></w:r><mc:AlternateContent><mc:Choice Requires="x">{math("a")}'
        f"</mc:Choice><mc:Fallback>{math('copy')}</mc:Fallback></mc:AlternateContent></w:p>"
    )
    result = read_docx(docx_bytes(body))
    assert [line.text for line in result.document.lines()] == ["Real a"]
