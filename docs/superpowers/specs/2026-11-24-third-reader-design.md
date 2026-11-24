# A third reader for disputed lines

Status: approved on 2026-11-24. Approach A, chosen on 2026-11-24.

## Why

On a scanned page, every line is read by Tesseract and checked by EasyOCR. When the two disagree, the line goes to a person. On the IRS reference set, 1,169 of the 1,242 lines sent to a person were sent only because the two readers disagreed, and Tesseract was right on 701 of those. Most of that review work is needless, but it cannot be cut by loosening the agreement rule: that lets wrong numbers through.

The aim, in the owner's words: doubtful lines should be fixed automatically by a local model, and only if confidence is still low is the user asked for the correction. The safety bar stays where 0.1 set it: no accepted line with a wrong number, date or negation.

## What the test showed

A throwaway test ran PaddleOCR-VL-1.6 (0.9 billion parameters, Apache 2.0, from Hugging Face) on the crop of each disputed line, on a 4-core CPU with no GPU.

- **Reading quality.** On its own it read 821 of the 1,169 IRS lines exactly right; Tesseract read 701.
- **Speed.** 3.5 seconds a line on average; 90% of lines took under 5.8 seconds. 23 lines took over 20 seconds, the slowest 65, when the model kept repeating a phrase. Code-table lines took 8.7 seconds on average.
- **Votes.** A line was confirmed when the model's reading exactly matched one of the other two readings, and passed four checks (below). It confirmed 592 IRS lines: 591 exactly right, and one with a different number of leader dots, which is layout, not content. On the code table it confirmed 144 of 168 lines, all right. It correctly refused 24 blurred lines where a comma and a full stop could not be told apart (10,000 against 10.000).
- **Effect.** Lines sent to a person fall from 1,242 to 650 on the IRS set (48% fewer) and from 322 to 178 on the code table (45% fewer). About 28 of the confirmed IRS lines were ones Tesseract had wrong, so they leave with the corrected text.
- **Rejected.** Granite-Docling-258M took 16 to 85 seconds a line and repeated and invented text. Reading whole pages with a model takes minutes a page on a CPU.

Two of the four checks were added after seeing the failures they prevent, on the same lines they were then scored on. The result is not trusted until it holds on pages the rule has never seen (see *Release gate*).

## Goals

- Settle as many disputed lines as possible without a person, with no accepted line wrong on a number, date or negation.
- Run free and offline on an ordinary CPU, using a GPU when one is present.
- Keep every decision explainable: which readings agreed, and which check held a line back.
- Give the same result for the same file, every time.

## Non-goals

- Lines with any flag other than the disagreement (validator flags such as the code rule, table lines, single readings). They stay with a person, as today.
- Machines without EasyOCR. There a scanned line has one reading and is flagged `ocr.single-reading`; a two-reader vote with the model alone was not measured, so those lines stay as they are.
- Text-layer (native) pages. The test only covered scans, and a text layer is its own answer key, so false accepts on it cannot be measured with the current references. Later, with human-verified pages.
- Reading whole pages with the model, hosted models, and choosing between models (GLM-OCR and others). The reader sits behind a small interface so another model can be measured later.
- The review UI (still milestone 0.4 work).

## Anti-goals

These would make it a failure even if it ran:

- A wrong number, date or negation accepted because the model agreed with it.
- Text changed with no record of where the new text came from.
- A model downloaded, or a network call made, without the user asking for it.
- A run that takes far longer with the model switched on than the user expected, with no limit and no warning.

## Constraints

- Principle 3 of the roadmap: model stages are optional extras, switched on explicitly, local by default. The core install stays small and does not import torch.
- Principle 2: nothing is changed silently. A model's reading is kept as a reading and accepted only by rule.
- The output contract (`document.json`) is versioned; changing it bumps the schema version, and the generated schema must stay in sync (an existing test checks this).
- CI cannot download or run a 1.8 GB model. Tests use a stand-in reader.

## Approaches

| | Approach | Trade-off |
| --- | --- | --- |
| **A (chosen)** | A third reader on disputed lines only. It reads the crop of each line the two readers disagree on, and a fixed rule decides. | About 3.5 seconds per disputed line on a CPU, and nothing on lines that already agree. Cuts reviews by about half on the test sets. |
| B | Suggestion only. The model's reading is shown to the reviewer but never settles a line. | Safest, but reviews do not fall at all. |
| C | The model reads whole pages as a full third reader. | Minutes per page on a CPU, and its text has to be matched back to the line boxes, which is a new source of error. |

