# Hoistra skill — Home and query

> One of the Hoistra platform skill files. Start with `skills/platform-overview.md` for the product model, principles and file map; each skill file covers one area of `Hoistra.dc.html` and its data files.

## Home and query

### 5.1 Home
The query input is the page. Beneath it: Portfolio P&L YTD (saved to date), Hoist Score, **Hoist Crons** live log (Compliance, Energy, Vendor, Orchestrator, Assets agents with timestamps and one-click actions), the four Spaces, pinned questions, recent sessions.

### 5.2 Answer view
Scope line · title · takeaway paragraph · caveat · four metric cards · ranked table (rows open the detail drawer) · chain of thought toggle · suggested follow-ups · actions. Answer set lives in `hoistway-data.js` (compliance, energy, vendors, ops).

Energy answer metrics: Portfolio EUI 194 vs 180 · Excess cost £312k · Anomalies 6 · **MEES — enforceable now: 0 below EPC E** · **MEES — proposed 2031: 2 below EPC B**. The caveat states which is law and which is a proposal (June 2026 interim response moved B from 2030 to 2031; the 2027 C milestone was dropped and is not scored).


---

## Custom reports
Saved from a session with a refresh cadence. Page: kicker *Custom report · dynamic*, title, the source question, last refresh and next run with the cadence, then the orchestrator's answer to the pinned question (structured compliance answer or markdown) with the tools behind it; the last three refreshes are selectable. States: first refresh scheduled, refreshing, ready, failed (the last good refresh stays). Run now, Export (markdown), Delete. Carries the ask bar.

