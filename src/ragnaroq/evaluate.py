"""Measure how often the engine is wrong, on references whose correct text is known.

The number that matters is not overall accuracy but this: of the lines the engine
*accepted* (so no person looks at them), how many are wrong, and how many are wrong
on a number, a date or a negation. The cost on the other side is how much of the
work is sent to a person although it was right.

Lines are matched to the truth by position, word by word: each true word belongs to
the line whose box contains its centre. So a line the engine split or merged
differently from the original is still compared with exactly the words it covers.
"""

import functools
import hashlib
import json
import unicodedata
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from pathlib import Path

from . import expected, policy, reference, structure
from .config import Config
from .crosscheck import LEADERS, TYPOGRAPHY, canonical, compare, critical_tokens
from .model import Document, Line
from .readers import ReaderRefused, refused_document
from .reference import Truth, Word

MARGIN = 3.0  # points around a line's box that still count as inside
# The form of a stored reading, in the cache key. It changes only when a stored reading can no
# longer be decided again like a fresh one; a new schema that still loads it is not such a change
# (0.3.0 loads 0.2 readings), so stored PDF readings, hours of OCR, keep their keys.
READING_FORMAT = "0.2.0"
THRESHOLDS = (0.80, 0.85, 0.88, 0.90, 0.92, 0.94, 0.96, 0.98, 1.00)


def critical(text: str):
    """The critical tokens of `text`, with the same typography forgiven as `canonical`:
    "Steps 2–4" and "Steps 2-4" name the same steps."""
    return critical_tokens(LEADERS.sub(" ", text.translate(TYPOGRAPHY)))


def distance(a: str, b: str) -> int:
    """Edit distance in characters (insertions, deletions, substitutions)."""
    if len(a) < len(b):
        a, b = b, a
    previous = list(range(len(b) + 1))
    for i, x in enumerate(a, 1):
        current = [i]
        for j, y in enumerate(b, 1):
            current.append(min(previous[j] + 1, current[j - 1] + 1, previous[j - 1] + (x != y)))
        previous = current
    return previous[-1]


@dataclass(frozen=True)
class LineScore:
    page: int
    line: str
    status: str
    read: str
    expected: str
    exact: bool  # the same text, after `canonical`
    critical_ok: bool  # the same numbers, dates and negations
    errors: int  # character edits from read to expected
    length: int  # characters expected
    eligible: bool  # passes every rule except the agreement threshold
    min_similarity: float | None  # lowest similarity to the other readings
    critical_agree: bool  # the readings agree on every critical token
    letters_agree: bool = True  # the readings have the same letters and digits
    settled: bool = False  # the third reader settled the line
    held_back: str | None = None  # the check that kept the third reader's reading from settling it

    def accepted_at(self, threshold: float, same_letters: bool = False) -> bool:
        return (
            self.eligible
            and self.critical_agree
            and self.min_similarity is not None
            and self.min_similarity >= threshold
            and (self.letters_agree or not same_letters)
        )


@dataclass
class Tally:
    lines: int = 0
    accepted: int = 0
    flagged: int = 0
    blocked: int = 0
    accepted_wrong: int = 0
    accepted_critical_wrong: int = 0
    flagged_right: int = 0
    extra_lines: int = 0
    truth_words: int = 0
    missed_words: int = 0
    char_errors: int = 0
    char_total: int = 0
    ignored_lines: int = 0  # lines over text the reference cannot vouch for
    third_settled: int = 0
    third_settled_wrong: int = 0
    third_settled_critical_wrong: int = 0
    third_held_no_match: int = 0
    third_held_validators: int = 0
    third_held_omission: int = 0
    third_held_underscore: int = 0
    third_held_neighbour: int = 0

    def add(self, other: "Tally") -> None:
        for key, value in asdict(other).items():
            setattr(self, key, getattr(self, key) + value)

    def rates(self) -> dict[str, float | None]:
        def share(part: int, whole: int) -> float | None:
            return round(part / whole, 4) if whole else None

        return {
            "accepted_share": share(self.accepted, self.lines),
            "false_accept_rate": share(self.accepted_wrong, self.accepted),
            "critical_false_accept_rate": share(self.accepted_critical_wrong, self.accepted),
            "review_share": share(self.flagged + self.blocked, self.lines),
            "needless_review_share": share(self.flagged_right, self.flagged),
            "character_error_rate": share(self.char_errors, self.char_total),
            "missed_word_share": share(self.missed_words, self.truth_words),
        }


