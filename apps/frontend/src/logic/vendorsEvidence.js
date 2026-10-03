// vendorsEvidence — the Evidence tab's values, from the jobs vendorsLive.evidenceJobs() built.
//
// On 28 Sep 2026 the tab was 242 flat rows: a row per check per work order, and every work
// order twice. It now reads as one row per work order with its four checks side by side.
// It opens on All — the misses lead that list anyway, and a filtered first view read as the
// vendor's whole month — and every filter says how many it holds; a search narrows by code,
// asset or building; the list is paged, ten jobs a page; a row opens to
// the job's own timeline. Filter, search, page and open row are held per vendor (`vendor` in the
// state), so picking another vendor starts clean instead of inheriting a search.
//
// A month and a year can be chosen (Hussain, 28 Sep): the tab opens on the card on screen, and
// the picker offers every month this vendor has a card for, every other month of the year
// listed but disabled. `month` in the state is null for the card's own month.
export const EV_PAGE = 10;

const RISK = "var(--st-risk)", OK = "var(--st-ok)", MUTED = "var(--color-neutral-500)", QUIET = "var(--color-neutral-400)";
const CRIT = { L1: ["var(--st-risk-bg)", RISK], L2: ["var(--st-warn-bg)", "var(--st-warn)"], L3: ["var(--color-neutral-900)", QUIET] };
const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

//: [key, label, which jobs it holds]. Every one but "all" is offered only when it holds a job.
const FILTERS = [
  ["missed", "Missed a check", (j) => j.missed.length > 0],
  ["response", "Response", (j) => j.response.met === false],
  ["completion", "Completion", (j) => j.completion.met === false],
  ["firstfix", "Return visits", (j) => j.firstFix === false],
  ["recall", "Recalls", (j) => j.recall === true],
  ["all", "All", () => true]
];

const plural = (n, one, many) => n + " " + (n === 1 ? one : many);
// Read off the ISO text itself, as fmtDay does, so the time shown is the one recorded.
const dayShort = (iso) => { const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(String(iso || "")); return m ? Number(m[3]) + " " + MONTHS[Number(m[2]) - 1] : ""; };
const clock = (iso) => { const m = /T(\d{2}):(\d{2})/.exec(String(iso || "")); return m ? m[1] + ":" + m[2] : ""; };
const sameDay = (a, b) => String(a || "").slice(0, 10) === String(b || "").slice(0, 10);

// The page buttons: every page when there are seven or fewer, else always seven slots — the
// first, the last, the current page and its neighbours, widened near either end, with "…"
// wherever pages are skipped. A gap of exactly one page shows that page instead of "…".
export function pageWindow(count, current) {
  if (count <= 7) return Array.from({ length: count }, (_, i) => i);
  const last = count - 1;
  const keep = new Set([0, last, current - 1, current, current + 1]);
  if (current <= 3) [1, 2, 3, 4].forEach((i) => keep.add(i));
  if (current >= last - 3) [last - 4, last - 3, last - 2, last - 1].forEach((i) => keep.add(i));
  const pages = [...keep].filter((i) => i >= 0 && i <= last).sort((a, b) => a - b);
  const out = [];
  pages.forEach((i, k) => {
    const prev = pages[k - 1];
    if (k && i - prev === 2) out.push(prev + 1);
    else if (k && i - prev > 2) out.push(null);
    out.push(i);
  });
  return out;
}

// Turning the page leaves the reader at the pager, below the rows they came to read; bring
// the top of the table back into view when it has scrolled off. No DOM in tests: skipped.
function toTableTop() {
  if (typeof document === "undefined" || !document.getElementById) return;
  const el = document.getElementById("vp-ev-table");
  if (el && el.getBoundingClientRect().top < 0) el.scrollIntoView({ block: "start", behavior: "smooth" });
}

function slaCell(c, noTarget) {
  return {
    value: c.actual || "—",
    of: !c.actual ? "not measured" : c.target ? "/ " + c.target : noTarget,
    fg: c.met === false ? RISK : c.met === true ? OK : MUTED
  };
}

function hoursText(c, noTarget) {
  if (!c.actual) return "not measured";
  return c.actual + (c.target ? " against " + c.target : " (" + noTarget + ")");
}

