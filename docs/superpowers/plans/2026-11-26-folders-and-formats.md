# Folders and Formats (0.3) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `ragnaroq index ./folder` turns a folder of PDF, Word, Excel, PowerPoint, HTML, Markdown, JSON, XML and CSV files into anchored, checked text plus chunks any vector database can load, and processes only what changed on the next run.

**Architecture:**
- **Output format.** It grows a typed `anchor` per line (`anchors.py`) and a `Figure` item for images, as schema 0.3.0.
- **Readers.** Each format has a reader in `readers/` that returns `ReadResult(document, images)`. A shared figures stage saves and describes the images.
- **Rules and exports.** The policy accepts exact text without a second reader. Markdown and chunks work from anchors. Chunks get UUID ids and flat metadata.
- **Folder.** `folder.index()` walks the folder, runs each reader inside the isolated worker, and writes `.ragnaroq/` with `manifest.json`, `changes.json` and one combined `chunks.jsonl`.

**Tech Stack:** Python 3.12, pydantic 2, pdfplumber; new: python-docx, openpyxl, python-pptx, markdown-it-py, lxml (extra `ragnaroq[formats]`), defusedxml (core).

**Spec:** [docs/superpowers/specs/2026-11-26-folders-and-formats-design.md](../specs/2026-11-26-folders-and-formats-design.md). One finding came after approval: Docling reports no source position for Word content (`prov` is empty), so it is not used. Every reader uses its library directly, as the spec allows: "Where it does not, the reader goes to the library underneath instead."

## Global Constraints

**Versions and checks**
- Schema `"0.3.0"`, policy `"0.6"`, package `0.3.0`.
- `ruff check .` and `python -m pytest -q` must pass. Lines are at most 110 characters.
- CI installs `.[test]`, which now includes the format libraries. torch and the model are never installed in CI.

**Dependencies**
- Core gains `defusedxml>=0.7,<1`.
- New extra `formats`: `python-docx>=1.1,<2`, `openpyxl>=3.1,<4`, `python-pptx>=1.0,<2`, `markdown-it-py>=3,<5`, `lxml>=5,<7`. The `test` extra adds the same libraries.
- The core never imports torch, transformers, huggingface_hub or any `formats` library at module level. Each reader imports its library inside its read function. A missing library raises `ReaderUnavailable("pip install 'ragnaroq[formats]'")`.

**PDF behaviour**
- Unchanged. Every existing test passes as it is, except those about the chunk shape (Task 3) and the schema version (Task 1).
- PDF evaluation numbers do not move.

**Network**
- Nothing reaches the network. Remote images (http, https, protocol-relative) are never fetched.

**Ids and chunks**
- UUID namespace: `NAMESPACE = uuid.uuid5(uuid.NAMESPACE_URL, "https://github.com/pTidke/ragnaroq")`.
- A file id is `str(uuid5(NAMESPACE, <path relative to the folder, POSIX>))`.
- A chunk id is `str(uuid5(NAMESPACE, <file sha256> + "|" + ",".join(<line ids>)))`.
- Chunk metadata uses one fixed set of keys on every chunk. Values are `str`, `int`, `float` or `bool` only: no `None`, no lists, no dicts. A missing value is `""` or `0`.

**Exact text and images**
- These sources are exact, never read from a picture: `EXACT_SOURCES = {"docx", "xlsx", "pptx", "html", "md", "json", "xml", "csv"}`.
- Images smaller than 32×32 pixels are not saved, only counted.

**Commits**
- Every commit message ends with:
  ```
  Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
  Claude-Session: https://claude.ai/code/session_01AfdfCKQJ1Kc3LDgTC4Jj5p
  ```
- Push only to `claude/trusting-hypatia-l5qg4h`. Open no pull request unless the owner asks.

## Review Focus

1. **Huge or deep JSON.** A JSON file nested 10,000 levels deep must fail alone with `json.too-deep`, not crash the run. Task 5: `test_deeply_nested_json_fails_alone`.
2. **CSV encodings.** A CSV in UTF-16 with a BOM, or in cp1252 with "₹" or "é", must decode to the right text, with `encoding.guessed` only for cp1252. Task 5: `test_csv_encodings`.
3. **A renamed or moved file.** Its old chunk ids must appear as removed in `changes.json` and its new ones as added, so a vector database never keeps stale chunks. Task 11: `test_a_renamed_file_removes_old_chunks_and_adds_new`.
4. **A corrupt or hostile file in the folder.** A truncated .docx or a zip bomb named .xlsx fails alone, with its reason in the manifest. Every other file is still indexed. Task 11: `test_one_bad_file_fails_alone`.
5. **A very large sheet.** A sheet above the cell limit stops reading at the limit and says so (`xlsx.cell-limit`), and never exhausts memory. Task 6: `test_a_sheet_over_the_cell_limit_is_cut_and_flagged`.

---

### Task 1: Output format 0.3.0: anchors, figures, cell values

**Files:**
- Create: `src/ragnaroq/anchors.py`
- Modify: `src/ragnaroq/model.py`
- Regenerate: `schemas/document.schema.json`
- Test: `tests/test_anchors.py`, `tests/test_convert.py`

**Interfaces:**
- Produces, in `anchors.py`:
  - **Anchor classes**, each with a literal `type`:
    - `PdfAnchor(page: int, box: tuple[float, float, float, float])`
    - `XlsxAnchor(sheet: str, cell: str)`, where `cell` is `"C7"` or `"B2:D2"`
    - `PptxAnchor(slide: int, shape: str, paragraph: int = 0, notes: bool = False)`
    - `DocxAnchor(paragraph: int | None = None, table: int | None = None, cell: tuple[int, int] | None = None, note: str | None = None)`, all 1-based
    - `HtmlAnchor(path: str)`
    - `MdAnchor(lines: tuple[int, int])`, 1-based and inclusive
    - `JsonAnchor(pointer: str)`
    - `XmlAnchor(path: str)`
    - `CsvAnchor(row: int, column: str)`, where `row` is 1-based and counts the header row
  - `Anchor = Annotated[PdfAnchor | … | CsvAnchor, Field(discriminator="type")]`
  - `cite(anchor: Anchor) -> str`
  - `key(anchor: Anchor) -> str`: canonical JSON with sorted keys, used to match anchors.
