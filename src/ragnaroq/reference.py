"""Reference sets: PDFs whose correct text is known, for measuring how often the engine is wrong.

A born-digital PDF carries its own text. `make` renders its pages to images, as a
scanner would, optionally roughened (lower resolution, blur, JPEG compression, a
slight tilt), and saves them as an image-only PDF. Next to it goes a truth file:
every word of the original text layer with its box, moved the same way the image
was. The engine then reads the image PDF as it would any scan, and `evaluate`
compares what it read with the truth.

Profiles are fixed and use no randomness, so the same PDF always gives the same
reference. Human-verified references (real scans, typed in or corrected by a
person) use the same truth format.
"""

import hashlib
import io
import json
import math
import time
import unicodedata
from dataclasses import asdict, dataclass
from pathlib import Path

from . import native
from .config import Config

FORMAT = "ragnaroq-reference/1"


@dataclass(frozen=True)
class Profile:
    """How a page is turned into a scan-like image."""

    dpi: int = 200
    blur: float = 0.0  # Gaussian blur radius in pixels
    jpeg_quality: int = 95  # every page image is stored as JPEG, as most scans are
    tilt: float = 0.0  # degrees, counter-clockwise


PROFILES: dict[str, Profile] = {
    "clean": Profile(),
    "low-res": Profile(dpi=110, jpeg_quality=80),
    "blurred": Profile(blur=1.3, jpeg_quality=40),
    "tilted": Profile(tilt=0.8, jpeg_quality=70),
}


@dataclass(frozen=True)
class Word:
    text: str
    x0: float
    top: float
    x1: float
    bottom: float

    @property
    def centre(self) -> tuple[float, float]:
        return (self.x0 + self.x1) / 2, (self.top + self.bottom) / 2


@dataclass(frozen=True)
class TruthPage:
    number: int  # page number in the reference PDF
    source_page: int  # page number in the original PDF
    width: float
    height: float
    words: tuple[Word, ...]
    # Text the reference cannot vouch for, such as words printed sideways: a PDF's text
    # layer often stores those in reverse order. Lines made only of these are not scored.
    ignored: tuple[Word, ...] = ()


@dataclass(frozen=True)
class Truth:
    pages: tuple[TruthPage, ...]
    meta: dict

    def page(self, number: int) -> TruthPage | None:
        return next((page for page in self.pages if page.number == number), None)


def _tilted(word: Word, page_width: float, page_height: float, degrees: float) -> Word:
    """Move a word's box the way the page image is turned about its centre.

    The angle is small, so only the box's position changes, not its size."""
    if not degrees:
        return word
    angle = math.radians(degrees)
    cx, cy = page_width / 2, page_height / 2
    x, y = word.centre
    dx, dy = x - cx, y - cy
    # Counter-clockwise on screen, where y grows downwards.
    nx = cx + dx * math.cos(angle) + dy * math.sin(angle)
    ny = cy - dx * math.sin(angle) + dy * math.cos(angle)
    shift_x, shift_y = nx - x, ny - y
    return Word(word.text, word.x0 + shift_x, word.top + shift_y, word.x1 + shift_x, word.bottom + shift_y)


def _joined(words: list[Word]) -> list[Word]:
    """Join words a reader sees as one, such as a label "20b" whose space the PDF overprints.

    A word merges into the one before it when their tops are within 1 pt and the visible
    gap between them is at most a tenth of the line height (slight overlaps included): a
    reader sees no space there. The merged text has no space and its box is the union."""
    joined: list[Word] = []
    for word in words:
        last = joined[-1] if joined else None
        if (
            last
            and abs(word.top - last.top) <= 1.0
            and word.x0 - last.x1 <= 0.1 * (last.bottom - last.top)
        ):
            joined[-1] = Word(
                last.text + word.text,
                min(last.x0, word.x0),
                min(last.top, word.top),
                max(last.x1, word.x1),
                max(last.bottom, word.bottom),
            )
        else:
            joined.append(word)
    return joined


def _roughen(image, profile: Profile):
    from PIL import Image, ImageFilter

    image = image.convert("L")
    if profile.blur:
        image = image.filter(ImageFilter.GaussianBlur(profile.blur))
    if profile.tilt:
        image = image.rotate(profile.tilt, resample=Image.Resampling.BICUBIC, expand=False, fillcolor=255)
    return image


