"""Figures: where an image's words come from, and where the image itself is kept.

`describe` reads the text in a picture that has no description from its file, with the same
two-reader rules as a scanned page. `save` writes each distinct image once, under a name made
from its citation, and sets `Figure.path` to where `document.md` can link it.

Everything here goes by the bytes: a name or an extension from the file is never believed.
An image is looked at (its header only) before anything is decoded, and nothing over
`max_render_pixels` is decoded at all. Core only; Pillow is a core dependency."""

import hashlib
import io
import re
import warnings
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from PIL import Image, ImageOps

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
_PNG_MODES = {"1", "L", "LA", "P", "RGB", "RGBA", "I;16"}
_SIXTEEN = {"I;16", "I;16L", "I;16B", "I;16N"}
_PROLOG = re.compile(
    rb"^<\?xml[^>]*\?>\s*(?:<!--.*?-->\s*|<!DOCTYPE[^>]*>\s*)*<svg", re.DOTALL | re.IGNORECASE
)
_TINY = "image.tiny-skipped"


@dataclass(frozen=True)
class Info:
    """What the header of an image says. `kind` is "web" (saved as it is), "svg" (the same,
    never read), "convert" (Pillow opens it; saved as PNG) or "opaque" (Pillow cannot)."""

    kind: str
    ext: str
    width: int = 0
    height: int = 0
    measured: bool = False
    too_large: bool = False
    frames: int = 1
    own_ext: str = "bin"  # the extension a "convert" image keeps if it cannot be converted

    @property
    def tiny(self) -> bool:
        return self.measured and self.width < MIN_SIDE and self.height < MIN_SIDE


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
    if data[:4] == b"\xd7\xcd\xc6\x9a" or data[:6] in (
        b"\x01\x00\x09\x00\x00\x03",
        b"\x02\x00\x09\x00\x00\x03",
    ):
        return "wmf"
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


def probe(data: bytes, config: Config) -> Info:
    """Look at the header only. Never decodes pixels, and survives a decompression bomb."""
    if _svg(data):
        return Info("svg", "svg")
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")  # a large image is judged by max_render_pixels, below
            with Image.open(io.BytesIO(data)) as image:
                fmt, (width, height) = image.format, image.size
                try:
                    orientation = image.getexif().get(0x0112)
                except Exception:  # a damaged EXIF block is not a damaged picture
                    orientation = None
                frames = 1 if fmt in WEB else getattr(image, "n_frames", 1)
    except Image.DecompressionBombError:
        ext = _sniff(data)
        return Info("web" if ext in WEB.values() else "opaque", ext, too_large=True)
    except Exception:  # unknown, or a header Pillow will not take
        return Info("opaque", _sniff(data))
    if orientation in (5, 6, 7, 8):
        width, height = height, width
    big = width * height > config.max_render_pixels
    if fmt in WEB:
        return Info("web", WEB[fmt], width, height, True, big)
    return Info("convert", "png", width, height, True, big, frames, _own_extension(fmt))


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
        with Image.open(io.BytesIO(data)) as image:
            image.seek(0)
            image.load()
            return _flat(ImageOps.exif_transpose(image))


def _png(data: bytes) -> bytes:
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        with Image.open(io.BytesIO(data)) as image:
            image.seek(0)
            image.load()
            upright = ImageOps.exif_transpose(image)
            if upright.mode not in _PNG_MODES:
                upright = upright.convert("RGB")
            buffer = io.BytesIO()
            upright.save(buffer, format="PNG")
            return buffer.getvalue()


class _Index:
    """The bytes behind each figure, hashed once per bytes object and looked at once per
    distinct image, so a file named by a thousand figures costs one pass."""

    def __init__(self, result: "ReadResult", config: Config):
        self.result, self.config = result, config
        self._sha: dict[int, str] = {}
        self._info: dict[str, Info] = {}

    def of(self, figure: Figure) -> tuple[str, bytes, Info] | None:
        data = self.result.images.get(figure.id)
        if not data:
            return None
        sha = self._sha.get(id(data))
        if sha is None:
            sha = self._sha[id(data)] = hashlib.sha256(data).hexdigest()
        if sha not in self._info:
            self._info[sha] = probe(data, self.config)
        return sha, data, self._info[sha]


def _add(figure: Figure, rule: str, message: str) -> None:
    if not any(flag.rule == rule for flag in figure.flags):
        figure.flags.append(Flag(rule=rule, severity="review", message=message))
        if figure.status == "accepted":
            figure.status = "flagged"


