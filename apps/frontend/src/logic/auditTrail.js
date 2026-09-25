// auditTrail — Ingestion audit trail (admin). Reads the same `audit` state the
// ingestion validation agent (logic/ingestion.js) writes to and auditLive.js replaces
// in place with live rows from GET /api/admin/ingestion-audit — the filter chips, tones
// and 8-field record render either without knowing which.
//
// The page fetches ONE page of the trail (limit 100) and narrows it here, in the browser.
// So two numbers sit next to each other and must not drift: the tiles describe the FILTERED
// set, the same rows listed beneath them. A tile that went on describing the whole trail
// while the list narrowed would be quietly wrong every time a filter was on — and nothing on
// screen would say so.
//
// The status chips are the deliberate exception. Their counts come from the set narrowed by
// every OTHER filter, never by the chip you are standing on: a chip whose own count changed
// when you pressed it could never tell you what pressing a different one would give you.
// Methods are mixed into HoistraLogic.prototype; `this` is the controller.
const AU_TONE = {
  ok: ["var(--st-ok)", "var(--st-ok-bg)"],
  warn: ["var(--st-warn)", "var(--st-warn-bg)"],
  risk: ["var(--st-risk)", "var(--st-risk-bg)"],
  dormant: ["var(--st-dormant)", "var(--st-dormant-bg)"],
  accent: ["var(--color-accent)", "var(--color-accent-900)"]
};

//: How long the search box waits before asking. Every keystroke on the wire is a request
//: per character and a list that reorders itself under the cursor as answers arrive out of
//: order; the state moves at once so the box stays responsive, and only the question waits.
export const AU_QUERY_DEBOUNCE_MS = 350;

const AU_STATUSES = ["All", "Accepted", "Reassigned", "Overridden", "Rejected"];
//: How many buildings the picker draws at once. The register runs to hundreds; a popover
//: that rendered all of them would be a scroll with no end and a list nobody reads past the
//: first screen of. The type-ahead is what reaches the rest, and the footer says so.
const AU_BLD_SHOWN = 60;
//: How many days back each range reaches. 0 = today only, null = the whole trail.
const AU_RANGES = [["Today", 0], ["7 days", 7], ["14 days", 14], ["All", null]];
const WEEKDAYS = ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"];
const MONTHS = ["January", "February", "March", "April", "May", "June",
  "July", "August", "September", "October", "November", "December"];

const midnight = (d) => new Date(d.getFullYear(), d.getMonth(), d.getDate());
//: Whole days between two instants, counted by calendar date rather than elapsed hours —
//: 23:59 and 00:01 are a day apart to a reader even though they are two minutes apart.
function daysBetween(at, now) {
  const d = at ? new Date(at) : null;
  if (!d || isNaN(d)) return null;
  return Math.round((midnight(now) - midnight(d)) / 86400000);
}

//: "Accepted" covers the engine's "Approved …" wordings too — the chip is the outcome a
//: reader recognises, not the token the row happens to carry.
function matchesStatus(outcome, status) {
  if (status === "All") return true;
  const o = String(outcome || "");
  if (o.indexOf(status) === 0) return true;
  return status === "Accepted" && o.indexOf("Approved") === 0;
}
const isClean = (outcome) => matchesStatus(outcome, "Accepted");

function dayLabel(at, now) {
  const ago = daysBetween(at, now);
  if (ago === null) return "Undated";
  if (ago === 0) return "Today";
  if (ago === 1) return "Yesterday";
  const d = new Date(at);
  if (ago > 0 && ago < 7) return WEEKDAYS[d.getDay()];
  return d.getDate() + " " + MONTHS[d.getMonth()] + (d.getFullYear() === now.getFullYear() ? "" : " " + d.getFullYear());
}

