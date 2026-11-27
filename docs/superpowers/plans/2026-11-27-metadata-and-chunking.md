# 0.4 Metadata and Chunking Strategies Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Every document gets a rule-made, grounded `metadata.json` entry and is cut by one of eight chunking strategies chosen from it, with the choice written to plan files, overridable, and measured against 0.3's chunks.

**Architecture:** Two new packages. `ragnaroq.semantic` turns a `Document` into paragraphs, finds dates, periods, amounts, references and structure in them, builds `DocumentMetadata`, and chooses a strategy by fixed rules or an override. `ragnaroq.chunking` splits sentences and builds `Piece`s per strategy; `export/chunks.py` turns pieces into 0.4.0 chunks. `ragnaroq.chunkeval` holds a frozen copy of 0.3's chunker and a BM25 keyword search for `evaluate --chunking`. The pipeline and the folder index call these after reading; the folder re-chunks from the stored `document.json` without reading again.

**Tech Stack:** Python 3.12+, pydantic (already used), `re`, `tomllib`, pytest, reportlab / python-docx / python-pptx / openpyxl (already test extras) for the generated set.

**Spec:** `docs/superpowers/specs/2026-11-27-metadata-and-chunking-design.md` (approved 2026-11-27). Read it before any task; section numbers below are its sections.

## Global Constraints

- Python 3.12 or newer. No new dependency in `[project.dependencies]`; `ragnaroq.toml` is read with `tomllib`.
- Offline and deterministic: the same folder and settings give byte-identical `metadata.json`, plan files and chunks.
- No language model anywhere in 0.4.
- `chunk_chars` stays the one size setting, default 1200.
- Chunks: UUID `id`; metadata values are str, int, float, bool, or a non-empty list of str (only `dates`, `periods`, `references`, `terms`); no nulls; the same keys on every chunk; `anchors_json` at most 20,000 UTF-8 bytes.
- An empty list key is `[""]`; a chunk's list key holds at most 50 values (`CHUNK_LIST_LIMIT = 50`).
- A document's list fields hold at most 1,000 values (`DOC_LIST_LIMIT = 1000`); past it the list is cut and the entry gets flag `metadata.list-limit` naming the field.
- Every regular expression is linear: no nested quantifiers; every rule is linear in the text.
- Schema of `document.json`, chunk format, metadata format, manifest: `"0.4.0"`. `CHUNKING_VERSION = "1"`.
- `ragnaroq index` stays POSIX-only; `ragnaroq convert` works anywhere.
- Override keys: `strategy`; `max_chars` 200 to 4,000 (default `chunk_chars`); `min_chars` 0 to half of `max_chars` (default 200). Nothing else.
- Strategy names, exactly: `single`, `question-answer`, `numbered-paragraphs`, `sections`, `table-rows`, `slides`, `records`, `sentences`.
- Kind names, exactly: `spreadsheet`, `slides`, `data`, `faq`, `numbered`, `sectioned`, `prose`.
- Doubtful (flagged) lines stay in chunks, as in 0.3; blocked and excluded lines do not.
- Commit messages: plain English subject; end with the two lines `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>` and `Claude-Session: https://claude.ai/code/session_01AfdfCGQZ3XqaayqdTTQVCa`. Run `ruff check .` and `ruff format --check` on changed files before each commit (line length 110).

## Review Focus

1. **A document with no usable lines** (an empty CSV, a PDF whose every page failed, every line excluded by `--exclude-script`): a valid metadata entry (title from the file name, empty lists, the kind its format gives, else `prose`), a plan entry, zero chunks, no exception. Tests in Task 9 (metadata) and Task 12 (chunks).
2. **A word longer than `max_chars` with no space in it** (a URL, base64, minified data): split at exactly `max_chars`, each piece flagged `chunk.long-sentence`, in linear time. Test in Task 11.
3. **Text whose sentences end with `।` or `॥` (Devanagari), or start in lower case:** `।`/`॥` end a sentence whatever follows; `. ` followed by a lower-case letter does not. Test in Task 11.
4. **A heading with nothing under it, and runs of headings** (sections strategy): a section shorter than `min_chars` joins the next section under the same parent instead of making a tiny chunk; its heading stays in the heading path. Test in Task 13.
5. **A `ragnaroq.toml` that is not valid TOML, or has no `[chunking]` table:** invalid TOML stops the run with the TOML error's line; a file without `[chunking]` means no overrides. Test in Task 10.

---

## File Structure

| File | Responsibility |
| --- | --- |
| `src/ragnaroq/chunkeval/baseline.py` (new) | 0.3's `to_chunks`, frozen, for comparison only |
| `src/ragnaroq/chunkeval/bm25.py` (new) | Tokenizer and BM25 ranking |
| `src/ragnaroq/chunkeval/__init__.py` (new) | Questions file, scoring, report |
| `src/ragnaroq/semantic/model.py` (new) | Pydantic models of `metadata.json` and plan entries |
| `src/ragnaroq/semantic/paragraphs.py` (new) | `Para`: a paragraph's joined text, its line spans, level, heading path |
| `src/ragnaroq/semantic/find.py` (new) | Dates, periods, amounts, references, file mentions in a string |
| `src/ragnaroq/semantic/shape.py` (new) | Numbered paragraphs, provisos, explanations, Q&A, definitions, records |
| `src/ragnaroq/semantic/describe.py` (new) | `DocumentMetadata` for a document |
| `src/ragnaroq/semantic/rules.py` (new) | Thresholds, kind rules, strategy rules, `choose` |
| `src/ragnaroq/semantic/overrides.py` (new) | `ragnaroq.toml` parsing, validation, matching |
| `src/ragnaroq/semantic/folder.py` (new) | Folder master file, links, glossary, plan files |
| `src/ragnaroq/semantic/__init__.py` (new) | Public names of the package |
| `src/ragnaroq/chunking/sentences.py` (new) | Sentence ends, packing with overlap, long sentences |
| `src/ragnaroq/chunking/pieces.py` (new) | `Piece`, `StrategyContext`, shared packers |
| `src/ragnaroq/chunking/strategies.py` (new) | The eight strategies and the additions |
| `src/ragnaroq/export/chunks.py` | Pieces to 0.4.0 chunk dicts |
| `src/ragnaroq/model.py` | `Line.level`, schema 0.4.0 |
| `src/ragnaroq/structure.py` | `levels(document)` for PDF headings |
| `src/ragnaroq/readers/_build.py`, `docx_reader.py`, `html_reader.py`, `md_reader.py`, `pptx_reader.py`, `json_reader.py`, `xml_reader.py` | Set `level` on heading lines |
| `src/ragnaroq/readers/__init__.py` | `READING_VERSIONS` |
| `src/ragnaroq/pipeline.py` | `write` writes `metadata.json` |
| `src/ragnaroq/folder.py` | Reading key, chunking key, re-chunking, 0.3 upgrade, folder files |
| `src/ragnaroq/cli.py` | `--strategy`, `schema --metadata`, `evaluate --chunking` |
| `schemas/metadata.schema.json` (new) | Generated from `semantic.model` |
| `references/chunking/` (new) | README, generator, generated and public documents, two questions files |

---

### Task 1: Freeze 0.3's chunker as the baseline