- Produces, in `model.py`:
  - **Line:**
    - `Line.anchor: Anchor | None = None`. A model validator fills `PdfAnchor(page, box)` when `anchor` is missing and `box` is present, so 0.1 and 0.2 documents load with anchors.
    - `Line.box: Box | None = None`
    - `Line.value: CellValue | None = None`, where `CellValue(stored: str | float | int | bool | None, formula: str | None = None, format: str | None = None)`
  - **Page:**
    - `Page.kind` adds `"sheet"`, `"slide"` and `"body"`.
    - `Page.name: str | None = None`
    - `Page.width` and `Page.height` become `float | None = None`.
    - `Page.figures: list[Figure] = []`
  - **Figure:** `Figure(id: str, page: int, anchor: Anchor, path: str = "", width: int = 0, height: int = 0, description: str = "", description_source: Literal["caption", "alt", "ocr", "none"] = "none", model_description: str | None = None, ocr_lines: list[Line] = [], status: Status = "flagged", flags: list[Flag] = [])`
  - **Table and Cell:** `Table.box: Box | None = None` and `Cell.anchor: Anchor | None = None`.
  - **Source:** `Source.format: str = "pdf"`.
  - **Schema version:** `SCHEMA_VERSION = "0.3.0"`. `Document.schema_version` accepts `"0.1.0"`, `"0.2.0"` and `"0.3.0"` and restamps to `"0.3.0"`.

- [ ] **Step 1: Write the failing tests** in `tests/test_anchors.py`

```python
@pytest.mark.parametrize("anchor, text", [
    (PdfAnchor(page=3, box=(72, 140, 520, 152)), "p. 3"),
    (XlsxAnchor(sheet="Rates", cell="C7"), "Rates!C7"),
    (PptxAnchor(slide=4, shape="Title 1"), "slide 4, Title 1"),
    (PptxAnchor(slide=4, shape="Notes", notes=True), "slide 4 notes"),
    (DocxAnchor(paragraph=18), "¶18"),
    (DocxAnchor(table=2, cell=(3, 1)), "table 2, row 3, column 1"),
    (DocxAnchor(note="footnote 1"), "footnote 1"),
    (HtmlAnchor(path="/html/body/main/p[2]"), "/html/body/main/p[2]"),
    (MdAnchor(lines=(40, 42)), "lines 40–42"),
    (JsonAnchor(pointer="/deductions/80C/limit"), "/deductions/80C/limit"),
    (XmlAnchor(path="/notice/section[2]/para[1]"), "/notice/section[2]/para[1]"),
    (CsvAnchor(row=12, column="Amount"), "row 12, Amount"),
])
def test_every_anchor_has_a_short_citation(anchor, text):
    assert cite(anchor) == text

def test_an_anchor_round_trips_through_json():
    line = Line(id="l1", page=1, order=0, raw="5%", text="5%", source="xlsx",
                anchor=XlsxAnchor(sheet="Rates", cell="C7"), value=CellValue(stored=0.05, format="0%"))
    again = Line.model_validate_json(line.model_dump_json())
    assert again.anchor == XlsxAnchor(sheet="Rates", cell="C7") and again.value.stored == 0.05
```

```python
# tests/test_convert.py
def test_a_0_2_0_document_loads_with_pdf_anchors(native_pdf):
    # Dump a converted document, set schema_version to "0.2.0", strip every "anchor" key, then load it:
    # the version is "0.3.0", and every line has PdfAnchor(page=line.page, box=<its box>).
```

- [ ] **Step 2: Run them to make sure they fail**

Run: `python -m pytest tests/test_anchors.py tests/test_convert.py -q`
Expected: FAIL on the import of `ragnaroq.anchors`.

- [ ] **Step 3: Implement**
  - **Both files:** `anchors.py` and the `model.py` changes as listed under Interfaces.
  - **`cite` formats:**
    - PDF: `"p. {page}"`;
    - Word: `"¶{paragraph}"` for a paragraph, `"table {t}, row {r}, column {c}"` for a cell, and the note's own text for a note;
    - Markdown: `"lines {a}–{b}"`, with an en dash.
  - **PDF lines:** `pipeline.py` and `native.py` set `anchor=PdfAnchor(...)` on the lines they create. Box values are rounded to 2 decimals.
  - **Schema file:** regenerate `schemas/document.schema.json`.

- [ ] **Step 4: Run the whole suite**

Run: `python -m pytest -q && ruff check .`
Expected: everything passes. Only the schema-version expectations in existing tests change.

- [ ] **Step 5: Commit** with the message `Output format 0.3.0: an anchor for every line, figures, cell values`.

---

### Task 2: Exact text is accepted by rule, and Markdown shows parts and figures (policy 0.6)

**Files:**
- Modify: `src/ragnaroq/policy.py`, `src/ragnaroq/config.py` (`Policy.version = "0.6"`), `src/ragnaroq/export/__init__.py`, `src/ragnaroq/export/markdown.py`
- Test: `tests/test_exact.py`

**Interfaces:**
- Consumes: Task 1's model.
- Produces:
  - `policy.EXACT_SOURCES`
  - `policy.decide` handling exact lines and figures
  - `export.units()` yielding `Unit(kind="image", page, lines=[], figure=Figure)` for figures, in order with the lines around them
  - `Unit.figure: Figure | None = None`

- [ ] **Step 1: Write the failing tests** in `tests/test_exact.py`. Build documents by hand with a helper `exact_document(source, texts, kind="body")`.

```python
def test_exact_text_is_accepted_without_a_second_reader():
    doc = decided(exact_document("docx", ["Rates for TY 2026-27", "The limit is ₹1,50,000."]))
    assert {l.status for l in doc.lines()} == {"accepted"}

def test_exact_text_still_goes_through_the_validators():
    doc = decided(exact_document("xlsx", ["New Delhi, the 31* March, 2025"]))
    assert "date.ordinal-suffix" in {f.rule for l in doc.lines() for f in l.flags}

def test_the_ocr_misreading_checks_do_not_run_on_exact_text():
    doc = decided(exact_document("csv", ["Cat. No. 11320B", "A | B"]))
    assert {l.status for l in doc.lines()} == {"accepted"}

def test_exact_table_cells_are_accepted():
    # a docx table built with Table and lines carrying table_id: status accepted, no "table.member" flag;
    # Table.status accepted, no "table.structure-unverified"

def test_a_figure_with_alt_text_is_accepted_and_one_with_no_description_is_flagged():
    # Figure(description="Bar chart of collections", description_source="alt") -> accepted;
    # Figure(description="", description_source="none") -> flagged with rule "image.no-description"

def test_an_ocr_description_follows_the_ocr_lines():
    # Figure with description_source="ocr" and two ocr_lines whose readings disagree -> figure flagged

def test_markdown_marks_parts_and_places_figures():
    # an xlsx document with a part named "Rates" and a pptx one with slide 4 holding a figure at
    # path "images/slide-4-picture-2.png", described "Bar chart", cited "slide 4, Picture 2":
    md = to_markdown(doc)
    assert "<!-- sheet Rates -->" in md_xlsx
    assert "![Bar chart](images/slide-4-picture-2.png) *(slide 4, Picture 2)*" in md_pptx
```

