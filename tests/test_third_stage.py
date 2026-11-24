import copy

import pytest
from conftest import FakeEngine, FakeLineReader
from PIL import Image

from ragnaroq import third
from ragnaroq.config import Config, ThirdReaderSettings
from ragnaroq.isolation import BudgetExceeded, PageReader
from ragnaroq.model import Reading
from ragnaroq.ocr import EngineUnavailable, LineUnreadable
from ragnaroq.pipeline import convert

DUE = "The due date is 31st July 2026."
MISREAD = "The due date is 31st July 2028."  # a wrong digit that passes every validator
TITLE = "Circular No. 7 of 2026"
TITLE_MISREAD = "Circular No. 7 of 2028"
BOX_A = (150, 200, 900, 260)  # pixels at 200 dpi
BOX_B = (150, 360, 1100, 420)


def disputed(scanned_pdf, both=False):
    """The scanned fixture, decided once: the due line (and with `both`, the title) disputed."""
    first = FakeEngine("one", [(TITLE, BOX_A), (DUE, BOX_B)])
    second = FakeEngine("two", [(TITLE_MISREAD if both else TITLE, BOX_A), (MISREAD, BOX_B)])
    return convert(scanned_pdf, engines=[first, second])


def test_only_disputed_lines_are_read_and_stored_as_a_vlm_reading(scanned_pdf):
    document = disputed(scanned_pdf)
    title, due = document.pages[0].lines
    reader = FakeLineReader([DUE])
    third.read_disputed(document, scanned_pdf.read_bytes(), Config(), reader)
    assert len(reader.crops) == 1 and reader.prepared == 1
    assert due.readings[-1] == Reading(source="vlm:fake", text=DUE)
    assert not any(r.source.startswith("vlm:") for r in title.readings)


