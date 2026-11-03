// integrations — integrations admin view model.
// Methods are mixed into HoistraLogic.prototype; `this` is the controller.
import { t } from './constants.js';

// The source types the connector service implements — one per plugin in
// cafm-connector-service/src/cafm_connector/connectors/plugins, described by that plugin's own
// docstring. test/integrations.test.mjs reads the plugins folder, so this list cannot drift
// from what the service can actually read. Nothing else on this page is listed: it does not
// read the connector service's saved connectors, so it names none.
export const SOURCE_TYPES = [
  { id: 'csv', name: 'CSV', group: 'files', reads: 'CSV files, parsed as a stream rather than loaded whole' },
  { id: 'excel', name: 'Excel', group: 'files', reads: 'Excel workbooks, every sheet' },
  { id: 'json', name: 'JSON', group: 'files', reads: 'JSON from a file, a blob URL or an HTTP endpoint' },
  { id: 'xml', name: 'XML', group: 'files', reads: 'XML files, parsed as a stream' },
  { id: 'parquet', name: 'Parquet', group: 'files', reads: 'Parquet files, read as a stream rather than loaded whole' },
  { id: 'postgresql', name: 'PostgreSQL', group: 'databases', reads: 'PostgreSQL tables' },
  { id: 'mysql', name: 'MySQL', group: 'databases', reads: 'MySQL tables' },
  { id: 'mssql', name: 'SQL Server', group: 'databases', reads: 'Microsoft SQL Server tables' },
  { id: 'mongodb', name: 'MongoDB', group: 'databases', reads: 'MongoDB collections, with change streams for new and changed documents' },
  { id: 'odata', name: 'OData', group: 'apis', reads: 'OData v2 and v4 services, filtered and paged' },
  { id: 'rest', name: 'REST API', group: 'apis', reads: 'REST endpoints, with Bearer, API key or Basic authentication' },
  { id: 'soap', name: 'SOAP', group: 'apis', reads: 'SOAP services, from their WSDL' }
];
const GROUPS = [
  { id: 'files', label: 'Files', icon: 'ph-file-text', blurb: 'Read from an uploaded file or from a URL.',
    urlLabel: 'File location or URL', urlPh: 'e.g. https://files.yourcompany.com/export.csv' },
  { id: 'databases', label: 'Databases', icon: 'ph-database', blurb: 'Read from a database the connector service can reach.',
    urlLabel: 'Host or connection string', urlPh: 'e.g. db.yourcompany.com:5432/cafm' },
  { id: 'apis', label: 'APIs', icon: 'ph-plugs', blurb: 'Read from a web service.',
    urlLabel: 'Endpoint', urlPh: 'e.g. https://api.yourcompany.com/v1' }
];

