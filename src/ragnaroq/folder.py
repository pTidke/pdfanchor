"""`ragnaroq index`: a folder of files made ready for retrieval, and kept ready.

Everything is written under `<folder>/.ragnaroq/`; the originals are only ever opened for
reading. A run reads each supported file, writes `files/<file_id>/` for it, and ends with
three files: `chunks.jsonl` (every file's chunks, by path), `changes.json` (what differs from
the last run, for a vector database to follow) and `manifest.json` (written last, so a run
that is interrupted only costs the next run some reading).

One file's failure never stops the run. A file that is unchanged since the last run, read
with the same settings, is not read again.
"""

import errno
import hashlib
import importlib.metadata
import json
import os
import re
import shutil
import stat
import time
import uuid
from collections.abc import Callable, Iterator, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path

from pydantic import ValidationError

from . import __version__, figures, isolation
from .config import Config
from .export.chunks import NAMESPACE
from .isolation import BudgetExceeded, FileReader
from .model import Model
from .ocr import LineReader, OcrEngine, available
from .pipeline import ConversionError, convert, write
from .policy import needs_review
from .readers import READER_VERSIONS, ReaderRefused, ReaderUnavailable, detect, refused_document

MANIFEST_VERSION = "0.3.0"
STATE = ".ragnaroq"
READ_TRIES = 3  # a file that keeps changing while it is read is given up on

# A failure that may not happen again: the file is read again next run. A refusal (a zip bomb,
# a truncated file) is the file's own, so it is carried over like any result.
TRANSIENT = frozenset(
    {"file.timeout", "file.memory", "file.crashed", "file.unreadable", "file.changed", "reader.unavailable"}
)
# Extensions whose files, when they are not what they say, fail instead of being "unsupported".
CLAIMED = frozenset("pdf docx xlsx pptx html htm xhtml md markdown json xml csv tsv".split())
_NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)
_NONBLOCK = getattr(os, "O_NONBLOCK", 0)
_ID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")


class FolderError(Exception):
    """The folder cannot be indexed at all (not a folder, locked, or a `.ragnaroq` that is not ours)."""


class FileEntry(Model):
    path: str
    file_id: str
    sha256: str = ""
    bytes: int = 0
    format: str = ""
    reader: str = ""
    settings: str = ""
    status: str  # ok, review, failed or skipped
    counts: dict[str, int] = {}
    chunk_ids: list[str] = []
    error: str = ""
    skipped: str = ""
    rule: str = ""  # for a failed file, the rule of its blocking flag


class Manifest(Model):
    schema_version: str = MANIFEST_VERSION
    ragnaroq: str = __version__
    files: list[FileEntry] = []


class Changes(Model):
    added: list[str] = []  # file ids
    changed: list[str] = []
    removed: list[str] = []
    chunks_added: list[str] = []
    chunks_removed: list[str] = []


class IndexReport(Model):
    manifest: Manifest
    changes: Changes
    failed: int = 0
    needs_review: bool = False
    notes: list[str] = []


def file_id(path: str) -> str:
    return str(uuid.uuid5(NAMESPACE, path))


# ---------------------------------------------------------------- the .ragnaroq folder


def _lstat(path: Path | str):
    try:
        return os.lstat(path)
    except FileNotFoundError:
        return None


def _remove(path: Path | str) -> None:
    """Delete a file, a link (never what it points to) or a folder."""
    info = _lstat(path)
    if info is None:
        return
    if stat.S_ISDIR(info.st_mode):
        shutil.rmtree(path)
    else:
        os.unlink(path)


def _real_dir(path: Path, *, replace: bool) -> None:
    """`path` is a real folder (not a link). With `replace`, something else in its place is removed."""
    info = _lstat(path)
    if info is not None and not stat.S_ISDIR(info.st_mode):
        if not replace:
            raise FolderError(f"{path} is not a folder, so nothing was written. Remove it and run again.")
        os.unlink(path)
        info = None
    if info is None:
        os.mkdir(path)


