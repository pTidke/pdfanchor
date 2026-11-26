# Third Reader Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A local model, PaddleOCR-VL-1.6, reads the crop of each scanned line that Tesseract and EasyOCR disagree on, and a 2-of-3 rule with four checks settles the line or keeps it for a person.

**Architecture:**
- A new stage, `third.read_disputed`, runs after the first decision. It redraws only the pages with disputed lines at 300 dpi and reads each line's crop with a `LineReader`, caching results by crop. It stores the result as a `vlm:` reading.
- `policy.decide` then applies the third-reader rule. It can accept a line, and replace its text with the agreeing OCR reader's text, recorded in a new `Line.resolution`.
- The model sits behind the `LineReader` interface, in an optional extra.

**Tech Stack:** Python 3.12, pydantic 2, pdfplumber, Pillow, pytest; optional `torch>=2.15` and `transformers>=5.24,<6`.

**Spec:** [docs/superpowers/specs/2026-11-24-third-reader-design.md](../specs/2026-11-24-third-reader-design.md)

## Global Constraints

- **Lint and tests:** `ruff check .` and `python -m pytest -q` must pass. Lines are at most 110 characters (`pyproject.toml`). CI never installs torch or the model.
- **Imports:** the core install never imports `torch`, `transformers` or `huggingface_hub`. They are imported only inside `ocr/paddle_vl.py` functions.
- **Model:** `PaddlePaddle/PaddleOCR-VL-1.6`, revision `132c45f52c0a26ed5a6b2f6bd8e3985794cc27dd`, prompt `OCR:`, greedy decoding, one line at a time. Load it with `trust_remote_code=False`, and never fetch the repository's `*.py` files.
- **Names and versions:**
  - reading source `vlm:paddleocr-vl`, written as `f"vlm:{reader.name}"` with `name = "paddleocr-vl"`;
  - policy version `"0.3"`, schema version `"0.2.0"`, package version `0.2.0`.
- **Defaults:**
  - crop: 300 dpi, 2.0 points of padding left and right, none above or below, a 12-pixel white frame;
  - per line: 160 new tokens and 20 seconds;
  - per document: no time budget unless one is set;
  - rule thresholds: dissent letter ratio 1.15, neighbour run of 4 words.
- **Off by default.** It runs only with `--third-reader`, or when a reader is passed to `convert()`, and downloads only with `--vlm-download`.
- **Scope:** scanned pages only. A line is a candidate only when it is flagged and its only flags are `crosscheck.disagreement`.
- **Commits:** every commit message ends with:
  ```
  Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
  Claude-Session: https://claude.ai/code/session_01AfdfCKQJ1Kc3LDgTC4Jj5p
  ```
- **Git:** push only to `claude/trusting-hypatia-l5qg4h`. Open no pull request unless the owner asks.

**Three settled details that differ in form from the spec:**
1. **Flags.** A held-back line gets one flag per refusal, with rule `crosscheck.third-reader.<step>`, where `<step>` is one of `no-match`, `validators`, `omission`, `underscore` or `neighbour`. Separate rule ids let `evaluate` count each check without parsing messages.
2. **Repeating output.** It is detected as output that reaches the token limit without finishing. A loop never finishes, and legitimately repeated form text, which does finish, is not refused.
3. **Model folder.** The default is the Hugging Face cache snapshot path, computed without importing anything. `--vlm-models DIR` is a plain folder holding the files.

## Review Focus

1. **Empty readings.** A third reading that is empty, or only layout marks, next to an empty second reading must never settle a line. Task 2: `test_an_empty_reading_never_settles_a_line`.
2. **No native bfloat16.** A CPU without native bfloat16 must get float32, recorded in the components. Task 4: `test_number_format_follows_the_hardware`.
3. **Redraw fails.** A page that cannot be drawn again within its limits keeps its first decisions, with a page flag. Task 3: `test_a_page_that_cannot_be_drawn_again_keeps_its_first_decisions`.
4. **Missing model files.** `--third-reader` with no model files and no `--vlm-download` exits 1 before any page is read, and the message names `--vlm-download`. Task 5: `test_a_missing_model_stops_the_command_before_reading`.
5. **Old cache.** An evaluate cache written by 0.1.x (schema 0.1.0) is read again, not loaded or crashed on. Task 1: `test_a_cache_from_another_schema_version_is_read_again`.

---

### Task 1: Output contract 0.2.0, with a resolution record

**Files:**
- Modify: `src/ragnaroq/model.py`
- Modify: `src/ragnaroq/evaluate.py` (`_converted`)
- Regenerate: `schemas/document.schema.json`
- Test: `tests/test_convert.py`, `tests/test_evaluate.py`

**Interfaces:**
- Produces:
  - `model.Resolution(rule: str, agreed: list[str], text_from: str)`;
  - `Line.resolution: Resolution | None = None`;
  - `model.SCHEMA_VERSION == "0.2.0"`;
  - `Document.schema_version: Literal["0.2.0"]`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_convert.py
