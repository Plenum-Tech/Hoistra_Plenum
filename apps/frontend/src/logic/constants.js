// Static tables and helpers shared by the controller methods (ported verbatim).
import { HOISTRA_CC } from '../data/hoistra-compliance.js';
import { HOISTRA_VP } from '../data/hoistra-vendors.js';

const KL = {
  Outlier: { c: "var(--st-risk)", b: "var(--st-risk-bg)" },
  Anomaly: { c: "var(--st-warn)", b: "var(--st-warn-bg)" },
  Deficit: { c: "var(--st-dormant)", b: "var(--st-dormant-bg)" },
  "Within band": { c: "var(--st-ok)", b: "var(--st-ok-bg)" }
};

// Floor-area use tints — one accent for the dominant commercial use, the rest
// a neutral ladder, so a mixed building reads as proportion not as a palette.
const USE_TINT = {
  Commercial: { color: "var(--color-accent)" },
  Residential: { color: "var(--color-neutral-300)" },
  Retail: { color: "var(--color-neutral-500)" },
  Mall: { color: "var(--color-neutral-700)" },
  Hospital: { color: "var(--color-neutral-500)", hatch: "repeating-linear-gradient(45deg, var(--color-bg) 0 1.5px, transparent 1.5px 3.5px)" }
};

const BUILDINGS = [
  { id: "B-001", name: "Bishopsgate Tower", cc: "UK", state: "Greater London", use: "Commercial", floors: 34, area: "412,000 ft²", euiN: 214, benchN: 215, hoist: 88, mix: [["Commercial", 92], ["Retail", 8]], route: "HH data collector · LoA", gran: "sub-metered" },
  { id: "B-002", name: "Kingsway House", cc: "UK", state: "Greater London", use: "Mixed", floors: 11, area: "148,000 ft²", euiN: 198, benchN: 172, hoist: 71, mix: [["Residential", 46], ["Commercial", 34], ["Retail", 20]], route: "HH data collector · LoA", gran: "sub-metered" },
  { id: "B-003", name: "Town Hall", cc: "UK", state: "Greater Manchester", use: "Commercial", floors: 6, area: "96,000 ft²", euiN: 231, benchN: 215, hoist: 62, mix: [["Commercial", 100]], route: "HH data collector · LoA", gran: "building-level" },
  { id: "B-004", name: "Meridian Quay", cc: "UK", state: "Scotland", use: "Residential", floors: 22, area: "186,000 ft²", euiN: 164, benchN: 150, hoist: 84, mix: [["Residential", 88], ["Retail", 12]], route: "SMETS2 · SEC intermediary", gran: "building-level" },
  { id: "B-005", name: "AN Other House", cc: "UK", state: "Greater London", use: "Retail", floors: 4, area: "72,000 ft²", euiN: 209, benchN: 165, hoist: 79, mix: [["Retail", 100]], route: "HH data collector · LoA", gran: "building-level" },
  { id: "B-006", name: "Riverside Court", cc: "UK", state: "Greater Manchester", use: "Residential", floors: 15, area: "121,000 ft²", euiN: 158, benchN: 145, hoist: 90, mix: [["Residential", 100]], route: "SMETS2 · SEC intermediary", gran: "building-level" },
  { id: "B-007", name: "Marina Heights", cc: "AE", state: "Dubai", use: "Hospital", floors: 8, area: "204,000 ft²", euiN: 246, benchN: 228, hoist: 58, mix: [["Hospital", 79], ["Commercial", 14], ["Retail", 7]], route: "Own sub-meters + BMS", gran: "sub-metered" },
  { id: "B-008", name: "Northgate Mall", cc: "US", state: "New York", use: "Mixed", floors: 3, area: "318,000 ft²", euiN: 188, benchN: 205, hoist: 74, mix: [["Mall", 62], ["Retail", 26], ["Commercial", 12]], route: "Green Button CMD · aggregator", gran: "sub-metered" },
  { id: "B-009", name: "Raffles Link", cc: "SG", state: "Central Region", use: "Commercial", floors: 19, area: "268,000 ft²", euiN: 176, benchN: 192, hoist: 92, mix: [["Commercial", 100]], route: "Retailer feed · contracted", gran: "sub-metered" }
];

