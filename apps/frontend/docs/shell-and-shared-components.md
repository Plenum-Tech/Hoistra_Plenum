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
- **User view:** Buildings (User), Compliance, Vendors, Energy, Assets, Work orders, then the saved custom reports with their cadence badge.
- **Admin view:** Buildings (Admin), Integrations (Admin) · 15 min — only these.

Active state follows the actual page and role. Order: Vendors sits above Energy.

**Spaces:** the four built-in spaces — Compliance, Energy, Vendor performance, Vendor operations — carry live badges (lapsed certificates, open anomalies, vendors below 80, approvals pending; `—` until the engine answers) and open a space page: figures, the sessions filed there, an ask bar. **+** adds a saved space (svc-udr `saved_spaces`); saved spaces can be renamed and deleted from their page.
**Sessions:** every conversation with the orchestrator and every orchestrator task, newest first with a real elapsed time; a chat session reopens the conversation page on its transcript and continues the same thread, a task reopens the dock on its chain. **All sessions** opens the Sessions page (search, by day, delete, file in a space).

**+ New report:** build a saved report from a session — source (a recent session's question), refresh cadence (30 min · 1 hr · 6 hr · 12 hr · 24 hr · daily 02:00 · chosen days with a 7-day picker and time), name. The first refresh runs on creation; the badge is the cadence, or Pending / Running / Failed.

### 4.4 Ask bar (query first)
Sits directly under the breadcrumb on every non-admin report: Compliance, Vendors, Energy / Assets / Work orders modules, custom reports, Buildings (user view). Sparkle icon, page-scoped placeholder, **Ask** button, three suggested questions for that page. Enter or Ask runs through the query interface and lands on the answer view. Admin pages (Buildings admin, Integrations) do not carry it.

### 4.5 Orchestrator dock
Fixed left panel (280px; 420px during an investigation) opened by any action that makes the platform *do* something. Shows the task as intent, the **Orchestrator → Planner → Worker → Quality** chain playing in, then an armed flow:
- **declare** — Hoist a building / Edit building (`HoistBuildingCard.jsx`, `logic/buildingsCrud.js`): the real form as a card. Hoist runs as three steps — the record (**Write the record** → `POST /api/energy/buildings`), the schema it landed in with the allocated code (**Next steps**), then documents (**Ingest documents now** → the **ingest** flow with that building preselected, or **Do it later**, which leaves the keyed-as line in the dock). Edit is the same card as one step (`PATCH`, only the changed fields). Field-keyed errors from the service render under their own input. Opening either clears a stale conversation already in the dock (`ccChatReset()`) — a new task gets a fresh panel; the old one is still reachable from Recent tasks / Sessions.
- **ingest** — Ingest documents (`logic/renderVals.js`, the `fIngest` block in `OrchestratorDock.jsx`): reachable on its own from a page-level "Ingest documents" button (no building preselected — nothing is guessed), or from the hoist card's step 3. A real upload: the attach control stages files into the same tray the composer's paperclip uses (`ccFiles`/`ccAddFiles`), "Start ingestion" is disabled with an inline reason until at least one file and a building are chosen, and it then sends a real question — `askScoped("Ingest N document(s) for <building>.")` — through `deepAgentsApi.runStatefulWithFiles`, the same multipart call the composer's own attach makes. The card closes immediately; the real answer streams into the transcript like any other turn.
- **booking**, **pick** (contractor swap), **new** (new vendor), **email** (draft with To / Subject / Body, Approve & send)
- **investigate** — the conversational investigation (see §9.6)

Every instruction is stored as a session. A free-text instruction box sits at the bottom.

### 4.6 Decision queue
Right drawer from the Pending pill: items needing approval, each with actions. Compliance route 1 lands here (risk card → pending drawer → action → orchestrator).

### 4.7 Detail drawer
Right drawer for any record (certificate, anomaly, vendor, work order): title, status meta, body, chain of thought, editable fields (accent = changeable, lock = fixed), one refinement suggestion, action buttons.

### 4.8 Toast
Bottom-centre confirmation stating what would be written.

