# Where RAGnaroq is going

Today ragnaroq reads one PDF and writes text a language model can read and a vector index can ingest, with every line anchored to its place on the page and a rule-based verdict on whether it can be trusted.

The goal is wider. The Norse names used below are explained in the [README](../README.md#rag-n-aroq). In three steps:

1. Add ragnaroq to your project.
2. Point it at a folder.
3. The folder becomes ready for retrieval: every file read, checked, described and indexed, and served to any model through MCP, on your machine or a server you choose.

The files can be of any type. It starts with the common ones: PDF, Word, Excel, PowerPoint, JSON, XML, CSV, Markdown and HTML.

```
folder ─▶ readers        one per file type, all writing the same document.json
       ─▶ trust          rules ─▶ a local OCR model reads doubtful lines again ─▶ a person decides the rest
       ─▶ semantic layer metadata.json: a master file of what each document is and says, every value grounded in its source
       ─▶ chunks         a strategy per document, chosen from the metadata (chunking-plan.json)
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
   - Model stages (OCR model, metadata, embeddings) are optional extras, switched on explicitly.
   - Local models from Hugging Face or Ollama are the default, so everything can run free and offline.
   - Hosted models are an option, with a budget per run. Results are cached.
4. **Incremental.** Files are identified by content hash. Changing one file re-processes that file only.
5. **Honest numbers.** For each file type, accuracy is measured on human-verified reference sets. A verdict of *accepted* means "passed the rules"; the measured error rate says how far to trust that.

## What a processed folder looks like

```
my-folder/
  ...your files, untouched...
  .ragnaroq/
    manifest.json      every file: hash, reader, status, flags, cost
    files/<id>/        document.json, document.md, chunks.jsonl
    metadata.json      the master metadata file for the whole folder
    review.json        decisions made by people in the review UI
    index/             the vector index
```

## The stages

### Readers: Runes

Each file type has a reader that writes the same `document.json` contract, with anchors suited to the type.

- **Office files:** Word, Excel and PowerPoint carry their text, so they need little OCR beyond embedded images.
- **Structured data:** in JSON and XML, the shape itself is the structure, so headings and sections come from keys and elements.

### Trust and repair: the Norns and Heimdall

The PDF rules that exist today stay first. Lines that remain doubtful get one more reader: a small document-OCR model from Hugging Face that runs locally, free and offline (`ragnaroq[vlm]`).

- **The model:** PaddleOCR-VL-1.6, shipped in 0.2.0 as an optional third reader. It has about 0.9 billion parameters, an Apache 2.0 licence and 109 languages including Hindi, and it runs on a CPU. It is still to be measured against GLM-OCR on our reference set.
- **What it sees:** only the crop of each doubtful line, so it runs on the few lines that need it, not on everything. Reading whole pages is not planned: it takes minutes a page on a CPU. A comparison with GLM-OCR comes later.
- **How its reading is used:** its reading is one more independent reading, like Tesseract's and EasyOCR's, and the existing rule decides. A line is accepted only if the validators pass and two independent readings agree on every critical token (numbers, dates, negations). The model is never trusted on its own. When it agrees with an OCR reading other than the first reader's, that reading's text can replace the first reader's, and `resolution` records it.
- **Reproducibility:** the weights are pinned to a revision and checked, and decoding is greedy, so with the same model, number format and versions the same crop gives the same reading. The per-line time limit and the optional budget are wall-clock limits, so on a slow or busy machine they can change which lines are read.
- **If it still disagrees,** the line stays flagged for a person in the review UI (0.5).

A hosted model (Gemini, OpenAI and others) can be switched in instead for people without the compute. It is optional, with a budget per run and the cost of every call recorded.

### The semantic layer, `metadata.json`: Mímir

A master file describing every document in the folder. It is built with Google's [LangExtract](https://github.com/google/langextract) (`ragnaroq[semantic]`), using a local model through Ollama by default (free and offline), or Gemini or OpenAI if you choose.

- **Fields:** they come from a schema the user picks. The defaults are title, type, date, issuer, parties, amounts, references, periods, topics and entities, and domains can add their own.
- **Grounding:** LangExtract maps each extracted value to its place in the text, and ragnaroq maps that back to the line anchors. A metadata value is therefore as checkable as a line.
- **Folder level:** a catalogue of documents, links between documents that cite each other, and a glossary.

### Chunking strategies: Mímir, with Huginn's second opinion

Chunking is chosen per document from `metadata.json`: fixed rules first, then a second opinion from a local language model. The result is written to `chunking-plan.json` and `chunking-plan.md` with the reasons for each choice. Where the two disagree, a questions file or a person settles it.

- **Strategies:** single, question-answer, numbered paragraphs, sections, table rows, slides, records, and sentences as the fallback.
- **Additions for every strategy:** definitions, entity filters, date filters, reference links, small-to-big parents.
- **Checking:** a questions file can test the choice against the alternatives.

[chunking.md](chunking.md) has the signals, the rules, the model's role and the size limits.

### Index and serve: Muninn, Bifröst and the Völva

- **Embeddings:** the provider is configurable.
- **Store:** a local store by default, with adapters for pgvector, Qdrant and others.
- **MCP server:** tools to search, list documents, fetch a document or a passage, and cite by anchor. It runs over stdio locally or HTTP on a server, with a container recipe.

### Review UI: Forseti

A lightweight local web app, `ragnaroq review ./folder`, shows each flagged line beside the crop of the original. The user accepts it, fixes it or marks it unreadable.

Decisions are written to `review.json` and become readings by a person. They also grow the reference set used to measure accuracy.

## Milestones

| Version | Theme | What it delivers |
| --- | --- | --- |
| **0.1** ✓ | Trustworthy PDFs | Done in 0.1.0: reference sets with a known answer and `ragnaroq evaluate` (false-accept rate, overall and on critical tokens); a first measurement on public IRS documents; the code rule and low-resolution reading that brought critical errors to zero on it; per-page time, memory and character limits; on PyPI. Moved on: human-verified real scans, and pinned OCR model versions. |
| **0.2** ✓ | Third reader | Done in 0.2.0, and in 0.2.1 a lone "|" in OCR text is flagged: a local OCR model (PaddleOCR-VL-1.6) reads the crop of each scanned line the two OCR readers dispute, and a fixed rule settles it or keeps it for a person; measured on a held-out set (41.7% fewer lines sent to a person, none settled wrong on a number, date or negation). Open: a "1" read as "|" by both readers. |
| 0.3 | Folders and formats | `ragnaroq index ./folder` writing `.ragnaroq/`; readers for Word, Excel, PowerPoint, JSON, XML, CSV, Markdown, HTML; incremental runs. |
| 0.4 | Semantic layer | `metadata.json` through LangExtract (local model by default); the chunking plan derived from it. |
| 0.5 | Review UI | The local review UI, where a person settles the flagged lines; hosted models optional. |
| 0.6 | Serve | Embeddings and stores; the MCP server, local and remote. |
| Later | Harder PDFs | Scan recovery, tables on scans, very long documents, more languages (see `porting-from-the-lab.md`). |

## Open questions

- **Defaults** for the embedding model, the local OCR model and the metadata model, chosen by measured accuracy and speed on a CPU. The aim is that the whole pipeline runs free and offline, with hosted models as an option.
