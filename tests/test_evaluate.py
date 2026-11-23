"""Reference sets and the evaluation. Synthetic PDFs and fake readers only, so every
outcome is set up on purpose."""

import json
import time

import pdfplumber
import pytest
from conftest import FakeEngine
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

from ragnaroq import Config, convert, evaluate, reference
from ragnaroq.cli import main

LINES = [
    "Circular No. 7 of 2026",
    "The due date is 31st July 2026.",
    "Interest is not payable on refunds.",
]


@pytest.fixture(scope="session")
def three_lines(tmp_path_factory):
    path = tmp_path_factory.mktemp("pdf") / "three.pdf"
    page = canvas.Canvas(str(path), pagesize=A4)
    page.setFont("Helvetica", 14)
    for index, text in enumerate(LINES):
        page.drawString(72, 760 - index * 40, text)
    page.save()
    return path


@pytest.fixture(scope="session")
def clean_reference(three_lines, tmp_path_factory):
    folder = tmp_path_factory.mktemp("refs")
    pdf, truth = reference.make(three_lines, "clean", folder)
    return folder, pdf, truth


def _line_boxes(truth_page, dpi):
    """The pixel box of each true line, grouping words by their top edge."""
    scale = dpi / 72
    rows: dict[int, list] = {}
    for word in truth_page.words:
        rows.setdefault(round(word.top), []).append(word)
    boxes = []
    for top in sorted(rows):
        words = rows[top]
        boxes.append(
            (
                min(w.x0 for w in words) * scale,
                min(w.top for w in words) * scale,
                max(w.x1 for w in words) * scale,
                max(w.bottom for w in words) * scale,
            )
        )
    return boxes


def _readers(truth, first_texts, second_texts, dpi=200):
    boxes = _line_boxes(truth.pages[0], dpi)
    first = FakeEngine("first", [(t, b) for t, b in zip(first_texts, boxes, strict=True) if t is not None])
    second = FakeEngine("second", [(t, b) for t, b in zip(second_texts, boxes, strict=True) if t is not None])
    return [first, second]


def test_a_reference_is_an_image_only_pdf_with_every_word_and_its_box(clean_reference):
    _, pdf, truth_path = clean_reference
    truth = reference.load(truth_path)
    words = [w.text for w in truth.pages[0].words]
    assert words[:4] == ["Circular", "No.", "7", "of"] and "31st" in words
    with pdfplumber.open(pdf) as opened:
        page = opened.pages[0]
        assert not page.chars  # no text layer: the engine must read it like a scan
        assert abs(float(page.width) - A4[0]) < 1 and abs(float(page.height) - A4[1]) < 1
    meta = json.loads(truth_path.read_text())
    assert meta["format"] == reference.FORMAT and meta["profile"]["name"] == "clean"


def test_the_same_pdf_always_gives_the_same_reference(three_lines, tmp_path):
    a, truth_a = reference.make(three_lines, "blurred", tmp_path / "a")
    time.sleep(1.1)  # a clock in the file would now show a different second
    b, truth_b = reference.make(three_lines, "blurred", tmp_path / "b")
    assert a.read_bytes() == b.read_bytes() and truth_a.read_text() == truth_b.read_text()


def test_a_tilted_page_moves_each_word_with_it(three_lines, tmp_path):
    _, straight = reference.make(three_lines, "clean", tmp_path)
    _, tilted = reference.make(three_lines, "tilted", tmp_path)
    a = reference.load(straight).pages[0].words[0]
    b = reference.load(tilted).pages[0].words[0]
    assert a.text == b.text
    # Turned 0.8 degrees counter-clockwise about the centre: a word near the top left
    # moves down and slightly left, by a few points, not more.
    assert 0 < b.top - a.top < 10 and -10 < b.x0 - a.x0 < 0


def test_a_file_with_no_known_answer_is_refused_plainly(scanned_pdf, tmp_path):
    with pytest.raises(ValueError, match="no page with a usable text layer"):
        reference.make(scanned_pdf, "clean", tmp_path)
    fake = tmp_path / "page.pdf"
    fake.write_text("<html>Access Denied</html>")
    with pytest.raises(ValueError, match="is not a PDF"):
        reference.make(fake, "clean", tmp_path)


def test_wrong_lines_that_both_readers_agree_on_are_caught_as_false_accepts(clean_reference):
    _, pdf, truth_path = clean_reference
    truth = reference.load(truth_path)
    engines = _readers(
        truth,
        ["Circular No. 1 of 2026", LINES[1], "Interest is not payable on refunds."],
        ["Circular No. 1 of 2026", LINES[1], "Interest is payable on refunds."],
    )
    result = evaluate.score(convert(pdf, engines=engines), truth)
    t = result.tally
    assert (t.lines, t.accepted, t.flagged) == (3, 2, 1)
    # Both readers misread 7 as 1 the same way, and no rule can tell: accepted, and
    # wrong on a critical token. This is the failure the evaluation exists to count.
    assert (t.accepted_wrong, t.accepted_critical_wrong) == (1, 1)
    # The dropped "not" is caught by the rules; the line kept is right, so this
    # review was the cost of safety.
    assert t.flagged_right == 1
    assert t.missed_words == 0 and t.extra_lines == 0
    rates = t.rates()
    assert rates["false_accept_rate"] == 0.5 and rates["needless_review_share"] == 1.0


