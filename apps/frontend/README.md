# Hoistra — frontend

AI-native property management platform. React 18 + Vite. The UI is split into screens and shell components; the behaviour lives in a small store split by domain. `reference/` holds the original prototype the port was made from; `docs/` is the specification.

```
npm install
npm run dev
```

## How it's organised

```
src/
  main.jsx                     mounts <App/>
  App.jsx                      routes on the store's flags: gated · signedIn · isHome · isVP · isCC …
  api/                         HTTP layer — client.js (fetch wrapper, base paths) · compliance.js, opsIntelligence.js, energy.js
                               (svc-operations-intelligence) · spaces.js (svc-udr) · deepAgents.js
                               (udr.js and docRag.js exist locally but nothing imports either — kept out of
                               commits until something actually uses them)
  logic/                       the store
    Controller.js              tiny base: state / setState / forceUpdate / subscribe
    HoistraLogic.js            the controller: state shape + domain method mix-ins
    useHoistra.js              React hook → one controller, returns renderVals() as `vals`
    core.js                    session, orchestrator, queries, drawers, table helpers
    compliance.js              compliance console model, certificate actions
    vendors.js                 vendor record drawer
    vendorsLive.js             Vendors page from svc-operations-intelligence: shapeLiveVendors + vpLoad
    buildingsLive.js           Buildings table from GET /api/energy/buildings (DB-only, no seed)
    buildingsCrud.js           Hoist / Edit / Remove a building: the dock card (three-step hoist, PATCH-diff edit — components/shell/HoistBuildingCard.jsx) and the delete dry-run dialog
    buildingsGraph.js          The per-row graph drawer: child-table counts and, via GET /buildings/{id}/cost-drivers, what it is costing
    graphLive.js               Hoist Graph diagram + per-building counts from the table already loaded, plus GET /api/energy/graph/tables (glBuildings, glCount, glHubs)
    sessions.js                every conversation with the orchestrator as a record keyed by its thread id (browser store)
    spacesLive.js              the four built-in spaces with live figures + saved spaces from svc-udr: shapeSpaces + sp*
    reports.js                 custom reports — a pinned question re-run on a cadence: nextRunAt, reportMarkdown, rp*
    session.js                 the reload slice (signed in, current view, active session id)
    energy.js                  energy scope, ratings, buildings list, investigate conversation
    integrations.js            integrations admin view model
    renderVals.js              the view model — every key the templates read
    constants.js               static tables (graph nodes, packs, market profiles, crons, cadences)
  screens/                     one file per page
    Gate · Home · Chat · Answer · Buildings · Compliance · Vendors · Module · CustomReport · Sessions · Space · Integrations
  components/shell/            TopBar · Navigator · OrchestratorDock · DecisionQueue · DetailDrawer · SessionList
                               · ConnectModal · CommandPalette · Toast · Markdown · ComplianceAnswer · RunTrace
  data/                        seed data as ES modules (portfolio, certificates, vendors, connectors, energy rules)
  styles/                      nocturne.css (base design system) · tokens.css (Hoistra palette, light + dark)
                               · hover.css (hover states) · base.css (resets, keyframes)
reference/                     the prototype: Hoistra.html (standalone) + Hoistra.dc.html source
docs/                          specification, one file per area — start with platform-overview.md
```

## The pattern

Every component is a pure function of `vals`:

```jsx
export default function Energy({ vals }) { … }
```

`vals` is `useHoistra()` → `HoistraLogic.renderVals()`. It contains every value and handler the screens read (`vals.enScopeCards`, `vals.toggleQueue`, `vals.investigate`…). Components hold no state of their own; clicks call handlers on `vals`, which call `this.setState` on the controller, which notifies React. That is the same contract the prototype ran on, so behaviour is identical.

`Module.jsx` renders Energy and the two Pending modules (Assets, Work orders) from `vals.mod`; splitting Energy into its own file is a straightforward next step once the section boundaries are agreed.

## What is deliberate

