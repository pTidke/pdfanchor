"""`ragnaroq index`: a folder of files made ready for retrieval, and kept ready.

Everything is written under `<folder>/.ragnaroq/`; the originals are only ever opened for
reading. A run reads each supported file, writes `files/<file_id>/` for it, and ends with
three files: `chunks.jsonl` (every file's chunks, by path), `changes.json` (what differs from
the last run, for a vector database to follow) and `manifest.json` (written last, so a run
that is interrupted only costs the next run some reading).

One file's failure never stops the run. A file that is unchanged since the last run, read
with the same settings, is not read again.

Someone may be writing into the folder while it is indexed, so nothing here works by path
after a first open: `.ragnaroq` is opened once and its descriptor held; `files/` and `tmp/`
are opened from it; every write, rename and removal inside it names a file relative to one
of those descriptors. The folder is walked, and each original opened, relative to the
descriptor of the folder, never following a link. POSIX only (Linux, macOS).
"""

import contextlib
import errno
import hashlib
import importlib.metadata
import json
import os
import shutil
import stat
import tempfile
import time
import uuid
from collections.abc import Callable, Iterator, Sequence
from dataclasses import asdict, dataclass, field
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
HEAD = 64 * 1024  # what is read to tell what a file is
TIMEOUT_TRIES = 2  # a file that timed out is tried once more, then left until it or the settings change

# A failure that may not happen again: the file is read again next run. A refusal (a zip bomb,
# a truncated file) and a crash or memory failure are the file's own, so they are carried over
# like any result; so, after TIMEOUT_TRIES, is a timeout.
TRANSIENT = frozenset({"file.unreadable", "file.changed", "reader.unavailable"})
# Extensions whose files, when they are not what they say, fail instead of being "unsupported".
CLAIMED = frozenset("pdf docx xlsx pptx html htm xhtml md markdown json xml csv tsv".split())
OFFICE = ("docx", "xlsx", "pptx")  # a file starting with PK is one of these by its name, or nothing
_NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)
_NONBLOCK = getattr(os, "O_NONBLOCK", 0)
_DIRECTORY = getattr(os, "O_DIRECTORY", 0)
_PRIVATE_FILE, _PRIVATE_DIR = 0o600, 0o700


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
    attempts: int = 0  # how many times in a row this file's bytes and settings ended in a timeout
    limit: float = 0  # for file.timeout the seconds, for file.memory the MB, it failed under


class Manifest(Model):
    schema_version: str = MANIFEST_VERSION
    ragnaroq: str = __version__
    files: list[FileEntry] = []


class Changes(Model):
    """What differs from the last run. A vector database upserts `chunks_added` and
    `chunks_changed` (ids that stay, in files whose content or verdict changed), and deletes
    `chunks_removed`. On a `reset` there was no usable last run: keep only what is in
    chunks.jsonl."""

    added: list[str] = []  # file ids
    changed: list[str] = []
    removed: list[str] = []
    chunks_added: list[str] = []
    chunks_changed: list[str] = []
    chunks_removed: list[str] = []
    reset: bool = False


class IndexReport(Model):
    manifest: Manifest
    changes: Changes
    failed: int = 0
    needs_review: bool = False
    notes: list[str] = []


def file_id(path: str) -> str:
    return str(uuid.uuid5(NAMESPACE, path))


# ---------------------------------------------------------------- the .ragnaroq folder, by descriptor


@dataclass
class _State:
    """The descriptors of `.ragnaroq` and what is in it. After they are opened, nothing there is
    reached by a path."""

    root: int
    state: int
    files: int
    tmp: int


def _remove_at(name: str, parent: int) -> None:
    """Delete a file, a link (never what it points to) or a folder, inside `parent`."""
    try:
        info = os.lstat(name, dir_fd=parent)
    except FileNotFoundError:
        return
    if stat.S_ISDIR(info.st_mode):
        shutil.rmtree(name, dir_fd=parent)
    else:
        os.unlink(name, dir_fd=parent)


