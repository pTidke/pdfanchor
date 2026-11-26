# Measuring how often RAGnaroq is wrong

A verdict of *accepted* means a line passed every rule, so no person looks at it. Whether that can be trusted is a number, not a promise: of the accepted lines, how many are wrong? Two commands measure it.

```bash
ragnaroq reference make notice.pdf report.pdf --out refs/
ragnaroq evaluate refs/ --out results/
```

## Reference sets

The engine can only be scored against text whose correct answer is known. A born-digital PDF (one made on a computer, not scanned) carries its exact text, so it is its own answer key.

`ragnaroq reference make` takes such PDFs and, for each page with a usable text layer, does three things:

1. Renders the page to an image, as a scanner would, roughened by a profile.
2. Saves the images as an image-only PDF: `<name>.<profile>.pdf`. It has no text layer, so the engine has to read it like any scan.
3. Writes `<name>.<profile>.truth.json`, with every word of the original text and its box on the page. The boxes move with the page when the profile tilts it. Words printed with no visible gap between them (a label like "20b", whose space the PDF overprints) are one word in the truth file, as a reader sees them.

| Profile | What it does to the page |
| --- | --- |
| `clean` | 200 dpi, high-quality JPEG. A good office scan. |
| `low-res` | 110 dpi. A quick, low-resolution scan. |
| `blurred` | A Gaussian blur and heavy JPEG compression. A poor copy. |
| `tilted` | Turned 0.8° on the glass. |

Profiles use no randomness: the same PDF always gives the same reference. Pages without a text layer are left out, since they have no known answer.

`--pages 2,9-10` takes a sample of a long document, so an evaluation stays quick: EasyOCR on a CPU takes tens of seconds a page.

A human-verified reference uses the same truth format: a real scan, with its text typed in or corrected by a person. This is how real paper, stamps, handwriting and show-through will be measured.

## What `evaluate` reports

`ragnaroq evaluate` runs the full engine on every reference in a folder, with whichever OCR engines are installed. It then compares every line with the truth. Each true word belongs to the line whose box contains its centre, so a line the engine split or merged differently from the original is still compared with exactly the words it covers.

Two cases need care:

- **Overlapping boxes.** On ruled forms, or where a stray mark stretches a box, a word can fall inside two lines. It then goes to the line that actually read it, and otherwise to the smallest box. A word is never blamed on a neighbour that merely overlaps it.
- **Sideways text.** Text printed sideways (`Form` running up the margin) is often stored reversed in a PDF's text layer (`mroF`). The truth file keeps such words in an `ignored` list, and lines over nothing but them are not scored.

The report goes to `evaluation.md` and `evaluation.json`. The JSON lists every accepted line that was wrong, those wrong on a critical token first.

Reading is the slow part, so each reading is kept in `<out>/cache`, keyed by the PDF's bytes, the reading settings and the readers' versions. A stored reading is decided again under the current rules and policy on every run, so changing a rule, the threshold or the scoring takes seconds. `--fresh` reads everything again.

| Measure | What it means |
| --- | --- |
| **Accepted but wrong** | Accepted lines whose text differs from the truth. Only these are forgiven: Unicode form, case, spacing, typographic quotes and dashes, and leader dots, rules and fill underscores (`. . . . 24`, `Name ____`), which are layout, not content. |
| **Accepted with a wrong number, date or negation** | Accepted lines whose critical tokens differ from the truth. This is the number that matters most. |
| Sent to a person | Lines flagged or blocked. |
| Sent to a person although right | Flagged lines that were correct after all: the cost of being careful. |
| Character error rate | Character edits from what was read to the truth, over all lines. This measures the OCR readers, whatever the verdict. |
| True words no line covered | Text the engine never found. |
| Lines with no true text under them | Text the engine made up, such as noise read as letters. |

The **threshold table** re-decides every line at agreement thresholds from 0.80 to 1.00, with every other rule kept as it is. It shows the trade between mistakes let through and work sent to a person. The policy's thresholds are set from this table, not guessed. It shows the two-reader rule only: a line the third reader settled is compared with the other readings by its first reader's reading. Whether it counts as right is still judged on the text it carries after the correction.