def test_an_agreed_misreading_of_a_date_is_still_caught_by_the_date_rule(clean_reference):
    _, pdf, truth_path = clean_reference
    truth = reference.load(truth_path)
    misread = "The due date is 3lst July 2026."
    engines = _readers(truth, [LINES[0], misread, LINES[2]], [LINES[0], misread, LINES[2]])
    result = evaluate.score(convert(pdf, engines=engines), truth)
    date = next(line for line in result.lines if "3lst" in line.read)
    assert date.status == "flagged" and result.tally.accepted_wrong == 0


def test_missed_and_invented_lines_are_counted(clean_reference):
    _, pdf, truth_path = clean_reference
    truth = reference.load(truth_path)
    engines = _readers(truth, [LINES[0], LINES[1], None], [LINES[0], LINES[1], None])
    stamp = ("RECEIVED", (150, 1500, 600, 1560))
    engines[0].lines.append(stamp)
    engines[1].lines.append(stamp)
    t = evaluate.score(convert(pdf, engines=engines), truth).tally
    assert t.missed_words == len(LINES[2].split())
    assert t.extra_lines == 1 and t.accepted_wrong == 1  # an invented line both readers agree on


def test_the_threshold_sweep_shows_what_each_threshold_would_accept(clean_reference):
    _, pdf, truth_path = clean_reference
    truth = reference.load(truth_path)
    engines = _readers(truth, LINES, ["Circular No. 7 of 2026.", LINES[1], LINES[2]])
    result = evaluate.score(convert(pdf, engines=engines), truth)
    rows = {row["threshold"]: row for row in evaluate.sweep(result.lines)}
    # A stray full stop: similar, same critical tokens. Accepted at 0.90, not at 1.00.
    assert rows[0.90]["accepted"] == 3 and rows[1.00]["accepted"] == 2
    assert rows[0.90]["accepted_wrong"] == 0


def test_a_folder_is_evaluated_and_reported(clean_reference, tmp_path):
    folder, _, truth_path = clean_reference
    truth = reference.load(truth_path)
    engines = _readers(truth, LINES, LINES)
    report = evaluate.run(folder, config=Config(), engines=engines)
    files = evaluate.write(report, tmp_path / "out")
    markdown = files["markdown"].read_text()
    assert "Accepted but wrong" in markdown and "| 0.90 ←" in markdown
    data = json.loads(files["json"].read_text())
    assert data["total"]["lines"] == 3 and data["total"]["false_accept_rate"] == 0.0


def test_reference_command_writes_every_profile(three_lines, tmp_path, capsys):
    assert main(["reference", "make", str(three_lines), "--out", str(tmp_path)]) == 0
    made = json.loads(capsys.readouterr().out)["references"]
    assert len(made) == len(reference.PROFILES)
    assert main(["reference", "make", str(tmp_path / "missing.pdf"), "--out", str(tmp_path)]) == 1


def test_comparison_forgives_typography_and_nothing_else():
    assert evaluate.canonical("“Quoted” — Text") == evaluate.canonical('"quoted" - text')
    assert evaluate.canonical("31st") != evaluate.canonical("3lst")
    assert evaluate.distance("kitten", "sitting") == 3


def test_a_reference_can_be_limited_to_chosen_pages(native_pdf, tmp_path):
    assert reference.page_numbers("1-3, 7") == {1, 2, 3, 7}
    with pytest.raises(ValueError):
        reference.page_numbers("3-1")
    _, truth_path = reference.make(native_pdf, "clean", tmp_path, pages={2})
    truth = reference.load(truth_path)
    assert [(p.number, p.source_page) for p in truth.pages] == [(1, 2)]
    # Without a selection, every page with a text layer is kept.
    _, all_pages = reference.make(native_pdf, "clean", tmp_path / "all")
    assert [p.source_page for p in reference.load(all_pages).pages] == [1, 2]


def test_leader_dots_and_fill_lines_are_layout_not_text():
    assert evaluate.canonical("Add lines 22 and 23 . . . . . . 24") == evaluate.canonical(
        "Add lines 22 and 23 24"
    )
    assert evaluate.canonical("Name ____________ Date") == evaluate.canonical("Name Date")
    assert evaluate.canonical("Section 10(46)") != evaluate.canonical("Section 10(48)")


def _line(id_, x0, top, x1, bottom, text):
    from ragnaroq.model import Box, Line

    return Line(
        id=id_,
        page=1,
        order=0,
        box=Box(x0=x0, top=top, x1=x1, bottom=bottom),
        raw=text,
        text=text,
        source="ocr:a",
    )


