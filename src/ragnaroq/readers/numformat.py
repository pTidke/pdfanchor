"""A cell's value as Excel shows it, from the value and its number format.

`render` covers what the files we read use: General and the 0 / #,##0 families, percent,
sections, [>=n] conditions, quoted and escaped literals (an escaped comma between digit
placeholders gives Indian grouping), [$₹-4009] currency tags, the common date tokens and @.
Anything else returns None, and the caller keeps the stored value instead of guessing."""

import re
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from decimal import ROUND_HALF_UP, Decimal

_CONDITION = re.compile(r"(<=|>=|<>|<|>|=)\s*(-?\d+(?:\.\d+)?)$")
_COLOR = re.compile(r"(black|blue|cyan|green|magenta|red|white|yellow|color\s*\d+)$", re.I)
_DATE_TOKEN = re.compile(r"yyyy|yy|mmmmm|mmmm|mmm|mm|m|dddd|ddd|dd|d|hh|h|ss|s|am/pm|a/p", re.I)
_PLAIN_LITERALS = " $-+()/:!^&'~{}<>="
_MONTHS = [
    "January",
    "February",
    "March",
    "April",
    "May",
    "June",
    "July",
    "August",
    "September",
    "October",
    "November",
    "December",
]
_DAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
_COMPARE = {
    "<": lambda a, b: a < b,
    "<=": lambda a, b: a <= b,
    ">": lambda a, b: a > b,
    ">=": lambda a, b: a >= b,
    "=": lambda a, b: a == b,
    "<>": lambda a, b: a != b,
}


class _Unknown(Exception):
    """The format uses something render does not handle."""


@dataclass
class _Section:
    tokens: list = field(default_factory=list)  # (kind, text): lit, ph, dot, comma, pct, at, date
    condition: tuple[str, Decimal] | None = None


def _split(fmt: str) -> list[str]:
    """Sections, split on ';' outside quotes, brackets and escapes."""
    parts, current, i = [], "", 0
    while i < len(fmt):
        char = fmt[i]
        if char in "\\_*" and i + 1 < len(fmt):
            current += fmt[i : i + 2]
            i += 2
            continue
        if char == '"':
            end = fmt.find('"', i + 1)
            end = len(fmt) - 1 if end < 0 else end
            current += fmt[i : end + 1]
            i = end + 1
            continue
        if char == "[":
            end = fmt.find("]", i)
            end = len(fmt) - 1 if end < 0 else end
            current += fmt[i : end + 1]
            i = end + 1
            continue
        if char == ";":
            parts.append(current)
            current = ""
        else:
            current += char
        i += 1
    parts.append(current)
    return parts


def _parse(text: str) -> _Section:
    section, i = _Section(), 0
    while i < len(text):
        char = text[i]
        if char == '"':
            end = text.find('"', i + 1)
            if end < 0:
                raise _Unknown
            section.tokens.append(("lit", text[i + 1 : end]))
            i = end + 1
        elif char == "\\" and i + 1 < len(text):
            section.tokens.append(("lit", text[i + 1]))
            i += 2
        elif char == "_" and i + 1 < len(text):
            section.tokens.append(("lit", " "))
            i += 2
        elif char == "*" and i + 1 < len(text):
            i += 2
        elif char == "[":
            end = text.find("]", i)
            if end < 0:
                raise _Unknown
            inner = text[i + 1 : end]
            if inner.startswith("$"):
                section.tokens.append(("lit", inner[1:].split("-", 1)[0]))
            elif match := _CONDITION.match(inner):
                section.condition = (match.group(1), Decimal(match.group(2)))
            elif not _COLOR.match(inner):
                raise _Unknown
            i = end + 1
        elif char in "0#?":
            section.tokens.append(("ph", char))
            i += 1
        elif char in ".,%@":
            section.tokens.append(({".": "dot", ",": "comma", "%": "pct", "@": "at"}[char], char))
            i += 1
        elif char in _PLAIN_LITERALS:
            section.tokens.append(("lit", char))
            i += 1
        elif match := _DATE_TOKEN.match(text, i):
            section.tokens.append(("date", match.group().lower()))
            i = match.end()
        else:
            raise _Unknown
    return section


