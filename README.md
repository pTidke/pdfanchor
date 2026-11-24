# RAGnaroq

**The end of bad RAG.** Point it at your files and get text a language model can trust: every line anchored to its place in the source, and judged before it is used.

Today it reads PDFs. The plan is any folder of files, ready for retrieval and served to any model over MCP ([roadmap](docs/roadmap.md)).

```bash
pip install ragnaroq          # with a Q
ragnaroq convert notification.pdf --out out/
```

## RAG 'n' AROQ

In Old Norse, *ragna rök* is "the fate of the gods", and *rök* means fate, or judgement. Passing judgement on every line is what this tool is for. So: **RAG 'n' AROQ**, like rock 'n' roll. RAG, and the four jobs that make it trustworthy. That is why the name ends in Q: it is Ragnarök, spelt the way the fourth job reads.

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
| **Yggdrasil** | The `.ragnaroq/` folder that ties a processed folder together | The world tree that connects the nine worlds. |

Norse names are for the big parts; the plumbing has plain names (`ragnaroq.ocr`, `ragnaroq.policy`). Wherever a Norse name appears, its plain meaning appears with it.

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

Version 0.2: small, tested, end to end, and measured. It was called `pdfanchor` until version 0.0.4. It grew out of a private extraction lab built for Indian tax documents, which still holds far more experience than this code does. `docs/porting-from-the-lab.md` lists what moves across and in what order.

The goal is wider than PDFs: point ragnaroq at a folder of any files and get a checked, described and indexed knowledge layer, served to any model through MCP. [docs/roadmap.md](docs/roadmap.md) sets out the stages, the principles and the milestones. [docs/chunking.md](docs/chunking.md) explains how the chunking strategy for each document is chosen from its metadata.

| Area | State |
| --- | --- |
| Output contract and JSON schema | Working, versioned `0.2.0` |
| Text-layer reading with line and table-cell boxes | Working |
| Scanned pages through Tesseract (command-line program) | Working, tested with the real program |
| Cross-check of the text layer against an OCR reading | Working |
| Validators: unmapped characters, malformed dates, codes where a letter can pass for a digit | Working |
| Decision policy: accepted / flagged / blocked, with rule ids | Working; measured on synthetic scans, not yet on real paper |
| Markdown and chunk exporters | Working |
| EasyOCR as a second reader on scanned pages | Working, tested with the real models in CI; English only |
| Process isolation and resource limits per page | Working: a worker process per document, with a time, memory and character limit per page |
| Low-resolution scans read at a higher resolution | Working: under 150 dpi, read at 300 |
| A third reader for lines the two readers dispute | Working, optional (`--third-reader`, `ragnaroq[vlm]`); scanned lines only; about 2.5 seconds a disputed line on a CPU with native bfloat16; float32 CPUs are about 2.5 times slower |
| Pinned, hash-checked OCR weights | Partly: the third reader's files are pinned to a revision and checked by SHA-256; EasyOCR checks its own MD5 sums and is not pinned yet |
| Scan recovery, rotation, show-through | Not ported |
| Table structure for scans; merged cells and header paths | Not ported |
| Large documents in page groups | Not built |
| Accuracy measured on reference pages | On public IRS documents turned into scans (0.1): 72.3% of lines accepted, none wrong on a number, date or negation. On a held-out set of five other IRS documents, 6 accepted lines were wrong on a number, with or without the third reader. In 5, both OCR readers read a "1" as "|" and agree. This is a known limitation, not fixed in 0.2.0; a follow-up is planned. See [references/](references/README.md) |

## Install and run

Python 3.12 or newer. For scanned pages, install the Tesseract program and the language data you need (`eng`, `hin`, …).

```bash
pip install ragnaroq
```

```bash
ragnaroq convert notification.pdf --out out/
```

```bash
ragnaroq convert gazette.pdf --out out/ --lang eng+hin --exclude-script Deva
```

A second reader for scanned pages, so lines that both readers agree on need no person:

```bash
pip install torch torchvision --index-url https://download.pytorch.org/whl/cpu
pip install "ragnaroq[easyocr]"
ragnaroq convert scan.pdf --out out/ --easyocr-models ~/easyocr-models --easyocr-download
```

