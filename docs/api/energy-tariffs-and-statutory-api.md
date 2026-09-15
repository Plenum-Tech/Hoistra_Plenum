# Market tariffs, unit conversions, and the rule each country is judged by

The market specification's commercial terms are now machine-readable, so excess energy is
priced rather than only counted — and the statutory rule each market is held to is on file
next to its tariff, because the two are country-specific for the same reason.

---

## What changed, and why it matters

The file held **one tariff per market**. The specification gives **bands**, because a
commercial rate is a contract range. Three of the four points on file sat above the published
ceiling, so anything priced from them overstated:

| Market | Was on file | Specification |
|---|---|---|
| UK | 28.4p/kWh | **21.0p – 26.0p/kWh** |
| US | $0.22/kWh | $0.22/kWh **+ demand charges** (per kW, not per kWh) |
| AE | 44.5 fils/kWh | **26.5 – 39.5 fils** (20–33 slab + ~6.5 fuel surcharge) |
| SG | S$0.30/kWh | **S$0.25 – S$0.28/kWh** |

Everything priced from these returns a **range**. A midpoint would be wrong by up to a quarter
either way and would read as a measurement.

---

## `GET /api/energy/tariffs`

The band each market bills each fuel at, plus the unit factors, plus each market's statutory
rule. `?market=UK` narrows to one.

```jsonc
{"ok": true,
 "conversions": {"area_sqft_to_sqm": 0.092903, "therm_to_kwh": 29.3001,
                 "rth_to_kwh": 3.51685,
                 "uk_gas_m3_to_kwh": {"formula": "m3 * vcf * cv / 3.6",
                                      "volume_correction_factor": 1.02264,
                                      "calorific_value_default": 39.5}},
 "markets": [{"market": "AE", "currency": "AED",
   "tariff": {"electricity": {"low": 0.265, "high": 0.395, "unit": "AED/kWh",
                              "label": "26.5 – 39.5 fils/kWh incl. fuel surcharge",
                              "components": {"slab_low": 0.20, "slab_high": 0.33,
                                             "fuel_surcharge": 0.065}},
              "gas": {"unpriceable": true,
                      "label": "LPG by cylinder or weight · no piped grid"}},
   "statutory": {"rule": "eui_over_reference", "reference_eui_kwh_m2": 228.0}}]}
```

**`unpriceable` is not zero.** LPG in the UAE is sold by weight with no piped grid, so there is
no per-kWh rate for it. A fuel nobody can price and a fuel that costs nothing are different
facts, and the second one is never asserted.

---

## `POST /api/energy/price`

```bash
{"kwh_by_market": {"UK": 50000, "AE": 30000}, "fuel": "electricity"}
```

Each market is priced in **its own currency**, as a range. Markets are totalled **only when
every one in scope shares a currency** — then it is arithmetic. Otherwise `total` is null with
the reason:

> *"3 currencies in scope (AED, GBP, SGD), and no exchange rate is held here — the markets are
> priced separately rather than added at a rate nobody stated."*

That is the deliberate answer. A single portfolio figure that quietly used a made-up rate is
worse than no portfolio figure. When you want one, supply an FX rate with a date and it becomes
a stated conversion rather than an invented one.

---

## `GET /api/energy/statutory/{market}`

The line is a **different shape** in each market, and returning one number for all four would
flatten exactly the difference the page exists to show.

| Market | Rule | Tested on | Penalty |
|---|---|---|---|
| UK | `eui_over_reference` | 190 kWh/m²/yr (TM46 statutory unweighted — 95 heating + 95 electric) | none |
| AE | `eui_over_reference` | 228 kWh/m²/yr rolling portfolio baseline | none |
| SG | `eui_over_reference` | 192 kWh/m²/yr BCA office reference | none |
| US | `carbon_cap` | tCO₂e against the LL97 cap for the occupancy group | **$268 per tonne over** |

```bash
GET /api/energy/statutory/UK?eui_kwh_m2=214
  → {"over": true, "over_by_kwh_m2": 24.0, "reference_eui_kwh_m2": 190.0}

GET /api/energy/statutory/US?tco2e=1200&carbon_cap_tco2e=1000
  → {"over": true, "over_by_tco2e": 200.0, "penalty": 53600.0, "currency": "USD"}
```

The same intensity gives different verdicts: **200 kWh/m²/yr is over Singapore's 192 and under
the UAE's 228.** That is the point of holding the rule per country.

A market whose rule cannot be answered from what was passed says **what it needs** rather than
returning a verdict — asking the UK rule without an intensity returns `assessable: false`, not
`over: false`.

---

## One note on the UK reference

The specification gives **190 kWh/m²/yr statutory unweighted**. The platform also computes a
TM46 figure **weighted by the use mix of the buildings actually in scope**, which is the more
precise read of the same standard. Both are on file: the weighted figure is what the page
shows, and the statutory 190 is what the country rule tests against. Neither silently replaced
the other, because they answer different questions.
