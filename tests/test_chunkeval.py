from pathlib import Path

from ragnaroq import Config
from ragnaroq.chunkeval.baseline import baseline_chunks
from ragnaroq.export.chunks import to_jsonl
from ragnaroq.readers import read_bytes

ROOT = Path(__file__).parent.parent
DEMO = ROOT / "references" / "demo"
GOLDEN = Path(__file__).parent / "golden" / "baseline-0.3"


def test_the_baseline_is_0_3s_chunker_byte_for_byte():
    goldens = sorted(GOLDEN.glob("*.jsonl"))
    assert goldens
    for golden in goldens:
        name = golden.name[: -len(".jsonl")]
        document = read_bytes((DEMO / name).read_bytes(), name, Config(), base=DEMO).document
        assert to_jsonl(baseline_chunks(document, Config())) == golden.read_text(encoding="utf8")
