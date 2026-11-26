import json
import uuid
from dataclasses import replace

from conftest import BAD_DATE, BODY

from ragnaroq import Config, Policy, convert
from ragnaroq.anchors import DocxAnchor, PdfAnchor, XlsxAnchor
from ragnaroq.export.chunks import METADATA_KEYS, NAMESPACE, chunk_anchors, to_chunks
from ragnaroq.model import Cell, Document, Engine, Figure, Line, Page, Source, Table
from ragnaroq.policy import decide

TRUSTING = Config(policy=Policy(accept_native_without_crosscheck=True))
EXPECTED_KEYS = (
    "schema_version file_id path file_type file_sha256 kind status rules sources cite part part_index "
    "page_first page_last heading title chars anchors_json image_path"
).split()


def hand_built(fmt, filename, pages, sha="0"):
    return Document(
        source=Source(sha256=sha, bytes=1, filename=filename, pages=len(pages), format=fmt),
        engine=Engine(name="t", version="0", policy_version="0.6", config_sha256="0", components={}),
        created_at="2026-01-01T00:00:00Z",
        pages=pages,
    )


def docx_line(i, text, paragraph, page=1):
    return Line(id=f"l{i}", page=page, order=i, raw=text, text=text, source="docx", paragraph=i,
                anchor=DocxAnchor(paragraph=paragraph))


def assert_flat(chunk):
    uuid.UUID(chunk["id"])
    assert set(chunk) == {"id", "text", "embedding_text", "metadata", "anchors"}
    assert list(chunk["metadata"]) == EXPECTED_KEYS == list(METADATA_KEYS)
    assert all(isinstance(v, (str, int, float, bool)) for v in chunk["metadata"].values())
    assert chunk["metadata"]["cite"]


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
    chunks = to_chunks(document, replace(TRUSTING, chunk_chars=100))
    body = next(c for c in chunks if c["text"] == f"{BODY[0]} {BODY[1]}")
    meta = body["metadata"]
    assert meta["cite"] == "native.pdf, p. 1" and meta["part"] == "p. 1" and meta["file_type"] == "pdf"
    assert meta["page_first"] == meta["page_last"] == 1
    (anchor,) = body["anchors"]
    assert anchor["page"] == 1 and len(anchor["box"]) == 4 and anchor["line_ids"]
    spanning = to_chunks(document, TRUSTING)[0]
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
        Line(id=f"c{i}", page=1, order=i, raw=r, text=r, source="xlsx", table_id="t1", role="table",
             anchor=XlsxAnchor(sheet="Rates", cell=r))
        for i, r in enumerate(refs)
    ]
    cells = [Cell(row=i // 4, column=i % 4, text=r) for i, r in enumerate(refs)]
    page = Page(number=1, kind="sheet", name="Rates", lines=lines,
                tables=[Table(id="t1", page=1, rows=12, columns=4, cells=cells)])
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
        Page(number=n, kind="body", name=f"Part {n}",
             lines=[docx_line(n * 10 + i, f"Text {n}.{i}", i + 1, page=n) for i in range(2)])
        for n in (1, 2)
    ]
    document = hand_built("docx", "guide.docx", pages)
    assert all(line.box is None for line in document.lines())
    decide(document, Policy())
    chunks = to_chunks(document, replace(Config(), chunk_chars=20))
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
    fig = Figure(id="f1", page=3, anchor=anchor, path="images/p-3.png",
                 description="Bar chart of collections",
                 model_description="Collections rose each year.", status="accepted")
    document = hand_built("pdf", "report.pdf", [Page(number=3, kind="native", lines=[], figures=[fig])])
    (chunk,) = to_chunks(document, Config())
    assert_flat(chunk)
    meta = chunk["metadata"]
    assert meta["kind"] == "image" and meta["image_path"] == "images/p-3.png"
    assert meta["cite"] == "report.pdf, p. 3" and meta["page_first"] == meta["page_last"] == 3
    assert chunk["text"] == "Bar chart of collections\n(model) Collections rose each year."
    assert chunk["embedding_text"].endswith("(model) Collections rose each year.")
    plain = fig.model_copy(update={"id": "f2", "model_description": None})
    document.pages[0].figures = [plain]
    assert to_chunks(document, Config())[0]["embedding_text"].endswith("Bar chart of collections")


def test_anchors_json_is_cut_with_a_flag_when_too_long():
    pages = [
        Page(number=n, kind="native", lines=[Line(
            id=f"l{n}", page=n, order=0, raw="x", text="x", source="native", status="accepted",
            box={"x0": 1, "top": 1, "x1": 50, "bottom": 9})])
        for n in range(1, 10_001)
    ]
    document = hand_built("pdf", "big.pdf", pages)
    (chunk,) = to_chunks(document, replace(Config(), chunk_chars=10**9))
    assert len(chunk["anchors"]) == 10_000
    meta = chunk["metadata"]
    kept = json.loads(meta["anchors_json"])
    assert 0 < len(meta["anchors_json"]) <= 20_000 and kept == chunk["anchors"][: len(kept)]
    assert "chunk.anchors-truncated" in meta["rules"].split(",")
