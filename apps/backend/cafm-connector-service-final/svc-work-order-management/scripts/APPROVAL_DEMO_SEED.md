# Approval demo data (DB seed)

Loads approvers, rules, and **historical work orders** so `suggest_approval_chain` returns real data for prompts like:

> Critical HVAC repair — **CHILLER-102**, **Building B, Basement 2**

## Auto (Docker)

`docker-compose.single-url.local.yml` runs this on `backend-app` start when `SEED_APPROVAL_DEMO=true`:

```text
alembic upgrade head
python -m scripts.seed_approval_demo
```

## Manual (Azure / prod)

From repo root:

```powershell
cd apps\backend\cafm-connector-service-final\svc-work-order-management
$env:DATABASE_URL = "postgresql+asyncpg://USER:PASS@HOST:5432/plenum_agent"
python -m scripts.seed_approval_demo --migrate
```

Or inside the running container:

```powershell
docker compose -f docker-compose.single-url.local.yml exec backend-app python -m scripts.seed_approval_demo --migrate
```

Re-seed approval steps only:

```powershell
python -m scripts.seed_approval_demo --force
```

## What gets created

| Item | IDs / values |
|------|----------------|
| Approver users | `khalid.alrashid@facility.ae`, `ops.manager@facility.ae`, `facilities.director@facility.ae` |
| Roles | Maintenance Supervisor, Operations Manager, Facilities Director |
| Demo WOs | `WO-DEMO-APPR-CHILLER-001` … `003` (CHILLER-102, Building B, Basement 2) |
| Approval history | 2–3 step chains, all `approved` |

Idempotent: existing rows are skipped.

## Verify

```http
POST /backend/work-order/api/work-orders/suggest-approval
{
  "work_type": "repair",
  "priority": "critical",
  "location": "Building B, Basement 2",
  "asset_category": "hvac",
  "estimated_cost": 8000
}
```

Expect `auto_suggestion.message`, `chain[]`, and `previous_approval_processes[]` with CHILLER demo WOs.
