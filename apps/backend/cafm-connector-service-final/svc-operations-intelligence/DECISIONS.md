# Phase 2 open questions — locked decisions

Source: Plenum Phase 2 PRD v1.2 § Open Questions.

| # | Decision | Scope | Locked choice |
|---|----------|-------|---------------|
| 1 | Vendor / alert email delivery | Compliance A2/A3 (shared pattern later) | **Handoff** — platform drafts; PM sends from own client via `mailto_uri`. OAuth later. |
| 2 | Certificate authenticity | Compliance A2 | **Soft warning** — flag and let PM proceed / override. |
| 3 | Accreditation register verification | Compliance A5 | **This phase: navigation links only.** API verify (Gas Safe, NICEIC) later. |
| 4 | Notifications & Approvals queue UI | Shared A+B+C | **Minimal Orchestrator UI extension first**; standalone view when volume justifies. |
| 5 | FM report ingestion cadence | Feature B (not Compliance) | **Option A: staleness flag only** (`data as of [date]`). Option B chase email = fast-follow. |

## Code mapping

- Q1 → `EMAIL_DELIVERY_MODE=handoff` (default), `build_email_handoff()`, Approvals decide → `status=handoff_ready`
- Q2 → `soft_authenticity_warning()` on certificate upsert
- Q3 → `POST /api/compliance/verify-now` navigation URL only
- Q4 → `/api/approvals` unified rail (A+B+C) + per-feature queues; FE `UnifiedApprovalsRail`
  with Approve / Edit / Dismiss on Compliance, Vendors, and Energy Saved Spaces
- Q5 → Feature B `POST /fm-staleness` (flag only)

## Residual closure (formerly PARTIAL)

- **Pinned Runs** → Compliance + Vendors + Energy catalog pins in `deep-agent-pinned-runs.ts`
  (≤5 visible per space via scoring)
- **UK pack count** → **54 types** (27 Building + 27 Vendor) from authoritative
  `UK_Compliance_Certification_Pack_v1.1.docx`; rebuild via
  `scripts/rebuild_uk_pack_from_docx.py`. Earlier 57 came from expanding P402/P403/P404
  and PA1/PA2/PA6 into separate CountryPack rows; those modules stay on ResourceSkill.
- **A4 CountryPack entity** → aggregate API `GET /api/compliance/country-pack/entity`;
  storage remains normalised `country_certificate_packs` (one row per type)
- **A4 pack admin UI** → Compliance Saved Space “Country Pack” panel (seed / JSON load /
  activate building / notify version)
- **A4 building thresholds on scan** → `resolve_site_pack_context` + `BuildingCountryPack`
  drives country/version; `effective_alert_thresholds` merges pack JSON with ladder defaults
- **A4 UK seed thresholds** → seed/backfill writes `DEFAULT_THRESHOLDS` (not empty `{}`)
- **Audit immutability** → `write_audit()` INSERT-only **and** DB triggers in
  `migrations/phase2_ops_audit_append_only.sql` (UPDATE/DELETE raise)
- **UDR hybrid** → documented in `UDR_PHASE2.md` (core UDR entities + Phase 2 extension tables
  in shared `plenum_cafm`)

## Swarm + gap-close (A/B/C)

- Planner → `src/swarm/planner.py` decomposes compliance scan into parallel Building + Vendor Workers
- Workers → separate AsyncSessions via `asyncio.gather`; Adversary outcomes:
  `approved` | `flagged` | `escalated` + confidence gate &lt; 0.85
- Certificate Single Door → `compliance_single_door.py` auto-routes cert PDFs to
  `/api/compliance/extract` + draft upsert (HITL)
- Phase 2 WO scope → DeepAgent system prompt: A/B/C never create/dispatch WOs; scope response
- ResourceSkill → Compliance Saved Space section + `GET /api/compliance/resource-skills`
- Energy PDF → multi-section reportlab (summary / EUI trend table / anomalies)

## Feature B (Contract Performance) — implemented in this service

- B1 → `POST /contracts/extract` (Relationships Agent) + ingest + asset criticality HITL / UDR propose
- B2 → WO scoring (L1 failures 3× vs L3), PPM ±7d auto-load, month-window cost variance, monthly scorecard cron, invoice ratio blended into overall (15%)
- B3 → `POST /invoices/extract-verify` + verify; flags ≥ £500 Adversary
- Admin weights → `PUT /admin/weights` + Vendors Saved Space UI
- Q5 → `POST /fm-staleness` (flag only)
- DeepAgent → `contract_performance` tools; FE Vendors Saved Space panel
- Single Door auto-route → `contract_performance_single_door.py` classifies
  contract/PO/invoice uploads and calls `/contracts/extract` or `/invoices/extract-verify`
  (alongside Doc RAG indexing for document vectors)

## Feature C (Energy Intelligence) — implemented in this service

- Smart meters → `POST /api/energy/meters`, `POST /api/energy/readings/ingest`,
  `POST /api/energy/meters/pull` (MPAN/DCC electricity, MPRN gas → `MeterReading`;
  2+ HH gaps flagged; worker retries **real DCC/sim pull** 3× every 10 min then escalates)
- EUI → `POST /api/energy/eui/compute` vs CIBSE TM46 (`reference/cibse_tm46_uk.json`);
  deviation % + £ at tariff
- Condition → `POST /api/energy/condition/deduce-from-vectors` (Doc RAG + chunk SQL fallback,
  optional Haiku); writes Asset provenance; **auto cross-ref** when score ≤2
- Cross-ref → queue/dashboard remediation with repair-vs-replace (**no WO**)
- Occupancy → `POST /api/energy/occupancy/log`; baseline drift suppressed if change in window
- Anomalies → weekend spike / baseline drift / asset spike + £ translation;
  Acknowledge / Monitor / Mark expected; status for future WO engine
- Monthly report → cron 1st @ 07:00; PDF → Azure Blob when configured (`GET .../reports/{id}/pdf`);
  Energy Saved Space shows reports, anomalies, remediation recommendations
- Env: `DCC_API_BASE_URL`, `DCC_API_KEY`, `DCC_SIMULATE_FILL`, `DOC_RAG_BASE_URL`,
  `AZURE_STORAGE_CONNECTION_STRING`
- Migration → `phase2_energy_intelligence.sql` + `phase2_energy_intelligence_gaps.sql`

