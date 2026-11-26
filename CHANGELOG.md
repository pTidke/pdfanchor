# Changelog

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

- **A "1" read as "|".** The 6 accepted lines wrong on a number, in both held-out runs, come from the two-reader rule, not the third reader. In 5, Tesseract and EasyOCR both read a "1" as "|" ("January |", "page |") and agree, so the line is accepted before the third reader is consulted. In the sixth, both missed the small "(Form 1040)" in the Schedule C title. The 0.1 result of none on the IRS set does not hold on these documents. It is not fixed in this release; a follow-up is planned.
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