// Which pack sets the reference value. UAE publishes no national benchmark,
// so the platform runs a rolling portfolio comparison in its place.
// The Hoist Graph as it actually resolves: one table per node, keyed on a
// primary key, children carrying it as a foreign key. Distance from
// `buildings` is what makes a question answerable — see hop.
const GRAPH = [
  { id: "portfolio", label: "Portfolio", tbl: "portfolios", pk: "portfolio_id", cx: 500, cy: 46, r: 32, hop: 2, rows: "3",
    cols: [["portfolio_id", "uuid", "PK"], ["name", "text", ""], ["owner_entity", "text", ""], ["reporting_currency", "char(3)", ""]] },
  { id: "site", label: "Site", tbl: "sites", pk: "site_id", cx: 500, cy: 148, r: 40, hop: 1, rows: "18",
    cols: [["site_id", "uuid", "PK"], ["portfolio_id", "uuid", "FK"], ["name", "text", ""], ["address", "text", ""]] },
  { id: "location", label: "Location", tbl: "locations", pk: "location_id", cx: 372, cy: 214, r: 40, hop: 1, rows: "8",
    cols: [["location_id", "uuid", "PK"], ["country_code", "char(2)", ""], ["region", "text", ""], ["pack_id", "uuid", "FK"]] },
  { id: "pack", label: "Regulation", label2: "pack", tbl: "regulation_packs", pk: "pack_id", cx: 182, cy: 168, r: 32, hop: 2, rows: "4",
    cols: [["pack_id", "uuid", "PK"], ["standard", "text", ""], ["required_types", "text[]", ""], ["benchmark_source", "text", ""]] },
  { id: "building", label: "Building", tbl: "buildings", pk: "building_id", cx: 500, cy: 296, r: 56, hop: 0, rows: "24",
    cols: [["building_id", "uuid", "PK"], ["site_id", "uuid", "FK"], ["location_id", "uuid", "FK"], ["name", "text", ""], ["primary_use", "enum", ""], ["floors", "int", ""], ["gross_area_sqft", "numeric", ""], ["eui_kwh_m2", "numeric", ""]] },
  { id: "floor", label: "Floor", tbl: "floors", pk: "floor_id", cx: 628, cy: 214, r: 40, hop: 1, rows: "312",
    cols: [["floor_id", "uuid", "PK"], ["building_id", "uuid", "FK"], ["level", "int", ""], ["use_class", "enum", ""], ["area_sqft", "numeric", ""]] },
  { id: "space", label: "Space", tbl: "spaces", pk: "space_id", cx: 816, cy: 136, r: 32, hop: 2, rows: "1,940",
    cols: [["space_id", "uuid", "PK"], ["floor_id", "uuid", "FK"], ["tenant_ref", "text", ""], ["lettable_area", "numeric", ""]] },
  { id: "asset", label: "Asset", tbl: "assets", pk: "asset_id", cx: 628, cy: 378, r: 40, hop: 1, rows: "2,847",
    cols: [["asset_id", "uuid", "PK"], ["building_id", "uuid", "FK"], ["floor_id", "uuid", "FK"], ["category", "enum", ""], ["install_date", "date", ""], ["design_load_kw", "numeric", ""], ["condition_grade", "int", ""]] },
  { id: "equipment", label: "Equipment", tbl: "equipment", pk: "equipment_id", cx: 852, cy: 320, r: 32, hop: 2, rows: "6,110",
    cols: [["equipment_id", "uuid", "PK"], ["asset_id", "uuid", "FK"], ["serial", "text", ""], ["manufacturer", "text", ""], ["warranty_to", "date", ""]] },
  { id: "meter", label: "Meter", tbl: "meters", pk: "meter_id", cx: 848, cy: 444, r: 32, hop: 2, rows: "96",
    cols: [["meter_id", "uuid", "PK"], ["asset_id", "uuid", "FK"], ["mpan_mprn", "text", ""], ["consent_status", "enum", ""], ["granularity", "enum", ""]] },
  { id: "document", label: "Document", tbl: "documents", pk: "document_id", cx: 372, cy: 378, r: 40, hop: 1, rows: "1,204",
    cols: [["document_id", "uuid", "PK"], ["building_id", "uuid", "FK"], ["vendor_id", "uuid", "FK"], ["doc_class", "enum", ""], ["ingested_at", "timestamptz", ""], ["extraction_confidence", "numeric", ""]] },
  { id: "certificate", label: "Certi-", label2: "ficate", tbl: "certificates", pk: "certificate_id", cx: 154, cy: 436, r: 32, hop: 2, rows: "784",
    cols: [["certificate_id", "uuid", "PK"], ["document_id", "uuid", "FK"], ["asset_id", "uuid", "FK"], ["cert_type", "enum", ""], ["expires_on", "date", ""], ["authenticity_score", "numeric", ""]] },
  { id: "vendor", label: "Vendor", tbl: "vendors", pk: "vendor_id", cx: 500, cy: 444, r: 40, hop: 1, rows: "31",
    cols: [["vendor_id", "uuid", "PK"], ["name", "text", ""], ["specialisation", "enum", ""], ["accreditation_status", "enum", ""], ["blocked", "boolean", ""]] },
  { id: "contract", label: "Contract", tbl: "contracts", pk: "contract_id", cx: 646, cy: 534, r: 32, hop: 2, rows: "47",
    cols: [["contract_id", "uuid", "PK"], ["vendor_id", "uuid", "FK"], ["building_id", "uuid", "FK"], ["sla_response_hrs", "int", ""], ["penalty_clause", "text", ""], ["annual_value", "numeric", ""]] },
  { id: "invoice", label: "Invoice", tbl: "invoices", pk: "invoice_id", cx: 336, cy: 536, r: 32, hop: 2, join: true, rows: "2,318",
    cols: [["invoice_id", "uuid", "PK"], ["vendor_id", "uuid", "FK"], ["work_order_id", "uuid", "FK"], ["net_amount", "numeric", ""], ["matched", "boolean", ""]] },
  { id: "workorder", label: "Work", label2: "order", tbl: "work_orders", pk: "work_order_id", cx: 744, cy: 510, r: 36, hop: 2, join: true, rows: "1,462",
    cols: [["work_order_id", "uuid", "PK"], ["asset_id", "uuid", "FK"], ["vendor_id", "uuid", "FK"], ["contract_id", "uuid", "FK"], ["trigger", "enum", ""], ["priority", "enum", ""], ["raised_at", "timestamptz", ""], ["closed_at", "timestamptz", ""]] }
];

// from → to, with the relationship and where its label sits
const GRAPH_EDGES = [
  ["building", "site", ":AT_SITE", 0, 0, 1],
  ["building", "location", ":IN_REGION", 0, 0, 1],
  ["site", "portfolio", ":IN_PORTFOLIO", 0, 0, 2],
  ["location", "pack", ":GOVERNED_BY", 0, 0, 2],
  ["floor", "building", ":HAS_FLOOR", 0, 0, 1],
  ["asset", "building", ":HAS_ASSET", 0, 0, 1],
  ["document", "building", ":HAS_DOC", 0, 0, 1],
  ["contract", "building", ":UNDER_CONTRACT", 0, 0, 1],
  ["space", "floor", ":CONTAINS_SPACE", 0, 0, 2],
  ["equipment", "asset", ":HAS_EQUIPMENT", 0, 0, 2],
  ["meter", "asset", ":METERED_BY", 0, 0, 2],
  ["certificate", "document", ":EVIDENCES", 0, 0, 2],
  ["certificate", "asset", ":CERTIFIES", 0, 0, 2],
  ["document", "vendor", ":SUPPLIED_BY", 0, 0, 2],
  ["contract", "vendor", ":WITH_VENDOR", 0, 0, 2],
  ["invoice", "vendor", ":INVOICED_BY", 0, 0, 2],
  ["invoice", "workorder", ":BILLS", 0, 0, 2],
  ["workorder", "asset", ":ASSIGNED_TO", 0, 0, 3],
  ["workorder", "vendor", ":SERVICES", 0, 0, 3],
  ["workorder", "contract", ":RAISED_UNDER", 0, 0, 3]
];

const GB = BUILDINGS;

