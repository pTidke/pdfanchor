"""HTML. One body page. Every element that holds text of its own is a line anchored at its path
(`lxml`'s `getpath` for the file as parsed); inline elements belong to the nearest block above
them, and a nested block is a line of its own after it. Scripts, styles, templates, comments and
the head are not read, except the title, which names the page. Text a visitor never sees is
read and flagged. Images are figures; none is fetched, and none leaves the file's folder."""

import codecs
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
# The WHATWG Encoding Standard's labels, by the decoder a browser uses for them. Where browsers
# read a superset (windows-949 for euc-kr, GB18030 for gbk, windows-31j for Shift_JIS, ...), so do we.
_DECODERS = {
    "utf-8": "unicode-1-1-utf-8 unicode11utf8 unicode20utf8 utf-8 utf8 x-unicode20utf8",
    "ibm866": "866 cp866 csibm866 ibm866",
    "iso8859-2": "csisolatin2 iso-8859-2 iso-ir-101 iso8859-2 iso88592 iso_8859-2 iso_8859-2:1987 l2 latin2",
    "iso8859-3": "csisolatin3 iso-8859-3 iso-ir-109 iso8859-3 iso88593 iso_8859-3 iso_8859-3:1988 l3 latin3",
    "iso8859-4": "csisolatin4 iso-8859-4 iso-ir-110 iso8859-4 iso88594 iso_8859-4 iso_8859-4:1988 l4 latin4",
    "iso8859-5": (
        "csisolatincyrillic cyrillic iso-8859-5 iso-ir-144 iso8859-5 iso88595 iso_8859-5 iso_8859-5:1988"
    ),
    "iso8859-6": (
        "arabic asmo-708 csiso88596e csiso88596i csisolatinarabic ecma-114 iso-8859-6 iso-8859-6-e"
        " iso-8859-6-i iso-ir-127 iso8859-6 iso88596 iso_8859-6 iso_8859-6:1987"
    ),
    "iso8859-7": (
        "csisolatingreek ecma-118 elot_928 greek greek8 iso-8859-7 iso-ir-126 iso8859-7 iso88597"
        " iso_8859-7 iso_8859-7:1987 sun_eu_greek"
    ),
    "iso8859-8": (
        "csiso88598e csiso88598i csisolatinhebrew hebrew iso-8859-8 iso-8859-8-e iso-8859-8-i"
        " iso-ir-138 iso8859-8 iso88598 iso_8859-8 iso_8859-8:1988 logical visual"
    ),
    "iso8859-10": "csisolatin6 iso-8859-10 iso-ir-157 iso8859-10 iso885910 l6 latin6",
    "iso8859-13": "iso-8859-13 iso8859-13 iso885913",
    "iso8859-14": "iso-8859-14 iso8859-14 iso885914",
    "iso8859-15": "csisolatin9 iso-8859-15 iso8859-15 iso885915 iso_8859-15 l9",
    "iso8859-16": "iso-8859-16",
    "koi8-r": "cskoi8r koi koi8 koi8-r koi8_r",
    "koi8-u": "koi8-ru koi8-u",
    "mac-roman": "csmacintosh mac macintosh x-mac-roman",
    "cp874": "dos-874 iso-8859-11 iso8859-11 iso885911 tis-620 windows-874",
    "cp1250": "cp1250 windows-1250 x-cp1250",
    "cp1251": "cp1251 windows-1251 x-cp1251",
    "cp1252": (
        "ansi_x3.4-1968 ascii cp1252 cp819 csisolatin1 ibm819 iso-8859-1 iso-ir-100 iso8859-1 iso88591"
        " iso_8859-1 iso_8859-1:1987 l1 latin1 us-ascii windows-1252 x-cp1252"
    ),
    "cp1253": "cp1253 windows-1253 x-cp1253",
    "cp1254": (
        "cp1254 csisolatin5 iso-8859-9 iso-ir-148 iso8859-9 iso88599 iso_8859-9 iso_8859-9:1989 l5 latin5"
        " windows-1254 x-cp1254"
    ),
    "cp1255": "cp1255 windows-1255 x-cp1255",
    "cp1256": "cp1256 windows-1256 x-cp1256",
    "cp1257": "cp1257 windows-1257 x-cp1257",
    "cp1258": "cp1258 windows-1258 x-cp1258",
    "mac-cyrillic": "x-mac-cyrillic x-mac-ukrainian",
    "gb18030": ("chinese csgb2312 csiso58gb231280 gb2312 gb_2312 gb_2312-80 gbk iso-ir-58 x-gbk gb18030"),
    "big5hkscs": "big5 big5-hkscs cn-big5 csbig5 x-x-big5",
    "euc_jp": "cseucpkdfmtjapanese euc-jp x-euc-jp",
    "iso2022_jp": "csiso2022jp iso-2022-jp",
    "cp932": "csshiftjis ms932 ms_kanji shift-jis shift_jis sjis windows-31j x-sjis",
    "cp949": (
        "cseuckr csksc56011987 euc-kr iso-ir-149 korean ks_c_5601-1987 ks_c_5601-1989 ksc5601 ksc_5601"
        " windows-949"
    ),
    # UTF-16 and UTF-32 cannot be declared for bytes that are not: read as UTF-8, as the HTML rule says.
    None: (
        "csunicode iso-10646-ucs-2 ucs-2 unicode unicodefeff unicodefffe utf-16 utf-16be utf-16le"
        " utf-32 utf-32be utf-32le ucs-4"
    ),
}
_BIG5_ERRORS = "ragnaroq-big5"
_MISMATCH = "The page declares {} but some bytes do not fit it; they were read as replacement characters."


