// auditTrail — Ingestion audit trail (admin). Reads the same `audit` state the
// ingestion validation agent (logic/ingestion.js) writes to and auditLive.js replaces
// in place with live rows from GET /api/admin/ingestion-audit — the filter chips, tones
// and 8-field record render either without knowing which.
// Methods are mixed into HoistraLogic.prototype; `this` is the controller.
const AU_TONE = {
  ok: ["var(--st-ok)", "var(--st-ok-bg)"],
  warn: ["var(--st-warn)", "var(--st-warn-bg)"],
  risk: ["var(--st-risk)", "var(--st-risk-bg)"],
  dormant: ["var(--st-dormant)", "var(--st-dormant-bg)"],
  accent: ["var(--color-accent)", "var(--color-accent-900)"]
};

export const auditMethods = {
  auditVals(s) {
    const live = (s.audit || []).some((a) => a.live);
    return {
      isAudit: s.signedIn && s.view === "audit",
      auCount: String(s.audit.length),
      // The live read's health, worn quietly next to the Entries tile: a dot, a word, and
      // a Retry link only when something needs retrying. The rows below keep rendering
      // either way — the seed until auLiveLoad() answers, live rows after.
      auLiveSourceLabel: s.auLiveLoading ? "Reading the ingestion audit…"
        : s.auLiveError ? "Unreachable — " + s.auLiveError
        : live ? "Live · svc-operations-intelligence"
        : "Sample data",
      auLiveSourceDot: s.auLiveError ? "var(--st-risk)" : live ? "var(--st-ok)" : "var(--color-neutral-600)",
      auLiveRetryShow: !s.auLiveLoading && (!live || !!s.auLiveError) ? "inline" : "none",
      auLiveRetry: () => this.auLiveRetryNow && this.auLiveRetryNow(),
      auFilters: ["All", "Accepted", "Reassigned", "Overridden", "Rejected"].map((f) => ({
        label: f, pick: () => this.setState({ auFilter: f, auOpen: null }),
        bg: s.auFilter === f ? "var(--color-accent)" : "var(--color-surface)",
        fg: s.auFilter === f ? "var(--accent-ink)" : "var(--color-neutral-400)"
      })),
      auRows: s.audit.filter((a) => s.auFilter === "All" ? true : a.outcome.indexOf(s.auFilter) === 0 || (s.auFilter === "Accepted" && a.outcome.indexOf("Approved") === 0)).map((a) => {
        const [fg, bg] = AU_TONE[a.tone] || AU_TONE.ok;
        return {
          ...a, fg, bg,
          route: a.building === a.finalB || a.finalB === "—" ? a.building : a.building + " → " + a.finalB,
          open: s.auOpen === a.id, arrow: s.auOpen === a.id ? "▾" : "▸", panelShow: s.auOpen === a.id ? "grid" : "none",
          toggle: () => this.setState((p) => ({ auOpen: p.auOpen === a.id ? null : a.id })),
          fields: [
            ["Validation checks", a.checks], ["Warning identified", a.issue], ["Suggested building", a.suggested],
            ["Uploader explanation", a.explanation], ["Agent assessment", a.assessment], ["Explicit approval", a.approval],
            ["Final building", a.finalB], ["Outcome", a.outcome]
          ].map((f) => ({ l: f[0], v: f[1] }))
        };
      })
    };
  }
};
