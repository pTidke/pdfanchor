# RAGnaroq evaluation

Engines: tesseract 5.3.4, easyocr 1.7.2. Agreement threshold: 0.9. References: 4, 3912 true words.

| Measure | Value |
| --- | --- |
| Lines read | 348 |
| Accepted by rule | 56 (16.09% of lines) |
| **Accepted but wrong** | **30 (53.57% of accepted)** |
| **Accepted with a wrong number, date or negation** | **30 (53.57% of accepted)** |
| Sent to a person | 292 (83.91% of lines) |
| Sent to a person although right | 168 (57.53% of flagged) |
| Character error rate, all lines | 1.12% |
| True words no line covered | 0 (0.00%) |
| Lines with no true text under them | 0 |

## By reference

| Reference | Profile | Lines | Accepted | Accepted but wrong | Wrong on a critical token | Character errors |
| --- | --- | --- | --- | --- | --- | --- |
| tables.blurred.pdf | blurred | 82 | 2 | 2 | 2 | 1.91% |
| tables.clean.pdf | clean | 82 | 10 | 10 | 10 | 0.70% |
| tables.low-res.pdf | low-res | 82 | 4 | 4 | 4 | 1.04% |
| tables.tilted.pdf | tilted | 102 | 40 | 14 | 14 | 0.84% |

## The agreement threshold

Every other rule kept as it is. A higher threshold accepts fewer lines: fewer mistakes get through, and more work goes to a person.

| Threshold | Accepted | Accepted but wrong | Wrong on a critical token | Sent to a person |
| --- | --- | --- | --- | --- |
| 0.80 | 56 | 30 | 30 | 83.91% |
| 0.85 | 56 | 30 | 30 | 83.91% |
| 0.88 | 56 | 30 | 30 | 83.91% |
| 0.90 ← | 56 | 30 | 30 | 83.91% |
| 0.92 | 56 | 30 | 30 | 83.91% |
| 0.94 | 56 | 30 | 30 | 83.91% |
| 0.96 | 56 | 30 | 30 | 83.91% |
| 0.98 | 56 | 30 | 30 | 83.91% |
| 1.00 | 56 | 30 | 30 | 83.91% |

## Accepted but wrong

Those wrong on a number, a date or a negation come first.

