# Compliance question bank — 120 questions by shape

Purpose: exercise every combination the compliance agent must handle, and check that the doc
router loads the right documents and the sub-agent calls the right tools. Kept under `eval/` so
the `*.md` glob that builds the agent's contract never loads it.

Columns: **Shape** is the answer shape from `core.md` §5. **Docs** are the topic documents the
router should add to core. **Tools** are the calls the sub-agent should make. **Answer form** is
what a correct answer looks like; where the current register's answer is known and verified by
direct query (2026-09-03) it is given in brackets.

Traps are marked ⚠ — the question is designed to tempt a wrong tool, filter or scope.

## A. Country pack — what the law requires (statutory scope, never held/missing)

| # | Question | Shape | Docs | Tools | Answer form |
|---|---|---|---|---|---|
| A1 | Which certificates must a UK building owner hold? | law | taxonomy, answering | list_country_pack(UK, Building), get_pack_facts | 27 types across 11 trades, by trade, with regulation and interval [27 / 11] |
| A2 | What accreditations must a contractor hold to do regulated work in the UK? | law | taxonomy | list_country_pack(UK, Vendor), get_pack_facts | 28 types across 10 trades [28 / 10] |
| A3 | What are the building and vendor regulations and certifications mandatory in UK building management? | law | taxonomy | pack both scopes, pack_facts | two parts, Building then Vendor, 55 total [55 = 27 + 28] |
| A4 | How many certificate types does the UK pack require, and how are they split? | one fact | taxonomy | get_pack_facts | one sentence [55: 27 building, 28 vendor] |
| A5 | Which fire-safety certificates does the pack require for a building, and how often? | law | taxonomy | list_country_pack(UK, Building, Fire) | five types with intervals [EL monthly; alarm 6m; fire door 6m; FRA 12m; sprinkler 60m] |
| A6 | Is Gas Safe registration a building certificate or a vendor accreditation? | one fact | taxonomy | list_country_pack(code GAS_SAFE) | vendor; CP17 is the building-side gas certificate |
| A7 | ⚠ Is LOLER a building duty or a contractor accreditation? | one fact | taxonomy | list_country_pack | both exist: LOLER (building) and LOLER competent person (vendor); say which is which |
| A8 | Which building duties apply only to higher-risk buildings? | law | taxonomy | list_country_pack(UK, Building) | Safety Case Report, HRB Registration; applicability sentence |
| A9 | What does the pack require for legionella control, on both sides? | law | taxonomy | list_country_pack | building: cold water tank, monitoring, L8 risk; vendor: BTEC legionella, LCA |
| A10 | Which pack types have the longest renewal cycles? | which one | taxonomy | list_country_pack | EPC 120 months, then the 60-month group [EPC; TM44, sprinkler, PA1/2/6, C&G 2382] |
| A11 | Which pack types have no fixed renewal cycle? | law | taxonomy | list_country_pack | list those with null frequency, say "no fixed cycle in the pack" |
| A12 | Which regulation requires a fire risk assessment, and how often? | one fact | taxonomy | list_country_pack(code FRA) | RRO 2005 (SI 2005/1541), every 12 months |
| A13 | What electrical certificates does the pack require and which accreditation must the contractor hold? | law | taxonomy | list_country_pack(Electrical, both scopes) | EICR/EIC on the building side; NICEIC/NAPIT/C&G 2382 on the vendor side |
| A14 | Which duties apply only where there is a gas supply? | law | taxonomy | list_country_pack(Gas) | CP17, boiler service; applicability sentence |
| A15 | ⚠ Which certificates must a UK building owner hold, and how many do we have? | law + lifecycle, two parts | taxonomy, answering | pack + both lists | two sections: statutory list; then what is on file. Never merge them |
| A16 | ⚠ Which certificates must a UK owner hold? Do not tell me what we have. | law | taxonomy | pack only | no held/missing wording anywhere, no KPI about the register |
| A17 | Does the US pack differ from the UK pack for fire alarm servicing? | law | taxonomy | list_country_pack(US) and (UK) | compare the two rows or say the US pack is not seeded |
| A18 | Which trade has the most required building types? | which one | taxonomy | get_pack_facts | one trade named with its count; ties named |
| A19 | Which issuing bodies does the pack name for asbestos work? | law | taxonomy | list_country_pack(Asbestos) | HSE licence, P402/403/404 |
| A20 | What accreditation must a lift contractor hold? | one fact | taxonomy | list_country_pack(Vendor, LOLER/Lifts) | LOLER competent person / LEIA |

