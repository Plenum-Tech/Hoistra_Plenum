// vendorAudit — the Vendor audit trail, the second tab on the Audit trail page (Hussain,
// 29 Sep 2026): which vendor changed, how, when, and who or what changed it.
//
// Three kinds, as GET /api/admin/vendor-audit returns them (svc-operations-intelligence
// engines/auth/vendor_audit.py):
//   reassigned  an asset moved between vendors in the vendor drawer, by a named admin
//   blocked     compliance blocked a vendor for a lapsed accreditation — automatic
//   cleared     a block was lifted by a scan or a newer certificate — automatic
//   deleted     an admin deleted a vendor (logic/vendorDelete.js): removed completely when it had
//               no history, retired — history kept, marked (deleted) — when it had; or a data
//               reset deleted vendors, one entry per run. Always a person's act, on every building
// Reassignments are recorded from 28 Sep 2026, blocks/clears from 29 Sep 2026 and deletions from
// 12 Oct 2026; earlier blocks were never kept, and the empty trail says so rather than looking
// broken.
//
// Hoisting (12 Oct 2026, logic/vendorHoist.js) adds more, which the server does not log:
//   hoist_saved      an admin hoisted a vendor from User view into the vendor master
//   hoist_failed     the vendor master refused a hoist — the reason is kept
//   hoist_prepared   a record prepared before saving was switched on (older entries)
//   hoist_dismissed  a detected vendor was marked Not a vendor
//   hoist_discarded  a record that was never saved was discarded
// They are this browser's own record of what was done here. They sit in the same list, and
// every one of them says so.
//
// The page reads one page of the trail and narrows it here, the way the ingestion tab does
// (logic/auditTrail.js): the tiles describe the filtered set, and each chip's count is the set
// narrowed by every OTHER filter, so pressing a chip never changes its own number.
//
// vendorAuditModel() is pure; the methods are mixed into HoistraLogic.prototype.
import { adminApi } from '../api/admin.js';
import { isStaleScope } from '../api/client.js';
import { AU_TONE, AU_RANGES, MONTHS, daysBetween, dayLabel } from './auditTrail.js';

const KINDS = [["All", null], ["Reassigned", "reassigned"], ["Blocked", "blocked"], ["Cleared", "cleared"], ["Deleted", "deleted"], ["Buildings", "buildings"], ["Hoisting", "hoist"]];
const BADGE = { reassigned: ["Reassigned", "accent"], blocked: ["Blocked", "risk"], cleared: ["Cleared", "ok"], deleted: ["Deleted", "risk"],
  assigned: ["Assigned", "accent"], unassigned: ["Unassigned", "dormant"],
  hoist_saved: ["Hoisted", "ok"], hoist_failed: ["Hoist failed", "risk"],
  hoist_prepared: ["Hoist prepared", "warn"], hoist_dismissed: ["Not a vendor", "dormant"], hoist_discarded: ["Hoist discarded", "dormant"] };
const HOIST_CHANGE = {
  hoist_saved: "Saved to the vendor master",
  hoist_failed: "Not saved — the vendor master refused it",
  hoist_prepared: "Hoist prepared · not saved to the vendor master",
  hoist_dismissed: "Marked Not a vendor · hidden from detected vendors",
  hoist_discarded: "Prepared record discarded"
};
const HOIST_FIELDS = [["trade", "Trade / scope"], ["email", "Accounts email"], ["phone", "Phone"], ["reg", "Company registration"],
  ["contract", "Contract"], ["rate", "Rate"], ["accreds", "Accreditations"], ["areas", "Buildings served"]];
// The answers the vendor master has no field for (logic/vendorHoist.js NOT_STORED).
// Buildings are stored now: the buildings question assigns the vendor (12 Oct 2026).
const NOT_STORED = [["trade", "trade"], ["reg", "registration"], ["contract", "contract"], ["rate", "rate"],
  ["accreds", "accreditations"]];
const SOURCE = { compliance_scan: "Compliance scan", certificate: "Certificate upload",
  certificate_superseded: "Newer certificate" };
const ROLE = { admin: "Admin", superadmin: "Super admin", user: "User" };