A was chosen because it removes the most review work for the least running time, and the line crops it reads are the same ones a reviewer would look at.

## Design

### 1. Where it runs

A new stage after the first decision, so the flow becomes: read pages, first decision, third reader, final decision.

1. `convert()` reads and decides every page as today.
2. It picks the lines the third reader can settle: on a scanned page, not excluded, flagged, and with no flag other than `crosscheck.disagreement`.
3. It draws again only the pages that have such lines, in the same isolated page worker and with the same safety limits, at the third reader's own resolution (300 dpi).
4. It cuts each line's crop, reads it, and stores the result as a reading on the line.
5. It decides the whole document again.

Running after the first decision, and not inside the page loop, keeps two things simple:
- the crops can be drawn at a higher resolution than the reading resolution;
- `evaluate` can run the stage on a stored reading without reading the pages again.

### 2. The model and how it reads

- **Model.** PaddleOCR-VL-1.6, at the pinned revision `132c45f52c0a26ed5a6b2f6bd8e3985794cc27dd`, loaded with `transformers` 5 (`AutoModelForImageTextToText`, `AutoProcessor`), prompt `OCR:`, greedy decoding.
- **One line at a time.** Batching was measured slower on a CPU (15 seconds a line against 7).
- **Number format.** bfloat16 on a CPU that supports it natively (AMX or AVX-512 BF16), otherwise float32. A GPU when one is present. On the test machine bfloat16 was 2.5 times faster than float32, with the same output.
- **Crop.** The line's exact box with 2 points of padding left and right, none above or below, and a 12-pixel white frame. Wider crops let neighbouring lines bleed in, and the model then transcribed them too.
- **Limits per line.**
  - At most 160 new tokens and 20 seconds (setting).
  - A line that hits either limit, or whose output repeats itself, gets no reading and stays with a person.
- **Limit per document.** An optional time budget, off by default. When it runs out, the remaining lines stay flagged, and the document carries a flag saying how many were not read.
- **Order.** The model is loaded once per run, before the first crop, so a missing or broken model fails at the start, not on every line.

### 3. Install and download

- **Install.** A new extra, `ragnaroq[vlm]`, adds `torch` and `transformers>=5,<6`. The README shows how to install the CPU-only torch build first, since the default Linux build pulls several gigabytes of GPU libraries.
- **Off by default.** It is switched on with `--third-reader` on `ragnaroq convert` and `ragnaroq evaluate`, or by passing a reader to `convert()`.
- **Download.** This mirrors the EasyOCR switches:
  - `--vlm-models DIR` says where the model files live (default: the Hugging Face cache);
  - `--vlm-download` lets missing files be fetched;
  - without that switch, a missing model is an error that says how to fetch it.
- **Integrity.** The files are fetched at the pinned revision, and their SHA-256 sums are checked against values kept in the code.

### 4. What is stored

- **The reading.** The model's raw output is stored as one more `Reading` on the line, with source `vlm:paddleocr-vl`.
  - It is stored raw, so a later change to how it is cleaned or compared needs no new reading.
  - The `vlm:` prefix keeps it out of the ordinary pairwise comparison. It is used only by the third-reader rule below.
- **Components.** `engine.components` records the model, its revision, the number format, the device, and the `torch` and `transformers` versions.
- **Resolution.** A line settled by the third reader gets a new `resolution` record:
  - the rule;
  - the sources that agreed;
  - the source whose text the line now carries.
- **Text.** When the agreeing pair does not include the first reader (EasyOCR and the model agree, Tesseract dissents), `text` becomes the agreeing OCR reader's text. `raw` stays exactly what the first reader returned, and the first reader's reading is still in `readings`. The documented meaning of `text` changes to: "NFC-normalised `raw`, unless `resolution` names another reading."
- **Schema.** This is an addition to the contract, so the schema version becomes 0.2.0.
- **Exports.** Markdown and chunks keep using `text`, so a correction reaches them.

### 5. The rule (policy 0.3)

Each line read by the third reader is decided by these steps, in order.

**Inputs:**
- `V`: the model's reading, with layout marks the other readers never write (check boxes such as ☐ □ ■, and a lone `|` between cells) removed.
- The other readings: the first reader's text and every other OCR reading.