- [ ] **Step 2: Run them to make sure they fail.** Run: `python -m pytest tests/test_exact.py -q`. Expected: FAIL.

- [ ] **Step 3: Implement**

**`decide_line`:**
- A line whose source is in `EXACT_SOURCES`:
  - runs `validators.run(text, ocr=False)`;
  - never gets `table.member`, `crosscheck.not-run` or `ocr.single-reading`.
- `table.member` stays only for lines whose source is `native` or `ocr:*`.

**`decide`:**
- A table whose member lines are all exact is not flagged `table.structure-unverified`; its status follows its lines.
- Each figure's `ocr_lines` is decided like scanned lines.
- A figure's status:
  - `accepted` for `caption` or `alt` with no flags;
  - for `ocr`, accepted when every OCR line is accepted, else flagged;
  - for `none`, flagged with the rule `image.no-description`.
- `model_description` never affects the status.

**Markdown:**
- The part marker is `<!-- page N -->` for PDF parts, and `<!-- sheet NAME -->`, `<!-- slide N -->` or `<!-- body NAME -->` for the others.
- A figure becomes `![{description}]({path}) *({cite})*`. For a remote image, `Figure.path` holds its URL (set in Task 9).

- [ ] **Step 4: Run the whole suite.** Run: `python -m pytest -q && ruff check .`. Expected: everything passes; PDF results are unchanged.

- [ ] **Step 5: Commit** with the message `Policy 0.6: exact text is accepted by rule; Markdown shows parts and figures`.

---

### Task 3: Chunks any vector database can load

**Files:**
- Modify: `src/ragnaroq/export/chunks.py`
- Create: `docs/vector-databases.md`
- Test: `tests/test_chunks.py`. Move and update the chunk assertions now in `tests/test_convert.py`.

**Interfaces:**
- Consumes: `anchors.cite`, `anchors.key`, `Unit.figure`.
- Produces:
  - `chunks.NAMESPACE`
  - `chunks.METADATA_KEYS: tuple[str, ...]`
  - `to_chunks(document, config, include=("accepted", "flagged"), *, path: str | None = None) -> list[dict]`
  - `chunk_anchors(lines: list[Line]) -> list[dict]`

- [ ] **Step 1: Write the failing tests** in `tests/test_chunks.py`

```python
METADATA_KEYS = ("schema_version", "file_id", "path", "file_type", "file_sha256", "kind", "status", "rules",
                 "sources", "cite", "part", "part_index", "page_first", "page_last", "heading", "title", "chars",
                 "anchors_json", "image_path")

def test_every_chunk_has_a_uuid_id_and_the_same_flat_metadata(native_pdf):
    chunks = to_chunks(convert(native_pdf, config=TRUSTING, engines=[]), Config())
    for chunk in chunks:
        uuid.UUID(chunk["id"])
        assert set(chunk) == {"id", "text", "embedding_text", "metadata", "anchors"}
        assert tuple(chunk["metadata"]) == METADATA_KEYS
        assert all(isinstance(v, (str, int, float, bool)) and v is not None for v in chunk["metadata"].values())

def test_ids_are_stable_for_the_same_content_and_change_with_it(native_pdf, tmp_path):
    # converting twice gives the same ids; a different file sha256 gives different ids

def test_a_pdf_chunk_cites_its_pages(native_pdf):
    # metadata["cite"] == "native.pdf, p. 1"; page_first == page_last == 1; anchors carry page and box

def test_an_xlsx_table_chunk_cites_its_cell_range():
    # hand-built xlsx document: a table whose cells run A1:D12 on sheet "Rates" ->
    # metadata["cite"] == "rates.xlsx, Rates!A1:D12"; anchors == [{"type": "xlsx", "sheet": "Rates", "cell": "A1:D12"}]

def test_other_chunks_cite_their_first_and_last_anchor():
    # docx paragraphs 18..24 in one chunk -> cite "guide.docx, ¶18–¶24"; anchors [{"from": {...}, "to": {...}}]

def test_a_figure_is_its_own_image_chunk():
    # Figure with description "Bar chart of collections", path "images/p-3.png" ->
    # one chunk with metadata kind "image", image_path "images/p-3.png", embedding_text ending with the description

def test_anchors_json_is_cut_with_a_flag_when_too_long():
    # a chunk with 10,000 anchors: len(metadata["anchors_json"]) <= 20_000 and "chunk.anchors-truncated" in rules
```

- [ ] **Step 2: Run them to make sure they fail.**

- [ ] **Step 3: Implement**
  - **Chunk shape:** `{"id", "text", "embedding_text", "metadata", "anchors"}`.
  - **The `metadata` values:**
    - `path` is the `path` argument, or `document.source.filename`;
    - `file_id` is the UUID of `path`;
    - `file_type` is `document.source.format`;
    - `rules` and `sources` are joined with `,`;
    - `part` is the page name, or `"p. N"` for a PDF;
    - `page_first` and `page_last` are page numbers for a PDF, and `0` otherwise;
    - `heading` and `title` are `""` when there is none;
    - `chars` is `len(text)`;
    - `image_path` is `""` except on image chunks.
  - **`chunk_anchors`:**
    - PDF: one anchor per page, with the box around the lines, as today;
    - xlsx table: one anchor whose cell is the bounding range;
    - otherwise: one `{"from": …, "to": …}` per part, from the first and last line's anchors.
  - **Limits:** `anchors_json` is capped at 20,000 characters; when cut, the chunk gets the rule `chunk.anchors-truncated`.
  - **Docs:** `docs/vector-databases.md` gives short snippets for Qdrant, Chroma, Pinecone and pgvector. Each loads `.ragnaroq/chunks.jsonl`, upserts by `id`, and uses `changes.json` to delete removed ids.
  - **README:** update the chunk-field table.
  - **Optional real-store tests:** `tests/test_vector_stores.py` uses `pytest.importorskip("qdrant_client")` and `pytest.importorskip("chromadb")`. It loads a converted document's chunks into an in-memory Qdrant collection and an ephemeral Chroma collection, upserts them twice, and asserts the count equals the number of chunks. It is not in CI.

