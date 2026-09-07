# Migration CSVs (Phase 1 UDR / Schema Mapper)

Upload these via Single Door Orchestrator (`/ai`) — Feature Option **CSV migration**:

1. `vendors.csv`
2. `assets.csv`
3. `work_orders.csv`
4. `contracts.csv` (metadata only — Feature B SLA params still need ops-intel ingest)
5. `invoices.csv` (index only — line verify needs ops-intel `/invoices/verify`)

Approve table/column mapping through the migration gates, then complete UDR write.

## After CSV migration — Feature B score from UDR

1. Apply WO scoring columns (once):
   `migrations/feature_b_udr_wo_columns.sql` + `phase2_contract_performance.sql`
2. Complete UDR write for these CSVs via `/ai` migration.
3. Confirm contracts (B1) in `/vendors` dashboard.
4. Score migrated WOs (no hand-crafted batches needed):

```bash
curl -X POST http://localhost:8009/api/contract-performance/score/from-udr \
  -H "Content-Type: application/json" \
  -d '{"all_buckets": true, "generate_scorecard": true}'
```

Or click **Score from UDR** on `/vendors` (B2 tab).

| Dashboard | What you see |
|-----------|----------------|
| **B1** Contract ingestion | BrightSpark contract left **draft**; confirm in Vendors space. |
| **B2** Vendor scoring | Scorecards built from migrated `plenum_cafm.work_orders`. |
| **B3** Invoice verification | Invoice verify / Approvals rail. |
