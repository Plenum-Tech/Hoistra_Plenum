---
name: contract-performance-tool-selection
agent: contract_performance
title: Which tool answers which question, and the name-to-id problem
description: ALWAYS loaded. Read before calling anything when the question names a company.
---
## Asking about a company by name

Every read tool takes `vendor_name`, and every row comes back carrying `vendor_name` — the
service resolves it after the query on scorecards, contracts and invoices alike. So a question
that names a company is answered directly:

```
list_vendor_scorecards(vendor_name="Apex")
list_contract_parameters()            → rows carry vendor_name; match the one you need
list_invoices(vendor_name="Apex")
```

Matching is tolerant: case, punctuation, spacing and Ltd/Limited/LLC do not distinguish two
companies, and one wrong letter does not lose the match. "gough and kelly", "Gough & Kelly" and
"GOUGH AND KELLY LTD." all find the same vendor.

**`vendor_id` takes a UUID and nothing else.** Putting a name there once produced
`422 uuid_parsing` and an answer written from no data at all. The tool now recognises a name in
that field and matches it rather than forwarding it, but pass it as `vendor_name` — say what you
mean.

### Read the match result before you write
- `vendor_match: "one"` — the rows are that company's.
- `vendor_match: "ambiguous"` — the name matched several. **Ask which is meant.** `answer_hint`
  lists them. Choosing one is guessing, and a figure attributed to the wrong company is worse
  than no answer.
- `vendor_match: "none"` — that company has no row of this kind. **That is the answer.**
  `answer_hint` names who does. Never answer about a different vendor.

## FETCH THE SET, THEN FILTER IN YOUR ANSWER

Do not try to express the question as a tool filter. `list_vendor_scorecards()` with no
`vendor_id` returns the set; `list_contract_parameters()` returns every contract. Pull the set
and apply the question's filter yourself when you compose the reply.

Filtering tool-side is how whole findings vanish: ask for one vendor and you cannot say where
they rank; ask for `status="pending"` approvals and you cannot say how many were already
decided. A comparison question ("who is worst", "which vendors are below 80") **requires** the
unfiltered set — there is no other way to know.

## Default reads by question type

| The question is about | Call |
|---|---|
| A named vendor's performance | resolve id (above) → `list_vendor_scorecards(vendor_id=…)` |
| Ranking / comparing vendors | `list_vendor_scorecards()` **unfiltered**, then rank yourself |
| What a contract commits to | `list_contract_parameters()`, match the row yourself |
| An invoice, by company or number | `list_invoices(vendor_name=…)` or `(invoice_ref=…)` |
| How scores are weighted | `get_score_weights()` |
| What is waiting on the PM | `list_contract_approvals(status="pending")` |

## What you cannot read

There is **no read tool for `invoice_lines` and none for `vendor_wo_scores`.** `list_invoices`
returns invoice *headers* — status, `matched_count`, `flagged_count`, `matched_flagged_ratio` —
and nothing line by line.

So "why is this line flagged?" cannot be answered from a read. Either the verification is re-run
(`verify_vendor_invoice` / `extract_and_verify_invoice`, which return the lines), or you say the
line detail is not retrievable and offer to re-run it. Never describe a line you have not been
given: the arithmetic is the whole point of that answer, and inventing it is the worst failure
available in this domain.

The same holds for per-work-order scores. You can say what a vendor's month came to; you cannot
list the jobs behind it without re-scoring.