**Files:**
- Create: `src/ragnaroq/chunkeval/__init__.py` (empty for now), `src/ragnaroq/chunkeval/baseline.py`
- Create: `tests/golden/baseline-0.3/` (one `<demo file>.jsonl` per demo file)
- Test: `tests/test_chunkeval.py`

**Interfaces:**
- Produces: `baseline_chunks(document: Document, config: Config, *, path: str | None = None) -> list[dict]` in `ragnaroq.chunkeval.baseline`: exactly 0.3's `to_chunks(document, config, path=path)`.

- [ ] **Step 1: Write the golden files and the failing test**

Generate the goldens once with the current `ragnaroq.export.chunks.to_chunks` (before anything else changes): for each file in `references/demo` that is not `*.expected.jsonl` or `*.png` and reads without `ReaderRefused`, write `to_jsonl(to_chunks(document, Config()))` to `tests/golden/baseline-0.3/<name>.jsonl`. Then:

```python
def test_the_baseline_is_0_3s_chunker_byte_for_byte():
    for golden in sorted(GOLDEN.glob("*.jsonl")):
        name = golden.name[: -len(".jsonl")]
        document = read_bytes((DEMO / name).read_bytes(), name, Config(), base=DEMO).document
        assert to_jsonl(baseline_chunks(document, Config())) == golden.read_text(encoding="utf8")
```

- [ ] **Step 2: Run it; it fails with ImportError**

Run: `python -m pytest tests/test_chunkeval.py -q`

- [ ] **Step 3: Copy 0.3's chunker into `baseline.py`**

Copy `to_chunks` and every private helper it uses from `export/chunks.py` (as of commit `eacb0ee`) into `baseline.py`, renamed `baseline_chunks`; import `units`, `table_markdown`, `grid_cell`, `grid_markdown`, `layout` from `ragnaroq.export` and keep `SCHEMA_VERSION = "0.3.0"` as a local constant so the baseline's chunks keep saying 0.3.0. The module docstring says it is frozen and used only by `evaluate --chunking`.

- [ ] **Step 4: Run the test; it passes**

- [ ] **Step 5: Commit** — "Keep 0.3's chunker as the baseline for measuring chunking"

---

### Task 2: Keyword search, questions file and `evaluate --chunking`

**Files:**
- Create: `src/ragnaroq/chunkeval/bm25.py`
- Modify: `src/ragnaroq/chunkeval/__init__.py`, `src/ragnaroq/cli.py`
- Test: `tests/test_chunkeval.py`

**Interfaces:**
- Consumes: `baseline_chunks` (Task 1); `ragnaroq.export.chunks.to_chunks` (the current chunker; Task 12 changes what it returns, not its name).
- Produces:
  - `tokens(text: str) -> list[str]`: `re.findall(r"\w+", text.lower())`.
  - `class BM25(docs: list[str], k1: float = 1.2, b: float = 0.75)` with `rank(query: str) -> list[int]` (document indexes, best first; ties by index).
  - `Question(question: str, path: str, answer: str)` (dataclass); `load_questions(path: Path) -> list[Question]` (JSON Lines; a blank line is skipped; a line missing a field raises `ValueError` naming the line number).
  - `ChunkerScore` (dataclass): `top1, top3, top10: int`, `mrr: float`, `mean_chars: float`, `chunks: int`, `ranks: list[int | None]` (1-based rank of the first hit, None when no hit).
  - `ChunkingReport` (dataclass): `questions: list[Question]`, `scores: dict[str, ChunkerScore]` with keys `"0.3"` and `"0.4"`, `missing: list[int]` (indexes of questions whose answer is in no 0.4 chunk of its document).
  - `run_chunking(folder: Path, questions: Path, config: Config) -> ChunkingReport`; `write_chunking(report: ChunkingReport, out: Path) -> dict[str, Path]` (writes `chunking.json` and `chunking.md`).
  - CLI: `ragnaroq evaluate FOLDER --chunking QUESTIONS --out OUT`; exit 1 when `report.missing` is not empty, else 0.

- [ ] **Step 1: Write the failing tests**

```python
def test_bm25_ranks_the_document_with_the_rare_word_first():
    docs = ["the tax year starts in april", "the tax return is due", "a proviso to section 10"]
    assert BM25(docs).rank("proviso section")[0] == 2

def test_a_hit_is_a_chunk_of_the_same_file_holding_the_passage_whitespace_aside():
    chunk = {"text": "Deduction  may be\nclaimed until 31 March.", "metadata": {"path": "a.md"}}
    assert is_hit(chunk, Question("q", "a.md", "may be claimed until 31 March"))
    assert not is_hit(chunk, Question("q", "b.md", "may be claimed until 31 March"))

def test_evaluate_chunking_scores_both_chunkers_on_the_same_questions(tmp_path):
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "a.md").write_text("# Rates\n\nThe rate is 5 per cent for small firms.\n")
    q = tmp_path / "q.jsonl"
    q.write_text('{"question": "rate for small firms?", "path": "a.md", "answer": "The rate is 5 per cent"}\n')
    report = run_chunking(tmp_path / "docs", q, Config())
    assert set(report.scores) == {"0.3", "0.4"} and report.missing == []
    assert report.scores["0.3"].top1 == 1 and report.scores["0.3"].mrr == 1.0
```

Plus a CLI test: `main(["evaluate", str(docs), "--chunking", str(q), "--out", str(out)])` returns 0 and writes `chunking.md` and `chunking.json`; with a question whose answer is in no chunk it returns 1.

- [ ] **Step 2: Run them; they fail**

- [ ] **Step 3: Implement**

`run_chunking` reads every supported file under `folder` (sorted, recursive, skipping hidden names) with `read_bytes(data, relative_posix_path, config, engines=[], base=folder)` and no OCR; a `ReaderRefused` file is skipped. For each chunker it builds the folder's chunks (`baseline_chunks` and `to_chunks`, `path=relative_posix_path`), ranks them per question by BM25 over `embedding_text`, and finds the first hit. `is_hit(chunk, question)` compares `chunk["metadata"]["path"]` with `question.path` and `" ".join(answer.split())` as a substring of `" ".join(chunk["text"].split())`. `chunking.md` has one table with a row per chunker (top 1/3/10 as count and share, MRR to three places, mean chunk length, chunks) and a list of the questions with their ranks; `chunking.json` holds the same as plain data.

- [ ] **Step 4: Run the tests; they pass**

- [ ] **Step 5: Commit** — "evaluate --chunking: rank chunks by keyword search and score both chunkers"

---

### Task 3: The held-out public set, questions written before any strategy code

**Files:**
- Create: `references/chunking/README.md`, `references/chunking/public/` (documents), `references/chunking/public.questions.jsonl`
- Test: `tests/test_chunkeval.py`

**Interfaces:**
- Consumes: `load_questions`, `read_bytes`.

This task must be finished and committed before Tasks 11–14 start, so the questions cannot be fitted to the chunker. Do not run `evaluate --chunking` on this set until Task 17.

- [ ] **Step 1: Choose and pin the documents**

From irs.gov only (the US Code hosts are blocked in the build environment). They are works of the US federal government, so public domain, and they are committed to the repo: irs.gov's HTML pages change daily, so a download pinned by hash could not be rebuilt, and committing them keeps the set fixed. The spec said "fetched by `ragnaroq reference chunking`"; this deviation is recorded in the README and the release notes, and no fetch command is built. At least:
- a sectioned publication PDF (for example `https://www.irs.gov/pub/irs-pdf/p501.pdf`);
- a numbered-sections PDF (a revenue procedure, for example `https://www.irs.gov/pub/irs-drop/rp-24-40.pdf`);
- two or three FAQ pages saved as HTML (under `https://www.irs.gov/faqs/`).

