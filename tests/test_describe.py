"""Optional model-written image descriptions: a label beside the description, never a verdict."""

import importlib.util
import os
import sys
import time
import types
from pathlib import Path

import pytest
from PIL import Image, ImageDraw, ImageFont
from test_figures import fig, lines, picture, result, twice

from ragnaroq import figures, folder, policy
from ragnaroq.cli import main
from ragnaroq.config import Config
from ragnaroq.export import chunks, markdown
from ragnaroq.model import Figure
from ragnaroq.ocr import EngineUnavailable, describe
from ragnaroq.ocr.describe import FILES, SmolVlm, accept, verify

CONFIG = Config()
DEMO = Path(__file__).parent.parent / "references" / "demo"


class Says:
    """A stand-in describer: always says `text`, and remembers what it was asked."""

    name = "says"

    def __init__(self, text):
        self.text, self.calls = text, []

    def version(self):
        return "says 1"

    def describe(self, image, seconds):
        self.calls.append((image.size, seconds))
        return self.text


def described(text, ocr="₹12,000", *, engines=None, source=None, deadline=None):
    figure = fig("p4-f1", **({"description": "Alt text", "description_source": source} if source else {}))
    res = result((figure, picture()))
    engines = twice(ocr) if engines is None else engines
    describer = Says(text)
    figures.describe(res, engines, CONFIG, deadline=deadline, describer=describer)
    return lines(res)[0], describer


# --- the plan's tests -------------------------------------------------------------------------------


def test_a_model_sentence_is_kept_as_a_label_and_never_changes_the_status():
    plain = result((fig("p4-f1"), picture()))
    figures.describe(plain, twice(), CONFIG)
    figure, describer = described("A price tag that reads ₹12,000.")
    assert figure.model_description == "A price tag that reads ₹12,000."
    assert figure.description == "₹12,000" and figure.description_source == "ocr"  # the label is extra
    assert figure.status == lines(plain)[0].status == "accepted"
    assert describer.calls == [((120, 60), describe.SECONDS)] and describe.SECONDS == 60
    # Even where nothing else describes the picture, the label decides nothing.
    blank, _ = described("A red rectangle.", engines=[])
    assert blank.model_description == "A red rectangle." and blank.description_source == "none"
    assert blank.status == "flagged"


def test_a_sentence_with_a_number_missing_from_the_ocr_text_is_dropped():
    figure, _ = described("A bar chart of 1,500 units.")
    assert figure.model_description is None
    assert figure.description == "₹12,000" and figure.status == "accepted"
    kept, _ = described("A price of 12000 rupees.")  # separators do not count
    assert kept.model_description == "A price of 12000 rupees."


def test_describing_is_off_by_default():
    res = result((fig("p4-f1"), picture()))
    figures.describe(res, twice(), CONFIG)
    assert lines(res)[0].model_description is None
    settings = folder._settings(CONFIG, [], None)
    assert settings["describe"] is None


# --- the filter, one case each (R31 b) --------------------------------------------------------------


@pytest.mark.parametrize(
    "raw",
    [
        "",
        "   ",
        "A table with rows of numbers and",  # cut off: no sentence end
        "0",
        "The image contains a table with columns labeled Advertising, Car and",
    ],
)
def test_a_sentence_that_does_not_end_cleanly_is_dropped(raw):
    figure, _ = described(raw)
    assert figure.model_description is None
    assert accept(raw, "") is None


def test_a_long_answer_is_cut_at_its_first_sentence_end():
    assert accept("A form. It then goes on and on 999.", "") == "A form."
    assert accept('A sign saying "Open." and more', "") == 'A sign saying "Open."'
    assert accept("Part I Income.", "part i income") == "Part I Income."
    assert accept("Pay 5.50 now. Then 7.", "5.50") == "Pay 5.50 now."  # a decimal point is no end


def test_a_critical_token_must_be_in_the_ocr_text():
    assert accept("The rate is 12%.", "rate 12 %") == "The rate is 12%."
    assert accept("The rate is 15%.", "rate 12 %") is None
    assert accept("Due 31 March 2026.", "Due date: 31/03/2026") is None  # a date written another way
    assert accept("Due 12/31/2026.", "due 12-31-2026") == "Due 12/31/2026."
    assert accept("It is Form 1040.", "SCHEDULE C (Form 1040)") == "It is Form 1040."
    assert accept("About 6 boxes.", "line 16") is None  # a number inside another number is not found
    assert accept("It is not 5.", "5") == "It is not 5."  # words such as "not" are never required


