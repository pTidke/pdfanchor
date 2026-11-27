"""Figures: described from OCR, saved once each under names from their citation."""

import io
import json
import shutil
from pathlib import Path

import pytest
from conftest import FakeEngine
from PIL import Image

from ragnaroq import figures, pipeline, policy
from ragnaroq.anchors import PdfAnchor, PptxAnchor, XlsxAnchor
from ragnaroq.config import Config
from ragnaroq.model import Figure
from ragnaroq.readers import ReadResult, _build, read_bytes

CONFIG = Config()
DEMO = Path(__file__).parent.parent / "references" / "demo"


def encode(image: Image.Image, fmt="PNG", **options) -> bytes:
    buffer = io.BytesIO()
    image.save(buffer, format=fmt, **options)
    return buffer.getvalue()


def picture(size=(120, 60), colour=(200, 30, 30), fmt="PNG", **options) -> bytes:
    return encode(Image.new("RGB", size, colour), fmt, **options)


def fig(ident, anchor=None, **fields) -> Figure:
    anchor = anchor or PptxAnchor(slide=4, shape="Picture 2")
    return Figure(id=ident, page=fields.pop("page", 1), anchor=anchor, **fields)


def result(*pairs, config=CONFIG) -> ReadResult:
    """A body document whose figures are `(figure, bytes | None)` pairs."""
    document = _build.start(b"x", "f.pptx", "pptx", config, {})
    images = {}
    for figure, data in pairs:
        document.pages[0].figures.append(figure)
        if data is not None:
            images[figure.id] = data
    return ReadResult(document, images)


def lines(result_):
    return [fig_ for page in result_.document.pages for fig_ in page.figures]


class Counting(FakeEngine):
    def __init__(self, name, found):
        super().__init__(name, found)
        self.calls = 0
        self.sizes = []

    def read(self, image, languages):
        self.calls += 1
        self.sizes.append((image.size, image.mode))
        return super().read(image, languages)


def twice(text="₹12,000", box=(5, 5, 100, 30)):
    return [Counting("one", [(text, box)]), Counting("two", [(text, box)])]


# --- the plan's tests --------------------------------------------------------------------------------


def test_a_figure_without_alt_text_is_described_by_its_ocr_text():
    res = result((fig("p4-f1"), picture()))
    figures.describe(res, twice(), CONFIG)
    policy.decide(res.document, CONFIG.policy)
    figure = lines(res)[0]
    assert figure.description == "₹12,000" and figure.description_source == "ocr"
    assert figure.status == "accepted"
    line = figure.ocr_lines[0]
    assert line.id == "p4-f1-l1" and line.page == 1 and line.anchor == figure.anchor
    assert line.source == "ocr:one" and line.box.x1 == 100  # image pixels, not points


def test_disagreeing_readers_leave_the_figure_flagged():
    res = result((fig("p4-f1"), picture()))
    engines = [
        Counting("one", [("₹12,000", (5, 5, 100, 30))]),
        Counting("two", [("₹21,000", (5, 5, 100, 30))]),
    ]
    figures.describe(res, engines, CONFIG)
    policy.decide(res.document, CONFIG.policy)
    figure = lines(res)[0]
    assert figure.description == "₹12,000" and figure.status == "flagged"
    assert {flag.rule for flag in figure.flags} == {"image.ocr-unverified"}


def test_a_figure_with_alt_text_is_not_ocr_read():
    res = result((fig("p4-f1", description="A chart", description_source="alt"), picture()))
    engines = twice()
    figures.describe(res, engines, CONFIG)
    assert [e.calls for e in engines] == [0, 0]
    assert lines(res)[0].description == "A chart" and not lines(res)[0].ocr_lines


def test_images_are_saved_under_names_from_their_citation(tmp_path):
    res = result(
        (fig("a", PptxAnchor(slide=4, shape="Picture 2")), picture()),
        (fig("b", XlsxAnchor(sheet="Rates", cell="C7")), picture(colour=(1, 2, 3))),
        (fig("c", PdfAnchor(page=18, box=(0, 0, 1, 1))), picture(colour=(9, 9, 9), fmt="JPEG")),
    )
    figures.save(res, tmp_path, CONFIG)
    paths = [f.path for f in lines(res)]
    assert paths == ["images/slide-4-picture-2.png", "images/rates-c7.png", "images/p-18.jpg"]
    assert all((tmp_path / p).is_file() for p in paths)
    assert (tmp_path / paths[0]).read_bytes() == res.images["a"]  # a web format is saved as it is


