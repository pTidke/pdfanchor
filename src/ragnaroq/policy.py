"""The decision policy. This is the one place that decides what a person must look at.

Each line ends as:
  accepted  every rule passed; no person needed (audit a sample)
  flagged   a rule wants a person to compare it with the page
  blocked   unusable until something is fixed

Every outcome other than "accepted" carries the rule that caused it.
"""

import re
import unicodedata

from . import validators
from .config import Policy
from .crosscheck import (
    LAYOUT_MARKS,
    LEADERS,
    NEGATIONS,
    TYPOGRAPHY,
    canonical,
    compare,
    critical_tokens,
    letters,
    shares_words,
    without_marks,
)
from .model import Document, Figure, Flag, Line, Resolution

# Text read straight from a file's content: no second reader exists, and none is needed.
EXACT_SOURCES = {"docx", "xlsx", "pptx", "html", "md", "json", "xml", "csv"}
# Rules a reader of a file's content sets on a line itself; deciding again keeps them.
READER_RULES = tuple(f"{source}." for source in sorted(EXACT_SOURCES))
VLM = "vlm:"  # sources starting with this are third readings (a local vision model)
THIRD = "crosscheck.third-reader"
# Flags the third reader may look past: the disagreement it settles, and a lone "|",
# which its own checks refuse to keep (a settled line never carries one).
SETTLEABLE = {"crosscheck.disagreement", "glyph.lone-bar"}


def settleable(line: Line) -> bool:
    """A flagged line whose only flags are ones the third reader may settle, at least
    one of them the disagreement: a bar both readers agree on goes to a person."""
    rules = {flag.rule for flag in line.flags}
    return line.status == "flagged" and "crosscheck.disagreement" in rules and rules <= SETTLEABLE


def _flag(line: Line | Figure, rule: str, message: str, severity="review") -> None:
    line.flags.append(Flag(rule=rule, severity=severity, message=message))


def decide_line(line: Line, policy: Policy, image_backed: bool) -> None:
    exact = line.source in EXACT_SOURCES
    line.flags.extend(validators.run(line.text, ocr=not exact and line.source != "native"))
    others = [r for r in line.readings if r.source != line.source and not r.source.startswith(VLM)]
    if line.role == "table" and not exact:
        _flag(line, "table.member", "Part of a table; table structure is never accepted by rule.")
    if others:
        for other in others:
            result = compare(line.text, other.text)
            if not result.agrees(policy.min_similarity, policy.same_letters):
                if not result.critical_equal:
                    detail = f"differs on {', '.join(result.only_first + result.only_second)}"
                elif result.similarity < policy.min_similarity:
                    detail = f"similarity {result.similarity}"
                else:
                    detail = "differs in letters"
                _flag(
                    line,
                    "crosscheck.disagreement",
                    f'{other.source} reads "{other.text}" ({detail}).',
                )
    elif exact:
        pass
    elif line.source == "native":
        if image_backed or not policy.accept_native_without_crosscheck:
            _flag(line, "crosscheck.not-run", "No independent reading was available to confirm it.")
    elif not policy.accept_single_ocr_reading:
        _flag(line, "ocr.single-reading", "Read by one OCR engine only.")
    if any(flag.severity == "blocking" for flag in line.flags):
        line.status = "blocked"
    else:
        line.status = "flagged" if line.flags else "accepted"


def _exact_critical(text: str):
    """Critical tokens without compatibility folding: "80¹" is not "801"."""
    return critical_tokens(LEADERS.sub(" ", text.translate(TYPOGRAPHY)), fold=False)


def _numbers(text: str):
    """The critical tokens made only of digits and number punctuation."""
    return [t for t in _exact_critical(text).elements() if re.fullmatch(r"[\d,.\-/%$()]*\d[\d,.\-/%$()]*", t)]


