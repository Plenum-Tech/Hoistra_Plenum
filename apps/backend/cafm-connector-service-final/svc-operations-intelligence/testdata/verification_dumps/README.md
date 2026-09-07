# CCC verification dumps (§8.2)

Sample CSV files for local smoke of `data_dump` channel verification.

## Layout

Filenames must start with the canonical `certificate_type_code` (stem before first `.`):

| File | Codes covered |
|------|----------------|
| `ASBESTOS_LICENCE.csv` | HSE licensed asbestos contractors (V-11) |
| `UKAS_ASBESTOS.csv` | UKAS asbestos / survey orgs |
| `SIA_ACS.csv` | SIA Approved Contractor Scheme |
| `BPCA_MEMBER.csv` | BPCA member list |
| `PESTICIDE_PAx.csv` | BASIS PROMPT pesticide quals |

Each row needs a resolvable key via one of: `lookup_key`, `registration_number`, `licence_number`, `member_number`, `company_number`, `acs_number`, `id`.

## How to use

1. Copy into the worker dump dir (or point `VERIFICATION_DUMP_DIR` here):

```powershell
$dst = $env:VERIFICATION_DUMP_DIR
if (-not $dst) { $dst = ".\data\verification_dumps" }
New-Item -ItemType Directory -Force -Path $dst | Out-Null
Copy-Item .\testdata\verification_dumps\*.csv $dst -Force
```

2. Trigger ingest:

```http
POST /api/compliance/verification-dumps/run-cron
```

Or upload via Center **Sources** → Ingest verification dump.

3. Verify a certificate whose `certificate_number` / accreditation matches a `lookup_key` → `status=verified`, `channel=data_dump`. Unknown key → `dump_miss` + Approvals class 3.

## Public API env (not dumps)

| Variable | Purpose |
|----------|---------|
| `GOVUK_EPC_EMAIL` + `GOVUK_EPC_API_KEY` | EPC / DEC / TM44 |
| `FCA_API_KEY` (+ `FCA_API_EMAIL`) | EL insurance firm check |
| `COMPANIES_HOUSE_API_KEY` | ESOS / company status |
| `VERIFICATION_DUMP_DIR` | Weekly dump scan path (default `/app/data/verification_dumps`) |

Do not commit real API keys. Without keys, public_api returns `needs_config` and falls back to Verify-now URL.
