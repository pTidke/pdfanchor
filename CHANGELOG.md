# Changelog

## 0.3.0 — 27 November 2026

Folders and formats. `ragnaroq index ./folder` reads a folder of mixed files and writes `.ragnaroq/`, ready for a vector database. The PDF engine and its numbers are unchanged.

### Added

- **`ragnaroq index`.** It reads every supported file and writes `.ragnaroq/` inside the folder: `manifest.json`, `changes.json`, `chunks.jsonl` for the whole folder, and for each file `document.json`, `document.md`, `chunks.jsonl` and `images/`. The originals are never changed. A second run reads only files whose content, reader or settings changed, and deletes the output of removed files. Exit code `1` when any file failed, `2` when anything needs a person, else `0`. One file's failure never stops a run.
  - **Status per file:** `ok`, `review`, `failed` or `skipped`. A file is `review` when a line, table or image is not accepted, or a flag on the file or a part says something was not read or not read with certainty; `image.tiny-skipped`, `docx.headers-footers` and `pptx.footers` only inform. A table whose cells are all accepted needs no person. PDFs count as before.
  - **POSIX only in 0.3** (Linux and macOS). On Windows it exits `1`.
  - **Skipped, and listed with the reason:** hidden files and folders, symbolic links that lead outside the folder, empty files, files over 100 MiB, and files of a type it does not read.
  - **Private:** `.ragnaroq/` folders are `0700` and files `0600`. A run that is killed may leave one private `ragnaroq-*` folder in `TMPDIR`.
  - **Options:** the PDF options of `convert` that apply (`--ocr`, `--lang`, `--dpi`, `--exclude-script`, `--third-reader` and its model options, `--easyocr-models`), `--fresh`, and `--file-timeout`.
- **Readers for Word, Excel, PowerPoint, HTML, Markdown, JSON, XML and CSV.** Each writes the same `document.json`, with a location suited to its file: paragraph or table cell, sheet and cell, slide and shape, element path, line range, JSON pointer, XML path, row and column. Every anchor has a short citation (`Rates!C7`, `¶18`, `slide 4, Title 1`, `lines 40–42`). Detection is by content first. JSON, CSV and XML are in the core install; the others need the new extra `ragnaroq[formats]`.
  - Excel shows cells as Excel does (`5%`, `₹1,50,000`), and keeps the stored value, formula and format in `Line.value`. Hidden sheets, rows and columns are left out, with a flag. A formula with no stored value is flagged `xlsx.no-cached-value`; a number format it does not know is flagged `xlsx.format-unknown`.
  - Word tracked changes are read as accepted, flagged `docx.tracked-changes`. Comments are left out, flagged `docx.comments`. Footnotes, endnotes and PowerPoint speaker notes are kept, each at its own anchor. A table nested in a Word cell is read into that cell, so its text is in the table's chunk and in `document.md`.
  - CSV is read as UTF-16 with a byte-order mark, else UTF-8, else Windows-1252 (flagged `encoding.guessed`). A duplicate JSON key is flagged `json.duplicate-key`; the last wins.
- **Images.** Every image in those files is saved under `images/`, in its own format when a browser can show it, and appears in `document.md` where it sits and as a chunk of its own (`kind` `image`). Its description comes from the file's caption or alt text, otherwise from the text OCR finds in it, read by the same two-reader rules as a scanned page. Images under 32 by 32 pixels are not saved, and a flag counts them. An image with no description is flagged `image.no-description`. Word VML pictures and the preview of an embedded object are figures too (the object is flagged `docx.object-not-read`), and a linked picture in Word or PowerPoint is a figure with no file, flagged `image.linked-not-fetched`.
- **Model-written descriptions, optional.** The new extra `ragnaroq[describe]` with `--describe-images`, `--describe-models` and `--describe-download` asks SmolVLM-500M-Instruct (Apache-2.0, a pinned revision, checksummed, no remote code) for one sentence per image. It is off by default. The download is about 1 GB, and a description takes about 7 seconds an image on 4 CPUs. The sentence is a label marked "(model)", never in `text`, and never changes a status. It is kept only if it is a whole sentence of three or more words and every number, currency, percent, date, negation, month and scale word in it appears in the image's OCR text. Claims about structure ("a table with 31 rows") are not checked.
- **`changes.json`**, for keeping a vector database in step: `chunks_added`, `chunks_changed`, `chunks_removed`, and `reset`. A store upserts added and changed, and deletes removed. On `reset` (no usable last run), it drops every chunk not in `chunks.jsonl`. [docs/vector-databases.md](docs/vector-databases.md) shows Qdrant, Chroma, Pinecone and pgvector.
- **A demo folder**, `references/demo/`, of awkward files with hand-written answer keys, and a scoring mode in `ragnaroq evaluate` for them: a folder with `*.expected.jsonl` files is scored by anchor. Every exact-format file scores `ok`.
- **Safety for files you did not choose.**
  - Each file is read in an isolated worker, with a time limit (`--file-timeout`, 120 seconds by default) and a memory limit (2,048 MB). A failed file is read again when its limit is raised.
  - Zip-based files are checked for unpacked size and entry count before anything opens them.
  - XML is read with defusedxml, so a file with entities or a DTD is refused (`xml.entities`).
  - Image paths cannot leave the file's folder, and Pillow opens only PNG, JPEG, GIF, WebP, BMP, TIFF, ICO and PPM, so Ghostscript never runs.
  - Nothing reaches the network. A remote image is kept with its URL and flagged `image.remote-not-fetched`.

