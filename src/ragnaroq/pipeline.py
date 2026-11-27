"""PDF in, anchored document out. One function runs every stage in order."""

import hashlib
import importlib.metadata
import io
import platform
import unicodedata
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path

from . import __version__, policy, structure, third
from .anchors import Anchor, pdf_anchor
from .config import Config
from .crosscheck import text_inside, to_points
from .export.chunks import to_chunks, to_jsonl
from .export.markdown import to_markdown
from .isolation import BudgetExceeded, PageReader
from .model import Box, Document, Engine, Flag, Line, Page, Reading, Source
from .ocr import LineReader, OcrEngine, OcrLine, available


class ConversionError(Exception):
    """The file could not be processed at all."""


def _preflight(data: bytes, config: Config) -> tuple[int, list[Flag], list[tuple[float, float]]]:
    from pypdf import PdfReader
    from pypdf.errors import PyPdfError

    if len(data) > config.max_bytes:
        raise ConversionError("The file is larger than the byte budget.")
    if not data.startswith(b"%PDF-"):
        raise ConversionError("The file is not a PDF.")
    try:
        reader = PdfReader(io.BytesIO(data))
        if reader.is_encrypted:
            raise ConversionError("The PDF is encrypted.")
        count = len(reader.pages)
        sizes = []
        for page in reader.pages:
            try:
                sizes.append((float(page.cropbox.width), float(page.cropbox.height)))
            except Exception:  # a page whose size cannot be read still gets a place
                sizes.append((0.0, 0.0))
        root = reader.trailer["/Root"]
        names = root.get("/Names", {})
        names = names.get_object() if hasattr(names, "get_object") else names
        fields = reader.get_fields() or {}
    except PyPdfError as error:
        raise ConversionError("The PDF could not be parsed.") from error
    flags = []
    if root.get("/OpenAction") or root.get("/AA") or names.get("/JavaScript"):
        flags.append(
            Flag(rule="pdf.active-content", severity="review", message="Has actions or scripts; none are run.")
        )
    if names.get("/EmbeddedFiles"):
        flags.append(
            Flag(rule="pdf.embedded-files", severity="review", message="Has attachments that were not read.")
        )
    if fields:
        flags.append(
            Flag(rule="pdf.form-fields", severity="review", message="Has form fields; their values were not read.")
        )
    return count, flags, sizes


def _ocr(engine: OcrEngine, image, config: Config, points: bool = True) -> list[tuple]:
    """The engine's lines with their boxes: in PDF points for a rendered page, else in the
    image's own pixels."""
    if not points:
        return [
            (Box(x0=line.box[0], top=line.box[1], x1=line.box[2], bottom=line.box[3]), line)
            for line in engine.read(image, config.languages)
        ]
    dpi = image.info.get("dpi", (config.dpi,))[0]
    return [(to_points(line, dpi), line) for line in engine.read(image, config.languages)]


def _others(
    flags: list[Flag],
    image,
    engines: Sequence[OcrEngine],
    config: Config,
    *,
    points: bool = True,
    what: str = "page",
) -> list[tuple[str, list[tuple]]]:
    """Readings from the confirming engines. One failing on a page costs that page its
    confirmation, flagged, never the page: the first engine's lines stand."""
    readings = []
    for engine in engines:
        try:
            readings.append((engine.name, _ocr(engine, image, config, points)))
        except Exception as error:
            flags.append(
                Flag(
                    rule="ocr.reader-failed",
                    severity="review",
                    message=f"ocr:{engine.name} could not read this {what} ({type(error).__name__}).",
                )
            )
    return readings


def ocr_lines(
    image,
    engines: Sequence[OcrEngine],
    config: Config,
    *,
    page: int,
    anchor: Anchor | None = None,
    prefix: str | None = None,
    flags: list[Flag] | None = None,
    points: bool = True,
    what: str = "page",
) -> list[Line]:
    """Lines from the first engine, each carrying the readings of the others.

    A rendered page (`points`) has boxes in PDF points, ids `p{page}-l{n}` and a PDF anchor
    for each line. A picture inside another kind of file has boxes in its own pixels, ids
    `{prefix}-l{n}` and the `anchor` of the picture. A confirming engine that fails is noted in
    `flags`; the first engine's failure is raised, for the caller to place."""
    first, *rest = engines
    others = _others(flags if flags is not None else [], image, rest, config, points=points, what=what)
    row = 4 if points else 12
    found = sorted(
        _ocr(first, image, config, points), key=lambda pair: (round(pair[0].top / row), pair[0].x0)
    )
    prefix = prefix or f"p{page}"
    lines = []
    for order, (box, read) in enumerate(found):
        line = Line(
            id=f"{prefix}-l{order + 1}",
            page=page,
            order=order,
            box=box,
            anchor=anchor or pdf_anchor(page, box.x0, box.top, box.x1, box.bottom),
            raw=read.text,
            text=unicodedata.normalize("NFC", read.text),
            source=f"ocr:{first.name}",
        )
        _attach(line, others, read)
        lines.append(line)
    return lines


def _scanned(page: Page, image, engines: Sequence[OcrEngine], config: Config) -> None:
    """Lines come from the first engine. Later engines only add comparison readings."""
    if not engines:
        page.flags.append(
            Flag(rule="ocr.unavailable", severity="blocking", message="A scanned page and no OCR engine.")
        )
        return
    page.lines.extend(ocr_lines(image, engines, config, page=page.number, flags=page.flags))
    if not page.lines:
        page.flags.append(
            Flag(rule="ocr.no-text", severity="blocking", message="The page has ink but OCR returned no lines.")
        )


