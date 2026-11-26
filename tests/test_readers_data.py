"""JSON, CSV and XML readers and the registry that finds them."""

import shutil
from pathlib import Path

import pytest

from ragnaroq import evaluate, readers
from ragnaroq.anchors import cite
from ragnaroq.config import Config
from ragnaroq.readers import ReaderRefused, detect, read_bytes, refused_document

DEMO = Path(__file__).parent.parent / "references" / "demo"
BOMB = (DEMO / "bomb.xml").read_bytes()


def triples(doc):
    return [(line.text, cite(line.anchor), line.role) for line in doc.lines()]


def test_json_keys_are_headings_and_scalars_carry_their_key():
    doc = read_bytes(b'{"deductions": {"80C": {"limit": 150000}}}', "d.json", Config()).document
    assert triples(doc) == [
        ("deductions", "/deductions", "heading"),
        ("80C", "/deductions/80C", "heading"),
        ("limit: 150000", "/deductions/80C/limit", "body"),
    ]
    assert doc.source.format == "json" and doc.title == "deductions"
    assert all(line.status == "accepted" for line in doc.lines())


def test_json_values_render_as_json_shows_them():
    data = b'{"a": true, "b": null, "c": "x", "d": 1.50, "e": ["u", 2]}'
    doc = read_bytes(data, "v.json", Config()).document
    assert [line.text for line in doc.lines()] == ["a: true", "b: null", "c: x", "d: 1.50", "e", "u", "2"]
    assert [cite(line.anchor) for line in doc.lines()][-2:] == ["/e/0", "/e/1"]


def test_json_pointers_are_escaped():
    doc = read_bytes(b'{"a/b": {"c~d": 1}}', "e.json", Config()).document
    assert [cite(line.anchor) for line in doc.lines()] == ["/a~1b", "/a~1b/c~0d"]


def test_a_json_array_of_records_is_a_table():
    data = b'[{"section":"80C","limit":150000},{"section":"80D","limit":25000}]'
    doc = read_bytes(data, "t.json", Config()).document
    (table,) = doc.pages[0].tables
    assert (table.rows, table.columns) == (3, 2)
    assert [c.text for c in table.cells][:2] == ["section", "limit"]
    cells = {cite(line.anchor): line.text for line in doc.lines() if line.role == "table"}
    assert cells["/0/section"] == "80C" and cells["/1/limit"] == "25000"
    assert all(line.status == "accepted" for line in doc.lines())


def test_a_duplicate_json_key_keeps_the_last_and_flags_the_document():
    doc = read_bytes(b'{"limit": 1, "limit": 2}', "d.json", Config()).document
    assert [line.text for line in doc.lines()] == ["limit: 2"]
    assert "json.duplicate-key" in {f.rule for f in doc.flags}


def test_deeply_nested_json_fails_alone():
    data = ("[" * 10_000 + "]" * 10_000).encode()
    with pytest.raises(ReaderRefused, match="nested") as e:
        read_bytes(data, "deep.json", Config())
    assert e.value.rule == "json.too-deep"


def test_brackets_inside_json_strings_do_not_count_as_depth():
    doc = read_bytes(('{"k": "' + "[" * 1000 + '"}').encode(), "s.json", Config()).document
    assert len(list(doc.lines())) == 1


def test_invalid_json_is_refused():
    with pytest.raises(ReaderRefused) as e:
        read_bytes(b'{"a": ', "bad.json", Config())
    assert e.value.rule == "json.invalid"


@pytest.mark.parametrize(
    "raw, flagged",
    [
        ("Section;Amount\n80C;₹1,50,000\n".encode("utf-16"), False),
        ("Section;Amount\nCafé;€12\n".encode("cp1252"), True),
        ("﻿Section,Amount\n80C,150000\n".encode(), False),
    ],
)
def test_csv_encodings(raw, flagged):
    doc = read_bytes(raw, "r.csv", Config()).document
    assert cite(next(doc.lines()).anchor) == "row 1, Section"
    assert ("encoding.guessed" in {f.rule for f in doc.flags}) is flagged


