"""Marks a reader writes where the page has a glyph it could not tell apart. A "|"
standing alone in text read from an image is a printed "1", "I" or "l" both readers
mistook, or a box or table border read as text. Either way the text is not what the
page says, and two readers often make the same mistake, so their agreement does not
catch it. The rule flags it; it never rewrites the text.
"""

import re

from ..model import Flag

LONE_BAR = re.compile(r"(?:^|(?<=\s))\|(?=\s|$)")


def check_marks(text: str) -> list[Flag]:
    if not LONE_BAR.search(text):
        return []
    return [
        Flag(
            rule="glyph.lone-bar",
            severity="review",
            message='A "|" stands alone: it may be a misread "1", "I" or "l", or a border read as text.',
        )
    ]
