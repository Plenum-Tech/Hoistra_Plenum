import { energyApi } from '../api/energy.js';

// buildingsGraph — the drawer that opens under a building row.
//
// It draws the canonical graph as a tree:
//
//     building
//       ├── floors ── spaces
//       ├── assets ── equipment / meters
//       ├── documents ── certificates
//       └── contracts ── work orders / invoices
//
// It shows HOW MANY of each, not which. Everything here comes off the row the table already
// loaded — `graph_counts`, `spaces`, `partial_counts`, `buildings_on_site` — so opening a
// building draws immediately from them. The child ROWS come from
// GET /buildings/{id}/graph, fetched on open and cached per building, so the tree never sits
// blank while it waits and never asserts a branch is empty before it has asked.
//
// A zero is not a blank. "No meter on record" is a fact about the building and the reason
// its EUI is a recorded figure rather than a reading, so every empty branch says what its
// emptiness means. A branch rendered as an empty space loses that, and the reader assumes
// the screen is unfinished rather than the record.

// What a branch says when the API returned no figure for it either way.
const NOT_COUNTED =
  "Not counted on this row — the rollup does not report this branch, so this is unknown "
  + "rather than none.";

// The tree, in the order the graph itself is written. `of` names the key on graph_counts;
// `own` reads a field that sits directly on the row instead.
const BRANCHES = [
  {
    key: "floors", label: "Floors",
    empty: "No floors on record — the floor count is surveyed, not counted.",
    children: [
      { key: "spaces", label: "Spaces", own: "spaces",
        empty: "No spaces recorded, so floor area cannot be counted from them." }
    ]
  },
  {
    key: "assets", label: "Assets",
    empty: "No plant recorded here. Work orders raised against this building will have no asset to inherit a location from.",
    children: [
      { key: "equipment", label: "Equipment", empty: "No equipment rows." },
      { key: "meters", label: "Meters",
        empty: "No meter on record — any EUI shown is a recorded figure, not a reading." }
    ]
  },
  {
    key: "documents", label: "Documents",
    empty: "Nothing filed against this building yet.",
    children: [
      { key: "certificates", label: "Certificates",
        empty: "No certificates filed — this building contributes nothing to compliance coverage." }
    ]
  },
  {
    key: "contracts", label: "Contracts",
    empty: "No contract covers this building.",
    children: [
      { key: "work_orders", label: "Work orders", empty: "No work orders raised here." },
      { key: "invoices", label: "Invoices", empty: "Nothing billed against this building." }
    ]
  }
];

// Three states, not two. A branch the API reports as 0 is empty; a branch it does not
// report AT ALL was never counted, and the two must not read alike — saying "nothing billed
// against this building" for a branch nobody counted is a false statement about the record,
// and it is exactly the kind that gets believed.
const readBranch = (b, node) => {
  const counts = b.counts || {};
  const raw = node.own ? b[node.own] : counts[node.key];
  if (typeof raw === "number") return { n: raw, counted: true };
  return { n: 0, counted: false };
};

