-- Local Supabase runtime schema for the TechShop RAG demo.
-- Embeddings use the local multilingual MiniLM model (384 dimensions).
-- Do not expose service_role credentials or this local database publicly.

create extension if not exists vector with schema extensions;

create table if not exists public.knowledge_chunks (
  id uuid primary key default gen_random_uuid(),
  source_id text not null unique,
  source_type text not null,
  source_title text not null,
  source_url text,
  page integer,
  intent text not null,
  approved boolean not null default false,
  policy_eligible boolean not null default false,
  fixture_only boolean not null default false,
  chunk_text text not null,
  embedding extensions.vector(384) not null,
  created_at timestamptz not null default now()
);

create index if not exists knowledge_chunks_embedding_idx
  on public.knowledge_chunks using hnsw (embedding extensions.vector_cosine_ops);
create index if not exists knowledge_chunks_intent_idx
  on public.knowledge_chunks(intent, approved, policy_eligible);

create table if not exists public.operation_log (
  id uuid primary key default gen_random_uuid(),
  created_at timestamptz not null default now(),
  channel text not null,
  input_redacted text not null,
  pii_types text[] not null default '{}',
  intent_predicted text not null,
  router_confidence numeric(4,3),
  agent_name text,
  evidence_ids text[] not null default '{}',
  output_draft text,
  validation jsonb not null default '{}',
  outcome text not null check (outcome in ('draft', 'escalated', 'error')),
  latency_ms integer,
  prompt_version text not null,
  model_id text,
  estimated_input_tokens integer,
  estimated_output_tokens integer,
  estimated_cost_eur numeric(12,6)
);

create table if not exists public.escalations (
  id uuid primary key default gen_random_uuid(),
  operation_id uuid not null references public.operation_log(id) on delete cascade,
  created_at timestamptz not null default now(),
  reason_codes text[] not null,
  priority text not null check (priority in ('normal', 'high', 'urgent')),
  redacted_summary text not null,
  status text not null default 'open' check (status in ('open', 'assigned', 'resolved')),
  assigned_to text,
  intent_actual text,
  operator_feedback text,
  resolved_at timestamptz
);

create table if not exists public.eval_log (
  id uuid primary key default gen_random_uuid(),
  operation_id uuid not null references public.operation_log(id) on delete cascade,
  evaluated_at timestamptz not null default now(),
  intent_predicted text not null,
  intent_actual text,
  accuracy smallint check (accuracy between 0 and 10),
  completeness smallint check (completeness between 0 and 10),
  tone smallint check (tone between 0 and 10),
  issues text[],
  judge_model text,
  judge_prompt_version text not null
);

create table if not exists public.pii_registry (
  id uuid primary key default gen_random_uuid(),
  operation_id uuid not null references public.operation_log(id) on delete cascade,
  token text not null,
  pii_type text not null check (pii_type in ('name', 'email', 'phone', 'address', 'order_reference', 'other')),
  vault_reference text,
  expires_at timestamptz not null,
  created_at timestamptz not null default now()
);

create index if not exists operation_log_created_at_idx on public.operation_log(created_at);
create index if not exists escalations_open_idx on public.escalations(status, created_at);
create index if not exists eval_log_evaluated_at_idx on public.eval_log(evaluated_at);

alter table public.knowledge_chunks enable row level security;
alter table public.operation_log enable row level security;
alter table public.escalations enable row level security;
alter table public.eval_log enable row level security;
alter table public.pii_registry enable row level security;

create or replace function public.match_knowledge_chunks(
  query_embedding extensions.vector(384),
  intent_filter text,
  match_count int default 4
)
returns table (
  source_id text, source_type text, page integer, intent text, text text,
  score double precision, policy_eligible boolean, fixture_only boolean
)
language sql stable
set search_path = public, extensions, pg_temp
as $$
  select k.source_id, k.source_type, k.page, k.intent, k.chunk_text,
         1 - (k.embedding <=> query_embedding) as score,
         k.policy_eligible, k.fixture_only
  from public.knowledge_chunks k
  where k.approved = true
    and k.intent = intent_filter
  order by k.embedding <=> query_embedding
  limit greatest(1, least(match_count, 8));
$$;