@dataclass
class FileScore:
    reference: str
    profile: str
    lines: list[LineScore] = field(default_factory=list)
    tally: Tally = field(default_factory=Tally)


def _excluded(word: Word, scripts: tuple[str, ...]) -> bool:
    found = structure.scripts_of(word.text)
    return bool(found) and set(found) <= set(scripts)


def _inside(word: Word, line: Line) -> bool:
    x, y = word.centre
    box = line.box
    return box.x0 - MARGIN <= x <= box.x1 + MARGIN and box.top - MARGIN <= y <= box.bottom + MARGIN


def _assign(words: list[Word], lines: list[Line]) -> tuple[dict[str, list[Word]], list[Word]]:
    """Each true word goes to a line whose box contains its centre.

    Where boxes overlap (a ruled form, a tilted page, a stray mark that stretches a
    box), the word goes to the line whose middle is nearest it vertically; between
    lines about as near, to one that read the word. So a word is not blamed on a
    neighbouring line that merely overlaps it."""
    owned: dict[str, list[Word]] = {line.id: [] for line in lines}
    missed = []
    read = {line.id: canonical(line.text) for line in lines}
    for word in words:
        holders = [line for line in lines if _inside(word, line)]
        if not holders:
            missed.append(word)
            continue
        wanted = canonical(word.text)
        _, y = word.centre
        ranked = [
            (
                round(abs(y - (line.box.top + line.box.bottom) / 2) / 2),
                not (wanted and wanted in read[line.id]),
                n,
            )
            for n, line in enumerate(holders)
        ]
        owned[holders[min(ranked)[2]].id].append(word)
    return owned, missed


def _line_score(line: Line, words: list[Word]) -> LineScore:
    expected = " ".join(word.text for word in sorted(words, key=lambda word: word.x0))
    read_c, expected_c = canonical(line.text), canonical(expected)
    others = [
        reading
        for reading in line.readings
        if reading.source != line.source and not reading.source.startswith(policy.VLM)
    ]
    prefix = f"{policy.THIRD}."
    held = next((flag.rule.removeprefix(prefix) for flag in line.flags if flag.rule.startswith(prefix)), None)
    # The first reader's own reading: after a correction `text` holds another reader's,
    # which would agree with itself at every threshold.
    first = unicodedata.normalize("NFC", line.raw)
    agreements = [compare(first, other.text) for other in others]
    return LineScore(
        page=line.page,
        line=line.id,
        status=line.status,
        read=line.text,
        expected=expected,
        exact=read_c == expected_c,
        critical_ok=critical(line.text) == critical(expected),
        errors=distance(read_c, expected_c),
        length=len(expected_c),
        eligible=bool(others) and not any(
            flag.rule != "crosscheck.disagreement" and not flag.rule.startswith(policy.THIRD)
            for flag in line.flags
        ),
        min_similarity=min((a.similarity for a in agreements), default=None),
        critical_agree=all(a.critical_equal for a in agreements),
        letters_agree=all(a.letters_equal for a in agreements),
        settled=line.resolution is not None and line.resolution.rule == policy.THIRD,
        held_back=held,
    )