Total size under 3 MB. The README gives each file's source URL, download date and SHA-256, and says the questions were committed before the chunking strategies were written.

- [ ] **Step 2: Write at least 40 questions** in `public.questions.jsonl`: at least 10 per document kind (sections, numbered, FAQ). Each `answer` is a passage copied verbatim from the document's text as `ragnaroq convert` reads it, one phrase or one sentence, appearing once in that document. Spread them over the whole of each document, including late pages and long paragraphs.

- [ ] **Step 3: Write the check, run it, and commit**

```python
def test_every_held_out_answer_is_in_its_document_once():
    for q in load_questions(PUBLIC / "../public.questions.jsonl"):
        text = " ".join(" ".join(l.text for l in read_public(q.path).lines()).split())
        assert text.count(" ".join(q.answer.split())) == 1, q.question
```

Run: `python -m pytest tests/test_chunkeval.py -q`. Expected: PASS. Commit — "Chunking reference set: held-out public documents and their questions".

---

### Task 4: The generated tuning set

**Files:**
- Create: `references/chunking/make_generated.py`, `references/chunking/generated/` (its output, committed), `references/chunking/generated.questions.jsonl`
- Test: `tests/test_chunkeval.py`

**Interfaces:**
- Consumes: `load_questions`.

- [ ] **Step 1: Write the generator**, deterministic like `references/make_demo_folder.py` (fixed dates, fixed zip times, no randomness). It writes:
  - `notice.pdf` (reportlab): eight numbered paragraphs `1.`–`8.`, two `Provided that` provisos, one `Explanation.—`, a paragraph longer than 2,000 characters, and a date, a period and a section reference in the text;
  - `faq.docx`: twelve `Q.`/`A.` pairs, some answers longer than 1,200 characters;
  - `manual.md` and `manual.html`: the same three-level sectioned text, with one heading that has nothing under it;
  - `contract.docx`: a definitions clause with six `"term" means …` sentences and numbered clauses;
  - `deck.pptx`: six slides with titles, body text and speaker notes;
  - `rates.xlsx`: one sheet of 300 rows with a header;
  - `records.json`: an object of nested records, and `table.json`: an array of flat same-key objects;
  - `essay.md`: 6,000 characters of prose with no headings.
- [ ] **Step 2: Write at least 30 questions** in `generated.questions.jsonl`, at least three per file, with the same rules as Task 3.
- [ ] **Step 3: Test that the generator is deterministic and every answer is in its document once**

```python
def test_the_generated_set_is_made_again_byte_for_byte(tmp_path):
    make_generated(tmp_path)
    for made in sorted(tmp_path.iterdir()):
        assert made.read_bytes() == (GENERATED / made.name).read_bytes(), made.name
```

Plus the answer check of Task 3 over `generated.questions.jsonl`. Run, pass, commit — "Chunking reference set: generated documents for tuning".

---

### Task 5: Heading levels and schema 0.4.0

**Files:**
- Modify: `src/ragnaroq/model.py`, `src/ragnaroq/structure.py`, `src/ragnaroq/readers/_build.py`, `docx_reader.py`, `html_reader.py`, `md_reader.py`, `pptx_reader.py`, `json_reader.py`, `xml_reader.py`, `src/ragnaroq/readers/__init__.py`, `schemas/document.schema.json`
- Test: `tests/test_levels.py`

**Interfaces:**
- Produces:
  - `Line.level: int = 0` (`Field(description="Heading level, 1 the highest; 0 when the line is not a heading.")`); `SCHEMA_VERSION = "0.4.0"`; `Document.schema_version` accepts `"0.1.0"`–`"0.4.0"` and restamps `"0.4.0"`.
  - `_build.add_line(..., level: int = 0)`.
  - `structure.levels(document: Document) -> None`: for every heading line with `level == 0`, sets its level; for PDF documents, the distinct `font_size` values of heading lines ranked descending, the largest 1, capped at 6, a heading with no `font_size` 1; for any other format, 1. Idempotent.
  - `READING_VERSIONS: dict[str, str]` in `ragnaroq.readers`: `{"pdf": "1", "xlsx": "1", "csv": "1", "docx": "2", "html": "2", "md": "2", "pptx": "2", "json": "2", "xml": "2"}` (raised where 0.4 changes what a reader writes).

- [ ] **Step 1: Write the failing tests**

```python
def test_word_html_markdown_and_pptx_headings_carry_their_level():
    assert levels_of(read_md(b"# A\n\n## B\n\n### C\n\ntext\n")) == [1, 2, 3, 0]
    assert levels_of(read_html(b"<h1>A</h1><h3>B</h3><p>x</p>")) == [1, 3, 0]
    # docx: Title -> 1, "heading 2" -> 2; pptx: slide title -> 1; json key depth, capped at 6

def test_pdf_heading_levels_come_from_font_sizes_largest_first():
    document = pdf_document_with_headings(sizes=[18, 14, 18, 11])  # body text 10
    structure.levels(document)
    assert [l.level for l in document.lines() if l.role == "heading"] == [1, 2, 1, 3]

def test_a_0_3_document_loads_restamped_with_levels_zero():
    old = json.loads(GOLDEN_03_DOCUMENT)  # a stored 0.3.0 document.json
    document = Document.model_validate(old)
    assert document.schema_version == "0.4.0" and {l.level for l in document.lines()} == {0}
```

- [ ] **Step 2: Run them; they fail**
- [ ] **Step 3: Implement** the field, `add_line`'s parameter, each reader's level (Word: `Title` 1, `heading N` N; HTML `hN`; Markdown the `#` count from the heading token's `tag`; PowerPoint title 1; JSON and XML depth of the key's path, capped at 6), `structure.levels`, and `READING_VERSIONS`. Regenerate `schemas/document.schema.json` the way its sync test expects.
- [ ] **Step 4: Run `python -m pytest -q`;** update only tests that assert `schema_version == "0.3.0"` or the document schema.
- [ ] **Step 5: Commit** — "Schema 0.4.0: a level on every heading line"

---

### Task 6: Paragraphs with line spans

**Files:**
- Create: `src/ragnaroq/semantic/__init__.py`, `src/ragnaroq/semantic/paragraphs.py`
- Test: `tests/test_paragraphs.py`

