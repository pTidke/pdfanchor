# 0.3: Folders and formats

Status: approved on 2026-11-26. Approach A.

## Why

RAGnaroq reads one PDF at a time. The aim, in the roadmap and in the owner's words: add ragnaroq to a project, point it at a folder, and the folder becomes ready for retrieval. Every file is read, checked and cited, and it loads straight into a vector database, because "people will add them to vector DBs for RAG".

0.3 is the step from one PDF to a folder of mixed files. It must keep the two things the PDF engine stands for:
- every piece of text is anchored to where it came from;
- nothing is accepted that a rule has not passed.

## Goals

1. **`ragnaroq index ./folder`.** It reads every supported file and writes `.ragnaroq/` beside them. The files themselves are never changed. A second run processes only what changed.
2. **New readers.** Word (.docx), Excel (.xlsx), PowerPoint (.pptx), HTML, Markdown, JSON, XML and CSV, alongside PDF. Each writes the same `document.json`, with a location suited to its type.
3. **Images.** Every image in any file is saved to an images folder and referred to in place with:
   - its location, for citation;
   - the saved file's path;
   - a short description. The description comes from the file's own caption or alt text when there is one, otherwise from the text OCR finds in the image. An optional local vision model can add one sentence, labelled as model-written.
4. **Chunks any vector database can load** without reshaping: Pinecone, Chroma, Qdrant, Weaviate, pgvector, LanceDB, Milvus.
   - stable UUID ids;
   - flat metadata;
   - a record of what changed since the last run, so a database can be kept in sync.
5. **A demo folder of awkward files,** made by a script in the repo, each with its expected text. Every reader is measured the way the PDF engine is.

## Non-goals

- Legacy binary formats (.doc, .xls, .ppt), and OpenDocument formats. Rich text, e-mail, archives (.zip) and audio are also out.
- Embedding the chunks and running a vector database. That is 0.6 ("Serve"). 0.3 makes the chunks ready for any database and shows how to load them.
- The metadata file and the chunking plan (0.4) and the review screen (0.5).
- Following links, or fetching remote images in HTML.

## Anti-goals

These would make 0.3 a failure even if it ran:

- **Silent loss:** an image dropped without trace, or text taken from an image without being checked.
- **Wrong values:** a spreadsheet value shown differently from how Excel shows it (0.05 for 5%) without the shown value being the text.
- **Uncitable text:** text that cannot be traced to its sheet and cell, slide, paragraph, key or element.
- **Unloadable chunks:** a chunk file a common vector database rejects (nested metadata, nulls, non-UUID ids).
- **Duplicates and stale chunks:** re-running a folder duplicates chunks in a vector database, or leaves chunks of deleted files behind.
- **Unsafe input:** a hostile file (a zip bomb disguised as .xlsx, an XML entity bomb, a script in HTML) that hangs or crashes the run, or runs anything.

## Constraints

- **PDF output stays the same.** Every existing PDF guarantee holds: the safety limits, the policy, the third reader and the evaluation numbers. A 0.2 `document.json` still loads.
- **Core stays light and offline.** New readers that need a dependency come as extras. The core install stays free of torch, and nothing reaches the network.
- **One format.** The output format is versioned. This is schema 0.3.0, and the published schema stays in sync (an existing test checks it).
- **Lint and tests.** `ruff check` and `pytest` must pass. CI does not install heavy extras; reader tests that need one are skipped without it.

## Approaches

| | Approach | Trade-off |
| --- | --- | --- |
| **A (chosen)** | One output format. Each line's location widens to suit its file type, and images become their own kind of item. | One format change touching every module, then one code path for chunks, rules, scoring, review and serving. |
| B | Keep the PDF format, add a second one for other files, and merge only at the chunks. | Less change now. Two of everything downstream, and image handling built twice. |
| C | Convert every file to PDF (LibreOffice) and run the PDF engine. | Little new code. But exact text becomes OCR with errors, sheet and cell citations become page boxes, and it needs a ~500 MB install. |

The owner chose A, with the requirement that the result be compatible with vector databases.

