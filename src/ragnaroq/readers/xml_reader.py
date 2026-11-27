"""XML, read with defusedxml. An element with child elements is a heading; a leaf is
`tag: text`; an attribute is `name: value` at `<path>/@name`."""

from collections import Counter

from defusedxml import DefusedXmlException
from defusedxml import ElementTree as SafeET

from ..anchors import XmlAnchor
from ..config import Config
from ..model import Flag
from . import READER_VERSIONS, ReaderRefused, ReadResult, _build


class _Full(Exception):
    """The file holds more lines than the cell limit: reading stops here."""


def _local(tag) -> str | None:
    return tag.rsplit("}", 1)[-1] if isinstance(tag, str) else None  # comments and instructions have none


def read(data: bytes, name: str, config: Config) -> ReadResult:
    try:
        root = SafeET.fromstring(data)
    except DefusedXmlException as error:
        message = f"{name} declares entities or a DTD, which are not read."
        raise ReaderRefused("xml.entities", message) from error
    except SafeET.ParseError as error:
        raise ReaderRefused("xml.invalid", f"{name} is not valid XML: {error}") from error
    document = _build.start(data, name, "xml", config, {"reader:xml": READER_VERSIONS["xml"]})
    page = document.pages[0]

    def add(text, path, role="body", level=0):
        if len(page.lines) >= config.max_cells:
            raise _Full
        _build.add_line(page, text, "xml", XmlAnchor(path=path), role=role, level=level)

    try:
        _walk(root, config, name, add)
    except _Full:
        page.flags.append(
            Flag(
                rule="xml.line-limit",
                severity="review",
                message=f"{name} has more than {config.max_cells} lines; the rest is not read.",
            )
        )
    return ReadResult(document)


def _walk(root, config: Config, name: str, add) -> None:
    """Every element, attribute and text in document order, each given to `add`."""
    stack = [(root, "/" + _local(root.tag), 1)]
    while stack:
        element, path, depth = stack.pop()
        if isinstance(element, str):  # text that follows a child element
            add(element, path)
            continue
        if depth > config.max_depth:
            raise ReaderRefused("xml.too-deep", f"{name} is nested more than {config.max_depth} levels deep.")
        children = [c for c in element if _local(c.tag)]
        text = (element.text or "").strip()
        if children:
            add(_local(element.tag), path, "heading", min(depth, 6))
        for attribute, value in element.attrib.items():
            label = _local(attribute)
            add(f"{label}: {value}", f"{path}/@{label}")
        if children:
            if text:
                add(text, f"{path}/text()")
        elif text:
            add(f"{_local(element.tag)}: {text}", path)
        counts = Counter(_local(c.tag) for c in children)
        seen: Counter = Counter()
        planned = []
        for child in children:
            tag = _local(child.tag)
            seen[tag] += 1
            index = f"[{seen[tag]}]" if counts[tag] > 1 else ""
            planned.append((child, f"{path}/{tag}{index}", depth + 1))
            tail = (child.tail or "").strip()
            if tail:
                planned.append((tail, f"{path}/text()", 0))
        stack.extend(reversed(planned))
