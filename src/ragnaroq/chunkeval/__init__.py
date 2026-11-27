"""Measure chunkers: rank each chunker's chunks for a set of questions and score where the answer lands."""

import json
from dataclasses import asdict, dataclass
from pathlib import Path

from ..config import Config
from ..export.chunks import to_chunks
from ..readers import ReaderRefused, detect, read_bytes
from .baseline import baseline_chunks
from .bm25 import BM25


@dataclass
class Question:
    question: str
    path: str
    answer: str


@dataclass
class ChunkerScore:
    top1: int
    top3: int
    top10: int
    mrr: float
    mean_chars: float
    chunks: int
    ranks: list[int | None]


@dataclass
class ChunkingReport:
    questions: list[Question]
    scores: dict[str, ChunkerScore]
    missing: list[int]


def load_questions(path: Path) -> list[Question]:
    questions = []
    for number, line in enumerate(Path(path).read_text(encoding="utf8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as error:
            raise ValueError(f"line {number}: not valid JSON ({error.msg})") from error
        if not isinstance(row, dict):
            raise ValueError(f"line {number}: expected a JSON object")
        values = {}
        for field in ("question", "path", "answer"):
            if not isinstance(row.get(field), str):
                raise ValueError(f"line {number}: missing field {field!r}")
            values[field] = row[field]
        questions.append(Question(**values))
    return questions


def is_hit(chunk: dict, question: Question) -> bool:
    if chunk["metadata"]["path"] != question.path:
        return False
    return " ".join(question.answer.split()) in " ".join(chunk["text"].split())


def _files(folder: Path):
    for path in sorted(folder.rglob("*")):
        relative = path.relative_to(folder)
        if any(part.startswith(".") for part in relative.parts) or not path.is_file():
            continue
        yield path, relative.as_posix()


def _score(chunks: list[dict], questions: list[Question]) -> ChunkerScore:
    search = BM25([chunk["embedding_text"] for chunk in chunks])
    ranks: list[int | None] = []
    for question in questions:
        found = None
        for position, index in enumerate(search.rank(question.question), start=1):
            if is_hit(chunks[index], question):
                found = position
                break
        ranks.append(found)
    return ChunkerScore(
        top1=sum(1 for r in ranks if r is not None and r <= 1),
        top3=sum(1 for r in ranks if r is not None and r <= 3),
        top10=sum(1 for r in ranks if r is not None and r <= 10),
        mrr=sum(1 / r for r in ranks if r) / len(ranks) if ranks else 0.0,
        mean_chars=sum(len(c["text"]) for c in chunks) / len(chunks) if chunks else 0.0,
        chunks=len(chunks),
        ranks=ranks,
    )


def run_chunking(folder: Path, questions: Path, config: Config) -> ChunkingReport:
    folder = Path(folder)
    asked = load_questions(questions)
    old: list[dict] = []
    new: list[dict] = []
    for path, name in _files(folder):
        data = path.read_bytes()
        if detect(name, data) is None:
            continue
        try:
            document = read_bytes(data, name, config, engines=[], base=folder).document
        except ReaderRefused:
            continue
        old.extend(baseline_chunks(document, config, path=name))
        new.extend(to_chunks(document, config, path=name))
    missing = [i for i, q in enumerate(asked) if not any(is_hit(c, q) for c in new)]
    scores = {"0.3": _score(old, asked), "0.4": _score(new, asked)}
    return ChunkingReport(questions=asked, scores=scores, missing=missing)


def _share(count: int, total: int) -> str:
    return f"{count} ({count / total:.0%})" if total else f"{count}"


def write_chunking(report: ChunkingReport, out: Path) -> dict[str, Path]:
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    total = len(report.questions)
    data = {
        "questions": [asdict(q) for q in report.questions],
        "scores": {name: asdict(score) for name, score in report.scores.items()},
        "missing": report.missing,
    }
    lines = [
        "# Chunking evaluation",
        "",
        f"{total} questions.",
        "",
        "| Chunker | Top 1 | Top 3 | Top 10 | MRR | Mean chars | Chunks |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    for name, s in report.scores.items():
        lines.append(
            f"| {name} | {_share(s.top1, total)} | {_share(s.top3, total)} | {_share(s.top10, total)} "
            f"| {s.mrr:.3f} | {s.mean_chars:.0f} | {s.chunks} |"
        )
    lines += ["", "## Questions", ""]
    for i, q in enumerate(report.questions):
        ranks = ", ".join(
            f"{name} {s.ranks[i] if s.ranks[i] is not None else 'none'}" for name, s in report.scores.items()
        )
        flag = " (answer in no 0.4 chunk)" if i in report.missing else ""
        lines.append(f"- {q.question} [{q.path}]: rank {ranks}{flag}")
    json_path = out / "chunking.json"
    md_path = out / "chunking.md"
    json_path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf8")
    md_path.write_text("\n".join(lines) + "\n", encoding="utf8")
    return {"json": json_path, "markdown": md_path}