export const auditMethods = {
  auditVals(s) {
    // Whether the READ happened, not whether it brought anything back. Inferring this from
    // the rows — some(a => a.live) — reads an empty trail as a failure to read one: on 17 Sep
    // 2026 production held no ingestion_audit_events at all and the page said "Sample data ·
    // 0 of 0 entries", which tells a reader the register never answered. It had.
    const live = !!s.auLiveLoadedAt && !s.auLiveError;
    const now = s.auNow ? new Date(s.auNow) : new Date();
    const rows = s.audit || [];
    // The read has not answered and nothing is held. Distinct from a live-but-empty
    // register, which is a real finding and already handled above.
    const unread = !live && !s.auLiveError && !rows.length;
    const range = s.auRange || "Today";
    const reach = (AU_RANGES.find((r) => r[0] === range) || AU_RANGES[3])[1];
    const q = String(s.auQuery || "").trim().toLowerCase();

    // On live data the register has ALREADY applied every one of these, so the client
    // pipeline stands down: a second pass with predicates that are merely similar — the
    // search reaching an email the row does not carry, a range landing the other side of
    // midnight — would hide rows the server deliberately sent while the counts beside them
    // still described the server's set. The pipeline is what makes the SEED usable.
    const keep = (a) => {
      if (reach !== null) {
        const ago = daysBetween(a.at, now);
        if (ago === null || ago < 0 || ago > reach) return false;
      }
      if (s.auBuilding && a.building !== s.auBuilding) return false;
      if (s.auPerson) { if (a.who !== s.auPerson) return false; }
      else if (s.auPeopleScope === "admins" && a.role !== "Admin") return false;
      else if (s.auPeopleScope === "users" && a.role === "Admin") return false;
      if (q && [a.doc, a.who, a.building].join(" ").toLowerCase().indexOf(q) < 0) return false;
      return true;
    };
    const scoped = live ? rows : rows.filter(keep);
    const shown = live ? rows : scoped.filter((a) => matchesStatus(a.outcome, s.auFilter || "All"));
    // The set the roster is counted over when there is no server roster to use.
    const peopleScope = live ? rows : rows.filter((a) => {
      if (reach !== null) {
        const ago = daysBetween(a.at, now);
        if (ago === null || ago < 0 || ago > reach) return false;
      }
      if (s.auBuilding && a.building !== s.auBuilding) return false;
      if (q && [a.doc, a.who, a.building].join(" ").toLowerCase().indexOf(q) < 0) return false;
      return true;
    });
    const serverCounts = s.auByOutcome || {};
    const serverTotal = Object.values(serverCounts).reduce((n, x) => n + (Number(x) || 0), 0);
    const serverActors = Array.isArray(s.auActors) ? s.auActors : [];

    const clean = shown.filter((a) => isClean(a.outcome)).length;
    const shape = (a) => {
      const [fg, bg] = AU_TONE[a.tone] || AU_TONE.ok;
      return {
        ...a, fg, bg,
        route: a.building === a.finalB || a.finalB === "—" ? a.building : a.building + " → " + a.finalB,
        open: s.auOpen === a.id, arrow: s.auOpen === a.id ? "▾" : "▸", panelShow: s.auOpen === a.id ? "grid" : "none",
        clock: String(a.when || "").replace(/^\S+\s+/, "") || a.when,
        toggle: () => this.setState((p) => ({ auOpen: p.auOpen === a.id ? null : a.id })),
        pickPerson: (ev) => { if (ev && ev.stopPropagation) ev.stopPropagation(); this.auApplyFilter({ auPerson: a.who, auPersonId: a.actorId || "", auPeopleScope: "" }); },
        fields: [
          ["Validation checks", a.checks], ["Warning identified", a.issue], ["Suggested building", a.suggested],
          ["Uploader explanation", a.explanation], ["Agent assessment", a.assessment], ["Explicit approval", a.approval],
          ["Final building", a.finalB], ["Outcome", a.outcome]
        ].map((f) => ({ l: f[0], v: f[1] }))
      };
    };

    // Grouped by the day it happened, newest first, with the day's own outcomes tallied
    // beside its name — so a day that is entirely clean says so without being read row by row.
    const order = [];
    const byDay = {};
    shown.forEach((a) => {
      const key = dayLabel(a.at, now);
      if (!byDay[key]) { byDay[key] = []; order.push(key); }
      byDay[key].push(a);
    });

    // ── the building picker ──
    // The company's own register when it is loaded (usersLive fetches it at sign-in), so a
    // building with no entries yet is still selectable — and valued as the id the server
    // filters on rather than the name a reader sees. The code rides along beside the name:
    // two buildings really can be called the same thing, and a picker that shows only names
    // asks a reader to choose between two identical rows.
    const bldOpts = [{ label: "All buildings", value: "", hint: "" }].concat(
      (Array.isArray(s.axBldsLive) && s.axBldsLive.length
        ? s.axBldsLive.map((b) => ({
          label: String(b.name || ""), value: String(b.id || b.building_id || ""),
          hint: String(b.building_code || b.code || "")
        })).filter((o) => o.label && o.value)
        : Array.from(new Set(rows.map((a) => a.building).filter((b) => b && b !== "—")))
          .map((b) => ({ label: b, value: b, hint: "" }))
      ).sort((a, b) => a.label.localeCompare(b.label))
    );
    const bldTyped = String(s.auBldQuery || "").trim().toLowerCase();
    // The code is searchable too — an FM who knows a building by its code should not have to
    // remember what the register calls it.
    const bldHits = bldOpts.slice(1).filter((o) =>
      !bldTyped || (o.label + " " + o.hint).toLowerCase().indexOf(bldTyped) >= 0);
    // A name that STARTS with what was typed is the one being reached for; alphabetical
    // order alone buries it beneath everything that merely contains the same letters.
    const bldRanked = bldTyped
      ? bldHits.filter((o) => o.label.toLowerCase().indexOf(bldTyped) === 0)
        .concat(bldHits.filter((o) => o.label.toLowerCase().indexOf(bldTyped) !== 0))
      : bldHits;

    return {
      isAudit: s.signedIn && s.view === "audit",
      auCount: String(rows.length),
      // The live read's health, worn quietly beside the tiles: a dot, a word, and a Retry
      // link only when something needs retrying. The rows below keep rendering either way —
      // the seed until auLiveLoad() answers, live rows after.
      auLiveSourceLabel: s.auLiveLoading ? "Reading the ingestion audit…"
        // Samples fill only a trail that was never read; after a successful read the rows
        // are the record itself, and the label says only that the refresh failed.
        : s.auLiveError ? "Unreachable — " + s.auLiveError + (s.auLiveLoadedAt ? "" : " · showing sample data")
        : live ? "Live · svc-operations-intelligence"
        // Nothing read and nothing held. Not "Sample data", which would be a claim about
        // rows that are not there, and not an empty trail either — the register has simply
        // not answered yet.
        : unread ? "Not read yet"
        : "Sample data",
      auLiveSourceDot: s.auLiveError ? "var(--st-risk)" : live ? "var(--st-ok)" : "var(--color-neutral-600)",
      auLiveRetryShow: !s.auLiveLoading && (!live || !!s.auLiveError) ? "inline" : "none",
      auLiveRetry: () => this.auLiveRetryNow && this.auLiveRetryNow(),

      // ── the three tiles: what is in view, never the whole trail ──
      auInView: String(live ? (s.auTotal || 0) : shown.length),
      auCleanRate: live
        ? (serverTotal ? Math.round(((serverCounts.accepted || 0) + (serverCounts.approved_on_confirmation || 0)) / serverTotal * 100) + "%" : "—")
        : (shown.length ? Math.round((clean / shown.length) * 100) + "%" : "—"),
      auNeedsReview: String(live
        ? Math.max(0, serverTotal - (serverCounts.accepted || 0) - (serverCounts.approved_on_confirmation || 0))
        : shown.length - clean),
      auShownLine: shown.length + " of " + (s.auTotal || rows.length) + " entries",

      // ── the filter bar ──
      auQuery: s.auQuery || "",
      auSetQuery: (value) => {
        this.setState({ auQuery: value, auOpen: null });
        clearTimeout(this._auQueryTimer);
        this._auQueryTimer = setTimeout(() => this.auLiveLoad && this.auLiveLoad(), AU_QUERY_DEBOUNCE_MS);
      },
      auBuilding: s.auBuilding || "",
      auBuildingOpts: bldOpts,
      auSetBuilding: (value) => this.auApplyFilter({ auBuilding: value }),
      // What the trigger wears: the building in force, or the standing "All buildings". A
      // filter held from a register that has not loaded yet still names itself rather than
      // reading "All buildings" while it narrows the page.
      auBuildingLabel: (bldOpts.find((o) => o.value === (s.auBuilding || "")) || {}).label
        || (s.auBuilding ? String(s.auBuilding) : "All buildings"),
      auBuildingChip: s.auBuilding
        ? { clear: () => this.auApplyFilter({ auBuilding: "", auBldOpen: false, auBldQuery: "" }) }
        : null,
      auBldOpen: !!s.auBldOpen,
      // Opening starts from the whole register: a leftover search from last time would open
      // on a list already narrowed by a word nobody remembers typing.
      auBldToggle: () => this.setState((p) => ({ auBldOpen: !p.auBldOpen, auBldQuery: "" })),
      auBldClose: () => this.setState({ auBldOpen: false, auBldQuery: "" }),
      auBldQuery: s.auBldQuery || "",
      auSetBldQuery: (value) => this.setState({ auBldQuery: value }),
      auBuildingAll: {
        label: "All buildings", on: !s.auBuilding,
        pick: () => this.auApplyFilter({ auBuilding: "", auBldOpen: false, auBldQuery: "" })
      },
      auBuildings: bldRanked.slice(0, AU_BLD_SHOWN).map((o) => ({
        label: o.label, hint: o.hint, on: (s.auBuilding || "") === o.value,
        pick: () => this.auApplyFilter({ auBuilding: o.value, auBldOpen: false, auBldQuery: "" })
      })),
      // Said only when there is something to say: what the cap is holding back, what the
      // typing reached, or simply how large the register is.
      auBuildingsLine: bldRanked.length > AU_BLD_SHOWN
        ? AU_BLD_SHOWN + " of " + bldRanked.length + " — keep typing to narrow"
        : bldTyped
          ? bldRanked.length + " of " + (bldOpts.length - 1)
          : (bldOpts.length - 1) + (bldOpts.length === 2 ? " building" : " buildings"),
      // The roster the picker offers. Counted over everything EXCEPT the person filter —
      // a name whose own count fell to nothing the moment you picked it could never tell
      // you what picking a different name would give you. Same rule as the status chips.
      auPersonLabel: s.auPerson
        || (s.auPeopleScope === "admins" ? "All admins" : s.auPeopleScope === "users" ? "All users" : "Everyone"),
      auPeopleOpen: !!s.auPeopleOpen,
      auPeopleToggle: () => this.setState((p) => ({ auPeopleOpen: !p.auPeopleOpen })),
      auPeopleQuery: s.auPeopleQuery || "",
      auSetPeopleQuery: (value) => this.setState({ auPeopleQuery: value }),
      auPeopleTabs: [["Everyone", ""], ["All admins", "admins"], ["All users", "users"]].map(([label, scope]) => ({
        label, on: !s.auPerson && (s.auPeopleScope || "") === scope,
        pick: () => this.auApplyFilter({ auPeopleScope: scope, auPerson: "", auPersonId: "" })
      })),
      auPeople: (() => {
        // The company's own roster is the list of names; the trail supplies the counts. Built
        // from the trail alone, the picker was empty on an empty register — dead on exactly
        // the page where you would go looking for a person. Somebody with no entries is still
        // offered: their zero IS the answer to "has this person ingested anything".
        const roster = (s.usLiveLoadedAt && Array.isArray(s.users)) ? s.users : [];
        if (live && (serverActors.length || roster.length)) {
          const typed0 = String(s.auPeopleQuery || "").trim().toLowerCase();
          const byId = {};
          const order = [];
          roster.forEach((u) => {
            const id = String(u.id || "");
            if (!id || !u.name || u.name === "—") return;
            byId[id] = { name: String(u.name), role: String(u.role || "user"), count: 0, id };
            order.push(id);
          });
          serverActors.forEach((a) => {
            const id = String(a.user_id || "");
            const n = a.count == null ? 0 : Number(a.count) || 0;
            if (byId[id]) { byId[id].count = n; return; }
            // Somebody who has left the company: their entries are still in the trail and
            // still need attributing, so dropping the name would strand rows nobody could
            // filter to.
            byId[id || a.name] = { name: String(a.name || "—"), role: String(a.role || "user"), count: n, id };
            order.push(id || a.name);
          });
          return order.map((k) => byId[k])
            .map((x) => ({
              name: x.name,
              // Rendered the way the ROWS render it, so the picker and the list agree.
              role: x.role === "admin" || x.role === "superadmin" ? "Admin" : "User",
              count: String(x.count), id: x.id
            }))
            .filter((x) => (s.auPeopleScope === "admins" ? x.role === "Admin"
              : s.auPeopleScope === "users" ? x.role !== "Admin" : true))
            .filter((x) => !typed0 || x.name.toLowerCase().indexOf(typed0) >= 0)
            .sort((a, b) => a.name.localeCompare(b.name))
            .map((x) => ({
              name: x.name, role: x.role, count: x.count, on: s.auPerson === x.name,
              // Two people can share a name; the id is what the server filters on.
              pick: () => this.auApplyFilter({ auPerson: x.name, auPersonId: x.id, auPeopleScope: "", auPeopleOpen: false, auPeopleQuery: "" })
            }));
        }
        const tally = {};
        peopleScope.forEach((a) => {
          const name = a.who;
          if (!name || name === "—") return;
          if (!tally[name]) tally[name] = { name, role: a.role || "User", n: 0 };
          tally[name].n += 1;
        });
        const typed = String(s.auPeopleQuery || "").trim().toLowerCase();
        return Object.values(tally)
          .filter((x) => (s.auPeopleScope === "admins" ? x.role === "Admin"
            : s.auPeopleScope === "users" ? x.role !== "Admin" : true))
          .filter((x) => !typed || x.name.toLowerCase().indexOf(typed) >= 0)
          .sort((a, b) => a.name.localeCompare(b.name))
          .map((x) => ({
            name: x.name, role: x.role, count: String(x.n), on: s.auPerson === x.name,
            pick: () => this.auApplyFilter({ auPerson: x.name, auPersonId: "", auPeopleScope: "", auPeopleOpen: false, auPeopleQuery: "" })
          }));
      })(),
      auPersonChip: s.auPerson
        ? { label: s.auPerson, clear: () => this.auApplyFilter({ auPerson: "", auPersonId: "", auPeopleScope: "" }) }
        : null,
      auRangePicks: AU_RANGES.map(([label]) => ({
        label, on: range === label,
        pick: () => this.auApplyFilter({ auRange: label })
      })),

      auFilters: AU_STATUSES.map((f) => ({
        label: f,
        count: String(live
          ? (f === "All" ? serverTotal
            : f === "Accepted" ? (serverCounts.accepted || 0) + (serverCounts.approved_on_confirmation || 0)
            : (serverCounts[f.toLowerCase()] || 0))
          : scoped.filter((a) => matchesStatus(a.outcome, f)).length),
        pick: () => this.auApplyFilter({ auFilter: f }),
        on: (s.auFilter || "All") === f,
        bg: (s.auFilter || "All") === f ? "var(--color-accent)" : "var(--color-surface)",
        fg: (s.auFilter || "All") === f ? "var(--accent-ink)" : "var(--color-neutral-400)"
      })),

      auGroups: order.map((label) => ({
        label, count: String(byDay[label].length),
        legend: AU_STATUSES.slice(1).map((st) => ({
          label: byDay[label].filter((a) => matchesStatus(a.outcome, st)).length + " " + st.toLowerCase(),
          n: byDay[label].filter((a) => matchesStatus(a.outcome, st)).length,
          dot: (AU_TONE[(byDay[label].find((a) => matchesStatus(a.outcome, st)) || {}).tone] || AU_TONE.dormant)[0]
        })).filter((l) => l.n > 0),
        rows: byDay[label].map(shape)
      })),
      auRows: shown.map(shape)
    };
  }
};
