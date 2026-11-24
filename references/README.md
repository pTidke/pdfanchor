# Reference sets

Documents whose correct text is known, for measuring how often RAGnaroq is wrong. See [docs/evaluating.md](../docs/evaluating.md) for the method.

The reference PDFs themselves are not stored here, since they are large images. They are rebuilt from public sources with the commands below, and the same input always gives byte-identical references.

## IRS forms and publications

These are born-digital and dense with numbers, dates and form layouts. As works of the US federal government, they are in the public domain. The IRS revises them, so the checksums record the exact versions measured.

| File | Source | Pages used | SHA-256 |
| --- | --- | --- | --- |
| `f1040.pdf` | https://www.irs.gov/pub/irs-pdf/f1040.pdf | 1–2 | `3d31c226df0d189ced80e039d01cf0f8820c1019681a0f0ca6264de277b7e982` |
| `fw4.pdf` | https://www.irs.gov/pub/irs-pdf/fw4.pdf | 1–2 | `92444d8856ce55d9e25dca8b6d1420634fc68b11e1ab1f760916ea29ddd312b2` |
| `p15.pdf` | https://www.irs.gov/pub/irs-pdf/p15.pdf | 5, 30 | `b46c3622439d8521e3a0faca4cd5b5ece3451b526f65f06a5e38d5c9f804c88b` |
| `p501.pdf` | https://www.irs.gov/pub/irs-pdf/p501.pdf | 2, 9 | `288b62a4917f3bc3df0bade7a22c23a8ae648ba9eb5086df5b8b50e881148ff6` |
| `p505.pdf` | https://www.irs.gov/pub/irs-pdf/p505.pdf | 3, 20 | `c69e87caa93cdefa4493f0ec1d8478b1ff9ec1329ee18c7b9d442f2dcb79c621` |

```bash
for f in f1040 fw4 p15 p501 p505; do curl -sO "https://www.irs.gov/pub/irs-pdf/$f.pdf"; done
sha256sum f1040.pdf fw4.pdf p15.pdf p501.pdf p505.pdf   # compare with the table
ragnaroq reference make f1040.pdf --out refs/irs
ragnaroq reference make fw4.pdf --pages 1-2 --out refs/irs
ragnaroq reference make p15.pdf --pages 5,30 --out refs/irs
ragnaroq reference make p501.pdf --pages 2,9 --out refs/irs
ragnaroq reference make p505.pdf --pages 3,20 --out refs/irs
ragnaroq evaluate refs/irs --out results/irs --easyocr-models ~/easyocr-models
```

## The code-table stress test

This is a synthetic table of codes such as `R28C0` beside amounts such as `28,000`. Codes that mix letters and digits are where readers confuse O with 0, and B with 8, and can agree on the same mistake.

```bash
python references/make_code_table.py code-table.pdf
ragnaroq reference make code-table.pdf --pages 1-2 --out refs/codes
ragnaroq evaluate refs/codes --out results/codes --easyocr-models ~/easyocr-models
```

## Held-out set (0.2.0)