// Buildings drawn as hubs, placed by hand so the clusters breathe.
// `ang` are the angles its child tables sit at, chosen to face away from
// the rest of the canvas. Screen y is inverted: y = cy - r·sin(a).
const HUBS = [
  { name: "Bishopsgate Tower", cx: 470, cy: 400, r: 62, sat: 176, ang: [96, 142, 188, 234] },
  { name: "Town Hall", cx: 1080, cy: 250, r: 56, sat: 164, ang: [40, 86, 132, 356] },
  { name: "Riverside Court", cx: 560, cy: 930, r: 56, sat: 164, ang: [186, 232, 278, 324] },
  { name: "Marina Heights", cx: 1660, cy: 490, r: 58, sat: 170, ang: [30, 76, 122, 336] },
  { name: "Raffles Link", cx: 1480, cy: 940, r: 54, sat: 200, ang: [242, 300, 358, 184] }
];

// Nodes two or more hubs share — the indirect building-to-building tie.
const SHARED_N = [
  { id: "pack", label: "CIBSE TM46", sub: "pack", cx: 906, cy: 660, r: 46,
    links: ["Bishopsgate Tower", "Town Hall", "Riverside Court"] },
  { id: "vendor", label: "Apex Lifts", sub: "vendor", cx: 128, cy: 700, r: 40,
    links: ["Bishopsgate Tower", "Riverside Court"] },
  { id: "vendor", label: "Gulf Facilities", sub: "vendor", cx: 2320, cy: 750, r: 40,
    links: ["Marina Heights", "Raffles Link"] },
  { id: "vendor", label: "Clearwater", sub: "vendor", cx: 1080, cy: 900, r: 40,
    links: ["Riverside Court", "Marina Heights"] }
];

// A building's direct children — the tables carrying building_id.
const CHILD_OF_BUILDING = [
  { id: "floor", glyph: "FL", rel: ":HAS_FLOOR", unit: "floors" },
  { id: "asset", glyph: "AS", rel: ":HAS_ASSET", unit: "assets" },
  { id: "document", glyph: "DO", rel: ":HAS_DOC", unit: "documents" },
  { id: "contract", glyph: "CO", rel: ":UNDER_CONTRACT", unit: "contracts" }
];

// Stage two of hoisting. Structured rows land in tables; scans and PDFs are
// vectorised and bound to the COLUMN they resemble, never to a table at large.
// Held as classes, not documents — a class is a pattern with a match score.
const VECTOR_CLASSES = {
  building: [{ label: "Site surveys", col: "buildings.name", sim: "0.81", w: 0.03 }],
  floor: [{ label: "Floor plans · fire strategy", col: "floors.level", sim: "0.88", w: 0.06 }],
  asset: [
    { label: "Warranty certificates", col: "assets.serial", sim: "0.91", w: 0.14 },
    { label: "O&M manuals", col: "equipment.manufacturer", sim: "0.84", w: 0.07 }
  ],
  document: [{ label: "Scanned statutory certificates", col: "certificates.cert_type", sim: "0.93", w: 0.24 }],
  contract: [{ label: "Signed contracts · rate schedules", col: "contracts.penalty_clause", sim: "0.86", w: 0.05 }],
  space: [{ label: "Lease plans · fit-out drawings", col: "spaces.tenant_ref", sim: "0.79", w: 0.05 }],
  equipment: [{ label: "Commissioning sheets", col: "equipment.serial", sim: "0.83", w: 0.09 }],
  meter: [{ label: "MPAN consent letters", col: "meters.mpan_mprn", sim: "0.90", w: 0.03 }],
  workorder: [{ label: "Engineer job sheets · site photos", col: "work_orders.raised_at", sim: "0.82", w: 0.13 }],
  certificate: [{ label: "Original certificate scans", col: "certificates.certificate_id", sim: "0.95", w: 0.08 }],
  invoice: [{ label: "Invoice PDFs", col: "invoices.invoice_id", sim: "0.89", w: 0.02 }],
  vendor: [{ label: "Accreditation scans", col: "vendors.name", sim: "0.87", w: 0.01 }]
};

// Files in a class = that share of the building's documents row count.
const VFILES = (v, PB) => Math.max(1, Math.round(PB.document * v.w));

const UNITS = { floor: "floors", asset: "assets", document: "documents", contract: "contracts",
  space: "spaces", equipment: "equipment items", meter: "meters", workorder: "work orders",
  certificate: "certificates", invoice: "invoices" };

// Every row count in the hierarchy comes from here, so a table shows the same
// figure whichever parent you reach it through.
const PER_BUILDING = (b) => {
  const a = AREA(b), fl = b.floors;
  const assets = Math.round(fl * (5 + a / fl / 9000) + a / 26000);
  const docs = Math.round(fl * 1.6 + a / 9000 + 22);
  return {
    floor: fl, asset: assets, document: docs,
    contract: Math.max(2, Math.round(fl / 6) + (b.mix.length > 1 ? 2 : 1)),
    space: Math.round(a / 4200),
    equipment: Math.round(assets * 2.1),
    meter: Math.max(1, Math.ceil(assets / 58)),
    workorder: Math.round(assets * 0.52),
    certificate: Math.round(docs * 0.64),
    invoice: Math.round(assets * 0.86)
  };
};

// "412,000 ft²" → 412000
const AREA = (b) => parseFloat(String(b.area).replace(/[^0-9.]/g, "")) || 0;
const NUM = (n) => String(n).replace(/\B(?=(\d{3})+(?!\d))/g, ",");

const SUB_OF = {
  floor: [{ id: "space", glyph: "SP", rel: ":CONTAINS_SPACE", on: "FK floor_id", join: true }],
  asset: [
    { id: "equipment", glyph: "EQ", rel: ":HAS_EQUIPMENT", on: "FK asset_id", join: true },
    { id: "meter", glyph: "ME", rel: ":METERED_BY", on: "FK asset_id", join: true },
    { id: "workorder", glyph: "WO", rel: ":ASSIGNED_TO", on: "2 FKs", join: true }
  ],
  document: [{ id: "certificate", glyph: "CE", rel: ":EVIDENCES", on: "FK document_id", join: true }],
  contract: [
    { id: "workorder", glyph: "WO", rel: ":RAISED_UNDER", on: "FK contract_id", join: true },
    { id: "invoice", glyph: "IN", rel: ":INVOICED_BY", on: "FK vendor_id", join: true }
  ]
};