def _third_reader(line: Line, neighbours: list[str], policy: Policy) -> None:
    """Settle a disputed scanned line when the third reading matches one OCR reading
    exactly (layout marks, case, spacing and typography aside). The first refusal adds
    its flag and stops: a number, date or negation is never taken on trust."""
    vlm = next(r for r in line.readings if r.source.startswith(VLM))
    seen = without_marks(vlm.text)
    # NFC, as `text` would carry it, so the validators judge the text that is kept.
    candidates = [(line.source, unicodedata.normalize("NFC", line.raw))] + [
        (r.source, unicodedata.normalize("NFC", r.text))
        for r in line.readings
        if r.source != line.source and not r.source.startswith(VLM)
    ]
    head = f'{vlm.source} reads "{seen}"'

    def refuse(name: str, message: str) -> None:
        _flag(line, f"{THIRD}.{name}", f"{head}; {message}")

    wanted, wanted_critical = canonical(seen), _exact_critical(seen)
    match = next(
        (
            (src, t)
            for src, t in candidates
            if letters(t) and canonical(t) == wanted and _exact_critical(t) == wanted_critical
        ),
        None,
    )
    if match is None or not letters(seen):
        refuse("no-match", "no other reading matches it exactly.")
        return
    source, text = match
    # Each reading is checked as the reader gave it and as `text` would carry it (NFC):
    # normalising must never hide what a validator would flag.
    given = {line.source: line.raw} | {r.source: r.text for r in line.readings if r.source != line.source}
    forms = {(source, text), (source, given[source]), (vlm.source, seen)}
    forms.add((vlm.source, unicodedata.normalize("NFC", seen)))
    fired = sorted({(who, f.rule) for who, t in forms for f in validators.run(t, ocr=True)})
    if fired:
        names = ", ".join(f"{rule} on {who}" for who, rule in fired)
        refuse("validators", f'agrees with {source} on "{text}", but a validator flags it ({names}).')
        return
    size = max(len(letters(text)), len(letters(seen)))
    limit = policy.third_reader_letter_ratio * size
    own = _exact_critical(text)
    marked = LAYOUT_MARKS.search(vlm.text) is not None
    for other, other_text in candidates:
        if canonical(other_text) == wanted:
            continue
        theirs = _exact_critical(other_text)
        if len(letters(other_text)) > limit:
            why = "has more text"
        elif any(w in NEGATIONS and w not in own for w in theirs):
            why = "has a negation"
        elif marked and len(_numbers(other_text)) > len(_numbers(text)):
            why = "has a number where a mark was removed"
        else:
            continue
        refuse("omission", f'{other} reads "{other_text}", which {why} that {source} does not.')
        return
    for who, t in [(vlm.source, seen), *candidates]:
        if re.search(r"[^\W_]_[^\W_]", t):
            refuse("underscore", f"{who} has an underscore inside a word; it may be a misread character.")
            return
    if any(shares_words(text, n, policy.third_reader_neighbour_words) for n in neighbours):
        refuse("neighbour", f'"{text}" shares words with a neighbouring line; it may run into it.')
        return
    line.flags = []
    line.status = "accepted"
    line.text = unicodedata.normalize("NFC", text)
    line.resolution = Resolution(rule=THIRD, agreed=[source, vlm.source], text_from=source)


def _neighbours(ordered: list[Line], i: int) -> list[Line]:
    """The lines a line's box may have run into: on its page, the nearest line above and
    the nearest below that overlap it horizontally. The lines just before and after it in
    reading order are kept too, as before, so this check only ever adds refusals."""
    box = ordered[i].box
    found = ordered[max(i - 1, 0) : i] + ordered[i + 1 : i + 2]
    if box is None:
        return found
    column = [
        n for j, n in enumerate(ordered) if j != i and n.box and n.box.x0 < box.x1 and box.x0 < n.box.x1
    ]
    above = [n for n in column if n.box.bottom <= box.top + 1]
    below = [n for n in column if n.box.top >= box.bottom - 1]
    if above:
        found.append(min(above, key=lambda n: box.top - n.box.bottom))
    if below:
        found.append(min(below, key=lambda n: n.box.top - box.bottom))
    return found


