"""HTML. One body page. Every element that holds text of its own is a line anchored at its path
(`lxml`'s `getpath` for the file as parsed); inline elements belong to the nearest block above
them, and a nested block is a line of its own after it. Scripts, styles, templates, comments and
the head are not read, except the title, which names the page. Text a visitor never sees is
read and flagged. Images are figures; none is fetched, and none leaves the file's folder."""

import codecs
import collections
import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from ..anchors import HtmlAnchor
from ..config import Config
from ..model import Cell, Figure, Flag, Page, Table
from . import READER_VERSIONS, ReaderRefused, ReaderUnavailable, ReadResult, _build, _images

# Not read: their text is code or markup, not the page's.
DROPPED = frozenset({"script", "style", "template", "noscript", "head"})
# Inline elements belong to their nearest non-inline ancestor. The list is the phrasing elements
# of HTML that hold words; `img`, `br` and `math` are handled by name.
_INLINE = frozenset(
    "a abbr b bdi bdo cite code data del dfn em font i ins kbd label mark q s samp small span strong"
    " sub sup time u var big tt strike nobr ruby wbr acronym output meter progress picture".split()
)
_HEADINGS = frozenset({"h1", "h2", "h3", "h4", "h5", "h6"})
_ANNOTATIONS = frozenset({"rt", "rp"})  # a reading aid, not the text
_MATH_SKIP = frozenset({"annotation", "annotation-xml"})  # other encodings of the same formula
_WS = re.compile(r"[ \t\n\r\f]+")
HIDDEN_STYLE = re.compile(r"(?:^|;)\s*(?:display\s*:\s*none|visibility\s*:\s*hidden)\b", re.I)
_META_CHARSET = re.compile(rb"<meta\b[^>]*?\bcharset\s*=\s*[\"']?\s*([A-Za-z0-9_.:\-]+)", re.I)
_COMMENT = re.compile(rb"<!--.*?(?:-->|$)", re.S)
_UTF8_BOM = b"\xef\xbb\xbf"
_END_TAGS = re.compile(rb"</(?:body|html)\s*>", re.I)
_LAST_TAGS = (re.compile(rb"</html\s*>", re.I), re.compile(rb"</body\s*>", re.I))
_MAX_COLSPAN, _MAX_ROWSPAN = 1000, 65534  # HTML's own limits; columns past 1000 are not tracked

HIDDEN_MESSAGE = "Not shown on the page; a reader of the page never sees it."
EQUATION_MESSAGE = (
    "An equation is read as its plain symbols; fractions, powers and layout are lost; compare with the page."
)


def is_hidden(element) -> bool:
    """Marked as not shown: the `hidden` attribute, or an inline style that hides it."""
    return "hidden" in element.attrib or bool(HIDDEN_STYLE.search(element.get("style") or ""))


def collapse(text: str) -> str:
    """Runs of ASCII whitespace become one space and the ends are trimmed. A no-break space is
    text, not spacing, and stays."""
    return _WS.sub(" ", text).strip(" ")


def _texts(element, skip, inline):
    """The text of `element` in order, without the subtrees named in `skip`. A child not in
    `inline` is set off by spaces. Iterative, so depth is not limited by Python's stack."""
    if element.text:
        yield element.text
    stack = [iter(element)]
    owners = [element]
    while stack:
        child = next(stack[-1], None)
        if child is None:
            stack.pop()
            owner = owners.pop()
            if stack:
                if owner.tag not in inline:
                    yield " "
                if owner.tail:
                    yield owner.tail
            continue
        tag = child.tag
        if not isinstance(tag, str) or tag in skip:
            if child.tail:
                yield child.tail
            continue
        if tag not in inline:
            yield " "
        if child.text:
            yield child.text
        stack.append(iter(child))
        owners.append(child)


def _math_text(math) -> str:
    """A formula's symbols, the text of its elements joined by spaces."""
    return collapse("".join(_texts(math, _MATH_SKIP | DROPPED, frozenset())))


