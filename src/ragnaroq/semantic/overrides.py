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
    """The line of a key: the quoted key (or the bare key) at a line start; 1 when not found."""
    quoted = [re.escape(json.dumps(key)), re.escape("'" + key + "'"), re.escape(key)]
    for number, line in enumerate(text.splitlines(), 1):
        if any(re.match(r"\s*" + q + r"\s*=", line) for q in quoted):
            return number
    return 1


def _integer(value: object, name: str, low: int, high: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{name} must be a whole number")
    if not low <= value <= high:
        raise ValueError(f"{name} = {value} is outside {low} to {high}")
    return value


def _one(pattern: str, value: object) -> Override:
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
        top = (max_chars if max_chars is not None else MAX_CHARS_LIMITS[1]) // 2
        min_chars = _integer(value["min_chars"], "min_chars", 0, top)
        if max_chars is not None and min_chars > max_chars // 2:
            raise ValueError(f"min_chars = {min_chars} is over half of max_chars = {max_chars}")
    return Override(pattern, strategy, max_chars, min_chars)


def load(text: str) -> list[Override]:
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
            found_overrides.append(_one(pattern, value))
        except ValueError as error:
            raise OverrideError(f"line {_line_of(text, pattern)}: {error}") from error
    return found_overrides


def load_file(path: Path) -> list[Override]:
    if not path.is_file():
        return []
    return load(path.read_text(encoding="utf-8"))


def match(overrides: list[Override], path: str) -> Override | None:
    return next((o for o in overrides if fnmatchcase(path, o.pattern)), None)
