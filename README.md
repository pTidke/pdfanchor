# RAGnarök

**The end of bad RAG.** Point it at your files and get text a language model can trust: every line anchored to its place in the source, and judged before it is used.

Today it reads PDFs. The plan is any folder of files, ready for retrieval and served to any model over MCP ([roadmap](docs/roadmap.md)).

```bash
pip install ragnarok          # once released; until then, install from this repository
ragnarok convert notification.pdf --out out/
```

## RAG 'n' AROQ

In Old Norse, *ragna rök* is "the fate of the gods", and *rök* means fate, or judgement. Passing judgement on every line is what this tool is for. So: **RAG 'n' AROQ**, like rock 'n' roll. RAG, and the four jobs that make it trustworthy. It is spelt with a K and heard with a Q.

| | Job | Norse name | Why the name fits |
| --- | --- | --- | --- |
| **A** | **Assess**: every line gets a verdict, *accepted*, *flagged* or *blocked*, by rules. | **The Norns** | Three Norns weave every fate at the roots of Yggdrasil. Three verdicts. |
| **R** | **Read**: the text layer, and later Word, Excel, PowerPoint, JSON, XML and more. | **Runes** | Odin hung nine nights on the world tree to learn to read the runes. |
| **O** | **OCR**: read what is only an image, with independent readers checking each other. | **Heimdall** | The watchman of the gods sees a hundred leagues, by night as well as by day. |
| **Q** | **Questions**: ask your folder and get answers that cite their lines. | **The Völva** | In the *Völuspá*, Odin questions a seeress, and her answer foretells Ragnarök itself. |

And around them:

| Name | Part | Why |
| --- | --- | --- |
| **Mímir** | `metadata.json`, the master file of what every document is and says | The keeper of the well of wisdom. Odin gave an eye for one drink. |
| **Huginn** | A local language model's second opinion (metadata, chunking plan) | Odin's raven of *thought*. |
| **Muninn** | The search index | Odin's raven of *memory*. |
| **Forseti** | The review screen, where a person settles what the readers disagree on | The god who settles every dispute. |
| **Bifröst** | The MCP server: the bridge from your folder to any model, local or on your server | The rainbow bridge between the worlds. |
| **Yggdrasil** | The `.ragnarok/` folder that ties a processed folder together | The world tree that connects the nine worlds. |

Norse names are for the big parts; the plumbing has plain names (`ragnarok.ocr`, `ragnarok.policy`). Wherever a Norse name appears, its plain meaning appears with it.

## How it reads a PDF

It is an extraction engine, not only OCR. Character recognition is one stage, used when a page has no usable text layer and as an independent check when it does.

```
PDF ─▶ preflight ─▶ text layer + geometry ─▶ OCR (scan, or cross-check)
    ─▶ rule validators ─▶ decision policy ─▶ structure
    ─▶ document.json   everything, with boxes, readings and flags
       document.md     for a language model to read
       chunks.jsonl    for a vector index, with citations
```

The core uses no language model and makes no network call. Model stages planned on the roadmap are optional and local by default.

## Status

This is a working skeleton: small, tested, and end to end. It was called `pdfanchor` until version 0.0.4. It grew out of a private extraction lab built for Indian tax documents, which still holds far more experience than this code does. `docs/porting-from-the-lab.md` lists what moves across and in what order.

The goal is wider than PDFs: point ragnarok at a folder of any files and get a checked, described and indexed knowledge layer, served to any model through MCP. [docs/roadmap.md](docs/roadmap.md) sets out the stages, the principles and the milestones. [docs/chunking.md](docs/chunking.md) explains how the chunking strategy for each document is chosen from its metadata.

| Area | State |
| --- | --- |
| Output contract and JSON schema | Working, versioned `0.1.0` |
| Text-layer reading with line and table-cell boxes | Working |
| Scanned pages through Tesseract (command-line program) | Working, tested with the real program |
| Cross-check of the text layer against an OCR reading | Working |
| Validators: unmapped characters, malformed dates | Working |
| Decision policy: accepted / flagged / blocked, with rule ids | Working; **thresholds are not calibrated** |
| Markdown and chunk exporters | Working |
| EasyOCR as a second reader on scanned pages | Working, tested with the real models in CI; English only |
| Process isolation and resource limits per page | Not ported (lab: `runner.py`, `worker.py`) |
| Pinned, hash-checked OCR weights | Partly: EasyOCR checks its own MD5 sums; versions are not pinned yet |
| Scan recovery, rotation, show-through | Not ported |
| Table structure for scans; merged cells and header paths | Not ported |
| Large documents in page groups | Not built |
| Accuracy measured on reference pages | Not done; needed before trusting "accepted" |

