# TechShop support assistant — grounded RAG + human review

A local prototype for customer-support requests. It combines an n8n workflow, FastAPI retrieval/audit endpoints, Supabase Postgres with pgvector, and a fail-closed human-review route.

> **Prototype, not production software.** All acceptance runs used synthetic requests in a local environment. The repository does not include customer data, credentials, the source FAQ text, an inbox connection, or an order API.

## What it demonstrates

- Intent-aware retrieval over page-cited FAQ chunks with Supabase pgvector.
- Evidence checks that route supported requests to a **draft for operator review**.
- Unsupported or ambiguous requests route to escalation; no customer action is sent automatically.
- PII-aware input handling and append-only operation/escalation logging.
- An importable n8n workflow export, FastAPI service, database schema, corpus-seeding utility, and acceptance materials.

## Verified prototype evidence

The recorded local acceptance package reports:

| Check | Result |
|---|---:|
| Supabase pgvector retrieval and safety boundaries | 9/9 synthetic checks passed |
| n8n integrated synthetic requests | 6/6 cases completed |
| n8n node executions | 50/50 completed |
| Persisted local audit receipts | 6/6 cases |
| External customer actions | 0 |

Four requests with eligible source evidence produced drafts requiring operator review; two unsupported requests escalated. These figures describe this small synthetic acceptance set, not a benchmark of model answer quality.

## Architecture

```text
Synthetic request
  → n8n intent route and redaction
  → FastAPI embedding service
  → Supabase Postgres / pgvector retrieval
  → evidence and policy-eligibility gate
  → draft for human review OR escalation
  → append-only operation log
```

### Key files

- [`n8n/techshop-support-workflow.json`](n8n/techshop-support-workflow.json) — inactive n8n candidate export; credentials are not included.
- [`service/app.py`](service/app.py) — `/health`, `/retrieve`, and `/audit` API routes.
- [`db/schema.sql`](db/schema.sql) — pgvector knowledge, operation log, escalation, evaluation, and PII registry tables.
- [`service/seed_corpus.py`](service/seed_corpus.py) — generates idempotent SQL from a locally supplied approved corpus.
- [`eval/`](eval/) — synthetic cases and redacted acceptance reports.

## Local setup

Requirements: Docker, a dedicated local Supabase/Postgres instance with pgvector, Python 3.11 for corpus seeding, and an n8n instance for workflow import.

1. Apply [`db/schema.sql`](db/schema.sql) to a disposable local Supabase project.
2. Prepare an **approved** local JSON corpus for your own demo data; do not commit source documents or customer records. Each record must include a `source_id`, `text`, `intent`, `page`, and `policy_eligible` field. Set `M5_CORPUS_PATH` to that file and run `python service/seed_corpus.py`; review the generated SQL before applying it.
3. Build the API image: `docker build -t techshop-rag ./service`.
4. Run it on localhost with `M5_SUPABASE_REST_URL` and `M5_SUPABASE_SERVICE_ROLE_KEY` set in your local environment. **Never commit a service-role key.**
5. Import the n8n JSON into a development workspace, configure your own credentials, and keep the workflow inactive until its endpoints and data boundaries have been reviewed.
6. Run acceptance only against a disposable local database seeded with compatible synthetic records. The included historical evidence is not a substitute for your own run.

The API image requires a `service/requirements.txt`; it is included in this repository. The local Supabase stack itself is intentionally not bundled.

## Safety and known limits

- No live customer inbox, order lookup API, or automatic send/action is connected.
- Source FAQ material and the course brief are not redistributed here; use an approved corpus you have rights to use.
- Generated answers still need human review. The small acceptance suite does not establish broad answer accuracy or production readiness.
- The service uses privileged Supabase credentials for writes. Keep it private to localhost in this prototype; add authentication, row-level access controls, request limits, monitoring, and a security/privacy review before any deployment.

## Provenance

Prepared as an AI-assisted portfolio prototype from a fictionalized support scenario. Public repository contents are limited to original workflow/service code, schema, synthetic cases, and redacted aggregate evidence.
