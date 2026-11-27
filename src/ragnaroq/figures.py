"""Figures: where an image's words come from, and where the image itself is kept.

`describe` reads the text in a picture that has no description from its file, with the same
two-reader rules as a scanned page. `save` writes each distinct image once, under a name made
from its citation, and sets `Figure.path` to where `document.md` can link it. Each ends by
deciding the document again, so the verdicts are never older than the figures.

Everything here goes by the bytes: a name or an extension from the file is never believed.
Pillow is only ever asked to open `ALLOWED` formats, and only after a look at the header:
nothing over `max_render_pixels` is decoded at all. Core only; Pillow is a core dependency."""

import hashlib
import io
import re
import time
import warnings
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from PIL import Image, ImageOps

from . import policy
from .anchors import cite
from .config import Config
from .model import Figure, Flag, Line
from .ocr import OcrEngine

if TYPE_CHECKING:
    from .readers import ReadResult

MIN_SIDE = 32  # an image smaller than this in both directions is counted, not saved
MAX_DESCRIPTION = 300
MAX_LINES = 1000  # per image; the rest is flagged, so a hostile picture cannot fill memory
MAX_NAME = 80
WEB = {"PNG": "png", "JPEG": "jpg", "MPO": "jpg", "GIF": "gif", "WEBP": "webp"}
# The only formats Pillow is ever asked to open. Anything else it knows (EPS, which runs
# Ghostscript on the file's own PostScript, among them) is kept as bytes and never opened.
ALLOWED = ["PNG", "JPEG", "GIF", "WEBP", "BMP", "DIB", "TIFF", "ICO", "PPM"]
_PNG_MODES = {"1", "L", "LA", "P", "RGB", "RGBA", "I;16"}
_SIXTEEN = {"I;16", "I;16L", "I;16B", "I;16N"}
_PROLOG = re.compile(
    rb"^<\?xml[^>]*\?>\s*(?:<!--.*?-->\s*|<!DOCTYPE[^>]*>\s*)*<svg", re.DOTALL | re.IGNORECASE
)
_TINY = "image.tiny-skipped"


@dataclass(frozen=True)
class Info:
    """What looking at an image told us. `kind` is "web" (saved as it is), "svg" (the same,
    never read), "convert" (Pillow opens it; saved as PNG) or "opaque" (Pillow will not)."""

    kind: str
    ext: str
    width: int = 0
    height: int = 0
    measured: bool = False
    too_large: bool = False
    more_frames: bool = False
    broken: bool = False  # the header reads but the pixels do not decode
    own_ext: str = "bin"  # the extension a "convert" image keeps if it cannot be converted

    @property
    def tiny(self) -> bool:
        return self.measured and self.width < MIN_SIDE and self.height < MIN_SIDE


_WMF = (b"\x01\x00\x09\x00\x00\x03", b"\x02\x00\x09\x00\x00\x03")


def _sniff(data: bytes) -> str:
    """The extension the bytes claim, for what Pillow will not or cannot open."""
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if data.startswith(b"\xff\xd8\xff"):
        return "jpg"
    if data.startswith((b"GIF87a", b"GIF89a")):
        return "gif"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp"
    if data[:4] == b"\x01\x00\x00\x00" and data[40:44] == b" EMF":
        return "emf"
    if data[:4] == b"\xd7\xcd\xc6\x9a" or data[:6] in _WMF:
        return "wmf"
    if data.startswith((b"%!PS", b"\xc5\xd0\xd3\xc6")):
        return "eps"
    if data.startswith(b"%PDF-"):
        return "pdf"
    if data.startswith(b"BM"):
        return "bmp"
    if data.startswith((b"II*\x00", b"MM\x00*")):
        return "tif"
    return "bin"


def _svg(data: bytes) -> bool:
    head = data[:4096].lstrip(b"\xef\xbb\xbf \t\r\n\x0b\x0c")
    return head[:4].lower() == b"<svg" or bool(_PROLOG.match(head))


def _own_extension(fmt: str) -> str:
    for suffix, name in sorted(Image.registered_extensions().items()):
        if name == fmt:
            return suffix.lstrip(".")
    return "bin"