def test_csv_sniffs_the_delimiter_and_builds_one_table():
    doc = read_bytes("Section;Amount\n80C;₹1,50,000\n".encode(), "r.csv", Config()).document
    assert [(line.text, cite(line.anchor)) for line in doc.lines()] == [
        ("Section", "row 1, Section"),
        ("Amount", "row 1, Amount"),
        ("80C", "row 2, Section"),
        ("₹1,50,000", "row 2, Amount"),
    ]
    (table,) = doc.pages[0].tables
    assert (table.rows, table.columns) == (2, 2)


def test_a_csv_cell_with_a_newline_stays_one_cell():
    doc = read_bytes(b'a,b\n"x\ny",z\nq,r\n', "n.csv", Config()).document
    texts = {cite(line.anchor): line.text for line in doc.lines()}
    assert texts["row 2, a"] == "x\ny" and texts["row 3, a"] == "q"


def test_empty_csv_cells_make_no_line():
    doc = read_bytes(b"a,b\n1,\n", "e.csv", Config()).document
    assert [line.text for line in doc.lines()] == ["a", "b", "1"]


def test_xml_elements_are_lines_at_their_paths():
    data = b'<notice id="1"><body><para>One</para><para lang="hi">Two</para></body></notice>'
    doc = read_bytes(data, "n.xml", Config()).document
    assert triples(doc) == [
        ("notice", "/notice", "heading"),
        ("id: 1", "/notice/@id", "body"),
        ("body", "/notice/body", "heading"),
        ("para: One", "/notice/body/para[1]", "body"),
        ("lang: hi", "/notice/body/para[2]/@lang", "body"),
        ("para: Two", "/notice/body/para[2]", "body"),
    ]


def test_an_xml_entity_is_refused():
    with pytest.raises(ReaderRefused) as e:
        read_bytes(BOMB, "bomb.xml", Config())
    assert e.value.rule == "xml.entities"


def test_a_refused_document_carries_the_blocking_flag():
    error = ReaderRefused("xml.entities", "Entities are not read.")
    doc = refused_document("bomb.xml", BOMB, error)
    assert not list(doc.lines()) and len(doc.pages) == 1
    (flag,) = doc.flags
    assert (flag.rule, flag.severity, flag.message) == ("xml.entities", "blocking", "Entities are not read.")


def test_unknown_content_is_not_read():
    assert detect("notes.txt", b"hello") is None
    assert detect("fake.xlsx", b"PK\x03\x04junk") is None
    with pytest.raises(ReaderRefused):
        read_bytes(b"hello", "notes.txt", Config())


def test_detect_checks_content_and_extension():
    assert detect("x.bin", b"%PDF-1.7\n") == "pdf"
    assert detect("a.json", b'{"a": 1}') == "json"
    assert detect("a.json", b"not json") is None
    assert detect("a.xml", b"<?xml version='1.0'?><a/>") == "xml"
    assert detect("a.csv", b"a,b\n1,2\n") == "csv"
    assert detect("a.md", b"# Title") == "md"
    assert detect("a.html", b"<html></html>") == "html"


def test_detect_zip_formats_need_their_main_part(tmp_path):
    import zipfile

    def build(*names):
        path = tmp_path / "z.zip"
        with zipfile.ZipFile(path, "w") as z:
            for n in names:
                z.writestr(n, "<x/>")
        return path.read_bytes()

    assert detect("a.docx", build("[Content_Types].xml", "word/document.xml")) == "docx"
    assert detect("a.xlsx", build("[Content_Types].xml", "xl/workbook.xml")) == "xlsx"
    assert detect("a.pptx", build("[Content_Types].xml", "ppt/presentation.xml")) == "pptx"
    assert detect("a.docx", build("[Content_Types].xml")) is None


def test_reader_versions_names_the_core_readers():
    assert {"json", "csv", "xml"} <= set(readers.READER_VERSIONS)
    assert readers.READER_VERSIONS["xml"]


def test_read_bytes_is_exported():
    import ragnaroq

    assert ragnaroq.read_bytes is read_bytes


def read_or_refuse(data, name, config):
    try:
        return read_bytes(data, name, config)
    except ReaderRefused as error:
        return refused_document(name, data, error)


