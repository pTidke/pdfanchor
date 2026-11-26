"""Settings. Everything that changes the output is here and is hashed into the result."""

import hashlib
import json
from dataclasses import asdict, dataclass, field


@dataclass(frozen=True)
class Policy:
    """Decision rules. The thresholds are starting values and are NOT calibrated yet.

    Until they are measured against verified reference pages, treat "accepted" as
    "passed every rule we have", not as a measured accuracy.
    """

    # 0.2: codes mixing letters and digits are checked (validators/codes.py)
    # 0.3: a third reading (vlm:) settles a disputed scanned line when it matches exactly
    # 0.4: a lone "|" in text read from an image is flagged (validators/marks.py)
    # 0.5: a day holding a letter read for a digit is flagged ("Sth January", validators/dates.py)
    # 0.6: text read straight from a file (exact) is accepted by rule; figures are decided
    version: str = "0.6"
    # Two readings of a line agree when their similarity is at least this and
    # every critical token (numbers, dates, section references, negations) matches.
    min_similarity: float = 0.90
    # Accept clean native text when no OCR engine was available to cross-check it.
    accept_native_without_crosscheck: bool = False
    # Accept scanned text read by one engine only.
    accept_single_ocr_reading: bool = False
    # Two readings must also have the same letters and digits (spacing, punctuation
    # and symbols may differ). Without it, readings a letter or two apart can agree,
    # and the line is accepted with the first reader's letters.
    same_letters: bool = False
    # The third-reader rule (see policy._third_reader). A reading that did not match the
    # third reader holds the line back when it has more than this many times the letters
    # of the matched text: it saw text the others missed. And the matched text holds the
    # line back when it shares this many consecutive words with a neighbouring line:
    # it probably runs into that line.
    third_reader_letter_ratio: float = 1.15
    third_reader_neighbour_words: int = 4


@dataclass(frozen=True)
class ThirdReaderSettings:
    """How disputed scanned lines are redrawn and cropped for the third reader (third.py)."""

    dpi: int = 300
    pad_points: float = 2.0  # added left and right of the line's box, none above or below
    frame_pixels: int = 12  # white border around the crop
    # The token limit is what stops a model that repeats itself, and it is deterministic.
    # The time limit is only a safety net: it is wall-clock time, so on a slow or busy
    # machine it could change which lines get a reading.
    max_new_tokens: int = 160
    line_seconds: float = 120.0
    # Wall-clock limit for the whole stage; lines not reached are left for a person.
    # Not part of the cache key: it changes how many lines are read, not what a crop reads as.
    budget_seconds: float | None = None


@dataclass(frozen=True)
class Config:
    dpi: int = 200
    languages: tuple[str, ...] = ("eng",)
    max_bytes: int = 100 * 1024**2
    max_pages: int = 1000
    max_render_pixels: int = 16_000_000
    min_native_chars: int = 20
    blank_dark_fraction: float = 0.0005
    # Lines made only of these scripts stay in document.json as evidence but are left
    # out of Markdown and chunks, and never need review. Example: ("Deva",).
    exclude_scripts: tuple[str, ...] = ()
    chunk_chars: int = 1200
    # How many OCR engines cross-check a page that has a text layer. One independent
    # reading is enough to confirm text the PDF already carries; the further engines
    # are for scanned pages, where every word comes from OCR, and running them on
    # every text page would multiply the time for little gain.
    native_crosscheck_engines: int = 1
    # A scanned page whose image holds fewer dots per inch than this is read at
    # `low_res_render_dpi` instead of `dpi`. Measured: on 110-dpi scans, reading at
    # 300 instead of 200 removed every critical error the rules let through (3 to 0)
    # and a quarter of all accepted mistakes, for about 1.7 times the reading time.
    low_res_source_dpi: int = 150
    low_res_render_dpi: int = 300
    # Safety limits for PDFs you did not choose (see isolation.py). Each page's text
    # layer is read, and the page drawn, in a worker process that is stopped after
    # `page_timeout_seconds` and may use at most `page_memory_mb`; a page with more
    # than `max_page_chars` characters is not read. A page that breaks a limit fails
    # alone, flagged with the limit. `isolate_pages` off reads pages in this process:
    # quicker to start, with no time or memory limit.
    isolate_pages: bool = True
    page_timeout_seconds: float = 60.0
    page_memory_mb: int = 2048
    max_page_chars: int = 200_000
    # Limits for structured files (JSON, CSV, XML): nesting depth, and cells or lines read.
    max_depth: int = 200
    max_cells: int = 200_000
    policy: Policy = field(default_factory=Policy)
    third_reader: ThirdReaderSettings = field(default_factory=ThirdReaderSettings)

    def sha256(self) -> str:
        return hashlib.sha256(json.dumps(asdict(self), sort_keys=True).encode()).hexdigest()

    def reading_sha256(self) -> str:
        """The settings that change what is read, leaving out the decision policy: a
        reading can be decided again under another policy without reading it again."""
        # The safety limits decide whether a page is read, not how: left out too.
        ignored = {"policy", "third_reader", "isolate_pages", "page_timeout_seconds", "page_memory_mb"}
        settings = {key: value for key, value in asdict(self).items() if key not in ignored}
        return hashlib.sha256(json.dumps(settings, sort_keys=True).encode()).hexdigest()
