---
name: contract-performance-recipes
agent: contract_performance
title: Question to tool calls, for the questions that actually get asked
description: ALWAYS loaded. Each recipe names the real tool parameters.
---
## "How is Apex performing?"

1. `list_vendor_scorecards(vendor_name="Apex")`.
2. Check `vendor_match` first. `"ambiguous"` → ask which company (`answer_hint` lists them).
   `"none"` → that company has no scorecard; say so and name who does. Never substitute.
3. Report: `overall_score` **with its `score_month`**, `trend_delta` against the previous month,
   then `component_breakdown` — SLA response, SLA completion, first fix, recall, accreditation —
   and `ppm_compliance_pct`. State whether `block_capped` is set. Attach `data_as_of`.

## "Which vendors are below 80?" / "Who is worst?"

`list_vendor_scorecards()` **unfiltered** — the set is the point. Rank, then say which measure
you ranked on (see `domain_contract_knowledge.md`). Separate capped scores from earned ones
rather than interleaving them. Rows carry `vendor_name`, so name the vendors directly.

## "Check this invoice" / "Did they overcharge us?"

- Already ingested: `list_invoices(vendor_name="apex")` or `list_invoices(invoice_ref="INV-2847")`.
  That gives headers — `status`, `matched_count`, `flagged_count`, `matched_flagged_ratio`.
- A fresh PDF: `extract_and_verify_invoice(source_text=…)`; lines already to hand:
  `verify_vendor_invoice(lines=…, work_orders=…)`.

Report matched, flagged, and the total of **Adversary-agreed** deltas. A flagged total that has
not been through the Adversary is a suspicion — label it as one.

## "Why is that line flagged?"

`list_invoices` returns headers only and there is **no read tool for `invoice_lines`**. Either
re-run verification (which returns the lines) or say the detail is not retrievable and offer to.
When you do have the line, show the arithmetic: billed hours × billed rate against recorded
hours × contracted rate, and the difference. Name which rate was used and where it came from —
`labour_hour_rate`, or `labour_day_rate ÷ 8`, or no check at all because neither was known.

## "What does their contract commit them to?"

`list_contract_parameters()` — it takes no vendor filter, so pull the set and find the row by
its `vendor_name`. Read `PRESENTATION_RULE` on that row before quoting any figure from it: it
names the fields that came from system defaults rather than the document.
Report the SLA hours for the priority asked about, the labour rates, and the PPM obligations.
Lead with `defaults_used` if anything material is defaulted, and say whether it is confirmed.
A question about clause *wording* goes to `doc_rag` via `contract_documents.document_id`.

## "What is waiting on me?"

`list_contract_approvals(status="pending")`. Say what the decision is, not what the record's
state is called. Decide with `decide_contract_approval(item_id=…, decision=…)`, and invoice
lines with `decide_invoice_line(verification_id=…, line_id=…, decision=…)` — one per line, as
the PM approves, challenges or rejects.

## "How are scores weighted?" / "Is the scoring fair?"

`get_score_weights()`. Give the five component weights and the two overrides —
`blocked_score_cap` and `cost_variance_alert_pct`. If the question is really "why is this vendor
scored like that", the answer is usually the L1 3× weighting or a block cap; check before
explaining the weights in the abstract.

## "Set up a new contract"

`extract_contract_from_document(source_text=…)` → present the draft **with `defaults_used`
marked** → `update_contract_parameters(parameters_id=…, updates={…})` for the PM's edits.

**STOP THERE.** Confirming is not the last step of this recipe — it is a separate decision a
PERSON makes, on a later turn, having read the draft. Never call
`confirm_contract_parameters` as part of an ingest, and never because the chain looks
unfinished. On 21 Sep 2026 that is exactly what happened: a set was created and confirmed one
second later, unattended, with seventeen platform defaults and nothing read from the
document. Those assumptions became the terms a vendor is judged and invoiced against.

Call it only when the reader has said, on this turn, that they want these terms confirmed.
The server now refuses an unattributed confirm and refuses any set with no contract-sourced
term, so an attempt will fail — but the reason it must not be attempted is that confirming
is theirs to do.

A draft does NOT score. Scoring loads a vendor's *confirmed* contract, so an unconfirmed set
governs nothing — say that, and say what is still needed to confirm it.

## "Which assets are critical?"

`propose_asset_criticality_from_udr()` for a bulk proposal, or `propose_asset_criticality(asset_id=…)`
for one, then `approve_asset_criticality(criticality_id=…)`. Unapproved proposals score as L2 —
that is not a gap in the data, it is the value used.