- **Inline styles.** Every element carries its exact spec inline, matching the design reference one-to-one. Move to a styling system per your conventions, but keep the values.
- **`hover.css` uses `!important`.** Hover states from the reference are class rules; inline styles would otherwise win. Replace with your hover mechanism if preferred.
- **One store, not many.** The controller is one state object because the prototype's cross-screen behaviour (orchestrator, queue, sessions, role) depends on it. Domain files are mix-ins on its prototype so `this` is shared. Break out per-domain stores once the API layer exists.
- **Seed data in `src/data/`.** Shapes show what each screen consumes; replace with API calls behind the same shapes.

## Backend connectivity

`src/api/` is the HTTP layer: `client.js` (one fetch wrapper, same-origin `/backend/<service>` paths, `VITE_*` overrides) and one file per service. The Compliance console is wired to `svc-operations-intelligence` through `src/logic/complianceLive.js`: it reads certificates, coverage and country packs, reshapes them to the seed's shape (`shapeLiveCompliance`, a pure function), and mixes `ccLoad` / `ccRunScan` / `ccVerify` / `ccRenewal` into the controller. `ccModel()` reads `this.ccData()` — the live register when loaded, the seed otherwise — and the page states which it is showing.

The Home page follows the same pattern through `src/logic/homeLive.js` (`api/opsIntelligence.js` wraps the approvals, contract-performance and energy routers). `shapeLiveHome`, a pure function, turns the compliance summary, contract parameters, meters, open anomalies and the approvals queue into the Hoist Score, the Hoist Crons feed and the hero line; `homeModel()` memoises it per load. Every read is independent, so a tile goes live when its source answers and each tile says whether it is live or seed. The P&L tile is seed by design (no budget ledger exists) and is labelled as such.

`screens/Chat.jsx` (view `chat`) is the full-page conversation with `svc-deepagents`, laid out like the Plenum AI shell's Orchestrator screen — your questions on the right, each answer on the left under the engine that produced it (`domainOf` in `logic/chat.js`, read off the tools behind the reply), structured answers carrying their "how this answer was produced" panel, and a composer that stays at the bottom. It is reached from a space's ask bar and by reopening a conversation from the sessions list. The compliance console, the Vendors page and the Buildings page answer in their side dock instead (`DOCK_VIEWS = ["cc","vp","buildings"]` in `complianceLive.js`), so the page stays in view. Home is deliberately not a dock view — its ask bar opens the full chat page too, same as reopening a conversation from the sessions list — and all three surfaces share one transcript. Both read the same transcript state (`ccChat`, `ccBusy`, `ccStream` in `complianceLive.js`); `askScoped` → `ccAsk` decides page or dock by the view it was asked from (`dockAnswers()`), and `chatContext()` tells the orchestrator which page that was. A question never discards the dock's flow panel; Cancel does. A reload on a dock page reopens the dock on the restored transcript. One turn runs at a time — sending mid-answer keeps the draft and says so. An engine that fails inside a "successful" turn (its answer is a bare JSON `error`) is shown as an error, not as prose.

`npm test` runs the shaping tests with `node --test` (Node 24).

The Vendors page follows the same pattern through `src/logic/vendorsLive.js`: `shapeLiveVendors`, a pure function, turns the contract-performance scorecards, parameter sets, weights and pending approvals plus the compliance register's vendor certificates and coverage into the seed's shape — a directory, a record per vendor (`contract`, `terms`, `rows`, `certs`, `invoices`) and the published score — and `vpModel()` memoises it per load. The `vp*` section of `renderVals` reads whichever is loaded; a value with no read endpoint behind it (per-work-order breaches, L1/L2/L3 split, spend, contract expiry, matched invoice lines, open work orders) shows as "—" or an empty table rather than a seed figure. Scores are the engine's `overall_score`, never recomputed. The write actions on the page (rebuild, claim credits, credit note, approve as charged) keep the scripted flow.