def test_a_resolution_records_which_readings_agreed(native_pdf):
    document = convert(native_pdf, engines=[])
    line = next(document.lines())
    line.resolution = Resolution(rule="crosscheck.third-reader", agreed=["ocr:two", "vlm:x"], text_from="ocr:two")
    again = Document.model_validate_json(document.model_dump_json())
    assert again.schema_version == "0.2.0"
    assert next(again.lines()).resolution.text_from == "ocr:two"

# tests/test_evaluate.py
def test_a_cache_from_another_schema_version_is_read_again(clean_reference, tmp_path, monkeypatch):
    folder, _, truth_path = clean_reference
    engines = _readers(reference.load(truth_path), LINES, LINES)
    evaluate.run(folder, engines=engines, cache=tmp_path / "cache")
    monkeypatch.setattr(evaluate, "SCHEMA_VERSION", "0.1.0")
    evaluate.run(folder, engines=engines, cache=tmp_path / "cache")
    assert len(list((tmp_path / "cache").glob("*.json"))) == 2
```

- [ ] **Step 2: Run them to make sure they fail**

Run: `python -m pytest tests/test_convert.py::test_a_resolution_records_which_readings_agreed tests/test_evaluate.py::test_a_cache_from_another_schema_version_is_read_again -q`
Expected: FAIL. `Resolution` cannot be imported, and `evaluate` has no `SCHEMA_VERSION`.

- [ ] **Step 3: Implement**
  - **`model.py`:**
    - Add the `Resolution(Model)` class, with docstring: "How a disputed line was settled by rule. `text_from` names the reading whose text `text` now holds."
    - Add `resolution: Resolution | None = None` to `Line`, after `flags`.
    - Change `Line.text`'s description to "NFC-normalised `raw`, unless `resolution.text_from` names another reading, whose text it then holds."
    - Set `SCHEMA_VERSION = "0.2.0"` and `Literal["0.2.0"]`.
  - **`evaluate.py`:** import `SCHEMA_VERSION` from `.model` and add it as a fourth part of the `_converted` cache key.
  - **Schema file:** regenerate with `python -m ragnaroq.cli schema > schemas/document.schema.json`.

- [ ] **Step 4: Run the whole suite**

Run: `python -m pytest -q && ruff check .`
Expected: everything passes, including `test_published_schema_matches_the_models`.

- [ ] **Step 5: Commit** with the message `Output contract 0.2.0: record how a disputed line was settled`.

---

### Task 2: The third-reader rule (policy 0.3)

**Files:**
- Modify: `src/ragnaroq/crosscheck.py`, `src/ragnaroq/evaluate.py` (imports), `src/ragnaroq/config.py` (`Policy`), `src/ragnaroq/policy.py`
- Create: `tests/test_third_rule.py`

**Interfaces:**
- Consumes: `Resolution` and `Line.resolution` (Task 1).
- Produces:
  - in `crosscheck.py`:
    - `TYPOGRAPHY`, `LEADERS` and `canonical(text: str) -> str`, moved from `evaluate.py`, which imports them back so `evaluate.canonical` still works;
    - `LAYOUT_MARKS` and `without_marks(text: str) -> str`;
    - `shares_words(a: str, b: str, n: int) -> bool`;
  - in `policy.py`: `VLM = "vlm:"` and `THIRD = "crosscheck.third-reader"`;
  - in `Policy`: `version = "0.3"`, `third_reader_letter_ratio: float = 1.15`, `third_reader_neighbour_words: int = 4`;
  - flag rules `crosscheck.third-reader.{no-match,validators,omission,underscore,neighbour}`, all severity `review`.

- [ ] **Step 1: Write the failing tests** in `tests/test_third_rule.py`.

Use two helpers. `scanned_document(*rows)` builds a one-page scanned `Document` with one line per `(first, second, third)` row:
- id `p1-l{i+1}` and order `i`;
- box `Box(x0=72, top=100 + 20 * i, x1=500, bottom=112 + 20 * i)`;
- `raw = text = first` and `source = "ocr:one"`;
- readings `ocr:one`, `ocr:two` and, when `third` is not None, `vlm:paddleocr-vl`.

`decided(*rows)` builds that document, runs `policy.decide(document, Policy())` and returns its lines.

```python
DUE, MISREAD = "The due date is 31st July 2026.", "The due date is 3lst July 2026."

def test_agreeing_with_the_first_reader_settles_the_line():
    (line,) = decided((DUE, MISREAD, DUE))
    assert line.status == "accepted" and line.flags == [] and line.text == DUE
    assert line.resolution == Resolution(
        rule="crosscheck.third-reader", agreed=["ocr:one", "vlm:paddleocr-vl"], text_from="ocr:one"
    )

def test_agreeing_with_the_second_reader_takes_its_text_and_keeps_the_raw_reading():
    (line,) = decided((MISREAD, DUE, DUE))
    assert line.status == "accepted" and line.text == DUE and line.raw == MISREAD
    assert line.resolution.text_from == "ocr:two"

def test_layout_marks_are_not_text():
    (line,) = decided((DUE, MISREAD, "☐ " + DUE + " |"))
    assert line.status == "accepted"

