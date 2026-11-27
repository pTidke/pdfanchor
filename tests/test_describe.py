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
    assert accept("A red form here. It then goes on 999.", "") == "A red form here."
    assert accept('A sign saying "Open." and more', "") == 'A sign saying "Open."'
    assert accept("Part I Income.", "part i income") == "Part I Income."  # three words, no number
    assert accept("Pay 5.50 now. Then 7.", "5.50") == "Pay 5.50 now."  # a decimal point is no end


def test_a_critical_token_must_be_in_the_ocr_text():
    assert accept("The rate is 12%.", "rate 12 %") == "The rate is 12%."
    assert accept("The rate is 15%.", "rate 12 %") is None
    assert accept("Due 31 March 2026.", "Due date: 31/03/2026") is None  # a date written another way
    assert accept("Due 12/31/2026 here.", "due 12-31-2026") is None  # "/" and "-" are not alike
    assert accept("Due 12/31/2026 here.", "due 12/31/2026") == "Due 12/31/2026 here."
    assert accept("It is Form 1040.", "SCHEDULE C (Form 1040)") == "It is Form 1040."
    assert accept("About 6 boxes.", "line 16") is None  # a number inside another number is not found
    assert accept("It is not 5.", "5") is None  # a "not" the picture does not have
    assert accept("It is not 5.", "is not 5") == "It is not 5."


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


def test_a_figure_whose_only_text_is_the_label_makes_no_chunk_but_keeps_the_label():
    figure, _ = described("Two boxes joined by a line.", engines=[])
    assert figure.description == "" and figure.model_description
    res = result((figure, None))
    assert chunks.to_chunks(res.document, CONFIG) == []
    assert "— model: Two boxes joined by a line." in markdown.to_markdown(res.document)
    assert "Two boxes joined" in res.document.model_dump_json()


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
    one = run(Says("A small picture here."))
    assert one.changes.changed == [e.file_id for e in one.manifest.files]  # the settings changed
    again = run(Says("A small picture here."))
    assert again.changes.changed == []
    other = Says("A small picture here.")
    other.version = lambda: "says 2"
    assert run(other).changes.changed == one.changes.changed
    assert [e.settings for e in base.manifest.files] != [e.settings for e in one.manifest.files]
    state = root / folder.STATE
    documents = [path.read_text(encoding="utf8") for path in state.rglob("document.json")]
    assert any("A small picture here." in text for text in documents)
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


# --- fix round 1 (R32) ------------------------------------------------------------------------------


@pytest.mark.parametrize("which", ["describe", "paddle_vl"])
def test_the_download_switches_xet_off_in_the_library_as_well_as_the_environment(
    which, monkeypatch, tmp_path
):
    from ragnaroq.ocr import paddle_vl

    reader = SmolVlm if which == "describe" else paddle_vl.PaddleOcrVl
    seen = []
    constants = types.SimpleNamespace(HF_HUB_DISABLE_XET=False)  # read once, when the library loaded

    def snapshot_download(repo, **kwargs):
        seen.append((constants.HF_HUB_DISABLE_XET, os.environ.get("HF_HUB_DISABLE_XET")))
        raise ConnectionError("stop here")

    hub = types.SimpleNamespace(snapshot_download=snapshot_download, constants=constants)
    monkeypatch.setitem(sys.modules, "huggingface_hub", hub)
    monkeypatch.setitem(sys.modules, "transformers", types.SimpleNamespace())
    monkeypatch.delenv("HF_HUB_DISABLE_XET", raising=False)
    monkeypatch.setattr(reader, "_torch", staticmethod(lambda: types.SimpleNamespace()))
    with pytest.raises(EngineUnavailable):
        reader(model_dir=str(tmp_path / "none"), download=True).prepare()
    assert seen == [(True, "1")]


