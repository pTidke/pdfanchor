# Where pdfanchor is going

Today pdfanchor reads one PDF and writes text a language model can read and a vector index can ingest, with every line anchored to its place on the page and a rule-based verdict on whether it can be trusted.

The goal is wider. In three steps:

1. Add pdfanchor to your project.
2. Point it at a folder.
3. The folder becomes ready for retrieval: every file read, checked, described and indexed, and served to any model through MCP, on your machine or a server you choose.

The files can be of any type. It starts with the common ones: PDF, Word, Excel, PowerPoint, JSON, XML, CSV, Markdown and HTML.

```
folder ─▶ readers        one per file type, all writing the same document.json
       ─▶ trust          rules ─▶ (optional) a cheap model repairs doubtful lines ─▶ a person decides the rest
       ─▶ semantic layer metadata.json: a master file of what each document is and says, every value grounded in its source
       ─▶ chunks         strategies chosen from the structure and the metadata
       ─▶ index          embeddings in a local store, or pgvector, Qdrant and others
       ─▶ serve          Python API, command line, MCP server (stdio locally, HTTP on a server)
```

## Principles

These hold for every stage.

1. **Everything is anchored.** Every unit of text records exactly where it came from:
   - PDF: page and box;
   - Excel: sheet and cell;
   - PowerPoint: slide and shape;
   - Word: paragraph or table cell;
   - JSON: JSON pointer;
   - XML: XPath.

   A chunk, a metadata value and an answer can always be traced back.
2. **Nothing is changed silently.** A model's correction is a proposal, kept as another reading with its provider, model and cost. It is accepted only by rule. When it fails the rules, a person is asked.
3. **The core stays offline and deterministic.**
   - Model calls (repair, metadata, embeddings) are optional extras, switched on explicitly.
   - Each run has a budget, and results are cached.
   - Local models (Ollama) are supported as well as hosted ones.
4. **Incremental.** Files are identified by content hash. Changing one file re-processes that file only.
5. **Honest numbers.** For each file type, accuracy is measured on human-verified reference sets. A verdict of *accepted* means "passed the rules"; the measured error rate says how far to trust that.

## What a processed folder looks like

```
my-folder/
  ...your files, untouched...
  .pdfanchor/
    manifest.json      every file: hash, reader, status, flags, cost
    files/<id>/        document.json, document.md, chunks.jsonl
    metadata.json      the master metadata file for the whole folder
    review.json        decisions made by people in the review UI
    index/             the vector index
```

## The stages

### Readers

Each file type has a reader that writes the same `document.json` contract, with anchors suited to the type.

- **Office files:** Word, Excel and PowerPoint carry their text, so they need little OCR beyond embedded images.
- **Structured data:** in JSON and XML, the shape itself is the structure, so headings and sections come from keys and elements.

### Trust and repair

The PDF rules that exist today stay first. Lines that remain doubtful go to an optional repair stage (`pdfanchor[repair]`). It sends the crop of the line and the existing readings to the cheapest capable vision model the user configures.

A proposal is accepted only if:

- the validators pass, and
- it agrees with an independent reading on every critical token (numbers, dates, negations).

Otherwise the line stays flagged for a person. A budget per run caps the spend, and every call is recorded with its cost.

### The semantic layer: `metadata.json`

A master file describing every document in the folder. It is built with Google's [LangExtract](https://github.com/google/langextract) (`pdfanchor[semantic]`), using Gemini, OpenAI or a local model through Ollama.

- **Fields:** they come from a schema the user picks. The defaults are title, type, date, issuer, parties, amounts, references, periods, topics and entities, and domains can add their own.
- **Grounding:** LangExtract maps each extracted value to its place in the text, and pdfanchor maps that back to the line anchors. A metadata value is therefore as checkable as a line.
- **Folder level:** a catalogue of documents, links between documents that cite each other, and a glossary.

### Chunking strategies

Chunking is derived from the structure and the metadata, not fixed.

- **Structural (the default):** paragraphs kept whole, provisos with their paragraph, tables as their own chunk.
- **Section-scoped:** headings from the metadata.
- **Entity-scoped.**
- **Table rows**, for spreadsheets.
- **One chunk per slide.**
- **Small-to-big:** small chunks to search, their parents to answer.

Each chunk carries its metadata, so a search can filter on it. The manifest records which strategy was used, and why.

### Index and serve

- **Embeddings:** the provider is configurable.
- **Store:** a local store by default, with adapters for pgvector, Qdrant and others.
- **MCP server:** tools to search, list documents, fetch a document or a passage, and cite by anchor. It runs over stdio locally or HTTP on a server, with a container recipe.

### Review UI

A lightweight local web app, `pdfanchor review ./folder`, shows each flagged line beside the crop of the original. The user accepts it, fixes it or marks it unreadable.

Decisions are written to `review.json` and become readings by a person. They also grow the reference set used to measure accuracy.

## Milestones

| Version | Theme | What it delivers |
| --- | --- | --- |
| **0.1** | Trustworthy PDFs | A human-verified reference set and `pdfanchor evaluate` (false-accept rate, overall and on critical tokens); thresholds calibrated from it; per-page time and memory limits; pinned OCR models; first release on PyPI. |
| 0.2 | Folders and formats | `pdfanchor index ./folder` writing `.pdfanchor/`; readers for Word, Excel, PowerPoint, JSON, XML, CSV, Markdown, HTML; incremental runs. |
| 0.3 | Semantic layer | `metadata.json` through LangExtract; chunking strategies derived from it. |
| 0.4 | Repair and review | The cheap-model repair stage with a budget; the local review UI. |
| 0.5 | Serve | Embeddings and stores; the MCP server, local and remote. |
| Later | Harder PDFs | Scan recovery, tables on scans, very long documents, more languages (see `porting-from-the-lab.md`). |

## Open questions

- **The name.** `pdfanchor` fits a PDF-first engine. A wider name may suit the folder-to-retrieval tool. Package names on PyPI are hard to change, so this should be settled before the 0.1 release.
- **Defaults** for the embedding model, the repair model and the metadata model, chosen by measured cost and accuracy.
