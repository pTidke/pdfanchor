"""The third reader: a model reads the crops of lines the two OCR engines disagree on.

Only disputed scanned lines are read: flagged for the disagreement, and for nothing
else but a lone "|" (`policy.settleable`). Their pages are
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
from .isolation import PageReader
from .model import Box, Document, Flag, Line, Reading
from .ocr import LineReader, LineUnreadable
from .policy import VLM, settleable

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
            and settleable(line)
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


def _key(image, version: str, settings: ThirdReaderSettings) -> str:
    knobs = {k: v for k, v in asdict(settings).items() if k != "budget_seconds"}
    digest = hashlib.sha256()
    parts = (image.mode, repr(image.size), image.tobytes(), version)
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
    if not isinstance(entry, dict):
        return None
    values = [entry[name] for name in ("text", "unreadable") if name in entry]
    return entry if values and all(isinstance(value, str) for value in values) else None


def _store(cache: Path | None, key: str, entry: dict) -> None:
    if cache is None:
        return
    try:
        cache.mkdir(parents=True, exist_ok=True)
        (cache / f"{key[:24]}.json").write_text(json.dumps(entry), encoding="utf8")
    except OSError:  # a cache that cannot be written only costs a later re-read
        pass


def _why(error: Exception) -> str:
    return getattr(error, "message", None) or str(error) or type(error).__name__


def _unread(page, message: str) -> None:
    message = f"The page could not be redrawn: {message}"
    page.flags.append(Flag(rule=_RULE, severity="review", message=message))


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
    version = reader.version()  # once a run: it may ask the installed libraries
    cache = Path(cache) if cache is not None else None
    pages = {page.number: page for page in document.pages}
    start, skipped, prepared = clock(), 0, False
    try:
        # Drawn again at the third reader's resolution, whatever the source resolution was.
        pages_again = PageReader(data, replace(config, dpi=s.dpi, low_res_render_dpi=s.dpi))
    except Exception as error:  # the first decisions stand, each page says why
        for number in wanted:
            _unread(pages[number], _why(error))
        return
    with pages_again:
        for number, lines in wanted.items():
            page = pages[number]
            if s.budget_seconds is not None and clock() - start >= s.budget_seconds:
                skipped += len(lines)  # spent: no page is drawn for lines it cannot read
                continue
            try:
                image = pages_again.read(number, crosscheck=True).image
                if image is None:
                    raise RuntimeError("The page could not be drawn again.")
            except Exception as error:  # one bad page must not lose the others
                _unread(page, _why(error))
                continue
            for line in lines:
                piece = crop(image, line.box, s)
                key = _key(piece, version, s)
                entry = _cached(cache, key)
                if entry is None:
                    if s.budget_seconds is not None and clock() - start >= s.budget_seconds:
                        skipped += 1
                        continue
                    if not prepared:
                        reader.prepare()  # a model that will not load fails the run, not a line
                        prepared = True
                    try:
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