@pytest.mark.parametrize(
    ("raw", "ocr"),
    [
        ("The amount is not 12,000.", "Amount 12,000"),  # a negation the picture does not have
        ("There is no balance due.", "Balance due"),
        ("Dated 5 January 2024.", "Dated 5 March 2024"),
        ("It shows 12,000 rupees.", "$12,000"),
        ("It shows 12,000 dollars.", "₹12,000"),
        ("It shows 12,000 euros.", "₹12,000"),
        ("It shows 12,000 yen in total.", "₹12,000"),
        ("It shows 12 lakh in total.", "12 crore"),
        ("It shows 12 million in total.", "12"),
    ],
)
def test_negations_months_currency_words_and_scales_must_be_in_the_ocr_text(raw, ocr):
    assert accept(raw, [ocr]) is None


@pytest.mark.parametrize(
    ("raw", "ocr"),
    [
        ("The amount is not 12,000.", "Amount is NOT 12,000"),
        ("Dated 5 Jan 2024 here.", "Dated 5 January 2024"),
        ("It shows 12,000 rupees.", "Rs. 12,000"),
        ("It shows 12,000 rupees.", "₹12,000"),
        ("It shows INR 12,000.", "₹12,000"),
        ("It shows 12 lakh in total.", "12 Lakh"),
        ("It may be 12 here.", "12"),  # "may" the verb is not a month
    ],
)
def test_the_same_facts_in_other_words_are_kept(raw, ocr):
    assert accept(raw, [ocr]) == raw


@pytest.mark.parametrize(
    ("raw", "lines"),
    [
        ("The rate is 1.5 here.", ["Rate 15"]),
        ("The rate is 15 here.", ["Rate 1.5"]),
        ("The value is 12 here.", ["Value 1.2"]),
        ("The page is 12 here.", ["Line 1", "2 apples"]),  # never across lines
        ("The page is 12 here.", ["Line 1 2 apples"]),
        ("The date is 2024-01-15 here.", ["Date 15/01/2024"]),
        ("The range is 10-20 here.", ["range 1020"]),
        ("The tax rate is 5% here.", ["Rate 5"]),
        ("The tax rate is 5% here.", ["Rate 15%"]),
        ("The total is 1,234 here.", ["Total 1,234,567"]),
    ],
)
def test_numbers_are_matched_as_tokens_one_ocr_line_at_a_time(raw, lines):
    assert accept(raw, lines) is None


@pytest.mark.parametrize(
    ("raw", "lines"),
    [
        ("The total is 12,000 here.", ["Total 12000"]),
        ("The total is 12000 here.", ["Total 12,000"]),
        ("The total is 12, 000 here.", ["Total 12,000"]),
        ("The rate is 1.5 here.", ["x", "Rate 1.5"]),
        ("The tax rate is 5% here.", ["Rate 5 %"]),
        ("The date is 15/01/2024 here.", ["Date 15/01/2024"]),
    ],
)
def test_commas_and_spaces_inside_a_number_do_not_matter(raw, lines):
    assert accept(raw, lines) == raw


@pytest.mark.parametrize(
    "raw",
    [
        "See Fig. 3 for the chart.",
        "It is No. 12 on the form.",
        "Dr. Rao paid Rs. 99 on the form.",
        "Meet at 10 a.m. for the forms.",
        "A form, e.g. a tax return, is shown.",
    ],
)
def test_a_point_after_an_abbreviation_is_not_a_sentence_end(raw):
    assert describe.first_sentence(raw) == raw


def test_an_abbreviation_with_no_real_end_gives_nothing_and_short_sentences_are_dropped():
    assert describe.first_sentence("See Fig. 3 for the chart") == ""
    assert accept("See Fig.", "") is None
    assert accept("A form.", "") is None
    assert accept("A red form.", "") == "A red form."


def test_the_describers_failures_are_counted_for_the_index_notes(tmp_path):
    class Broken(Says):
        def describe(self, image, seconds):
            raise RuntimeError("out of memory")

    root = tmp_path / "demo"
    root.mkdir()
    (root / "deck.pptx").write_bytes((DEMO / "deck.pptx").read_bytes())
    report = folder.index(str(root), CONFIG, engines=[], describer=Broken(""))
    assert any("could not be described" in note and note.startswith("1 ") for note in report.notes)
    assert not folder.index(str(root), CONFIG, engines=[], describer=Says("A small picture here.")).notes