// The job's own story: when it was reported, attended and closed, each against its target.
const fullDay = (iso) => { const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(String(iso || "")); return m ? dayShort(iso) + " " + m[1] : ""; };
function timeline(j) {
  const at = (label, iso, c) => {
    if (!iso) return label + " time not recorded";
    const when = sameDay(iso, j.reportedAt) ? clock(iso) : dayShort(iso) + " " + clock(iso);
    return label + " " + when + " (" + hoursText(c, j.noTarget) + ")";
  };
  const story = [
    j.reportedAt ? "Reported " + fullDay(j.reportedAt) + " " + clock(j.reportedAt) : "Report time not recorded",
    at("attended", j.attendedAt, j.response),
    at("completed", j.completedAt, j.completion)
  ].join(" → ");
  return [
    story,
    j.missed.length ? "Missed: " + j.missed.join(", ") + "." : "Met every check.",
    (j.priority ? j.priority + " · " : "") + j.crit + " asset — an SLA miss here counts " +
      ({ L1: "3×", L2: "1.5×", L3: "1×" })[j.crit] + (j.score !== null ? " · job score " + Math.round(j.score) + " / 100" : "")
  ];
}

// The picker's two selects. Years newest first; the twelve months of the chosen year, the
// ones with no card disabled. A year change keeps the month when that year has a card for
// it, else takes the year's newest.
export function monthPicker(months, selected, pick) {
  const list = months || [];
  const years = [...new Set(list.map((m) => m.slice(0, 4)))];
  const year = (selected || list[0] || "").slice(0, 4);
  return {
    evMonthShow: list.length ? "flex" : "none",
    evYear: year,
    evYears: years.map((y) => ({ value: y, label: y })),
    evMonth: selected || "",
    evMonthOpts: year ? MONTHS.map((label, i) => {
      const iso = year + "-" + String(i + 1).padStart(2, "0") + "-01";
      return { value: iso, label: label, disabled: list.indexOf(iso) < 0 };
    }) : [],
    evPickMonth: (e) => { const iso = e && e.target ? e.target.value : ""; if (list.indexOf(iso) >= 0) pick(iso); },
    evPickYear: (e) => {
      const y = String(e && e.target ? e.target.value : "");
      const same = y + (selected || "").slice(4);
      const to = list.indexOf(same) >= 0 ? same : list.find((m) => m.slice(0, 4) === y);
      if (to) pick(to);
    }
  };
}