def _caption(figure) -> str:
    node = next((child for child in figure if child.tag == "figcaption"), None)
    return collapse("".join(_texts(node, DROPPED, _INLINE))) if node is not None else ""


def _span(value, cap: int) -> int:
    try:
        return min(max(int(value), 1), cap)
    except (TypeError, ValueError):
        return 1


@dataclass
class _Table:
    number: int
    row: int = 0
    column: int = 0  # the next free column, 0-based
    rows: int = 0
    columns: int = 0
    busy: dict[int, int] = field(default_factory=dict)  # column -> last row a rowspan holds it
    cells: list[Cell] = field(default_factory=list)

    @property
    def id(self) -> str:
        return f"p1-t{self.number}"


@dataclass
class _Cell:
    table: _Table
    row: int
    column: int
    anchor: HtmlAnchor
    parts: list[str] = field(default_factory=list)


@dataclass
class _Frame:
    items: object  # an iterator over what the block holds, in order
    closer: object  # the table or cell to finish when the block ends
    pre: bool
    caption: str
    element: object  # the block, whose own text the "text" items are
    path: str
    hidden: bool


class Reader:
    def __init__(self, page: Page, config: Config, base: Path | None, loader=None):
        self.page = page
        self.config = config
        self.loader = loader or _images.Images(base, config)
        self.images: dict[str, bytes] = {}
        self.iframe = False
        self.stopped = False
        self.too_deep = False
        self.tables: list[_Table] = []
        self.cells: list[_Cell] = []
        self.table_count = 0

    # -- lines -------------------------------------------------------------------------------

    def full(self) -> bool:
        if self.stopped:
            return True
        if len(self.page.lines) + len(self.page.figures) < self.config.max_cells:
            return False
        self.stopped = True
        self.page.flags.append(
            Flag(
                rule="html.line-limit",
                severity="review",
                message=f"More than {self.config.max_cells} lines; the rest is not read.",
            )
        )
        return True

    def active_cell(self) -> _Cell | None:
        """The open cell whose table is the innermost: lines of a nested table's caption are not its."""
        if self.cells and self.tables and self.cells[-1].table is self.tables[-1]:
            return self.cells[-1]
        return None

    def line(self, element, path, text, hidden, equation) -> bool:
        if self.full():
            return False
        cell = self.active_cell()
        role = "table" if cell else ("heading" if element.tag in _HEADINGS else "body")
        line = _build.add_line(
            self.page,
            text,
            "html",
            HtmlAnchor(path=path),
            role=role,
            table_id=cell.table.id if cell else None,
        )
        if hidden:
            line.flags.append(Flag(rule="html.hidden", severity="review", message=HIDDEN_MESSAGE))
        if equation:
            line.flags.append(Flag(rule="html.equation", severity="review", message=EQUATION_MESSAGE))
        if cell:
            cell.parts.append(text)
        return True

    # -- figures -----------------------------------------------------------------------------

    def figure(self, element, path, hidden, caption):
        if self.full():
            return
        loaded = self.loader.load(element.get("src"))
        alt = collapse(element.get("alt") or "")
        description, source = (caption, "caption") if caption else (alt, "alt") if alt else ("", "none")
        flags = [loaded.flag] if loaded.flag else []
        if hidden:
            flags.append(Flag(rule="html.hidden", severity="review", message=HIDDEN_MESSAGE))
        figure = Figure(
            id=f"p1-f{len(self.page.figures) + 1}",
            page=1,
            order=len(self.page.lines),
            anchor=HtmlAnchor(path=path),
            path=loaded.path,
            description=description,
            description_source=source,
            flags=[flag.model_copy() for flag in flags],
        )
        if loaded.data is not None:
            self.images[figure.id] = loaded.data
        self.page.figures.append(figure)

    # -- walking -----------------------------------------------------------------------------

    def scan(self, element, path, depth, hidden, pre):
        """What `element` holds itself, in document order: each run of its own text (inline
        elements read in place) as a ("text", ...) item, then the images in it, with each
        nested block as a ("block", ...) item between the runs."""
        parts: list[str] = []
        items: list[tuple] = []
        pending: list[tuple] = []  # images in the run, which follow its line
        state = {"covert": False, "equation": False}

        def add(text, hid):
            if text:
                parts.append(text)
                if hid and not hidden and text.strip():
                    state["covert"] = True  # hidden words inside a visible element

        def flush():
            text = "".join(parts)
            if not pre:
                text = collapse(text)
            elif text.endswith("\n"):
                text = text[:-1]
            if text.strip():
                items.append(("text", text, state["equation"], state["covert"]))
            items.extend(pending)
            parts.clear()
            pending.clear()
            state["covert"] = state["equation"] = False

        def level(el, el_path, hid):
            return [iter(el), el, el_path, Counter(c.tag for c in el), {}, hid]

        add(element.text, hidden)
        stack = [level(element, path, hidden)]
        while stack:
            top = stack[-1]
            child = next(top[0], None)
            if child is None:
                stack.pop()
                if stack:
                    add(top[1].tail, stack[-1][5])
                continue
            tag = child.tag
            if not isinstance(tag, str):  # a comment or instruction that survived parsing
                add(child.tail, top[5])
                continue
            seen = top[4]
            seen[tag] = number = seen.get(tag, 0) + 1
            child_path = f"{top[2]}/{tag}" + (f"[{number}]" if top[3][tag] > 1 else "")
            if tag in DROPPED or tag in _ANNOTATIONS:
                add(child.tail, top[5])
                continue
            child_depth = depth + len(stack)
            if child_depth > self.config.max_depth:
                self.too_deep = True
                add(child.tail, top[5])
                continue
            child_hidden = top[5] or is_hidden(child)
            if tag == "iframe" and child.get("srcdoc") is not None:
                self.iframe = True
            if tag == "img":
                pending.append(("img", child, child_path, child_hidden))
            elif tag == "br":
                add("\n" if pre else " ", top[5])
            elif tag == "math":
                text = _math_text(child)
                if text:
                    add(f" {text} ", child_hidden)
                    state["equation"] = True
            elif tag in _INLINE:
                add(child.text, child_hidden)
                stack.append(level(child, child_path, child_hidden))
                continue
            else:
                flush()
                items.append(("block", child, child_path, child_depth, child_hidden))
            add(child.tail, top[5])
        flush()
        return items

    def enter(self, element, path, depth, hidden, pre, caption, stack):
        tag = element.tag
        hidden = hidden or is_hidden(element)
        pre = pre or tag == "pre"
        if tag == "figure":
            caption = _caption(element)
        closer = None
        if tag == "table":
            self.table_count += 1
            closer = _Table(number=self.table_count)
            self.tables.append(closer)
        elif tag == "tr" and self.tables:
            table = self.tables[-1]
            table.row += 1
            table.rows = table.row
            table.column = 0
        elif tag in ("td", "th") and self.tables:
            closer = self.open_cell(element, path)
        items = self.scan(element, path, depth, hidden, pre)
        stack.append(_Frame(iter(items), closer, pre, caption, element, path, hidden))

    def open_cell(self, element, path) -> _Cell:
        table = self.tables[-1]
        if table.row == 0:  # a cell with no row around it
            table.row = table.rows = 1
        column = table.column
        while table.busy.get(column, 0) >= table.row:
            column += 1
        columns = _span(element.get("colspan"), _MAX_COLSPAN)
        rows = _span(element.get("rowspan"), _MAX_ROWSPAN)
        if rows > 1:
            for spanned in range(column, min(column + columns, _MAX_COLSPAN)):
                table.busy[spanned] = table.row + rows - 1
        table.column = column + columns
        table.columns = max(table.columns, table.column)
        cell = _Cell(table, table.row - 1, column, HtmlAnchor(path=path))
        self.cells.append(cell)
        return cell

    def close(self, closer):
        if isinstance(closer, _Cell):
            self.cells.pop()
            text = "\n".join(closer.parts)
            if text:
                closer.table.cells.append(
                    Cell(row=closer.row, column=closer.column, text=text, anchor=closer.anchor)
                )
        elif isinstance(closer, _Table):
            self.tables.pop()
            if closer.cells:
                self.page.tables.append(
                    Table(id=closer.id, page=1, rows=closer.rows, columns=closer.columns, cells=closer.cells)
                )

    def run(self, root, hidden=False):
        stack: list[_Frame] = []
        self.enter(root, "/" + root.tag, 1, hidden, False, "", stack)
        while stack and not self.stopped:
            frame = stack[-1]
            item = next(frame.items, None)
            if item is None:
                stack.pop()
                self.close(frame.closer)
            elif item[0] == "text":
                self.line(frame.element, frame.path, item[1], frame.hidden or item[3], item[2])
            elif item[0] == "img":
                self.figure(item[1], item[2], item[3], frame.caption)
            else:
                self.enter(item[1], item[2], item[3], item[4], frame.pre, frame.caption, stack)
        while stack:  # stopped at the line limit: what is open is still finished
            self.close(stack.pop().closer)
        self.page.tables.sort(key=lambda table: int(table.id.rsplit("-t", 1)[1]))
        if self.iframe:
            self.page.flags.append(
                Flag(
                    rule="html.iframe-not-read",
                    severity="review",
                    message="An iframe's srcdoc holds a page of its own; it is not read.",
                )
            )
        if self.too_deep:
            self.page.flags.append(
                Flag(
                    rule="html.too-deep",
                    severity="review",
                    message=f"Elements nested more than {self.config.max_depth} deep are not read.",
                )
            )