def test_tiny_images_are_counted_not_saved(tmp_path):
    res = result((fig("a"), picture((8, 8))), (fig("b", PptxAnchor(slide=5, shape="x")), picture()))
    figures.save(res, tmp_path, CONFIG)
    assert [f.id for f in lines(res)] == ["b"]
    assert [p.name for p in (tmp_path / "images").iterdir()] == ["slide-5-x.png"]
    flag = next(f for f in res.document.flags if f.rule == "image.tiny-skipped")
    assert "1" in flag.message


def test_an_image_that_cannot_be_shown_on_the_web_is_converted_to_png(tmp_path):
    res = result((fig("a"), encode(Image.new("RGB", (60, 40), "red"), "BMP")))
    figures.save(res, tmp_path, CONFIG)
    path = lines(res)[0].path
    assert path.endswith(".png")
    with Image.open(tmp_path / path) as saved:
        assert saved.format == "PNG" and saved.size == (60, 40)


def test_an_image_pillow_cannot_open_is_kept_and_flagged(tmp_path):
    emf = b"\x01\x00\x00\x00" + b"\x00" * 36 + b" EMF" + b"\x00" * 200
    res = result((fig("a"), emf))
    figures.save(res, tmp_path, CONFIG)
    figure = lines(res)[0]
    assert figure.path.endswith(".emf") and (tmp_path / figure.path).read_bytes() == emf
    assert "image.not-viewable" in {f.rule for f in figure.flags}


@pytest.mark.skipif(not shutil.which("tesseract"), reason="needs the tesseract program")
def test_the_demo_deck_picture_gets_its_text_from_ocr():
    from ragnaroq.ocr.tesseract import TesseractCli

    res = read_bytes((DEMO / "deck.pptx").read_bytes(), "deck.pptx", CONFIG)
    figures.describe(res, [TesseractCli()], CONFIG)
    policy.decide(res.document, CONFIG.policy)
    figure = lines(res)[0]
    assert "12,000" in figure.description  # OCR reads "212,000" for "₹12,000"
    assert figure.status != "accepted" and "image.ocr-unverified" in {f.rule for f in figure.flags}


# --- R20 -----------------------------------------------------------------------------------------------


def test_a_figure_with_no_bytes_is_skipped_and_keeps_its_path(tmp_path):
    remote = fig("a", path="https://example.org/x.png")
    linked = fig("b", PptxAnchor(slide=2, shape="Linked"))
    res = result((remote, None), (linked, None))
    engines = twice()
    figures.describe(res, engines, CONFIG)
    figures.save(res, tmp_path, CONFIG)
    assert [e.calls for e in engines] == [0, 0]
    assert [f.path for f in lines(res)] == ["https://example.org/x.png", ""]
    assert [f.width for f in lines(res)] == [0, 0] and len(lines(res)) == 2
    assert not (tmp_path / "images").exists()
    assert not any(f.rule == "image.tiny-skipped" for f in res.document.flags)


def test_two_figures_sharing_one_image_give_one_file_and_one_ocr_pass(tmp_path):
    shared = picture()
    res = result(
        (fig("p1-f1", PptxAnchor(slide=1, shape="Logo")), shared),
        (fig("p2-f1", PptxAnchor(slide=2, shape="Logo"), page=2), shared),
        (
            fig("p3-f1", PptxAnchor(slide=3, shape="Logo"), page=3),
            bytes(bytearray(shared)),
        ),  # equal, not same
    )
    engines = twice()
    figures.describe(res, engines, CONFIG)
    figures.save(res, tmp_path, CONFIG)
    assert [e.calls for e in engines] == [1, 1]
    first, second, third = lines(res)
    assert first.path == second.path == third.path == "images/slide-1-logo.png"
    assert len(list((tmp_path / "images").iterdir())) == 1
    assert [f.ocr_lines[0].id for f in (first, second, third)] == ["p1-f1-l1", "p2-f1-l1", "p3-f1-l1"]
    assert second.ocr_lines[0].page == 2 and second.ocr_lines[0].anchor == second.anchor
    assert first.ocr_lines[0] is not second.ocr_lines[0]
    assert first.ocr_lines[0].readings is not second.ocr_lines[0].readings


