"""Scoring a reader's document against a hand-written answer key, by anchor."""

import json
import unicodedata
from pathlib import Path

from ragnaroq import evaluate, expected
from ragnaroq.anchors import CsvAnchor, DocxAnchor, XlsxAnchor
from ragnaroq.model import Document, Engine, Figure, Flag, Line, Page, Source

DEMO = Path(__file__).parent.parent / "references" / "demo"
DEMO_FILES = [
    "guide.docx",
    "rates.xlsx",
    "rates-nocache.xlsx",
    "deck.pptx",
    "notice.html",
    "readme.md",
    "deductions.json",
    "notice.xml",
    "bomb.xml",
    "rates.csv",
]


def _line(n, anchor, text, flags=()):
    return Line(
        id=f"l{n}",
        page=1,
        order=n,
        anchor=anchor,
        raw=text,
        text=text,
        source="native",
        flags=[Flag(rule=r, severity="review", message=r) for r in flags],
    )


def _document(lines=(), figures=(), flags=(), name="x.xlsx"):
    page = Page(number=1, kind="sheet", lines=list(lines), figures=list(figures))
    return Document(
        source=Source(sha256="0" * 64, bytes=1, filename=name, pages=1, format="xlsx"),
        engine=Engine(name="t", version="0", policy_version="0", config_sha256="0", components={}),
        created_at="2026-11-26T00:00:00Z",
        pages=[page],
        flags=[Flag(rule=r, severity="review", message=r) for r in flags],
    )


A1 = XlsxAnchor(sheet="Rates", cell="A1")
A2 = XlsxAnchor(sheet="Rates", cell="A2")
ITEMS = [
    expected.Expected(anchor=A1, text="5%"),
    expected.Expected(anchor=A2, text="₹1,50,000", flag="xlsx.format-unknown"),
    expected.Expected(document_flag="xlsx.hidden"),
]


def _right():
    return _document(
        [_line(1, A1, "5%"), _line(2, A2, "₹1,50,000", ["xlsx.format-unknown"])], flags=["xlsx.hidden"]
    )


def test_a_right_document_scores_ok():
    result = expected.score(_right(), ITEMS)
    assert result.ok and result.right == 3
    assert not (result.wrong or result.missing or result.extra or result.flags_missing)


def test_wrong_text_is_reported_with_its_anchor():
    document = _right()
    document.pages[0].lines[0].text = "0.05"
    result = expected.score(document, ITEMS)
    assert not result.ok and result.right == 2
    assert result.wrong == [{"anchor": A1.model_dump(), "expected": "5%", "read": "0.05"}]


def test_text_is_compared_after_nfc_and_nothing_else():
    items = [expected.Expected(anchor=A1, text="é")]
    assert expected.score(_document([_line(1, A1, unicodedata.normalize("NFD", "é"))]), items).ok
    assert not expected.score(_document([_line(1, A1, "é ")]), items).ok
    assert not expected.score(_document([_line(1, A1, "E")]), items).ok


def test_a_missing_anchor_is_reported():
    document = _document([_line(1, A1, "5%")], flags=["xlsx.hidden"])
    result = expected.score(document, ITEMS)
    assert not result.ok
    assert result.missing == [{"anchor": A2.model_dump(), "expected": "₹1,50,000"}]


def test_a_missing_flag_fails_the_score():
    document = _document([_line(1, A1, "5%"), _line(2, A2, "₹1,50,000")])
    result = expected.score(document, ITEMS)
    assert not result.ok
    assert {"flag": "xlsx.format-unknown", "anchor": A2.model_dump()} in result.flags_missing
    assert {"document_flag": "xlsx.hidden"} in result.flags_missing


def test_extra_lines_are_reported_but_do_not_fail():
    extra = XlsxAnchor(sheet="Rates", cell="Z9")
    document = _right()
    document.pages[0].lines.append(_line(3, extra, "stray"))
    result = expected.score(document, ITEMS)
    assert result.ok
    assert result.extra == [{"anchor": extra.model_dump(), "read": "stray"}]


