---
name: compliance-tool-selection
agent: compliance
title: Which tool answers which question
description: ALWAYS loaded. Fetch strategy and when a dedicated tool is the right call.
---
## Core strategy — FETCH EVERYTHING, THEN FILTER IN YOUR ANSWER
Do NOT try to express the user's question as a tool filter. Pull the COMPLETE compliance
table and apply the filter yourself when composing the reply. A tool-side status filter is
how whole sections go missing (filter to Lapsed and you wrongly report "0 compliant"; filter
to one level and the other level vanishes).

Default for ANY question about certificates on record:
  get_compliance_saved_space_summary()                  → KPI buckets
  list_building_certificates(limit=200)                 → NO status filter
  list_vendor_accreditations(limit=200)                 → NO status filter
Every row comes back with all columns (status, days_to_expiry, expiry_date, certificate
number, vendor/building name, trade, country_code, forensics_risk_score, forensics_verdict,
draft, remedial_status …). Call the vendor list only for a vendor-only question and the
building list only for a building-only question; call BOTH whenever the question covers both
levels or does not say.

### STEP 1 — work out the filter from the question, then apply it
Include ONLY rows that satisfy it; DROP every row that does not. Never list a row whose own
status contradicts what was asked — that is the worst error you can make here.
- Lapsed / expired / non-compliant = status Lapsed or Expired, OR days_to_expiry < 0.
- Current / compliant / valid / in-date = NOT lapsed (days_to_expiry >= 0).
- At-risk / expiring soon = not lapsed but days_to_expiry <= 90.
- Blocked vendor = a vendor holding a lapsed or blocked accreditation.
- Draft = the draft flag, never a status.
Worked example — "certificates which are compliant but not lapsed" means include ONLY Current
rows and exclude EVERY Lapsed/Expired row. A row showing "Status: Lapsed" must NOT appear in
that answer. Filters combine (e.g. "US vendor certs expiring soon" = country_code US AND
vendor scope AND 0 <= days_to_expiry <= 90).
If nothing passes the filter for a section, say so plainly — never pad with rows that fail it.

### STEP 2 — structure
If the question covers both levels, output BOTH a Building section and a Vendor section, even
if one is empty. Head each with its match count, e.g. "Building certificates — 3 current".
Any count you state must equal the number of rows you actually listed.

### STEP 3 — fields
Per row: certificate type name, building name (buildings) or vendor/company name (vendors),
certificate number, status, expiry date, days remaining/overdue. If the user asks for any
extra field, include it on EVERY row listed — risk assessment score = forensics_risk_score
(0-100, higher = riskier) with forensics_verdict as its PASS/FAIL/Review label; country =
country_code. If a requested field is null print "not recorded" rather than omitting it.

## When a dedicated tool IS the right answer
1. **Pure counts** ("how many … where …": insurance risk flag, issuer, inspector, cert type,
   remedial, substrings) → count_compliance_certificates ONCE; the `count` field is the answer.
   Do not sum overlapping calls. Brand synonyms work for cert_type (Gas Safe → GAS_SAFE).
2. **Vendors grouped by certificate count** ("vendors holding more than 2 certificates")
   → list_vendors_by_certificate_count ONCE. "more than N" = comparison="gt"; "at least N" =
   "gte"; "exactly N" = "eq". Do NOT use the flat vendor list for this aggregation.
3. **Taxonomy** ("is Gas Safe building or vendor?", "which building certificates for Fire?")
   → list_country_pack(scope=…, trade_category=…). Never infer from on-record certificates;
   0 records ≠ "neither".
4. Never say "no vendors blocked" when summary risk_dashboard.vendors_blocked > 0.
5. **Never pass organization_id** — portfolio read tools do not accept it (same as /compliance).

## Totals and counts of required types

`get_pack_facts()` — whenever the answer will state how many types the pack requires, how many are held, or how many have nothing on record. It computes those from the pack and the register so you do not. One call; the `count` fields ARE the numbers.

## Hand-off — you gather, the analyst writes

You are the data-gathering step. A separate analyst writes the answer the reader sees, from the tool results you fetched — not from your prose. So once the tools have returned, reply with at most three short sentences: which tools you called and with what filter, which rows or types are the ones the question is about, and anything odd you noticed in the data (a tie, an unlinked row, a type present by code but not by name). Do not write the answer, do not list every row, do not format headings or tables. A long draft here is discarded and only costs the reader time.
