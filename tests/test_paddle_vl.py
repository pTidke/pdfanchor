import os
import sys
import types
from pathlib import Path

import pytest
from PIL import Image, ImageDraw, ImageFont

from ragnaroq.ocr import EngineUnavailable, LineUnreadable, paddle_vl
from ragnaroq.ocr.paddle_vl import FILES, PaddleOcrVl, check_finished, number_format, verify


@pytest.fixture
def no_torch_needed(monkeypatch):
    """Stand-ins for torch and the hardware probe, so the reader's bookkeeping runs in CI."""
    torch = types.SimpleNamespace(bfloat16="bfloat16", float32="float32")
    monkeypatch.setattr(PaddleOcrVl, "_torch", staticmethod(lambda: torch))
    monkeypatch.setattr(PaddleOcrVl, "_hardware", lambda self, torch: ("cpu", "bfloat16"))
    versions = {"torch": "2.9.1+cpu", "transformers": "5.2.0", "torchvision": "0.24.1+cpu"}
    monkeypatch.setattr(paddle_vl, "_package_version", versions.get)
    return versions


@pytest.fixture
def fake_stack(monkeypatch, tmp_path, no_torch_needed):
    """transformers and huggingface_hub stand-ins that record what they are asked for. A
    download lands in `<tmp_path>/landed`, as snapshot_download returns its own folder."""
    calls = []

    class Loaded:
        device = "cpu"

        def eval(self):
            return self

        def to(self, device):
            return self

    def loader(kind):
        def from_pretrained(folder, **kwargs):
            calls.append((kind, Path(folder), kwargs))
            return Loaded()

        return types.SimpleNamespace(from_pretrained=from_pretrained)

    def snapshot_download(repo, **kwargs):
        calls.append(("download", None, kwargs))
        return str(tmp_path / "landed")

    transformers = types.SimpleNamespace(
        AutoProcessor=loader("processor"), AutoModelForImageTextToText=loader("model")
    )
    monkeypatch.setitem(sys.modules, "transformers", transformers)
    hub = types.SimpleNamespace(snapshot_download=snapshot_download)
    monkeypatch.setitem(sys.modules, "huggingface_hub", hub)
    monkeypatch.setattr(paddle_vl, "verify", lambda folder: calls.append(("verify", Path(folder), {})))
    return calls


def test_number_format_follows_the_hardware():
    assert number_format("cpu", frozenset({"avx512_bf16"})) == "bfloat16"
    assert number_format("cpu", frozenset({"amx_bf16"})) == "bfloat16"
    assert number_format("cpu", frozenset({"avx2", "avx512f"})) == "float32"
    assert number_format("cuda", frozenset(), cuda_bf16=True) == "bfloat16"
    assert number_format("cuda", frozenset(), cuda_bf16=False) == "float32"


def test_missing_files_name_the_download_switch(tmp_path):
    with pytest.raises(EngineUnavailable, match="--vlm-download"):
        PaddleOcrVl(model_dir=str(tmp_path)).prepare()


def test_a_changed_file_is_refused(tmp_path):
    for name in FILES:
        (tmp_path / name).write_text("x")
    with pytest.raises(EngineUnavailable, match="added_tokens.json"):
        verify(tmp_path)


def test_the_version_names_the_libraries_and_the_reading_recipe(no_torch_needed):
    reader = PaddleOcrVl()
    version = reader.version()
    assert version == (
        "PaddlePaddle/PaddleOCR-VL-1.6@132c45f52c0a bfloat16 cpu torch=2.9.1+cpu transformers=5.2.0 "
        "torchvision=0.24.1+cpu recipe=ocr-greedy-1"
    )
    no_torch_needed["torch"] = "2.10.0+cpu"
    assert reader.version() == version  # worked out once for the reader
    assert "torch=2.10.0+cpu" in PaddleOcrVl().version()  # an upgrade changes it, and so the cache key
    del no_torch_needed["torchvision"]
    assert " no torchvision " in PaddleOcrVl().version()
    del no_torch_needed["transformers"]
    with pytest.raises(EngineUnavailable, match="transformers"):
        PaddleOcrVl().version()


def test_the_default_folder_follows_hf_hub_cache(monkeypatch, tmp_path):
    snapshot = Path("models--PaddlePaddle--PaddleOCR-VL-1.6", "snapshots", paddle_vl.REVISION)
    for name in ("HF_HUB_CACHE", "HUGGINGFACE_HUB_CACHE", "HF_HOME", "XDG_CACHE_HOME"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    assert PaddleOcrVl()._folder() == tmp_path / "home" / ".cache" / "huggingface" / "hub" / snapshot
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "xdg"))
    assert PaddleOcrVl()._folder() == tmp_path / "xdg" / "huggingface" / "hub" / snapshot
    monkeypatch.setenv("HF_HOME", str(tmp_path / "hf"))
    assert PaddleOcrVl()._folder() == tmp_path / "hf" / "hub" / snapshot
    monkeypatch.setenv("HUGGINGFACE_HUB_CACHE", str(tmp_path / "legacy"))
    assert PaddleOcrVl()._folder() == tmp_path / "legacy" / snapshot
    monkeypatch.setenv("HF_HUB_CACHE", str(tmp_path / "hub"))
    assert PaddleOcrVl()._folder() == tmp_path / "hub" / snapshot
    assert PaddleOcrVl(model_dir=str(tmp_path / "mine"))._folder() == tmp_path / "mine"


def test_a_missing_file_is_reported_by_name(tmp_path):
    for name in FILES:
        if name != "tokenizer.json":
            (tmp_path / name).write_text("x")
    with pytest.raises(EngineUnavailable, match="tokenizer.json") as raised:
        verify(tmp_path)
    assert "added_tokens.json" not in str(raised.value)


def test_after_a_download_the_files_are_checked_and_loaded_where_they_landed(
    fake_stack, monkeypatch, tmp_path
):
    monkeypatch.setenv("HF_HUB_CACHE", str(tmp_path / "empty"))
    PaddleOcrVl(download=True).prepare()
    assert [(kind, folder) for kind, folder, _ in fake_stack] == [
        ("download", None),
        ("verify", tmp_path / "landed"),
        ("processor", tmp_path / "landed"),
        ("model", tmp_path / "landed"),
    ]


def test_no_code_from_the_model_repository_is_fetched():
    assert FILES and not any(name.endswith(".py") for name in FILES)


def test_an_unfinished_line_is_given_up():
    check_finished(40, 160, 3.0, 120.0)  # no error
    with pytest.raises(LineUnreadable, match="120 seconds"):
        check_finished(40, 160, 120.0, 120.0)
    with pytest.raises(LineUnreadable, match="160 tokens"):
        check_finished(160, 160, 3.0, 120.0)


@pytest.mark.skipif(
    not os.environ.get("RAGNAROQ_VLM_MODELS"), reason="set RAGNAROQ_VLM_MODELS to a model folder"
)
def test_the_real_model_reads_a_clean_line():
    text = "The due date is 31st July 2026."
    image = Image.new("RGB", (1400, 120), "white")
    ImageDraw.Draw(image).text((20, 30), text, fill="black", font=ImageFont.load_default(size=48))
    reader = PaddleOcrVl(model_dir=os.environ["RAGNAROQ_VLM_MODELS"])
    reader.prepare()
    assert reader.read_line(image, 160, 120.0) == text