### Changed

- **Schema 0.3.0.** Every line has an `anchor` (new, one type per file kind). `Page.kind` gains `sheet`, `slide` and `body`, and `Page.name` is new. `Line.box` and the page size are optional outside PDF. `Line.source` names the format. `Figure` is new: an image's id, anchor, saved path, size, description and where the description came from. A 0.1 or 0.2 `document.json` still loads, and is restamped `0.3.0`.
- **Chunks have a new shape** that loads into any vector database: a UUID `id`, `text`, `embedding_text`, `anchors` and flat `metadata` (no nulls, no nesting, the same 19 keys on every chunk, `anchors_json` capped at 20,000 bytes). Tables and images are chunks of their own. A table of a file that is not a PDF is cut into groups of rows that fit `chunk_chars`, each with the header row above it and its own id, anchors and citation; a row longer than that is a chunk alone. A PDF table is one chunk, as before. `metadata.cite` is a short citation. The ids are stable for the same file content and lines.
- **Policy 0.6.** Text from a file's own content (any source other than `native` and `ocr:*`) is accepted unless a validator flags it, because it was not read from a picture. Cells of Word, Excel, HTML, CSV and JSON tables are accepted the same way. Text read from an image follows the scanned-page rules. A model's sentence is never accepted: it is a label.
- **`document.md` escapes `\ [ ] < > &`** in text from the file, so file text cannot make an image, link, raw HTML or a comment. This is the one change to PDF output. `document.json`, chunks and evaluation are unchanged. `document.md` is for viewing, not a sanitiser: block structure (`#` headings, lists) can still render, and bare URLs become links where a viewer links them. A chunk's `text` is the file's own text, raw: escape it before you render it.

### Measured