def _decide_lines(lines: list[Line], scanned: bool, image_backed: bool, policy: Policy) -> None:
    ordered = sorted(lines, key=lambda line: line.order)
    for i, line in enumerate(ordered):
        if line.excluded:
            line.status = "accepted"
            continue
        decide_line(line, policy, image_backed)
        if scanned and any(r.source.startswith(VLM) for r in line.readings) and settleable(line):
            near = _neighbours(ordered, i)
            _third_reader(line, [unicodedata.normalize("NFC", n.raw) for n in near], policy)


def _decide_figure(figure: Figure, policy: Policy) -> None:
    """A figure is accepted when its description is the file's own (caption or alt text) or
    every line OCR read from it is accepted. `model_description` is never consulted."""
    for line in figure.ocr_lines:
        line.text = unicodedata.normalize("NFC", line.raw)
        line.resolution = None
        line.flags = []
    figure.flags = []
    _decide_lines(figure.ocr_lines, True, False, policy)
    if figure.description_source == "ocr" and figure.ocr_lines:
        if any(line.status != "accepted" for line in figure.ocr_lines):
            _flag(figure, "image.ocr-unverified", "Text read from the image is not settled; compare it.")
    elif figure.description_source not in {"caption", "alt"} or not figure.description:
        _flag(figure, "image.no-description", "The image has no description from the file.")
    figure.status = "flagged" if figure.flags else "accepted"


def decide(document: Document, policy: Policy) -> None:
    """Decide every line and table. Only this decides, so any earlier verdict is
    cleared first: a stored reading can be decided again under new rules."""
    for page in document.pages:
        image_backed = any(flag.rule == "native.image-backed" for flag in page.flags)
        for table in page.tables:
            table.flags = []
        for line in page.lines:
            line.text = unicodedata.normalize("NFC", line.raw)
            line.resolution = None
            line.flags = [f for f in line.flags if f.rule.startswith(READER_RULES)]
        for table in page.tables:
            members = [line for line in page.lines if line.table_id == table.id]
            if members and all(line.source in EXACT_SOURCES for line in members):
                continue  # its status follows its lines, below
            table.flags.append(
                Flag(
                    rule="table.structure-unverified",
                    severity="review",
                    message="Check headers, merged cells, units and footnotes against the page.",
                )
            )
            table.status = "flagged"
        _decide_lines(page.lines, page.kind == "scanned", image_backed, policy)
        for table in page.tables:
            if not table.flags:
                states = {line.status for line in page.lines if line.table_id == table.id}
                table.status = (
                    "blocked" if "blocked" in states else "flagged" if "flagged" in states else "accepted"
                )
        for figure in page.figures:
            _decide_figure(figure, policy)
    figures = [figure for page in document.pages for figure in page.figures]
    counted = [line for line in document.lines() if not line.excluded]
    document.summary = {
        "pages": len(document.pages),
        "pages_failed": sum(page.kind == "failed" for page in document.pages),
        "pages_scanned": sum(page.kind == "scanned" for page in document.pages),
        "lines": len(counted),
        "accepted": sum(line.status == "accepted" for line in counted),
        "flagged": sum(line.status == "flagged" for line in counted),
        "blocked": sum(line.status == "blocked" for line in counted),
        "excluded": sum(1 for line in document.lines() if line.excluded),
        "tables": sum(len(page.tables) for page in document.pages),
        "figures": len(figures),
        "figures_flagged": sum(f.status == "flagged" for f in figures),
        "figures_blocked": sum(f.status == "blocked" for f in figures),
    }


def needs_review(document: Document) -> bool:
    return bool(
        document.summary.get("flagged")
        or document.summary.get("blocked")
        or document.summary.get("tables")
        or document.summary.get("figures_flagged")
        or document.summary.get("figures_blocked")
        or any(flag.severity == "blocking" for flag in document.flags)
        or any(flag.severity == "blocking" for page in document.pages for flag in page.flags)
    )
