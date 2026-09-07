# Phase 2 ↔ UDR contract (hybrid ownership)

Phase 2 does **not** invent a second data plane. All structured CAFM data lives in
PostgreSQL schema **`plenum_cafm`**. Ownership is split so engines extend UDR
without duplicating core entities.

## Core UDR entities (read / light-update by Phase 2)

Owned by cafm-connector-service / platform migrations. Ops-intel **must not**
redefine ORM for these — query via SQL or shared imports.

| Entity | Typical table | Phase 2 use |
|--------|---------------|-------------|
| Asset | `assets` | A2 site/asset FK; B1 criticality; C condition write-back |
| Vendor | `vendors` | A3 accreditation + `block_state` columns; B scoring |
| WorkOrder | `work_orders` | B2/B3 **ingested** FM reports only — never created by A/B/C |
| Location / Site | `locations` | Building pack activation, energy site scope |
| Contract (legacy) | connector contract tables if present | B1 may map into ops `contract_parameters` |

## Phase 2 extension tables (ops-intelligence owned)

Same schema `plenum_cafm`, created by `svc-operations-intelligence/migrations/*.sql`.
These are **UDR extensions**, not a parallel register.

### Feature A — Compliance
`compliance_certificates`, `country_certificate_packs`, `building_country_packs`,
`resource_skills`, `approvals_queue_items`, `ops_audit_log`, `ops_email_log`,
`compliance_scan_runs`, `compliance_risk_snapshots`, `approval_action_tokens`

### Feature B — Contract Performance
`contract_parameters`, `asset_criticalities`, `vendor_scorecards`,
`vendor_wo_score_lines`, `invoice_verifications`, `fm_report_staleness`,
score weight config tables as migrated

### Feature C — Energy
`energy_meters`, `meter_readings`, `meter_reading_gaps`, `eui_snapshots`,
`energy_anomalies`, `energy_recommendations`, `energy_monthly_reports`,
`site_occupancy_logs`, building energy profiles as migrated

## Document vector layer

Inspection / cert PDFs are indexed by **Doc RAG** (`doc-rag-main`). Feature C
condition deduction reads vectors / chunks via HTTP or SQL into Doc RAG tables —
provenance stored on Asset / recommendation rows. Original blobs stay in Azure Blob.

## Write rules

1. **A/B/C never INSERT work orders** — Approvals / dashboards / email handoff only.
2. **Vendor `block_state`** — governance field on core `vendors`; future WO Engine enforces at allocation.
3. **`ops_audit_log`** — append-only (app `write_audit` + DB trigger forbidding UPDATE/DELETE).
4. Certificate / contract / energy rows FK to core UDR ids where possible; unmatched refs stay in `raw_metadata` / draft until PM confirms.

## Single Door

Orchestrator routes uploads to:
- Migration / Doc RAG (Phase 1)
- Feature A certificate extract (`compliance_single_door`)
- Feature B contract/invoice extract (`contract_performance_single_door`)

All converge on `plenum_cafm` + Approvals queue — one interface layer.
