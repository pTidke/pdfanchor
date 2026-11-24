import os

import pytest
from PIL import Image, ImageDraw, ImageFont

from ragnaroq.ocr import EngineUnavailable, LineUnreadable
from ragnaroq.ocr.paddle_vl import FILES, PaddleOcrVl, check_finished, number_format, verify


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