- [ ] **Step 4: Run the whole suite.** Run: `python -m pytest -q && ruff check .`

- [ ] **Step 5: Commit** with the message `Chunks for vector databases: UUID ids, flat metadata, a citation on every chunk`.

---

### Task 4: The demo folder and scoring by anchor

**Files:**
- Create: `references/make_demo_folder.py` and the committed `references/demo/`: the files below plus `<file>.expected.jsonl` beside each.
- Create: `src/ragnaroq/expected.py`
- Modify: `src/ragnaroq/evaluate.py` (a folder with `*.expected.jsonl` is scored by anchor)
- Test: `tests/test_expected.py`

**Interfaces:**
- Produces:
  - `expected.Expected(anchor: Anchor | None, text: str | None = None, flag: str | None = None, figure: Anchor | None = None, description: str | None = None, description_source: str | None = None, document_flag: str | None = None)`
  - `expected.load(path) -> list[Expected]`
  - `expected.score(document, items: list[Expected]) -> ExactScore`, where `ExactScore` holds `right: int`, `wrong: list[dict]`, `missing: list[dict]`, `extra: list[dict]` and `flags_missing: list[dict]`
  - `ExactScore.ok -> bool`, true when nothing is wrong, missing or unflagged; extra lines are reported but don't fail it
  - `evaluate.run()` scores `*.expected.jsonl` folders with `expected.score`. Their section in `evaluation.md` is "Exact formats".

**The demo files.** One file per row, made by `make_demo_folder.py` with the format libraries. Each holds the corner cases listed, with fixed, made-up content:

| File | Holds |
| --- | --- |
| `guide.docx` | Heading 1 and 2; a 3×2 table; footnote 1; a tracked insertion and a deletion; a comment; a PNG chart picture with alt text "Bar chart of tax collections" and a "Caption" paragraph under it |
| `rates.xlsx` | sheet "Rates": 5% (format `0%`), ₹1,50,000 (Indian lakh format), 2026-11-26 (format `dd-mmm-yyyy`), `=B2*B3` with a cached value, a merged header B1:D1, a cell with format `0.0E+00;;` (unknown); a hidden sheet "Lookup"; hidden row 9 |
| `rates-nocache.xlsx` | a formula saved without a cached value |
| `deck.pptx` | a title slide; bullets; speaker notes; a picture with no alt text and the text "₹12,000" drawn in it; a table |
| `notice.html` | h1, p, a table, `<img alt="Logo" src="logo.png">` (with `logo.png` beside it), `<img src="https://example.org/x.png">`, `<script>` |
| `readme.md` | headings, a paragraph, a table, a code block, `![Diagram](diagram.png)` |
| `deductions.json` | nested keys, an array of 3 records with the same keys, a duplicate key |
| `notice.xml` | nested elements and attributes |
| `bomb.xml` | a DTD with an entity |
| `rates.csv` | a `;` delimiter, cp1252 encoding with "é" and "€", a quoted value holding a newline |

Each `.expected.jsonl` holds one JSON object per line:
- `{"anchor": {...}, "text": "..."}` for a line;
- `{"anchor": {...}, "flag": "xlsx.format-unknown"}` for a flagged line;
- `{"figure": {...}, "description": "...", "description_source": "alt"}` for a figure;
- `{"document_flag": "xlsx.hidden"}` for a document flag.

- [ ] **Step 1: Write the failing tests** in `tests/test_expected.py`, using hand-built documents:
  - `test_a_right_document_scores_ok`
  - `test_wrong_text_is_reported_with_its_anchor`
  - `test_a_missing_anchor_is_reported`
  - `test_a_missing_flag_fails_the_score`
  - `test_extra_lines_are_reported_but_do_not_fail`
  - `test_a_folder_with_expected_files_is_scored_by_anchor`: uses `evaluate.run` with a stub reader registered for the test.

- [ ] **Step 2: Run them to make sure they fail.**

- [ ] **Step 3: Implement**
  - **Scoring:** `expected.py` matches by `anchors.key`, comparing text after `unicodedata.normalize("NFC")` and nothing else.
  - **The script:** `make_demo_folder.py` writes the files listed above. Write each `.expected.jsonl` by hand, from what each file holds; never from a reader's output.
  - **CI:** `tests/test_expected.py::test_the_demo_folder_files_are_present` checks that every demo file has its expected file.

- [ ] **Step 4: Run the whole suite.** Run: `python -m pytest -q && ruff check .`

- [ ] **Step 5: Commit** with the message `Demo folder of awkward files, and scoring by anchor`.

---

### Task 5: Reader registry; JSON, CSV and XML readers

**Files:**
- Create: `src/ragnaroq/readers/__init__.py`, `readers/_build.py`, `readers/json_reader.py`, `readers/csv_reader.py`, `readers/xml_reader.py`
- Modify: `pyproject.toml` (`defusedxml` core; extras `formats` and `test`)
- Test: `tests/test_readers_data.py`

**Interfaces:**
- Produces, in `readers/__init__.py`:
  - `ReaderUnavailable(RuntimeError)` and `ReaderRefused(Exception)`, the latter with `rule: str` and `message: str`
  - `ReadResult(document: Document, images: dict[str, bytes])`, keyed by figure id
  - `detect(name: str, data: bytes) -> str | None`, returning one of `pdf docx xlsx pptx html md json xml csv` or `None`. A zip-based format must contain `[Content_Types].xml` and its main part: `word/document.xml`, `xl/workbook.xml` or `ppt/presentation.xml`.
  - `read_bytes(data: bytes, name: str, config: Config, *, engines=None, third_reader=None, base: Path | None = None) -> ReadResult`. It dispatches to the reader, passing `base` to the HTML and Markdown readers, then runs `finish(document, config)` (structure and `policy.decide`) for non-PDF formats.
  - `READER_VERSIONS: dict[str, str]`: the reader name and the library version, for the manifest.
