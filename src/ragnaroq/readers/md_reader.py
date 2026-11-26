"""Markdown (CommonMark and pipe tables). One body page. Every block that holds text is a line
anchored at the source lines it spans, 1-based and inclusive; a table cell is anchored at its
row's lines. Raw HTML is read as its visible text and flagged. Images are figures; none is
fetched, and none leaves the file's folder."""

import html
import re
from dataclasses import dataclass, field
from pathlib import Path

from ..anchors import MdAnchor
from ..config import Config
from ..model import Cell, Figure, Flag, Table
from . import READER_VERSIONS, ReaderRefused, ReaderUnavailable, ReadResult, _build, _images
from .html_reader import DROPPED, HIDDEN_MESSAGE, HIDDEN_STYLE, collapse, fragment

_NEWLINE = re.compile(r"\r\n?|\n")
_TAG = re.compile(r"<(/?)([A-Za-z][A-Za-z0-9:-]*)((?:[^>\"']|\"[^\"]*\"|'[^']*')*)>")
_ATTRIBUTE = re.compile(r"([^\s=/\"'>]+)(?:\s*=\s*(?:\"([^\"]*)\"|'([^']*)'|([^\s\"'>]+)))?")
_COMMENT = re.compile(r"<!--.*?(?:-->|$)", re.S)
_CODE = re.compile(r"<(script|style)\b.*?</\1\s*>", re.S | re.I)
_WORDS = ("text", "text_special", "code_inline")
_VOID = frozenset("area base br col embed hr img input link meta param source track wbr".split())


def _attributes(text: str) -> dict[str, str]:
    found = {}
    for name, double, single, bare in _ATTRIBUTE.findall(text):
        found.setdefault(name.lower(), html.unescape(double or single or bare))
    return found


class _Tags:
    """The raw-HTML tags left open so far in the file, across blocks: text between `<div hidden>`
    and `</div>` is hidden, and text inside an open `<template>` is not read, though the tags
    sit in other blocks than the text."""

    def __init__(self):
        self.open: list[tuple[str, bool, bool]] = []  # tag name, hides, drops

    @property
    def hidden(self) -> bool:
        return any(hides for _, hides, _ in self.open)

    @property
    def dropped(self) -> bool:
        return any(drops for _, _, drops in self.open)

    def feed(self, content: str):
        """Apply every tag in `content`; yields (name, attributes) of each start tag."""
        content = _CODE.sub("", _COMMENT.sub("", content))
        for closing, name, rest in _TAG.findall(content):
            name = name.lower()
            if closing:
                names = [entry[0] for entry in self.open]
                if name in names:
                    del self.open[len(names) - 1 - names[::-1].index(name) :]
                continue
            attributes = _attributes(rest)
            if name not in _VOID and not rest.rstrip().endswith("/"):
                hides = "hidden" in attributes or bool(HIDDEN_STYLE.search(attributes.get("style", "")))
                self.open.append((name, hides, name in DROPPED))
            yield name, attributes


@dataclass
class _Table:
    number: int
    row: int = 0
    column: int = 0
    columns: int = 0
    cells: list[Cell] = field(default_factory=list)

    @property
    def id(self) -> str:
        return f"p1-t{self.number}"