def _parser(lxml_html, encoding: str | None):
    options = {"encoding": encoding} if encoding else {}
    # `huge_tree` lifts libxml2's own size and depth limits, which drop the rest of a page without
    # a word: the file size is already capped, no entity is expanded, and `max_depth` still applies.
    return lxml_html.HTMLParser(
        no_network=True, remove_comments=True, remove_pis=True, huge_tree=True, **options
    )


def _limit_flags(parser) -> list[Flag]:
    """What libxml2 gave up on, which it reports only in its error log."""
    found = []
    for error in parser.error_log:
        if error.type_name != "ERR_RESOURCE_LIMIT":
            continue
        if "depth" in error.message.lower():
            rule, message = (
                "html.too-deep",
                "The page nests deeper than the parser reads; the rest is not read.",
            )
        else:
            rule, message = "html.parse-limit", "The parser stopped at a size limit; the rest is not read."
        if all(flag.rule != rule for flag in found):
            found.append(Flag(rule=rule, severity="review", message=message))
    return found


def fragment(content: str, config: Config, base: Path | None, hidden: bool = False, loader=None) -> Reader:
    """Raw HTML found inside another file, read as a page of its own. Its lines, figures and
    images are in the returned reader's `page` and `images`."""
    import lxml.html
    from lxml import etree

    reader = Reader(Page(number=1, kind="body"), config, base, loader)
    parser = _parser(lxml.html, "utf-8")
    try:
        root = lxml.html.document_fromstring(content.encode("utf-8"), parser=parser)
    except etree.ParserError:  # nothing in it but comments or markup
        return reader
    reader.run(root, hidden)
    for flag in _limit_flags(parser):
        if all(flag.rule != f.rule for f in reader.page.flags):
            reader.page.flags.append(flag)
    return reader


