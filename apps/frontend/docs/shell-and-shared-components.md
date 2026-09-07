# Hoistra skill — Shell and shared components

> One of the Hoistra platform skill files. Start with `skills/platform-overview.md` for the product model, principles and file map; each skill file covers one area of `Hoistra.dc.html` and its data files.

## Shell and shared components

### 4.1 Sign-in gate
Email field + Continue. Session opens in **user view**.

### 4.2 Top bar
Logo → home · reporting currency (GBP · USD · AED · SGD) · **Pending** pill (decision queue count, pulsing) · tenant · orchestrator icon · **account avatar "A"**.

**Account menu (Aasim):** header line states the current mode ("User view · Planum Technologies"). Items: **Pricing**, **Support**, and a mode toggle that reads **Admin view** in user mode and **User view** in admin mode. **Sign out** as a separated last row. The click-away layer sits below the header's stacking context so menu rows stay clickable.

### 4.3 Navigator (left)
Collapsed rail (icons) or open panel (248px). Open panel shows: **New query** · **Reports** group · **Spaces** · **Sessions**.

Reports group is scoped by mode:
- **User view:** Buildings (User), Compliance, Vendors, Energy, Assets, Work orders, then saved custom reports (e.g. Risky Buildings · 30 min).
- **Admin view:** Buildings (Admin), Integrations (Admin) · 15 min — only these.

Active state follows the actual page and role. Order: Vendors sits above Energy.

**Spaces:** Compliance (3 lapsed), Energy (6 anomalies), Vendor performance (3 below 80), Vendor operations (14 to approve) — each opens its module.
**Sessions:** every query and every orchestrator task, newest first; a task reopens the orchestrator, a query re-runs, "What needs my approval today?" opens the decision queue.

**+ New report:** build a saved report from a session — source query, refresh cadence (30 min · 1 hr · 6 hr · 12 hr · 24 hr · daily 02:00 · chosen days with a 7-day picker and time), name.

### 4.4 Ask bar (query first)
Sits directly under the breadcrumb on every non-admin report: Compliance, Vendors, Energy / Assets / Work orders modules, custom reports, Buildings (user view). Sparkle icon, page-scoped placeholder, **Ask** button, three suggested questions for that page. Enter or Ask runs through the query interface and lands on the answer view. Admin pages (Buildings admin, Integrations) do not carry it.

### 4.5 Orchestrator dock
Fixed left panel (280px; 420px during an investigation) opened by any action that makes the platform *do* something. Shows the task as intent, the **Orchestrator → Planner → Worker → Quality** chain playing in, then an armed flow:
- **declare** — hoist a building (3 steps: record → schema → documents)
- **booking**, **pick** (contractor swap), **new** (new vendor), **email** (draft with To / Subject / Body, Approve & send)
- **investigate** — the conversational investigation (see §9.6)

Every instruction is stored as a session. A free-text instruction box sits at the bottom.

### 4.6 Decision queue
Right drawer from the Pending pill: items needing approval, each with actions. Compliance route 1 lands here (risk card → pending drawer → action → orchestrator).

### 4.7 Detail drawer
Right drawer for any record (certificate, anomaly, vendor, work order): title, status meta, body, chain of thought, editable fields (accent = changeable, lock = fixed), one refinement suggestion, action buttons.

### 4.8 Toast
Bottom-centre confirmation stating what would be written.

