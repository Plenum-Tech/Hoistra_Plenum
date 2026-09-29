// vendorAudit — the Vendor audit trail, the second tab on the Audit trail page (Hussain,
// 29 Sep 2026): which vendor changed, how, when, and who or what changed it.
//
// Three kinds, as GET /api/admin/vendor-audit returns them (svc-operations-intelligence
// engines/auth/vendor_audit.py):
//   reassigned  an asset moved between vendors in the vendor drawer, by a named admin
//   blocked     compliance blocked a vendor for a lapsed accreditation — automatic
//   cleared     a block was lifted by a scan or a newer certificate — automatic
// Reassignments are recorded from 28 Sep 2026 and blocks/clears from 29 Sep 2026; earlier
// blocks were never kept, and the empty trail says so rather than looking broken.
//
// The page reads one page of the trail and narrows it here, the way the ingestion tab does
// (logic/auditTrail.js): the tiles describe the filtered set, and each chip's count is the set
// narrowed by every OTHER filter, so pressing a chip never changes its own number.
//
// vendorAuditModel() is pure; the methods are mixed into HoistraLogic.prototype.
import { adminApi } from '../api/admin.js';
import { isStaleScope } from '../api/client.js';
import { AU_TONE, AU_RANGES, MONTHS, daysBetween, dayLabel } from './auditTrail.js';

const KINDS = [["All", null], ["Reassigned", "reassigned"], ["Blocked", "blocked"], ["Cleared", "cleared"]];
const BADGE = { reassigned: ["Reassigned", "accent"], blocked: ["Blocked", "risk"], cleared: ["Cleared", "ok"] };
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

/** One trail entry as the row shows it. Pure. */
export function vendorRow(e) {
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
  return { id: e.id, at: e.at, kind: e.kind, clock: clock(e.at), who, role, vendor, change, building,
    badge, fg, bg, fields: fields.map(([l, v]) => ({ l, v })), words,
    vendors: [nameOf(e.from_vendor), nameOf(e.to_vendor), nameOf(e.vendor)].filter(Boolean) };
}

/** What the tab renders, from the state. Pure: every line is what the read returned. */
export function vendorAuditModel(s, h) {
  const now = s.vaNow ? new Date(s.vaNow) : new Date();
  const all = (s.vaEvents || []).map(vendorRow);
  const q = String(s.vaQuery || "").trim().toLowerCase();
  const reach = (AU_RANGES.find((r) => r[0] === s.vaRange) || AU_RANGES[3])[1];
  const inRange = (r) => reach === null || ((daysBetween(r.at, now) ?? Infinity) <= reach);
  const byOthers = all.filter((r) => inRange(r) && (!q || r.words.indexOf(q) !== -1));
  const kind = (KINDS.find((k) => k[0] === s.vaKind) || KINDS[0])[1];
  const shown = byOthers.filter((r) => !kind || r.kind === kind)
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
    vaSourceLabel: s.vaError ? "Not read — svc-operations-intelligence" : loaded ? "Live · svc-operations-intelligence" : "Reading…",
    vaSourceDot: s.vaError ? "var(--st-risk)" : loaded ? "var(--st-ok)" : "var(--st-dormant)",
    vaQuery: s.vaQuery || "",
    vaSetQuery: (v) => h.query(v),
    vaFilters: KINDS.map(([label, k]) => {
      const on = (s.vaKind || "All") === label;
      const [fg, bg] = on ? ["var(--accent-ink)", "var(--color-accent)"] : ["var(--color-neutral-300)", "var(--color-surface)"];
      return { label, count: byOthers.filter((r) => !k || r.kind === k).length, on, fg, bg, pick: () => h.kind(label) };
    }),
    vaRangePicks: AU_RANGES.map(([label]) => ({ label, on: (s.vaRange || "All") === label, pick: () => h.range(label) })),
    vaGroups: groups,
    vaInView: shown.length,
    vaVendorsAffected: new Set(shown.flatMap((r) => r.vendors)).size,
    vaBlocks: shown.filter((r) => r.kind === "blocked").length,
    vaShownLine: shown.length + " of " + all.length + " change" + (all.length === 1 ? "" : "s"),
    vaEmpty: !loaded || s.vaError || groups.length ? ""
      : all.length ? "Nothing in this range. Widen the dates, or clear a filter."
        : "No vendor changes recorded yet. Asset reassignments are recorded from 28 Sep 2026, and compliance "
          + "blocks and clears from 29 Sep 2026 — blocks before that were never kept."
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
    }));
  }
};