## B. Compliant and current

| # | Question | Shape | Docs | Tools | Answer form |
|---|---|---|---|---|---|
| B1 | Which building certificates are in date? | lifecycle | answering, recipes | list_building_certificates | rows with status Current [RT5 TM44 only, to 2031-05-18] |
| B2 | Which vendor accreditations are current today? | lifecycle | answering | list_vendor_accreditations | Current rows, plus a sentence on those due within 90 days [7 Current + 5 due/expiring] |
| B3 | ⚠ Which certificates are compliant but not lapsed? | lifecycle | answering | both lists | Current rows only; no Lapsed row may appear |
| B4 | Is RT5 compliant? | particular rows | answering, recipes | list_building_certificates(building_name="RT5") | one certificate, current; 26 of 27 types unevidenced is completeness, not non-compliance |
| B5 | Does AIB hold a valid asbestos removal licence? | particular rows | recipes | list_vendor_accreditations(vendor_name="AIB") | yes, to 2026-12-30, forensics pass, block state Clear |
| B6 | Which vendors have no lapsed accreditation at all? | lifecycle | answering | list_vendor_accreditations | vendors whose rows are all non-lapsed [6, including two that are blocked or forged — say so] |
| B7 | How many certificates are on file, by scope? | one fact | core | count or both lists | [26: 8 building, 18 vendor] |
| B8 | Which current certificates were verified as genuine? | authenticity + lifecycle | answering | both lists | Current and forensics pass |
| B9 | Which vendors are fully clear: current, genuine, not blocked? | lifecycle + authenticity | answering | list_vendor_accreditations | intersection of the three; name the excluded reasons |
| B10 | Which building certificates were renewed in the last 12 months? | lifecycle | answering | list_building_certificates | issue_date within 12 months |
| B11 | Which current certificates expire beyond 2028? | lifecycle | answering | both lists | expiry after 2028-12-31 [Kurt J. Lesker ISO 9001 2029; RT5 TM44 2031] |
| B12 | ⚠ Show compliant vendors using status=compliant | lifecycle | core | list_vendor_accreditations no filter | agent must not pass a filter value "compliant" |

## C. Lapsed, expiring, at risk

| # | Question | Shape | Docs | Tools | Answer form |
|---|---|---|---|---|---|
| C1 | Which building certificates have expired? | lifecycle | answering | list_building_certificates | Lapsed rows sorted by expiry [6 rows: FRA 2006, two unlinked EICR 2012, DEC 2013, Building 5 EICR 2015, EL insurance 2022] |
| C2 | Which vendor accreditations are lapsed? | lifecycle | answering | list_vendor_accreditations | [6 rows] |
| C3 | What expires in the next 90 days? | lifecycle | answering, renewals | both lists | 0 ≤ days ≤ 90, soonest first [5 rows: EPA 608 29d, NICEIC 41d, Apex PL 58d, SafeLift PL 88d, BrightSpark PL 88d] |
| C4 | What expires in the next 30 days? | lifecycle | answering | both lists | [1: Apex EPA 608] |
| C5 | Which certificate expires next? | which one | answering | both lists | one row [Apex Mechanical Contractors EPA 608, 2026-10-02] |
| C6 | Which certificate has been expired the longest? | which one | answering | both lists | [AN Other House FRA, 7,277 days] |
| C7 | Which vendor accreditations lapsed this year? | lifecycle | answering | list_vendor_accreditations | expiry in current year [ProudCastle Jan, Cleansing Mar, SafeLift Apr, Apex Gas Safe Aug] |
| C8 | What is at risk but not yet lapsed? | lifecycle | answering | both lists | not lapsed and ≤ 90 days; say "at risk" not "expired" |
| C9 | ⚠ Which certificates are overdue? | lifecycle | answering | both lists | Overdue is a status word; note the row that shows Overdue while 29 days from expiry |
| C10 | Which lapsed certificates were never chased? | lifecycle + renewals | renewals | both lists | Lapsed with empty email_sent_status [9 rows] |
| C11 | Has the Town Hall DEC renewal been chased? | particular rows | renewals, recipes | list_building_certificates(building_name="Town Hall") | email status and renewal_initiated as recorded [dry run email, not initiated] |
| C12 | Which lapsed building certificates are in draft? | lifecycle | answering | list_building_certificates | Lapsed and draft=true |
| C13 | What expires this month? | lifecycle | answering | both lists | expiry within the calendar month |
| C14 | Which US-scheme certificates are expiring soon? | lifecycle | answering | both lists | country_code US and 0 ≤ days ≤ 90 [Apex EPA 608] |
| C15 | Which vendors have both a lapsed and a current accreditation? | lifecycle | answering | list_vendor_accreditations | per vendor [Apex Mechanical Services, SafeLift] |
| C16 | Order all lapsed certificates from oldest lapse to newest. | lifecycle | answering | both lists | one ranked list across scopes with the scope on each row |
| C17 | How many days until the next building certificate lapses? | one fact | core | list_building_certificates | none due: all building rows are lapsed or long-dated; say so |
| C18 | Which at-risk certificates have no renewal started? | renewals | renewals | both lists | ≤ 90 days and renewal_initiated false |

