# Memorilla + cognee Hybrid Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Combine Memorilla's recalled memories with cognee's graph edges into one recall block in the orchestrator, shadow by default.

**Architecture:** `services/cognee_graph.py` owns cognee (ported from the shadow pilot); pure `combine()` builds the block; `chat_memories` feeds/removes graph facts; `_build_stateful_input` gathers both recalls.

**Tech Stack:** Python 3.12, cognee[postgres] 1.6.x, pytest (asyncio auto).

**Spec:** docs/superpowers/specs/2026-11-20-memorilla-cognee-hybrid-design.md

> **As built:** Tasks 4–5 were superseded after the offline comparison: the graph adds *linked memories*
> (`linked_texts`, `linked_memories`, `shadow_soon`) instead of an edge block (`combine`, `memory_block`). See the spec's
> "Changed by the offline comparison" and "Changed by review" sections.

## Global Constraints

- Worktree `/Users/hussain/Desktop/hoist/wt-memorilla-cognee`, branch `memorilla-cognee` off `origin/memorilla-recall`. NO commits/pushes until Hussain says so.
- Tests run as `DB_URL='postgresql+asyncpg://t:t@localhost:5499/t' pytest tests/test_cognee_graph.py -q` from svc-deepagents; local env lacks langchain (orchestrator not importable) — seam-level tests.
- Off unless `COGNEE_MEMORY_COMPANIES` names the company AND `COGNEE_DB_URL` names a database other than DB_URL (any spelling). `COGNEE_MEMORY_MODE` default `shadow`.
- No cognee call may raise into a turn or a memory write; reads capped at `GRAPH_TIMEOUT_S = 3.0`.
- Only active, non-personal `fact`/`correction` texts enter the graph.

## Review Focus

- Forgotten memory still in graph → must be removed (forget path test).
- Pending (unapproved) fact → never ingested until approved (store + review tests).
- Personal preference → never ingested (store test).
- cognee down / slow at read → recall block identical to Memorilla-only (combine/read tests).
- Edge flood (100+ edges) → block capped at 12 lines, valid text (combine test).

---

### Task 1: Port gating + config as `cognee_graph`

**Files:** Create `src/services/cognee_graph.py`, `tests/test_cognee_graph.py`; Modify `src/config.py` (+`cognee_memory_companies`, `cognee_db_url`, `cognee_memory_mode`), `pyproject.toml` (`[cognee]` extra), add `docker-compose.cognee.yml`.
**Produces:** `enabled(org)->bool`, `live()->bool`, `_cognee_settings()->dict`, `_configure_cognee()`, `_dataset(org)->str`.
- [ ] Port the shadow pilot's gating/config tests (URL-variant guard, empty org, flag scoping, 1.6 config fields, per-dataset handlers, embeddings model, CACHING off) renamed to `cognee_graph`; add `test_mode_defaults_to_shadow`, `test_live_mode`. RED.
- [ ] Port the code (no turn ingest, no shadow search); add `cognee_memory_mode`. GREEN.

### Task 2: Ingest and forget company facts

**Files:** Modify `cognee_graph.py`, `src/services/chat_memories.py` (`store`, `review`, `forget`).
**Produces:** `add_facts_soon(org: str, texts: list[str]) -> None`, `forget_fact_soon(org: str, text: str) -> None`, `fact_text(kind, text, subject) -> str` (same wording as `memorilla.memory_text`).
- [ ] Tests: only active non-personal fact/correction texts reach `_cognee_add`; pending → none; preference → none; disabled → none; errors swallowed; `forget_fact_soon` calls `_cognee_forget` with org dataset + text. RED → GREEN.
- [ ] Hook: in `store()` after Memorilla's add (status active, not personal); in `review()` on approve (add) / reject (forget); in `forget()` (forget). Keep each a one-line guarded call.

### Task 3: Read graph context

**Produces:** `parse_edges(context: str) -> list[Edge]` (`Edge = (source, relation, target, description)`), `async graph_context(question: str, org: str) -> dict | None` → `{"edges": list[Edge], "latency_ms": int, "error": str|None}`.
- [ ] Test `parse_edges` on the real 20 Nov sample (4 edges incl. 2 "contains" chunk edges); timeout → `error="timeout"`; exception captured; disabled → None. RED → GREEN.

### Task 4: Combine

**Produces:** `combine(question: str, hits: list[dict], graph: dict | None, *, cap: int = 12) -> tuple[str, dict]` → (block text to append, trace payload {anchors, kept, dropped}).
- [ ] Tests: anchor filter keeps fire-door→FRA example, drops unrelated edge; drops "contains" chunk edges; drops edge already stated by a recalled memory; caps at 12 with 100 edges; graph None/empty → ("", payload); block ends with the records-win rule. RED → GREEN.

### Task 5: Orchestrator hook + trace

**Produces:** `async memory_block(question: str, hits: list[dict]) -> str` (runs `graph_context` for the caller's org, `combine`, records `trace.on_memory("graph recall", ...)`, returns block only when live).
- [ ] Seam tests: shadow → "" returned + span recorded; live → block returned; disabled → "" and no span; graph error → "" + span with error.
- [ ] `_build_stateful_input`: `hits = await chat_memories.recall(q)`; `graph_block = await cognee_graph.memory_block(q, hits)`; `memories_text = chat_memories.format_recall(hits) + graph_block`. py_compile.

### Task 6: Offline comparison (Memorilla alone vs combined)

- [ ] With the plenum-memory server (memorilla-search build) loaded with the export's memories and cognee's OpenAI build: for the 7 demo questions + multi-hop set, build (a) Memorilla-only block, (b) combined block; answer each with the orchestrator's model; grade against the source memories. Report table to Hussain/Bala.