**Interfaces:**
- Consumes: `ragnaroq.export.units`, `structure.levels`.
- Produces:
  - `@dataclass(frozen=True) class Span: line: Line; start: int; end: int; offset: int` (the line's `text[start:end]` sits at `offset` in the paragraph's text).
  - `@dataclass class Para: kind: Literal["heading", "body", "table", "image"]; page: int; text: str; spans: list[Span]; breaks: tuple[int, ...]; level: int; heading_path: tuple[str, ...]; table: Table | None; figure: Figure | None; status: Status` (status = lowest of its lines).
  - `paragraphs(document: Document) -> list[Para]`: calls `structure.levels(document)` first; one `Para` per `Unit` of `units(document)`, in order; text is the unit's non-excluded lines' `text.strip()` joined with one space (tables: `table_markdown(unit.table)`; images: the figure's description); `breaks` are the offsets of lines that start with a list marker (`•`, `◦`, `▪`, `-`, `*`, `–`, or a numbering form from `shape.numbering`); `heading_path` is the texts of the enclosing headings by level (a heading of level n closes every heading of level ≥ n).
  - `locate(para: Para, start: int, end: int) -> list[tuple[Line, int, int]]`: the lines and `(from_char, to_char)` covering `para.text[start:end]`.

- [ ] **Step 1: Write the failing tests**

```python
def test_a_pdf_paragraphs_printed_lines_are_joined_with_one_space():
    para = only_body(paragraphs(pdf_with_lines(["The deduction may be", "claimed until 31 March."])))
    assert para.text == "The deduction may be claimed until 31 March."
    assert [(s.line.text, s.offset) for s in para.spans] == [("The deduction may be", 0), ("claimed until 31 March.", 21)]

def test_locate_maps_a_range_back_to_lines_and_characters():
    para = only_body(paragraphs(pdf_with_lines(["The deduction may be", "claimed until 31 March."])))
    start = para.text.index("may")
    assert [(l.text, a, b) for l, a, b in locate(para, start, start + 15)] == [
        ("The deduction may be", 14, 20), ("claimed until 31 March.", 0, 7)]

def test_heading_paths_close_at_the_same_or_a_higher_level():
    paths = [p.heading_path for p in paragraphs(read_md(b"# A\n\n## B\n\nx\n\n## C\n\ny\n\n# D\n\nz\n")) if p.kind == "body"]
    assert paths == [("A", "B"), ("A", "C"), ("D",)]

def test_excluded_lines_are_left_out_and_a_document_with_none_left_has_no_paragraphs(): ...
```

- [ ] **Step 2–4:** Run (fail), implement, run (pass).
- [ ] **Step 5: Commit** — "Paragraphs with the lines and characters they come from"

---

### Task 7: Finders for dates, periods, amounts, references and file mentions

**Files:**
- Create: `src/ragnaroq/semantic/find.py`
- Test: `tests/test_find.py`

**Interfaces:**
- Produces: `@dataclass(frozen=True) class Match: text: str; value: str | dict; start: int; end: int`; `dates(text) -> list[Match]`, `periods(text)`, `amounts(text)`, `references(text)`, `file_mentions(text)` (each sorted by `start`, no overlaps).

Values, exactly as the spec's section 2 table: dates ISO only when unambiguous (month name, ISO order, or day over 12), else `""`; periods `"2026-04-01/2027-03-31"` only for `FY`, `AY` and `financial year` forms, else `"2026/2027"` (a year span) or `"2026-Q3"` / `"2026-H1"`; amounts `{"amount": 150000, "currency": "INR"}` (`₹`, `Rs.`, `INR` are INR; `$`/`USD` USD; `€`/`EUR` EUR; `£`/`GBP` GBP; `lakh` ×100,000, `crore` ×10,000,000, `million`, `billion`) or `{"percent": 5}`; references normalised to lower-case unit + number (`"section 10(46)"`, `"article 21"`, `"notification 1/2026"`, `"income-tax act, 1961"`), web addresses as written; file mentions match `[\w.-]+(?:/[\w.-]+)*\.(pdf|docx|xlsx|pptx|html|htm|md|json|xml|csv|tsv)` with `value` `""`.

- [ ] **Step 1: Write the failing tests**, parametrised over the spec's examples, for example:

```python
@pytest.mark.parametrize("text, value", [
    ("on 5th January, 2026", "2026-01-05"), ("January 5, 2026", "2026-01-05"),
    ("2026-01-05", "2026-01-05"), ("13/01/2026", "2026-01-13"), ("05/01/2026", "")])
def test_dates(text, value): assert [m.value for m in dates(text)] == [value]

@pytest.mark.parametrize("text, value", [
    ("for 2026-27", "2026/2027"), ("FY 2025-26", "2025-04-01/2026-03-31"),
    ("AY 2026-27", "2026-04-01/2027-03-31"), ("Q3 2026", "2026-Q3"), ("calendar year 2026", "2026/2026")])
def test_periods(text, value): ...

def test_amounts_read_indian_grouping_and_scale_words():
    assert [m.value for m in amounts("₹1,50,000 and Rs. 2 lakh and 5%")] == [
        {"amount": 150000, "currency": "INR"}, {"amount": 200000, "currency": "INR"}, {"percent": 5}]

def test_references(): assert [m.value for m in references("under section 10(46) and Notification No. 1/2026")] == [
    "section 10(46)", "notification 1/2026"]

def test_finders_are_linear_on_hostile_text():
    started = time.perf_counter()
    for finder in (dates, periods, amounts, references, file_mentions):
        finder("1" * 200_000 + "/" + "a." * 100_000)
    assert time.perf_counter() - started < 2
```

- [ ] **Step 2–4:** Run (fail), implement, run (pass).
- [ ] **Step 5: Commit** — "Find dates, periods, amounts, references and file names in text"

---

### Task 8: Structure detection

**Files:**
- Create: `src/ragnaroq/semantic/shape.py`
- Test: `tests/test_shape.py`

**Interfaces:**
- Consumes: `Para`, `paragraphs` (Task 6); `find.references` (Task 7).
- Produces:
  - `numbering(text: str) -> str | None`: the paragraph's leading number token (`"1."`, `"1.1"`, `"(1)"`, `"1)"`, `"(a)"`, `"(i)"`, `"I."`, `"Article 5"`, `"Section 3."`, `"Clause 4"`), or None.
  - `@dataclass class Definition: term: str; para: Para; start: int; end: int`.
  - `@dataclass class Shape: numbered: list[Para]; provisos: list[Para]; explanations: list[Para]; qa: list[tuple[Para, list[Para]]]; definitions: list[Definition]; records: list[str]` (record parent paths) `; headings: int; heading_depth: int; tables: int; table_chars: int; body_chars: int; chars: int; figures: int; slides: int`.
  - `shape(paras: list[Para], document: Document) -> Shape`, following the spec's detection rules (section 2), including: a numbered paragraph counts only in a run of at least two in sequence; provisos and explanations attach to the numbered paragraph before them; questions per the three forms, with `A.`/`A:`/`Ans.`/`Answer` answers; definitions by sentence form or a two-column `Term`/`Definition` (`Word`/`Meaning`) table; records from `JsonAnchor.pointer` / `XmlAnchor.path` per the spec, with a JSON array of same-key flat objects counted as a table, not records.

- [ ] **Step 1: Write the failing tests**: one per rule, with the spec's examples, including:

```python
def test_a_lone_numbered_line_in_prose_is_not_a_numbered_paragraph(): ...
def test_a_proviso_and_an_explanation_attach_to_the_paragraph_before():
    s = shape_of(md("1. Every person shall file.\n\nProvided that a minor need not.\n\nExplanation.— A minor is under 18.\n\n2. Next.\n"))
    assert len(s.numbered) == 2 and len(s.provisos) == 1 and len(s.explanations) == 1
def test_a_rhetorical_question_in_prose_is_not_a_qa_pair_unless_an_answer_follows(): ...
def test_definitions_record_the_term_as_written():
    assert [d.term for d in shape_of(md('"Assessee" means a person.\n\nWord means x.\n')).definitions] == ["Assessee", "Word"]
def test_a_json_array_of_flat_same_key_objects_is_a_table_not_records(): ...
```

- [ ] **Step 2–4:** Run (fail), implement, run (pass).
- [ ] **Step 5: Commit** — "Detect numbered paragraphs, provisos, questions, definitions and records"

---

### Task 9: Metadata models, `describe`, and the metadata schema

**Files:**
- Create: `src/ragnaroq/semantic/model.py`, `src/ragnaroq/semantic/describe.py`, `schemas/metadata.schema.json`
- Modify: `src/ragnaroq/cli.py` (`schema --metadata`)
- Test: `tests/test_describe.py`

**Interfaces:**
- Consumes: Tasks 6–8. This task creates `semantic/rules.py` with `kind_of`, the kind thresholds (`FAQ_MIN_PAIRS`, `FAQ_MIN_SHARE`, `NUMBERED_MIN`, `NUMBERED_WITH_NOTE_MIN`, `SECTIONED_MIN_DEPTH`, `SECTIONED_MIN_HEADINGS`, `SECTIONED_MIN_CHARS`, `TABLE_SHARE`) and `DOC_LIST_LIMIT`, all with the values listed in Task 10; Task 10 adds the rest.
- Produces (in `semantic.model`, all `extra="forbid"`):
  - `METADATA_VERSION = "0.4.0"`.
  - `Grounded(value: str | dict = "", text: str, line_ids: list[str], cite: str, status: Status)`; `GroundedDefinition(Grounded)` with `term: str`.
  - `Kind(value: Literal[...the seven kinds], because: list[str])`.
  - `StructureCounts(headings, heading_depth, numbered_paragraphs, provisos, explanations, qa_pairs: int = 0; definitions: list[GroundedDefinition] = []; tables: int = 0; table_share: float = 0.0; figures, slides, records: int = 0)`.
  - `Length(chars: int, parts: int)`; `ScriptShare(script: str, share: float, excluded: bool)`; `Trust(accepted: float, flagged: float, blocked: float)`.
  - `Chunking(strategy: str, chosen_by: Literal["rules", "override"], parameters: dict, reasons: list[str], filters: list[str])`.
  - `DocumentMetadata(schema_version: str = METADATA_VERSION, path, format, sha256: str, title: Grounded, kind: Kind, dates, periods, amounts, references, files: list[Grounded] = [], structure: StructureCounts, length: Length, scripts: list[ScriptShare], trust: Trust, chunking: Chunking | None = None, flags: list[Flag] = [])`.
  - `describe(document: Document, *, path: str | None = None) -> DocumentMetadata` in `semantic.describe` (`chunking` None; `files` empty: mentions are resolved by the folder, Task 16).
  - `mentions(document: Document) -> list[Grounded]` in `semantic.describe`: file-name candidates, `value` `""`.
  - `rules.kind_of(shape: Shape, document: Document) -> Kind` with the spec's section 3 kind table; `because` lists the counts that decided it, as `"numbered_paragraphs = 14"`.

Grounding: each value's `line_ids` and `cite` come from `locate` over the match's range (`cite` built like a chunk's citation: `_citation` in `export/chunks.py`, moved to a public `citation(lines: list[Line]) -> str` in `ragnaroq.export.chunks` and used by both); `status` is the lowest of those lines'.

- [ ] **Step 1: Write the failing tests**

```python
def test_every_value_names_lines_whose_text_holds_it(): ...  # over every demo and generated file
def test_a_date_on_a_flagged_line_is_flagged(): ...
def test_a_list_over_1000_values_is_cut_and_flagged():
    meta = describe(md_document("\n\n".join(f"On 1 January {2000 + i % 20}." for i in range(1500))))
    assert len(meta.dates) == 1000 and [f.rule for f in meta.flags] == ["metadata.list-limit"]
def test_an_empty_document_has_a_valid_entry_titled_by_its_file_name():
    meta = describe(empty_document("empty.csv"))
    assert meta.title.text == "empty.csv" and meta.kind.value == "spreadsheet" and meta.length.chars == 0
def test_kinds_of_the_generated_documents():
    assert {name: kind_of_file(name) for name in GENERATED_NAMES} == {
        "notice.pdf": "numbered", "faq.docx": "faq", "manual.md": "sectioned", "manual.html": "sectioned",
        "contract.docx": "numbered", "deck.pptx": "slides", "rates.xlsx": "spreadsheet",
        "records.json": "data", "table.json": "data", "essay.md": "prose"}
def test_the_metadata_schema_file_is_in_sync(): ...  # like the document schema's test
```

- [ ] **Step 2–4:** Run (fail), implement, run (pass). `ragnaroq schema --metadata` prints `DocumentMetadata.model_json_schema()` as `schemas/metadata.schema.json` holds it.
- [ ] **Step 5: Commit** — "metadata.json for a document: grounded dates, periods, amounts, references and structure"

---

### Task 10: Strategy rules and overrides

**Files:**
- Modify: `src/ragnaroq/semantic/rules.py`
- Create: `src/ragnaroq/semantic/overrides.py`
- Test: `tests/test_rules.py`, `tests/test_overrides.py`

**Interfaces:**
- Produces in `semantic.rules`: the constants `SINGLE_MAX_CHARS = 1500`, `FAQ_MIN_PAIRS = 3`, `FAQ_MIN_SHARE = 0.4`, `NUMBERED_MIN = 3`, `NUMBERED_WITH_NOTE_MIN = 2`, `SECTIONED_MIN_DEPTH = 2`, `SECTIONED_MIN_HEADINGS = 3`, `SECTIONED_MIN_CHARS = 3000`, `TABLE_SHARE = 0.6`, `DEFAULT_MIN_CHARS = 200`, `MAX_CHARS_LIMITS = (200, 4000)`, `REFERENCE_OVERRUN = 0.5`, `CHUNK_LIST_LIMIT = 50`, `DOC_LIST_LIMIT = 1000`, `STRATEGIES` (the eight names, in the spec's order), `CHUNKING_VERSION = "1"`; and `choose(meta: DocumentMetadata, config: Config, override: Override | None = None) -> Chunking` with the spec's section 3 strategy table. `parameters` always holds `max_chars`, `min_chars`, `parent` and `keep_with_parent` (fixed per strategy: `numbered-paragraphs` → `"section"`, `["proviso", "explanation"]`; `question-answer` → `"question"`, `["answer"]`; `sections` → `"section"`, `[]`; `table-rows` → `"table"`, `[]`; `slides` → `"slide"`, `["notes"]`; `records` → `"record"`, `[]`; `single`, `sentences` → `"document"`, `[]`). `reasons` name the fields, e.g. `"kind = numbered (numbered_paragraphs = 14, provisos = 3)"`, `"length.chars = 9120 > 1500"`; with an override, `"override: faq/*.pdf"`. `filters` lists `dates`, `periods`, `references` when the document has any.
- Produces in `semantic.overrides`: `@dataclass(frozen=True) class Override: pattern: str; strategy: str; max_chars: int | None = None; min_chars: int | None = None` with `key() -> str` (stable JSON of its fields); `class OverrideError(ValueError)`; `load(text: str) -> list[Override]` (in file order); `load_file(path: Path) -> list[Override]` (`[]` when the file is missing); `match(overrides: list[Override], path: str) -> Override | None` (`fnmatch.fnmatchcase` on the POSIX path, first match).

- [ ] **Step 1: Write the failing tests**

```python
def test_each_generated_document_gets_its_strategy(): ...  # notice.pdf -> numbered-paragraphs, faq.docx -> question-answer, ...
def test_a_short_document_is_single_whatever_its_kind(): ...
def test_a_data_file_that_is_one_table_is_cut_as_table_rows(): ...
def test_an_unknown_strategy_names_its_line():
    with pytest.raises(OverrideError, match="line 3: unknown strategy 'faqs'"):
        load('[chunking]\n"a/*.md" = "sentences"\n"faq/*.pdf" = "faqs"\n')
def test_keys_and_limits_are_checked():
    for bad in ('{ strategy = "sentences", rows = 2 }', '{ strategy = "sentences", max_chars = 100 }',
                '{ strategy = "sentences", max_chars = 800, min_chars = 500 }'):
        with pytest.raises(OverrideError, match="line 2"):
            load(f'[chunking]\n"x/*.md" = {bad}\n')
def test_invalid_toml_stops_with_its_line():
    with pytest.raises(OverrideError, match="line 2"):
        load('[chunking]\n"x" = = 1\n')
def test_a_file_without_a_chunking_table_has_no_overrides():
    assert load('[other]\nx = 1\n') == []
def test_the_first_matching_pattern_wins(): ...
```

The line of a key is found by searching the raw text for the key in quotes at a line start; a TOML syntax error reports `tomllib.TOMLDecodeError`'s line.

- [ ] **Step 2–4:** Run (fail), implement, run (pass).
- [ ] **Step 5: Commit** — "Choose a chunking strategy by rule, or by ragnaroq.toml"

---

### Task 11: Sentences and packing

Do not start before Task 3 is committed.

**Files:**
- Create: `src/ragnaroq/chunking/__init__.py`, `src/ragnaroq/chunking/sentences.py`
- Test: `tests/test_sentences.py`

**Interfaces:**
- Produces:
  - `sentences(text: str, breaks: Sequence[int] = ()) -> list[tuple[int, int]]`: contiguous ranges covering `text` (whitespace between sentences belongs to the earlier one). Ends per the spec's section 5, plus `।` and `॥` whatever follows; never after the abbreviation list, an initial (`A.`), or inside a number; every offset in `breaks` starts a sentence.
  - `@dataclass(frozen=True) class Cut: start: int; end: int; long: bool`.
  - `pack(text: str, ranges: list[tuple[int, int]], max_chars: int, *, protected: frozenset[int] = frozenset(), overlap: bool = True) -> list[Cut]`: greedy over sentence ranges, each cut at most `max_chars`; with `overlap`, each cut after the first starts with the previous cut's last sentence; a sentence over `max_chars` is cut at the last space before the limit (at exactly `max_chars` when there is none), each part `long=True`; a sentence whose index is in `protected` (it holds a reference) may make its cut run to `max_chars * (1 + REFERENCE_OVERRUN)` before it is cut as a long sentence.

- [ ] **Step 1: Write the failing tests**

```python
def test_abbreviations_initials_and_numbers_do_not_end_a_sentence():
    text = "Under Sec. 10(46). the limit is Rs. 1.5 lakh, e.g. for Mr. A. Kumar. Next one."
    assert [text[a:b].strip() for a, b in sentences(text)] == [
        "Under Sec. 10(46). the limit is Rs. 1.5 lakh, e.g. for Mr. A. Kumar.", "Next one."]
def test_a_lower_case_start_is_not_a_new_sentence_but_a_danda_is():
    text = "see p. 4. then more। अगला वाक्य॥ end"
    assert [text[a:b].strip() for a, b in sentences(text)] == ["see p. 4. then more।", "अगला वाक्य॥", "end"]
def test_pack_repeats_one_sentence_and_never_cuts_one(): ...
def test_a_word_longer_than_max_chars_is_cut_at_the_limit_and_marked_long():
    cuts = pack("x" * 5000, [(0, 5000)], 1200)
    assert [(c.end - c.start, c.long) for c in cuts] == [(1200, True)] * 4 + [(200, True)]
def test_a_protected_sentence_may_overrun_by_half(): ...
def test_sentences_and_pack_are_linear():
    text = "A b. " * 100_000
    started = time.perf_counter(); pack(text, sentences(text), 1200)
    assert time.perf_counter() - started < 3
```

- [ ] **Step 2–4:** Run (fail), implement, run (pass).
- [ ] **Step 5: Commit** — "Split text between sentences, with one sentence of overlap"

---

### Task 12: Chunk format 0.4.0 and the `single` and `sentences` strategies

**Files:**
- Create: `src/ragnaroq/chunking/pieces.py`, `src/ragnaroq/chunking/strategies.py`
- Modify: `src/ragnaroq/export/chunks.py`, `tests/test_chunks.py`, `tests/test_vector_stores.py`
- Test: `tests/test_strategies.py`, `tests/test_chunks.py`

**Interfaces:**
- Consumes: `paragraphs`, `locate` (Task 6), `describe` (Task 9), `choose` (Task 10), `sentences`, `pack` (Task 11), `find.dates/periods/references` (Task 7).
- Produces:
  - `@dataclass class Piece: kind: Literal["text", "table", "image", "definition", "summary"]; parts: list[tuple[Para, int, int]]` (ranges of paragraphs' text) `; text: str; embedding_text: str; parent: str` (a stable key of the parent, e.g. the parent's first line id) `; parent_cite: str; heading_path: tuple[str, ...]; part_of: tuple[int, int] | None; rules: list[str]; terms: list[str]`.
  - `@dataclass class StrategyContext: document: Document; paras: list[Para]; shape: Shape; meta: DocumentMetadata; choice: Chunking; config: Config` with `max_chars` and `min_chars` properties read from `choice.parameters`.
  - `pieces(context: StrategyContext) -> list[Piece]` in `chunking.strategies`: dispatch on `choice.strategy` through `STRATEGY_FUNCTIONS: dict[str, Callable[[StrategyContext], list[Piece]]]`; this task registers `single` and `sentences` (the others raise `NotImplementedError` until Tasks 13–14); tables and images in any strategy go through `table_pieces` / `image_pieces` (images and tables as in 0.3 for now).
  - `to_chunks(document, config, include=("accepted", "flagged"), *, path=None, choice: Chunking | None = None, meta: DocumentMetadata | None = None) -> list[dict]`: describes and chooses when not given; builds chunks from pieces.
  - `METADATA_KEYS`: 0.3's 19 keys, then `strategy`, `parent_id`, `parent_cite`, `heading_path`, `dates`, `date_first`, `date_last`, `periods`, `references`, `terms`, `scripts`, `part_of`; `LIST_KEYS = ("dates", "periods", "references", "terms")`.
  - Anchors: an entry of a line covered only in part has `from_char`/`to_char` (PDF entries per line id: `"chars": {"<line id>": [from, to]}` on the page entry; other formats `from_char`/`to_char` on the `from`/`to` dicts). Chunk id: `uuid5(NAMESPACE, f"{file_id}|{sha256}|{what}")` where `what` is the line ids joined by `,`, each followed by `:from-to` when partial.
  - `parent_id = uuid5(NAMESPACE, f"{file_id}|parent|{piece.parent}")`.

- [ ] **Step 1: Write the failing tests**

```python
def test_every_chunk_has_the_0_4_keys_and_list_keys_are_never_empty():
    for chunk in all_chunks(DEMO) + all_chunks(GENERATED):
        assert list(chunk["metadata"]) == list(METADATA_KEYS)
        for key in LIST_KEYS:
            values = chunk["metadata"][key]
            assert isinstance(values, list) and values and all(isinstance(v, str) for v in values) and len(values) <= 50
        assert all(isinstance(v, (str, int, float, bool, list)) for v in chunk["metadata"].values())
def test_a_long_paragraph_is_split_between_sentences_with_one_repeated(): ...  # essay.md, notice.pdf
def test_two_pieces_of_one_line_have_different_ids_and_their_char_ranges(): ...
def test_dates_and_bounds_are_filters(): ...  # date_first/date_last as YYYYMMDD ints, [""] when none
def test_the_same_document_gives_the_same_chunks_twice(): ...
def test_a_document_with_no_lines_gives_no_chunks(): ...  # empty csv, all lines excluded, every PDF page failed
def test_a_piece_under_min_chars_joins_its_neighbour_under_the_same_parent():
    chunks = chunks_of_md("Short line.\n\n" + "A long sentence about rates. " * 30)
    assert len(chunks) == 1 or all(c["metadata"]["chars"] >= 200 for c in chunks[:-1])
```

Update `tests/test_chunks.py`'s `EXPECTED_KEYS`, `assert_flat` (lists allowed for `LIST_KEYS` only) and any 0.3 expectation that a long paragraph is one chunk; `tests/test_vector_stores.py` loads the new shape into qdrant.

- [ ] **Step 2–4:** Run (fail), implement, run `python -m pytest -q` (pass).
- [ ] **Step 5: Commit** — "Chunk format 0.4.0: strategies, parents, filters; single and sentences"

---

### Task 13: `numbered-paragraphs`, `sections` and `question-answer`

**Files:**
- Modify: `src/ragnaroq/chunking/strategies.py`
- Test: `tests/test_strategies.py`

**Interfaces:**
- Consumes: Task 12's `Piece`, `StrategyContext`, `STRATEGY_FUNCTIONS`; `Shape` (Task 8).
- Produces: the three strategies registered in `STRATEGY_FUNCTIONS`, per the spec's section 3. Every piece of a split unit repeats its anchor text (the paragraph's number token, or the question) at its start; `part_of` is `(n, m)`; units under `min_chars` join the next unit under the same parent (a question-answer pair never joins another).

- [ ] **Step 1: Write the failing tests**

```python
def test_a_proviso_and_an_explanation_are_in_their_paragraphs_chunk(): ...  # notice.pdf
def test_every_piece_of_a_split_numbered_paragraph_starts_with_its_number(): ...
def test_a_question_is_never_apart_from_its_answer():
    for chunk in chunks_of(GENERATED / "faq.docx"):
        assert chunk["text"].startswith("Q.")
def test_sections_carry_their_heading_path(): ...  # manual.md: "Part II › Section 4"
def test_a_heading_with_nothing_under_it_joins_the_next_section(): ...  # Review Focus 4
```

- [ ] **Step 2–4:** Run (fail), implement, run (pass).
- [ ] **Step 5: Commit** — "Chunk by numbered paragraph, section and question"

---

### Task 14: `table-rows`, `slides`, `records`, and the additions

**Files:**
- Modify: `src/ragnaroq/chunking/strategies.py`, `src/ragnaroq/export/chunks.py` (move `_table_groups` here or call it)
- Test: `tests/test_strategies.py`

**Interfaces:**
- Consumes: Task 12–13; `_table_groups` from `export/chunks.py` for non-PDF tables.
- Produces:
  - `table-rows`, `slides`, `records` registered; every table, in any strategy, cut as `table-rows` cuts it: non-PDF tables by `_table_groups` with `embedding_text` labelling each cell `"<sheet or caption> › <header>: <value>"`, PDF tables one piece each; one `summary` piece per table: `"<sheet or caption>: <n> rows; columns <h1>, <h2>, …; range <cite>"`.
  - Additions in every strategy: a `definition` piece per definition (`terms` = its term); each piece's `terms` lists the defined terms (case-insensitive whole words) it uses; a piece never mixes scripts (a change of `Line.scripts` starts a new piece with the same parent); `scripts` key holds the piece's script joined by `+`.

- [ ] **Step 1: Write the failing tests**

```python
def test_table_chunks_keep_the_grid_text_and_embed_labelled_cells(): ...  # rates.xlsx
def test_every_table_has_one_summary_chunk(): ...
def test_a_pdf_table_stays_one_chunk(): ...
def test_one_chunk_per_slide_with_its_notes(): ...  # deck.pptx
def test_records_are_labelled_with_their_path_and_small_ones_join(): ...  # records.json
def test_each_definition_is_its_own_chunk_and_others_list_the_terms_they_use(): ...  # contract.docx
def test_no_chunk_mixes_scripts(): ...  # a page with Latin and Devanagari paragraphs
```

- [ ] **Step 2–4:** Run (fail), implement, run `python -m pytest -q` (pass; the demo table tests of 0.3 still pass).
- [ ] **Step 5: Commit** — "Chunk tables, slides and records; definitions, terms and one script per chunk"

---

### Task 15: `convert` writes `metadata.json`; `--strategy`

**Files:**
- Modify: `src/ragnaroq/pipeline.py`, `src/ragnaroq/cli.py`
- Test: `tests/test_convert.py`

**Interfaces:**
- Consumes: `describe`, `choose`, `to_chunks(..., choice=, meta=)`, `Override`.
- Produces: `write(document, out, config=None, images=None, *, override: Override | None = None, path: str | None = None) -> dict[str, Path]` writes `document.json`, `document.md`, `chunks.jsonl` and `metadata.json` (`DocumentMetadata` with `chunking` set; JSON with `indent=2`); returns the four paths, key `"metadata"` added. `ragnaroq convert FILE --out OUT --strategy NAME` (choices `STRATEGIES`) passes `Override(pattern="*", strategy=NAME)`.

- [ ] **Step 1: Write the failing tests**

```python
def test_convert_writes_metadata_with_its_chunking_choice(tmp_path): ...  # metadata.json validates as DocumentMetadata, chunking.chosen_by == "rules"
def test_strategy_overrides_the_rules(tmp_path): ...  # --strategy sentences on notice.pdf: chosen_by == "override", every chunk's strategy == "sentences"
```

- [ ] **Step 2–4:** Run (fail), implement, run (pass).
- [ ] **Step 5: Commit** — "convert: metadata.json beside document.json, and --strategy"

---

### Task 16: The folder: keys, re-chunking, 0.3 upgrade, folder files

**Files:**
- Modify: `src/ragnaroq/folder.py`, `src/ragnaroq/config.py`
- Create: `src/ragnaroq/semantic/folder.py`
- Test: `tests/test_folder.py`

**Interfaces:**
- Consumes: Tasks 9, 10, 15; `READING_VERSIONS` (Task 5).
- Produces:
  - `Config.folder_reading_sha256() -> str`: `reading_sha256()` computed without `chunk_chars`. `reading_sha256()` itself is unchanged, so `evaluate`'s stored readings stay valid.
  - In `folder.py`: `MANIFEST_VERSION = "0.4.0"`; `FileEntry.chunking: str = ""` (the chunking key) and `FileEntry.strategy: str = ""`; `_settings` without `"ragnaroq"` and with `"reading": config.folder_reading_sha256()`; `_hash` adds `"reading_version": READING_VERSIONS.get(fmt, "")`; `_chunking_key(config: Config, override: Override | None) -> str` = sha256 of `{"chunk_chars", "override": override.key() or "", "version": CHUNKING_VERSION}`; `_hash_0_3(settings: dict, config: Config, fmt: str) -> str`, the 0.3 formula (with `"ragnaroq": "0.3.0"` and `config.reading_sha256()`), used only to recognise 0.3 entries.
  - `_load_manifest` accepts `"0.3.0"` manifests (entries get `chunking=""`).
  - A carried entry (same bytes, same reading key, or a 0.3 entry whose `settings == _hash_0_3(...)` for a format whose `READING_VERSIONS` value is `"1"`) whose `chunking` key differs is re-chunked by `_rechunk(held: _State, entry: FileEntry, override: Override | None, config: Config) -> FileEntry`: copy its folder's `document.json` and `images/` into a private temporary folder, load the document, `write(document, out, config, override=override, path=entry.path)`, install, and return the entry with new `settings`, `chunking`, `strategy`, `chunk_ids`, `counts`.
  - Each file's folder also holds `mentions.json` (`list[Grounded]` from `describe.mentions`).
  - The run loads `ragnaroq.toml` from the folder root by descriptor before the walk (`overrides.load`, an `OverrideError` becomes `FolderError` with its message); `ragnaroq.toml` itself is skipped like any unsupported file.
  - `semantic.folder.folder_metadata(entries: list[DocumentMetadata], mentions: dict[str, list[Grounded]], catalogue: list[CatalogueEntry]) -> FolderMetadata`; `CatalogueEntry(path, title, kind, status)`; `Link(from_: str = Field(alias="from"), to, text, line_ids, cite)`; `GlossaryEntry(term, path, line_ids, cite, status)`; `FolderMetadata(schema_version, catalogue, documents, links, glossary)`; `plan_entries(entries) -> list[PlanEntry]`; `plan_markdown(entries) -> str`. Links per spec section 2: a mention whose name equals another file's path or base name; a numbered-instrument reference whose value appears in another document's normalised title references.
  - `_run` writes `metadata.json`, `chunking-plan.json`, `chunking-plan.md` before `manifest.json`, after `chunks.jsonl` and `changes.json`; failed and skipped files appear only in `catalogue`.

- [ ] **Step 1: Write the failing tests**

```python
def test_a_run_writes_the_master_metadata_and_the_plan(tmp_path): ...  # validates; one plan entry per ok/review file; plan md has the per-strategy table
def test_changing_chunk_chars_rechunks_without_reading_again(tmp_path, monkeypatch):
    root = small(tmp_path, **GENERATED_SUBSET); run(root)
    calls = count_reads(monkeypatch)  # patch folder._make to count
    run(root, replace(Config(), chunk_chars=600))
    assert calls() == 0  # nothing read again
    moved = changes(root)
    assert moved["chunks_added"] and moved["chunks_removed"]  # new chunks replace the old ones
def test_an_override_rechunks_only_the_files_it_matches(tmp_path): ...
def test_a_0_3_index_keeps_its_pdf_readings(tmp_path): ...  # write a 0.3 manifest and folders (from a fixture made with the 0.3 formula), run 0.4: the PDF is not read again, the docx is
def test_links_and_glossary(tmp_path): ...  # a.md mentions b.md -> one link; contract.docx definitions in the glossary
def test_a_bad_ragnaroq_toml_stops_the_run_with_its_line(tmp_path): ...
def test_two_runs_give_identical_folder_files(tmp_path): ...
```

- [ ] **Step 2–4:** Run (fail), implement, run `python -m pytest -q` (pass).
- [ ] **Step 5: Commit** — "Index: metadata.json and the plan for a folder; rechunk without reading again"

---

### Task 17: Tune on the generated half, then score the held-out half once

**Files:**
- Modify: `src/ragnaroq/semantic/rules.py` (thresholds only, if the tuning half calls for it)
- Create: `references/results/2026-11-27-chunking-generated.md`, `references/results/2026-11-27-chunking-public.md`
- Modify: `references/chunking/README.md`
- Test: `tests/test_gates.py`

- [ ] **Step 1: Run on the tuning half**

Run: `ragnaroq evaluate references/chunking/generated --chunking references/chunking/generated.questions.jsonl --out /tmp/gen`
Expected: exit 0. If a generated document gets the wrong kind or strategy (Task 10's test says which it should), change only the thresholds in `rules.py`, re-run, and record each change and why in the README. Copy the report to `references/results/2026-11-27-chunking-generated.md`.

- [ ] **Step 2: Freeze the rules** — commit "Chunking rules frozen after tuning on the generated set".

- [ ] **Step 3: Score the held-out half once**

Run: `ragnaroq evaluate references/chunking/public --chunking references/chunking/public.questions.jsonl --out /tmp/pub`
Copy the report to `references/results/2026-11-27-chunking-public.md`. The gate: 0.4's MRR ≥ 0.3's and 0.4's top-3 ≥ 0.3's. If it fails, stop and report to the owner with the numbers; do not change rules or questions to pass it.

- [ ] **Step 4: Write the gate tests** in `tests/test_gates.py`, over every document of the demo folder, the generated set and the public set:

```python
def test_no_chunk_cuts_a_word(): ...          # each chunk's text starts and ends at a word boundary of its paragraph, unless "chunk.long-sentence" is in its rules
def test_no_chunk_cuts_a_sentence(): ...      # each chunk's parts start and end on sentences() boundaries, the same exceptions
def test_provisos_stay_with_their_paragraph(): ...
def test_every_metadata_value_is_grounded(): ...
def test_two_runs_are_identical(): ...
```

- [ ] **Step 5: Run `python -m pytest -q`; commit** — "Chunking measured: tuned on generated documents, scored once on public ones"

---

### Task 18: Release 0.4.0

**Files:**
- Modify: `pyproject.toml`, `src/ragnaroq/__init__.py` (version `0.4.0`), `CHANGELOG.md`, `README.md`, `docs/chunking.md`, `docs/vector-databases.md`, `docs/roadmap.md`

- [ ] **Step 1: Docs** per the spec's section 8: the 0.4.0 changelog (added, changed, measured with both reports' numbers, known limits: PDF tables not cut into rows, no LangExtract fields, held-out documents committed instead of fetched); README sections on `metadata.json`, strategies, `ragnaroq.toml`, `evaluate --chunking`; `docs/chunking.md` corrected (1,200 characters, doubtful lines kept, the second opinion and entity filters moved to the next release); `docs/vector-databases.md` (the new keys; a filter on `dates`/`periods` for Pinecone `$in`, Chroma `$contains` with the Chroma release that first takes list metadata, Qdrant `MatchAny`, pgvector `?|`; `[""]` for empty lists); `docs/roadmap.md` (0.4 done by rule; LangExtract and the second opinion next).
- [ ] **Step 2: Verify** — `ruff check .`, `python -m pytest -q` on Python 3.12 and 3.13, `ragnaroq evaluate references/demo --out /tmp/demo` (every exact file `ok`), `python -m build` and `twine check dist/*`.
- [ ] **Step 3: Commit** — "Release 0.4.0: metadata and chunking strategies, by rule"; push the branch. Opening the PR waits for the owner.
