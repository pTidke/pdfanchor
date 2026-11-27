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
from collections.abc import Sequence
from pathlib import Path

from ..crosscheck import NEGATIONS
from . import EngineUnavailable, disable_xet

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
_ABBREVIATION = re.compile(
    r"(?:^|[^\w.])(?:fig|no|dr|mr|mrs|ms|rs|st|sr|jr|co|inc|ltd|vs|etc|e\.g|i\.e|a\.m|p\.m)\.$", re.IGNORECASE
)
MIN_WORDS = 3
_NUMBER = re.compile(r"(?<![\d.,/\-])(\d+(?:,\d+|,\s\d{3}(?!\d)|[./\-]\d+)*)(\s?%)?")
_WORD = re.compile(r"[^\W\d_]+")
_MONTHS = ("january february march april may june july august september october november december").split()
_MONTH = {name: name for name in _MONTHS} | {name[:3]: name for name in _MONTHS} | {"sept": "september"}
# One entry per currency: its sign and the words that name it. Either one in the picture will do.
_CURRENCIES = (
    ("₹", {"rupee", "rupees", "inr", "rs"}),
    ("$", {"dollar", "dollars", "usd"}),
    ("€", {"euro", "euros", "eur"}),
    ("¥", {"yen", "jpy"}),
    ("£", {"pound", "pounds", "gbp"}),
)
_SCALES = {"thousand", "lakh", "lakhs", "crore", "crores", "million", "millions", "billion", "billions"}


def _fold(text: str) -> str:
    return unicodedata.normalize("NFKC", text).casefold()


def _ascii_digits(text: str) -> str:
    return "".join(str(unicodedata.digit(ch)) if ch.isdigit() else ch for ch in text)


def _numbers(text: str) -> set[str]:
    """The numbers in `text` as tokens: the commas and spaces inside a number removed, the
    point, "/" and "-" kept, a percent sign kept. "1.5" is not "15" and "12" is not "1.2"."""
    found = set()
    for match in _NUMBER.finditer(_ascii_digits(_fold(text))):
        number = re.sub(r"[,\s]", "", match[1])
        found.add(number + "%" if match[2] else number)
    return found


def _ocr_numbers(lines: Sequence[str]) -> set[str]:
    """The numbers of each OCR line on its own: lines are never joined, so "Line 1" and
    "2 apples" do not make 12. A number with a percent sign also stands as itself."""
    found: set[str] = set()
    for line in lines:
        for token in _numbers(line):
            found.add(token)
            found.add(token.rstrip("%"))
    return found


def first_sentence(raw: str) -> str:
    """The text up to the first sentence end, or "" when it is empty or has no clean end (a
    model stopped at its token limit does not end). A point after an abbreviation ("Fig.",
    "No.", "Rs.", "e.g.") is not an end."""
    text = " ".join(raw.split())
    for found in _END.finditer(text):
        if text[found.start()] == "." and _ABBREVIATION.search(text[: found.start() + 1]):
            continue
        sentence = text[: found.end()]
        return sentence if any(ch.isalpha() for ch in sentence) else ""
    return ""


def _groups(words: set[str], signs: set[str]) -> set[str]:
    """The currencies a text names, by sign or by word."""
    return {sign for sign, names in _CURRENCIES if sign in signs or names & words}


def accept(raw: str, ocr: "Sequence[str] | str") -> str | None:
    """The first sentence of what the model said, if it can stand beside the image's OCR text
    (one string per OCR line); otherwise None.

    A sentence is dropped when it has fewer than `MIN_WORDS` words, or when any of these is not
    in the OCR text: a number, date or percent (matched as a token, one OCR line at a time); a
    currency sign, or the currency a word such as "rupees" names; a month name; a scale word
    (lakh, crore, million ...); a negation. With no OCR text, only a sentence with none of
    them is kept. Structural claims ("31 rows", "two columns") cannot be checked."""
    sentence = first_sentence(raw)
    if len(sentence.split()) < MIN_WORDS:
        return None
    lines = ocr.splitlines() if isinstance(ocr, str) else list(ocr)
    seen_text = _fold(" ".join(lines))
    seen_words = set(_WORD.findall(seen_text))
    seen_signs = {ch for ch in seen_text if unicodedata.category(ch) == "Sc"}
    said = _fold(sentence)
    words = set(_WORD.findall(said))
    signs = {ch for ch in said if unicodedata.category(ch) == "Sc"}
    if not _numbers(sentence) <= _ocr_numbers(lines):
        return None
    if not NEGATIONS & words <= seen_words:
        return None
    months = {_MONTH[word] for word in words if word in _MONTH and word != "may"}
    if re.search(r"\bmay\s+\d|\d\s+may\b", said):
        months.add("may")
    if not months <= {_MONTH[word] for word in seen_words if word in _MONTH}:
        return None
    wanted = _groups(words, signs)
    if not wanted <= _groups(seen_words, seen_signs):
        return None
    known = {sign for sign, _ in _CURRENCIES}
    if not (signs - known) <= seen_signs:  # a sign no word names must be there itself
        return None
    scales = {word.rstrip("s") for word in words & _SCALES}
    if not scales <= {word.rstrip("s") for word in seen_words & _SCALES}:
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
        self._found: Path | None = None  # where check() found the files
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

    def _missing(self, folder: Path) -> list[str]:
        """The files not in `folder`; without `download`, any missing file is an error."""
        missing = [name for name in FILES if not (folder / name).is_file()]
        if missing and not self.download:
            raise EngineUnavailable(
                f"SmolVLM files are missing from {folder} ({', '.join(missing)}). Pass "
                f"--describe-download to fetch {REPO} at revision {REVISION[:12]} (1.0 GB)."
            )
        return missing

    def _locate(self) -> Path:
        """The folder holding the files: `model_dir` or the cache, after a download when it is
        asked for and files are missing. Nothing is checked or loaded."""
        folder = self._folder()
        if not self._missing(folder):
            return folder
        disable_xet()  # Xet transfers hang behind a proxy
        try:
            from huggingface_hub import snapshot_download

            # Checked and loaded where the files landed, whatever the cache settings.
            return Path(
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

    def check(self) -> None:
        """What a run asks for at its start: the libraries are installed and the files are
        there (fetched first, with `download=True`). Their checksums and the loading wait for
        the first image, so a folder with no images pays nothing."""
        self._missing(self._folder())  # the files first: that is what a person can fix
        self._torch()
        try:
            import transformers  # noqa: F401
        except ImportError as error:
            raise EngineUnavailable(NEEDS) from error
        self._found = self._locate()

    def prepare(self) -> None:
        if self._loaded:
            return
        self._missing(self._folder())
        torch = self._torch()
        try:
            import transformers
        except ImportError as error:
            raise EngineUnavailable(NEEDS) from error
        folder = self._found or self._locate()
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
