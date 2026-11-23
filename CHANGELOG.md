# Changelog

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