def _open_dir(name: str, parent: int) -> int:
    """A real folder inside `parent`, made (private) if missing and put in the place of a link
    or file that stands there."""
    flags = os.O_RDONLY | _DIRECTORY | _NOFOLLOW
    for _ in range(5):
        try:
            return os.open(name, flags, dir_fd=parent)
        except FileNotFoundError:
            try:
                os.mkdir(name, _PRIVATE_DIR, dir_fd=parent)
            except FileExistsError:
                pass
        except OSError as error:
            if error.errno not in (errno.ELOOP, errno.ENOTDIR, errno.EMLINK):
                raise
            _remove_at(name, parent)
    raise FolderError(f"{name} inside {STATE} keeps changing; run again.")


def _open_state(root: int) -> int:
    flags = os.O_RDONLY | _DIRECTORY | _NOFOLLOW
    try:
        return os.open(STATE, flags, dir_fd=root)
    except FileNotFoundError:
        os.mkdir(STATE, _PRIVATE_DIR, dir_fd=root)
        return os.open(STATE, flags, dir_fd=root)
    except OSError as error:
        if error.errno in (errno.ELOOP, errno.ENOTDIR, errno.EMLINK):
            raise FolderError(
                f"{STATE} in this folder is not a folder, so nothing was written. Remove it and run again."
            ) from None
        raise


def _open_file(name: str, parent: int, *, single: bool = False) -> int:
    """A descriptor for a regular file (with one link, if `single`) that is not a link."""
    fd = os.open(name, os.O_RDONLY | _NOFOLLOW | _NONBLOCK, dir_fd=parent)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or (single and info.st_nlink != 1):
            raise OSError(errno.EINVAL, "not a plain file")
    except BaseException:
        os.close(fd)
        raise
    return fd


def _read_at(name: str, parent: int, limit: int = 1 << 30) -> bytes:
    with os.fdopen(_open_file(name, parent), "rb") as handle:
        return handle.read(limit)


def _lock(state: int) -> int:
    for _ in range(3):
        try:
            fd = os.open("lock", os.O_RDWR | os.O_CREAT | _NOFOLLOW | _NONBLOCK, _PRIVATE_FILE, dir_fd=state)
        except OSError as error:
            if error.errno not in (errno.ELOOP, errno.EISDIR, errno.EMLINK):
                raise
            _remove_at("lock", state)  # a link or folder planted where the lock goes
            continue
        if stat.S_ISREG(os.fstat(fd).st_mode):
            break
        os.close(fd)
        _remove_at("lock", state)
    else:
        raise FolderError("The lock file could not be made.")
    try:
        import fcntl

        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError as error:
        os.close(fd)
        if error.errno in (errno.EAGAIN, errno.EACCES, errno.EWOULDBLOCK):
            raise FolderError(
                "Another ragnaroq index is already running on this folder; wait for it to finish."
            ) from None
        raise FolderError(f"The folder could not be locked ({error.strerror}).") from None
    return fd


def _write_file(held: _State, name: str, write_to: Callable) -> None:
    """Write `.ragnaroq/<name>` through a temporary file, so it is whole or not there."""
    part = f"{name}.part"
    fd = os.open(part, os.O_WRONLY | os.O_CREAT | os.O_EXCL | _NOFOLLOW, _PRIVATE_FILE, dir_fd=held.tmp)
    with os.fdopen(fd, "wb") as handle:
        write_to(handle)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(part, name, src_dir_fd=held.tmp, dst_dir_fd=held.state)


def _load_manifest(held: _State, notes: list[str]) -> Manifest | None:
    try:
        text = _read_at("manifest.json", held.state)
    except FileNotFoundError:
        return None
    except OSError as error:
        notes.append(
            f"The last manifest could not be read ({type(error).__name__}); reading every file again."
        )
        return None
    try:
        manifest = Manifest.model_validate_json(text)
    except (ValidationError, ValueError) as error:
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
    # An entry is only trusted for the id its path gives; a folder is only ever named by an id
    # computed here from a path.
    sound = [entry for entry in manifest.files if entry.file_id == file_id(entry.path)]
    if len(sound) != len(manifest.files):
        notes.append(
            f"{len(manifest.files) - len(sound)} entries of the last manifest were not sound; ignored."
        )
    manifest.files = sound
    return manifest


