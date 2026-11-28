"""Load a document's chunks into real vector stores. Needs qdrant-client and chromadb; not run in CI."""

import pytest

from ragnaroq import Config, Policy, convert
from ragnaroq.export.chunks import LIST_KEYS, to_chunks
from ragnaroq.semantic.describe import describe
from ragnaroq.semantic.overrides import Override
from ragnaroq.semantic.rules import choose

TRUSTING = Config(policy=Policy(accept_native_without_crosscheck=True))


@pytest.fixture
def chunks(native_pdf):
    document = convert(native_pdf, config=TRUSTING, engines=[])
    # the rules cut this short document as one chunk; cut by sentences, it gives a text and a table chunk
    choice = choose(describe(document), TRUSTING, Override("*", "sentences"))
    chunks = to_chunks(document, TRUSTING, choice=choice)
    assert len(chunks) > 1
    return chunks


def vector(text):
    """A tiny stand-in embedding; the stores need a vector, not a good one."""
    return [float(len(text) % 7), float(len(text) % 11), 1.0]


def test_qdrant_loads_the_chunks_and_an_upsert_replaces_them(chunks):
    qdrant = pytest.importorskip("qdrant_client")
    from qdrant_client.models import (
        Distance,
        FieldCondition,
        Filter,
        MatchAny,
        PointStruct,
        Range,
        VectorParams,
    )

    client = qdrant.QdrantClient(":memory:")
    client.create_collection("chunks", vectors_config=VectorParams(size=3, distance=Distance.COSINE))
    points = [
        PointStruct(
            id=c["id"], vector=vector(c["embedding_text"]), payload={"text": c["text"], **c["metadata"]}
        )
        for c in chunks
    ]
    client.upsert("chunks", points)
    client.upsert("chunks", points)
    assert client.count("chunks").count == len(chunks)
    stored = client.retrieve("chunks", [chunks[0]["id"]])[0].payload
    assert stored["cite"] == chunks[0]["metadata"]["cite"] and stored["text"] == chunks[0]["text"]
    assert all(stored[key] == chunks[0]["metadata"][key] for key in LIST_KEYS)

    # the list keys filter as lists, and the date bounds as a range
    dated = [c["id"] for c in chunks if "2026-04-01" in c["metadata"]["dates"]]
    assert dated
    found, _ = client.scroll(
        "chunks", scroll_filter=Filter(must=[FieldCondition(key="dates", match=MatchAny(any=["2026-04-01"]))])
    )
    assert sorted(p.id for p in found) == sorted(dated)
    window = Filter(
        must=[
            FieldCondition(key="date_first", range=Range(lte=20260401)),
            FieldCondition(key="date_last", range=Range(gte=20260401)),
        ]
    )
    found, _ = client.scroll("chunks", scroll_filter=window)
    assert sorted(p.id for p in found) == sorted(dated)


def test_chroma_loads_the_chunks_and_an_upsert_replaces_them(chunks):
    chromadb = pytest.importorskip("chromadb")
    client = chromadb.EphemeralClient()
    collection = client.get_or_create_collection("chunks")
    arguments = {
        "ids": [c["id"] for c in chunks],
        "documents": [c["text"] for c in chunks],
        "embeddings": [vector(c["embedding_text"]) for c in chunks],
        "metadatas": [c["metadata"] for c in chunks],
    }
    collection.upsert(**arguments)
    collection.upsert(**arguments)
    assert collection.count() == len(chunks)
    found = collection.get(ids=[chunks[0]["id"]])
    assert found["metadatas"][0]["cite"] == chunks[0]["metadata"]["cite"]