- Produces, in `readers/_build.py`:
  - `start(data: bytes, name: str, fmt: str, config: Config, components: dict[str, str]) -> Document`
  - `add_line(page: Page, text: str, source: str, anchor: Anchor, *, role="body", table_id=None, value=None) -> Line`, which numbers ids `p{page}-l{n}`
  - `finish(document, config) -> Document`
- Reader functions, one per module: `read(data: bytes, name: str, config: Config) -> ReadResult`.
- New `Config` fields: `max_depth: int = 200` and `max_cells: int = 200_000`.

- [ ] **Step 1: Write the failing tests**

```python
def test_json_scalars_are_lines_at_their_pointers():
    doc = read_bytes(b'{"deductions": {"80C": {"limit": 150000}}}', "d.json", Config()).document
    assert [(l.text, cite(l.anchor)) for l in doc.lines()] == [("150000", "/deductions/80C/limit")]

def test_a_json_array_of_records_is_a_table(): ...
    # [{"section":"80C","limit":150000},{"section":"80D","limit":25000}] -> one Table 3x2 with a header row,
    # cell anchors "/0/section" etc., lines accepted

def test_a_duplicate_json_key_keeps_the_last_and_flags_the_document(): ...  # "json.duplicate-key"

def test_deeply_nested_json_fails_alone():
    data = ("[" * 10_000 + "]" * 10_000).encode()
    with pytest.raises(ReaderRefused, match="nested") as e:
        read_bytes(data, "deep.json", Config())
    assert e.value.rule == "json.too-deep"

@pytest.mark.parametrize("raw, flagged", [
    ("Section;Amount\n80C;₹1,50,000\n".encode("utf-16"), False),
    ("Section;Amount\nCafé;€12\n".encode("cp1252"), True),
    ("﻿Section,Amount\n80C,150000\n".encode("utf-8"), False),
])
def test_csv_encodings(raw, flagged):
    doc = read_bytes(raw, "r.csv", Config()).document
    assert cite(next(doc.lines()).anchor) == "row 1, Section"
    assert ("encoding.guessed" in {f.rule for f in doc.flags}) is flagged

def test_a_csv_cell_with_a_newline_stays_one_cell(): ...

def test_xml_elements_are_lines_at_their_paths(): ...  # "/notice/section[2]/para[1]"; attributes become lines "@name"

def test_an_xml_entity_is_refused():
    with pytest.raises(ReaderRefused) as e:
        read_bytes(BOMB, "bomb.xml", Config())
    assert e.value.rule == "xml.entities"

def test_unknown_content_is_not_read():
    assert detect("notes.txt", b"hello") is None and detect("fake.xlsx", b"PK\x03\x04junk") is None
```

- [ ] **Step 2: Run them to make sure they fail.**

- [ ] **Step 3: Implement**
  - **JSON:**
    - `json.loads` with an `object_pairs_hook` that records duplicate keys;
    - nesting is measured iteratively before parsing, and over `max_depth` gives `json.too-deep`;
    - pointers are escaped per RFC 6901 (`~0`, `~1`).
  - **CSV:**
    - encodings tried in order: UTF-8-sig, UTF-16 when there is a BOM, then cp1252 with the document flag `encoding.guessed`;
    - `csv.Sniffer` on the first 64 KB for the delimiter, with `,` as the fallback;
    - the header row names the columns;
    - one line per non-empty cell;
    - one `Table` per file.
  - **XML:**
    - `defusedxml.ElementTree.fromstring`;
    - `EntitiesForbidden` and `DTDForbidden` become `ReaderRefused("xml.entities", …)`;
    - paths use the 1-based position among same-named siblings, with `[n]` only when there are several.
  - **Pages:** each reader's single page is `kind="body"`, `name=<file name>`, width and height `None`.
  - **Exported names:** `ragnaroq/__init__.py` exports `read_bytes`.

- [ ] **Step 4: Score the demo files.** `deductions.json`, `notice.xml`, `bomb.xml` and `rates.csv` must give `ExactScore.ok`. Run: `python -m pytest -q && ruff check .`

- [ ] **Step 5: Commit** with the message `Readers for JSON, CSV and XML, and a registry that detects formats by content`.

---

### Task 6: The Excel reader, with values as Excel shows them

**Files:**
- Create: `src/ragnaroq/readers/xlsx_reader.py`, `src/ragnaroq/readers/numformat.py`, `src/ragnaroq/readers/_zip.py`
- Test: `tests/test_readers_xlsx.py`

**Interfaces:**
- Produces:
  - `numformat.render(value, number_format: str) -> str | None`, where `None` means the format is not known
  - `_zip.check(data: bytes, config: Config) -> None`, which raises `ReaderRefused("file.zip-limits", …)`
  - New `Config` fields: `max_unzipped_bytes: int = 500 * 1024**2` and `max_zip_entries: int = 10_000`

- [ ] **Step 1: Write the failing tests**

```python
@pytest.mark.parametrize("value, fmt, shown", [
    (0.05, "0%", "5%"), (0.1234, "0.00%", "12.34%"), (1234567, "#,##0", "1,234,567"),
    (150000, '[>=10000000]"₹"##\\,##\\,##\\,##0;[>=100000]"₹"##\\,##\\,##0;"₹"##,##0', "₹1,50,000"),
    (12500000, '[>=10000000]"₹"##\\,##\\,##\\,##0;[>=100000]"₹"##\\,##\\,##0;"₹"##,##0', "₹1,25,00,000"),
    (1234.5, '[$₹-4009] #,##0.00', "₹ 1,234.50"), (-42, "#,##0;(#,##0)", "(42)"),
    (datetime(2026, 11, 26), "dd-mmm-yyyy", "26-Nov-2026"), (datetime(2026, 11, 26), "d/m/yyyy", "26/11/2026"),
    (7, "General", "7"), (2.5, "General", "2.5"), ("80C", "@", "80C"),
])
def test_values_render_as_excel_shows_them(value, fmt, shown):
    assert numformat.render(value, fmt) == shown

def test_an_unknown_format_is_none():
    assert numformat.render(0.001, "0.0E+00;;") is None

def test_the_demo_workbook_scores_by_anchor():
    # read references/demo/rates.xlsx and score it against rates.xlsx.expected.jsonl: ExactScore.ok

def test_a_hidden_sheet_and_row_are_left_out_and_named(): ...   # document flag "xlsx.hidden" names "Lookup" and row 9
def test_a_merged_cell_is_one_line_anchored_to_its_range(): ... # "Rates!B1:D1"
def test_a_formula_without_a_cached_value_gives_no_text(): ...   # rates-nocache.xlsx: text "", flag "xlsx.no-cached-value"
def test_the_stored_value_and_formula_are_kept(): ...            # line "5%": value.stored == 0.05, value.format == "0%"
def test_a_sheet_over_the_cell_limit_is_cut_and_flagged(): ...   # Config(max_cells=10) -> 10 lines, "xlsx.cell-limit"
def test_a_zip_bomb_is_refused_before_it_is_opened(): ...        # entries summing over max_unzipped_bytes -> "file.zip-limits"
```