export const buildingsGraphMethods = {

  // Opening a building fetches its children. The counts on the row draw the tree
  // immediately; the rows fill in when they arrive, so the drawer is never blank while
  // waiting and never claims a branch is empty before it has been asked.
  bgToggle(id) {
    const opening = this.state.bgOpen !== id;
    this.setState((p) => ({ bgOpen: p.bgOpen === id ? null : id }));
    if (opening) this.bgLoad(id);
  },

  async bgLoad(id) {
    const cache = this.state.bgTree || {};
    if (cache[id] && !cache[id].error) return;
    this.setState((p) => ({ bgTree: Object.assign({}, p.bgTree, { [id]: { loading: true } }) }));
    try {
      const res = await energyApi.buildingGraph(id);
      this.setState((p) => ({ bgTree: Object.assign({}, p.bgTree, { [id]: res }) }));
    } catch (e) {
      this.setState((p) => ({
        bgTree: Object.assign({}, p.bgTree, {
          [id]: { error: (e && e.message) || String(e) }
        })
      }));
    }
  },

  // One building's branches, each with its count or the reason it has none.
  bgBranches(b) {
    return BRANCHES.map((br) => {
      const shape = (node) => {
        const { n, counted } = readBranch(b, node);
        const known = counted && n > 0;
        return {
          key: node.key,
          label: node.label,
          count: counted ? n : 0,
          // "?" for a branch nobody counted — an em-dash would read as "none".
          countText: known ? String(n) : (counted ? "0" : "?"),
          has: known,
          counted: counted,
          tone: known ? "var(--color-neutral-300)" : "var(--color-neutral-500)",
          note: known ? "" : (counted ? node.empty : NOT_COUNTED),
          noteShow: known ? "none" : "block"
        };
      };
      const top = shape(br);
      top.children = (br.children || []).map(shape);
      return top;
    });
  },

  // The API returns branches nested; flatten to a lookup so the tree built from counts can
  // pick up its rows without depending on the two orders matching.
  bgRowsFor(id) {
    const t = (this.state.bgTree || {})[id];
    if (!t || t.loading || t.error || !t.branches) return null;
    const byTable = {};
    t.branches.forEach((br) => {
      byTable[br.table] = br;
      (br.children || []).forEach((c) => { byTable[c.table] = c; });
    });
    // The API calls it compliance_certificates; the tree node is `certificates`.
    if (byTable.compliance_certificates) byTable.certificates = byTable.compliance_certificates;
    return byTable;
  },

  bgVals() {
    const open = this.state.bgOpen || null;
    return {
      bgOpenId: open,
      bgToggle: (id) => this.bgToggle(id),
      bgIsOpen: (id) => open === id,
      // Built per row by the screen, so a closed drawer costs nothing to render.
      bgFor: (b) => {
        const fetched = this.bgRowsFor(b.id);
        const state = (this.state.bgTree || {})[b.id] || {};
        const branches = this.bgBranches(b).map((br) => {
          const attach = (node) => {
            const f = fetched && fetched[node.key];
            if (!f) return node;
            // The fetched branch is the authority once it arrives: it knows the difference
            // between a branch that is empty and one this database cannot join, which the
            // counts on the row cannot express.
            return Object.assign({}, node, {
              count: f.count,
              countText: f.available ? String(f.count) : "?",
              counted: f.available,
              has: f.count > 0,
              tone: f.count > 0 ? "var(--color-neutral-300)" : "var(--color-neutral-500)",
              note: f.count > 0 ? "" : (f.empty_reason || node.note),
              noteShow: f.count > 0 ? "none" : "block",
              rows: (f.rows || []).map((r) => ({
                id: r.id, label: r.label, detail: r.detail || ""
              })),
              more: f.truncated ? (f.count - (f.rows || []).length) + " more not shown" : "",
              moreShow: f.truncated ? "block" : "none"
            });
          };
          const top = attach(br);
          top.children = (br.children || []).map(attach);
          return top;
        });
        const total = branches.reduce(
          (t, br) => t + br.count + br.children.reduce((u, c) => u + c.count, 0), 0
        );
        const uncounted = branches
          .flatMap((br) => [br].concat(br.children))
          .filter((x) => !x.counted)
          .map((x) => x.label.toLowerCase());
        const notes = [];
        // The three things about a building that are worth saying above the tree, because
        // each one explains a figure in the row above it.
        if ((b.partial || []).length) {
          notes.push({
            tone: "var(--st-warn)",
            text: b.partial[0] + " — the survey stands; the count sits beside it."
          });
        }
        if ((b.counts || {}).meters === 0) {
          if (b.euiN !== null && b.euiN !== undefined) {
            notes.push({
              tone: "var(--st-warn)",
              text: "The EUI here was recorded on the row, not metered — there is no meter on the graph."
            });
          }
        }
        if (b.buildingsOnSite > 1) {
          notes.push({
            tone: "var(--color-neutral-500)",
            text: "This site holds " + b.buildingsOnSite + " buildings, so its energy reading is "
              + "attributed to none of them — splitting one meter across two buildings would "
              + "invent a division the data does not contain."
          });
        }
        if (b.missing && b.missing.length) {
          notes.push({
            tone: "var(--color-neutral-500)",
            text: "Not yet on the record: " + b.missing.join(", ") + "."
          });
        }
        return {
          branches: branches,
          loading: !!state.loading,
          loadingShow: state.loading ? "block" : "none",
          error: state.error || "",
          errorShow: state.error ? "block" : "none",
          total: total,
          totalText: (total ? total + (total === 1 ? " record" : " records") + " counted"
            : "Nothing counted on the graph yet")
            + (uncounted.length ? " · " + uncounted.join(", ") + " not counted" : ""),
          notes: notes,
          emptyShow: total ? "none" : "block",
          // Said plainly rather than implied, so nobody reads a count as a list.
          scopeNote: state.loading ? "Reading the graph…"
            : fetched ? "Rows read from the graph"
            : "Counts from the table — rows loading"
        };
      }
    };
  }
};