const pad = (n) => String(n).padStart(2, "0");
function clock(at) {
  const d = at ? new Date(at) : null;
  return d && !isNaN(d) ? pad(d.getHours()) + ":" + pad(d.getMinutes()) : "—";
}
function fullWhen(at) {
  const d = at ? new Date(at) : null;
  return d && !isNaN(d) ? d.getDate() + " " + MONTHS[d.getMonth()] + " " + d.getFullYear() + ", " + clock(at) : "—";
}
const nameOf = (p) => (p && (p.name || p.id)) || null;
const assetLine = (a) => (a ? [a.name, a.code].filter(Boolean).join(" · ") : null);

// ── deletions ─────────────────────────────────────────────────────────────────
// What a delete changed, counted by the service per group under the table it touched ("assets",
// or "table.column" for a reference found by its column). Named here as a person reads them; a
// key not listed is counted as "other record", never shown by its table name.
const EFFECT = {
  vendor_contacts: ["contact", "contacts"], files: ["file link", "file links"],
  document_links: ["document link", "document links"], approval_action_tokens: ["share link", "share links"],
  fm_report_staleness: ["report tracking record", "report tracking records"], resource_skills: ["skill record", "skill records"],
  assets: ["asset", "assets"], work_orders: ["open work order", "open work orders"], ppm_visits: ["planned visit", "planned visits"],
  spare_parts: ["spare part", "spare parts"], maintenance_plans: ["maintenance plan", "maintenance plans"],
  approvals_queue_items: ["pending decision", "pending decisions"], compliance_certificates: ["certificate", "certificates"],
  invoice_verifications: ["invoice", "invoices"], invoice_lines: ["invoice line", "invoice lines"],
  cost_variance_alerts: ["cost alert", "cost alerts"], vendor_wo_scores: ["scored job", "scored jobs"],
  vendor_monthly_scorecards: ["monthly scorecard", "monthly scorecards"],
  contract_sla_parameters: ["set of contract terms", "sets of contract terms"], contract_documents: ["contract document", "contract documents"],
  vendor_contracts: ["contract", "contracts"], resources: ["technician", "technicians"], purchase_orders: ["purchase order", "purchase orders"]
};
// History is the closed side of the same tables.
const KEPT_EFFECT = { work_orders: ["closed work order", "closed work orders"], ppm_visits: ["past planned visit", "past planned visits"] };
const NAMES_SHOWN = 10;
const n0 = (v) => (typeof v === "number" && isFinite(v) && v > 0 ? v : Number(v) > 0 ? Number(v) : 0);
const count = (n, [one, many]) => n.toLocaleString("en-GB") + " " + (n === 1 ? one : many);
const sumOf = (o) => Object.keys(o || {}).reduce((t, k) => t + n0(o[k]), 0);
// "2 contacts · 1 document link", in the order given, unknown keys folded into "other records".
function effectLine(groups, kept) {
  const parts = [];
  let other = 0;
  groups.forEach((g) => {
    Object.keys(g || {}).forEach((k) => {
      const n = n0(g[k]);
      if (!n) return;
      const label = (kept && KEPT_EFFECT[k]) || (k.indexOf(".") < 0 && EFFECT[k]);
      if (label) parts.push(count(n, label)); else other += n;
    });
  });
  if (other) parts.push(count(other, ["other record", "other records"]));
  return parts.join(" · ");
}
// The first names of a list, and how many more the count says there were.
function namesLine(names, total) {
  const shown = names.filter(Boolean).slice(0, NAMES_SHOWN);
  const rest = Math.max(total || 0, names.length) - shown.length;
  return shown.join(", ") + (rest > 0 ? " and " + rest.toLocaleString("en-GB") + " more" : "");
}
const PERSON_ROLE = (e) => (e.actor ? (ROLE[String(e.actor.role || "").toLowerCase()] || "User") : "—");

