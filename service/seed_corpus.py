"""Generate idempotent SQL inserts for the local Supabase pgvector demo."""

import json
import os
from pathlib import Path

from fastembed import TextEmbedding

CORPUS = Path(os.getenv("M5_CORPUS_PATH", "/app/m5_corpus.json"))
OUTPUT = Path(os.getenv("M5_SQL_OUTPUT", "/tmp/m5_supabase_seed.sql"))
MODEL = os.getenv(
    "M5_EMBEDDING_MODEL",
    "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2",
)


def quote(value: str | None) -> str:
    if value is None:
        return "NULL"
    return "'" + str(value).replace("'", "''") + "'"


def main() -> None:
    rows = json.loads(CORPUS.read_text(encoding="utf-8"))
    if not rows:
        raise SystemExit("Refusing to seed an empty corpus")
    embeddings = list(TextEmbedding(model_name=MODEL).embed([row["text"] for row in rows]))
    if len(embeddings) != len(rows) or any(len(vector) != 384 for vector in embeddings):
        raise SystemExit("Embedding count or dimension does not match schema")

    sql = ["BEGIN;"]
    for row, vector in zip(rows, embeddings):
        intent = row.get("intent") or "other"
        policy = row.get("policy_eligible") is True
        fixture = row.get("source_type") == "synthetic_fixture"
        vector_text = "[" + ",".join(f"{float(x):.8f}" for x in vector) + "]"
        cols = (
            "source_id, source_type, source_title, source_url, page, intent, "
            "approved, policy_eligible, fixture_only, chunk_text, embedding"
        )
        vals = ", ".join(
            [
                quote(row["source_id"]),
                quote(row.get("source_type", "unknown")),
                quote(row.get("source_name", row["source_id"])),
                quote(row.get("source_url")),
                str(int(row["page"])) if row.get("page") is not None else "NULL",
                quote(intent),
                "TRUE" if policy else "FALSE",
                "TRUE" if policy else "FALSE",
                "TRUE" if fixture else "FALSE",
                quote(row["text"]),
                quote(vector_text) + "::extensions.vector",
            ]
        )
        sql.append(
            f"INSERT INTO public.knowledge_chunks ({cols}) VALUES ({vals}) "
            "ON CONFLICT (source_id) DO UPDATE SET "
            "source_type=EXCLUDED.source_type, source_title=EXCLUDED.source_title, "
            "source_url=EXCLUDED.source_url, page=EXCLUDED.page, intent=EXCLUDED.intent, "
            "approved=EXCLUDED.approved, policy_eligible=EXCLUDED.policy_eligible, "
            "fixture_only=EXCLUDED.fixture_only, chunk_text=EXCLUDED.chunk_text, "
            "embedding=EXCLUDED.embedding;"
        )
    sql.extend(["COMMIT;", "SELECT count(*) AS seeded_chunks FROM public.knowledge_chunks;"])
    OUTPUT.write_text("\n".join(sql) + "\n", encoding="utf-8")
    print(f"generated {len(rows)} idempotent chunk upserts; vector_dimension=384")


if __name__ == "__main__":
    main()
