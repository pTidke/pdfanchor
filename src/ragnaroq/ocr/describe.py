"""SmolVLM-500M as the writer of a short description of a picture. Optional:
`pip install ragnaroq[describe]`.

A small vision-language model looks at one image and says one sentence about it. The sentence is
a label, never a reading: it is kept only when `accept` finds every number, date, percent and
currency sign in it in the image's own OCR text, and it never changes a status. The model runs
through Hugging Face transformers, offline, and is held to the same rules as the third reader:
the files are pinned to one revision and their SHA-256 sums are checked before anything is
loaded; no code from the model repository is ever fetched or run (`trust_remote_code=False`,
and `FILES` holds no `.py` file). The files are fetched only when `download=True`.

Core code reaches this module for `accept`, which needs neither torch nor transformers.
"""

import hashlib
import importlib.metadata
import os
import re
import time
import unicodedata
from pathlib import Path

from ..crosscheck import critical_tokens
from . import EngineUnavailable

REPO = "HuggingFaceTB/SmolVLM-500M-Instruct"
REVISION = "753c1752602b5e739af7a7fa6947a635051897a3"
PROMPT = (
    "Describe this image in one short factual sentence, for search. "
    "Only mention text and numbers that are visible."
)
# Names how a picture is described: the prompt and greedy decoding. Change it when either
# changes, so a stored description is not reused for a different recipe.
RECIPE = "describe-greedy-1"
SECONDS = 60.0  # per image
MAX_NEW_TOKENS = 60
NEEDS = "The image describer needs torch and transformers: pip install 'ragnaroq[describe]'."
FILES: dict[str, str] = {
    "added_tokens.json": "74135b8664b56088c0006f1c8e848d79a8eba003411f72ebf1dc2ee96227be3a",
    "chat_template.json": "12462caec000542ca1bb73cd0ba25aaf320d90335fc46731fa6a8355ea34bd26",
    "config.json": "daacbbca6af3c34e50466c72aed2df4553a08084f6a0551d4ae420a241cb66c6",
    "generation_config.json": "067a2a54e5f87162ecac6e0e911cc4665fc8f7f3324794ecbac0f76badb56636",
    "merges.txt": "0b54e8aa4e53d5383e2e4bc635a56b43f9647f7b13832d5d9ecd8f82dac4f510",
    "model.safetensors": "d05b567eeaf534e83d375551f068ed57b5f52d37c657197f644af5ef9db091a2",
    "preprocessor_config.json": "6cb6e36d6fcb88ca1502c4a26750715dc3e7dedddc9a8f17b27d8d167d1457e7",
    "processor_config.json": "e7bff42da73ae9eec9042ef20e066e11f1ee20f025358ff79131e3c0fb549b46",
    "special_tokens_map.json": "aa0ff906077086dfa9734a7f97f68c825877a48f9468807be65504495cdeef09",
    "tokenizer.json": "5ece781dc8d2b2f3e2f289ca0ae50b17cfc27dd27bfe7971bb8241e0b964331a",
    "tokenizer_config.json": "8a68fb58021bc3df79d70f3d5fa79d2e709083872be1f6965cc9bcd12f3ba66a",
    "vocab.json": "82b84012e3add4d01d12ba14442026e49b8cbbaead1f79ecf3d919784f82dc79",
}

# --------------------------------------------------------------------------------- the filter

_END = re.compile(r"""[.!?]["”’')\]]*(?=\s|$)""")


def _squash(text: str) -> str:
    """Letters, digits, currency signs and percent signs only, case-folded: every separator
    (spaces, commas, points, slashes, dashes, brackets) is gone."""
    folded = unicodedata.normalize("NFKC", text).casefold()
    return "".join(ch for ch in folded if ch.isalnum() or ch == "%" or unicodedata.category(ch) == "Sc")


def first_sentence(raw: str) -> str:
    """The text up to the first sentence end, or "" when it is empty or has no clean end (a
    model stopped at its token limit does not end)."""
    text = " ".join(raw.split())
    found = _END.search(text)
    sentence = text[: found.end()] if found else ""
    return sentence if any(ch.isalpha() for ch in sentence) else ""


def accept(raw: str, ocr_text: str) -> str | None:
    """The first sentence of what the model said, if it can stand beside the image's OCR text;
    otherwise None. Every critical token (a number, date or percent, as
    `crosscheck.critical_tokens` gives them) and every currency sign in the sentence must be in
    the OCR text, separators ignored. With no OCR text, only a sentence with none is kept."""
    sentence = first_sentence(raw)
    if not sentence:
        return None
    seen = _squash(ocr_text)
    needed = {_squash(token) for token in critical_tokens(sentence) if any(c.isdigit() for c in token)}
    signs = {ch for ch in _squash(sentence) if unicodedata.category(ch) == "Sc"}
    if any(sign not in seen for sign in signs):
        return None
    for token in needed:
        # Not inside a longer number: "6" is not in "16".
        if not re.search(rf"(?<!\d){re.escape(token)}(?!\d)", seen):
            return None
    return sentence


# ------------------------------------------------------------------------------- the reader