## D. Blocked vendors, renewals, next actions

| # | Question | Shape | Docs | Tools | Answer form |
|---|---|---|---|---|---|
| D1 | Which vendors are blocked, and why? | blocked | renewals, answering | list_vendor_accreditations(risk_filter="blocked") | 7 vendors, reason from each one's lapsed or forged row [Joe COI; Arcus ISO 14001; ProudCastle SP203-1; Cleansing PL; SafeLift LOLER; Apex Gas Safe; Kurt J. Lesker forged ISO 9001] |
| D2 | How many vendors are blocked? | one fact | core | summary risk_dashboard | [7] |
| D3 | ⚠ Are any vendors blocked? Use status=Blocked. | blocked | core | risk_filter="blocked" | agent must not pass status="Blocked" |
| D4 | Can we instruct Apex Mechanical Services for gas work? | blocked + particular | renewals, recipes | list_vendor_accreditations(vendor_name=…) | no: Gas Safe lapsed 2026-08-31, vendor blocked; who else holds Gas Safe |
| D5 | Which vendor can lawfully do the Building 5 EICR re-inspection? | which one | recipes, domain | list_vendor_accreditations(trade_category="Electrical") | the NICEIC holder [BrightSpark, current, due 2026-10-14] |
| D6 | Which blocked vendors have live work? | cross-domain | cross-domain, renewals | blocked list + wo_engine | your half: the blocked list; say the WO join is the other half |
| D7 | Which vendors should we not assign, by trade? | blocked | renewals | blocked list | grouped by trade [gas: Apex Mech Services; lifts: SafeLift; fire: ProudCastle; general: Arcus, Cleansing, Kurt, Joe] |
| D8 | What should we do first this week? | risk | domain, renewals | summary + both lists | one or two actions ranked by consequence [FRA at AN Other House; forged EICR at Building 5] |
| D9 | Draft a renewal email for certificate 104021. | renewals | renewals | draft_certificate_renewal(104021) | the draft goes to Approvals; never invent the email text |
| D10 | Renew every lapsed vendor accreditation. | renewals | renewals | draft_certificate_renewal per row | one draft per lapsed row; no work orders |
| D11 | ⚠ Raise a work order for the lapsed FRA. | renewals | renewals, never | none | refuse the WO; offer the booking request and email draft |
| D12 | What is waiting on the property manager? | pending | answering | both lists (draft=true) + approvals | draft rows and open approvals as PM decisions [14 draft rows] |
| D13 | Which blocked vendors would be cleared by one renewal? | blocked | renewals | blocked list | vendors whose only failing row is one lapsed accreditation |
| D14 | Why is Kurt J. Lesker blocked when its ISO 9001 is current? | particular + authenticity | recipes, domain | list_vendor_accreditations(vendor_name="Kurt Lesker") | forged (score 94) current certificate; a forged document evidences nothing |
| D15 | Which vendors have a lapsed insurance certificate? | lifecycle | answering | list_vendor_accreditations(trade_category="insurance") | [Joe Facility-User, Cleansing Service Group] |
| D16 | Which vendors lack employers' liability insurance on record? | lifecycle | answering | list_vendor_accreditations | no CONTRACTOR_EL_INSURANCE rows: all vendors; say what "no record" means |

