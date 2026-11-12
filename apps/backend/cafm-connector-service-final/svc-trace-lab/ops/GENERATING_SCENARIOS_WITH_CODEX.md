# Generating scenario questions for Hoistra (with expected outcomes from SPEC.md)

Instructions for a coding agent (Codex). Give this whole file to the agent. It explains how to write a batch of test
scenarios for Hoistra's orchestrator: realistic questions (single-turn and multi-turn), each with the expected outcome
written **before** anything is asked, derived from the active SPEC.md, the domain skill files and the asker's role.

The output is one file, `scenarios.jsonl`, that Trace Lab imports. Asking the questions and reading the results is the
next step: see `ASKING_SCENARIOS_WITH_CODEX.md` in this folder.

## Read these first

All paths are under `apps/backend/cafm-connector-service-final/` in the Hoistra_Plenum repo.

| Read | For |
|---|---|
| `svc-deepagents/skills/spec/SPEC.md` | The rules an answer must follow (S-1, S-2, ...). Every scenario tests one or more of them. |
| `svc-deepagents/skills/query-builder/answer-spec.md` | The form an answer takes (written from what the tools returned, says plainly what failed). |
| `svc-deepagents/skills/<area>/SKILL.md`, `never.md`, `vocabulary.md`, `domain_*_knowledge.md` | What each area knows and must never do. Areas: `compliance`, `wo-engine`, `contract-performance`, `energy-intelligence`, `udr` (buildings, assets). |
| Trace Lab `GET /backend/trace-lab/api/trace-lab/eval/spec` | The section ids of the specification Trace Lab has loaded. Use these ids in `spec_sections`. Today: `s_1_overdue_reported_as_none` ... `s_11_incorrect_date_range_filtering`. |

A section id is the heading made lower-case, with everything that is not a letter or digit turned into `_`:
`### S-7 · grouping_read_as_filter` becomes `s_7_grouping_read_as_filter`.

## The method: tuples

Do not write questions one by one. Build a grid of tuples, then write one scenario per tuple:

```
(rule, area or areas, role, type, data condition)
```

| Part | Values |
|---|---|
| rule | Each SPEC rule (S-1 ... S-11). A scenario may test two related rules. |
| area | `compliance`, `wo_engine`, `contract_performance`, `energy_intelligence`; two areas for multi-domain. |
| role | `general_user`, `portfolio_manager`, `property_manager`, `compliance_officer` |
| type | `single_domain`, `multi_domain`, `multi_turn`, `failure` |
| data condition | records exist / no records exist / records conflict / the role may not see them |

Mix for a batch of N (rounded): 40% single_domain, 24% multi_domain, 20% multi_turn, 16% failure. For 30: 12, 7, 6, 5.
For 200: 80, 48, 40, 32.

Coverage, checked before handing over:
- every SPEC rule is tested by at least 3 scenarios, at least 1 of them multi-turn;
- every area and every role appears;
- at least 1 scenario per rule where the records needed **do not exist** (the answer must say so plainly, not invent).

Example grid rows:

| id | rule | area | role | type | data |
|---|---|---|---|---|---|
| SD-S10-001 | S-10 | compliance | compliance_officer | single_domain | records exist |
| MD-S02-001 | S-2 | compliance + wo_engine | property_manager | multi_domain | records exist |
| MT-S05-001 | S-5, S-4 | compliance, wo_engine | portfolio_manager | multi_turn | records exist |
| FL-S03-001 | S-3 | contract_performance | portfolio_manager | failure | no records exist |

## Writing the question

- Write it as a real person in that role would type it: short, specific, one intent per turn. "Which certificates at
  Bishopsgate Tower are due for renewal?", not "Apply the renewal band rule to certificates".
- Never name the rule, the engine or a field in the question. The question tests whether Hoistra finds them.
- Use names from the company's own records (buildings, vendors, asset references) when you know them. Ask the person
  for a few if you do not. A question about data the company does not have only tests the "nothing on record" path.
- Dates: say the period plainly ("in March 2027", "in the next 60 days", "last month"). For S-11, use an exact month and
  year.
