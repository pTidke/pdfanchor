# Choosing a chunking strategy from `metadata.json`

There is no single best way to cut documents into chunks. A FAQ, a legal notification, a spreadsheet and a slide deck each want something different. pdfanchor picks a strategy per document from what `metadata.json` says about it, writes down the reason, and lets you override it.

The metadata is extracted with a model (LangExtract). The choice of strategy is not: it is a fixed set of rules over the metadata, so the same metadata always gives the same plan.

## What the plan looks like

Each run writes two files next to the metadata:

- `.pdfanchor/chunking-plan.json`: for every document, the strategy, its parameters and the reasons. Each reason names the metadata field that led to it.
- `.pdfanchor/chunking-plan.md`: the same, for a person to read.

```json
{
  "file": "circulars/circular-3-2026.pdf",
  "strategy": "numbered-paragraphs",
  "parameters": { "max_chars": 2000, "parent": "section", "keep_with_parent": ["proviso", "explanation"] },
  "reasons": [
    "structure.numbered_paragraphs = 14",
    "structure.provisos = 3",
    "type = circular"
  ],
  "filters": ["period", "entity", "issued_on"]
}
```

To override, name a strategy for a file or a pattern in `pdfanchor.toml`:

```toml
[chunking]
"faq/*.pdf" = "question-answer"
"data/*.xlsx" = { strategy = "table-rows", rows_per_chunk = 20 }
```

## The signals it reads

From `metadata.json`, for each document:

| Field | Example | Used for |
| --- | --- | --- |
| `type` | notification, circular, contract, report, manual, faq, spreadsheet, slides, data | the first guess |
| `structure.headings`, `structure.heading_depth` | 12, 3 | section-scoped chunking |
| `structure.numbered_paragraphs`, `structure.provisos`, `structure.explanations` | 14, 3, 1 | numbered-paragraph chunking |
| `structure.qa_pairs` | 25 | question-answer chunking |
| `structure.tables`, `structure.table_share` | 4, 0.6 | table chunking |
| `structure.definitions` | 18 terms | definition chunks |
| `length.chars`, `length.pages` | 1,400, 1 | single-chunk documents |
| `entities`, `entities_per_section` | parties, companies named | entity-scoped chunks and filters |
| `references` | "section 10(46)", "Notification 1/2026" | links between chunks; never cut a citing sentence |
| `periods`, `dates` | "TY 2026-27", effective from | filters, not cut points |
| `language`, `scripts` | en, hi | separate chunks per script when mixed |
| `trust.flagged_share` | 0.08 | whether flagged lines are kept, marked or left out |

## The rules

The plan takes the first rule that matches. Some rules add to every strategy, whichever is chosen.

| # | When the metadata says | Strategy | Why |
| --- | --- | --- | --- |
| 1 | The document is short: under about 1,500 characters. | **single**: one chunk. | Splitting a short document only loses context. |
| 2 | The document is a FAQ, or has many question-answer pairs. | **question-answer**: one chunk per question, with its answer. | People ask in the same form as the questions. Keeping each pair whole gives the best match. |
| 3 | The document has numbered paragraphs, provisos or explanations, as in notifications, circulars, Acts and contracts. | **numbered-paragraphs**: one numbered paragraph per chunk, its provisos and explanations kept with it, and its section as the parent. | A proviso read without its paragraph says the opposite of what it means. The paragraph number is how people cite it. |
| 4 | The document has a heading hierarchy two or more levels deep, as in reports and manuals. | **sections**: one section per chunk. A long section is split between paragraphs, and every chunk carries its heading path. | Headings are the author's own topic boundaries. |
| 5 | The document is mostly tables, or is a spreadsheet. | **table-rows**: groups of rows, each cell labelled with its header path ("Sheet › Table › Column: value"), plus one summary chunk per table. | A number without its column and row heading means nothing. |
| 6 | The document is a slide deck. | **slides**: one chunk per slide, with its title and speaker notes. | A slide is a unit of meaning by design. |
| 7 | The document is data (JSON, XML). | **records**: one object or repeated element per chunk, labelled with its path (JSON pointer or XPath). | The records are the units. Their keys give the context. |
| 8 | None of the above: plain prose with no structure. | **sentences**: paragraphs joined up to the size limit, split between sentences, with one sentence of overlap. | The safe fallback. It never cuts a sentence or a word. |

**Additions that apply to every strategy:**

- **Definitions.** If the document defines terms, each definition also becomes its own small chunk, and chunks that use a defined term link to it.
- **Entities.** If different sections concern different entities (a notification naming one company, a contract with several parties), each chunk records its entities as a filter, and the entity's name goes into the text that is embedded. A search for one company then does not return another company's terms.
- **Dates and periods.** Tax years, effective dates and similar become filters on the chunk. They are never used as cut points.
- **References.** A sentence that cites another section or document is never split. The reference is stored as a link, so an answer can follow it.
- **Mixed scripts.** When scripts are mixed, as with Hindi and English on the same Gazette page, each script gets its own chunks, and only the scripts you choose are indexed.
- **Doubtful lines.** Lines that pdfanchor flagged or blocked are left out by default. Two settings change this: `include_flagged = "mark"` keeps them with a marker, and `"keep"` keeps them as they are.
- **Small-to-big.** Every strategy records a parent (the section, the table, the slide), so the search can match a small chunk and the answer can use its parent.

## Size limits

- **Maximum.** A chunk holds at most 2,000 characters by default (`max_chars`).
- **Splitting.** A unit above the limit is split between sentences, never inside a word or a sentence, and the previous sentence is repeated at the start of the next chunk.
- **Minimum.** A chunk smaller than about 200 characters joins its neighbour in the same section, except for a definition or a question-answer pair, which stay whole.

## Checking the choice

Rules give a sensible start. Questions show whether it works. If you provide a questions file, `pdfanchor evaluate --chunking` builds each candidate strategy for a document and searches with your questions.

- **Questions file:** each question names the document and the passage that answers it.
- **Scores:** the found rank and the mean reciprocal rank.
- **Suggestions:** where another strategy scores clearly better, the plan says so. It never switches without being told.

## Where this comes from

These rules come from building the retrieval for Indian tax documents in the All About Tax project, where two things went wrong before them:

- Chunks were cut mid-sentence.
- Provisos were separated from their paragraphs.

They will be revised as other kinds of documents and question sets are measured.