def test_no_exact_match_keeps_the_line_for_a_person():
    (line,) = decided((DUE, MISREAD, "The due date is 31 July 2026."))
    assert line.status == "flagged" and line.resolution is None
    assert {f.rule for f in line.flags} == {"crosscheck.disagreement", "crosscheck.third-reader.no-match"}

def test_an_agreeing_reading_must_pass_the_validators():
    (line,) = decided(("The earlier order dated 31 March, 2025 is withdrawn.", BAD_DATE, BAD_DATE))
    assert "crosscheck.third-reader.validators" in {f.rule for f in line.flags}

def test_a_reader_that_saw_more_text_holds_the_line_back():
    seen = "Designee Designee's Phone Personal identification"
    more = "Designee Designee's name Phone no. Personal identification number (PIN)"
    (line,) = decided((seen, more, seen))
    assert "crosscheck.third-reader.omission" in {f.rule for f in line.flags}

def test_an_underscore_inside_a_word_holds_the_line_back():
    (line,) = decided(("See 2026-02 IRB#NOT-2026-3.", "See 2026-02_IRBANOT 2026-3.", "See 2026-02 IRB#NOT-2026-3."))
    assert "crosscheck.third-reader.underscore" in {f.rule for f in line.flags}

def test_text_that_belongs_to_the_next_line_holds_the_line_back():
    longer = "Personal Address name on your social security card? If not, to ensure you get"
    lines = decided(
        ("Enter Address Does your name match the", "Enter Address Does your name match the", None),
        ("Personal name on your social security", longer, longer),
        ("card? If not, to ensure you get", "card? If not, to ensure you get", None),
    )
    assert "crosscheck.third-reader.neighbour" in {f.rule for f in lines[1].flags}

def test_an_empty_reading_never_settles_a_line():
    (line,) = decided(("...", "", "☐"))
    assert line.status == "flagged" and line.resolution is None

def test_a_line_with_another_flag_is_left_to_a_person():
    (line,) = decided((BAD_DATE, "The earlier order dated 31 March, 2025 is withdrawn.", BAD_DATE))
    assert line.resolution is None
    assert not any(f.rule.startswith("crosscheck.third-reader") for f in line.flags)

def test_the_third_reading_is_not_compared_like_the_others():
    (line,) = decided((DUE, DUE, "something else entirely"))
    assert line.status == "accepted" and line.flags == [] and line.resolution is None

def test_deciding_again_starts_from_the_first_reader():
    document = scanned_document((MISREAD, DUE, DUE))
    policy.decide(document, Policy())
    first = document.model_dump()
    policy.decide(document, Policy())
    assert document.model_dump() == first
    line = next(document.lines())
    line.readings = [r for r in line.readings if not r.source.startswith("vlm:")]
    policy.decide(document, Policy())
    assert line.text == MISREAD and line.status == "flagged" and line.resolution is None

def test_shares_words():
    assert shares_words("a card? If not, to b", "card? If not, to ensure", 4)
    assert not shares_words("card? If not", "card? If not, to", 4)
