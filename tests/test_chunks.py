import json
import uuid
from dataclasses import replace

from conftest import BAD_DATE, BODY

from ragnaroq import Config, Policy, convert
from ragnaroq.anchors import DocxAnchor, PdfAnchor, XlsxAnchor
from ragnaroq.export.chunks import LIST_KEYS, METADATA_KEYS, NAMESPACE, chunk_anchors, to_chunks, to_jsonl
from ragnaroq.model import Cell, Document, Engine, Figure, Line, Page, Source, Table
from ragnaroq.policy import decide
from ragnaroq.semantic.describe import describe
from ragnaroq.semantic.overrides import Override
from ragnaroq.semantic.rules import choose

TRUSTING = Config(policy=Policy(accept_native_without_crosscheck=True))
EXPECTED_KEYS = (
    "schema_version file_id path file_type file_sha256 kind status rules sources cite part part_index "
    "page_first page_last heading title chars anchors_json image_path "
    "strategy parent_id parent_cite heading_path dates date_first date_last periods references terms "
    "scripts part_of"
).split()


def sentences_plan(document, config=None, max_chars=None, min_chars=None):
    """The plan that cuts `document` with `sentences`, whatever the rules would choose for it."""
    override = Override("*", "sentences", max_chars, min_chars)
    return choose(describe(document), config or Config(), override)


def hand_built(fmt, filename, pages, sha="0"):
    return Document(
        source=Source(sha256=sha, bytes=1, filename=filename, pages=len(pages), format=fmt),
        engine=Engine(name="t", version="0", policy_version="0.6", config_sha256="0", components={}),
        created_at="2026-01-01T00:00:00Z",
        pages=pages,
    )


def docx_line(i, text, paragraph, page=1):
    return Line(
        id=f"l{i}",
        page=page,
        order=i,
        raw=text,
        text=text,
        source="docx",
        paragraph=i,
        anchor=DocxAnchor(paragraph=paragraph),
    )


def assert_flat(chunk):
    uuid.UUID(chunk["id"])
    assert set(chunk) == {"id", "text", "embedding_text", "metadata", "anchors"}
    assert list(chunk["metadata"]) == EXPECTED_KEYS == list(METADATA_KEYS)
    for key, value in chunk["metadata"].items():
        if key in LIST_KEYS:  # a non-empty list of strings, [""] when there is none
            assert isinstance(value, list) and value and len(value) <= 50, (key, value)
            assert all(isinstance(v, str) for v in value), (key, value)
        else:
            assert isinstance(value, (str, int, float, bool)), (key, value)
    assert chunk["metadata"]["cite"]


def test_the_namespace_is_pinned():
    assert str(NAMESPACE) == "bbc5afe7-b0fc-54ab-9334-33219fb1b9c5"


def test_identical_files_at_two_paths_get_different_chunk_ids(native_pdf):
    document = convert(native_pdf, config=TRUSTING, engines=[])
    a = to_chunks(document, Config(), path="a/x.pdf")
    b = to_chunks(document, Config(), path="b/x.pdf")
    assert a and not {c["id"] for c in a} & {c["id"] for c in b}
    assert [c["id"] for c in a] == [c["id"] for c in to_chunks(document, Config(), path="a/x.pdf")]


def test_every_chunk_has_a_uuid_id_and_the_same_flat_metadata(native_pdf):
    chunks = to_chunks(convert(native_pdf, config=TRUSTING, engines=[]), Config())
    assert chunks
    for chunk in chunks:
        assert_flat(chunk)
        assert json.loads(chunk["metadata"]["anchors_json"]) == chunk["anchors"]
    assert len({chunk["id"] for chunk in chunks}) == len(chunks)
    assert {chunk["metadata"]["file_id"] for chunk in chunks} == {str(uuid.uuid5(NAMESPACE, "native.pdf"))}