## Install and run

Python 3.12 or newer. For scanned pages, install the Tesseract program and the language data you need (`eng`, `hin`, …).

```bash
pip install -e ".[test]"
```

```bash
ragnarok convert notification.pdf --out out/
```

```bash
ragnarok convert gazette.pdf --out out/ --lang eng+hin --exclude-script Deva
```

A second reader for scanned pages, so lines that both readers agree on need no person:

```bash
pip install torch torchvision --index-url https://download.pytorch.org/whl/cpu
pip install -e ".[easyocr]"
ragnarok convert scan.pdf --out out/ --easyocr-models ~/easyocr-models --easyocr-download
```

The models are fetched once into `--easyocr-models`; leave out `--easyocr-download` afterwards to stay offline. If they are missing, EasyOCR is left out at the start and Tesseract reads alone. EasyOCR confirms scanned pages only: a page with a text layer is cross-checked by the first engine (`Config.native_crosscheck_engines`). A confirming reader that fails on a page flags the page (`ocr.reader-failed`) and the first reader's lines stand.

Exit code `0` means every line was accepted by rule, `2` means the files were written and something needs a person, `1` means the file could not be processed.

```python
from ragnarok import Config, convert, write

document = convert("notification.pdf", config=Config(languages=("eng",)))
print(document.summary)        # {'lines': 120, 'accepted': 112, 'flagged': 8, ...}
write(document, "out/")
```

## What comes out

**`document.json`** is the full record. For each page: its kind (`native`, `scanned`, `blank`, `failed`), and every line with its box in PDF points, the exact text the reader returned (`raw`), the NFC-normalised `text`, where it came from (`native` or `ocr:<engine>`), every other engine's reading of the same place, its `status`, and the `flags` that explain any status other than `accepted`. Tables keep every cell with its box; an empty reading is `null`, never a guessed blank.

**`document.md`** is Markdown in reading order. Headings become `##`, the lines of a paragraph are joined, tables become Markdown tables. Page breaks and review flags are HTML comments (`<!-- page 3 -->`, `<!-- flagged: date.ordinal-suffix -->`), so a model sees them without mistaking them for content.

**`chunks.jsonl`** has one JSON object per line:

| Field | Use |
| --- | --- |
| `embedding_text` | What to embed: the title and heading, then the text |
| `text` | What to show |
| `context` | Title and current heading |
| `pages`, `anchors` | Where it came from: page, bounding box and line ids, one anchor per page the chunk touches |
| `status`, `rules` | `accepted`, `flagged` or `blocked`, and the rules that fired. Filter on this |
| `sources` | `native`, `ocr:tesseract`, … |
| `id` | Stable for the same source file and lines |

A chunk never splits a paragraph and may run across a page break. A table is always its own chunk.

## How it decides

Rules, in `policy.py`. Nothing is decided by engine confidence, because confidence is high on exactly the errors that matter (`31st` read as `31*`, `01st` read as `015`).

- A text-layer line is **accepted** when no validator objects and an independent OCR reading of the rendered page agrees with it: similar overall, and identical on every critical token (anything containing a digit, and negations).
- A scanned line is **accepted** when two engines agree by the same test.
- Anything else is **flagged** with the rule that caused it. Tables are always flagged.
- A page with ink and no way to read it is **blocked**. It is never silently empty.
- Text is never repaired. A flag says what looks wrong; the text stays as read.

Two switches loosen this when you have no second reader: `Policy.accept_native_without_crosscheck` and `Policy.accept_single_ocr_reading`. Both are off by default.

The thresholds are starting values. Until they are measured against human-verified pages, read "accepted" as "passed every rule", not as an accuracy figure.

## Layout

```
src/ragnarok/
  model.py        output contract (pydantic)        config.py   settings and policy thresholds
  native.py       text layer, tables, rendering     ocr/        engine interface and adapters
  crosscheck.py   compare two readings              validators/ rule checks that raise flags
  policy.py       accepted / flagged / blocked      structure.py scripts, headings, paragraphs
  export/         markdown and chunks               pipeline.py convert() and write()
  cli.py
schemas/document.schema.json   generated; a test keeps it in sync
tests/                         synthetic PDFs only
```

Adding an OCR engine means one class with `name`, `version()` and `read(image, languages)`. Adding a rule means one function from text to flags, listed in `validators/__init__.py`.

## Development

```bash
python -m pytest
```

```bash
ruff check .
```

After changing `model.py`, regenerate the schema:

```bash
ragnarok schema > schemas/document.schema.json
```

## Licence

Apache License 2.0. See [LICENSE](LICENSE).