def _open_read(path: Path | str) -> int:
    """A file descriptor for a regular file that is not a link; OSError otherwise."""
    fd = os.open(path, os.O_RDONLY | _NOFOLLOW | _NONBLOCK)
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise OSError(errno.EINVAL, "not a regular file")
    except BaseException:
        os.close(fd)
        raise
    return fd


def _read_state(path: Path | str, limit: int = 1 << 30) -> bytes:
    fd = _open_read(path)
    with os.fdopen(fd, "rb") as handle:
        return handle.read(limit)


def _lock(state: Path):
    path = state / "lock"
    fd = os.open(path, os.O_RDWR | os.O_CREAT | _NOFOLLOW, 0o644)
    try:
        try:
            import fcntl

            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except ImportError:  # Windows
            import msvcrt

            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
    except OSError as error:
        os.close(fd)
        if error.errno in (errno.EAGAIN, errno.EACCES, errno.EWOULDBLOCK):
            raise FolderError(
                "Another ragnaroq index is already running on this folder; wait for it to finish."
            ) from None
        raise FolderError(f"The folder could not be locked ({error.strerror}).") from None
    return fd


def _write_file(state: Path, name: str, write_to: Callable) -> None:
    """Write `.ragnaroq/<name>` through a temporary file, so it is whole or not there."""
    temporary = state / "tmp" / f"{name}.part"
    with open(temporary, "wb") as handle:
        write_to(handle)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, state / name)


def _load_manifest(state: Path, notes: list[str]) -> Manifest | None:
    path = state / "manifest.json"
    if _lstat(path) is None:
        return None
    try:
        manifest = Manifest.model_validate_json(_read_state(path))
    except (OSError, ValidationError, ValueError) as error:
        notes.append(
            f"The last manifest could not be read ({type(error).__name__}); reading every file again."
        )
        return None
    if manifest.schema_version != MANIFEST_VERSION:
        notes.append(
            f"The last manifest has schema {manifest.schema_version}, not {MANIFEST_VERSION}; "
            "reading every file again."
        )
        return None
    # An entry is only trusted for the id its path gives; ids are never used to build a path
    # before that check, and the folders are named by ids computed here.
    sound = [entry for entry in manifest.files if entry.file_id == file_id(entry.path)]
    if len(sound) != len(manifest.files):
        notes.append(
            f"{len(manifest.files) - len(sound)} entries of the last manifest were not sound; ignored."
        )
    manifest.files = sound
    return manifest


def _chunk_ids(path: Path) -> list[str] | None:
    """The ids in a file's chunks.jsonl, or None if it is missing, a link or not chunks."""
    try:
        fd = _open_read(path)
    except OSError:
        return None
    ids = []
    try:
        with os.fdopen(fd, "rb") as handle:
            for line in handle:
                chunk_id = json.loads(line)["id"]
                if not isinstance(chunk_id, str):
                    return None
                ids.append(chunk_id)
    except (ValueError, KeyError, TypeError):
        return None
    return ids


# ---------------------------------------------------------------- walking the folder


@dataclass
class Found:
    path: str  # relative, POSIX
    full: str | None = None  # where to read it; None for a skipped one
    skipped: str = ""


def _bad_name(name: str) -> bool:
    try:
        name.encode("utf-8")
    except UnicodeEncodeError:
        return True
    return False


def _shown(path: str) -> str:
    """A path that is not valid UTF-8, with its odd bytes written as \\xNN."""
    return os.fsencode(path).decode("utf-8", "backslashreplace")


def _inside(real_root: str, path: str) -> bool:
    try:
        return os.path.commonpath([real_root, path]) == real_root
    except ValueError:
        return False


