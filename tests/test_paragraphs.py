"""Paragraphs with the lines and characters they come from."""

import pytest

from ragnaroq.anchors import PdfAnchor
from ragnaroq.config import Config
from ragnaroq.model import Document, Engine, Figure, Line, Page, Source
from ragnaroq.readers import read_bytes
from ragnaroq.semantic import locate, numbering, paragraphs


def pdf_with_lines(texts, paragraph=1, **changes):
    lines = [
        Line(
            id=f"l{i}",
            page=1,
            order=i,
            raw=text,
            text=text,
            source="native",
            paragraph=paragraph,
            status="accepted",
            anchor=PdfAnchor(page=1, box=(0, 10 * i, 100, 10 * i + 9)),
            **changes,
        )
        for i, text in enumerate(texts)
    ]
    return Document(
        source=Source(sha256="0", bytes=1, filename="a.pdf", pages=1, format="pdf"),
        engine=Engine(name="t", version="0", policy_version="0.6", config_sha256="0", components={}),
        created_at="2026-01-01T00:00:00Z",
        pages=[Page(number=1, kind="native", lines=lines)],
    )


def read_md(data):
    pytest.importorskip("markdown_it")
    return read_bytes(data, "a.md", Config(), engines=[]).document


def only_body(paras):
    (para,) = [p for p in paras if p.kind == "body"]
    return para


def test_a_pdf_paragraphs_printed_lines_are_joined_with_one_space():
    para = only_body(paragraphs(pdf_with_lines(["The deduction may be", "claimed until 31 March."])))
    assert para.text == "The deduction may be claimed until 31 March."
    assert [(s.line.text, s.offset) for s in para.spans] == [
        ("The deduction may be", 0),
        ("claimed until 31 March.", 21),
    ]


def test_spans_index_the_stripped_text_of_each_line():
    para = only_body(paragraphs(pdf_with_lines(["  padded  ", "next"])))
    assert para.text == "padded next"
    first = para.spans[0]
    assert first.line.text[first.start : first.end] == "padded" and first.offset == 0


def test_locate_maps_a_range_back_to_lines_and_characters():
    para = only_body(paragraphs(pdf_with_lines(["The deduction may be", "claimed until 31 March."])))
    start = para.text.index("may")
    assert [(line.text, a, b) for line, a, b in locate(para, start, start + 14)] == [
        ("The deduction may be", 14, 20),
        ("claimed until 31 March.", 0, 7),
    ]
    assert [(line.text, a, b) for line, a, b in locate(para, 0, 3)] == [("The deduction may be", 0, 3)]


def test_locate_counts_the_stripped_lines_leading_space():
    para = only_body(paragraphs(pdf_with_lines(["  padded", "next"])))
    assert [(line.text, a, b) for line, a, b in locate(para, 0, 6)] == [("  padded", 2, 8)]


def test_heading_paths_close_at_the_same_or_a_higher_level():
    paras = paragraphs(read_md(b"# A\n\n## B\n\nx\n\n## C\n\ny\n\n# D\n\nz\n"))
    assert [p.heading_path for p in paras if p.kind == "body"] == [("A", "B"), ("A", "C"), ("D",)]
    heading = next(p for p in paras if p.text == "B")
    assert (heading.kind, heading.level, heading.heading_path) == ("heading", 2, ("A", "B"))


def test_excluded_lines_are_left_out_and_a_document_with_none_left_has_no_paragraphs():
    document = pdf_with_lines(["kept", "dropped"])
    document.pages[0].lines[1].excluded = "header"
    assert only_body(paragraphs(document)).text == "kept"
    document.pages[0].lines[0].excluded = "header"
    assert paragraphs(document) == []


def test_status_is_the_lowest_of_the_lines():
    document = pdf_with_lines(["a", "b"])
    document.pages[0].lines[1].status = "flagged"
    assert only_body(paragraphs(document)).status == "flagged"


def test_breaks_are_the_offsets_of_list_marker_lines():
    para = only_body(paragraphs(pdf_with_lines(["Intro", "• one", "- two", "(a) three", "plain"])))
    assert para.breaks == (para.text.index("•"), para.text.index("- two"), para.text.index("(a)"))


def test_a_table_and_a_figure_are_paragraphs_too():
    document = pdf_with_lines(["text"])
    document.pages[0].figures = [
        Figure(
            id="f",
            page=1,
            anchor=PdfAnchor(page=1, box=(0, 0, 1, 1)),
            path="x.png",
            description="A bar chart",
            order=0,
            status="accepted",
        )
    ]
    paras = paragraphs(document)
    assert [(p.kind, p.text) for p in paras] == [("image", "A bar chart"), ("body", "text")]
    assert paras[0].figure is not None and paras[0].spans == []


@pytest.mark.parametrize(
    "text, token",
    [
        ("1. Scope", "1."),
        ("1.1 Scope", "1.1"),
        ("1.1.2. Scope", "1.1.2."),
        ("(1) Scope", "(1)"),
        ("1) Scope", "1)"),
        ("(a) Scope", "(a)"),
        ("(i) Scope", "(i)"),
        ("I. Scope", "I."),
        ("Article 5 Scope", "Article 5"),
        ("Section 3. Scope", "Section 3."),
        ("Clause 4", "Clause 4"),
        ("  2. indented", "2."),
        ("plain prose here", None),
        ("2026 was a year", None),
        ("31 March", None),
        ("Word", None),
        ("Article", None),
        ("Article five", None),
        ("", None),
    ],
)
def test_numbering_finds_a_leading_number_token(text, token):
    assert numbering(text) == token