The Buildings page below its table reads two live sources. `src/logic/graphLive.js` draws the Hoist Graph diagram from buildings already loaded by the table (`glBuildings`/`glHubs` fill the hand-placed hub positions in `HUBS` with real rows, and note when the portfolio is larger than the canvas draws) and reads each building's `graph_counts` for the branch figures (`glCount`) — a real count, a real zero, or "?" when nothing on the register has ever reported that branch (`glBranchCounted`), never invented from floor count the way the seed formula did. `GET /api/energy/graph/tables` (`glLoadTables`) backs the per-table row/column counts. `src/logic/buildingsGraph.js` is the per-row drawer: a canonical tree (floors → spaces, assets → equipment/meters, documents → certificates, contracts → work orders/invoices) built immediately from the row's own counts, then `GET /api/energy/buildings/{id}/graph` fills in the real child rows, cached per building. Not sourced, and shown as such: vector-similarity badges, document file sizes, and which vendor serves which building on the diagram (the seed's vendor nodes were invented and are gone rather than kept).

## Sessions, spaces and reports

The navigator's three groups follow the Plenum AI shell's model and hold no seed data.

- **Sessions** (`src/logic/sessions.js`). Every conversation with the orchestrator is a record keyed by the
  `session_id` the thread runs under on svc-deepagents: first question, page it was asked from, the engine
  that answered (read off the tools used), real timestamps and the transcript. Orchestrator tasks are
  sessions of kind `task`. svc-deepagents has no route that lists threads, so — as the Plenum shell does —
  the list and the transcripts live in this browser's `localStorage` (`hoistra.sessions.v1`, capped at 60
  sessions / 40 messages each, shrinking traces first when the browser refuses the size). Opening a session
  restores its transcript and continues the same server thread; "New query" starts a fresh one on the chat page — the main orchestrator — with the dock closed and the composer focused. The
  transcript is mirrored into the record from `HoistraLogic.setState` whenever `ccChat` changes. "All
  sessions" is a page (`screens/Sessions.jsx`): search, grouped by day, delete, file in a space.
- **Spaces** (`src/logic/spacesLive.js`). Four built-in spaces — Compliance, Energy, Vendor performance,
  Vendor operations — whose badges are live figures from reads the pages already make: lapsed certificates
  (compliance saved-space summary), open anomalies, vendors scored below 80 on their newest card, pending
  approvals (`500+` at the route's page cap). A figure whose source has not answered shows `—`. Saved spaces
  are read from svc-udr (`GET /backend/udr/api/spaces`, `plenum_cafm.saved_spaces`) and created, renamed and
  deleted through the same router (`src/api/spaces.js`) — **the one place the navigator writes to a backend,
  on the user's click**. A space page (`screens/Space.jsx`) shows the figures, the sessions filed there and
  an ask bar that starts a session in it. Which sessions sit in a saved space is kept on the session record
  (there is no `saved_space_item` route). The navigator section counts read the same model.
- **Custom reports** (`src/logic/reports.js`). A report pins a session's question with a cadence (30 min ·
  1 hr · 6 hr · 12 hr · 24 hr · daily 02:00 · chosen days at a time). Creating one runs it at once through
  `POST /api/workflow/run-stateful` on a fresh `report-…` thread, with a context line saying it is a
  scheduled report; the answer (markdown, or the structured compliance payload) is stored with the tools
  behind it, the last three refreshes are kept, and a 30-second tick re-runs due reports one at a time
  while the app is open. The page renders the refresh in view, says when it ran and when the next is due,
  and Export saves it as markdown. `plenum_cafm.pinned_run` has no route yet, so reports live in
  `localStorage` (`hoistra.reports.v1`) and the page says they re-run "while Hoistra is open".

`test/sessions.test.mjs`, `test/spacesLive.test.mjs`, `test/reports.test.mjs` cover the pure shaping;
`test/store.test.mjs` runs the controller against a dead backend and an in-memory `localStorage`.

Everything else (investigations, email drafts, the seed modules, the Pending pill and decision queue) is still scripted against the seed data in `src/data/`. No auth; the persistence that exists is the browser stores above and svc-udr's saved spaces.

Dev: `npm run dev` on :5173 proxies `/backend` to the docker gateway on :3000 (`vite.config.js`). Build: `Dockerfile` produces the static bundle and serves it with nginx on :3000.

## Fidelity

High. Colours, type, spacing, states and copy are final; the port is a mechanical translation of the reference, so what you see in `npm run dev` is what was designed.
