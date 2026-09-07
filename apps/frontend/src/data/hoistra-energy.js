/* Hoistra — energy investigation and rule layer.
   An anomaly is only a number until the graph is asked why. The investigation
   walks the tables that could explain it — readings, BMS trends, work orders,
   inspection reports, PPM schedule, vendor contract, weather — and reports
   what it found, what it could not find, and the actions the evidence supports.
   Every finding names its table and a confidence; a missing document is a
   finding too. */
export const HOISTRA_EN = (function () {

  /* Detection rules. `needs` is the data route each rule depends on, so a rule
     can be shown as armed or unavailable per building rather than pretended. */
  var RULES = [
    { id: "nonocc", name: "Non-occupancy spike", test: "consumption in unoccupied hours > 30% of the occupied-hours average", needs: "half-hourly or interval meter", cls: "core" },
    { id: "spike", name: "Single-asset spike", test: "one sub-metered asset > 2σ above its own 4-week profile", needs: "sub-meter or BMS trend", cls: "core" },
    { id: "drift", name: "Baseline drift", test: "week-on-week baseline rising 3 weeks running with no occupancy change", needs: "weekly totals", cls: "core" },
    { id: "schedule", name: "Schedule mismatch", test: "plant start or stop more than 45 min outside the occupancy calendar", needs: "interval meter + occupancy calendar", cls: "added" },
    { id: "baseload", name: "Baseload creep", test: "overnight minimum rising week on week while daytime is flat", needs: "half-hourly or interval meter", cls: "added" },
    { id: "calendar", name: "Calendar rule", test: "weekend or public-holiday profile within 15% of a weekday", needs: "daily totals", cls: "added" },
    { id: "weather", name: "Weather-normalised residual", test: "CUSUM on degree-day regression residuals breaches the control limit", needs: "monthly totals + local degree days", cls: "added" },
    { id: "peak", name: "Peak demand excursion", test: "kVA or kW peak above agreed capacity or prior-year max", needs: "half-hourly or interval meter", cls: "added" },
    { id: "fight", name: "Simultaneous heating and cooling", test: "heating and cooling both calling in the same zone for > 30 min", needs: "BMS trend", cls: "added" },
    { id: "cop", name: "Chiller efficiency", test: "kW per RT more than 15% above design at matched ambient", needs: "BMS trend or cooling sub-meter", cls: "added" },
    { id: "regress", name: "Post-works regression", test: "consumption back to pre-fix level within 30 days of a closed work order", needs: "meter + work order record", cls: "added" },
    { id: "dataq", name: "Data-quality anomaly", test: "gaps, flatlines or estimated reads in the feed — scored as a data fault, never a building fault", needs: "any feed", cls: "added" },
    { id: "tou", name: "Time-of-use misalignment", test: "shiftable load sitting in the peak price band", needs: "interval meter + tariff bands", cls: "added" }
  ];

  /* Which rules a data route can arm. */
  function armed(gran, route) {
    var hh = /HH|SMETS2|Green Button|Retailer|BMS/.test(route || "");
    var sub = gran === "sub-metered";
    return RULES.map(function (r) {
      var ok = true;
      if (/sub-meter or BMS|BMS trend/.test(r.needs)) ok = sub;
      if (/half-hourly or interval|interval meter/.test(r.needs)) ok = hh;
      return { id: r.id, on: ok };
    });
  }

  /* Investigation templates keyed by anomaly type. `{asset}`, `{building}`,
     `{impact}`, `{vendor}` are substituted at run time. */
  var T = {
    "Non-occupancy spike": {
      sources: [
        ["meter_reading", "6 weeks half-hourly on the landlord supply", "2,016 rows"],
        ["bms_trend", "{asset} supply-fan command and status", "8,640 points"],
        ["bms_audit_log", "schedule and override changes, 90 days", "14 entries"],
        ["work_order", "orders touching {asset}, 12 months", "3 orders"],
        ["document", "inspection reports bound to those orders", "2 of 3 found"],
        ["contract", "{vendor} PPM terms", "clauses 5.2, 6.3, 7.1"]
      ],
      findings: [
        { t: "{asset} supply fan commanded ON 02:00–06:00 on the last three weekends while the occupancy calendar shows the floor closed.", src: "bms_trend · meter_reading", conf: 96 },
        { t: "A schedule override was entered on 12 Jul at 18:42 under the {vendor} maintenance login and never released.", src: "bms_audit_log", conf: 93 },
        { t: "WO-3921, the PPM visit that day, closed as 'filters changed, controls checked' — 38 minutes on site against 2 hours contracted.", src: "work_order · contract clause 5.2", conf: 88 },
        { t: "No inspection report is attached to WO-3921. The contract requires one on every PPM visit.", src: "document — gap · contract clause 7.1", conf: 100 }
      ],
      cause: "Not a plant fault. A controls override left in place after a PPM visit, unreported because the visit report was never filed.",
      costLine: "£4,430 to date · {impact} annualised if left",
      actions: [
        { l: "Raise corrective WO — release override", s: "P2 · {vendor} · £0, rework under clause 6.3", k: "wo" },
        { l: "Request the missing inspection report", s: "draft to {vendor} citing clause 7.1", k: "email" },
        { l: "Log service credit £620", s: "clause 7.1 breach · feeds the vendor scorecard", k: "credit" },
        { l: "Arm rule: override older than 72h", s: "watch every BMS override across the portfolio", k: "rule" }
      ]
    },
    "Single-asset spike": {
      sources: [
        ["bms_trend", "{asset} load, COP and condenser approach temperature", "12,400 points"],
        ["ppm_schedule", "{asset} planned visits", "1 overdue"],
        ["work_order", "orders touching {asset}, 12 months", "4 orders"],
        ["vendor", "{vendor} August scorecard", "6 P2 misses"],
        ["weather", "ambient against same weeks last year", "within 1.2 °C"],
        ["document", "last condenser clean report", "not found"]
      ],
      findings: [
        { t: "{asset} COP fell from 4.1 to 3.2 over four weeks at matched ambient — a plant-side loss, not a load-side one.", src: "bms_trend · weather", conf: 94 },
        { t: "The condenser clean due 30 Jul (WO-4102) is still 'Scheduled' — 41 days overdue.", src: "ppm_schedule · work_order", conf: 100 },
        { t: "{vendor} has missed 6 P2 windows this month; this asset is L1, so each miss weighs 3×.", src: "vendor · contract clause 5.4", conf: 100 },
        { t: "No condenser clean report exists for the last 14 months.", src: "document — gap", conf: 100 }
      ],
      cause: "Condenser fouling from a missed PPM. The work order was raised and never executed; the spike is the cost of that delay.",
      costLine: "£6,100 to date · {impact} annualised if left",
      actions: [
        { l: "Expedite WO-4102 as P1", s: "L1 asset · {vendor} · 24h window", k: "wo" },
        { l: "Claim service credit £1,240", s: "missed PPM window · clause 5.4", k: "credit" },
        { l: "Request approach-temperature log", s: "draft to {vendor} · evidence for the clean", k: "email" },
        { l: "Re-check in 7 days", s: "COP back above 3.9 closes the anomaly", k: "watch" }
      ]
    },
    "Baseline drift": {
      sources: [
        ["meter_reading", "10 weeks of weekly totals", "10 rows"],
        ["occupancy", "occupied hours per week from the IWMS", "unchanged"],
        ["weather", "heating and cooling degree days", "normalised"],
        ["ppm_schedule", "deferred visits at {building}", "2 deferrals"],
        ["work_order", "WO-3877 and WO-3899 — {asset}", "both deferred"],
        ["asset", "metering at {building}", "building-level only"]
      ],
      findings: [
        { t: "Baseline is up 9% over five weeks after degree-day normalisation; occupancy is unchanged, so the drift is plant, not people.", src: "meter_reading · occupancy · weather", conf: 91 },
        { t: "The {asset} filter change has been deferred twice (WO-3877, WO-3899). Blocked filters raise fan power steadily — the exact shape of this drift.", src: "ppm_schedule · work_order", conf: 78 },
        { t: "{building} is metered at building level, so the attribution to {asset} is inferred from timing, not measured.", src: "asset — limit", conf: 100 }
      ],
      cause: "Probable: deferred filter change raising fan power. Unconfirmed until the asset is sub-metered or the deferred work is done and the baseline falls.",
      costLine: "£2,900 to date · {impact} annualised if left",
      actions: [
        { l: "Complete the deferred PPM", s: "WO-3899 · this week · confirms or clears the cause", k: "wo" },
        { l: "Sub-meter the {asset} circuit", s: "capex £1,800 · turns inference into measurement", k: "capex" },
        { l: "Re-check in 14 days", s: "baseline back inside band closes the anomaly", k: "watch" }
      ]
    },
    "Cooling load drift": {
      sources: [
        ["bms_trend", "{asset} kW per RT and condenser water approach", "9,800 points"],
        ["meter_reading", "sub-metered chiller plant, 8 weeks", "sub-metered"],
        ["utility_bill", "DEWA billing-period consumption vs same month last year", "+12%"],
        ["weather", "cooling degree days vs last year", "flat"],
        ["work_order", "cooling tower PPM by {vendor}", "closed, no report"],
        ["document", "water-treatment log", "not found"]
      ],
      findings: [
        { t: "{asset} is running at 0.81 kW/RT against 0.68 design, with cooling degree days flat on last year — efficiency loss, not weather.", src: "bms_trend · weather", conf: 92 },
        { t: "Condenser water approach temperature has risen 2.4 °C since June — the signature of cooling-tower fouling or poor water treatment.", src: "bms_trend", conf: 85 },
        { t: "The cooling-tower PPM closed on 3 Jul with no report and no water-treatment log filed.", src: "work_order · document — gap", conf: 100 },
        { t: "The DEWA bill confirms +12% on the same period; it cannot attribute it, the BMS can.", src: "utility_bill", conf: 100 }
      ],
      cause: "Cooling-tower fouling from a lapse in water treatment, unreported because the PPM closed without a report.",
      costLine: "£5,300 to date · {impact} annualised if left",
      actions: [
        { l: "Raise WO — water treatment and tower inspection", s: "P2 · {vendor} · at contracted rate", k: "wo" },
        { l: "Request PPM report and treatment log", s: "draft to {vendor}", k: "email" },
        { l: "Re-sequence chillers to favour CH-1", s: "BMS change · interim while CH-2 is cleaned", k: "bms" },
        { l: "Re-check kW/RT in 10 days", s: "back under 0.72 closes the anomaly", k: "watch" }
      ]
    },
    "Schedule overrun": {
      sources: [
        ["meter_reading", "15-minute interval on the common-area circuit", "CMD live"],
        ["bms_audit_log", "lighting schedule changes, 90 days", "1 change"],
        ["occupancy", "mall trading hours", "closes 22:00"],
        ["work_order", "tenant fit-out requests, Aug", "1 request"],
        ["emissions", "LL97 position, year to date", "within cap"]
      ],
      findings: [
        { t: "Common-area lighting runs to 02:00 against a 22:00 close, every night since 3 Aug.", src: "meter_reading · occupancy", conf: 97 },
        { t: "The schedule was extended on 3 Aug for a tenant fit-out and never reverted; the fit-out closed on 19 Aug.", src: "bms_audit_log · work_order", conf: 95 },
        { t: "Adds an estimated 14 tCO₂e a year — Northgate stays under its LL97 cap, but with 9% headroom rather than 13%.", src: "emissions", conf: 80 }
      ],
      cause: "A temporary schedule change that outlived the reason for it. Process, not plant.",
      costLine: "£1,600 to date · {impact} annualised if left",
      actions: [
        { l: "Revert the lighting schedule", s: "BMS change · tonight", k: "bms" },
        { l: "Add schedule revert to fit-out close-out", s: "process change · every future fit-out", k: "rule" },
        { l: "Re-check in 7 days", s: "profile back to 22:00 closes the anomaly", k: "watch" }
      ]
    }
  };

  /* Building-level investigation: why is this building where it is against
     its pack, and how much of the gap is actionable. */
  var B = {
    sources: [
      ["building", "{building} record, pack and use class", "1 row"],
      ["meter_reading", "12 months, annualised", "{route}"],
      ["anomaly", "open anomalies at {building}", "{nAnom} open"],
      ["occupancy", "actual operating hours vs the pack's assumption", "{hours}"],
      ["asset", "plant age and criticality", "{plant}"],
      ["document", "EPC or rating report on file", "{rating}"]
    ],
    findings: [
      { t: "EUI {eui} against a reference of {bench} — {delta} — worth {excess} a year at the local tariff.", src: "building · meter_reading", conf: 100 },
      { t: "Open anomalies explain {anomShare} of that gap. The remainder is structural: it does not move when the anomalies are fixed.", src: "anomaly", conf: 90 },
      { t: "{hoursFinding}", src: "occupancy", conf: 82 },
      { t: "{plantFinding}", src: "asset · document", conf: 75 }
    ],
    cause: "{cause}",
    costLine: "{excess} a year above reference · {anomExcess} of it actionable now",
    actions: [
      { l: "Work the open anomalies", s: "{nAnom} open · {anomExcess} annualised", k: "anoms" },
      { l: "Re-benchmark with actual hours", s: "the pack assumes fewer hours than the building keeps", k: "bench" },
      { l: "Open a capex case", s: "{capex}", k: "capex" },
      { l: "Schedule audit", s: "every plant start/stop against the occupancy calendar", k: "audit" }
    ]
  };

  return { RULES: RULES, armed: armed, T: T, B: B };
})();
