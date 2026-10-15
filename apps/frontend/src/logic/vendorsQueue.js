// vendorsQueue — the Vendors page's insight cards, opened (Hussain, 7 Oct 2026).
//
// Each card used to jump to one vendor's tab — "Vendors blocked: 2" opened the first blocked
// vendor's Coverage and nothing said where the other one was. Now a card opens its list in
// place, the way the Compliance page's Needs-you cards do: every item behind the number, each
// one opening to the actions that resolve it. Those actions are the ones the page already
// has — the accreditation request email, the Decision queue's plan for an approval item (its
// page and its email draft), the vendor's own tabs, the terms confirmation.
//
// Methods are mixed into HoistraLogic.prototype; `this` is the controller.
import { canRequest } from './accreditationRequest.js';
import { humanise } from './homeLive.js';

const TONE = {
  risk: { bg: "var(--st-risk-bg)", fg: "var(--st-risk)" },
  warn: { bg: "var(--st-warn-bg)", fg: "var(--st-warn)" },
  ok: { bg: "var(--st-ok-bg)", fg: "var(--st-ok)" },
  none: { bg: "var(--color-neutral-900)", fg: "var(--color-neutral-400)" }
};
const chip = (label, tone) => Object.assign({ label: label }, TONE[tone] || TONE.none);
const isCritical = (it) => /high|critical/i.test(String(it.severity || ""));
const plural = (n, one, many) => n + " " + (n === 1 ? one : (many || one + "s"));
const day = (iso) => {
  const d = iso ? new Date(iso) : null;
  return d && !isNaN(d) ? d.toLocaleDateString("en-GB", { day: "2-digit", month: "short", year: "numeric" }) : null;
};

export const VP_QUEUES = {
  blocked: { title: "Vendors blocked", hint: "A blocked vendor cannot be allocated regulated work, and its score is held at the ceiling of 60. Each one is short of an accreditation — request it, and the block lifts when it is on record.", noun: ["vendor", "vendors"] },
  pending: { title: "Pending tasks", hint: "Everything the vendor engine has put to you for a decision. Act on one to open it where it belongs, with the email that resolves it drafted.", noun: ["task", "tasks"] },
  critical: { title: "Pending critical", hint: "The pending tasks marked high or critical — the ones touching L1 assets. Act on these first.", noun: ["task", "tasks"] },
  l1: { title: "L1 breaches this period", hint: "Response or completion targets missed on L1 assets, weighted three times in the score. Each is a scored work order — open the evidence behind it, or claim the service credit.", noun: ["breach", "breaches"] },
  held: { title: "Invoice lines held", hint: "Lines that fail the contract's rate schedule, held until someone decides. Query one with the vendor, or open the invoice tab to approve or credit it.", noun: ["line", "lines"] },
  defaults: { title: "Terms on platform default", hint: "Contract terms the document did not state, so the platform's default is in force. Confirm each vendor's terms, or ingest the signed contract to replace them.", noun: ["vendor", "vendors"] }
};

