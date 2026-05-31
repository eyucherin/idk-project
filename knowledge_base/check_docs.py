from chunk_and_embed import connect, COLLECTION
from pymilvus import Collection

connect()
col = Collection(COLLECTION)
col.load()

results = col.query(expr="id >= 0", output_fields=["source"], limit=16384)
sources = sorted(set(r["source"] for r in results))
print(f"Total chunks: {len(results)}")
print(f"\nDocuments ({len(sources)}):")
for s in sources:
    count = sum(1 for r in results if r["source"] == s)
    print(f"  {s}  ({count} chunks)")
