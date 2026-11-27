# RAGnaroq

**The end of bad RAG.** Point it at your files and get text a language model can trust: every line anchored to its place in the source, and judged before it is used.

It reads PDFs, Word, Excel, PowerPoint, HTML, Markdown, JSON, XML and CSV, and turns a folder of them into chunks any vector database can load. The plan is to serve the folder to any model over MCP ([roadmap](docs/roadmap.md)).

```bash
pip install "ragnaroq[formats]"          # with a Q
ragnaroq index ./my-folder               # a folder: writes ./my-folder/.ragnaroq/
ragnaroq convert notification.pdf --out out/    # one PDF
```

## RAG 'n' AROQ

In Old Norse, *ragna rök* is "the fate of the gods", and *rök* means fate, or judgement. Passing judgement on every line is what this tool is for. So: **RAG 'n' AROQ**, like rock 'n' roll. RAG, and the four jobs that make it trustworthy. That is why the name ends in Q: it is Ragnarök, spelt the way the fourth job reads.

| | Job | Norse name | Why the name fits |
| --- | --- | --- | --- |
| **A** | **Assess**: every line gets a verdict, *accepted*, *flagged* or *blocked*, by rules. | **The Norns** | Three Norns weave every fate at the roots of Yggdrasil. Three verdicts. |
| **R** | **Read**: the text layer, Word, Excel, PowerPoint, HTML, Markdown, JSON, XML and CSV. | **Runes** | Odin hung nine nights on the world tree to learn to read the runes. |
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
| **Yggdrasil** | The `.ragnaroq/` folder that ties a processed folder together (0.3) | The world tree that connects the nine worlds. |

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

Version 0.3: small, tested, end to end, and measured. It was called `pdfanchor` until version 0.0.4. It grew out of a private extraction lab built for Indian tax documents, which still holds far more experience than this code does. `docs/porting-from-the-lab.md` lists what moves across and in what order.

The goal is wider than PDFs: point ragnaroq at a folder of any files and get a checked, described and indexed knowledge layer, served to any model through MCP. [docs/roadmap.md](docs/roadmap.md) sets out the stages, the principles and the milestones. [docs/chunking.md](docs/chunking.md) explains how the chunking strategy for each document is chosen from its metadata.

| Area | State |
| --- | --- |
| Output contract and JSON schema | Working, versioned `0.3.0`; a 0.1 or 0.2 `document.json` still loads |
| `ragnaroq index`: a folder made ready for retrieval, with incremental runs | Working (0.3), on Linux and macOS only |
| Readers for Word, Excel, PowerPoint, HTML, Markdown, JSON, XML and CSV, each line anchored to its place in the file | Working (0.3); the demo folder scores 100% on every exact format. Not read: charts, `.doc`, `.xls`, `.ppt` |
| Images in those files: saved, described from the file's own caption or alt text or from OCR | Working (0.3); PDF images are not extracted yet |
| A model's one-sentence description of an image | Working, optional, off by default (`--describe-images`, `ragnaroq[describe]`) |
| Chunks that load into Qdrant, Chroma, Pinecone, pgvector and others, and `changes.json` to keep a database in sync | Working (0.3); see [docs/vector-databases.md](docs/vector-databases.md) |
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
| Accuracy measured on reference pages (PDF) | Synthetic scans of public IRS documents and of two CBDT documents, with 0.2.2: on the IRS set 71.9% of lines accepted, none wrong on a number, date or negation; on five held-out IRS documents and on the CBDT pages, 1 accepted line each is wrong on a number, a small "(Form 1040)" and a list number both readers missed. With the third reader, 45% fewer lines go to a person on both, and none it settled was wrong on a number. The 0.3 release leaves these numbers unchanged. See [references/](references/README.md) |

## Install and run

Python 3.12 or newer. For scanned pages, install the Tesseract program and the language data you need (`eng`, `hin`, …).

```bash
pip install ragnaroq
```