These pages were never used to tune the third-reader rule. With the rule frozen, the documents were read once, as the release gate for 0.2.0. The readings were scored twice: the first scoring failed the gate, and the second followed a correction to the answer key (see [the first run](#the-first-run-kept-on-record)). All five are public-domain IRS documents, downloaded on 24 November 2026.

| File | Source | Pages used | SHA-256 |
| --- | --- | --- | --- |
| `fw9.pdf` | https://www.irs.gov/pub/irs-pdf/fw9.pdf | 1–2 | `2d420cbb4123dcf1fb82595b2359cfbb5d81f00b9df9d359fcc7af361d093f53` |
| `f941.pdf` | https://www.irs.gov/pub/irs-pdf/f941.pdf | 1–2 | `38a3d8cf7a455101d52543c8c48e66202bc25c189ead878627e35c797c88e2ad` |
| `f1040sc.pdf` | https://www.irs.gov/pub/irs-pdf/f1040sc.pdf | 1–2 | `cc44e7d1150e54d991835814c1191f60a32617730ca9e50e6f532b51293a86b4` |
| `p596.pdf` | https://www.irs.gov/pub/irs-pdf/p596.pdf | 2–4 | `29e8c6695912318253743210fcd68aaa10ad874c76edcab4aa7dc4b23ff51dbf` |
| `i1040gi.pdf` | https://www.irs.gov/pub/irs-pdf/i1040gi.pdf | 10–12 | `482e9c487c608f1bbeaceef35bc3c0933e8b35443cfff447e4279d590468364a` |

That makes 20 references (5 documents, 4 profiles each), with 3,712 lines and 36,468 true words.

```bash
for f in fw9 f941 f1040sc p596 i1040gi; do curl -sO "https://www.irs.gov/pub/irs-pdf/$f.pdf"; done
sha256sum fw9.pdf f941.pdf f1040sc.pdf p596.pdf i1040gi.pdf   # compare with the table
ragnaroq reference make fw9.pdf f941.pdf f1040sc.pdf --pages 1-2 --out held-out/refs
ragnaroq reference make p596.pdf --pages 2-4 --out held-out/refs
ragnaroq reference make i1040gi.pdf --pages 10-12 --out held-out/refs
ragnaroq evaluate held-out/refs --out held-out/without --easyocr-models ~/easyocr-models
mkdir -p held-out/with && cp -r held-out/without/cache held-out/with/cache
ragnaroq evaluate held-out/refs --out held-out/with --easyocr-models ~/easyocr-models --third-reader
```

The second run starts from the first run's stored readings, so both runs decide the same base readings. Measured with Tesseract 5.3.4, EasyOCR 1.7.2 and PaddleOCR-VL-1.6 (PaddlePaddle/PaddleOCR-VL-1.6 at revision `132c45f52c0a`, bfloat16, CPU, 4 cores).

| Measure | Without third reader | With third reader |
| --- | --- | --- |
| Lines sent to a person | 1,072 | 625 (41.7% fewer) |
| Sent to a person although right | 467 | 84 |
| Accepted by rule | 2,640 | 3,087 |
| Accepted but wrong | 330 (12.5%) | 350 (11.3%) |
| Accepted with a wrong number, date or negation | 6 | 6 |

The third reader settled 447 lines. 20 of them were not exactly the answer key: all spacing, punctuation, or a line box that also covers a neighbouring field's words. None was wrong on a number, date or negation. It held back the lines where its reading failed a check: no match 491, validators 0, omission 47, underscore 10, neighbour 16. It read 1,024 crops in about 43 minutes, about 2.5 seconds a line.

The accepted-but-wrong count rose from 330 to 350 because more lines were accepted (2,640 to 3,087); the rate fell from 12.5% to 11.3%.

### The first run, kept on record

The first run used the 0.1 answer key. It reported 6 lines settled by the third reader as wrong on a critical token (with the third reader: 27 accepted critical, 362 accepted wrong; without: 21 and 336). All 6 were Schedule C line labels printed "20b", "16b" and "16c" with no visible gap. The PDF stores an overprinted space, so the answer key said "20 b", while every reader read what is printed.

The owner chose to fix the answer key (words printed with no visible gap are one word, commit `fb2dddc`) and score again. The scans were byte-identical, only Schedule C's four truth files changed, and nothing was read again. No rule or threshold was changed after seeing the held-out set.

### Known limitation found on this set

The 6 accepted lines wrong on a number in both runs come from the two-reader rule that existed in 0.1, not from the third reader. In 5 of them, Tesseract and EasyOCR both read a "1" as "|" ("January |", "page |") and agree, so the line is accepted before the third reader is consulted. In the sixth, the Schedule C title, both readers missed the small "(Form 1040)". The 0.1 result of none on the IRS set does not hold on these documents. A follow-up is planned; this release does not fix it.

### Next steps before relying on this result

- The next fresh held-out set includes All About Tax PDFs, and confirms the answer-key change (words printed with no visible gap are one word) on documents it was not made for.
- The 0.1 sets are scored again under the new answer key, so every published number uses one answer key.

### From the spike

Before the build, the same rule was simulated on stored readings of the 0.1 reference sets. On the IRS set, lines sent to a person fell from 1,242 to about 650 (48% fewer), and on the code table from 322 to 178 (45% fewer), with no line settled wrong on a number, date or negation. Code-table lines took 8.7 seconds each on average, IRS lines 3.5 seconds.

## Results

Each run below used Tesseract 5.3.4 and EasyOCR 1.7.2 (English) on a CPU, with the default policy (agreement threshold 0.90), on the four profiles: clean, low-res, blurred and tilted.

| Run | Lines | Accepted | Accepted but wrong | Wrong number, date or negation | Sent to a person | Sent although right |
| --- | --- | --- | --- | --- | --- | --- |
| [IRS, 23 November 2026](results/2026-11-23-irs.md) | 4,476 | 72.5% | 6.7% of accepted | **0.15%** of accepted (5 lines) | 27.5% | 57.7% of flagged |
| [Code table, 23 November 2026](results/2026-11-23-code-table.md) | 348 | 16.1% | 53.6% of accepted | **53.6%** of accepted (30 lines) | 83.9% | 57.5% of flagged |
| [IRS, with the code rule and low-res reading](results/2026-11-23-irs-code-rule-low-res.md) | 4,468 | 72.3% | 6.2% of accepted | **0** | 27.7% | 58.0% of flagged |
| [Code table, with the code rule and low-res reading](results/2026-11-23-code-table-code-rule-low-res.md) | 348 | 7.5% | 0 | **0** | 92.5% | 57.1% of flagged |
| [IRS, strict mode (`--same-letters`)](results/2026-11-23-irs-same-letters.md) | 4,468 | 62.8% | 3.5% of accepted | **0** | 37.2% | 62.7% of flagged |

### What the runs show

**The first runs** (policy 0.1) found that every critical error let through was one kind: both readers made the same letter–digit mistake inside a code. Examples are `11320B` read as `113208`, `R28C0` as `R28CO`, and `(b)` as `(6)`. All five on the IRS set came from the low-resolution profile. The agreement threshold cannot catch these, because the readers agree exactly.

**Two changes then brought critical errors to zero on both sets:**

- **The code rule (policy 0.2).** In text read from an image, a code that mixes letters and digits is flagged when it holds a letter readers take for a digit, or a bracket the line never pairs. On the IRS set it costs 10 more lines sent to a person; the code table now goes almost entirely to a person, as a page of `R28C0`-style codes should.
- **Reading low-resolution scans at 300 dpi.** A scanned page whose image holds under 150 dots per inch is read at 300 instead of 200. On the 110-dpi references this removed the three errors no text rule can see (`113208`, `102200`, `(6)`) and a quarter of all accepted mistakes. It costs about 1.7 times the reading time, for those pages only.

**What is left:**

- **Accepted lines with other mistakes, 6.2% of accepted.** About half are harmless symbols, such as checkbox drawings read as `[_]`. The other half are letter slips, such as `Hf you` for *If you*. A letter slip gets through when the readers differ by a letter or two, because the line is accepted with the first reader's text.

  **Strict mode (`--same-letters`)** requires the readers to have the same letters and digits, not only to be similar. It cuts the letter slips let through from 90 to 8. But in most lines where the readers differ by a letter, the first reader was right and the second slipped. So strict mode sends 324 correct lines to a person to catch 82 slips, and the accepted share falls from 72.3% to 62.8%. It is an option for word-perfect needs, such as legal text and citations, not the default. Since 0.2.0, the third reader looks only at lines the two readers disagree on, so a letter slip both readers share, or one within the agreement threshold, is still accepted.
- **Lines sent to a person although right, 58% of flagged.** That is the price of caution; the next experiments should lower it.

The runs in this table were measured with the 0.1 answer key, before words printed with no visible gap were joined into one word (0.2.0). These runs were not scored again. The held-out set above uses the new answer key.

These are synthetic scans of born-digital pages: a best case for real paper. See [the limits](../docs/evaluating.md#limits-of-synthetic-references).
