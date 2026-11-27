# Chunking reference set: held-out public documents

Real public documents with hand-written questions, used to judge the chunking strategies of release 0.4 with `ragnaroq evaluate FOLDER --chunking QUESTIONS`. The questions in `public.questions.jsonl` were committed before any of the new chunking strategies were written, so the chunker could not be fitted to them. The release gate scores this set once.

Each question names a document (`path`, relative to `public/`), and its `answer` is a phrase or sentence copied verbatim from the text of that document as `ragnaroq convert` reads it (PDF text layer, no OCR), occurring exactly once in it. A test checks this. There are 62 questions: 21 for the sectioned publication, 23 for the numbered revenue procedure and 18 for the FAQ pages.

## Documents

All were downloaded on 2026-11-27 from irs.gov. They are works of the US federal government and so in the public domain.

| File | Kind | Source | SHA-256 |
| --- | --- | --- | --- |
| `p501.pdf` | sectioned | https://www.irs.gov/pub/irs-pdf/p501.pdf | `288b62a4917f3bc3df0bade7a22c23a8ae648ba9eb5086df5b8b50e881148ff6` |
| `rp-24-40.pdf` | numbered | https://www.irs.gov/pub/irs-drop/rp-24-40.pdf | `4de9db6b6662b5c305a59100a027051628823b42fd5817ea4e383ad4c302af1f` |
| `faq-address-changes.html` | FAQ | https://www.irs.gov/faqs/irs-procedures/address-changes | `71b5b05e301cbbca51227b6c27f8bd93cb23700cabf469800377678c67aaae07` |
| `faq-refund-inquiries.html` | FAQ | https://www.irs.gov/faqs/irs-procedures/refund-inquiries | `23cb544589c9ae00a298688686ea04228bc904bb69b37bdb03c838b9358de0a8` |
| `faq-amended-returns.html` | FAQ | https://www.irs.gov/faqs/electronic-filing-e-file/amended-returns | `cb0f56879de835d8b2c11d6a4403856e7897bddbc19d81a3708bea6d3e356b68` |

The FAQ pages include irs.gov's site navigation; the questions target the FAQ content only.

## Deviations from the design

- The spec said the documents would be fetched by a `ragnaroq reference chunking` command. They are committed instead, and no fetch command exists. irs.gov's HTML pages change daily, so a download pinned by hash could not be rebuilt; committing the files keeps the set fixed.
- The US Code could not be reached from the build environment (uscode.house.gov, govinfo.gov and law.cornell.edu are blocked), so Revenue Procedure 2024-40, with numbered paragraphs such as `.15 Standard Deduction.` and `(1) In general.`, stands in for numbered subsections.
