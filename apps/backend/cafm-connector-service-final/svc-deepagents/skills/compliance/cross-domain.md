---
name: compliance-cross-domain
agent: compliance
title: Joining compliance to work orders, vendors and sites
description: Questions that touch another domain — work orders, vendor performance, energy.
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