def score(
    document: Document, truth: Truth, *, exclude_scripts: tuple[str, ...] = (), name: str = ""
) -> FileScore:
    result = FileScore(
        reference=name or document.source.filename, profile=str(truth.meta.get("profile", {}).get("name", ""))
    )
    tally = result.tally
    pages = {page.number: page for page in document.pages}
    for truth_page in truth.pages:
        # A word that is only leader dots or a fill line has nothing to read.
        words = [
            word for word in truth_page.words if canonical(word.text) and not _excluded(word, exclude_scripts)
        ]
        tally.truth_words += len(words)
        page = pages.get(truth_page.number)
        lines = [line for line in page.lines if not line.excluded] if page else []
        owned, missed = _assign(words, lines)
        tally.missed_words += len(missed)
        shunned, _ = _assign(list(truth_page.ignored), lines)
        for line in lines:
            if not owned[line.id] and shunned[line.id]:
                tally.ignored_lines += 1
                continue
            line_score = _line_score(line, owned[line.id])
            result.lines.append(line_score)
            tally.lines += 1
            tally.char_errors += line_score.errors
            tally.char_total += line_score.length
            if not line_score.expected:
                tally.extra_lines += 1
            if line_score.settled:
                tally.third_settled += 1
                tally.third_settled_wrong += not line_score.exact
                tally.third_settled_critical_wrong += not line_score.critical_ok
            elif line_score.held_back:
                key = f"third_held_{line_score.held_back.replace('-', '_')}"
                setattr(tally, key, getattr(tally, key) + 1)
            if line.status == "accepted":
                tally.accepted += 1
                tally.accepted_wrong += not line_score.exact
                tally.accepted_critical_wrong += not line_score.critical_ok
            elif line.status == "flagged":
                tally.flagged += 1
                tally.flagged_right += line_score.exact
            else:
                tally.blocked += 1
    return result


def sweep(lines: list[LineScore], thresholds=THRESHOLDS, same_letters: bool = False) -> list[dict]:
    """What the agreement threshold would change, with every other rule kept as it is."""
    rows = []
    for threshold in thresholds:
        accepted = [line for line in lines if line.accepted_at(threshold, same_letters)]
        rows.append(
            {
                "threshold": threshold,
                "accepted": len(accepted),
                "accepted_wrong": sum(not line.exact for line in accepted),
                "accepted_critical_wrong": sum(not line.critical_ok for line in accepted),
                "review_share": round(1 - len(accepted) / len(lines), 4) if lines else None,
            }
        )
    return rows


@dataclass
class Report:
    engines: dict[str, str]
    min_similarity: float
    files: list[FileScore]
    total: Tally
    sweep: list[dict]
    exact: "ExactReport | None" = None  # files scored by anchor, from *.expected.jsonl

    def accepted_but_wrong(self) -> list[LineScore]:
        """Every accepted line that is wrong, those wrong on a critical token first."""
        wrong = [line for f in self.files for line in f.lines if line.status == "accepted" and not line.exact]
        return sorted(wrong, key=lambda line: line.critical_ok)

    def to_dict(self) -> dict:
        wrong = self.accepted_but_wrong()
        return {
            "engines": self.engines,
            "policy": {"min_similarity": self.min_similarity},
            "total": {**asdict(self.total), **self.total.rates()},
            "files": [
                {"reference": f.reference, "profile": f.profile, **asdict(f.tally), **f.tally.rates()}
                for f in self.files
            ],
            "sweep": self.sweep,
            "accepted_but_wrong": [asdict(line) for line in wrong],
            **({"exact": [_exact_dict(f) for f in self.exact.files]} if self.exact else {}),
        }