def _note(figure: Figure, info: Info) -> None:
    if info.too_large:
        _add(
            figure,
            "image.too-large",
            "The image has too many pixels to read or convert; it is kept as it is.",
        )
    elif info.kind == "opaque":
        _add(figure, "image.not-viewable", "The image is in a format that cannot be shown on the web.")


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


def describe(result: "ReadResult", engines: Sequence[OcrEngine], config: Config) -> None:
    """Give each figure with no description from its file the text OCR reads in it. The lines
    are decided like any other: `policy.decide` runs after this. A distinct image is read
    once, however many figures show it; each figure gets its own copy of the lines."""
    index = _Index(result, config)
    reads: dict[str, _Read] = {}
    for figure in _figures(result):
        got = index.of(figure)
        if got is None:  # remote, linked or missing: nothing to read, and the path stays
            continue
        sha, data, info = got
        _note(figure, info)
        if figure.description_source != "none" or not engines:
            continue
        if info.kind in ("svg", "opaque") or info.too_large or info.tiny:
            continue
        if sha not in reads:
            reads[sha] = _read(data, engines, config)
        shared = reads[sha]
        for flag in shared.flags:
            _add(figure, flag.rule, flag.message)
        copies = []
        for k, line in enumerate(shared.lines, 1):
            copy = line.model_copy(deep=True)
            copy.id, copy.page, copy.anchor = f"{figure.id}-l{k}", figure.page, figure.anchor.model_copy()
            copies.append(copy)
        text = " ".join(line.text for line in copies).strip()
        if text:
            figure.ocr_lines = copies
            figure.description = text[:MAX_DESCRIPTION]
            figure.description_source = "ocr"


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


def _payload(data: bytes, info: Info) -> tuple[bytes, str, list[Flag]]:
    """What is written for an image, its extension, and what was lost making it."""
    if info.kind != "convert" or info.too_large:
        return data, info.ext if info.kind != "convert" else info.own_ext, []
    flags = []
    try:
        payload = _png(data)
    except Exception:
        flags.append(
            Flag(
                rule="image.not-viewable",
                severity="review",
                message="The image could not be converted for the web.",
            )
        )
        return data, info.own_ext, flags
    if info.frames > 1:
        flags.append(
            Flag(
                rule="image.first-frame-only",
                severity="review",
                message=f"Only the first of {info.frames} frames was kept.",
            )
        )
    return payload, "png", flags


def save(result: "ReadResult", out: str | Path, config: Config | None = None) -> None:
    """Write each distinct image once to `out/images/` and set `Figure.path` to
    `images/<name>.<ext>`. Figures with no bytes keep the path their reader set. An image under
    32x32 pixels is not saved: it is dropped, and counted in the document flag `image.tiny-skipped`,
    unless its description is the file's own, when it stays without a file."""
    config = config or Config()
    out = Path(out)
    index = _Index(result, config)
    placed: dict[str, tuple[str, list[Flag]]] = {}
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
            if info.measured:
                figure.width, figure.height = info.width, info.height
            _note(figure, info)
            if info.tiny:
                if figure.description_source in ("caption", "alt") and figure.description:
                    kept.append(figure)
                else:
                    removed += 1
                continue
            if sha not in placed:
                payload, ext, flags = _payload(data, info)
                path = f"images/{_unique(_base(figure), taken)}.{ext}"
                (out / "images").mkdir(parents=True, exist_ok=True)
                (out / path).write_bytes(payload)
                placed[sha] = (path, flags)
            figure.path = placed[sha][0]
            for flag in placed[sha][1]:
                _add(figure, flag.rule, flag.message)
            kept.append(figure)
        page.figures = kept
    alive = {figure.id for figure in _figures(result)}
    for figure_id in [key for key in result.images if key not in alive]:
        del result.images[figure_id]  # a dropped figure's bytes are not kept
    _count_tiny(result, removed)
    _recount(result)


def _count_tiny(result: "ReadResult", removed: int) -> None:
    if not removed:
        return
    flags = result.document.flags
    before = next((flag for flag in flags if flag.rule == _TINY), None)
    total = removed + (int(before.message.split()[0]) if before else 0)
    flags[:] = [flag for flag in flags if flag.rule != _TINY]
    flags.append(
        Flag(
            rule=_TINY,
            severity="review",
            message=f"{total} images smaller than {MIN_SIDE}×{MIN_SIDE} pixels were not saved.",
        )
    )


def _recount(result: "ReadResult") -> None:
    summary = result.document.summary
    if "figures" in summary:
        figures = list(_figures(result))
        summary["figures"] = len(figures)
        summary["figures_flagged"] = sum(f.status == "flagged" for f in figures)
        summary["figures_blocked"] = sum(f.status == "blocked" for f in figures)