def _big5_errors(error):
    """Python's Big5 lacks the one code browsers map that matters on tax pages, the euro (A3E1).
    That alone is mapped here; any other byte that does not fit is a replacement character. The
    page is never decoded again with another table, which reads other characters differently."""
    if not isinstance(error, UnicodeDecodeError):
        raise error
    if error.object[error.start : error.start + 2] == b"\xa3\xe1":
        return "\u20ac", error.start + 2
    return "\ufffd", error.end


codecs.register_error(_BIG5_ERRORS, _big5_errors)
_LABEL_DECODER = {label: codec for codec, labels in _DECODERS.items() for label in labels.split()}
_MAX_COLSPAN, _MAX_ROWSPAN = 1000, 65534  # HTML's own limits; columns past 1000 are not tracked

HIDDEN_MESSAGE = "Not shown on the page; a reader of the page never sees it."
EQUATION_MESSAGE = (
    "An equation is read as its plain symbols; fractions, powers and layout are lost; compare with the page."
)


def is_hidden(element) -> bool:
    """Marked as not shown: the `hidden` attribute, or an inline style that hides it."""
    style = element.get("style")
    return element.get("hidden") is not None or bool(style and HIDDEN_STYLE.search(style))


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
        self.table_limit = False
        self.untracked = False
        self.work = 0
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
        if not len(element):  # nothing nested: the common case, and cheap
            text = element.text or ""
            text = text[:-1] if pre and text.endswith("\n") else text if pre else collapse(text)
            return [("text", text, False, False)] if text.strip() else []
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
            if not parts and not pending:
                return
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
            return [iter(el), el, el_path, Counter(c.tag for c in el) if len(el) > 1 else None, {}, hid]

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
            child_path = f"{top[2]}/{tag}" + (f"[{number}]" if top[3] is not None and top[3][tag] > 1 else "")
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

    def spend(self, steps: int = 1) -> bool:
        """Count work on rowspans; past the budget they are no longer tracked."""
        self.work += steps
        if self.work > 4 * self.config.max_cells and not self.untracked:
            self.untracked = self.table_limit = True
        return not self.untracked

    def open_cell(self, element, path) -> _Cell:
        table = self.tables[-1]
        if table.row == 0:  # a cell with no row around it
            table.row = table.rows = 1
        column = table.column
        while not self.untracked and table.busy.get(column, 0) >= table.row:
            column += 1  # a slot a rowspan above still holds
            self.spend()
        columns = _span(element.get("colspan"), _MAX_COLSPAN)
        rows = _span(element.get("rowspan"), _MAX_ROWSPAN)
        if columns > 1 and table.row * (column + columns) > self.config.max_cells:
            columns = 1  # a table is not stretched past the cell limit
            self.table_limit = True
        if rows > 1 and not self.untracked:
            until = table.row + rows - 1
            for spanned in range(column, min(column + columns, _MAX_COLSPAN)):
                table.busy[spanned] = max(table.busy.get(spanned, 0), until)
            self.spend(columns)
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

    def run(self, roots, hidden=False):
        """Read each `html` root: libxml2 gives content after a closing `</html>` a root of its own,
        and its paths are numbered among them (`/html[2]/p`)."""
        stack: list[_Frame] = []
        counts = Counter(root.tag for root in roots)
        seen: Counter = Counter()
        for root in roots:
            seen[root.tag] += 1
            path = f"/{root.tag}" + (f"[{seen[root.tag]}]" if counts[root.tag] > 1 else "")
            self.enter(root, path, 1, hidden, False, "", stack)
            self.walk(stack)
        self.finish()

    def walk(self, stack):
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

    def finish(self):
        self.page.tables.sort(key=lambda table: int(table.id.rsplit("-t", 1)[1]))
        if self.table_limit:
            self.page.flags.append(
                Flag(
                    rule="html.table-limit",
                    severity="review",
                    message=(
                        "A table is too large to lay out exactly (columns stop stretching at "
                        f"{self.config.max_cells} cells, and rowspans stop being tracked); cell "
                        "positions in that table may be off. The text is kept."
                    ),
                )
            )
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
    reader.run([root, *root.itersiblings()], hidden)
    for flag in _limit_flags(parser):
        if all(flag.rule != f.rule for f in reader.page.flags):
            reader.page.flags.append(flag)
    return reader


