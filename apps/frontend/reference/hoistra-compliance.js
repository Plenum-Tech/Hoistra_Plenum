/* Hoistra — compliance console data. Portfolio as at 2 Sep 2026.
   Four regulation packs: UK, US, UAE, Singapore. Coverage is required
   certificate types on file — pack completeness, not a compliance score. */
window.HOISTRA_CC = (function () {
  var countries = [
    { code: "UK", flag: "🇬🇧", name: "United Kingdom", pack: "CIBSE TM46 · LOLER · EICR · CP12", certs: 21, lapsed: 9 },
    { code: "US", flag: "🇺🇸", name: "United States", pack: "Energy Star · ASHRAE 100", certs: 8, lapsed: 2 },
    { code: "AE", flag: "🇦🇪", name: "UAE", pack: "Rolling portfolio benchmark", certs: 6, lapsed: 1 },
    { code: "SG", flag: "🇸🇬", name: "Singapore", pack: "BCA Benchmarking Report", certs: 5, lapsed: 0 }
  ];

  var states = [
    { name: "Greater London", country: "UK", certs: 13, lapsed: 6 },
    { name: "Greater Manchester", country: "UK", certs: 5, lapsed: 2 },
    { name: "Scotland", country: "UK", certs: 3, lapsed: 1 },
    { name: "New York", country: "US", certs: 5, lapsed: 2 },
    { name: "Texas", country: "US", certs: 3, lapsed: 0 },
    { name: "Dubai", country: "AE", certs: 4, lapsed: 1 },
    { name: "Abu Dhabi", country: "AE", certs: 2, lapsed: 0 },
    { name: "Central Region", country: "SG", certs: 5, lapsed: 0 }
  ];

  var buildings = [
    { name: "Bishopsgate Tower", state: "Greater London", use: "Commercial", cov: 41, on: 11, req: 27, certs: 11, high: 2, med: 3, cur: 6, blocked: 2 },
    { name: "Kingsway House", state: "Greater London", use: "Mixed", cov: 33, on: 9, req: 27, certs: 9, high: 1, med: 2, cur: 6, blocked: 1 },
    { name: "AN Other House", state: "Greater London", use: "Retail", cov: 22, on: 6, req: 27, certs: 6, high: 2, med: 1, cur: 3, blocked: 2 },
    { name: "Town Hall", state: "Greater Manchester", use: "Commercial", cov: 26, on: 7, req: 27, certs: 7, high: 3, med: 1, cur: 3, blocked: 2 },
    { name: "Riverside Court", state: "Greater Manchester", use: "Residential", cov: 63, on: 17, req: 27, certs: 17, high: 0, med: 2, cur: 15, blocked: 0 },
    { name: "Meridian Quay", state: "Scotland", use: "Residential", cov: 55, on: 12, req: 22, certs: 12, high: 1, med: 1, cur: 10, blocked: 0 },
    { name: "Hudson Yards Annex", state: "New York", use: "Commercial", cov: 48, on: 10, req: 21, certs: 10, high: 1, med: 2, cur: 7, blocked: 1 },
    { name: "Northgate Mall", state: "New York", use: "Mall", cov: 38, on: 8, req: 21, certs: 8, high: 1, med: 1, cur: 6, blocked: 1 },
    { name: "Congress Plaza", state: "Texas", use: "Commercial", cov: 71, on: 15, req: 21, certs: 15, high: 0, med: 1, cur: 14, blocked: 0 },
    { name: "Marina Heights", state: "Dubai", use: "Mixed", cov: 44, on: 8, req: 18, certs: 8, high: 1, med: 2, cur: 5, blocked: 1 },
    { name: "Deira Trade Centre", state: "Dubai", use: "Commercial", cov: 56, on: 10, req: 18, certs: 10, high: 0, med: 1, cur: 9, blocked: 0 },
    { name: "Corniche One", state: "Abu Dhabi", use: "Commercial", cov: 67, on: 12, req: 18, certs: 12, high: 0, med: 0, cur: 12, blocked: 0 },
    { name: "Raffles Link", state: "Central Region", use: "Commercial", cov: 80, on: 16, req: 20, certs: 16, high: 0, med: 1, cur: 15, blocked: 0 },
    { name: "Tanjong Pagar Centre", state: "Central Region", use: "Mixed", cov: 75, on: 15, req: 20, certs: 15, high: 0, med: 1, cur: 14, blocked: 0 }
  ];

  var vendors = [
    { name: "Apex Lifts", cov: 100, on: 4, req: 4, block: "Clear", worst: "OK", sev: "ok", serves: ["Bishopsgate Tower", "Kingsway House", "Riverside Court"], gaps: [], certs: 4, spec: "Lifts — LOLER" },
    { name: "SafeLift Engineering", cov: 50, on: 2, req: 4, block: "Blocked", worst: "Lapsed", sev: "risk", serves: ["Bishopsgate Tower", "Town Hall"], gaps: ["LOLER Thorough Examination", "Public Liability Insurance"], certs: 2, spec: "Lifts — LOLER" },
    { name: "Meridian Heating", cov: 40, on: 2, req: 5, block: "Blocked", worst: "Lapsed", sev: "risk", serves: ["Town Hall", "AN Other House", "Meridian Quay"], gaps: ["Gas Safe Registration", "ACS Competency Card", "Employers' Liability"], certs: 2, spec: "Gas — Gas Safe" },
    { name: "Northgate Electrical", cov: 75, on: 3, req: 4, block: "Clear", worst: "<30d", sev: "warn", serves: ["AN Other House", "Northgate Mall", "Hudson Yards Annex"], gaps: ["NAPIT Registration"], certs: 3, spec: "Electrical — NICEIC" },
    { name: "Clearwater Compliance", cov: 80, on: 4, req: 5, block: "Clear", worst: "<90d", sev: "warn", serves: ["Kingsway House", "Riverside Court", "Marina Heights"], gaps: ["ISO 14001:2015 Environmental"], certs: 4, spec: "Water hygiene — L8" },
    { name: "ProudCastle Fire", cov: 25, on: 1, req: 4, block: "Blocked", worst: "Lapsed", sev: "risk", serves: ["Bishopsgate Tower", "Kingsway House"], gaps: ["BAFE SP101 Extinguishers", "BAFE SP105 Riser", "NSI Gold Fire & Security"], certs: 1, spec: "Fire safety" },
    { name: "Gulf Facilities LLC", cov: 67, on: 4, req: 6, block: "Clear", worst: "<90d", sev: "warn", serves: ["Marina Heights", "Deira Trade Centre", "Corniche One"], gaps: ["Dubai Civil Defence Permit", "ISO 45001 Occupational Health"], certs: 4, spec: "HVAC and refrigeration" },
    { name: "Lion City Engineering", cov: 100, on: 5, req: 5, block: "Clear", worst: "OK", sev: "ok", serves: ["Raffles Link", "Tanjong Pagar Centre"], gaps: [], certs: 5, spec: "Building fabric" },
    { name: "Empire Mechanical", cov: 60, on: 3, req: 5, block: "Clear", worst: "<30d", sev: "warn", serves: ["Hudson Yards Annex", "Congress Plaza", "Northgate Mall"], gaps: ["ASHRAE Certified Operator", "Workers' Compensation"], certs: 3, spec: "HVAC and refrigeration" }
  ];

  /* pos = % across the runway; 0–14% lapsed, 14% is today */
  var certs = [
    { nm: "Gas Safety (CP12)", holder: "Town Hall", kind: "building", exp: "27 Aug 2026", days: -6, risk: "Lapsed", sev: "risk", auth: "genuine", authSev: "ok", ver: "Gas Safe Register", pos: 12 },
    { nm: "EICR — Landlord Supply", holder: "AN Other House", kind: "building", exp: "14 Jul 2026", days: -50, risk: "Lapsed", sev: "risk", auth: "suspect · 41", authSev: "warn", ver: "NICEIC portal", pos: 8 },
    { nm: "Fire Risk Assessment", holder: "Bishopsgate Tower", kind: "building", exp: "02 Jun 2026", days: -92, risk: "Lapsed", sev: "risk", auth: "genuine", authSev: "ok", ver: "Not checked", pos: 5 },
    { nm: "LOLER Thorough Examination", holder: "SafeLift Engineering", kind: "vendor", exp: "30 Apr 2026", days: -125, risk: "Blocked", sev: "risk", auth: "not checked", authSev: "none", ver: "LEIA register", pos: 3 },
    { nm: "Gas Safe Registration", holder: "Meridian Heating", kind: "vendor", exp: "31 Mar 2026", days: -155, risk: "Blocked", sev: "risk", auth: "suspect · 38", authSev: "warn", ver: "Gas Safe Register", pos: 2 },
    { nm: "BAFE SP203-1 Registration", holder: "ProudCastle Fire", kind: "vendor", exp: "03 Jan 2026", days: -242, risk: "Blocked", sev: "risk", auth: "genuine", authSev: "ok", ver: "BAFE register", pos: 1 },
    { nm: "Asbestos Re-inspection", holder: "Kingsway House", kind: "building", exp: "11 Sep 2026", days: 9, risk: "<30d", sev: "warn", auth: "genuine", authSev: "ok", ver: "UKAS register", pos: 16 },
    { nm: "NICEIC Approved Contractor", holder: "Northgate Electrical", kind: "vendor", exp: "24 Sep 2026", days: 22, risk: "<30d", sev: "warn", auth: "not checked", authSev: "none", ver: "NICEIC portal", pos: 19 },
    { nm: "ASHRAE Operator Certification", holder: "Empire Mechanical", kind: "vendor", exp: "28 Sep 2026", days: 26, risk: "<30d", sev: "warn", auth: "not checked", authSev: "none", ver: "Needs partner", pos: 21 },
    { nm: "Legionella Risk Assessment (L8)", holder: "Riverside Court", kind: "building", exp: "19 Oct 2026", days: 47, risk: "<90d", sev: "warn", auth: "genuine", authSev: "ok", ver: "LCA register", pos: 27 },
    { nm: "Energy Star Submission", holder: "Hudson Yards Annex", kind: "building", exp: "01 Nov 2026", days: 60, risk: "<90d", sev: "warn", auth: "not checked", authSev: "none", ver: "Portfolio Manager", pos: 31 },
    { nm: "ISO 14001:2015 Environmental", holder: "Clearwater Compliance", kind: "vendor", exp: "15 Nov 2026", days: 74, risk: "<90d", sev: "warn", auth: "not checked", authSev: "none", ver: "UKAS register", pos: 36 },
    { nm: "Dubai Civil Defence Permit", holder: "Gulf Facilities LLC", kind: "vendor", exp: "27 Nov 2026", days: 86, risk: "<90d", sev: "warn", auth: "not checked", authSev: "none", ver: "DCD portal", pos: 40 },
    { nm: "BCA Benchmarking Submission", holder: "Raffles Link", kind: "building", exp: "31 Dec 2026", days: 120, risk: "OK", sev: "ok", auth: "genuine", authSev: "ok", ver: "BCA portal", pos: 49 },
    { nm: "Public Liability Insurance", holder: "Apex Lifts", kind: "vendor", exp: "14 Jan 2027", days: 134, risk: "OK", sev: "ok", auth: "genuine", authSev: "ok", ver: "Verified", pos: 54 },
    { nm: "Emergency Lighting Test", holder: "Congress Plaza", kind: "building", exp: "20 Feb 2027", days: 171, risk: "OK", sev: "ok", auth: "genuine", authSev: "ok", ver: "Not checked", pos: 63 },
    { nm: "Lift LOLER Examination", holder: "Corniche One", kind: "building", exp: "18 Mar 2027", days: 197, risk: "OK", sev: "ok", auth: "genuine", authSev: "ok", ver: "Verified", pos: 70 },
    { nm: "Fire & Security — NSI Gold", holder: "Lion City Engineering", kind: "vendor", exp: "22 Apr 2027", days: 232, risk: "OK", sev: "ok", auth: "genuine", authSev: "ok", ver: "Verified", pos: 79 },
    { nm: "Boiler Service Record", holder: "Tanjong Pagar Centre", kind: "building", exp: "09 Jun 2027", days: 280, risk: "OK", sev: "ok", auth: "genuine", authSev: "ok", ver: "Not checked", pos: 90 }
  ];

  var mxTypes = ["Fire Risk Assessment", "Gas Safety (CP12)", "EICR", "Asbestos Register", "Emergency Lighting", "Legionella (L8)", "Employers' Liability", "Energy Rating", "Boiler Service", "Lift LOLER"];
  var mxShort = ["FRA", "CP12", "EICR", "ASB", "EM LT", "L8", "EL INS", "ENERGY", "BOILER", "LOLER"];
  var mx = {
    "Bishopsgate Tower": ["risk", "ok", "ok", "gap", "gap", "ok", "risk", "ok", "gap", "risk"],
    "Kingsway House": ["ok", "ok", "gap", "warn", "gap", "ok", "ok", "ok", "gap", "gap"],
    "AN Other House": ["ok", "gap", "risk", "gap", "gap", "gap", "gap", "ok", "gap", "na"],
    "Town Hall": ["ok", "risk", "risk", "gap", "gap", "ok", "gap", "gap", "risk", "na"],
    "Riverside Court": ["ok", "ok", "ok", "ok", "ok", "warn", "ok", "ok", "ok", "ok"],
    "Meridian Quay": ["ok", "ok", "ok", "gap", "ok", "ok", "ok", "warn", "ok", "na"],
    "Hudson Yards Annex": ["ok", "na", "ok", "gap", "ok", "gap", "ok", "warn", "ok", "ok"],
    "Northgate Mall": ["ok", "na", "warn", "gap", "gap", "gap", "ok", "ok", "gap", "ok"],
    "Congress Plaza": ["ok", "na", "ok", "ok", "ok", "ok", "ok", "ok", "ok", "ok"],
    "Marina Heights": ["ok", "na", "ok", "na", "gap", "warn", "ok", "gap", "ok", "ok"],
    "Deira Trade Centre": ["ok", "na", "ok", "na", "ok", "ok", "ok", "ok", "gap", "ok"],
    "Corniche One": ["ok", "na", "ok", "na", "ok", "ok", "ok", "ok", "ok", "ok"],
    "Raffles Link": ["ok", "na", "ok", "na", "ok", "ok", "ok", "ok", "ok", "ok"],
    "Tanjong Pagar Centre": ["ok", "na", "ok", "na", "ok", "warn", "ok", "ok", "ok", "ok"]
  };
  var mxLabel = { ok: "On file, current", warn: "Expiring soon", risk: "Lapsed or blocked", gap: "Not on record — required, nothing filed", na: "Not required for this building" };
  var mxGlyph = { ok: "✓", warn: "!", risk: "✕", gap: "–", na: "" };

  var gapTypes = ["Asbestos Register (Living Document)", "Boiler Service Record", "Cold Water Storage Tank Inspection", "Display Energy Certificate", "Electrical Installation Certificate", "Emergency Lighting Test Certificate", "Fixed Wire Testing Schedule", "Lightning Protection Test", "Sprinkler System Certificate", "Ventilation Hygiene Report"];

  return { countries: countries, states: states, buildings: buildings, vendors: vendors, certs: certs,
           mxTypes: mxTypes, mxShort: mxShort, mx: mx, mxLabel: mxLabel, mxGlyph: mxGlyph, gapTypes: gapTypes };
})();