def _ids_in(handle) -> list[str] | None:
    ids = []
    try:
        for line in handle:
            chunk_id = json.loads(line)["id"]
            if not isinstance(chunk_id, str):
                return None
            ids.append(chunk_id)
    except (ValueError, KeyError, TypeError):
        return None
    return ids


def _chunk_ids_at(folder_fd: int) -> list[str] | None:
    """The ids in `chunks.jsonl` of a file's folder, or None if it is missing, a link, shared
    with another name, or not chunks."""
    try:
        fd = _open_file("chunks.jsonl", folder_fd, single=True)
    except OSError:
        return None
    with os.fdopen(fd, "rb") as handle:
        return _ids_in(handle)


def _existing_dir(name: str, parent: int) -> int | None:
    try:
        return os.open(name, os.O_RDONLY | _DIRECTORY | _NOFOLLOW, dir_fd=parent)
    except OSError:
        return None


def _leftover_ids(held: _State) -> set[str]:
    """The chunk ids of every file folder found, for a run with no usable last manifest."""
    found: set[str] = set()
    with os.scandir(held.files) as listing:
        names = [item.name for item in listing]
    for name in names:
        fd = _existing_dir(name, held.files)
        if fd is None:
            continue
        try:
            found.update(_chunk_ids_at(fd) or [])
        finally:
            os.close(fd)
    return found


# ---------------------------------------------------------------- walking the folder


@dataclass
class Found:
    path: str  # relative, POSIX, as shown
    dirfd: int = -1  # the folder it is in, open while this is the current file
    name: str = ""
    parts: tuple[str, ...] = ()  # its names below the folder
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


def _is_link(name: str, dirfd: int) -> bool:
    try:
        return stat.S_ISLNK(os.lstat(name, dir_fd=dirfd).st_mode)
    except OSError:
        return False


def walk(root: int) -> Iterator[Found]:
    """Every file under the folder `root` (a descriptor), or the reason it is left out. Links to
    folders are not followed; hidden things and `.ragnaroq` are not entered."""
    unreadable: list[str] = []

    def lost() -> Iterator[Found]:
        while unreadable:
            relative = os.path.relpath(unreadable.pop(0), ".").replace(os.sep, "/")
            yield Found(_shown(relative), skipped="unreadable folder")

    for here, names, files, dirfd in os.fwalk(
        ".", dir_fd=root, follow_symlinks=False, onerror=lambda e: unreadable.append(e.filename)
    ):
        yield from lost()
        relative = os.path.relpath(here, ".")
        above = () if relative == "." else tuple(relative.split(os.sep))
        prefix = "".join(f"{part}/" for part in above)
        keep = []
        for name in sorted(names):
            if not above and name == STATE:
                continue
            if _bad_name(name):
                yield Found(_shown(prefix + name), skipped="unreadable name")
            elif name.startswith("."):
                yield Found(prefix + name, skipped="hidden")
            elif _is_link(name, dirfd):
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
                yield Found(prefix + name, dirfd, name, (*above, name))
    yield from lost()


def _checked(fd: int) -> tuple[int | None, str]:
    if not stat.S_ISREG(os.fstat(fd).st_mode):
        os.close(fd)
        return None, "not a regular file"
    return fd, ""