def test_a_crop_is_the_line_box_at_300_dpi_in_a_white_frame(scanned_pdf):
    due = disputed(scanned_pdf).pages[0].lines[1]
    settings = ThirdReaderSettings()
    page = Image.new("RGB", (2480, 3508), "black")
    image = third.crop(page, due.box, settings)
    scale = 300 / 72
    width = round((due.box.x1 - due.box.x0 + 4) * scale) + 24
    height = round((due.box.bottom - due.box.top) * scale) + 24
    assert abs(image.width - width) <= 1 and abs(image.height - height) <= 1
    assert image.getpixel((0, 0)) == (255, 255, 255)
    assert image.getpixel((image.width // 2, image.height // 2)) == (0, 0, 0)


def test_a_line_the_reader_gives_up_on_keeps_its_first_decision(scanned_pdf):
    document = disputed(scanned_pdf)
    reader = FakeLineReader([LineUnreadable("stopped after 20 seconds")])
    third.read_disputed(document, scanned_pdf.read_bytes(), Config(), reader)
    assert not any(r.source.startswith("vlm:") for line in document.lines() for r in line.readings)
    (flag,) = document.pages[0].flags
    assert flag.rule == "third-reader.unread" and flag.severity == "review"
    assert "p1-l2" in flag.message and "stopped after 20 seconds" in flag.message


def test_any_other_failure_is_flagged_by_type_and_not_cached(scanned_pdf, tmp_path):
    document = disputed(scanned_pdf)
    third.read_disputed(
        document, scanned_pdf.read_bytes(), Config(), FakeLineReader([ValueError("boom")]), cache=tmp_path
    )
    (flag,) = document.pages[0].flags
    assert flag.rule == "third-reader.unread" and "ValueError" in flag.message
    assert list(tmp_path.iterdir()) == []


def test_the_time_budget_leaves_the_rest_for_a_person(scanned_pdf):
    document = disputed(scanned_pdf, both=True)
    reader = FakeLineReader([])
    config = Config(third_reader=ThirdReaderSettings(budget_seconds=0.0))
    third.read_disputed(document, scanned_pdf.read_bytes(), config, reader)
    assert reader.crops == [] and reader.prepared == 0
    (flag,) = document.flags
    assert flag.rule == "third-reader.budget" and flag.severity == "review"
    assert "2 disputed lines" in flag.message and "time budget of 0 seconds" in flag.message


def test_a_page_that_cannot_be_drawn_again_keeps_its_first_decisions(scanned_pdf, monkeypatch):
    def refuse(self, number, crosscheck):
        raise BudgetExceeded("budget.page-time", "too slow")

    document = disputed(scanned_pdf)
    monkeypatch.setattr(PageReader, "read", refuse)
    reader = FakeLineReader([DUE])
    third.read_disputed(document, scanned_pdf.read_bytes(), Config(), reader)
    assert not any(r.source.startswith("vlm:") for line in document.lines() for r in line.readings)
    (flag,) = document.pages[0].flags
    assert flag.rule == "third-reader.unread" and "too slow" in flag.message
    assert reader.crops == []


def test_readings_and_refusals_are_cached_by_crop(scanned_pdf, tmp_path):
    data = scanned_pdf.read_bytes()
    document = disputed(scanned_pdf)
    fresh = copy.deepcopy(document)
    third.read_disputed(document, data, Config(), FakeLineReader([DUE]), cache=tmp_path)
    again = FakeLineReader([AssertionError("read again")])
    third.read_disputed(fresh, data, Config(), again, cache=tmp_path)
    assert fresh.pages[0].lines[1].readings[-1] == Reading(source="vlm:fake", text=DUE)
    assert again.prepared == 0 and again.crops == []

    other = tmp_path / "refusals"
    document, fresh = disputed(scanned_pdf), disputed(scanned_pdf)
    refusing = FakeLineReader([LineUnreadable("stopped after 20 seconds")])
    third.read_disputed(document, data, Config(), refusing, cache=other)
    again = FakeLineReader([AssertionError("read again")])
    third.read_disputed(fresh, data, Config(), again, cache=other)
    assert again.prepared == 0 and again.crops == []
    assert fresh.pages[0].flags == document.pages[0].flags and len(fresh.pages[0].flags) == 1


def test_nothing_is_loaded_when_nothing_is_disputed(scanned_pdf):
    first = FakeEngine("one", [(TITLE, BOX_A), (DUE, BOX_B)])
    document = convert(scanned_pdf, engines=[first, FakeEngine("two", first.lines)])
    reader = FakeLineReader([])
    third.read_disputed(document, scanned_pdf.read_bytes(), Config(), reader)
    assert reader.prepared == 0 and reader.crops == []
    assert third.candidates(document) == {}


def test_running_twice_replaces_the_third_reading(scanned_pdf):
    document = disputed(scanned_pdf)
    data = scanned_pdf.read_bytes()
    third.read_disputed(document, data, Config(), FakeLineReader([DUE]))
    third.read_disputed(document, data, Config(), FakeLineReader(["other"]))
    vlm = [r for r in document.pages[0].lines[1].readings if r.source.startswith("vlm:")]
    assert [r.text for r in vlm] == ["other"]


@pytest.mark.parametrize("both,expected", [(False, ["p1-l2"]), (True, ["p1-l1", "p1-l2"])])
def test_candidates_are_the_disputed_lines_in_order(scanned_pdf, both, expected):
    found = third.candidates(disputed(scanned_pdf, both=both))
    assert {number: [line.id for line in lines] for number, lines in found.items()} == {1: expected}


def test_a_model_that_will_not_load_stops_the_stage(scanned_pdf, tmp_path):
    class Broken(FakeLineReader):
        def prepare(self):
            self.prepared += 1
            raise EngineUnavailable("no model here")

    reader = Broken([DUE])
    with pytest.raises(EngineUnavailable):
        third.read_disputed(
            disputed(scanned_pdf, both=True), scanned_pdf.read_bytes(), Config(), reader, cache=tmp_path
        )
    assert reader.prepared == 1 and reader.crops == []
    assert list(tmp_path.iterdir()) == []


def test_a_drawing_error_without_isolation_keeps_the_first_decisions(scanned_pdf, monkeypatch):
    document = disputed(scanned_pdf)

    def refuse(*args, **kwargs):
        raise ValueError("Render exceeds the pixel budget")

    monkeypatch.setattr("ragnaroq.native.render", refuse)
    reader = FakeLineReader([DUE])
    third.read_disputed(document, scanned_pdf.read_bytes(), Config(isolate_pages=False), reader)
    assert not any(r.source.startswith("vlm:") for line in document.lines() for r in line.readings)
    (flag,) = document.pages[0].flags
    assert flag.rule == "third-reader.unread" and "Render exceeds the pixel budget" in flag.message
    assert reader.crops == []


def test_convert_settles_a_disputed_scanned_line(scanned_pdf):
    first = FakeEngine("one", [(TITLE, BOX_A), (DUE, BOX_B)])
    second = FakeEngine("two", [(TITLE, BOX_A), (MISREAD, BOX_B)])
    document = convert(scanned_pdf, engines=[first, second], third_reader=FakeLineReader([DUE]))
    due = document.pages[0].lines[1]
    assert due.status == "accepted" and due.resolution.agreed == ["ocr:one", "vlm:fake"]
    assert document.engine.components["vlm:fake"] == "test"


def test_a_corrected_line_reaches_the_markdown(scanned_pdf):
    from ragnaroq.export.markdown import to_markdown

    first = FakeEngine("one", [(TITLE, BOX_A), (MISREAD, BOX_B)])
    second = FakeEngine("two", [(TITLE, BOX_A), (DUE, BOX_B)])
    document = convert(scanned_pdf, engines=[first, second], third_reader=FakeLineReader([DUE]))
    markdown = to_markdown(document)
    assert DUE in markdown and MISREAD not in markdown


def test_a_corrected_title_line_corrects_the_document_title(scanned_pdf):
    from ragnaroq.export.chunks import to_chunks

    heading, misread = "SCHEDULE 7 of 2026", "SCHEDULE 7 of 2028"
    first = FakeEngine("one", [(misread, BOX_A), (DUE, BOX_B)])
    second = FakeEngine("two", [(heading, BOX_A), (DUE, BOX_B)])
    document = convert(scanned_pdf, engines=[first, second], third_reader=FakeLineReader([heading]))
    title = document.pages[0].lines[0]
    assert title.role == "heading" and title.text == heading and title.resolution.text_from == "ocr:two"
    assert document.title == heading
    (chunk,) = to_chunks(document, Config())
    assert chunk["context"] == [heading] and misread not in chunk["embedding_text"]
