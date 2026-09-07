# Layer-1 ingest test data (FM)

Two source files to upload **together as a single batch** through the migration UI to exercise
**Layer 1 — pre-processing + unique-table identification**.

| File | Contents |
|---|---|
| `fm_source_primary.xlsx` | 5 sheets: **Assets, Sites, WorkOrders, Vendors, Resources** (the Layer-1 entities, FM-native column names) |
| `work_tasks.csv` | A single table that is the **same entity as `Assets`** under a different name |

Regenerate any time: `python tests/fixtures/generate_fm_test_data.py`

## What's deliberately embedded (and what to expect)

### 1. Pre-processing (spec §1)
- **NaN / empty values** — `Assets.status` has one blank cell → removed/handled.
- **A 100%-empty column** — `Assets.notes_blank` is entirely empty → dropped.
- **An exact duplicate row** — the `A-003` asset row is repeated → de-duplicated (Assets 9 → 8 rows).
- **Two columns, different names, identical values** — `Assets.ASSETNUM` and `Assets.asset_ref` hold the same IDs (100% overlap) → flagged as an **auto-merge candidate**; the model proposes a name from the *values* (challenge 2). *(Heuristic proposes a generic id name offline; with the LLM hook it infers `asset_id`/`asset_code`.)*
- Partial-similarity / 60–94% overlap pairs would be **flagged for manual review** in the central query space + activity log.

### 2. Unique-table identification (spec §2 / 7.3)
- **Per-table metadata + primary keys** — each table gets a natural PK via uniqueness + non-null:
  `Assets→ASSETNUM`, `Sites→site_ref`, `WorkOrders→WONUM`, `Vendors→vendor_id`, `Resources→engineer_id`.
  *(Note the duplicate row makes `ASSETNUM` non-unique **before** de-dup — it only becomes a clean PK after pre-processing, which is the point.)*
- **Same entity, different name** — `work_tasks` has the same metadata as `Assets` (same columns/PK, overlapping values) but a different name → flagged as a **consolidation candidate** (≈0.89 metadata similarity, name < 0.95) for the user to confirm. This is the spec's exact `work_tasks == assets` example.
- `Sites`, `Vendors`, `Resources`, `WorkOrders` stay **unique** (distinct metadata).

### 3. Downstream (bonus, also exercised)
- **Foreign keys** — `WorkOrders.asset_no → Assets.ASSETNUM` and `*.site_ref → Sites.site_ref` (100% referential integrity).
- **FM ontology (Step-1b)** — FM-native names resolve deterministically: `ASSETNUM→asset_code`, `WONUM→wo_code`, `EQKTX→asset_name`, `fault_description→wo_description`, `manufacturer→make`, `vendor_name→supplier`, `date_raised→created_date`, `full_name→user_full_name`.
- **Sanctity-check fodder** — `WorkOrders` contains `"Replaced pump on Level 5 AHU"` and `"Recurring damp on north wall of Level 3"` for the F7-5 misallocation / Job-1 discovery checks.

## How to test from the UI
1. Open the **migration** flow.
2. Upload **both** files in one go (multi-file → single batch). Expect **one** UDR run / activity entry, not two.
3. Watch the **pre-processing** step: NaN handled, 1 duplicate row removed, `notes_blank` dropped, the `ASSETNUM/asset_ref` merge surfaced.
4. Watch the **unique-table** step: `Assets ~ work_tasks` presented as a consolidation candidate to confirm/reject.
5. Continue through mapping (FM-native names auto-resolve at the deterministic/alias step).

## Cross-check the backend (no UI / DB needed)
Run the dry-run — it executes the **same pure modules** the pipeline uses and prints/asserts the expected outcomes:

```
python tests/test_fm_ingest_dryrun.py
```

Expected: every PASS line above (dup removed, candidate flagged, PKs natural, FKs detected, FM names resolved) and `ALL TESTS PASSED`.
