"""Finders for dates, periods, amounts, references and file names in one string.

Each finder is a pure function: it returns `Match(text, value, start, end)` sorted by
`start`, with no overlaps. Every pattern is linear: no nested quantifiers, and each one is
anchored by a lookbehind so a long run of digits or dots is scanned once.
"""

import re
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from ..validators.dates import MONTHS


@dataclass(frozen=True)
class Match:
    text: str
    value: str | dict
    start: int
    end: int


def _keep(found: list[Match]) -> list[Match]:
    """Sort by start (longer first on a tie) and drop any match that overlaps an earlier one."""
    kept: list[Match] = []
    for m in sorted(found, key=lambda m: (m.start, -(m.end - m.start))):
        if not kept or m.start >= kept[-1].end:
            kept.append(m)
    return kept


def _match(m: re.Match, value: str | dict) -> Match:
    return Match(m.group(0), value, m.start(), m.end())


# --- dates ---------------------------------------------------------------------------------

_MONTH_NUMBER = {
    name: number
    for number, name in enumerate(
        ("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"), 1
    )
}
_ORDINAL = r"(?:st|nd|rd|th)?"
_DAY_MONTH_YEAR = re.compile(
    rf"(?<![\w/.-])(?P<day>\d{{1,2}}){_ORDINAL}\s*,?\s+(?:day\s+of\s+)?(?P<month>{MONTHS})\b\.?"
    r"\s*,?\s*(?P<year>\d{4})(?!\d)",
    re.IGNORECASE,
)
_MONTH_DAY_YEAR = re.compile(
    rf"(?<!\w)(?P<month>{MONTHS})\b\.?\s+(?P<day>\d{{1,2}}){_ORDINAL}\s*,?\s*(?P<year>\d{{4}})(?!\d)",
    re.IGNORECASE,
)
_ISO = re.compile(r"(?<![\d/.-])(?P<year>\d{4})-(?P<month>\d{2})-(?P<day>\d{2})(?![\d-])")
_NUMERIC = re.compile(
    r"(?<![\d/.-])(?P<a>\d{1,2})(?P<sep>[/.-])(?P<b>\d{1,2})(?P=sep)(?P<year>\d{4})(?![\d/])"
)


def _iso(year: int, month: int, day: int) -> str:
    try:
        return date(year, month, day).isoformat()
    except ValueError:
        return ""


def dates(text: str) -> list[Match]:
    found = []
    for m in _DAY_MONTH_YEAR.finditer(text):
        month = _MONTH_NUMBER[m["month"][:3].lower()]
        found.append(_match(m, _iso(int(m["year"]), month, int(m["day"]))))
    for m in _MONTH_DAY_YEAR.finditer(text):
        month = _MONTH_NUMBER[m["month"][:3].lower()]
        found.append(_match(m, _iso(int(m["year"]), month, int(m["day"]))))
    for m in _ISO.finditer(text):
        found.append(_match(m, _iso(int(m["year"]), int(m["month"]), int(m["day"]))))
    for m in _NUMERIC.finditer(text):
        first, second, year = int(m["a"]), int(m["b"]), int(m["year"])
        if first > 12 and second <= 12:
            value = _iso(year, second, first)
        elif second > 12 and first <= 12:
            value = _iso(year, first, second)
        elif first == second and first <= 12:
            value = _iso(year, first, second)
        else:
            value = ""
        found.append(_match(m, value))
    return _keep(found)


# --- periods -------------------------------------------------------------------------------

_SPAN = r"(?P<first>(?:19|20)\d{2}|2100)\s?[-–]\s?(?P<second>\d{4}|\d{2})(?![\d/-])"
_FISCAL = re.compile(
    rf"(?<![\w/.-])(?P<kind>FY|AY|F\.Y\.|A\.Y\.|(?i:financial\s+year|assessment\s+year))\s*{_SPAN}"
)
_BARE_SPAN = re.compile(rf"(?<![\d/.-]){_SPAN}")
_QUARTER = re.compile(r"(?<![\w/.-])(?P<part>[QH][1-4])(?:\s+|-|,\s*)(?P<year>(?:19|20)\d{2})(?![\d/-])")
_YEAR_THEN_PART = re.compile(r"(?<![\d/.-])(?P<year>(?:19|20)\d{2})[\s-](?P<part>[QH][1-4])\b")
_CALENDAR = re.compile(r"(?<!\w)(?i:calendar\s+year)\s+(?P<year>(?:19|20)\d{2})(?![\d/-])")