def test_names_that_collide_get_a_number(tmp_path):
    same = PptxAnchor(slide=4, shape="Picture 2")
    res = result(
        (fig("a", same), picture()),
        (fig("b", same), picture(colour=(0, 0, 1))),
        (fig("c", PptxAnchor(slide=4, shape="PICTURE-2")), picture(colour=(0, 0, 2))),
    )
    figures.save(res, tmp_path, CONFIG)
    assert [f.path for f in lines(res)] == [
        "images/slide-4-picture-2.png",
        "images/slide-4-picture-2-2.png",
        "images/slide-4-picture-2-3.png",
    ]


def test_an_svg_is_saved_as_it_is_and_never_read(tmp_path):
    svg = b'<?xml version="1.0"?>\n<svg xmlns="http://www.w3.org/2000/svg" width="400" height="300"/>'
    bare = b"<svg width='400' height='300'></svg>"
    res = result((fig("a"), svg), (fig("b", PptxAnchor(slide=1, shape="s")), bare))
    engines = twice()
    figures.describe(res, engines, CONFIG)
    figures.save(res, tmp_path, CONFIG)
    assert [e.calls for e in engines] == [0, 0]
    for figure, data in zip(lines(res), (svg, bare), strict=True):
        assert figure.path.endswith(".svg") and (tmp_path / figure.path).read_bytes() == data
        assert "image.not-viewable" not in {f.rule for f in figure.flags}


def test_an_image_over_the_pixel_limit_is_flagged_not_read_nor_converted(tmp_path):
    small = Config(max_render_pixels=1000)
    res = result(
        (fig("a"), picture((100, 100))),
        (fig("b", PptxAnchor(slide=1, shape="b")), encode(Image.new("RGB", (100, 100)), "BMP")),
        config=small,
    )
    engines = twice()
    figures.describe(res, engines, small)
    figures.save(res, tmp_path, small)
    assert [e.calls for e in engines] == [0, 0]
    png, bmp = lines(res)
    assert "image.too-large" in {f.rule for f in png.flags} and png.path.endswith(".png")
    assert (tmp_path / png.path).read_bytes() == res.images["a"]
    assert bmp.path.endswith(".bmp") and "image.too-large" in {f.rule for f in bmp.flags}
    assert png.description_source == "none"


def test_exif_rotation_is_applied_before_ocr_and_the_saved_file_is_the_original(tmp_path):
    image = Image.new("RGB", (100, 40), "white")
    exif = Image.Exif()
    exif[0x0112] = 6  # rotate 90 clockwise to show
    data = encode(image, "JPEG", exif=exif.tobytes())
    res = result((fig("a"), data))
    engine = Counting("one", [("hello", (1, 1, 5, 5))])
    figures.describe(res, [engine], CONFIG)
    figures.save(res, tmp_path, CONFIG)
    assert engine.sizes[0][0] == (40, 100)
    assert (tmp_path / lines(res)[0].path).read_bytes() == data
    assert (lines(res)[0].width, lines(res)[0].height) == (40, 100)


def test_deciding_twice_keeps_the_figure_flags_without_duplicates():
    small = Config(max_render_pixels=1000)
    emf = b"\x01\x00\x00\x00" + b"\x00" * 36 + b" EMF" + b"\x00" * 100
    res = result((fig("a"), picture((100, 100))), (fig("b", PptxAnchor(slide=1, shape="b")), emf))
    figures.describe(res, twice(), small)
    for _ in range(2):
        policy.decide(res.document, small.policy)
        figures.describe(res, twice(), small)
    a, b = lines(res)
    assert sorted(f.rule for f in a.flags) == ["image.no-description", "image.too-large"]
    assert sorted(f.rule for f in b.flags) == ["image.no-description", "image.not-viewable"]


def test_describing_twice_does_not_read_again():
    res = result((fig("a"), picture()))
    engines = twice()
    figures.describe(res, engines, CONFIG)
    figures.describe(res, engines, CONFIG)
    assert [e.calls for e in engines] == [1, 1] and len(lines(res)[0].ocr_lines) == 1


def test_a_tiny_image_with_alt_text_stays_without_a_file(tmp_path):
    res = result(
        (fig("a", description="dot", description_source="alt"), picture((8, 8))),
        (fig("b", PptxAnchor(slide=9, shape="b")), picture((31, 31))),
        (fig("c", PptxAnchor(slide=9, shape="c")), picture((32, 32))),
    )
    figures.save(res, tmp_path, CONFIG)
    assert [f.id for f in lines(res)] == ["a", "c"]
    assert lines(res)[0].path == "" and lines(res)[1].path != ""
    assert next(f for f in res.document.flags if f.rule == "image.tiny-skipped").message.startswith("1 ")
    figures.save(res, tmp_path, CONFIG)  # again: the count is kept, not reset or doubled
    assert [f.message for f in res.document.flags if f.rule == "image.tiny-skipped"][0].startswith("1 ")
    assert len([f for f in res.document.flags if f.rule == "image.tiny-skipped"]) == 1


