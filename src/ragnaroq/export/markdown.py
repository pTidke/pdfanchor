"""Markdown for a language model to read. Page breaks and review flags are HTML comments,
so they survive in the text without being mistaken for content."""

import re

from ..anchors import cite
from ..model import Document, Figure, Page
from . import table_markdown, units

_REMOTE = re.compile(r"^(?:https?:|//)", re.IGNORECASE)
_SAFE_PATH = re.compile(r"images/[a-z0-9][a-z0-9-]*\.[a-z0-9]+")
_MARKUP = str.maketrans({ch: "\\" + ch for ch in "\\[]<>&"})
_URL = str.maketrans({ch: f"%{ord(ch):02X}" for ch in " ()<>\\\r\n\t"})


def escape(text: str) -> str:
    """Words from a file, written so that no file can make markup: a backslash before each
    character that opens a link, an image or a tag."""
    return text.translate(_MARKUP)


def _one_line(text: str) -> str:
    return escape(" ".join(text.split()))


def _comment(text: str) -> str:
    """Text inside an HTML comment, which backslashes do not protect: nothing in it can end it."""
    return " ".join(text.split()).replace("<", "&lt;").replace(">", "&gt;")


def _destination(url: str) -> str:
    return "<" + url.translate(_URL) + ">"


def marker(page: Page) -> str:
    match page.kind:
        case "sheet":
            return f"<!-- sheet {_comment(page.name or '')} -->"
        case "slide":
            return f"<!-- slide {page.number} -->"
        case "body":
            return f"<!-- body {_comment(page.name)} -->" if page.name else "<!-- body -->"
    return f"<!-- page {page.number} -->"


def _figure(figure: Figure) -> str:
    """A saved image is embedded. One with no file is text, and a remote one a plain link, so
    no viewer fetches an address a file only named. Everything the file wrote is escaped."""
    place = f"*({_one_line(cite(figure.anchor))})*"
    description = _one_line(figure.description)
    path = figure.path
    label = f" — model: {_one_line(figure.model_description)}" if figure.model_description else ""
    if path and not _REMOTE.match(path):
        return (
            f"![{description}]({path if _SAFE_PATH.fullmatch(path) else _destination(path)}){label} {place}"
        )
    link = f"({_destination(path)})" if path and _REMOTE.match(path) else ""
    return f"[image: {description}]{link}{label} {place}"


def _text(unit) -> str:
    if unit.figure:
        return _figure(unit.figure)
    if unit.table:
        return table_markdown(unit.table, escape)
    return " ".join(escape(line.text.strip()) for line in unit.lines if line.text.strip())


def to_markdown(document: Document, include: tuple[str, ...] = ("accepted", "flagged")) -> str:
    out: list[str] = []
    page = 0
    parts = {p.number: p for p in document.pages}
    for unit in units(document):
        if unit.page != page:
            page = unit.page
            out.append(marker(parts[page]))
        if unit.status not in include:
            out.append(f"<!-- omitted ({unit.status}): {', '.join(unit.rules)} -->")
            continue
        text = _text(unit)
        if not text:
            continue
        out.append(f"## {text}" if unit.kind == "heading" else text)
        if unit.status != "accepted":
            out.append(f"<!-- {unit.status}: {', '.join(unit.rules)} -->")
    missing = [p.number for p in document.pages if p.kind in {"failed", "scanned"} and not p.lines]
    if missing:
        out.append(f"<!-- no text for pages: {', '.join(map(str, missing))} -->")
    return "\n\n".join(out) + "\n"