def make(
    source: str | Path,
    profile: str | Profile,
    out: str | Path,
    config: Config | None = None,
    pages: set[int] | None = None,
) -> tuple[Path, Path]:
    """Write `<name>.<profile>.pdf` and `<name>.<profile>.truth.json` into `out`.

    `pages`, if given, limits it to those page numbers of the original.

    Only pages with a usable text layer are kept, since only they have a known
    answer. Returns the two paths."""
    import pdfplumber

    name = profile if isinstance(profile, str) else "custom"
    chosen = PROFILES[profile] if isinstance(profile, str) else profile
    config = config or Config()
    source = Path(source)
    data = source.read_bytes()
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    stem = f"{source.stem}.{name}"
    images, kept = [], []
    if not data.startswith(b"%PDF-"):
        raise ValueError(f"{source.name} is not a PDF.")
    try:
        pdf = pdfplumber.open(io.BytesIO(data))
    except Exception as error:  # pdfminer raises many kinds for a damaged file
        raise ValueError(f"{source.name} could not be parsed as a PDF.") from error
    with pdf:
        for number, raw in enumerate(pdf.pages, 1):
            if pages and number not in pages:
                continue
            raw, _ = native.visible(raw)
            if native.read_page(raw, number, config).kind != "native":
                continue
            width, height = float(raw.width), float(raw.height)
            origin_x, origin_y = raw.bbox[0], raw.bbox[1]
            words, ignored = [], []
            # Some PDFs draw text twice, slightly offset, to fake bold. Count it once.
            for found in raw.dedupe_chars().extract_words(keep_blank_chars=False, use_text_flow=False):
                text = unicodedata.normalize("NFC", found["text"]).strip()
                # Text with no size is never drawn (hidden labels, link text): no
                # scanner could see it, so it is not part of the answer.
                too_small = (
                    float(found["x1"]) - float(found["x0"]) < 0.5
                    or float(found["bottom"]) - float(found["top"]) < 0.5
                )
                if not text or too_small:
                    continue
                word = Word(
                    text,
                    float(found["x0"]) - origin_x,
                    float(found["top"]) - origin_y,
                    float(found["x1"]) - origin_x,
                    float(found["bottom"]) - origin_y,
                )
                (words if found.get("upright", True) else ignored).append(word)
            words = [_tilted(w, width, height, chosen.tilt) for w in _joined(words)]
            ignored = [_tilted(w, width, height, chosen.tilt) for w in ignored]
            images.append(_roughen(native.render(raw, chosen.dpi, config.max_render_pixels), chosen))
            kept.append(
                TruthPage(
                    number=len(kept) + 1,
                    source_page=number,
                    width=width,
                    height=height,
                    words=tuple(words),
                    ignored=tuple(ignored),
                )
            )
    if not images:
        raise ValueError(f"{source.name} has no page with a usable text layer, so it has no known answer.")
    pdf_path = out / f"{stem}.pdf"
    first, *rest = images
    first.save(
        pdf_path,
        "PDF",
        save_all=True,
        append_images=rest,
        resolution=chosen.dpi,
        quality=chosen.jpeg_quality,
        # A fixed date, so the same input always gives byte-identical files.
        creationDate=time.gmtime(0),
        modDate=time.gmtime(0),
    )
    truth_path = out / f"{stem}.truth.json"
    truth_path.write_text(
        json.dumps(
            {
                "format": FORMAT,
                "made_from": {"filename": source.name, "sha256": hashlib.sha256(data).hexdigest()},
                "profile": {"name": name, **asdict(chosen)},
                "pdf_sha256": hashlib.sha256(pdf_path.read_bytes()).hexdigest(),
                "pages": [
                    {
                        "number": page.number,
                        "source_page": page.source_page,
                        "width": page.width,
                        "height": page.height,
                        "words": [_row(w) for w in page.words],
                        "ignored": [_row(w) for w in page.ignored],
                    }
                    for page in kept
                ],
            },
            ensure_ascii=False,
            indent=1,
        ),
        encoding="utf8",
    )
    return pdf_path, truth_path


def _row(word: Word) -> list:
    return [word.text, round(word.x0, 2), round(word.top, 2), round(word.x1, 2), round(word.bottom, 2)]


def page_numbers(spec: str) -> set[int]:
    """Parse a page selection such as "1-3,7,10-12" into page numbers."""
    chosen: set[int] = set()
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        first, _, last = part.partition("-")
        start, end = int(first), int(last or first)
        if start < 1 or end < start:
            raise ValueError(f"Bad page range: {part}")
        chosen.update(range(start, end + 1))
    return chosen


def load(path: str | Path) -> Truth:
    raw = json.loads(Path(path).read_text(encoding="utf8"))
    if raw.get("format") != FORMAT:
        raise ValueError(f"{Path(path).name} is not a {FORMAT} truth file.")
    pages = tuple(
        TruthPage(
            number=page["number"],
            source_page=page.get("source_page", page["number"]),
            width=page["width"],
            height=page["height"],
            words=tuple(Word(*row) for row in page["words"]),
            ignored=tuple(Word(*row) for row in page.get("ignored", [])),
        )
        for page in raw["pages"]
    )
    meta = {key: value for key, value in raw.items() if key != "pages"}
    return Truth(pages=pages, meta=meta)