```

`BAD_DATE` comes from `conftest`; it is already known to trip a validator. If `test_an_agreeing_reading_must_pass_the_validators` passes for another reason, check that `validators.run(BAD_DATE, ocr=True)` is non-empty.

- [ ] **Step 2: Run them to make sure they fail**

Run: `python -m pytest tests/test_third_rule.py -q`
Expected: FAIL on imports (`shares_words`) and on the assertions.

- [ ] **Step 3: Move and add the helpers in `crosscheck.py`**
  - **Move:** `TYPOGRAPHY`, `LEADERS` and `canonical` move here unchanged. `evaluate.py` imports them.
  - **`LAYOUT_MARKS`:** `re.compile(r"[☐□■☑☒▢◻◼▪●○◯]|(?:^|\s)\|(?=\s|$)")`.
  - **`without_marks`:** replaces each match with a space and collapses whitespace.
  - **`shares_words`:** true when the two canonical texts, split on spaces, have `n` consecutive words in common.

- [ ] **Step 4: Implement the rule in `policy.py`**
  - **Reset.** At the start of `decide`, for every line: `line.text = unicodedata.normalize("NFC", line.raw)` and `line.resolution = None`, before flags are cleared.
  - **Pairwise comparison.** In `decide_line`, `others` leaves out readings whose source starts with `VLM`.
  - **After each line's `decide_line`.** When the page is scanned, the line has a `vlm:` reading, the line is flagged, and all its flags are `crosscheck.disagreement`, call `_third_reader(line, neighbours, policy)`. `neighbours` is the NFC `raw` of the lines just before and after it by `order` on the same page.
  - **The steps,** in order; the first refusal adds its flag and stops:

```python
V = without_marks(vlm reading text)
candidates = [(line.source, NFC(line.raw))] + [(r.source, r.text) for r in other OCR readings]  # first reader first
R = first candidate with letters(text) and canonical(text) == canonical(V)  # none, or V has no letters -> "no-match"
validators.run(R.text, ocr=True) or validators.run(V, ocr=True)             # -> "validators"
any(len(letters(d)) > ratio * max(len(letters(R.text)), len(letters(V))) for d in dissenting candidates)  # -> "omission"
any(re.search(r"\w_\w", t) for t in [V, *candidate texts])                  # -> "underscore"
any(shares_words(R.text, n, policy.third_reader_neighbour_words) for n in neighbours)  # -> "neighbour"
```

  - **On success:**
    - `line.flags = []` and `status = "accepted"`;
    - `line.text = NFC(R.text)`;
    - `line.resolution = Resolution(rule=THIRD, agreed=[R.source, vlm source], text_from=R.source)`.
  - **Flag messages** say what was seen, for example `vlm:paddleocr-vl reads "<V>"; no other reading matches it exactly.`
  - **Policy:** bump `version` to `"0.3"` with a comment, and add the two fields.

- [ ] **Step 5: Run the whole suite**

Run: `python -m pytest -q && ruff check .`
Expected: everything passes. Nothing changes for documents without a `vlm:` reading.

- [ ] **Step 6: Commit** with the message `Policy 0.3: settle a disputed line when a third reading agrees exactly`.

---

### Task 3: The third-reader stage

**Files:**
- Modify: `src/ragnaroq/ocr/__init__.py`, `src/ragnaroq/config.py`
- Create: `src/ragnaroq/third.py`
- Modify: `tests/conftest.py` (add `FakeLineReader`)
- Create: `tests/test_third_stage.py`

**Interfaces:**
- Consumes: `PageReader` and `BudgetExceeded` (`isolation.py`); `Config`.
- Produces:
  - in `ocr/__init__.py`:
    - `class LineUnreadable(RuntimeError)`;
    - `LineReader` Protocol with `name: str`, `version() -> str`, `prepare() -> None` and `read_line(image, max_new_tokens: int, seconds: float) -> str`. `version()` must work without loading the model, and `prepare()` is idempotent.
  - in `config.py`:
    - `@dataclass(frozen=True) class ThirdReaderSettings`, with fields `dpi: int = 300`, `pad_points: float = 2.0`, `frame_pixels: int = 12`, `max_new_tokens: int = 160`, `line_seconds: float = 20.0` and `budget_seconds: float | None = None`;
    - `Config.third_reader: ThirdReaderSettings`;
    - `"third_reader"` is added to the keys `reading_sha256` ignores.
  - in `third.py`:
    - `candidates(document: Document) -> dict[int, list[Line]]`: page number to lines in `order`;
    - `crop(image, box: Box, settings: ThirdReaderSettings)`: returns a PIL image;
    - `read_disputed(document: Document, data: bytes, config: Config, reader: LineReader, cache: str | Path | None = None, clock=time.monotonic) -> None`.
  - Flag rules: page flag `third-reader.unread` and document flag `third-reader.budget`, both severity `review`.

- [ ] **Step 1: Write the failing tests** in `tests/test_third_stage.py`.

`FakeLineReader(answers)` in `conftest.py`:
- `name = "fake"` and `version()` returns `"test"`;
- it counts `prepare()` calls in `prepared`;
- `read_line` appends the image to `crops`, pops the next answer, and raises it if it is an exception.

Start every test from `convert(scanned_pdf, engines=[first, second])`, with the boxes from `test_scanned_lines_need_two_agreeing_readers`:
- `box_a = (150, 200, 900, 260)` and `box_b = (150, 360, 1100, 420)` at 200 dpi;
- `second` misreads the due date, or both lines where a test needs two.

```python
def test_only_disputed_lines_are_read_and_stored_as_a_vlm_reading(scanned_pdf):
    title, due = document.pages[0].lines
    third.read_disputed(document, scanned_pdf.read_bytes(), Config(), reader := FakeLineReader([DUE]))
    assert len(reader.crops) == 1 and reader.prepared == 1
    assert due.readings[-1] == Reading(source="vlm:fake", text=DUE)
    assert not any(r.source.startswith("vlm:") for r in title.readings)

def test_a_crop_is_the_line_box_at_300_dpi_in_a_white_frame(scanned_pdf):
    # crop() on the due line's box: width round((x1 - x0 + 4) * 300 / 72) + 24, height round((bottom - top) * 300 / 72) + 24
    # (each within 1 pixel); pixel (0, 0) is white

def test_a_line_the_reader_gives_up_on_keeps_its_first_decision(scanned_pdf):
    # answers [LineUnreadable("stopped after 20 seconds")]: no vlm reading; one page flag "third-reader.unread"
    # whose message contains "p1-l2" and "stopped after 20 seconds"

def test_the_time_budget_leaves_the_rest_for_a_person(scanned_pdf):
    # both lines disputed, Config(third_reader=ThirdReaderSettings(budget_seconds=0.0)): no crops read,
    # reader.prepared == 0, document flag "third-reader.budget" whose message contains "2 disputed lines"