def walk(root: Path, real_root: str) -> Iterator[Found]:
    """Every file under `root`, or the reason it is left out. Links to folders are not
    followed; hidden things and `.ragnaroq` are not entered."""
    top = os.fspath(root)
    unreadable: list[str] = []

    def lost() -> Iterator[Found]:
        while unreadable:
            relative = os.path.relpath(unreadable.pop(0), top).replace(os.sep, "/")
            yield Found(_shown(relative), skipped="unreadable folder")

    for here, names, files in os.walk(
        top, followlinks=False, onerror=lambda e: unreadable.append(e.filename)
    ):
        yield from lost()
        relative = os.path.relpath(here, top)
        prefix = "" if relative == "." else relative.replace(os.sep, "/") + "/"
        keep = []
        for name in sorted(names):
            if here == top and name == STATE:
                continue
            if _bad_name(name):
                yield Found(_shown(prefix + name), skipped="unreadable name")
            elif name.startswith("."):
                yield Found(prefix + name, skipped="hidden")
            elif os.path.islink(os.path.join(here, name)):
                yield Found(prefix + name, skipped="symbolic link to a folder")
            else:
                keep.append(name)
        names[:] = keep
        for name in sorted(files):
            if _bad_name(name):
                yield Found(_shown(prefix + name), skipped="unreadable name")
            elif name.startswith("."):
                yield Found(prefix + name, skipped="hidden")
            else:
                yield Found(prefix + name, full=os.path.join(here, name))
    yield from lost()


def _resolve(found: Found, real_root: str) -> tuple[str | None, str]:
    """The regular file to open for `found`, or the reason it is left out."""
    try:
        info = os.lstat(found.full)
        target = found.full
        if stat.S_ISLNK(info.st_mode):
            target = os.path.realpath(found.full)
            if not _inside(real_root, target):
                return None, "outside the folder"
            info = os.stat(target)
            if stat.S_ISDIR(info.st_mode):
                return None, "symbolic link to a folder"
    except FileNotFoundError:
        return None, "broken symbolic link"
    except OSError:
        return None, "unreadable"
    if not stat.S_ISREG(info.st_mode):
        return None, "not a regular file"
    return target, ""


def _read_stable(target: str, limit: int) -> tuple[bytes | None, str]:
    """The bytes of the file (at most limit + 1), read while it did not change. Never blocks on
    a file that turned into a fifo, and never follows a link planted since the check."""
    for _ in range(READ_TRIES):
        fd = os.open(target, os.O_RDONLY | _NOFOLLOW | _NONBLOCK)
        with os.fdopen(fd, "rb") as handle:
            before = os.fstat(handle.fileno())
            if not stat.S_ISREG(before.st_mode):
                return None, "not a regular file"
            if before.st_size > limit:
                return None, "over the size limit"
            data = handle.read(limit + 1)
            after = os.fstat(handle.fileno())
        if (before.st_size, before.st_mtime_ns) == (after.st_size, after.st_mtime_ns) and len(
            data
        ) == after.st_size:
            return data, ""
    raise OSError(errno.EBUSY, f"changed {READ_TRIES} times while it was being read")


def _zip_too_big(data: bytes, config: Config) -> str:
    """Before a zip is opened here, look at its end record: an archive that claims more
    entries than allowed, or a directory too big for that many, is not opened at all."""
    if not data.startswith(b"PK\x03\x04"):
        return ""
    at = data.rfind(b"PK\x05\x06", max(0, len(data) - 65_557))
    if at < 0 or len(data) < at + 22:
        return ""
    entries = int.from_bytes(data[at + 10 : at + 12], "little")
    size = int.from_bytes(data[at + 12 : at + 16], "little")
    if entries >= 0xFFFF or entries > config.max_zip_entries or size > config.max_zip_entries * 1024:
        return f"The archive holds more than the {config.max_zip_entries} entries allowed."
    return ""


# ---------------------------------------------------------------- reading one file


def _version(distribution: str) -> str:
    try:
        return f"{distribution} {importlib.metadata.version(distribution)}"
    except importlib.metadata.PackageNotFoundError:
        return ""


def _reader_name(fmt: str) -> str:
    if fmt == "pdf":
        return ", ".join(filter(None, (_version(name) for name in ("pdfplumber", "pypdf", "pypdfium2"))))
    return READER_VERSIONS.get(fmt, "")