class _Reader:
    def __init__(self, document, config: Config, base: Path | None):
        self.document = document
        self.page = document.pages[0]
        self.config = config
        self.base = base
        self.images: dict[str, bytes] = {}
        self.loaded: dict[str, _images.Loaded] = {}
        self.tags = _Tags()
        self.stopped = False
        self.nested_too_deep = False
        self.tables = 0

    def full(self) -> bool:
        if self.stopped:
            return True
        if len(self.page.lines) < self.config.max_cells and len(self.page.figures) < self.config.max_cells:
            return False
        self.stopped = True
        self.page.flags.append(
            Flag(
                rule="md.line-limit",
                severity="review",
                message=f"More than {self.config.max_cells} lines; the rest is not read.",
            )
        )
        return True

    def line(self, text, lines, role="body", table_id=None, flags=(), hidden=False) -> bool:
        if not text.strip() or self.full():
            return False
        line = _build.add_line(self.page, text, "md", MdAnchor(lines=lines), role=role, table_id=table_id)
        for flag in flags:
            line.flags.append(flag.model_copy())
        if hidden:
            line.flags.append(Flag(rule="html.hidden", severity="review", message=HIDDEN_MESSAGE))
        return True

    def figure(self, src, alt, lines, hidden):
        if self.full():
            return
        src = (src or "").strip()
        key = src if len(src) < 4096 else None
        loaded = self.loaded.get(key) if key else None
        if loaded is None:
            loaded = _images.load(src, self.base, self.config)
            if key:
                self.loaded[key] = loaded
        alt = collapse(alt)
        flags = [loaded.flag] if loaded.flag else []
        if hidden or self.tags.hidden:
            flags.append(Flag(rule="html.hidden", severity="review", message=HIDDEN_MESSAGE))
        figure = Figure(
            id=f"p1-f{len(self.page.figures) + 1}",
            page=1,
            order=len(self.page.lines),
            anchor=MdAnchor(lines=lines),
            path=loaded.path,
            description=alt,
            description_source="alt" if alt else "none",
            flags=[flag.model_copy() for flag in flags],
        )
        if loaded.data is not None:
            self.images[figure.id] = loaded.data
        self.page.figures.append(figure)

    # -- inline content ----------------------------------------------------------------------

    def inline(self, token, lines, role="body", table_id=None) -> str:
        """The block's text from its inline children, and its images and hidden words; makes
        the line, then the figures. Returns the text."""
        out = [""]  # one running string; a space that would follow a space is dropped
        images: list[tuple] = []
        hidden = self.tags.hidden

        def add(piece):
            out[0] += piece.lstrip(" ") if out[0].endswith(" ") else piece

        for child in token.children or []:
            kind = child.type
            if kind == "html_inline":
                for name, attributes in self.tags.feed(child.content):
                    if name == "br" and not self.tags.dropped:
                        add(" ")
                    elif name == "img" and not self.tags.dropped:
                        images.append((attributes.get("src"), attributes.get("alt", ""), self.tags.hidden))
                continue
            if self.tags.dropped:
                continue
            if kind == "image":
                alt = "".join(c.content for c in child.children or [] if c.type in _WORDS)
                images.append((child.attrGet("src"), alt, self.tags.hidden))
            elif kind in _WORDS:
                add(child.content)
                hidden = hidden or (self.tags.hidden and bool(child.content.strip()))
            elif kind in ("softbreak", "hardbreak"):
                add(" ")
        text = out[0].strip()
        self.line(text, lines, role, table_id, hidden=hidden)
        for src, alt, hid in images:
            self.figure(src, alt, lines, hid)
        return text

    # -- raw HTML ----------------------------------------------------------------------------

    def raw_html(self, token, lines):
        content = token.content
        if not self.tags.dropped:
            try:
                reader = fragment(content, self.config, self.base, hidden=self.tags.hidden)
            except ModuleNotFoundError as error:
                raise ReaderUnavailable(
                    "Raw HTML in Markdown needs lxml: pip install 'ragnaroq[formats]'"
                ) from error
            text = collapse(" ".join(line.text for line in reader.page.lines))
            seen = {flag.rule: flag for line in reader.page.lines for flag in line.flags}
            flags = [
                Flag(rule="md.raw-html", severity="review", message="Raw HTML; read as its visible text.")
            ]
            flags += [seen[rule] for rule in ("html.equation",) if rule in seen]
            self.line(text, lines, flags=flags, hidden="html.hidden" in seen)
            for flag in reader.page.flags:
                if all(flag.rule != existing.rule for existing in self.page.flags):
                    self.page.flags.append(flag)
            for found in reader.page.figures:
                data = reader.images.get(found.id)
                self.figure_from(found, data, lines)
        for _ in self.tags.feed(content):
            pass

    def figure_from(self, found: Figure, data, lines):
        if self.full():
            return
        figure = found.model_copy(
            update={
                "id": f"p1-f{len(self.page.figures) + 1}",
                "order": len(self.page.lines),
                "anchor": MdAnchor(lines=lines),
            }
        )
        if data is not None:
            self.images[figure.id] = data
        self.page.figures.append(figure)

    # -- blocks ------------------------------------------------------------------------------

    def front_matter(self, rows: list[str]) -> int:
        """Lines 2 to the closing line are lines of their own; returns how many source lines
        they and the fences take, 0 when the file has no front matter."""
        if not rows or rows[0] != "---":
            return 0
        end = next((i for i in range(1, len(rows)) if rows[i] in ("---", "...")), None)
        if end is None:
            return 0
        for number in range(2, end + 1):
            self.line(rows[number - 1].strip(), (number, number))
        return end + 1

    def run(self, source: str, markdown):
        rows = _NEWLINE.split(source)
        taken = self.front_matter(rows)
        if taken:  # blank, so the numbers of the lines after it stay the file's own
            source = "\n".join([""] * taken + rows[taken:])
        tokens = markdown.parse(source)
        table = None
        role, row_lines, block_lines = "body", None, None
        for token in tokens:
            if self.stopped:
                break
            kind = token.type
            lines = (token.map[0] + 1, token.map[1]) if token.map else None
            if kind == "heading_open":
                role, block_lines = "heading", lines
            elif kind == "paragraph_open":
                block_lines = lines
            elif kind in ("heading_close", "paragraph_close"):
                role = "body"
            elif kind == "table_open":
                self.tables += 1
                table = _Table(self.tables)
            elif kind == "tr_open" and table:
                table.row += 1
                table.column = 0
                row_lines = lines
            elif kind in ("th_open", "td_open") and table:
                table.column += 1
            elif kind == "inline":
                if table:
                    text = self.inline(token, row_lines, "table", table.id)
                    table.cells.append(
                        Cell(
                            row=table.row - 1,
                            column=table.column - 1,
                            text=text,
                            anchor=MdAnchor(lines=row_lines),
                        )
                    )
                    table.columns = max(table.columns, table.column)
                else:
                    self.inline(token, block_lines, role)
            elif kind == "table_close" and table:
                if table.cells:
                    self.page.tables.append(
                        Table(id=table.id, page=1, rows=table.row, columns=table.columns, cells=table.cells)
                    )
                table = None
            elif kind in ("fence", "code_block"):
                if not self.tags.dropped:
                    self.line(token.content.removesuffix("\n"), lines, hidden=self.tags.hidden)
            elif kind == "html_block":
                self.raw_html(token, lines)
        if table and table.cells:  # stopped inside a table
            self.page.tables.append(
                Table(id=table.id, page=1, rows=table.row, columns=table.columns, cells=table.cells)
            )


