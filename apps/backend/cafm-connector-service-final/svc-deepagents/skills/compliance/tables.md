---
name: compliance-tables
agent: compliance
title: The tables underneath the tools
description: ALWAYS loaded. Column names, the building key, and what coverage measures.
---
## 1. Tables underneath your tools

| Table | Grain | Key columns |
|-------|-------|-------------|
| `compliance_certificates` | one certificate or accreditation | `id`, `certificate_type_code`, `certificate_number`, `cert_scope` (Building \| Vendor), `asset_id`, `site_id`, `site_ref`, `building_name`, `building_reference`, `vendor_id`, `issuer`, `issue_date`, `expiry_date`, `next_due_date`, `status`, `days_to_expiry`, `result`, `defects_found`, `remedial_actions`, `remedial_status`, `document_id`, `country_code`, `authenticity_warning`, `insurance_risk_flag` |
| `country_certificate_packs` | the statutory taxonomy | `certificate_type_code`, `certificate_type_name`, `certificate_scope`, `trade_category`, `country_code`, `frequency_months`, `regulation_reference`, `issuing_body`, `required_contractor_accreditation` |
| `building_country_packs` | which pack a building runs | `site_id`, `country_code`, `pack_version` |
| `compliance_verification_sources` | the register to verify against | `certificate_type_code`, `register`, `source_url`, `channel` |
| `resource_skills` | an operative's personal ticket | `vendor_id`, `operative_name`, `skill_type_code`, `certificate_number`, `expiry_date`, `days_to_expiry` |
| `approvals_queue_items` | the PM's queue | `source_feature`, `item_type`, `severity`, `status`, `summary`, `payload`, `email_draft` |
| `compliance_scan_runs` | one nightly-equivalent scan | `scope`, `building_scanned`, `vendor_scanned`, `alerts_created`, `blocks_set` |
| `compliance_risk_snapshots` | risk over time | `snapshot_date`, `vendors_blocked`, `high_risk_lt_30`, `medium_risk_lt_90` |
| `vendors` (core) | the vendor itself | `id`, `vendor_name`, `block_state`, `block_reason`, `blocked_accreditation_type` |

---

## 2. The building key — read this before grouping by site

A building certificate can identify its building **three different ways**, and which one is
populated depends on how it was ingested:

1. `site_id` — a UUID, set when the certificate resolved to a UUID-keyed site row;
2. `site_ref` — a varchar, set when the site row is keyed on `sites.site_id varchar(50)`
   (which is what this deployment actually uses);
3. `building_name` / `building_reference` only — the building is named on the document but
   matches no site row at all.

So bucket by **`site_id` → else `site_ref` → else normalised `building_name` → else portfolio**.
A certificate that names a building is still that building's certificate even when nothing in
`sites` matches it. Collapsing all three cases into one "portfolio" bucket is the bug this
rule exists to prevent.

`get_compliance_coverage` already does this. Prefer it over hand-rolling the grouping.

## 3. What `coverage` actually measures — say so

Coverage compares certificates on file against **every Building-scope type in the country
pack**. Applicability per building is not recorded, so it measures **pack completeness, not
compliance** — no building is expected to hold all 27 types. Every coverage row carries
`required_basis: "country_pack"` and a note saying this. When you quote a coverage percentage,
quote it as *"N of 27 pack types on file"*, never as a compliance score.

---