The models are fetched once into `--easyocr-models`; leave out `--easyocr-download` afterwards to stay offline. If they are missing, EasyOCR is left out at the start and Tesseract reads alone. EasyOCR confirms scanned pages only: a page with a text layer is cross-checked by the first engine (`Config.native_crosscheck_engines`). A confirming reader that fails on a page flags the page (`ocr.reader-failed`) and the first reader's lines stand.

A third reader for the scanned lines the two readers still dispute, PaddleOCR-VL-1.6 through Hugging Face transformers (an optional extra; about 1.9 GB of model files, checked against pinned SHA-256 sums):

```bash
pip install torch torchvision --index-url https://download.pytorch.org/whl/cpu
pip install "ragnaroq[vlm]"
ragnaroq convert scan.pdf --out out/ --third-reader --vlm-download
```

The files are fetched once, into `--vlm-models` or the Hugging Face cache when it is not given; leave out `--vlm-download` afterwards to stay offline.

Exit code `0` means every line was accepted by rule, `2` means the files were written and something needs a person, `1` means the file could not be processed.

```python
from ragnaroq import Config, convert, write

document = convert("notification.pdf", config=Config(languages=("eng",)))
print(document.summary)        # {'lines': 120, 'accepted': 112, 'flagged': 8, ...}
write(document, "out/")
```

### PDFs you did not choose

A crafted or broken PDF can make a parser spin for minutes, use memory until the machine stalls, or crash. Each page's text layer is therefore read, and the page drawn for OCR, in a worker process with limits:

- **Time:** `--page-timeout`, 60 seconds by default.
- **Memory:** `--page-memory-mb`, 2,048 MB by default.
- **Characters:** 200,000 per page (`Config.max_page_chars`).

A page that breaks a limit fails alone, flagged with the limit (`budget.page-time`, `budget.page-memory`, `budget.page-glyphs`, `page.crashed`), and the rest of the document is read. A PDF that cannot even be opened within the limits fails as a whole. `--no-isolation` reads in the same process: quicker to start, with no time or memory limit.

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
- Two engines can agree on the same mistake. In text read from an image, a code that mixes letters and digits is therefore flagged when it holds a letter readers take for a digit: `R28C0` read as `R28CO`, `PAN AABTS1181R`. So is a bracket the line never pairs, as in `4a)` read for `4(a)`. Measured: this cut the critical errors let through from 5 to 3 on the IRS set, and from 30 to 0 on the code table.
- A scanned page whose image holds under 150 dots per inch is read at 300 instead of 200. On low-resolution scans this removed the critical errors no text rule can see, such as `113208` read for `11320B`.
- Anything else is **flagged** with the rule that caused it. Tables are always flagged.
- A page with ink and no way to read it is **blocked**. It is never silently empty.
- Text is never repaired. A flag says what looks wrong; the text stays as read.

- **Third reader (optional, `--third-reader`).** A scanned line the two engines disagree on is read again, as a crop, by a local model. The line is accepted only if the model's reading exactly matches one of the two engines' readings and four checks pass:
  - neither matching reading trips a validator;
  - no other reading saw something the agreeing pair missed: clearly more letters (small print both missed), a negation, or a number where the model wrote a layout mark such as a checkbox or `|`;
  - no reading has an underscore inside a word or number, which an underline can hide;
  - the line does not repeat four words of the line above or below it in the same column, or of the line before or after it in reading order, which happens when its box covers two printed lines.

  Otherwise the line stays flagged for a person, with the model's reading and the check that held it back. The line keeps the agreeing engine's text, and `resolution` records which readings agreed. On a held-out set it cut lines sent to a person by 41.7% and settled 447 lines, none wrong on a number, date or negation. After the gate the neighbour check was tightened, which only holds lines back: the shipped rule, re-scored on the same readings, settles 440 lines (none wrong on a number, date or negation), sends 632 to a person (41.0% fewer than 1,072; 91 of them right), accepts 350 lines wrong (11.4%, against 12.5% without the third reader) and holds back 23 at the neighbour check. It costs about 2.5 seconds a disputed line on a CPU with native bfloat16; float32 CPUs are about 2.5 times slower.