def _open_found(found: Found, root: int, real_root: str) -> tuple[int | None, str, tuple[str, ...]]:
    """A descriptor for the regular file `found` is, or the reason it is left out, and its
    names below the folder. A file is opened where it was listed, never through a link. A link
    is resolved, must lead to a file inside the folder, and is then opened from the folder one
    name at a time, never through a link."""
    try:
        info = os.lstat(found.name, dir_fd=found.dirfd)
    except OSError:
        return None, "unreadable", ()
    flags = os.O_RDONLY | _NOFOLLOW | _NONBLOCK
    if stat.S_ISREG(info.st_mode):
        try:
            fd = os.open(found.name, flags, dir_fd=found.dirfd)
        except OSError:
            return None, "unreadable", ()
        return (*_checked(fd), found.parts)
    if not stat.S_ISLNK(info.st_mode):  # a fifo, a socket or a device is never opened
        return None, "not a regular file", ()
    resolved = os.path.realpath(os.path.join(real_root, *found.parts))
    try:
        below = os.path.relpath(resolved, real_root).split(os.sep)
    except ValueError:
        return None, "outside the folder", ()
    if below[0] == "..":
        return None, "outside the folder", ()
    if below == ["."]:
        return None, "symbolic link to a folder", ()
    if any(part.startswith(".") for part in below):
        return None, "link to a hidden or internal file", ()
    held = [os.dup(root)]
    try:
        for name in below[:-1]:
            held.append(os.open(name, flags | _DIRECTORY, dir_fd=held[-1]))
        fd = os.open(below[-1], flags, dir_fd=held[-1])
    except FileNotFoundError:
        return None, "broken symbolic link", ()
    except OSError:
        return None, "unreadable", ()
    finally:
        for descriptor in held:
            os.close(descriptor)
    if stat.S_ISDIR(os.fstat(fd).st_mode):
        os.close(fd)
        return None, "symbolic link to a folder", ()
    return (*_checked(fd), tuple(below))


def _kind(path: str, head: bytes) -> tuple[str, tuple[str, str] | None]:
    """What a file is, from its first bytes alone, and the failure if it says it is a format and
    is not. A zip is never opened here: by its name it is Word, Excel or PowerPoint, or nothing,
    and the worker finds out."""
    suffix = Path(path).suffix.lower().lstrip(".")
    if head.startswith(b"PK\x03\x04"):
        return (suffix, None) if suffix in OFFICE else ("", None)
    fmt = detect(path, head) or ""
    if fmt:
        return fmt, None
    if suffix in CLAIMED:  # it says what it is, and it is not
        return suffix, ("format.unknown", f"{path} is not a valid {suffix} file.")
    return "", None


@dataclass
class Read:
    data: bytes | None = None
    fmt: str = ""
    skip: str = ""
    size: int = 0
    base: tuple | None = None  # (folder, names below it) for the images a file names
    failure: tuple[str, str] | None = None


def _read_file(found: Found, root: int, real_root: str, config: Config) -> Read:
    """The file's bytes, read while it did not change, or why it is left out. Only the first
    64 KiB are read to tell what it is; a file that is not a format we read is not read further."""
    limit = config.max_bytes
    for _ in range(READ_TRIES):
        fd, skip, parts = _open_found(found, root, real_root)
        if skip:
            return Read(skip=skip)
        with os.fdopen(fd, "rb") as handle:
            before = os.fstat(fd)
            if before.st_size == 0:
                return Read(skip="empty")
            if before.st_size > limit:
                return Read(skip="over the size limit", size=before.st_size)
            head = handle.read(HEAD)
            fmt, failure = _kind(found.path, head)
            if not fmt:
                return Read(skip="unsupported", size=before.st_size)
            data = head + handle.read(limit + 1 - len(head))
            after = os.fstat(fd)
        if len(data) > limit:
            return Read(skip="over the size limit", size=len(data))
        if (before.st_size, before.st_mtime_ns) == (after.st_size, after.st_mtime_ns) and len(
            data
        ) == after.st_size:
            return Read(data=data, fmt=fmt, size=len(data), base=(real_root, parts[:-1]), failure=failure)
    message = f"The file could not be read (changed {READ_TRIES} times while it was being read)."
    return Read(failure=("file.changed", message))


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


def _settings(
    config: Config, engines: Sequence[OcrEngine], third_reader: LineReader | None, describer=None
) -> dict:
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
        "describe": _identity(describer) if describer is not None else None,
    }


