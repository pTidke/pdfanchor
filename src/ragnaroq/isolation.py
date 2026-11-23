"""Reading the pages of a PDF you did not choose, safely.

A crafted or broken page can make a PDF parser spin for minutes, allocate memory until
the machine stalls, or crash the process outright. So the text layer of each page is
read, and the page drawn for OCR, in a worker process that has a memory cap, and the
caller gives each page a time limit. A page that breaks a limit, or kills the worker,
fails alone with the reason; the worker is started again for the next page, and the
rest of the document comes out as usual.

OCR itself runs in the calling process: the page image it reads is already bounded by
`Config.max_render_pixels`, and Tesseract runs as a separate program with its own
time limit.
"""

import io
import multiprocessing
from dataclasses import dataclass

from . import native
from .config import Config
from .model import Page


class BudgetExceeded(Exception):
    """A page broke a safety limit. `rule` names the limit for the page's flag."""

    def __init__(self, rule: str, message: str):
        super().__init__(message)
        self.rule, self.message = rule, message


@dataclass
class PageRead:
    page: Page
    image: object | None  # the page drawn for OCR (PIL image, dpi in .info), or None
    hidden: int  # characters outside the visible area


def read_one(pdf, number: int, config: Config, crosscheck: bool) -> PageRead:
    """Read page `number` of an open pdfplumber document: its text layer, and its image
    when it is scanned or will be cross-checked by OCR."""
    raw, hidden = native.visible(pdf.pages[number - 1])
    if len(raw.chars) > config.max_page_chars:
        raise BudgetExceeded(
            "budget.page-glyphs",
            f"The page has {len(raw.chars):,} characters, more than the limit of "
            f"{config.max_page_chars:,}; it was not read.",
        )
    page = native.read_page(raw, number, config)
    image = None
    if page.kind == "scanned" or (page.kind == "native" and crosscheck):
        dpi = config.dpi
        if page.kind == "scanned":
            found = native.source_dpi(raw)
            if found is not None:
                page.metrics["source_dpi"] = round(found, 1)
                if found < config.low_res_source_dpi:
                    dpi = max(dpi, config.low_res_render_dpi)
        page.metrics["render_dpi"] = dpi
        image = native.render(raw, dpi, config.max_render_pixels)
        image.info["dpi"] = (dpi, dpi)
    return PageRead(page=page, image=image, hidden=hidden)


def _worker(conn, data: bytes, config: Config) -> None:
    if config.page_memory_mb:
        try:
            import resource

            limit = config.page_memory_mb * 1024 * 1024
            resource.setrlimit(resource.RLIMIT_AS, (limit, limit))
        except (ImportError, ValueError, OSError):  # not on every platform
            pass
    import pdfplumber

    try:
        pdf = pdfplumber.open(io.BytesIO(data))
        conn.send(("ready", len(pdf.pages)))
    except Exception as error:
        conn.send(("error", type(error).__name__))
        return
    with pdf:
        while True:
            request = conn.recv()
            if request is None:
                return
            number, crosscheck = request
            try:
                conn.send(("ok", read_one(pdf, number, config, crosscheck)))
            except BudgetExceeded as error:
                conn.send(("budget", (error.rule, error.message)))
            except MemoryError:
                conn.send(
                    (
                        "budget",
                        (
                            "budget.page-memory",
                            f"Reading the page needed more than {config.page_memory_mb:,} MB; it was stopped.",
                        ),
                    )
                )
            except Exception as error:
                conn.send(("error", type(error).__name__))


class PageReader:
    """Reads the pages of one PDF, one at a time, within the configured limits.

    With `Config.isolate_pages` off, pages are read in this process: faster to start,
    with the glyph limit but no time or memory limit."""

    def __init__(self, data: bytes, config: Config):
        self.data, self.config = data, config
        self.page_count: int | None = None
        self._pdf = self._process = self._conn = None
        if not config.isolate_pages:
            import pdfplumber

            self._pdf = pdfplumber.open(io.BytesIO(data))
            self.page_count = len(self._pdf.pages)
        else:
            # Started now, so a PDF that cannot be opened within the limits fails as
            # a whole, before any page is attempted.
            self._start()

    def _start(self) -> None:
        context = multiprocessing.get_context("spawn")  # never fork a process that may hold threads
        self._conn, child = context.Pipe()
        self._process = context.Process(target=_worker, args=(child, self.data, self.config), daemon=True)
        self._process.start()
        child.close()
        if not self._conn.poll(self.config.page_timeout_seconds + 30):
            self._stop()
            raise BudgetExceeded("budget.open-time", "The PDF could not be opened within the time limit.")
        kind, payload = self._recv()
        if kind != "ready":
            self._stop()
            raise BudgetExceeded("pdf.open-failed", f"The PDF could not be opened ({payload}).")
        self.page_count = payload

    def _recv(self):
        try:
            return self._conn.recv()
        except (EOFError, OSError):
            self._stop()
            raise BudgetExceeded(
                "page.crashed", "The reader stopped while reading this page, perhaps out of memory."
            ) from None

    def _stop(self) -> None:
        if self._process is not None:
            self._process.kill()
            self._process.join(5)
        if self._conn is not None:
            self._conn.close()
        self._process = self._conn = None

    def read(self, number: int, crosscheck: bool) -> PageRead:
        """Read one page, or raise BudgetExceeded (with the rule to flag) or RuntimeError."""
        if self._pdf is not None:
            return read_one(self._pdf, number, self.config, crosscheck)
        if self._process is None or not self._process.is_alive():
            self._start()
        self._conn.send((number, crosscheck))
        if not self._conn.poll(self.config.page_timeout_seconds):
            self._stop()
            raise BudgetExceeded(
                "budget.page-time",
                f"Reading the page took longer than {self.config.page_timeout_seconds:g} seconds; it was stopped.",
            )
        kind, payload = self._recv()
        if kind == "ok":
            return payload
        if kind == "budget":
            raise BudgetExceeded(*payload)
        raise RuntimeError(f"{payload} while reading")

    def close(self) -> None:
        if self._pdf is not None:
            self._pdf.close()
        if self._conn is not None:
            try:
                self._conn.send(None)
            except (OSError, BrokenPipeError):
                pass
        self._stop()

    def __enter__(self):
        return self

    def __exit__(self, *exc) -> None:
        self.close()