def test_a_page_that_cannot_be_drawn_again_keeps_its_first_decisions(scanned_pdf, monkeypatch):
    # monkeypatch PageReader.read to raise BudgetExceeded("budget.page-time", "too slow"):
    # no vlm reading, page flag "third-reader.unread" containing "too slow", reader.crops == []

def test_readings_and_refusals_are_cached_by_crop(scanned_pdf, tmp_path):
    # run once with cache=tmp_path, then again on a fresh copy of the document with
    # FakeLineReader([AssertionError("read again")]): same vlm reading, reader.prepared == 0.
    # Same again with a LineUnreadable answer: the second run adds the same page flag without reading.

def test_nothing_is_loaded_when_nothing_is_disputed(scanned_pdf):
    # second reader agrees everywhere: reader.prepared == 0 and no crops

def test_running_twice_replaces_the_third_reading(scanned_pdf):
    # two runs with [DUE] then ["other"]: exactly one vlm reading on the line, with text "other"
```

- [ ] **Step 2: Run them to make sure they fail**

Run: `python -m pytest tests/test_third_stage.py -q`
Expected: FAIL. `third` does not exist.

- [ ] **Step 3: Implement `third.py`**
  - **Candidates:** lines on `kind == "scanned"` pages that are not excluded, have status `flagged`, and have only `crosscheck.disagreement` flags.
  - **Redrawing:** one `PageReader(data, replace(config, dpi=s.dpi, low_res_render_dpi=s.dpi))` for the run. Read with `read(number, crosscheck=True)` and use `.image`. If it raises `BudgetExceeded` or `RuntimeError`, add the page flag `third-reader.unread` with the error message and go on.
  - **Crop:**
    - box in points times `s.dpi / 72`;
    - `pad_points` added left and right, none above or below;
    - clamped to the image;
    - pasted on a white canvas `frame_pixels` larger on each side.
  - **Cache key:** `sha256(crop.mode | crop.size | crop.tobytes() | reader.version() | json of the settings without budget_seconds)`.
  - **Cache file:** `<cache>/<key[:24]>.json`, holding `{"text": ...}` or `{"unreadable": reason}`.
  - **Reading:** a cached entry is used without touching the budget.
  - **Budget:**
    - check it before each uncached line: `clock() - start >= s.budget_seconds` stops reading;
    - lines not reached are counted for the document flag, worded `"{n} disputed lines were not read by the third reader: its time budget of {b:g} seconds ran out."`
  - **Loading:** `reader.prepare()` is called once, just before the first uncached read.
  - **Storing:** before adding the new `Reading(source=f"vlm:{reader.name}", text=text)`, remove any earlier `vlm:` reading.
  - **Failures:**
    - `LineUnreadable`: cached, and a page flag `f"{line.id}: {reason}"`;
    - any other exception: a page flag naming its type, not cached.

- [ ] **Step 4: Run the whole suite**

Run: `python -m pytest -q && ruff check .`
Expected: everything passes.

- [ ] **Step 5: Commit** with the message `Third-reader stage: read the crops of disputed scanned lines, with limits and a cache`.

---

### Task 4: The PaddleOCR-VL reader, an optional extra

**Files:**
- Create: `src/ragnaroq/ocr/paddle_vl.py`
- Modify: `pyproject.toml` (extra `vlm = ["torch>=2.15", "transformers>=5.24,<6"]`)
- Create: `tests/test_paddle_vl.py`

**Interfaces:**
- Consumes: `LineReader`, `LineUnreadable` and `EngineUnavailable` (Task 3).
- Produces:
  - `PaddleOcrVl(model_dir: str | None = None, download: bool = False)`, implementing `LineReader`, with `name = "paddleocr-vl"`;
  - module constants `REPO`, `REVISION`, `FILES: dict[str, str]` (name to SHA-256) and `PROMPT = "OCR:"`;
  - `number_format(device: str, cpu_flags: frozenset[str], cuda_bf16: bool = False) -> str`, returning `"bfloat16"` or `"float32"`;
  - `verify(folder: Path) -> None`;
  - `check_finished(new_tokens: int, max_new_tokens: int, elapsed: float, seconds: float) -> None`.

`FILES` holds exactly these files and hashes, measured at the pinned revision:

```
added_tokens.json         f59f889088e0fe21c523e7cf121bb6dca3b0bb148cb7159fbb4572c74dfc5644
chat_template.jinja       2f27812dab7f333e471884e0c803d807f11953d5453140dfb1aaba234f872bc8
config.json               ce7f4565f8b1db78532ad5d1b9ebe55c2139d49bd4cb04778b580a08a598f171
generation_config.json    a6701d78ab3b4d972307cdec3b69d4c13f46e0d5140514f50ab7d84259324b94
model.safetensors         85a479d506a11e724e7285d395c551be69f41dbc16b6342d3cacfb189aed71db
preprocessor_config.json  111872ab1e8bb7fd040ac5087bfced7ab8f011f02139b088cba294964c3b1d0e
processor_config.json     1568858960a9760c54431dae693a6152e601ff55cdf6d2eab97a4a99958faea0
special_tokens_map.json   d3a125c03103deb2acaf7730791bdbbf196f620e5a2213b664511ff9b4b25bab
tokenizer.json            c8a215a59183d0d0781adc33bacd3ce6162716f7fd568fb30234a74d69803a7d
tokenizer.model           34ef7db83df785924fb83d7b887b6e822a031c56e15cff40aaf9b982988180df
tokenizer_config.json     1f979337347cc0cb72a6282d8a23ed183539aa81a87a906f022aee2bab83c7c5
```

- [ ] **Step 1: Write the failing tests**

```python
def test_number_format_follows_the_hardware():
    assert number_format("cpu", frozenset({"avx512_bf16"})) == "bfloat16"
    assert number_format("cpu", frozenset({"amx_bf16"})) == "bfloat16"
    assert number_format("cpu", frozenset({"avx2", "avx512f"})) == "float32"
    assert number_format("cuda", frozenset(), cuda_bf16=True) == "bfloat16"
    assert number_format("cuda", frozenset(), cuda_bf16=False) == "float32"

