"""Readers for files that hold their text exactly (everything but PDF), and the registry
that finds the right one by looking at the content as well as the name.

Only the readers for JSON, CSV and XML live in the core install. The others import their
library when they run, never when this module loads."""

import importlib
import importlib.metadata
import io
import platform
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

from ..config import Config
from ..model import Document, Flag, Page
from . import _build

FORMATS = ("pdf", "docx", "xlsx", "pptx", "html", "md", "json", "xml", "csv")


class ReaderUnavailable(RuntimeError):
    """The library a reader needs is not installed."""


class ReaderRefused(Exception):
    """The reader will not read this file; `rule` says why, in a stable identifier."""

    def __init__(self, rule: str, message: str):
        super().__init__(message)
        self.rule = rule
        self.message = message


@dataclass
class ReadResult:
    document: Document
    images: dict[str, bytes] = field(default_factory=dict)  # keyed by figure id


def _installed(distribution: str) -> str | None:
    try:
        return importlib.metadata.version(distribution)
    except importlib.metadata.PackageNotFoundError:
        return None


def _versions() -> dict[str, str]:
    python = f"python {platform.python_version()}"
    found = {"json": python, "csv": python, "xml": f"defusedxml {_installed('defusedxml')}"}
    for name, distribution in (
        ("docx", "python-docx"),
        ("xlsx", "openpyxl"),
        ("pptx", "python-pptx"),
        ("html", "lxml"),
        ("md", "markdown-it-py"),
    ):
        version = _installed(distribution)
        if version:
            found[name] = f"{distribution} {version}"
    return found


READER_VERSIONS: dict[str, str] = _versions()

_MAIN_PARTS = {"docx": "word/document.xml", "xlsx": "xl/workbook.xml", "pptx": "ppt/presentation.xml"}
_BOMS = {b"\xef\xbb\xbf": "utf-8", b"\xff\xfe": "utf-16", b"\xfe\xff": "utf-16"}


def _head(data: bytes) -> str:
    """The start of a text file as text, for looking at; "" when it is not text."""
    sample = data[:4096]
    if b"\x00" in sample and not sample.startswith((b"\xff\xfe", b"\xfe\xff")):
        return ""
    for bom, encoding in _BOMS.items():
        if sample.startswith(bom):
            return sample.decode(encoding, errors="ignore").lstrip("﻿").lstrip()
    return sample.decode("utf-8", errors="ignore").lstrip()


def _blank(data: bytes) -> bool:
    """Nothing but ASCII whitespace, after at most one byte-order mark."""
    if data.startswith((b"\xff\xfe", b"\xfe\xff")):
        try:
            return not data.decode("utf-16").strip(" \t\r\n\x0b\x0c")
        except UnicodeDecodeError:
            return False
    return not data.removeprefix(b"\xef\xbb\xbf").strip(b" \t\r\n\x0b\x0c")


def detect(name: str, data: bytes) -> str | None:
    """One of `FORMATS`, or None. Content decides first; the extension picks among text formats."""
    if data[:1024].lstrip().startswith(b"%PDF-"):
        return "pdf"
    if data.startswith(b"PK\x03\x04"):
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as archive:
                names = set(archive.namelist())
        except zipfile.BadZipFile:
            return None
        if "[Content_Types].xml" not in names:
            return None
        suffix = Path(name).suffix.lower().lstrip(".")
        found = [fmt for fmt, part in _MAIN_PARTS.items() if part in names]
        return suffix if suffix in found else (found[0] if len(found) == 1 else None)
    head = _head(data)
    if not head:
        # A file with nothing in it is an empty document, if its name says what it would be.
        blank = _blank(data)
        suffix = Path(name).suffix.lower()
        if blank and suffix in (".html", ".htm", ".xhtml"):
            return "html"
        return "md" if blank and suffix in (".md", ".markdown") else None
    suffix = Path(name).suffix.lower()
    if suffix == ".json":
        return "json" if head[0] in "{[" else None
    if suffix == ".xml":
        return "xml" if head[0] == "<" else None
    if suffix in (".html", ".htm", ".xhtml"):
        return "html" if head[0] == "<" else None
    if head[:15].lower().startswith(("<!doctype html", "<html")):
        return "html"
    if suffix in (".md", ".markdown"):
        return "md"
    if suffix in (".csv", ".tsv"):
        return "csv"
    return None


def _module(fmt: str):
    try:
        return importlib.import_module(f"{__name__}.{fmt}_reader")
    except ModuleNotFoundError as error:
        if error.name == f"{__name__}.{fmt}_reader":
            raise ReaderUnavailable(f"There is no reader for {fmt} yet.") from error
        hint = "pip install 'ragnaroq[formats]'"
        raise ReaderUnavailable(f"The {fmt} reader needs {error.name}: {hint}") from error


def read_bytes(
    data: bytes,
    name: str,
    config: Config,
    *,
    engines=None,
    third_reader=None,
    base: Path | None = None,
) -> ReadResult:
    """Read one file. Raises `ReaderRefused` for a file that must not be read, and
    `ReaderUnavailable` when the format's library is missing."""
    fmt = detect(name, data)
    if fmt is None:
        raise ReaderRefused("format.unknown", f"{name} is not a format RAGnaroq reads.")
    if len(data) > config.max_bytes:
        raise ReaderRefused("file.too-large", f"{name} is larger than {config.max_bytes} bytes.")
    if fmt == "pdf":
        from ..pipeline import convert

        document = convert(data, config=config, engines=engines, filename=name, third_reader=third_reader)
        return ReadResult(document)
    reader = _module(fmt)
    extra = {"base": base} if fmt in ("html", "md") else {}
    result = reader.read(data, name, config, **extra)
    _build.finish(result.document, config)
    return result


def refused_document(name: str, data: bytes, error: ReaderRefused) -> Document:
    """What a refused file becomes: one body page, no lines, and a blocking flag saying why."""
    fmt = detect(name, data) or Path(name).suffix.lower().lstrip(".") or "unknown"
    document = _build.start(data, name, fmt, Config(), {})
    document.flags.append(Flag(rule=error.rule, severity="blocking", message=error.message))
    return document


__all__ = [
    "READER_VERSIONS",
    "Page",
    "ReadResult",
    "ReaderRefused",
    "ReaderUnavailable",
    "detect",
    "read_bytes",
    "refused_document",
]
