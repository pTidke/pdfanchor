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
from .html_reader import HIDDEN_MESSAGE, HIDDEN_STYLE, collapse, fragment

MAX_NESTING = 100  # markdown-it's own is 20 for CommonMark
_NEWLINE = re.compile(r"\r\n?|\n")
_TAG = re.compile(r"<(/?)([A-Za-z][A-Za-z0-9:-]*)(?=[\s/>])((?:[^<>\"']|\"[^\"<]*\"|'[^'<]*')*)>")
_ATTRIBUTE = re.compile(r"([^\s=/\"'>]+)(?:\s*=\s*(?:\"([^\"]*)\"|'([^']*)'|([^\s\"'>]+)))?")
_COMMENT = re.compile(r"<!--.*?(?:-->|$)", re.S)
_NAME = re.compile(r"<(/?)([A-Za-z][A-Za-z0-9:-]*)")
_HIDING = ("template", "noscript")  # left open in a block, they hide the blocks after it
_WORDS = ("text", "text_special", "code_inline")
# Raw-HTML elements whose content is code or markup, not text, when the tag is closed in the same block.
_CODE = ("script", "style", "template", "noscript")
_VOID = frozenset("area base br col embed hr img input link meta param source track wbr".split())
_RAW_HTML = Flag(rule="md.raw-html", severity="review", message="Raw HTML; read as its visible text.")


def _tag_attributes(tag: str) -> dict[str, str]:
    """The attributes of one start tag, as a browser reads them."""
    try:
        import lxml.html
        from lxml import etree

        parent = lxml.html.fragment_fromstring(tag, create_parent="div")
        return {name.lower(): value for name, value in (parent[0].attrib.items() if len(parent) else [])}
    except (ModuleNotFoundError, etree.LxmlError):
        match = _NAME.match(tag)
        return _attributes(tag[match.end() :]) if match else {}


def _attributes(text: str) -> dict[str, str]:
    found = {}
    for name, double, single, bare in _ATTRIBUTE.findall(text):
        found.setdefault(name.lower(), html.unescape(double or single or bare))
    return found


class _Tags:
    """The raw-HTML tags left open so far in the file, across blocks: text between `<div hidden>`
    and `</div>` is hidden though the tags sit in other blocks than the text. Never more than
    `limit` are kept, and every step is constant time."""

    def __init__(self, limit: int):
        self.limit = limit
        self.open: list[tuple[str, bool]] = []  # tag name, whether it hides
        self.counts: dict[str, int] = {}
        self.hiding = 0
        self.overflow = False

    @property
    def hidden(self) -> bool:
        return self.hiding > 0

    def close(self, name: str) -> None:
        if not self.counts.get(name):
            return
        while self.open:
            last, hides = self.open.pop()
            self.counts[last] -= 1
            self.hiding -= hides
            if last == name:
                break

    def apply(self, closing: str, name: str, attributes: dict, selfclosed: bool, hides: bool) -> None:
        if closing:
            self.close(name)
            return
        if name in _VOID or selfclosed:
            return
        hides = hides or "hidden" in attributes or bool(HIDDEN_STYLE.search(attributes.get("style", "")))
        if len(self.open) >= self.limit:
            self.overflow = True
            return
        self.open.append((name, hides))
        self.counts[name] = self.counts.get(name, 0) + 1
        self.hiding += hides

    def feed(self, content: str):
        """Apply every tag in a raw-HTML block; yields (name, attributes) of each start tag. A
        script or style closed in the same content is skipped whole, so its code is not read as
        tags. A template or noscript left open hides what follows it."""
        content = _COMMENT.sub("", content)
        position, unclosed = 0, set()
        while match := _TAG.search(content, position):
            position = match.end()
            closing, name, rest = match.groups()
            name = name.lower()
            if name in ("script", "style") and not closing and name not in unclosed:
                end = re.compile(rf"</{name}\s*>", re.I).search(content, position)
                if end:
                    position = end.end()
                    continue
                unclosed.add(name)
            attributes = {} if closing else _attributes(rest)
            self.apply(closing, name, attributes, rest.rstrip().endswith("/"), name in _HIDING)
            if not closing:
                yield name, attributes

    def feed_inline(self, content: str):
        """The one tag of an inline raw-HTML token. Its attributes are parsed by lxml, which
        copes with a `<` inside a quoted value; the block scanner does not."""
        match = _NAME.match(content)
        if not match:
            return
        closing, name = match.group(1), match.group(2).lower()
        attributes = {} if closing or "=" not in content else _tag_attributes(content)
        self.apply(closing, name, attributes, content.rstrip().endswith("/>"), False)
        if not closing:
            yield name, attributes


