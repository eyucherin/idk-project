"""
Preprocessing pipeline: PDF/HTML → chunks → embeddings

Chunking strategy: sliding window over per-page/per-section text
  chunk_size=1000 chars, overlap=200 chars
  Rationale: keeps chunks small enough for precise retrieval while
  overlap preserves context across boundaries. Page-level splitting
  (PDF) and heading-level splitting (HTML) ensure metadata stays accurate.

Embedding model: intfloat/multilingual-e5-large (DIM=1024)
  Indexed and queried WITHOUT e5 prefixes so that wxO's plain query
  embeddings match what is stored in Milvus.
"""

import os
from pathlib import Path

import fitz  # pymupdf
from bs4 import BeautifulSoup
from dotenv import load_dotenv
from pymilvus import Collection, CollectionSchema, DataType, FieldSchema, connections, utility
from sentence_transformers import SentenceTransformer

load_dotenv()

COLLECTION = os.environ.get("MILVUS_COLLECTION", "scenario_chunks")
DIM = 1024
MODEL_NAME = "intfloat/multilingual-e5-large"
CHUNK_SIZE = 1000
CHUNK_OVERLAP = 200
BATCH_SIZE = 32


def connect() -> None:
    connections.connect(
        alias="default",
        host=os.environ["MILVUS_HOST"],
        port=os.environ.get("MILVUS_PORT", "31140"),
        user=os.environ["MILVUS_USER"],
        password=os.environ["MILVUS_PASSWORD"],
        secure=True,
        db_name=os.environ["MILVUS_DATABASE"],
    )


def create_collection() -> Collection:
    fields = [
        FieldSchema(name="id", dtype=DataType.INT64, is_primary=True, auto_id=True),
        FieldSchema(name="embedding", dtype=DataType.FLOAT_VECTOR, dim=DIM),
        FieldSchema(name="text", dtype=DataType.VARCHAR, max_length=8192),
        FieldSchema(name="source", dtype=DataType.VARCHAR, max_length=512),
        FieldSchema(name="source_url", dtype=DataType.VARCHAR, max_length=1024),
        FieldSchema(name="page", dtype=DataType.INT64),
        FieldSchema(name="chunk_id", dtype=DataType.VARCHAR, max_length=256),
    ]
    schema = CollectionSchema(fields=fields, description="Scenario RAG chunks")

    if not utility.has_collection(COLLECTION):
        collection = Collection(COLLECTION, schema=schema)
        collection.create_index(
            field_name="embedding",
            index_params={
                "index_type": "HNSW",
                "metric_type": "COSINE",
                "params": {"M": 16, "efConstruction": 200},
            },
        )
    else:
        collection = Collection(COLLECTION)

    collection.load()
    return collection


def load_pdf(path: Path) -> list[dict]:
    doc = fitz.open(str(path))
    pages = []
    for i, page in enumerate(doc):
        text = page.get_text().strip()
        if text:
            pages.append({"text": text, "page": i + 1})
    return pages


def load_html(path: Path) -> list[dict]:
    """Parse an HTML file into sections using heading tags as page boundaries."""
    soup = BeautifulSoup(
        path.read_text(encoding="utf-8", errors="replace"), "html.parser"
    )
    for tag in soup(["script", "style", "nav", "header", "footer", "noscript"]):
        tag.decompose()

    sections = []
    heading = ""
    body_lines: list[str] = []

    for el in soup.find_all(["h1", "h2", "h3", "p", "li", "td", "th"]):
        if el.name in ("h1", "h2", "h3"):
            if body_lines:
                sections.append((heading + "\n" + "\n".join(body_lines)).strip())
            heading = el.get_text(separator=" ", strip=True)
            body_lines = []
        else:
            t = el.get_text(separator=" ", strip=True)
            if t:
                body_lines.append(t)

    if body_lines:
        sections.append((heading + "\n" + "\n".join(body_lines)).strip())

    # Fallback: no headings found — treat entire body as one section
    if not sections:
        sections = [soup.get_text(separator="\n", strip=True)]

    return [
        {"text": s, "page": i + 1}
        for i, s in enumerate(sections)
        if s.strip()
    ]


def load_document(path: Path) -> list[dict]:
    """Dispatch to the correct loader based on file extension."""
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        return load_pdf(path)
    if suffix in (".html", ".htm"):
        return load_html(path)
    raise ValueError(f"Unsupported file type: {path.suffix}")


def chunk_text(text: str) -> list[str]:
    chunks = []
    start = 0
    while start < len(text):
        chunks.append(text[start : start + CHUNK_SIZE])
        start += CHUNK_SIZE - CHUNK_OVERLAP
    return chunks


def embed(texts: list[str], model: SentenceTransformer) -> list[list[float]]:
    return model.encode(texts, normalize_embeddings=True).tolist()


def search(collection: Collection, query_vector: list[float], limit: int = 5) -> list[dict]:
    collection.load()
    results = collection.search(
        data=[query_vector],
        anns_field="embedding",
        param={"metric_type": "COSINE", "params": {"ef": 64}},
        limit=limit,
        output_fields=["text", "source", "source_url", "page", "chunk_id"],
    )[0]
    return [
        {
            "score": hit.distance,
            "text": hit.entity.get("text"),
            "source": hit.entity.get("source"),
            "source_url": hit.entity.get("source_url"),
            "page": hit.entity.get("page"),
            "chunk_id": hit.entity.get("chunk_id"),
        }
        for hit in results
    ]


if __name__ == "__main__":
    connect()
    collection = Collection(COLLECTION)
    model = SentenceTransformer(MODEL_NAME)

    query = input("Search query: ")
    query_vector = model.encode(query, normalize_embeddings=True).tolist()

    results = search(collection, query_vector)
    if not results:
        print("No results found.")
    for i, r in enumerate(results, 1):
        print(f"\n[{i}] score={r['score']:.4f} | {r['source']} p.{r['page']} | {r['chunk_id']}")
        print(f"    {r['text'][:300]}")
