// integrations — integrations admin view model.
// Methods are mixed into HoistraLogic.prototype; `this` is the controller.
import { t } from './constants.js';
import { HOISTRA_INT } from '../data/hoistra-integrations.js';

export const integrationsMethods = {

  /* Integrations (admin). A connector is never presented as a logo and a button:
     each one declares the graph tables it enriches, the tables that only exist
     because of it, and what those tables make answerable. */
  intMark(name) {
    const w = name.replace(/[^A-Za-z0-9 ]/g, " ").split(/\s+/).filter(Boolean);
    return ((w[0] || "?")[0] + (w[1] ? w[1][0] : (w[0] || "?")[1] || "")).toUpperCase();
  },

  intVals(s) {
    const I = HOISTRA_INT;
    if (!I) return { isInteg: false, navAdmin: [] };
    const SHORT = { fin: "Finance", orc: "Oracle", ms: "Microsoft", sap: "SAP", cmms: "CMMS · CAFM", iwms: "IWMS", asset: "Asset · BMS", news: "News", custom: "Custom" };
    const extra = s.intExtra || [];
    const conn = I.CONNECTED.concat(extra);
    const connNames = conn.map((c) => c.name);
    const q = (s.intQ || "").trim().toLowerCase();
    const open = s.intOpen || [];

    const tableSet = {};
    conn.forEach((c) => { (c.enrich || []).concat(c.create || []).forEach((t) => { tableSet[t[0]] = 1; }); });
    const tableCount = Object.keys(tableSet).length;
    const attention = conn.filter((c) => c.st !== "ok");
    const catalogueCount = I.CATALOGUE.reduce((n, g) => n + g.items.length, 0);

    const hit = (c) => !q || (c.name + " " + c.cat + " " + c.kind + " " + (c.enrich || []).concat(c.create || []).map((t) => t[0]).join(" ")).toLowerCase().indexOf(q) > -1;
    const shown = conn.filter(hit);

    const rows = shown.map((c) => {
      const st = I.ST[c.st] || I.ST.ok;
      const isOpen = open.indexOf(c.id) > -1;
      const fed = (c.enrich || []).concat(c.create || []).map((t) => t[0]);
      const acts = (c.st === "risk"
        ? [["Re-authorise", 1], ["Resync since last good row", 0], ["Open in the graph", 0], ["Disconnect", 0]]
        : [["Resync now", 0], ["Field mapping", 0], ["Open in the graph", 0], ["Disconnect", 0]]
      ).map((a) => ({
        label: a[0],
        edge: a[1] ? "var(--color-accent)" : "var(--color-divider)",
        fg: a[1] ? "var(--color-accent)" : "var(--color-neutral-400)",
        click: () => this.orch(a[0] + " — " + c.name, "Integrations")
      }));
      return {
        id: c.id, name: c.name, mark: this.intMark(c.name),
        cat: c.cat.toLowerCase().indexOf(c.kind.toLowerCase()) > -1 ? c.cat : c.cat + " · " + c.kind,
        stLabel: st.label, stBg: st.bg, stFg: st.fg,
        last: c.last, lastFg: c.st === "risk" ? "var(--st-risk)" : "var(--color-neutral-400)",
        vol: c.vol, volFg: c.vol === "0" ? "var(--st-risk)" : "var(--color-text)",
        tablesLine: fed.join(" · "), by: c.by, since: c.since, mode: c.mode, auth: c.auth, note: c.note,
        enrich: (c.enrich || []).map((t) => ({ tbl: t[0], n: t[1] })),
        create: (c.create || []).map((t) => ({ tbl: t[0], n: t[1] })),
        unlocks: (c.unlocks || []).map((u) => ({ label: u, click: () => this.flash(u + " — reads " + fed.slice(0, 2).join(" and ") + " from " + c.name + ".") })),
        acts: acts,
        caret: isOpen ? "ph-caret-down" : "ph-caret-right",
        openShow: isOpen ? "flex" : "none",
        bg: isOpen ? "var(--color-neutral-900)" : "transparent",
        toggle: () => this.setState((p) => ({ intOpen: (p.intOpen || []).indexOf(c.id) > -1 ? (p.intOpen || []).filter((x) => x !== c.id) : (p.intOpen || []).concat([c.id]) }))
      };
    });

    const catHit = (i) => !q || (i.name + " " + i.fam + " " + i.gives + " " + i.tables.join(" ")).toLowerCase().indexOf(q) > -1;
    const cats = I.CATALOGUE
      .filter((g) => !s.intCat || s.intCat === g.id)
      .map((g) => {
        const items = g.items.filter(catHit).map((i) => {
          const isConn = i.st === "connected" || connNames.indexOf(i.name) > -1;
          const inReview = !isConn && (i.st === "review" || extra.some((e) => e.name === i.name));
          return {
            name: i.name, gives: i.gives,
            fam: i.name.split(" ")[0].toLowerCase() === i.fam.toLowerCase() ? "" : i.fam,
            mark: this.intMark(i.name),
            markBg: isConn ? "var(--color-accent-900)" : "var(--color-neutral-900)",
            markFg: isConn ? "var(--color-accent)" : "var(--color-neutral-400)",
            tables: i.tables.map((t) => ({ label: t })),
            btn: isConn ? "Connected" : inReview ? "In review" : "Connect",
            btnEdge: isConn ? "var(--color-accent)" : "var(--color-divider)",
            btnBg: isConn ? "var(--color-accent-900)" : "transparent",
            btnFg: isConn ? "var(--color-accent)" : "var(--color-neutral-300)",
            cursor: isConn ? "default" : "pointer",
            click: isConn
              ? () => this.setState({ intTab: 0, intQ: "", intOpen: [(conn.find((c) => c.name === i.name) || {}).id].filter(Boolean) })
              : () => this.setState({ intModal: { id: g.id + "-" + i.name, name: i.name, fam: i.fam, gives: i.gives, cat: g.label, tables: i.tables }, intName: "", intUrl: "" })
          };
        });
        return { id: g.id, label: g.label, icon: g.icon, blurb: g.blurb, n: items.length + " sources", items: items, keep: items.length > 0 };
      })
      .filter((g) => g.keep);

    const m = s.intModal;

    return {
      isInteg: s.signedIn && s.view === "integ",
      navAdmin: s.role !== "admin" ? [] : [{
        label: "Integrations", icon: "ph-plugs-connected", badge: "15 min",
        color: s.view === "integ" ? "var(--color-accent)" : "var(--color-neutral-300)",
        chip: s.view === "integ" ? "var(--color-accent-900)" : "transparent",
        click: () => { window.scrollTo(0, 0); this.setState({ view: "integ", role: "admin", navOpen: true, detail: null }); }
      }, {
        label: "Users & access", icon: "ph-users-three", badge: String(s.users.length),
        color: s.view === "users" ? "var(--color-accent)" : "var(--color-neutral-300)",
        chip: s.view === "users" ? "var(--color-accent-900)" : "transparent",
        click: () => { window.scrollTo(0, 0); this.setState({ view: "users", role: "admin", navOpen: true, detail: null }); }
      }, {
        label: "Audit trail", icon: "ph-scroll", badge: String(s.audit.length),
        color: s.view === "audit" ? "var(--color-accent)" : "var(--color-neutral-300)",
        chip: s.view === "audit" ? "var(--color-accent-900)" : "transparent",
        click: () => { window.scrollTo(0, 0); this.setState({ view: "audit", role: "admin", navOpen: true, detail: null }); }
      }],

      intTiles: [
        { value: conn.length, label: "Connected", hint: "sources landing rows", color: "var(--color-accent)", click: () => this.setState({ intTab: 0, intQ: "" }) },
        { value: tableCount, label: "Tables fed", hint: "in the Hoist Graph", color: "var(--color-neutral-300)", click: () => this.setState({ intTab: 0, intOpen: conn.map((c) => c.id) }) },
        { value: attention.length, label: "Need attention", hint: attention.map((a) => a.name.replace(/^(IBM|SAP|Oracle|Microsoft|Custom API —)\s*/, "")).join(", ") || "all healthy", color: attention.length ? "var(--st-risk)" : "var(--st-ok)", click: () => this.setState({ intTab: 0, intQ: "", intOpen: attention.map((a) => a.id) }) },
        { value: catalogueCount, label: "Available", hint: "connectors in the catalogue", color: "var(--color-neutral-300)", click: () => this.setState({ intTab: 1, intQ: "" }) }
      ],

      intTabs: [["Connected", conn.length], ["Available sources", catalogueCount], ["Custom API", I.ENDPOINTS.length]].map((t, i) => ({
        label: t[0], n: t[1], pick: () => this.setState({ intTab: i, intQ: "" }),
        edge: s.intTab === i ? "var(--color-accent)" : "transparent",
        fg: s.intTab === i ? "var(--color-accent)" : "var(--color-neutral-400)"
      })),
      intTabConnected: s.intTab === 0, intTabAvailable: s.intTab === 1, intTabApi: s.intTab === 2,

      intQ: s.intQ || "",
      intSetQ: (e) => this.setState({ intQ: e.target.value }),
      intRows: rows,
      intConnSummary: shown.length + " of " + conn.length + " shown · " + tableCount + " tables fed",
      intExpandLabel: open.length >= shown.length && shown.length ? "Collapse all" : "Expand all",
      intExpandAll: () => this.setState((p) => ({ intOpen: (p.intOpen || []).length >= shown.length && shown.length ? [] : shown.map((c) => c.id) })),

      intCats: cats,
      intNoneShow: cats.length ? "none" : "block",
      intCatChips: [{ id: null, label: "All" }].concat(I.CATALOGUE.map((g) => ({ id: g.id, label: SHORT[g.id] || g.label }))).map((c) => ({
        label: c.label,
        pick: () => this.setState({ intCat: c.id }),
        edge: (s.intCat || null) === c.id ? "var(--color-accent)" : "var(--color-divider)",
        bg: (s.intCat || null) === c.id ? "var(--color-accent-900)" : "transparent",
        fg: (s.intCat || null) === c.id ? "var(--color-accent)" : "var(--color-neutral-400)"
      })),

      intEndpoints: I.ENDPOINTS.map((e) => ({
        m: e.m, p: e.p, d: e.d,
        mBg: e.m === "POST" ? "var(--color-accent-900)" : "var(--color-neutral-900)",
        mFg: e.m === "POST" ? "var(--color-accent)" : "var(--color-neutral-400)"
      })),
      intMapping: I.MAPPING.map((r) => ({ src: r.src, type: r.type, target: r.tbl + "." + r.col, note: r.note })),
      intKey: s.intKeyShown ? "hst_live_7f3a91c4d0e85b2f6a1c9d47" : "hst_live_••••••••••••••••••1c9d47",
      intKeyLabel: s.intKeyShown ? "Hide" : "Reveal",
      intToggleKey: () => this.setState((p) => ({ intKeyShown: !p.intKeyShown })),
      intRotate: () => this.orch("Rotate the ingest token", "Integrations"),
      intDocs: () => this.flash("Ingest guide: declare a natural key per table, send rows in any order, and every row keeps the source and timestamp it arrived with."),
      intOnPrem: () => this.flash("On-prem and VPC deployments run the same ingest API inside your network; only the graph metadata leaves it."),
      intBrowse: () => this.setState({ intTab: 1, intQ: "", intCat: null }),
      intSyncAll: () => this.orch("Sync every connected source", "Integrations"),

      intModalOn: !!m,
      intModal: m ? {
        name: m.name, fam: m.fam, gives: m.gives,
        namePh: m.name + " — " + (m.cat.indexOf("ERP") === 0 ? "production" : "portfolio"),
        urlLabel: m.id.indexOf("custom") === 0 ? "Endpoint" : "Instance or tenant URL",
        urlPh: m.id.indexOf("custom") === 0 ? "e.g. https://data.yourcompany.com/hoistra" : "e.g. https://<tenant>.example.com/api"
      } : { name: "", fam: "", gives: "", namePh: "", urlLabel: "", urlPh: "" },
      intModalTables: m ? m.tables.map((t) => ({ label: t })) : [],
      intName: s.intName || "", intSetName: (e) => this.setState({ intName: e.target.value }),
      intUrl: s.intUrl || "", intSetUrl: (e) => this.setState({ intUrl: e.target.value }),
      intCancel: () => this.setState({ intModal: null }),
      intConfirm: () => {
        if (!m) return;
        const rec = {
          id: "x-" + m.name.toLowerCase().replace(/[^a-z0-9]+/g, "-"),
          name: s.intName && s.intName.trim() ? s.intName.trim() : m.name,
          cat: m.cat, kind: "Pending", st: "review", last: "not yet", vol: "0",
          mode: "Schema read · mapping in review", by: "You", since: "today",
          auth: s.intUrl && s.intUrl.trim() ? s.intUrl.trim() : "credentials pending",
          enrich: [], create: m.tables.filter((t) => t !== "any table").map((t) => [t, "0"]),
          unlocks: ["Held until the field mapping is approved"],
          note: "The orchestrator has read the source schema and proposed a mapping. Nothing writes to the graph until you approve it in the decision queue."
        };
        this.setState((p) => ({ intExtra: (p.intExtra || []).concat([rec]), intModal: null, intTab: 0, intQ: "", intOpen: [rec.id] }));
        this.orch("Connect " + rec.name, "Integrations");
      }
    };
  }
};