def test_a_figure_is_matched_by_anchor_and_checked_for_its_description():
    anchor = DocxAnchor(paragraph=4)
    items = [expected.Expected(figure=anchor, description="Bar chart", description_source="alt")]
    figure = Figure(id="f1", page=1, anchor=anchor, description="Bar chart", description_source="alt")
    assert expected.score(_document(figures=[figure]), items).ok
    figure.description_source = "caption"
    result = expected.score(_document(figures=[figure]), items)
    assert not result.ok and len(result.wrong) == 1
    assert not expected.score(_document(), items).ok


def test_load_reads_one_object_per_line(tmp_path):
    path = tmp_path / "a.expected.jsonl"
    path.write_text(
        json.dumps({"anchor": {"type": "csv", "row": 2, "column": "name"}, "text": "é"}, ensure_ascii=False)
        + "\n\n"
        + json.dumps({"document_flag": "xml.entities"})
        + "\n",
        encoding="utf8",
    )
    items = expected.load(path)
    assert items[0].anchor == CsvAnchor(row=2, column="name") and items[0].text == "é"
    assert items[1].document_flag == "xml.entities"


def test_a_folder_with_expected_files_is_scored_by_anchor(tmp_path):
    (tmp_path / "a.xlsx").write_bytes(b"one")
    (tmp_path / "b.xlsx").write_bytes(b"two")
    (tmp_path / "unscored.xlsx").write_bytes(b"three")
    for name in ("a", "b"):
        (tmp_path / f"{name}.xlsx.expected.jsonl").write_text(
            json.dumps({"anchor": A1.model_dump(), "text": "5%"}) + "\n", encoding="utf8"
        )

    def read(data, filename, config):
        text = "5%" if filename == "a.xlsx" else "6%"
        return _document([_line(1, A1, text)], name=filename)

    report = evaluate.run_exact(tmp_path, read)
    assert [f.name for f in report.files] == ["a.xlsx", "b.xlsx"]
    assert [f.score.ok for f in report.files] == [True, False]
    assert not report.ok
    text = evaluate.exact_markdown(report)
    assert "## Exact formats" in text and "b.xlsx" in text


def test_run_exact_accepts_an_object_with_a_document_and_reports_a_failed_read(tmp_path):
    (tmp_path / "a.xlsx").write_bytes(b"one")
    (tmp_path / "a.xlsx.expected.jsonl").write_text(json.dumps({"document_flag": "xml.entities"}) + "\n")

    class Result:
        document = _document(flags=["xml.entities"])

    assert evaluate.run_exact(tmp_path, lambda data, name, config: Result()).ok

    def refuse(data, name, config):
        raise ValueError("refused")

    report = evaluate.run_exact(tmp_path, refuse)
    assert not report.ok and "refused" in report.files[0].error


def test_the_demo_folder_files_are_present():
    for name in DEMO_FILES:
        assert (DEMO / name).is_file(), name
        key = DEMO / f"{name}.expected.jsonl"
        assert key.is_file(), key.name
        assert expected.load(key), key.name
    for name in ("logo.png", "diagram.png"):
        assert (DEMO / name).is_file()
    on_disk = {p.name.removesuffix(".expected.jsonl") for p in DEMO.glob("*.expected.jsonl")}
    assert on_disk == set(DEMO_FILES)


def test_cells_that_share_one_anchor_are_matched_one_to_one():
    from ragnaroq.anchors import MdAnchor

    row = MdAnchor(lines=(7, 7))
    items = [expected.Expected(anchor=row, text="Slab"), expected.Expected(anchor=row, text="Rate")]
    document = _document([_line(1, row, "Rate"), _line(2, row, "Slab")])
    assert expected.score(document, items).ok
    assert not expected.score(_document([_line(1, row, "Slab"), _line(2, row, "Slab")]), items).ok
    assert expected.score(
        _document([_line(1, row, "Slab"), _line(2, row, "Rate"), _line(3, row, "x")]), items
    ).ok


def test_a_figure_flag_is_checked():
    anchor = DocxAnchor(paragraph=4)
    items = [expected.Expected(figure=anchor, description="", flag="image.remote-not-fetched")]
    figure = Figure(id="f1", page=1, anchor=anchor)
    assert not expected.score(_document(figures=[figure]), items).ok
    figure.flags = [Flag(rule="image.remote-not-fetched", severity="review", message="m")]
    assert expected.score(_document(figures=[figure]), items).ok
