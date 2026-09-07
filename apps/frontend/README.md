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
  api/                         HTTP layer — client.js (fetch wrapper, base paths) · compliance.js (svc-operations-intelligence)
  logic/                       the store
    Controller.js              tiny base: state / setState / forceUpdate / subscribe
    HoistraLogic.js            the controller: state shape + domain method mix-ins
    useHoistra.js              React hook → one controller, returns renderVals() as `vals`
    core.js                    session, orchestrator, queries, drawers, table helpers
    compliance.js              compliance console model, certificate actions
    vendors.js                 vendor record drawer
    energy.js                  energy scope, ratings, buildings list, investigate conversation
    integrations.js            integrations admin view model
    renderVals.js              the view model — every key the templates read
    constants.js               static tables (graph nodes, packs, market profiles, crons, cadences)
  screens/                     one file per page
    Gate · Home · Answer · Buildings · Compliance · Vendors · Module · CustomReport · Integrations
  components/shell/            TopBar · Navigator · OrchestratorDock · DecisionQueue · DetailDrawer
                               · ConnectModal · CommandPalette · Toast
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

Everything else (queries, investigations, email drafts, the other screens) is still scripted against the seed data in `src/data/`. No auth, no persistence yet.

Dev: `npm run dev` on :5173 proxies `/backend` to the docker gateway on :3000 (`vite.config.js`). Build: `Dockerfile` produces the static bundle and serves it with nginx on :3000.

## Fidelity

High. Colours, type, spacing, states and copy are final; the port is a mechanical translation of the reference, so what you see in `npm run dev` is what was designed.
