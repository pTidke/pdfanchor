"""What every reader of a format with exact text shares: starting a document, adding
anchored lines, and finishing it. Core only; nothing here imports a format library."""

import hashlib
import platform
from datetime import UTC, datetime

from .. import __version__, policy, structure
from ..anchors import Anchor
from ..config import Config
from ..model import CellValue, Document, Engine, Line, Page, Source


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
) -> Line:
    """Append a line with id `p{page}-l{n}`. Without `paragraph` it starts a paragraph of its own."""
    n = len(page.lines) + 1
    if paragraph is None:
        paragraph = max((line.paragraph for line in page.lines), default=0) + 1
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
