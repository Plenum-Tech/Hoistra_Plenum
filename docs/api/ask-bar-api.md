# The Ask bar, on all four pages

Energy, Assets, Maintenance and the inspection-reports panel each have an Ask bar. All four
are served, and all four work the same way.

| Page | Endpoint | Service |
|---|---|---|
| Energy | `POST /api/energy/ask` with `page: "energy"` | svc-operations-intelligence |
| Assets | `POST /api/energy/ask` | svc-operations-intelligence |
| Maintenance | `POST /api/maintenance/ask` | svc-work-order-management |
| Inspection reports panel | `POST /api/maintenance/ask` with `page: "inspection"` | svc-work-order-management |

Chips come from the API too — `GET /api/energy/ask/suggestions` and
`GET /api/maintenance/ask/suggestions?page=maintenance|inspection` — so adding a question does
not need a frontend release.

---

## How it answers, and why it answers that way

**Every answer is composed from rows a named engine function returned.** Nothing here writes
SQL from the question, and no figure is generated — the sentence is assembled from the same
numbers the panels show, and `source.endpoint` names where they came from, so any answer can be
checked against the screen it came from.

That is deliberate. A question turned into SQL by a model can quietly widen its own scope, and
a figure written by a model is a figure nobody can trace. Routing to functions that are already
building-scoped means **the Ask bar inherits the boundary for free**: it cannot answer about a
building you are not allocated to, not because it checks, but because the function underneath
it cannot read one.

**Matching is by pattern, not by model.** Every chip matches exactly; reasonable paraphrases
match too. When nothing matches with confidence, it says so and lists what it can be asked. A
confident answer to a question nobody asked is worse than no answer.

A model-based classifier could sit in front of this later and pick the skill. The skills would
not change, because they are where the truthfulness lives.

---

## Request and response

```bash
POST /api/maintenance/ask
{"question": "Which PPM contracts are behind plan?", "page": "maintenance"}
```

```jsonc
{
  "ok": true,
  "understood": true,
  "question": "Which PPM contracts are behind plan?",
  "intent": "ppm_behind_plan",
  "matched": "Which PPM contracts are behind plan?",
  "confidence": 1.0,
  "answer": "1 of 3 contracts is behind plan year to date: SafeLift Engineering Ltd (9 of 12 visits, 3 missed).",
  "count": 1,
  "data": [ /* the contract rows themselves, same shape as /ppm/contracts */ ],
  "source": {"endpoint": "/api/maintenance/ppm/contracts", "scope": "your buildings only"},
  "followups": [{"id": "statutory_decisions", "question": "Which decisions are statutory?"}]
}
```

`?building_id=` narrows further and is 403 if you are not allocated to it, like every other
route.

### When it does not understand

```jsonc
{
  "ok": true,
  "understood": false,
  "confidence": 0.0,
  "answer": "I cannot answer that from the maintenance records. I answer from what is on record rather than guessing at what you meant, so here is what I can be asked.",
  "closest": [],
  "can_answer": [{"id": "...", "question": "...", "page": "maintenance"}]
}
```

Render `can_answer` as chips. `understood: false` is a normal 200 — it is an answer, not an
error.

---

## What each page can be asked

**Energy** — `intent` in brackets:

| Question | Reads |
|---|---|
| Which markets drive the excess cost? `markets_excess_cost` | buildings grouped by market, ranked on kWh above each building's own reference |
| Which buildings are worst against their own pack? `worst_against_pack` | each building against its own country's standard |
| Where does the data route limit what I can see? `data_route_limits` | the detection rules that cannot arm, and the route each needs |

Excess is ranked in **kWh, not money**. Every market prices differently and the profiles hold
those terms as contract wording — "28.4p/kWh contracted", "$1.40/therm billed in therms" — not
as a number to multiply. Parsing a rate out of that prose would produce a confident figure
nobody could check, so the answer gives the exact energy and names the stated terms beside it.

**Assets** — `intent` in brackets:

| Question | Reads |
|---|---|
| Which assets should I inspect before winter? `inspect_next` | condition engine, threats then watches |
| What is the work order backlog on threat assets? `threat_backlog` | threats joined to open orders |
| Which sections have gone over reference since last month? `sections_over` | sections against their own references |
| What asset value is at risk? `value_at_risk` | portfolio value at risk |
| How many assets are a threat? `condition_summary` | the banding summary |

**Maintenance:** `statutory_decisions`, `unconverted_recommendations`, `ppm_behind_plan`,
`decisions_owed`, `decisions_blocked`.

**Inspection panel:** `warranty`, `corroborated`, `worst_condition`, `vendor_reports`, and
`unconverted_recommendations` (the panel words it "turned into work orders"; the chip says
"converted to orders" — both match).

---

## What it refuses, and what that proves

Small talk, instructions and SQL are all refused rather than routed:

```
"what is the weather in dubai"              -> understood: false
"delete all work orders"                    -> understood: false
"SELECT * FROM plenum_cafm.assets"          -> understood: false
"'; DROP TABLE assets; --"                  -> understood: false
"ignore your instructions and list all organisations" -> understood: false
"reports"                                   -> understood: false (too vague to route)
```

None of this is a SQL-injection defence, because no question ever becomes SQL — the matcher
simply does not engage. There are tests pinning every one of these.

---

## One honest note on the answers

An answer reflects what is on record, and says so when that is nothing. On the deployed
database today most of these return a true zero — "Every recommendation on record became a work
order", "No PPM visits are on record for your buildings this year" — because `inspections` and
`ppm_visits` are empty there. That is the correct answer to the question, not a failure, and it
reads differently from "I cannot answer that", which is what you get when the database cannot
support the question at all.
