---
name: migration-ui-persistence
description: How the migration UI persists snapshots/overrides/history — and the append-only node_logs gotcha
metadata:
  type: project
---

The migration panel (`apps/frontend/src/features/ai/pipeline/migration/`) renders completed steps from per-node snapshots derived from the backend `GET /api/migration/{id}/status` `nodes[]` array. That array is built (schema-mapper `src/app.py`, `_MIGRATION_PIPELINE`, node_ids 1–9) from `MigrationJob.node_logs`, which is **append-only** (`services/job_progress.py` `append_migration_node_log` only appends; the status builder keeps last-entry-per-node_id).

**Key gotcha:** Node 2 (deterministic mapping) is logged once BEFORE the pre-semantic gate and never re-logged, so table/column **overrides** (rename table, new table, rename column — applied at the pre-semantic gate, node 3) never reach node 2's output. Symptom: completed cards / chat history / archived runs / exports show stale pre-override names even though the backend state is correct. Fixed (2026-06) by: (a) `pre_semantic_review_node.py` persists the FULL renamed `tier1_mappings_by_table` + `table_routing` + `new_tables` in node 3's log; (b) the status builder in `app.py` OVERLAYS node-3's `table_routing`/`new_tables`/renamed mappings onto node 2's output before returning. Frontend `Node2Deterministic` (`step-pause.tsx`) relabels table headers via `table_routing` and renders `target_field`.

**Restart from Node 1:** the rerun endpoint (`app.py` `migration_rerun_from`) now clears `node_logs`+`current_step` only for `node_num==1` so the fresh run doesn't echo the discarded run's nodes. The frontend archives the discarded run into sessionStorage (`plenum-migration-archived-runs:<id>`) BEFORE wiping, as a collapsed `ArchivedRun` (see [[architecture-caveats]]).

**Frontend storage keys (sessionStorage, per migrationId/version):** `plenum-migration-snapshot-by-node:<id>`, `plenum-migration-pre-semantic-history:<id>`, `plenum-migration-archived-runs:<id>`; chat turns in localStorage `plenum_deep_agent_turns_v1`; completed-migration cards in `plenum-orch-completed-migrations:<sessionId>`. Hierarchy/FK gate snapshots: node 3/5/8 are gate-completion nodes needing dedicated renderers in `MigrationStepSnapshot` (else they fall through to a generic "Step complete").
