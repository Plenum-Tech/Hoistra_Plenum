# CAFM AI Platform — Project Context for Claude Code

> Short rules file. The full April 2026 architecture spec (7 layers, agent specs, eval-layer map,
> tables, env vars, task breakdown, definition of done) lives in
> `docs/architecture/cafm-platform-spec.md` — read the relevant section of it on demand, not up front.

## What this is
AI-powered CAFM (facilities management) platform for the UAE/UK. Pillar 1: structured ingestion
(`cafm-connector-service`, production-ready — extend, never rewrite). Pillar 2: multi-agent ingestion,
query and document generation, all converging on the PostgreSQL `plenum_cafm` schema.

## Evaluation-first principle
Every agent output at every layer is evaluated before it advances (EL-2.x ingestion, EL-3.0 schema
mapping, EL-4.0 pre-write, EL-5.x data agents, EL-6.x orchestration, EL-7.x outputs). Eval is a hard
gate, never advisory, and every EL result is logged to its audit table. Hard YAML rules always beat AI votes.

## Coding conventions
- Async everywhere (DB, HTTP, file I/O). Pydantic for all schemas. Python 3.12, FastAPI, SQLAlchemy 2.0 async.
- No hardcoded credentials — env vars only. UUID primary keys on new tables. All tables in `plenum_cafm`.
- Never duplicate ORM models — import from cafm-connector-service. Extend `CAFMError` for exceptions.
- structlog via `get_logger(__name__)` with OTel trace_id; an OTel span on every operation.
- 3x exponential backoff on Claude API calls; N=3 runs always via `asyncio.gather`, never sequential.
- Claude called once per file for CSV/Excel schema mapping, never per row.
- Ruff line length 100; type hints everywhere (mypy strict).

## Never
- Execute raw LLM SQL (parameterised only); put table/column names in SQL without the `_SAFE_IDENT`
  regex + `information_schema` allow-list; run DDL without `?confirm=true`.
- Let an agent output advance without its eval layer; send raw data to Layer 6 (only typed `AgentResult`).
- UPDATE or DELETE `orchestration_audit_log` (INSERT only).
- Let Claude generate document files directly (planner emits a DocumentPlan JSON; renderer is deterministic;
  every value traces to a DB row; eval_score < 0.85 is held for review).
- Add a separate vector DB (pgvector only), OCR/NER/fine-tuned models, or sync DB/HTTP calls.

## Company boundary (14 Oct 2026)
Every read and write is bounded to the asking company: a row's own company column decides, else its
building's, then its parents'; NULL never means shared; sites belong to a company only through a company
building standing on it. See `svc-deepagents/src/services/principal.py`, `svc-udr/src/services/scope.py`.