// Nodes shared by several buildings — the indirect building-to-building tie.
const SHARED = [
  { id: "pack", kind: "pack", cc: "UK", glyph: "UK", label: "CIBSE TM46", note: "6 buildings on this pack", buildings: [] },
  { id: "pack", kind: "pack", cc: "US", glyph: "US", label: "Energy Star · ASHRAE", note: "1 building on this pack", buildings: [] },
  { id: "pack", kind: "pack", cc: "AE", glyph: "AE", label: "Rolling benchmark", note: "1 building — no national standard", buildings: [] },
  { id: "pack", kind: "pack", cc: "SG", glyph: "SG", label: "BCA Benchmarking", note: "1 building on this pack", buildings: [] },
  { id: "vendor", kind: "vendor", glyph: "AP", label: "Apex Lifts", note: "serves 3 buildings", buildings: ["Bishopsgate Tower", "Kingsway House", "Riverside Court"] },
  { id: "vendor", kind: "vendor", glyph: "MH", label: "Meridian Heating", note: "serves 3 · blocked", buildings: ["Town Hall", "AN Other House", "Meridian Quay"] },
  { id: "vendor", kind: "vendor", glyph: "GF", label: "Gulf Facilities", note: "serves 2 buildings", buildings: ["Marina Heights", "Raffles Link"] },
  { id: "vendor", kind: "vendor", glyph: "EM", label: "Empire Mechanical", note: "serves 2 buildings", buildings: ["Northgate Mall", "Congress Plaza"] }
];

const REGIONS = {
  UK: ["Greater London", "Greater Manchester", "West Midlands", "Scotland", "Wales", "Northern Ireland"],
  US: ["New York", "Texas", "California", "Illinois", "Florida", "Massachusetts"],
  AE: ["Dubai", "Abu Dhabi", "Sharjah", "Ras Al Khaimah"],
  SG: ["Central Region", "East Region", "North Region", "West Region"]
};

/* Each pack carries the legal standing of the standard it benchmarks against,
   because a guidance benchmark, an enacted duty and a proposal that never
   became law cannot be read off the same number. */
const PACKS = {
  UK: { flag: "🇬🇧", name: "United Kingdom", std: "CIBSE TM46",
    note: "guidance · EPC E law, EPC B proposed 2031", tone: "warn" },
  US: { flag: "🇺🇸", name: "United States", std: "Energy Star · ASHRAE 100",
    note: "enacted, city-scoped · NYC LL97", tone: "ok" },
  AE: { flag: "🇦🇪", name: "UAE", std: "NA",
    note: "no operational standard · portfolio benchmark", tone: "dormant" },
  SG: { flag: "🇸🇬", name: "Singapore", std: "BCA Benchmarking Report",
    note: "submission mandatory · rating voluntary", tone: "ok" }
};

/* Country attribution for energy rows. "Building 5" is a UK site that predates
   the canonical naming and is mapped explicitly rather than guessed. */
const CC_OF = (function () {
  const m = { "Building 5": "UK" };
  BUILDINGS.forEach(function (b) { m[b.name] = b.cc; });
  return m;
})();

/* What the energy engine can actually say, per market. Capability is a function
   of the data route, not of ambition: a market with no interval feed and no
   national benchmark cannot support the same analysis as a half-hourly settled
   one, and mixing markets collapses the analysis to what they share. */
const ENC = {
  UK: {
    tariff: 0.284, tariffLabel: "28.4p/kWh contracted",
    routes: ["Half-hourly data collector under Letter of Authority", "SMETS2 via a Smart Energy Code intermediary"],
    grain: "Half-hourly · sub-metered on 2 of 6 sites",
    avail: [
      "Half-hourly profile analysis — controls signatures separable from demand",
      "Weather correction on heating degree days",
      "CIBSE TM46 benchmark by use class",
      "MEES position: EPC E enforceable now, EPC B proposed 2031",
      "ESOS evidence pack export"
    ],
    held: ["Plant-level attribution on the four building-level sites — cause is inferred"]
  },
  US: {
    tariff: 0.152, tariffLabel: "15.2p/kWh equivalent",
    routes: ["Green Button Connect My Data through an aggregator"],
    grain: "15-minute interval where the utility runs CMD",
    avail: [
      "Energy Star score and ASHRAE 100 target",
      "NYC Local Law 97 emissions limit and penalty exposure",
      "Interval analysis on supplies where CMD is live"
    ],
    held: [
      "Tenant-level aggregation below the utility's aggregation threshold",
      "Comparison across utilities — each publishes its own interval basis",
      "Any national benchmark: LL97 is city-scoped, not federal"
    ]
  },
  AE: {
    tariff: 0.096, tariffLabel: "9.6p/kWh equivalent",
    routes: ["Own sub-meters and BMS trends"],
    grain: "Sub-metered · 15-minute BMS trend logs",
    avail: [
      "Rolling benchmark against comparable buildings in the portfolio",
      "Cooling degree day normalisation",
      "Plant-level attribution from BMS trends"
    ],
    held: [
      "National or emirate benchmark — none is published for operation",
      "Utility interval data — DEWA gives billing-period consumption only",
      "Regulatory exposure: Estidama and Al Sa'fat rate new build, not operation"
    ]
  },
  SG: {
    tariff: 0.198, tariffLabel: "19.8p/kWh equivalent",
    routes: ["Contracted retailer feed against the SP advanced meter"],
    grain: "Half-hourly · sub-metered",
    avail: [
      "BCA Benchmarking Report submission pack",
      "Half-hourly profile analysis",
      "Green Mark context for retrofit cases"
    ],
    held: ["Continuity past retail contract expiry — the data clause has to be restated at renewal"]
  }
};

/* The three things that change with every regulation: what the building is
   measured against, how its consumption data arrives, and what the energy
   costs and is billed in. One profile per market, one shared attribute order,
   so markets can be laid side by side without pretending they are alike. */
const EN_ATTRS = [
  ["Benchmark", "Standard", "std"], ["Benchmark", "Reference EUI", "ref"], ["Benchmark", "Unit of measure", "unit"],
  ["Data source", "Route", "route"], ["Data source", "Refresh", "refresh"], ["Data source", "Per meter · per section", "per"],
  ["Data source", "Identifier", "ident"], ["Data source", "Known limits", "limits"],
  ["Commercial", "Electricity", "elec"], ["Commercial", "Gas", "gas"], ["Commercial", "Other utilities", "other"], ["Commercial", "Currency", "cur"]
];

