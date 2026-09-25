---
name: contract-performance-tables
agent: contract_performance
title: The tables underneath your tools, and which you can actually read
description: ALWAYS loaded. The data layer, including what has no read route.
---
## Tables you own

| Table | Grain | Key columns | Readable? |
|---|---|---|---|
| `contract_sla_parameters` | one contract's commercial terms | `vendor_id`, `contract_id`, `contract_ref`, `sla_response_p1..p4_hours`, `sla_completion_p1..p4_hours`, `labour_day_rate`, `labour_hour_rate`, `overtime_rate`, `call_out_rate`, `parts_pricing_json`, `ppm_obligations_json`, `defaults_used`, `confirmed_by`, `confirmed_at` | `list_contract_parameters()` — no vendor filter; rows carry `vendor_name`, match them yourself |
| `vendor_monthly_scorecards` | vendor × month | `vendor_id`, `score_month`, `overall_score`, `trend_delta`, `component_breakdown`, `ppm_compliance_pct`, `matched_flagged_ratio`, `block_capped` | `list_vendor_scorecards(vendor_id?, vendor_name?)` — rows carry `vendor_name` |
| `vendor_wo_scores` | one scored work order | `vendor_id`, `work_order_id`, `wo_code`, `score_month`, `sla_response_met`, `sla_completion_met`, `first_fix`, `recall`, `accreditation_current`, `component_scores`, `overall_score`, `capped_by_block`, `cost_actual`, `cost_estimated`, `cost_variance_pct` | **no read route** — only produced by re-scoring |
| `vendor_score_weight_config` | the weights | `sla_response_pct`, `sla_completion_pct`, `first_fix_pct`, `recall_pct`, `accreditation_pct`, `blocked_score_cap`, `cost_variance_alert_pct`, `invoice_flag_adversary_gbp` | `get_score_weights()` |
| `invoice_verifications` | one invoice | `vendor_id`, `invoice_ref`, `document_id`, `status`, `matched_count`, `flagged_count`, `matched_flagged_ratio` | `list_invoices(vendor_name?, building_name?, invoice_ref?, status?)` — **takes names** |
| `invoice_lines` | one invoice line | `invoice_verification_id`, `line_no`, `wo_code`, `work_order_id`, `description`, `labour_hours`, `labour_rate`, `labour_amount`, `part_code`, `parts_qty`, `parts_cost`, `line_total`, `reported_labour_hours`, `contract_labour_rate`, `match_status`, `discrepancy_code`, `discrepancy_text`, `delta_gbp`, `adversary_reviewed`, `adversary_agreed`, `pm_decision` | **no read route** — only returned by a verification run |
| `ppm_visits` | one PPM visit vs obligation | `vendor_id`, `asset_id`, `work_order_id`, `ppm_ref`, `frequency`, `scheduled_date`, `completed_date`, `tolerance_days`, `variance_days`, `within_tolerance` | via the scorecard's `ppm_compliance_pct` |
| `asset_criticality` | one asset's L1/L2/L3 | `asset_id`, `asset_code`, `criticality`, `proposed_criticality`, `source`, `approved` | proposal tools; unapproved scores as L2 |
| `cost_variance_alerts` | vendor × month cost breach | `vendor_id`, `score_month`, `threshold_pct`, `breach_job_count`, `total_delta`, `avg_variance_pct`, `max_variance_pct` | surfaced with scoring |
| `contract_documents` | the source PDF | `vendor_id`, `contract_id`, `document_id`, `document_kind`, `blob_url`, `extraction_status`, `extracted_fields` | hand `document_id` to `doc_rag` |
| `fm_report_staleness` | how old the vendor's data is | `vendor_id`, `last_report_at`, `data_as_of` | **no read route** — written by `POST /fm-staleness`, never returned. Use `score_month` for staleness. |

## Vendor names are on the rows

There is no `vendors` table in this list, but you are not blind to names: scorecards, contracts
and invoices all come back with `vendor_name` resolved onto each row, and the read tools accept
`vendor_name` as a filter. `vendor_id` remains a UUID and only a UUID.

## Two tables you cannot read

`vendor_wo_scores` and `invoice_lines` have **no GET route**. They are written by scoring and
verification runs and returned in those responses. If the question needs them and you have not
just run one, the honest answer names the limit and offers to re-run. Do not reach for a
neighbouring table and present it as the same thing.
