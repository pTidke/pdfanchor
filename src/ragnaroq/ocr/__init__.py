"""OCR engines sit behind one small interface so they can be compared and swapped.

An engine reads an image and returns lines with pixel boxes. It never sees another
engine's output or any expected text.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol, runtime_checkable


class EngineUnavailable(RuntimeError):
    """The engine's program or package is not installed."""


@dataclass(frozen=True)
class OcrLine:
    text: str
    box: tuple[float, float, float, float]  # x0, top, x1, bottom in image pixels
    confidence: float | None = None  # an engine signal, never a reason to accept


@runtime_checkable
class OcrEngine(Protocol):
    name: str

    def version(self) -> str: ...

    def read(self, image, languages: Sequence[str]) -> list[OcrLine]: ...


def available(
    languages: Sequence[str] = ("eng",),
    *,
    easyocr_models: str | None = None,
    easyocr_download: bool = False,
) -> list[OcrEngine]:
    """Engines that can run on this machine, most established first. The first is the
    one whose lines are kept; the others only confirm or dispute them.

    An engine that needs model files is loaded here, so one whose models are missing
    is left out now instead of failing on every page."""
    from .easyocr import EasyOcr
    from .tesseract import TesseractCli

    found: list[OcrEngine] = []
    for engine in (TesseractCli(), EasyOcr(easyocr_models, easyocr_download)):
        try:
            engine.version()
            prepare = getattr(engine, "prepare", None)
            if prepare:
                prepare(languages)
        except EngineUnavailable:
            continue
        found.append(engine)
    return found


class LineUnreadable(RuntimeError):
    """A line reader gave up on one crop. The message says why and is shown to a reviewer."""


@runtime_checkable
class LineReader(Protocol):
    """Reads one cropped line image as text, for the third-reader stage."""

    name: str

    def version(self) -> str:
        """Identifies the model and its settings. Must work without loading the model."""
        ...

    def prepare(self) -> None:
        """Load the model. Idempotent; called before the first read that needs it."""
        ...

    def read_line(self, image, max_new_tokens: int, seconds: float) -> str:
        """The text of the line in `image`, or raise LineUnreadable."""
        ...