const EN_PROFILE = {
  UK: {
    std: "CIBSE TM46 by use class", ref: "180 kWh/m²/yr weighted · offices 215, retail 165, residential 150", unit: "kWh/m²/yr",
    route: "Half-hourly data collector under LoA · SMETS2 via SEC intermediary", refresh: "Daily, D+1 · half-hourly resolution",
    per: "Per MPAN and MPRN · per section only where sub-metered (2 of 6)", ident: "MPAN (electricity) · MPRN (gas)",
    limits: "Consent per supply point · SMETS1 sites wait on DCC migration",
    elec: "28.4p/kWh contracted", gas: "7.1p/kWh · m³ converted to kWh by calorific value", other: "—", cur: "GBP"
  },
  US: {
    std: "Energy Star score · ASHRAE 100 · NYC LL97 emissions limit", ref: "Energy Star 75 for certification · LL97 limit by occupancy group", unit: "kBtu/ft²/yr site EUI · tCO₂e/ft² for LL97",
    route: "Green Button Connect My Data via aggregator", refresh: "Daily · 15-minute interval where CMD is live",
    per: "Per utility account and service point · tenant aggregate only above the utility threshold", ident: "Utility account + service point ID · no national identifier",
    limits: "CMD availability varies by utility · aggregation thresholds",
    elec: "$0.22/kWh commercial, Con Edison", gas: "$1.40/therm · billed in therms, not kWh", other: "District steam in Mlb", cur: "USD"
  },
  SG: {
    std: "BCA Building Energy Benchmarking Report", ref: "192 kWh/m²/yr office reference", unit: "kWh/m²/yr",
    route: "Contracted retailer feed against the SP advanced meter", refresh: "Daily · half-hourly resolution",
    per: "Per SP account and meter · per section via own sub-meters", ident: "SP account number + meter serial · no national supply-point ID",
    limits: "Feed ends at retail contract expiry · SP takes no third-party requests",
    elec: "S$0.30/kWh contestable commercial", gas: "Town gas by City Energy, in kWh", other: "District cooling in RTh where connected", cur: "SGD"
  },
  AE: {
    std: "None for operation · Estidama and Al Sa'fat rate new build", ref: "228 kWh/m²/yr rolling portfolio benchmark", unit: "kWh/m²/yr · cooling in RTh",
    route: "Own sub-meters and BMS trends", refresh: "15-minute BMS trends, live · DEWA billing monthly",
    per: "Per sub-meter · DEWA at premise level only", ident: "DEWA premise and account number · tenant-held on most leases",
    limits: "No utility interval feed · account holder is often the tenant",
    elec: "44.5 fils/kWh commercial slab incl. fuel surcharge", gas: "LPG by cylinder or kg · no piped network", other: "District cooling in RTh · water in imperial gallons", cur: "AED"
  }
};

/* Ratings and duties are country-scoped by definition, so they render only
   when a single market is selected. A market with no operational scheme says so
   rather than borrowing a neighbour's. */
/* `basis` decides how a rating earns the word "actual": a certificate or a
   filing is actual the day it is on file; a consumption-based figure is actual
   only once its 12-month window is complete, projected from 3 months, and not
   shown at all below that. Confidence rises with every month landed. */
const EN_RATINGS = {
  UK: [
    { l: "MEES — enforceable now", v: "0", s: "below EPC E · all 6 lettable", tone: "ok", basis: "certificate" },
    { l: "MEES — proposed 2031", v: "2", s: "below EPC B · both over 1,000 m²", tone: "risk", basis: "certificate" },
    { l: "EPCs on file", v: "6 / 6", s: "current, none expiring inside 12 months", tone: "ok", basis: "certificate" }
  ],
  US: [
    { l: "LL97 2024–29 limit", v: "Within", s: "Northgate Mall · 1 of 1 under its occupancy-group cap", tone: "ok", basis: "consumption", months: 8 },
    { l: "Energy Star score", v: "71", s: "certification at 75 · 4 points short", tone: "warn", basis: "consumption", months: 12 },
    { l: "LL84 benchmarking", v: "Filed", s: "annual submission on record", tone: "ok", basis: "filing" }
  ],
  SG: [
    { l: "BCA energy submission", v: "Filed", s: "mandatory annual return · 1 of 1", tone: "ok", basis: "filing" },
    { l: "EUI vs BCA reference", v: "−8%", s: "176 against 192 kWh/m²/yr", tone: "ok", basis: "consumption", months: 12 },
    { l: "Green Mark", v: "None", s: "voluntary · not certified", tone: "dormant", basis: "certificate" }
  ],
  AE: [
    { l: "Operational rating", v: "None", s: "Estidama and Al Sa'fat rate new build only", tone: "dormant", basis: "scheme" },
    { l: "EUI vs rolling benchmark", v: "+8%", s: "246 against 228 kWh/m²/yr · cooling-degree-day normalised", tone: "warn", basis: "consumption", months: 5 },
    { l: "Chiller plant kW/RT", v: "0.81", s: "design 0.68 · CH-2 drifting", tone: "risk", basis: "consumption", months: 2 }
  ]
};

function ratingState(r) {
  if (r.basis === "certificate") return { badge: "Actual · from certificate", fg: "var(--st-ok)", conf: 100, show: "none", ok: true };
  if (r.basis === "filing") return { badge: "Actual · from filing", fg: "var(--st-ok)", conf: 100, show: "none", ok: true };
  if (r.basis === "scheme") return { badge: "No scheme to measure against", fg: "var(--st-dormant)", conf: 0, show: "none", ok: true };
  const mo = r.months || 0;
  if (mo >= 12) return { badge: "Actual · 12 of 12 months", fg: "var(--st-ok)", conf: 100, show: "block", ok: true };
  if (mo >= 3) { const c = 45 + (mo - 3) * 5; return { badge: "Projected · " + mo + " of 12 months · " + c + "% confidence", fg: "var(--st-warn)", conf: c, show: "block", ok: true }; }
  return { badge: "Insufficient data · " + mo + " of 12 months · shown from 3", fg: "var(--st-risk)", conf: Math.round(mo * 10), show: "block", ok: false };
}

const ENC_MIXED_AVAIL = [
  "Each building scored against its own country pack",
  "Anomaly detection relative to each building's own baseline",
  "Cost normalised to the reporting currency at the month-end rate"
];