def test_a_currency_sign_must_be_in_the_ocr_text_too():
    assert accept('The text reads "¥12,000".', "₹12,000") is None  # the spike's yen for rupee
    assert accept('The text reads "₹12,000".', "₹12,000") == 'The text reads "₹12,000".'
    assert accept("It shows $ 11,950.", "$11,950 or less") == "It shows $ 11,950."
    assert accept("It lists amounts in €.", "12,000") is None  # a sign with no number beside it
    figure, _ = described('The text reads "¥12,000".')
    assert figure.model_description is None


def test_an_image_with_no_ocr_text_keeps_only_a_sentence_with_no_critical_token():
    assert accept("A red rectangle.", "") == "A red rectangle."
    assert accept("A red rectangle 3 wide.", "") is None
    assert accept("A coin marked $.", "") is None
    nothing, _ = described("Two boxes joined by a line.", engines=[])
    assert nothing.model_description == "Two boxes joined by a line."
    invented, _ = described("A chart with 12 bars.", engines=[])
    assert invented.model_description is None


# --- where and when it runs -------------------------------------------------------------------------


def test_the_ocr_text_checked_is_the_images_own_even_when_alt_text_describes_it():
    ok, _ = described("A price tag reading ₹12,000.", source="alt")
    assert ok.description == "Alt text" and ok.model_description == "A price tag reading ₹12,000."
    bad, _ = described("A chart of 99 bars.", source="alt")
    assert bad.model_description is None
    assert bad.description == "Alt text"


def test_one_image_is_described_once_however_many_figures_show_it():
    data = picture()
    res = result((fig("a"), data), (fig("b"), data))
    describer = Says("A red box.")
    figures.describe(res, [], CONFIG, describer=describer)
    assert len(describer.calls) == 1
    assert [f.model_description for f in lines(res)] == ["A red box.", "A red box."]


def test_images_past_the_deadline_are_not_described():
    figure, describer = described("A red box.", deadline=time.monotonic() - 1)
    assert describer.calls == [] and figure.model_description is None


def test_a_describer_that_fails_leaves_the_figure_as_it_was():
    class Broken(Says):
        def describe(self, image, seconds):
            raise RuntimeError("out of memory")

    res = result((fig("a"), picture()))
    figures.describe(res, twice(), CONFIG, describer=Broken(""))
    figure = lines(res)[0]
    assert figure.model_description is None and figure.status == "accepted"


def test_an_image_the_model_cannot_be_given_is_not_described():
    svg = b'<svg xmlns="http://www.w3.org/2000/svg" width="40" height="40"/>'
    res = result((fig("a"), svg), (fig("b"), picture(size=(10, 10))), (fig("c"), None))
    describer = Says("A red box.")
    figures.describe(res, [], CONFIG, describer=describer)
    assert describer.calls == []


def test_a_second_run_replaces_the_label():
    res = result((fig("a"), picture()))
    figures.describe(res, twice(), CONFIG, describer=Says("It reads ₹12,000."))
    figures.describe(res, twice(), CONFIG, describer=Says("It reads 5."))
    assert lines(res)[0].model_description is None


def test_a_model_description_is_excluded_from_the_critical_token_rules():
    figure = Figure(
        id="f",
        page=1,
        anchor=fig("x").anchor,
        description="₹12,000",
        description_source="alt",
        model_description="Wrong 99.",
    )
    res = result((figure, None))
    policy.decide(res.document, CONFIG.policy)
    assert lines(res)[0].status == "accepted" and lines(res)[0].flags == []


# --- export -------------------------------------------------------------------------------------------


