"""Rule checks on text. A validator only ever raises a flag; it never changes text."""

from collections.abc import Callable

from ..model import Flag
from .codes import check_codes
from .dates import check_dates
from .encoding import check_encoding

Validator = Callable[[str], list[Flag]]

DEFAULT: tuple[Validator, ...] = (check_encoding, check_dates)
# Risks of reading an image, which text taken from a PDF's own text layer cannot have.
READING: tuple[Validator, ...] = (check_codes,)


def run(text: str, validators: tuple[Validator, ...] = DEFAULT, *, ocr: bool = True) -> list[Flag]:
    """Flags for `text`. `ocr` adds the checks for misreadings, for text that came
    from reading an image rather than from the PDF's text layer."""
    chosen = validators + READING if ocr else validators
    return [flag for validator in chosen for flag in validator(text)]