## E. Named company, building, trade (matching)

| # | Question | Shape | Docs | Tools | Answer form |
|---|---|---|---|---|---|
| E1 | AIB Solutions — status of their certificates? | particular | recipes | both lists vendor_name="AIB Solutions" | holds one current licence; issued nothing |
| E2 | Show all certificates belonging to RDM Electrical Services Ltd. | particular | recipes | both lists vendor_name=… | holds none; issued Building 5 EICR (lapsed 2015, forged 100) |
| E3 | rdm electrical certificates | particular | recipes | same | same answer; report the register's spelling |
| E4 | R.D.M. Electrcal — anything on file? | particular | recipes | same | same; initials and typo tolerated |
| E5 | What does C & H Fire hold, and is anything suspect? | particular + authenticity | recipes | vendor_name="C & H Fire" | two BAFE rows, both current, both suspect (65, 47), neither forged |
| E6 | Kurt Lesker — certificates? | particular | recipes | vendor_name="Kurt Lesker" | one row, current, forged 94, vendor blocked |
| E7 | ⚠ Apex Mechanical — what do they hold? | particular | recipes | vendor_name="Apex Mechanical" | two companies match; keep them apart and say so |
| E8 | Brightspark Electrcal accreditations | particular | recipes | vendor_name=… | NICEIC due for renewal, public liability |
| E9 | Certificates for Building 5 | particular | recipes | building_name="Building 5" | one EICR, lapsed, forged; do not return RT5 |
| E10 | building5 certs | particular | recipes | building_name="building5" | same |
| E11 | What does Bishopsgate hold? | particular | recipes | building_name="bishopsgate" | EL insurance lapsed 2022, suspect 37 |
| E12 | the town hall — anything lapsed? | particular | recipes | building_name="the town hall" | DEC lapsed 2013, genuine |
| E13 | ⚠ Certificates for Building 9 | particular | recipes | building_name="Building 9" | no match: all rows returned with names; answer "not on record", list the buildings that exist |
| E14 | Unknown Vendor Ltd — status? | particular | recipes | vendor_name=… | no match in either scope: not on record; offer closest names |
| E15 | Fire certificates on file | trade | recipes | both lists trade_category="fire" | Fire rows both scopes, counts per scope |
| E16 | fire safety certs for buildings | trade | recipes | building list trade_category="fire safety" | maps to Fire only, not H&S |
| E17 | Which electricians do we have? | trade | recipes | vendor list trade_category="electric" | Electrical vendor rows |
| E18 | lift contractors | trade | recipes | vendor list trade_category="lifts" | LOLER category [SafeLift] |
| E19 | air conditioning accreditations | trade | recipes | vendor list trade_category="air conditioning" | HVAC [ECO STAR licence; Apex EPA 608] |
| E20 | ⚠ plumbing certificates | trade | recipes | both lists trade_category="plumbing" | no match: say the category does not exist in the register and list the ones that do |
| E21 | Which companies issued certificates for our buildings? | particular | recipes, answering | list_building_certificates | distinct vendor_name on building rows [RDM Electrical, AN Other (Holdings), Manchester City Council, Vital Energy, ZZ Test Contractor] |
| E22 | Which vendor holds more than two certificates? | count | recipes | list_vendors_by_certificate_count(2, gt) | [Apex Mechanical Services, 3] |
| E23 | Which vendors hold exactly two? | count | recipes | list_vendors_by_certificate_count(2, eq) | [SafeLift, Apex Contractors, BrightSpark, C&H] |
| E24 | How many certificates carry an insurance risk flag? | one fact | recipes | count_compliance_certificates(insurance_risk_flag) | the count field |
| E25 | How many certificates did Vital Energy issue? | one fact | recipes | count_compliance_certificates(issuer/vendor) | the count field |

## F. Forensics and forgery

