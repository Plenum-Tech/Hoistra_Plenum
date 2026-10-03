---
name: energy-anomalies
description: The thirteen detectors, what each one actually claims, how an anomaly is priced, and the shape of a single-anomaly answer.
---

# Anomalies — what each rule means, and what it costs

An anomaly is a **claim about a window of consumption**, not a fault. It says "this period
consumed more than the profile predicts", and the money is that excess priced at the tariff the
building is billed on. Nothing here is a diagnosis, and none of it creates a work order.

---

## 1. The detectors, and what each one is really saying

| Rule | The claim | What it does NOT mean |
|---|---|---|
| `baseline_drift` | the overnight/closed-hours floor has risen against its own prior median | that consumption rose overall — the peak may be unchanged |
| `nonocc_spike` | consumption during non-occupied hours exceeds the profile | that anyone was in the building |
| `weekend_spike` | the same, restricted to weekends | a separate fault — **it is contained inside `nonocc_spike`** |
| `asset_spike` | one sub-metered circuit stepped up against its own history | that the asset is failing; a schedule change reads identically |
| `peak_demand` | a new maximum half-hour demand | sustained cost — a single half-hour can set it |
| `night_setback_lost` | the expected night reduction did not happen | the BMS is broken; a manual override looks the same |
| `holiday_occupancy` | load on a closed day | unauthorised occupancy |
| `seasonal_step` | a step that degree-days do not explain | a fault, before weather normalisation is checked |
| `meter_stuck` | identical values across periods | zero consumption — it usually means no telemetry |
| `negative_reading` | a reading below zero | export; on a non-export meter it is a data fault |
| `tariff_mismatch` | billed rate differs from contracted | overcharging, until the contract date is checked |
| `gap_inflated` | a gap was backfilled and the estimate exceeds the profile | measured excess — **this is estimated, say so** |
| `carbon_step` | emissions rose faster than kWh | more energy; the grid mix moved |

**`weekend_spike` is contained in `nonocc_spike`.** This is the single most important line in
this file. A weekend is non-occupied time, so every weekend spike is already counted inside the
non-occupied figure. Adding them is double counting, and it is why `get_anomaly_rollup` exists.

---

## 2. How an anomaly becomes money

```
excess_kwh      = observed_kwh − profile_kwh            (over the window)
annualised_kwh  = excess_kwh × (365 / window_days)
financial_gbp   = annualised_kwh × tariff_for_that_market
```

Three things this means in practice:

- **The figure is annualised, not incurred.** "£19,600" is the cost over a year if the deviation
  persists. It is not money already spent. Say "annualised" every time — the difference between
  "this has cost you £19,600" and "this will cost £19,600 a year if it continues" is the
  difference between a true statement and a false one.
- **The tariff belongs to the market.** 28.4p/kWh in the UK, $0.22 in the US, 44.5 fils in the
  UAE, S$0.30 in Singapore. Name the rate you used. Never convert between currencies.
- **Some rules are unpriced.** `meter_stuck`, `negative_reading` and `carbon_step` carry no
  per-kWh excess and contribute £0. They are real findings with no cost attached — report them
  as findings, and do not let a £0 read as "no problem".

`days_active` is how long the deviation has persisted, not how long anyone has known about it.
A 34-day drift at `Inspected` means it was seen and the cause has not yet been removed.

---

## 3. Answering about one anomaly

This is the activity-log entry a person opens. Lead with the sentence, then the fields.

> **Baseline drift — Domestic hot water, Town Hall.** Acknowledged, active 12 days, annualised
> impact £8,900 at the contracted 28.4p/kWh. Detected by the daily 03:00 scan. The closed-hours
> floor has risen against this circuit's own prior median; the daytime peak is unchanged.

| Field | Where it comes from |
|---|---|
| Building | `energy_anomalies.building_id` → the building's name |
| Asset / circuit | `asset_id` where sub-metered, otherwise **"Whole building"** |
| Anomaly type | `anomaly_type` |
| Annualised cost | `financial_gbp` — **always with the tariff** |
| Days active | now − `window_start` |
| Status | `status`: Open, Acknowledged, Inspected, Monitoring, Expected |
| Action | what `act_on_energy_anomaly` offers, or the WO the PM may raise themselves |

**"Whole building" is a limit, not a finding.** Where metering is building-level, the circuit is
unknown and the cause is *inferred*. Kingsway House is building-level; Town Hall's domestic hot
water is sub-metered, so that one genuinely names a circuit. Say which you have — a PM sent to
inspect a specific circuit that was never measured has been sent on the strength of a guess.

**Status is not severity.** `Expected` means somebody decided a known load is not a fault; it
suppresses re-alerting and does not mean the consumption stopped. `Acknowledged` means seen, not
fixed.

---

## 4. Reading the numbers correctly

- **Rank by money, not by percentage.** A 300% spike on a 2 kW circuit is smaller than a 20%
  drift on a chiller. `metric_pct` decides how unusual, `financial_gbp` decides whether anyone
  should care.
- **A big percentage on a small base is noise.** Always give the kWh alongside the percentage.
- **An occupancy change inside the window suppresses `baseline_drift`** — the load moved because
  the building did. If an anomaly is absent where one is expected, check
  `site_occupancy_logs` before saying the building is clean.
- **The reading window is the last 35 days.** A scan returning nothing may mean no recent
  readings rather than no anomalies. Check the reading dates before reporting "clean".
- **A gap-inflated finding is an estimate.** It rests on backfilled data. Mark it.

---

## 5. Never

- Never sum the detectors. `get_anomaly_rollup` gives the headline; the sum is double counting.
- Never report an annualised figure as money already spent.
- Never price an anomaly without naming the tariff and its currency.
- Never name a circuit the metering cannot see.
- Never present `metric_pct` without the kWh behind it.
- Never create or dispatch a work order from an anomaly. Queue and dashboard only.
