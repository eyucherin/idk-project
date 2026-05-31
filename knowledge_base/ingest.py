"""
Milvus ingestion script: orchestrates PDF/HTML → Milvus end-to-end.
"""

import argparse
import sys
import unicodedata
from pathlib import Path

from pymilvus import Collection, utility
from sentence_transformers import SentenceTransformer

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(Path(__file__).parent))
from chunk_and_embed import BATCH_SIZE, COLLECTION, MODEL_NAME, chunk_text, connect, create_collection, embed, load_document


def get_existing_chunk_ids(collection: Collection, source: str) -> set[str]:
    """Return chunk_ids already in Milvus for a given source file."""
    results = collection.query(
        expr=f'source == "{source}"',
        output_fields=["chunk_id"],
        limit=16384,
    )
    return {r["chunk_id"] for r in results}


def insert_chunks(collection: Collection, chunks: list[dict]) -> None:
    collection.insert([
        [c["embedding"] for c in chunks],
        [c["text"] for c in chunks],
        [c["source"] for c in chunks],
        [c["source_url"] for c in chunks],
        [c.get("page", 0) for c in chunks],
        [c["chunk_id"] for c in chunks],
    ])
    collection.flush()
    collection.load()


def ingest_document(
    doc_path: Path,
    source_url: str,
    model: SentenceTransformer,
    collection: Collection,
) -> tuple[int, int]:
    name = unicodedata.normalize("NFC", doc_path.name)
    stem = unicodedata.normalize("NFC", doc_path.stem)
    existing = get_existing_chunk_ids(collection, name)

    sections = load_document(doc_path)
    all_chunks = []
    for section in sections:
        for j, text in enumerate(chunk_text(section["text"])):
            chunk_id = f"{stem}_p{section['page']}_c{j}"
            if chunk_id in existing:
                continue
            all_chunks.append({
                "text": text,
                "source": name,
                "source_url": source_url,
                "page": section["page"],
                "chunk_id": chunk_id,
            })

    if not all_chunks:
        return 0, len(existing)

    for i in range(0, len(all_chunks), BATCH_SIZE):
        batch = all_chunks[i : i + BATCH_SIZE]
        embeddings = embed([c["text"] for c in batch], model)
        for chunk, emb in zip(batch, embeddings):
            chunk["embedding"] = emb
        insert_chunks(collection, batch)

    return len(all_chunks), len(existing)


def main():
    parser = argparse.ArgumentParser(description="Ingest PDFs into Milvus")
    parser.add_argument(
        "--docs-dir",
        default=str(PROJECT_ROOT / "data" / "raw"),
        help="Directory containing PDFs (default: data/raw)",
    )
    parser.add_argument("--source-url", default="", help="Base URL for source documents")
    parser.add_argument(
        "--drop",
        action="store_true",
        help="Drop and recreate the Milvus collection before ingesting (full replacement)",
    )
    args = parser.parse_args()

    connect()

    if args.drop and utility.has_collection(COLLECTION):
        utility.drop_collection(COLLECTION)
        print(f"Dropped existing collection '{COLLECTION}'.")

    collection = create_collection()
    model = SentenceTransformer(MODEL_NAME)

    docs_dir = Path(args.docs_dir)
    if not docs_dir.is_absolute():
        docs_dir = PROJECT_ROOT / docs_dir

    docs = sorted(
        p for p in docs_dir.iterdir()
        if p.suffix.lower() in (".pdf", ".html", ".htm")
    )

    if not docs:
        print(f"No PDF or HTML files found in {docs_dir}/")
        return

    total_inserted = 0
    total_skipped = 0
    for doc in docs:
        print(f"Ingesting {doc.name} ...")
        inserted, skipped = ingest_document(doc, args.source_url or doc.name, model, collection)
        print(f"  → {inserted} chunks inserted, {skipped} skipped (already exist)")
        total_inserted += inserted
        total_skipped += skipped

    print(f"\nDone. Total inserted: {total_inserted}, skipped: {total_skipped}")


if __name__ == "__main__":
    main()
