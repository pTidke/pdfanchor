# 0.4: Metadata and chunking strategies, by rule

Status: draft for the owner's approval, 2026-11-27. Approach B. Builder check done; the chunk filter keys are lists of strings (owner's choice).

## Why

RAGnaroq is a public library for anyone who wants a folder of files to be ready for retrieval. In the owner's words from the roadmap: add ragnaroq to a project, point it at a folder, and the folder becomes ready for retrieval, with every file read, checked, described and indexed. 0.3 does the reading, the checking and the loading into a vector database. 0.4 does the describing, and uses the description to cut each document the way it should be cut.

Today every document is cut the same way: paragraphs joined up to `chunk_chars`, a long paragraph left whole however long it is, and tables in groups of rows. That serves prose, but not a notice whose provisos must stay with their paragraph, a FAQ whose questions must stay with their answers, a manual whose sections are its topics, or a deck whose slides are its units. The roadmap and `docs/chunking.md` set out the answer, agreed with the owner on 23 November 2026: describe each document in `metadata.json`, choose a chunking strategy for it from that description by fixed rules, and write the choice and its reasons down.

The roadmap builds the description with LangExtract and a local model. The owner chose to build this release by rule first (approach B below), and to keep `metadata.json` in it: every field a rule can find and ground goes in now, and LangExtract fills the fields that need understanding in the next release. Rules first is also the plan's own principle: there is always a choice that can be explained and repeated, and the model stages later have a measured baseline to beat.

The library is for general use. Nothing here is tuned to one domain or one application.

## Goals

1. **`metadata.json`.** For every document, a description made by rules, in which every value is grounded: it names the lines it came from, their citation and their status. For a folder, a master file with a catalogue of the documents, the links between them, and a glossary.
2. **Eight chunking strategies,** each chosen for a document by a fixed rule over its metadata: single, question-answer, numbered paragraphs, sections, table rows, slides, records, sentences.
3. **Additions to every strategy:** definitions as chunks of their own; a parent for every chunk (small-to-big); dates, periods and references as filters; a sentence with a reference never split; one script per chunk.
4. **Chunks that never cut a word or a sentence.** A long paragraph is split between sentences, with the previous sentence repeated. A fragment too small to stand alone joins its neighbour.
5. **The plan written down,** in `chunking-plan.json` and `chunking-plan.md`: for every document, the strategy, its parameters and the reasons, each reason naming the metadata field behind it. A person can override the choice in `ragnaroq.toml`.
6. **Measured.** `ragnaroq evaluate --chunking QUESTIONS` searches the chunks with a built-in keyword search and reports where the passage that answers each question ranks. 0.4's chunks are compared with 0.3's on the same questions.

## Non-goals

- **LangExtract and any language model.** Issuer, parties, entities, topics, the document's own type ("circular", "invoice") and its main date need understanding; they are the next release, with entity filters on chunks.
- **The model's second opinion on the plan,** and `ragnaroq plan --accept`. Next release, with the questions-file check built here as its referee.
- **Embeddings, a vector store and the MCP server** (0.6). The keyword search here exists only to measure.
- **The review screen** (0.5).
- **Domain profiles** (tax, legal, medical). The rules use general patterns; a domain can add its own later.

## Anti-goals

These would make 0.4 a failure even if it ran:

- **A cut word or sentence.** The one exception is a single sentence longer than `chunk_chars`, which is split between words and flagged.
- **A proviso or an explanation separated from its paragraph,** or a question from its answer.
- **An ungrounded value:** a metadata value that does not name its lines, or a value from a flagged or blocked line presented as if it were accepted.
- **Different output for the same input.** The same folder and settings give the same metadata, plan and chunks, byte for byte.
- **A model needed for anything.**
- **Reading a file again because the chunking changed.** Reading is the expensive part (OCR); metadata, plan and chunks are made again from the stored `document.json`.
- **Worse retrieval than 0.3** on the questions set.
- **A hostile file that stalls a run:** every rule is linear in the text, and every list has a limit.

