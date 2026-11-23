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
import json
import os
import pickle
import queue
import signal
import struct
import subprocess
import sys
import threading
from collections import deque
from dataclasses import dataclass

from . import native
from .config import Config
from .model import Page

# The worker is a new interpreter running this line, never the caller's script. (The
# `spawn` start method of multiprocessing runs the caller's `__main__` again in the
# child, which fails for a script read from stdin and recurses for a script without an
# `if __name__ == "__main__":` guard.) `sys.argv[1]` carries the caller's import path,
# so the worker imports the same ragnaroq.
_BOOTSTRAP = (
    "import json, sys; sys.path[:] = json.loads(sys.argv[1]); "
    "from ragnaroq.isolation import _serve; _serve()"
)

# Messages both ways are pickles, each after its length as 8 bytes, big-endian.
_HEADER = struct.Struct("!Q")
_GONE = object()  # the worker's replies ended


def _send(stream, message) -> None:
    body = pickle.dumps(message, protocol=pickle.HIGHEST_PROTOCOL)
    stream.write(_HEADER.pack(len(body)))
    stream.write(body)
    stream.flush()


def _receive(stream):
    header = stream.read(_HEADER.size)
    if len(header) < _HEADER.size:
        raise EOFError
    (size,) = _HEADER.unpack(header)
    body = stream.read(size)
    if len(body) < size:
        raise EOFError
    return pickle.loads(body)


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


def _limit_memory(megabytes: int) -> None:
    if not megabytes:
        return
    try:
        import resource

        limit = megabytes * 1024 * 1024
        resource.setrlimit(resource.RLIMIT_AS, (limit, limit))
    except (ImportError, ValueError, OSError):  # not on every platform
        pass


def _serve() -> None:
    """The worker: reads requests on stdin and answers on stdout until stdin closes."""
    signal.signal(signal.SIGINT, signal.SIG_IGN)  # Ctrl-C is for the caller, who then stops us
    requests, replies = os.fdopen(os.dup(0), "rb"), os.fdopen(os.dup(1), "wb")
    if sys.platform == "win32":
        import msvcrt

        for stream in (requests, replies):
            msvcrt.setmode(stream.fileno(), os.O_BINARY)
    # Whatever a library prints goes to stderr, so it cannot garble the replies.
    nothing = os.open(os.devnull, os.O_RDONLY)
    os.dup2(nothing, 0)
    os.close(nothing)
    os.dup2(2, 1)
    try:
        _answer(requests, replies)
    except (EOFError, BrokenPipeError):  # the caller has gone
        pass


def _answer(requests, replies) -> None:
    data, config = _receive(requests)
    _limit_memory(config.page_memory_mb)
    try:
        import pdfplumber

        pdf = pdfplumber.open(io.BytesIO(data))
    except Exception as error:
        _send(replies, ("error", type(error).__name__))
        return
    with pdf:
        _send(replies, ("ready", len(pdf.pages)))
        while (request := _receive(requests)) is not None:
            number, crosscheck = request
            try:
                reply = ("ok", read_one(pdf, number, config, crosscheck))
            except BudgetExceeded as error:
                reply = ("budget", (error.rule, error.message))
            except MemoryError:
                reply = (
                    "budget",
                    (
                        "budget.page-memory",
                        f"Reading the page needed more than {config.page_memory_mb:,} MB; it was stopped.",
                    ),
                )
            except Exception as error:
                reply = ("error", type(error).__name__)
            _send(replies, reply)


def _collect(stream, replies: queue.Queue) -> None:
    """In the caller, a thread per worker: every reply goes to `replies`, then _GONE."""
    try:
        while True:
            replies.put(_receive(stream))
    except Exception:  # EOFError when the worker exits; anything else ends it as well
        pass
    replies.put(_GONE)


def _relay(stream, last: deque) -> None:
    """Pass on what the worker writes to stderr, keeping its last lines for an error."""
    for raw in iter(stream.readline, b""):
        line = raw.decode("utf-8", "replace")
        if line.strip():
            last.append(line.strip())
        try:
            sys.stderr.write(line)
        except Exception:  # no stderr (pythonw), or one that was closed
            pass


def _feed(stream, message) -> None:
    try:
        _send(stream, message)
    except OSError:  # the worker died before reading; _collect reports it
        pass


def _how_it_stopped(code: int | None) -> str:
    if code is None:
        how = "stopped answering"
    elif code < 0:
        try:
            name = signal.Signals(-code).name
        except ValueError:
            name = f"signal {-code}"
        how = f"was killed by {name}, perhaps for running out of memory or by a fault in the PDF parser"
    else:
        how = f"exited with code {code}"
    return how


