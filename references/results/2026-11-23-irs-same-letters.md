# RAGnaroq evaluation

Engines: tesseract 5.3.4, easyocr 1.7.2. Agreement threshold: 0.9. References: 20, 43256 true words.

| Measure | Value |
| --- | --- |
| Lines read | 4468 |
| Accepted by rule | 2806 (62.80% of lines) |
| **Accepted but wrong** | **98 (3.49% of accepted)** |
| **Accepted with a wrong number, date or negation** | **0 (0.00% of accepted)** |
| Sent to a person | 1662 (37.20% of lines) |
| Sent to a person although right | 1042 (62.70% of flagged) |
| Character error rate, all lines | 3.90% |
| True words no line covered | 1386 (3.20%) |
| Lines with no true text under them | 5 |

## By reference

| Reference | Profile | Lines | Accepted | Accepted but wrong | Wrong on a critical token | Character errors |
| --- | --- | --- | --- | --- | --- | --- |
| f1040.blurred.pdf | blurred | 120 | 4 | 2 | 0 | 32.03% |
| f1040.clean.pdf | clean | 145 | 44 | 7 | 0 | 11.93% |
| f1040.low-res.pdf | low-res | 127 | 17 | 6 | 0 | 23.11% |
| f1040.tilted.pdf | tilted | 139 | 23 | 4 | 0 | 15.01% |
| fw4.blurred.pdf | blurred | 190 | 98 | 1 | 0 | 4.00% |
| fw4.clean.pdf | clean | 190 | 147 | 5 | 0 | 1.19% |
| fw4.low-res.pdf | low-res | 190 | 126 | 3 | 0 | 1.48% |
| fw4.tilted.pdf | tilted | 191 | 131 | 3 | 0 | 1.64% |
| p15.blurred.pdf | blurred | 236 | 158 | 0 | 0 | 0.13% |
| p15.clean.pdf | clean | 237 | 212 | 1 | 0 | 0.07% |
| p15.low-res.pdf | low-res | 237 | 199 | 1 | 0 | 0.11% |
| p15.tilted.pdf | tilted | 237 | 203 | 1 | 0 | 0.08% |
| p501.blurred.pdf | blurred | 266 | 62 | 5 | 0 | 1.04% |
| p501.clean.pdf | clean | 313 | 258 | 8 | 0 | 0.25% |
| p501.low-res.pdf | low-res | 301 | 205 | 7 | 0 | 0.38% |
| p501.tilted.pdf | tilted | 311 | 254 | 9 | 0 | 0.35% |
| p505.blurred.pdf | blurred | 221 | 60 | 6 | 0 | 3.49% |
| p505.clean.pdf | clean | 273 | 210 | 11 | 0 | 0.19% |
| p505.low-res.pdf | low-res | 271 | 179 | 8 | 0 | 0.24% |
| p505.tilted.pdf | tilted | 273 | 216 | 10 | 0 | 0.19% |

## The agreement threshold

Every other rule kept as it is. A higher threshold accepts fewer lines: fewer mistakes get through, and more work goes to a person.

| Threshold | Accepted | Accepted but wrong | Wrong on a critical token | Sent to a person |
| --- | --- | --- | --- | --- |
| 0.80 | 2849 | 108 | 0 | 36.24% |
| 0.85 | 2838 | 105 | 0 | 36.48% |
| 0.88 | 2823 | 98 | 0 | 36.82% |
| 0.90 ← | 2806 | 98 | 0 | 37.20% |
| 0.92 | 2776 | 93 | 0 | 37.87% |
| 0.94 | 2726 | 84 | 0 | 38.99% |
| 0.96 | 2583 | 72 | 0 | 42.19% |
| 0.98 | 2021 | 20 | 0 | 54.77% |
| 1.00 | 1511 | 11 | 0 | 66.18% |

## Accepted but wrong

Those wrong on a number, a date or a negation come first.

| Line | Critical | Read | Correct |
| --- | --- | --- | --- |
| p1-l16 |  | Filing Status Single ¦) Head of household (HOH) | Filing Status Single Head of household (HOH) |
| p2-l19 |  | 24 _Add lines 22 and 23. This is your total tax . 24 | 24 Add lines 22 and 23. This is your total tax . . . . . . . . . . . . . . . . . 24 |
| p1-l2 |  | € Department of the Treasury—Internal Revenue Service | Department of the Treasury—Internal Revenue Service |
| p1-l3 |  | OMB No. 1545-0074 ¦ IRS Use Only—Do not write or staple in this space. | OMB No. 1545-0074 IRS Use Only—Do not write or staple in this space. |
| p1-l19 |  | Filing Status [¦ Single [_] Head of household (HOH) | Filing Status Single Head of household (HOH) |
| p1-l43 |  | ¦_¦ Check if your filing status is MFS or HOH and you lived apart from your spouse for the last 6 months of 2025, or you are legally | Check if your filing status is MFS or HOH and you lived apart from your spouse for the last 6 months of 2025, or you are legally |
| p2-l12 |  | * Married filing | • Married filing |
| p2-l18 |  | *° If you checked | • If you checked |
| p2-l67 |  | Preparer [_] Self-employed | Preparer Self-employed |
| p1-l17 |  | Filing Status {_! Single (_) Head of household (HOH) | Filing Status Single Head of household (HOH) |
| p2-l3 |  | Credits 12a Someonecanclaim ¦_¦] Youasadependent  [_¦ Your spouse as a dependent | Credits 12a Someone can claim You as a dependent Your spouse as a dependent |
| p2-l21 |  | Payments 25 ~=—- Federal income tax withheld from: | Payments 25 Federal income tax withheld from: |
| p2-l33 |  | to claim the ACTC, check here . : - 28 | to claim the ACTC, check here . . . . . . . . . . . . 28 |
| p2-l43 |  | 36 Amount of line 34 you want applied to your 2026 estimatedtax. . . ¦ 36 | 36 Amount of line 34 you want applied to your 2026 estimated tax . . . 36 |
| p2-l44 |  | Amount 37 = Subtract line 33 from line 24. This is the amount you owe. | Amount 37 Subtract line 33 from line 24. This is the amount you owe. |
| p1-l62 |  | d_ Ifyou are married filing separately and lived apart from your spouse the entire year (see inst.), check here [_] | d If you are married filing separately and lived apart from your spouse the entire year (see inst.), check here |
| p1-l64 |  | b Check if; [_] Schedule D not required (_] Includes child’s capital gain or (loss) | b Check if: Schedule D not required Includes child’s capital gain or (loss) |
| p2-l17 |  | * If you checked | • If you checked |
| p2-l22 |  | Payments 25 ‘Federal income tax withheld from: | Payments 25 Federal income tax withheld from: |
| p2-l47 |  | one Form W-4, Withholding will be most accurate if you | one Form W-4. Withholding will be most accurate if you |

…and 78 more in evaluation.json.
