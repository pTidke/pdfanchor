"""PaddleOCR-VL-1.6 as the third reader of a disputed scanned line. Optional:
`pip install ragnaroq[vlm]`.

A small vision-language model reads the crop of one line. It runs through Hugging Face
transformers on the CPU or a GPU, offline. The files are pinned to one revision and
their SHA-256 sums are checked before anything is loaded; no code from the model
repository is ever fetched or run (`trust_remote_code=False`, and `FILES` holds no
`.py` file). The files are fetched only when `download=True`.
"""

import hashlib
import importlib.metadata
import os
import re
import time
from pathlib import Path

from . import EngineUnavailable, LineUnreadable

REPO = "PaddlePaddle/PaddleOCR-VL-1.6"
REVISION = "132c45f52c0a26ed5a6b2f6bd8e3985794cc27dd"
PROMPT = "OCR:"
# Names how a crop is read: the prompt and greedy decoding. Change it when either changes,
# so stored readings are not reused for a different recipe.
RECIPE = "ocr-greedy-1"
NEEDS = "The PaddleOCR-VL reader needs torch and transformers: pip install 'ragnaroq[vlm]'."
FILES: dict[str, str] = {
    "added_tokens.json": "f59f889088e0fe21c523e7cf121bb6dca3b0bb148cb7159fbb4572c74dfc5644",
    "chat_template.jinja": "2f27812dab7f333e471884e0c803d807f11953d5453140dfb1aaba234f872bc8",
    "config.json": "ce7f4565f8b1db78532ad5d1b9ebe55c2139d49bd4cb04778b580a08a598f171",
    "generation_config.json": "a6701d78ab3b4d972307cdec3b69d4c13f46e0d5140514f50ab7d84259324b94",
    "model.safetensors": "85a479d506a11e724e7285d395c551be69f41dbc16b6342d3cacfb189aed71db",
    "preprocessor_config.json": "111872ab1e8bb7fd040ac5087bfced7ab8f011f02139b088cba294964c3b1d0e",
    "processor_config.json": "1568858960a9760c54431dae693a6152e601ff55cdf6d2eab97a4a99958faea0",
    "special_tokens_map.json": "d3a125c03103deb2acaf7730791bdbbf196f620e5a2213b664511ff9b4b25bab",
    "tokenizer.json": "c8a215a59183d0d0781adc33bacd3ce6162716f7fd568fb30234a74d69803a7d",
    "tokenizer.model": "34ef7db83df785924fb83d7b887b6e822a031c56e15cff40aaf9b982988180df",
    "tokenizer_config.json": "1f979337347cc0cb72a6282d8a23ed183539aa81a87a906f022aee2bab83c7c5",
}


def number_format(device: str, cpu_flags: frozenset[str], cuda_bf16: bool = False) -> str:
    """bfloat16 where the hardware does it natively, else float32 (half precision on a
    CPU without bf16 support is slow and less exact)."""
    if device == "cuda":
        return "bfloat16" if cuda_bf16 else "float32"
    return "bfloat16" if cpu_flags & {"avx512_bf16", "amx_bf16"} else "float32"


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
        raise EngineUnavailable(f"PaddleOCR-VL files are missing from {folder} ({', '.join(missing)}).")
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


def check_finished(new_tokens: int, max_new_tokens: int, elapsed: float, seconds: float) -> None:
    """A line the model had to be stopped on is not a reading: raise LineUnreadable."""
    if elapsed >= seconds:
        raise LineUnreadable(f"the model did not finish the line within {seconds:g} seconds")
    if new_tokens >= max_new_tokens:
        raise LineUnreadable(f"the model did not finish the line within {max_new_tokens} tokens")


def _package_version(name: str) -> str | None:
    try:
        return importlib.metadata.version(name)
    except importlib.metadata.PackageNotFoundError:
        return None


def _cpu_flags() -> frozenset[str]:
    try:
        text = Path("/proc/cpuinfo").read_text()
    except OSError:
        return frozenset()
    for line in text.splitlines():
        if line.startswith("flags"):
            return frozenset(line.partition(":")[2].split())
    return frozenset()


class PaddleOcrVl:
    name = "paddleocr-vl"

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

    def _hardware(self, torch) -> tuple[str, str]:
        device = "cuda" if torch.cuda.is_available() else "cpu"
        cuda_bf16 = device == "cuda" and bool(torch.cuda.is_bf16_supported())
        return device, number_format(device, _cpu_flags(), cuda_bf16)

    def version(self) -> str:
        """The model, its revision, number format and device, the library versions and the
        reading recipe. It keys the cache, so an upgrade of any of them reads crops again.
        Worked out once, without loading the model."""
        if self._version is None:
            device, kind = self._hardware(self._torch())
            transformers = _package_version("transformers")
            if transformers is None:
                raise EngineUnavailable(NEEDS)
            vision = _package_version("torchvision")
            self._version = (
                f"{REPO}@{REVISION[:12]} {kind} {device} torch={_package_version('torch') or 'unknown'} "
                f"transformers={transformers} {f'torchvision={vision}' if vision else 'no torchvision'} "
                f"recipe={RECIPE}"
            )
        return self._version

    def prepare(self) -> None:
        if self._loaded:
            return
        folder = self._folder()
        missing = [name for name in FILES if not (folder / name).is_file()]
        if missing and not self.download:
            raise EngineUnavailable(
                f"PaddleOCR-VL files are missing from {folder} ({', '.join(missing)}). Pass "
                f"--vlm-download to fetch {REPO} at revision {REVISION[:12]} (1.9 GB)."
            )
        torch = self._torch()
        try:
            import transformers
        except ImportError as error:
            raise EngineUnavailable(NEEDS) from error
        if missing:
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
                    f"Could not download {REPO}@{REVISION[:12]} ({type(error).__name__})."
                ) from error
        verify(folder)
        device, kind = self._hardware(torch)
        try:
            processor = transformers.AutoProcessor.from_pretrained(folder, trust_remote_code=False)
            model = transformers.AutoModelForImageTextToText.from_pretrained(
                folder, dtype=getattr(torch, kind), trust_remote_code=False
            )
        except Exception as error:
            raise EngineUnavailable(f"PaddleOCR-VL could not be loaded ({type(error).__name__}).") from error
        self._loaded = (processor, model.eval().to(device), torch)

    def read_line(self, image, max_new_tokens: int, seconds: float) -> str:
        self.prepare()
        assert self._loaded
        processor, model, torch = self._loaded
        messages = [
            {
                "role": "user",
                "content": [{"type": "image", "image": image}, {"type": "text", "text": PROMPT}],
            }
        ]
        inputs = processor.apply_chat_template(
            messages, add_generation_prompt=True, tokenize=True, return_dict=True, return_tensors="pt"
        ).to(model.device)
        started = time.monotonic()
        with torch.inference_mode():
            out = model.generate(**inputs, max_new_tokens=max_new_tokens, do_sample=False, max_time=seconds)
        elapsed = time.monotonic() - started
        new = out[0][inputs["input_ids"].shape[-1] :]
        check_finished(len(new), max_new_tokens, elapsed, seconds)
        return re.sub(r"\s+", " ", processor.decode(new, skip_special_tokens=True)).strip()