class PageReader:
    """Reads the pages of one PDF, one at a time, within the configured limits.

    With `Config.isolate_pages` off, pages are read in this process: faster to start,
    with the glyph limit but no time or memory limit."""

    def __init__(self, data: bytes, config: Config):
        self.data, self.config = data, config
        self.page_count: int | None = None
        self._pdf = self._process = None
        if not config.isolate_pages:
            import pdfplumber

            self._pdf = pdfplumber.open(io.BytesIO(data))
            self.page_count = len(self._pdf.pages)
        else:
            # Started now, so a PDF that cannot be opened within the limits fails as
            # a whole, before any page is attempted.
            self._start()

    def _start(self) -> None:
        if not sys.executable:
            raise BudgetExceeded(
                "pdf.open-failed",
                "No Python interpreter was found to read the pages in. Read them in this "
                "process instead, with isolation off.",
            )
        path = [os.fspath(entry) for entry in sys.path if isinstance(entry, (str, os.PathLike))]
        # The caller's -O, -X and -W options, passed on as multiprocessing does.
        flags = getattr(subprocess, "_args_from_interpreter_flags", list)()
        try:
            self._process = subprocess.Popen(
                [sys.executable, *flags, "-c", _BOOTSTRAP, json.dumps(path)],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
        except OSError as error:
            raise BudgetExceeded("pdf.open-failed", f"The reader could not be started ({error}).") from None
        self._replies: queue.Queue = queue.Queue()
        self._last: deque = deque(maxlen=5)
        threading.Thread(target=_collect, args=(self._process.stdout, self._replies), daemon=True).start()
        self._relay = threading.Thread(target=_relay, args=(self._process.stderr, self._last), daemon=True)
        self._relay.start()
        # In a thread, so the time limit holds even if the worker never reads it.
        message = (self.data, self.config)
        threading.Thread(target=_feed, args=(self._process.stdin, message), daemon=True).start()
        kind, payload = self._wait(
            self.config.page_timeout_seconds + 30,
            ("budget.open-time", "The PDF could not be opened within the time limit."),
            "before it could open the PDF",
        )
        if kind != "ready":
            self._stop()
            raise BudgetExceeded("pdf.open-failed", f"The PDF could not be opened ({payload}).")
        self.page_count = payload

    def _wait(self, seconds: float, late: tuple[str, str], when: str):
        """The worker's next reply. Raises BudgetExceeded, with `late` after `seconds`,
        or with page.crashed, saying how and `when`, if the worker stopped."""
        try:
            reply = self._replies.get(timeout=seconds)
        except queue.Empty:
            self._stop()
            raise BudgetExceeded(*late) from None
        if reply is not _GONE:
            return reply
        process = self._process
        try:
            code = process.wait(5)
        except subprocess.TimeoutExpired:
            code = None
        self._relay.join(2)  # its last words, if it wrote any
        message = f"The reader {_how_it_stopped(code)} {when}."
        if self._last:
            message += f" It last wrote: {self._last[-1][:300]}"
        self._stop()
        raise BudgetExceeded("page.crashed", message)

    def _stop(self) -> None:
        process, self._process = self._process, None
        if process is None:
            return
        if process.poll() is None:
            process.kill()
        try:
            process.wait(5)
        except subprocess.TimeoutExpired:
            pass
        for stream in (process.stdin, process.stdout, process.stderr):
            try:
                stream.close()
            except OSError:
                pass

    def read(self, number: int, crosscheck: bool) -> PageRead:
        """Read one page, or raise BudgetExceeded (with the rule to flag) or RuntimeError."""
        if self._pdf is not None:
            return read_one(self._pdf, number, self.config, crosscheck)
        if self._process is None or self._process.poll() is not None:
            self._stop()
            self._start()
        try:
            _send(self._process.stdin, (number, crosscheck))
        except OSError:  # it died since the last page; _wait says how
            pass
        kind, payload = self._wait(
            self.config.page_timeout_seconds,
            (
                "budget.page-time",
                f"Reading the page took longer than {self.config.page_timeout_seconds:g} seconds; "
                "it was stopped.",
            ),
            "while reading this page",
        )
        if kind == "ok":
            return payload
        if kind == "budget":
            raise BudgetExceeded(*payload)
        raise RuntimeError(f"{payload} while reading")

    def close(self) -> None:
        if self._pdf is not None:
            self._pdf.close()
            self._pdf = None
        self._stop()  # the worker holds nothing that needs a goodbye

    def __enter__(self):
        return self

    def __exit__(self, *exc) -> None:
        self.close()