def _identity(thing) -> list[str]:
    try:
        return [thing.name, thing.version()]
    except Exception:  # an engine that cannot say what it is changes nothing but is not trusted
        return [getattr(thing, "name", "?"), "unknown"]


def _settings(config: Config, engines: Sequence[OcrEngine], third_reader: LineReader | None) -> dict:
    limits = (
        "max_depth",
        "max_cells",
        "max_unzipped_bytes",
        "max_zip_entries",
        "max_image_bytes",
        "max_figure_ocr_lines",
        "max_render_pixels",
    )
    return {
        "reading": config.reading_sha256(),
        "policy": asdict(config.policy),
        "ragnaroq": __version__,
        "limits": {name: getattr(config, name) for name in limits},
        "ocr": [_identity(engine) for engine in engines],
        "third": _identity(third_reader) if third_reader is not None else None,
    }


def _hash(settings: dict, fmt: str) -> str:
    body = json.dumps({**settings, "reader": _reader_name(fmt), "format": fmt}, sort_keys=True)
    return hashlib.sha256(body.encode()).hexdigest()


class _Context:
    def __init__(self, config, engines, third_reader, worker, state: Path):
        self.config, self.engines, self.third_reader = config, engines, third_reader
        self.worker, self.state = worker, state


def _make(context: _Context, found: Found, target: str, data: bytes, fmt: str, out: Path) -> tuple:
    """Read one file into `out`. Returns the document, or the (rule, message) it failed with."""
    config = context.config
    base = Path(os.path.dirname(os.path.realpath(target)))
    started = time.monotonic()
    try:
        if fmt == "pdf":
            document = convert(
                data,
                config=config,
                engines=context.engines,
                filename=found.path,
                third_reader=context.third_reader,
            )
        else:
            result = isolation.read_file(data, found.path, config, base=base, reader=context.worker)
            deadline = started + config.file_timeout_seconds
            figures.describe(result, context.engines, config, deadline=deadline)
            figures.save(result, out, config, deadline=deadline)
            document = result.document
        write(document, out, config)
        (out / "images").mkdir(exist_ok=True)
        return document, None
    except ReaderRefused as error:
        return None, (error.rule, error.message)
    except ReaderUnavailable as error:
        return None, ("reader.unavailable", str(error))
    except BudgetExceeded as error:
        return None, (error.rule, error.message)
    except ConversionError as error:
        return None, ("pdf.unreadable", str(error))
    except MemoryError:
        return None, ("file.memory", "Reading the file ran out of memory.")
    except OSError as error:
        return None, ("file.unreadable", f"The file could not be read ({error.strerror or error}).")
    except Exception as error:  # one file must not stop the run
        return None, ("file.failed", f"{type(error).__name__} while reading the file.")


def _entry_of(found: Found, data: bytes, fmt: str, settings: str, document, failure, out: Path) -> FileEntry:
    entry = FileEntry(
        path=found.path,
        file_id=file_id(found.path),
        sha256=hashlib.sha256(data).hexdigest(),
        bytes=len(data),
        format=fmt,
        reader=_reader_name(fmt),
        settings=settings,
        status="ok",
    )
    if failure is not None:
        rule, message = failure
        entry.status, entry.error, entry.rule = "failed", message, rule
        return entry
    entry.counts = {**document.summary, "chunks": 0}
    entry.chunk_ids = _chunk_ids(out / "chunks.jsonl") or []
    entry.counts["chunks"] = len(entry.chunk_ids)
    entry.status = "review" if needs_review(document) else "ok"
    return entry


def _fail_output(
    found: Found, data: bytes, fmt: str, failure: tuple[str, str], config: Config, out: Path
) -> None:
    """What a failed file leaves: a document with one blocking flag, and no chunks. The file is
    not detected again here: that could be the very work that failed."""
    rule, message = failure
    document = refused_document(found.path, b"", ReaderRefused(rule, message))
    source = document.source
    source.sha256, source.bytes, source.format = (
        hashlib.sha256(data).hexdigest(),
        len(data),
        fmt or source.format,
    )
    shutil.rmtree(out, ignore_errors=True)
    write(document, out, config)
    (out / "images").mkdir(exist_ok=True)