def test_the_describer_is_not_part_of_the_settings_of_formats_without_figures():
    settings = folder._settings(CONFIG, [], None, Says("x"))
    for fmt in ("pdf", "csv", "json", "xml"):
        assert folder._hash(settings, fmt) == folder._hash({**settings, "describe": None}, fmt)
    for fmt in ("docx", "pptx", "xlsx", "html", "md"):
        assert folder._hash(settings, fmt) != folder._hash({**settings, "describe": None}, fmt)


def test_startup_only_checks_that_the_files_exist(fake_stack, tmp_path):
    for name in FILES:
        (tmp_path / name).write_text("x")  # not the real files: a checksum would refuse them
    reader = SmolVlm(model_dir=str(tmp_path))
    reader.check()
    assert fake_stack == []  # nothing verified or loaded
    with pytest.raises(EngineUnavailable, match="--describe-download"):
        SmolVlm(model_dir=str(tmp_path / "empty")).check()


# --- metadata of a document (semantic.describe) ---------------------------------------------

import json  # noqa: E402

from ragnaroq.anchors import PdfAnchor  # noqa: E402
from ragnaroq.model import Document, Engine, Line, Page, Source  # noqa: E402
from ragnaroq.readers import ReaderRefused, read_bytes  # noqa: E402
from ragnaroq.readers._build import start as empty_start  # noqa: E402
from ragnaroq.semantic.describe import describe as describe_document  # noqa: E402
from ragnaroq.semantic.describe import mentions  # noqa: E402
from ragnaroq.semantic.model import DocumentMetadata  # noqa: E402
from ragnaroq.semantic.paragraphs import paragraphs  # noqa: E402
from ragnaroq.semantic.rules import kind_of  # noqa: E402
from ragnaroq.semantic.shape import shape  # noqa: E402

GENERATED = Path(__file__).parent.parent / "references" / "chunking" / "generated"
GENERATED_NAMES = sorted(p.name for p in GENERATED.iterdir())


def read_file(folder, name):
    return read_bytes((folder / name).read_bytes(), name, Config(), engines=[], base=folder).document


def md_document(text, status="accepted"):
    lines = []
    for i, block in enumerate(text.split("\n\n")):
        heading = block.startswith("# ")
        block = block[2:] if heading else block
        lines.append(
            Line(
                id=f"l{i}",
                page=1,
                order=i,
                raw=block,
                text=block,
                source="native",
                paragraph=i,
                role="heading" if heading else "body",
                level=1 if heading else 0,
                status=status,
                anchor=PdfAnchor(page=1, box=(0, 10 * i, 100, 10 * i + 9)),
            )
        )
    return Document(
        source=Source(sha256="0", bytes=1, filename="a.pdf", pages=1, format="pdf"),
        engine=Engine(name="t", version="0", policy_version="0.6", config_sha256="0", components={}),
        created_at="2026-01-01T00:00:00Z",
        pages=[Page(number=1, kind="native", lines=lines)],
    )


def empty_document(name):
    return empty_start(b"", name, "csv", Config(), {})


def kind_of_file(name):
    document = read_file(GENERATED, name)
    return kind_of(shape(paragraphs(document), document), document).value


def every_file():
    for where in (GENERATED, DEMO):
        for path in sorted(where.iterdir()):
            if path.suffix not in (".png", ".jsonl", ".toml"):
                yield where, path.name


def grounded_values(meta):
    yield from (meta.dates + meta.periods + meta.amounts + meta.references + meta.files)
    yield from meta.structure.definitions


def squash(text):
    return " ".join(text.split())


FLOORS = {"dates": 7, "periods": 1, "amounts": 6, "references": 5, "definitions": 6}