def test_a_word_inside_two_overlapping_lines_goes_to_the_one_that_read_it():
    word = reference.Word("12d", 100, 100, 120, 110)
    tall = _line("tall", 50, 90, 400, 130, "—— 23 Other taxes")  # a stray mark stretched this box
    small = _line("small", 90, 95, 200, 112, "or 12d, see inst.")
    owned, missed = evaluate._assign([word], [tall, small])
    assert owned["small"] == [word] and not missed
    # When no line read it, the smallest box still gets it.
    owned, _ = evaluate._assign([word], [tall, _line("other", 90, 95, 200, 112, "unrelated")])
    assert owned["other"] == [word]


def test_lines_over_sideways_text_are_not_scored(clean_reference):
    _, pdf, truth_path = clean_reference
    truth = reference.load(truth_path)
    page = truth.pages[0]
    sideways = reference.Word("mroF", 20, 400, 30, 440)
    truth = reference.Truth(
        pages=(
            reference.TruthPage(
                page.number, page.source_page, page.width, page.height, page.words, (sideways,)
            ),
        ),
        meta=truth.meta,
    )
    engines = _readers(truth, LINES, LINES)
    noise = ("€", (20 * 200 / 72, 400 * 200 / 72, 30 * 200 / 72, 440 * 200 / 72))
    engines[0].lines.append(noise)
    engines[1].lines.append(noise)
    t = evaluate.score(convert(pdf, engines=engines), truth).tally
    assert t.ignored_lines == 1 and t.lines == 3 and t.accepted_wrong == 0


def test_a_cached_reading_is_reused_and_scored_again(clean_reference, tmp_path):
    folder, _, truth_path = clean_reference
    truth = reference.load(truth_path)
    engines = _readers(truth, LINES, LINES)
    first = evaluate.run(folder, engines=engines, cache=tmp_path / "cache")
    assert len(list((tmp_path / "cache").glob("*.json"))) == 1

    def unreadable(image, languages):
        raise AssertionError("read again although cached")

    for engine in engines:
        engine.read = unreadable
    again = evaluate.run(folder, engines=engines, cache=tmp_path / "cache")
    assert again.total.lines == first.total.lines == 3


def test_dash_styles_do_not_count_as_a_different_number():
    assert evaluate.critical("Complete Steps 2–4") == evaluate.critical("Complete Steps 2-4")
    assert evaluate.critical("Steps 2–4") != evaluate.critical("Steps 2–5")


def test_text_drawn_twice_to_fake_bold_counts_once(tmp_path):
    path = tmp_path / "bold.pdf"
    page = canvas.Canvas(str(path), pagesize=A4)
    page.setFont("Helvetica", 12)
    for offset in (0, 0.3):  # the same text, overprinted a hair to the right
        page.drawString(72 + offset, 760, "Publication 559 Survivors")
    page.drawString(72, 740, "and executors of an estate.")
    page.save()
    _, truth_path = reference.make(path, "clean", tmp_path / "out")
    words = [w.text for w in reference.load(truth_path).pages[0].words]
    assert words.count("559") == 1


def test_text_with_no_size_is_not_part_of_the_answer(tmp_path):
    path = tmp_path / "hidden.pdf"
    page = canvas.Canvas(str(path), pagesize=A4)
    page.setFont("Helvetica", 12)
    page.drawString(72, 760, "Publication 559 Survivors")
    page.setFont("Helvetica", 0.01)  # drawn, but too small to exist on paper
    page.drawString(300, 760, "559")
    page.save()
    _, truth_path = reference.make(path, "clean", tmp_path / "out")
    assert [w.text for w in reference.load(truth_path).pages[0].words] == ["Publication", "559", "Survivors"]


def test_a_stored_reading_is_decided_again_under_the_current_policy(clean_reference, tmp_path):
    from dataclasses import replace

    from ragnaroq import Policy

    folder, _, truth_path = clean_reference
    truth = reference.load(truth_path)
    engines = _readers(truth, LINES, ["Circular No. 7 of 2026.", LINES[1], LINES[2]])
    cache = tmp_path / "cache"
    lenient = evaluate.run(folder, engines=engines, cache=cache)
    strict = evaluate.run(
        folder, config=replace(Config(), policy=Policy(min_similarity=1.0)), engines=engines, cache=cache
    )
    # One reading, stored once, decided twice: the stray full stop passes at 0.90
    # and not at 1.00.
    assert len(list(cache.glob("*.json"))) == 1
    assert (lenient.total.accepted, strict.total.accepted) == (3, 2)


def test_deciding_twice_gives_the_same_verdicts(clean_reference):
    from ragnaroq import policy

    _, pdf, truth_path = clean_reference
    truth = reference.load(truth_path)
    engines = _readers(truth, LINES, ["Circular No. 7 of 2026", LINES[1], "Interest is payable on refunds."])
    document = convert(pdf, engines=engines)
    before = [(line.status, [flag.rule for flag in line.flags]) for line in document.lines()]
    policy.decide(document, Config().policy)
    assert [(line.status, [flag.rule for flag in line.flags]) for line in document.lines()] == before
