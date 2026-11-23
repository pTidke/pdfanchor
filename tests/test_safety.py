"""Safety limits for PDFs you did not choose. Synthetic PDFs only."""

import sys
from dataclasses import replace

import pytest
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

from ragnaroq import Config, ConversionError, convert


@pytest.fixture(scope="module")
def dense_then_plain(tmp_path_factory):
    """Page 1 carries tens of thousands of characters; page 2 one short line."""
    path = tmp_path_factory.mktemp("pdf") / "dense.pdf"
    page = canvas.Canvas(str(path), pagesize=A4)
    page.setFont("Helvetica", 3)
    for row in range(260):
        page.drawString(10, 830 - row * 3.2, "0123456789 abcdefghij " * 12)
    page.showPage()
    page.setFont("Helvetica", 12)
    page.drawString(72, 760, "This notification shall come into force on the 1st day of April, 2026.")
    page.save()
    return path


def _rules(document):
    return [[flag.rule for flag in page.flags] for page in document.pages]


def test_a_page_with_too_many_characters_is_refused_alone(dense_then_plain):
    document = convert(dense_then_plain, engines=[], config=replace(Config(), max_page_chars=20_000))
    first, second = document.pages
    assert first.kind == "failed" and _rules(document)[0] == ["budget.page-glyphs"]
    assert first.flags[0].severity == "blocking" and "20,000" in first.flags[0].message
    assert second.kind == "native" and second.lines


def test_a_page_that_takes_too_long_is_stopped_and_the_rest_is_read(dense_then_plain):
    document = convert(dense_then_plain, engines=[], config=replace(Config(), page_timeout_seconds=0.3))
    first, second = document.pages
    assert first.kind == "failed" and _rules(document)[0] == ["budget.page-time"]
    # The worker was stopped and started again: the next page is read as usual.
    assert second.kind == "native" and "1st day of April" in second.lines[0].text


@pytest.mark.skipif(sys.platform != "linux", reason="memory limits are enforced on Linux")
def test_a_reader_out_of_memory_fails_the_document_plainly(dense_then_plain):
    with pytest.raises(ConversionError):
        convert(dense_then_plain, engines=[], config=replace(Config(), page_memory_mb=40))


def test_reading_in_process_gives_the_same_result(native_pdf):
    isolated = convert(native_pdf, engines=[])
    in_process = convert(native_pdf, engines=[], config=replace(Config(), isolate_pages=False))
    assert [line.text for line in isolated.lines()] == [line.text for line in in_process.lines()]
    assert [line.box for line in isolated.lines()] == [line.box for line in in_process.lines()]