def test_a_tiny_image_is_not_ocr_read():
    res = result((fig("a"), picture((8, 8))))
    engines = twice()
    figures.describe(res, engines, CONFIG)
    assert [e.calls for e in engines] == [0, 0]


def test_names_are_safe_and_never_empty(tmp_path):
    odd = fig("p3-f2", XlsxAnchor(sheet="../../etc/ पत्र", cell="A1"))
    symbols = fig("p3-f3", XlsxAnchor(sheet="?!*", cell="::"))
    long = fig("p3-f4", XlsxAnchor(sheet="s" * 300, cell="A1"))
    res = result((odd, picture()), (symbols, picture(colour=(1, 1, 1))), (long, picture(colour=(2, 2, 2))))
    figures.save(res, tmp_path, CONFIG)
    names = [f.path for f in lines(res)]
    assert names[0] == "images/etc-a1.png"
    assert names[1] == "images/p3-f3.png"
    assert len(names[2]) <= len("images/") + 80 + len(".png")
    for path in names:
        assert path.count("/") == 1 and ".." not in path
        assert (tmp_path / path).is_file()
    assert {p.name for p in (tmp_path / "images").iterdir()} == {n.split("/")[1] for n in names}


def test_a_truncated_image_is_kept_and_flagged_and_does_not_stop_the_others(tmp_path):
    whole = picture((200, 200), colour=(7, 7, 7))
    noisy = Image.effect_noise((200, 200), 80).convert("RGB")
    cut = encode(noisy)[:300]
    res = result((fig("a"), cut), (fig("b", PptxAnchor(slide=1, shape="b")), whole))
    engines = twice()
    figures.describe(res, engines, CONFIG)
    figures.save(res, tmp_path, CONFIG)
    a, b = lines(res)
    assert "image.not-viewable" in {f.rule for f in a.flags} and a.path.endswith(".png")
    assert (tmp_path / a.path).read_bytes() == cut
    assert b.description_source == "ocr"


def test_an_ocr_reader_that_fails_costs_the_figure_its_reading_not_the_run():
    class Broken(FakeEngine):
        def read(self, image, languages):
            raise RuntimeError("model crashed")

    res = result((fig("a"), picture()))
    figures.describe(res, [Broken("bad", []), twice()[1]], CONFIG)
    figure = lines(res)[0]
    assert figure.description_source == "none" and not figure.ocr_lines
    assert "ocr.reader-failed" in {f.rule for f in figure.flags}
    res = result((fig("a"), picture()))
    figures.describe(res, [twice()[0], Broken("bad", [])], CONFIG)
    figure = lines(res)[0]
    assert figure.description == "₹12,000"
    assert "ocr.reader-failed" in {f.rule for f in figure.flags}
    policy.decide(res.document, CONFIG.policy)
    assert "ocr.reader-failed" in {f.rule for f in figure.flags}


def test_an_image_with_no_text_stays_undescribed():
    res = result((fig("a"), picture()))
    figures.describe(res, [Counting("one", [])], CONFIG)
    assert lines(res)[0].description_source == "none"


def test_the_description_is_the_first_readers_lines_joined_and_cut_at_300():
    long = [("word " * 20, (0, i * 20, 10, i * 20 + 10)) for i in range(5)]
    res = result((fig("a"), picture()))
    figures.describe(res, [Counting("one", long)], CONFIG)
    figure = lines(res)[0]
    assert figure.description.endswith("word…") and len(figure.description) <= 301
    assert len(figure.ocr_lines) == 5


def test_write_saves_the_images_and_the_outputs_carry_their_paths(tmp_path):
    res = result((fig("a"), picture()))
    figures.describe(res, twice(), CONFIG)
    policy.decide(res.document, CONFIG.policy)
    files = pipeline.write(res.document, tmp_path, CONFIG, res.images)
    assert (tmp_path / "images" / "slide-4-picture-2.png").is_file()
    assert "images/slide-4-picture-2.png" in files["markdown"].read_text()
    assert json.loads(files["document"].read_text())["pages"][0]["figures"][0]["path"].startswith("images/")
    chunks = [json.loads(row) for row in files["chunks"].read_text().splitlines()]
    assert chunks[0]["metadata"]["image_path"] == "images/slide-4-picture-2.png"


