# Investigate an asset

`GET /api/energy/assets/{asset_id}/investigate` — what the **Investigate** button opens.

Six sources are walked for one asset, each says what it gave back, evidence rules fire over
what was found, a conclusion states what it rests on, and actions come back as proposals.
**Nothing is written.**

---

## Three principles, and they are the whole design

**Absence is evidence, and it is the strongest kind.** A planned visit that closed with no
report and no treatment log is a fact with no inference in it. Findings like that carry
confidence **1.0**. A cause reasoned from a trend does not, and the difference is declared
rather than blurred.

**Confidence is a property of the rule, not of the run.** Each rule states how directly its
evidence supports its claim, fixed where the rule is written — so two investigations that found
the same thing say the same thing about it. Nothing asks a model.

| Kind | Confidence | Meaning |
|---|---|---|
| `missing record` / `missing reading` | **1.00** | The record is not there. Nothing is inferred |
| `corroborating total` | **1.00** | A total that is simply what it is, even if it explains nothing |
| `measured gap` | **0.92** | Measured against a design figure, confounder ruled out |
| `trend to cause` | **0.85** | A measured trend whose signature fits a small set of causes |
| (confounder still live) | **0.80** | Consistent with the finding, but could be other things |

**Nothing is written.** `written` is `false` on every response. Each action names the endpoint
and body it *would* be raised with; a person approves and the caller raises it. There is a test
asserting the module contains no INSERT, UPDATE, DELETE or commit.

---

## Response

```jsonc
{"ok": true,
 "asset": {"asset_name": "Chiller plant CH-2", "building": "Marina Heights",
           "vendor": "Gulf Cooling"},
 "method": "rule:asset-investigation/v1",
 "plan": "I will walk 6 sources — the readings first, then the maintenance record around the asset, then the documents that should exist for it.",

 "sources": [
   {"source": "bms_trend",     "status": "found",     "badge": "9,800 points",
    "kw_per_rt": 0.81, "design_kw_per_rt": 0.68, "over_design": true,
    "kw_per_rt_drift": 0.09, "drift_at_matched_ambient": true,
    "ambient_early_c": 34.0, "ambient_late_c": 34.5},
   {"source": "meter_reading", "status": "found",     "badge": "sub-metered"},
   {"source": "utility_bill",  "status": "partial",   "badge": "+12%"},
   {"source": "weather",       "status": "found",     "badge": "flat"},
   {"source": "work_order",    "status": "partial",   "badge": "closed, no report"},
   {"source": "document",      "status": "not_found", "badge": "not found"}],

 "evidence": [
   {"statement": "… running at 0.81 kW/RT against 0.68 design, with cooling degree days flat on last year — efficiency loss, not weather.",
    "sources": ["bms_trend", "weather"], "confidence": 0.92, "kind": "measured gap"},
   {"statement": "The cooling tower PPM closed on 2026-07-03 with no report, and no service or treatment log is filed against the asset.",
    "sources": ["work_order", "document"], "confidence": 1.0, "kind": "missing record"}],

 "conclusion": {
   "cause": "Cooling-tower fouling from a lapse in water treatment, unreported because the planned visit closed without a report.",
   "cost_to_date": 5300.0, "cost_annualised": 22700.0, "currency": "AED",
   "rests_on": "inference", "confirmation_required": true,
   "caveat": "A record the contract requires is missing, so the cause rests on inference. Confirm with the people who were on site and request the missing document before anything is claimed."},

 "actions": [
   {"id": "raise_work_order", "label": "Raise WO — water treatment and tower inspection",
    "detail": "P2 · Gulf Cooling · at contracted rate",
    "endpoint": "POST /api/work-orders/",
    "body": {"asset": "Chiller plant CH-2", "building_id": "…", "priority": "P2"}},
   {"id": "resequence", "label": "Re-sequence to favour the healthier unit",
    "endpoint": null, "note": "a BMS change; this platform proposes it but does not make it"}],

 "written": false}
```

---

## The six sources, and what each really reads

| Source | Reads | When it cannot answer |
|---|---|---|
| `bms_trend` | `chiller_performance_readings` — kW/RT against `chiller_design_specs`, and drift across the window | `not_found` with no readings |
| `meter_reading` | `energy_meters` + `meter_readings` — is the plant sub-metered | `partial` if building-level only |
| `utility_bill` | **There is no utility-bill register.** Metered consumption stands in, and the response says so | `not_found` without a comparable period |
| `weather` | `weather_degree_days` — cooling degree days against last year | `not_found` with no degree days |
| `work_order` | Orders naming this asset, and whether a report came off each | `not_found` if none names it |
| `document` | `asset_documents` | `not_found` — which is itself a finding |

### Two measurements it deliberately refuses

**Condenser water approach** is the cleaner signal for tower fouling, and it is **not
computed** — `chw_supply_c` and `chw_return_c` hold nothing on either database. Reporting an
approach figure from an empty column would be inventing a measurement, so the unreadable signal
is returned as its own full-confidence finding instead.

**Efficiency drift** is only claimed **at matched ambient** (within 2 °C between the two halves
of the window). A hotter second half explains a worse second half without anything being wrong
with the plant, so where the halves cannot be matched, `kw_per_rt_drift` is null and
`drift_at_matched_ambient` is false — and no cause is named from it.

---

## Scope

Building-scoped like everything else. **404** if the asset is not in your buildings — the same
answer as an asset that does not exist, so the API never confirms somebody else's asset is
there.
