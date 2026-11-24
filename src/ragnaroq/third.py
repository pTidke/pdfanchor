"""The third reader: a model reads the crops of lines the two OCR engines disagree on.

Only scanned lines whose sole flag is `crosscheck.disagreement` are read. Their pages are
drawn again at a higher resolution in the isolated page worker, each line is cut out, and
the reader's text is stored as a `vlm:<name>` reading. This stage never decides anything:
`policy.decide` does, afterwards. A line the reader cannot read keeps its first decision
and its page says why.
"""

import hashlib
import json
import time
from dataclasses import asdict, replace
from pathlib import Path

from PIL import Image

from .config import Config, ThirdReaderSettings
from .isolation import BudgetExceeded, PageReader
from .model import Box, Document, Flag, Line, Reading
from .ocr import LineReader, LineUnreadable
from .policy import VLM

_RULE = "third-reader.unread"


def candidates(document: Document) -> dict[int, list[Line]]:
    """Page number to the lines the third reader should read, in order."""
    found: dict[int, list[Line]] = {}
    for page in document.pages:
        if page.kind != "scanned":
            continue
        lines = [
            line
            for line in sorted(page.lines, key=lambda line: line.order)
            if not line.excluded
            and line.status == "flagged"
            and line.flags
            and all(flag.rule == "crosscheck.disagreement" for flag in line.flags)
        ]
        if lines:
            found[page.number] = lines
    return found


def crop(image, box: Box, settings: ThirdReaderSettings):
    """The line's box (in points) cut from a page drawn at `settings.dpi`, with a little
    room left and right, in a white frame."""
    scale = settings.dpi / 72
    pad = settings.pad_points
    left = max(0, int((box.x0 - pad) * scale))
    right = min(image.width, round((box.x1 + pad) * scale))
    top = max(0, int(box.top * scale))
    bottom = min(image.height, round(box.bottom * scale))
    piece = image.convert("RGB").crop((left, top, max(right, left + 1), max(bottom, top + 1)))
    frame = settings.frame_pixels
    canvas = Image.new("RGB", (piece.width + 2 * frame, piece.height + 2 * frame), "white")
    canvas.paste(piece, (frame, frame))
    return canvas


def _key(image, reader: LineReader, settings: ThirdReaderSettings) -> str:
    knobs = {k: v for k, v in asdict(settings).items() if k != "budget_seconds"}
    digest = hashlib.sha256()
    parts = (image.mode, repr(image.size), image.tobytes(), reader.version())
    parts += (json.dumps(knobs, sort_keys=True),)
    for part in parts:
        digest.update(part if isinstance(part, bytes) else part.encode())
        digest.update(b"|")
    return digest.hexdigest()


def _cached(cache: Path | None, key: str) -> dict | None:
    if cache is None:
        return None
    try:
        entry = json.loads((cache / f"{key[:24]}.json").read_text(encoding="utf8"))
    except (OSError, ValueError):
        return None
    return entry if isinstance(entry, dict) and ("text" in entry or "unreadable" in entry) else None


def _store(cache: Path | None, key: str, entry: dict) -> None:
    if cache is None:
        return
    try:
        cache.mkdir(parents=True, exist_ok=True)
        (cache / f"{key[:24]}.json").write_text(json.dumps(entry), encoding="utf8")
    except OSError:  # a cache that cannot be written only costs a later re-read
        pass


def read_disputed(
    document: Document,
    data: bytes,
    config: Config,
    reader: LineReader,
    cache: str | Path | None = None,
    clock=time.monotonic,
) -> None:
    """Add a `vlm:<name>` reading to each disputed scanned line, within the limits."""
    wanted = candidates(document)
    if not wanted:
        return
    s = config.third_reader
    cache = Path(cache) if cache is not None else None
    pages = {page.number: page for page in document.pages}
    start, skipped, prepared = clock(), 0, False
    # Drawn again at the third reader's resolution, whatever the source resolution was.
    with PageReader(data, replace(config, dpi=s.dpi, low_res_render_dpi=s.dpi)) as pages_again:
        for number, lines in wanted.items():
            page = pages[number]
            try:
                image = pages_again.read(number, crosscheck=True).image
                if image is None:
                    raise RuntimeError("The page could not be drawn again.")
            except (BudgetExceeded, RuntimeError) as error:
                message = getattr(error, "message", None) or str(error) or type(error).__name__
                page.flags.append(
                    Flag(rule=_RULE, severity="review", message=f"The page could not be redrawn: {message}")
                )
                continue
            for line in lines:
                piece = crop(image, line.box, s)
                key = _key(piece, reader, s)
                entry = _cached(cache, key)
                if entry is None:
                    if s.budget_seconds is not None and clock() - start >= s.budget_seconds:
                        skipped += 1
                        continue
                    try:
                        if not prepared:
                            reader.prepare()
                            prepared = True
                        entry = {"text": reader.read_line(piece, s.max_new_tokens, s.line_seconds)}
                    except LineUnreadable as error:
                        entry = {"unreadable": str(error) or "the reader gave up"}
                        _store(cache, key, entry)
                    except Exception as error:  # one bad line must not lose the others
                        page.flags.append(
                            Flag(
                                rule=_RULE,
                                severity="review",
                                message=f"{line.id}: the third reader failed ({type(error).__name__}).",
                            )
                        )
                        continue
                    else:
                        _store(cache, key, entry)
                if "text" in entry:
                    line.readings = [r for r in line.readings if not r.source.startswith(VLM)]
                    line.readings.append(Reading(source=f"{VLM}{reader.name}", text=entry["text"]))
                else:
                    message = f"{line.id}: {entry['unreadable']}"
                    page.flags.append(Flag(rule=_RULE, severity="review", message=message))
    if skipped:
        document.flags.append(
            Flag(
                rule="third-reader.budget",
                severity="review",
                message=f"{skipped} disputed lines were not read by the third reader: its time budget of "
                f"{s.budget_seconds:g} seconds ran out.",
            )
        )
