"""What every reader of a format with exact text shares: starting a document, adding
anchored lines, and finishing it. Core only; nothing here imports a format library."""

import hashlib
import platform
import re
from datetime import UTC, datetime

from .. import __version__, policy, structure
from ..anchors import Anchor
from ..config import Config
from ..model import CellValue, Document, Engine, Line, Page, Source

_PICTURE_WORDS = (
    "picture|pic|image|img|photo|photograph|graphic|figure|fig|illustration|drawing|screenshot|"
    "screen shot|untitled"
)
# A word that only says a picture is here, with the number a program adds ("Picture 3", "Image1").
_PICTURE_NAME = re.compile(rf"(?:{_PICTURE_WORDS})[\s_.#-]*\d*", re.IGNORECASE)
# A file name or path ("IMG_2041.JPG", "media/image3.emf", "C:\Users\...\scan.jpeg").
_FILE_NAME = re.compile(
    r"[\w\s\-.()\[\]~/\\:%+,&@'#]*\.(?:png|jpe?g|jpe|gif|bmp|dib|tiff?|webp|svg|emf|wmf|ico|heic|heif|avif|jp2)",
    re.IGNORECASE,
)


def alt_text(*candidates: str | None) -> str:
    """The first alt text that describes its picture, or "". An alt text is not a description when
    it only says a picture is here ("Picture", "Image 3", "Figure 2"), is a file name or path
    ("chart.png"), or has no letter or digit: openpyxl writes "Picture" on every image, and
    python-pptx the picture's file name. A picture without one is read by OCR instead."""
    for candidate in candidates:
        text = " ".join((candidate or "").split())
        if not any(ch.isalnum() for ch in text):
            continue
        if _PICTURE_NAME.fullmatch(text) or _FILE_NAME.fullmatch(text):
            continue
        return candidate
    return ""


def start(data: bytes, name: str, fmt: str, config: Config, components: dict[str, str]) -> Document:
    """An empty document with one body page named after the file."""
    return Document(
        source=Source(
            sha256=hashlib.sha256(data).hexdigest(), bytes=len(data), filename=name, pages=1, format=fmt
        ),
        engine=Engine(
            name="ragnaroq",
            version=__version__,
            policy_version=config.policy.version,
            config_sha256=config.sha256(),
            components={"python": platform.python_version(), **components},
        ),
        created_at=datetime.now(UTC).isoformat(timespec="seconds"),
        pages=[Page(number=1, kind="body", name=name)],
    )


def decode(data: bytes) -> tuple[str, bool]:
    """The text of a text file, and whether the encoding was a guess: UTF-16 with a byte-order
    mark, else UTF-8 (a BOM is dropped), else Windows-1252."""
    if data.startswith((b"\xff\xfe", b"\xfe\xff")):
        return data.decode("utf-16"), False
    try:
        return data.decode("utf-8-sig"), False
    except UnicodeDecodeError:
        return data.decode("cp1252", errors="replace"), True


def next_paragraph(page: Page) -> int:
    """The number after the page's last paragraph. Readers add lines in order, so the last
    line holds the highest number; this avoids rescanning every line."""
    return page.lines[-1].paragraph + 1 if page.lines else 1


def add_line(
    page: Page,
    text: str,
    source: str,
    anchor: Anchor,
    *,
    role="body",
    table_id: str | None = None,
    value: CellValue | None = None,
    paragraph: int | None = None,
    level: int = 0,
) -> Line:
    """Append a line with id `p{page}-l{n}`. Without `paragraph` it starts a paragraph of its own."""
    n = len(page.lines) + 1
    if paragraph is None:
        paragraph = next_paragraph(page)
    line = Line(
        id=f"p{page.number}-l{n}",
        page=page.number,
        order=n - 1,
        anchor=anchor,
        value=value,
        raw=text,
        text=text,
        source=source,
        role=role,
        level=level,
        paragraph=paragraph,
        table_id=table_id,
    )
    page.lines.append(line)
    return line


def finish(document: Document, config: Config) -> Document:
    """Scripts, exclusions, paragraph numbers and title, then the verdict.

    Not `structure.apply`: that reads PDF boxes and font sizes and merges lines. The role
    a reader set is kept."""
    for page in document.pages:
        numbered = max((line.paragraph for line in page.lines), default=0)
        for line in page.lines:
            line.scripts = structure.scripts_of(line.text)
            if line.scripts and config.exclude_scripts and set(line.scripts) <= set(config.exclude_scripts):
                line.excluded = "script:" + "+".join(line.scripts)
            if line.paragraph == 0:
                numbered += 1
                line.paragraph = numbered
    document.title = structure.title(document)
    policy.decide(document, config.policy)
    return document