// A deletion, by an admin or by a data reset. Pure.
function deletedRow(e) {
  const person = (e.actor && (e.actor.name || e.actor.email)) || "A user no longer on record";
  const role = PERSON_ROLE(e);
  const [badge, tone] = BADGE.deleted;
  const [fg, bg] = AU_TONE[tone] || AU_TONE.dormant;
  const by = [person, e.actor && e.actor.name && e.actor.email ? e.actor.email : null, e.actor ? role : null].filter(Boolean).join(" · ");
  const reset = e.mode === "reset";
  const list = reset ? (Array.isArray(e.vendors) ? e.vendors : []) : (e.vendor ? [e.vendor] : []);
  const names = list.map(nameOf).filter(Boolean);
  const n = reset ? (n0(e.count) || names.length) : 1;
  const vendorCol = reset ? (names.length ? namesLine(names.slice(0, 2), n) : count(n, ["vendor", "vendors"])) : (names[0] || "—");
  const codes = list.map((v) => v && v.code).filter(Boolean);
  const how = reset ? "Deleted by a data reset — " + count(n, ["vendor", "vendors"])
    : e.mode === "remove" ? "Removed completely — it had no history"
      : "Retired — hidden everywhere, its history kept and marked (deleted)";
  const change = reset ? how : e.mode === "remove" ? "Removed completely" : "Retired — history kept";
  const fx = e.effects || {};
  const un = fx.unassigned || {};
  const unParts = [];
  [["assets", "Assets", e.assets, assetLine], ["work_orders", "Open work orders", e.work_orders, (w) => (w ? [w.code, w.title].filter(Boolean).join(" · ") : null)]]
    .forEach(([k, label, items, line]) => {
      const named = (Array.isArray(items) ? items : []).map(line).filter(Boolean);
      const total = Math.max(n0(un[k]), named.length);
      if (total) unParts.push(label + ": " + (named.length ? namesLine(named, total) : count(total, EFFECT[k])));
    });
  const rest = effectLine([Object.fromEntries(Object.keys(un).filter((k) => k !== "assets" && k !== "work_orders").map((k) => [k, un[k]]))]);
  if (rest) unParts.push(rest);
  const closedN = sumOf(fx.closed), archivedN = sumOf(fx.archived);
  const kept = effectLine([fx.kept, fx.other], true);
  const fields = reset
    ? [["Vendor", names.length ? namesLine(names, n) : "—"], ["Vendor code", codes.length ? namesLine(codes, codes.length) : "—"],
       ["How", how], ["Removed", count(n, ["vendor", "vendors"])], ["Unassigned", "—"], ["Pending decisions closed", "—"],
       ["Accreditations archived", "—"], ["History kept", "—"]]
    : [["Vendor", names[0] || "—"], ["Vendor code", codes[0] || "—"], ["How", how],
       ["Removed", effectLine([fx.removed, fx.ended, fx.revoked]) || (e.mode === "remove" ? "Nothing else" : "Nothing")],
       ["Unassigned", unParts.join("; ") || "Nothing"],
       ["Pending decisions closed", closedN ? closedN.toLocaleString("en-GB") : "None"],
       ["Accreditations archived", archivedN ? archivedN.toLocaleString("en-GB") : "None"],
       ["History kept", kept || (e.mode === "remove" ? "None — it had no history" : "None")]];
  fields.push(["Why", e.reason || "No reason given"], ["Deleted by", by], ["When", fullWhen(e.at)]);
  const words = names.concat(codes, [person, e.actor && e.actor.email, e.reason, change, badge]).filter(Boolean).join(" ").toLowerCase();
  return { id: e.id, at: e.at, kind: "deleted", group: "deleted", clock: clock(e.at), who: person, role, vendor: vendorCol,
    change, building: "Every building", badge, fg, bg, fields: fields.map(([l, v]) => ({ l, v })), words, vendors: names };
}