**Steps:**
1. **Agreement.** Find a reading `R` among the other readings that equals `V` in canonical form, preferring the first reader's if more than one does. Canonical form is the one `evaluate` scores with, moved into `crosscheck.py` so both use the same one: Unicode-normalised, case-folded, single spaces, plain quotes and dashes, leader dots and fill underscores removed. If none matches, the line stays flagged.
2. **Validators.** Neither `R` nor `V` may trip a validator.
3. **Omission check.** No dissenting reading may have more than 1.15 times the letters of the larger of `R` and `V`. This catches the case where two readers both missed small print that the third saw (the "Designee's name / Phone no. / PIN" line on Form 1040).
4. **Underscore check.** No reading may have an underscore between two letters or digits. An underline hides an underscore: in "2026-02_IRB" both Tesseract and the model read a space.
5. **Neighbour check.** `R` may not share four consecutive words (in canonical form) with the first reader's text of the line just before or after it on the page. This catches a line box that covers two printed lines, whose text would otherwise appear twice.

The thresholds (1.15, four words) are policy settings in `Policy`. The reading limits (resolution, crop padding, tokens, seconds, budget) are in `Config`. Both are hashed into the result like every other setting.

**Result:**
- If every step passes, the line is accepted, with its `resolution` and the text from step 1.
- Otherwise it stays flagged, with one more flag, `crosscheck.third-reader`. The flag gives the model's reading and the step that held the line back. A reviewer sees the suggestion, and `evaluate` can count each check's effect.

### 6. Caching and `evaluate`

- **Third-reader cache.** Readings are cached by a hash of:
  - the crop image;
  - the model, its revision and the number format;
  - the reader settings.
- **Re-deciding.** `evaluate --third-reader` re-decides a stored reading in seconds. It draws pages again only for crops it has not read before, for example after a policy change brings new lines into dispute.
- **Report.** The evaluation report adds four counts:
  - lines settled by the third reader;
  - how many of those were wrong;
  - how many were wrong on a critical token;
  - how many each check held back.

### 7. Failures

- **Model.** A model that will not load fails the run at the start when `--third-reader` was asked for. A document is never silently produced without it.
- **Single line.** A line that cannot be read (an error, a time limit, a repeating output) keeps its first decision, with a flag saying why.
- **Page.** A page that cannot be drawn again keeps its first decisions.

### 8. Release gate

The rule is tuned on the IRS and code-table sets, so it must hold on pages it has never seen before it ships.

- **The held-out set.** New born-digital PDFs, about the size of the IRS set (five documents, sampled pages), made into references with all four profiles:
  - IRS forms and publications not used so far;
  - PDFs from the All About Tax collection, as test data only.
- **Use it once.** The set is run once, with the rule frozen.
- **The bar.** Among lines the third reader accepted, no wrong number, date or negation, and the accepted-but-wrong rate does not rise against a run without the third reader.
- **If it fails.** Stop and report to the owner. A fix is then checked on another fresh set, never tuned on this one.
- **Record.** The numbers go into `docs/evaluating.md` and the changelog.

### 9. Release

- **Version.** It ships as 0.2.0.
- **Roadmap.** It moves the third reader ahead of "Folders and formats". Each later milestone shifts by one, and the review UI keeps its place after the semantic layer.
- **Documentation.** The README, `docs/roadmap.md`, `docs/evaluating.md` and `CHANGELOG.md` say:
  - how to install and switch it on;
  - what it costs in time;
  - what the measured numbers are.

## Left to the builder

- **Code layout.** Module and class names, and how the stage sits in `pipeline.py` and `evaluate.py`.
- **Neighbours.** How the policy gets each line's neighbours.
- **Redrawing.** Whether the page worker gets a draw-only request for redrawing pages.
- **Repetition.** How repeated output is detected.
- **Cache.** The cache folder layout.
- **Tests.** Tests with a stand-in reader that returns fixed text, covering:
  - every step of the rule;
  - the text change and the resolution record;
  - the time limits and a missing model;
  - the schema bump.
- **Real-model test.** An optional slow test, skipped in CI, that runs the real model on a few stored crops.

## Calls made in writing this

These were not discussed and are open to change:

1. **Corrections.** When EasyOCR and the model agree against Tesseract, the line is accepted with their text, recorded in `resolution`. That is about 28 of the 592 IRS lines. The alternative is to accept only lines where the model agrees with Tesseract, and keep the other cases for a person.
2. **Scope.** Scanned pages only, because that is what was measured.
3. **Off by default.** The third reader is switched on with a flag, following roadmap principle 3.
4. **Text source.** The accepted text comes from the agreeing OCR reader, not from the model. When they differ only in case, quotes or leader dots, the established reader's text is kept.
5. **Version.** It ships as 0.2.0, with the roadmap reordered.
