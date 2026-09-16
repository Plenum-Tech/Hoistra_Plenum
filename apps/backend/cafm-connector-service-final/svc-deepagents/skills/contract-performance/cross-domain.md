---
name: contract-performance-cross-domain
agent: contract_performance
title: Where your half of a question ends
description: ALWAYS loaded. Hand over rather than guess.
---
## Cross-domain

| The question | Your half | Theirs |
|---|---|---|
| "Is the score fair — the vendor was blocked?" | `capped_by_block` / `block_capped` | `compliance` owns *why* they are blocked |
| "Which company is this?" | your rows carry `vendor_name`, and the read tools filter on it | only if a vendor is absent from your tables entirely — `udr.query_table("vendors")` |
| "Which WOs is this score built from?" | `vendor_wo_scores.work_order_id` (no read route — re-score) | `wo_engine` for the work-order detail |
| "What does the contract actually say about X?" | `contract_documents.document_id` | `doc_rag` reads the clause |
| "Which assets drove the cost variance?" | `cost_variance_alerts` | `udr` joins to assets |
| "Should we still be using them?" | performance and money | `compliance` owns whether they may work at all |

Join keys out: `vendor_id` → `vendors.id`; `work_order_id` → `work_orders.id`;
`asset_id` → `assets.id`; `document_id` → `ingestion_documents.id`.

A question that spans both halves gets both, labelled. Do not answer the other agent's half from
your fields — a block state inferred from a capped score is a guess about compliance, and the
compliance register may say something different.