def test_missing_files_name_the_download_switch(tmp_path):
    with pytest.raises(EngineUnavailable, match="--vlm-download"):
        PaddleOcrVl(model_dir=str(tmp_path)).prepare()

def test_a_changed_file_is_refused(tmp_path):
    for name in FILES:
        (tmp_path / name).write_text("x")
    with pytest.raises(EngineUnavailable, match="added_tokens.json"):
        verify(tmp_path)

def test_no_code_from_the_model_repository_is_fetched():
    assert FILES and not any(name.endswith(".py") for name in FILES)

def test_an_unfinished_line_is_given_up():
    check_finished(40, 160, 3.0, 20.0)  # no error
    with pytest.raises(LineUnreadable, match="20 seconds"):
        check_finished(40, 160, 20.0, 20.0)
    with pytest.raises(LineUnreadable, match="160 tokens"):
        check_finished(160, 160, 3.0, 20.0)

@pytest.mark.skipif(not os.environ.get("RAGNAROQ_VLM_MODELS"), reason="set RAGNAROQ_VLM_MODELS to a model folder")
def test_the_real_model_reads_a_clean_line():
    # Draw "The due date is 31st July 2026." in black on a white 1400x120 image with
    # ImageFont.load_default(size=48), then:
    reader = PaddleOcrVl(model_dir=os.environ["RAGNAROQ_VLM_MODELS"])
    reader.prepare()
    assert reader.read_line(image, 160, 20.0) == "The due date is 31st July 2026."
```

- [ ] **Step 2: Run them to make sure they fail**

Run: `python -m pytest tests/test_paddle_vl.py -q`
Expected: FAIL on import.

- [ ] **Step 3: Implement `paddle_vl.py`**
  - **Folder.**
    - Default: `$HF_HOME/hub/models--PaddlePaddle--PaddleOCR-VL-1.6/snapshots/<REVISION>`, where `HF_HOME` defaults to `~/.cache/huggingface`.
    - With `model_dir`: that folder itself.
  - **`prepare()`,** in this order:
    1. Look for each file in `FILES` with `pathlib` only. If any are missing and `download` is false, raise `EngineUnavailable`, naming the folder, `--vlm-download`, the repo, `REVISION[:12]` and "1.9 GB".
    2. Import `torch` and `transformers`. On `ImportError`, raise `EngineUnavailable("… pip install 'ragnaroq[vlm]' …")`.
    3. Download when needed: `huggingface_hub.snapshot_download(REPO, revision=REVISION, allow_patterns=list(FILES), local_dir=model_dir)`, or `cache_dir` left default when there is no `model_dir`.
    4. Run `verify(folder)`, which hashes each file in 1 MB blocks and raises `EngineUnavailable` naming the first mismatch.
    5. Load `AutoProcessor` and `AutoModelForImageTextToText` from the folder, with `trust_remote_code=False` and `dtype` from `number_format`, then `.eval()` and move to the device.
    6. A second call does nothing.
  - **Hardware.**
    - Device: `"cuda"` if `torch.cuda.is_available()`, else `"cpu"`.
    - CPU flags: from `/proc/cpuinfo` on Linux, an empty set elsewhere (which gives float32).
    - `version()` imports torch to decide these, without loading the model, and returns `f"{REPO}@{REVISION[:12]} {number_format} {device}"`.
  - **`read_line(image, max_new_tokens, seconds)`:**
    - build `[{"role": "user", "content": [{"type": "image", "image": image}, {"type": "text", "text": PROMPT}]}]`;
    - call `processor.apply_chat_template(..., add_generation_prompt=True, tokenize=True, return_dict=True, return_tensors="pt")`;
    - under `torch.inference_mode()`, call `generate(**inputs, max_new_tokens=..., do_sample=False, max_time=seconds)`;
    - pass the new-token count and the elapsed time to `check_finished`;
    - decode the new tokens with `skip_special_tokens=True`, collapse whitespace, and return the text.
  - **Install note:** add the extra to `pyproject.toml`. In the README's "Install and run", show:
    - installing the CPU build of torch first: `pip install torch --index-url https://download.pytorch.org/whl/cpu`;
    - then `pip install "ragnaroq[vlm]"`.