const ENC_MIXED_HELD = [
  "One ranking on a single benchmark — the packs measure different things",
  "Portfolio-wide half-hourly pattern analysis — the routes differ, so granularity is not comparable",
  "A single regulatory exposure number — duties are country and city scoped",
  "Tenant-level detail in any market that withholds it"
];

// Every action card in the product resolves to one of three shapes:
//   retrieve  — the graph already holds it, so the orchestrator just states it
//   ask       — inputs the user must supply (a slot, an address, a priority)
//   draft     — an email put up for approve / edit / cancel
// A spec declares the inputs it needs and the letter it ends with. `custom`
// hands off to the three bespoke flows (booking form, contractor picker).
const ORG = "Planum Technologies";
const ACTION_SPECS = [
  { k: "booking", m: [/approve booking/, /assign contractor/, /approve all/, /approve work order/, /create inspection wo/], custom: "booking" },
  { k: "pick", m: [/change contractor/], custom: "pick" },
  { k: "extend", m: [/extend deadline/, /extend the deadline/],
    inputs: [{ key: "to", label: "Authority email", type: "text", ph: "compliance@authority.gov.uk" }, { key: "days", label: "Extension sought", type: "select", options: ["30 days", "14 days", "60 days"] }],
    mail: (v, sub, ven) => ({
      kicker: "Extension request · draft", to: v.to,
      subject: "Request to extend compliance deadline — " + sub,
      body: "Dear Sir or Madam,\n\nWe are writing to request an extension of " + v.days + " to the compliance deadline for " + sub + ".\n\nThe responsible contractor is " + ven + ". A booking has been drafted and the works are scheduled, but the current expiry falls before the earliest available attendance date.\n\nWe will provide the satisfactory certificate immediately on completion.\n\nYours faithfully,\n" + ORG
    }),
    done: (v) => "Extension request sent to " + (v.to || "the authority") + ". The obligation is marked as contested pending their reply."
  },
  { k: "reassign", m: [/reassign/],
    inputs: [
      { key: "vendor", label: "Reassign to", type: "select", options: ["Apex Lifts", "Northgate Electrical", "Clearwater Compliance"] },
      { key: "date", label: "Attendance date", type: "date", seed: "2026-09-16" },
      { key: "window", label: "Window · 24h", type: "text", seed: "08:00–12:00" }
    ],
    mail: (v, sub) => ({
      kicker: "Reassignment instruction · draft", to: "ops@" + v.vendor.toLowerCase().replace(/[^a-z]/g, "") + ".co.uk",
      subject: "Work order reassignment — " + sub,
      body: "Dear " + v.vendor + ",\n\nThe work orders relating to " + sub + " have been reassigned to you with immediate effect.\n\nAttendance: " + v.date + ", " + v.window + "\n\nYour accreditation for this asset type has been verified against the issuing register. Please confirm attendance by return and upload the certificate on completion.\n\nIssued under the existing framework agreement at contracted rates.\n\nKind regards,\n" + ORG
    }),
    done: (v) => "Work orders reassigned to " + v.vendor + " and the instruction sent. The outgoing vendor's scorecard is annotated."
  },
  { k: "evidence", m: [/request evidence/, /request quotation/, /request a quote/],
    inputs: [{ key: "to", label: "Vendor email", type: "text", ph: "ops@vendor.co.uk" }, { key: "by", label: "Response required by", type: "date", seed: "2026-09-09" }],
    mail: (v, sub) => ({
      kicker: "Evidence request · draft", to: v.to,
      subject: "Evidence request — " + sub,
      body: "Dear Sir or Madam,\n\nWe require documentary evidence in relation to " + sub + ".\n\nPlease provide attendance records, engineer accreditation references and the completion documentation for the affected work orders by " + v.by + ".\n\nWhere evidence is not received by that date the associated invoice lines will be held and service credits calculated under the penalty clause.\n\nKind regards,\n" + ORG
    }),
    done: (v) => "Evidence request sent to " + (v.to || "the vendor") + ". Affected invoice lines are held pending the reply."
  },
  { k: "credit", m: [/credit note/, /claim/, /challenge/, /service credit/],
    inputs: [{ key: "to", label: "Vendor email", type: "text", ph: "accounts@vendor.co.uk" }, { key: "amount", label: "Amount claimed", type: "text", seed: "£2,100" }],
    mail: (v, sub) => ({
      kicker: "Credit claim · draft", to: v.to,
      subject: "Service credit claim — " + sub,
      body: "Dear Sir or Madam,\n\nWe are claiming service credits of " + v.amount + " in relation to " + sub + ".\n\nThe claim is calculated under the penalty clause of the framework agreement against recorded SLA breaches. Attendance timestamps, work order references and the clause calculation are attached.\n\nPlease raise a credit note against the current period and confirm by return.\n\nKind regards,\n" + ORG
    }),
    done: (v) => "Credit claim for " + v.amount + " sent to " + (v.to || "the vendor") + ". Recovery is tracked in Vendor Performance."
  },
  { k: "inspect", m: [/inspect/, /raise wo/, /raise work order/, /book$/, /^book/],
    inputs: [
      { key: "vendor", label: "Contractor", type: "select", options: ["Apex Lifts", "Northgate Electrical", "Clearwater Compliance"] },
      { key: "date", label: "Attendance date", type: "date", seed: "2026-09-16" },
      { key: "priority", label: "Priority", type: "select", options: ["P2 — next business day", "P1 — emergency", "P3 — routine"] }
    ],
    mail: (v, sub) => ({
      kicker: "Inspection instruction · draft", to: "ops@" + v.vendor.toLowerCase().replace(/[^a-z]/g, "") + ".co.uk",
      subject: "Inspection instruction — " + sub,
      body: "Dear " + v.vendor + ",\n\nPlease attend to inspect and report on " + sub + ".\n\nDate: " + v.date + "\nPriority: " + v.priority + "\n\nThe asset has been flagged by our monitoring against its own baseline and its peer group. Please report findings and any remedial recommendation with your completion documentation.\n\nKind regards,\n" + ORG
    }),
    done: (v) => "Inspection raised with " + v.vendor + " at " + v.priority + ". The work order is live and the asset is watched until it clears."
  },
  { k: "priority", m: [/change priority/],
    inputs: [{ key: "priority", label: "New priority", type: "select", options: ["P1 — emergency", "P2 — next business day", "P3 — routine"] }, { key: "to", label: "Vendor email", type: "text", seed: "ops@meridianheating.co.uk" }],
    mail: (v, sub) => ({
      kicker: "Priority change · draft", to: v.to,
      subject: "Priority change — " + sub,
      body: "Dear Sir or Madam,\n\nThe priority of the work order relating to " + sub + " has been changed to " + v.priority + ".\n\nPlease adjust your attendance accordingly. SLA response and completion windows are recalculated from this notice.\n\nKind regards,\n" + ORG
    }),
    done: (v) => "Priority set to " + v.priority + " and the vendor notified. SLA windows recalculated."
  },
  { k: "report", m: [/export/, /owner report/, /data pack/, /decision log/],
    inputs: [{ key: "to", label: "Send to", type: "text", seed: "asset.committee@ashcombe.com" }, { key: "format", label: "Format", type: "select", options: ["PDF pack", "XLSX + evidence", "Both"] }],
    mail: (v, sub) => ({
      kicker: "Report circulation · draft", to: v.to,
      subject: sub + " — dated pack",
      body: "Dear all,\n\nPlease find the requested pack for " + sub + ", generated from the Hoist Graph as at today's date and issued as " + v.format + ".\n\nEvery figure in the pack carries its source record and the agent chain that produced it, so any line can be traced back to the underlying certificate, meter reading or invoice.\n\nKind regards,\n" + ORG
    }),
    done: (v) => "Pack issued to " + (v.to || "the recipient") + " as " + v.format + " and pinned to the session."
  },
  { k: "ack", m: [/acknowledge/, /monitor/, /override as expected/, /approve as charged/],
    noMail: true,
    done: (v, label, sub) => label + " recorded against " + sub + ". No communication issued; the entry stays in the Activity Log with actor and timestamp."
  }
];

