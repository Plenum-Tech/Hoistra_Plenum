---
name: energy-assets
description: Asset and circuit level energy — chillers (kW/RT), gas and heating, HVAC, domestic hot water, lighting and lifts. What each one's numbers mean, what good looks like, and what the metering can and cannot attribute.
---

# Assets and circuits — chiller, gas, HVAC, hot water

A question about **plant** is a different question from one about a building. "Why is the
chiller costing so much?" is answered from the circuit, its design figures and its condition —
not from the building's EUI, which averages the chiller together with everything else.

**First, establish whether the plant is metered at all.** `energy_meters.is_sub_meter` and
`energy_meters.asset_id` say whether a circuit exists for that asset. Where it does not, the
honest answer is that consumption cannot be attributed to that asset, and anything further is
inference from the building total. Two of six UK buildings are sub-metered; the rest are
building-level.

---

## 1. Chillers and cooling

The efficiency measure is **kW/RT** — electrical kilowatts drawn per ton of refrigeration
delivered. **Lower is better**, which is the opposite direction to almost every other number in
this domain, and getting it backwards inverts the finding.

| | Typical |
|---|---|
| Good, water-cooled centrifugal at load | 0.5 – 0.7 kW/RT |
| Acceptable | 0.7 – 0.9 kW/RT |
| Investigate | above 1.0 kW/RT |

- `record_chiller_design` holds the design figure; `scan_chiller_efficiency` compares measured
  against it. Report the **gap from design**, not the raw number — a 0.9 kW/RT machine designed
  for 0.85 is close to specification; one designed for 0.55 is 60% adrift.
- **Part-load is not a fault.** Efficiency falls at low load by design. A chiller at 30% load
  reading poorly is behaving as built. Check load before calling degradation.
- **In the UAE, cooling is billed in RTh**, not kWh, and district cooling is measured at the
  premise. A kWh figure there may be a conversion — say so.
- Degree-days (`ingest_degree_days`) separate weather from fault. A hot month raises cooling
  legitimately; `seasonal_step` fires precisely when degree-days do *not* explain the rise.

## 2. Gas and heating

- Gas arrives as **MPRN**, and the UK bills by **calorific value** — the meter records volume
  (m³) and the bill converts it to kWh. A raw m³ figure compared against a kWh benchmark is out
  by roughly a factor of 11. Always confirm which unit you hold.
- In the US gas is billed **per therm**, not per kWh. In the UAE, LPG is sold by cylinder or by
  weight and has no per-kWh rate at all — it is **unpriceable**, not free.
- Heating load tracks weather. Normalise with degree-days before comparing months, and never
  compare a January to a June without saying so.
- `night_setback_lost` is the common heating finding: the setback did not happen, so the
  building held temperature overnight. A manual override looks identical to a failed BMS — the
  data cannot tell them apart.

## 3. HVAC and air handling

- HVAC is usually the largest movable load and the one most often on a schedule. `asset_spike`
  on an AHU is frequently a **schedule change**, not a fault: a time clock moved, or occupancy
  hours were extended.
- Check the shape before the size. A step that starts exactly on an hour boundary and holds is a
  schedule; a ramp that grows over days is degradation.
- Where AHUs are not separately metered, HVAC load is inferred from the building's daily profile
  — the difference between occupied and base load. That is an estimate. Mark it.

## 4. Domestic hot water

- A circulating DHW system runs continuously, so it sits mostly in the **overnight base load** —
  which is why `baseline_drift` is the detector that catches it, as at Town Hall.
- A rising DHW floor with an unchanged daytime peak usually means a failed thermostatic control,
  a stuck circulating pump, or a new continuous draw. The metering cannot distinguish these.
- It is a small circuit by comparison, so watch the percentage-on-a-small-base trap: a large
  `metric_pct` here can still be modest money. Give the kWh.

## 5. Lighting, lifts, small power

- Lighting responds to occupancy and season together; a step in one without the other is worth a
  look. `holiday_occupancy` catches lighting left on over a closure.
- Lifts and small power are rarely worth separate investigation on cost alone — they appear when
  a sub-meter exists and are usually noise against the chiller and HVAC.

---

## 6. Condition against consumption

`cross_ref_condition_consumption` joins the 1–5 condition score to consumption against
benchmark. The rule that matters:

> **Condition ≤ 2 AND consumption > 15% over reference** → remediation queue, with a
> repair-versus-replace recommendation. **Queue and dashboard only — never a work order.**

Two cautions, both of which have produced wrong answers:

- **Always show provenance.** A 2/5 with no visible source is not actionable. Give
  `provenance_json` / `source_report_ref` with the score.
- **The text heuristic does not handle negation.** "no failed drivers" can read as a failure.
  When a score contradicts the narrative it was drawn from, say so rather than repeating it.

A high consumption figure with a *good* condition score usually means a schedule or control
problem rather than worn plant — the machine is healthy and is being asked to run too much.

---

## 7. Which tool for an asset question

| Question | Call |
|---|---|
| "Why is the chiller costing so much?" | `scan_chiller_efficiency` + `list_energy_anomalies(asset_id=…)` + `cross_ref_condition_consumption` |
| "Is this chiller efficient?" | `scan_chiller_efficiency` — report the gap from design |
| "What is driving cost in this building?" | `get_building_cost_drivers(building_id)` |
| "What has this asset consumed?" | `list_building_meter_readings`, then the meter for that `asset_id` |
| "What work has this asset had?" | cross to `wo_engine`; energy holds the consumption, not the history |
| "Is the gas meter reading right?" | check unit (m³ vs kWh), then `list_energy_anomalies` for `meter_stuck` / `negative_reading` |

---

## 8. Never

- Never read kW/RT as "higher is better".
- Never call part-load inefficiency a fault without checking load.
- Never compare gas m³ against a kWh benchmark.
- Never attribute consumption to an asset that has no sub-meter — say the metering cannot see it.
- Never present a condition score without its provenance.
- Never raise a work order from an efficiency finding or a recommendation.
