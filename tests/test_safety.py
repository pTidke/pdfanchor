"""Safety limits for PDFs you did not choose. Synthetic PDFs only."""

import subprocess
import sys
from dataclasses import replace

import pytest
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

from ragnaroq import Config, ConversionError, convert, isolation


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


SCRIPT = """
from ragnaroq import convert
document = convert({path!r}, engines=[])
print("FIRST LINE:", list(document.lines())[0].text)
"""


@pytest.mark.parametrize("how", ["stdin", "file without a main guard"])
def test_convert_works_from_any_kind_of_script(native_pdf, tmp_path, how):
    """The worker must not run the caller's script again, as multiprocessing's spawn
    does: that fails for a script on stdin, and recurses for one with no main guard."""
    script = SCRIPT.format(path=str(native_pdf))
    if how == "stdin":
        command, stdin = [sys.executable, "-"], script
    else:
        path = tmp_path / "unguarded.py"
        path.write_text(script)
        command, stdin = [sys.executable, str(path)], None
    result = subprocess.run(command, input=stdin, capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, result.stderr
    assert "FIRST LINE:" in result.stdout


# Replacing the worker's start-up line lets a test make it misbehave in ways no PDF
# reliably does. `sys.argv[1]` carries the caller's import path.
SETUP = "import json, os, sys; sys.path[:] = json.loads(sys.argv[1]); import ragnaroq.isolation as i; "


def test_a_worker_that_cannot_start_says_why(native_pdf, monkeypatch):
    monkeypatch.setattr(isolation, "_BOOTSTRAP", "raise SystemExit('ragnaroq is not installed here')")
    with pytest.raises(ConversionError) as raised:
        convert(native_pdf, engines=[])
    message = str(raised.value)
    assert "before it could open the PDF" in message and "exited with code 1" in message
    assert "ragnaroq is not installed here" in message and "memory" not in message


def test_a_worker_that_dies_on_a_page_fails_that_page_and_says_how(dense_then_plain, monkeypatch):
    monkeypatch.setattr(isolation, "_BOOTSTRAP", SETUP + "i.read_one = lambda *a: os._exit(3); i._serve()")
    document = convert(dense_then_plain, engines=[])
    assert _rules(document) == [["page.crashed"], ["page.crashed"]]  # started again for page 2
    assert "exited with code 3 while reading this page" in document.pages[0].flags[0].message


def test_a_library_printing_in_the_worker_does_not_garble_the_pages(native_pdf, monkeypatch):
    monkeypatch.setattr(
        isolation,
        "_BOOTSTRAP",
        SETUP + "real = i.read_one; i.read_one = lambda *a: (print('chatty'), real(*a))[1]; i._serve()",
    )
    chatty = convert(native_pdf, engines=[])
    quiet = convert(native_pdf, engines=[], config=replace(Config(), isolate_pages=False))
    assert [line.text for line in chatty.lines()] == [line.text for line in quiet.lines()]