def test_ids_are_stable_for_the_same_content_and_change_with_it(native_pdf):
    first = to_chunks(convert(native_pdf, config=TRUSTING, engines=[]), Config())
    again = to_chunks(convert(native_pdf, config=TRUSTING, engines=[]), Config())
    assert [c["id"] for c in first] == [c["id"] for c in again]
    other = convert(native_pdf, config=TRUSTING, engines=[])
    other.source.sha256 = "f" * 64
    assert not {c["id"] for c in to_chunks(other, Config())} & {c["id"] for c in first}
    moved = to_chunks(convert(native_pdf, config=TRUSTING, engines=[]), Config(), path="a/native.pdf")
    assert moved[0]["metadata"]["file_id"] != first[0]["metadata"]["file_id"]
    assert moved[0]["metadata"]["cite"] == "a/native.pdf, pp. 1–2"


def test_a_pdf_chunk_cites_its_pages(native_pdf):
    document = convert(native_pdf, config=TRUSTING, engines=[])
    small = replace(TRUSTING, chunk_chars=100)
    chunks = to_chunks(document, small, choice=sentences_plan(document, small))
    # 0.3 kept the 153-character paragraph whole; 0.4 splits it between its two sentences
    assert [c["metadata"]["part_of"] for c in chunks if c["text"] in BODY] == ["1/2", "2/2"]
    body = next(c for c in chunks if c["text"] == BODY[0])
    meta = body["metadata"]
    assert meta["cite"] == "native.pdf, p. 1" and meta["part"] == "p. 1" and meta["file_type"] == "pdf"
    assert meta["page_first"] == meta["page_last"] == 1
    (anchor,) = body["anchors"]
    assert anchor["page"] == 1 and len(anchor["box"]) == 4 and anchor["line_ids"]
    (whole,) = to_chunks(document, TRUSTING)  # the rules cut a short document as one chunk, table and all
    assert whole["metadata"]["strategy"] == "single" and "5,00,000" in whole["text"]
    assert "table.member" in whole["metadata"]["rules"]
    spanning = to_chunks(document, TRUSTING, choice=sentences_plan(document, TRUSTING))[0]
    assert spanning["metadata"]["cite"] == "native.pdf, pp. 1–2"
    assert [a["page"] for a in spanning["anchors"]] == [1, 2]
    assert (spanning["metadata"]["page_first"], spanning["metadata"]["page_last"]) == (1, 2)
    assert spanning["metadata"]["status"] == "flagged"
    assert spanning["metadata"]["rules"] == "date.ordinal-suffix"
    assert BAD_DATE in spanning["text"]
    assert spanning["embedding_text"].startswith("Notification No. 1/2026\n")
    table = next(c for c in chunks if c["metadata"]["kind"] == "table")
    assert table["metadata"]["page_first"] == 2 and "5,00,000" in table["text"]
    accepted = to_chunks(document, TRUSTING, include=("accepted",))
    assert {c["metadata"]["status"] for c in accepted} == {"accepted"}