NO_FIGURES = {"pdf", "csv", "json", "xml"}


def _hash(settings: dict, fmt: str) -> str:
    if fmt in NO_FIGURES:  # nothing to describe: the describer is not part of how they are read
        settings = {key: value for key, value in settings.items() if key != "describe"}
    body = json.dumps({**settings, "reader": _reader_name(fmt), "format": fmt}, sort_keys=True)
    return hashlib.sha256(body.encode()).hexdigest()


@dataclass
class _Context:
    config: Config
    engines: Sequence[OcrEngine]
    third_reader: LineReader | None
    worker: FileReader
    held: _State = field(default=None)
    describer: object = None  # writes a sentence for each image; None leaves images as they are
    described_failed: int = 0  # images it failed on, for the run's notes


def _make(context: _Context, found: Found, read: Read, out: Path) -> tuple:
    """Read one file into `out` (a private folder). Returns the document, or the (rule, message)
    it failed with."""
    config = context.config
    started = time.monotonic()
    try:
        if read.fmt == "pdf":
            document = convert(
                read.data,
                config=config,
                engines=context.engines,
                filename=found.path,
                third_reader=context.third_reader,
            )
        else:
            result = isolation.read_file(read.data, found.path, config, base=read.base, reader=context.worker)
            deadline = started + config.file_timeout_seconds
            figures.describe(result, context.engines, config, deadline=deadline, describer=context.describer)
            context.described_failed += result.cache.get(figures.DESCRIBE_FAILED, 0)
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
    with open(out / "chunks.jsonl", "rb") as handle:  # in a private folder made by this run
        entry.chunk_ids = _ids_in(handle) or []
    entry.counts = {**document.summary, "chunks": len(entry.chunk_ids)}
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


def _carry(
    held: _State, old: FileEntry | None, found: Found, sha: str, settings: str, config: Config
) -> FileEntry | None:
    """The last run's entry, if this file need not be read again: same bytes, same settings,
    and its output is still there, is the output of those bytes, and is what the manifest says."""
    if old is None or old.status == "skipped" or old.sha256 != sha or old.settings != settings:
        return None
    if old.status == "failed" and (
        old.rule in TRANSIENT or (old.rule == "file.timeout" and old.attempts < TIMEOUT_TRIES)
    ):
        return None
    # A failure of a limit is only left alone while the limit is not raised.
    now = {"file.timeout": config.file_timeout_seconds, "file.memory": config.page_memory_mb}
    if old.status == "failed" and old.rule in now and now[old.rule] > old.limit:
        return None
    folder = _existing_dir(old.file_id, held.files)
    if folder is None:
        return None
    try:
        stored = FileEntry.model_validate_json(_read_at("entry.json", folder, 1 << 26))
        os.close(_open_file("document.json", folder))
        ids = _chunk_ids_at(folder)
    except (OSError, ValidationError, ValueError):
        return None
    finally:
        os.close(folder)
    if ids is None or ids != old.chunk_ids:
        return None
    if (stored.path, stored.sha256, stored.settings) != (found.path, sha, settings):
        return None
    return old.model_copy(deep=True)


def _copy_into(source: str, destination: int) -> None:
    """Copy the files and folders of the private folder `source` into the folder `destination`
    (a descriptor), made private."""
    with os.scandir(source) as listing:
        for item in list(listing):
            if item.is_symlink():
                continue
            if item.is_dir(follow_symlinks=False):
                os.mkdir(item.name, _PRIVATE_DIR, dir_fd=destination)
                inner = os.open(item.name, os.O_RDONLY | _DIRECTORY | _NOFOLLOW, dir_fd=destination)
                try:
                    _copy_into(item.path, inner)
                finally:
                    os.close(inner)
            elif item.is_file(follow_symlinks=False):
                fd = os.open(
                    item.name,
                    os.O_WRONLY | os.O_CREAT | os.O_EXCL | _NOFOLLOW,
                    _PRIVATE_FILE,
                    dir_fd=destination,
                )
                with open(item.path, "rb") as origin, os.fdopen(fd, "wb") as target:
                    shutil.copyfileobj(origin, target)