def _attach(line: Line, readings: list[tuple[str, list[tuple]]], own: OcrLine | None = None) -> None:
    if own is not None:
        line.readings.append(Reading(source=line.source, text=own.text, confidence=own.confidence))
    for name, lines in readings:
        other = text_inside(line.box, lines)
        line.readings.append(
            Reading(
                source=f"ocr:{name}",
                text=other.text if other else "",
                confidence=other.confidence if other else None,
            )
        )


def convert(
    source: str | Path | bytes,
    *,
    config: Config | None = None,
    engines: Sequence[OcrEngine] | None = None,
    filename: str | None = None,
    third_reader: LineReader | None = None,
    third_cache: str | Path | None = None,
) -> Document:
    """Convert one PDF. `engines=None` uses whatever OCR engines are installed;
    pass `[]` to read the text layer only. A `third_reader` reads the scanned lines the
    first two readers disagree on, and the verdict is decided again with its reading."""
    config = config or Config()
    data = source if isinstance(source, bytes) else Path(source).read_bytes()
    name = filename or (Path(source).name if not isinstance(source, bytes) else "document.pdf")
    count, flags, sizes = _preflight(data, config)
    engines = available(config.languages) if engines is None else list(engines)
    # Asked before any page is read, so a third reader that cannot run (no torch) fails
    # now; its model is still loaded only when a crop needs it.
    third_version = third_reader.version() if third_reader is not None else None
    crosscheck = bool(engines and config.native_crosscheck_engines)
    pages: list[Page] = []
    try:
        reader = PageReader(data, config)
    except BudgetExceeded as error:
        raise ConversionError(error.message) from error
    with reader:
        for number in range(1, count + 1):
            size = sizes[number - 1]
            if number > config.max_pages:
                pages.append(_failed(size, number, "budget.page-limit", "Beyond the page budget; not read."))
                continue
            try:
                got = reader.read(number, crosscheck)
            except BudgetExceeded as error:
                pages.append(_failed(size, number, error.rule, error.message))
                continue
            except Exception as error:  # one bad page must not lose the others
                pages.append(_failed(size, number, "page.failed", str(error) or f"{type(error).__name__} while reading."))
                continue
            page, image, hidden = got.page, got.image, got.hidden
            try:
                if hidden:
                    page.flags.append(
                        Flag(
                            rule="page.hidden-text",
                            severity="review",
                            message=f"{hidden} characters lie outside the visible page area. They "
                            "are not shown by PDF viewers and were left out.",
                        )
                    )
                if image is not None:
                    if page.kind == "scanned":
                        _scanned(page, image, engines, config)
                    else:
                        readings = _others(
                            page.flags, image, engines[: config.native_crosscheck_engines], config
                        )
                        for line in page.lines:
                            _attach(line, readings)
                pages.append(page)
            except Exception as error:  # one bad page must not lose the others
                pages.append(_failed(size, number, "page.failed", f"{type(error).__name__} while reading."))
        readable = reader.page_count
    components = {
        package: importlib.metadata.version(package)
        for package in ("pdfplumber", "pypdf", "pypdfium2", "pydantic", "Pillow")
    }
    components["python"] = platform.python_version()
    for engine in engines:
        components[f"ocr:{engine.name}"] = engine.version()
    if third_reader is not None:
        components[f"vlm:{third_reader.name}"] = third_version
    document = Document(
        source=Source(sha256=hashlib.sha256(data).hexdigest(), bytes=len(data), filename=name, pages=count),
        engine=Engine(
            name="ragnaroq",
            version=__version__,
            policy_version=config.policy.version,
            config_sha256=config.sha256(),
            components=components,
        ),
        created_at=datetime.now(UTC).isoformat(timespec="seconds"),
        pages=pages,
        flags=flags,
    )
    if readable is not None and readable != count:
        document.flags.append(
            Flag(rule="pdf.page-count-mismatch", severity="blocking", message="Readers disagree on page count.")
        )
    structure.apply(document, config)
    policy.decide(document, config.policy)
    if third_reader is not None:
        third.read_disputed(document, data, config, third_reader, third_cache)
        policy.decide(document, config.policy)
    document.title = structure.title(document)  # a settled line may now carry other text
    return document


def _failed(size: tuple[float, float], number: int, rule: str, message: str) -> Page:
    return Page(
        number=number,
        width=size[0],
        height=size[1],
        kind="failed",
        flags=[Flag(rule=rule, severity="blocking", message=message)],
    )


def write(
    document: Document,
    out: str | Path,
    config: Config | None = None,
    images: dict[str, bytes] | None = None,
) -> dict[str, Path]:
    """Write the three output files and return their paths. With `images` (a reader's
    figure id to bytes), each distinct image is saved under `images/` first, so the three
    files carry the saved paths."""
    from . import figures
    from .readers import ReadResult

    config = config or Config()
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    if images is not None:
        figures.save(ReadResult(document, images), out, config)
    files = {
        "document": out / "document.json",
        "markdown": out / "document.md",
        "chunks": out / "chunks.jsonl",
    }
    files["document"].write_text(document.model_dump_json(indent=2), encoding="utf8")
    files["markdown"].write_text(to_markdown(document), encoding="utf8")
    files["chunks"].write_text(to_jsonl(to_chunks(document, config)), encoding="utf8")
    return files
