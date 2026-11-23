"""EasyOCR as an independent second reader. Optional: `pip install ragnaroq[easyocr]`.

A different kind of reader from Tesseract (a neural detector and recogniser), so the
two rarely make the same mistake in the same place; where they agree, a line can be
accepted by rule (see policy.py), and where they differ, a person looks.

Runs on the CPU and offline. The model files live in `model_dir`; they are fetched only
when `download=True` (for example the first run of a cached CI job), and EasyOCR
checks their MD5 sums against its own list. `prepare` loads them up front, so an
engine whose models are missing is left out at the start instead of failing on every
page.
"""

from collections.abc import Sequence

from . import EngineUnavailable, OcrLine

CODES = {"eng": "en", "hin": "hi"}


class EasyOcr:
    name = "easyocr"

    def __init__(
        self,
        model_dir: str | None = None,
        download: bool = False,
        languages: Sequence[str] | None = ("eng",),
    ):
        # `languages` overrides the document's OCR languages for this reader: a second
        # reader is there to confirm the indexed (English) lines, and each extra
        # EasyOCR language is another model to fetch and run.
        self.model_dir, self.download = model_dir, download
        self.languages = tuple(languages) if languages else None
        self._readers: dict[tuple[str, ...], object] = {}

    def _module(self):
        try:
            import easyocr
        except ImportError as error:
            raise EngineUnavailable("The easyocr package is not installed.") from error
        return easyocr

    def version(self) -> str:
        return str(self._module().__version__)

    def _reader(self, languages: Sequence[str]):
        codes = tuple(CODES.get(code, code) for code in (self.languages or languages))
        if codes not in self._readers:
            try:
                self._readers[codes] = self._module().Reader(
                    list(codes),
                    gpu=False,
                    model_storage_directory=self.model_dir,
                    download_enabled=self.download,
                    verbose=False,
                )
            except EngineUnavailable:
                raise
            except Exception as error:  # missing or damaged model files, unknown language
                raise EngineUnavailable(
                    f"EasyOCR could not load its models ({type(error).__name__})."
                ) from error
        return self._readers[codes]

    def prepare(self, languages: Sequence[str]) -> None:
        """Load the models now. Raises EngineUnavailable when they are not there."""
        self._reader(languages)

    def read(self, image, languages: Sequence[str]) -> list[OcrLine]:
        import numpy

        lines = []
        # paragraph=False keeps one result per text run, each with its own box, so a
        # line of the first reader collects exactly the runs inside it.
        for polygon, text, confidence in self._reader(languages).readtext(
            numpy.asarray(image.convert("RGB")), paragraph=False, detail=1
        ):
            xs, ys = [p[0] for p in polygon], [p[1] for p in polygon]
            lines.append(
                OcrLine(
                    text=text,
                    box=(float(min(xs)), float(min(ys)), float(max(xs)), float(max(ys))),
                    confidence=round(float(confidence) * 100, 1),
                )
            )
        return lines