def _install(held: _State, out: Path, entry: FileEntry) -> None:
    """Put a file's output in `files/<id>`: copied into `tmp/`, then renamed over the old one."""
    (out / "entry.json").write_text(entry.model_dump_json(indent=2), encoding="utf8")
    _remove_at(entry.file_id, held.tmp)
    os.mkdir(entry.file_id, _PRIVATE_DIR, dir_fd=held.tmp)
    inner = os.open(entry.file_id, os.O_RDONLY | _DIRECTORY | _NOFOLLOW, dir_fd=held.tmp)
    try:
        _copy_into(os.fspath(out), inner)
    finally:
        os.close(inner)
    _remove_at(entry.file_id, held.files)
    os.rename(entry.file_id, entry.file_id, src_dir_fd=held.tmp, dst_dir_fd=held.files)


def _sweep(held: _State, live: set[str]) -> None:
    """Remove what is in `files/` and is not a live file's folder."""
    with os.scandir(held.files) as listing:
        names = [item.name for item in listing]
    for name in names:
        if name not in live:
            _remove_at(name, held.files)


def _present(manifest: Manifest | None) -> dict[str, FileEntry]:
    return {e.file_id: e for e in manifest.files if e.status != "skipped"} if manifest else {}


def compare(old: Manifest | None, new: Manifest, leftover: set[str] = frozenset()) -> Changes:
    """What differs. Without a last manifest everything is added, and `leftover` (ids found in
    the folders of an earlier run) that are not in the new set are the chunks removed."""
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
    old_ids = {c for e in before.values() for c in e.chunk_ids} if old else set(leftover)
    new_ids = {c for e in after.values() for c in e.chunk_ids}
    kept = {c for key in changed for c in set(before[key].chunk_ids) & set(after[key].chunk_ids)}
    return Changes(
        added=[key for key in after if key not in before],
        changed=changed,
        removed=[key for key in before if key not in after],
        chunks_added=sorted(new_ids if old is None else new_ids - old_ids),
        chunks_changed=sorted(kept),
        chunks_removed=sorted(old_ids - new_ids),
        reset=old is None,
    )


def _copy_chunks(held: _State, entries: list[FileEntry]):
    def write_to(handle) -> None:
        for entry in entries:
            if not entry.chunk_ids:
                continue
            folder = _existing_dir(entry.file_id, held.files)
            if folder is None:
                raise OSError(errno.ENOENT, f"the output of {entry.path} is gone")
            try:
                with os.fdopen(_open_file("chunks.jsonl", folder, single=True), "rb") as source:
                    shutil.copyfileobj(source, handle)
            finally:
                os.close(folder)

    return write_to


def index(
    root: Path | str,
    config: Config,
    *,
    engines: Sequence[OcrEngine] | None = None,
    third_reader: LineReader | None = None,
    describer=None,
    fresh: bool = False,
    progress: Callable[[str, str], None] | None = None,
) -> IndexReport:
    """Index the folder `root` (see the module's docstring). `engines=None` uses the OCR
    engines installed here; `[]` reads text layers only. `describer` (off by default) writes a
    sentence for each image, kept as a label. `fresh` reads every file again."""
    if os.name != "posix" or not hasattr(os, "fwalk"):
        raise FolderError("ragnaroq index works on Linux and macOS only in this version.")
    root = Path(root)
    with contextlib.ExitStack() as stack:
        try:
            root_fd = os.open(root, os.O_RDONLY | _DIRECTORY)
        except OSError:
            raise FolderError(f"{root} is not a folder.") from None
        stack.callback(os.close, root_fd)
        state = _open_state(root_fd)
        stack.callback(os.close, state)
        stack.callback(os.close, _lock(state))  # the lock goes with it
        files = _open_dir("files", state)
        stack.callback(os.close, files)
        _remove_at("tmp", state)  # what an interrupted run left
        tmp = _open_dir("tmp", state)
        stack.callback(os.close, tmp)
        held = _State(root_fd, state, files, tmp)
        return _run(held, os.path.realpath(root), config, engines, third_reader, describer, fresh, progress)