| # | Question | Shape | Docs | Tools | Answer form |
|---|---|---|---|---|---|
| F1 | Show all forged documents at building level and vendor level separately. | authenticity | recipes, answering | both lists no filter | two lists by owner, score and reason each [Building 4: Building 5 EICR, two unlinked EICR, NFPA 72; Vendor 3: Joe COI, Apex EPA 608, Kurt ISO 9001] |
| F2 | Which documents are suspect but not forged? | authenticity | recipes | both lists | review verdict only [5: Bishopsgate EL, Arcus, Cleansing, C&H ×2] |
| F3 | How many documents are suspicious or potentially forged? | one fact | recipes | both lists | fail + review shown as the sum [7 + 5 = 12] |
| F4 | Which vendor has a forged certificate that is still in date? | authenticity + lifecycle | recipes | list_vendor_accreditations | [Kurt J. Lesker ISO 9001; Apex Mechanical Contractors EPA 608] |
| F5 | What is the highest forensics score on file? | which one | recipes | both lists | [100, three EICRs] |
| F6 | Which forged documents cannot be attributed to a building? | authenticity | recipes | list_building_certificates | unlinked fail rows [two EICR, NFPA 72] |
| F7 | Which certificates have never been assessed by forensics? | authenticity | recipes | both lists | empty verdict rows, counted apart [9 vendor rows] |
| F8 | Is the AN Other House FRA genuine? | particular + authenticity | recipes | building_name="AN Other House" | pass, 27, with the findings quoted; still a draft |
| F9 | Which vendors have suspicious documents? | authenticity | recipes | list_vendor_accreditations | fail and review grouped by vendor, labelled from each row's own verdict |
| F10 | Re-check the forensics on the Building 5 EICR. | authenticity | recipes | run_document_forensics(document_id) | re-run one document only |
| F11 | ⚠ List forged certificates using risk_filter=forged | authenticity | core | both lists no filter | forgery is not a lifecycle filter |
| F12 | Which forged documents are on Tier 1 life-safety duties? | authenticity + risk | domain, recipes | both lists | EICRs and NFPA 72; explain why a forged Tier 1 document is worse than a lapse |
| F13 | What reasons did forensics give for the Kurt J. Lesker ISO 9001? | particular | recipes | vendor_name="Kurt Lesker" | authenticity_warning quoted |
| F14 | Which genuine documents are lapsed anyway? | authenticity + lifecycle | recipes | both lists | pass and lapsed [Town Hall DEC, ProudCastle SP203-1, AN Other House FRA] |
| F15 | Which building has the most forged documents? | which one | recipes, answering | list_building_certificates | Building 5 with one; three unlinked cannot be ranked, say so |

## G. Risk and priority (ranked by consequence)

| # | Question | Shape | Docs | Tools | Answer form |
|---|---|---|---|---|---|
| G1 | Which building has the highest risk in terms of expired certifications? | which one | domain, answering | summary + list_building_certificates | AN Other House (FRA, Tier 1); Building 5 runner-up (EICR, forged); others as sentences; name the ranking order |
| G2 | Which vendor is the highest risk? | which one | domain | list_vendor_accreditations | a Tier 1 lapse first [Apex Mechanical Services Gas Safe or SafeLift LOLER]; justify by consequence |
| G3 | Which certificates are high risk right now? | lifecycle | answering | summary | risk_badge High [1: Apex EPA 608, 29 days, forged] |
| G4 | Which certificates are medium risk? | lifecycle | answering | summary | [2: BrightSpark NICEIC and public liability] |
| G5 | Which lapsed certificates carry a life-safety consequence? | risk | domain | both lists | Tier 1 lapsed [FRA, three EICRs, SP203-1, LOLER, Gas Safe] |
| G6 | Which lapses are only energy or administrative? | risk | domain | both lists | Tier 2 and 3 [DEC; EL insurance; ISO 14001; PL insurance; COI] |
| G7 | Bishopsgate Tower or Town Hall — which is worse off? | which one | domain | list_building_certificates | Bishopsgate (Tier 2, suspect) above Town Hall (Tier 3, genuine) despite fewer days |
| G8 | ⚠ Which building has been non-compliant the longest? | which one | answering | list_building_certificates | this is duration, not risk: AN Other House FRA; do not re-rank by tier |
| G9 | Rank all buildings by risk. | risk | domain, answering | list_building_certificates | ranked list with the basis; unlinked rows counted apart |
| G10 | What is the single most urgent action across the estate? | which one | domain, renewals | summary + lists | one action, with the reason |
| G11 | Which vendors pose a life-safety risk if used today? | risk | domain | blocked list | Tier 1 trades among blocked: gas, lifts, fire |
| G12 | Is a lapsed DEC a safety issue? | one fact | domain | none needed beyond the row | no: Tier 3 energy duty, fines not injury |
| G13 | How has vendor risk changed over time? | portfolio | answering | compliance_risk_snapshots via summary | snapshots trend if available |
| G14 | Which building would fail an audit first? | which one | domain | coverage + list | the Tier 1 lapse first, coverage as context only |

