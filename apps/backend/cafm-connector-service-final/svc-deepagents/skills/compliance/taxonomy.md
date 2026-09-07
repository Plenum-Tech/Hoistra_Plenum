---
name: compliance-taxonomy
agent: compliance
title: Answering "what is required" from the country pack
description: Verbatim from _TAXONOMY_DIRECTIVE. Appended to answering.md when the question asks what the law requires rather than what is on file.
---
THIS QUESTION IS ABOUT WHAT IS REQUIRED, NOT WHAT IS ON FILE.
The answer is the COUNTRY CERTIFICATE PACK in the data (list_country_pack) — the statutory list. The certificate register is secondary here: it shows only what is held against that list.
- Answer in TWO parts, because they are two different duties: certificate_scope "Building" is what the owner/occupier must hold; "Vendor" is the accreditation a contractor must hold to do that regulated work.
- Counts come from the pack_facts source, already computed per scope and per trade. Use those numbers verbatim; do not tally the pack yourself and do not fill a gap from what you know of the certificates that exist in the world — if a type is not in this pack it is not part of this answer, and every type that IS in it must appear. codes_with_nothing_on_record lists the ones you are most likely to drop.
- Group the types by trade_category and name them with their regulation_reference and frequency_months. Do not silently drop types because nothing is on record against them — a type with zero certificates is still required.
- Say plainly that applicability depends on the building: gas types apply only where there is gas, LOLER only where there is a lift, Building Safety Act duties only to higher-risk buildings. The pack is the universe of obligations, not a list every building owes.
- DO NOT report what is held or what has nothing on record. This question is about the law, and the answer is complete without any reference to this portfolio's records: no KPI tile, no insight, no closing sentence about held / on record / missing / coverage. The held and missing figures in pack_facts exist for one purpose here — codes_with_nothing_on_record tells you which required types you are most likely to forget to NAME. Use it as a checklist, never as content.
- KPIs count PACK TYPES here, not certificates. The unit enum has no entry for a required type, so use unit "other" with the label saying what is counted (e.g. "Required certificate types"), and leave cert_ids empty — a required type with nothing on record has no certificate id to cite.

- On the agent path there is no pack_facts block in your context: call `get_pack_facts()` and take the required-type totals from it verbatim (55 / 27 / 28 and the per-trade counts). The pack list from `list_country_pack` is for NAMING the types; the counts come from `get_pack_facts`. Do not re-derive either.

PRESENTATION OF A STATUTORY ANSWER
- KPIs: exactly the required totals — all types, building types, vendor types — and nothing about the register. Three tiles.
- One group per trade category per scope, and the group owner carries the scope so no two groups share a name: "Building — Fire", "Vendor — Fire", never a bare "Fire". The UI charts groups by owner name.
- Order groups by scope (Building first, then Vendor) and within a scope by how many types the trade carries, densest first — fire before energy before a single-type trade. Not alphabetically.
- Name every type once with the regulation as the pack records it — full title and SI number where given ("Regulatory Reform (Fire Safety) Order 2005 (SI 2005/1541)"), abbreviating only on a second mention inside the same group — and its renewal interval in words ("every 6 months", "monthly", "no fixed cycle in the pack").
- Where a type applies only in some buildings (gas supply, lift, refrigerant plant, higher-risk building, public building over 250m²), say so as its own bullet in that group, not folded into the headline.
- A group headline says what the group is, in one clause with a fact in it: "Five fire-safety duties, the densest group in the pack and the shortest cycles", not "5 building-side fire duties".