const CC = HOISTRA_CC;
const VP = HOISTRA_VP;
// Service package each vendor sits in — the division the portfolio is bought by.
const PKG = { v1: "MEP · HVAC", v2: "MEP · gas and heating", v3: "MEP · electrical", v4: "Elevators", v5: "Fire and life safety", v6: "Water hygiene" };
const TAG = {
  risk: { bg: "var(--st-risk-bg)", fg: "var(--st-risk)" },
  warn: { bg: "var(--st-warn-bg)", fg: "var(--st-warn)" },
  ok: { bg: "var(--st-ok-bg)", fg: "var(--st-ok)" },
  none: { bg: "var(--color-neutral-900)", fg: "var(--color-neutral-400)" }
};
const MK = {
  ok: { bg: "var(--st-ok-bg)", fg: "var(--st-ok)", border: "1px solid transparent" },
  warn: { bg: "var(--st-warn-bg)", fg: "var(--st-warn)", border: "1px solid transparent" },
  risk: { bg: "var(--st-risk-bg)", fg: "var(--st-risk)", border: "1px solid transparent" },
  gap: { bg: "transparent", fg: "var(--st-risk)", border: "1.5px dashed color-mix(in srgb, var(--st-risk) 45%, transparent)" },
  na: { bg: "repeating-linear-gradient(45deg, var(--color-neutral-900), var(--color-neutral-900) 3px, transparent 3px, transparent 6px)", fg: "transparent", border: "1px solid transparent" }
};

const VENDOR_POOL = [
  { name: "Apex Lifts", spec: "Lifts — LOLER", acc: "LEIA current to 04/27", email: "ops@apexlifts.co.uk" },
  { name: "Northgate Electrical", spec: "Electrical — NICEIC", acc: "NICEIC current to 11/26", email: "dispatch@northgate-electrical.co.uk" },
  { name: "Clearwater Compliance", spec: "Water hygiene — L8", acc: "LCA current to 02/27", email: "bookings@clearwatercompliance.co.uk" }
];

const CRONS = [
  { text: "CP12 at Town Hall lapsed 6 days ago — no booking on record", agent: "Compliance", t: "02:14", dot: "var(--st-risk)", action: "Assign" },
  { text: "CHILLER-101 drew 34% above its peer group overnight", agent: "Energy", t: "02:31", dot: "var(--st-warn)", action: "Raise WO" },
  { text: "Meridian Lifts missed 6 P2 windows — £2,100 recoverable", agent: "Vendor", t: "02:48", dot: "var(--st-warn)", action: "Claim" },
  { text: "24 buildings re-benchmarked against their regulation packs", agent: "Orchestrator", t: "03:02", dot: "var(--st-ok)", action: null },
  { text: "AHU-7 filter change deferred twice — static pressure rising", agent: "Assets", t: "03:19", dot: "var(--st-warn)", action: "Raise WO" },
  { text: "EICR overdue at AN Other House — circuit split unverified", agent: "Compliance", t: "03:34", dot: "var(--st-risk)", action: "Escalate" },
  { text: "Wet riser pump under design load at Riverside Court", agent: "Assets", t: "03:51", dot: "var(--st-risk)", action: "Inspect" },
  { text: "8 invoice lines matched and auto-approved", agent: "Vendor", t: "04:07", dot: "var(--st-ok)", action: null },
  { text: "Service charge recovery re-estimated for Q3", agent: "Orchestrator", t: "04:22", dot: "var(--st-ok)", action: null },
  { text: "LOLER certificate ingested for Lift Asset-4471", agent: "Compliance", t: "04:38", dot: "var(--st-ok)", action: null },
  { text: "Half-hourly MPAN feeds reconciled — 22 of 24 buildings", agent: "Energy", t: "04:51", dot: "var(--st-ok)", action: null },
  { text: "Hoist Score recomputed — 78, unchanged", agent: "Orchestrator", t: "05:03", dot: "var(--st-ok)", action: null },
  { text: "PPM calendar refreshed for October", agent: "Assets", t: "05:16", dot: "var(--st-ok)", action: null },
  { text: "3 vendor accreditations verified against issuing registers", agent: "Vendor", t: "05:29", dot: "var(--st-ok)", action: null },
  { text: "Bishopsgate weather-corrected baseline rebuilt", agent: "Energy", t: "05:44", dot: "var(--st-ok)", action: null },
  { text: "L8 flushing regime logged at Building 5", agent: "Compliance", t: "22:41", dot: "var(--st-ok)", action: null, day: "Yesterday" },
  { text: "Boiler-22 combustion efficiency below design", agent: "Energy", t: "21:08", dot: "var(--st-warn)", action: "Raise WO", day: "Yesterday" },
  { text: "F-Gas register cross-checked — 14 assets current", agent: "Compliance", t: "19:52", dot: "var(--st-ok)", action: null, day: "Yesterday" },
  { text: "Northgate Mall service charge pack issued", agent: "Orchestrator", t: "17:20", dot: "var(--st-ok)", action: null, day: "Yesterday" },
  { text: "Asbestos re-inspection due at Kingsway House", agent: "Compliance", t: "23:15", dot: "var(--st-warn)", action: "Book", day: "31 Aug" },
  { text: "Q2 vendor scorecards archived", agent: "Vendor", t: "20:33", dot: "var(--st-ok)", action: null, day: "31 Aug" }
];