def _declared(data: bytes) -> str | None:
    """The charset a `<meta>` tag near the top names; text that merely mentions one, and a
    tag in a comment, do not count."""
    found = _META_CHARSET.search(_COMMENT.sub(b"", data[:8192]))
    return found.group(1).decode("ascii", "ignore") if found else None


def _text_of(data: bytes, name: str, document) -> str:
    """The page as text. A byte-order mark decides; else a declared charset that Python knows;
    else UTF-8 when the bytes are, and Windows-1252 when they are not. UTF-16 and UTF-32 cannot
    be declared for bytes that are not, so they count as no declaration. Anything that did not
    fit is flagged."""
    utf8 = data.startswith(_UTF8_BOM)
    declared = None if utf8 or data.startswith((b"\xff\xfe", b"\xfe\xff")) else _declared(data)
    codec = None
    if declared:
        try:
            codec = codecs.lookup(declared).name
        except LookupError:
            codec = ""
        if codec.startswith(("utf-16", "utf-32", "utf_16", "utf_32")):
            codec = None
        elif codec in ("iso8859-1", "ascii"):
            codec = "cp1252"  # what browsers read a Latin-1 or ASCII declaration as
    if codec:
        try:
            return data.decode(codec)
        except UnicodeDecodeError:
            _note(
                document,
                "encoding.mismatch",
                f"The page declares {declared} but some bytes do not fit it; they were read as "
                "replacement characters.",
            )
            return data.decode(codec, errors="replace")
    try:
        text, guessed = _build.decode(data)
    except UnicodeDecodeError as error:
        raise ReaderRefused("html.invalid", f"{name} is not valid UTF-16 text: {error}") from error
    if codec == "":
        _note(
            document,
            "encoding.mismatch",
            f"The declared encoding {declared} is not known; read as "
            f"{'Windows-1252' if guessed else 'UTF-8'}.",
        )
    elif guessed:
        _note(document, "encoding.guessed", "Not UTF-8; read as Windows-1252.")
    return text