// A hoisting entry, kept in this browser (logic/vendorHoist.js). Pure.
function hoistRow(e) {
  const person = (e.actor && (e.actor.name || e.actor.email)) || "Unknown person";
  const role = (e.actor ? (ROLE[String(e.actor.role || "").toLowerCase()] || "User") : "—") + " · this browser";
  const [badge, tone] = BADGE[e.kind];
  const [fg, bg] = AU_TONE[tone] || AU_TONE.dormant;
  const vendor = nameOf(e.vendor) || "—";
  const f = e.fields || {};
  const by = [person, e.actor && e.actor.name && e.actor.email ? e.actor.email : null].filter(Boolean).join(" · ");
  const found = e.certificates ? e.certificates + (e.certificates === 1 ? " certificate" : " certificates") : "Hoisted from scratch";
  const answers = HOIST_FIELDS.map(([k, l]) => [l, String(f[k] || "").trim() || "—"]);
  const kept = NOT_STORED.filter(([k]) => String(f[k] || "").trim()).map(([, l]) => l);
  const contact = e.contact === "saved" ? "Saved" : e.contact === "failed" ? "Not saved — " + (e.contactError || "refused") : "None given";
  const body = e.kind === "hoist_saved"
    ? [["Vendor id", e.vendorId || "—"],
       ["In the vendor master", e.created === false ? "Yes — matched an existing vendor of that name" : "Yes — created"]]
      .concat(e.certificates ? [["Certificates linked", String(e.linked || 0) + (e.linkFailed ? " · " + e.linkFailed + " could not be linked" : "")]] : [])
      .concat([["Contact", contact]], answers,
        [["Not stored in the vendor master", kept.length ? kept.join(", ") + " — no field for them yet; shown here from the hoist" : "—"]])
    : e.kind === "hoist_failed"
      ? [["Why it failed", e.error || "—"]].concat(answers, [["Found on", found]])
      : e.kind === "hoist_prepared"
        ? answers.concat([["Found on", found], ["Saved to the vendor master", "No — saving wasn't switched on when this was prepared."]])
        : [["What changed", HOIST_CHANGE[e.kind]], ["Trade / scope", e.trade || "—"]]
          .concat(e.kind === "hoist_dismissed" ? [["Found on", found]] : [], [["Recorded in", "This browser only"]]);
  const fields = [["Vendor", vendor]].concat(body, [["Made by", by], ["When", fullWhen(e.at)]]);
  const words = [vendor, person, e.actor && e.actor.email, e.trade, f.areas, HOIST_CHANGE[e.kind], badge].filter(Boolean).join(" ").toLowerCase();
  return { id: e.id, at: e.at, kind: e.kind, group: "hoist", clock: clock(e.at), who: person, role, vendor,
    change: HOIST_CHANGE[e.kind], building: String(f.areas || "").trim() || "—", badge, fg, bg, local: true,
    fields: fields.map(([l, v]) => ({ l, v })), words, vendors: [vendor] };
}

// A vendor assigned to buildings, or taken off one (ops-intelligence compliance/vendor_buildings.py).
function buildingsRow(e) {
  const person = (e.actor && (e.actor.name || e.actor.email)) || (e.actor ? "A user no longer on record" : "Unknown person");
  const role = e.actor ? (ROLE[String(e.actor.role || "").toLowerCase()] || "User") : "—";
  const [badge, tone] = BADGE[e.kind];
  const [fg, bg] = AU_TONE[tone] || AU_TONE.dormant;
  const vendor = nameOf(e.vendor) || "—";
  const names = (Array.isArray(e.buildings) ? e.buildings : []).map((b) => (b && (b.name || b.id)) || null).filter(Boolean);
  const where = names.join(", ") || "—";
  const change = e.kind === "assigned" ? "Assigned to " + where + (e.source === "hoist" ? " · when it was hoisted" : "") : "Taken off " + where;
  const by = [person, e.actor && e.actor.name && e.actor.email ? e.actor.email : null, e.actor ? role : null].filter(Boolean).join(" · ");
  const fields = [["Vendor", vendor], [e.kind === "assigned" ? "Assigned to" : "Taken off", where],
    ["How", e.kind === "assigned" ? (e.source === "hoist" ? "Hoist vendors · the buildings question" : "Vendors page · Buildings served") : "Vendors page · Buildings served"],
    ["Made by", by], ["When", fullWhen(e.at)]];
  const words = [vendor, where, person, e.actor && e.actor.email, change, badge].filter(Boolean).join(" ").toLowerCase();
  return { id: e.id, at: e.at, kind: e.kind, group: "buildings", clock: clock(e.at), who: person, role, vendor,
    change, building: where, badge, fg, bg, fields: fields.map(([l, v]) => ({ l, v })), words, vendors: [vendor] };
}

