"""Markdown for a language model to read. Page breaks and review flags are HTML comments,
so they survive in the text without being mistaken for content."""

from ..anchors import cite
from ..model import Document, Page
from . import units


def marker(page: Page) -> str:
    match page.kind:
        case "sheet":
            return f"<!-- sheet {page.name} -->"
        case "slide":
            return f"<!-- slide {page.number} -->"
        case "body":
            return f"<!-- body {page.name} -->" if page.name else "<!-- body -->"
    return f"<!-- page {page.number} -->"


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
        figure = unit.figure
        text = f"![{figure.description}]({figure.path}) *({cite(figure.anchor)})*" if figure else unit.text
        if not text:
            continue
        out.append(f"## {text}" if unit.kind == "heading" else text)
        if unit.status != "accepted":
            out.append(f"<!-- {unit.status}: {', '.join(unit.rules)} -->")
    missing = [p.number for p in document.pages if p.kind in {"failed", "scanned"} and not p.lines]
    if missing:
        out.append(f"<!-- no text for pages: {', '.join(map(str, missing))} -->")
    return "\n\n".join(out) + "\n"
