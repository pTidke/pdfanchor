"""What a document is made of: numbered paragraphs, provisos, explanations, question-answer pairs,
definitions and records, and the counts the kind rules need. Every pattern is linear."""

import re
from dataclasses import dataclass, field

from ..anchors import JsonAnchor, XmlAnchor
from ..model import Document
from .paragraphs import Para, numbering

_PROVISO = re.compile(r"Provided\s(?:(?:further|also)\s)?that\b")
_EXPLANATION = re.compile(r"Explanation\b\s?(?:\d{1,3}[A-Za-z]?\s?)?[.:—–-]")
_QUESTION_MARK = re.compile(r"(?:Q\d{0,3}[.:]|Question\b)")
_ANSWER_MARK = re.compile(r"(?:A[.:]|Ans\.|Answer\b)")
_SHORT_QUESTION = 200

_QUOTED_TERM = re.compile(r'(?:"([^"\n]{1,80})"|“([^”\n]{1,80})”)\s+(?:means|shall\s+mean|includes)\b')
_PLAIN_TERM = re.compile(r"(?P<term>[A-Za-z][\w'’&/-]*(?:\s[\w'’&/-]+){0,3}?):?\s(?:means|shall\s+mean)\b")
_NOT_TERMS = frozenset(
    "this that it which what who he she they these those there here nothing everything anything not".split()
)
_SENTENCE_END = re.compile(r"\.(?=\s+[A-Z\"“(]|\s*$)")
_SENTENCE_START = re.compile(r"[.;]\s+")
_TABLE_HEADERS = (("term", "definition"), ("word", "meaning"))

_DOTTED = re.compile(r"\d{1,3}(?:\.\d{1,3})*\.?")
_ROMAN = re.compile(r"m{0,3}(?:cm|cd|d?c{0,3})(?:xc|xl|l?x{0,3})(?:ix|iv|v?i{0,3})")
_ROMAN_VALUES = {"i": 1, "v": 5, "x": 10, "l": 50, "c": 100, "d": 500, "m": 1000}
_LABELLED = re.compile(r"(Article|Section|Clause)\s+(\S+)")


@dataclass
class Definition:
    term: str
    para: Para
    start: int
    end: int


@dataclass
class Shape:
    numbered: list[Para] = field(default_factory=list)
    provisos: list[Para] = field(default_factory=list)
    explanations: list[Para] = field(default_factory=list)
    qa: list[tuple[Para, list[Para]]] = field(default_factory=list)
    definitions: list[Definition] = field(default_factory=list)
    records: list[str] = field(default_factory=list)
    headings: int = 0
    heading_depth: int = 0
    tables: int = 0
    table_chars: int = 0
    body_chars: int = 0
    chars: int = 0
    figures: int = 0
    slides: int = 0


# numbered paragraphs


def _roman(letters: str) -> int | None:
    if not letters or not _ROMAN.fullmatch(letters):
        return None
    total = 0
    for i, letter in enumerate(letters):
        value = _ROMAN_VALUES[letter]
        total += -value if i + 1 < len(letters) and _ROMAN_VALUES[letters[i + 1]] > value else value
    return total


def _numbers(token: str) -> list[tuple[tuple, int]]:
    """The (form, value) readings of a numbering token. Two tokens are in sequence when
    they share a form and the value goes up by one. An unreadable token has no reading."""
    if label := _LABELLED.fullmatch(token):
        parts = _DOTTED.fullmatch(label.group(2))
        if not parts:
            return []
        numbers = label.group(2).rstrip(".").split(".")
        return [((label.group(1), tuple(numbers[:-1])), int(numbers[-1]))]
    if _DOTTED.fullmatch(token):
        numbers = token.rstrip(".").split(".")
        return [(("dot", tuple(numbers[:-1])), int(numbers[-1]))]
    if token[0] == "(":
        mark, inner = "()", token[1:-1]
    else:
        mark, inner = token[-1], token[:-1]
    if inner.isdigit():
        return [(("number", mark), int(inner))]
    case = "upper" if inner.isupper() else "lower"
    found = []
    if len(inner) == 1:
        found.append((("letter", mark, case), ord(inner.lower()) - 96))
    if (value := _roman(inner.lower())) is not None:
        found.append((("roman", mark, case), value))
    return found


def _runs(paras: list[Para]) -> list[Para]:
    """Body paragraphs whose number follows another's of the same form (1., 2.; (a), (b); I., II.),
    with any paragraphs between. A number nothing continues is not counted."""
    last: dict[tuple, tuple[int, list[int]]] = {}
    members: set[int] = set()
    for index, para in enumerate(paras):
        if para.kind != "body" or not (token := numbering(para.text)):
            continue
        for form, value in _numbers(token):
            previous = last.get(form)
            if previous and previous[0] + 1 == value:
                run = previous[1]
                run.append(index)
                members.update(run)
            else:
                run = [index]
            last[form] = (value, run)
    return [paras[i] for i in sorted(members)]


