import subprocess
import sys
from pathlib import Path

import pytest

from ragnaroq import Config
from ragnaroq.chunkeval import Question, is_hit, load_questions, run_chunking
from ragnaroq.chunkeval.baseline import baseline_chunks
from ragnaroq.chunkeval.bm25 import BM25
from ragnaroq.cli import main
from ragnaroq.export.chunks import to_jsonl
from ragnaroq.readers import read_bytes

ROOT = Path(__file__).parent.parent
DEMO = ROOT / "references" / "demo"
GOLDEN = Path(__file__).parent / "golden" / "baseline-0.3"


def test_the_baseline_is_0_3s_chunker_byte_for_byte():
    goldens = sorted(GOLDEN.glob("*.jsonl"))
    assert goldens
    for golden in goldens:
        name = golden.name[: -len(".jsonl")]
        document = read_bytes((DEMO / name).read_bytes(), name, Config(), base=DEMO).document
        assert to_jsonl(baseline_chunks(document, Config())) == golden.read_text(encoding="utf8")


def test_bm25_ranks_the_document_with_the_rare_word_first():
    docs = ["the tax year starts in april", "the tax return is due", "a proviso to section 10"]
    assert BM25(docs).rank("proviso section")[0] == 2


def test_a_hit_is_a_chunk_of_the_same_file_holding_the_passage_whitespace_aside():
    chunk = {"text": "Deduction  may be\nclaimed until 31 March.", "metadata": {"path": "a.md"}}
    assert is_hit(chunk, Question("q", "a.md", "may be claimed until 31 March"))
    assert not is_hit(chunk, Question("q", "b.md", "may be claimed until 31 March"))


def _folder(tmp_path, answer="The rate is 5 per cent"):
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "a.md").write_text("# Rates\n\nThe rate is 5 per cent for small firms.\n")
    q = tmp_path / "q.jsonl"
    q.write_text('{"question": "rate for small firms?", "path": "a.md", "answer": "' + answer + '"}\n\n')
    return tmp_path / "docs", q


def test_evaluate_chunking_scores_both_chunkers_on_the_same_questions(tmp_path):
    docs, q = _folder(tmp_path)
    report = run_chunking(docs, q, Config())
    assert set(report.scores) == {"0.3", "0.4"} and report.missing == []
    assert report.scores["0.3"].top1 == 1 and report.scores["0.3"].mrr == 1.0


def test_a_question_line_missing_a_field_names_its_line_number(tmp_path):
    q = tmp_path / "q.jsonl"
    q.write_text('{"question": "a", "path": "a.md", "answer": "b"}\n{"question": "a"}\n')
    with pytest.raises(ValueError, match="line 2"):
        load_questions(q)


def test_the_evaluate_command_writes_the_chunking_report(tmp_path, capsys):
    docs, q = _folder(tmp_path)
    out = tmp_path / "out"
    assert main(["evaluate", str(docs), "--chunking", str(q), "--out", str(out)]) == 0
    assert (out / "chunking.md").exists() and (out / "chunking.json").exists()
    (tmp_path / "two").mkdir()
    _, q2 = _folder(tmp_path / "two", "nothing like this")
    assert main(["evaluate", str(tmp_path / "two" / "docs"), "--chunking", str(q2), "--out", str(out)]) == 1


def test_the_chunking_evaluation_runs_as_a_module(tmp_path):
    docs, q = _folder(tmp_path)
    out = tmp_path / "out"
    run = subprocess.run(
        [
            sys.executable,
            "-m",
            "ragnaroq.cli",
            "evaluate",
            str(docs),
            "--chunking",
            str(q),
            "--out",
            str(out),
        ],
        capture_output=True,
        text=True,
    )
    assert run.returncode == 0, run.stderr
    assert (out / "chunking.md").exists()