def test_write_without_images_writes_none(tmp_path):
    res = result((fig("a"), picture()))
    pipeline.write(res.document, tmp_path, CONFIG)
    assert not (tmp_path / "images").exists()


def test_the_pixel_mode_of_what_the_reader_sees_is_one_it_can_use():
    from ragnaroq.figures import _for_ocr

    cases = {
        "cmyk": encode(Image.new("CMYK", (40, 40), (0, 255, 255, 0)), "JPEG"),
        "sixteen": encode(Image.new("I;16", (40, 40), 60000)),
        "palette": encode(Image.new("P", (40, 40), 3), "GIF"),
        "alpha": encode(Image.new("RGBA", (40, 40), (0, 0, 0, 0))),
        "bilevel": encode(Image.new("1", (40, 40), 1)),
    }
    for name, data in cases.items():
        image = _for_ocr(data, CONFIG)
        assert image.mode in ("L", "RGB"), name
        image.save(io.BytesIO(), format="PNG")
    # 60000 of 65535 is light grey, not black or clipped differently from white
    sixteen = _for_ocr(cases["sixteen"], CONFIG)
    assert 200 <= sixteen.getpixel((0, 0)) <= 240
    # a transparent picture is drawn on white, not black
    assert _for_ocr(cases["alpha"], CONFIG).getpixel((0, 0)) == (255, 255, 255)


def test_an_animated_gif_is_read_on_its_first_frame_only(tmp_path):
    frames = [Image.new("RGB", (64, 64), (i % 256, 0, 0)) for i in range(500)]
    buffer = io.BytesIO()
    frames[0].save(buffer, format="GIF", save_all=True, append_images=frames[1:])
    res = result((fig("a"), buffer.getvalue()))
    engine = Counting("one", [("hi", (1, 1, 5, 5))])
    figures.describe(res, [engine], CONFIG)
    figures.save(res, tmp_path, CONFIG)
    assert engine.calls == 1 and lines(res)[0].path.endswith(".gif")


def test_a_decompression_bomb_header_is_not_decoded(tmp_path):
    import struct
    import zlib

    def chunk(kind, body):
        return struct.pack(">I", len(body)) + kind + body + struct.pack(">I", zlib.crc32(kind + body))

    bomb = (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", 50000, 50000, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(b"\x00"))
        + chunk(b"IEND", b"")
    )
    res = result((fig("a"), bomb))
    engines = twice()
    figures.describe(res, engines, CONFIG)
    figures.save(res, tmp_path, CONFIG)
    figure = lines(res)[0]
    assert [e.calls for e in engines] == [0, 0]
    assert "image.too-large" in {f.rule for f in figure.flags}
    assert figure.path.endswith(".png") and (tmp_path / figure.path).read_bytes() == bomb


def test_a_figure_whose_one_image_serves_many_figures_is_written_once(tmp_path):
    shared = picture((300, 300))
    pairs = [(fig(f"f{i}", PptxAnchor(slide=1, shape=f"s{i}")), shared) for i in range(200)]
    res = result(*pairs)
    figures.save(res, tmp_path, CONFIG)
    assert len(list((tmp_path / "images").iterdir())) == 1
    assert len({f.path for f in lines(res)}) == 1


# --- PDF behaviour is unchanged --------------------------------------------------------------------


def test_a_scanned_pdf_converts_exactly_as_it_did_before_ocr_lines_was_shared(scanned_pdf):
    box_a, box_b = (150, 200, 900, 260), (150, 360, 1100, 420)
    one = FakeEngine("one", [("The due date is 31st July 2026.", box_b), ("Circular No. 7 of 2026", box_a)])
    two = FakeEngine("two", [("Circular No. 7 of 2026", box_a), ("The due date is 31st July 2025.", box_b)])
    document = pipeline.convert(scanned_pdf, engines=[one, two]).model_dump(mode="json")
    golden = json.loads((Path(__file__).parent / "golden" / "scanned-two-engines.json").read_text())
    assert {k: document[k] for k in golden} == golden


# --- fix round 1 (R26) -----------------------------------------------------------------------------------

EPS = b"%!PS-Adobe-3.0 EPSF-3.0\n%%BoundingBox: 0 0 400 300\nshowpage\n"


