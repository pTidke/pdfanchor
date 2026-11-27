# Loading chunks into a vector database

`ragnaroq index ./folder` writes `.ragnaroq/chunks.jsonl`, with every chunk of the folder. `ragnaroq convert` writes one for a single PDF. Either file is built to load as it stands. Every line is one chunk:

```json
{"id": "6f1c…", "text": "…", "embedding_text": "…", "metadata": {…}, "anchors": […]}
```

- `id` is a UUID, stable for the same file content and the same lines. Upsert by it.
- `embedding_text` is what to embed. `text` is what to show. `text` is the file's own text, raw: escape it before you render it as Markdown or HTML.
- `metadata` is flat: every value is a string, integer, float or boolean, never null, and every chunk has the same keys. It loads into stores that reject nested or null metadata.
- `anchors` is the full list of places the chunk came from. `metadata["anchors_json"]` holds the same list as a string. It is cut to 20,000 bytes if needed, by dropping whole anchors from the end, so it is always valid JSON; a cut chunk carries the rule `chunk.anchors-truncated` in `metadata["rules"]`.
- `metadata["cite"]` is a citation you can show as it is: `native.pdf, p. 1`, `rates.xlsx, Rates!A1:D12`, `guide.docx, ¶18–¶24`.
- A table chunk of a file that is not a PDF holds a group of rows that fits `chunk_chars` (1,200 characters by default), with the table's header row repeated above it, so a long CSV or sheet never makes one record too large for Pinecone's 40 KB metadata limit. Only a single row longer than `chunk_chars` is a larger chunk. A PDF table is one chunk.

| Key | Meaning |
| --- | --- |
| `schema_version`, `file_type` | Output version; `pdf`, `xlsx`, `docx` … |
| `file_id`, `path`, `file_sha256` | The file: a UUID made from its path, its path, its content hash |
| `kind` | `text`, `table` or `image` |
| `status`, `rules`, `sources` | `accepted`, `flagged` or `blocked`; the rules that fired and the readers used, comma-separated. Filter on `status` |
| `cite` | Short citation |
| `part`, `part_index` | The page, sheet or slide: its name (`p. 3` for a PDF) and its number |
| `page_first`, `page_last` | PDF page numbers; `0` for other files |
| `heading`, `title` | The heading the chunk sits under, and the document title; `""` when there is none |
| `chars` | Length of `text` |
| `anchors_json` | The anchors as JSON text |
| `image_path` | The saved image on an `image` chunk, else `""` |

## Keeping a database in step

A second `ragnaroq index` writes `changes.json`, which says what differs from the last run:

```json
{
  "added": ["<file id>"],
  "changed": ["<file id>"],
  "removed": ["<file id>"],
  "chunks_added": ["<chunk id>"],
  "chunks_changed": ["<chunk id>"],
  "chunks_removed": ["<chunk id>"],
  "reset": false
}
```

- **Upsert** `chunks_added` and `chunks_changed`. A changed chunk keeps its id: its file's content or verdict changed.
- **Delete** `chunks_removed`.
- **On `reset`, drop every chunk whose id is not in `chunks.jsonl`.** `reset` is `true` when there was no usable last run to compare with (the first run, or a manifest that is missing or cannot be read), so `changes.json` cannot list what to delete.
- Every chunk also carries `file_id`, so a store can delete a file's chunks by filter.

Ids are stable: the same file content and lines give the same ids, so loading the same run twice does not duplicate anything. The snippets below upsert every chunk, which is always safe; to send only what changed, filter `chunks` on `chunks_added` and `chunks_changed`.

To embed, use any model. The snippets below use `embed(texts)` for yours, and read a folder index at `.ragnaroq/`.

```python
import json
from pathlib import Path

index = Path(".ragnaroq")
chunks = [json.loads(line) for line in (index / "chunks.jsonl").read_text().splitlines()]
changes = json.loads((index / "changes.json").read_text())
removed = changes["chunks_removed"]  # ids that are no longer in chunks.jsonl
vectors = embed([chunk["embedding_text"] for chunk in chunks])
```

## Qdrant

```python
from qdrant_client import QdrantClient
from qdrant_client.models import Distance, PointStruct, PointIdsList, VectorParams

client = QdrantClient(":memory:")  # or QdrantClient(url=...)
if not client.collection_exists("chunks"):
    client.create_collection("chunks", vectors_config=VectorParams(size=len(vectors[0]), distance=Distance.COSINE))
client.delete("chunks", points_selector=PointIdsList(points=removed))
client.upsert("chunks", [
    PointStruct(id=c["id"], vector=v, payload={"text": c["text"], **c["metadata"]})
    for c, v in zip(chunks, vectors)
])
```

## Chroma

```python
import chromadb

collection = chromadb.PersistentClient("chroma").get_or_create_collection("chunks")
if removed:
    collection.delete(ids=removed)
collection.upsert(
    ids=[c["id"] for c in chunks],
    documents=[c["text"] for c in chunks],
    embeddings=vectors,
    metadatas=[c["metadata"] for c in chunks],
)
```

## Pinecone

```python
from pinecone import Pinecone

index = Pinecone(api_key="…").Index("chunks")
if removed:
    index.delete(ids=removed)
index.upsert(vectors=[
    {"id": c["id"], "values": v, "metadata": {"text": c["text"], **c["metadata"]}}
    for c, v in zip(chunks, vectors)
])
```

Pinecone limits a record's metadata to about 40 KB, including `text` if you store it there. `anchors_json` is capped at 20,000 bytes, so it leaves room; keep the rest under the limit too.

## pgvector

```python
import psycopg
from pgvector.psycopg import register_vector

with psycopg.connect("dbname=chunks") as db:
    register_vector(db)
    db.execute(
        "CREATE TABLE IF NOT EXISTS chunks (id uuid PRIMARY KEY, text text, embedding vector(%s), metadata jsonb)"
        % len(vectors[0])
    )
    db.execute("DELETE FROM chunks WHERE id = ANY(%s::uuid[])", (removed,))
    for c, v in zip(chunks, vectors):
        db.execute(
            "INSERT INTO chunks VALUES (%s, %s, %s, %s) ON CONFLICT (id) DO UPDATE "
            "SET text = EXCLUDED.text, embedding = EXCLUDED.embedding, metadata = EXCLUDED.metadata",
            (c["id"], c["text"], v, json.dumps(c["metadata"])),
        )
```

Weaviate, LanceDB and Milvus take the same fields: use `id` as the object id, `embedding_text` for the vector and `metadata` as properties.