def _general(value) -> str:
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, (float, Decimal)):
        value = float(value)
        if value.is_integer() and abs(value) < 1e15:
            return str(int(value))
        shown = f"{value:.15g}"
        if "e" in shown:
            raise _Unknown
        return shown
    if isinstance(value, str):
        return value
    raise _Unknown


def _picked(sections: list[_Section], number: Decimal) -> tuple[_Section, bool]:
    """The section for this number, and whether it is the one made for negatives (which shows
    the number without a sign)."""
    if any(s.condition for s in sections):
        for section in sections:
            if section.condition is None or _COMPARE[section.condition[0]](number, section.condition[1]):
                return section, False
        return sections[-1], False
    if len(sections) == 1 or number > 0 or (number == 0 and len(sections) == 2):
        return sections[0], False
    if number < 0:
        return sections[1], True
    return sections[2], False


def _group(digits: str) -> str:
    head = len(digits) % 3 or 3
    return ",".join([digits[:head], *(digits[i : i + 3] for i in range(head, len(digits), 3))])


def _integer(items: list, number: int) -> str:
    """Digits laid into the integer placeholders from the right; literals between them show
    only while digits or zeros stand to their left."""
    placeholders = [i for i, (kind, _) in enumerate(items) if kind == "ph"]
    grouped = any(kind == "comma" for kind, _ in items)
    if not placeholders:
        return ""
    first, last = placeholders[0], placeholders[-1]
    inside = items[first : last + 1]
    if any(kind == "ph" and char == "?" for kind, char in inside):
        raise _Unknown
    if grouped and any(kind == "lit" for kind, _ in inside):
        raise _Unknown
    if any(kind not in ("ph", "lit", "comma") for kind, _ in inside):
        raise _Unknown
    if any(kind == "comma" for kind, _ in items[last + 1 :]):
        raise _Unknown  # a trailing comma scales by a thousand
    digits = "" if number == 0 else str(number)
    pieces, zeros_left = [], sum(1 for kind, char in inside if kind == "ph" and char == "0")
    for kind, char in reversed(inside):
        if kind == "ph":
            if char == "0":
                zeros_left -= 1
            if digits:
                pieces.append(digits[-1])
                digits = digits[:-1]
            elif char == "0":
                pieces.append("0")
        elif kind == "lit" and (digits or zeros_left):
            pieces.append(char)
    core = digits + "".join(reversed(pieces))
    if grouped:
        core = _group(core)
    return core


def _number(section: _Section, number: Decimal, negative: bool) -> str:
    kinds = [kind for kind, _ in section.tokens]
    if "date" in kinds or kinds.count("dot") > 1:
        raise _Unknown
    if any(kind == "lit" and char == "/" for kind, char in section.tokens) and any(
        kind == "ph" and char == "?" for kind, char in section.tokens
    ):
        raise _Unknown  # a fraction
    minus = "-" if number < 0 and not negative else ""
    number = abs(number) if negative else number
    number *= Decimal(100) ** kinds.count("pct")
    dot = kinds.index("dot") if "dot" in kinds else len(kinds)
    decimals = [char for kind, char in section.tokens[dot:] if kind == "ph"]
    if any(kind == "comma" for kind, _ in section.tokens[dot:]):
        raise _Unknown
    rounded = number.quantize(Decimal(1).scaleb(-len(decimals)), rounding=ROUND_HALF_UP)
    whole, _, fraction = f"{abs(rounded):f}".partition(".")
    fraction = list(fraction.ljust(len(decimals), "0"))
    for index in range(len(decimals) - 1, -1, -1):
        if decimals[index] == "0" or fraction[index] != "0":
            break
        fraction[index] = "" if decimals[index] == "#" else " "
    integer_phs = [i for i, (kind, _) in enumerate(section.tokens[:dot]) if kind == "ph"]
    span = range(integer_phs[0], integer_phs[-1] + 1) if integer_phs else range(0)
    out, i = [], 0
    while i < len(section.tokens):
        kind, text = section.tokens[i]
        if i in span:
            if i == span.start:
                out.append(_integer(section.tokens[:dot], int(whole)))
        elif kind == "ph":
            pass
        elif kind == "comma" and i < dot:
            raise _Unknown  # a comma outside the digits scales by a thousand
        elif kind == "dot":
            out.append("." + "".join(fraction))
        elif kind == "at":
            out.append(_general(number))
        elif kind == "pct":
            out.append("%")
        elif kind == "lit":
            out.append(text)
        else:
            raise _Unknown
        i += 1
    return minus + "".join(out)