## Design

### 1. The output format (schema 0.3.0)

The document keeps its shape, so PDF output is unchanged apart from new optional fields.

**Parts.**
- `Document.pages` stays the list of parts of a document. It now holds a PDF page, an Excel sheet, a slide, or the body of a Word, HTML, Markdown, JSON, XML or CSV file.
- `Page.kind` keeps `native`, `scanned`, `blank` and `failed` for PDF, and gains `sheet`, `slide` and `body`.
- `Page.name` is new: the sheet name, the slide title, or the file name.
- `width` and `height` become optional: none for a part without a page size.

**A location on every line.** `Line.anchor` is a small typed object, a pydantic union:

| Type | Anchor | Example |
| --- | --- | --- |
| PDF | page and box (as today) | `{"type": "pdf", "page": 3, "box": [72, 140, 520, 152]}` |
| Excel | sheet and cell, or a cell range for a merged cell | `{"type": "xlsx", "sheet": "Rates", "cell": "C7"}` |
| PowerPoint | slide, shape, and notes or not | `{"type": "pptx", "slide": 4, "shape": "Title 1"}` |
| Word | paragraph number, or table and cell | `{"type": "docx", "paragraph": 18}` |
| HTML | path of the element | `{"type": "html", "path": "main/table[1]/tr[3]/td[2]"}` |
| Markdown | line range | `{"type": "md", "lines": [40, 42]}` |
| JSON | JSON Pointer | `{"type": "json", "pointer": "/deductions/80C/limit"}` |
| XML | XPath | `{"type": "xml", "path": "/notice/section[2]/para[1]"}` |
| CSV | row and column | `{"type": "csv", "row": 12, "column": "Amount"}` |
| Image | the anchor of the place it sits, plus `image` set to its saved path | |

- `Line.page` and `Line.box` stay. `box` becomes optional (none outside PDF), and `page` is the index of the part.
- Every anchor has a short human-readable form for citations: `Rates!C7`, `slide 4`, `¶18`, `/deductions/80C/limit`, `p. 3`.

**The source of the text.** `Line.source` names where the text came from: `native` and `ocr:<engine>` as today, plus `docx`, `xlsx`, `pptx`, `html`, `md`, `json`, `xml` and `csv`.

**Excel values.** An Excel line's `text` is the value as Excel displays it. The stored value and any formula are kept in `Line.value` (new, optional): `{"stored": 0.05, "formula": "=B7/B2", "format": "0%"}`.

**Images.** `Figure` is a new item on a part. It has:
- `id` and `anchor`;
- `path`, relative to the file's output folder;
- `width` and `height` in pixels;
- `description`;
- `description_source`: `caption`, `alt`, `ocr` or `model:<name>`;
- `ocr_lines`: lines, so the text read from an image goes through the same two-reader rules as a scanned page.

**Old documents.** Documents of schema 0.1.0 and 0.2.0 load and are restamped 0.3.0, as in 0.2.

### 2. Readers

**One interface.** Each reader is `read(path or bytes, config) -> Document`, behind a registry chosen by the file's content and extension. Content is checked first: an .xlsx must be a zip with an Excel manifest.

| Format | Reader | Extra |
| --- | --- | --- |
| PDF | the existing engine | core |
| .docx, .pptx, HTML, Markdown | Docling (`docling-slim`, MIT, no torch) | `ragnaroq[office]` |
| .xlsx | openpyxl, read directly, so the displayed value can be rendered (Docling gives the stored value) | `ragnaroq[office]` |
| JSON, CSV | standard library | core |
| XML | defusedxml, against entity bombs | core |

**Excel display.**
- A number format renderer covers the formats in common use:
  - General, integers and decimals, thousands separators;
  - Indian lakh and crore grouping (`[>=10000000]##\,##\,##\,##0;…`);
  - percent, currency including ₹, dates and times, and text.
- A format it does not know gives the stored value as text, with the flag `xlsx.format-unknown`.