def default_folder() -> Path:
    """Where huggingface_hub keeps the pinned revision: `HF_HUB_CACHE`, else
    `HUGGINGFACE_HUB_CACHE`, else `$HF_HOME/hub`, with `HF_HOME` defaulting to
    `$XDG_CACHE_HOME/huggingface` and that to `~/.cache/huggingface`."""
    hub = os.environ.get("HF_HUB_CACHE") or os.environ.get("HUGGINGFACE_HUB_CACHE")
    if not hub:
        cache = os.environ.get("XDG_CACHE_HOME") or os.path.join(os.path.expanduser("~"), ".cache")
        hub = os.path.join(os.environ.get("HF_HOME") or os.path.join(cache, "huggingface"), "hub")
    root = Path(os.path.expandvars(os.path.expanduser(hub)))
    return root / f"models--{REPO.replace('/', '--')}" / "snapshots" / REVISION


def verify(folder: Path) -> None:
    """Raise EngineUnavailable naming the files that are missing, or the first file whose
    SHA-256 differs from `FILES`."""
    missing = [name for name in FILES if not (folder / name).is_file()]
    if missing:
        raise EngineUnavailable(f"SmolVLM files are missing from {folder} ({', '.join(missing)}).")
    for name, expected in FILES.items():
        digest = hashlib.sha256()
        try:
            with (folder / name).open("rb") as handle:
                while block := handle.read(1 << 20):
                    digest.update(block)
        except OSError as error:
            raise EngineUnavailable(
                f"{name} in {folder} could not be read ({type(error).__name__}: {error})."
            ) from error
        if digest.hexdigest() != expected:
            raise EngineUnavailable(
                f"{name} in {folder} is not the file of {REPO}@{REVISION[:12]} (its SHA-256 differs)."
            )


def _package_version(name: str) -> str | None:
    try:
        return importlib.metadata.version(name)
    except importlib.metadata.PackageNotFoundError:
        return None


class SmolVlm:
    name = "smolvlm"

    def __init__(self, model_dir: str | None = None, download: bool = False):
        self.model_dir, self.download = model_dir, download
        self._loaded: tuple | None = None  # processor, model, torch
        self._version: str | None = None

    def _folder(self) -> Path:
        return Path(self.model_dir) if self.model_dir else default_folder()

    @staticmethod
    def _torch():
        try:
            import torch
        except ImportError as error:
            raise EngineUnavailable(NEEDS) from error
        return torch

    def version(self) -> str:
        """The model, its revision, the library versions and the recipe. It is part of what the
        index remembers, so an upgrade of any of them describes the pictures again. Worked out
        once, without loading the model."""
        if self._version is None:
            torch = self._torch()
            build = getattr(torch, "__version__", None) or _package_version("torch") or "unknown"
            transformers = _package_version("transformers")
            if transformers is None:
                raise EngineUnavailable(NEEDS)
            self._version = (
                f"{REPO}@{REVISION[:12]} float32 torch={build} transformers={transformers} recipe={RECIPE}"
            )
        return self._version

    def prepare(self) -> None:
        if self._loaded:
            return
        folder = self._folder()
        missing = [name for name in FILES if not (folder / name).is_file()]
        if missing and not self.download:
            raise EngineUnavailable(
                f"SmolVLM files are missing from {folder} ({', '.join(missing)}). Pass "
                f"--describe-download to fetch {REPO} at revision {REVISION[:12]} (1.0 GB)."
            )
        torch = self._torch()
        try:
            import transformers
        except ImportError as error:
            raise EngineUnavailable(NEEDS) from error
        if missing:
            os.environ["HF_HUB_DISABLE_XET"] = "1"  # Xet transfers hang behind a proxy
            try:
                from huggingface_hub import snapshot_download

                # Checked and loaded where the files landed, whatever the cache settings.
                folder = Path(
                    snapshot_download(
                        REPO,
                        revision=REVISION,
                        allow_patterns=list(FILES),
                        **({"local_dir": self.model_dir} if self.model_dir else {}),
                    )
                )
            except Exception as error:
                raise EngineUnavailable(
                    f"Could not download {REPO}@{REVISION[:12]} ({type(error).__name__}: {error})."
                ) from error
        verify(folder)
        # Only the checked files: nothing fetched, no code run, no pickled weights.
        offline = {"trust_remote_code": False, "local_files_only": True, "use_safetensors": True}
        try:
            processor = transformers.AutoProcessor.from_pretrained(folder, **offline)
            model = transformers.AutoModelForImageTextToText.from_pretrained(
                folder, dtype=torch.float32, **offline
            )
        except Exception as error:
            raise EngineUnavailable(
                f"SmolVLM could not be loaded ({type(error).__name__}: {error})."
            ) from error
        self._loaded = (processor, model.eval(), torch)

    def describe(self, image, seconds: float = SECONDS) -> str:
        """What the model says about `image`, unfiltered; "" when it was stopped at `seconds`."""
        self.prepare()
        assert self._loaded
        processor, model, torch = self._loaded
        from transformers import MaxTimeCriteria, StoppingCriteriaList

        messages = [{"role": "user", "content": [{"type": "image"}, {"type": "text", "text": PROMPT}]}]
        prompt = processor.apply_chat_template(messages, add_generation_prompt=True)
        inputs = processor(text=prompt, images=[image.convert("RGB")], return_tensors="pt").to(model.device)
        started = time.monotonic()
        with torch.inference_mode():
            out = model.generate(
                **inputs,
                do_sample=False,
                max_new_tokens=MAX_NEW_TOKENS,
                stopping_criteria=StoppingCriteriaList([MaxTimeCriteria(seconds)]),
            )
        if time.monotonic() - started >= seconds:
            return ""
        new = out[:, inputs["input_ids"].shape[-1] :]
        return processor.batch_decode(new, skip_special_tokens=True)[0].strip()
