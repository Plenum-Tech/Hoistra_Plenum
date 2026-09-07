/* Hoistra — vendor performance evidence layer.
   Every score decomposes to: what the contract says (and whether that term was
   READ from the contract or fell back to a platform default), what was measured,
   the work orders behind the measurement, and the compliance coverage that caps it.
   L1 SLA misses weigh 3× an L3 miss. A lapsed accreditation caps the score at 60. */
export const HOISTRA_VP = (function () {

  /* Each weight declares its OWN percentage target and the unit it is measured
     in, so nothing is compared across units. `pctTerm` names the contract term
     that supplies the target when the document actually states a percentage;
     where a contract states only durations (response) or has no accuracy term
     at all (invoicing), the target is a platform default and says so, and
     `cite` names the clause the underlying obligation does come from. */
  var WEIGHTS = [
    { k: "sla_r", label: "SLA response", w: 25, unit: "%", pctTerm: null, defPct: 95, cite: "P2 response",
      basis: "jobs answered inside their contracted response window" },
    { k: "sla_c", label: "SLA completion", w: 25, unit: "%", pctTerm: "Completion target", defPct: 95,
      basis: "jobs closed inside their contracted completion window" },
    { k: "firstfix", label: "First-time fix", w: 20, unit: "%", pctTerm: "First-time fix target", defPct: 85,
      basis: "jobs resolved on the first attendance" },
    { k: "recall", label: "Recall rate", w: 15, unit: "%", ceiling: true, pctTerm: null, defPct: 8, cite: "Recall window",
      basis: "jobs the vendor returned to inside the recall window" },
    { k: "invoice", label: "Invoice accuracy", w: 15, unit: "%", pctTerm: null, defPct: 98, cite: "Labour rate — standard",
      basis: "invoice lines matching the contracted rate schedule" }
  ];

  /* Shortfall is penalised at 3× its relative size, so a 7% miss on a 95%
     target costs about a fifth of the available points rather than a rounding
     error. Beating a ceiling metric earns full marks, not extra. */
  function points(w, measured) {
    var target = w.target;
    var rel = w.ceiling ? (measured - target) / target : (target - measured) / target;
    return Math.round(w.w * Math.max(0, Math.min(1, 1 - rel * 3)));
  }

  /* src: "contract" = parsed from the signed document, with clause and page.
     "default" = not found in the document; platform default applied. */
  var V = {
    v1: {
      contract: { ref: "AM-2024-HVAC-07", signed: "01 Apr 2024", expires: "31 Mar 2027", pages: 34, read: 9, fields: 12 },
      terms: [
        { label: "P1 response", value: "4 hours", src: "contract", clause: "5.2(a)", page: 11 },
        { label: "P2 response", value: "1 business day", src: "contract", clause: "5.2(b)", page: 11 },
        { label: "P3 response", value: "5 business days", src: "contract", clause: "5.2(c)", page: 11 },
        { label: "Completion target", value: "95%", src: "contract", clause: "5.4", page: 12 },
        { label: "First-time fix target", value: "85%", src: "contract", clause: "5.6", page: 12 },
        { label: "Recall window", value: "28 days", src: "contract", clause: "5.7", page: 13 },
        { label: "Labour rate — standard", value: "£68 / hr", src: "contract", clause: "Sch. 2", page: 26 },
        { label: "Labour rate — out of hours", value: "£102 / hr", src: "contract", clause: "Sch. 2", page: 26 },
        { label: "Service credit formula", value: "2% of monthly fee per P2 breach", src: "contract", clause: "8.3", page: 18 },
        { label: "Recall chargeability", value: "Not chargeable", src: "default", clause: "—", page: 0 },
        { label: "Parts mark-up cap", value: "15%", src: "default", clause: "—", page: 0 },
        { label: "Uplift review", value: "Annual, CPI-linked", src: "default", clause: "—", page: 0 }
      ],
      measured: { sla_r: 94, sla_c: 88, firstfix: 81, recall: 6, invoice: 74 },
      samples: { sla_r: 312, sla_c: 312, firstfix: 268, recall: 268, invoice: 42 },
      crit: { L1: 34, L2: 198, L3: 80 },
      breaches: [
        { wo: "WO-4188", asset: "CHILLER-101", building: "Bishopsgate Tower", crit: "L1", metric: "Completion", target: "95%", actual: "missed by 2 days", mult: "3×", cost: "£1,240" },
        { wo: "WO-4204", asset: "AHU-3", building: "Bishopsgate Tower", crit: "L1", metric: "First-time fix", target: "85%", actual: "3 visits", mult: "3×", cost: "£920" },
        { wo: "WO-4231", asset: "AHU-7", building: "Kingsway House", crit: "L2", metric: "Response", target: "1 business day", actual: "2.4 days", mult: "1×", cost: "£310" },
        { wo: "WO-4266", asset: "FCU-22", building: "Kingsway House", crit: "L3", metric: "Completion", target: "95%", actual: "missed by 1 day", mult: "0.5×", cost: "£90" },
        { wo: "WO-4290", asset: "CHILLER-101", building: "Bishopsgate Tower", crit: "L1", metric: "Recall", target: "28 days", actual: "returned day 11", mult: "3×", cost: "£1,240" }
      ],
      certs: [
        { name: "Employers' Liability Insurance", req: "£10m", status: "Current", exp: "14 Jan 2027", ver: "Verified — insurer" },
        { name: "Public Liability Insurance", req: "£5m", status: "Current", exp: "14 Jan 2027", ver: "Verified — insurer" },
        { name: "F-Gas Company Certificate", req: "Mandatory", status: "Current", exp: "22 Jun 2027", ver: "Verified — REFCOM" },
        { name: "SafeContractor / SSIP", req: "Mandatory", status: "Expiring", exp: "28 Sep 2026", ver: "Verified — SSIP portal" },
        { name: "ISO 45001", req: "Preferred", status: "Not on record", exp: "—", ver: "Never supplied" },
        { name: "ISO 14001", req: "Preferred", status: "Not on record", exp: "—", ver: "Never supplied" }
      ],
      invoices: [
        { ref: "INV-8841", period: "Aug 2026", line: "Labour — 32 hrs out of hours", charged: "£3,808", should: "£3,264", delta: "+£544", flag: "Standard hours billed at OOH rate", status: "Held" },
        { ref: "INV-8802", period: "Jul 2026", line: "Parts — compressor seal kit", charged: "£1,420", should: "£1,208", delta: "+£212", flag: "Mark-up 22% vs 15% cap", status: "Held" },
        { ref: "INV-8766", period: "Jul 2026", line: "Recall attendance — WO-4290", charged: "£380", should: "£0", delta: "+£380", flag: "Recall inside 28-day window", status: "Credited" },
        { ref: "INV-8744", period: "Jun 2026", line: "PPM — quarterly HVAC", charged: "£12,400", should: "£12,400", delta: "—", flag: "", status: "Approved" }
      ]
    },
    v2: {
      contract: { ref: "MH-2023-GAS-02", signed: "12 Sep 2023", expires: "11 Sep 2026", pages: 18, read: 4, fields: 12 },
      terms: [
        { label: "P1 response", value: "4 hours", src: "contract", clause: "4.1", page: 6 },
        { label: "P2 response", value: "1 business day", src: "default", clause: "—", page: 0 },
        { label: "P3 response", value: "5 business days", src: "default", clause: "—", page: 0 },
        { label: "Completion target", value: "95%", src: "default", clause: "—", page: 0 },
        { label: "First-time fix target", value: "85%", src: "default", clause: "—", page: 0 },
        { label: "Recall window", value: "28 days", src: "default", clause: "—", page: 0 },
        { label: "Labour rate — standard", value: "£54 / hr", src: "contract", clause: "Annex A", page: 14 },
        { label: "Labour rate — out of hours", value: "£81 / hr", src: "contract", clause: "Annex A", page: 14 },
        { label: "Service credit formula", value: "2% of monthly fee per P2 breach", src: "default", clause: "—", page: 0 },
        { label: "Recall chargeability", value: "Not chargeable", src: "default", clause: "—", page: 0 },
        { label: "Parts mark-up cap", value: "15%", src: "default", clause: "—", page: 0 },
        { label: "Uplift review", value: "Annual, CPI-linked", src: "contract", clause: "9.2", page: 11 }
      ],
      measured: { sla_r: 89, sla_c: 76, firstfix: 72, recall: 11, invoice: 68 },
      samples: { sla_r: 96, sla_c: 96, firstfix: 84, recall: 84, invoice: 18 },
      crit: { L1: 22, L2: 54, L3: 20 },
      breaches: [
        { wo: "WO-4512", asset: "Boiler-22", building: "Town Hall", crit: "L1", metric: "Completion", target: "95%", actual: "blocked — accreditation", mult: "3×", cost: "£640" },
        { wo: "WO-4519", asset: "Boiler-14", building: "Meridian Quay", crit: "L1", metric: "Completion", target: "95%", actual: "blocked — accreditation", mult: "3×", cost: "£380" },
        { wo: "WO-4498", asset: "Boiler-22", building: "Town Hall", crit: "L1", metric: "Response", target: "4 hours", actual: "9.2 hours", mult: "3×", cost: "£640" },
        { wo: "WO-4441", asset: "Water heater-3", building: "AN Other House", crit: "L2", metric: "Recall", target: "28 days", actual: "returned day 6", mult: "1×", cost: "£220" }
      ],
      certs: [
        { name: "Gas Safe Registration", req: "Mandatory", status: "Lapsed", exp: "31 Mar 2026", ver: "Gas Safe Register — not listed" },
        { name: "ACS Competency — engineers", req: "Mandatory", status: "Lapsed", exp: "31 Mar 2026", ver: "Not supplied" },
        { name: "Employers' Liability Insurance", req: "£10m", status: "Current", exp: "30 Nov 2026", ver: "Verified — insurer" },
        { name: "Public Liability Insurance", req: "£5m", status: "Not on record", exp: "—", ver: "Never supplied" },
        { name: "SafeContractor / SSIP", req: "Mandatory", status: "Not on record", exp: "—", ver: "Never supplied" }
      ],
      invoices: [
        { ref: "INV-7702", period: "Aug 2026", line: "Attendance — Boiler-22", charged: "£640", should: "£0", delta: "+£640", flag: "Work performed without valid Gas Safe registration", status: "Disputed" },
        { ref: "INV-7688", period: "Jul 2026", line: "Labour — 12 hrs", charged: "£972", should: "£648", delta: "+£324", flag: "OOH rate on weekday attendance", status: "Held" },
        { ref: "INV-7651", period: "Jun 2026", line: "PPM — boiler service", charged: "£1,840", should: "£1,840", delta: "—", flag: "", status: "Approved" }
      ]
    },
    v3: {
      contract: { ref: "NE-2025-ELEC-01", signed: "06 Jan 2025", expires: "05 Jan 2028", pages: 41, read: 12, fields: 12 },
      terms: [
        { label: "P1 response", value: "2 hours", src: "contract", clause: "6.1", page: 14 },
        { label: "P2 response", value: "8 hours", src: "contract", clause: "6.2", page: 14 },
        { label: "P3 response", value: "3 business days", src: "contract", clause: "6.3", page: 15 },
        { label: "Completion target", value: "97%", src: "contract", clause: "6.5", page: 15 },
        { label: "First-time fix target", value: "90%", src: "contract", clause: "6.7", page: 16 },
        { label: "Recall window", value: "42 days", src: "contract", clause: "6.8", page: 16 },
        { label: "Labour rate — standard", value: "£72 / hr", src: "contract", clause: "Sch. 3", page: 31 },
        { label: "Labour rate — out of hours", value: "£108 / hr", src: "contract", clause: "Sch. 3", page: 31 },
        { label: "Service credit formula", value: "3% of monthly fee per P1 breach", src: "contract", clause: "11.4", page: 22 },
        { label: "Recall chargeability", value: "Not chargeable, 42 days", src: "contract", clause: "6.9", page: 16 },
        { label: "Parts mark-up cap", value: "12%", src: "contract", clause: "Sch. 3", page: 32 },
        { label: "Uplift review", value: "Annual, capped at CPI + 1%", src: "contract", clause: "14.1", page: 27 }
      ],
      measured: { sla_r: 98, sla_c: 96, firstfix: 92, recall: 3, invoice: 96 },
      samples: { sla_r: 204, sla_c: 204, firstfix: 186, recall: 186, invoice: 34 },
      crit: { L1: 41, L2: 122, L3: 41 },
      breaches: [
        { wo: "WO-4302", asset: "DB-4", building: "Northgate Mall", crit: "L2", metric: "Response", target: "8 hours", actual: "11 hours", mult: "1×", cost: "£180" },
        { wo: "WO-4355", asset: "Lighting circuit 7", building: "AN Other House", crit: "L3", metric: "Completion", target: "97%", actual: "missed by 1 day", mult: "0.5×", cost: "£60" }
      ],
      certs: [
        { name: "NICEIC Approved Contractor", req: "Mandatory", status: "Expiring", exp: "24 Sep 2026", ver: "Verified — NICEIC portal" },
        { name: "Employers' Liability Insurance", req: "£10m", status: "Current", exp: "11 Nov 2026", ver: "Verified — insurer" },
        { name: "Public Liability Insurance", req: "£5m", status: "Current", exp: "11 Nov 2026", ver: "Verified — insurer" },
        { name: "SafeContractor / SSIP", req: "Mandatory", status: "Current", exp: "18 Apr 2027", ver: "Verified — SSIP portal" },
        { name: "ISO 9001", req: "Preferred", status: "Current", exp: "02 Feb 2027", ver: "Verified — UKAS" },
        { name: "NAPIT Registration", req: "Preferred", status: "Not on record", exp: "—", ver: "Never supplied" }
      ],
      invoices: [
        { ref: "INV-9120", period: "Aug 2026", line: "PPM — fixed wire testing", charged: "£8,640", should: "£8,640", delta: "—", flag: "", status: "Approved" },
        { ref: "INV-9088", period: "Aug 2026", line: "Parts — RCBO replacement ×12", charged: "£1,344", should: "£1,344", delta: "—", flag: "", status: "Approved" },
        { ref: "INV-9041", period: "Jul 2026", line: "Labour — 6 hrs out of hours", charged: "£648", should: "£648", delta: "—", flag: "", status: "Approved" }
      ]
    }
  };

  /* Vendors 4–6 carry a lighter but structurally identical record. */
  var LIGHT = {
    v4: { ref: "AL-2024-LIFT-03", signed: "18 Feb 2024", expires: "17 Feb 2027", pages: 29, read: 10,
      spec: "Lifts — LOLER", rate: "£78 / hr", crit: { L1: 28, L2: 96, L3: 24 },
      measured: { sla_r: 96, sla_c: 93, firstfix: 88, recall: 4, invoice: 91 },
      samples: { sla_r: 148, sla_c: 148, firstfix: 132, recall: 132, invoice: 26 },
      certKeys: [["LOLER Thorough Examination competence", "Current", "04 Apr 2027"], ["LEIA Membership", "Current", "31 Dec 2026"], ["Employers' Liability Insurance", "Current", "09 Aug 2027"], ["Public Liability Insurance", "Current", "09 Aug 2027"], ["SafeContractor / SSIP", "Current", "12 Mar 2027"]],
      breach: [["WO-4377", "Lift-2", "Bishopsgate Tower", "L1", "Response", "4 hours", "5.1 hours", "3×", "£820"], ["WO-4401", "Lift-4", "Riverside Court", "L2", "First-time fix", "85%", "2 visits", "1×", "£240"]],
      inv: [["INV-8210", "Aug 2026", "PPM — monthly lift service ×6", "£4,680", "£4,680", "—", "", "Approved"], ["INV-8177", "Jul 2026", "Callout — Lift-2", "£420", "£390", "+£30", "Callout rate £30 above schedule", "Held"]] },
    v5: { ref: "SF-2024-FIRE-05", signed: "03 Jun 2024", expires: "02 Jun 2027", pages: 24, read: 8,
      spec: "Fire safety", rate: "£64 / hr", crit: { L1: 38, L2: 84, L3: 18 },
      measured: { sla_r: 95, sla_c: 91, firstfix: 85, recall: 5, invoice: 88 },
      samples: { sla_r: 140, sla_c: 140, firstfix: 124, recall: 124, invoice: 22 },
      certKeys: [["BAFE SP203-1 Registration", "Current", "17 May 2027"], ["Employers' Liability Insurance", "Current", "21 Feb 2027"], ["Public Liability Insurance", "Current", "21 Feb 2027"], ["SafeContractor / SSIP", "Current", "06 Jul 2027"], ["NSI Gold", "Not on record", "—"]],
      breach: [["WO-4288", "Sprinkler zone 3", "Northgate Mall", "L1", "Completion", "95%", "missed by 3 days", "3×", "£1,100"], ["WO-4312", "Extinguisher set B", "Kingsway House", "L3", "Response", "5 business days", "7 days", "0.5×", "£70"]],
      inv: [["INV-8560", "Aug 2026", "PPM — quarterly fire systems", "£6,240", "£6,240", "—", "", "Approved"], ["INV-8522", "Jul 2026", "Parts — sprinkler heads ×24", "£1,680", "£1,512", "+£168", "Mark-up 21% vs 15% default", "Held"]] },
    v6: { ref: "CW-2023-WATER-04", signed: "20 Oct 2023", expires: "19 Oct 2026", pages: 15, read: 5,
      spec: "Water hygiene — L8", rate: "£58 / hr", crit: { L1: 12, L2: 62, L3: 34 },
      measured: { sla_r: 88, sla_c: 81, firstfix: 79, recall: 9, invoice: 79 },
      samples: { sla_r: 108, sla_c: 108, firstfix: 94, recall: 94, invoice: 20 },
      certKeys: [["LCA Registration", "Current", "02 Feb 2027"], ["Employers' Liability Insurance", "Current", "14 Dec 2026"], ["Public Liability Insurance", "Expiring", "30 Sep 2026"], ["SafeContractor / SSIP", "Current", "22 Jan 2027"], ["ISO 14001", "Not on record", "—"]],
      breach: [["WO-4260", "CWST-1", "Riverside Court", "L2", "Completion", "95%", "81% over period", "1×", "£420"], ["WO-4275", "Calorifier-2", "Kingsway House", "L2", "First-time fix", "85%", "3 visits", "1×", "£280"], ["WO-4299", "TMV set", "Marina Heights", "L3", "Recall", "28 days", "returned day 19", "0.5×", "£110"]],
      inv: [["INV-8330", "Aug 2026", "L8 monitoring — monthly", "£2,480", "£2,480", "—", "", "Approved"], ["INV-8301", "Jul 2026", "Labour — 18 hrs", "£1,140", "£1,044", "+£96", "Rate £5.30/hr above schedule", "Held"]] }
  };

  var DEFAULT_TERMS = [
    ["P1 response", "4 hours"], ["P2 response", "1 business day"], ["P3 response", "5 business days"],
    ["Completion target", "95%"], ["First-time fix target", "85%"], ["Recall window", "28 days"],
    ["Labour rate — standard", null], ["Labour rate — out of hours", null],
    ["Service credit formula", "2% of monthly fee per P2 breach"], ["Recall chargeability", "Not chargeable"],
    ["Parts mark-up cap", "15%"], ["Uplift review", "Annual, CPI-linked"]
  ];

  Object.keys(LIGHT).forEach(function (k) {
    var L = LIGHT[k];
    V[k] = {
      contract: { ref: L.ref, signed: L.signed, expires: L.expires, pages: L.pages, read: L.read, fields: 12 },
      terms: DEFAULT_TERMS.map(function (t, i) {
        var fromContract = i < L.read;
        var val = t[1];
        if (t[0] === "Labour rate — standard") val = L.rate;
        if (t[0] === "Labour rate — out of hours") val = "£" + Math.round(parseInt(L.rate.replace(/\D/g, ""), 10) * 1.5) + " / hr";
        return { label: t[0], value: val, src: fromContract ? "contract" : "default",
                 clause: fromContract ? (i < 3 ? "5." + (i + 1) : i < 6 ? "6." + (i - 2) : "Sch. 2") : "—",
                 page: fromContract ? 6 + i * 2 : 0 };
      }),
      measured: L.measured, samples: L.samples, crit: L.crit,
      breaches: L.breach.map(function (b) {
        return { wo: b[0], asset: b[1], building: b[2], crit: b[3], metric: b[4], target: b[5], actual: b[6], mult: b[7], cost: b[8] };
      }),
      certs: L.certKeys.map(function (c) {
        return { name: c[0], req: /Insurance/.test(c[0]) ? (/Employers/.test(c[0]) ? "£10m" : "£5m") : (/ISO|NSI|NAPIT/.test(c[0]) ? "Preferred" : "Mandatory"),
                 status: c[1], exp: c[2], ver: c[1] === "Not on record" ? "Never supplied" : "Verified — issuing register" };
      }),
      invoices: L.inv.map(function (i) {
        return { ref: i[0], period: i[1], line: i[2], charged: i[3], should: i[4], delta: i[5], flag: i[6], status: i[7] };
      })
    };
  });

  /* Resolve each weight's target against a vendor's parsed terms, then score.
     The returned rows are the single source of the headline score. */
  function scorecard(rec) {
    var rows = WEIGHTS.map(function (w) {
      var term = w.pctTerm ? rec.terms.filter(function (t) { return t.label === w.pctTerm; })[0] : null;
      var fromContract = !!(term && term.src === "contract");
      var pct = fromContract ? parseInt(String(term.value).replace(/[^0-9]/g, ""), 10) : w.defPct;
      var cited = w.cite ? rec.terms.filter(function (t) { return t.label === w.cite; })[0] : null;
      var resolved = Object.assign({}, w, { target: pct });
      var measured = rec.measured[w.k];
      return {
        k: w.k, label: w.label, w: w.w, basis: w.basis, unit: w.unit, ceiling: !!w.ceiling,
        target: pct, measured: measured, pts: points(resolved, measured),
        fromContract: fromContract,
        clause: fromContract ? term.clause : (cited && cited.src === "contract" ? cited.clause : null),
        page: fromContract ? term.page : (cited && cited.src === "contract" ? cited.page : 0),
        citeLabel: w.cite || w.pctTerm
      };
    });
    var raw = rows.reduce(function (q, r) { return q + r.pts; }, 0);
    return { rows: rows, raw: raw };
  }

  return { WEIGHTS: WEIGHTS, V: V, scorecard: scorecard };
})();