export const vendorsQueueMethods = {
  vpQueueOpen(id) {
    this.setState((p) => ({ vpQueue: p.vpQueue === id ? null : id, vpQueueOpenId: null }));
  },
  vpQueueClose() { this.setState({ vpQueue: null, vpQueueOpenId: null }); },
  // Leave the list for one vendor's tab.
  vpQueueVendor(id, tab) {
    this.setState({ vpQueue: null, vpQueueOpenId: null, vpVendor: id, vpTab: tab });
    if (typeof window !== "undefined" && window.scrollTo) window.scrollTo(0, 0);
  },

  vpQueueVals() {
    const s = this.state;
    const q = VP_QUEUES[s.vpQueue] || null;
    if (!q) return { vpQueueShow: false, vpBrowse: true };
    const vm = this.vpModel();
    const vendors = vm.vendors || [];
    const V = vm.V || {};
    const byId = {};
    vendors.forEach((v) => { byId[String(v.id)] = v; });
    const nameOf = (id) => (byId[String(id)] && byId[String(id)].name) || null;

    // One row: a head that opens, and the actions under it.
    const row = (key, head, actions) => {
      const open = s.vpQueueOpenId === key;
      return Object.assign({}, head, {
        key: key, open: open,
        caret: open ? "ph-caret-up" : "ph-caret-down",
        toggle: () => this.setState((p) => ({ vpQueueOpenId: p.vpQueueOpenId === key ? null : key })),
        actions: actions.filter(Boolean).map((a, j) => ({
          label: a.label, run: a.run,
          bg: j === 0 ? "var(--color-accent)" : "transparent",
          fg: j === 0 ? "var(--accent-ink)" : "var(--color-neutral-300)",
          border: j === 0 ? "var(--color-accent)" : "var(--color-divider)"
        }))
      });
    };
    const approvalCard = (it) => ({ kind: "approval", module: "Vendors", title: it.summary || humanise(it.item_type), item: it });

    let rows = [];
    if (s.vpQueue === "blocked") {
      rows = vendors.filter((v) => v.blocked).map((v) => {
        const R = V[v.id] || {};
        const owed = (R.certs || []).filter(canRequest);
        const lapsed = owed.filter((c) => c.status === "Lapsed").length;
        const missing = owed.filter((c) => c.status === "Not on record").length;
        const first = owed[0] || null;
        return row("b:" + v.id, {
          title: v.name,
          sub: vm.pkgOf(v.id) + " · " + (owed.length ? [lapsed ? lapsed + " lapsed" : null, missing ? missing + " never supplied" : null, owed.length - lapsed - missing ? (owed.length - lapsed - missing) + " expiring" : null].filter(Boolean).join(" · ") : "no owed accreditation on record"),
          meta: first ? "First to resolve: " + first.name + " — " + first.status.toLowerCase() : "",
          chips: [chip("Blocked", "risk")]
        }, [
          first ? { label: "Request " + first.name, run: () => { this.vpQueueClose(); this.vpRequestAccreditation(v, first); } } : null,
          { label: "Open coverage", run: () => this.vpQueueVendor(v.id, 3) },
          { label: "Open accreditations", run: () => this.vpQueueVendor(v.id, 5) }
        ]);
      });
    } else if (s.vpQueue === "pending" || s.vpQueue === "critical") {
      const items = (vm.approvalItems || []).filter((it) => s.vpQueue === "pending" || isCritical(it))
        .slice().sort((a, b) => (isCritical(b) ? 1 : 0) - (isCritical(a) ? 1 : 0) || String(b.created_at || "").localeCompare(String(a.created_at || "")));
      rows = items.map((it, i) => {
        const vid = it.related_entity_type === "vendor" ? it.related_entity_id : ((it.payload || {}).vendor_id || null);
        const vendor = nameOf(vid);
        const sev = String(it.severity || "");
        return row("p:" + (it.id || i), {
          title: it.summary || humanise(it.item_type),
          sub: [vendor, humanise(it.item_type)].filter(Boolean).join(" · "),
          meta: day(it.created_at) ? "Raised " + day(it.created_at) : "",
          chips: [chip(sev ? sev.replace(/^./, (x) => x.toUpperCase()) : "Pending", isCritical(it) ? "risk" : "warn")]
        }, [
          { label: "Act on it", run: () => { this.vpQueueClose(); this.queueOpenItem(approvalCard(it)); } },
          vid && byId[String(vid)] ? { label: "Open vendor", run: () => this.vpQueueVendor(vid, 0) } : null
        ]);
      });
    } else if (s.vpQueue === "l1") {
      vendors.forEach((v) => {
        ((V[v.id] || {}).breaches || []).filter((b) => b.crit === "L1" && b.met === false && b.mult !== "—").forEach((b, i) => {
          rows.push(row("l:" + v.id + ":" + b.wo + ":" + b.metric + ":" + i, {
            title: b.wo + " · " + b.metric + " missed",
            sub: v.name + " · " + b.asset + (b.building && b.building !== "—" ? " · " + b.building : ""),
            meta: "Target " + b.target + " · actual " + b.actual + " · weighted " + b.mult,
            chips: [chip("L1", "risk")]
          }, [
            { label: "Open evidence", run: () => this.vpQueueVendor(v.id, 2) },
            { label: "Claim service credits", run: () => { this.vpQueueClose(); this.runAction("Claim service credits", v.name); } }
          ]));
        });
      });
    } else if (s.vpQueue === "held") {
      const items = vm.approvalItems || [];
      vendors.forEach((v) => {
        ((V[v.id] || {}).invoices || []).filter((l) => l.status === "Held" || l.status === "Disputed").forEach((l, i) => {
          const it = items.find((x) => x && x.id === l.itemId) || null;
          rows.push(row("h:" + (l.itemId || v.id + ":" + i), {
            title: l.ref + " · " + l.line,
            sub: v.name + (l.period ? " · " + l.period : ""),
            meta: l.flag,
            chips: [chip(l.status, l.status === "Disputed" ? "risk" : "warn")].concat(l.delta && l.delta !== "—" ? [chip(l.delta, "none")] : [])
          }, [
            it ? { label: "Query the invoice", run: () => { this.vpQueueClose(); this.queueOpenItem(approvalCard(it)); } } : null,
            { label: "Open invoices", run: () => this.vpQueueVendor(v.id, 4) }
          ]));
        });
      });
    } else if (s.vpQueue === "defaults") {
      rows = vendors.filter((v) => ((V[v.id] || {}).contract || {}).defaults > 0)
        .sort((a, b) => V[b.id].contract.defaults - V[a.id].contract.defaults)
        .map((v) => {
          const k = V[v.id].contract;
          const draft = !!(k.id && k.status && !/confirm/i.test(String(k.status)));
          return row("d:" + v.id, {
            title: v.name,
            sub: k.defaults + " of " + k.fields + " terms on platform default" + (k.ref ? " · " + k.ref : ""),
            meta: k.read ? k.read + " read from the signed contract" + (k.doc ? " (" + k.doc + ")" : "") : "Nothing read from a signed contract yet",
            chips: [chip(plural(k.defaults, "default"), "warn")].concat(draft ? [chip("Draft", "none")] : [])
          }, [
            k.id ? { label: "Review and confirm terms", run: () => { this.setState({ vpQueue: null, vpQueueOpenId: null }); this.vpGoConfirm(v.id); } } : null,
            { label: "Open contract terms", run: () => this.vpQueueVendor(v.id, 1) }
          ]);
        });
    }

    const n = rows.length;
    // The tile can count more than the list holds — the summary counts every pending item, the
    // list only those the approvals read returned. Say so rather than look short. Terms on
    // default is the exception: its tile counts terms and its list counts vendors, so it says
    // both rather than comparing the two.
    const terms = s.vpQueue === "defaults" ? vendors.reduce((t, v) => t + ((((V[v.id] || {}).contract || {}).defaults) || 0), 0) : 0;
    const tile = vm.tiles && s.vpQueue !== "defaults" ? vm.tiles[s.vpQueue === "l1" ? "L1" : s.vpQueue] : null;
    const shortBy = typeof tile === "number" && tile > n ? tile - n : 0;
    return {
      vpQueueShow: true, vpBrowse: false,
      vpQTitle: q.title, vpQHint: q.hint,
      vpQCount: s.vpQueue === "defaults" ? plural(terms, "term") + " across " + plural(n, "vendor") : plural(n, q.noun[0], q.noun[1]),
      vpQNote: shortBy ? shortBy + " more counted by the engine than this read returned." : "",
      vpQRows: rows,
      vpQEmpty: n ? "" : "Nothing in this list right now.",
      vpQClose: () => this.vpQueueClose()
    };
  }
};
