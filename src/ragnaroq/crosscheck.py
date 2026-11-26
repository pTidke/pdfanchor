"""Compare two readings of the same place. Agreement is decided by rule, not by confidence."""

import re
import unicodedata
from collections import Counter
from dataclasses import dataclass
from difflib import SequenceMatcher

from .model import Box
from .ocr import OcrLine

NEGATIONS = {"not", "no", "nor", "never", "without", "except", "unless", "neither", "none"}
EDGE = ".,;:'\"“”‘’"
TYPOGRAPHY = str.maketrans(
    {"‘": "'", "’": "'", "“": '"', "”": '"', "–": "-", "—": "-", "−": "-", "…": "..."}
)
# Leaders and fill lines: ". . . . 24", "Name ________". Layout, not content.
LEADERS = re.compile(r"(?:[._-]\s?){3,}")


def normalise(text: str, fold: bool = True) -> str:
    """Case-folded, single-spaced. `fold` also applies compatibility folding (NFKC), which
    turns a superscript or a ligature into the plain characters; off, only NFC is used."""
    return " ".join(unicodedata.normalize("NFKC" if fold else "NFC", text).casefold().split())


def canonical(text: str) -> str:
    """The form two texts are compared in: Unicode-normalised, case-folded, single
    spaces, typographic quotes and dashes made plain, and runs of leader dots, rules
    and fill underscores removed. Nothing else is forgiven."""
    return normalise(LEADERS.sub(" ", text.translate(TYPOGRAPHY)))


# Checkboxes, bullets and stray form rules a reader may draw as characters: layout, not text
# when they stand alone. Inside a word a mark may stand for a character, so it stays.
LAYOUT_MARKS = re.compile(r"(?:^|(?<=\s))[☐□■☑☒▢◻◼▪●○◯|](?=\s|$)")


def without_marks(text: str) -> str:
    """`text` with layout marks replaced by spaces and whitespace collapsed."""
    return " ".join(LAYOUT_MARKS.sub(" ", text).split())


def shares_words(a: str, b: str, n: int) -> bool:
    """True when the canonical texts have `n` consecutive words in common."""
    first, second = canonical(a).split(), canonical(b).split()
    if n < 1 or len(first) < n or len(second) < n:
        return False
    grams = {tuple(first[i : i + n]) for i in range(len(first) - n + 1)}
    return any(tuple(second[i : i + n]) in grams for i in range(len(second) - n + 1))


def letters(text: str) -> str:
    """Only the letters and digits, case-folded: two readings that differ in nothing
    else differ only in spacing, punctuation or symbols."""
    return "".join(ch for ch in normalise(text) if ch.isalnum())


def critical_tokens(text: str, fold: bool = True) -> Counter:
    """Tokens where one wrong character changes the meaning: anything with a digit
    (amounts, dates, section and instrument numbers) and negations. With `fold` off,
    "80¹" is not "801"."""
    tokens: Counter = Counter()
    for word in normalise(text, fold).split():
        word = word.strip(EDGE)
        if word.count("(") > word.count(")"):
            word = word.lstrip("(")
        if word.count(")") > word.count("("):
            word = word.rstrip(")")
        if any(ch.isdigit() for ch in word) or word in NEGATIONS:
            tokens[word] += 1
    return tokens


@dataclass(frozen=True)
class Agreement:
    similarity: float
    critical_equal: bool
    only_first: tuple[str, ...]
    only_second: tuple[str, ...]

    letters_equal: bool = True  # the same letters and digits, whatever the spacing and punctuation

    def agrees(self, min_similarity: float, same_letters: bool = False) -> bool:
        return (
            self.critical_equal
            and self.similarity >= min_similarity
            and (self.letters_equal or not same_letters)
        )


def compare(first: str, second: str) -> Agreement:
    a, b = critical_tokens(first), critical_tokens(second)
    return Agreement(
        similarity=round(SequenceMatcher(None, normalise(first), normalise(second)).ratio(), 4),
        critical_equal=a == b,
        only_first=tuple(sorted((a - b).elements())),
        only_second=tuple(sorted((b - a).elements())),
        letters_equal=letters(first) == letters(second),
    )


def to_points(line: OcrLine, dpi: int) -> Box:
    scale = 72 / dpi
    x0, top, x1, bottom = (value * scale for value in line.box)
    return Box(x0=x0, top=top, x1=x1, bottom=bottom)


def text_inside(box: Box, readings: list[tuple[Box, OcrLine]], margin: float = 2.0) -> OcrLine | None:
    """The other engine's reading of the same line: OCR lines whose centre falls in `box`,
    joined left to right. None when the other engine found nothing there."""
    inside = [
        (other, line)
        for other, line in readings
        if box.x0 - margin <= (other.x0 + other.x1) / 2 <= box.x1 + margin
        and box.top - margin <= (other.top + other.bottom) / 2 <= box.bottom + margin
    ]
    if not inside:
        return None
    inside.sort(key=lambda pair: pair[0].x0)
    scores = [line.confidence for _, line in inside if line.confidence is not None]
    return OcrLine(
        text=" ".join(line.text for _, line in inside),
        box=(0, 0, 0, 0),
        confidence=round(sum(scores) / len(scores), 1) if scores else None,
    )