def _moment(value) -> datetime:
    if isinstance(value, datetime):
        return value
    if isinstance(value, date):
        return datetime(value.year, value.month, value.day)
    if isinstance(value, time):
        return datetime(1900, 1, 1, value.hour, value.minute, value.second)
    return datetime(1899, 12, 30) + timedelta(days=float(value))


def _date(section: _Section, value) -> str:
    if any(kind in ("ph", "dot", "pct", "at", "comma") for kind, _ in section.tokens):
        raise _Unknown
    moment = _moment(value)
    dated = [i for i, (kind, _) in enumerate(section.tokens) if kind == "date"]
    meridian = any(section.tokens[i][1] in ("am/pm", "a/p") for i in dated)
    out = []
    for index, (kind, text) in enumerate(section.tokens):
        if kind == "lit":
            out.append(text)
            continue
        if text in ("m", "mm"):
            at = dated.index(index)
            before = section.tokens[dated[at - 1]][1] if at else ""
            after = section.tokens[dated[at + 1]][1] if at + 1 < len(dated) else ""
            if before in ("h", "hh") or after in ("s", "ss"):
                out.append(f"{moment.minute:0{len(text)}d}")
                continue
        hour = (moment.hour % 12 or 12) if meridian else moment.hour
        out.append(
            {
                "yyyy": f"{moment.year:04d}",
                "yy": f"{moment.year % 100:02d}",
                "m": str(moment.month),
                "mm": f"{moment.month:02d}",
                "mmm": _MONTHS[moment.month - 1][:3],
                "mmmm": _MONTHS[moment.month - 1],
                "mmmmm": _MONTHS[moment.month - 1][0],
                "d": str(moment.day),
                "dd": f"{moment.day:02d}",
                "ddd": _DAYS[moment.weekday()][:3],
                "dddd": _DAYS[moment.weekday()],
                "h": str(hour),
                "hh": f"{hour:02d}",
                "s": str(moment.second),
                "ss": f"{moment.second:02d}",
                "am/pm": "AM" if moment.hour < 12 else "PM",
                "a/p": "A" if moment.hour < 12 else "P",
            }[text]
        )
    return "".join(out)


def render(value, number_format: str) -> str | None:
    """`value` as a cell with `number_format` shows it, or None when the format is not one
    this module knows. A text value is shown as it is unless the format has an @ section."""
    try:
        if number_format.strip().lower() == "general":
            return _general(value)
        sections = [_parse(part) for part in _split(number_format)]
        if isinstance(value, str):
            for section in sections:
                if any(kind == "at" for kind, _ in section.tokens):
                    return "".join(value if kind == "at" else text for kind, text in section.tokens)
            return value
        if isinstance(value, bool):
            return _general(value)
        if isinstance(value, (datetime, date, time)):
            return _date(sections[0], value)
        number = Decimal(str(value))
        section, negative = _picked(sections, number)
        if any(kind == "date" for kind, _ in section.tokens):
            return _date(section, value)
        return _number(section, number, negative)
    except (_Unknown, KeyError, ArithmeticError, ValueError, TypeError):
        return None
