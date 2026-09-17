// hoistra-access — demo data for Users & access, Audit trail, the ingestion
// validation agent and the Super Admin console. UI-only: no backend behind
// any of this yet, so these seed the same shapes the real endpoints will
// eventually return.

export const AX_BUILDINGS = ["Bishopsgate Tower", "Riverside Court", "Meridian Quay", "Kingsway House", "Town Hall", "Building 5", "Marina Heights", "Northgate Mall"];

export const AX_USERS = [
  { id: "u1", name: "Amara Osei", email: "a.osei@plenum-tech.com", title: "Facilities lead", buildings: ["Bishopsgate Tower", "Kingsway House"], ingest: true, status: "Active", queries: 214, ingests: 38, last: "12 min ago" },
  { id: "u2", name: "Daniel Reyes", email: "d.reyes@plenum-tech.com", title: "Building manager", buildings: ["Riverside Court"], ingest: true, status: "Active", queries: 96, ingests: 41, last: "1 hour ago" },
  { id: "u3", name: "Priya Nair", email: "p.nair@plenum-tech.com", title: "Compliance analyst", buildings: ["Bishopsgate Tower", "Riverside Court", "Meridian Quay", "Town Hall"], ingest: false, status: "Active", queries: 187, ingests: 0, last: "Yesterday" },
  { id: "u4", name: "Tom Whitfield", email: "t.whitfield@plenum-tech.com", title: "FM operative", buildings: ["Building 5", "Northgate Mall"], ingest: false, status: "Active", queries: 22, ingests: 0, last: "Tue" },
  { id: "u5", name: "Sofia Lindqvist", email: "s.lindqvist@plenum-tech.com", title: "Energy analyst", buildings: ["Meridian Quay"], ingest: false, status: "Invited", queries: 0, ingests: 0, last: "—" }
];

export const SA_COMPANIES = [
  { id: "c1", name: "Plenum Technologies", cc: "UK", status: "Active", buildings: 24, graphs: 24, last: "12 min ago", udr: "4.9 GB", certs: 118, certCc: "UK", api: "1.21M", credits: 8420, users: 6, invited: true },
  { id: "c2", name: "Meridian REIT", cc: "US", status: "Active", buildings: 11, graphs: 11, last: "2 hours ago", udr: "1.8 GB", certs: 64, certCc: "US", api: "460k", credits: 3180, users: 4, invited: true },
  { id: "c3", name: "Gulf Estates FZ", cc: "UAE", status: "Onboarding", buildings: 7, graphs: 5, last: "Yesterday", udr: "760 MB", certs: 41, certCc: "UAE", api: "112k", credits: 2040, users: 1, invited: true },
  { id: "c4", name: "Lion City Properties", cc: "Singapore", status: "Created", buildings: 4, graphs: 1, last: "Mon", udr: "90 MB", certs: 18, certCc: "Singapore", api: "9k", credits: 960, users: 0, invited: false }
];

export const AX_CHECKS = ["Building name and identity", "Country and location", "Approved vendors", "Assets, equipment and floors", "Existing certificates", "UDR and graph relationships"];

export const ING_DOCS = [
  { file: "Fire alarm service certificate — Sentinel Fire Systems.pdf", kind: "Compliance certificate", home: "Bishopsgate Tower", detail: "Sentinel Fire Systems is not an approved vendor for {b}, and the certificate references asset FA-2201 on the Level 12 plant deck — both are on record at {h}" },
  { file: "Legionella risk assessment — Clearwater Compliance.pdf", kind: "Compliance certificate", home: "Riverside Court", detail: "Clearwater Compliance holds the legionella contract at {h}, and the sampled outlets match its riser schedule — not anything on record at {b}" },
  { file: "Vendor invoice — Apex Lifts · INV-30412.pdf", kind: "Vendor invoice", home: null, detail: "" }
];

export const AU_SEED = [
  { id: "a1", when: "Today 09:41", who: "Daniel Reyes", role: "User", building: "Riverside Court", finalB: "Riverside Court", doc: "Legionella risk assessment — Clearwater Compliance.pdf", outcome: "Accepted", tone: "ok", checks: "6 of 6 passed", issue: "None — vendor, country and certificate type all matched the building ontology", suggested: "—", explanation: "—", assessment: "Validated against the Riverside Court graph", approval: "Not required — clean match" },
  { id: "a2", when: "Today 08:12", who: "Amara Osei", role: "User", building: "Riverside Court", finalB: "Bishopsgate Tower", doc: "Fire alarm service certificate — Sentinel Fire Systems.pdf", outcome: "Reassigned", tone: "accent", checks: "6 run · 2 mismatched", issue: "Vendor and referenced asset FA-2201 not associated with Riverside Court", suggested: "Bishopsgate Tower", explanation: "Agent suggestion accepted — wrong building selected", assessment: "Certificate matches the Bishopsgate Tower ontology in full", approval: "Yes — after switch" },
  { id: "a3", when: "Mon 16:20", who: "Marcus Hale", role: "Admin", building: "Town Hall", finalB: "Town Hall", doc: "Contract addendum — NovaClean FM.pdf", outcome: "Overridden", tone: "warn", checks: "6 run · 1 unresolved", issue: "NovaClean FM is not an approved vendor for Town Hall", suggested: "—", explanation: "New vendor — contract onboarding completes next week", assessment: "Plausible: vendor record missing rather than wrong. Concern noted, not resolved", approval: "Yes — explicit override" },
  { id: "a4", when: "Mon 11:03", who: "Tom Whitfield", role: "User", building: "Building 5", finalB: "—", doc: "Vendor invoice — HVAC quarterly service.pdf", outcome: "Rejected", tone: "risk", checks: "6 run · 3 mismatched", issue: "Invoice lines reference AHU assets on record at Marina Heights", suggested: "Marina Heights", explanation: "Withdrawn by uploader after review", assessment: "Mismatch confirmed — document does not belong to Building 5", approval: "No" },
  { id: "a5", when: "28 Aug 14:47", who: "Marcus Hale", role: "Admin", building: "Marina Heights", finalB: "Marina Heights", doc: "First UDR pack — Marina Heights (14 files)", outcome: "Approved on confirmation", tone: "dormant", checks: "6 run · 4 inconclusive", issue: "New building — insufficient reference data to validate relationships", suggested: "—", explanation: "First ingestion for a newly created building", assessment: "Cannot be logically proven — the building ontology is still empty", approval: "Yes — explicit confirmation" }
];
