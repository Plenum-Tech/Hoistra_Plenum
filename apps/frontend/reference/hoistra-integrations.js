/* Hoistra — integrations layer (admin only).
   A source is only worth connecting if it lands somewhere in the Hoist Graph.
   Every entry therefore declares the tables it ENRICHES (already in the graph,
   gains columns and rows) and the tables it CREATES (new tables the graph does
   not have until this source is connected), plus what those tables unlock.
   Nothing here asserts a capability the tables cannot support. */
window.HOISTRA_INT = (function () {

  var ST = {
    ok: { label: "Healthy", fg: "var(--st-ok)", bg: "var(--st-ok-bg)" },
    warn: { label: "Degraded", fg: "var(--st-warn)", bg: "var(--st-warn-bg)" },
    risk: { label: "Auth expired", fg: "var(--st-risk)", bg: "var(--st-risk-bg)" },
    limit: { label: "Rate limited", fg: "var(--st-warn)", bg: "var(--st-warn-bg)" },
    review: { label: "In review", fg: "var(--st-dormant)", bg: "var(--st-dormant-bg)" }
  };

  /* Connected sources. `rows` is the 30-day landed row count per table, which is
     what the reconciliation on the Buildings page counts against. */
  var CONNECTED = [
    {
      id: "yardi", name: "Yardi Voyager", cat: "Finance and property accounting", kind: "Property accounting",
      st: "ok", last: "14 min ago", vol: "8,420", mode: "Two-way · 15-minute poll",
      by: "R. Achebe", since: "12 Mar 2026", auth: "OAuth 2.0 · tenant hoistra-uk",
      enrich: [["invoice", "1,880"], ["building", "24"], ["vendor", "31"]],
      create: [["service_charge_line", "4,210"], ["lease", "624"]],
      unlocks: ["Service charge recovery per building", "Invoice lines checked against the contracted rate schedule", "Recoverable versus non-recoverable split on every work order"],
      note: "Cost codes map to the graph's asset hierarchy through the GL segment, so a spend line resolves to a specific plant item rather than a building total."
    },
    {
      id: "s4", name: "SAP S/4HANA", cat: "ERP — SAP", kind: "ERP",
      st: "ok", last: "1 h ago", vol: "3,150", mode: "Read-only · hourly CDS extract",
      by: "R. Achebe", since: "28 Mar 2026", auth: "Service user · OData v4",
      enrich: [["asset", "1,204"], ["work_order", "612"]],
      create: [["purchase_order", "890"], ["cost_centre", "64"], ["gl_posting", "1,480"]],
      unlocks: ["Capex against opex per asset", "Purchase order reconciled to the work order it was raised for", "Budget variance by cost centre"],
      note: "Plant Maintenance orders arrive with their functional location, which is what binds an ERP asset to a Hoistra asset without a manual mapping table."
    },
    {
      id: "maximo", name: "IBM Maximo", cat: "CMMS and CAFM", kind: "CMMS",
      st: "risk", last: "3 d ago", vol: "0", mode: "Two-way · webhook",
      by: "D. Ferreira", since: "04 Feb 2026", auth: "API key — expired 3 days ago",
      enrich: [["work_order", "0"], ["asset", "0"], ["equipment", "0"]],
      create: [["ppm_schedule", "0"]],
      unlocks: ["PPM calendar against statutory frequency", "First-time fix measured on the CMMS record rather than the invoice"],
      note: "Nothing has landed since the key expired. Vendor scorecards are still being computed, but on work orders up to 3 days old — the Vendors page marks that staleness."
    },
    {
      id: "planon", name: "Planon Universe", cat: "IWMS", kind: "IWMS",
      st: "ok", last: "6 h ago", vol: "1,940", mode: "Read-only · nightly",
      by: "D. Ferreira", since: "19 Apr 2026", auth: "OAuth 2.0",
      enrich: [["space", "2,110"], ["floor", "186"], ["building", "24"]],
      create: [["occupancy", "1,940"]],
      unlocks: ["Energy intensity per occupied m² instead of per gross m²", "Space-level attribution of a landlord supply draw"],
      note: "Occupancy is the denominator the energy engine was missing. EUI figures before 19 April are gross-area only and labelled as such."
    },
    {
      id: "reuters", name: "Reuters Connect", cat: "News and market intelligence", kind: "News",
      st: "ok", last: "22 min ago", vol: "612", mode: "Read-only · streaming",
      by: "R. Achebe", since: "02 May 2026", auth: "API key",
      enrich: [["vendor", "31"]],
      create: [["news_item", "590"], ["regulation_signal", "22"]],
      unlocks: ["Counterparty watch on every vendor in the directory", "Regulatory change picked up before the pack update lands", "Market rate context on contract renewals"],
      note: "Filtered to the vendor names and regulation packs already in the graph. An unmatched story is discarded rather than stored."
    },
    {
      id: "lake", name: "Custom API — portfolio data lake", cat: "Custom and direct", kind: "Custom",
      st: "limit", last: "2 min ago", vol: "41,300", mode: "Push · REST",
      by: "Platform team", since: "08 Jan 2026", auth: "Bearer token · rotated 60d",
      enrich: [["meter_reading", "38,900"], ["invoice", "1,120"]],
      create: [["tenant_billing", "1,280"]],
      unlocks: ["Half-hourly MPAN and MPRN feeds", "Tenant recharge reconciled against landlord supply"],
      note: "Throttled at 10k rows a minute. The backlog clears inside the hour; readings arrive in order, so no gap is created."
    }
  ];

  /* Catalogue. `tables` is what the source would write to once connected —
     the admin sees the graph consequence before authorising anything. */
  var CATALOGUE = [
    { id: "fin", label: "Finance and property accounting", icon: "ph-bank",
      blurb: "Rent, service charge, budgets and payables. These sources give every number on the platform a ledger it can be traced back to.",
      items: [
        { name: "Yardi Voyager", fam: "Yardi", st: "connected", tables: ["service_charge_line", "invoice", "lease"], gives: "Full property accounting ledger" },
        { name: "Yardi Breeze Premier", fam: "Yardi", tables: ["invoice", "lease"], gives: "Lighter Yardi stack for smaller portfolios" },
        { name: "MRI Property Management X", fam: "MRI Software", tables: ["service_charge_line", "lease", "budget_line"], gives: "Lease and service charge ledger" },
        { name: "MRI Horizon", fam: "MRI Software", tables: ["gl_posting", "budget_line"], gives: "Commercial property GL" },
        { name: "RealPage IMS", fam: "RealPage", tables: ["lease", "tenant_billing"], gives: "Investor and tenant billing" },
        { name: "AppFolio Property Manager", fam: "AppFolio", tables: ["lease", "invoice"], gives: "Residential ledger and payables" },
        { name: "Entrata", fam: "Entrata", tables: ["lease", "tenant_billing"], gives: "Resident billing and renewals" },
        { name: "Sage Intacct", fam: "Sage", tables: ["gl_posting", "invoice"], gives: "GL and AP for the managing agent" },
        { name: "Coupa", fam: "Coupa", tables: ["purchase_order", "invoice"], gives: "Procure-to-pay and PO matching" },
        { name: "AvidXchange", fam: "AvidXchange", tables: ["invoice"], gives: "Invoice capture and approval trail" }
      ] },
    { id: "orc", label: "ERP — Oracle", icon: "ph-database",
      blurb: "Where the corporate ledger and the asset register already live. Connected read-only unless a write-back is explicitly authorised.",
      items: [
        { name: "Oracle Fusion Cloud ERP", fam: "Oracle", tables: ["gl_posting", "cost_centre", "asset"], gives: "GL, projects and fixed assets" },
        { name: "Oracle NetSuite", fam: "Oracle", tables: ["gl_posting", "purchase_order"], gives: "Mid-market GL and procurement" },
        { name: "Oracle JD Edwards EnterpriseOne", fam: "Oracle", tables: ["asset", "work_order", "purchase_order"], gives: "Capital asset and maintenance module" },
        { name: "Oracle E-Business Suite", fam: "Oracle", tables: ["gl_posting", "asset"], gives: "Legacy GL and fixed asset register" },
        { name: "Oracle PeopleSoft Financials", fam: "Oracle", tables: ["gl_posting", "cost_centre"], gives: "Cost centre and budget structure" },
        { name: "Oracle Primavera", fam: "Oracle", tables: ["project_task", "asset"], gives: "Capital project programme" }
      ] },
    { id: "ms", label: "ERP — Microsoft", icon: "ph-database",
      blurb: "Dynamics stacks bring the finance ledger and, with Field Service, the engineer's own record of what happened on site.",
      items: [
        { name: "Dynamics 365 Finance", fam: "Microsoft", tables: ["gl_posting", "cost_centre", "invoice"], gives: "Enterprise GL and AP" },
        { name: "Dynamics 365 Business Central", fam: "Microsoft", tables: ["gl_posting", "purchase_order"], gives: "Mid-market GL and purchasing" },
        { name: "Dynamics 365 Field Service", fam: "Microsoft", tables: ["work_order", "asset", "ppm_schedule"], gives: "Engineer dispatch and job history" },
        { name: "Dynamics 365 Supply Chain", fam: "Microsoft", tables: ["asset", "purchase_order"], gives: "Asset lifecycle and stock" },
        { name: "Dynamics GP", fam: "Microsoft", tables: ["gl_posting"], gives: "Legacy GL extract" },
        { name: "Microsoft Fabric / Azure SQL", fam: "Microsoft", tables: ["meter_reading", "gl_posting"], gives: "Direct warehouse replica" }
      ] },
    { id: "sap", label: "ERP — SAP", icon: "ph-database",
      blurb: "SAP holds the functional location hierarchy, which is the cleanest join into the graph's asset tree that exists in most portfolios.",
      items: [
        { name: "SAP S/4HANA", fam: "SAP", st: "connected", tables: ["purchase_order", "cost_centre", "gl_posting"], gives: "Core ledger and asset accounting" },
        { name: "SAP S/4HANA Cloud", fam: "SAP", tables: ["gl_posting", "purchase_order"], gives: "Public cloud ledger" },
        { name: "SAP ECC 6.0", fam: "SAP", tables: ["gl_posting", "asset"], gives: "Legacy ledger via IDoc or RFC" },
        { name: "SAP Plant Maintenance (PM)", fam: "SAP", tables: ["work_order", "asset", "ppm_schedule"], gives: "Maintenance orders and notifications" },
        { name: "SAP Enterprise Asset Management", fam: "SAP", tables: ["asset", "equipment"], gives: "Equipment master and criticality" },
        { name: "SAP Business One", fam: "SAP", tables: ["gl_posting", "invoice"], gives: "Small-portfolio ledger" },
        { name: "SAP Ariba", fam: "SAP", tables: ["purchase_order", "vendor"], gives: "Supplier master and sourcing" }
      ] },
    { id: "cmms", label: "CMMS and CAFM", icon: "ph-wrench",
      blurb: "The work order record. Without one of these, vendor performance is measured on invoices, which is the weaker evidence.",
      items: [
        { name: "IBM Maximo", fam: "IBM", st: "connected", tables: ["work_order", "asset", "ppm_schedule"], gives: "Enterprise maintenance management" },
        { name: "Archibus", fam: "Eptura", tables: ["work_order", "space", "asset"], gives: "CAFM with space and moves" },
        { name: "FSI Concept Evolution", fam: "FSI", tables: ["work_order", "ppm_schedule", "asset"], gives: "UK FM contractor standard" },
        { name: "MRI Evolution", fam: "MRI Software", tables: ["work_order", "asset"], gives: "Contractor job and SLA record" },
        { name: "Elogbooks", fam: "Elogbooks", tables: ["work_order", "certificate"], gives: "Helpdesk with compliance logbook" },
        { name: "Fiix", fam: "Rockwell", tables: ["work_order", "asset"], gives: "Cloud CMMS" },
        { name: "Limble CMMS", fam: "Limble", tables: ["work_order", "ppm_schedule"], gives: "Mobile-first maintenance" },
        { name: "Corrigo", fam: "JLL", tables: ["work_order", "vendor", "invoice"], gives: "Vendor dispatch and invoicing" }
      ] },
    { id: "iwms", label: "IWMS", icon: "ph-squares-four",
      blurb: "Space, occupancy and lease in one model. This is what turns per-m² figures into per-occupied-m² figures.",
      items: [
        { name: "Planon Universe", fam: "Planon", st: "connected", tables: ["space", "occupancy", "floor"], gives: "Space, occupancy and PPM" },
        { name: "IBM Tririga", fam: "IBM", tables: ["space", "lease", "asset"], gives: "Lease accounting and portfolio" },
        { name: "Nuvolo Connected Workplace", fam: "Nuvolo", tables: ["asset", "work_order", "space"], gives: "ServiceNow-native IWMS" },
        { name: "Spacewell Workplace", fam: "Spacewell", tables: ["space", "occupancy"], gives: "Sensor-led occupancy" },
        { name: "FM:Systems", fam: "Johnson Controls", tables: ["space", "floor"], gives: "Space planning and CAD" },
        { name: "Eptura Workplace", fam: "Eptura", tables: ["space", "occupancy"], gives: "Desk and room utilisation" }
      ] },
    { id: "asset", label: "Asset, plant and BMS", icon: "ph-cube",
      blurb: "Condition and telemetry at the plant item itself, which is where an energy anomaly stops being a building-level guess.",
      items: [
        { name: "Brightly Assetic", fam: "Siemens", tables: ["asset", "condition_survey"], gives: "Lifecycle and condition modelling" },
        { name: "Schneider EcoStruxure Building", fam: "Schneider Electric", tables: ["meter_reading", "equipment"], gives: "BMS points and sub-metering" },
        { name: "Honeywell Forge", fam: "Honeywell", tables: ["meter_reading", "equipment", "asset"], gives: "Plant telemetry and faults" },
        { name: "Siemens Desigo CC", fam: "Siemens", tables: ["meter_reading", "equipment"], gives: "Building automation records" },
        { name: "Johnson Controls OpenBlue", fam: "Johnson Controls", tables: ["equipment", "meter_reading"], gives: "Equipment performance" },
        { name: "Trane Connect", fam: "Trane", tables: ["equipment", "meter_reading"], gives: "Chiller and HVAC telemetry" }
      ] },
    { id: "news", label: "News and market intelligence", icon: "ph-newspaper",
      blurb: "Outside signal. Used to watch counterparties in the vendor directory and regulation changes ahead of a pack update.",
      items: [
        { name: "Reuters Connect", fam: "Reuters", st: "connected", tables: ["news_item", "regulation_signal"], gives: "Wire feed filtered to graph entities" },
        { name: "Bloomberg B-PIPE", fam: "Bloomberg", tables: ["news_item", "market_rate"], gives: "Market data and counterparty news" },
        { name: "LexisNexis Nexis", fam: "LexisNexis", tables: ["news_item", "regulation_signal"], gives: "Legal and regulatory archive" },
        { name: "gov.uk and HSE bulletins", fam: "Public", st: "review", tables: ["regulation_signal"], gives: "Statutory guidance changes" },
        { name: "CoStar", fam: "CoStar", tables: ["market_rate", "building"], gives: "Comparable rents and valuations" },
        { name: "Companies House", fam: "Public", tables: ["vendor"], gives: "Vendor solvency and filings" }
      ] },
    { id: "custom", label: "Custom and direct", icon: "ph-plugs",
      blurb: "For anything without a listed connector. The graph does not care how a row arrives, only that it can be keyed and dated.",
      items: [
        { name: "REST API", fam: "Hoistra", st: "connected", tables: ["any table"], gives: "Push rows directly from your service" },
        { name: "Webhook receiver", fam: "Hoistra", tables: ["any table"], gives: "Event-driven single-record push" },
        { name: "SFTP drop", fam: "Hoistra", tables: ["any table"], gives: "Scheduled flat-file collection" },
        { name: "Read replica (Postgres / SQL Server)", fam: "Hoistra", tables: ["any table"], gives: "Direct query against your warehouse" },
        { name: "CSV upload", fam: "Hoistra", tables: ["any table"], gives: "One-off load with column mapping" },
        { name: "Email ingestion", fam: "Hoistra", tables: ["document", "certificate"], gives: "Forwarded certificates and invoices" }
      ] }
  ];

  var ENDPOINTS = [
    { m: "POST", p: "/v1/ingest/rows", d: "Batch upsert into any graph table. Keyed on the natural key you declare in the mapping." },
    { m: "POST", p: "/v1/ingest/documents", d: "Push a file. Structured sources become rows; scans are vectorised and bound to a column." },
    { m: "GET", p: "/v1/graph/tables", d: "The current table and column list, with row counts, so a mapping can be generated." },
    { m: "GET", p: "/v1/graph/query", d: "Read the graph in the same shape the query interface uses. Returns the evidence chain with each row." },
    { m: "POST", p: "/v1/actions", d: "Raise a work order, book a contractor, or claim a service credit from your own system." }
  ];

  var MAPPING = [
    { src: "SUPPLIER_ID", type: "string", tbl: "vendor", col: "external_ref", note: "natural key" },
    { src: "WORK_ORDER_NO", type: "string", tbl: "work_order", col: "external_ref", note: "natural key" },
    { src: "FUNCTIONAL_LOC", type: "string", tbl: "asset", col: "asset_path", note: "resolves the asset tree" },
    { src: "COMPLETED_AT", type: "timestamp", tbl: "work_order", col: "closed_at", note: "SLA measurement point" },
    { src: "NET_AMOUNT", type: "numeric", tbl: "invoice_line", col: "net", note: "checked against rate schedule" },
    { src: "COST_CODE", type: "string", tbl: "service_charge_line", col: "code", note: "recoverability" }
  ];

  return { ST: ST, CONNECTED: CONNECTED, CATALOGUE: CATALOGUE, ENDPOINTS: ENDPOINTS, MAPPING: MAPPING };
})();
