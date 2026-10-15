---
name: contract-performance-contract-terms
agent: contract_performance
title: What a contract holds, and what "the contract says" is allowed to mean
description: ALWAYS loaded when the question is about terms, rates or obligations.
---
## The terms you hold

`contract_sla_parameters` is the commercial contract reduced to fields:

- **Response and completion**, per priority: `sla_response_p1..p4_hours`,
  `sla_completion_p1..p4_hours`. Response is to *attend*; completion is to *finish*. A vendor
  can meet every response target and fail every completion one — quote the one that was asked
  about.
- **Labour**: `labour_hour_rate`, `labour_day_rate`, `overtime_rate`, `call_out_rate`.
- **Parts**: `parts_pricing_json`.
- **Planned work**: `ppm_obligations_json` — what they committed to visit and how often.
- **Provenance**: `defaults_used`, `confirmed_by`, `confirmed_at`, `contract_ref`.

## Three states, and they are not the same answer

1. **Confirmed** — a human accepted the extraction. The terms are the contract.
2. **Draft / unconfirmed** — extracted and persisted, nobody has signed it off. Usable, but say
   so in the same breath.
3. **Defaulted** — the field is in `defaults_used`. This is the platform's assumption, not the
   vendor's commitment. It cannot be quoted at a vendor and must never appear in a sentence
   beginning "the contract says".

## What the contract does not contain

The parameters are an extraction, not the document. When the question is about wording — a
clause, an exclusion, a liability cap, a notice period — the answer is in the PDF, and that is
`doc_rag`'s to read via `contract_documents.document_id`. Say you hold the commercial terms and
hand the clause question over; do not paraphrase a clause from field values.

## When no contract is on file

A vendor with no `contract_sla_parameters` row is not a vendor with bad terms — it is a vendor
whose terms were never ingested. Their scores still compute against system defaults, which is
exactly the case where `defaults_used` must lead the answer. "No contract on file" is a finding
a PM can act on; "SLA 24h" invented from a default is not.