| Line | Critical | Read | Correct |
| --- | --- | --- | --- |
| p1-l30 | yes | R28CO 28,000 R28C1 28,001 R28C2 28,002 R28C3 28,003 R28C4 28,004 R28C5 28,005 | R28C0 28,000 R28C1 28,001 R28C2 28,002 R28C3 28,003 R28C4 28,004 R28C5 28,005 |
| p2-l30 | yes | R28CO 28,000 R28C1 28,001 R28C2 28,002 R28C3 28,003 R28C4 28,004 R28C5 28,005 | R28C0 28,000 R28C1 28,001 R28C2 28,002 R28C3 28,003 R28C4 28,004 R28C5 28,005 |
| p1-l3 | yes | R1CO 1,000 R1C1 1,001 R1C2 1,002 R1C3 1,003 R1C4 1,004 R1C5 1,005 | R1C0 1,000 R1C1 1,001 R1C2 1,002 R1C3 1,003 R1C4 1,004 R1C5 1,005 |
| p1-l5 | yes | R3CO 3,000 R3C1 3,001 R3C2 3,002 R3C3 3,003 R3C4 3,004 R3C5 3,005 | R3C0 3,000 R3C1 3,001 R3C2 3,002 R3C3 3,003 R3C4 3,004 R3C5 3,005 |
| p1-l10 | yes | R8CO 8,000 R8C1 8,001 R8C2 8,002 R8C3 8,003 R8C4 8,004 R8C5 8,005 | R8C0 8,000 R8C1 8,001 R8C2 8,002 R8C3 8,003 R8C4 8,004 R8C5 8,005 |
| p1-l18 | yes | R16CO 16,000 R16C1 16,001 R16C2 16,002 R16C3 16,003 R16C4 16,004 R16C5 16,005 | R16C0 16,000 R16C1 16,001 R16C2 16,002 R16C3 16,003 R16C4 16,004 R16C5 16,005 |
| p1-l38 | yes | R36CO 36,000 R36C1 36,001 R36C2 36,002 R36C3 36,003 R36C4 36,004 R36C5 36,005 | R36C0 36,000 R36C1 36,001 R36C2 36,002 R36C3 36,003 R36C4 36,004 R36C5 36,005 |
| p2-l3 | yes | R1CO 1,000 R1C1 1,001 R1C2 1,002 R1C3 1,003 R1C4 1,004 R1C5 1,005 | R1C0 1,000 R1C1 1,001 R1C2 1,002 R1C3 1,003 R1C4 1,004 R1C5 1,005 |
| p2-l5 | yes | R3CO 3,000 R3C1 3,001 R3C2 3,002 R3C3 3,003 R3C4 3,004 R3C5 3,005 | R3C0 3,000 R3C1 3,001 R3C2 3,002 R3C3 3,003 R3C4 3,004 R3C5 3,005 |
| p2-l10 | yes | R8CO 8,000 R8C1 8,001 R8C2 8,002 R8C3 8,003 R8C4 8,004 R8C5 8,005 | R8C0 8,000 R8C1 8,001 R8C2 8,002 R8C3 8,003 R8C4 8,004 R8C5 8,005 |
| p2-l18 | yes | R16CO 16,000 R16C1 16,001 R16C2 16,002 R16C3 16,003 R16C4 16,004 R16C5 16,005 | R16C0 16,000 R16C1 16,001 R16C2 16,002 R16C3 16,003 R16C4 16,004 R16C5 16,005 |
| p2-l38 | yes | R36CO 36,000 R36C1 36,001 R36C2 36,002 R36C3 36,003 R36C4 36,004 R36C5 36,005 | R36C0 36,000 R36C1 36,001 R36C2 36,002 R36C3 36,003 R36C4 36,004 R36C5 36,005 |
| p1-l5 | yes | R3CO 3,000 R3C1 3,001 R3C2 3,002 R3C3 3,003 R3C4 3,004 R3C5 3,005 | R3C0 3,000 R3C1 3,001 R3C2 3,002 R3C3 3,003 R3C4 3,004 R3C5 3,005 |
| p1-l11 | yes | R9CO 9,000 R9C1 9,001 R9C2 9,002 R9C3 9,003 R9C4 9,004 R9C5 9,005 | R9C0 9,000 R9C1 9,001 R9C2 9,002 R9C3 9,003 R9C4 9,004 R9C5 9,005 |
| p2-l5 | yes | R3CO 3,000 R3C1 3,001 R3C2 3,002 R3C3 3,003 R3C4 3,004 R3C5 3,005 | R3C0 3,000 R3C1 3,001 R3C2 3,002 R3C3 3,003 R3C4 3,004 R3C5 3,005 |
| p2-l11 | yes | R9CO 9,000 R9C1 9,001 R9C2 9,002 R9C3 9,003 R9C4 9,004 R9C5 9,005 | R9C0 9,000 R9C1 9,001 R9C2 9,002 R9C3 9,003 R9C4 9,004 R9C5 9,005 |
| p1-l3 | yes | R1CO 1,000 R1C1 1,001 R1C2 1,002 R1C3 1,003 R1C4 1,004 R1C5 1,005 | R1C0 1,000 R1C1 1,001 R1C2 1,002 R1C3 1,003 R1C4 1,004 R1C5 1,005 |
| p1-l5 | yes | R3CO 3,000 R3C1 3,001 R3C2 3,002 R3C3 3,003 R3C4 3,004 R3C5 3,005 | R3C0 3,000 R3C1 3,001 R3C2 3,002 R3C3 3,003 R3C4 3,004 R3C5 3,005 |
| p1-l8 | yes | R6CO 6,000 R6C1 6,001 R6C2 6,002 R6C3 6,003 R6C4 6,004 R6C5 6,005 | R6C0 6,000 R6C1 6,001 R6C2 6,002 R6C3 6,003 R6C4 6,004 R6C5 6,005 |
| p1-l10 | yes | R8CO 8,000 R8C1 8,001 R8C2 8,002 R8C3 8,003 R8C4 8,004 R8C5 8,005 | R8C0 8,000 R8C1 8,001 R8C2 8,002 R8C3 8,003 R8C4 8,004 R8C5 8,005 |

…and 10 more in evaluation.json.
