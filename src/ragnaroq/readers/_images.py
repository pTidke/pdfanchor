"""Where an image named by a web page or a Markdown file comes from, and what is refused.

Nothing here reaches the network, and nothing outside the file's folder is opened: a remote
image is kept as its URL, a `data:` URI is decoded in memory, and a relative path must stay
inside `base` after symlinks are followed. Core only."""

import base64
import binascii
import re
from dataclasses import dataclass
from pathlib import Path, PurePosixPath, PureWindowsPath
from urllib.parse import unquote, unquote_to_bytes

from ..config import Config
from ..model import Flag

_SCHEME = re.compile(r"^([A-Za-z][A-Za-z0-9+.\-]*):")


@dataclass
class Loaded:
    """What an `src` came to: its bytes, the URL kept for a remote image, and why not, if not."""

    data: bytes | None = None
    path: str = ""
    flag: Flag | None = None


def _refused(rule: str, message: str) -> Loaded:
    return Loaded(flag=Flag(rule=rule, severity="review", message=message))


def _remote(src: str) -> Loaded:
    """Kept as its address; nothing is downloaded."""
    loaded = _refused("image.remote-not-fetched", "The image is on another site; it was not fetched.")
    loaded.path = src
    return loaded


def _outside() -> Loaded:
    return _refused("image.outside-folder", "The image is not inside the file's folder; it was not opened.")


def _too_large(config: Config) -> Loaded:
    return _refused("image.too-large", f"The image is larger than {config.max_image_bytes} bytes; not read.")


def _unreadable() -> Loaded:
    return _refused("image.unreadable", "The image data could not be read.")


def _not_found() -> Loaded:
    return _refused("image.not-found", "The image file was not found beside the file.")


def _data_uri(src: str, config: Config) -> Loaded:
    header, comma, payload = src[5:].partition(",")
    if not comma:
        return _unreadable()
    # The limit is on the encoded length, so nothing large is ever decoded.
    if len(payload) > config.max_image_bytes:
        return _too_large(config)
    try:
        if header.rstrip().lower().endswith(";base64"):
            text = "".join(unquote(payload).split())
            data = base64.b64decode(text + "=" * (-len(text) % 4), validate=True)
        else:
            data = unquote_to_bytes(payload)
    except (binascii.Error, ValueError):
        return _unreadable()
    return Loaded(data=data) if data else _unreadable()


def _local(src: str, base: Path | None, config: Config) -> Loaded:
    relative = unquote(re.split(r"[?#]", src, maxsplit=1)[0])
    if not relative.strip():
        return _not_found()
    if "\0" in relative:
        return _outside()
    if PurePosixPath(relative).is_absolute() or PureWindowsPath(relative).anchor:
        return _outside()
    if base is None:
        return _not_found()
    try:
        root = Path(base).resolve()
        path = (root / relative).resolve()
    except (OSError, RuntimeError):  # a symlink loop, or a path the system cannot hold
        return _outside()
    if not path.is_relative_to(root):
        return _outside()
    try:
        if not path.is_file():
            return _not_found()
        if path.stat().st_size > config.max_image_bytes:
            return _too_large(config)
        with path.open("rb") as handle:
            data = handle.read(config.max_image_bytes + 1)
    except OSError:
        return _unreadable()
    return _too_large(config) if len(data) > config.max_image_bytes else Loaded(data=data)


def load(src: str | None, base: Path | None, config: Config) -> Loaded:
    """The image `src` names, as a page or Markdown file at `base` would see it."""
    src = (src or "").strip()
    if not src:
        return _not_found()
    if src.startswith("//"):
        return _remote(src)
    scheme = _SCHEME.match(src)
    if scheme and len(scheme.group(1)) > 1:  # a single letter is a drive, not a scheme
        name = scheme.group(1).lower()
        if name in ("http", "https"):
            return _remote(src)
        return _data_uri(src, config) if name == "data" else _outside()
    if src.startswith(("/", "\\")):
        return _outside()
    return _local(src, base, config)