## H. Coverage and gaps

| # | Question | Shape | Docs | Tools | Answer form |
|---|---|---|---|---|---|
| H1 | Which building has the worst compliance coverage? | which one (tie) | answering | get_compliance_coverage | five tied at 1 of 27; list gaps per building; exclude the portfolio bucket from the ranking |
| H2 | What is Building 5 missing? | gaps | answering | get_compliance_coverage | 26 missing types by name |
| H3 | Which building types have nothing on record anywhere? | gaps | answering | pack + building list | [22 of 27; fire: emergency lighting, alarm service, fire door, sprinkler] |
| H4 | Which buildings have no gas certificate, and does it matter? | gaps | answering, taxonomy | building list | none on file anywhere; applies only where there is a gas supply |
| H5 | ⚠ What is our compliance percentage? | one fact | answering | coverage | refuse the percentage framing; "N of 27 pack types on file" is completeness |
| H6 | Which fire duties are unevidenced at AN Other House? | gaps | answering | coverage | the missing Fire types for that building |
| H7 | Which vendor accreditation types has no vendor got? | gaps | answering | pack + vendor list | vendor pack types with zero rows |
| H8 | Which certificates are not linked to any building? | gaps | tables (core) | list_building_certificates | rows with no site_id, site_ref or building_name [3] |
| H9 | Which certificates fall outside the UK pack? | gaps | answering | both lists + pack | codes not in the pack [9 rows: US schemes, PUBLIC_LIABILITY, SAFE_CONTRACTOR] |
| H10 | Which buildings hold anything at all? | one fact | core | coverage | the five named buildings |

## I. Portfolio and status

| # | Question | Shape | Docs | Tools | Answer form |
|---|---|---|---|---|---|
| I1 | Are we compliant? | portfolio | answering, renewals | summary + both lists | full treatment: kpis, groups worst first, actions, insights |
| I2 | Give me the compliance status across the estate. | portfolio | answering | same | same |
| I3 | Summarise vendor compliance. | portfolio (vendor) | answering | summary + vendor list | vendor scope only |
| I4 | Summarise building compliance. | portfolio (building) | answering | summary + building list | building scope only |
| I5 | What changed since the last scan? | portfolio | answering | compliance_scan_runs via summary | alerts created, blocks set |
| I6 | How many certificates are compliant, at risk, and lapsed? | one fact ×3 | answering | summary | three numbers from risk_dashboard, no table |
| I7 | ⚠ How many vendors are compliant? | one fact | core | summary | vendor_compliant [5]; do not list rows |
| I8 | Which owners have more than one problem? | portfolio | answering | both lists | owners with several failing rows [Apex Mechanical Services; SafeLift] |
| I9 | Where do issues compound each other? | portfolio | answering | both lists | correlations the data supports, or none |

## J. Cross-domain

| # | Question | Shape | Docs | Tools | Answer form |
|---|---|---|---|---|---|
| J1 | Can SafeLift do the lift job at Bishopsgate? | cross-domain | cross-domain, renewals | vendor list + wo_engine | LOLER lapsed, blocked: no; the job is the other half |
| J2 | Which lapsed-certificate buildings have open urgent work orders? | cross-domain | cross-domain | building list + wo_engine | your half, join on site |
| J3 | Does Apex Mechanical Services' SLA score reflect their blocked state? | cross-domain | cross-domain | vendor list + contract_performance | blocked caps the score at 60 |
| J4 | What does the Building 5 EICR document actually say? | cross-domain | cross-domain | document_id → doc_rag | hand off to doc_rag with the id |
| J5 | Which engineers hold a personal ticket that has expired? | cross-domain | tables | resource_skills | operative rows with days_to_expiry < 0 |