- Vary the wording across scenarios that test the same rule. Do not reuse a question.
- At most 4000 characters per turn, at most 6 turns per scenario.

## Multi-turn patterns

Every turn after the first depends on the one before. Use these patterns:

| Pattern | Example turns | What the follow-up must keep |
|---|---|---|
| Narrow | "List all open work orders by building." then "Which of those at Bishopsgate Tower are overdue?" | the set from turn 1, cut to one building |
| Carry | "...overdue at Bishopsgate Tower?" then "Who is the vendor on each of them?" | exactly the same records, nothing added or dropped |
| Count then name | "How many vendors are blocked right now?" then "Name them and say why each one is blocked." | the count; the list has that many lines |
| Pivot | "Which contracts renew in the next 90 days?" then "Which of those vendors have open work orders?" | the vendors from turn 1, a new area |
| Correct | "Show overdue PPM visits." then "I meant only statutory ones." | the original question, narrowed by the correction |

For a follow-up that should answer from the previous turn without a new call, the expected plan is `[]`.

## Writing the expected outcome (before anything is asked)

For each turn, from the rule text, the area's skill files and the role:

| Field | How to fill it |
|---|---|
| `plan` | The areas that must be called, in order. `depends_on` lists the calls whose output this call needs (S-2: the work-order lookup needs the inspections first). `[]` for a follow-up answered from the previous turn. |
| `answer_format` | `success`; `partial_success` when part of it must fail; `denial` when the role may not see the data; `failure` when nothing can be answered. |
| `failure_handling` | `null`, or the state the answer must report: `zero_results`, `not_recorded`, `unavailable`, `tool_failed`, `partial_failure`, `total_failure`, `authorization_failure`, `evidence_conflict`, with the failing call ids. |
| `must_not_include` | Phrases the rule forbids, taken from its "never" clauses: S-1 `nothing is due`; S-3 `data is unavailable`, `not available`. |
| `must_include` | Only phrases that must appear whatever the data is. Usually empty. |
| `answer` | The expected answer **in words**: its shape and what it must say ("one line per certificate: certificate, building, expiry date, days to expiry; lapsed ones included"). Values only when you read them from the records. |
| `domain_rules` (per scenario) | The text of each rule tested, copied word for word from SPEC.md, with its id (`S-7`). The domain judge checks the answer against these. |

Never write an expected outcome from an answer Hoistra gave. Expected outcomes come from the specification and the
records, so that a wrong answer shows as wrong.

## Rule cheat sheet (SPEC.md as of 12 Nov 2026)

Re-read SPEC.md before using this: rules change, and Trace Lab flags scenarios whose rules changed.

| Rule | Questions that test it | The expected answer must |
|---|---|---|
| S-1 overdue_reported_as_none | overdue certificates or inspections | give due date, days overdue, and any work order raised; never "nothing is due" |
| S-2 for_each_missing_gaps | "is a work order booked for each inspection due" | count an inspection booked only by a work order for that inspection, never a repair on the same asset |
| S-3 wrong_work_order_counted | contract renewals, contract dates, anything another engine holds | answer from the engine that holds it (contract performance); never say the data is unavailable |
| S-4 list_answer_as_paragraph | any "which" or "list" question | answer as a short list (item, reason, since when), not a paragraph |
| S-5 blocked_and_at_risk_conflated | blocked vendors, vendor counts | one blocked count (block flag), vendors with a lapsed accreditation reported separately, named |
| S-6 draft_treated_as_block | lapsed or blocked counts | count drafts or items awaiting sign-off as tasks awaiting confirmation, not lapses or blocks |
| S-7 grouping_read_as_filter | "by building", "per vendor" | group by it, never filter; list every record; "No building on record" for records with none |
| S-8 next_due_date_ignored | "due in <month>" | use the expiry date, or the next-due date when there is no expiry date |
| S-9 redundant_filter_matches_all | "lapsed or expiring within N days" | one condition, days to expiry ≤ N, lapsed included |
| S-10 stale_status_field_trusted | "due for renewal" | the 31 to 60 days band from the expiry date, not the stored status |
| S-11 incorrect_date_range_filtering | "in March 2027", "in Q1 2027" | only that exact month and year |