def _more_frames(image: Image.Image) -> bool:
    """Whether there is a second frame, by moving to it: never by counting, which walks
    every frame of the file."""
    try:
        image.seek(1)
    except Exception:  # EOFError for one frame; anything else, treated alike
        return False
    finally:
        try:
            image.seek(0)
        except Exception:
            pass
    return True


def probe(data: bytes, config: Config) -> Info:
    """Look at an image once: its header, and (unless it is over the pixel limit) its first
    frame's pixels, to know that they decode. A decompression bomb is never decoded."""
    if _svg(data):
        return Info("svg", "svg")
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")  # a large image is judged by max_render_pixels, below
            with Image.open(io.BytesIO(data), formats=ALLOWED) as image:
                fmt, (width, height) = image.format, image.size
                try:
                    orientation = image.getexif().get(0x0112)
                except Exception:  # a damaged EXIF block is not a damaged picture
                    orientation = None
                big = width * height > config.max_render_pixels
                more = fmt not in WEB and not big and _more_frames(image)
                broken = False
                if not big:
                    try:
                        image.load()
                    except Exception:
                        broken = True
    except Image.DecompressionBombError:
        ext = _sniff(data)
        return Info("web" if ext in WEB.values() else "opaque", ext, too_large=True)
    except Exception:  # unknown, or a header Pillow will not take
        return Info("opaque", _sniff(data))
    if fmt in WEB:
        if orientation in (5, 6, 7, 8):  # a viewer shows it upright
            width, height = height, width
        return Info("web", WEB[fmt], width, height, True, big, False, broken)
    return Info("convert", "png", width, height, True, big, more, broken, _own_extension(fmt))


def _flat(image: Image.Image) -> Image.Image:
    """Grey or colour pixels a reader can use: a 16-bit picture scaled down, not clipped, and a
    transparent one drawn on white, not black."""
    if image.mode in _SIXTEEN:
        image = image.convert("I")
    if image.mode == "I":
        return image.point(lambda value: value / 256).convert("L")
    if image.mode in ("RGBA", "LA", "PA") or "transparency" in image.info:
        rgba = image.convert("RGBA")
        page = Image.new("RGB", rgba.size, "white")
        page.paste(rgba, mask=rgba.getchannel("A"))
        return page
    if image.mode == "1":
        return image.convert("L")
    return image if image.mode in ("L", "RGB") else image.convert("RGB")


def _for_ocr(data: bytes, config: Config) -> Image.Image:
    """The first frame, turned upright by its EXIF orientation, in a mode readers can use.
    Raises when the pixels cannot be decoded."""
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        with Image.open(io.BytesIO(data), formats=ALLOWED) as image:
            image.seek(0)
            image.load()
            return _flat(ImageOps.exif_transpose(image))


def _png(data: bytes) -> tuple[bytes, tuple[int, int]]:
    """The first frame as an upright PNG, and the size of what was written."""
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        with Image.open(io.BytesIO(data), formats=ALLOWED) as image:
            image.seek(0)
            image.load()
            upright = ImageOps.exif_transpose(image)
            if upright.mode not in _PNG_MODES:
                upright = upright.convert("RGB")
            buffer = io.BytesIO()
            upright.save(buffer, format="PNG")
            return buffer.getvalue(), upright.size


class _Index:
    """The bytes behind each figure, hashed once per bytes object and looked at once per
    distinct image. It lives on the result, so `describe` and `save` share it."""

    def __init__(self, result: "ReadResult", config: Config):
        self.result, self.config = result, config
        self._sha: dict[int, tuple[bytes, str]] = {}  # the bytes are held, so an id is never reused
        self._info: dict[str, Info] = {}

    def of(self, figure: Figure) -> tuple[str, bytes, Info] | None:
        data = self.result.images.get(figure.id)
        if not data:
            return None
        held = self._sha.get(id(data))
        if held is None:
            held = self._sha[id(data)] = (data, hashlib.sha256(data).hexdigest())
        sha = held[1]
        if sha not in self._info:
            self._info[sha] = probe(data, self.config)
        return sha, data, self._info[sha]


def _index(result: "ReadResult", config: Config) -> _Index:
    return result.cache.setdefault(("figures", config.max_render_pixels), _Index(result, config))