**Structure.** Headings come from Word styles, slide titles, HTML and Markdown headings, and JSON and XML keys. Tables become `Table` items with cells, each cell carrying its anchor.

**Excel layout.**
- Each visible sheet is a part, and its used range is a `Table`.
- Every non-empty cell is a line anchored to its cell, or to its range for a merged cell.
- The first non-empty row is taken as the header row. A table chunk repeats it above each group of rows, so every chunk can be read on its own.

**Exact anchors come first.** Docling is used where it exposes each element's exact place. Where it does not, the reader goes to the library underneath instead:
- python-docx or python-pptx for Word paragraph numbers and slide shapes;
- markdown-it-py for Markdown line ranges;
- an HTML parser that keeps element paths.

All are MIT or BSD licensed, and Docling already depends on the Word and PowerPoint ones.

**Text inside an image.** OCR runs on the saved image with the existing OCR engines. Each line read gets the image's anchor plus `box`, in image pixels.

### 3. Corner cases (decided)

| Case | Decision |
| --- | --- |
| Excel hidden sheets, rows and columns | Left out, like PDF text hidden outside the page. A document flag names what was left out. |
| Excel formula with no cached value (saved without calculation) | No text is invented. The line is flagged `xlsx.no-cached-value`. |
| Excel merged cells | One line, anchored to the range (`B2:D2`). |
| Word tracked changes | Text as it reads with the changes accepted. A document flag says changes are pending. |
| Word comments | Left out of the text, and flagged. |
| Word footnotes and endnotes | Kept, each anchored to its note. |
| PowerPoint speaker notes | Kept as `notes` lines anchored to their slide. |
| HTML script, style and template | Ignored. Remote images are not fetched: an image reference is kept with its URL and no file. |
| CSV | The delimiter is detected. Encodings tried in order: UTF-8 (with or without BOM), UTF-16 (with BOM), then cp1252 with the flag `encoding.guessed`. |
| JSON | Each scalar is a line at its pointer. An array of similar objects is a table. |
| Duplicate keys in JSON | The last one wins, as in Python, and the document is flagged. |
| A file that cannot be read | It fails alone, with a reason in the manifest. The run goes on. |
| Charts stored as data (Word, Excel and PowerPoint charts, not pictures) | Not read in 0.3. Each is flagged `chart.not-read` with its anchor, so nothing is silently missing. |

### 4. Trust (policy 0.6)

- **Exact text** (any source other than `native` or `ocr:*`) is accepted unless a validator flags it. The OCR misreading checks don't run on it, because it was not read from a picture.
- **Table cells** from Word, Excel, HTML, CSV and JSON are accepted like other exact text. The `table.structure-unverified` flag stays for PDF tables only, where the structure is guessed from the layout.
- **Text read from an image** follows the scanned-page rules: two readers, and the third reader if it is switched on.
- **A description from a caption or alt text** is exact text from the file. An OCR description follows the OCR rules. A model-written description is never `accepted`: it is a label, kept as `model`, and excluded from the critical-token rules.

### 5. Images

- **Saving.** Images are saved to `.ragnaroq/files/<file-id>/images/<anchor-based name>.<ext>` in their original format. They are converted to PNG only when that format cannot be shown on the web.
- **Small images.** Images smaller than 32×32 pixels (bullets, spacers) are not saved. They are counted in a document flag.
- **References in the text.** In `document.md` the image appears where it sits:

  ```
  ![<description>](images/slide-4-picture-2.png) *(slide 4, Picture 2)*
  ```

- **Image chunks.** An image is also its own chunk (`kind: "image"`) whose embedding text is its description. A search for "the bar chart of collections" can find it.
- **Model-written descriptions.** The extra `ragnaroq[describe]` with `--describe-images` adds one sentence per image. The model is chosen by a measured spike (candidates: small vision-language models native in transformers, Apache or MIT licensed, under 2B parameters). It is pinned to a revision and checksummed like the third reader, with a time limit per image, and switched off by default.

### 6. The folder

