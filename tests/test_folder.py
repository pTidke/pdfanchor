"""`ragnaroq index`: the folder, the manifest and incremental runs."""

import hashlib
import io
import json
import os
import shutil
import subprocess
import sys
import threading
import zipfile
from dataclasses import replace
from pathlib import Path

import pytest
from test_chunks import assert_flat

from ragnaroq import Config, folder, isolation, readers
from ragnaroq.cli import main
from ragnaroq.folder import STATE, FolderError, Manifest, file_id

if sys.platform == "win32":
    pytest.skip("ragnaroq index works on Linux and macOS only", allow_module_level=True)

DEMO = Path(__file__).parents[1] / "references" / "demo"


@pytest.fixture
def demo(tmp_path):
    target = tmp_path / "demo"
    shutil.copytree(DEMO, target)
    return target


def run(root, config=None, **kwargs):
    kwargs.setdefault("engines", [])
    return folder.index(root, config or Config(), **kwargs)


def entries(report):
    return {entry.path: entry for entry in report.manifest.files}


def snapshot(root):
    """Every file under root outside .ragnaroq, with its bytes' hash."""
    found = {}
    for path in sorted(root.rglob("*")):
        if STATE in path.relative_to(root).parts or path.is_symlink() or not path.is_file():
            continue
        found[str(path.relative_to(root))] = hashlib.sha256(path.read_bytes()).hexdigest()
    return found


def state_files(root):
    return sorted(str(p.relative_to(root / STATE)) for p in (root / STATE).rglob("*"))


def small(root, **files):
    for name, text in files.items():
        (root / name).write_text(text, encoding="utf8")


def chunks_of(root):
    lines = (root / STATE / "chunks.jsonl").read_text(encoding="utf8").splitlines()
    return [json.loads(line) for line in lines]


# ---------------------------------------------------------------- the plan's tests


def test_index_writes_the_folder_layout(demo):
    before = snapshot(demo)
    report = run(demo)
    state = demo / STATE
    for name in ("manifest.json", "changes.json", "chunks.jsonl"):
        assert (state / name).is_file()
    ok = [e for e in report.manifest.files if e.status in ("ok", "review")]
    assert {e.path for e in ok} >= {"guide.docx", "rates.xlsx", "deck.pptx", "notice.html", "readme.md"}
    for entry in ok:
        folder_ = state / "files" / entry.file_id
        for name in ("document.json", "document.md", "chunks.jsonl", "images"):
            assert (folder_ / name).exists(), (entry.path, name)
        assert (folder_ / "images").is_dir()
    assert entry.file_id == file_id(entry.path)
    stored = Manifest.model_validate_json((state / "manifest.json").read_text())
    assert stored == report.manifest and stored.schema_version == "0.3.0"
    assert snapshot(demo) == before  # the originals are byte-identical, and nothing else is new
    assert sorted(p.name for p in demo.iterdir() if p.name != STATE) == sorted(before)


def test_a_second_run_reads_nothing_and_reports_no_changes(demo, monkeypatch):
    first = run(demo)
    manifest = (demo / STATE / "manifest.json").read_text()

    def refuse(*args, **kwargs):
        raise AssertionError("a file was read again")

    monkeypatch.setattr(readers, "read_bytes", refuse)
    monkeypatch.setattr(isolation, "read_file", refuse)
    monkeypatch.setattr(folder, "convert", refuse)
    second = run(demo)
    changes = json.loads((demo / STATE / "changes.json").read_text())
    assert changes == {
        "added": [],
        "changed": [],
        "removed": [],
        "chunks_added": [],
        "chunks_changed": [],
        "chunks_removed": [],
        "reset": False,
    }
    assert second.manifest == first.manifest
    assert (demo / STATE / "manifest.json").read_text() == manifest


def test_an_edited_file_is_read_again_and_its_chunks_replaced(demo):
    first = run(demo)
    old = entries(first)["readme.md"]
    (demo / "readme.md").write_text("# Changed\n\nA brand new paragraph about the rate.\n", encoding="utf8")
    second = run(demo)
    new = entries(second)["readme.md"]
    assert new.sha256 != old.sha256 and new.file_id == old.file_id
    changes = second.changes
    assert changes.changed == [old.file_id] and not changes.added and not changes.removed
    assert set(changes.chunks_removed) == set(old.chunk_ids) and set(changes.chunks_added) == set(
        new.chunk_ids
    )
    assert not set(old.chunk_ids) & {c["id"] for c in chunks_of(demo)}
    others = [e for p, e in entries(first).items() if p != "readme.md" and e.status != "skipped"]
    assert all(entries(second)[e.path] == e for e in others)


def test_a_deleted_file_is_removed_with_its_chunks(demo):
    first = run(demo)
    gone = entries(first)["rates.csv"]
    assert gone.chunk_ids
    (demo / "rates.csv").unlink()
    second = run(demo)
    assert second.changes.removed == [gone.file_id] and second.changes.chunks_removed == sorted(
        gone.chunk_ids
    )
    assert "rates.csv" not in entries(second)
    assert not (demo / STATE / "files" / gone.file_id).exists()
    assert not set(gone.chunk_ids) & {c["id"] for c in chunks_of(demo)}


def test_a_renamed_file_removes_old_chunks_and_adds_new(demo):
    first = run(demo)
    old = entries(first)["rates.csv"]
    (demo / "sub").mkdir()
    (demo / "rates.csv").rename(demo / "sub" / "rates-2026.csv")
    second = run(demo)
    new = entries(second)["sub/rates-2026.csv"]
    assert second.changes.removed == [old.file_id] and second.changes.added == [new.file_id]
    assert new.sha256 == old.sha256 and new.file_id != old.file_id
    assert set(second.changes.chunks_removed) == set(old.chunk_ids)
    assert set(second.changes.chunks_added) == set(new.chunk_ids) and not set(old.chunk_ids) & set(
        new.chunk_ids
    )
    assert not (demo / STATE / "files" / old.file_id).exists()
    now = {c["id"] for c in chunks_of(demo)}
    assert set(new.chunk_ids) <= now and not set(old.chunk_ids) & now