## K. Evidence, audit, approvals

| # | Question | Shape | Docs | Tools | Answer form |
|---|---|---|---|---|---|
| K1 | Prove Bishopsgate Tower's compliance for the auditor. | evidence | recipes | generate_compliance_evidence_pack | the pack, with what it will show |
| K2 | Give me AIB's vendor passport. | evidence | recipes | get_vendor_passport | the passport |
| K3 | Share SafeLift's passport with the client. | evidence | recipes | share_vendor_passport | share action; note the lapsed LOLER |
| K4 | What is in the approvals queue for compliance? | pending | answering | list_compliance_approvals | items by type and severity |
| K5 | Approve the renewal booking for the Town Hall DEC. | pending | renewals | decide_compliance_approval | the decision recorded |
| K6 | Which draft extractions need confirming? | pending | answering | both lists draft=true | as PM decisions, not record states |

## L. Traps, negatives and ambiguity

| # | Question | Shape | Docs | Tools | Answer form |
|---|---|---|---|---|---|
| L1 | ⚠ Which certificates are required and which are lapsed? | two parts | taxonomy, answering | pack + lists | two sections with distinct ids, never a duplicate id |
| L2 | ⚠ Is the register the same as the pack? | one fact | taxonomy | none | no: pack is the law, register is what is held |
| L3 | ⚠ We have no blocked vendors, right? | one fact | core | summary | contradict: 7 blocked |
| L4 | ⚠ Which building is worst? | which one | domain, answering | coverage + list | ask what "worst" means or answer both readings briefly: coverage tie; risk AN Other House |
| L5 | ⚠ Show me everything. | portfolio | answering | summary + lists | full treatment, but lead with the finding |
| L6 | ⚠ How many certificates does AN Other House hold? Include vendors. | one fact | recipes | building_name=… | one building row; vendors do not "belong" to a building — say so |
| L7 | ⚠ Which vendor certificates are lapsed for Building 5? | particular | recipes | building_name + issued rows | vendor rows have no building; answer with the issued row and explain |
| L8 | ⚠ Give me the compliance score per building. | coverage | answering | coverage | reframe as pack completeness |
| L9 | ⚠ Which building has the most expired certificates? | which one | answering | building list | count, not risk: four buildings tied at one each; unlinked rows apart |
| L10 | ⚠ Create a work order for every lapsed certificate. | renewals | never | none | refuse; offer booking requests |
| L11 | ⚠ Why did the ISO 9001 not block Kurt J. Lesker? | particular | recipes | vendor_name | it did: the vendor is blocked; correct the premise |
| L12 | ⚠ Which certificates expire in the last 30 days? | lifecycle | answering | lists | interpret as lapsed within 30 days [Apex Gas Safe, 7 days ago] |
| L13 | ⚠ Are all our fire certificates fine? | lifecycle + gaps | answering, domain | building list Fire | one FRA lapsed since 2006, four fire types unevidenced: no |
| L14 | ⚠ Which vendor issued the most building certificates? | which one | recipes | building list | count by vendor_name on building rows; ties named |
| L15 | ⚠ Find certificates for Bishopsgate Tower Ltd | particular | recipes | building_name and vendor_name | try both scopes; the building matches, no vendor does |

Total: 120 questions. Sections A to L cover: statutory scope (20), current (12), lapsed and expiring (18), blocked and renewals (16), named entities and matching (25), forensics (15), risk (14), coverage (10), portfolio (9), cross-domain (5), evidence and approvals (6), traps (15) — some sections overlap by design.

## Using the bank

1. **Dry run first, no model.** For every bracketed answer, verify against the register with a direct query before judging a model. Ground truth drifts as the register changes.
2. **Router check.** Log which docs the doc router chose per question and compare with the Docs column. A miss on a ⚠ question is a routing defect; a miss elsewhere is a catalogue wording problem in `TOPIC_DOCS`.
3. **Tool check.** Compare the sub-agent's calls with the Tools column. A status filter where the column says "no filter" is a defect.
4. **Shape check.** A one-fact question answered with tiles, or a which-one question with a third group, fails regardless of the facts.
5. **Score per shape**, not per question, so the weakest shape is visible.