- **Known limits.**
  - Both engines can read a "1" as "|" and agree, and the line is then accepted before the third reader is consulted. Of the six accepted lines wrong on a number on the held-out set, with or without the third reader, five were this. A follow-up is planned.
  - A digit that one reader saw and the other two missed is outvoted.
  - Where EasyOCR read nothing, Tesseract and the model settle the line between them.
  - Negations such as "cannot", and contractions such as "don't", are not recognised as negations (this predates 0.2.0).
  - Superscript digits are folded into plain digits in the two-reader comparison, so "80¹" agrees with "801" there (this predates 0.2.0).
  - Pages over 16 million pixels at 300 dpi (A3 and larger; A4, Letter and Legal fit) are not read by the third reader; their disputed lines stay with a person.
  - The per-line time limit and the budget are wall-clock limits, so on a slow or busy machine they can change which lines are read.

Two switches loosen this when you have no second reader: `Policy.accept_native_without_crosscheck` and `Policy.accept_single_ocr_reading`. Both are off by default.

One switch tightens it: `Policy.same_letters` (`--same-letters`) makes two readings agree only if they have the same letters and digits, not only if they are similar. It catches letter slips such as `Hf you` for *If you*, at a measured cost: on the IRS set, 82 slips caught for 324 correct lines sent to a person.

The thresholds are starting values, now measured on synthetic scans ([references/](references/README.md)) but not yet on real paper. Read "accepted" as "passed every rule", with the measured error rates as a guide.

## Layout

```
src/ragnaroq/
  model.py        output contract (pydantic)        config.py   settings and policy thresholds
  native.py       text layer, tables, rendering     ocr/        engine interface and adapters
  crosscheck.py   compare two readings              validators/ rule checks that raise flags
  policy.py       accepted / flagged / blocked      structure.py scripts, headings, paragraphs
  export/         markdown and chunks               pipeline.py convert() and write()
  reference.py    reference PDFs with known text    evaluate.py how often accepted is wrong
  cli.py
schemas/document.schema.json   generated; a test keeps it in sync
tests/                         synthetic PDFs only
```

Adding an OCR engine means one class with `name`, `version()` and `read(image, languages)`. Adding a rule means one function from text to flags, listed in `validators/__init__.py`.

## Measuring it

```bash
ragnaroq reference make notice.pdf --out refs/     # born-digital PDFs become scans with a known answer
ragnaroq evaluate refs/ --out results/            # how often an accepted line is wrong
```

The report counts accepted lines that are wrong, and those wrong on a number, a date or a negation. It also counts flagged lines that were right, and shows how each agreement threshold trades one against the other. [docs/evaluating.md](docs/evaluating.md) explains the method and its limits.

On the held-out set (five other IRS documents, 3,712 lines), without and with the third reader:

| | Without | With |
| --- | --- | --- |
| Lines sent to a person | 1,072 | 625 |
| Accepted but wrong | 12.5% | 11.3% |
| Accepted with a wrong number, date or negation | 6 | 6 |

After the gate the neighbour check was tightened, which only holds lines back: the shipped rule, re-scored on the same readings, settles 440 lines (none wrong on a number, date or negation), sends 632 to a person (41.0% fewer than 1,072; 91 of them right), accepts 350 lines wrong (11.4%, against 12.5% without the third reader) and holds back 23 at the neighbour check.

First results (0.1), on public IRS forms and publications turned into scans (clean, low-res, blurred, tilted), read by Tesseract and EasyOCR:

| Lines accepted without a person | Accepted but wrong | Accepted and wrong on a number, date or negation |
| --- | --- | --- |
| 72.3% | 6.2% | 0 |

The first run let 5 such lines through (0.15%), and a stress test of codes such as `R28C0` let half its accepted lines through wrong. A rule for codes that mix letters and digits, and reading low-resolution scans at 300 dpi, brought both to zero on those sets; the held-out set above shows the limit of that result. [references/](references/README.md) has the full results and how to reproduce them.

## Development

```bash
python -m pytest
```

```bash
ruff check .
```

After changing `model.py`, regenerate the schema:

```bash
ragnaroq schema > schemas/document.schema.json
```

## Licence

Apache License 2.0. See [LICENSE](LICENSE).
