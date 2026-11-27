"""Where an image named by a web page or a Markdown file comes from, and what is refused.

Nothing here reaches the network, and nothing outside the file's folder is opened: a remote
image is kept as its URL, a `data:` URI is decoded in memory, and a relative path must stay
inside `base` after symlinks are followed. Core only."""

import base64
import binascii
import errno
import os
import re
import stat
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


_C0 = "".join(chr(code) for code in range(0x21))  # controls and the space, trimmed from a src
_LONG = 4096


class Images:
    """Loads the images one file names. Bytes are held once per real file, and the distinct
    bytes of a document together stay under `max_unzipped_bytes`."""

    def __init__(self, base: "Path | tuple[str, tuple[str, ...]] | None", config: Config):
        self.base = base
        self.config = config
        self.files: dict[str, bytes] = {}  # real path -> bytes
        self.total = 0
        self.seen: dict[str, Loaded] = {}  # a short src -> what it came to

    def _room(self, size: int) -> bool:
        return self.total + size <= self.config.max_unzipped_bytes

    def _data_uri(self, src: str) -> Loaded:
        config = self.config
        header, comma, payload = src[5:].partition(",")
        if not comma:
            return _unreadable()
        # The limit is on the encoded length, so nothing large is ever decoded.
        if len(payload) > config.max_image_bytes or not self._room(len(payload)):
            return _too_large(config)
        try:
            if header.rstrip().lower().endswith(";base64"):
                text = "".join(unquote(payload).split())
                data = base64.b64decode(text + "=" * (-len(text) % 4), validate=True)
            else:
                data = unquote_to_bytes(payload)
        except (binascii.Error, ValueError):
            return _unreadable()
        if not data:
            return _unreadable()
        self.total += len(data)
        return Loaded(data=data)

    def _local(self, src: str) -> Loaded:
        config = self.config
        relative = unquote(re.split(r"[?#]", src, maxsplit=1)[0])
        if not relative.strip():
            return _not_found()
        if "\0" in relative or PurePosixPath(relative).is_absolute() or PureWindowsPath(relative).anchor:
            return _outside()
        if self.base is None:
            return _not_found()
        if isinstance(self.base, tuple):
            return self._local_below(relative)
        try:
            root = os.path.realpath(self.base)
            path = os.path.realpath(os.path.join(root, relative))
            inside = os.path.commonpath([root, path]) == root
        except (OSError, ValueError, RuntimeError):  # a symlink loop, or a path the system cannot hold
            return _outside()
        if not inside:
            return _outside()
        if path in self.files:
            return Loaded(data=self.files[path])
        try:
            if not os.path.isfile(path):
                return _not_found()
            size = os.stat(path).st_size
            if size > config.max_image_bytes or not self._room(size):
                return _too_large(config)
            with open(path, "rb") as handle:
                data = handle.read(config.max_image_bytes + 1)
        except OSError:
            return _unreadable()
        if len(data) > config.max_image_bytes or not self._room(len(data)):
            return _too_large(config)
        self.files[path] = data
        self.total += len(data)
        return Loaded(data=data)

    def _local_below(self, relative: str) -> Loaded:
        """A file next to the one being read, for a base given as (folder, parts below it). The
        folder is opened, then each part from the one before, never following a link: so a
        link planted since the folder was listed cannot lead out of it."""
        folder, below = self.base
        found: list[str] = []
        for part in relative.split("/"):
            if part in ("", "."):
                continue
            if part == "..":
                if not found:
                    return _outside()
                found.pop()
            else:
                found.append(part)
        if not found:
            return _not_found()
        names = [*below, *found]
        key = "/".join(names)
        if key in self.files:
            return Loaded(data=self.files[key])
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        held = [os.open(folder, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))]
        try:
            for name in names[:-1]:
                held.append(os.open(name, flags | getattr(os, "O_DIRECTORY", 0), dir_fd=held[-1]))
            fd = os.open(names[-1], flags | getattr(os, "O_NONBLOCK", 0), dir_fd=held[-1])
        except (FileNotFoundError, NotADirectoryError):
            return _not_found()
        except OSError as error:
            return _outside() if error.errno == errno.ELOOP else _unreadable()
        finally:
            for descriptor in held:
                os.close(descriptor)
        with os.fdopen(fd, "rb") as handle:
            size = os.fstat(fd)
            if not stat.S_ISREG(size.st_mode):
                return _not_found()
            if size.st_size > self.config.max_image_bytes or not self._room(size.st_size):
                return _too_large(self.config)
            data = handle.read(self.config.max_image_bytes + 1)
        if len(data) > self.config.max_image_bytes or not self._room(len(data)):
            return _too_large(self.config)
        self.files[key] = data
        self.total += len(data)
        return Loaded(data=data)

    def load(self, src: str | None) -> Loaded:
        """The image `src` names, as a page or Markdown file at `base` would see it."""
        src = (src or "").strip(_C0)
        if not src:
            return _not_found()
        if src in self.seen:
            return self.seen[src]
        loaded = self._load(src)
        if len(src) <= _LONG:
            self.seen[src] = loaded
        return loaded

    def _load(self, src: str) -> Loaded:
        scheme = _SCHEME.match(src)
        name = scheme.group(1).lower() if scheme and len(scheme.group(1)) > 1 else ""
        if name == "data":
            return self._data_uri(src)
        if src.startswith("//") or name in ("http", "https"):
            if len(src) > _LONG:
                loaded = _refused(
                    "image.remote-not-fetched",
                    "The image is on another site; it was not fetched, and its address is too long to keep.",
                )
                return loaded
            return _remote(src)
        if len(src) > _LONG:  # no real path is this long
            return _unreadable()
        if name or src.startswith(("/", "\\")):  # a single letter is a drive, not a scheme
            return _outside()
        return self._local(src)
