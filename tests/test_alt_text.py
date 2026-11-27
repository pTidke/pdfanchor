"""Final review: an alt text that only says "a picture is here" is not a description.

openpyxl writes "Picture" on every image and python-pptx the file's name; such figures were
accepted as described and never read by OCR."""

import io

import pytest
from conftest import FakeEngine
from PIL import Image

from ragnaroq import figures, policy
from ragnaroq.config import Config
from ragnaroq.readers import _build, read_bytes

CONFIG = Config()


def png(size=(120, 60)) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", size, "white").save(buffer, "PNG")
    return buffer.getvalue()


def only_figure(document):
    (figure,) = [figure for page in document.pages for figure in page.figures]
    return figure


@pytest.mark.parametrize(
    "alt",
    [
        "Picture",
        "picture 3",
        "Image1",
        "IMG",
        "image_01",
        "Figure 2",
        "Fig. 4",
        "Photo",
        "Graphic 7",
        "Screenshot",
        "Untitled",
        "chart.png",
        "IMG_2041.JPG",
        "Screenshot 2026-11-01 at 10.12.33.png",
        r"C:\Users\me\Desktop\scan.jpeg",
        "media/image3.emf",
        "  ",
        "-",
        "…",
    ],
)
def test_generic_alt_text_is_not_a_description(alt):
    assert _build.alt_text(alt) == ""


@pytest.mark.parametrize(
    "alt",
    [
        "Logo",
        "Diagram",
        "Tax collections by year",
        "Picture of the Mumbai office",
        "Figure 2: collections by year",
        "2026",
        "Form 16.png showing the TDS rows",
        "Organisation chart",
    ],
)
def test_alt_text_that_says_something_is_kept(alt):
    assert _build.alt_text(alt) == alt


def test_the_first_candidate_that_says_something_is_used():
    assert _build.alt_text("image1.png", "Revenue by year") == "Revenue by year"
    assert _build.alt_text(None, "", "Picture 2") == ""


def test_an_openpyxl_picture_is_not_described_by_its_default_alt_text():
    openpyxl = pytest.importorskip("openpyxl")
    from openpyxl.drawing.image import Image as Picture

    book = openpyxl.Workbook()
    book.active.add_image(Picture(io.BytesIO(png())), "B2")
    buffer = io.BytesIO()
    book.save(buffer)
    figure = only_figure(read_bytes(buffer.getvalue(), "book.xlsx", CONFIG).document)
    assert (figure.description, figure.description_source) == ("", "none")
    assert figure.status == "flagged" and [f.rule for f in figure.flags] == ["image.no-description"]


def test_a_python_pptx_picture_is_not_described_by_its_file_name(tmp_path):
    pptx = pytest.importorskip("pptx")
    path = tmp_path / "chart.png"
    path.write_bytes(png())
    deck = pptx.Presentation()
    deck.slides.add_slide(deck.slide_layouts[6]).shapes.add_picture(str(path), 0, 0)
    buffer = io.BytesIO()
    deck.save(buffer)
    figure = only_figure(read_bytes(buffer.getvalue(), "deck.pptx", CONFIG).document)
    assert (figure.description, figure.description_source) == ("", "none")


def test_a_word_picture_with_a_file_name_for_alt_text_falls_back_to_its_title(tmp_path):
    docx = pytest.importorskip("docx")
    from docx.oxml.ns import qn

    path = tmp_path / "image1.png"
    path.write_bytes(png())
    document = docx.Document()
    document.add_picture(str(path))
    (props,) = document.element.body.iter(qn("wp:docPr"))
    props.set("descr", "image1.png")
    props.set("title", "Revenue by year")
    buffer = io.BytesIO()
    document.save(buffer)
    figure = only_figure(read_bytes(buffer.getvalue(), "report.docx", CONFIG).document)
    assert (figure.description, figure.description_source) == ("Revenue by year", "alt")


def test_html_and_markdown_generic_alt_text_is_not_a_description(tmp_path):
    (tmp_path / "a.png").write_bytes(png())
    page = b'<html><body><p>Text</p><img src="a.png" alt="IMG_2041.JPG"><img src="a.png" alt="Logo"></body></html>'
    html = read_bytes(page, "notice.html", CONFIG, base=tmp_path).document
    assert [(f.description, f.description_source) for f in html.pages[0].figures] == [
        ("", "none"),
        ("Logo", "alt"),
    ]
    markdown = read_bytes(b"Text\n\n![Picture 2](a.png)\n", "readme.md", CONFIG, base=tmp_path).document
    assert (only_figure(markdown).description, only_figure(markdown).description_source) == ("", "none")


def test_a_picture_with_generic_alt_text_is_read_by_ocr():
    openpyxl = pytest.importorskip("openpyxl")
    from openpyxl.drawing.image import Image as Picture

    book = openpyxl.Workbook()
    book.active.add_image(Picture(io.BytesIO(png())), "B2")
    buffer = io.BytesIO()
    book.save(buffer)
    result = read_bytes(buffer.getvalue(), "book.xlsx", CONFIG)
    engines = [FakeEngine(name, [("₹12,000", (5, 5, 100, 30))]) for name in ("one", "two")]
    figures.describe(result, engines, CONFIG)
    policy.decide(result.document, CONFIG.policy)
    figure = only_figure(result.document)
    assert (figure.description, figure.description_source, figure.status) == ("₹12,000", "ocr", "accepted")
