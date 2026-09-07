---
name: compliance-recipes
agent: compliance
title: Worked recipes from question to rows
description: Questions about specific rows, owners, counts, a named company, or forged / suspect / unverified documents.
---
## 4. Recipes

### "Are we compliant?" / "What's our compliance status?"
`get_compliance_saved_space_summary()` first — the UI renders its KPIs as cards. Then, if
detail is wanted, `list_building_certificates(limit=200)` and
`list_vendor_accreditations(limit=200)` with **no status filter**, and filter in your answer.

### "AIB Solutions — status of their certificates" / "all certificates belonging to RDM Electrical"
One company named = not a portfolio question. First identify the company name in the
question — the words that name a firm, without "certificates", "for", "belonging to" — and pass
them as `vendor_name` exactly as the user wrote them. You do not need the legal name and you do
not need the spelling to be right: matching is case-insensitive, ignores punctuation, spacing and
Ltd/Limited/LLC/Inc/plc, tolerates a missing or wrong letter, and needs only the distinctive
words — "rdm electrical", "C & H Fire", "Kurt Lesker" and "Brightspark Electrcal" all find their
company. Never retype or "correct" the name before the call. Every result carries
`vendor_name_match`: report the name the register uses (`matched_values`); if more than one
company matched (two "Apex Mechanical" firms exist) keep them apart by name and say that two
matched; if `no_match` is true the tool has returned ALL rows plus `all_values` — look through
those names yourself, and if one is plainly the company the user meant (a shortened or misspelt
form) answer from its rows and say which name you took, otherwise answer that the name is not on
record and offer the closest names. A company can appear in the register in two places, so make
TWO calls, both with the same `vendor_name` and no status filter:
`list_vendor_accreditations(vendor_name="RDM Electrical")` returns what the company **holds**
(its own accreditations and insurance); `list_building_certificates(vendor_name="RDM
Electrical")` returns what the company **issued** (building certificates it signed — an EICR,
a gas certificate, an FRA). Present the two under their own headings — "holds" and "issued
for a building" — and say which is empty. Only when BOTH are empty is the company not on
record. A firm with no accreditation rows has no block state, so do not call it "clear";
say it holds no accreditation on file. Do not pull the portfolio summary, and never include
another company's certificates.

### "Certificates for Building 5" / "what does Bishopsgate hold" / "fire certificates" / "electrical certs by vendor"
The same rule for a building or a trade. Pass the building as the user wrote it in
`list_building_certificates(building_name="building 5")` — "building5", "bldg 5", "bishopsgate",
"the town hall" all match; never look up a site id first. Pass the trade as the user said it in
`trade_category` — "fire safety", "electric", "lifts", "air conditioning", "insurance" are mapped
onto the register's categories (Fire, Electrical, LOLER, HVAC, Insurance) and typos are tolerated;
a trade question about contractors uses the same `trade_category` on `list_vendor_accreditations`.
Read `building_name_match` / `trade_category_match`: say which register value you matched; if
`no_match` is true you have ALL rows and `all_values` — choose the value the user meant if one is
plainly it and say so, otherwise say it is not on record and list the values that exist.

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

### "Show me all forged documents at building level and vendor level separately"
`list_building_certificates(limit=200)` and `list_vendor_accreditations(limit=200)`, both with **no status filter** — forgery is not a lifecycle status, so `risk_filter` will not find it. Keep rows whose `forensics_verdict` is `fail` (`forensics_ccc_verdict` `edited`); that is the forged set. Present two lists, Building then Vendor, each grouped by owner, every row with its type, `forensics_risk_score` and the first reason from `authenticity_warning`, and the certificate ids in the table. State each list's count. Rows at `review`/`suspect` are not forged — mention how many there are and offer them, or list them under their own heading if the question also asks for suspicious documents. Rows with no verdict were never assessed; say so and count them, do not present them as genuine. `run_document_forensics` re-runs the check on one document — use it only when asked to re-check, never to answer a list question.

### "Which vendors have suspicious documents?"
Same two calls; keep `forensics_verdict` in {`fail`, `review`}, group by vendor, worst score first, and label each row forged or suspect from its own verdict — never upgrade a suspect row to forged.
