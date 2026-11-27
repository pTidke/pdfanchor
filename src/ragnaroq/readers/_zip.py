"""Limits for zip-based files (xlsx, docx, pptx), checked before any library opens them."""

import io
import zipfile

from ..config import Config
from . import ReaderRefused


def check(data: bytes, config: Config) -> None:
    """Raises `ReaderRefused("file.zip-limits", …)` when the archive holds too many entries or
    would unpack to more than `config.max_unzipped_bytes`. Only the directory is read."""
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            entries = archive.infolist()
    except zipfile.BadZipFile as error:
        raise ReaderRefused("file.zip-invalid", f"The file is not a readable zip archive: {error}") from error
    if len(entries) > config.max_zip_entries:
        raise ReaderRefused(
            "file.zip-limits",
            f"The archive holds {len(entries)} entries; the limit is {config.max_zip_entries}.",
        )
    total = sum(entry.file_size for entry in entries)
    if total > config.max_unzipped_bytes:
        raise ReaderRefused(
            "file.zip-limits",
            f"The archive unpacks to {total} bytes; the limit is {config.max_unzipped_bytes}.",
        )