The PDF numbers did not move. The four sets were scored again from their stored readings under the 0.3.0 code, with nothing read again, and match the 0.2.2 table: IRS 0 wrong on a number, date or negation and 1,255 lines to a person; code table 0 and 322; held-out IRS with the third reader 1 and 686 (1,126 with two readers); CBDT with the third reader 1 and 74 (134 with two readers). See [references/](references/README.md#checked-again-for-030). `evaluate`'s cache keys hold the reading format (`0.2.0`), not the schema version, so readings stored by 0.2 are still found. On the demo folder, every exact-format file scores `ok`.

### Known limits

- Charts in Word, Excel and PowerPoint are not read. Each is flagged `chart.not-read`.
- PDF embedded images are not extracted in 0.3.
- `.doc`, `.xls` and `.ppt` are not read.
- Remote images are not fetched: they are kept with their URL and flagged.
- Word headers and footers, and PowerPoint footer, date and slide-number placeholders, are left out, with a flag on the document.
- Hidden HTML text is read and flagged `html.hidden`.
- Equations are read as plain symbols and flagged. SmartArt, OLE objects and image fills are flagged, not read.
- Hidden Excel sheets, rows and columns are left out, with a flag.
- Two shapes on one slide with the same name share an anchor.
- `ragnaroq index` does not run on Windows.
- From 0.2: a number both readers leave out is not seen.

## 0.2.2 — 26 November 2026

Measured on Indian tax documents for the first time: two CBDT documents, Circular 7/2026 and Notification 1/2026. See [references/](references/README.md#cbdt-set-022).

### Fixed

- **"Sth January" is no longer accepted.** Both readers read "5th January" as "Sth January" and agreed, losing the date. A day holding a letter readers take for a digit ("Sth", "lst", "3Oth") before a month name is now flagged (`date.confusable-day`, policy 0.5). On all four reference sets it flags no line that was right.
- **The answer key splits words at a visible gap.** Some PDFs place the next word a space apart without drawing a space ("2." then "Violation"); the answer key joined them, and scored correct readings as wrong. A gap of a fifth of the type size now separates words. 20 of 21,909 words change across the four sets.
- **Scans with a hidden text layer are not an answer key.** Many published circulars are scanned pages with the publisher's OCR laid invisibly over them ("Government of lndia"). `reference make` now leaves such pages out.

### Measured

Every set made again under one answer key (the scans are byte-identical) and scored with the 0.2.2 rules:

| Set | Wrong number, date or negation accepted | Lines sent to a person |
| --- | --- | --- |
| IRS (0.1 set) | 0 | 1,255 |
| Code table | 0 | 322 |
| Held-out IRS, with the third reader | 1 | 686 (1,126 with two readers) |
| CBDT, with the third reader | 1 | 74 (134 with two readers) |

The third reader settled 60 CBDT lines and 440 held-out lines, none wrong on a number, date or negation. The two remaining errors are numbers both readers left out.

### Known limits

- A number both readers leave out (a list number, small print in a title) is not seen.

## 0.2.1 — 26 November 2026

### Fixed

- **A "1" read as "|" is no longer accepted.** In text read from an image, a "|" standing alone (with a space or the line's edge on both sides) is now flagged for a person (`glyph.lone-bar`, policy 0.4). It is a printed "1", "I" or "l" that both readers mistook ("January |", "| declare that"), or a box or table border read as text. Two readers often make the same mistake, so their agreement did not catch it. The text layer is not affected: a "|" there is real text.
- The third reader can still settle such a line when the two readers disagree on it ("page |" against "page 1"). Its existing checks refuse any agreed reading that keeps a lone "|", and a "|" both readers agree on goes to a person.

### Known limits

- The third reader only sees lines the two readers disagree on, and never overrules two readers that agree. A "|" both readers agree on (most often a printed "1" both misread) therefore always goes to a person, even though the model could likely read it. Letting the model settle that one ambiguous character is a possible next step, to be measured first.

### Measured

On the stored readings, with nothing read again:

| Set | Accepted with a wrong number, date or negation | Lines sent to a person | Right lines newly sent to a person |
| --- | --- | --- | --- |
| Held-out, two readers | 6 → 1 | 1,072 → 1,126 | 0 |
| Held-out, with the third reader | 6 → 1 | 632 → 686 | 0 |
| IRS (0.1 set) | 0 → 0 | 1,237 → 1,255 | 0 |
| Code table | 0 → 0 | unchanged | 0 |

Every line newly sent to a person was already wrong. On the reference sets, a lone "|" never matched the answer key. The accepted-but-wrong rate on the held-out set falls from 12.5% to 10.7% with two readers, and from 11.4% to 9.8% with the third reader. The one remaining accepted error is the Schedule C title, where both readers missed the small "(Form 1040)". The held-out set had been used once for the 0.2.0 gate; this check only adds refusals, so it cannot make that result worse.

## 0.2.0 — 24 November 2026

A third reader for the scanned lines the two OCR readers still dispute.

### Added

- **A third reader.** A local model, PaddleOCR-VL-1.6, reads the crop of each scanned line that Tesseract and EasyOCR disagree on. A fixed rule then settles the line, or keeps it for a person. The line is accepted only when:
  - the model's reading matches one of the two OCR readings exactly (in the form `evaluate` scores with);
  - the validators pass;
  - no reading saw something the pair that agree missed: clearly more letters, a negation, or a number where the model wrote a layout mark such as a checkbox or `|` (omission);
  - no reading has an underscore inside a word or number (underscore);
  - the agreeing text does not share four words with the line above or below it in the same column, or the line before or after it in reading order (neighbour).

  A line that fails any check stays flagged, with `crosscheck.third-reader` and the check that held it back. Text is still never repaired: a settled line carries the agreeing OCR reader's text, and `resolution` records which readings agreed.
- **How to switch it on.** It is off by default. Install the extra, `pip install "ragnaroq[vlm]"` (install CPU-only `torch` and `torchvision` first; about 1.9 GB of model files, checked against pinned SHA-256 sums). Then add `--third-reader` to `ragnaroq convert` or `ragnaroq evaluate`.
  - `--vlm-models DIR` says where the model files live.
  - `--vlm-download` lets missing files be fetched. Without it a missing model stops the run at the start.
  - `--third-reader-budget SECONDS` limits the time it may spend on each document; lines not read stay flagged.
- **Its cost in time.** About 2.5 seconds for each disputed line on a 4-core CPU with native bfloat16 and no GPU (3.5 seconds on the 0.1 IRS set, 8.7 on the code table), and nothing for lines the two readers agree on; float32 CPUs are about 2.5 times slower. Each line is limited to 160 new tokens, which stops a model that repeats itself, and to 120 seconds, a safety net only. Readings are cached, so `evaluate` decides them again in seconds.
- **Third reader section in the evaluation report:** lines settled, how many were wrong, how many wrong on a number, date or negation, and how many each check held back.

### Changed

- **Policy 0.3**, with the third-reader rule.
- **Schema 0.2.0.** `Line.resolution` is new: the rule, the readings that agreed, and the reading whose text the line carries. `text` is the NFC-normalised `raw`, unless `resolution` names another reading. A stored 0.1.0 `document.json` still loads, and is restamped `0.2.0`.
- The comparison used to score and to match readings now lives in `crosscheck.py`, so the policy and `evaluate` use the same one.

### Fixed

- **The answer key joined words printed with no visible gap.** A label such as "20b" has its space overprinted in some PDFs, so the answer key said "20 b" while every reader read what is printed. Such words are now one word in the truth file (`reference make`). This affects scoring only, not reading.

### Measured

On a held-out set of 5 IRS documents the rule had never seen (20 references, 3,712 lines), read by Tesseract 5.3.4, EasyOCR 1.7.2 and PaddleOCR-VL-1.6, without and with the third reader:

| Measure | Without third reader | With third reader |
| --- | --- | --- |
| Lines sent to a person | 1,072 | 625 (41.7% fewer) |
| Sent to a person although right | 467 | 84 |
| Accepted by rule | 2,640 | 3,087 |
| Accepted but wrong | 330 (12.5%) | 350 (11.3%) |
| Accepted with a wrong number, date or negation | 6 | 6 |

- The third reader settled 447 lines. 20 were not exactly the answer key (spacing, punctuation, or a line box that also covers a neighbouring field's words). None was wrong on a number, date or negation.
- After the gate the neighbour check was tightened, which only holds lines back: the shipped rule, re-scored on the same readings, settles 440 lines (none wrong on a number, date or negation), sends 632 to a person (41.0% fewer than 1,072; 91 of them right), accepts 350 lines wrong (11.4%, against 12.5% without the third reader) and holds back 23 at the neighbour check.
- The first run used the 0.1 answer key and reported 6 settled lines wrong on a critical token. All were Schedule C labels printed "20b", "16b" and "16c" with no visible gap. The owner chose to fix the answer key and score again; the scans were byte-identical and nothing was read again. No rule or threshold was changed after seeing the held-out set.
- On the 0.1 IRS and code-table sets, before the build, the same rule was simulated on stored readings: lines sent to a person fell from 1,242 to about 650 (48% fewer) and from 322 to 178 (45% fewer), with no line settled wrong on a number, date or negation. The 0.1 numbers above were measured with the 0.1 answer key.

Sources, checksums and commands: [references/](references/README.md).

### Known limits

- **A "1" read as "|".** The 6 accepted lines wrong on a number, in both held-out runs, come from the two-reader rule, not the third reader. In 5, Tesseract and EasyOCR both read a "1" as "|" ("January |", "page |") and agree, so the line is accepted before the third reader is consulted. In the sixth, both missed the small "(Form 1040)" in the Schedule C title. The 0.1 result of none on the IRS set does not hold on these documents. Fixed in 0.2.1.
- The third reader covers scanned lines only, and only lines disputed by two readers. Text-layer pages and machines without EasyOCR are unchanged.
- A digit that one reader saw and the other two missed is outvoted.
- Where EasyOCR read nothing, Tesseract and the model settle the line between them.
- Negations such as "cannot", and contractions such as "don't", are not recognised as negations (this predates 0.2.0).
- Superscript digits are folded into plain digits in the two-reader comparison, so "80¹" agrees with "801" there (this predates 0.2.0).
- Pages over 16 million pixels at 300 dpi (A3 and larger; A4, Letter and Legal fit) are not read by the third reader; their disputed lines stay with a person.
- The per-line time limit and the budget are wall-clock limits, so on a slow or busy machine they can change which lines are read.
- The set is synthetic scans of IRS documents, a best case, as in 0.1.

## 0.1.1 — 23 November 2026

### Fixed

- **`convert()` failed from a script on stdin or a script with no main guard.** The page worker was started by multiprocessing's `spawn` method, which runs the caller's main script again in the worker. A script read from stdin (`python - < script.py`) has no file to run again, so the worker died. A script without `if __name__ == "__main__":` started a second conversion inside the worker, which Python refuses. Either way `convert()` raised `ConversionError`, wrongly blaming memory. The worker is now a new Python interpreter that imports ragnaroq and nothing of the caller's, so it no longer matters how the calling code is run. It is as fast as before.
- **A worker that stops now says how.** It reports its exit code or signal, and the last line it wrote, such as an import error. It suggests memory only when the worker was killed by a signal.
- Anything a library prints inside the worker goes to stderr and can no longer garble the pages sent back.

## 0.1.0 — 23 November 2026

Trustworthy PDFs: a measured number behind *accepted*, and limits for PDFs you did not choose.

### Added

- **Measuring.**
  - `ragnaroq reference make` turns born-digital PDFs into scan-like, image-only PDFs, each with a truth file of every word and its box. There are four profiles (`clean`, `low-res`, `blurred`, `tilted`) and `--pages`. The output is byte-identical for the same input.
  - `ragnaroq evaluate` reports:
    - accepted lines that are wrong, and those wrong on a number, a date or a negation;
    - right lines sent to a person;
    - character error rate, missed and invented lines;
    - a threshold sweep.

    It caches readings, re-decides them under the current rules, and `--max-critical-false-accepts` exits with code 3 for CI. See [docs/evaluating.md](docs/evaluating.md).
- **The first measurement**, on public-domain IRS forms and publications and a code-table stress test. [references/](references/README.md) holds the sources and checksums, the commands to rebuild them, and the results.
- **The code rule (policy 0.2).** In text read from an image, a code that mixes letters and digits is flagged when it holds a letter readers take for a digit (`R28CO`, `11320B`), or when it has a bracket the line never pairs (`4a)` read for `4(a)`).
- **Low-resolution scans** under 150 dpi are read at 300 instead of 200.
- **Strict mode**, `--same-letters`: two readings agree only with the same letters and digits. This is an option, not the default; see the measured trade-off in [references/](references/README.md).
- **Safety limits per page.** Pages are read in a worker process with a time limit (`--page-timeout`, 60 s), a memory limit (`--page-memory-mb`, 2,048 MB) and a character limit (200,000). A page that breaks one fails alone, flagged with the limit. `--no-isolation` reads in-process.

### Measured

These are synthetic scans of IRS documents, read by Tesseract 5.3.4 and EasyOCR 1.7.2:

| Measure | Value |
| --- | --- |
| Lines accepted without a person | 72.3% |
| Accepted with any mistake | 6.2% |
| Accepted with a wrong number, date or negation | none (5 before the code rule and low-resolution reading) |

### Known limits

- Synthetic scans are a best case: no real paper, stamps or handwriting yet.
- When readers differ by a letter, the first reader's letters are kept (`Hf you` for *If you*). Strict mode catches this at a cost; a third reader is the planned fix.
- 58% of the lines sent to a person are right.
- Tables are always flagged.
- Hindi has one reader.

## 0.0.4 — 23 November 2026

- Renamed from `pdfanchor` to `ragnaroq`: the package, the import path, the command and the engine name.
- PyPI release workflow (trusted publishing).

## 0.0.3

- EasyOCR as a second reader on scanned pages.

## 0.0.2

- Pages measured in their visible area (crop box), as PDF viewers show them.

## 0.0.1

- First working skeleton: text layer with boxes, Tesseract, cross-check, rule validators, decision policy, Markdown and chunk exports.
