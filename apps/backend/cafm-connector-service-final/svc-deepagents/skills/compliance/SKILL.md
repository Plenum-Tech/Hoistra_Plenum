---
name: compliance-engine
agent: compliance
description: Statutory compliance — building certificates and vendor accreditations, their lifecycle status, expiry and renewal, blocked vendors, country certificate packs, coverage by building, evidence packs, the compliance approvals queue, and document authenticity — every certificate carries a forensics verdict (genuine / edited / suspect) with a risk score and the reasons, so questions about forged, fake, tampered, edited or suspicious documents, authenticity warnings or verification against the issuing register belong here. Use for "compliant", "certificate", "accreditation", "lapsed", "expired", "expiring", "blocked vendor", "forged", "forgery", "fake document", "authenticity", "forensics", "EICR", "gas safety", "LOLER", "fire risk assessment", "EPC".
triggers:
  - compliance
  - compliant
  - non compliant
  - certificate
  - certificates
  - accreditation
  - accreditations
  - lapsed
  - expired
  - forged
  - forgery
  - fake
  - authenticity
  - forensics
  - tampered
  - expiring
  - renewal
  - renew
  - due for renewal
  - blocked vendor
  - blocked
  - country pack
  - eicr
  - gas safety
  - gas safe
  - loler
  - legionella
  - fire risk
  - fra
  - epc
  - asbestos
  - pat
  - tm44
  - verify now
  - evidence pack
  - vendor passport
  - statutory
  - coverage
---

# Compliance Engine — certificates and accreditations

You answer from **live Postgres via svc-operations-intelligence (:8009)**. Never from Fiix,
never from the work-order dashboard, never from memory.

> Your tool-routing and vocabulary contract is loaded separately and takes precedence on
> *which tool to call and what a status word means*. This skill covers the **data layer** —
> the tables underneath, how coverage is computed, and how to join out to other domains.

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

## 4. Recipes

### "Are we compliant?" / "What's our compliance status?"
`get_compliance_saved_space_summary()` first — the UI renders its KPIs as cards. Then, if
detail is wanted, `list_building_certificates(limit=200)` and
`list_vendor_accreditations(limit=200)` with **no status filter**, and filter in your answer.

### "AIB Solutions — status of their certificates"
One company named = not a portfolio question. `list_vendor_accreditations(vendor_name="AIB
Solutions")` only. Do not pull the building list or the portfolio summary, and never include
another company's certificates.

### "How many certificates have an insurance risk flag?"
`count_compliance_certificates(...)` once. The `count` field **is** the answer. Do not sum
overlapping calls, do not recount rows yourself.

### "Which vendors hold more than 2 certificates?"
`list_vendors_by_certificate_count(min_count=2, comparison="gt")`. Not the flat list.

### "Which fire certificates are lapsed at Tower A?"
`list_building_certificates(limit=200)` → filter to `trade_category` Fire, building Tower A,
`status` Lapsed or `days_to_expiry < 0`. State the count and list exactly those rows.

### "Is Gas Safe a building or a vendor certificate?"
`list_country_pack(scope=..., trade_category=...)` — the pack is the taxonomy source of truth.
Zero certificates on file is not evidence a type does not exist.

### "Prove it" — an audit asks for evidence
`generate_compliance_evidence_pack(...)` for a building; `get_vendor_passport(vendor_id)` and
`share_vendor_passport(...)` for a contractor.

---

## 5. Cross-domain

| Question | Your half | Their half |
|----------|-----------|------------|
| "Can this vendor do the job?" | accreditation status + `block_state` | `wo_engine` holds the job |
| "Which lapsed-certificate buildings have open urgent WOs?" | certificates grouped by building | `wo_engine` for the WOs, joined on the site/asset |
| "Does this vendor's SLA score reflect their blocked state?" | block state and why | `contract_performance` — Blocked caps the score at 60 |
| "What does the certificate document actually say?" | `document_id` on the certificate | `doc_rag` reads the chunks |

Join keys out: `vendor_id` → `vendors.id`; `asset_id` → `assets.id`; `document_id` →
`ingestion_documents.id`; building via `site_id`/`site_ref`/`building_name` as above.

---

## 6. Never

- **Never create or dispatch a work order.** A lapsed certificate produces a *booking request*
  in the Approvals queue and a renewal email draft — not a WO. If asked, give the Phase 2
  scope response.
- Never pass `organization_id` to the portfolio read tools — they do not accept it.
- Never pass `status="Blocked"` — blocked is a vendor risk badge; use `risk_filter="blocked"`.
- Never list a row whose own status contradicts the filter the user asked for.
- Never say "no vendors blocked" when the summary's `risk_dashboard.vendors_blocked > 0`.
- Never ask for Fiix credentials — Fiix is for CMMS schema mapping, never for compliance data.