def _closes(content: str, name: str) -> bool:
    match = _NAME.match(content)
    return bool(match and match.group(1) and match.group(2).lower() == name)


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
        self.loader = _images.Images(base, config)
        self.tags = _Tags(config.max_depth)
        self.stopped = False
        self.nested_too_deep = False
        self.tables = 0

    def full(self) -> bool:
        if self.stopped:
            return True
        if len(self.page.lines) + len(self.page.figures) < self.config.max_cells:
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
        loaded = self.loader.load(src)
        alt = _build.alt_text(collapse(alt))
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
        out: list[str] = []
        images: list[tuple] = []
        hidden = self.tags.hidden
        raw = False
        children = token.children or []
        unclosed: set[str] = set()
        spaced = True  # the text so far ends in a space, or is empty

        def add(piece):
            nonlocal spaced
            if spaced:
                piece = piece.lstrip(" ")
            if piece:
                out.append(piece)
                spaced = piece.endswith(" ")

        index = 0
        while index < len(children):
            child = children[index]
            index += 1
            kind = child.type
            if kind == "html_inline":
                start = _NAME.match(child.content)
                name = start.group(2).lower() if start and not start.group(1) else ""
                if name in _CODE:
                    raw = True  # code inside the tag is not text, if the tag is closed in this block
                    if name not in unclosed:
                        end = next(
                            (
                                k
                                for k in range(index, len(children))
                                if children[k].type == "html_inline" and _closes(children[k].content, name)
                            ),
                            None,
                        )
                        if end is not None:
                            index = end + 1
                            continue
                        unclosed.add(name)
                for name, attributes in self.tags.feed_inline(child.content):
                    if name == "br":
                        add(" ")
                    elif name == "img":
                        images.append((attributes.get("src"), attributes.get("alt", ""), self.tags.hidden))
                continue
            if kind == "image":
                alt = "".join(c.content for c in child.children or [] if c.type in _WORDS)
                images.append((child.attrGet("src"), alt, self.tags.hidden))
            elif kind in _WORDS:
                add(child.content)
                hidden = hidden or (self.tags.hidden and bool(child.content.strip()))
            elif kind in ("softbreak", "hardbreak"):
                add(" ")
        text = "".join(out).strip()
        self.line(text, lines, role, table_id, flags=[_RAW_HTML] if raw else (), hidden=hidden)
        for src, alt, hid in images:
            self.figure(src, alt, lines, hid)
        return text

    # -- raw HTML ----------------------------------------------------------------------------

    def raw_html(self, token, lines):
        content = token.content
        try:
            reader = fragment(content, self.config, self.base, self.tags.hidden, self.loader)
        except ModuleNotFoundError as error:
            raise ReaderUnavailable(
                "Raw HTML in Markdown needs lxml: pip install 'ragnaroq[formats]'"
            ) from error
        text = collapse(" ".join(line.text for line in reader.page.lines))
        seen = {flag.rule: flag for line in reader.page.lines for flag in line.flags}
        flags = [_RAW_HTML] + [seen[rule] for rule in ("html.equation",) if rule in seen]
        self.line(text, lines, flags=flags, hidden="html.hidden" in seen)
        for flag in reader.page.flags:
            if all(flag.rule != existing.rule for existing in self.page.flags):
                self.page.flags.append(flag)
        for found in reader.page.figures:
            self.figure_from(found, reader.images.get(found.id), lines)
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
                    if text:  # an empty cell is left out, as in Word and HTML
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
    markdown = MarkdownIt("commonmark", {"maxNesting": MAX_NESTING}).enable("table")
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
                message=(
                    f"Nested more than {MAX_NESTING} levels; the rest of the file from that point "
                    "is not read."
                ),
            )
        )
    if reader.tags.overflow:
        document.pages[0].flags.append(
            Flag(
                rule="md.tag-limit",
                severity="review",
                message=f"More than {config.max_depth} raw HTML tags are left open; hidden text past "
                "them may not be flagged.",
            )
        )
    return ReadResult(document, reader.images)
