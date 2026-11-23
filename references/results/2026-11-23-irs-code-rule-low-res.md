# RAGnaroq evaluation

Engines: tesseract 5.3.4, easyocr 1.7.2. Agreement threshold: 0.9. References: 20, 43256 true words.

| Measure | Value |
| --- | --- |
| Lines read | 4468 |
| Accepted by rule | 3231 (72.31% of lines) |
| **Accepted but wrong** | **199 (6.16% of accepted)** |
| **Accepted with a wrong number, date or negation** | **0 (0.00% of accepted)** |
| Sent to a person | 1237 (27.69% of lines) |
| Sent to a person although right | 718 (58.04% of flagged) |
| Character error rate, all lines | 3.90% |
| True words no line covered | 1386 (3.20%) |
| Lines with no true text under them | 5 |

## By reference

| Reference | Profile | Lines | Accepted | Accepted but wrong | Wrong on a critical token | Character errors |
| --- | --- | --- | --- | --- | --- | --- |
| f1040.blurred.pdf | blurred | 120 | 11 | 5 | 0 | 32.03% |
| f1040.clean.pdf | clean | 145 | 57 | 18 | 0 | 11.93% |
| f1040.low-res.pdf | low-res | 127 | 35 | 16 | 0 | 23.11% |
| f1040.tilted.pdf | tilted | 139 | 30 | 10 | 0 | 15.01% |
| fw4.blurred.pdf | blurred | 190 | 111 | 6 | 0 | 4.00% |
| fw4.clean.pdf | clean | 190 | 158 | 9 | 0 | 1.19% |
| fw4.low-res.pdf | low-res | 190 | 140 | 7 | 0 | 1.48% |
| fw4.tilted.pdf | tilted | 191 | 135 | 4 | 0 | 1.64% |
| p15.blurred.pdf | blurred | 236 | 174 | 4 | 0 | 0.13% |
| p15.clean.pdf | clean | 237 | 218 | 3 | 0 | 0.07% |
| p15.low-res.pdf | low-res | 237 | 204 | 5 | 0 | 0.11% |
| p15.tilted.pdf | tilted | 237 | 211 | 4 | 0 | 0.08% |
| p501.blurred.pdf | blurred | 266 | 165 | 12 | 0 | 1.04% |
| p501.clean.pdf | clean | 313 | 275 | 13 | 0 | 0.25% |
| p501.low-res.pdf | low-res | 301 | 237 | 11 | 0 | 0.38% |
| p501.tilted.pdf | tilted | 311 | 269 | 12 | 0 | 0.35% |
| p505.blurred.pdf | blurred | 221 | 127 | 15 | 0 | 3.49% |
| p505.clean.pdf | clean | 273 | 234 | 17 | 0 | 0.19% |
| p505.low-res.pdf | low-res | 271 | 209 | 16 | 0 | 0.24% |
| p505.tilted.pdf | tilted | 273 | 231 | 12 | 0 | 0.19% |

## The agreement threshold

Every other rule kept as it is. A higher threshold accepts fewer lines: fewer mistakes get through, and more work goes to a person.

| Threshold | Accepted | Accepted but wrong | Wrong on a critical token | Sent to a person |
| --- | --- | --- | --- | --- |
| 0.80 | 3365 | 256 | 1 | 24.69% |
| 0.85 | 3316 | 234 | 0 | 25.78% |
| 0.88 | 3274 | 211 | 0 | 26.72% |
| 0.90 ← | 3231 | 199 | 0 | 27.69% |
| 0.92 | 3157 | 178 | 0 | 29.34% |
| 0.94 | 3023 | 141 | 0 | 32.34% |
| 0.96 | 2754 | 100 | 0 | 38.36% |
| 0.98 | 2054 | 22 | 0 | 54.03% |
| 1.00 | 1511 | 11 | 0 | 66.18% |

## Accepted but wrong

Those wrong on a number, a date or a negation come first.

| Line | Critical | Read | Correct |
| --- | --- | --- | --- |
| p1-l5 |  | Your first name and middie initial Last name Your social security number | Your first name and middle initial Last name Your social security number |
| p1-l16 |  | Filing Status Single ¦) Head of household (HOH) | Filing Status Single Head of household (HOH) |
| p2-l4 |  | d You: ¦_¦ Were born before January 2, 1961 ¦) Are blind | d You: Were born before January 2, 1961 Are blind |
| p2-l19 |  | 24 _Add lines 22 and 23. This is your total tax . 24 | 24 Add lines 22 and 23. This is your total tax . . . . . . . . . . . . . . . . . 24 |
| p2-l20 |  | Payments 25 — Federal income tax withheld from: | Payments 25 Federal income tax withheld from: |
| p1-l2 |  | € Department of the Treasury—Internal Revenue Service | Department of the Treasury—Internal Revenue Service |
| p1-l3 |  | OMB No. 1545-0074 ¦ IRS Use Only—Do not write or staple in this space. | OMB No. 1545-0074 IRS Use Only—Do not write or staple in this space. |
| p1-l18 |  | You O Spouse | You Spouse |
| p1-l19 |  | Filing Status [¦ Single [_] Head of household (HOH) | Filing Status Single Head of household (HOH) |
| p1-l22 |  | one box. L] Married filing separately (MFS). Enter spouse’s SSN above If you checked the HOH or QSS box, enter the child’s name | one box. Married filing separately (MFS). Enter spouse’s SSN above If you checked the HOH or QSS box, enter the child’s name |
| p1-l25 |  | ini At any time during 2025, did you: (a) receive (as a reward, award, or payment for property or services); or (b) sell, | At any time during 2025, did you: (a) receive (as a reward, award, or payment for property or services); or (b) sell, |
| p1-l41 |  | Credit: Child tax Credit for Child tax Credit for Child tax Credit for Child tax Credit for | Child tax Credit for Child tax Credit for Child tax Credit for Child tax Credit for |
| p1-l43 |  | ¦_¦ Check if your filing status is MFS or HOH and you lived apart from your spouse for the last 6 months of 2025, or you are legally | Check if your filing status is MFS or HOH and you lived apart from your spouse for the last 6 months of 2025, or you are legally |
| p1-l68 |  | d_ If you are married filing separately and lived apart from your spouse the entire year (see inst.), check here oO | d If you are married filing separately and lived apart from your spouse the entire year (see inst.), check here |
| p2-l3 |  | Credits 12a Someone can claim L] You asa dependent L] Your spouse as a dependent | Credits 12a Someone can claim You as a dependent Your spouse as a dependent |
| p2-l4 |  | b LJ Spouse itemizes on a separate return c ¦] You were a dual-status alien | b Spouse itemizes on a separate return c You were a dual-status alien |
| p2-l5 |  | d You: im Were born before January 2, 1961 im Are blind | d You: Were born before January 2, 1961 Are blind |
| p2-l12 |  | * Married filing | • Married filing |
| p2-l18 |  | *° If you checked | • If you checked |
| p2-l35 |  | attach Sch. EIC. Clergy filing Schedule SE (see instructions) oO | attach Sch. EIC. b Clergy filing Schedule SE (see instructions) . . . . . . . . . . . . . . . . |

…and 179 more in evaluation.json.