- [ ] **Step 2: Run them to make sure they fail.**

- [ ] **Step 3: Implement**
  - **Opening:** `_zip.check` runs first. The workbook is loaded twice with openpyxl: with `data_only=False` for formulas and with `data_only=True` for cached values. Visible sheets only.
  - **Layout:**
    - each sheet is one page (`kind="sheet"`, `name=<sheet title>`);
    - its used range is one `Table`;
    - every non-empty, non-hidden cell is a line;
    - the first non-empty row is the header (`role="heading"` is not used for it).
  - **Images:** pictures (`ws._images`) become figures anchored to their top-left cell, with their bytes in `images`.
  - **Charts:** each chart (`ws._charts`) gives a page flag `chart.not-read` with its anchor.
  - **`numformat`:** implements the cases in the test table:
    - `General` and the `0` / `#,##0` families with decimals;
    - percent, and sections separated by `;`;
    - conditional sections `[>=n]`;
    - quoted literals and `\,` escapes, which give Indian grouping;
    - `[$₹-…]` currency tags;
    - the date tokens `d dd mmm mmmm m mm yy yyyy`, plus `h:mm`;
    - `@`.

    Anything else returns `None`, and the line takes the stored value as text with the flag `xlsx.format-unknown`.

- [ ] **Step 4: Run the whole suite.** Run: `python -m pytest -q && ruff check .`

- [ ] **Step 5: Commit** with the message `Excel reader: cells as Excel shows them, with stored values, formulas and cell anchors`.

---

### Task 7: The Word reader

**Files:**
- Create: `src/ragnaroq/readers/docx_reader.py`
- Test: `tests/test_readers_docx.py`

**Interfaces:**
- Consumes: `_zip.check`, `_build`, `ReadResult`.

- [ ] **Step 1: Write the failing tests**
  - `test_the_demo_document_scores_by_anchor` (`guide.docx`: `ExactScore.ok`)
  - `test_headings_come_from_heading_styles`
  - `test_paragraph_numbers_count_every_body_paragraph`: an empty paragraph still takes a number, so numbers stay stable.
  - `test_tracked_changes_read_as_accepted_and_are_flagged`: an inserted word is in the text and a deleted one is not; document flag `docx.tracked-changes`.
  - `test_comments_are_left_out_and_flagged`: document flag `docx.comments`.
  - `test_a_footnote_is_its_own_line`: anchor `DocxAnchor(note="footnote 1")`.
  - `test_a_picture_becomes_a_figure_with_its_alt_text_and_caption`: the caption paragraph wins over alt text as the description, and `description_source == "caption"`.
  - `test_a_chart_object_is_flagged_not_read`: `chart.not-read`.

- [ ] **Step 2: Run them to make sure they fail.**

- [ ] **Step 3: Implement**
  - **Order:** python-docx; walk `document.element.body` children in order.
  - **Paragraphs:**
    - each `w:p` is numbered from 1;
    - its text is every `w:t` under it, including those inside `w:ins`. Deleted text lives in `w:delText` and is never read.
  - **Tables:** each `w:tbl` is numbered from 1; every cell is a line anchored `DocxAnchor(table=t, cell=(r, c))`.
  - **Footnotes:** read from the package's footnotes part (`word/footnotes.xml`), skipping the separator notes, with ids starting at 1.
  - **Pictures:**
    - `a:blip r:embed` gives the image part's bytes;
    - alt text comes from `wp:docPr/@descr`, else `@title`;
    - the caption is the next paragraph whose style name starts with "Caption".
  - **Charts:** `c:chart` inside `a:graphicData` gives `chart.not-read`.
  - **Page:** one page, `kind="body"`.

- [ ] **Step 4: Run the whole suite.** Run: `python -m pytest -q && ruff check .`

- [ ] **Step 5: Commit** with the message `Word reader: paragraphs, tables, footnotes and pictures with exact anchors`.

---

### Task 8: The PowerPoint reader

**Files:**
- Create: `src/ragnaroq/readers/pptx_reader.py`
- Test: `tests/test_readers_pptx.py`

**Interfaces:**
- Consumes: `_zip.check`, which runs before the file is opened; `_build`; `ReadResult`.

- [ ] **Step 1: Write the failing tests**
  - `test_the_demo_deck_scores_by_anchor` (`deck.pptx`)
  - `test_the_title_is_a_heading_and_each_slide_a_part`: `kind="slide"`, `name=<title>`
  - `test_bullets_are_lines_with_their_shape_and_paragraph`: `PptxAnchor(slide=2, shape="Content Placeholder 2", paragraph=1)`
  - `test_speaker_notes_are_lines_marked_as_notes`
  - `test_shapes_inside_a_group_are_read`
  - `test_a_picture_without_alt_text_has_no_description_until_ocr`: `description_source == "none"` straight from the reader
  - `test_a_slide_table_is_a_table_with_cell_anchors`
  - `test_a_chart_is_flagged_not_read`

- [ ] **Step 2: Run them to make sure they fail.**

- [ ] **Step 3: Implement**
  - **Library:** python-pptx. Slides are numbered from 1.
  - **Shapes:** walked in order, recursing into groups. The shape name is `shape.name`.
  - **Text:** each text-frame paragraph is a line. The title placeholder gets `role="heading"`.
  - **Notes:** the notes slide's text is lines with `notes=True` and `shape="Notes"`.
  - **Tables:** `shape.has_table` gives a `Table`.
  - **Pictures:** `MSO_SHAPE_TYPE.PICTURE` gives a figure; alt text comes from `cNvPr/@descr`.
  - **Charts:** `shape.has_chart` gives `chart.not-read`.

