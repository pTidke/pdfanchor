"""Load a document's chunks into real vector stores. Needs qdrant-client and chromadb; not run in CI."""

import pytest

from ragnaroq import Config, Policy, convert
from ragnaroq.export.chunks import to_chunks

TRUSTING = Config(policy=Policy(accept_native_without_crosscheck=True))


@pytest.fixture
def chunks(native_pdf):
    chunks = to_chunks(convert(native_pdf, config=TRUSTING, engines=[]), TRUSTING)
    assert len(chunks) > 1
    return chunks


def vector(text):
    """A tiny stand-in embedding; the stores need a vector, not a good one."""
    return [float(len(text) % 7), float(len(text) % 11), 1.0]


def test_qdrant_loads_the_chunks_and_an_upsert_replaces_them(chunks):
    qdrant = pytest.importorskip("qdrant_client")
    from qdrant_client.models import Distance, PointStruct, VectorParams

    client = qdrant.QdrantClient(":memory:")
    client.create_collection("chunks", vectors_config=VectorParams(size=3, distance=Distance.COSINE))
    points = [
        PointStruct(id=c["id"], vector=vector(c["embedding_text"]), 
            payload={"text": c["text"], **c["metadata"]})
        for c in chunks
    ]
    client.upsert("chunks", points)
    client.upsert("chunks", points)
    assert client.count("chunks").count == len(chunks)
    stored = client.retrieve("chunks", [chunks[0]["id"]])[0].payload
    assert stored["cite"] == chunks[0]["metadata"]["cite"] and stored["text"] == chunks[0]["text"]


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