def _next_year(m: re.Match) -> int | None:
    """The span's first year when its second part is the next year (two or four digits)."""
    first, second = m["first"], m["second"]
    start = int(first)
    if len(second) == 4:
        return start if int(second) == start + 1 else None
    return start if int(second) == (start + 1) % 100 else None


def periods(text: str) -> list[Match]:
    found = []
    for m in _FISCAL.finditer(text):
        year = _next_year(m)
        if year is not None:
            found.append(_match(m, f"{year}-04-01/{year + 1}-03-31"))
    for m in _BARE_SPAN.finditer(text):
        year = _next_year(m)
        if year is not None:
            found.append(_match(m, f"{year}/{year + 1}"))
    for m in _QUARTER.finditer(text):
        found.append(_match(m, f"{m['year']}-{m['part']}"))
    for m in _YEAR_THEN_PART.finditer(text):
        found.append(_match(m, f"{m['year']}-{m['part']}"))
    for m in _CALENDAR.finditer(text):
        found.append(_match(m, f"{m['year']}/{m['year']}"))
    return _keep(found)


# --- amounts -------------------------------------------------------------------------------

_CURRENCIES = {
    "₹": "INR",
    "rs.": "INR",
    "rs": "INR",
    "inr": "INR",
    "$": "USD",
    "usd": "USD",
    "€": "EUR",
    "eur": "EUR",
    "£": "GBP",
    "gbp": "GBP",
}
_SCALES = {
    "lakh": 100_000,
    "lakhs": 100_000,
    "lac": 100_000,
    "lacs": 100_000,
    "crore": 10_000_000,
    "crores": 10_000_000,
    "million": 1_000_000,
    "billion": 1_000_000_000,
}
# Western grouping first (1,500,000), then Indian (1,50,000), then plain digits.
_NUMBER = r"(?<![\d,])(?<!\d\.)(?P<num>\d{1,3}(?:,\d{3})+|\d{1,2}(?:,\d{2})*,\d{3}|\d+)(?P<frac>\.\d+)?"
_SYMBOL = r"₹|\$|€|£|(?i:rs\.?|inr|usd|eur|gbp)(?![A-Za-z])"
_CODE = r"(?i:rs|inr|usd|eur|gbp)(?![A-Za-z\d])"
_AMOUNT = re.compile(
    rf"(?:(?P<pre>{_SYMBOL})\s*)?{_NUMBER}"
    rf"(?:\s*(?P<scale>(?i:lakhs?|lacs?|crores?|million|billion))\b)?"
    rf"(?:\s*(?P<post>{_CODE}))?"
)
_PERCENT = re.compile(rf"{_NUMBER}\s*(?:%|(?i:per\s*cent\b|percent\b))")


def _decimal(text: str) -> Decimal:
    return Decimal(text.replace(",", ""))


def _plain(value: Decimal) -> int | float:
    return int(value) if value == value.to_integral_value() else float(value)


def amounts(text: str) -> list[Match]:
    found = []
    for m in _AMOUNT.finditer(text):
        currency = m["pre"] or m["post"]
        if currency is None:
            continue
        number = m["num"] + (m["frac"] or "")
        value = _decimal(number) * _SCALES.get((m["scale"] or "").lower(), 1)
        code = _CURRENCIES[currency.lower()]
        found.append(_match(m, {"amount": _plain(value), "currency": code}))
    for m in _PERCENT.finditer(text):
        number = m["num"] + (m["frac"] or "")
        found.append(_match(m, {"percent": _plain(_decimal(number))}))
    return _keep(found)


# --- references ----------------------------------------------------------------------------

