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
3. Writes `<name>.<profile>.truth.json`, with every word of the original text and its box on the page. The boxes move with the page when the profile tilts it.

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

The report goes to `evaluation.md` and `evaluation.json`.

| Measure | What it means |
| --- | --- |
| **Accepted but wrong** | Accepted lines whose text differs from the truth. Only Unicode form, case, spacing, and typographic quotes and dashes are forgiven. |
| **Accepted with a wrong number, date or negation** | Accepted lines whose critical tokens differ from the truth. This is the number that matters most. |
| Sent to a person | Lines flagged or blocked. |
| Sent to a person although right | Flagged lines that were correct after all: the cost of being careful. |
| Character error rate | Character edits from what was read to the truth, over all lines. This measures the OCR readers, whatever the verdict. |
| True words no line covered | Text the engine never found. |
| Lines with no true text under them | Text the engine made up, such as noise read as letters. |

The **threshold table** re-decides every line at agreement thresholds from 0.80 to 1.00, with every other rule kept as it is. It shows the trade between mistakes let through and work sent to a person. The policy's thresholds are set from this table, not guessed.

`--max-critical-false-accepts 0.001` makes the command exit with code 3 when the critical false-accept rate is above that share. That lets a CI job fail when a change makes the engine less safe.

## Limits of synthetic references

A page rendered from a born-digital PDF is cleaner than real paper in several ways:

- it has no show-through, stamps, signatures or handwriting;
- its fonts are the ones the document was typeset in;
- it is not folded or curled.

The numbers from these references are a best case for scans. A set of real, human-verified scans is the next step.

Some born-digital PDFs have broken text layers: wrong character maps, or Hindi in legacy fonts. For those, the "truth" is wrong, and the engine looks worse than it is. `reference make` keeps only pages the engine itself classifies as having a usable text layer. Check a reference's truth file before trusting a surprising result.