def _sentence_starts(text: str) -> list[int]:
    """Where the paragraph's text begins after any number, and where each sentence after it begins."""
    text_start = len(text) - len(text.lstrip())
    if token := numbering(text):
        text_start += len(token)
        text_start = len(text) - len(text[text_start:].lstrip())
    return [text_start, *(m.end() for m in _SENTENCE_START.finditer(text))]


# question-answer pairs


def _is_question(paras: list[Para], i: int) -> bool:
    para = paras[i]
    text = para.text.strip()
    if para.kind == "heading":
        return text.endswith("?")
    if para.kind != "body" or _ANSWER_MARK.match(text):
        return False
    if _QUESTION_MARK.match(text):
        return True
    return (
        len(text) <= _SHORT_QUESTION
        and text.endswith("?")
        and i + 1 < len(paras)
        and not paras[i + 1].text.rstrip().endswith("?")
    )


def _pairs(paras: list[Para]) -> list[tuple[Para, list[Para]]]:
    questions = [_is_question(paras, i) for i in range(len(paras))]
    pairs = []
    for i, para in enumerate(paras):
        if not questions[i]:
            continue
        answers = []
        for j in range(i + 1, len(paras)):
            if questions[j] or paras[j].kind == "heading":
                break
            answers.append(paras[j])
        if any(a.kind == "body" for a in answers):
            pairs.append((para, answers))
    return pairs


# definitions


def _sentence_end(text: str, after: int) -> int:
    end = _SENTENCE_END.search(text, after)
    return end.end() if end else len(text)


def _definitions_in(para: Para) -> list[Definition]:
    text = para.text
    found: dict[int, Definition] = {}
    for match in _QUOTED_TERM.finditer(text):
        term = match.group(1) or match.group(2)
        found[match.start()] = Definition(term, para, match.start(), _sentence_end(text, match.end()))
    for start in _sentence_starts(text):
        match = _PLAIN_TERM.match(text, start)
        if match and not any(w.lower() in _NOT_TERMS for w in match.group("term").split()):
            found.setdefault(
                start, Definition(match.group("term"), para, start, _sentence_end(text, match.end()))
            )
    return [found[start] for start in sorted(found)]


def _table_definitions(para: Para) -> list[Definition]:
    table = para.table
    if table is None or table.columns != 2:
        return []
    cells = {(c.row, c.column): (c.text or "").strip() for c in table.cells}
    header = (cells.get((0, 0), "").casefold(), cells.get((0, 1), "").casefold())
    if header not in _TABLE_HEADERS:
        return []
    lines, offset = [], 0
    for line in para.text.split("\n"):
        lines.append((offset, offset + len(line), line))
        offset += len(line) + 1
    found, cursor = [], 2
    for row in range(1, table.rows):
        term = cells.get((row, 0), "")
        if not term:
            continue
        start, end = 0, len(para.text)
        for k in range(cursor, len(lines)):
            if lines[k][2].startswith("|") and term in lines[k][2]:
                start, end, cursor = lines[k][0], lines[k][1], k + 1
                break
        found.append(Definition(term, para, start, end))
    return found


# records


def _records(document: Document) -> list[str]:
    json_records: dict[str, None] = {}
    xml_records: dict[str, None] = {}
    xml_names: set[str] = set()
    for line in document.lines():
        anchor = line.anchor
        if line.excluded:
            continue
        if isinstance(anchor, JsonAnchor) and line.role != "table" and anchor.pointer.startswith("/"):
            json_records.setdefault("/" + anchor.pointer.split("/")[1], None)
        elif isinstance(anchor, XmlAnchor):
            parts = anchor.path.split("/")
            if len(parts) > 2 and not parts[2].startswith("@") and parts[2] != "text()":
                xml_records.setdefault("/".join(parts[:3]), None)
                xml_names.add(re.sub(r"\[\d+\]$", "", parts[2]))
    if json_records:
        return list(json_records)
    return list(xml_records) if len(xml_names) == 1 and len(xml_records) > 1 else []


def shape(paras: list[Para], document: Document) -> Shape:
    result = Shape(numbered=_runs(paras), qa=_pairs(paras), records=_records(document))
    for para in paras:
        if para.kind == "heading":
            result.headings += 1
            result.heading_depth = max(result.heading_depth, para.level)
        elif para.kind == "table":
            result.tables += 1
            result.table_chars += len(para.text)
            result.definitions += _table_definitions(para)
        elif para.kind == "body":
            result.body_chars += len(para.text)
            starts = _sentence_starts(para.text)
            if any(_PROVISO.match(para.text, s) for s in starts):
                result.provisos.append(para)
            if any(_EXPLANATION.match(para.text, s) for s in starts):
                result.explanations.append(para)
            result.definitions += _definitions_in(para)
        if para.kind != "image":
            result.chars += len(para.text)
    result.figures = sum(len(page.figures) for page in document.pages)
    result.slides = sum(1 for page in document.pages if page.kind == "slide")
    return result