def fake_ghostscript(tmp_path, monkeypatch):
    """A `gs` first on PATH that records every call."""
    log = tmp_path / "gs.log"
    program = tmp_path / "bin" / "gs"
    program.parent.mkdir()
    program.write_text(f'#!/bin/sh\necho "$@" >> {log}\nexit 1\n')
    program.chmod(0o755)
    monkeypatch.setenv("PATH", f"{program.parent}:" + __import__("os").environ["PATH"])
    return log


def test_c1_postscript_is_never_opened_so_ghostscript_never_runs(tmp_path, monkeypatch):
    log = fake_ghostscript(tmp_path, monkeypatch)
    pdf = b"%PDF-1.4\n1 0 obj<<>>endobj\n"
    res = result((fig("a"), EPS), (fig("b", PptxAnchor(slide=1, shape="b")), pdf))
    engines = twice()
    figures.describe(res, engines, CONFIG)
    figures.save(res, tmp_path / "out", CONFIG)
    assert not log.exists()
    a, b = lines(res)
    assert a.path.endswith(".eps") and b.path.endswith(".pdf")
    assert (tmp_path / "out" / a.path).read_bytes() == EPS
    for figure in (a, b):
        assert "image.not-viewable" in {f.rule for f in figure.flags}
    assert [e.calls for e in engines] == [0, 0]


def test_c1_pillow_opens_only_the_allowed_formats():
    assert figures.probe(EPS, CONFIG).kind == "opaque"
    assert figures.probe(encode(Image.new("RGB", (40, 40)), "PPM"), CONFIG).kind == "convert"
    assert figures.probe(encode(Image.new("RGB", (40, 40)), "ICO"), CONFIG).kind == "convert"
    assert figures.probe(encode(Image.new("RGB", (40, 40)), "PCX"), CONFIG).kind == "opaque"


def many_page_tiff(pages):
    import struct

    out = bytearray(b"II*\x00" + struct.pack("<I", 8))
    for p in range(pages):
        at = len(out)
        entries = [(256, 3, 1), (257, 3, 1), (258, 3, 8), (259, 3, 1), (262, 3, 1), (273, 4, 0), (277, 3, 1)]
        entries += [(278, 3, 1), (279, 4, 1)]
        size = 2 + 12 * len(entries) + 4
        out += struct.pack("<H", len(entries))
        for tag, kind, value in entries:
            value = at + size if tag == 273 else value
            out += (
                struct.pack("<HHIHH", tag, kind, 1, value, 0)
                if kind == 3
                else struct.pack("<HHII", tag, kind, 1, value)
            )
        out += struct.pack("<I", at + size + 2 if p < pages - 1 else 0) + b"\x80\x00"
    return bytes(out)


def test_c2_frames_are_never_counted_and_each_image_is_probed_once(monkeypatch, tmp_path):
    import time

    tiff = many_page_tiff(30000)
    res = result((fig("a"), tiff), (fig("b", PptxAnchor(slide=1, shape="b")), tiff))
    calls = []
    real = figures.probe
    monkeypatch.setattr(figures, "probe", lambda data, config: calls.append(1) or real(data, config))
    start = time.monotonic()
    figures.describe(res, twice(), CONFIG)
    figures.save(res, tmp_path, CONFIG)
    assert len(calls) == 1
    assert time.monotonic() - start < 3


def test_c2_a_many_frame_tiff_is_converted_from_its_first_frame_and_flagged(tmp_path):
    frames = [Image.new("RGB", (60, 60), (i * 20, 0, 0)) for i in range(3)]
    buffer = io.BytesIO()
    frames[0].save(buffer, format="TIFF", save_all=True, append_images=frames[1:])
    res = result((fig("a"), buffer.getvalue()))
    figures.save(res, tmp_path, CONFIG)
    assert "image.first-frame-only" in {f.rule for f in lines(res)[0].flags}


def test_c2_a_single_frame_tiff_is_not_flagged(tmp_path):
    res = result((fig("a"), encode(Image.new("RGB", (60, 60)), "TIFF")))
    figures.save(res, tmp_path, CONFIG)
    assert "image.first-frame-only" not in {f.rule for f in lines(res)[0].flags}


def shown_by(count, found=(("one two", (5, 5, 50, 20)), ("three", (5, 30, 50, 45)))):
    pairs = [(fig(f"f{i}", PptxAnchor(slide=i + 1, shape="s")), picture()) for i in range(count)]
    res = result(*pairs)
    return res, [Counting("one", list(found)), Counting("two", list(found))]