export const integrationsMethods = {

  intMark(name) {
    const w = name.replace(/[^A-Za-z0-9 ]/g, " ").split(/\s+/).filter(Boolean);
    return ((w[0] || "?")[0] + (w[1] ? w[1][0] : (w[0] || "?")[1] || "")).toUpperCase();
  },

  intVals(s) {
    // Connections are not read from anywhere yet, so there are none to show: every count that
    // depends on them is 0 and every time is "—", never a stand-in.
    const conn = [];
    const q = (s.intQ || "").trim().toLowerCase();
    const hit = (t) => !q || (t.name + " " + t.reads).toLowerCase().indexOf(q) > -1;
    const cats = GROUPS
      .filter((g) => !s.intCat || s.intCat === g.id)
      .map((g) => {
        const items = SOURCE_TYPES.filter((t) => t.group === g.id && hit(t)).map((t) => ({
          name: t.name, fam: "", gives: t.reads, mark: this.intMark(t.name), tables: [],
          markBg: "var(--color-neutral-900)", markFg: "var(--color-neutral-400)",
          btn: "Connect", btnEdge: "var(--color-divider)", btnBg: "transparent", btnFg: "var(--color-neutral-300)", cursor: "pointer",
          click: () => this.setState({ intModal: { id: t.id, name: t.name, fam: g.label, gives: t.reads, cat: g.label,
            urlLabel: g.urlLabel, urlPh: g.urlPh, tables: [] }, intName: "", intUrl: "" })
        }));
        return { id: g.id, label: g.label, icon: g.icon, blurb: g.blurb, n: items.length + (items.length === 1 ? " type" : " types"), items: items, keep: items.length > 0 };
      })
      .filter((g) => g.keep);
    const m = s.intModal;

    return {
      isInteg: s.signedIn && s.view === "integ",
      navAdmin: s.role !== "admin" ? [] : [{
        label: "Integrations", icon: "ph-plugs-connected", badge: String(conn.length),
        color: s.view === "integ" ? "var(--color-accent)" : "var(--color-neutral-300)",
        chip: s.view === "integ" ? "var(--color-accent-900)" : "transparent",
        click: () => { window.scrollTo(0, 0); this.setState({ view: "integ", role: "admin", navOpen: true, detail: null }); }
      }, {
        // "0" is a finding nobody has established while the read is still out; the count
        // appears once a read has answered (or failed and filled the labelled samples).
        label: "Users & access", icon: "ph-users-three", badge: s.usLiveLoadedAt || s.usLiveError ? String(s.users.length) : "…",
        color: s.view === "users" ? "var(--color-accent)" : "var(--color-neutral-300)",
        chip: s.view === "users" ? "var(--color-accent-900)" : "transparent",
        click: () => { window.scrollTo(0, 0); this.setState({ view: "users", role: "admin", navOpen: true, detail: null }); }
      }, {
        // Scheduled engine jobs and questions (logic/cronsPage.js). The badge is the company's
        // job count once the list has been read, "…" before.
        label: "Hoist Crons", icon: "ph-clock-clockwise", badge: s.cronLoadedAt || s.cronLoadErr ? String((s.cronJobs || []).length) : "…",
        color: s.view === "crons" ? "var(--color-accent)" : "var(--color-neutral-300)",
        chip: s.view === "crons" ? "var(--color-accent-900)" : "transparent",
        click: () => this.cpOpen()
      }, {
        // What the chat has learned for this company (logic/memoriesPage.js). The badge is the
        // count once the page has read it, "…" before.
        label: "Chat memory", icon: "ph-brain", badge: s.mpLoadedAt || s.mpErr ? String((s.mpRows || []).length) : "…",
        color: s.view === "memories" ? "var(--color-accent)" : "var(--color-neutral-300)",
        chip: s.view === "memories" ? "var(--color-accent-900)" : "transparent",
        click: () => this.mpOpen()
      }, {
        // Every turn as a span tree with cost (logic/tracesPage.js). The badge is the turns in
        // the range once read, "…" before.
        label: "Hoist Traces", icon: "ph-flow-arrow", badge: s.tpLoadedAt || s.tpErr ? String((s.tpTurns || []).length) : "…",
        color: s.view === "traces" ? "var(--color-accent)" : "var(--color-neutral-300)",
        chip: s.view === "traces" ? "var(--color-accent-900)" : "transparent",
        click: () => this.tpOpen()
      }, {
        // Agent instructions measured on replayed questions (logic/skillLabPage.js). The badge is
        // the rewrites waiting for approval once read.
        label: "Skill lab", icon: "ph-flask", badge: s.slLoadedAt || s.slErr ? String((s.slProposals || []).filter((p) => p.status === "proposed").length) : "…",
        color: s.view === "skilllab" ? "var(--color-accent)" : "var(--color-neutral-300)",
        chip: s.view === "skilllab" ? "var(--color-accent-900)" : "transparent",
        click: () => this.slOpen()
      }, {
        label: "Audit trail", icon: "ph-scroll", badge: s.auLiveLoadedAt || s.auLiveError ? String(s.audit.length) : "…",
        color: s.view === "audit" ? "var(--color-accent)" : "var(--color-neutral-300)",
        chip: s.view === "audit" ? "var(--color-accent-900)" : "transparent",
        click: () => {
          window.scrollTo(0, 0);
          this.setState({ view: "audit", role: "admin", navOpen: true, detail: null });
          // Coming back to the page is a visit to the tab it is on (pre-push review, 29 Sep 2026).
          if (this.state.auTab === "vendors") this.vaLoad();
        }
      }],

      intTiles: [
        { value: conn.length, label: "Connected", hint: "sources landing rows", color: "var(--color-accent)", click: () => this.setState({ intTab: 0, intQ: "" }) },
        { value: 0, label: "Tables fed", hint: "in the Hoist Graph", color: "var(--color-neutral-300)", click: () => this.setState({ intTab: 0, intQ: "" }) },
        { value: 0, label: "Need attention", hint: "none", color: "var(--color-neutral-300)", click: () => this.setState({ intTab: 0, intQ: "" }) },
        { value: SOURCE_TYPES.length, label: "Available", hint: "source types the connector service reads", color: "var(--color-neutral-300)", click: () => this.setState({ intTab: 1, intQ: "" }) }
      ],
      intLastSync: "—",

      intTabs: [["Connected", conn.length], ["Available sources", SOURCE_TYPES.length], ["Custom API", 0]].map((t, i) => ({
        label: t[0], n: t[1], pick: () => this.setState({ intTab: i, intQ: "" }),
        edge: (s.intTab || 0) === i ? "var(--color-accent)" : "transparent",
        fg: (s.intTab || 0) === i ? "var(--color-accent)" : "var(--color-neutral-400)"
      })),
      intTabConnected: (s.intTab || 0) === 0, intTabAvailable: s.intTab === 1, intTabApi: s.intTab === 2,

      intQ: s.intQ || "",
      intSetQ: (e) => this.setState({ intQ: e.target.value }),
      intRows: [],
      intConnSummary: "0 of 0 shown · 0 tables fed",
      intConnEmpty: "No sources connected.",
      intExpandLabel: "Expand all",
      intExpandAll: () => {},

      intCats: cats,
      intNoneShow: cats.length ? "none" : "block",
      intCatChips: [{ id: null, label: "All" }].concat(GROUPS).map((c) => ({
        label: c.label,
        pick: () => this.setState({ intCat: c.id }),
        edge: (s.intCat || null) === c.id ? "var(--color-accent)" : "var(--color-divider)",
        bg: (s.intCat || null) === c.id ? "var(--color-accent-900)" : "transparent",
        fg: (s.intCat || null) === c.id ? "var(--color-accent)" : "var(--color-neutral-400)"
      })),

      // The Custom API tab keeps its panels; no token, endpoint or mapping exists to fill them.
      intBaseUrl: "—",
      intKey: "—",
      intKeyNote: "No ingest token has been issued.",
      intKeyLabel: s.intKeyShown ? "Hide" : "Reveal",
      intToggleKey: () => this.setState((p) => ({ intKeyShown: !p.intKeyShown })),
      intLimits: [
        { value: "—", label: "rows per minute" },
        { value: "—", label: "per document push" },
        { value: "—", label: "ingest availability" }
      ],
      intEndpoints: [],
      intEndpointsEmpty: "No endpoints.",
      intMapping: [],
      intMappingEmpty: "No field mappings.",
      intRotate: () => this.flash("No ingest token has been issued, so there is none to rotate."),
      intDocs: () => this.flash("Ingest guide: declare a natural key per table, send rows in any order, and every row keeps the source and timestamp it arrived with."),
      intOnPrem: () => this.flash("On-prem and VPC deployments run the same ingest API inside your network; only the graph metadata leaves it."),
      intBrowse: () => this.setState({ intTab: 1, intQ: "", intCat: null }),
      intSyncAll: () => this.flash("No sources are connected, so there is nothing to sync."),

      // Connecting is asked of the orchestrator. Nothing is added here: a connection is listed
      // when one exists, never as a stand-in while it is being set up.
      intModalOn: !!m,
      intModal: m ? {
        name: m.name, fam: m.fam, gives: m.gives, namePh: m.name + " — production",
        urlLabel: m.urlLabel, urlPh: m.urlPh
      } : { name: "", fam: "", gives: "", namePh: "", urlLabel: "", urlPh: "" },
      intModalTables: m ? (m.tables || []).map((t) => ({ label: t })) : [],
      intName: s.intName || "", intSetName: (e) => this.setState({ intName: e.target.value }),
      intUrl: s.intUrl || "", intSetUrl: (e) => this.setState({ intUrl: e.target.value }),
      intCancel: () => this.setState({ intModal: null }),
      intConfirm: () => {
        if (!m) return;
        const name = s.intName && s.intName.trim() ? s.intName.trim() : m.name;
        const where = s.intUrl && s.intUrl.trim() ? " at " + s.intUrl.trim() : "";
        this.setState({ intModal: null, intName: "", intUrl: "" });
        this.orch("Connect a " + m.name + " source named " + name + where, "Integrations");
      }
    };
  }
};