_UNITS = {
    "section": "section",
    "sections": "section",
    "sec": "section",
    "s": "section",
    "article": "article",
    "articles": "article",
    "art": "article",
    "rule": "rule",
    "rules": "rule",
    "clause": "clause",
    "clauses": "clause",
    "regulation": "regulation",
    "regulations": "regulation",
    "reg": "regulation",
    "paragraph": "paragraph",
    "paragraphs": "paragraph",
    "para": "paragraph",
    "chapter": "chapter",
    "chapters": "chapter",
    "part": "part",
    "schedule": "schedule",
}
_UNIT_WORD = r"(?i:sections?|sec\.?|s\.|articles?|art\.?|rules?|clauses?|regulations?|reg\.?|paragraphs?|para\.?|chapters?|part|schedule)"
_UNIT_NUMBER = (
    r"(?:\d+(?:-?[A-Za-z](?![A-Za-z\d]))?(?:\([A-Za-z0-9]{1,4}\))*"
    r"|\([A-Za-z0-9]{1,4}\)"
    r"|(?-i:[IVXLC]+)(?![A-Za-z\d]))"
)
_UNIT_REF = re.compile(rf"(?<![\w'’./-])(?P<unit>{_UNIT_WORD})\s*(?P<number>{_UNIT_NUMBER})(?![\w])")
_INSTRUMENT = re.compile(
    r"(?<![\w'’./-])(?P<kind>(?i:notification|circular|order))\s+(?:(?i:no)\.?\s*)?"
    r"(?P<number>\d{1,6}/\d{2,4})(?![\w/])"
)
_ACT = re.compile(r"(?<![\w-])Act,?\s+(?P<year>(?:18|19|20)\d{2})(?!\d)")
_URL = re.compile(r"(?<![\w.@/-])(?:https?://|www\.)[^\s<>\"'“”]+", re.IGNORECASE)
_CONNECTORS = {"of", "and", "for", "to", "the"}
_ACT_WORDS = 8


def _unit_ref(m: re.Match) -> Match | None:
    unit = m["unit"].lower().rstrip(".")
    if unit == "s" and not m["unit"].endswith("."):
        return None
    return _match(m, f"{_UNITS[unit]} {m['number']}")


def _acts(text: str) -> list[Match]:
    found = []
    for m in _ACT.finditer(text):
        before = text[max(0, m.start() - 160) : m.start()]
        words = list(re.finditer(r"[^\s]+", before))[-_ACT_WORDS:]
        first = None  # index of the earliest word of the Act's name
        for i in range(len(words) - 1, -1, -1):
            word = words[i].group(0)
            if re.fullmatch(r"[A-Z][\w'’-]*", word) and word != "The":
                first = i
            elif word in ("of", "and", "for", "to") or (
                word == "the" and i > 0 and words[i - 1].group(0) == "of"
            ):
                continue
            else:
                break
        if first is None:
            continue
        gap = before[words[first].start() :]
        start = m.start() - len(gap)
        name = " ".join(gap.split())
        found.append(Match(text[start : m.end()], f"{name.lower()} act, {m['year']}", start, m.end()))
    return found


def references(text: str) -> list[Match]:
    found = []
    for m in _UNIT_REF.finditer(text):
        ref = _unit_ref(m)
        if ref:
            found.append(ref)
    for m in _INSTRUMENT.finditer(text):
        found.append(_match(m, f"{m['kind'].lower()} {m['number']}"))
    found.extend(_acts(text))
    for m in _URL.finditer(text):
        url = m.group(0).rstrip(".,;:!?)]}")
        found.append(Match(url, url, m.start(), m.start() + len(url)))
    return _keep(found)


# --- file mentions -------------------------------------------------------------------------

_FILE = re.compile(
    r"(?<![\w./-])[\w.-]+(?:/[\w.-]+)*\.(?:pdf|docx|xlsx|pptx|html|htm|md|json|xml|csv|tsv)(?!\w)",
    re.IGNORECASE,
)


def file_mentions(text: str) -> list[Match]:
    return _keep([_match(m, "") for m in _FILE.finditer(text)])