def _add(figure: Figure, rule: str, message: str) -> None:
    if not any(flag.rule == rule for flag in figure.flags):
        figure.flags.append(Flag(rule=rule, severity="review", message=message))


def _note(figure: Figure, info: Info) -> None:
    if info.too_large:
        _add(
            figure,
            "image.too-large",
            "The image has too many pixels to read or convert; it is kept as it is.",
        )
    if info.kind == "opaque" or (info.too_large and info.kind == "convert"):
        _add(figure, "image.not-viewable", "The image is in a format that cannot be shown on the web.")
    elif info.broken:
        _add(figure, "image.not-viewable", "The image could not be decoded.")


def _figures(result: "ReadResult"):
    return (figure for page in result.document.pages for figure in page.figures)


@dataclass
class _Read:
    lines: list[Line] = field(default_factory=list)
    flags: list[Flag] = field(default_factory=list)


def _read(data: bytes, engines: Sequence[OcrEngine], config: Config) -> _Read:
    from .pipeline import ocr_lines

    read = _Read()
    try:
        image = _for_ocr(data, config)
    except Exception:
        read.flags.append(
            Flag(rule="image.not-viewable", severity="review", message="The image could not be decoded.")
        )
        return read
    try:
        read.lines = ocr_lines(
            image,
            engines,
            config,
            page=0,
            anchor=None,
            prefix="",
            flags=read.flags,
            points=False,
            what="image",
        )
    except Exception as error:
        read.flags.append(
            Flag(
                rule="ocr.reader-failed",
                severity="review",
                message=f"ocr:{engines[0].name} could not read this image ({type(error).__name__}).",
            )
        )
        return read
    if len(read.lines) > MAX_LINES:
        del read.lines[MAX_LINES:]
        read.flags.append(
            Flag(
                rule="image.ocr-truncated",
                severity="review",
                message=f"Only the first {MAX_LINES} lines were kept.",
            )
        )
    return read


def _cut(text: str) -> str:
    """At most MAX_DESCRIPTION characters, ended at a word and marked, so a number is never
    shortened to a different number. One long token is cut where it stands."""
    if len(text) <= MAX_DESCRIPTION:
        return text
    head = text[: MAX_DESCRIPTION + 1]
    stops = [i for i, ch in enumerate(head) if ch.isspace()]
    kept = head[: stops[-1]].rstrip() if stops else ""
    return (kept or text[:MAX_DESCRIPTION]) + "…"


def describe(
    result: "ReadResult", engines: Sequence[OcrEngine], config: Config, deadline: float | None = None
) -> None:
    """Give each figure with no description from its file the text OCR reads in it, then
    decide the document again. A distinct image is read once, however many figures show it;
    each figure gets its own copy of the lines, until `max_figure_ocr_lines` is used up: past
    it a figure keeps its description and has no lines. An image not reached by `deadline`
    (a `time.monotonic()` value) is not read, and says so."""
    index = _index(result, config)
    reads: dict[str, _Read] = {}
    used = sum(len(figure.ocr_lines) for figure in _figures(result))
    for figure in _figures(result):
        got = index.of(figure)
        if got is None:  # remote, linked or missing: nothing to read, and the path stays
            continue
        sha, data, info = got
        _note(figure, info)
        if figure.description_source != "none" or not engines:
            continue
        if info.kind in ("svg", "opaque") or info.too_large or info.tiny or info.broken:
            continue
        if sha not in reads:
            if deadline is not None and time.monotonic() >= deadline:
                _add(figure, "image.ocr-skipped", "Not read within the file's time limit.")
                continue
            reads[sha] = _read(data, engines, config)
        shared = reads[sha]
        for flag in shared.flags:
            _add(figure, flag.rule, flag.message)
        text = " ".join(line.text for line in shared.lines).strip()
        if not text:
            continue
        figure.description, figure.description_source = _cut(text), "ocr"
        if used + len(shared.lines) > config.max_figure_ocr_lines:
            _add(
                figure,
                "image.ocr-truncated",
                "The text lines of this image are not kept: the limit was reached.",
            )
            continue
        used += len(shared.lines)
        for k, line in enumerate(shared.lines, 1):
            copy = line.model_copy(deep=True)
            copy.id, copy.page, copy.anchor = f"{figure.id}-l{k}", figure.page, figure.anchor.model_copy()
            figure.ocr_lines.append(copy)
    policy.decide(result.document, config.policy)