def run(
    folder: str | Path,
    *,
    config: Config | None = None,
    engines=None,
    progress=None,
    cache: str | Path | None = None,
    third_reader=None,
) -> Report:
    """Convert every reference in `folder` (`*.truth.json` next to its `.pdf`) and score it.

    `progress`, if given, is called as progress(done, total, name) after each reference.
    `cache`, if given, is a folder where each reading is kept, keyed by the PDF's
    bytes, the reading settings, the readers' versions and READING_FORMAT. A stored reading is decided
    again under the current rules and policy, so a change to the rules, the threshold
    or the scoring takes seconds, not a new reading."""
    from . import readers
    from .ocr import available
    from .pipeline import convert
    from .third import read_disputed

    config = config or Config()
    # HTML and Markdown read a local image from beside the file, so they are told the folder.
    read_exact = functools.partial(readers.read_bytes, base=Path(folder))
    exact = run_exact(folder, read_exact) if next(Path(folder).glob("*.expected.jsonl"), None) else None
    if exact is not None and next(Path(folder).glob("*.truth.json"), None) is None:
        return Report(engines={}, min_similarity=config.policy.min_similarity, files=[], total=Tally(),
                      sweep=[], exact=exact)
    engines = available(config.languages) if engines is None else list(engines)
    readers = "+".join(f"{engine.name}={engine.version()}" for engine in engines)
    files: list[FileScore] = []
    total = Tally()
    names: dict[str, str] = {}
    references = sorted(Path(folder).glob("*.truth.json"))
    for done, truth_path in enumerate(references, 1):
        pdf_path = truth_path.with_name(truth_path.name.removesuffix(".truth.json") + ".pdf")
        if not pdf_path.exists():
            raise FileNotFoundError(f"{truth_path.name} has no {pdf_path.name} beside it.")
        document = _converted(pdf_path, config, engines, readers, cache, convert)
        if third_reader is not None:
            # After the stored reading, so what is kept is the base reading.
            read_disputed(
                document,
                pdf_path.read_bytes(),
                config,
                third_reader,
                Path(cache) / "third" if cache else None,
            )
            policy.decide(document, config.policy)
            document.title = structure.title(document)  # a settled line may now carry other text
            document.engine.components[f"vlm:{third_reader.name}"] = third_reader.version()
        names.update(
            {
                key: value
                for key, value in document.engine.components.items()
                if key.startswith(("ocr:", policy.VLM))
            }
        )
        result = score(
            document, reference.load(truth_path), exclude_scripts=config.exclude_scripts, name=pdf_path.name
        )
        files.append(result)
        total.add(result.tally)
        if progress:
            progress(done, len(references), pdf_path.name)
    if not files:
        raise FileNotFoundError(f"No *.truth.json references in {folder}.")
    return Report(
        engines=names,
        min_similarity=config.policy.min_similarity,
        files=files,
        total=total,
        sweep=sweep([line for f in files for line in f.lines], same_letters=config.policy.same_letters),
        exact=exact,
    )


def _converted(pdf_path: Path, config: Config, engines, readers: str, cache, convert) -> Document:
    if cache is None:
        return convert(pdf_path, config=config, engines=engines)
    key = hashlib.sha256(
        b"|".join(
            [
                pdf_path.read_bytes(),
                config.reading_sha256().encode(),
                readers.encode(),
                READING_FORMAT.encode(),
            ]
        )
    ).hexdigest()[:24]
    stored = Path(cache) / f"{pdf_path.stem}.{key}.json"
    if stored.exists():
        document = Document.model_validate_json(stored.read_text(encoding="utf8"))
        policy.decide(document, config.policy)
        return document
    document = convert(pdf_path, config=config, engines=engines)
    stored.parent.mkdir(parents=True, exist_ok=True)
    stored.write_text(document.model_dump_json(), encoding="utf8")
    return document


def _pct(value: float | None) -> str:
    return "—" if value is None else f"{value * 100:.2f}%"


def _third_section(t: Tally) -> list[str]:
    held = {
        "no match": t.third_held_no_match,
        "validators": t.third_held_validators,
        "omission": t.third_held_omission,
        "underscore": t.third_held_underscore,
        "neighbour": t.third_held_neighbour,
    }
    if not (t.third_settled or any(held.values())):
        return []
    return [
        "## Third reader",
        "",
        "| Measure | Value |",
        "| --- | --- |",
        f"| Settled by the third reader | {t.third_settled} |",
        f"| Of which wrong | {t.third_settled_wrong} |",
        f"| Of which wrong on a number, date or negation | {t.third_settled_critical_wrong} |",
        *(f"| Held back: {step} | {count} |" for step, count in held.items()),
        "",
    ]


