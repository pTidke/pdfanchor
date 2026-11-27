"""Sentence ranges and greedy packing of whole sentences into cuts.

Both functions work on one paragraph's joined text and are linear in its length.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass

from ragnaroq.semantic.rules import REFERENCE_OVERRUN

ABBREVIATIONS = frozenset(
    {
        "no", "nos", "rs", "sec", "s", "art", "cl", "e.g", "i.e", "viz", "etc",
        "mr", "mrs", "ms", "dr", "st", "vs", "ltd", "co", "inc",
        "jan", "feb", "mar", "apr", "jun", "jul", "aug", "sep", "sept", "oct", "nov", "dec",
    }
)  # fmt: skip
OPENERS = frozenset("([\"'“‘")
_MARK = re.compile(r"([.?!;]+|[।॥]+)([)\]\"'”’]*)(\s*)")
_TOKEN_LIMIT = 12


def _is_abbreviation(text: str, dot: int) -> bool:
    """True when the dot at `dot` follows an abbreviation or a single-letter initial."""
    start = dot
    low = max(0, dot - _TOKEN_LIMIT)
    while start > low and not text[start - 1].isspace() and text[start - 1] not in OPENERS:
        start -= 1
    token = text[start:dot].lower()
    if len(token) == 1:
        return token.isalpha()
    return token in ABBREVIATIONS


def sentences(text: str, breaks: Sequence[int] = ()) -> list[tuple[int, int]]:
    """Contiguous ranges covering `text`; whitespace between sentences goes to the earlier one."""
    size = len(text)
    if not size:
        return []
    starts = {0}
    for found in _MARK.finditer(text):
        mark, _, space = found.groups()
        after = found.end()
        if after >= size:
            continue
        if mark[0] in "।॥":
            starts.add(after)
            continue
        if not space:
            continue
        follower = text[after]
        if not (follower.isupper() or follower.isdigit() or follower in OPENERS):
            continue
        if mark[-1] == "." and len(mark) == 1 and _is_abbreviation(text, found.start(1)):
            continue
        starts.add(after)
    starts.update(b for b in breaks if 0 < b < size)
    ordered = sorted(starts)
    return list(zip(ordered, [*ordered[1:], size], strict=True))


@dataclass(frozen=True)
class Cut:
    start: int
    end: int
    long: bool


def _split_long(text: str, start: int, end: int, trimmed: int, max_chars: int) -> list[Cut]:
    cuts = []
    pos = start
    while trimmed - pos > max_chars:
        space = text.rfind(" ", pos + 1, pos + max_chars + 1)
        stop = space if space > pos else pos + max_chars
        following = stop
        while following < trimmed and text[following] == " ":
            following += 1
        cuts.append(Cut(pos, following, True))
        pos = following
    cuts.append(Cut(pos, end, True))
    return cuts


def pack(
    text: str,
    ranges: list[tuple[int, int]],
    max_chars: int,
    *,
    protected: frozenset[int] = frozenset(),
    overlap: bool = True,
) -> list[Cut]:
    """Greedy cuts of whole sentences, each at most `max_chars` (trailing whitespace not counted).

    With `overlap`, a cut after the first begins with the previous cut's last sentence, unless that
    sentence plus the next one would not fit in `max_chars`; then that cut has no overlap. A cut
    after a long sentence's parts has none either. A sentence over `max_chars` is cut at the last
    space before the limit (at the limit when there is none) into `Cut(long=True)` parts. A sentence
    whose index is in `protected` may make its cut run to `max_chars * (1 + REFERENCE_OVERRUN)`
    before it counts as long.
    """
    count = len(ranges)
    stretched = int(max_chars * (1 + REFERENCE_OVERRUN))
    ends = [stop - (len(text[start:stop]) - len(text[start:stop].rstrip())) for start, stop in ranges]

    def limit(index: int) -> int:
        return stretched if index in protected else max_chars

    def is_long(index: int) -> bool:
        return ends[index] - ranges[index][0] > limit(index)

    cuts: list[Cut] = []
    previous: int | None = None
    i = 0
    while i < count:
        start, stop = ranges[i]
        if is_long(i):
            cuts.extend(_split_long(text, start, stop, ends[i], max_chars))
            previous = None
            i += 1
            continue
        first = i
        if overlap and previous is not None and ends[i] - ranges[previous][0] <= limit(i):
            first = previous
        last = i
        origin = ranges[first][0]
        while last + 1 < count and not is_long(last + 1) and ends[last + 1] - origin <= limit(last + 1):
            last += 1
        cuts.append(Cut(origin, ranges[last][1], False))
        previous = last
        i = last + 1
    return cuts