## Constraints

- Python 3.12 or newer. No new dependency in the core: the rules are plain Python and regular expressions, and `ragnaroq.toml` is read with the standard library's `tomllib`.
- Offline and deterministic, as the rest of the core.
- Chunks keep the rules of 0.3: a UUID `id`, flat metadata with no nulls and no nesting, the same keys on every chunk, `anchors_json` capped at 20,000 bytes, metadata well under Pinecone's 40 KB. One rule changes, by the owner's choice: the four filter keys of section 5 are lists of strings, so stores can filter on them.
- `ragnaroq index` stays POSIX-only, as in 0.3. `ragnaroq convert` works anywhere.
- `chunk_chars` stays the one size setting, 1,200 by default. `docs/chunking.md`'s 2,000 predates 0.3, which chose 1,200 for the stores' limits; `chunking.md` is corrected.

## Approaches

| | Approach | Trade-off |
| --- | --- | --- |
| A | **Metadata first, as the roadmap orders it.** LangExtract fields and rule-made fields in `metadata.json`; chunking stays as in 0.3. | The model part can only be tested here with a stand-in (Ollama's registry is blocked in the build environment; Hugging Face is reachable). Better chunks wait for the next release. |
| **B** | **Strategies first, on rule-made metadata.** `metadata.json` holds every field a rule can find and ground; the eight strategies and the plan are built on it; LangExtract and the model's second opinion come next and only refine. | **Chosen by the owner.** Everything is measurable in the build environment, and every user gets better chunks without installing a model. Until the model arrives, a prose document's kind is the rules' guess. |
| C | **Both in one release.** | Twice the size, with the model half untestable here; rejected. |

## Design

### 1. The output format (schema 0.4.0)

`document.json` changes in one way:

- **`Line.level`** (integer, 0 for a line that is not a heading). The heading level, 1 the highest. Readers that know it set it: Word (`heading N`, and `Title` as 1), HTML (`h1`–`h6`), Markdown (`#` count), PowerPoint (slide title 1), JSON and XML (the key's or element's depth, capped at 6). For PDF, `structure.apply` ranks the distinct font sizes of the lines it marks as headings, the largest as 1. A 0.3 `document.json` still loads, with every heading at level 1, and is restamped `0.4.0`.

Two files are new: `metadata.json` (section 2) and `chunking-plan.json` with `chunking-plan.md` (section 4). Chunks gain keys (section 5). `schemas/metadata.schema.json` describes `metadata.json`, and `ragnaroq schema --metadata` prints it.

### 2. `metadata.json`

**A grounded value.** Every value in a document's entry has this shape:

```json
{ "value": "2026-01-05", "text": "5th January, 2026", "line_ids": ["p1-l7"], "cite": "p. 1, ¶2", "status": "accepted" }
```

- `text` is the words as they stand in the line; `value` is the normalised form, or `""` when it cannot be normalised without guessing.
- `status` is the lowest status of the lines it came from (blocked, then flagged, then accepted). A consumer that trusts only what passed the rules filters on it.
- Values come only from lines that are not excluded (`--exclude-script`).

**One entry per document**, in reading order of the fields below:

| Field | What it holds | How it is found |
| --- | --- | --- |
| `path`, `format`, `sha256` | Where the file is, its format, its content hash | From the source |
| `title` | A grounded value | The first heading; else the first line of the first part; the file name when there is no line |
| `kind` | `spreadsheet`, `slides`, `data`, `faq`, `numbered`, `sectioned`, `prose`, with `because`: the counts that decided it | The kind rules (section 3) |
| `dates` | Every date in the text, grounded | Day month year (the date validator's pattern), month day year, ISO `2026-01-05`, and numeric `05/01/2026` forms. `value` is ISO when unambiguous: a month name, ISO order, or a day over 12; else `""` |
| `periods` | Year spans and parts of years | `2026-27`, `2026–2027`, `FY 2025-26`, `AY 2026-27`, `financial year 2025-26`, `Q3 2026`, `H1 2026`, `calendar year 2026`. `value` is the span (`2026-04-01/2027-03-31` only where the form says where the year starts; else `2026/2027`) |
| `amounts` | Currency amounts and percentages | A currency symbol or code (`₹`, `Rs.`, `INR`, `$`, `USD`, `€`, `EUR`, `£`, `GBP`, …) next to a number, with Indian and Western digit grouping and `lakh`, `crore`, `million`, `billion`; a number with `%` or `per cent`. `value` is `{"amount": 150000, "currency": "INR"}` or `{"percent": 5}` |
| `references` | Citations, grounded | A unit and a number: `section 10(46)`, `s. 3`, `Article 21`, `Rule 3`, `clause (b)`, `regulation 4`, `paragraph 2`, `Chapter IV`, `Part II`, `Schedule II`; a numbered instrument: `Notification No. 1/2026`, `Circular 7/2026`, `Order No. …`, `the Income-tax Act, 1961`; a web address. `value` is the normalised reference (`section 10(46)`) |
| `files` | Mentions of other files in the folder | A file name, or a path relative to the folder, that matches a file the index holds. Folder runs only |
| `structure` | Counts | `headings`, `heading_depth`, `numbered_paragraphs`, `provisos`, `explanations`, `qa_pairs`, `definitions` (each grounded, with its term), `tables`, `table_share` (characters in tables over all characters), `figures`, `slides`, `records` |
| `length` | `chars`, `parts` (pages, sheets, slides, or 1) | From `document.json` |
| `scripts` | The scripts present, with the share of characters in each, and which are excluded | From each line's `scripts` |
| `trust` | Shares of lines accepted, flagged and blocked | From each line's status |
| `chunking` | The strategy chosen, its parameters, the reasons, and whether the rules or an override chose it | Section 4 |

**Detection rules** (general patterns, not one domain's):

- **Numbered paragraph:** a body paragraph that starts with a number or letter in a numbering form: `1.`, `1.1`, `(1)`, `1)`, `(a)`, `(i)`, `I.`, `Article 5`, `Section 3.`, `Clause 4`. A run of at least two in sequence counts; a lone `1.` in prose does not.
- **Proviso:** a paragraph that starts `Provided that`, `Provided further that`, `Provided also that`. **Explanation:** one that starts `Explanation` (with an optional number and a dash, colon or full stop). Both are kept with the numbered paragraph before them.
- **Question-answer pair:** a question followed by at least one body paragraph before the next question. A question is a paragraph that starts `Q.`, `Q:`, `Q1.` or `Question`; a heading that ends in `?`; or a paragraph of at most 200 characters that ends in `?` and is followed by one that does not. An `A.`/`A:`/`Ans.`/`Answer` paragraph is the answer. (No reader but PDF knows bold, so bold is not used.) The `faq` kind needs at least 3 pairs holding 40% of the text, so a rhetorical question in prose does not make a FAQ.
- **Definition:** a sentence of the form `"term" means …`, `term means …`, `"term" shall mean …`, `"term" includes …`, `term: means …`, or a two-column table whose header is `Term`/`Definition` (or `Word`/`Meaning`). The term is recorded as written.
- **Record:** for JSON, each element of the top-level array, or each value of the top-level object when there is no array; for XML, each child of the root when the children repeat one element name. A JSON array of flat objects with the same keys is already a table in `document.json` (the JSON reader makes it one), and is cut as a table, not as records.

**Limits.** At most 1,000 values for each list field of one document (dates, periods, amounts, references, files, definitions); past it the list is cut and `metadata.list-limit` names the field. Every pattern is linear: no nested quantifiers.

**The folder's master file**, `.ragnaroq/metadata.json`:

```json
{
  "schema_version": "0.4.0",
  "documents": [ /* every document's entry, by path */ ],
  "links": [ { "from": "a.pdf", "to": "b.docx", "text": "b.docx", "line_ids": ["p2-l4"], "cite": "p. 2" } ],
  "glossary": [ { "term": "assessee", "path": "act.pdf", "line_ids": ["p1-l12"], "cite": "p. 1, ¶3", "status": "accepted" } ]
}
```

`links` come from `files`; a reference to a numbered instrument links to a document only when that document's title holds the same normalised reference. The glossary lists every definition, sorted by term, then path.

Each file's own entry is also written beside its `document.json` (`files/<id>/metadata.json` in a folder, `metadata.json` beside `document.json` for `convert`).

### 3. The kind and the strategy rules

**Kind**, by the first rule that matches:

| # | When | Kind |
| --- | --- | --- |
| 1 | Format is xlsx or csv | `spreadsheet` |
| 2 | Format is pptx | `slides` |
| 3 | Format is json or xml | `data` |
| 4 | At least 3 question-answer pairs, holding at least 40% of the body characters | `faq` |
| 5 | At least 3 numbered paragraphs, or any proviso or explanation with at least 2 numbered paragraphs | `numbered` |
| 6 | Heading depth 2 or more, or at least 3 headings over at least 3,000 characters | `sectioned` |
| 7 | Tables hold at least 60% of the characters | `spreadsheet` |
| 8 | Otherwise | `prose` |

**Strategy**, by the first rule that matches (the eight of `chunking.md`):

| # | When | Strategy |
| --- | --- | --- |
| 1 | Under 1,500 characters in all, and no table over `chunk_chars` | `single` |
| 2 | Kind `faq` | `question-answer` |
| 3 | Kind `numbered` | `numbered-paragraphs` |
| 4 | Kind `sectioned` | `sections` |
| 5 | Kind `spreadsheet` | `table-rows` |
| 6 | Kind `slides` | `slides` |
| 7 | Kind `data`, unless the whole body is one table (then `table-rows`) | `records` |
| 8 | Otherwise | `sentences` |

The thresholds above are the starting values. They are tuned on the tuning half of the questions set only (section 7) and then fixed in code; the plan records the values that applied. A table in a document of any strategy is cut as `table-rows` cuts it.

**PDF tables stay one chunk each,** as in 0.3: a PDF's lines are not matched to its table's cells, so a PDF table cannot be cut into rows yet, and it is the one exception to `chunk_chars` besides a long sentence. It gets a summary chunk like any table. Cutting PDF tables into rows is later work.

**What each strategy makes** (every chunk at most `chunk_chars`, except as section 5 says):

- **single:** the whole document, tables included, as one chunk.
- **question-answer:** one chunk per question with its answer; a long answer is split between sentences, each piece repeating the question.
- **numbered-paragraphs:** one chunk per numbered paragraph, with its provisos and explanations, and its section's heading path as parent. Paragraphs shorter than the minimum join the next one in the same section; a long one is split between sentences, every piece repeating the paragraph number.
- **sections:** one chunk per section (a heading and what follows until a heading of the same or a higher level); a long section is split between paragraphs, then between sentences. Every chunk carries its heading path.
- **table-rows:** as 0.3 cuts tables (groups of rows under the header row, only the rows and columns that hold a cell). New: the embedded text labels each cell with its header (`Sheet › Column: value`), while `text` stays the Markdown grid; and one summary chunk per table, made by rule: its sheet or caption, its columns, its row count and its range.
- **slides:** one chunk per slide: its title, its text and its speaker notes; a long slide is split between paragraphs.
- **records:** one chunk per record, labelled with its path (JSON pointer or XPath); records shorter than the minimum join their neighbours under the same parent.
- **sentences:** paragraphs joined up to `chunk_chars`; a paragraph over it split between sentences with the previous sentence repeated.

**Overrides.** `ragnaroq.toml` at the folder's root may name a strategy for a file or a pattern (`fnmatch` on the POSIX path from the root; the first match in file order wins):

```toml
[chunking]
"faq/*.pdf" = "question-answer"
"notes/*.md" = { strategy = "sentences", max_chars = 800, min_chars = 100 }
```

An override sets `strategy`, and may set `max_chars` (200 to 4,000; default `chunk_chars`) and `min_chars` (0 to half of `max_chars`; default 200) for the files it matches. Nothing else is settable: each strategy's parent and what it keeps together are fixed, and shown in the plan. A strategy name that is unknown, a key that is not one of these three, or a value outside its limits, stops the run at the start with a message naming the line. `ragnaroq convert` takes `--strategy NAME`. The override that applies to a file is part of that file's chunking key (section 6), so changing it remakes that file's chunks only.

### 4. The plan files

`chunking-plan.json`, at `.ragnaroq/`, is a list in path order:

```json
{ "path": "circulars/circular-3-2026.pdf", "strategy": "numbered-paragraphs", "chosen_by": "rules",
  "parameters": { "max_chars": 1200, "min_chars": 200, "parent": "section", "keep_with_parent": ["proviso", "explanation"] },
  "reasons": ["kind = numbered (numbered_paragraphs = 14, provisos = 3)", "length.chars = 9120 > 1500"],
  "filters": ["dates", "periods", "references"] }
```

`chosen_by` is `rules` or `override`. `chunking-plan.md` says the same for a person: one short section per document, and a table of how many documents took each strategy. `convert` writes the document's plan inside its `metadata.json` (`chunking`), not as separate files.

### 5. Chunks (format 0.4.0)

The 0.3 rules all hold, but one. Every chunk now also has these metadata keys; a scalar key is `""` or `0` when empty:

| Key | Holds |
| --- | --- |
| `strategy` | The strategy that made the chunk |
| `parent_id` | A UUID of the chunk's parent (section, numbered paragraph, table, slide, record parent), made like a chunk id from the file and the parent's anchor; every chunk with the same parent has it |
| `parent_cite` | The parent's citation |
| `heading_path` | The heading path, `Part II › Section 4` |
| `dates` | List of the ISO dates found in the chunk, in order, without repeats |
| `date_first`, `date_last` | The first and last of them as `YYYYMMDD` integers, for range filters |
| `periods` | List of the normalised periods |
| `references` | List of the normalised references |
| `terms` | List of the defined terms the chunk uses |
| `scripts` | The chunk's script |
| `part_of` | `n/m` when a unit was split into m pieces |

**The list keys** (`dates`, `periods`, `references`, `terms`) are the one change to 0.3's rule that every metadata value is a scalar, chosen by the owner so that stores can filter on them: Pinecone with `$in`, Chroma with `$contains` (lists since February 2026), Qdrant and pgvector natively. Each is a list of strings, never empty: Chroma refuses an empty list, so a chunk with none holds `[""]`, and every chunk keeps the same keys. Each list holds at most 50 values. `docs/vector-databases.md` names the Chroma release that first takes lists, and shows a filter on each store. The CI test of chunk shape checks: scalars or non-empty lists of strings only, no nulls, the same keys everywhere.

Kinds of chunk: `text`, `table`, `image` (as 0.3), and new `definition` (a definition alone, with its term in `terms`) and `summary` (a table's summary). The metadata keys are the same on every chunk, as in 0.3.

**Splitting.**
- **A paragraph's lines are joined first.** A PDF line is one printed line, so the lines of a PDF paragraph are joined with a space before sentences are found; a printed line's end is never a sentence end. A line that starts with a list marker (`•`, `◦`, `▪`, `-`, `*`, `–`, or a numbering form) starts a new sentence.
- **Sentences** end at `.`, `?`, `!` or `;` followed by a space and a capital, a digit or an opening bracket or quote, or at the paragraph's end; not after a known abbreviation (`No.`, `Nos.`, `Rs.`, `Sec.`, `S.`, `Art.`, `Cl.`, `e.g.`, `i.e.`, `viz.`, `etc.`, `Mr.`, `Mrs.`, `Ms.`, `Dr.`, `St.`, `vs.`, `Ltd.`, `Co.`, `Inc.`, `Jan.`…`Dec.`), an initial (`A.`), or inside a number (`1.5`, `10(46).`).
- A chunk split from a unit repeats the unit's last sentence of the previous piece at its start (one sentence of overlap).
- A single sentence over `chunk_chars` is split at the last space before the limit, and the chunk carries `chunk.long-sentence` in `rules`.
- A sentence holding a reference is never split, even when it makes the chunk run over `chunk_chars` by up to half; past that, it is split as a long sentence.
- A piece under 200 characters (`min_chars`) joins its neighbour under the same parent, except a definition, a question-answer pair and a table summary.
- No chunk mixes scripts: lines of another script start a new chunk with the same parent.

**Anchors.** A chunk that holds part of a line records, in that line's entry of `anchors`, `from_char` and `to_char` (offsets into the line's `text`). A chunk id is made from the file, its content hash, and its line ids with their character ranges, so two pieces of one line have different ids.

**Doubtful lines** stay in chunks, as in 0.3; each chunk's `status` and `rules` say what they hold. `docs/chunking.md` is corrected to say so.

### 6. The folder and incremental runs

- A file's metadata, plan entry and chunks are made from its `document.json` by a pure function of: the document, the chunking settings (`chunk_chars`), the override that applies to it, and `CHUNKING_VERSION` (a constant raised whenever a rule changes). That key is stored per file in the manifest.
- **The reading key no longer holds the package version or `chunk_chars`.** In 0.3 it held `ragnaroq`'s version and `Config.reading_sha256()`, which includes `chunk_chars`, so any upgrade or any change of chunk size read every file again, OCR included. In 0.4 it holds a reading version per format instead (each reader's, and one for the PDF pipeline), raised only when that format's reading changes, and `chunk_chars` moves to the chunking key.
- A run reads a file again only when its reading key changes. When only the chunking key changes, its metadata and chunks are made again from the stored `document.json`, and `changes.json` lists the chunks that changed.
- **Heading levels for PDFs are worked out when chunking,** from the stored lines' `font_size`, so a PDF read by 0.3 gains levels without being read again. The readers of the other formats set `Line.level` while reading, so their reading versions rise in 0.4.
- **Upgrading from 0.3 reads no PDF again** when its settings are unchanged: 0.4 recognises the key 0.3 stored for the same settings, and keeps the reading. Files of the other formats are read again once, which is quick because they need no OCR.
- `files` mentions and the folder's links and glossary are made at the end of each run from every file's entry; they are cheap and always made again.
- `metadata.json`, `chunking-plan.json` and `chunking-plan.md` are written with the run's other files, before `manifest.json`, as 0.3 writes `chunks.jsonl` and `changes.json`.
- A failed or skipped file has no metadata entry; the catalogue lists it with its status.

### 7. Measuring

**`ragnaroq evaluate --chunking QUESTIONS FOLDER`.** A questions file is JSON Lines:

```json
{ "question": "Until when can the deduction be claimed?", "path": "notice.pdf", "answer": "may be claimed until 31st March, 2027" }
```

`answer` is a short passage copied from the document, a phrase or one sentence, that answers the question. For each question, the chunks of the whole folder are ranked by BM25 (k1 1.2, b 0.75; lower-cased words, digits kept, no stemming) over `embedding_text`; a chunk is a hit when it comes from `path` and its `text` holds the passage, whitespace aside. The same test applies to 0.3's chunks and 0.4's, so a chunk that holds a whole long paragraph and a piece that holds only the right sentence are judged alike. The report gives, for 0.4's chunks and 0.3's on the same documents: hits in the top 1, 3 and 10, the mean reciprocal rank, the mean chunk length, and the number of chunks. 0.3's chunker is kept inside the evaluation code as the baseline, not as a user option.

**The questions set**, `references/chunking/`:

- **Tuning half: generated documents**, made by a script in the repo like the demo folder: a notice with numbered paragraphs, provisos and explanations; a FAQ in Word; a sectioned manual in Markdown and HTML; a contract with definitions; a slide deck; a sheet; a JSON data file; a long plain essay. Their questions are written by hand with the script. The thresholds of section 3 are tuned on this half only.
- **Held-out half: public documents**, fetched by `ragnaroq reference chunking` and pinned by SHA-256, as the IRS reference set is: public-domain US government documents (an IRS publication with sections, an IRS FAQ page in HTML, a section of the US Code with numbered subsections). At least 40 questions, written by hand and committed before any strategy code exists, so they cannot be fitted to the chunker; the 0.2 gate used a held-out set the same way. The builder writes them; the owner may check them.

**Gates for the release:**
- On the held-out half, scored once, 0.4's mean reciprocal rank is at least 0.3's, and its top-3 hits at least 0.3's.
- On every document in the demo folder, the generated set and the public set: no chunk cuts a word; no chunk cuts a sentence except as section 5 allows; no proviso or explanation is in a different chunk from the start of its paragraph unless the paragraph was split, and then its piece carries the paragraph's number; no question is apart from its answer.
- Every metadata value names lines that exist and whose text holds the value's `text`.
- Two runs on the same folder give identical `metadata.json`, plan files and chunks.
- The PDF reading numbers do not move (they cannot: reading does not change).

### 8. Release

0.4.0. Schema, chunk format and the metadata format 0.4.0. `CHANGELOG.md`, `README.md` (metadata, strategies, overrides, evaluating chunking), `docs/chunking.md` (corrected to what is built: 1,200 characters, doubtful lines kept, what moved to the next release), `docs/vector-databases.md` (the new keys and filters), and `docs/roadmap.md` (0.4 split: rules now, LangExtract and the second opinion next).

## Left to the builder

- The exact regular expressions, the abbreviation list's full contents, and the order of fields inside JSON objects, within the rules above.
- How the strategies are laid out in code (one module per strategy or one table of them), as long as each is a pure function of the document and its plan.
- The wording of `chunking-plan.md` and of the evaluation report.
- Which public documents make the public set, within: public domain, stable URL, pinned by hash, at least one of each of sections, FAQ, numbered subsections.
- Whether `ragnaroq schema --metadata` is a flag or a subcommand.

## Calls made in writing this

These were not discussed with the owner; each is the writer's call, for checking:

- `chunk_chars` stays 1,200 (not `chunking.md`'s 2,000); doubtful lines stay in chunks (not left out by default).
- `kind` uses its own small vocabulary (`numbered`, `sectioned`, …), separate from the document's own type, which LangExtract adds later.
- Numeric dates are normalised only when unambiguous; `05/01/2026` keeps `value` `""`.
- An empty filter list is written `[""]`, and a list holds at most 50 values.
- A reference sentence may run a chunk over by half before it is split.
- The questions set mixes generated documents (for tuning) with fetched public-domain US government documents (held out, questions committed before the strategies are written); no Indian government documents, whose redistribution is not settled. The builder writes the questions.
- 0.3's chunker survives only inside `evaluate`, as the baseline.
- A question's answer is a short passage of text, not line ids, so split paragraphs and whole paragraphs are scored alike.
- The reading key drops the package version and `chunk_chars`; upgrading from 0.3 keeps PDF readings, and PDF heading levels come from stored font sizes.
- An array of same-key JSON objects is cut as a table; PDF tables stay one chunk each, with a summary.
- Overrides may set only `strategy`, `max_chars` and `min_chars`.
- Questions are found without bold; a printed PDF line's end is never a sentence end.