/** One trail entry as the row shows it. Pure. */
export function vendorRow(e) {
  if (e && HOIST_CHANGE[e.kind]) return hoistRow(e);
  if (e && (e.kind === "assigned" || e.kind === "unassigned")) return buildingsRow(e);
  // Its own branch: the one below reads any other kind as a block lifted by Compliance.
  if (e && e.kind === "deleted") return deletedRow(e);
  const byPerson = e.source === "person";
  // A reassignment is always a person's. Its admin may no longer be on record (a deleted user),
  // and that is said — never credited to Compliance (pre-push review, 29 Sep 2026).
  const person = (e.actor && (e.actor.name || e.actor.email))
    || (byPerson ? (e.actor ? "A user no longer on record" : "Unknown person") : null);
  const role = e.actor ? (ROLE[String(e.actor.role || "").toLowerCase()] || "User") : byPerson ? "—" : "Automatic";
  const who = person || SOURCE[e.source] || "Automatic";
  const [badge, tone] = BADGE[e.kind] || ["Changed", "dormant"];
  const [fg, bg] = AU_TONE[tone] || AU_TONE.dormant;
  const vendor = e.kind === "reassigned"
    ? (nameOf(e.from_vendor) || "No vendor") + " → " + (nameOf(e.to_vendor) || "—")
    : nameOf(e.vendor) || "—";
  const change = e.kind === "reassigned" ? (assetLine(e.asset) || "An asset")
    : e.kind === "blocked" ? (e.reason || "Blocked" + (e.accreditation ? " — " + e.accreditation : ""))
      : "Block lifted" + (e.accreditation ? " — " + e.accreditation : "");
  const building = e.kind === "reassigned" ? ((e.building && e.building.name) || "—") : "Every building it serves";
  const by = byPerson
    ? [person, e.actor && e.actor.name && e.actor.email ? e.actor.email : null, e.actor ? role : null]
        .filter(Boolean).join(" · ")
    : (SOURCE[e.source] || "Compliance") + " — automatic";
  const fields = e.kind === "reassigned"
    ? [["Asset", assetLine(e.asset) || "—"], ["From vendor", nameOf(e.from_vendor) || "No vendor"],
       ["To vendor", nameOf(e.to_vendor) || "—"], ["Building", building], ["Changed by", by],
       ["Why", e.note || "No reason given"], ["When", fullWhen(e.at)]]
    : [["Vendor", vendor], [e.kind === "blocked" ? "Reason" : "What changed", change],
       ["Accreditation", e.accreditation || "—"], ["Made by", by],
       ["Certificate", e.certificate_id || "—"], ["When", fullWhen(e.at)]];
  const words = [vendor, nameOf(e.from_vendor), nameOf(e.to_vendor), nameOf(e.vendor), assetLine(e.asset),
    person, e.actor && e.actor.email, e.building && e.building.name, e.reason, e.accreditation, e.note, who]
    .filter(Boolean).join(" ").toLowerCase();
  return { id: e.id, at: e.at, kind: e.kind, group: e.kind, clock: clock(e.at), who, role, vendor, change, building,
    badge, fg, bg, fields: fields.map(([l, v]) => ({ l, v })), words,
    vendors: [nameOf(e.from_vendor), nameOf(e.to_vendor), nameOf(e.vendor)].filter(Boolean) };
}

/** What the tab renders, from the state. Pure: every line is what the read returned, plus this
 *  browser's own hoisting entries (`local`), which say what they are. */
