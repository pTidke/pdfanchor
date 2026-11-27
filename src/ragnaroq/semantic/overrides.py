"""`ragnaroq.toml`: the strategy a file or pattern is cut with (spec section 3). A mistake raises
`OverrideError` naming the line."""

import json
import re
import tomllib
from dataclasses import asdict, dataclass
from fnmatch import fnmatchcase
from pathlib import Path

from .rules import MAX_CHARS_LIMITS, STRATEGIES

KEYS = ("strategy", "max_chars", "min_chars")


class OverrideError(ValueError):
    pass


@dataclass(frozen=True)
class Override:
    pattern: str
    strategy: str
    max_chars: int | None = None
    min_chars: int | None = None

    def key(self) -> str:
        return json.dumps(asdict(self), sort_keys=True, ensure_ascii=False)


def _line_of(text: str, key: str) -> int:
    """The line of a key inside `[chunking]`: the key quoted (single or double, as written), bare or
    dotted, or a `[chunking."key"]` header. Never guesses 1: when the key cannot be found, the line
    of the `[chunking]` header."""
    forms = "|".join(
        [re.escape(json.dumps(key, ensure_ascii=False)), re.escape("'" + key + "'"), re.escape(key)]
    )
    entry = re.compile(rf"\s*(?:{forms})\s*[=.]")
    sub = re.compile(rf"\s*\[\s*chunking\s*\.\s*(?:{forms})\s*[\].]")
    header = re.compile(r"\s*\[\s*chunking\s*\]")
    found_header, inside = 1, False
    for number, line in enumerate(text.splitlines(), 1):
        if sub.match(line):
            return number
        if header.match(line):
            found_header, inside = number, True
        elif line.lstrip().startswith("["):
            inside = False
        elif inside and entry.match(line):
            return number
    return found_header


def _integer(value: object, name: str, low: int, high: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{name} must be a whole number")
    if not low <= value <= high:
        raise ValueError(f"{name} = {value} is outside {low} to {high}")
    return value


def _one(pattern: str, value: object, chunk_chars: int) -> Override:
    if isinstance(value, str):
        value = {"strategy": value}
    if not isinstance(value, dict):
        raise ValueError("expected a strategy name or a table")
    extra = sorted(set(value) - set(KEYS))
    if extra:
        raise ValueError(f"unknown key {extra[0]!r}; the keys are {', '.join(KEYS)}")
    if "strategy" not in value:
        raise ValueError("strategy is missing")
    strategy = value["strategy"]
    if not isinstance(strategy, str) or strategy not in STRATEGIES:
        raise ValueError(f"unknown strategy {strategy!r}; the strategies are {', '.join(STRATEGIES)}")
    max_chars = min_chars = None
    if "max_chars" in value:
        max_chars = _integer(value["max_chars"], "max_chars", *MAX_CHARS_LIMITS)
    if "min_chars" in value:
        top = (max_chars if max_chars is not None else chunk_chars) // 2
        min_chars = _integer(value["min_chars"], "min_chars", 0, top)
    return Override(pattern, strategy, max_chars, min_chars)


def load(text: str, *, chunk_chars: int = 1200) -> list[Override]:
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError as error:
        found = re.search(r"\(at line (\d+), column \d+\)", str(error))
        line = int(found.group(1)) if found else 1
        raise OverrideError(f"line {line}: {error}") from error
    table = data.get("chunking", {})
    if not isinstance(table, dict):
        raise OverrideError(f"line {_line_of(text, 'chunking')}: [chunking] must be a table")
    found_overrides = []
    for pattern, value in table.items():
        try:
            found_overrides.append(_one(pattern, value, chunk_chars))
        except ValueError as error:
            raise OverrideError(f"line {_line_of(text, pattern)}: {error}") from error
    return found_overrides


def load_file(path: Path, *, chunk_chars: int = 1200) -> list[Override]:
    if not path.is_file():
        return []
    return load(path.read_text(encoding="utf-8"), chunk_chars=chunk_chars)


def match(overrides: list[Override], path: str) -> Override | None:
    return next((o for o in overrides if fnmatchcase(path, o.pattern)), None)