const TONE = {
  risk: { color: "var(--st-risk)", bg: "var(--st-risk-bg)" },
  warn: { color: "var(--st-warn)", bg: "var(--st-warn-bg)" },
  ok:   { color: "var(--st-ok)",   bg: "var(--st-ok-bg)" }
};
const t = (k) => TONE[k] || TONE.ok;

const MODULES = {
  compliance: {
    name: "Compliance", kicker: "Feature A · compliance-engine", icon: "ph-shield-check", answer: "compliance",
    blurb: "Every statutory obligation in the building’s regulation pack, tracked against the ComplianceCertificate entity and the accreditation currency of the vendor who has to do the work.",
    scanLabel: "Run compliance scan", exportLabel: "Export compliance pack",
    tableTitle: "Certificates by building and category",
    head: ["Building", "Certificate type", "Asset", "Expiry", "Status", "Responsible vendor"],
    tableFoot: "118 obligations tracked. Alert ladder: 90d informational · 60d contractor suggested · 30d booking drafted · 7d escalation.",
    sideTitle: "Coverage by category", sideFoot: "A category is Current only when every tracked obligation in it is inside its renewal window.",
    filters: ["All", "Lapsed & overdue", "Inside 30 days"],
    asks: ["Which lapses void insurance?", "Show me every obligation with no contractor on record", "What did the 60-day ladder book last month?"]
  },
  energy: {
    name: "Energy", kicker: "Feature C · energy-intelligence-engine", icon: "ph-lightning", answer: "energy",
    blurb: "Half-hourly MPAN and MPRN readings aggregated into EUI per building, benchmarked against the regulation pack for each country — CIBSE TM46, Energy Star and ASHRAE 100, BCA, or a rolling portfolio benchmark — then priced before anything reaches you. Anomaly scan daily at 03:00.",
    scanLabel: "Run energy scan", exportLabel: "Export ESOS data pack",
    tableTitle: "Anomalies ranked by annualised cost impact",
    head: ["Building", "Asset / circuit", "Anomaly type", "Annualised £", "Status", "Days active"],
    tableFoot: "Rules: (a) non-occupancy spike >30% of occupied average · (b) single-asset spike vs sub-meter baseline · (c) baseline drift week-on-week.",
    sideTitle: "EUI vs TM46 benchmark", sideFoot: "Bar length is EUI against the 180 kWh/m²/yr benchmark. Over-benchmark buildings carry the portfolio's £312k gap.",
    filters: ["All", "Over benchmark", "With anomalies", "New", "Above £20k"],
    asks: ["Why did Bishopsgate spike on Saturday?", "Which buildings miss EPC B by 2031?", "Rank buildings by cost per m²"]
  },
  vendors: {
    name: "Vendor performance", kicker: "Feature B · contract-performance-engine", icon: "ph-chart-line-up", answer: "vendors",
    blurb: "SLA targets, rates and penalty clauses extracted from the contracts you ingested, then every completed work order scored against them. Failures on L1 assets carry 3× weight.",
    scanLabel: "Rebuild scorecards", exportLabel: "Export scorecard pack",
    tableTitle: "Monthly scorecard — August 2026",
    head: ["Vendor", "Score", "Trend", "Annual spend", "Accreditation", "Flag"],
    tableFoot: "Score weighting: SLA response 25% · SLA completion 25% · first fix 20% · recall 15% · accreditation 15%. Lapsed accreditation caps the score at 60.",
    sideTitle: "SLA completion vs 95% target", sideFoot: "Completion is the component driving this month's movement. Response times are broadly held; completion is not.",
    filters: ["All", "Below 80", "Declining"],
    asks: ["Which vendor is costing me money?", "Show every invoice line flagged this quarter", "What service credits can I recover?"]
  },
  ops: {
    name: "Vendor operations", kicker: "Feature D · work-order-engine", icon: "ph-wrench", answer: "queue",
    blurb: "Optional section, depending on the operating structure of the building management chain. Work orders are generated here — from PPM cycles mapped to assets, ad-hoc triggers from EUI asset status, and requests read from Outlook — not raised by the FM operative. The FM company works inside the parameters you set.",
    scanLabel: "Predictive maintenance scan", exportLabel: "Open PPM calendar",
    tableTitle: "Live work orders",
    head: ["Work order", "Asset", "Building", "Estimate", "Status", "Next step"],
    tableFoot: "Lifecycle: Draft → Approved → Assigned → In Progress → Completed → Verified → Closed. Invoices and completion photographs are ingested through the query bar; statutory jobs require a certificate, which lands in the Hoist Graph.",
    sideTitle: "Building health score", sideFoot: "Composite of PPM compliance, open reactive volume and compliance coverage per building.",
    filters: ["All", "Awaiting approval", "Blocked"],
    asks: ["What is awaiting my approval?", "Which assets failed twice in 90 days?", "Show the PPM calendar for October"]
  }
};

export { KL, USE_TINT, BUILDINGS, GRAPH, GRAPH_EDGES, GB, HUBS, SHARED_N, CHILD_OF_BUILDING, VECTOR_CLASSES, VFILES, UNITS, PER_BUILDING, AREA, NUM, SUB_OF, SHARED, REGIONS, PACKS, CC_OF, ENC, EN_ATTRS, EN_PROFILE, EN_RATINGS, ratingState, ENC_MIXED_AVAIL, ENC_MIXED_HELD, ORG, ACTION_SPECS, CC, VP, PKG, TAG, MK, VENDOR_POOL, CRONS, TONE, t, MODULES };
