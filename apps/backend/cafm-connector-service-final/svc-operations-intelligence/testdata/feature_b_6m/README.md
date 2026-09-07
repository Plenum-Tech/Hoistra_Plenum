# Feature B — 6-month contract performance test data

Generated for org `00000000-0000-0000-0000-0000000000b1` covering score months:
- 2026-02-01
- 2026-03-01
- 2026-04-01
- 2026-05-01
- 2026-06-01
- 2026-07-01

## Vendors
| Vendor | Profile | Block |
|--------|---------|-------|
| Apex Mechanical | Strong SLA / high first-fix | Clear |
| BrightSpark Electrical | Mid performance / more overruns | Clear (contract **draft** for B1 HITL) |
| SafeLift Engineering | Weaker SLA; blocked from May 2026 | Blocked (cap 60) |

## Counts
- Work orders: **559** (~28 × 3 vendors × 6 months)
- PPM visits: **60**
- Invoices: **36**
- Assets: **15** (one unapproved criticality for B1)

## End-to-end (CSV migration → Feature B dashboards)

### 1) Apply Feature B schema (creates contract-performance tables)
```bash
# Requires DATABASE_URL / DB_URL
psql "$DATABASE_URL" -f migrations/phase2_contract_performance.sql
# or let the seeder apply it:
python scripts/seed_feature_b_6m.py --base-url http://localhost:8009 --dry-run
# (real seed applies schema when DATABASE_URL is set; use --no-schema to skip)
```

### 2) CSV migration (Phase 1 UDR) — Feature Option CSV
Upload folder `migration_csvs/` via `/ai` Single Door:
`vendors.csv`, `assets.csv`, `work_orders.csv`, `contracts.csv`, `invoices.csv`

### 3) Seed Feature B engine (B1/B2/B3 APIs)
```bash
python scripts/seed_feature_b_6m.py --base-url http://localhost:8009 --data C:/CMMS/Plenum-CAFM/apps/backend/cafm-connector-service-final/svc-operations-intelligence/testdata/feature_b_6m --skip-vendor-sql
```

### 4) Open Feature B dashboards
- UI: `/ai?space=vendors` (Vendors Saved Space)
- **B1** — draft BrightSpark contract + pending asset criticality
- **B2** — scorecards / KPIs for Apex + SafeLift
- **B3** — invoice verification via Approvals / chat tools

## Load via API only (skip CSV if UDR already has core tables)
```bash
python scripts/seed_feature_b_6m.py --base-url http://localhost:8009 --data C:/CMMS/Plenum-CAFM/apps/backend/cafm-connector-service-final/svc-operations-intelligence/testdata/feature_b_6m
```

## Manual API sequence
1. `PUT /api/contract-performance/admin/weights` ← `weights.json`
2. For each contract in `contracts.json`: ingest; confirm only if `status=confirmed`
3. For each asset: `POST /asset-criticality` → approve if `approved`
4. For each item in `score_batches.json`: `POST /score/work-orders`
5. For each invoice (confirmed vendors): `POST /invoices/verify`
6. For each scorecard request (confirmed vendors): `POST /scorecards/monthly`
