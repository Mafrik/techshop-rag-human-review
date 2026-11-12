"""Live synthetic acceptance for local Supabase retrieval and audit writes."""

import json
import os
from datetime import datetime, timezone
from pathlib import Path

import requests

BASE = os.environ.get("M5_API_BASE", "http://127.0.0.1:8000").rstrip("/")
REST = os.environ.get("M5_SUPABASE_REST_URL", "http://127.0.0.1:8011/rest/v1").rstrip("/")
KEY = os.environ["M5_SUPABASE_SERVICE_ROLE_KEY"]
HEADERS = {"apikey": KEY, "Authorization": f"Bearer {KEY}", "Content-Type": "application/json"}

cases = [
    ("returns", "Can I return my laptop within 14 days?", "returns", {"TS-FAQ-RETURNS-001"}),
    ("warranty", "My new headphones have one dead earbud; what is the warranty process?", "warranty", {"TS-FAQ-WARRANTY-001", "TS-FAQ-WARRANTY-HEADPHONES-002"}),
    ("payment", "Can I split payment into instalments with N26?", "payment", {"TS-FAQ-PAYMENT-N26-001", "TS-FAQ-PAYMENT-EASYCREDIT-002"}),
    ("complaint_wrong_item", "The wrong item arrived. How do I report it?", "complaint", {"TS-FAQ-COMPLAINT-WRONG-ITEM-001"}),
    ("complaint_damage", "The shipment box is damaged and the appliance is broken. What do I do?", "complaint", {"TS-FAQ-COMPLAINT-DAMAGE-002"}),
    ("complaint_ambiguous", "I have a complaint about my order. What is the complaint process?", "complaint", set()),
    ("unsupported_order_api", "Where is synthetic order SYNTH-001?", "order_status", set()),
]
results = []
for label, question, intent, expected_any in cases:
    r = requests.post(f"{BASE}/retrieve", json={"question": question, "intent": intent, "top_k": 5}, timeout=60)
    r.raise_for_status()
    d = r.json()
    got_intents = {h["intent"] for h in d["hits"]}
    evidence = set(d["evidence_ids"])
    route_ok = d["route"] == ("answer_with_citation" if expected_any else "escalate")
    intent_ok = got_intents <= {intent}
    source_ok = bool(evidence & expected_any) if expected_any else not evidence
    results.append({"case": label, "passed": route_ok and intent_ok and source_ok,
                    "route": d["route"], "evidence_ids": sorted(evidence),
                    "returned_intents": sorted(got_intents)})

draft = requests.post(f"{BASE}/audit", json={
    "channel": "synthetic_acceptance", "input_redacted": "SYNTH-M5-RAG-AUDIT-DRAFT",
    "intent_predicted": "returns", "evidence_ids": ["TS-FAQ-RETURNS-001"],
    "output_draft": "Synthetic draft; human review required.",
    "validation": {"citation_present": True, "test_case": "synthetic"}, "outcome": "draft",
}, timeout=20)
draft.raise_for_status()
draft_receipt = draft.json()
esc = requests.post(f"{BASE}/audit", json={
    "channel": "synthetic_acceptance", "input_redacted": "SYNTH-M5-RAG-AUDIT-ESCALATION",
    "intent_predicted": "order_status", "evidence_ids": [], "output_draft": None,
    "validation": {"reason": "no_order_api", "test_case": "synthetic"}, "outcome": "escalated",
    "reason_codes": ["no_order_api", "human_review_required"], "priority": "normal",
}, timeout=20)
esc.raise_for_status()
escalation_receipt = esc.json()

read_draft = requests.get(f"{REST}/operation_log?id=eq.{draft_receipt['operation_id']}&select=id,outcome,evidence_ids", headers=HEADERS, timeout=15)
read_draft.raise_for_status()
read_escalation = requests.get(f"{REST}/escalations?operation_id=eq.{escalation_receipt['operation_id']}&select=id,operation_id,status,reason_codes", headers=HEADERS, timeout=15)
read_escalation.raise_for_status()
db_draft = read_draft.json()
db_escalation = read_escalation.json()
results.append({"case": "audit_draft_persisted", "passed": len(db_draft) == 1 and db_draft[0]["outcome"] == "draft",
                "operation_id": draft_receipt["operation_id"]})
results.append({"case": "audit_escalation_persisted", "passed": len(db_escalation) == 1 and db_escalation[0]["status"] == "open",
                "operation_id": escalation_receipt["operation_id"], "escalation_id": escalation_receipt.get("escalation_id")})

report = {"timestamp_utc": datetime.now(timezone.utc).isoformat(), "backend": "Supabase Postgres + pgvector",
          "corpus_chunks": 12, "embedding_model": "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2",
          "synthetic_cases": results, "passed": sum(x["passed"] for x in results), "total": len(results),
          "external_side_effects": False, "production_eligible": False}
Path("/tmp/M5_Supabase_Live_Acceptance_20261112.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
print(json.dumps(report, indent=2))
if report["passed"] != report["total"]:
    raise SystemExit(1)



