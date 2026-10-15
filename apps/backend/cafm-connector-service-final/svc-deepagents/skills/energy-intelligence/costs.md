---
name: energy-costs
description: How consumption becomes money — cost above benchmark, anomaly cost, what the two headline figures do and do not include, and why they must never be added to each other.
---

# Cost — what each money figure counts

There are **two different money figures** on the Energy page and they answer different
questions. Confusing them, or adding them, is the most expensive mistake available here.

| Figure | Formula | Answers |
|---|---|---|
| **Cost above benchmark** | (EUI − reference) × area × tariff | "how much more does this building use than its pack?" |
| **Anomaly cost** | deviation from the building's **own** baseline × tariff | "what specific events did we detect?" |

```
Cost above benchmark / year   £369k     (EUI − reference) × area × tariff
Anomaly cost / year           £154k     9 anomalies, deviation from own baseline × tariff
```

**These are two lenses on overlapping energy, not two bills.** £369k + £154k is not £523k. A
baseline drift is usually *part of* why a building is above its benchmark, so the anomaly cost
is largely inside the benchmark figure already. Present them side by side, never summed.

And the reverse case is real: **a building can be under its benchmark and still have anomalies.**
It is efficient against its pack and still wasting energy against its own normal. Bishopsgate
Tower is at reference and carries two anomalies worth £66k. Neither figure invalidates the other.

---

## 1. Cost above benchmark

```
excess_kwh      = (eui_kwh_per_m2 − benchmark_kwh_per_m2) × gia_m2
financial_gbp   = excess_kwh × tariff
```

- **Negative is a real answer.** A building under its reference has no cost above benchmark, and
  the correct report is "at or under reference" — not £0 presented as if something were missing.
- The benchmark is **that building's**, for its market and use class. It is not a portfolio
  average and not another building's.
- Needs `gia_m2`. Without the area there is no EUI and no cost — say the profile is incomplete
  rather than estimating one.

## 2. Anomaly cost

Deviation from the building's **own** prior profile, annualised and priced. See `anomalies.md`
for the detectors and the annualisation. The one rule that matters here:

**Use `get_anomaly_rollup`. Never sum `list_energy_anomalies`.** Detectors overlap — a weekend
spike sits inside a non-occupied spike — so adding them counts the same kilowatt-hour twice.
That is how a building **3% under its reference** came to display a six-figure anomaly cost.

## 3. Rolling a portfolio up

The portfolio runs across four markets in four currencies with different units. So:

- **Never add across markets.** A portfolio figure is *normalised*, and you say so when you show
  one. Name the metrics that did not survive the normalisation rather than approximating them.
- Within one market, totals are legitimate: "6 UK buildings · £336k above reference · £109k in
  anomalies" adds buildings that share a standard, a unit and a currency.
- **Never convert currencies.** Report each market in its own.

## 4. The tariff decides the money

| Market | Electricity | Gas | Note |
|---|---|---|---|
| UK | 28.4p/kWh contracted | 7.1p/kWh, m³ converted by calorific value | |
| US | $0.22/kWh commercial | $1.40/therm | billed in therms, not kWh |
| UAE | 44.5 fils/kWh incl. fuel surcharge | LPG by cylinder or kg | LPG has no per-kWh rate — **unpriceable** |
| SG | S$0.30/kWh contestable | | |

`get_energy_tariffs` returns these as **bands**, not points — a commercial tariff is a contract
range, and pricing from a single figure invents precision that is not there. Where a band is
given, say which end you used, or give the range.

**Always name the tariff behind a cost figure.** "£19,600" is unverifiable; "£19,600 at
28.4p/kWh" can be checked by the person reading it.

## 5. Ranking and thresholds

- Rank by money, always. `metric_pct` says how unusual; `financial_gbp` says whether to act.
- The "Above £20k" filter is applied to the building's total cost above reference, not to
  individual anomalies. Say which you filtered on.
- A £0 cost is either "at or under reference" or "unpriced rule" — two different statements.
  Never let £0 read as "nothing wrong".

## 6. Never

- Never add cost-above-benchmark to anomaly cost.
- Never add money across markets, or convert currencies.
- Never quote a cost without the tariff and its currency.
- Never sum the anomaly detectors — use the rollup.
- Never report an annualised figure as money already spent.
- Never estimate a missing GIA to produce a number.
