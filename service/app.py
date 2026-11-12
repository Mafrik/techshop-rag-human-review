"""M5 local RAG service backed by Supabase Postgres + pgvector."""

import os
import re
import time
from typing import Any

import requests
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field
from fastembed import TextEmbedding

REST_URL = os.environ.get("M5_SUPABASE_REST_URL", "http://host.docker.internal:8011/rest/v1").rstrip("/")
API_KEY = os.environ.get("SERVICE_ROLE_KEY") or os.environ.get("M5_SUPABASE_SERVICE_ROLE_KEY", "")
MODEL = os.environ.get("M5_EMBEDDING_MODEL", "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2")
PROMPT_VERSION = "m5-supabase-rag-local-v1"

app = FastAPI(title="M5 TechShop Supabase RAG", version="1.0.0")
embedder: TextEmbedding | None = None
http = requests.Session()


class RetrieveRequest(BaseModel):
    question: str = Field(min_length=1, max_length=4000)
    intent: str | None = Field(default=None, max_length=40)
    top_k: int = Field(default=3, ge=1, le=5)


class AuditRequest(BaseModel):
    channel: str = Field(default="n8n", max_length=40)
    input_redacted: str = Field(min_length=1, max_length=4000)
    intent_predicted: str = Field(min_length=1, max_length=40)
    evidence_ids: list[str] = Field(default_factory=list, max_length=10)
    output_draft: str | None = Field(default=None, max_length=8000)
    validation: dict[str, Any] = Field(default_factory=dict)
    outcome: str = Field(pattern="^(draft|escalated|error)$")
    reason_codes: list[str] = Field(default_factory=list, max_length=20)
    priority: str = Field(default="normal", pattern="^(normal|high|urgent)$")


def headers() -> dict[str, str]:
    if not API_KEY:
        raise HTTPException(status_code=503, detail="Supabase service key is not configured")
    return {"apikey": API_KEY, "Authorization": f"Bearer {API_KEY}", "Content-Type": "application/json"}


@app.on_event("startup")
def initialize() -> None:
    global embedder
    embedder = TextEmbedding(model_name=MODEL)


@app.get("/health")
def health() -> dict[str, Any]:
    try:
        r = http.get(f"{REST_URL}/knowledge_chunks?select=source_id&limit=1", headers=headers(), timeout=8)
        r.raise_for_status()
        return {"status": "ok", "backend": "Supabase Postgres + pgvector", "embedding_model": MODEL,
                "source_chunks_available": len(r.json()), "production_eligible": False,
                "side_effects_allowed": False}
    except (requests.RequestException, ValueError) as exc:
        raise HTTPException(status_code=503, detail=f"Supabase health check failed: {type(exc).__name__}") from exc


@app.post("/retrieve")
def retrieve(req: RetrieveRequest) -> dict[str, Any]:
    if not embedder:
        raise HTTPException(status_code=503, detail="Embedding model is initializing")
    intent = (req.intent or "other").strip().lower()
    allowed = {"order_status", "returns", "warranty", "payment", "complaint", "other"}
    if intent not in allowed:
        intent = "other"
    vector = next(embedder.query_embed(req.question.strip())).tolist()
    try:
        r = http.post(f"{REST_URL}/rpc/match_knowledge_chunks", headers=headers(),
                      json={"query_embedding": "[" + ",".join(f"{x:.8f}" for x in vector) + "]",
                            "intent_filter": intent, "match_count": req.top_k}, timeout=20)
        r.raise_for_status()
        rows = r.json()
    except (requests.RequestException, ValueError) as exc:
        raise HTTPException(status_code=502, detail=f"Supabase retrieval failed: {type(exc).__name__}") from exc
    if intent == "complaint":
        wrong_item = bool(re.search(r"wrong item|incorrect item|different product|не тот товар|неправильн\w* товар|ошибочн\w* товар", req.question, re.I))
        damage = bool(re.search(r"damage|damaged|broken|cracked|царап|поврежд|разбит", req.question, re.I))
        if wrong_item and not damage:
            rows = [row for row in rows if row.get("source_id") == "TS-FAQ-COMPLAINT-WRONG-ITEM-001"]
        elif damage and not wrong_item:
            rows = [row for row in rows if row.get("source_id") == "TS-FAQ-COMPLAINT-DAMAGE-002"]
        else:
            rows = []  # Ambiguous complaint: fail closed instead of mixing policies.
    hits = [{"source_id": row.get("source_id"), "source_type": row.get("source_type"),
             "page": row.get("page"), "intent": row.get("intent"), "text": row.get("text"),
             "score": round(float(row.get("score") or 0), 4),
             "policy_eligible": row.get("policy_eligible") is True,
             "fixture_only": row.get("fixture_only") is True} for row in rows]
    eligible = [h for h in hits if h["policy_eligible"] and not h["fixture_only"]]
    return {"question": req.question.strip(), "intent_filter": intent, "hits": hits,
            "evidence_ids": [h["source_id"] for h in eligible if h["source_id"]],
            "evidence_sufficient": bool(eligible),
            "route": "answer_with_citation" if eligible else "escalate",
            "retrieval_method": "FastEmbed multilingual MiniLM + Supabase pgvector cosine search",
            "backend": "Supabase Postgres + pgvector", "production_eligible": False,
            "side_effects_allowed": False}


@app.post("/audit")
def append_audit(req: AuditRequest) -> dict[str, Any]:
    started = time.monotonic()
    payload = {"channel": req.channel, "input_redacted": req.input_redacted,
               "pii_types": [], "intent_predicted": req.intent_predicted,
               "agent_name": "techshop-rag", "evidence_ids": req.evidence_ids,
               "output_draft": req.output_draft, "validation": req.validation,
               "outcome": req.outcome, "latency_ms": 0, "prompt_version": PROMPT_VERSION,
               "model_id": MODEL}
    try:
        r = http.post(f"{REST_URL}/operation_log", headers={**headers(), "Prefer": "return=representation"},
                      json=payload, timeout=10)
        r.raise_for_status()
        row = r.json()[0]
        result = {"operation_id": row["id"], "outcome": row["outcome"], "audit_stored": True}
        if req.outcome == "escalated":
            escalation = {"operation_id": row["id"], "reason_codes": req.reason_codes or ["needs_human_review"],
                          "priority": req.priority, "redacted_summary": req.input_redacted[:1000], "status": "open"}
            er = http.post(f"{REST_URL}/escalations", headers={**headers(), "Prefer": "return=representation"},
                           json=escalation, timeout=10)
            er.raise_for_status()
            result["escalation_id"] = er.json()[0]["id"]
        result["latency_ms"] = round((time.monotonic() - started) * 1000)
        return result
    except (requests.RequestException, ValueError, IndexError, KeyError) as exc:
        raise HTTPException(status_code=502, detail=f"Supabase audit write failed: {type(exc).__name__}") from exc