def test_the_four_demo_files_score_ok():
    report = evaluate.run_exact(DEMO, read_or_refuse)
    by_name = {f.name: f for f in report.files}
    for name in ("deductions.json", "notice.xml", "bomb.xml", "rates.csv"):
        assert by_name[name].ok, (name, by_name[name].error, by_name[name].score)


def test_run_exact_scores_a_refusal_as_that_document():
    report = evaluate.run_exact(DEMO, readers.read_bytes)
    assert {f.name: f.ok for f in report.files}["bomb.xml"]


def test_evaluate_run_scores_a_folder_of_answer_keys(tmp_path):
    for name in ("deductions.json", "bomb.xml"):
        shutil.copy(DEMO / name, tmp_path / name)
        shutil.copy(DEMO / (name + ".expected.jsonl"), tmp_path / (name + ".expected.jsonl"))
    report = evaluate.run(tmp_path, config=Config(), engines=[])
    assert report.exact is not None and report.exact.ok
    markdown = evaluate.to_markdown(report)
    assert "## Exact formats" in markdown and "deductions.json" in markdown
    assert evaluate.write(report, tmp_path / "out")["markdown"].read_text().count("## Exact formats") == 1


def _timed(data, name):
    import time

    start = time.perf_counter()
    read_bytes(data, name, Config())
    return time.perf_counter() - start


def test_a_wide_file_reads_in_linear_time():
    xml = ("<r>" + "<a>x</a>" * 20_000 + "</r>").encode()
    csv_data = ("a,b\n" + "1,2\n" * 20_000).encode()
    assert _timed(xml, "w.xml") < 3
    assert _timed(csv_data, "w.csv") < 3
    records = ('[' + ",".join('{"k": 1}' for _ in range(20_000)) + "]").encode()
    assert _timed(records, "w.json") < 3
    flat = ('{"k": [' + ",".join(["1"] * 20_000) + "]}").encode()
    assert _timed(flat, "f.json") < 3


def test_a_crafted_unterminated_string_does_not_stall_the_depth_check():
    import time

    data = b'{"a":"' + b'\\"' * 200_000
    start = time.perf_counter()
    with pytest.raises(ReaderRefused) as e:
        read_bytes(data, "evil.json", Config())
    assert time.perf_counter() - start < 1
    assert e.value.rule == "json.invalid"


def test_a_huge_json_integer_is_refused_cleanly():
    with pytest.raises(ReaderRefused) as e:
        read_bytes(b'{"a": ' + b"9" * 5000 + b"}", "big.json", Config())
    assert e.value.rule == "json.invalid"


def test_a_truncated_utf16_csv_is_refused_cleanly():
    data = "a,b\n1,2\n".encode("utf-16") + b"\x00"
    with pytest.raises(ReaderRefused) as e:
        read_bytes(data, "odd.csv", Config())
    assert e.value.rule == "csv.invalid"


def test_a_missing_format_library_says_how_to_install_it(monkeypatch):
    import importlib
    import io
    import zipfile

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as z:
        z.writestr("[Content_Types].xml", "<x/>")
        z.writestr("xl/workbook.xml", "<x/>")

    def missing(name, *args):
        raise ModuleNotFoundError("No module named 'openpyxl'", name="openpyxl")

    monkeypatch.setattr(importlib, "import_module", missing)
    with pytest.raises(readers.ReaderUnavailable) as e:
        read_bytes(buffer.getvalue(), "a.xlsx", Config())
    assert "pip install 'ragnaroq[formats]'" in str(e.value)


def test_format_reader_limits_do_not_change_the_pdf_reading_key():
    from dataclasses import replace

    assert Config().reading_sha256() == replace(
        Config(), max_cells=5, max_depth=3, max_unzipped_bytes=1, max_zip_entries=1
    ).reading_sha256()


def test_json_depth_scan_handles_escapes_and_brackets_in_strings():
    data = br'{"a": "x\\", "b": "[[[\"[", "c": [1]}'
    doc = read_bytes(data, "esc.json", Config(max_depth=2)).document
    assert [line.text for line in doc.lines()] == ["a: x\\", 'b: [[["[', "c", "1"]
