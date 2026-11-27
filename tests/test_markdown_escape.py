"""document.md never carries markup that came from a file: no image, link or tag."""

import re
import sys
from pathlib import Path

import pytest

pytest.importorskip("markdown_it")
sys.path.insert(0, str(Path(__file__).parent))

from markdown_it import MarkdownIt  # noqa: E402
from test_exact import decided, exact_document, figure  # noqa: E402

from ragnaroq import figures  # noqa: E402
from ragnaroq.anchors import PptxAnchor  # noqa: E402
from ragnaroq.config import Config  # noqa: E402
from ragnaroq.export.markdown import to_markdown  # noqa: E402
from ragnaroq.model import Cell, Table  # noqa: E402
from ragnaroq.readers import read_bytes  # noqa: E402

VIEWER = MarkdownIt("commonmark")  # raw HTML allowed, as in many viewers
TRACKER = "https://tracker.example"


def fetched(markdown: str) -> list[str]:
    html = VIEWER.render(markdown)
    return re.findall(r"<img[^>]+src=\"?(https?://[^\"> ]+)", html, re.IGNORECASE) + re.findall(
        r"<(?:script|iframe|object|embed)\b", html, re.IGNORECASE
    )


def test_a_remote_urls_close_paren_and_newline_cannot_open_an_image():
    for url in (
        f"https://ok.example/a.png) ![t]({TRACKER}/one.png",
        f"https://ok.example/a.png\n\n<img src={TRACKER}/two.png>",
        "https://ok.example/a b.png",
    ):
        fig = figure(path=url, description="x", description_source="alt")
        md = to_markdown(decided(exact_document("html", ["Hello"], figures=[fig])))
        assert fetched(md) == [], md


def test_a_description_cannot_close_its_label_or_start_an_image():
    for text in (
        f"x](https://ok.example) ![t]({TRACKER}/a.png)",
        f"x] ![t]({TRACKER}/b.png) [y",
        "x](javascript:alert(1)) [y",
    ):
        for path in ("images/a.png", "", "https://ok.example/a.png"):
            fig = figure(path=path, description=text, description_source="alt")
            md = to_markdown(decided(exact_document("html", ["Hello"], figures=[fig])))
            html = VIEWER.render(md)
            assert fetched(md) == [] and "javascript:" not in re.findall(r'href="([^"]*)"', html), md


def test_a_description_with_newlines_and_a_cite_stay_on_one_line():
    fig = figure(description="a\n\nb\n# c", description_source="alt")
    fig.anchor = PptxAnchor(slide=1, shape="Pic\n\n![t](https://tracker.example/c.png)")
    md = to_markdown(decided(exact_document("pptx", ["Hello"], figures=[fig])))
    assert fetched(md) == []
    assert [line for line in md.splitlines() if "image" in line or "a b" in line]


def test_a_word_paragraph_and_a_pdf_line_holding_markup_render_as_text():
    hostile = f"![t]({TRACKER}/a.png) and <img src={TRACKER}/b.png> [x]({TRACKER}) <script>x</script>"
    for source in ("docx", "pdf"):
        md = to_markdown(decided(exact_document(source, [hostile, "Next."])))
        html = VIEWER.render(md)
        assert fetched(md) == [] and "<a " not in html, (source, md)
        assert "tracker.example/a.png" in html  # the words are still there


def test_a_heading_and_a_table_cell_are_escaped_too():
    hostile = f"![t]({TRACKER}/a.png) | <img src={TRACKER}/b.png>"
    doc = exact_document("xlsx", [hostile], table=True)
    doc.pages[0].tables[0] = Table(
        id="t1", page=1, rows=1, columns=1, cells=[Cell(row=0, column=0, text=hostile)]
    )
    md = to_markdown(decided(doc))
    assert fetched(md) == [] and "\\|" in md
    doc = exact_document("docx", [hostile])
    doc.pages[0].lines[0].role = "heading"
    assert fetched(to_markdown(decided(doc))) == []


def test_a_part_name_cannot_end_its_comment():
    doc = exact_document("xlsx", ["x"], name=f"a --> ![t]({TRACKER}/n.png) <!--")
    md = to_markdown(decided(doc))
    assert fetched(md) == [] and "<img" not in VIEWER.render(md)


def test_chunk_text_stays_raw():
    from ragnaroq.export.chunks import to_chunks

    doc = decided(exact_document("docx", ["a [b] <c> \\d"]))
    assert to_chunks(doc, Config())[0]["text"] == "a [b] <c> \\d"


def test_the_real_html_reader_cases_do_not_fetch_anything(tmp_path):
    cases = [
        f'<img src="https://ok.example/a.png) ![t]({TRACKER}/r.png">',
        f'<img src="https://ok.example/a.png&#10;&#10;<img src={TRACKER}/n.png>">',
        f'<img src="https://ok.example/a.png" alt="x](https://ok.example) ![t]({TRACKER}/alt.png">',
        f'<img src="missing.png" alt="x] ![t]({TRACKER}/nofile.png) [y">',
        '<img src="a.png" alt="x](https://t.example/s.png) ![y">',
    ]
    from PIL import Image

    Image.new("RGB", (40, 40), "red").save(tmp_path / "a.png")
    for tag in cases:
        html = f"<html><body><p>Text.</p>{tag}<p>End.</p></body></html>".encode()
        res = read_bytes(html, "p.html", Config(), base=tmp_path)
        figures.describe(res, [], Config())
        figures.save(res, tmp_path / "out", Config())
        assert fetched(to_markdown(res.document)) == [], tag
