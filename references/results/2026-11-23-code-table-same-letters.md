# RAGnaroq evaluation

Engines: tesseract 5.3.4, easyocr 1.7.2. Agreement threshold: 0.9. References: 4, 3912 true words.

| Measure | Value |
| --- | --- |
| Lines read | 348 |
| Accepted by rule | 26 (7.47% of lines) |
| **Accepted but wrong** | **0 (0.00% of accepted)** |
| **Accepted with a wrong number, date or negation** | **0 (0.00% of accepted)** |
| Sent to a person | 322 (92.53% of lines) |
| Sent to a person although right | 184 (57.14% of flagged) |
| Character error rate, all lines | 1.05% |
| True words no line covered | 0 (0.00%) |
| Lines with no true text under them | 0 |

## By reference

| Reference | Profile | Lines | Accepted | Accepted but wrong | Wrong on a critical token | Character errors |
| --- | --- | --- | --- | --- | --- | --- |
| tables.blurred.pdf | blurred | 82 | 0 | 0 | 0 | 1.91% |
| tables.clean.pdf | clean | 82 | 0 | 0 | 0 | 0.70% |
| tables.low-res.pdf | low-res | 82 | 0 | 0 | 0 | 0.74% |
| tables.tilted.pdf | tilted | 102 | 26 | 0 | 0 | 0.84% |

## The agreement threshold

Every other rule kept as it is. A higher threshold accepts fewer lines: fewer mistakes get through, and more work goes to a person.

| Threshold | Accepted | Accepted but wrong | Wrong on a critical token | Sent to a person |
| --- | --- | --- | --- | --- |
| 0.80 | 26 | 0 | 0 | 92.53% |
| 0.85 | 26 | 0 | 0 | 92.53% |
| 0.88 | 26 | 0 | 0 | 92.53% |
| 0.90 ← | 26 | 0 | 0 | 92.53% |
| 0.92 | 26 | 0 | 0 | 92.53% |
| 0.94 | 26 | 0 | 0 | 92.53% |
| 0.96 | 26 | 0 | 0 | 92.53% |
| 0.98 | 26 | 0 | 0 | 92.53% |
| 1.00 | 26 | 0 | 0 | 92.53% |

## Accepted but wrong

Those wrong on a number, a date or a negation come first.

None.
