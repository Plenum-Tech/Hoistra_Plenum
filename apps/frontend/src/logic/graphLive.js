import { energyApi } from '../api/energy.js';
import { HUBS, SHARED_N } from './constants.js';

// graphLive — real numbers for the four panels that used to compute their own.
//
// The Hoist Graph diagram, the hierarchy tree, the export table and the documents list all
// drew from two things compiled into the bundle: a seed array of nine buildings, and a
// formula that invented each building's child counts from its floor count and area. The
// layout of those panels is hand-tuned and stays exactly as it was. What they read changes:
//
//   the portfolio        GET /api/energy/buildings          (already loaded for the table)
//   per-building counts  graph_counts on each row           (counted, per FK, per building)
//   per-table counts     GET /api/energy/graph/tables       (counted, plus history)
//   a building's rows    GET /api/energy/buildings/{id}/graph
//
// Three states, never two. A figure the graph counted is a number. A figure it counted as
// none is a zero. A figure nobody counted — a table absent from this deployment, a rollup
// that does not report that branch — is "?" and says why. The formula could not tell those
// apart, which is how a building with no meters and a building whose meters nobody has
// counted both came out as "4 meters".
//
// Two things the old panels showed have no source at all and are therefore gone rather than
// re-derived: the vector-similarity badges (no service reachable from here holds them) and
// document file sizes (plenum_cafm.documents records no size). Both are reported as absent
// where they used to be shown, so the space reads as unavailable rather than empty.

// Diagram node id → the table it actually is.
export const NODE_TABLE = {
  portfolio: "portfolios", site: "sites", location: "locations", pack: "regulation_packs",
  building: "buildings", floor: "floors", space: "spaces", asset: "assets",
  equipment: "equipment", meter: "meters", document: "documents",
  certificate: "compliance_certificates", vendor: "vendors", contract: "contracts",
  invoice: "invoices", workorder: "work_orders"
};

// Diagram node id → the key it is counted under on a building's graph_counts.
// `space` is absent on purpose: spaces sit directly on the row, not in counts.
const NODE_COUNT = {
  floor: "floors", asset: "assets", document: "documents", contract: "contracts",
  equipment: "equipment", meter: "meters", workorder: "work_orders",
  certificate: "certificates", invoice: "invoices"
};

const NUM = (n) => String(n).replace(/\B(?=(\d{3})+(?!\d))/g, ",");