- [ ] **Step 4: Run the whole suite.** Run: `python -m pytest -q && ruff check .`

- [ ] **Step 5: Commit** with the message `PowerPoint reader: slides, shapes, notes, tables and pictures`.

---

### Task 9: The HTML and Markdown readers

**Files:**
- Create: `src/ragnaroq/readers/html_reader.py`, `src/ragnaroq/readers/md_reader.py`
- Test: `tests/test_readers_web.py`

**Interfaces:**
- Both readers take one extra keyword, `base: Path | None = None`: the file's folder. A local image path resolves against it, never outside it.

- [ ] **Step 1: Write the failing tests**
  - `test_the_demo_page_scores_by_anchor` (`notice.html`)
  - `test_the_demo_readme_scores_by_anchor` (`readme.md`)
  - `test_scripts_styles_and_templates_are_ignored`
  - `test_a_remote_image_is_kept_unfetched_with_a_flag`: `Figure.path == "https://example.org/x.png"` and flag `image.remote-not-fetched`. The test also asserts no socket was opened by monkeypatching `socket.socket` to raise.
  - `test_a_local_image_is_read_from_beside_the_file`
  - `test_an_image_path_leaving_the_folder_is_refused`: `../../etc/passwd.png` gives flag `image.outside-folder` and no bytes.
  - `test_a_data_uri_image_is_decoded_within_the_size_limit`
  - `test_markdown_lines_are_anchored_to_their_source_lines`: `MdAnchor(lines=(3, 4))` for a two-line paragraph starting at line 3.

- [ ] **Step 2: Run them to make sure they fail.**

- [ ] **Step 3: Implement**
  - **HTML:**
    - parsed with `lxml.html` using `HTMLParser(no_network=True, remove_comments=True)`;
    - `script`, `style`, `template` and `noscript` are dropped;
    - the block elements `h1`–`h6`, `p`, `li`, `pre`, `blockquote`, `dt`, `dd`, `caption`, and table cells each become a line;
    - the anchor is `tree.getpath(element)`;
    - `img` becomes a figure: the description from `alt`, and the bytes from a local file, a `data:` URI, or none for a remote image.
  - **Markdown:**
    - `markdown_it.MarkdownIt("commonmark").enable("table")`;
    - each block token's `map` gives the line range;
    - inline images become figures;
    - a fenced code block is a line with `role="body"`.

- [ ] **Step 4: Run the whole suite.** Run: `python -m pytest -q && ruff check .`

- [ ] **Step 5: Commit** with the message `HTML and Markdown readers, with element paths, line ranges and local images`.

---

### Task 10: Figures: describe from OCR, save, and write

**Files:**
- Create: `src/ragnaroq/figures.py`
- Modify: `src/ragnaroq/pipeline.py`: move the line-building in `_scanned` into `ocr_lines(image, engines, config, *, page: int, anchor: Anchor) -> list[Line]` and reuse it; `write(document, out, config, images=None)` saves the images.
- Test: `tests/test_figures.py`

**Interfaces:**
- Produces:
  - `figures.describe(result: ReadResult, engines, config) -> None`: OCR for each figure whose `description_source == "none"`.
  - `figures.save(result: ReadResult, out: Path) -> None`: writes `images/` and sets `Figure.path`.
  - `pipeline.write(document, out, config, images: dict[str, bytes] | None = None) -> dict[str, Path]`
  - Image file names come from the citation: `slide-4-picture-2.png`, `rates-c7.png`, `p18.png`.

- [ ] **Step 1: Write the failing tests**, with `FakeEngine` from conftest:
  - `test_a_figure_without_alt_text_is_described_by_its_ocr_text`: two agreeing readers on "₹12,000" give `description == "₹12,000"`, `description_source == "ocr"`, and status accepted after `policy.decide`.
  - `test_disagreeing_readers_leave_the_figure_flagged`
  - `test_a_figure_with_alt_text_is_not_ocr_read`: the engines are never called.
  - `test_images_are_saved_under_names_from_their_citation`
  - `test_tiny_images_are_counted_not_saved`: an 8×8 PNG gives no file, and the document flag `image.tiny-skipped` says "1".
  - `test_an_image_that_cannot_be_shown_on_the_web_is_converted_to_png`: a BMP gives `.png`.
  - `test_an_image_pillow_cannot_open_is_kept_and_flagged`: an EMF is kept as `.emf` with flag `image.not-viewable`.
  - `test_the_demo_deck_picture_gets_its_text_from_ocr`: marked `skipif` without tesseract. It runs real Tesseract only and expects "12,000" in the description.

- [ ] **Step 2: Run them to make sure they fail.**

- [ ] **Step 3: Implement**
  - **Opening an image:** with Pillow. The check `max_render_pixels` applies, and EXIF orientation is respected.
  - **OCR text:** OCR lines go through `ocr_lines` with the figure's anchor, `page=figure.page` and boxes in image pixels.
  - **Description:** the first reader's lines joined by spaces, at most 300 characters.
  - **Web formats:** PNG, JPEG, GIF, WebP and SVG are saved as they are. Other formats are converted to PNG when Pillow can open them, and kept with `image.not-viewable` when it cannot.

- [ ] **Step 4: Run the whole suite.** Run: `python -m pytest -q && ruff check .`

- [ ] **Step 5: Commit** with the message `Figures: OCR descriptions through the two-reader rules, images saved beside the text`.

---

### Task 11: `ragnaroq index`: the folder, the manifest and incremental runs

**Files:**
- Create: `src/ragnaroq/folder.py`
- Modify:
  - `src/ragnaroq/isolation.py`: a whole-file request `isolation.read_file(data: bytes, name: str, config, *, base: Path | None) -> ReadResult`. It runs `readers.read_bytes` in the worker with `config.file_timeout_seconds` and `page_memory_mb`.
  - `src/ragnaroq/cli.py`: the `index` command.
  - `src/ragnaroq/config.py`: `file_timeout_seconds: float = 120.0`.
- Test: `tests/test_folder.py`

