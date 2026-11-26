"""Answer keys written by hand, and scoring a document against one by anchor.

An answer key is a `<file>.expected.jsonl` beside the file, one JSON object per line:
a line (`anchor`, `text`, and `flag` if the line must carry that rule), a figure (`figure`,
`description`, `description_source`) or a document flag (`document_flag`).

A line is matched by `anchors.key`, and its text is compared after NFC normalisation and
nothing else: no trimming, no case folding. A file the reader refuses is a document with no
lines and the flag that says why, so its key is a single `document_flag` entry.
"""

import unicodedata
from dataclasses import dataclass, field
from pathlib import Path

from pydantic import BaseModel, ConfigDict, TypeAdapter

from .anchors import Anchor, key
from .model import Document

_ANCHOR = TypeAdapter(Anchor)


class Expected(BaseModel):
    model_config = ConfigDict(extra="forbid")

    anchor: Anchor | None = None
    text: str | None = None
    flag: str | None = None
    figure: Anchor | None = None
    description: str | None = None
    description_source: str | None = None
    document_flag: str | None = None


@dataclass
class ExactScore:
    right: int = 0
    wrong: list[dict] = field(default_factory=list)
    missing: list[dict] = field(default_factory=list)
    extra: list[dict] = field(default_factory=list)
    flags_missing: list[dict] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        """Nothing wrong, missing or unflagged. Extra lines are reported but do not fail."""
        return not (self.wrong or self.missing or self.flags_missing)


def load(path: str | Path) -> list[Expected]:
    items = []
    for number, text in enumerate(Path(path).read_text(encoding="utf8").splitlines(), 1):
        if text.strip():
            try:
                items.append(Expected.model_validate_json(text))
            except ValueError as error:
                raise ValueError(f"{Path(path).name}, line {number}: {error}") from error
    return items


def _nfc(text: str) -> str:
    return unicodedata.normalize("NFC", text)


def _dump(anchor: Anchor) -> dict:
    return anchor.model_dump(mode="json")


def score(document: Document, items: list[Expected]) -> ExactScore:
    result = ExactScore()
    lines: dict[str, list] = {}
    for line in document.lines():
        if line.anchor is not None:
            lines.setdefault(key(line.anchor), []).append(line)
    figures = {key(figure.anchor): figure for page in document.pages for figure in page.figures}
    document_flags = {flag.rule for flag in document.flags}
    seen: set[str] = set()
    taken: set[int] = set()  # lines already matched, for several cells that share one anchor
    for item in items:
        if item.document_flag is not None:
            if item.document_flag in document_flags:
                result.right += 1
            else:
                result.flags_missing.append({"document_flag": item.document_flag})
        elif item.figure is not None:
            seen.add(key(item.figure))
            figure = figures.get(key(item.figure))
            if figure is None:
                result.missing.append({"figure": _dump(item.figure), "expected": item.description})
                continue
            problems = {}
            if item.description is not None and _nfc(figure.description) != _nfc(item.description):
                problems["description"] = {"expected": item.description, "read": figure.description}
            if item.description_source is not None and figure.description_source != item.description_source:
                problems["description_source"] = {
                    "expected": item.description_source,
                    "read": figure.description_source,
                }
            if problems:
                result.wrong.append({"figure": _dump(item.figure), **problems})
            elif item.flag is not None and item.flag not in {flag.rule for flag in figure.flags}:
                result.flags_missing.append({"flag": item.flag, "figure": _dump(item.figure)})
            else:
                result.right += 1
        elif item.anchor is not None:
            seen.add(key(item.anchor))
            found = lines.get(key(item.anchor))
            if not found:
                result.missing.append({"anchor": _dump(item.anchor), "expected": item.text})
                continue
            free = [line for line in found if id(line) not in taken] or found
            line = free[0]
            if item.text is not None:
                line = next((c for c in free if _nfc(c.text) == _nfc(item.text)), line)
            taken.add(id(line))
            if item.text is not None and _nfc(line.text) != _nfc(item.text):
                result.wrong.append({"anchor": _dump(item.anchor), "expected": item.text, "read": line.text})
                continue
            if item.flag is not None and item.flag not in {flag.rule for flag in line.flags}:
                result.flags_missing.append({"flag": item.flag, "anchor": _dump(item.anchor)})
                continue
            result.right += 1
    for k, found in lines.items():
        for line in found:
            if k not in seen or id(line) not in taken:
                result.extra.append({"anchor": _dump(line.anchor), "read": line.text})
    return result