def _run(held: _State, real_root, config, engines, third_reader, describer, fresh, progress) -> IndexReport:
    notes: list[str] = []
    if engines is None:
        engines = available(config.languages)
    engines = list(engines)
    old = _load_manifest(held, notes)
    leftover = set() if old else _leftover_ids(held)
    before = {entry.file_id: entry for entry in old.files} if old else {}
    settings = _settings(config, engines, third_reader, describer)
    entries: list[FileEntry] = []

    def done(entry: FileEntry) -> None:
        entries.append(entry)
        if progress:
            try:
                progress(entry.path, entry.status)
            except OSError:  # a closed pipe, a full disk: reporting is not the work
                pass

    with FileReader(config) as worker:
        context = _Context(config, engines, third_reader, worker, held, describer)

        def process(found: Found, read: Read, hashed: str, previous: FileEntry | None) -> FileEntry:
            private = tempfile.mkdtemp(prefix="ragnaroq-")  # private, and never inside the folder
            try:
                out = Path(private) / "out"
                if read.failure is not None:
                    document, failure = None, read.failure
                else:
                    document, failure = _make(context, found, read, out)
                if failure is not None:
                    _fail_output(found, read.data or b"", read.fmt, failure, config, out)
                entry = _entry_of(found, read.data or b"", read.fmt, hashed, document, failure, out)
                if read.data is None:
                    entry.sha256 = ""  # nothing was read, so nothing can match it
                if document is not None:  # what the reader that read it says it was, not its name
                    used = document.source.format
                    entry.format, entry.reader, entry.settings = (
                        used,
                        _reader_name(used),
                        _hash(settings, used),
                    )
                limits = {"file.timeout": config.file_timeout_seconds, "file.memory": config.page_memory_mb}
                if entry.rule in limits:
                    entry.limit = float(limits[entry.rule])
                if entry.rule == "file.timeout":
                    again = previous is not None and previous.sha256 == entry.sha256
                    again = again and previous.settings == entry.settings and previous.limit == entry.limit
                    entry.attempts = previous.attempts + 1 if again else 1
                _install(held, out, entry)
                return entry
            finally:
                shutil.rmtree(private, ignore_errors=True)

        for found in walk(held.root):
            identity = file_id(found.path)
            if found.skipped:
                read = Read(skip=found.skipped)
            else:
                read = _read_file(found, held.root, real_root, config)
            if read.skip:
                skipped = FileEntry(path=found.path, file_id=identity, bytes=read.size, status="skipped")
                skipped.skipped = read.skip
                done(skipped)
                continue
            if read.data is None:  # it changed under every attempt
                done(process(found, read, "", None))
                continue
            sha = hashlib.sha256(read.data).hexdigest()
            hashed = _hash(settings, read.fmt)
            previous = before.get(identity)
            # What was recorded is settled for the format the reader found, which a name may not tell.
            recorded = _hash(settings, previous.format) if previous and previous.format else hashed
            carried = None if fresh else _carry(held, previous, found, sha, recorded, config)
            done(carried if carried is not None else process(found, read, hashed, previous))
            read = None

    if context.described_failed:
        notes.append(f"{context.described_failed} images could not be described by the model.")
    entries.sort(key=lambda entry: entry.path)
    manifest = Manifest(files=entries)
    changes = compare(old, manifest, leftover)
    _write_file(held, "chunks.jsonl", _copy_chunks(held, entries))
    _write_file(held, "changes.json", lambda h: h.write(changes.model_dump_json(indent=2).encode()))
    _write_file(held, "manifest.json", lambda h: h.write(manifest.model_dump_json(indent=2).encode()))
    _sweep(held, {entry.file_id for entry in entries if entry.status != "skipped"})
    _remove_at("tmp", held.state)
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
