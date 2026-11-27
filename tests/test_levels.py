"""Heading levels: each reader sets them, and a PDF's come from its font sizes."""

import io
import json

import pytest

from ragnaroq import structure
from ragnaroq.config import Config
from ragnaroq.model import SCHEMA_VERSION, Document, Engine, Line, Page, Source
from ragnaroq.readers import READING_VERSIONS, read_bytes


def read(data: bytes, name: str):
    return read_bytes(data, name, Config()).document


def levels_of(document) -> list[int]:
    return [line.level for line in document.lines()]


def test_markdown_html_json_and_xml_headings_carry_their_level():
    pytest.importorskip("markdown_it")
    pytest.importorskip("lxml")
    assert levels_of(read(b"# A\n\n## B\n\n### C\n\ntext\n", "a.md")) == [1, 2, 3, 0]
    assert levels_of(read(b"<h1>A</h1><h3>B</h3><p>x</p>", "a.html")) == [1, 3, 0]
    assert levels_of(read(b'{"a": {"b": {"c": 1}}}', "a.json")) == [1, 2, 0]
    assert levels_of(read(b"<r><a><b>x</b></a></r>", "a.xml")) == [1, 2, 0]
    deep = '{"a":' * 8 + "{}" + "}" * 8
    assert max(levels_of(read(deep.encode(), "d.json"))) == 6


def test_word_titles_and_numbered_headings_carry_their_level():
    docx = pytest.importorskip("docx")
    word = docx.Document()
    word.add_paragraph("T", style="Title")
    word.add_heading("H2", level=2)
    word.add_heading("H1", level=1)
    word.add_paragraph("body")
    buffer = io.BytesIO()
    word.save(buffer)
    assert levels_of(read(buffer.getvalue(), "a.docx")) == [1, 2, 1, 0]


def test_a_slide_title_is_level_one():
    pptx = pytest.importorskip("pptx")
    deck = pptx.Presentation()
    slide = deck.slides.add_slide(deck.slide_layouts[1])
    slide.shapes.title.text = "Title"
    slide.placeholders[1].text = "point"
    buffer = io.BytesIO()
    deck.save(buffer)
    assert levels_of(read(buffer.getvalue(), "a.pptx")) == [1, 0]


def pdf_document_with_headings(sizes):
    lines, order = [], 0
    for size in sizes:
        for role, font in (("heading", size), ("body", 10)):
            order += 1
            lines.append(
                Line(
                    id=f"p1-l{order}",
                    page=1,
                    order=order - 1,
                    raw="t",
                    text="t",
                    source="native",
                    role=role,
                    font_size=font,
                )
            )
    page = Page(number=1, width=1, height=1, kind="native", lines=lines)
    return Document(
        source=Source(sha256="0", bytes=0, filename="a.pdf", pages=1),
        engine=Engine(name="x", version="1", policy_version="1", config_sha256="0", components={}),
        created_at="2026-01-01T00:00:00Z",
        pages=[page],
    )


def test_pdf_heading_levels_come_from_font_sizes_largest_first():
    document = pdf_document_with_headings([18, 14, 18, 11])
    structure.levels(document)
    assert [line.level for line in document.lines() if line.role == "heading"] == [1, 2, 1, 3]
    assert {line.level for line in document.lines() if line.role == "body"} == {0}
    structure.levels(document)  # idempotent
    assert [line.level for line in document.lines() if line.role == "heading"] == [1, 2, 1, 3]


def test_another_format_or_a_missing_font_size_is_level_one():
    document = pdf_document_with_headings([18, 14])
    document.source.format = "md"
    structure.levels(document)
    assert [line.level for line in document.lines() if line.role == "heading"] == [1, 1]
    document = pdf_document_with_headings([18, 14])
    next(line for line in document.lines() if line.role == "heading").font_size = None
    structure.levels(document)
    assert [line.level for line in document.lines() if line.role == "heading"] == [1, 1]


def test_a_0_3_document_loads_restamped_with_levels_zero():
    old = json.loads(pdf_document_with_headings([18]).model_dump_json())
    old["schema_version"] = "0.3.0"
    for page in old["pages"]:
        for line in page["lines"]:
            del line["level"]
    document = Document.model_validate(old)
    assert document.schema_version == SCHEMA_VERSION == "0.4.0"
    assert {line.level for line in document.lines()} == {0}


def test_reading_versions_are_published():
    assert READING_VERSIONS["pdf"] == "1" and READING_VERSIONS["docx"] == "2"
    assert set(READING_VERSIONS) == {"pdf", "xlsx", "csv", "docx", "html", "md", "pptx", "json", "xml"}