// `month` is what evidenceForMonth() (vendorsLive.js) says the chosen month holds, and `pick`
// turns the tab to another month. Without them the pane shows R's own month, as it always did.
export function evidencePane(R, vendorId, state, setState, month) {
  const m = (month && month.view) || { iso: R && R.monthIso, label: R && R.month, latest: true, jobs: (R && R.jobs) || [], dupes: (R && R.evidenceDupes) || 0 };
  const jobs = m.jobs || [];
  const missedN = jobs.filter(FILTERS[0][2]).length;
  const ev = state && state.vendor === vendorId ? state
    : { vendor: vendorId, filter: "all", page: 0, q: "", open: null, month: null };
  const set = (patch) => setState(Object.assign({}, ev, patch));

  const active = FILTERS.find((f) => f[0] === ev.filter) || FILTERS[FILTERS.length - 1];
  const q = String(ev.q || "").trim().toLowerCase();
  const hits = jobs.filter(active[2]).filter((j) => !q || (j.wo + " " + j.asset + " " + (j.building || "")).toLowerCase().includes(q));
  const pageCount = Math.max(1, Math.ceil(hits.length / EV_PAGE));
  // A list that shrank under the page (a re-read, a copy dropped) shows its last page.
  const page = Math.min(Math.max(0, ev.page || 0), pageCount - 1);
  const shown = hits.slice(page * EV_PAGE, page * EV_PAGE + EV_PAGE);
  const goTo = (n) => {
    const to = Math.min(Math.max(0, n), pageCount - 1);
    if (to === page) return;
    set({ page: to, open: null });
    toTableTop();
  };

  const buildings = [...new Set(jobs.map((j) => j.building).filter(Boolean))];
  const oneBuilding = buildings.length === 1 ? buildings[0] : "";

  const filters = FILTERS.map(([key, label, test]) => {
    const n = jobs.filter(test).length;
    const on = key === active[0];
    if (!n && key !== "all" && !on) return null;
    return {
      key, label, n: String(n), active: on,
      border: on ? "var(--color-accent)" : "var(--color-divider)",
      fg: on ? "var(--color-accent)" : QUIET,
      bg: on ? "var(--color-accent-900)" : "transparent",
      click: () => set({ filter: key, page: 0, open: null })
    };
  }).filter(Boolean);

  const rows = shown.map((j) => {
    const open = ev.open === j.key;
    const crit = CRIT[j.crit] || CRIT.L2;
    return {
      key: j.key, wo: j.wo, crit: j.crit, critBg: crit[0], critFg: crit[1],
      weight: j.weight ? "×" + j.weight.replace("×", "") : "",
      sub: [j.asset, dayShort(j.completedAt), oneBuilding ? "" : (j.building || "")].filter(Boolean).join(" · "),
      response: slaCell(j.response, j.noTarget),
      completion: slaCell(j.completion, j.noTarget),
      firstFix: j.firstFix === false ? { value: "Return visit", fg: RISK } : j.firstFix === true ? { value: "First visit", fg: QUIET } : { value: "—", fg: MUTED },
      recall: j.recall === true ? { value: "Recalled", fg: RISK } : j.recall === false ? { value: "None", fg: QUIET } : { value: "—", fg: MUTED },
      edge: j.missed.length ? RISK : "transparent",
      open, caret: open ? "ph-caret-down" : "ph-caret-right",
      bg: open ? "var(--color-bg)" : "transparent",
      detail: open ? timeline(j) : [],
      toggle: () => set({ open: open ? null : j.key })
    };
  });

  const scope = m.label ? "for the " + m.label + " card" : "across every scored month";
  const summary = !jobs.length ? ""
    : plural(jobs.length, "work order", "work orders") + " scored " + scope + (oneBuilding ? " at " + oneBuilding : "") +
      (missedN
        ? " — " + (jobs.length - missedN) + " met every check, " + missedN + " missed at least one check."
        : " — every work order met every check.");

  const dupes = m.dupes || 0;
  const pick = (month && month.pick) || (() => {});
  const latestLabel = R && R.month ? R.month : "";
  return {
    ...monthPicker(month ? (R && R.months) || [] : [], m.iso, pick),
    evLatestShow: !m.latest && latestLabel ? "inline-flex" : "none",
    evLatestLabel: latestLabel ? "Back to " + latestLabel : "",
    evLatest: () => pick(R && R.monthIso),
    evShow: jobs.length ? "block" : "none",
    evSummary: summary,
    evBuilding: oneBuilding,
    evFilters: filters,
    evQuery: ev.q || "",
    evSetQuery: (e) => set({ q: (e && e.target ? e.target.value : "") || "", page: 0, open: null }),
    evClearQuery: () => set({ q: "", page: 0, open: null }),
    evClearShow: ev.q ? "inline-flex" : "none",
    evRows: rows,
    evPagerShow: pageCount > 1 ? "flex" : "none",
    evPageLabel: hits.length ? (page * EV_PAGE + 1) + "–" + (page * EV_PAGE + shown.length) + " of " + hits.length : "",
    evPages: pageWindow(pageCount, page).map((i) => i === null
      ? { label: "…", current: false, gap: true }
      : {
          label: String(i + 1), current: i === page, gap: false,
          border: i === page ? "var(--color-accent)" : "var(--color-divider)",
          fg: i === page ? "var(--color-accent)" : "var(--color-text)",
          bg: i === page ? "var(--color-accent-900)" : "transparent",
          click: () => goTo(i)
        }),
    evPrevOn: page > 0,
    evNextOn: page < pageCount - 1,
    evPrev: () => goTo(page - 1),
    evNext: () => goTo(page + 1),
    evNoMatchShow: jobs.length && !hits.length ? "block" : "none",
    evNoMatch: q
      ? "No work order matches “" + String(ev.q).trim() + "”" + (active[0] !== "all" ? " under " + active[1] : "") + "."
      : "No work order in this view.",
    evDupeNote: dupes
      ? (dupes === 1 ? "1 work order was" : dupes + " work orders were") +
        " scored more than once for this month. Each is listed and counted once here; Rebuild scorecards replaces the extra scores."
      : ""
  };
}