JSON, CSV and XML files are read by the core install. Word, Excel, PowerPoint, HTML and Markdown need the `formats` extra. Without it, those files are listed as failed, with the command that fixes it:

```bash
pip install "ragnaroq[formats]"
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

## Index a folder

```bash
ragnaroq index ./my-folder
```

It reads every file it supports and writes `.ragnaroq/` inside the folder. Your files are only ever opened for reading. Run it again after the folder changes: only files whose content, reader or settings changed are read again.

`ragnaroq index` works on Linux and macOS only in 0.3. On Windows it exits with code `1`.

The options for PDFs (`--ocr`, `--lang`, `--dpi`, `--exclude-script`, `--third-reader` and the rest, and `--easyocr-models`) work the same as in `convert`. `--fresh` reads every file again.

### Formats and anchors

Every line records where it came from, in a form suited to its file.

| Format | Files | Anchor | Citation |
| --- | --- | --- | --- |
| PDF | `.pdf` | page and box | `p. 3` |
| Word | `.docx` | paragraph, table cell, or footnote | `¶18`, `table 1, row 2, column 1`, `footnote 1` |
| Excel | `.xlsx` | sheet and cell, or range for a merged cell | `Rates!C7`, `Rates!B2:D2` |
| PowerPoint | `.pptx` | slide, shape, and notes | `slide 4, Title 1`, `slide 2 notes` |
| HTML | `.html`, `.htm`, `.xhtml` | path of the element | `/html/body/table/tr[1]/td[2]` |
| Markdown | `.md`, `.markdown` | line range | `lines 40–42` |
| JSON | `.json` | JSON pointer | `/deductions/0/section` |
| XML | `.xml` | path of the element | `/notice/section[2]/para[1]` |
| CSV | `.csv`, `.tsv` | row and column | `row 12, Amount` |

The format is found from the file's content first, then its name. A file that says it is a `.docx` and is not fails alone, with the reason in the manifest.

Text from these files is exact: it is the file's own text, not a reading of a picture. It is accepted unless a rule flags it. Only text read from an image goes through the two-reader rules. Excel shows a cell as Excel does (`5%`, not `0.05`; `₹1,50,000` with lakh grouping) and keeps the stored value and any formula in the line's `value`. A number format it does not know gives the stored value, flagged `xlsx.format-unknown`.

### What it writes

```
my-folder/
  ...your files, untouched...
  .ragnaroq/
    manifest.json     every file: path, sha256, reader, status, counts, chunk ids, error
    changes.json      this run against the last: files and chunk ids added, changed, removed
    chunks.jsonl      every chunk of the folder, ready for a vector database
    lock              stops two runs at once
    files/<file-id>/  document.json, document.md, chunks.jsonl, entry.json, images/