## Output: scenarios.jsonl

One scenario per line (JSON, UTF-8). Id scheme: `<SD|MD|MT|FL>-<rule>-<nnn>`, e.g. `MT-S05-001`. Ids are 1 to 60
letters, digits, dots, dashes or single underscores. The full field list is in `ASKING_SCENARIOS_WITH_CODEX.md`.

Three examples (each is one line in the file):

```json
{"scenario_id": "SD-S10-001", "type": "single_domain", "role": "compliance_officer", "space": "comp", "spec_sections": ["s_10_stale_status_field_trusted"], "scope": {}, "faults": [], "grounded_on": [], "domain_rules": [{"id": "S-10", "text": "\"Due for renewal\" is the platform's renewal band: 31–60 days to expiry. Filter on days to expiry (gte 31, lte 60), never on the stored status — the scan that sets it may not have run."}], "turns": [{"message": "Which certificates are due for renewal?", "expected": {"plan": [{"call_id": "c1", "domain": "compliance", "tool": null, "params": {}, "depends_on": []}], "failure_handling": null, "answer_format": "success", "must_include": [], "must_not_include": [], "answer": "The certificates with 31 to 60 days to expiry, worked out from the expiry date and not the stored status, each with its expiry date and days to expiry; or, if none, says so plainly."}}]}
{"scenario_id": "MD-S02-001", "type": "multi_domain", "role": "property_manager", "space": "ops", "spec_sections": ["s_2_for_each_missing_gaps"], "scope": {}, "faults": [], "grounded_on": [], "domain_rules": [{"id": "S-2", "text": "A statutory inspection is covered only by a work order for that inspection. A repair or other work order on the same asset never counts as the inspection booked."}], "turns": [{"message": "For each statutory inspection due in the next 60 days, is a work order booked for it?", "expected": {"plan": [{"call_id": "c1", "domain": "compliance", "tool": null, "params": {}, "depends_on": []}, {"call_id": "c2", "domain": "wo_engine", "tool": null, "params": {}, "depends_on": ["c1"]}], "failure_handling": null, "answer_format": "success", "must_include": [], "must_not_include": [], "answer": "One line per statutory inspection due in the next 60 days: booked (a work order for that inspection) or not booked. A repair on the same asset is not counted as booked."}}]}
{"scenario_id": "MT-S05-001", "type": "multi_turn", "role": "portfolio_manager", "space": "full", "spec_sections": ["s_5_blocked_and_at_risk_conflated", "s_4_list_answer_as_paragraph"], "scope": {}, "faults": [], "grounded_on": [], "domain_rules": [{"id": "S-5", "text": "An answer must not show this failure: Mixes vendors explicitly blocked (block flag, as on the Vendors page) with vendors holding a lapsed accreditation, giving two different blocked counts or pushing the user to check the list themselves. From note on R33-12."}], "turns": [{"message": "How many vendors are blocked right now?", "expected": {"plan": [{"call_id": "c1", "domain": "compliance", "tool": null, "params": {}, "depends_on": []}], "failure_handling": null, "answer_format": "success", "must_include": [], "must_not_include": [], "answer": "One count of vendors blocked by the block flag; vendors with only a lapsed accreditation are not in it."}}, {"message": "Name them and say why each one is blocked.", "expected": {"plan": [], "failure_handling": null, "answer_format": "success", "must_include": [], "must_not_include": [], "answer": "A short list of the same blocked vendors, one line each: vendor, why blocked, since when. As many lines as the count given before; never a different count."}}]}
```

## Check before handing over

1. Every line parses as JSON and has the fields above. Every turn has an `expected`.
2. Every id in `spec_sections` is in `GET .../eval/spec`.
3. The coverage rules above hold (print a table: rule × type count).
4. No two questions are the same; no question names a rule, engine or field.
5. No personal data, passwords or tokens anywhere in the file.
6. Trace Lab accepts the file without storing it:
   `POST .../eval/scenarios/import?dry_run=true` with the file returns `{"scenarios": N}`; a 422 lists every bad line.

Then hand the file to the asking step (`ASKING_SCENARIOS_WITH_CODEX.md`).
