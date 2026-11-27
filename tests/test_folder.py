"""`ragnaroq index`: the folder, the manifest and incremental runs."""

import fcntl
import hashlib
import io
import json
import os
import shutil
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
    assert changes == {"added": [], "changed": [], "removed": [], "chunks_added": [], "chunks_removed": []}
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


def mixed(tmp_path):
    root = tmp_path / "m"
    root.mkdir()
    small(
        root,
        **{
            "a.json": '{"a": 1}',
            "slow.csv": "SLOW,1\n",
            "crash.csv": "CRASH,1\n",
            "hungry.csv": "HUNGRY,1\n",
            "z.json": '{"z": 2}',
            "ok.csv": "x,y\n1,2\n",
        },
    )
    return root


def test_a_file_that_takes_too_long_fails_alone(tmp_path, misbehaving):
    root = mixed(tmp_path)
    report = run(root, replace(Config(), file_timeout_seconds=3))
    found = entries(report)
    assert found["slow.csv"].status == "failed" and found["slow.csv"].rule == "file.timeout"
    assert "3 seconds" in found["slow.csv"].error
    for name in ("a.json", "z.json", "ok.csv"):  # before and after it, with a new worker
        assert found[name].status in ("ok", "review") and found[name].chunk_ids, name
    assert report.failed == 2  # slow.csv, and crash.csv (which kills the worker when it is reached)


@pytest.mark.skipif(sys.platform != "linux", reason="memory limits are enforced on Linux")
def test_a_file_that_needs_too_much_memory_or_crashes_the_reader_fails_alone(tmp_path, misbehaving):
    root = mixed(tmp_path)
    found = entries(run(root, replace(Config(), page_memory_mb=700, file_timeout_seconds=3)))
    assert found["crash.csv"].rule == "file.crashed" and "exited with code 7" in found["crash.csv"].error
    assert found["hungry.csv"].rule == "file.memory"
    assert all(found[n].status in ("ok", "review") for n in ("a.json", "z.json", "ok.csv"))


def test_failures_that_may_not_repeat_are_tried_again_and_refusals_are_not(
    tmp_path, misbehaving, monkeypatch
):
    root = mixed(tmp_path)
    config = replace(Config(), file_timeout_seconds=3)
    shutil.copy(DEMO / "bomb.xml", root)
    first = run(root, config)
    assert entries(first)["bomb.xml"].status == "failed" and entries(first)["slow.csv"].status == "failed"
    calls = []
    real = isolation.read_file

    def spy(data, name, *args, **kwargs):
        calls.append(name)
        return real(data, name, *args, **kwargs)

    monkeypatch.setattr(isolation, "read_file", spy)
    again = run(root, config)
    # The timeout and the crash may not happen again; the refused bomb, and what was read, are carried over.
    assert sorted(calls) == ["crash.csv", "slow.csv"]
    assert entries(again)["bomb.xml"] == entries(first)["bomb.xml"] and not again.changes.changed


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
    real = os.walk

    def walking(top, followlinks=False, onerror=None):
        yield from real(top, followlinks=followlinks, onerror=onerror)
        onerror(PermissionError(13, "Permission denied", os.path.join(top, "locked")))

    monkeypatch.setattr(folder.os, "walk", walking)
    found = entries(run(demo))
    assert found["locked"].skipped == "unreadable folder" and found["readme.md"].status in ("ok", "review")