```

A file's id is a UUID made from its path, so an edited file keeps its place. The status of a file is `ok`, `review`, `failed` or `skipped`. A file needs `review` when a line, table or image is not accepted, or when a flag on the file or one of its parts says something was not read or not read with certainty (`chart.not-read`, `md.too-deep`, `json.duplicate-key`, `encoding.guessed`, a line limit and the rest). Three flags only inform and do not count: `image.tiny-skipped`, `docx.headers-footers` and `pptx.footers`. A PDF counts as before: any table, and only blocking flags on the file or a page. Skipped files are listed with the reason: hidden files and folders, symbolic links that lead outside the folder, empty files, files over 100 MiB, and files of a type it does not read (`unsupported`). A symbolic link to a file inside the folder is read.

The exit code is `1` when any file failed, else `2` when anything needs a person, else `0`. One file's failure never stops a run.

`.ragnaroq/` is private: its folders are made `0700` and its files `0600`. A run that is killed may leave one private `ragnaroq-*` folder in `TMPDIR`; it holds nothing the next run needs.

`changes.json` and `chunks.jsonl` are what keep a vector database in step with the folder. [docs/vector-databases.md](docs/vector-databases.md) shows how to load and sync Qdrant, Chroma, Pinecone and pgvector.

### Files you did not choose

A hostile file must not hang or crash a run, or run anything.

- Each file is read in an isolated worker process, with a time limit (`--file-timeout`, 120 seconds by default) and a memory limit (2,048 MB, `Config.page_memory_mb`). A PDF's pages also keep their own limits (see above).
- A file that breaks a limit fails alone, flagged with the limit. It is read again when you raise that limit.
- Word, Excel and PowerPoint files are zip files. Their size when unpacked (500 MiB) and entry count (10,000) are checked before any library opens them.
- XML is read with defusedxml. A file that declares entities or a DTD is refused (`xml.entities`).
- HTML scripts, styles and templates are not run or read. Nothing reaches the network: a remote image is kept with its URL and flagged `image.remote-not-fetched`.
- Image paths cannot leave the file's folder.
- `ragnaroq index` never writes into your folder outside `.ragnaroq/`. While it works it uses a private folder in `TMPDIR` for each file in turn, and removes it.

### Images

Every image in a Word, PowerPoint, Excel, HTML or Markdown file is saved to `files/<file-id>/images/`, in its own format when a browser can show it (PNG, JPEG, GIF, WebP) and as PNG otherwise. Images smaller than 32 by 32 pixels (bullets, spacers) are not saved; a flag counts them. Pillow opens only PNG, JPEG, GIF, WebP, BMP, TIFF, ICO and PPM files, so Ghostscript never runs.

Each image has a description, from the file's caption or alt text when there is one, otherwise from the text OCR finds in it. Text read from an image goes through the same two-reader rules as a scanned page, so with one reader the text is flagged (`ocr.single-reading`), not accepted. An image with neither is kept and flagged `image.no-description`. In `document.md` it appears where it sits, and it is its own chunk (`kind` `image`), so a search for "bar chart of collections" can find it.

PDF images are not extracted in 0.3.

**A model's description (optional).** With the extra `ragnaroq[describe]`, `--describe-images` asks a small local model, SmolVLM-500M-Instruct (Apache-2.0), for one sentence about each image:

```bash
pip install torch torchvision --index-url https://download.pytorch.org/whl/cpu
pip install "ragnaroq[describe]"
ragnaroq index ./my-folder --describe-images --describe-download
```

It is off by default. The files (about 1 GB) are pinned to one revision, checked against SHA-256 sums, and fetched only with `--describe-download`, into `--describe-models` or the Hugging Face cache. No code from the model's repository is run. On 4 CPUs a description takes about 7 seconds an image.

The sentence is a label, marked "(model)" in the chunk's `embedding_text` and in `document.md`. It never changes a status and is never in `text`. It is kept only if it is a whole sentence of three or more words, and every number, currency, percent, date, negation, month and scale word (lakh, crore, million) in it also appears in the image's OCR text. A claim about structure ("a table with 31 rows") is not checked.

### Known limits

- Charts in Word, Excel and PowerPoint are not read. Each is flagged `chart.not-read`.
- `.doc`, `.xls` and `.ppt` are not read, and neither are OpenDocument files.
- Remote images are not fetched.
- Word headers and footers, and PowerPoint footer, date and slide-number placeholders, are left out, with a flag on the document.
- Hidden Excel sheets, rows and columns are left out, with a flag. Hidden HTML text is read, and flagged `html.hidden`.
- Equations are read as plain symbols, and flagged. SmartArt, OLE objects and image fills are flagged, not read.
- Two shapes on one slide with the same name share an anchor.

## What comes out

**`document.json`** is the full record. For each page: its kind (`native`, `scanned`, `blank`, `failed`), and every line with its box in PDF points, the exact text the reader returned (`raw`), the NFC-normalised `text`, where it came from (`native` or `ocr:<engine>`), every other engine's reading of the same place, its `status`, and the `flags` that explain any status other than `accepted`. Tables keep every cell with its box; an empty reading is `null`, never a guessed blank.

**`document.md`** is Markdown in reading order. Headings become `##`, the lines of a paragraph are joined, tables become Markdown tables. Page breaks and review flags are HTML comments (`<!-- page 3 -->`, `<!-- flagged: date.ordinal-suffix -->`), so a model sees them without mistaking them for content. Slides, sheets and bodies are marked the same way (`<!-- slide 4 -->`, `<!-- sheet Rates -->`).