# ---------------------------------------------------------------- a run


def _carry(state: Path, old: FileEntry | None, found: Found, sha: str, settings: str) -> FileEntry | None:
    """The last run's entry, if this file need not be read again: same bytes, same settings,
    and its output is still there and is the output of those bytes."""
    if old is None or old.status in ("skipped",) or old.sha256 != sha or old.settings != settings:
        return None
    if old.status == "failed" and old.rule in TRANSIENT:
        return None
    folder = state / "files" / old.file_id
    info = _lstat(folder)
    if info is None or not stat.S_ISDIR(info.st_mode):
        return None
    try:
        stored = FileEntry.model_validate_json(_read_state(folder / "entry.json", 1 << 26))
        for name in ("document.json", "chunks.jsonl"):
            if not stat.S_ISREG(os.lstat(folder / name).st_mode):
                return None
    except (OSError, ValidationError, ValueError):
        return None
    if (stored.path, stored.sha256, stored.settings) != (found.path, sha, settings):
        return None
    ids = _chunk_ids(folder / "chunks.jsonl")
    if ids is None:
        return None
    entry = old.model_copy(deep=True)
    entry.chunk_ids = ids
    return entry


def _install(state: Path, out: Path, entry: FileEntry) -> None:
    (out / "entry.json").write_text(entry.model_dump_json(indent=2), encoding="utf8")
    files = state / "files"
    _real_dir(files, replace=True)
    target = files / entry.file_id
    _remove(target)
    os.replace(out, target)


def _present(manifest: Manifest | None) -> dict[str, FileEntry]:
    return {e.file_id: e for e in manifest.files if e.status != "skipped"} if manifest else {}


def compare(old: Manifest | None, new: Manifest) -> Changes:
    before, after = _present(old), _present(new)
    changed = [
        key
        for key, entry in after.items()
        if key in before
        and (
            (before[key].sha256, before[key].settings, before[key].status, before[key].chunk_ids)
            != (entry.sha256, entry.settings, entry.status, entry.chunk_ids)
        )
    ]
    old_ids = {c for e in before.values() for c in e.chunk_ids}
    new_ids = {c for e in after.values() for c in e.chunk_ids}
    return Changes(
        added=[key for key in after if key not in before],
        changed=changed,
        removed=[key for key in before if key not in after],
        chunks_added=sorted(new_ids - old_ids),
        chunks_removed=sorted(old_ids - new_ids),
    )


def _copy_chunks(state: Path, entries: list[FileEntry]):
    def write_to(handle) -> None:
        for entry in entries:
            if not entry.chunk_ids:
                continue
            with os.fdopen(_open_read(state / "files" / entry.file_id / "chunks.jsonl"), "rb") as source:
                shutil.copyfileobj(source, handle)

    return write_to


def index(
    root: Path | str,
    config: Config,
    *,
    engines: Sequence[OcrEngine] | None = None,
    third_reader: LineReader | None = None,
    fresh: bool = False,
    progress: Callable[[str, str], None] | None = None,
) -> IndexReport:
    """Index the folder `root` (see the module's docstring). `engines=None` uses the OCR
    engines installed here; `[]` reads text layers only. `fresh` reads every file again."""
    root = Path(root)
    if not root.is_dir():
        raise FolderError(f"{root} is not a folder.")
    real_root = os.path.realpath(root)
    state = root / STATE
    _real_dir(state, replace=False)
    descriptor = _lock(state)
    try:
        return _run(root, real_root, state, config, engines, third_reader, fresh, progress)
    finally:
        os.close(descriptor)  # the lock goes with it