def _note(document, rule: str, message: str) -> None:
    document.flags.append(Flag(rule=rule, severity="review", message=message))


def _trim_end_tags(payload: bytes) -> bytes:
    """libxml2 stops at `</html>` and drops what follows; browsers do not. The last `</html>` and
    the last `</body>` are removed when something other than closing tags follows them."""
    for pattern in _LAST_TAGS:
        last = next(iter(collections.deque(pattern.finditer(payload), maxlen=1)), None)
        if last and _END_TAGS.sub(b"", payload[last.end() :]).strip():
            payload = payload[: last.start()] + payload[last.end() :]
    return payload


def read(data: bytes, name: str, config: Config, base: Path | None = None) -> ReadResult:
    try:
        import lxml.html
        from lxml import etree
    except ModuleNotFoundError as error:
        raise ReaderUnavailable("The html reader needs lxml: pip install 'ragnaroq[formats]'") from error
    document = _build.start(data, name, "html", config, {"reader:html": READER_VERSIONS["html"]})
    page = document.pages[0]
    if not data.replace(_UTF8_BOM, b"").strip() or data in (b"\xff\xfe", b"\xfe\xff"):
        return ReadResult(document)
    text = _text_of(data, name, document)
    payload = _trim_end_tags(text.encode("utf-8"))
    encoding = "utf-8"
    parser = _parser(lxml.html, encoding)
    try:
        root = lxml.html.document_fromstring(payload, parser=parser)
    except etree.ParserError as error:
        if "empty" in str(error).lower():  # only comments or declarations: no content
            return ReadResult(document)
        raise ReaderRefused("html.invalid", f"{name} could not be read as a page: {error}") from error
    except (etree.LxmlError, ValueError) as error:
        raise ReaderRefused("html.invalid", f"{name} could not be read as a page: {error}") from error
    head = root.find("head")
    title = head.find("title") if head is not None else None
    if title is not None:
        page.name = collapse("".join(_texts(title, frozenset(), _INLINE))) or page.name
    reader = Reader(page, config, base)
    reader.run(root)
    for flag in _limit_flags(parser):
        if all(flag.rule != f.rule for f in page.flags):
            page.flags.append(flag)
    return ReadResult(document, reader.images)