`document.md` is for viewing, not a sanitiser. Text from the file has `\ [ ] < > &` escaped, so an image, a link, raw HTML or a comment ending in the file's text cannot break out. Block structure from the file's text can still render (a line starting with `#` is a heading, `-` a list), and a bare URL becomes a link in a viewer that links bare URLs (GitHub-flavoured Markdown). For a PDF this is the one change in 0.3: `document.md` now escapes those characters. `document.json`, the chunks and the evaluation are unchanged.

A chunk's `text` is the file's own text, raw, not escaped. Escape it before you show it as Markdown or HTML.

**`chunks.jsonl`** has one JSON object per line, built to load into any vector database as it stands ([docs/vector-databases.md](docs/vector-databases.md)):

| Field | Use |
| --- | --- |
| `id` | A UUID, stable for the same file content and lines. Upsert by it |
| `embedding_text` | What to embed: the title and heading, then the text |
| `text` | What to show |
| `anchors` | Where it came from: for a PDF a page, bounding box and line ids per page; for a spreadsheet table its cell range; otherwise `from` and `to` anchors per part. `metadata.anchors_json` holds the same as a string, capped at 20,000 bytes |
| `metadata` | Flat, no nulls, the same keys on every chunk: `schema_version`, `file_id`, `path`, `file_type`, `file_sha256`, `kind`, `status`, `rules`, `sources`, `cite`, `part`, `part_index`, `page_first`, `page_last`, `heading`, `title`, `chars`, `anchors_json`, `image_path` |
| `metadata.cite` | A citation on every chunk: `native.pdf, p. 1`, `rates.xlsx, Rates!A1:D12`, `guide.docx, ¶18–¶24` |
| `metadata.status`, `metadata.rules` | `accepted`, `flagged` or `blocked`, and the rules that fired (comma-separated). Filter on this |
| `metadata.sources` | `native`, `ocr:tesseract`, … (comma-separated) |

