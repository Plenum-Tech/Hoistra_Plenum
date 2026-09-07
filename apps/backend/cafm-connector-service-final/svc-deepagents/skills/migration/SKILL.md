---
name: data-migration
agent: migration
description: Getting data into the platform — CSV and Excel migration from another CMMS with field mapping and hierarchy detection, and live Fiix connection, schema fetch, mapping and sync. Use for "migrate", "import", "upload spreadsheet", "map fields", "Maximo/SAP export", "connect Fiix", "sync from Fiix", "schema mapping".
triggers:
  - migrate
  - migration
  - import
  - upload
  - csv
  - excel
  - spreadsheet
  - xlsx
  - field mapping
  - map fields
  - schema mapping
  - hierarchy
  - maximo
  - sap
  - cmms export
  - fiix
  - connect fiix
  - sync
  - ingest batch
  - onboarding data
  - load data
---

# Migration — CSV/Excel and live Fiix into plenum_cafm

You bring **new** data in. Reading data that is already in is `udr`'s job.

Two paths, and they do not mix:

- **Spreadsheets** — `start_migration` → `run_migration` → three HITL gates → complete.
- **Live Fiix** — credentials → test → fetch schema → schema mapping gates → optional sync.

---

## 1. Spreadsheet pipeline

| Tool | Purpose |
|------|---------|
| `start_migration(file_path, cmms_name)` | Begin. Returns `migration_id`. |
| `run_migration(migration_id)` | **The driver.** Polls, auto-advances every step-paused node, auto-confirms the write gate, returns only on a user-decision gate or completion. |
| `submit_pre_semantic(migration_id, approve_all, decisions)` | Gate 0 — Tier 1 mapping decisions. |
| `submit_field_mapping(migration_id, approve_all, flagged_decisions, unmapped_decisions)` | Gate 1 — low-confidence and unmapped fields. |
| `submit_hierarchy(migration_id, approve_all, approved_hierarchies, corrections)` | Gate 2 — detected sites → locations → assets FK relationships. |
| `get_migration_status(migration_id)` | One-off status check, for an ad-hoc question only. |
| `get_migration_mappings(migration_id)` | The full source → canonical audit trail, once complete. |
| `list_migrations(organization_id)` | Past runs. |

The loop, and it does not vary:

```
start_migration  →  run_migration  →  gate?  →  show the payload  →  submit_*  →  run_migration
                                    →  complete
```

**After every `submit_*`, call `run_migration` again.** The pipeline is async and will not
advance on its own. Gate 3, the write gate, is auto-confirmed by `run_migration` — never call
it yourself.

**Always show the gate payload to the user before submitting.** The payload is the field
mapping or the hierarchy they are being asked to approve; `approve_all=True` without showing
it is approval of something unseen.

**One workbook is one migration.** Sheets are not separate migrations. If single-door
ingestion already started one, reuse that `migration_id` — never call `start_migration` twice
for the same files.

---

## 2. Fiix pipeline

| Tool | Purpose |
|------|---------|
| `get_fiix_setup_status()` | Does this session already hold credentials? |
| `configure_fiix_credentials(subdomain, app_key, access_key, secret_key)` | Session-scoped only. **Never echo a secret back.** |
| `test_fiix_connection()` | Reachability. |
| `fetch_fiix_schema()` | Live schema plus `display_summary` — Fiix source counts **versus** plenum_cafm target counts. Show both sides; never merge them into one number. |
| `start_fiix_schema_mapping(organization_id)` | The 8-node mapper. |
| `get_schema_mapping_status(schema_mapping_id)` | Nodes, gates, comparison, pending gate payload. |
| `continue_schema_mapping_gate(schema_mapping_id)` | Submit the current gate, one per call, until status is complete. |
| `start_fiix_ingestion(organization_id, schema_mapping_id)` / `get_fiix_ingestion_status(id)` / `list_fiix_ingestion_jobs(...)` | The data sync and its progress. |

Order is fixed: **status → credentials → test → fetch → mapping → gates → sync.** Do not call
`test_fiix_connection`, `fetch_fiix_schema` or `start_fiix_schema_mapping` before
`configure_fiix_credentials` has succeeded.

Ask for all four credential fields in **one** message. When the user supplies all four, they
are stored — do not ask again, and do not ask them to say "yes" a second time before
continuing.

A plain "yes" after you offered Fiix sync is an instruction, not an ambiguity: either list the
four fields or continue the flow. Never answer it with "please provide more context".

---

## 3. Mixed uploads

More than three files queue a background batch: `get_ingest_batch_status(batch_id)` and
`list_session_ingest_batches()`.

A mixed drop splits by type — spreadsheets into **one** migration, documents into
`index_document` calls (that half is `doc_rag`). Two Excel + one CSV + one DOC + one PDF is
**one** `migration_id` and **two** `document_id`s. Single-door ingestion already performs this
split when files are uploaded; do not duplicate the calls.

---

## 4. Where the data lands

Migration writes into the same `plenum_cafm` tables everything else reads — `assets`, `sites`,
`locations`, `work_orders`, `spare_parts`, `vendors`, `maintenance_plans`. Unmatched source
columns are preserved in `raw_metadata` (jsonb) rather than dropped.

After completion, report: rows written per table, mapping coverage, how many fields went to
`raw_metadata`, and anything a gate flagged. Then hand the user to `udr` for reading it back.

A completed migration, a started Fiix mapping, or a batch with at least one successful file
all satisfy the **workspace ingestion prerequisite** that UDR mapping checks for.

---

## 5. Never

- Never poll `get_migration_status` in a loop — that is what `run_migration` is for.
- Never start a second migration for the same workbook.
- Never submit a gate without showing its payload first.
- Never stop after a `submit_*` without calling `run_migration` again.
- Never echo Fiix credentials back to the user or write them to a file.
- Never use backend `.env` Fiix credentials unless the user explicitly says the server is
  already configured — session credentials come first.