`--max-critical-false-accepts 0.001` makes the command exit with code 3 when the critical false-accept rate is above that share. That lets a CI job fail when a change makes the engine less safe.

## The third reader

```bash
ragnaroq evaluate refs/ --out results/ --easyocr-models ~/easyocr-models --third-reader
```

`--third-reader` adds a local model (PaddleOCR-VL-1.6, from `ragnaroq[vlm]`) that reads the crop of each scanned line Tesseract and EasyOCR disagree on. A fixed rule then settles the line or keeps it for a person; see "How it decides" in the [README](../README.md#how-it-decides). The model is chosen with `--vlm-models DIR` and fetched only with `--vlm-download`. `--third-reader-budget SECONDS` limits the time it may spend on each document. That budget, like the limit of 120 seconds on each line, is wall-clock time, so on a slow or busy machine it can change which lines are read.

It changes `evaluate` in three ways:

- It reads only the disputed lines' crops, about 2.5 seconds a line on a 4-core CPU with native bfloat16 in the held-out run; float32 CPUs are about 2.5 times slower. Each reading is kept in `<out>/cache/third`, keyed by the crop and the model's settings, so a run decides stored readings again in seconds. Every page with disputed lines is still drawn again, to work out each crop's key, but only crops not in the cache are read by the model.
- To compare with and without the third reader on the same base readings, run without it first, then copy the `cache` folder into a new output folder and run again with `--third-reader`. The commands are in [references/](../references/README.md#held-out-set-020).
- The report gains a **Third reader** section:

| Measure | What it means |
| --- | --- |
| Settled by the third reader | Lines the rule accepted after the model's reading agreed with one of the two OCR readings. |
| Of which wrong | Settled lines whose text differs from the truth. |
| Of which wrong on a number, date or negation | Settled lines whose critical tokens differ from the truth. The release gate requires 0. |
| Held back: no match, validators, omission, underscore, neighbour | Disputed lines the model read but the rule kept for a person, by the check that held them back. |

The release gate for 0.2.0 was: no settled line wrong on a number, date or negation, and an accepted-but-wrong rate no higher than without the third reader. It was applied to a held-out set the rule was never tuned on, with the rule frozen and the documents read once. The first scoring, under the 0.1 answer key, failed the gate: 6 settled lines were wrong on a critical token. All 6 were Schedule C line labels that the answer key split ("20 b" for the printed "20b"). The answer key was corrected (commit `fb2dddc`) and the same readings were scored again; see [the first run](../references/README.md#the-first-run-kept-on-record). The result below is from the second scoring. On that set 447 lines were settled, none wrong on a number, date or negation, and the rate fell from 12.5% to 11.3% of accepted lines. After the gate the neighbour check was tightened, which only holds lines back: the shipped rule, re-scored on the same readings, settles 440 lines (none wrong on a number, date or negation), sends 632 to a person (41.0% fewer than 1,072; 91 of them right), accepts 350 lines wrong (11.4%, against 12.5% without the third reader) and holds back 23 at the neighbour check. See [references/](../references/README.md#held-out-set-020) for the sources, checksums and every number.

The same set found 6 accepted lines wrong on a number with and without the third reader. They come from the two-reader rule: both OCR readers read a "1" as "|" and agree. Since 0.2.1 a lone "|" in text read from an image is flagged, and 1 remains (a title where both readers missed the small "(Form 1040)").

## Limits of synthetic references

A page rendered from a born-digital PDF is cleaner than real paper in several ways:

- it has no show-through, stamps, signatures or handwriting;
- its fonts are the ones the document was typeset in;
- it is not folded or curled.

The numbers from these references are a best case for scans. A set of real, human-verified scans is the next step.

Some born-digital PDFs have broken text layers: wrong character maps, or Hindi in legacy fonts. For those, the "truth" is wrong, and the engine looks worse than it is. `reference make` keeps only pages the engine itself classifies as having a usable text layer. Check a reference's truth file before trusting a surprising result.