A figure is its own chunk, with `kind` `image`, its description as the text and its saved file in `image_path` (relative to the file's folder in `.ragnaroq/files/`).

A chunk never splits a paragraph and may run across a page break. A table is always its own chunk. A PDF table is one chunk. A table of any other file is cut into groups of rows that fit `chunk_chars` (1,200 characters by default), each with the table's header row (its first row) above it, so every chunk reads on its own and stays small enough for a store's metadata limit. A row longer than that is a chunk of its own. Each group has its own id, anchors and citation: for a sheet, the range its rows cover (`rates.xlsx, Rates!A32:C60`).

## How it decides

Rules, in `policy.py`. Nothing is decided by engine confidence, because confidence is high on exactly the errors that matter (`31st` read as `31*`, `01st` read as `015`).

- A text-layer line is **accepted** when no validator objects and an independent OCR reading of the rendered page agrees with it: similar overall, and identical on every critical token (anything containing a digit, and negations).
- A scanned line is **accepted** when two engines agree by the same test.
- Two engines can agree on the same mistake. In text read from an image, a code that mixes letters and digits is therefore flagged when it holds a letter readers take for a digit: `R28C0` read as `R28CO`, `PAN AABTS1181R`. So is a bracket the line never pairs, as in `4a)` read for `4(a)`. Measured: this cut the critical errors let through from 5 to 3 on the IRS set, and from 30 to 0 on the code table.
- A scanned page whose image holds under 150 dots per inch is read at 300 instead of 200. On low-resolution scans this removed the critical errors no text rule can see, such as `113208` read for `11320B`.
- In text read from an image, a "|" standing alone is flagged (`glyph.lone-bar`): it is a printed "1", "I" or "l" both readers mistook, or a box or table border read as text. On the reference sets it was never the right text.
- Anything else is **flagged** with the rule that caused it. PDF tables are always flagged; a table of any other file follows its cells.
- A page with ink and no way to read it is **blocked**. It is never silently empty.
- Text is never invented or edited. A flag says what looks wrong. A line the third reader settles carries the text of the OCR reader it agreed with, exactly as that reader read it.

- **Third reader (optional, `--third-reader`).** A scanned line the two engines disagree on is read again, as a crop, by a local model. The line is accepted only if the model's reading exactly matches one of the two engines' readings and four checks pass:
  - neither matching reading trips a validator;
  - no other reading saw something the agreeing pair missed: clearly more letters (small print both missed), a negation, or a number where the model wrote a layout mark such as a checkbox or `|`;
  - no reading has an underscore inside a word or number, which an underline can hide;
  - the line does not repeat four words of the line above or below it in the same column, or of the line before or after it in reading order, which happens when its box covers two printed lines.

  Otherwise the line stays flagged for a person, with the model's reading and the check that held it back. The line keeps the agreeing engine's text, and `resolution` records which readings agreed. On a held-out set it cut lines sent to a person by 41.7% and settled 447 lines, none wrong on a number, date or negation. After the gate the neighbour check was tightened, which only holds lines back: the shipped rule, re-scored on the same readings, settles 440 lines (none wrong on a number, date or negation), sends 632 to a person (41.0% fewer than 1,072; 91 of them right), accepts 350 lines wrong (11.4%, against 12.5% without the third reader) and holds back 23 at the neighbour check. It costs about 2.5 seconds a disputed line on a CPU with native bfloat16; float32 CPUs are about 2.5 times slower.
- **Known limits.**
  - A number both readers leave out (a list number, small print) is not seen: two readers agree on the line without it, and no text rule can see a number that is not there.
  - The third reader only sees lines the two readers disagree on, and never overrules two readers that agree. A "|" both readers agree on (most often a printed "1" both misread) therefore always goes to a person, even though the model could likely read it. Letting the model settle that one ambiguous character is a possible next step, to be measured first.
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
  readers/        one reader per format, and detect  folder.py   ragnaroq index and .ragnaroq/
  anchors.py      where a line lives in its file     figures.py  images: saved, checked, described
  expected.py     answer keys for exact formats      isolation.py workers with time and memory limits
  cli.py
schemas/document.schema.json   generated; a test keeps it in sync
tests/                         synthetic PDFs only
```

Adding a format means one module in `readers/` with a `read(data, name, config)` function, and its name in `FORMATS` and `detect`. Adding an OCR engine means one class with `name`, `version()` and `read(image, languages)`. Adding a rule means one function from text to flags, listed in `validators/__init__.py`.

## Measuring it

**The exact formats** are scored against answer keys written by hand. [`references/demo/`](references/demo) holds awkward files for every format (a tracked-changes Word file, an Excel file with lakh grouping and a hidden sheet, an XML entity bomb, a CSV in cp1252 and more), each with a `<file>.expected.jsonl`: one line per anchor, with the expected text. A folder with `*.expected.jsonl` files is scored by anchor:

```bash
ragnaroq evaluate references/demo --out results/demo
```

In 0.3.0 every exact-format file scores `ok` there: every expected anchor is present with the right text, and every case the design says to flag is flagged.

**PDFs** are scored against scans of known text:

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

Tests of the Office, HTML and Markdown readers need the `formats` libraries. CI installs them with `pip install -e ".[test]"`; without them those tests are skipped.

```bash
ruff check .
```

After changing `model.py`, regenerate the schema:

```bash
ragnaroq schema > schemas/document.schema.json
```

## Licence

Apache License 2.0. See [LICENSE](LICENSE).
