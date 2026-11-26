"""Dates written as "day month year" are where OCR errors do the most damage and where
engine confidence helps least: `31st` read as `31*`, `01st` read as `015`, both with high
confidence. These rules flag the shape of the error. They never rewrite the date.
"""

import re

from ..model import Flag

MONTHS = (
    "january|february|march|april|may|june|july|august|september|october|november|december"
    "|jan|feb|mar|apr|jun|jul|aug|sep|sept|oct|nov|dec"
)
# A run of digits, whatever is glued to it, then a month name.
DATE = re.compile(
    rf"(?<![\w/.-])(?P<day>\d{{1,4}})(?P<suffix>[^\s,\d]{{0,4}})\s*,?\s+(?:day\s+of\s+)?"
    rf"(?P<month>{MONTHS})\b\.?(?:\s*,?\s*(?P<year>\d{{2,5}})\b)?",
    re.IGNORECASE,
)
SUFFIXES = {"st", "nd", "rd", "th"}
# A day where a reader put a letter for a digit: "Sth January" for "5th", "lst" for "1st",
# "3Oth" for "30th". DATE needs a digit first, so it never sees these; two readers often
# make the same confusion, so their agreement does not catch it either.
CONFUSED = "SOoIlBZGQD"
CONFUSABLE_DAY = re.compile(
    rf"(?<![\w/.-])(?=[0-9]?[{CONFUSED}])(?P<day>[0-9{CONFUSED}]{{1,2}})(?:st|nd|rd|th)\s*,?\s+"
    rf"(?:day\s+of\s+)?(?i:{MONTHS})\b"
)


def expected_suffix(day: int) -> str:
    if 10 <= day % 100 <= 20:
        return "th"
    return {1: "st", 2: "nd", 3: "rd"}.get(day % 10, "th")


def check_dates(text: str) -> list[Flag]:
    flags = []
    for match in DATE.finditer(text):
        shown = match.group(0)
        day, suffix, year = match.group("day"), match.group("suffix").lower(), match.group("year")
        if len(day) > 2 or not 1 <= int(day) <= 31:
            flags.append(
                Flag(
                    rule="date.day-out-of-range",
                    severity="review",
                    message=f'"{shown}": the day is not between 1 and 31. '
                    "A suffix may have been read as a digit.",
                )
            )
        elif suffix and suffix not in SUFFIXES:
            flags.append(
                Flag(
                    rule="date.ordinal-suffix",
                    severity="review",
                    message=f'"{shown}": "{match.group("suffix")}" is not st, nd, rd or th.',
                )
            )
        elif suffix and suffix != expected_suffix(int(day)):
            flags.append(
                Flag(
                    rule="date.ordinal-mismatch",
                    severity="review",
                    message=f'"{shown}": the suffix does not match the day. '
                    "It may be a misread digit, or printed that way.",
                )
            )
        if year and not (len(year) == 2 or 1900 <= int(year) <= 2100):
            flags.append(
                Flag(
                    rule="date.year-implausible",
                    severity="review",
                    message=f'"{shown}": the year is outside 1900 to 2100.',
                )
            )
    for match in CONFUSABLE_DAY.finditer(text):
        flags.append(
            Flag(
                rule="date.confusable-day",
                severity="review",
                message=f'"{match.group(0)}": the day "{match.group("day")}" holds a letter readers '
                "take for a digit.",
            )
        )
    return flags
