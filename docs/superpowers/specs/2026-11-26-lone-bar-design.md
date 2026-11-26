# A lone "|" in OCR text goes to a person

Status: approved on 2026-11-26 (owner: "yes go ahead" to measure, add the check, and measure its cost). Small change for 0.2.1.

## Why

On the held-out set, 5 of the 6 accepted lines with a wrong number read a printed "1" as "|". Tesseract and EasyOCR both made the same mistake and agreed, so the line was accepted before the third reader saw it ("January |", "top of page | of Form").

## What the stored readings show

These are lines whose OCR text has a "|" standing alone, under the 0.2.0 rules, on all three reference sets:

- **The answer key never holds a "|".** No line with a lone "|" matches it exactly.
- **What the "|" stands for:** a misread "1", a misread "I" ("| declare that | have examined"), or a box or table border read as text.
- **Accepted lines with a lone "|":**
  - IRS: 18, all wrong, none on a critical token;
  - code table: none;
  - held-out: 54, all wrong, 5 of them on a critical token.
- **Third reader on the held-out set:** 19 of its settled lines had a lone "|" in Tesseract's reading, and it cleaned every one. Most were Form 941 borders; one was "page |" corrected to "page 1".

## Design

1. **A new misreading check,** `glyph.lone-bar` (severity review), in `validators/marks.py`.
   - It flags OCR text that contains a "|" with whitespace or the line edge on both sides.
   - Message: `A "|" stands alone: it may be a misread "1", "I" or "l", or a border read as text.`
   - It joins `READING`, so it never runs on a PDF's own text layer, where a "|" is real text.
2. **The third reader may still settle these lines.** A disputed scanned line is a candidate when every flag on it is `crosscheck.disagreement` or `glyph.lone-bar`, and at least one is the disagreement. This is defined once in `policy.py` and used by `third.candidates`.
   - The rule already refuses an agreed reading that trips a validator. A settled line can therefore never keep a lone "|".
   - A line flagged only for the bar, where both readers agree on it, goes to a person. The rule never lets the model outvote two agreeing readers.
3. **Versions:** policy 0.4 and package 0.2.1. The schema is unchanged.

## Expected effect (simulated on stored readings)

| Set | Extra lines to a person | Accepted with a wrong number, date or negation |
| --- | --- | --- |
| IRS | 18 (all already wrong) | 0, unchanged |
| Held-out (with the third reader) | 54 (all already wrong) | 6 to 1; the last is the Schedule C title missing "(Form 1040)" |

The check only adds refusals, so it cannot make the number of wrong accepted numbers worse.

## Approaches considered

- **Chosen:** flag any lone "|" in OCR text. It is simple, and on these sets a lone "|" was never right.
- **Flag only a "|" next to words that usually precede a number** ("page", "line", "January"). Rejected: it misses the "I" cases, and the word list would be guesswork.
- **Let the third reader correct the bar by outvoting both readers.** Rejected: it breaks the rule that the model never overrides two agreeing readers.