```
my-folder/
  ...the files, untouched...
  .ragnaroq/
    manifest.json        every file: path, sha256, size, reader and version, settings hash,
                         status counts, chunk ids, error
    changes.json         this run against the last: files and chunk ids added, changed, removed
    chunks.jsonl         every chunk of the folder, ready for a vector database
    files/<file-id>/     document.json, document.md, chunks.jsonl, images/
```

- **File ids.** A file's id is a UUID derived from its path relative to the folder, so the same file keeps its slot when edited.
- **When a file is processed again:**
  - when its sha256, its reader's version or the settings that change the output have changed;
  - a removed file's folder is deleted, and its chunks go into `changes.json` as removed.
- **What is skipped:**
  - `.ragnaroq/` itself;
  - hidden files and folders;
  - symbolic links that lead outside the folder;
  - files over the size limit.

  Everything skipped is listed in the manifest with the reason.
- **Files the readers don't support** are listed in the manifest as skipped, never silently ignored.
- **Errors.** One file's failure never stops the run. The exit code says whether anything failed or needs a person.
- **Safety limits per file:**
  - the PDF limits as today;
  - for the zip-based Office formats: a cap on the total uncompressed size and the number of entries, checked before a reader opens the file;
  - for XML, defusedxml;
  - a time limit per file, in the existing isolated worker. The worker gains a whole-file request beside its PDF page request.

### 7. Chunks for vector databases

Every line of `chunks.jsonl` is one chunk (the comments here are explanation, not part of the file):

```json
{
  "id": "6f1c9a52-…",                    // UUID, stable for the same file content and chunk
  "text": "…",                           // what to show
  "embedding_text": "Title > Heading\n…",// what to embed
  "metadata": {                          // flat: strings, numbers, booleans only; no nulls, no nesting
    "file_id": "…", "path": "rates/2026.xlsx", "file_type": "xlsx", "file_sha256": "…",
    "kind": "table", "status": "accepted", "rules": "", "cite": "2026.xlsx, Rates!A1:D12",
    "part": "Rates", "part_index": 1, "page_first": 0, "page_last": 0,
    "heading": "Rates for TY 2026-27", "title": "…", "chars": 812,
    "anchors_json": "[{\"type\":\"xlsx\",\"sheet\":\"Rates\",\"cell\":\"A1:D12\"}]",
    "image_path": "", "schema_version": "0.3.0"
  },
  "anchors": [ … ]                       // the full anchors, for stores that keep JSON
}
```

- **Flat metadata.** `metadata` holds only flat values, with no nulls. A missing value is an empty string or 0, so every chunk has the same keys and typed stores (LanceDB, Weaviate, Milvus) see one schema.
- **Lists as strings.** Lists become comma-joined strings, for example `rules`. The full anchors travel as a JSON string, `anchors_json`.
- **Stable ids.** An id is a UUIDv5 of the file's content hash and the chunk's line ids, so re-indexing unchanged files gives the same ids, and an upsert does not duplicate. Ids are valid for Qdrant and Weaviate (UUID) and for Pinecone, Chroma, pgvector and Milvus (string).
- **Keeping a database in sync.** `changes.json` lists the chunk ids added, changed and removed. Every chunk also carries `file_id`, so a store can delete a file's chunks by filter.
- **Anchors of a chunk.** For a PDF, as today: one per page, with the box around the chunk's lines. For an Excel table chunk: the cell range it covers. For any other file: one per part touched, holding the first and last line's anchors, as `from` and `to`. `cite` is the short form, for example `2026.xlsx, Rates!A1:D12` or `guide.docx, ¶18–¶24`.
- **Size.** Chunks stay under the size limit (`chunk_chars`, 1,200 by default). A metadata object stays well under Pinecone's 40 KB limit; `anchors_json` is truncated, with a flag, if a chunk would exceed it.
- **One file for the whole folder.** `.ragnaroq/chunks.jsonl` concatenates every file's chunks, so the whole folder can be loaded at once.
- **Format checked in CI.** A test checks every chunk of the demo folder against the rules above: UUID ids, flat metadata, the same keys on every chunk, no nulls.
- **Real loading, not in CI.** Optional tests (skipped without the clients) load the demo folder's chunks into an in-memory Qdrant and a local Chroma, upsert them twice and check the count does not change.
- **Docs.** `docs/vector-databases.md` shows a few lines each for loading into Qdrant, Chroma, Pinecone and pgvector, and for syncing with `changes.json`.