- [ ] **Step 4: Run the tests, then try the real model if it is at hand**

Run: `python -m pytest tests/test_paddle_vl.py -q && ruff check .`
Expected: 5 passed and 1 skipped.

If the model is available, run `RAGNAROQ_VLM_MODELS=<folder> python -m pytest tests/test_paddle_vl.py -q`. Expected: 6 passed.

In this cloud environment, the spike's copy is at `$S/hf/hub/models--PaddlePaddle--PaddleOCR-VL-1.6/snapshots/132c45f52c0a26ed5a6b2f6bd8e3985794cc27dd`, where `S` is the session scratchpad. The spike's venv `$S/eovenv` has torch and transformers. Downloads here need `HF_HUB_DISABLE_XET=1`.

- [ ] **Step 5: Commit** with the message `PaddleOCR-VL reader at a pinned, checked revision (ragnaroq[vlm])`.

---

### Task 5: Wire it into convert, evaluate and the command line

**Files:**
- Modify: `src/ragnaroq/pipeline.py`, `src/ragnaroq/evaluate.py`, `src/ragnaroq/cli.py`
- Test: `tests/test_third_stage.py`, `tests/test_evaluate.py`, `tests/test_convert.py`

**Interfaces:**
- Consumes: `third.read_disputed` (Task 3), `PaddleOcrVl` (Task 4) and `policy.decide` (Task 2).
- Produces:
  - `convert(source, *, config=None, engines=None, filename=None, third_reader: LineReader | None = None, third_cache: str | Path | None = None) -> Document`;
  - `evaluate.run(folder, *, config=None, engines=None, progress=None, cache=None, third_reader: LineReader | None = None) -> Report`;
  - new `Tally` int fields: `third_settled`, `third_settled_wrong`, `third_settled_critical_wrong` and `third_held_{no_match,validators,omission,underscore,neighbour}`;
  - new `LineScore` fields: `settled: bool = False` and `held_back: str | None = None`;
  - command-line options on `convert` and `evaluate`: `--third-reader`, `--vlm-models DIR`, `--vlm-download` and `--third-reader-budget SECONDS`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_third_stage.py
def test_convert_settles_a_disputed_scanned_line(scanned_pdf):
    document = convert(scanned_pdf, engines=[first, second], third_reader=FakeLineReader([DUE]))
    due = document.pages[0].lines[1]
    assert due.status == "accepted" and due.resolution.agreed == ["ocr:one", "vlm:fake"]
    assert document.engine.components["vlm:fake"] == "test"

def test_a_corrected_line_reaches_the_markdown(scanned_pdf):
    # first misreads, second reads DUE, the third reader answers DUE: to_markdown(document) contains DUE, not MISREAD

# tests/test_evaluate.py
def test_the_report_counts_what_the_third_reader_settled_and_held_back(clean_reference, tmp_path):
    # first reader misreads LINES[1] ("3lst"), second reads LINES: run with third_reader=FakeLineReader([LINES[1]])
    # and cache=tmp_path / "cache": total.third_settled == 1, third_settled_wrong == 0;
    # (tmp_path / "cache" / "third") holds one file; evaluation.md has a "Third reader" section.
    # Again with FakeLineReader(["something else"]) and a fresh cache: total.third_held_no_match == 1.

# tests/test_convert.py
def test_a_missing_model_stops_the_command_before_reading(scanned_pdf, tmp_path, capsys, monkeypatch):
    monkeypatch.setattr("ragnaroq.pipeline.PageReader", None)  # any read would fail loudly
    code = main(["convert", str(scanned_pdf), "--out", str(tmp_path / "out"),
                 "--third-reader", "--vlm-models", str(tmp_path / "none")])
    assert code == 1 and "--vlm-download" in json.loads(capsys.readouterr().err)["error"]