def test_c3_ocr_lines_copied_onto_figures_are_capped_per_document():
    config = Config(max_figure_ocr_lines=5)
    res, engines = shown_by(4)
    figures.describe(res, engines, config)
    first, second, third, fourth = lines(res)
    assert len(first.ocr_lines) == 2 and len(second.ocr_lines) == 2
    for late in (third, fourth):
        assert (
            late.ocr_lines == [] and late.description == "one two three" and late.description_source == "ocr"
        )
        assert "image.ocr-truncated" in {f.rule for f in late.flags}
        assert {f.rule for f in late.flags} == {"image.ocr-truncated", "image.ocr-unverified"}
        assert late.status == "flagged"
    assert not {f.rule for f in first.flags}
    assert [e.calls for e in engines] == [1, 1]


def test_c3_the_cap_is_a_reading_setting_that_is_left_out_of_the_reading_hash():
    assert Config().max_figure_ocr_lines == 20_000
    assert Config(max_figure_ocr_lines=1).reading_sha256() == Config().reading_sha256()
    assert Config(max_figure_ocr_lines=1).sha256() != Config().sha256()


def test_c3_an_ocr_description_with_no_lines_is_unverified_never_undescribed():
    res = result((fig("a", description="12,000", description_source="ocr"), picture()))
    policy.decide(res.document, CONFIG.policy)
    figure = lines(res)[0]
    assert {f.rule for f in figure.flags} == {"image.ocr-unverified"}


def test_i1_describe_and_save_leave_the_document_decided(tmp_path):
    res = result((fig("a"), picture()))
    figures.describe(res, twice(), CONFIG)
    figure = lines(res)[0]
    assert figure.status == "accepted" and not figure.flags
    assert figure.ocr_lines[0].status == "accepted" and res.document.summary["figures"] == 1
    figures.save(res, tmp_path, CONFIG)
    assert res.document.summary["figures_flagged"] == 0


def test_i1_save_never_sets_a_status_itself(monkeypatch, tmp_path):
    res = result((fig("a"), picture((8, 8))), (fig("b", PptxAnchor(slide=1, shape="b")), picture()))
    monkeypatch.setattr(policy, "decide", lambda document, policy_: None)
    figures.save(res, tmp_path, CONFIG)
    assert [f.status for f in lines(res)] == ["flagged"] and res.document.summary == {}


def dump(res):
    data = res.document.model_dump(mode="json")
    data.pop("created_at")
    return data


def test_i1_every_order_of_the_stages_gives_the_same_document(tmp_path):
    def build():
        return result(
            (fig("a"), picture()),
            (fig("b", PptxAnchor(slide=1, shape="b")), picture((8, 8))),
            (fig("c", PptxAnchor(slide=2, shape="c")), encode(Image.new("RGB", (60, 40)), "BMP")),
        )

    one = build()
    figures.describe(one, twice(), CONFIG)
    figures.save(one, tmp_path / "one", CONFIG)
    pipeline.write(one.document, tmp_path / "one", CONFIG)
    two = build()
    figures.describe(two, twice(), CONFIG)
    pipeline.write(two.document, tmp_path / "two", CONFIG, two.images)
    assert dump(one) == dump(two)
    assert (tmp_path / "one" / "document.md").read_text() == (tmp_path / "two" / "document.md").read_text()
    assert (tmp_path / "one" / "chunks.jsonl").read_text() == (tmp_path / "two" / "chunks.jsonl").read_text()
    assert one.document.summary["figures"] == 2


def test_i2_the_description_is_cut_at_a_word_and_marked():
    res = result((fig("a"), picture()))
    figures.describe(res, [Counting("one", [("Total " + "word " * 80 + "1,234,567", (0, 0, 9, 9))])], CONFIG)
    description = lines(res)[0].description
    assert description.endswith("…") and len(description) <= 301
    assert not description[:-1].endswith("1,234") and description[:-1].split()[-1] == "word"
    assert lines(res)[0].ocr_lines[0].text.endswith("1,234,567")


def test_i2_a_single_long_token_is_cut_at_300_code_points():
    res = result((fig("a"), picture()))
    figures.describe(res, [Counting("one", [("é" * 500, (0, 0, 9, 9))])], CONFIG)
    assert lines(res)[0].description == "é" * 300 + "…"


def test_i2_a_text_of_300_or_fewer_is_whole():
    res = result((fig("a"), picture()))
    figures.describe(res, [Counting("one", [("a " * 150, (0, 0, 9, 9))])], CONFIG)
    assert lines(res)[0].description == ("a " * 150).strip()