def check_grounding(document, value):
    """The cited lines are kept lines, hold the value's text, and each one contributes to it."""
    by_id = {line.id: line for line in document.lines()}
    cited = [by_id[i] for i in value.line_ids]
    assert cited and not any(line.excluded for line in cited), value
    held = squash(" ".join(line.text for line in cited))
    assert squash(value.text) in held, (value, held)
    for k in range(len(cited)):
        rest = squash(" ".join(line.text for j, line in enumerate(cited) if j != k))
        assert squash(value.text) not in rest, ("a cited line adds nothing", value, cited[k].id)
    assert value.cite
    assert value.status == min((line.status for line in cited), key=("blocked", "flagged", "accepted").index)


def test_every_value_names_lines_whose_text_holds_it():
    found = dict.fromkeys(FLOORS, 0)
    for base, name in every_file():
        try:
            document = read_file(base, name)
        except ReaderRefused:
            assert name == "bomb.xml", name  # the one file a reader must refuse
            continue
        meta = describe_document(document)
        if not meta.title.line_ids:
            assert meta.title.text == document.source.filename
        else:
            check_grounding(document, meta.title)
        for field in FLOORS:
            values = meta.structure.definitions if field == "definitions" else getattr(meta, field)
            for value in values:
                check_grounding(document, value)
            found[field] += len(values)
        for value in mentions(document):
            check_grounding(document, value)
    assert all(found[field] >= floor for field, floor in FLOORS.items()), found


def lines_document(texts, statuses=None):
    """One PDF paragraph (one `paragraph` number) over several lines."""
    document = md_document("\n\n".join(texts))
    for i, line in enumerate(document.pages[0].lines):
        line.paragraph = 1
        line.status = (statuses or ["accepted"] * len(texts))[i]
    return document


def test_a_value_split_across_two_lines_cites_exactly_those_lines():
    document = lines_document(["The board met on 5 January", "2026 and agreed.", "Nothing else here."])
    meta = describe_document(document)
    assert [(d.value, d.line_ids) for d in meta.dates] == [("2026-01-05", ["l0", "l1"])]
    check_grounding(document, meta.dates[0])


def test_a_value_split_across_lines_takes_the_lowest_status_of_them():
    document = lines_document(["The board met on 5 January", "2026 and agreed."], ["accepted", "flagged"])
    (date,) = describe_document(document).dates
    assert (date.line_ids, date.status) == (["l0", "l1"], "flagged")
    document = lines_document(
        ["A line.", "Met on 5 January 2026 here.", "More."], ["flagged", "accepted", "blocked"]
    )
    (date,) = describe_document(document).dates
    assert (date.line_ids, date.status) == (["l1"], "accepted")


def test_a_value_on_an_excluded_line_is_not_taken():
    document = lines_document(["Kept line.", "Met on 5 January 2026."])
    document.pages[0].lines[1].excluded = "script:Deva"
    assert describe_document(document).dates == []


def test_a_date_on_a_flagged_line_is_flagged():
    meta = describe_document(md_document("# Notice\n\nIssued on 5 January 2026."))
    assert [d.status for d in meta.dates] == ["accepted"]
    meta = describe_document(md_document("# Notice\n\nIssued on 5 January 2026.", status="flagged"))
    assert [(d.value, d.status) for d in meta.dates] == [("2026-01-05", "flagged")]
    assert meta.trust.flagged == 1.0


def test_a_list_over_1000_values_is_cut_and_flagged():
    meta = describe_document(md_document("\n\n".join(f"On 1 January {2000 + i % 20}." for i in range(1500))))
    assert len(meta.dates) == 1000 and [f.rule for f in meta.flags] == ["metadata.list-limit"]
    assert "dates" in meta.flags[0].message


def test_an_empty_document_has_a_valid_entry_titled_by_its_file_name():
    meta = describe_document(empty_document("empty.csv"))
    assert meta.title.text == "empty.csv" and meta.kind.value == "spreadsheet" and meta.length.chars == 0
    assert meta.length.parts == 1 and meta.path == "empty.csv" and meta.format == "csv"
    assert meta.trust.accepted == meta.trust.flagged == meta.trust.blocked == 0
    DocumentMetadata.model_validate(meta.model_dump())