```

- [ ] **Step 2: Run them to make sure they fail**

Run: `python -m pytest tests/test_third_stage.py tests/test_evaluate.py tests/test_convert.py -q`
Expected: the new tests FAIL, on the unknown keyword argument and the unknown option.

- [ ] **Step 3: Implement**
  - **`convert`.**
    - After `structure.apply` and `policy.decide`, when `third_reader` is given, call `third.read_disputed(document, data, config, third_reader, third_cache)` and then `policy.decide` again.
    - Add `components[f"vlm:{third_reader.name}"] = third_reader.version()`.
  - **`evaluate.run`.**
    - After `_converted`, so the stored reading stays the base reading, when `third_reader` is given, call `read_disputed(document, pdf_path.read_bytes(), config, third_reader, Path(cache) / "third" if cache else None)` and `policy.decide`.
    - Add the `vlm:` components to `names`.
  - **Scoring changes.**
    - `_line_score`'s `others` leaves out `vlm:` readings.
    - `eligible` ignores flags starting with `crosscheck.third-reader`.
    - `settled` is `line.resolution is not None`.
    - `held_back` is the `<step>` of a `crosscheck.third-reader.<step>` flag.
  - **Tally and report.**
    - `score` fills the new `Tally` fields from `settled`, `exact`, `critical_ok` and `held_back`.
    - `to_markdown` adds a "Third reader" section when any of them is non-zero: settled, of which wrong, of which wrong on a critical token, and held back per check.
  - **Command line.**
    - With `--third-reader`, build `PaddleOcrVl(model_dir=args.vlm_models, download=args.vlm_download)` and call `prepare()` before any conversion. On `EngineUnavailable`, print the JSON error and exit 1.
    - `--third-reader-budget` sets `ThirdReaderSettings.budget_seconds`.
    - `evaluate` passes the reader to `evaluate.run`.

- [ ] **Step 4: Run the whole suite**

Run: `python -m pytest -q && ruff check .`
Expected: everything passes.

- [ ] **Step 5: Try it end to end on a few real crops** (only where the model and EasyOCR are installed). Run `ragnaroq evaluate` with `--third-reader` on the IRS references from `references/README.md`, limited to `f1040` pages 1–2. Then check:
  - the "Third reader" section appears;
  - no line it settled is wrong on a critical token.

- [ ] **Step 6: Commit** with the message `Switch the third reader on with --third-reader in convert and evaluate`.

---

### Task 6: The release gate, on pages the rule has never seen

**Files:**
- Modify: `references/README.md` (a new section, "Held-out set (0.2.0)"), `docs/evaluating.md` (the third reader and its numbers)

**Interfaces:**
- Consumes: the `ragnaroq` command line with `--third-reader` (Task 5).

- [ ] **Step 1: Fetch the held-out PDFs and record their checksums**

```bash
for f in fw9 f941 f1040sc p596 i1040gi; do curl -sO "https://www.irs.gov/pub/irs-pdf/$f.pdf"; done
sha256sum fw9.pdf f941.pdf f1040sc.pdf p596.pdf i1040gi.pdf
```

If a file no longer exists, use another IRS form or publication not in `references/README.md`, and record it.

- [ ] **Step 2: Make the references**, with all four profiles:
  - `fw9`, `f941` and `f1040sc`: pages `1-2`;
  - `p596`: pages `2-4`;
  - `i1040gi`: pages `10-12`.

Write them to `held-out/refs`.

- [ ] **Step 3: Evaluate without the third reader, then with it, on the same base readings**

```bash
ragnaroq evaluate held-out/refs --out held-out/without --easyocr-models ~/easyocr-models
mkdir -p held-out/with && cp -r held-out/without/cache held-out/with/cache
ragnaroq evaluate held-out/refs --out held-out/with --easyocr-models ~/easyocr-models --third-reader
```

Expected: both runs succeed. The second reads only the disputed lines' crops; at about 3.5 seconds each, run it in the background. Point `HF_HOME` at the folder that holds the model (in this environment `$S/hf`), or pass `--vlm-models`.

- [ ] **Step 4: Apply the gate, with the rule frozen.** It passes only if both hold in `held-out/with/evaluation.json`:
  - `third_settled_critical_wrong == 0`;
  - the accepted-but-wrong rate is not above `held-out/without`'s.

If it fails, **stop**. Report the failing lines to the owner, and change no threshold or check on this set.

- [ ] **Step 5: Record the results.**
  - **`references/README.md`:** sources, checksums, commands, and both runs' headline numbers.
  - **`docs/evaluating.md`:** how `--third-reader` changes `evaluate`, and its report section.

- [ ] **Step 6: Commit** with the message `Measure the third reader on a held-out set`.

---

### Task 7: Release 0.2.0

**Files:**
- Modify: `pyproject.toml`, `src/ragnaroq/__init__.py` (version `0.2.0`), `CHANGELOG.md`, `README.md`, `docs/roadmap.md`

- [ ] **Step 1: Docs.**
  - **`CHANGELOG.md`:** a 0.2.0 entry. Added: the third reader, how to switch it on, its cost in time. Changed: policy 0.3 and schema 0.2.0. Measured: the IRS, code-table and held-out numbers.
  - **`README.md`:**
    - "How it decides": the third-reader rule in plain words;
    - "Status": updated;
    - "Install and run": already done in Task 4.
  - **`docs/roadmap.md`:**
    - milestones become 0.2 third reader (done), 0.3 folders and formats, 0.4 semantic layer, 0.5 review UI, 0.6 serve;
    - in "Trust and repair": line crops only, and GLM-OCR comparison later.

- [ ] **Step 2: Bump the version** to `0.2.0` in `pyproject.toml` and `__init__.py`.

- [ ] **Step 3: Verify**

Run: `python -m pytest -q && ruff check . && python -m ragnaroq.cli --version`
Expected: everything passes, and it prints `0.2.0`.

- [ ] **Step 4: Commit and push** with the message `Release 0.2.0: a third reader for disputed lines`, then `git push -u origin claude/trusting-hypatia-l5qg4h`. Open no pull request unless the owner asks.
