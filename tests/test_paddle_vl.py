import os

import pytest
from PIL import Image, ImageDraw, ImageFont

from ragnaroq.ocr import EngineUnavailable, LineUnreadable, paddle_vl
from ragnaroq.ocr.paddle_vl import FILES, PaddleOcrVl, check_finished, number_format, verify


@pytest.fixture
def no_torch_needed(monkeypatch):
    """Stand-ins for torch and the hardware probe, so the reader's bookkeeping runs in CI."""
    monkeypatch.setattr(PaddleOcrVl, "_torch", staticmethod(lambda: object()))
    monkeypatch.setattr(PaddleOcrVl, "_hardware", lambda self, torch: ("cpu", "bfloat16"))
    versions = {"torch": "2.9.1+cpu", "transformers": "5.2.0", "torchvision": "0.24.1+cpu"}
    monkeypatch.setattr(paddle_vl, "_package_version", versions.get)
    return versions


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