def bombs(root):
    data = (DEMO / "guide.docx").read_bytes()
    (root / "truncated.docx").write_bytes(data[: len(data) // 2])
    names = {"[Content_Types].xml": b"<Types/>", "xl/workbook.xml": b"<workbook/>"}
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, body in names.items():
            archive.writestr(name, body)
        archive.writestr("xl/worksheets/sheet1.xml", b"0" * 3_000_000)
    (root / "bomb.xlsx").write_bytes(buffer.getvalue())
    many = io.BytesIO()
    with zipfile.ZipFile(many, "w") as archive:
        archive.writestr("[Content_Types].xml", b"<Types/>")
        archive.writestr("xl/workbook.xml", b"<workbook/>")
        for number in range(10_100):
            archive.writestr(f"xl/x{number}", b"")
    (root / "many.xlsx").write_bytes(many.getvalue())


def test_one_bad_file_fails_alone(demo, capsys):
    (demo / "bomb.xml").unlink()
    bombs(demo)
    config = replace(Config(), max_unzipped_bytes=1_000_000)
    report = run(demo, config)
    found = entries(report)
    for name in ("truncated.docx", "bomb.xlsx", "many.xlsx"):
        entry = found[name]
        assert entry.status == "failed" and entry.error and entry.rule and not entry.chunk_ids, name
        document = json.loads((demo / STATE / "files" / entry.file_id / "document.json").read_text())
        assert [f["severity"] for f in document["flags"]] == ["blocking"]
        assert document["flags"][0]["rule"] == entry.rule
        assert (demo / STATE / "files" / entry.file_id / "chunks.jsonl").read_text() == ""
    assert found["bomb.xlsx"].rule == "file.zip-limits" and found["many.xlsx"].rule == "file.zip-limits"
    fine = [
        e for e in report.manifest.files if e.path in ("guide.docx", "rates.xlsx", "readme.md", "notice.xml")
    ]
    assert len(fine) == 4 and all(e.status in ("ok", "review") and e.chunk_ids for e in fine)
    assert report.failed == 3
    assert main(["index", str(demo), "--ocr", "none"]) == 1
    capsys.readouterr()


def test_unsupported_hidden_and_escaping_files_are_listed_as_skipped(demo):
    (demo / "notes.txt").write_text("plain notes", encoding="utf8")
    (demo / ".secret.json").write_text('{"k": 1}', encoding="utf8")
    (demo / ".hidden").mkdir()
    (demo / ".hidden" / "inside.json").write_text("{}", encoding="utf8")
    (demo / "link").symlink_to("/etc/passwd")
    (demo / "empty.json").write_bytes(b"")
    report = run(demo)
    found = entries(report)
    assert found["notes.txt"].skipped == "unsupported" and found["notes.txt"].status == "skipped"
    assert found[".secret.json"].skipped == "hidden"
    assert found[".hidden"].skipped == "hidden" and ".hidden/inside.json" not in found
    assert found["link"].skipped == "outside the folder"
    assert found["empty.json"].skipped == "empty"
    assert not any(path.startswith(STATE) for path in found)
    small_limit = run(demo, replace(Config(), max_bytes=100))
    assert entries(small_limit)["deck.pptx"].skipped == "over the size limit"
    assert not (demo / STATE / "files" / file_id("link")).exists()


def test_combined_chunks_hold_every_file_and_pass_the_vector_database_rules(demo):
    report = run(demo)
    chunks = chunks_of(demo)
    assert chunks
    for chunk in chunks:
        assert_flat(chunk)
    assert len({c["id"] for c in chunks}) == len(chunks)
    paths = [c["metadata"]["path"] for c in chunks]
    assert paths == sorted(paths)
    expected = [i for e in report.manifest.files for i in e.chunk_ids]
    assert [c["id"] for c in chunks] == expected
    for entry in report.manifest.files:
        if entry.chunk_ids:
            lines = (demo / STATE / "files" / entry.file_id / "chunks.jsonl").read_text().splitlines()
            assert [json.loads(line)["id"] for line in lines] == entry.chunk_ids
            assert {c["metadata"]["file_id"] for c in chunks if c["metadata"]["path"] == entry.path} == {
                entry.file_id
            }
    # Loading twice replaces; checked on real stores when they are installed.
    if (qdrant := pytest.importorskip("qdrant_client", reason="no qdrant")) is not None:
        from qdrant_client.models import Distance, PointStruct, VectorParams

        client = qdrant.QdrantClient(":memory:")
        client.create_collection("c", vectors_config=VectorParams(size=3, distance=Distance.COSINE))
        points = [
            PointStruct(id=c["id"], vector=[1.0, 0.0, 1.0], payload={"text": c["text"], **c["metadata"]})
            for c in chunks
        ]
        client.upsert("c", points)
        client.upsert("c", points)
        assert client.count("c").count == len(chunks)


def test_the_vector_database_follows_changes_json_exactly(demo):
    """Keep a dict as the database: apply changes.json each run and it ends equal to chunks.jsonl."""
    database = {}

    def apply():
        changes = json.loads((demo / STATE / "changes.json").read_text())
        wanted = {c["id"]: c for c in chunks_of(demo)}
        for chunk_id in changes["chunks_removed"]:
            del database[chunk_id]
        for chunk_id in changes["chunks_added"]:
            database[chunk_id] = wanted[chunk_id]
        assert set(database) == set(wanted)

    run(demo)
    apply()
    (demo / "readme.md").write_text("# New\n\nText.\n", encoding="utf8")
    (demo / "rates.csv").rename(demo / "taxes.csv")
    (demo / "deck.pptx").unlink()
    run(demo)
    apply()


def test_index_command_exit_codes(tmp_path, capsys):
    ok = tmp_path / "ok"
    ok.mkdir()
    shutil.copy(DEMO / "notice.xml", ok)
    assert main(["index", str(ok), "--ocr", "none"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["files"]["ok"] == 1 and out["changes"]["added"] == 1
    review = tmp_path / "review"
    review.mkdir()
    shutil.copy(DEMO / "rates.csv", review)
    assert main(["index", str(review), "--ocr", "none"]) == 2
    failing = tmp_path / "failing"
    failing.mkdir()
    shutil.copy(DEMO / "rates.csv", failing)
    shutil.copy(DEMO / "bomb.xml", failing)
    assert main(["index", str(failing), "--ocr", "none"]) == 1
    assert main(["index", str(tmp_path / "nowhere"), "--ocr", "none"]) == 1
    capsys.readouterr()


# ---------------------------------------------------------------- R21


def test_a_second_run_at_the_same_time_fails_clearly(demo, capsys):
    run(demo)
    import fcntl

    handle = os.open(demo / STATE / "lock", os.O_RDWR)
    fcntl.flock(handle, fcntl.LOCK_EX)
    try:
        with pytest.raises(FolderError, match="already running"):
            run(demo)
        assert main(["index", str(demo), "--ocr", "none"]) == 1
        assert "already running" in capsys.readouterr().err
    finally:
        os.close(handle)
    run(demo)  # the lock is free again


def test_a_fifo_is_never_opened(tmp_path):
    root = tmp_path / "f"
    root.mkdir()
    small(root, **{"a.json": '{"a": 1}'})
    os.mkfifo(root / "pipe.csv")
    os.symlink("pipe.csv", root / "alias.csv")
    done = []
    thread = threading.Thread(target=lambda: done.append(run(root)), daemon=True)
    thread.start()
    thread.join(60)
    assert done, "the run blocked on a fifo"
    found = entries(done[0])
    assert found["pipe.csv"].skipped == "not a regular file"
    assert found["alias.csv"].skipped == "not a regular file"
    assert found["a.json"].status in ("ok", "review")


def test_a_symbolic_link_to_a_folder_is_not_followed(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    small(outside, **{"secret.json": '{"s": 1}'})
    root = tmp_path / "f"
    root.mkdir()
    (root / "inner").mkdir()
    small(root / "inner", **{"x.json": '{"x": 1}'})
    (root / "elsewhere").symlink_to(outside, target_is_directory=True)
    (root / "alias").symlink_to(root / "inner", target_is_directory=True)
    (root / "up").symlink_to(tmp_path, target_is_directory=True)  # its own parent
    (root / "inner" / "self").symlink_to(root, target_is_directory=True)  # and a loop
    report = run(root)
    found = entries(report)
    for name in ("elsewhere", "alias", "up", "inner/self"):
        assert found[name].skipped == "symbolic link to a folder", name
    assert [p for p, e in found.items() if e.status != "skipped"] == ["inner/x.json"]
    assert snapshot(outside) == {"secret.json": snapshot(outside)["secret.json"]}
    assert not (outside / STATE).exists() and not (tmp_path / STATE).exists()


def test_a_symbolic_link_to_a_file_inside_is_read_under_its_own_path(tmp_path):
    root = tmp_path / "f"
    root.mkdir()
    small(root, **{"real.json": '{"a": 1}'})
    (root / "alias.json").symlink_to("real.json")
    found = entries(run(root))
    assert found["alias.json"].status in ("ok", "review") and found["real.json"].status in ("ok", "review")
    assert found["alias.json"].file_id != found["real.json"].file_id
    assert found["alias.json"].sha256 == found["real.json"].sha256


def test_a_path_that_is_not_utf8_is_skipped_and_named_safely(tmp_path):
    root = tmp_path / "f"
    root.mkdir()
    small(root, **{"a.json": '{"a": 1}'})
    try:
        fd = os.open(os.fsencode(root) + b"/bad\xff.json", os.O_CREAT | os.O_WRONLY)
        os.close(fd)
        os.mkdir(os.fsencode(root) + b"/dir\xfe")
        fd = os.open(os.fsencode(root) + b"/dir\xfe/x.json", os.O_CREAT | os.O_WRONLY)
        os.close(fd)
    except OSError:
        pytest.skip("this filesystem does not allow such names")
    report = run(root)
    found = entries(report)
    assert found["bad\\xff.json"].skipped == "unreadable name"
    assert found["dir\\xfe"].skipped == "unreadable name" and len(found) == 3
    text = (root / STATE / "manifest.json").read_bytes().decode("utf-8")  # valid UTF-8, strictly
    assert "bad\\\\xff.json" in text
    assert found["a.json"].status in ("ok", "review")


def test_an_interrupted_run_leaves_the_last_manifest_and_the_next_run_recovers(demo):
    first = run(demo)
    manifest = (demo / STATE / "manifest.json").read_bytes()
    chunks = (demo / STATE / "chunks.jsonl").read_bytes()
    (demo / "readme.md").write_text("# One\n\nEdited once.\n", encoding="utf8")
    (demo / "rates.csv").write_text("a,b\n1,2\n", encoding="utf8")
    seen = []

    def stop(path, status):
        seen.append(path)
        if path == "rates.csv":
            raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        run(demo, progress=stop)
    assert (demo / STATE / "manifest.json").read_bytes() == manifest  # never half-written
    assert (demo / STATE / "chunks.jsonl").read_bytes() == chunks
    assert not list((demo / STATE).glob("*.part"))
    second = run(demo)
    # The interrupted run changed nothing the manifest knows: the changes are against the first run.
    assert set(second.changes.changed) == {
        entries(first)["readme.md"].file_id,
        entries(first)["rates.csv"].file_id,
    }
    clean = demo.parent / "clean"
    shutil.copytree(demo, clean, ignore=shutil.ignore_patterns(STATE))
    run(clean)
    assert (demo / STATE / "chunks.jsonl").read_text() == (clean / STATE / "chunks.jsonl").read_text()
    assert not (demo / STATE / "tmp").exists()


def test_a_run_killed_after_installing_a_file_reads_it_again_if_it_changes_back(demo):
    run(demo)
    original = (demo / "readme.md").read_bytes()
    (demo / "readme.md").write_text("# Edited\n\nSomething else.\n", encoding="utf8")

    def stop(path, status):
        if path == "readme.md":
            raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        run(demo, progress=stop)  # readme's new output is installed; the manifest is still the old one
    (demo / "readme.md").write_bytes(original)
    after = run(demo)
    entry = entries(after)["readme.md"]
    document = json.loads((demo / STATE / "files" / entry.file_id / "document.json").read_text())
    assert document["source"]["sha256"] == hashlib.sha256(original).hexdigest()


# The worker is a new interpreter: a `sitecustomize` on its path makes one reader misbehave.
SITE = """
import os, time
import ragnaroq.readers.csv_reader as csv_reader

_read = csv_reader.read


def read(data, name, config, *args, **kwargs):
    if b"SLOW" in data:
        time.sleep(60)
    if b"CRASH" in data:
        os._exit(7)
    if b"HUNGRY" in data:
        keep = bytearray(1_500_000_000)
    return _read(data, name, config, *args, **kwargs)


csv_reader.read = read
"""


@pytest.fixture
def misbehaving(tmp_path, monkeypatch):
    site = tmp_path / "site"
    site.mkdir()
    (site / "sitecustomize.py").write_text(SITE)
    monkeypatch.setenv(
        "PYTHONPATH", os.pathsep.join([str(site), *filter(None, [os.environ.get("PYTHONPATH")])])
    )


def mixed(tmp_path, *names):
    root = tmp_path / "m"
    root.mkdir()
    every = {
        "a.json": '{"a": 1}',
        "slow.csv": "SLOW,1\n",
        "crash.csv": "CRASH,1\n",
        "hungry.csv": "HUNGRY,1\n",
        "z.json": '{"z": 2}',
        "ok.csv": "x,y\n1,2\n",
    }
    small(root, **{name: every[name] for name in names})
    return root


def test_a_file_that_takes_too_long_fails_alone(tmp_path, misbehaving):
    root = mixed(tmp_path, "a.json", "slow.csv", "z.json", "ok.csv")
    report = run(root, replace(Config(), file_timeout_seconds=6))  # the sleep is 60: no load can fool it
    found = entries(report)
    assert found["slow.csv"].status == "failed" and found["slow.csv"].rule == "file.timeout"
    assert "6 seconds" in found["slow.csv"].error and found["slow.csv"].attempts == 1
    for name in ("a.json", "z.json", "ok.csv"):  # before and after it, with a new worker
        assert found[name].status in ("ok", "review") and found[name].chunk_ids, name
    assert report.failed == 1


@pytest.mark.skipif(sys.platform != "linux", reason="memory limits are enforced on Linux")
def test_a_file_that_needs_too_much_memory_or_crashes_the_reader_fails_alone(tmp_path, misbehaving):
    root = mixed(tmp_path, "a.json", "crash.csv", "hungry.csv", "z.json", "ok.csv")
    found = entries(run(root, replace(Config(), page_memory_mb=700, file_timeout_seconds=30)))
    crash = found["crash.csv"]
    assert crash.rule == "file.crashed" and "exited with code 7" in crash.error
    assert "reader for csv files" in crash.error and "PDF" not in crash.error
    assert found["hungry.csv"].rule == "file.memory"
    assert all(found[n].status in ("ok", "review") for n in ("a.json", "z.json", "ok.csv"))


def test_what_is_the_files_own_is_carried_over_and_a_timeout_is_tried_once_more(
    tmp_path, misbehaving, monkeypatch
):
    root = mixed(tmp_path, "a.json", "slow.csv", "crash.csv", "hungry.csv")
    shutil.copy(DEMO / "bomb.xml", root)
    config = replace(Config(), page_memory_mb=700, file_timeout_seconds=4)
    first = run(root, config)
    assert {n: e.rule for n, e in entries(first).items() if e.status == "failed"} == {
        "bomb.xml": entries(first)["bomb.xml"].rule,
        "slow.csv": "file.timeout",
        "crash.csv": "file.crashed",
        "hungry.csv": "file.memory",
    }
    calls = []
    real = isolation.read_file

    def spy(data, name, *args, **kwargs):
        calls.append(name)
        return real(data, name, *args, **kwargs)

    monkeypatch.setattr(isolation, "read_file", spy)
    second = run(root, config)
    assert calls == [
        "slow.csv"
    ]  # the timeout once more; the crash, the memory and the bomb are the file's own
    assert entries(second)["slow.csv"].attempts == 2
    calls.clear()
    third = run(root, config)
    assert calls == [] and entries(third) == entries(second)  # and now it is left alone
    (root / "slow.csv").write_text("SLOW,2\n")  # a change of the file starts again
    calls.clear()
    assert entries(run(root, config))["slow.csv"].attempts == 1 and calls == ["slow.csv"]
    calls.clear()
    run(root, config, fresh=True)
    assert sorted(calls) == ["a.json", "bomb.xml", "crash.csv", "hungry.csv", "slow.csv"], calls


def test_a_missing_library_fails_the_file_and_the_hint_is_printed_once(demo, monkeypatch, capsys):
    hint = "pip install 'ragnaroq[formats]'"

    def missing(data, name, config, **kwargs):
        raise readers.ReaderUnavailable(f"The docx reader needs docx: {hint}")

    monkeypatch.setattr(isolation, "read_file", missing)
    for number in range(3):
        shutil.copy(DEMO / "guide.docx", demo / f"more{number}.docx")
    report = run(demo)
    docx = [e for e in report.manifest.files if e.format == "docx"]
    assert len(docx) == 4 and all(e.status == "failed" and hint in e.error for e in docx)
    assert all(e.rule == "reader.unavailable" for e in docx)
    capsys.readouterr()
    assert main(["index", str(demo), "--ocr", "none"]) == 1
    assert capsys.readouterr().err.count(hint) == 1


def test_the_settings_decide_whether_a_file_is_read_again(demo):
    first = run(demo)
    assert run(demo, Config(), fresh=False).changes.changed == []
    again = run(demo, replace(Config(), max_cells=1_000))
    read = entries(again)
    assert read["rates.csv"].settings != entries(first)["rates.csv"].settings  # a format limit counts
    assert run(demo, replace(Config(), max_cells=1_000), fresh=True).changes.changed == []  # same output

    # The OCR engines count, and so does the timeout not.
    class Engine:
        name = "fake"

        def version(self):
            return "1"

    other = run(demo, replace(Config(), max_cells=1_000), engines=[Engine()])
    assert entries(other)["rates.csv"].settings != read["rates.csv"].settings
    same = run(demo, replace(Config(), max_cells=1_000, file_timeout_seconds=5), engines=[Engine()])
    assert entries(same)["rates.csv"].settings == entries(other)["rates.csv"].settings


def test_progress_is_called_once_for_every_file(demo):
    seen = []
    report = run(demo, progress=lambda path, status: seen.append((path, status)))
    assert sorted(seen) == sorted((e.path, e.status) for e in report.manifest.files)


def test_a_file_renamed_and_edited_in_one_run(demo):
    first = run(demo)
    old = entries(first)["readme.md"]
    (demo / "readme.md").rename(demo / "about.md")
    (demo / "about.md").write_text("# About\n\nCompletely new words.\n", encoding="utf8")
    second = run(demo)
    new = entries(second)["about.md"]
    assert second.changes.removed == [old.file_id] and second.changes.added == [new.file_id]
    assert set(second.changes.chunks_removed) == set(old.chunk_ids)
    assert set(second.changes.chunks_added) == set(new.chunk_ids)


def test_a_file_that_changes_while_it_is_read_is_not_indexed_torn(demo, monkeypatch):
    target = demo / "rates.csv"
    real = os.fstat
    state = {"calls": 0}

    def growing(fd):
        info = real(fd)
        try:
            if os.readlink(f"/proc/self/fd/{fd}") == str(target):
                state["calls"] += 1
                if state["calls"] % 2 == 0:  # the after-read stat: it grew since the first
                    return os.stat_result((*info[:6], info.st_size + 1, *info[7:]))
        except OSError:
            pass
        return info

    monkeypatch.setattr(folder.os, "fstat", growing)
    report = run(demo)
    entry = entries(report)["rates.csv"]
    assert entry.status == "failed" and entry.rule == "file.changed" and entry.sha256 == ""
    monkeypatch.undo()
    assert entries(run(demo))["rates.csv"].status in ("ok", "review")  # tried again, and fine


# ---------------------------------------------------------------- a folder whose .ragnaroq was tampered with


def test_a_tampered_manifest_cannot_reach_outside_the_state_folder(demo, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "keep.txt").write_text("keep")
    run(demo)
    path = demo / STATE / "manifest.json"
    manifest = json.loads(path.read_text())
    real = manifest["files"][0]
    manifest["files"] += [
        {**real, "path": "../outside", "file_id": "../../outside", "status": "ok"},
        {**real, "path": "../../x", "file_id": file_id("../../x"), "status": "ok"},
        {**real, "path": "ghost.json", "file_id": "../outside", "status": "ok"},
        {**real, "path": "ghost2.json", "file_id": str(outside), "status": "ok"},
    ]
    path.write_text(json.dumps(manifest))
    report = run(demo)
    assert (outside / "keep.txt").read_text() == "keep" and list(outside.iterdir()) == [outside / "keep.txt"]
    assert len(report.notes) == 1 and "not sound" in report.notes[0]
    assert [p for p in entries(report) if p.startswith(("..", "ghost"))] == []
    # An entry that is sound (its id is its path's) but names a file that is not there is removed by id.
    manifest = json.loads(path.read_text())
    manifest["files"].append({**real, "path": "../../x", "file_id": file_id("../../x"), "status": "ok"})
    path.write_text(json.dumps(manifest))
    assert run(demo).changes.removed == [file_id("../../x")]
    assert list(outside.iterdir()) == [outside / "keep.txt"]


@pytest.mark.parametrize(
    "text",
    [
        "",
        "not json",
        "[1, 2]",
        '{"schema_version": "9.9.9", "files": []}',
        '{"files": [{"nonsense": 1}]}',
        "\x00" * 100,
    ],
)
def test_a_manifest_that_cannot_be_read_is_treated_as_none(demo, text):
    first = run(demo)
    (demo / STATE / "manifest.json").write_text(text)
    report = run(demo)
    assert len(report.notes) == 1
    assert len(report.changes.added) == len([e for e in first.manifest.files if e.status != "skipped"])
    assert Manifest.model_validate_json((demo / STATE / "manifest.json").read_text())


def test_a_files_folder_replaced_by_a_symlink_is_not_read_through_or_written_through(demo, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "chunks.jsonl").write_text(
        json.dumps({"id": "00000000-0000-0000-0000-000000000000", "text": "FORGED"}) + "\n"
    )
    (outside / "keep.txt").write_text("keep")
    first = run(demo)
    entry = entries(first)["readme.md"]
    target = demo / STATE / "files" / entry.file_id
    shutil.rmtree(target)
    target.symlink_to(outside, target_is_directory=True)
    second = run(demo)
    assert not target.is_symlink() and (target / "document.json").is_file()
    assert sorted(p.name for p in outside.iterdir()) == ["chunks.jsonl", "keep.txt"]
    assert "FORGED" not in (demo / STATE / "chunks.jsonl").read_text()
    assert entries(second)["readme.md"].chunk_ids == entry.chunk_ids
    # The same for a single file inside it.
    chunks = target / "chunks.jsonl"
    chunks.unlink()
    chunks.symlink_to(outside / "chunks.jsonl")
    run(demo)
    assert not chunks.is_symlink() and "FORGED" not in (demo / STATE / "chunks.jsonl").read_text()
    assert "FORGED" in (outside / "chunks.jsonl").read_text()  # never written


def test_a_state_folder_that_is_a_link_is_refused(demo, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (demo / STATE).symlink_to(outside, target_is_directory=True)
    with pytest.raises(FolderError, match="not a folder"):
        run(demo)
    assert list(outside.iterdir()) == []
    (demo / STATE).unlink()
    (demo / STATE).write_text("a file")
    with pytest.raises(FolderError):
        run(demo)


def test_planted_links_inside_the_state_folder_are_replaced_not_followed(demo, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    run(demo)
    for name in ("manifest.json", "chunks.jsonl", "changes.json"):
        (demo / STATE / name).unlink()
        (demo / STATE / name).symlink_to(outside / "target")
    shutil.rmtree(demo / STATE / "files")
    (demo / STATE / "files").symlink_to(outside, target_is_directory=True)
    (demo / STATE / "tmp").symlink_to(outside, target_is_directory=True)
    run(demo)
    assert list(outside.iterdir()) == []
    assert not (demo / STATE / "manifest.json").is_symlink() and not (demo / STATE / "files").is_symlink()


def test_the_originals_are_never_changed(demo):
    before = {p: (p.stat().st_mtime_ns, p.read_bytes()) for p in demo.iterdir() if p.is_file()}
    run(demo)
    run(demo, fresh=True)
    (demo / "x.json").write_text("{}", encoding="utf8")
    run(demo)
    for path, (mtime, data) in before.items():
        assert path.read_bytes() == data and path.stat().st_mtime_ns == mtime


def test_a_folder_that_cannot_be_listed_is_skipped_and_named(demo, monkeypatch):
    real = os.fwalk

    def walking(top, *args, onerror=None, **kwargs):
        yield from real(top, *args, onerror=onerror, **kwargs)
        onerror(PermissionError(13, "Permission denied", "./locked"))

    monkeypatch.setattr(folder.os, "fwalk", walking)
    found = entries(run(demo))
    assert found["locked"].skipped == "unreadable folder" and found["readme.md"].status in ("ok", "review")


# ---------------------------------------------------------------- fix round 1 (R29)

FLIPPER = """
import ctypes, os, sys, time
libc = ctypes.CDLL(None, use_errno=True)
a, b = os.fsencode(sys.argv[1]), os.fsencode(sys.argv[2])
end = time.time() + float(sys.argv[3])
while time.time() < end:
    if libc.renameat2(-100, a, -100, b, 2) != 0:
        raise OSError(ctypes.get_errno(), "renameat2")
"""


def outside_folder(tmp_path):
    victim = tmp_path / "outside"
    (victim / "Documents").mkdir(parents=True)
    (victim / "Documents" / "thesis.txt").write_text("years of work")
    (victim / "chunks.jsonl").write_text("not ours")
    return victim


def contents(victim):
    return sorted(
        (str(p.relative_to(victim)), p.read_bytes() if p.is_file() else b"") for p in victim.rglob("*")
    )


def swap_files(demo, victim):
    state = demo / STATE
    os.rename(state / "files", state / "files.moved")
    os.symlink(victim, state / "files")


def swap_state(demo, victim):
    os.rename(demo / STATE, demo / "moved-state")
    os.symlink(victim, demo / STATE)


@pytest.mark.parametrize("hook", ["_install", "_write_file", "_sweep", "after_manifest"])
@pytest.mark.parametrize("swap", [swap_files, swap_state])
def test_swapping_the_state_folders_for_links_at_a_write_point_reaches_nothing_outside(
    demo, tmp_path, monkeypatch, hook, swap
):
    victim = outside_folder(tmp_path)
    run(demo)
    (demo / "readme.md").write_text("# Edited\n\nSo it is installed.\n", encoding="utf8")
    (demo / "rates.csv").unlink()  # and something is swept
    before = contents(victim)
    after = hook == "after_manifest"  # the swap comes just after manifest.json is written
    real = getattr(folder, "_write_file" if after else hook)
    calls = []

    def swapping(*args, **kwargs):
        if after:
            done = real(*args, **kwargs)
            if args[1] == "manifest.json":
                calls.append(1)
                swap(demo, victim)
            return done
        if not calls:
            calls.append(1)
            swap(demo, victim)
        return real(*args, **kwargs)

    monkeypatch.setattr(folder, "_write_file" if after else hook, swapping)
    run(demo)
    assert calls and contents(victim) == before


def test_a_thread_swapping_the_state_folders_all_the_time_reaches_nothing_outside(demo, tmp_path):
    victim = outside_folder(tmp_path)
    run(demo)
    before = contents(victim)
    stop = threading.Event()

    def flip():
        state = demo / STATE
        while not stop.is_set():
            try:
                os.rename(state / "files", state / "files.m")
                os.symlink(victim, state / "files")
                os.unlink(state / "files")
                os.rename(state / "files.m", state / "files")
            except OSError:
                pass

    thread = threading.Thread(target=flip, daemon=True)
    thread.start()
    try:
        for number in range(3):
            (demo / "readme.md").write_text(f"# Edit {number}\n\nWords {number}.\n", encoding="utf8")
            (demo / "gone.json").write_text('{"a": 1}', encoding="utf8")
            try:
                run(demo)
            except (OSError, FolderError):  # the flipper may break a run; it must not break out
                pass
            (demo / "gone.json").unlink()
    finally:
        stop.set()
        thread.join()
    assert contents(victim) == before


@pytest.mark.skipif(sys.platform != "linux", reason="renameat2 exchange")
def test_a_folder_swapped_with_a_link_while_it_is_read_never_gets_outside_text_in(tmp_path):
    root, outside = tmp_path / "root", tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    (outside / "notes.md").write_text("# Secret\n\nSECRET-TOKEN-123 from outside\n", encoding="utf8")
    (root / "sub").mkdir()
    (root / "sub" / "notes.md").write_text("# Inside\n\nharmless words\n", encoding="utf8")
    (root / "sub.x").symlink_to(outside, target_is_directory=True)
    (root / "link.md").symlink_to("sub/notes.md")
    flipper = subprocess.Popen([sys.executable, "-c", FLIPPER, str(root / "sub"), str(root / "sub.x"), "40"])
    try:
        leaked = 0
        for _ in range(12):
            run(root, fresh=True)
            leaked += "SECRET-TOKEN" in (root / STATE / "chunks.jsonl").read_text(encoding="utf8")
    finally:
        flipper.kill()
    assert leaked == 0


def test_a_link_that_is_swapped_for_a_file_outside_after_the_check_is_not_read(tmp_path, monkeypatch):
    root, outside = tmp_path / "root", tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    (outside / "secret.md").write_text("# Secret\n\nSECRET-TOKEN-123\n", encoding="utf8")
    (root / "real.md").write_text("# Real\n\nwords\n", encoding="utf8")
    (root / "alias.md").symlink_to("real.md")
    real = os.path.realpath

    def swapped(path, *args, **kwargs):
        found = real(path, *args, **kwargs)
        if str(path).endswith("alias.md"):  # after the check, the target is replaced by a link outside
            (root / "real.md").unlink()
            (root / "real.md").symlink_to(outside / "secret.md")
        return found

    monkeypatch.setattr(folder.os.path, "realpath", swapped)
    report = run(root)
    monkeypatch.undo()
    assert "SECRET-TOKEN" not in (root / STATE / "chunks.jsonl").read_text(encoding="utf8")
    assert entries(report)["alias.md"].status == "skipped" or entries(report)["alias.md"].chunk_ids == []


def test_an_image_path_swapped_for_a_link_is_not_followed(tmp_path):
    from ragnaroq.readers import _images

    root, outside = tmp_path / "root", tmp_path / "outside"
    (root / "pics").mkdir(parents=True)
    outside.mkdir()
    png = (DEMO / "logo.png").read_bytes()
    (outside / "a.png").write_bytes(png)
    (root / "pics" / "a.png").write_bytes(png)
    images = _images.Images((str(root), ()), Config())
    assert images.load("pics/a.png").data == png and images.load("pics/../pics/a.png").data == png
    assert images.load("../a.png").flag is not None or images.load("../a.png").data is None
    (root / "pics").rename(root / "pics.real")
    (root / "pics").symlink_to(outside, target_is_directory=True)
    again = _images.Images((str(root), ()), Config())
    assert again.load("pics/a.png").data is None  # a link is never followed
    (root / "pics").unlink()
    (root / "pics.real").rename(root / "pics")
    (root / "pics" / "a.png").unlink()
    (root / "pics" / "a.png").symlink_to(outside / "a.png")
    assert _images.Images((str(root), ()), Config()).load("pics/a.png").data is None


def one_line_pdf(root):
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen import canvas

    root.mkdir()
    page = canvas.Canvas(str(root / "native.pdf"), pagesize=A4)
    page.setFont("Helvetica", 11)
    page.drawString(72, 730, "This notification shall come into force on the 1st day of April, 2026.")
    page.save()


def test_a_changed_file_that_keeps_its_chunk_ids_is_listed_in_chunks_changed(tmp_path):
    root = tmp_path / "pdfs"
    one_line_pdf(root)
    trusting = replace(Config(), policy=replace(Config().policy, accept_native_without_crosscheck=True))
    first = run(root, Config())
    before = {c["id"]: c["metadata"]["status"] for c in chunks_of(root)}
    second = run(root, trusting)
    old, new = entries(first)["native.pdf"], entries(second)["native.pdf"]
    assert old.chunk_ids == new.chunk_ids and old.chunk_ids
    changes = second.changes
    assert changes.changed == [new.file_id] and not changes.chunks_added and not changes.chunks_removed
    assert changes.chunks_changed == sorted(new.chunk_ids) and not changes.reset
    after = {c["id"]: c["metadata"]["status"] for c in chunks_of(root)}
    assert before != after and before.keys() == after.keys()  # the verdict moved, the ids did not


def test_a_store_that_follows_upserts_and_deletes_matches_chunks_even_when_ids_stay(tmp_path):
    root = tmp_path / "pdfs"
    one_line_pdf(root)
    trusting = replace(Config(), policy=replace(Config().policy, accept_native_without_crosscheck=True))
    store = {}
    for config in (Config(), trusting, Config()):
        run(root, config)
        changes = json.loads((root / STATE / "changes.json").read_text())
        wanted = {c["id"]: c for c in chunks_of(root)}
        for chunk_id in changes["chunks_removed"]:
            del store[chunk_id]
        for chunk_id in changes["chunks_added"] + changes["chunks_changed"]:
            store[chunk_id] = wanted[chunk_id]
        assert store == wanted


def test_the_parent_never_opens_a_zip(demo, monkeypatch):
    def refuse(*args, **kwargs):
        raise AssertionError("the parent opened a zip")

    first = run(demo)  # to know what is there
    bombs(demo)
    monkeypatch.setattr(zipfile, "ZipFile", refuse)
    (demo / "plain.zip").write_bytes(b"PK\x03\x04" + b"0" * 100)
    (demo / "named.docx").write_text("not a zip at all", encoding="utf8")
    report = run(demo, fresh=True)
    found = entries(report)
    assert found["plain.zip"].skipped == "unsupported"
    assert found["named.docx"].rule == "format.unknown"
    assert found["truncated.docx"].status == "failed" and found["many.xlsx"].rule == "file.zip-limits"
    assert found["guide.docx"].status in ("ok", "review") and entries(first)["guide.docx"].chunk_ids


def test_only_the_head_of_a_file_that_is_not_read_is_read(tmp_path, monkeypatch):
    root = tmp_path / "f"
    root.mkdir()
    (root / "big.bin").write_bytes(os.urandom(8_000_000))
    small(root, **{"a.json": '{"a": 1}'})
    amounts = []
    real = os.fdopen

    class Counting:
        def __init__(self, handle):
            self.handle = handle

        def read(self, *args):
            data = self.handle.read(*args)
            amounts.append(len(data))
            return data

        def __getattr__(self, name):
            return getattr(self.handle, name)

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            self.handle.close()

    monkeypatch.setattr(folder.os, "fdopen", lambda *a, **k: Counting(real(*a, **k)))
    found = entries(run(root))
    assert found["big.bin"].skipped == "unsupported" and found["big.bin"].bytes == 8_000_000
    assert max(amounts) <= 64 * 1024


def test_the_state_folder_and_everything_in_it_is_private(demo):
    for path in demo.iterdir():
        path.chmod(0o600) if path.is_file() else None
    run(demo)
    state = demo / STATE
    assert state.stat().st_mode & 0o777 == 0o700
    for path in state.rglob("*"):
        mode = path.stat().st_mode & 0o777
        assert mode == (0o700 if path.is_dir() else 0o600), (path, oct(mode))
    # An existing .ragnaroq keeps its mode; what is made in it is private all the same.
    state.chmod(0o755)
    (demo / "readme.md").write_text("# New\n\nwords\n", encoding="utf8")
    run(demo)
    assert state.stat().st_mode & 0o777 == 0o755
    new = state / "files" / file_id("readme.md") / "document.json"
    assert new.stat().st_mode & 0o777 == 0o600
    assert (state / "manifest.json").stat().st_mode & 0o777 == 0o600


def test_progress_output_that_fails_never_stops_the_run(demo):
    def broken(path, status):
        raise BrokenPipeError(errno_pipe, "Broken pipe")

    errno_pipe = 32
    report = run(demo, progress=broken)
    assert len(report.manifest.files) >= 20 and (demo / STATE / "manifest.json").is_file()


def test_names_are_printed_without_control_characters(tmp_path, capsys):
    root = tmp_path / "f"
    root.mkdir()
    small(root, **{"a\x1b[31mred.json": '{"a": 1}', "b\x85c.json": '{"b": 1}', "d\x7f.json": "{}"})
    assert main(["index", str(root), "--ocr", "none"]) in (0, 2)
    err = capsys.readouterr().err
    assert "\x1b" not in err and "\x85" not in err and "\x7f" not in err
    assert "a\\x1b[31mred.json" in err and "b\\x85c.json" in err and "d\\x7f.json" in err


def test_a_link_to_a_hidden_or_internal_file_is_skipped(demo):
    run(demo)
    (demo / ".hid").mkdir()
    (demo / ".hid" / "x.json").write_text('{"x": 1}', encoding="utf8")
    (demo / "to-hidden.json").symlink_to(".hid/x.json")
    (demo / "to-state.json").symlink_to(".ragnaroq/changes.json")
    (demo / "sub").mkdir()
    (demo / "sub" / "to-dot.json").symlink_to("../.ragnaroq/manifest.json")
    found = entries(run(demo))
    for name in ("to-hidden.json", "to-state.json", "sub/to-dot.json"):
        assert found[name].skipped == "link to a hidden or internal file", name


def test_a_planted_lock_link_is_removed_and_the_lock_made(demo, tmp_path):
    run(demo)
    target = tmp_path / "target"
    target.write_text("keep")
    (demo / STATE / "lock").unlink()
    (demo / STATE / "lock").symlink_to(target)
    run(demo)
    assert target.read_text() == "keep" and (demo / STATE / "lock").is_file()
    assert not (demo / STATE / "lock").is_symlink()


def test_a_files_output_that_does_not_match_the_manifest_is_read_again(demo, tmp_path):
    first = run(demo)
    entry = entries(first)["rates.csv"]
    folder_ = demo / STATE / "files" / entry.file_id
    forged = json.dumps({"id": "00000000-0000-0000-0000-000000000000", "text": "FORGED"}) + "\n"
    with open(folder_ / "chunks.jsonl", "a") as handle:  # a chunk the manifest does not know
        handle.write(forged)
    second = run(demo)
    assert "FORGED" not in (demo / STATE / "chunks.jsonl").read_text()
    assert entries(second)["rates.csv"].chunk_ids == entry.chunk_ids and second.changes.changed == []
    outside = tmp_path / "other"
    outside.write_text("shared")
    (folder_ / "chunks.jsonl").unlink()
    os.link(outside, folder_ / "chunks.jsonl")  # a second name for it: not what this run wrote
    run(demo)
    assert outside.read_text() == "shared" and (folder_ / "chunks.jsonl").stat().st_nlink == 1


def test_fresh_reads_every_file_again(demo, monkeypatch):
    run(demo)
    calls = []
    real = isolation.read_file

    def spy(data, name, *args, **kwargs):
        calls.append(name)
        return real(data, name, *args, **kwargs)

    monkeypatch.setattr(isolation, "read_file", spy)
    run(demo)
    assert calls == []
    report = run(demo, fresh=True)
    assert {"guide.docx", "rates.xlsx", "deck.pptx", "notice.html", "readme.md"} <= set(calls)
    assert report.changes.changed == [] and report.changes.added == []


def test_nested_folders_are_listed_in_path_order(tmp_path):
    root = tmp_path / "f"
    for name in ("b", "a/b", "a", "B", "a-b"):
        (root / name).mkdir(parents=True, exist_ok=True)
        small(root / name, **{"z.json": '{"z": 1}', "A.json": '{"a": 1}'})
    small(root, **{"top.json": '{"t": 1}'})
    report = run(root)
    paths = [e.path for e in report.manifest.files]
    assert paths == sorted(paths) and len(paths) == 11
    in_chunks = [c["metadata"]["path"] for c in chunks_of(root)]
    assert in_chunks == sorted(in_chunks)


def test_the_manifest_is_written_last_and_an_interrupted_run_leaves_the_old_one(demo, monkeypatch):
    run(demo)
    old = (demo / STATE / "manifest.json").read_bytes()
    (demo / "readme.md").write_text("# Edited\n\nSo that a run has something to do.\n", encoding="utf8")
    order = []
    real = folder._write_file

    def writing(held, name, write_to):
        order.append(name)
        if name == "manifest.json":
            raise KeyboardInterrupt
        return real(held, name, write_to)

    monkeypatch.setattr(folder, "_write_file", writing)
    with pytest.raises(KeyboardInterrupt):
        run(demo)
    assert order == ["chunks.jsonl", "changes.json", "manifest.json"]
    assert (demo / STATE / "manifest.json").read_bytes() == old
    monkeypatch.undo()
    report = run(demo)  # the next run is judged against the last whole run
    assert [entries(report)["readme.md"].file_id] == report.changes.changed


def test_with_no_usable_manifest_the_chunks_of_the_folders_left_are_removed_and_it_is_a_reset(demo):
    first = run(demo)
    assert first.changes.reset  # the first run has no last manifest either
    gone = entries(first)["rates.csv"]
    (demo / STATE / "manifest.json").write_text("garbage")
    (demo / "rates.csv").unlink()
    second = run(demo)
    changes = second.changes
    assert changes.reset and set(gone.chunk_ids) <= set(changes.chunks_removed)
    assert set(changes.chunks_removed).isdisjoint({c["id"] for c in chunks_of(demo)})
    assert set(changes.chunks_added) == {c["id"] for c in chunks_of(demo)}
    assert not run(demo).changes.reset


def test_index_says_plainly_that_it_is_for_linux_and_macos(demo, monkeypatch):
    monkeypatch.setattr(folder.os, "name", "nt")
    with pytest.raises(FolderError, match="Linux and macOS"):
        run(demo)


# ---------------------------------------------------------------- fix round 2 (R30)


def test_a_raised_limit_reads_a_file_again_that_failed_under_the_old_one(tmp_path, misbehaving, monkeypatch):
    root = mixed(tmp_path, "a.json", "slow.csv", "hungry.csv")
    calls = []
    real = isolation.read_file

    def spy(data, name, *args, **kwargs):
        calls.append(name)
        return real(data, name, *args, **kwargs)

    monkeypatch.setattr(isolation, "read_file", spy)
    tight = replace(Config(), file_timeout_seconds=2, page_memory_mb=700)
    for _ in range(2):  # a timeout is tried twice
        found = entries(run(root, tight))
    assert found["slow.csv"].attempts == 2 and found["slow.csv"].limit == 2
    assert found["hungry.csv"].rule == "file.memory" and found["hungry.csv"].limit == 700
    calls.clear()
    assert entries(run(root, tight))["slow.csv"].rule == "file.timeout" and calls == []  # carried over
    calls.clear()
    # A higher time limit reads the file again (and, here, it still times out under the new limit).
    found = entries(run(root, replace(tight, file_timeout_seconds=4)))
    assert calls == ["slow.csv"] and found["slow.csv"].limit == 4 and found["slow.csv"].attempts == 1
    assert "4 seconds" in found["slow.csv"].error
    calls.clear()
    # A higher memory limit reads the memory failure again; the same or a lower one does not.
    run(root, replace(tight, file_timeout_seconds=4, page_memory_mb=500))
    assert "hungry.csv" not in calls
    found = entries(run(root, replace(tight, file_timeout_seconds=4, page_memory_mb=3000)))
    assert "hungry.csv" in calls and found["hungry.csv"].status in ("ok", "review")


def test_a_file_is_recorded_as_the_format_it_was_read_as_and_is_not_read_again(demo, monkeypatch):
    shutil.copy(DEMO / "guide.docx", demo / "really-a-docx.xlsx")
    first = run(demo)
    entry = entries(first)["really-a-docx.xlsx"]
    assert entry.status in ("ok", "review") and entry.format == "docx"
    assert entry.reader == readers.READER_VERSIONS["docx"] and "openpyxl" not in entry.reader
    document = json.loads((demo / STATE / "files" / entry.file_id / "document.json").read_text())
    assert document["source"]["format"] == "docx"

    def refuse(*args, **kwargs):
        raise AssertionError("read again")

    monkeypatch.setattr(isolation, "read_file", refuse)
    second = run(demo)
    assert second.changes.changed == [] and entries(second)["really-a-docx.xlsx"] == entry


def test_images_behind_links_inside_the_folder_load_and_links_out_do_not(tmp_path):
    root, outside = tmp_path / "root", tmp_path / "outside"
    (root / "assets").mkdir(parents=True)
    outside.mkdir()
    png = (DEMO / "diagram.png").read_bytes()
    (root / "assets" / "d.png").write_bytes(png)
    (outside / "o.png").write_bytes(png)
    (root / "pics").mkdir()
    (root / "pics" / "d.png").symlink_to("../assets/d.png")
    (root / "pics" / "out.png").symlink_to(outside / "o.png")
    (root / "alias").symlink_to("assets", target_is_directory=True)
    (root / "away").symlink_to(outside, target_is_directory=True)
    (root / "page.md").write_text(
        "# Page\n\n![a](pics/d.png)\n\n![b](alias/d.png)\n\n![c](assets/d.png)\n\n"
        "![d](pics/out.png)\n\n![e](away/o.png)\n",
        encoding="utf8",
    )
    report = run(root)
    entry = entries(report)["page.md"]
    document = json.loads((root / STATE / "files" / entry.file_id / "document.json").read_text())
    figures = [f for page in document["pages"] for f in page["figures"]]
    rules = {f["description"]: [x["rule"] for x in f["flags"]] for f in figures}
    for name in "abc":
        assert "image.outside-folder" not in rules[name] and "image.not-found" not in rules[name], name
    assert figures[0]["path"].startswith("images/") and figures[1]["path"] == figures[0]["path"]
    assert "image.outside-folder" in rules["d"] and "image.outside-folder" in rules["e"]


# --- final review I4: index takes convert's output options ---------------------------------------


def test_index_passes_convert_options_and_max_cells_to_the_run(tmp_path, monkeypatch):
    seen = {}

    def fake_index(root, config, **kwargs):
        seen["config"] = config
        raise folder.FolderError("stop here")

    monkeypatch.setattr(folder, "index", fake_index)
    options = ["--chunk-chars", "300", "--trust-native", "--same-letters", "--page-timeout", "30",
               "--page-memory-mb", "4096", "--max-cells", "5000", "--file-timeout", "9"]
    assert main(["index", str(tmp_path), "--ocr", "none", *options]) == 1
    config = seen["config"]
    assert (config.chunk_chars, config.page_timeout_seconds, config.page_memory_mb) == (300, 30, 4096)
    assert (config.max_cells, config.file_timeout_seconds) == (5000, 9)
    assert config.policy.accept_native_without_crosscheck and config.policy.same_letters


def test_every_option_that_changes_output_changes_the_settings_hash():
    def digest(fmt, **changes):
        config = replace(Config(), **changes)
        return folder._hash(folder._settings(config, [], None), fmt)

    base = {fmt: digest(fmt) for fmt in ("pdf", "csv", "docx")}
    for change in (
        {"chunk_chars": 300},
        {"max_cells": 5000},
        {"policy": replace(Config().policy, accept_native_without_crosscheck=True)},
        {"policy": replace(Config().policy, same_letters=True)},
    ):
        assert all(digest(fmt, **change) != base[fmt] for fmt in base), change
    # A PDF's pages are read under the page limits, so they change its output; other files are
    # read whole under file_timeout_seconds and page_memory_mb, and a failure records its limit.
    for change in ({"page_timeout_seconds": 30.0}, {"page_memory_mb": 4096}):
        assert digest("pdf", **change) != base["pdf"], change
        assert digest("csv", **change) == base["csv"] and digest("docx", **change) == base["docx"], change
    assert digest("csv", file_timeout_seconds=9.0) == base["csv"]