def to_markdown(report: Report, examples: int = 20) -> str:
    if not report.files and report.exact is not None:  # a folder of answer keys only
        return "# RAGnaroq evaluation\n\n" + exact_markdown(report.exact)
    t, r = report.total, report.total.rates()
    engines = (
        ", ".join(f"{name.split(':', 1)[-1]} {version}" for name, version in report.engines.items())
        or "none"
    )
    out = [
        "# RAGnaroq evaluation",
        "",
        f"Engines: {engines}. Agreement threshold: {report.min_similarity}. "
        f"References: {len(report.files)}, {t.truth_words} true words.",
        "",
        "| Measure | Value |",
        "| --- | --- |",
        f"| Lines read | {t.lines} |",
        f"| Accepted by rule | {t.accepted} ({_pct(r['accepted_share'])} of lines) |",
        f"| **Accepted but wrong** | **{t.accepted_wrong} ({_pct(r['false_accept_rate'])} of accepted)** |",
        f"| **Accepted with a wrong number, date or negation** | **{t.accepted_critical_wrong} ({_pct(r['critical_false_accept_rate'])} of accepted)** |",
        f"| Sent to a person | {t.flagged + t.blocked} ({_pct(r['review_share'])} of lines) |",
        f"| Sent to a person although right | {t.flagged_right} ({_pct(r['needless_review_share'])} of flagged) |",
        f"| Character error rate, all lines | {_pct(r['character_error_rate'])} |",
        f"| True words no line covered | {t.missed_words} ({_pct(r['missed_word_share'])}) |",
        f"| Lines with no true text under them | {t.extra_lines} |",
        "",
        *_third_section(t),
        "## By reference",
        "",
        "| Reference | Profile | Lines | Accepted | Accepted but wrong | Wrong on a critical token | Character errors |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    for f in report.files:
        fr = f.tally.rates()
        out.append(
            f"| {f.reference} | {f.profile} | {f.tally.lines} | {f.tally.accepted} | {f.tally.accepted_wrong} | "
            f"{f.tally.accepted_critical_wrong} | {_pct(fr['character_error_rate'])} |"
        )
    out += [
        "",
        "## The agreement threshold",
        "",
        "Every other rule kept as it is. A higher threshold accepts fewer lines: fewer mistakes get through, and more work goes to a person.",
        "",
        "The table shows the two-reader rule only: a line the third reader settled is compared by "
        "its first reader's reading.",
        "",
        "| Threshold | Accepted | Accepted but wrong | Wrong on a critical token | Sent to a person |",
        "| --- | --- | --- | --- | --- |",
    ]
    for row in report.sweep:
        mark = " ←" if abs(row["threshold"] - report.min_similarity) < 1e-9 else ""
        out.append(
            f"| {row['threshold']:.2f}{mark} | {row['accepted']} | {row['accepted_wrong']} | "
            f"{row['accepted_critical_wrong']} | {_pct(row['review_share'])} |"
        )
    wrong = report.accepted_but_wrong()
    out += [
        "",
        "## Accepted but wrong",
        "",
        "Those wrong on a number, a date or a negation come first.",
        "",
    ]
    if not wrong:
        out.append("None.")
    else:
        out += ["| Line | Critical | Read | Correct |", "| --- | --- | --- | --- |"]
        for line in wrong[:examples]:
            critical = "" if line.critical_ok else "yes"
            read, correct = line.read.replace("|", "¦"), line.expected.replace("|", "¦")
            out.append(f"| {line.line} | {critical} | {read} | {correct} |")
        if len(wrong) > examples:
            out.append(f"\n…and {len(wrong) - examples} more in evaluation.json.")
    if report.exact is not None:
        out += ["", exact_markdown(report.exact).rstrip("\n")]
    return "\n".join(out) + "\n"


def write(report: Report, out: str | Path) -> dict[str, Path]:
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    files = {"json": out / "evaluation.json", "markdown": out / "evaluation.md"}
    files["json"].write_text(json.dumps(report.to_dict(), ensure_ascii=False, indent=1), encoding="utf8")
    files["markdown"].write_text(to_markdown(report), encoding="utf8")
    return files


@dataclass
class ExactFile:
    name: str
    score: expected.ExactScore | None = None
    error: str | None = None  # the reader raised: nothing was scored

    @property
    def ok(self) -> bool:
        return self.error is None and self.score is not None and self.score.ok


@dataclass
class ExactReport:
    files: list[ExactFile]

    @property
    def ok(self) -> bool:
        return all(f.ok for f in self.files)


def run_exact(folder: str | Path, read: Callable[[bytes, str, Config], object]) -> ExactReport:
    """Score every file with a `<file>.expected.jsonl` beside it, by anchor.

    `read(data, filename, config)` returns a `Document`, or an object with a `.document`.
    A file the reader refuses (`ReaderRefused`) is scored as the document `refused_document`
    makes, with the refusing flag its answer key lists as a `document_flag`; a reader that
    raises anything else is reported as an error."""
    config = Config()
    files = []
    for key_path in sorted(Path(folder).glob("*.expected.jsonl")):
        path = key_path.with_name(key_path.name.removesuffix(".expected.jsonl"))
        if not path.exists():
            raise FileNotFoundError(f"{key_path.name} has no {path.name} beside it.")
        items = expected.load(key_path)
        data = path.read_bytes()
        try:
            result = read(data, path.name, config)
            document = result if isinstance(result, Document) else result.document
        except ReaderRefused as refusal:  # a refusal is a document with the flag that says why
            document = refused_document(path.name, data, refusal)
        except Exception as error:  # a reader that fails is a result, not a crash of the run
            files.append(ExactFile(path.name, error=f"{type(error).__name__}: {error}"))
            continue
        files.append(ExactFile(path.name, score=expected.score(document, items)))
    if not files:
        raise FileNotFoundError(f"No *.expected.jsonl files in {folder}.")
    return ExactReport(files)


def _exact_dict(f: "ExactFile") -> dict:
    return {"file": f.name, "ok": f.ok, "error": f.error, **(asdict(f.score) if f.score else {})}


def exact_markdown(report: ExactReport) -> str:
    out = [
        "## Exact formats",
        "",
        "Each file is scored line by line against a hand-written answer key, matched by anchor.",
        "",
        "| File | Result | Right | Wrong | Missing | Flags missing | Extra |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    details = []
    for f in report.files:
        if f.score is None:
            out.append(f"| {f.name} | error | | | | | |")
            details.append(f"- {f.name}: {f.error}".replace("|", "¦"))
            continue
        s = f.score
        out.append(
            f"| {f.name} | {'ok' if f.ok else 'FAIL'} | {s.right} | {len(s.wrong)} | {len(s.missing)} | "
            f"{len(s.flags_missing)} | {len(s.extra)} |"
        )
        for kind, rows in (("wrong", s.wrong), ("missing", s.missing), ("flag missing", s.flags_missing)):
            details += [f"- {f.name}, {kind}: `{json.dumps(row, ensure_ascii=False)}`" for row in rows]
    return "\n".join(out + ([""] + details if details else [])) + "\n"


def write_exact(report: ExactReport, out: str | Path) -> Path:
    """Write the "Exact formats" section as its own evaluation.md, for `evaluate.run` to merge."""
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    path = out / "evaluation.md"
    path.write_text("# RAGnaroq evaluation\n\n" + exact_markdown(report), encoding="utf8")
    return path