### 8. The demo folder and measuring

- **The script.** `references/make_demo_folder.py` writes `references/demo/`. The files are committed, because Office files embed timestamps and cannot be made again byte for byte; the script records how they were made. It writes with about ten files made with the same libraries the readers use. Each file is made on purpose to hold one or more of the corner cases in section 3, and comes with `<file>.expected.jsonl`: one line per anchor, with the expected text.
  - **Word:** headings, a table, a footnote, tracked changes, a comment, an embedded chart image with alt text.
  - **Excel:** percent, ₹ lakh grouping, dates, a formula, a merged cell, a hidden sheet, an unknown number format.
  - **PowerPoint:** a title, bullets, speaker notes, a picture with no alt text but text in it.
  - **HTML:** a table, an image with alt text, a script, a remote image.
  - **Markdown:** a table, code, an image.
  - **JSON:** nested keys, an array of records, a duplicate key.
  - **XML:** nested elements, attributes, a declared entity, which must not expand.
  - **CSV:** a semicolon delimiter, cp1252 text, a quoted newline.
- **Scoring.** `ragnaroq evaluate` gains a mode for these files. A folder holding `*.expected.jsonl` is scored in this mode. It matches expected and actual lines by anchor and reports:
  - wrong text;
  - missing anchors;
  - extra anchors;
  - images saved and described.

  The PDF scoring is unchanged.
- **Delivery.** A zip of the demo folder goes to the owner, to unpack into `Downloads/ragx`.
- **The bar for 0.3.** For every exact-text format, the demo folder scores 100%: every expected anchor present, with the right text. The only exception is a case the design says to flag, which must be flagged.

### 9. Release

- 0.3.0, with the schema 0.3.0 and policy 0.6.
- The README, roadmap, `docs/evaluating.md` and the new `docs/vector-databases.md` explain:
  - the folder;
  - the formats and their anchors;
  - images;
  - loading into vector databases.

## Left to the builder

- **Code structure:** module layout (`readers/` package), names, and how the registry picks a reader.
- **Number formats:** the exact set the Excel renderer covers, beyond the list in section 2, guided by the demo file.
- **Docling:** how Docling's output is turned into lines and anchors, and how a Docling element's position becomes the anchor.
- **Folder index:** the manifest's exact layout, and the size limits' default values.
- **Description model:** the spike that picks it, its revision pin and its default time limit. It can be the last task, and can move to 0.3.1 if the spike finds no model good enough.
- **Order:** the order of the tasks, as long as the format change comes first and the demo folder comes before each reader.

## Calls made in writing this

These were not discussed and are open to change:

1. **The name `pages` stays.** The list of parts keeps the name `pages`, so 0.2 consumers keep working. The alternative is to rename it `parts` in 0.3.0.
2. **Hidden Excel content is left out,** with a flag, as with hidden PDF text. The alternative is to include it, marked as hidden.
3. **Word tracked changes** are read as if accepted, with a flag.
4. **Exact text from a file's own content is accepted without a second reader.** Only image text needs two readers.
5. **Chunk metadata uses empty strings and zeros, not nulls,** so every chunk has the same keys.
6. **Model-written image descriptions** may slip to 0.3.1 if the spike finds no suitable model.
7. **Excel lines are cells,** anchored one by one, with a sheet's used range as a table. Table chunks repeat the header row.
8. **Exact anchors come first.** Where Docling does not give an exact position, the reader uses the library underneath (python-docx, python-pptx, markdown-it-py, an HTML parser).
9. **Charts stored as data are flagged, not read,** in 0.3.
10. **`ragnaroq index` passes the existing options through** (`--ocr`, `--third-reader` and the rest) for PDFs and images.