def test_the_chunk_keeps_the_description_and_embeds_the_label_marked_model():
    figure, _ = described("A price tag reading ₹12,000.")
    figure.status = "accepted"
    res = result((figure, None))
    res.document.title = None
    (chunk,) = chunks.to_chunks(res.document, CONFIG)
    assert chunk["text"] == "₹12,000"
    assert chunk["embedding_text"].endswith("₹12,000\n(model) A price tag reading ₹12,000.")
    assert chunk["metadata"]["chars"] == len("₹12,000")
    only = figure.model_copy(update={"description": "", "description_source": "none"})
    res.document.pages[0].figures = [only]
    (chunk,) = chunks.to_chunks(res.document, CONFIG, include=("accepted", "flagged"))
    assert chunk["text"] == "" and chunk["embedding_text"].endswith("(model) A price tag reading ₹12,000.")


def test_document_md_shows_the_label_after_the_description_and_escapes_it():
    figure = fig("a", description="₹12,000", description_source="alt", path="images/a.png")
    figure.model_description = "A <b>tag</b> [x](http://evil) & more."
    res = result((figure, None))
    text = markdown.to_markdown(res.document)
    assert "— model: A \\<b\\>tag\\</b\\> \\[x\\](http://evil) \\& more." in text
    assert text.index("![₹12,000](images/a.png)") < text.index("— model:")
    figure.model_description = None
    assert "model:" not in markdown.to_markdown(res.document)


# --- the index ----------------------------------------------------------------------------------------


def test_the_describer_is_part_of_the_settings_and_reaches_the_figures(tmp_path):
    root = tmp_path / "demo"
    root.mkdir()
    (root / "deck.pptx").write_bytes((DEMO / "deck.pptx").read_bytes())

    def run(describer):
        return folder.index(str(root), CONFIG, engines=[], describer=describer)

    base = run(None)
    one = run(Says("A picture."))
    assert one.changes.changed == [e.file_id for e in one.manifest.files]  # the settings changed
    again = run(Says("A picture."))
    assert again.changes.changed == []
    other = Says("A picture.")
    other.version = lambda: "says 2"
    assert run(other).changes.changed == one.changes.changed
    assert [e.settings for e in base.manifest.files] != [e.settings for e in one.manifest.files]
    state = root / folder.STATE
    assert "(model) A picture." in (state / "chunks.jsonl").read_text(encoding="utf8")
    assert folder._settings(CONFIG, [], None, Says("x"))["describe"] == ["says", "says 1"]


# --- the reader -------------------------------------------------------------------------------------


def test_the_pinned_files_are_the_twelve_from_the_spike():
    assert describe.REPO == "HuggingFaceTB/SmolVLM-500M-Instruct"
    assert describe.REVISION == "753c1752602b5e739af7a7fa6947a635051897a3"
    assert len(FILES) == 12 and not any(name.endswith(".py") for name in FILES)
    assert FILES["model.safetensors"].startswith("d05b567e")


def test_missing_files_name_the_download_switch(tmp_path):
    with pytest.raises(EngineUnavailable, match="--describe-download"):
        SmolVlm(model_dir=str(tmp_path)).prepare()


def test_a_changed_or_missing_file_is_refused(tmp_path):
    for name in FILES:
        (tmp_path / name).write_text("x")
    with pytest.raises(EngineUnavailable, match="added_tokens.json"):
        verify(tmp_path)
    (tmp_path / "vocab.json").unlink()
    with pytest.raises(EngineUnavailable, match="vocab.json"):
        verify(tmp_path)


@pytest.fixture
def fake_stack(monkeypatch, tmp_path):
    calls = []

    class Loaded:
        device = "cpu"

        def eval(self):
            return self

        def to(self, device):
            return self

    def loader(kind):
        def from_pretrained(folder_, **kwargs):
            calls.append((kind, Path(folder_), kwargs))
            return Loaded()

        return types.SimpleNamespace(from_pretrained=from_pretrained)

    def snapshot_download(repo, **kwargs):
        calls.append(("download", None, {**kwargs, "xet": os.environ.get("HF_HUB_DISABLE_XET")}))
        return str(tmp_path / "landed")

    torch = types.SimpleNamespace(float32="float32")
    monkeypatch.setattr(SmolVlm, "_torch", staticmethod(lambda: torch))
    transformers = types.SimpleNamespace(
        AutoProcessor=loader("processor"), AutoModelForImageTextToText=loader("model")
    )
    monkeypatch.setitem(sys.modules, "transformers", transformers)
    monkeypatch.setitem(
        sys.modules, "huggingface_hub", types.SimpleNamespace(snapshot_download=snapshot_download)
    )
    monkeypatch.setattr(describe, "verify", lambda folder_: calls.append(("verify", Path(folder_), {})))
    monkeypatch.delenv("HF_HUB_DISABLE_XET", raising=False)
    return calls