def test_an_xlsx_table_chunk_cites_its_cell_range():
    refs = [f"{col}{row}" for row in range(1, 13) for col in "ABCD"]
    lines = [
        Line(
            id=f"c{i}",
            page=1,
            order=i,
            raw=r,
            text=r,
            source="xlsx",
            table_id="t1",
            role="table",
            anchor=XlsxAnchor(sheet="Rates", cell=r),
        )
        for i, r in enumerate(refs)
    ]
    cells = [Cell(row=i // 4, column=i % 4, text=r) for i, r in enumerate(refs)]
    page = Page(
        number=1,
        kind="sheet",
        name="Rates",
        lines=lines,
        tables=[Table(id="t1", page=1, rows=12, columns=4, cells=cells)],
    )
    document = hand_built("xlsx", "rates.xlsx", [page])
    decide(document, Policy())
    (chunk,) = to_chunks(document, Config())
    assert_flat(chunk)
    assert chunk["metadata"]["cite"] == "rates.xlsx, Rates!A1:D12"
    assert chunk["metadata"]["part"] == "Rates" and chunk["metadata"]["page_first"] == 0
    assert chunk["anchors"] == [{"type": "xlsx", "sheet": "Rates", "cell": "A1:D12"}]


def test_other_chunks_cite_their_first_and_last_anchor():
    lines = [docx_line(i, f"Paragraph {i}.", 18 + i) for i in range(7)]
    document = hand_built("docx", "guide.docx", [Page(number=1, kind="body", lines=lines)])
    decide(document, Policy())
    (chunk,) = to_chunks(document, Config())
    assert chunk["metadata"]["cite"] == "guide.docx, ¶18–¶24"
    assert chunk["anchors"] == [
        {"from": {"type": "docx", "paragraph": 18}, "to": {"type": "docx", "paragraph": 24}}
    ]


def test_to_chunks_works_on_an_exact_document_without_boxes():
    pages = [
        Page(
            number=n,
            kind="body",
            name=f"Part {n}",
            lines=[docx_line(n * 10 + i, f"Text {n}.{i}", i + 1, page=n) for i in range(2)],
        )
        for n in (1, 2)
    ]
    document = hand_built("docx", "guide.docx", pages)
    assert all(line.box is None for line in document.lines())
    decide(document, Policy())
    small = replace(Config(), chunk_chars=20)
    chunks = to_chunks(document, small, choice=sentences_plan(document, small))
    assert len(chunks) > 1
    for chunk in chunks:
        assert_flat(chunk)
        assert chunk["metadata"]["page_first"] == 0 and chunk["metadata"]["file_type"] == "docx"
    assert chunks[0]["metadata"]["part"] == "Part 1"
    assert chunk_anchors(pages[0].lines) == [
        {"from": {"type": "docx", "paragraph": 1}, "to": {"type": "docx", "paragraph": 2}}
    ]


def test_a_figure_is_its_own_image_chunk():
    anchor = PdfAnchor(page=3, box=(10, 20, 200, 300))
    fig = Figure(
        id="f1",
        page=3,
        anchor=anchor,
        path="images/p-3.png",
        description="Bar chart of collections",
        model_description="Collections rose each year.",
        status="accepted",
    )
    document = hand_built("pdf", "report.pdf", [Page(number=3, kind="native", lines=[], figures=[fig])])
    (chunk,) = to_chunks(document, Config())
    assert_flat(chunk)
    meta = chunk["metadata"]
    assert meta["kind"] == "image" and meta["image_path"] == "images/p-3.png"
    assert meta["cite"] == "report.pdf, p. 3" and meta["page_first"] == meta["page_last"] == 3
    assert chunk["text"] == "Bar chart of collections"  # the label is for the embedding only
    assert chunk["embedding_text"].endswith("Bar chart of collections\n(model) Collections rose each year.")
    plain = fig.model_copy(update={"id": "f2", "model_description": None})
    document.pages[0].figures = [plain]
    assert to_chunks(document, Config())[0]["embedding_text"].endswith("Bar chart of collections")


def test_anchors_json_is_cut_with_a_flag_when_too_long():
    pages = [
        Page(
            number=n,
            kind="native",
            lines=[
                Line(
                    id=f"l{n}",
                    page=n,
                    order=0,
                    raw="x",
                    text="x",
                    source="native",
                    status="accepted",
                    box={"x0": 1, "top": 1, "x1": 50, "bottom": 9},
                )
            ],
        )
        for n in range(1, 10_001)
    ]
    document = hand_built("pdf", "big.pdf", pages)
    (chunk,) = to_chunks(document, replace(Config(), chunk_chars=10**9))
    assert len(chunk["anchors"]) == 10_000
    meta = chunk["metadata"]
    kept = json.loads(meta["anchors_json"])
    assert 0 < len(meta["anchors_json"]) <= 20_000 and kept == chunk["anchors"][: len(kept)]
    assert "chunk.anchors-truncated" in meta["rules"].split(",")


def test_anchors_json_is_cut_at_20000_bytes_not_characters():
    pages = [
        Page(
            number=n,
            kind="sheet",
            name="दरें",
            lines=[
                Line(
                    id=f"l{n}",
                    page=n,
                    order=0,
                    raw="x",
                    text="x",
                    source="xlsx",
                    status="accepted",
                    anchor=XlsxAnchor(sheet="दरें", cell="A1"),
                )
            ],
        )
        for n in range(1, 1001)
    ]
    document = hand_built("xlsx", "rates.xlsx", pages)
    (chunk,) = to_chunks(document, replace(Config(), chunk_chars=10**9))
    meta = chunk["metadata"]
    assert len(json.dumps(chunk["anchors"], ensure_ascii=False).encode()) > 20_000
    assert len(meta["anchors_json"].encode("utf8")) <= 20_000
    assert len(json.loads(meta["anchors_json"])) > 0
    assert "chunk.anchors-truncated" in meta["rules"].split(",")


# --- a table of a file that is not a PDF is cut into groups of rows (final review C2) --------------

DEMO = __import__("pathlib").Path(__file__).parent.parent / "references" / "demo"


def _header(table) -> str:
    from ragnaroq.export import table_markdown

    return "\n".join(table_markdown(table).split("\n")[:2])


def _rows_shown(chunk) -> int:
    """Data rows in a table chunk's grid: every line after the header and the --- line."""
    return len(chunk["text"].split("\n")) - 2


def assert_table_chunks_fit(document, config):
    chunks = to_chunks(document, config, path=document.source.filename)
    assert len({c["id"] for c in chunks}) == len(chunks)
    headers = [_header(t) for page in document.pages for t in page.tables]
    tables = [c for c in chunks if c["metadata"]["kind"] == "table"]
    for chunk in tables:
        assert any(chunk["text"].startswith(h + "\n") or chunk["text"] == h for h in headers), chunk["text"]
        assert len(chunk["text"]) <= config.chunk_chars or _rows_shown(chunk) <= 1, chunk["text"]
        assert chunk["metadata"]["chars"] == len(chunk["text"])
    return tables


def test_a_2000_row_csv_is_cut_into_chunks_that_fit_each_with_the_header():
    from ragnaroq import read_bytes

    rows = ["Section,Amount,Note"] + [f"80C-{i},{i * 100},row note number {i}" for i in range(2000)]
    document = read_bytes("\n".join(rows).encode(), "big.csv", Config()).document
    tables = assert_table_chunks_fit(document, Config())
    assert len(tables) > 70  # 86,715 characters in one chunk before
    assert all(c["text"].startswith("| Section | Amount | Note |\n| --- | --- | --- |\n") for c in tables)
    assert sum(_rows_shown(c) for c in tables) == 2000  # every row once, the header in each
    assert tables[0]["metadata"]["cite"].startswith("big.csv, row 1, Section–row ")
    assert tables[1]["metadata"]["cite"].startswith(f"big.csv, row {_rows_shown(tables[0]) + 2}, Section–")
    assert max(len(json.dumps(c["metadata"]).encode()) for c in tables) < 40_000


def test_a_2000_row_sheet_cites_each_group_by_its_own_range():
    import io

    import pytest

    openpyxl = pytest.importorskip("openpyxl")
    from ragnaroq import read_bytes

    book = openpyxl.Workbook()
    sheet = book.active
    sheet.title = "Rates"
    sheet.append(["Section", "Amount", "Note"])
    for i in range(2000):
        sheet.append([f"80C-{i}", i * 100, f"row note number {i}"])
    buffer = io.BytesIO()
    book.save(buffer)
    document = read_bytes(buffer.getvalue(), "big.xlsx", Config()).document
    tables = assert_table_chunks_fit(document, Config())
    first, second = tables[0], tables[1]
    shown = _rows_shown(first)
    assert first["metadata"]["cite"] == f"big.xlsx, Rates!A1:C{shown + 1}"
    assert second["metadata"]["cite"] == f"big.xlsx, Rates!A{shown + 2}:C{shown + 1 + _rows_shown(second)}"
    cell = second["metadata"]["cite"].split("!")[1]
    assert second["anchors"] == [{"type": "xlsx", "sheet": "Rates", "cell": cell}]
    assert sum(_rows_shown(c) for c in tables) == 2000


def test_a_row_longer_than_chunk_chars_is_a_chunk_of_its_own():
    from ragnaroq import read_bytes

    long = "x " * 400
    data = f"Name,Note\na,short\nb,{long}\nc,short\n".encode()
    document = read_bytes(data, "wide.csv", Config()).document
    tables = assert_table_chunks_fit(document, replace(Config(), chunk_chars=300))
    assert [_rows_shown(c) for c in tables] == [1, 1, 1]
    assert all(c["text"].startswith("| Name | Note |\n| --- | --- |\n") for c in tables)


def test_a_small_table_is_still_one_chunk():
    from ragnaroq import read_bytes

    document = read_bytes(b"Section,Amount\n80C,150000\n80D,25000\n", "r.csv", Config()).document
    (chunk,) = to_chunks(document, Config())
    assert chunk["text"] == "| Section | Amount |\n| --- | --- |\n| 80C | 150000 |\n| 80D | 25000 |"


def test_a_sheet_with_a_value_in_the_far_corner_is_cut_in_time_proportional_to_its_cells():
    """Final review N1: a 5 KB workbook with a long A1 and one value in XFD1048576 spans
    1,048,576 rows by 16,384 columns. Cutting it walked every row of that span and summed a
    16,384-column width list for each, about two minutes in the main process with no limit."""
    import io
    import time

    import pytest

    openpyxl = pytest.importorskip("openpyxl")
    from ragnaroq import read_bytes

    book = openpyxl.Workbook()
    sheet = book.active
    sheet["A1"] = "word " * 300  # longer than chunk_chars, so the table is cut into groups
    sheet["XFD1048576"] = "corner"
    buffer = io.BytesIO()
    book.save(buffer)
    document = read_bytes(buffer.getvalue(), "corner.xlsx", Config()).document
    table = document.pages[0].tables[0]
    assert (table.rows, table.columns) == (1_048_576, 16_384)
    started = time.perf_counter()
    (chunk,) = to_chunks(document, Config())
    assert time.perf_counter() - started < 2  # 126 seconds before
    assert chunk["metadata"]["cite"] == "corner.xlsx, Sheet!A1:XFD1048576"
    assert chunk["text"].endswith("| --- | --- |\n|  | corner |")  # the rows and columns that hold a cell


def test_a_stray_cell_far_to_the_right_does_not_widen_every_chunk():
    """One note in XFD1 above a column of 2,000 values made each row a chunk of its own, with
    16,384 columns of empty cells: 375 MB of chunk text. Columns that hold no cell are left out."""
    import io

    import pytest

    openpyxl = pytest.importorskip("openpyxl")
    from ragnaroq import read_bytes

    book = openpyxl.Workbook()
    sheet = book.active
    sheet["A1"], sheet["XFD1"] = "Section", "stray note"
    for row in range(2, 2_002):
        sheet[f"A{row}"] = f"80C-{row}"
    buffer = io.BytesIO()
    book.save(buffer)
    document = read_bytes(buffer.getvalue(), "stray.xlsx", Config()).document
    tables = assert_table_chunks_fit(document, Config())
    assert len(tables) < 60  # 2,000 before, one a row
    assert all(c["text"].startswith("| Section | stray note |\n| --- | --- |\n") for c in tables)
    assert sum(_rows_shown(c) for c in tables) == 2_000
    assert tables[1]["metadata"]["cite"].startswith(f"stray.xlsx, Sheet!A{_rows_shown(tables[0]) + 2}:A")


def test_an_empty_row_or_column_inside_a_sheet_is_left_out_of_its_markdown():
    from ragnaroq.export import table_markdown

    cells = [Cell(row=0, column=0, text="Section"), Cell(row=0, column=2, text="Amount")]
    cells += [Cell(row=3, column=0, text="80C"), Cell(row=3, column=2, text="150000")]
    table = Table(id="t", page=1, rows=4, columns=3, cells=cells)
    assert table_markdown(table) == "| Section | Amount |\n| --- | --- |\n| 80C | 150000 |"


def test_a_table_with_no_cells_keeps_its_declared_shape():
    from ragnaroq.export import table_markdown

    assert (
        table_markdown(Table(id="t", page=1, rows=2, columns=2, cells=[]))
        == "|  |  |\n| --- | --- |\n|  |  |"
    )


def test_every_demo_table_chunk_fits_or_holds_one_row_and_starts_with_its_header():
    from ragnaroq import read_bytes
    from ragnaroq.readers import ReaderRefused

    for path in sorted(DEMO.iterdir()):
        if path.name.endswith(".expected.jsonl") or path.suffix == ".png":
            continue
        try:
            document = read_bytes(path.read_bytes(), path.name, Config(), base=DEMO).document
        except ReaderRefused:
            continue  # bomb.xml
        for size in (1200, 120, 40):
            assert_table_chunks_fit(document, replace(Config(), chunk_chars=size))


def test_each_group_of_rows_is_decided_alone_and_shows_its_header_status():
    from ragnaroq import read_bytes

    rows = ["Section,Amount"] + [f"80C-{i},{i}" for i in range(500)]
    document = read_bytes("\n".join(rows).encode(), "big.csv", Config()).document
    lines = document.pages[0].lines
    before = to_chunks(document, Config())
    lines[0].status = "flagged"  # the header cell "Section"
    blocked = next(line for line in lines if line.text == "80C-250")
    blocked.status = "blocked"
    after = to_chunks(document, Config())
    assert len(after) == len(before) - 1
    assert [c["id"] for c in after] == [c["id"] for c in before if "| 80C-250 |" not in c["text"]]
    assert {c["metadata"]["status"] for c in after} == {"flagged"}  # each shows the flagged header


def test_a_remote_figure_chunk_has_no_image_path():
    """Final review m1: `image_path` names a saved file, never a URL nothing fetched."""
    from ragnaroq.anchors import HtmlAnchor

    remote = Figure(
        id="f1",
        page=1,
        anchor=HtmlAnchor(path="/html/body/img"),
        path="https://example.org/x.png",
        description="Collections by year",
        description_source="alt",
        status="accepted",
    )
    saved = remote.model_copy(update={"id": "f2", "path": "images/html-body-img.png"})
    page = Page(number=1, kind="body", lines=[], figures=[remote, saved])
    chunks = to_chunks(hand_built("html", "notice.html", [page]), Config())
    assert [c["metadata"]["image_path"] for c in chunks] == ["", "images/html-body-img.png"]


# --- chunk format 0.4.0 -------------------------------------------------------------------------------

GENERATED = DEMO.parent / "chunking" / "generated"


def read_file(folder, name):
    from ragnaroq import read_bytes

    return read_bytes((folder / name).read_bytes(), name, Config(), engines=[], base=folder).document


def all_chunks(folder):
    from ragnaroq.readers import ReaderRefused

    found = []
    for path in sorted(folder.iterdir()):
        if path.name.endswith(".jsonl") or path.suffix in (".png", ".toml"):
            continue
        try:
            found += to_chunks(read_file(folder, path.name), Config(), path=path.name)
        except ReaderRefused:
            assert path.name == "bomb.xml"
    return found


def md_chunks(text, **limits):
    from ragnaroq import read_bytes

    document = read_bytes(text.encode(), "notes.md", Config(), engines=[]).document
    return document, to_chunks(document, Config(), choice=sentences_plan(document, **limits))


def test_every_chunk_has_the_0_4_keys_and_list_keys_are_never_empty():
    chunks = all_chunks(DEMO) + all_chunks(GENERATED)
    assert len(chunks) > 50
    for chunk in chunks:
        assert list(chunk["metadata"]) == list(METADATA_KEYS)
        for key in LIST_KEYS:
            values = chunk["metadata"][key]
            assert isinstance(values, list) and values and all(isinstance(v, str) for v in values)
            assert len(values) <= 50
        assert all(isinstance(v, (str, int, float, bool, list)) for v in chunk["metadata"].values())
        assert_flat(chunk)
        assert json.loads(chunk["metadata"]["anchors_json"]) == chunk["anchors"]
    assert LIST_KEYS == ("dates", "periods", "references", "terms")
    assert {c["metadata"]["terms"] == [""] for c in chunks} == {True}  # terms come with definitions


def test_two_pieces_of_one_line_have_different_ids_and_their_char_ranges():
    sentence = "A stall holder must show the certificate to the market officer on request. "
    document, chunks = md_chunks("Short.\n\n" + sentence * 6, max_chars=200, min_chars=0)
    line = document.pages[0].lines[1]
    pieces = chunks[1:]
    assert len(pieces) > 2 and len({c["id"] for c in chunks}) == len(chunks)
    for chunk in pieces:
        (anchor,) = chunk["anchors"]
        start, end = anchor["from"]["from_char"], anchor["from"]["to_char"]
        assert anchor["to"] == anchor["from"] and anchor["from"]["lines"] == [3, 3]
        assert line.text[start:end] == chunk["text"]
        what = f"{line.id}:{start}-{end}"
        file_id = chunk["metadata"]["file_id"]
        assert chunk["id"] == str(uuid.uuid5(NAMESPACE, f"{file_id}|{document.source.sha256}|{what}"))
    whole = chunks[0]  # a whole line keeps 0.3's anchor and id
    assert whole["anchors"] == [
        {"from": {"type": "md", "lines": [1, 1]}, "to": {"type": "md", "lines": [1, 1]}}
    ]
    file_id = whole["metadata"]["file_id"]
    assert whole["id"] == str(uuid.uuid5(NAMESPACE, f"{file_id}|{document.source.sha256}|p1-l1"))


def test_a_pdf_piece_names_the_characters_of_each_line_it_holds_in_part():
    texts = [
        "The first sentence of the paragraph is on the first line. The second one",
        "runs on to the second line and ends here. The third sentence is short. The fourth",
        "sentence is all on the third line and ends the paragraph.",
    ]
    lines = [
        Line(
            id=f"l{i}",
            page=1,
            order=i,
            raw=text,
            text=text,
            source="native",
            paragraph=1,
            status="accepted",
            box={"x0": 10, "top": 10 * i, "x1": 400, "bottom": 10 * i + 9},
        )
        for i, text in enumerate(texts)
    ]
    document = hand_built("pdf", "p.pdf", [Page(number=1, kind="native", lines=lines)])
    config = replace(Config(), chunk_chars=200)
    chunks = to_chunks(document, config, choice=sentences_plan(document, config, min_chars=0))
    assert len(chunks) > 1 and len({c["id"] for c in chunks}) == len(chunks)
    by_id = {line.id: line.text for line in lines}
    partial = 0
    for chunk in chunks:
        (page,) = chunk["anchors"]
        assert page["page"] == 1 and page["box"][0] == 10 and page["line_ids"]
        chars = page.get("chars", {})
        assert set(chars) <= set(page["line_ids"])
        shown = []
        for line_id in page["line_ids"]:
            start, end = chars.get(line_id, (0, len(by_id[line_id])))
            shown.append(by_id[line_id][start:end])
        assert " ".join(shown) == chunk["text"]
        partial += len(chars)
    assert partial >= 2


def test_dates_and_bounds_are_filters():
    text = (
        "Issued on 5 January 2026 for FY 2025-26 under section 10(46) and Rule 3. Due by 31 March 2026, "
        "not 05/01/2026, and from 1 April 2025. Again on 5 January 2026 in Q3 2026."
        "\n\n# Other\n\nNothing dated here."
    )
    _, (dated, plain) = md_chunks(text, min_chars=0)
    meta = dated["metadata"]
    assert meta["dates"] == ["2026-01-05", "2026-03-31", "2025-04-01"]  # in order, no repeats, ISO only
    assert (meta["date_first"], meta["date_last"]) == (20250401, 20260331)  # the earliest and the latest
    assert meta["periods"] == ["2025-04-01/2026-03-31", "2026-Q3"]
    assert meta["references"] == ["section 10(46)", "rule 3"]
    meta = plain["metadata"]
    assert [meta[k] for k in ("dates", "periods", "references", "terms")] == [[""]] * 4
    assert (meta["date_first"], meta["date_last"]) == (0, 0)
    many = " ".join(f"On 1 January {year}." for year in range(2000, 2060))
    _, (chunk,) = md_chunks(many)
    assert len(chunk["metadata"]["dates"]) == 50 and chunk["metadata"]["dates"][-1] == "2049-01-01"
    assert (chunk["metadata"]["date_first"], chunk["metadata"]["date_last"]) == (20000101, 20590101)


def test_the_scripts_key_joins_the_scripts_of_the_chunks_lines():
    from ragnaroq import read_bytes

    document = read_bytes("Hello world.\n\nनमस्ते दुनिया".encode(), "s.md", Config(), engines=[]).document
    (chunk,) = to_chunks(document, Config())
    assert chunk["metadata"]["scripts"] == "Deva+Latn"


def test_the_same_document_gives_the_same_chunks_twice():
    for path in sorted(GENERATED.iterdir()):
        first = to_jsonl(to_chunks(read_file(GENERATED, path.name), Config(), path=path.name))
        again = to_jsonl(to_chunks(read_file(GENERATED, path.name), Config(), path=path.name))
        assert first == again, path.name


def test_a_document_with_no_lines_gives_no_chunks():
    from ragnaroq.readers._build import start

    empty = start(b"", "empty.csv", "csv", Config(), {})
    failed = hand_built("pdf", "failed.pdf", [Page(number=n, kind="failed", lines=[]) for n in (1, 2)])
    excluded = hand_built("docx", "x.docx", [Page(number=1, kind="body", lines=[docx_line(0, "नमस्ते", 1)])])
    excluded.pages[0].lines[0].excluded = "script:Deva"
    for document in (empty, failed, excluded):
        assert to_chunks(document, Config()) == []
        assert to_chunks(document, Config(), choice=sentences_plan(document)) == []


def test_to_chunks_takes_the_plan_and_the_metadata_by_keyword():
    import pytest

    from ragnaroq import read_bytes

    document = read_bytes(("Some words here. " * 40).encode(), "w.md", Config(), engines=[]).document
    choice = sentences_plan(document, max_chars=200)
    with pytest.raises(TypeError):
        to_chunks(document, Config(), ("accepted", "flagged"), None, choice)
    meta = describe(document)
    assert [c["metadata"]["strategy"] for c in to_chunks(document, Config(), meta=meta)] == ["single"]
    meta.chunking = choice  # a plan already in the metadata is the one used
    chunks = to_chunks(document, Config(), meta=meta)
    assert len(chunks) > 1 and all(c["metadata"]["chars"] <= 200 for c in chunks)
    assert to_chunks(document, Config(), choice=choice, meta=meta) == chunks