def _run(root, real_root, state, config, engines, third_reader, fresh, progress) -> IndexReport:
    notes: list[str] = []
    if engines is None:
        engines = available(config.languages)
    engines = list(engines)
    _remove(state / "tmp")  # what an interrupted run left
    os.mkdir(state / "tmp")
    _real_dir(state / "files", replace=True)
    old = _load_manifest(state, notes)
    before = {entry.file_id: entry for entry in old.files} if old else {}
    settings = _settings(config, engines, third_reader)
    entries: list[FileEntry] = []
    max_bytes = config.max_bytes

    def done(entry: FileEntry) -> None:
        entries.append(entry)
        if progress:
            progress(entry.path, entry.status)

    with FileReader(config) as worker:
        context = _Context(config, engines, third_reader, worker, state)
        for found in walk(root, real_root):
            identity = file_id(found.path)
            skip = found.skipped
            target, data, fmt, guard, guard_rule = None, None, "", "", ""
            if not skip:
                target, skip = _resolve(found, real_root)
            if not skip:
                try:
                    data, skip = _read_stable(target, max_bytes)
                except OSError as error:
                    failure = (
                        "file.changed" if error.errno == errno.EBUSY else "file.unreadable",
                        f"The file could not be read ({error.strerror or error}).",
                    )
                    out = state / "tmp" / identity
                    _fail_output(found, b"", "", failure, config, out)
                    entry = _entry_of(found, b"", "", "", None, failure, out)
                    entry.sha256 = ""  # nothing was read, so nothing can match it
                    _install(state, out, entry)
                    done(entry)
                    continue
                if not skip and not data:
                    skip = "empty"
                elif not skip and len(data) > max_bytes:
                    skip = "over the size limit"
                elif not skip:
                    guard, guard_rule = _zip_too_big(data, config), "file.zip-limits"
                    suffix = Path(found.path).suffix.lower().lstrip(".")
                    if guard:  # not detected: that would open the archive here
                        fmt = suffix if suffix in ("docx", "xlsx", "pptx") else ""
                    else:
                        fmt = detect(found.path, data) or ""
                    if not fmt and suffix in CLAIMED:  # it says what it is, and it is not
                        fmt = suffix
                        guard = guard or f"{found.path} is not a valid {suffix} file."
                        guard_rule = "format.unknown"
                    skip = "" if fmt else "unsupported"
            if skip:
                done(
                    FileEntry(
                        path=found.path,
                        file_id=identity,
                        bytes=len(data or b""),
                        status="skipped",
                        skipped=skip,
                    )
                )
                continue
            sha = hashlib.sha256(data).hexdigest()
            hashed = _hash(settings, fmt)
            carried = None if fresh else _carry(state, before.get(identity), found, sha, hashed)
            if carried is not None:
                done(carried)
                continue
            out = state / "tmp" / identity
            if guard:
                document, failure = None, (guard_rule, guard)
            else:
                document, failure = _make(context, found, target, data, fmt, out)
            if failure is not None:
                _fail_output(found, data, fmt, failure, config, out)
            entry = _entry_of(found, data, fmt, hashed, document, failure, out)
            _install(state, out, entry)
            del data, document
            done(entry)

    entries.sort(key=lambda entry: entry.path)
    manifest = Manifest(files=entries)
    changes = compare(old, manifest)
    _write_file(state, "chunks.jsonl", _copy_chunks(state, entries))
    _write_file(state, "changes.json", lambda h: h.write(changes.model_dump_json(indent=2).encode()))
    _write_file(state, "manifest.json", lambda h: h.write(manifest.model_dump_json(indent=2).encode()))
    live = {entry.file_id for entry in entries if entry.status != "skipped"}
    with os.scandir(state / "files") as listing:
        for item in list(listing):
            if item.name not in live:
                _remove(item.path)
    _remove(state / "tmp")
    failed = sum(entry.status == "failed" for entry in entries)
    review = any(entry.status == "review" for entry in entries)
    return IndexReport(manifest=manifest, changes=changes, failed=failed, needs_review=review, notes=notes)


__all__ = [
    "Changes",
    "FileEntry",
    "FolderError",
    "IndexReport",
    "Manifest",
    "compare",
    "file_id",
    "index",
]