def _base(figure: Figure) -> str:
    def clean(text: str) -> str:
        return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:MAX_NAME].strip("-")

    return clean(cite(figure.anchor)) or clean(figure.id) or "image"


def _unique(base: str, taken: dict[str, int]) -> str:
    """`base`, or `base-2`, `base-3` and so on when the name is taken. `taken` counts the
    names given for each base; a numbered name is itself kept, so none is given twice."""
    n = taken.get(base, 0) + 1
    while True:
        suffix = f"-{n}" if n > 1 else ""
        name = base[: MAX_NAME - len(suffix)].rstrip("-") + suffix
        if name not in taken:
            break
        n += 1
    taken[base] = n
    taken.setdefault(name, 1)
    return name


def _payload(data: bytes, info: Info) -> tuple[bytes, str, list[Flag], tuple[int, int] | None]:
    """What is written for an image, its extension, what was lost making it, and the size of
    what was written when that is not the size of the original."""
    if info.kind != "convert" or info.too_large:
        return data, info.ext if info.kind != "convert" else info.own_ext, [], None
    flags = []
    try:
        payload, size = _png(data)
    except Exception:
        flags.append(
            Flag(
                rule="image.not-viewable",
                severity="review",
                message="The image could not be converted for the web.",
            )
        )
        return data, info.own_ext, flags, None
    if info.more_frames:
        flags.append(
            Flag(rule="image.first-frame-only", severity="review", message="Only the first frame was kept.")
        )
    return payload, "png", flags, size


def save(result: "ReadResult", out: str | Path, config: Config) -> None:
    """Write each distinct image once to `out/images/` and set `Figure.path` to
    `images/<name>.<ext>`, then decide the document again. Figures with no bytes keep the path
    their reader set. An image under 32x32 pixels is not saved: it is dropped, and counted in
    the document flag `image.tiny-skipped`, unless its description is the file's own, when it
    stays without a file."""
    out = Path(out)
    index = _index(result, config)
    placed: dict[str, tuple[str, list[Flag], tuple[int, int] | None]] = {}
    taken: dict[str, int] = {}
    removed = 0
    for page in result.document.pages:
        kept = []
        for figure in page.figures:
            got = index.of(figure)
            if got is None:
                kept.append(figure)
                continue
            sha, data, info = got
            _note(figure, info)
            if info.tiny:
                if figure.description_source in ("caption", "alt") and figure.description:
                    figure.width, figure.height = info.width, info.height
                    kept.append(figure)
                else:
                    removed += 1
                continue
            if sha not in placed:
                payload, ext, flags, size = _payload(data, info)
                path = f"images/{_unique(_base(figure), taken)}.{ext}"
                (out / "images").mkdir(parents=True, exist_ok=True)
                (out / path).write_bytes(payload)
                placed[sha] = (path, flags, size)
            path, flags, size = placed[sha]
            figure.path = path
            if size or info.measured:
                figure.width, figure.height = size or (info.width, info.height)
            for flag in flags:
                _add(figure, flag.rule, flag.message)
            kept.append(figure)
        page.figures = kept
    alive = {figure.id for figure in _figures(result)}
    for figure_id in [key for key in result.images if key not in alive]:
        del result.images[figure_id]  # a dropped figure's bytes are not kept
    _count_tiny(result, removed)
    policy.decide(result.document, config.policy)


def _count_tiny(result: "ReadResult", removed: int) -> None:
    if not removed:
        return
    flags = result.document.flags
    before = next((flag for flag in flags if flag.rule == _TINY), None)
    total = removed + (int(before.message.split()[0]) if before else 0)
    flags[:] = [flag for flag in flags if flag.rule != _TINY]
    message = f"{total} images smaller than {MIN_SIDE}×{MIN_SIDE} pixels were not saved."
    flags.append(Flag(rule=_TINY, severity="review", message=message))