export const graphLiveMethods = {

  // ── the portfolio ──────────────────────────────────────────────────────────

  // Live buildings, in the order the register returned them.
  glBuildings() { return this.bldData(); },

  // The building the graph panels are focused on. `gBuilding` holds a NAME, which was safe
  // when the names were compiled in and is not now: a saved selection can name a building
  // this deployment does not have. Falling back to the first live one keeps the panel
  // populated instead of blank.
  glSelected() {
    const rows = this.glBuildings();
    if (!rows.length) return null;
    return rows.find((b) => b.name === this.state.gBuilding) || rows[0];
  },

  // ── counts ─────────────────────────────────────────────────────────────────

  // One branch of one building: how many, and whether anybody counted.
  glCount(b, nodeId) {
    if (!b) return { n: 0, counted: false, why: "No building selected." };
    if (nodeId === "space") {
      const n = b.spaces;
      return typeof n === "number"
        ? { n: n, counted: true, why: "" }
        : { n: 0, counted: false, why: "Spaces are not counted on this row." };
    }
    if (nodeId === "floor" && typeof b.floors === "number" && b.floorsSource !== "floors_table") {
      // The floor count on the row is a survey, not a count of floor rows. Saying so keeps
      // it out of the same sentence as the counted branches.
      const c = (b.counts || {}).floors;
      if (typeof c !== "number") {
        return { n: b.floors, counted: true, surveyed: true,
          why: "Recorded on the building row — no floor rows on record." };
      }
    }
    const key = NODE_COUNT[nodeId];
    if (!key) return { n: 0, counted: false, why: "Not a branch of a building." };
    const n = (b.counts || {})[key];
    if (typeof n === "number") return { n: n, counted: true, why: "" };
    // A branch is reported per building only where it has rows, so a missing key is
    // ambiguous on its own: it means none, or it means the rollup never counted this
    // branch at all. The portfolio settles it. If ANY building carries a figure for this
    // branch the query ran and produced keys, so a building absent from it has none. If no
    // building carries one, nothing here can tell zero from uncounted, and it stays "?".
    return this.glBranchCounted(key)
      ? { n: 0, counted: true, why: "" }
      : { n: 0, counted: false, why: "Not counted anywhere in the portfolio — unknown rather than none." };
  },

  // Whether the rollup reports this branch for any building at all.
  glBranchCounted(key) {
    const cache = this._glBranch || (this._glBranch = {});
    const rows = this.glBuildings();
    // Recomputed when the register reloads; the row count is a cheap stamp for that.
    if (cache.n !== rows.length) { cache.n = rows.length; cache.seen = {}; cache.done = false; }
    if (!cache.done) {
      rows.forEach((b) => Object.keys(b.counts || {}).forEach((k) => { cache.seen[k] = true; }));
      cache.done = true;
    }
    return !!cache.seen[key];
  },

  // The same figure as text, for a label. "?" where nobody counted: an em-dash reads as none.
  glCountText(b, nodeId) {
    const c = this.glCount(b, nodeId);
    return c.counted ? NUM(c.n) : "?";
  },

  // ── per-table counts and history ───────────────────────────────────────────

  async glLoadTables() {
    if (this._glTablesLoading) return;
    this._glTablesLoading = true;
    try {
      const res = await energyApi.graphTables();
      if (res && res.ok) this.setState({ glTables: res });
    } catch (e) {
      // The export panel falls back to "not counted"; a stale page beats a broken one.
    } finally {
      this._glTablesLoading = false;
    }
  },

  // One table as the database holds it: rows, columns, and the counts behind it.
  glTable(nodeId) {
    const payload = this.state.glTables;
    const table = NODE_TABLE[nodeId] || nodeId;
    const found = payload && (payload.tables || []).find((t) => t.table === table);
    if (!found) {
      return {
        table: table, rows: null, columns: null, versions: [],
        why: payload
          ? "plenum_cafm." + table + " is not in this database, so it has no rows to count."
          : "Reading the graph…",
        basis: (payload && payload.history_basis) || ""
      };
    }
    return {
      table: table, rows: found.rows, columns: found.columns,
      versions: found.versions || [],
      // The engine says why a table has no history; it is not re-derived here.
      why: found.error || found.why || "",
      basis: payload.history_basis || ""
    };
  },

  // ── the diagram ────────────────────────────────────────────────────────────

  // The hand-placed hub positions, filled with real buildings. The coordinates, radii and
  // satellite angles in HUBS were tuned by eye so the clusters breathe; they are kept
  // exactly, and only what sits at each position changes. A position with no building
  // behind it is dropped rather than drawn empty.
  glHubs() {
    const rows = this.glBuildings();
    return HUBS.map((h, i) => (rows[i] ? Object.assign({}, h, {
      name: rows[i].name, b: rows[i]
    }) : null)).filter(Boolean);
  },

  // How much of the portfolio the diagram is showing. The canvas holds a fixed number of
  // hand-placed positions, so a portfolio larger than that is partly drawn — said out
  // loud, because a diagram that silently shows five of forty buildings reads as complete.
  glHubNote() {
    const n = this.glBuildings().length, drawn = this.glHubs().length;
    if (!n) return "No buildings on the register yet — nothing to draw.";
    if (n > drawn) return drawn + " of " + n + " buildings drawn — the canvas holds "
      + drawn + " hand-placed positions. Every building is in the table below.";
    return n + (n === 1 ? " building" : " buildings") + " on the register, all drawn.";
  },

  // Nodes more than one building shares. The seed named four: a regulation pack and three
  // vendors, with the buildings under each typed in. The pack is real — a building is
  // scored against the standard its location's pack names, and that standard is on every
  // row — so packs are grouped from the rows themselves. Vendors are not: nothing this
  // panel can reach says which vendor serves which building, so those nodes are gone
  // rather than kept with invented membership.
  glShared() {
    const hubs = this.glHubs();
    const by = {};
    hubs.forEach((h) => {
      const std = h.b && h.b.std;
      if (!std || std === "—") return;
      (by[std] = by[std] || []).push(h.name);
    });
    // Only a standard TWO or more drawn buildings are held to is a shared node; one
    // building on its own standard is not a tie between anything.
    return Object.keys(by).filter((std) => by[std].length > 1)
      .slice(0, SHARED_N.length)
      .map((std, i) => Object.assign({}, SHARED_N[i], {
        id: "pack", label: std, sub: "pack", links: by[std]
      }));
  }
};