export function vendorAuditModel(s, h, local) {
  const now = s.vaNow ? new Date(s.vaNow) : new Date();
  const mine = local || [];
  const all = (s.vaEvents || []).concat(mine).map(vendorRow);
  const q = String(s.vaQuery || "").trim().toLowerCase();
  const reach = (AU_RANGES.find((r) => r[0] === s.vaRange) || AU_RANGES[3])[1];
  const inRange = (r) => reach === null || ((daysBetween(r.at, now) ?? Infinity) <= reach);
  const byOthers = all.filter((r) => inRange(r) && (!q || r.words.indexOf(q) !== -1));
  const kind = (KINDS.find((k) => k[0] === s.vaKind) || KINDS[0])[1];
  const shown = byOthers.filter((r) => !kind || r.group === kind)
    .sort((a, b) => String(b.at || "").localeCompare(String(a.at || "")));
  const groups = [];
  for (const r of shown) {
    const label = dayLabel(r.at, now);
    let g = groups[groups.length - 1];
    if (!g || g.label !== label) groups.push(g = { label, rows: [] });
    g.rows.push(Object.assign({}, r, { open: s.vaOpen === r.id, panelShow: s.vaOpen === r.id ? "grid" : "none",
      toggle: () => h.toggle(r.id) }));
  }
  groups.forEach((g) => { g.count = g.rows.length; });
  const loaded = Array.isArray(s.vaEvents);
  return {
    vaLoading: !!s.vaLoading && !loaded,
    vaError: s.vaError || "",
    vaRetry: () => h.load(),
    vaUnreadableNote: (s.vaUnreadable || []).length ? "Could not be read just now: " + s.vaUnreadable.join(", ") + "." : "",
    vaSourceLabel: s.vaError ? "Not read" : loaded ? "Live" : "Reading…",
    vaSourceDot: s.vaError ? "var(--st-risk)" : loaded ? "var(--st-ok)" : "var(--st-dormant)",
    vaQuery: s.vaQuery || "",
    vaSetQuery: (v) => h.query(v),
    vaFilters: KINDS.map(([label, k]) => {
      const on = (s.vaKind || "All") === label;
      const [fg, bg] = on ? ["var(--accent-ink)", "var(--color-accent)"] : ["var(--color-neutral-300)", "var(--color-surface)"];
      return { label, count: byOthers.filter((r) => !k || r.group === k).length, on, fg, bg, pick: () => h.kind(label) };
    }),
    vaRangePicks: AU_RANGES.map(([label]) => ({ label, on: (s.vaRange || "All") === label, pick: () => h.range(label) })),
    vaGroups: groups,
    vaInView: shown.length,
    vaVendorsAffected: new Set(shown.flatMap((r) => r.vendors)).size,
    vaBlocks: shown.filter((r) => r.kind === "blocked").length,
    vaShownLine: shown.length + " of " + all.length + " change" + (all.length === 1 ? "" : "s"),
    // The server does not log hoists, so these entries are this browser's own record.
    vaLocalNote: mine.length
      ? mine.length + (mine.length === 1 ? " hoisting entry is" : " hoisting entries are") + " recorded in this browser — the server does not log hoists yet, so other admins won't see them here."
      : "",
    vaEmpty: (!loaded && !mine.length) || s.vaError || groups.length ? ""
      : all.length ? "Nothing in this range. Widen the dates, or clear a filter."
        : "No vendor changes recorded yet. Asset reassignments are recorded from 28 Sep 2026, compliance "
          + "blocks and clears from 29 Sep 2026 — blocks before that were never kept — and deletions from 12 Oct 2026."
  };
}

export const vendorAuditMethods = {
  // Every visit to the tab reads the trail again: the change someone just made in the vendor
  // drawer is exactly what they come here to see (29 Sep 2026). Rows already shown stay on
  // screen while it reads; vaLoading only shows before the first answer.
  async auSetTab(tab) {
    this.setState({ auTab: tab === "vendors" ? "vendors" : "ingestion" });
    if (tab === "vendors") return this.vaLoad();
  },

  async vaLoad() {
    const token = (this._vaToken = (this._vaToken || 0) + 1);
    this.setState({ vaLoading: true, vaError: "" });
    let res = null, err = null;
    try { res = await adminApi.vendorAudit(); } catch (e) { err = e; }
    if (token !== this._vaToken) return;
    if (err && isStaleScope(err)) return this.setState({ vaLoading: false });
    if (err || !res || res.ok === false) {
      return this.setState({ vaLoading: false,
        vaError: "The vendor trail could not be read — " + ((err && err.message) || "no response") + "." });
    }
    this.setState({ vaLoading: false, vaEvents: res.events || [], vaUnreadable: res.unreadable || [] });
  },

  vendorAuditVals(s) {
    const tab = s.auTab === "vendors" ? "vendors" : "ingestion";
    return Object.assign({
      auTabs: [["Ingestion audit trail", "ingestion"], ["Vendor audit trail", "vendors"]].map(([label, key]) => ({
        label, on: tab === key, pick: () => this.auSetTab(key)
      })),
      auIsVendors: tab === "vendors",
      auTitle: tab === "vendors" ? "Vendor audit trail" : "Ingestion audit trail",
      auCrumb: tab === "vendors" ? "Vendor audit trail" : "Ingestion audit trail"
    }, vendorAuditModel(s, {
      load: () => this.vaLoad(),
      toggle: (id) => this.setState({ vaOpen: this.state.vaOpen === id ? null : id }),
      query: (v) => this.setState({ vaQuery: String(v || "").slice(0, 120) }),
      kind: (label) => this.setState({ vaKind: label, vaOpen: null }),
      range: (label) => this.setState({ vaRange: label, vaOpen: null })
    }, typeof this.vhTrailEvents === "function" ? this.vhTrailEvents(s) : []));
  }
};