def _declared(data: bytes) -> str | None:
    """The charset a `<meta>` tag near the top names; text that merely mentions one, and a
    tag in a comment, do not count."""
    found = _META_CHARSET.search(_COMMENT.sub(b"", data[:8192]))
    return found.group(1).decode("ascii", "ignore") if found else None


def _codec(label: str) -> str | None:
    """The decoder for a declared label, None for UTF-16 and UTF-32 (read as UTF-8), and "" for a
    label the standard does not know, which is never trusted."""
    return _LABEL_DECODER.get(label.strip(" \t\n\r\f").lower(), "")


def _text_of(data: bytes, name: str, document) -> str:
    """The page as text. A byte-order mark decides; else a declared charset that Python knows;
    else UTF-8 when the bytes are, and Windows-1252 when they are not. UTF-16 and UTF-32 cannot
    be declared for bytes that are not, so they count as no declaration. Anything that did not
    fit is flagged."""
    utf8 = data.startswith(_UTF8_BOM)
    declared = None if utf8 or data.startswith((b"\xff\xfe", b"\xfe\xff")) else _declared(data)
    codec = None
    if declared:
        codec = _codec(declared)
    if codec:
        if codec == "big5hkscs":
            text = data.decode(codec, errors=_BIG5_ERRORS)
            if "\ufffd" in text:  # Big5 has no U+FFFD, so it is a byte that did not fit
                _note(document, "encoding.mismatch", _MISMATCH.format(declared))
            return text
        try:
            return data.decode(codec)
        except UnicodeDecodeError:
            _note(
                document,
                "encoding.mismatch",
                _MISMATCH.format(declared),
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
    payload = text.encode("utf-8")
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
    reader.run([root, *root.itersiblings()])
    for flag in _limit_flags(parser):
        if all(flag.rule != f.rule for f in page.flags):
            page.flags.append(flag)
    return ReadResult(document, reader.images)