def read(data: bytes, name: str, config: Config, base: Path | None = None) -> ReadResult:
    try:
        from markdown_it import MarkdownIt
    except ModuleNotFoundError as error:
        raise ReaderUnavailable(
            "The md reader needs markdown-it-py: pip install 'ragnaroq[formats]'"
        ) from error
    try:
        text, guessed = _build.decode(data)
    except UnicodeDecodeError as error:
        raise ReaderRefused("md.invalid", f"{name} is not valid UTF-16 text: {error}") from error
    document = _build.start(data, name, "md", config, {"reader:md": READER_VERSIONS["md"]})
    if guessed:
        document.flags.append(
            Flag(rule="encoding.guessed", severity="review", message="Not UTF-8; read as Windows-1252.")
        )
    reader = _Reader(document, config, base)
    markdown = MarkdownIt("commonmark").enable("table")
    markdown.validateLink = lambda url: True  # a path that is refused later is a flagged figure, not text
    plain_tokenize = markdown.block.tokenize

    def tokenize(state, start, end):
        line = state.skipEmptyLines(start)
        if (
            state.level >= state.md.options.maxNesting
            and line < end
            and state.sCount[line] >= state.blkIndent
        ):
            reader.nested_too_deep = True  # markdown-it drops the rest at this depth
        return plain_tokenize(state, start, end)

    markdown.block.tokenize = tokenize
    reader.run(text, markdown)
    if reader.nested_too_deep:
        document.pages[0].flags.append(
            Flag(
                rule="md.too-deep",
                severity="review",
                message=f"Nested more than {markdown.options.maxNesting} levels; what is below is not read.",
            )
        )
    return ReadResult(document, reader.images)