def test_the_title_is_the_first_heading_else_the_first_line_else_the_file_name():
    assert describe_document(md_document("# Big Notice\n\nBody text.")).title.text == "Big Notice"
    first = describe_document(md_document("Just a line.\n\nMore."), path="x/a.pdf")
    assert first.title.text == "Just a line." and first.title.line_ids == ["l0"] and first.path == "x/a.pdf"


def test_values_in_table_cells_are_grounded_to_the_cell_line():
    document = read_bytes(
        b"# T\n\n| Item | Due |\n|---|---|\n| Fee | 5 January 2026 |\n| Tax | Rs. 1,500 |\n",
        "a.md",
        Config(),
        engines=[],
    ).document
    meta = describe_document(document)
    cell_ids = {line.id for line in document.lines() if line.role == "table"}
    assert [d.value for d in meta.dates] == ["2026-01-05"]
    assert set(meta.dates[0].line_ids) <= cell_ids and set(meta.amounts[0].line_ids) <= cell_ids
    assert meta.amounts[0].value == {"amount": 1500, "currency": "INR"}
    assert meta.structure.tables == 1 and 0 < meta.structure.table_share < 1


def test_a_table_definition_is_grounded_to_its_row():
    document = read_bytes(
        b"# T\n\n| Term | Definition |\n|---|---|\n| Fee | The money. |\n| Day | 24 hours. |\n",
        "a.md",
        Config(),
        engines=[],
    ).document
    definitions = describe_document(document).structure.definitions
    assert [d.term for d in definitions] == ["Fee", "Day"]
    by_id = {line.id: line.text for line in document.lines()}
    assert [[by_id[i] for i in d.line_ids] for d in definitions] == [
        ["Fee", "The money."],
        ["Day", "24 hours."],
    ]


def test_scripts_use_each_lines_own_list_over_one_denominator():
    document = md_document("Hello world.\n\nनमस्ते दुनिया\n\nabc")
    lines = document.pages[0].lines
    lines[0].scripts = ["Latn"]
    lines[1].scripts = ["Deva"]
    lines[1].excluded = "script:Deva"
    lines[2].scripts = ["Grek"]  # the text alone would say Latin: only the line's list counts
    meta = describe_document(document)
    assert {s.script: s.excluded for s in meta.scripts} == {"Latn": False, "Deva": True, "Grek": False}
    assert "Latn" in {s.script for s in meta.scripts}
    shares = {s.script: s.share for s in meta.scripts}
    assert round(sum(shares.values()), 3) == 1.0
    assert shares["Grek"] == 0.0 and shares["Latn"] > 0 and shares["Deva"] > 0
    assert meta.length.chars == len("Hello world.") + len("abc")


def test_kinds_of_the_generated_documents():
    assert {name: kind_of_file(name) for name in GENERATED_NAMES} == {
        "notice.pdf": "numbered",
        "faq.docx": "faq",
        "manual.md": "sectioned",
        "manual.html": "sectioned",
        "contract.docx": "numbered",
        "deck.pptx": "slides",
        "rates.xlsx": "spreadsheet",
        "records.json": "data",
        "table.json": "data",
        "essay.md": "prose",
    }


def test_the_kind_says_which_counts_decided_it():
    kind = describe_document(read_file(GENERATED, "notice.pdf")).kind
    assert kind.value == "numbered" and any(b.startswith("numbered_paragraphs = ") for b in kind.because)


def test_the_metadata_schema_file_is_in_sync():
    schema = Path(__file__).parents[1] / "schemas" / "metadata.schema.json"
    assert json.loads(schema.read_text()) == DocumentMetadata.model_json_schema()


def test_schema_metadata_prints_the_schema(capsys):
    assert main(["schema", "--metadata"]) == 0
    assert json.loads(capsys.readouterr().out) == DocumentMetadata.model_json_schema()