**Interfaces:**
- Produces:
  - `folder.index(root: Path, config: Config, *, engines=None, third_reader=None, fresh=False, progress=None) -> IndexReport`
  - **Pydantic models:**
    - `FileEntry(path, file_id, sha256, bytes, format, reader, settings, status, counts: dict[str, int], chunk_ids: list[str], error: str = "", skipped: str = "")`
    - `Manifest(schema_version="0.3.0", ragnaroq: str, files: list[FileEntry])`
    - `Changes(added: list[str], changed: list[str], removed: list[str], chunks_added: list[str], chunks_removed: list[str])`, where the first three list file ids
    - `IndexReport(manifest, changes, failed: int, needs_review: bool)`
  - **CLI:** `ragnaroq index FOLDER [--fresh] [the convert options: --ocr --lang --dpi --exclude-script --third-reader --vlm-models --vlm-download --easyocr-models --easyocr-download]`
  - **Exit codes:** 1 if any file failed; else 2 if anything needs a person; else 0.

- [ ] **Step 1: Write the failing tests**, using a copy of `references/demo` in `tmp_path` and `engines=[]` for speed:
  - `test_index_writes_the_folder_layout`: `manifest.json`, `changes.json`, `chunks.jsonl`, and `files/<file_id>/{document.json,document.md,chunks.jsonl,images/}`. The originals are byte-identical afterwards.
  - `test_a_second_run_reads_nothing_and_reports_no_changes`: monkeypatch `readers.read_bytes` to fail if called.
  - `test_an_edited_file_is_read_again_and_its_chunks_replaced`
  - `test_a_deleted_file_is_removed_with_its_chunks`
  - `test_a_renamed_file_removes_old_chunks_and_adds_new`
  - `test_one_bad_file_fails_alone`: a truncated `.docx` and a zip bomb named `.xlsx` both fail, with their errors in the manifest; every other file is indexed; exit code 1.
  - `test_unsupported_hidden_and_escaping_files_are_listed_as_skipped`: `notes.txt` ("unsupported"), `.secret.json` ("hidden"), and a symlink to `/etc/passwd` ("outside the folder").
  - `test_combined_chunks_hold_every_file_and_pass_the_vector_database_rules`: reuse Task 3's checks.
  - `test_index_command_exit_codes`

- [ ] **Step 2: Run them to make sure they fail.**

- [ ] **Step 3: Implement**
  - **Walking the folder:** `os.walk`, sorted. Skip `.ragnaroq`, hidden files and folders, symbolic links whose real path leaves `root`, and files over `config.max_bytes`.
  - **Settings:** `settings` is a hash of `config.reading_sha256()`, `policy.version`, `READER_VERSIONS[format]` and `ragnaroq.__version__`. A file is read again when its sha256 or settings differ, or with `--fresh`.
  - **Reading:** PDFs use `pipeline.convert` as today; every other format uses `isolation.read_file`, then `figures.describe` and `figures.save`.
  - **Writing:** write to a temporary folder and rename, so an interrupted run never leaves half a file.
  - **Combined chunks:** `chunks.jsonl` concatenates every file's chunks, sorted by path.

- [ ] **Step 4: Run the whole suite.** Run: `python -m pytest -q && ruff check .`

- [ ] **Step 5: Commit** with the message `ragnaroq index: a folder made ready for retrieval, with incremental runs`.

---

### Task 12: Model-written image descriptions (optional; may move to 0.3.1)

**Files:**
- Create: `src/ragnaroq/ocr/describe.py`
- Modify: `pyproject.toml` (extra `describe` = the `vlm` dependencies), `cli.py` (`--describe-images`), `figures.py`
- Test: `tests/test_describe.py`

- [ ] **Step 1: Spike (throwaway, in the scratchpad).** Try up to three small vision-language models that are native in transformers, Apache or MIT licensed, and under 2B parameters (for example SmolVLM-256M or -500M Instruct). Use the demo pictures and 10 IRS form crops.
  - Measure: seconds per image on CPU, and whether the sentence invents numbers. Any number in the sentence must appear in the image's OCR text.
  - Pick one that invents no numbers. If none passes, stop here, and record in `CHANGELOG.md` that model descriptions move to 0.3.1.
- [ ] **Step 2: Write the failing tests** with a stand-in describer:
  - `test_a_model_sentence_is_kept_as_a_label_and_never_changes_the_status`
  - `test_a_sentence_with_a_number_missing_from_the_ocr_text_is_dropped`
  - `test_describing_is_off_by_default`
- [ ] **Step 3: Implement**
  - The reader pattern is the same as Task 4 of the third-reader plan: a pinned revision, file checksums, `trust_remote_code=False`, `local_files_only=True`, and a time limit per image (60 s).
  - `Figure.model_description` holds the sentence.
  - The image chunk's embedding text appends it, marked `(model)`.
- [ ] **Step 4: Run the whole suite**, and run the real-model test once in the model venv.
- [ ] **Step 5: Commit** with the message `Optional model-written image descriptions, kept as labels`.

---

### Task 13: Release 0.3.0

**Files:**
- Modify: `pyproject.toml` and `src/ragnaroq/__init__.py` (0.3.0), `CHANGELOG.md`, `README.md`, `docs/roadmap.md`, `docs/evaluating.md`, `references/README.md`

- [ ] **Step 1: Measure.**
  - **Demo folder:** run `ragnaroq evaluate references/demo` and record that every exact-format file scores `ok`.
  - **PDF sets:** re-score the IRS, held-out and CBDT sets from their stored readings. Their numbers must match 0.2.2's.
- [ ] **Step 2: Docs.**
  - **README:** formats and anchors, `ragnaroq index`, the `.ragnaroq/` layout, images, a link to `docs/vector-databases.md`, and the status table.
  - **Roadmap:** 0.3 done.
  - **Changelog:** a 0.3.0 entry. It covers the schema change (`anchor`, `Figure`, the new chunk shape, and that 0.1 and 0.2 documents load), policy 0.6, the new extra, and the known limits: charts not read, `.doc`/`.xls`/`.ppt` not read, and remote images not fetched.
- [ ] **Step 3: Verify.** Run: `python -m pytest -q && ruff check . && python -m ragnaroq.cli --version` (expect `0.3.0`), then `python -m ragnaroq.cli index references/demo` in a copy (expect exit 2, with flags from the corner cases).
- [ ] **Step 4: Commit and push** with the message `Release 0.3.0: folders and formats`. The controller zips `references/demo` and sends it to the owner for `Downloads/ragx`.