def test_the_model_is_loaded_offline_from_safetensors_and_never_runs_remote_code(fake_stack, tmp_path):
    for name in FILES:
        (tmp_path / name).write_text("x")
    SmolVlm(model_dir=str(tmp_path)).prepare()
    loads = [kwargs for kind, _, kwargs in fake_stack if kind in ("processor", "model")]
    assert len(loads) == 2
    for kwargs in loads:
        assert kwargs["local_files_only"] is True and kwargs["use_safetensors"] is True
        assert kwargs["trust_remote_code"] is False


def test_a_download_fetches_only_the_pinned_files_without_xet(fake_stack, monkeypatch, tmp_path):
    monkeypatch.setenv("HF_HUB_CACHE", str(tmp_path / "empty"))
    SmolVlm(download=True).prepare()
    (kind, _, kwargs), *rest = fake_stack
    assert kind == "download" and kwargs["allow_patterns"] == list(FILES)
    assert kwargs["revision"] == describe.REVISION and kwargs["xet"] == "1"
    assert [(k, f) for k, f, _ in rest] == [
        ("verify", tmp_path / "landed"),
        ("processor", tmp_path / "landed"),
        ("model", tmp_path / "landed"),
    ]


def test_a_download_error_keeps_its_message(fake_stack, monkeypatch, tmp_path):
    def offline(repo, **kwargs):
        raise ConnectionError("no route to host")

    monkeypatch.setattr(sys.modules["huggingface_hub"], "snapshot_download", offline)
    with pytest.raises(EngineUnavailable, match="ConnectionError: no route to host"):
        SmolVlm(model_dir=str(tmp_path / "empty"), download=True).prepare()


# --- the command line -------------------------------------------------------------------------------


def test_index_and_convert_take_the_describe_switches(tmp_path, capsys, native_pdf):
    root = tmp_path / "docs"
    root.mkdir()
    (root / "a.md").write_text("# A\n\nWords.\n", encoding="utf8")
    code = main(
        [
            "index",
            str(root),
            "--ocr",
            "none",
            "--describe-images",
            "--describe-models",
            str(tmp_path / "none"),
        ]
    )
    assert code == 1 and "--describe-download" in capsys.readouterr().err
    assert main(["index", str(root), "--ocr", "none"]) in (0, 2)
    capsys.readouterr()
    # A PDF has no figures yet, so the model is not loaded for it, and the run says so.
    code = main(
        ["convert", str(native_pdf), "--out", str(tmp_path / "o"), "--ocr", "none", "--describe-images"]
    )
    assert code in (0, 2)
    assert "no effect" in capsys.readouterr().err


# --- the real model -----------------------------------------------------------------------------------


def _model_folder():
    return (
        Path(os.environ["RAGNAROQ_DESCRIBE_MODELS"]) if os.environ.get("RAGNAROQ_DESCRIBE_MODELS") else None
    )


def _have_model():
    if importlib.util.find_spec("torch") is None or importlib.util.find_spec("transformers") is None:
        return False
    return (_model_folder() or describe.default_folder()).joinpath("model.safetensors").is_file()


@pytest.mark.skipif(not _have_model(), reason="needs torch, transformers and the SmolVLM files")
def test_the_real_model_describes_a_picture_and_the_filter_checks_it(capsys):
    text = "Investment income $11,950 or less"
    image = Image.new("RGB", (900, 120), "white")
    ImageDraw.Draw(image).text((20, 30), text, fill="black", font=ImageFont.load_default(size=44))
    reader = SmolVlm(model_dir=str(_model_folder()) if _model_folder() else None)
    reader.prepare()
    raw = reader.describe(image, describe.SECONDS)
    kept = accept(raw, text)
    with capsys.disabled():
        print(f"\nREAL MODEL raw={raw!r} kept={kept!r}")
    assert isinstance(raw, str) and (kept is None or kept in raw)
    assert reader.version().startswith(describe.REPO)
