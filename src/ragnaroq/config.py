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

    version: str = "0.2"  # 0.2: codes mixing letters and digits are checked (validators/codes.py)
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
    policy: Policy = field(default_factory=Policy)

    def sha256(self) -> str:
        return hashlib.sha256(json.dumps(asdict(self), sort_keys=True).encode()).hexdigest()

    def reading_sha256(self) -> str:
        """The settings that change what is read, leaving out the decision policy: a
        reading can be decided again under another policy without reading it again."""
        settings = {key: value for key, value in asdict(self).items() if key != "policy"}
        return hashlib.sha256(json.dumps(settings, sort_keys=True).encode()).hexdigest()
