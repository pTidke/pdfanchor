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

## Results

Each run below used Tesseract 5.3.4 and EasyOCR 1.7.2 (English) on a CPU, with the default policy (agreement threshold 0.90), on the four profiles: clean, low-res, blurred and tilted.

| Run | Lines | Accepted | Accepted but wrong | Wrong number, date or negation | Sent to a person | Sent although right |
| --- | --- | --- | --- | --- | --- | --- |
| [IRS, 23 November 2026](results/2026-11-23-irs.md) | 4,476 | 72.5% | 6.7% of accepted | **0.15%** of accepted (5 lines) | 27.5% | 57.7% of flagged |
| [Code table, 23 November 2026](results/2026-11-23-code-table.md) | 348 | 16.1% | 53.6% of accepted | **53.6%** of accepted (30 lines) | 83.9% | 57.5% of flagged |

### What the runs show

- **Every critical error was one of a single kind:** both readers made the same letter–digit mistake inside a code. Examples are `11320B` read as `113208`, `R28C0` as `R28CO`, and `(b)` as `(6)`. All five on the IRS set came from the low-resolution profile.
- **The agreement threshold cannot fix these.** The readers agree exactly, so critical errors barely move between 0.80 and 1.00. They need a different signal: a rule for codes that mix letters and digits, a third and different reader, or reading low-resolution pages at a higher resolution.
- **Most other accepted mistakes are harmless symbols**, such as checkbox drawings read as `[_]`, **or letter slips**, such as `Hf you` for *If you*. A letter slip gets through when the readers differ by a letter or two, because the line is accepted with the first reader's text. Requiring letters to match, or keeping the reading both agree on, is the next experiment.
- **More than half of what goes to a person is right**: the price of the rules as they stand.

These are synthetic scans of born-digital pages: a best case for real paper. See [the limits](../docs/evaluating.md#limits-of-synthetic-references).
