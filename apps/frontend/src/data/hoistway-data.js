/* Hoistway — shared demo dataset for both frontend options.
   Portfolio: Plenum Technologies, 24 commercial buildings, UK regulatory scope. */
export const HOISTWAY = {
  tenant: "Plenum Technologies",
  portfolio: { buildings: 24, area: "1.84m ft²", gav: "£1.42bn" },

  headline: [
    { k: "compliance", label: "Compliance exposure", value: "3", unit: "lapsed", note: "11 expiring <30d · 2 vendors blocked", tone: "risk" },
    { k: "energy", label: "Energy excess vs CIBSE", value: "£312k", unit: "/yr", note: "Portfolio EUI 18% above TM46 benchmark", tone: "warn" },
    { k: "vendors", label: "Vendor leakage caught", value: "£1,450", unit: "flagged", note: "2 invoice lines · 7 SLA breaches this month", tone: "warn" },
    { k: "ops", label: "Work orders live", value: "148", unit: "open", note: "14 awaiting your approval · PPM 92%", tone: "ok" }
  ],

  pnl: [
    { head: "Maintenance", budget: "£4.10m", actual: "£3.86m", delta: "-5.9%", tone: "ok" },
    { head: "Energy", budget: "£2.74m", actual: "£3.05m", delta: "+11.3%", tone: "risk" },
    { head: "Compliance", budget: "£0.62m", actual: "£0.58m", delta: "-6.5%", tone: "ok" },
    { head: "Unplanned failure", budget: "£0.30m", actual: "£0.19m", delta: "-36.7%", tone: "ok" }
  ],

  decisions: [
    {
      id: "d1", module: "Compliance", icon: "ph-shield-check", tone: "warn",
      title: "LOLER certificate for Lift Asset-4471 expires in 23 days",
      meta: "Bishopsgate Tower · Level 12 plant · nightly compliance scan 02:14",
      money: "Statutory · insurance void if lapsed",
      body: "Contractor recommendation: Apex Lifts (current LEIA member, last inspection satisfactory). Booking request drafted for 14–18 Sep window.",
      fields: [
        { l: "Certificate type", v: "LOLER thorough examination" },
        { l: "Asset", v: "Lift Asset-4471 · criticality L1" },
        { l: "Expiry date", v: "21 Sep 2026" },
        { l: "Proposed contractor", v: "Apex Lifts · LEIA current", editable: true },
        { l: "Booking window", v: "14–18 Sep 2026", editable: true },
        { l: "Escalation deadline", v: "07 Sep 2026", editable: true }
      ],
      chain: [
        { a: "Orchestrator", t: "Nightly cron 02:00 → intent: compliance-engine, scope: all buildings" },
        { a: "Planner", t: "5 sub-tasks: fetch certs <90d → compute alert ladder → fetch approved contractors for AssetType Lift → draft booking → write Activity Log" },
        { a: "Worker", t: "Read 118 ComplianceCertificate records. 14 inside 90d window, 1 inside 30d. Matched 3 LEIA contractors on Contract entity." },
        { a: "Quality", t: "Validated Apex Lifts LEIA accreditation currency against issuing register. Confidence 97%. Approved → human queue." }
      ],
      refinement: "Three other LOLER certificates at Bishopsgate Tower expire within 90 days — consolidate inspection visits to reduce call-out costs? Estimated saving £420.",
      actions: ["Approve booking", "Change contractor", "Extend deadline"]
    },
    {
      id: "d2", module: "Compliance", icon: "ph-prohibit", tone: "risk",
      title: "Gas Safe registration for Meridian Heating Ltd expired 3 days ago",
      meta: "Portfolio-wide · allocation blocked for all gas work orders",
      money: "2 pending work orders held",
      body: "Vendor score capped at 60 until accreditation is restored. WO-4512 (Town Hall boiler) and WO-4519 (Meridian Quay) require reassignment.",
      fields: [
        { l: "Vendor", v: "Meridian Heating Ltd" },
        { l: "Accreditation", v: "Gas Safe · 604412 · expired 26 Aug 2026" },
        { l: "Blocked work types", v: "Gas appliance, boiler, flue, LPG" },
        { l: "Held work orders", v: "WO-4512, WO-4519" },
        { l: "Reassign to", v: "Northgate Mechanical · Gas Safe current", editable: true }
      ],
      chain: [
        { a: "Orchestrator", t: "Nightly accreditation scan → intent: compliance-engine (A2)" },
        { a: "Planner", t: "Cross-check every Vendor accreditation expiry against open WO AssetTypes" },
        { a: "Worker", t: "1 lapse found. 2 open WOs matched to blocked AssetType. Allocation lock written." },
        { a: "Quality", t: "Irreversible consequence (vendor block) — validated lapse against Gas Safe Register. Confidence 100%. Approved." }
      ],
      refinement: "Meridian Heating holds 4 other contracts in the portfolio worth £186k/yr. Flag for contract review at renewal?",
      actions: ["Reassign work orders", "Request evidence from vendor", "Acknowledge"]
    },
    {
      id: "d3", module: "Energy", icon: "ph-lightning", tone: "warn",
      title: "Weekend non-occupancy spike — Bishopsgate Tower",
      meta: "Saturday 02:00–06:00 · 847 kWh vs 210 kWh weekend baseline",
      money: "£38,400/yr if recurring",
      body: "Likely cause: HVAC setpoint override or controls fault. Sub-meter attribution points to AHU-3 and the level 4 fan coil circuit.",
      fields: [
        { l: "Building", v: "Bishopsgate Tower · 142,000 ft²" },
        { l: "Anomaly type", v: "Weekend / non-occupancy spike" },
        { l: "Deviation", v: "+303% vs weekend baseline" },
        { l: "Annualised cost impact", v: "£38,400 at 28.4p/kWh" },
        { l: "Days active", v: "3 consecutive weekends" },
        { l: "Action", v: "Create inspection WO — AHU-3 controls", editable: true }
      ],
      chain: [
        { a: "Orchestrator", t: "Daily energy scan 03:00 → intent: energy-intelligence-engine" },
        { a: "Planner", t: "Fetch MeterReading (half-hourly, MPAN) → compute EUI → run anomaly rules → translate to £ → check WO threshold" },
        { a: "Worker", t: "Rule (a) non-occupancy spike triggered: 847 kWh vs 210 kWh baseline. Sub-meter attribution: AHU-3 68% of delta." },
        { a: "Quality", t: "Display-only output — no Quality gate. Gate applies only if WO auto-dispatches." }
      ],
      refinement: "Two other buildings on the same BMS vendor show a smaller weekend drift. Run a portfolio-wide setpoint audit?",
      actions: ["Inspect now", "Monitor", "Override as expected"]
    },
    {
      id: "d4", module: "Vendors", icon: "ph-receipt", tone: "risk",
      title: "Invoice INV-2847 — 2 of 12 lines flagged (£1,450)",
      meta: "Apex Mechanical · £8,340 total · 10 lines auto-matched (£6,890)",
      money: "£230.25 over-charge identified",
      body: "WO-4421 labour charge 5.0 hrs vs attendance records 2.75 hrs (delta £146.25). WO-4438 non-approved part charged (delta £84.00).",
      fields: [
        { l: "Invoice", v: "INV-2847 · received 14 Sep 2026" },
        { l: "Vendor", v: "Apex Mechanical · score 78" },
        { l: "Matched", v: "10 lines · £6,890 · auto-approved" },
        { l: "Flagged", v: "2 lines · £1,450 · delta £230.25" },
        { l: "Contract rate", v: "£65/hr labour · parts mark-up cap 12%" }
      ],
      chain: [
        { a: "Orchestrator", t: "Invoice ingested via Orchestrator upload → intent: contract-performance-engine (B3)" },
        { a: "Planner", t: "Parse invoice → match line to WO ref → verify completion, hours vs attendance, parts vs approvals, rate vs contract" },
        { a: "Worker", t: "12 lines parsed. 10 matched inside 10% tolerance. 2 discrepancies with plain-language description." },
        { a: "Quality", t: "Flagged amount £1,450 exceeds £500 gate → mandatory validation. Re-checked attendance timestamps. Approved." }
      ],
      refinement: "Apex Mechanical labour-hour variance has appeared on 4 of the last 6 invoices. Raise as a contract performance item at the October review?",
      actions: ["Challenge flagged lines", "Approve as charged", "Open vendor record"]
    },
    {
      id: "d5", module: "Assets", icon: "ph-cube", tone: "warn",
      title: "AHU-3 supply fan — bearing degradation signature",
      meta: "Bishopsgate Tower · current draw 19% above 30-day baseline for 72 hrs",
      money: "£1,800 planned vs £38,000 emergency",
      body: "ASHRAE Guideline 36 Rule 12 signature. Estimated failure horizon 3–6 weeks. Tenant disruption risk: level 4–7 comfort cooling.",
      fields: [
        { l: "Asset", v: "AHU-3 · criticality L1" },
        { l: "Signature", v: "Motor current 19% above baseline, 72 hrs" },
        { l: "Failure horizon", v: "3–6 weeks" },
        { l: "Planned service", v: "£1,800 · Apex Mechanical" },
        { l: "Emergency replacement", v: "£38,000 + tenant disruption" },
        { l: "Proposed priority", v: "P2 · 8hr response / 48hr completion", editable: true }
      ],
      chain: [
        { a: "Orchestrator", t: "IoT threshold breach from monitoring layer → intent: work-order-engine (D3)" },
        { a: "Planner", t: "Classify WO type (predictive) → fetch asset → priority from criticality × severity → select contractor → estimate cost → draft WO" },
        { a: "Worker", t: "Vibration + current time series matched to bearing wear rule. Cost comparison built from Contract entity rates." },
        { a: "Quality", t: "Draft shown to PM — PM is the quality gate. No auto-dispatch." }
      ],
      refinement: "AHU-1 at Meridian Quay shows the same early signature at 8% above baseline. Add to the same contractor visit?",
      actions: ["Approve work order", "Change priority", "Monitor"]
    }
  ],

  certificates: [
    { id: "c1", building: "Bishopsgate Tower", type: "LOLER", asset: "Lift Asset-4471", expiry: "21 Sep 2026", days: 23, status: "Due for renewal", tone: "warn", vendor: "Apex Lifts" },
    { id: "c2", building: "Town Hall", type: "Gas Safety (CP12)", asset: "Boiler-22", expiry: "12 Aug 2026", days: -17, status: "Lapsed", tone: "risk", vendor: "Meridian Heating Ltd" },
    { id: "c3", building: "AN Other House", type: "EICR", asset: "Main distribution", expiry: "04 Sep 2026", days: 6, status: "Overdue", tone: "risk", vendor: "Northgate Electrical" },
    { id: "c4", building: "Meridian Quay", type: "Fire Risk Assessment", asset: "Whole building", expiry: "30 Nov 2026", days: 93, status: "Current", tone: "ok", vendor: "Sentinel Fire Systems" },
    { id: "c5", building: "Building 5", type: "Legionella (L8)", asset: "Domestic water", expiry: "18 Oct 2026", days: 50, status: "Expiring soon", tone: "warn", vendor: "Clearwater Compliance" },
    { id: "c6", building: "Kingsway House", type: "Asbestos re-inspection", asset: "Basement plant", expiry: "07 Jul 2026", days: -53, status: "Lapsed", tone: "risk", vendor: "Arden Surveying" },
    { id: "c7", building: "Bishopsgate Tower", type: "F-Gas", asset: "CHILLER-101", expiry: "02 Oct 2026", days: 34, status: "Expiring soon", tone: "warn", vendor: "Apex Mechanical" },
    { id: "c8", building: "Riverside Court", type: "EPC", asset: "Whole building", expiry: "14 Mar 2027", days: 197, status: "Current", tone: "ok", vendor: "Elmore Energy" }
  ],

  certCategories: [
    { name: "Fire", tracked: 48, current: 44, gap: 4 },
    { name: "Electrical", tracked: 36, current: 33, gap: 3 },
    { name: "Gas", tracked: 22, current: 19, gap: 3 },
    { name: "Legionella", tracked: 24, current: 23, gap: 1 },
    { name: "LOLER", tracked: 31, current: 29, gap: 2 },
    { name: "Asbestos", tracked: 18, current: 16, gap: 2 },
    { name: "Energy / EPC", tracked: 24, current: 22, gap: 2 },
    { name: "F-Gas", tracked: 14, current: 13, gap: 1 }
  ],

  vendors: [
    { id: "v1", name: "Apex Mechanical", score: 78, trend: "declining", spend: "£1.24m", sla_r: 94, sla_c: 88, firstfix: 81, recall: 6, accred: "Current", tone: "warn", note: "Labour-hour variance on 4 of last 6 invoices" },
    { id: "v2", name: "Meridian Heating Ltd", score: 58, trend: "declining", spend: "£186k", sla_r: 89, sla_c: 76, firstfix: 72, recall: 11, accred: "Lapsed", tone: "risk", note: "Score capped at 60 — Gas Safe lapsed" },
    { id: "v3", name: "Northgate Electrical", score: 91, trend: "improving", spend: "£640k", sla_r: 98, sla_c: 96, firstfix: 92, recall: 3, accred: "Current", tone: "ok", note: "Best P1 response in portfolio" },
    { id: "v4", name: "Apex Lifts", score: 87, trend: "stable", spend: "£410k", sla_r: 96, sla_c: 93, firstfix: 88, recall: 4, accred: "Current", tone: "ok", note: "LEIA current, all inspections satisfactory" },
    { id: "v5", name: "Sentinel Fire Systems", score: 84, trend: "stable", spend: "£520k", sla_r: 95, sla_c: 91, firstfix: 85, recall: 5, accred: "Current", tone: "ok", note: "" },
    { id: "v6", name: "Clearwater Compliance", score: 72, trend: "declining", spend: "£148k", sla_r: 88, sla_c: 81, firstfix: 79, recall: 9, accred: "Expiring", tone: "warn", note: "P2 completion 81% vs 95% target" }
  ],

  workorders: [
    { id: "WO-4512", asset: "Boiler-22", building: "Town Hall", type: "Compliance", priority: "P2", status: "Held", vendor: "Meridian Heating Ltd", tone: "risk", est: "£640", due: "Blocked — accreditation" },
    { id: "WO-4519", asset: "Boiler-14", building: "Meridian Quay", type: "PPM", priority: "P3", status: "Held", vendor: "Meridian Heating Ltd", tone: "risk", est: "£380", due: "Blocked — accreditation" },
    { id: "WO-4527", asset: "AHU-3", building: "Bishopsgate Tower", type: "Predictive", priority: "P2", status: "Draft", vendor: "Apex Mechanical", tone: "warn", est: "£1,800", due: "Awaiting approval" },
    { id: "WO-4421", asset: "CHILLER-101", building: "Bishopsgate Tower", type: "Reactive", priority: "P1", status: "Completed", vendor: "Apex Mechanical", tone: "ok", est: "£2,240", due: "Invoice flagged" },
    { id: "WO-4533", asset: "Lift Asset-4471", building: "Bishopsgate Tower", type: "Inspection", priority: "P3", status: "Draft", vendor: "Apex Lifts", tone: "warn", est: "£520", due: "Awaiting approval" },
    { id: "WO-4540", asset: "Main distribution", building: "AN Other House", type: "Compliance", priority: "P1", status: "In progress", vendor: "Northgate Electrical", tone: "ok", est: "£1,150", due: "Operative checked in 09:12" },
    { id: "WO-4544", asset: "Wet riser", building: "Riverside Court", type: "PPM", priority: "P3", status: "Assigned", vendor: "Sentinel Fire Systems", tone: "ok", est: "£290", due: "Scheduled 04 Sep" },
    { id: "WO-4551", asset: "FCU L4-12", building: "Bishopsgate Tower", type: "Reactive", priority: "P2", status: "In progress", vendor: "Apex Mechanical", tone: "ok", est: "£410", due: "SLA 31 hrs remaining" }
  ],

  anomalies: [
    { id: "a1", asset: "AHU-3", building: "Bishopsgate Tower", type: "Non-occupancy spike", impact: "£38,400", days: 21, status: "New", tone: "risk" },
    { id: "a2", asset: "CHILLER-101", building: "Bishopsgate Tower", type: "Single-asset spike", impact: "£28,000", days: 9, status: "Acknowledged", tone: "risk" },
    { id: "a3", asset: "Whole building", building: "Kingsway House", type: "Baseline drift", impact: "£19,600", days: 34, status: "Inspected", tone: "warn" },
    { id: "a4", asset: "Server room", building: "Meridian Quay", type: "Single-asset spike", impact: "£14,200", days: 5, status: "New", tone: "warn" },
    { id: "a5", asset: "Domestic hot water", building: "Town Hall", type: "Baseline drift", impact: "£8,900", days: 12, status: "Acknowledged", tone: "warn" },
    { id: "a6", asset: "Car park lighting", building: "Building 5", type: "Non-occupancy spike", impact: "£4,100", days: 60, status: "Resolved", tone: "ok" },
    { id: "a7", asset: "Chiller plant CH-2", building: "Marina Heights", type: "Cooling load drift", impact: "£22,700", days: 27, status: "New", tone: "risk" },
    { id: "a8", asset: "Common area lighting", building: "Northgate Mall", type: "Schedule overrun", impact: "£11,300", days: 16, status: "Acknowledged", tone: "warn" },
    { id: "a9", asset: "AHU-2 · level 12", building: "Raffles Link", type: "Baseline drift", impact: "£6,400", days: 8, status: "New", tone: "warn" }
  ],

  buildings: [
    { name: "Bishopsgate Tower", eui: 214, bench: 180, cov: 86, wo: 32, score: 72, tone: "risk" },
    { name: "Meridian Quay", eui: 168, bench: 180, cov: 96, wo: 18, score: 91, tone: "ok" },
    { name: "Kingsway House", eui: 198, bench: 180, cov: 78, wo: 24, score: 68, tone: "risk" },
    { name: "Town Hall", eui: 186, bench: 180, cov: 88, wo: 14, score: 79, tone: "warn" },
    { name: "AN Other House", eui: 172, bench: 180, cov: 92, wo: 11, score: 85, tone: "ok" },
    { name: "Riverside Court", eui: 159, bench: 180, cov: 100, wo: 9, score: 94, tone: "ok" },
    { name: "Building 5", eui: 191, bench: 180, cov: 84, wo: 16, score: 74, tone: "warn" },
    { name: "Marina Heights", eui: 246, bench: 228, cov: 74, wo: 21, score: 58, tone: "risk" },
    { name: "Northgate Mall", eui: 188, bench: 205, cov: 81, wo: 13, score: 74, tone: "ok" },
    { name: "Raffles Link", eui: 176, bench: 192, cov: 95, wo: 7, score: 92, tone: "ok" }
  ],

  activity: [
    { t: "09:15", agent: "compliance-engine", text: "Gas Safe registration for Meridian Heating Ltd expired 3 days ago → allocation blocked for all gas work orders → 2 pending WOs held → PM action required", tone: "risk" },
    { t: "09:14", agent: "compliance-engine", text: "Nightly compliance scan → LOLER certificate for Lift Asset-4471 expires in 23 days → contractor recommendation: Apex Lifts → booking request drafted → awaiting PM approval", tone: "warn" },
    { t: "08:00", agent: "work-order-engine", text: "Monthly PPM run: 14 assets due this month → 14 WO drafts created → 12 auto-approved within authority → 2 require PM approval (above cost threshold)", tone: "ok" },
    { t: "03:22", agent: "energy-intelligence-engine", text: "Weekend non-occupancy spike: Bishopsgate Tower Saturday 02:00–06:00 = 847 kWh vs weekend baseline 210 kWh → annualised impact £38,400 → create inspection WO?", tone: "warn" },
    { t: "11:05", agent: "work-order-engine", text: "AHU-3 supply fan current draw 19% above 30-day baseline for 72 hrs → ASHRAE Rule 12 bearing degradation → planned £1,800 vs emergency £38,000 → WO draft created", tone: "warn" },
    { t: "16:42", agent: "contract-performance-engine", text: "Invoice INV-2847 (£8,340) matched against 12 WOs → 10 lines matched (£6,890) → 2 lines flagged (£1,450) → PM decision required", tone: "risk" }
  ],

  answers: {
    compliance: {
      match: ["compliance", "certificate", "cert", "lapsed", "loler", "eicr", "gas safe", "risk this month", "scan"],
      scope: "COMPLIANCE · 24 BUILDINGS · 4 REGULATION PACKS · AS AT 03 SEP 2026",
      title: "Three lapses carry criminal liability today, and all three sit in two buildings",
      takeaway: "Portfolio compliance coverage is 91%, but the gap is concentrated: Town Hall (Gas Safety CP12, lapsed 17 days) and Kingsway House (Asbestos re-inspection, lapsed 53 days) account for two of the three lapses, and AN Other House EICR goes overdue in 6 days. The lapses are not a tracking failure — all three had a contractor booked and the booking was never confirmed. Two vendors are currently blocked from regulated work, which is why 2 gas work orders are held.",
      caveat: "Coverage is measured against the regulation pack for each building (118 tracked obligations in the UK pack). Buildings added in the last 30 days show 0% until first ingestion completes — 1 building affected.",
      metrics: [
        { l: "Coverage vs regulation pack", v: "91%", s: "118 obligations tracked", tone: "warn" },
        { l: "Lapsed", v: "3", s: "Insurance void risk on 2 buildings", tone: "risk" },
        { l: "Inside 30 days", v: "11", s: "8 have a booking drafted", tone: "warn" },
        { l: "Vendors blocked", v: "2", s: "Meridian Heating, Clearwater", tone: "risk" }
      ],
      rows: [
        ["Town Hall", "Gas Safety (CP12)", "Lapsed 17d", "Vendor blocked — reassign"],
        ["Kingsway House", "Asbestos re-inspection", "Lapsed 53d", "No contractor on record"],
        ["AN Other House", "EICR", "Overdue in 6d", "Northgate booked 09 Sep"],
        ["Bishopsgate Tower", "LOLER", "23d", "Booking drafted — approve"],
        ["Bishopsgate Tower", "F-Gas", "34d", "Consolidate with LOLER visit"]
      ],
      rowHead: ["Building", "Obligation", "Status", "Next action"],
      chain: [
        { a: "Orchestrator", t: "Intent classified: compliance-engine. Scope resolved: all buildings, all categories." },
        { a: "Planner", t: "4 sub-tasks: fetch ComplianceCertificate where expiry < 90d → compute alert ladder → join blocked vendors → rank by liability" },
        { a: "Worker", t: "118 obligations read from UDR. 3 Lapsed, 1 Overdue, 11 inside 30d. Joined 2 vendor accreditation lapses." },
        { a: "Quality", t: "Lapsed flags validated against issuing registers before insurance risk flag raised. Confidence 98%." }
      ],
      actions: ["Approve all 8 drafted bookings", "Open Compliance module", "Export owner report"]
    },
    energy: {
      match: ["energy", "kwh", "consumption", "spike", "eui", "meter", "carbon", "bishopsgate", "why did energy"],
      scope: "ENERGY · 24 BUILDINGS · HALF-HOURLY MPAN · AUG 2026",
      title: "Energy is £312k a year above benchmark, and 62% of it is one building",
      takeaway: "Portfolio EUI is 194 kWh/m²/yr against a CIBSE TM46 benchmark of 180 — 18% above, worth £312k a year at the current 28.4p tariff. Bishopsgate Tower carries £194k of that gap on its own. The driver is not occupancy: weekend consumption there has run at 847 kWh in the 02:00–06:00 window for three consecutive weekends against a 210 kWh baseline, which is a controls signature, not a demand signature.",
      caveat: "Six buildings are on building-level metering only, so fault attribution there is inferred rather than sub-metered. Tariff is the current contracted rate, not a forward curve. MEES is read on two clocks: the enforceable EPC E floor, which all 24 buildings currently clear, and the proposed EPC B standard for privately-let buildings over 1,000 m², which the June 2026 interim response moved from 2030 to 2031 and which two buildings would miss on their current certificates. The EPC C milestone once proposed for 2027 was dropped and is not scored.",
      metrics: [
        { l: "Portfolio EUI", v: "194", s: "kWh/m²/yr · benchmark 180", tone: "warn" },
        { l: "Excess cost", v: "£312k", s: "per year at 28.4p/kWh", tone: "risk" },
        { l: "Anomalies active", v: "6", s: "£113k annualised impact", tone: "warn" },
        { l: "MEES — enforceable now", v: "0", s: "below EPC E · all 24 lettable", tone: "ok" },
        { l: "MEES — proposed 2031", v: "2", s: "below EPC B · both over 1,000 m²", tone: "risk" }
      ],
      rows: [
        ["Bishopsgate Tower", "Non-occupancy spike", "£38,400", "AHU-3 controls — inspect"],
        ["Bishopsgate Tower", "Single-asset spike", "£28,000", "CHILLER-101 condenser fouling"],
        ["Kingsway House", "Baseline drift", "£19,600", "Inspected — awaiting parts"],
        ["Meridian Quay", "Single-asset spike", "£14,200", "Server room — expected, override"],
        ["Town Hall", "Baseline drift", "£8,900", "Acknowledged — monitoring"]
      ],
      rowHead: ["Building", "Anomaly", "Annualised £", "Next action"],
      chain: [
        { a: "Orchestrator", t: "Intent: energy-intelligence-engine. Daily scan 03:00 results reused, no re-fetch needed." },
        { a: "Planner", t: "6 sub-tasks: fetch MeterReading → compute EUI per building → benchmark vs TM46 → run 13 anomaly rules → translate to £ → rank" },
        { a: "Worker", t: "1.2m half-hourly readings aggregated. 6 anomalies scored. Sub-meter attribution available on 18 of 24 buildings." },
        { a: "Quality", t: "Not fired — display-only output. Gate would apply only on automatic WO dispatch." }
      ],
      actions: ["Create inspection WO — AHU-3", "Open Energy module", "Export ESOS data pack"]
    },
    vendors: {
      match: ["vendor", "contractor", "invoice", "sla", "scorecard", "costing me money", "performance"],
      scope: "VENDOR PERFORMANCE · 6 CONTRACTED VENDORS · AUG 2026",
      title: "Apex Mechanical is your largest spend and your fastest declining score",
      takeaway: "Apex Mechanical holds £1.24m of annual spend at a score of 78 and declining, driven by P2 completion at 88% against a 95% contracted target and a labour-hour variance that has now appeared on 4 of the last 6 invoices. Meridian Heating scores 58 and is capped at 60 while its Gas Safe registration is lapsed. Northgate Electrical at 91 is the only vendor improving. Service credits recoverable under clause 8.3 this month: £2,100.",
      caveat: "Scores weight SLA response 25%, completion 25%, first fix 20%, recall 15%, accreditation 15%. SLA failures on L1 assets carry 3× weight, so one chiller miss moves the score more than three lighting jobs.",
      metrics: [
        { l: "Service credits due", v: "£2,100", s: "clause 8.3 · 6 P2 breaches", tone: "warn" },
        { l: "Invoice leakage caught", v: "£230", s: "on £8,340 invoice · 2 lines", tone: "warn" },
        { l: "Vendors below 80", v: "3", s: "of 6 contracted", tone: "risk" },
        { l: "PPM compliance", v: "92%", s: "portfolio · ±7 day window", tone: "ok" }
      ],
      rows: [
        ["Apex Mechanical", "78 ↓", "£1.24m", "Raise clause 8.3 credit note"],
        ["Meridian Heating Ltd", "58 ↓", "£186k", "Accreditation blocked — reassign"],
        ["Clearwater Compliance", "72 ↓", "£148k", "P2 completion 81% — review"],
        ["Sentinel Fire Systems", "84 =", "£520k", "No action"],
        ["Northgate Electrical", "91 ↑", "£640k", "Candidate for gas reassignment"]
      ],
      rowHead: ["Vendor", "Score", "Annual spend", "Next action"],
      chain: [
        { a: "Orchestrator", t: "Intent: contract-performance-engine. Monthly scorecard period resolved to Aug 2026." },
        { a: "Planner", t: "4 sub-tasks: fetch completed WOs this period per vendor → score against extracted SLA → aggregate → flag variances" },
        { a: "Worker", t: "312 completed WOs scored against contract baseline. PPM compliance computed separately. Cost variance run on 6 vendors." },
        { a: "Quality", t: "Fired on Meridian Heating: 20-point month-on-month drop checked for data anomaly before presenting. Confirmed genuine." }
      ],
      actions: ["Raise £2,100 credit note", "Open Vendor Performance", "Reassign gas work orders"]
    },
    queue: {
      match: ["approval", "approve", "today", "queue", "what needs", "decisions"],
      scope: "DECISION QUEUE · 5 ITEMS · AS AT 09:20 TODAY",
      title: "Five decisions today; two are irreversible if you leave them",
      takeaway: "The queue holds £39,850 of avoidable annualised cost and two statutory exposures. The Gas Safe lapse and the Town Hall CP12 lapse are the irreversible pair — the vendor block is already active and 2 work orders are held, so every day adds tenant risk without adding cost. The AHU-3 approval is the highest-return item in the queue: £1,800 now against £38,000 and tenant disruption later.",
      caveat: "Items are ranked by consequence, not by age. Three further items are inside the agents' delegated authority and were auto-approved overnight — visible in the Activity Log, no action needed.",
      metrics: [
        { l: "Awaiting you", v: "5", s: "2 statutory · 3 financial", tone: "warn" },
        { l: "Avoidable cost", v: "£39.9k", s: "annualised, if actioned now", tone: "ok" },
        { l: "Auto-approved", v: "12", s: "within delegated authority", tone: "ok" },
        { l: "Oldest item", v: "3 days", s: "Gas Safe lapse", tone: "risk" }
      ],
      rows: [
        ["Reassign 2 held gas WOs", "Compliance", "Statutory", "Northgate Electrical ready"],
        ["Approve AHU-3 work order", "Assets", "£36,200 avoided", "£1,800 planned service"],
        ["Approve LOLER booking", "Compliance", "Statutory", "Apex Lifts, 14–18 Sep"],
        ["Challenge INV-2847 lines", "Vendors", "£230 recovered", "Evidence attached"],
        ["Bishopsgate weekend spike", "Energy", "£38,400/yr", "Inspect or monitor"]
      ],
      rowHead: ["Decision", "Module", "Consequence", "Recommendation"],
      chain: [
        { a: "Orchestrator", t: "Intent: cross-module human queue. Fan-out to all four Phase 2 tool agents." },
        { a: "Planner", t: "Collect open human-queue items → rank by consequence class (statutory > irreversible financial > recoverable) → attach recommendation" },
        { a: "Worker", t: "5 open items, 12 auto-approved inside authority. Consequence values pulled from Contract and Meter entities." },
        { a: "Quality", t: "Each actionable item already carried its own gate at creation. No second gate applied here." }
      ],
      actions: ["Work the queue", "Approve all recommended", "Export decision log"]
    }
  }
};