def test_i3_figures_not_reached_by_the_deadline_are_not_read_and_say_so():
    import time

    res, engines = shown_by(3)
    figures.describe(res, engines, CONFIG, deadline=time.monotonic() - 1)
    assert [e.calls for e in engines] == [0, 0]
    for figure in lines(res):
        assert figure.description_source == "none"
        assert "image.ocr-skipped" in {f.rule for f in figure.flags}


def test_i3_a_deadline_that_passes_midway_keeps_what_was_read():
    import time

    clock = iter([0.0])
    pairs = [(fig(f"f{i}", PptxAnchor(slide=i + 1, shape="s")), picture(colour=(i, 0, 0))) for i in range(3)]
    res = result(*pairs)
    engines = twice()
    real = time.monotonic
    time.monotonic = lambda: next(clock, 99.0)
    try:
        figures.describe(res, engines, CONFIG, deadline=50.0)
    finally:
        time.monotonic = real
    first, second, third = lines(res)
    assert first.description_source == "ocr"
    assert second.description_source == third.description_source == "none"
    assert "image.ocr-skipped" in {f.rule for f in third.flags}
    assert not {f.rule for f in first.flags}


def test_m_a_real_wmf_is_saved_as_wmf_and_an_emf_as_emf(tmp_path):
    wmf = b"\xd7\xcd\xc6\x9a" + b"\x00" * 40
    emf = b"\x01\x00\x00\x00" + b"\x00" * 36 + b" EMF" + b"\x00" * 60
    res = result((fig("a"), wmf), (fig("b", PptxAnchor(slide=1, shape="b")), emf))
    figures.save(res, tmp_path, CONFIG)
    assert [f.path.rsplit(".", 1)[1] for f in lines(res)] == ["wmf", "emf"]


def test_m_a_rotated_tiff_records_the_size_of_the_saved_image(tmp_path):
    exif = Image.Exif()
    exif[0x0112] = 6
    res = result((fig("a"), encode(Image.new("RGB", (100, 40)), "TIFF", exif=exif.tobytes())))
    figures.save(res, tmp_path, CONFIG)
    figure = lines(res)[0]
    with Image.open(tmp_path / figure.path) as saved:
        assert (figure.width, figure.height) == saved.size


def test_m_a_truncated_web_image_is_flagged_with_or_without_engines(tmp_path):
    cut = encode(Image.effect_noise((200, 200), 80).convert("RGB"))[:300]
    res = result((fig("a"), cut))
    figures.describe(res, [], CONFIG)
    assert "image.not-viewable" in {f.rule for f in lines(res)[0].flags}
    res = result((fig("a"), cut))
    figures.save(res, tmp_path, CONFIG)
    assert "image.not-viewable" in {f.rule for f in lines(res)[0].flags}


def test_m_a_too_large_image_that_is_not_a_web_format_is_also_not_viewable(tmp_path):
    small = Config(max_render_pixels=1000)
    res = result((fig("a"), encode(Image.new("RGB", (100, 100)), "BMP")), config=small)
    figures.save(res, tmp_path, small)
    assert {f.rule for f in lines(res)[0].flags} >= {"image.too-large", "image.not-viewable"}


def test_m_an_ocr_described_image_chunk_names_the_ocr_sources(tmp_path):
    res = result((fig("a"), picture()))
    figures.describe(res, twice(), CONFIG)
    from ragnaroq.export.chunks import to_chunks

    chunk = [c for c in to_chunks(res.document, CONFIG) if c["metadata"]["kind"] == "image"][0]
    assert chunk["metadata"]["sources"] == "ocr:one"


def test_m_markdown_links_a_saved_image_and_never_embeds_one_it_has_not(tmp_path):
    from ragnaroq.export.markdown import to_markdown

    res = result(
        (fig("a", PptxAnchor(slide=1, shape="saved")), picture()),
        (fig("b", PptxAnchor(slide=1, shape="gone"), description="Chart", description_source="alt"), None),
        (
            fig(
                "c",
                PptxAnchor(slide=1, shape="far"),
                path="https://example.org/x.png",
                description="Far",
                description_source="alt",
            ),
            None,
        ),
    )
    figures.describe(res, twice(), CONFIG)
    figures.save(res, tmp_path, CONFIG)
    md = to_markdown(res.document)
    assert "![₹12,000](images/slide-1-saved.png) *(slide 1, saved)*" in md
    assert "[image: Chart] *(slide 1, gone)*" in md and "![Chart]" not in md
    assert "[image: Far](https://example.org/x.png) *(slide 1, far)*" in md and "![Far]" not in md
