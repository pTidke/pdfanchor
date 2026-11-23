"""Codes that mix letters and digits: form and catalogue numbers, PANs and TANs,
section and clause references. Inside them readers confuse O with 0, I and l with 1,
S with 5, B with 8, Z with 2, G with 6 and Q or D with 0, and two different readers
often make the same confusion, so their agreement does not catch it. These rules
flag the shape of the risk. They never rewrite the code.
"""

import re

from ..model import Flag

# Letters a reader can take for a digit, or a digit for.
CONFUSABLE = set("OoIlSBZGQD")
EDGE = ".,;:'\"“”‘’!?"
ORDINAL = re.compile(r"\d+(st|nd|rd|th)", re.IGNORECASE)
# Web addresses mix letters and digits by design (IRS.gov/Form1040).
ADDRESS = re.compile(r"(\.(gov|com|org|net|in|nic)\b|www\.|https?:|@)", re.IGNORECASE)
# Parts of a compound are read separately: "1040-SR" is a number and a suffix, not a
# code that mixes them; "R28C0" and "11320B" are.
JOINS = re.compile(r"[-‐‑–—/_#]+")


def _codes(text: str):
    for word in text.split():
        if ADDRESS.search(word):
            continue
        for part in JOINS.split(word.strip(EDGE)):
            core = part.strip(EDGE + "()[]")
            if not any(ch.isdigit() for ch in core) or not any(ch.isalpha() for ch in core):
                continue
            if ORDINAL.fullmatch(core):
                continue
            yield part.strip(EDGE)


def check_codes(text: str) -> list[Flag]:
    confusable, unbalanced = [], []
    # A bracket counts as unpaired only if the whole line fails to pair it: in
    # "(see line 12b)" the word "12b)" closes a bracket opened two words earlier.
    open_round = text.count("(") != text.count(")")
    open_square = text.count("[") != text.count("]")
    for code in _codes(text):
        if any(ch in CONFUSABLE for ch in code if ch.isalpha()):
            confusable.append(code)
        if (open_round and ("(" in code or ")" in code)) or (open_square and ("[" in code or "]" in code)):
            unbalanced.append(code)
    flags = []
    if confusable:
        flags.append(
            Flag(
                rule="code.confusable",
                severity="review",
                message=f"{', '.join(confusable)}: a code mixing letters and digits, with a letter "
                "readers can take for a digit (O and 0, I and 1, B and 8). Check each character.",
            )
        )
    if unbalanced:
        flags.append(
            Flag(
                rule="code.unbalanced-bracket",
                severity="review",
                message=f'{", ".join(unbalanced)}: a bracket without its pair, as in "4a)" read for "4(a)".',
            )
        )
    return flags
