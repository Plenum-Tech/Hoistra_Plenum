# Migration in the chat — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A CMMS export is migrated from the Orchestrator conversation — every gate answered in the transcript — and `screens/Migration.jsx` is deleted, while hoisting a building and assigning it to a user become reachable from the same chat.

**Architecture:** The migration engine in `logic/migration.js` is not rewritten. Its view model (`mgVals`) is already spread into every render, so the gate tables move out of the deleted page into `components/shell/MigrationRun.jsx`, which `screens/Chat.jsx` renders under the transcript. Only three behaviours change on the controller: where a run is shown, how a run is started from the composer, and which typed messages are answered deterministically instead of by the model.

**Tech Stack:** React 18 + Vite (no JSX runtime tests — `node --test` drives the controller against a mocked `fetch`; `vite build` is what proves the JSX). Backend `svc-ai-schema-mapper` via `api/schemaMapper.js`, unchanged.

**Spec:** `docs/superpowers/specs/2026-09-21-migration-in-chat-design.md`

## Global Constraints

- **No git commits.** Hussain's standing rule: nothing on `Hoistra_Plenum` is committed, merged, stashed, reset or pushed unless he names it. Each task therefore ends with a green test run, not a commit. Leave the work in the tree.
- **No database writes against `:3000`.** The dev server's default proxy target is the production-pointed gateway. Every live check runs with `VITE_DEV_PROXY_TARGET=http://localhost:3010` (the `docker-compose.testenv.yml` stack — own postgres, invented buildings).
- **Baseline is 777 passing tests** (`npm test` in `apps/frontend`, Node 24). No task may leave a red one.
- **`logic/migration.js`'s gate engine is not to be redesigned.** `defaultGateBody`, `NODES`, `pollDelay`, `runKind`, `shapeNodes` and the arm/confirm write gate stay exactly as they are; their bodies are held to the service's handlers by existing tests.
- **Whole-message matching only** for any typed intent. Never a substring — `chatCases.js` documents why (a substring match on "file it" mis-routed a held document on 21 Sep 2026).
- Node is v24.18.1; run everything from `apps/frontend`.

---

### Task 1: The run card component

**Files:**
- Create: `apps/frontend/src/components/shell/MigrationRun.jsx`
- Read (source of the move): `apps/frontend/src/screens/Migration.jsx:1-341,423-539`
- Test: `apps/frontend/npm run build` (JSX integrity), `npm test` (must stay 777)

**Interfaces:**
- Consumes: the keys `mgVals()` already returns — `mgHasRun, mgId, mgIdShort, mgFile, mgCmmsName, mgStarted, mgPill, mgKind, mgProgress, mgStepLabel, mgCoverage, mgNodes, mgAuto, mgToggleAuto, mgRefresh, mgError, mgGate, mgGateTitle, mgGateBlurb, mgGateCount, mgPrimary, mgStepFacts, mgOutputs, mgDecided, mgResetDecisions, mgArm, mgDisarm, mgConfirmWrite, mgRejectWrite, mgWriteLabel`, plus the per-gate `...g` spread (`pkRows, utRows, psRows, clRows, cmRows, fmRows, hiRows, wrTotal`, etc.).
- Produces: `export default function MigrationRun({ vals })` — one element, no props beyond `vals`. Task 2 and Task 6 both import it by that name.

- [ ] **Step 1: Copy the gate bodies across verbatim**

Take `screens/Migration.jsx` lines 1–341 — the style constants (`BARE, KICKER, MONO, CARD, TH, TD, SELECT`), the four helpers (`Seg, Pill, Changed, Table`), the eight gate components (`PkGate, UniqueTablesGate, PreSemanticGate, ClassificationGate, ColumnMappingGate, FieldMappingGate, HierarchyGate, WriteGate`) and the `GateBody` switch — into the new file unchanged. They read only `vals`, so nothing in them needs editing.

- [ ] **Step 2: Write the card shell, stacked instead of side-by-side**

The page put the node tracker in a 280px left column. At transcript width (≈930px: `.chat-grid` is `minmax(0,1fr) 320px` inside `max-width: 1280px`) the tracker goes above the gate, completed nodes collapsed — which is also what the reference does ("COMPLETED STEPS — scroll up to review", "Previous node outputs stay visible while the next node runs").

```jsx
export default function MigrationRun({ vals }) {
  return (
    <div style={{ marginTop: "18px", border: "1px solid var(--color-divider)", borderRadius: "12px", background: "var(--color-surface)", overflow: "hidden" }}>
      {/* head — what run this is */}
      <div style={{ display: "flex", alignItems: "center", gap: "10px", flexWrap: "wrap", padding: "12px 16px", borderBottom: "1px solid var(--color-divider)" }}>
        <i className="ph ph-file-arrow-up" style={{ fontSize: "14px", color: "var(--color-accent)" }}></i>
        <span style={{ fontSize: "12.5px", fontWeight: "500" }}>{"Migration ingest"}</span>
        <span style={{ ...MONO, fontSize: "10.5px", color: "var(--color-neutral-500)" }} title={vals.mgId}>{vals.mgIdShort}</span>
        <span style={{ fontSize: "11px", color: "var(--color-neutral-500)" }}>{vals.mgFile}</span>
        <span style={{ flex: "1" }}></span>
        <Pill {...vals.mgPill} />
        <label style={{ display: "inline-flex", alignItems: "center", gap: "5px", fontSize: "10.5px", color: "var(--color-neutral-400)", cursor: "pointer" }}>
          <input type="checkbox" checked={vals.mgAuto} onChange={vals.mgToggleAuto} />
          {"Auto-continue"}
        </label>
        <button type="button" className="hv13" onClick={vals.mgRefresh} style={{ ...BARE, fontSize: "10.5px", padding: "4px 9px", borderRadius: "6px", border: "1px solid var(--color-divider)", color: "var(--color-neutral-400)" }}>
          {"Refresh"}
        </button>
      </div>
      <div style={{ height: "3px", background: "var(--color-neutral-900)" }}>
        <div style={{ height: "100%", width: vals.mgProgress + "%", background: vals.mgKind === "failed" ? "var(--st-risk)" : "var(--color-accent)", transition: "width 0.4s ease" }}></div>
      </div>

      {/* completed steps — collapsed, newest last, exactly the reference's stack */}
      <div style={{ padding: "12px 16px", display: "flex", flexDirection: "column", gap: "6px" }}>
        <div style={KICKER}>{"Completed steps"}</div>
        {vals.mgNodes.filter((n) => n.status === "complete" || n.current).map((n) => (
          <div key={n.id} style={{ border: "1px solid var(--color-divider)", borderRadius: "8px", padding: "8px 10px", background: n.current ? "var(--color-accent-900)" : "var(--color-bg)" }}>
            <button type="button" onClick={n.toggle} style={{ ...BARE, display: "flex", alignItems: "center", gap: "8px", width: "100%", textAlign: "left" }}>
              <i className={`ph ${n.icon}`} style={{ fontSize: "13px", color: n.tone }}></i>
              <span style={{ fontSize: "11.5px" }}>{n.id + " · " + n.name}</span>
              <span style={{ flex: "1" }}></span>
              <span style={{ ...MONO, fontSize: "9.5px", color: "var(--color-neutral-500)" }}>{n.ms}</span>
            </button>
            {n.outcome ? <div style={{ fontSize: "11px", color: "var(--color-neutral-400)", marginTop: "4px" }}>{n.outcome}</div> : null}
            {n.logsOpen ? <div style={{ ...MONO, fontSize: "10px", color: "var(--color-neutral-500)", marginTop: "6px", whiteSpace: "pre-wrap" }}>{n.logs.join("\n")}</div> : null}
          </div>
        ))}
      </div>

      {vals.mgError ? (
        <div style={{ margin: "0 16px 12px", fontSize: "11.5px", padding: "8px 11px", borderRadius: "8px", background: "var(--st-risk-bg)", color: "var(--st-risk)" }}>{vals.mgError}</div>
      ) : null}

      {/* the open gate */}
      <div style={{ padding: "4px 16px 16px" }}>
        <div style={{ fontSize: "14px", marginBottom: "3px" }}>{vals.mgGateTitle}</div>
        <div style={{ fontSize: "11.5px", color: "var(--color-neutral-400)", lineHeight: "1.5", marginBottom: "10px" }}>{vals.mgGateBlurb}</div>
        <GateBody vals={vals} />
        {vals.mgPrimary ? (
          <button type="button" className="hv7" onClick={vals.mgPrimary.run} style={{ ...BARE, marginTop: "12px", fontSize: "12.5px", padding: "9px 18px", borderRadius: "8px", background: "var(--color-accent)", color: "var(--accent-ink)" }}>
            {vals.mgPrimary.label}
          </button>
        ) : null}
        {vals.mgOutputs.length ? (
          <div style={{ display: "flex", gap: "8px", flexWrap: "wrap", marginTop: "12px" }}>
            {vals.mgOutputs.map((o) => (
              <a key={o.label} href={o.href} target="_blank" rel="noreferrer" style={{ fontSize: "11px", padding: "5px 10px", borderRadius: "7px", border: "1px solid var(--color-divider)", color: "var(--color-accent)", textDecoration: "none" }}>{o.label}</a>
            ))}
          </div>
        ) : null}
      </div>
    </div>
  );
}
```

- [ ] **Step 3: Verify the build compiles the new file**

Nothing imports it yet, so add the import in Task 2 — for now check the file parses:

Run: `cd apps/frontend && npx vite build 2>&1 | tail -5`
Expected: build succeeds (the new file is not yet in the graph; this only proves no syntax error elsewhere). Then `node --check` is not applicable to JSX, so confirm by Task 2's build instead.

- [ ] **Step 4: Run the suite**

Run: `cd apps/frontend && npm test 2>&1 | tail -6`
Expected: `pass 777`, `fail 0` — nothing has changed behaviourally yet.

---

### Task 2: The chat renders the run, and the run follows the chat

**Files:**
- Modify: `apps/frontend/src/screens/Chat.jsx` (import + render below the transcript)
- Modify: `apps/frontend/src/logic/migration.js:255-278` (`mgShow`, `mgOpen`), `:363` (the poll's continuation guard)
- Test: `apps/frontend/test/migration.test.mjs`

**Interfaces:**
- Consumes: `MigrationRun` from Task 1.
- Produces: `mgShow()` and `mgOpen(id)` leave `state.view === 'chat'`; the poll continues while `this.chatView()` is true.

- [ ] **Step 1: Write the failing tests**

Replace the reload test at `test/migration.test.mjs:481` and add two new ones:

```js
test('opening a run puts it in the Orchestrator, not on a page of its own', async () => {
  handlers[STATUS] = doc();
  c.mgOpen(ID);
  await settle();
  assert.equal(c.state.view, 'chat', 'the run is answered in the conversation');
  assert.equal(c.state.mgId, ID);
  assert.ok(c.renderVals().mgHasRun, 'the card has a run to render');
});

test('a run keeps polling while the chat is showing', async () => {
  handlers[STATUS] = doc();
  c.setState({ view: 'chat' });
  c.mgOpen(ID);
  await settle();
  assert.ok(c._mgTimer, 'the next status read is scheduled from the chat');
  c.setState({ view: 'buildings' });
  clearTimeout(c._mgTimer);
  c._mgTimer = null;
  await c.mgPoll(true);
  assert.ok(c._mgTimer, 'a dock page follows the run too — chatView() covers both');
});

test('a reload lands back on the open run: the chat and the migration id both persist', async () => {
  const { loadSession, saveSession } = await import('../src/logic/session.js');
  saveSession({ signedIn: true, refreshToken: 'ref-1', email: 'a@b.c', role: 'admin', account: { id: 'u1', email: 'a@b.c', role: 'admin', status: 'active' }, view: 'chat', mgId: ID }, { signedIn: true });
  const back = loadSession();
  assert.equal(back.view, 'chat');
  assert.equal(back.mgId, ID);
  saveSession({ signedIn: true, refreshToken: 'ref-1', email: 'a@b.c', role: 'admin', account: { id: 'u1', email: 'a@b.c', role: 'admin', status: 'active' }, view: 'home', mgId: 'not an id at all!' }, { signedIn: true });
  assert.equal(loadSession().mgId, undefined, 'a malformed id is not restored');
});
```

- [ ] **Step 2: Run them to watch them fail**

Run: `cd apps/frontend && node --test test/migration.test.mjs 2>&1 | grep -E "^not ok|# fail" | head`
Expected: the first two fail — `view` is `'migration'`, and the poll stops because its guard names a view that the chat is not.

- [ ] **Step 3: Make `mgShow` / `mgOpen` open the conversation**

In `logic/migration.js`, replace the two navigation methods:

```js
  // ── navigation ────────────────────────────────────────────────────────────────────
  // A migration is answered in the conversation it was started from. There is no page:
  // openChat() puts the transcript up, and MigrationRun renders under it.
  mgShow() {
    this.openChat();
    this.mgListLoad();
    if (this.state.mgId) this.mgPoll(true);
  },

  mgOpen(id) {
    const same = this.state.mgId === id;
    this.openChat();
    this.setState({
      mgId: id, mgError: '', mgArmed: false,
      mgStatus: same ? this.state.mgStatus : null,
      mgDec: same ? this.state.mgDec : {},
      mgOpenNodes: same ? this.state.mgOpenNodes : {}
    });
    if (!same) this._mgAdvanced = null;
    this.mgPoll(true);
  },
```

- [ ] **Step 4: Widen the poll's guard from one view to the conversation**

Two lines in `mgPoll`, both currently `this.state.view === 'migration'`:

```js
    // The status document runs to ~500 KB, so it is only re-read where it is rendered —
    // which is now wherever the conversation is: the chat page, or a page keeping its dock.
    if (this.chatView()) this._mgTimer = setTimeout(() => this.mgPoll(), 6000);
```

and

```js
    const delay = pollDelay(doc.status);
    if (delay && this.chatView()) this._mgTimer = setTimeout(() => this.mgPoll(), delay);
```

- [ ] **Step 5: Render the card in the transcript**

In `screens/Chat.jsx`, add the import beside the others:

```jsx
import MigrationRun from '../components/shell/MigrationRun.jsx';
```

and render it after the transcript's `.map(...)`, before the `vals.orchBusy` streaming block, so the open gate is what the follow-bottom scroll lands on:

```jsx
            {/* A migration started from this conversation is answered in it —
                every gate, up to and including the write. */}
            {vals.mgHasRun ? <MigrationRun vals={vals} /> : null}
```

- [ ] **Step 6: Run the tests and the build**

Run: `cd apps/frontend && node --test test/migration.test.mjs 2>&1 | tail -6 && npx vite build 2>&1 | tail -3`
Expected: the migration file passes; the build succeeds with `MigrationRun.jsx` now in the graph.

---

### Task 3: Starting a migration from the composer

**Files:**
- Modify: `apps/frontend/src/logic/complianceLive.js:1425-1431` (`orchSubmitNow`), `:1105` region (the reply's migration ids)
- Modify: `apps/frontend/src/logic/migration.js` (`mgFromChat`, and a new `mgStartFromChat`)
- Test: `apps/frontend/test/chatMigration.test.mjs` (new)

**Interfaces:**
- Consumes: `schemaMapperApi.start(files, cmms, orgId)` → `{migration_id}`; `isSpreadsheet(file)`.
- Produces: `mgStartFromChat()` — starts a run from `state.ccFiles`' spreadsheets, leaves a bot note in `ccChat`, opens the card. Called by `orchSubmitNow` and by the composer's "Migrate it" button.

- [ ] **Step 1: Write the failing tests**

Create `apps/frontend/test/chatMigration.test.mjs`, copying the harness header from `test/migration.test.mjs:1-45` (the `globalThis.window` / `fetch` mock and `const { HoistraLogic } = await import(...)`), then:

```js
test('spreadsheets staged with nothing typed start the run directly — no model is asked whether a .xlsx is a migration', async () => {
  handlers['POST /backend/schema-mapper/api/migration/start-with-upload'] = { migration_id: ID, status: 'running' };
  handlers[STATUS] = doc();
  c.setState({ view: 'chat' });
  c.ccAddFiles([new File(['a,b'], 'cmms_clean_test.xlsx')]);
  await c.orchSubmitNow();
  await settle();
  assert.ok(calls.some((x) => x.key.endsWith('/start-with-upload')), 'the upload went straight to schema-mapper');
  assert.ok(!calls.some((x) => x.key.includes('deep-agents')), 'the orchestrator was never consulted');
  assert.equal(c.state.mgId, ID);
  assert.deepEqual(c.state.ccFiles, [], 'the tray is cleared');
  const note = (c.state.ccChat || []).filter((m) => m.role === 'bot').pop();
  assert.match(note.text, /cmms_clean_test\.xlsx/, 'the transcript says what was started');
});

test('a spreadsheet sent WITH a question still goes to the orchestrator, and its run opens in the card', async () => {
  handlers['POST /backend/deep-agents/api/workflow/run-stateful-with-files'] = { session_id: 's1', answer: 'Paused at the primary-key gate.', tool_calls: [], success: true, error: null, ingested_migration_ids: [ID] };
  handlers[STATUS] = doc();
  c.setState({ view: 'chat', sessionId: null });
  c.ccAddFiles([new File(['a,b'], 'assets.csv')]);
  await c.ccAsk('migrate this and tell me what it found');
  await settle();
  const post = calls.find((x) => x.key.endsWith('/run-stateful-with-files'));
  assert.equal(post.body.get('interactive_migration'), 'true');
  assert.equal(c.state.mgId, ID, 'the returned run opens in the transcript, not behind a link');
});

test('a failed upload says so in the transcript instead of doing nothing', async () => {
  handlers['POST /backend/schema-mapper/api/migration/start-with-upload'] = null; // 404 from the mock
  c.setState({ view: 'chat' });
  c.ccAddFiles([new File(['a,b'], 'broken.csv')]);
  await c.orchSubmitNow();
  await settle();
  const note = (c.state.ccChat || []).filter((m) => m.role === 'bot').pop();
  assert.ok(note.error, 'the note is an error note');
  assert.match(note.text, /broken\.csv/);
  assert.equal(c.state.mgId, null);
});
```

- [ ] **Step 2: Run them to watch them fail**

Run: `cd apps/frontend && node --test test/chatMigration.test.mjs 2>&1 | grep -E "^not ok|# fail" | head`
Expected: all three fail — `orchSubmitNow` returns early on empty text, and the reply's ids only decorate a bubble.

- [ ] **Step 3: Start the run from the composer**

In `logic/migration.js`, replace `mgFromChat` with a version that starts rather than navigates:

```js
  // The chat's staged spreadsheets ARE the migration. Pressing send with nothing typed
  // starts it here: no model is asked whether a .xlsx is a migration, for the reason
  // chatCases.js gives about the held-document case — routing is what fails, and there is
  // nothing here to judge. PDFs and images stay in the tray for the next question.
  async mgStartFromChat() {
    const staged = (this.state.ccFiles || []).filter(isSpreadsheet);
    if (!staged.length || this.state.mgBusy) return;
    const names = staged.map((f) => f.name).join(', ');
    this.setState((p) => ({
      ccFiles: (p.ccFiles || []).filter((f) => !isSpreadsheet(f)),
      mgId: null, mgStatus: null, mgDec: {}, mgArmed: false, mgBusy: 'Uploading…', mgError: ''
    }));
    try {
      const r = await schemaMapperApi.start(staged, this.state.mgCmms || 'Custom', currentOrgId() || undefined);
      const id = r && r.migration_id;
      if (!id) throw new Error('the service accepted the upload but returned no migration id');
      this.setState((p) => ({
        mgBusy: '',
        ccChat: (p.ccChat || []).concat([{ role: 'bot', isNote: true, text: names + ' is migrating as run ' + shortId(id) + '. Every gate is below — nothing reaches plenum_cafm until the last one is confirmed.' }])
      }));
      this.mgOpen(id);
    } catch (e) {
      if (isStaleScope(e)) return this.setState({ mgBusy: '' });
      this.setState((p) => ({
        mgBusy: '',
        ccChat: (p.ccChat || []).concat([{ role: 'bot', isNote: true, error: true, text: names + ' did not start a migration: ' + ((e && e.message) || String(e)) }])
      }));
    }
  },

  // Kept for the dock's button, which no longer navigates either.
  mgFromChat() { return this.mgStartFromChat(); },
```

- [ ] **Step 4: Let an empty send with spreadsheets through**

In `logic/complianceLive.js`, `orchSubmitNow`:

```js
  orchSubmitNow() {
    const q = String(this.state.orchQuery || "").trim();
    // Nothing typed, but a spreadsheet staged: send starts the migration. An empty send
    // with nothing staged is still nothing.
    if (!q) return (this.state.ccFiles || []).some(isSpreadsheet) ? this.mgStartFromChat() : undefined;
    if (this.state.ccBusy) return this.flash("Still answering — stop it first, or wait for it to finish.");
    this.setState({ orchQuery: "" });
    this.askScoped(q);
  },
```

Add `isSpreadsheet` to that file's imports from `./migration.js`.

- [ ] **Step 5: Adopt the run the orchestrator started**

In `ccAsk`, immediately after the `migrations:` field is computed for the bot turn, open the first id so the gates render in place:

```js
    // A turn whose attachment started a migration opens it here. The reply used to carry a
    // link to a page; the page is gone, and the gates belong under the answer that made them.
    const started = (r && Array.isArray(r.ingested_migration_ids)) ? r.ingested_migration_ids.filter(Boolean).map(String) : [];
    if (started.length) this.mgOpen(started[0]);
```

- [ ] **Step 6: Run the new tests, then the whole suite**

Run: `cd apps/frontend && node --test test/chatMigration.test.mjs 2>&1 | tail -6 && npm test 2>&1 | tail -6`
Expected: the new file passes; the suite is green. The old assertion at `test/migration.test.mjs:445` that `orchMigrateHere()` sets `view: 'migration'` will now fail — rewrite it in Task 6 where the strip's label changes, or here if it blocks.

---

### Task 4: Listing runs, and the composer's migrate strip

**Files:**
- Modify: `apps/frontend/src/logic/migration.js` (a whole-message intent + `mgListCard`)
- Modify: `apps/frontend/src/logic/complianceLive.js` (`ccAsk`'s deterministic pre-step)
- Modify: `apps/frontend/src/logic/renderVals.js:1909-1912`, `apps/frontend/src/components/shell/OrchestratorDock.jsx:761-768`
- Test: `apps/frontend/test/chatMigration.test.mjs`

**Interfaces:**
- Consumes: `mgListLoad()` → `state.mgList`; `mgRecent[]` from `mgVals`.
- Produces: `mgIsListRequest(text)` → boolean (pure, exported for the test); a bot turn carrying `mgList: true` that `Chat.jsx` renders as the recent-runs card.

- [ ] **Step 1: Write the failing tests**

```js
import { mgIsListRequest } from '../src/logic/migration.js';

test('"migrations" lists the runs; a question about a migration does not', () => {
  assert.ok(mgIsListRequest('migrations'));
  assert.ok(mgIsListRequest('  My Migrations  '));
  assert.ok(mgIsListRequest('show my migrations'));
  assert.ok(mgIsListRequest('recent migrations'));
  assert.ok(!mgIsListRequest('why did that migration fail?'), 'a question is for the orchestrator');
  assert.ok(!mgIsListRequest('list the migrations that touched assets'), 'a qualified ask is a question');
});

test('asking for migrations answers from schema-mapper without calling the orchestrator', async () => {
  handlers['GET /backend/schema-mapper/api/migration'] = { total_count: 1, migrations: [{ migration_id: ID, cmms_name: 'Custom', status: 'awaiting_review', t1_count: 26, t2_count: 0, started_at: '2026-09-21T09:29:01Z' }] };
  c.setState({ view: 'chat' });
  await c.ccAsk('migrations');
  await settle();
  assert.ok(!calls.some((x) => x.key.includes('deep-agents')), 'the orchestrator was not asked');
  const turn = (c.state.ccChat || []).filter((m) => m.role === 'bot').pop();
  assert.ok(turn.mgList, 'the turn renders as the recent-runs card');
  assert.equal(c.renderVals().mgRecent.length, 1);
});
```

- [ ] **Step 2: Run them to watch them fail**

Run: `cd apps/frontend && node --test test/chatMigration.test.mjs 2>&1 | grep -E "^not ok|# fail" | head`
Expected: `mgIsListRequest is not a function`.

- [ ] **Step 3: Add the matcher and the answer**

In `logic/migration.js`, beside the other pure helpers:

```js
// A request to SEE the runs, matched on the whole normalised message and never a substring.
// "why did that migration fail?" contains the word and is a question for the orchestrator;
// the four phrases below are the only ones that mean "show me the list".
const MG_LIST = new Set(['migrations', 'my migrations', 'show my migrations', 'show me my migrations', 'recent migrations', 'list migrations']);
export const mgIsListRequest = (text) => MG_LIST.has(String(text || '').trim().toLowerCase().replace(/[.!?]+$/, ''));
```

and the method:

```js
  async mgAnswerList() {
    await this.mgListLoad();
    const n = (this.state.mgList || []).length;
    this.setState((p) => ({
      ccChat: (p.ccChat || []).concat([{
        role: 'bot', mgList: true,
        text: n ? 'The runs this company has started. Open one to pick its gates back up.' : 'No migrations yet. Attach a CMMS export below and press send.'
      }])
    }));
  },
```

- [ ] **Step 4: Route it in `ccAsk`, beside the held-document rule**

Immediately after the `ccCaseShouldAnswer` block (which already establishes the pattern of deciding a message here rather than in the router), and only when no files are staged:

```js
    // "migrations" is a request to see the list, not a question about one. Answered here
    // for the same reason the held case is: there is nothing to judge.
    if (!files.length && mgIsListRequest(q)) {
      await this.mgAnswerList();
      return this.setState({ ccBusy: false });
    }
```

- [ ] **Step 5: Render the card and relabel the strip**

In `screens/Chat.jsx`, inside the bot branch, after the `m.migShow` block:

```jsx
                        {m.mgList ? (
                          <div style={{ display: "flex", flexDirection: "column", gap: "6px", marginTop: "10px" }}>
                            {(vals.mgRecent || []).map((r) => (
                              <button key={r.id} type="button" className="hv13" onClick={r.open} style={{ ...BARE, display: "flex", alignItems: "center", gap: "10px", padding: "7px 10px", borderRadius: "8px", border: "1px solid " + (r.active ? "var(--color-accent)" : "var(--color-divider)"), textAlign: "left" }}>
                                <span style={{ fontFamily: "ui-monospace,monospace", fontSize: "10.5px", color: "var(--color-neutral-500)" }}>{r.short}</span>
                                <span style={{ fontSize: "11.5px" }}>{r.cmms}</span>
                                <span style={{ flex: "1" }}></span>
                                <span style={{ fontSize: "10.5px", color: r.tone }}>{r.status}</span>
                                <span style={{ fontSize: "10.5px", color: "var(--color-neutral-500)" }}>{r.when}</span>
                              </button>
                            ))}
                          </div>
                        ) : null}
```

In `renderVals.js:1909-1912` and `OrchestratorDock.jsx:761-768`, change the strip's copy from "Open on the Migration page" to "Migrate it" and its blurb to "Spreadsheets run through the migration pipeline. Send with nothing typed and the run opens here at its first gate." `orchMigrateHere` now points at `mgStartFromChat`.

- [ ] **Step 6: Run the tests**

Run: `cd apps/frontend && node --test test/chatMigration.test.mjs 2>&1 | tail -6 && npm test 2>&1 | tail -6`
Expected: green.

---

### Task 5: Hoisting a building from the chat, and assigning it

**Files:**
- Modify: `apps/frontend/src/logic/buildingsCrud.js:133-143` (`bcOpenForm`), plus a whole-message intent
- Modify: `apps/frontend/src/logic/complianceLive.js` (`ccAsk`'s deterministic pre-step)
- Modify: `apps/frontend/src/screens/Chat.jsx` (render `HoistBuildingCard`)
- Test: `apps/frontend/test/buildingsCrud.test.mjs`

**Interfaces:**
- Consumes: `adminApi.listUsers()` → `{users: [{id, full_name, email, buildings: [{id}]}]}`; `adminApi.patchUser(id, {building_ids})`.
- Produces: `bcIsHoistRequest(text)` → boolean (pure, exported).

- [ ] **Step 1: Write the failing tests**

In `test/buildingsCrud.test.mjs`:

```js
import { bcIsHoistRequest } from '../src/logic/buildingsCrud.js';

test('"hoist a building" opens the form; a question about buildings does not', () => {
  assert.ok(bcIsHoistRequest('hoist a building'));
  assert.ok(bcIsHoistRequest('Hoist Building'));
  assert.ok(bcIsHoistRequest('add a building'));
  assert.ok(bcIsHoistRequest('new building'));
  assert.ok(!bcIsHoistRequest('which building has the worst hoist score?'));
  assert.ok(!bcIsHoistRequest('hoist a building in Dubai with 24 floors'), 'a qualified instruction is a question for the orchestrator');
});

test('hoisting from the chat opens the card in the transcript, not the dock', async () => {
  c.setState({ view: 'chat' });
  await c.ccAsk('hoist a building');
  assert.ok(c.state.bcOpen, 'the form is open');
  assert.equal(c.state.orchOpen, false, 'the dock is not forced over the conversation');
  assert.equal(c.state.bcStep, 0);
});

test('assigning the new building appends it to the user\'s allocation and never replaces it', async () => {
  handlers['GET /backend/ops-intelligence/api/admin/users'] = { users: [
    { id: 'u-7', full_name: 'Marcus Hale', email: 'm@h.c', buildings: [{ id: 'b-old-1' }, { id: 'b-old-2' }] }
  ] };
  handlers['PATCH /backend/ops-intelligence/api/admin/users/u-7'] = { ok: true, user_id: 'u-7', changed: ['building_ids'] };
  c.setState({ bcResult: { buildingId: 'b-new', name: 'Bishopsgate Tower', code: 'B-01' }, bcStep: 2 });
  await c.bcUsersLoad();
  c.bcSetAssignUser('u-7');
  await c.bcAssignSubmit();
  await settle();
  const patch = calls.find((x) => x.key === 'PATCH /backend/ops-intelligence/api/admin/users/u-7');
  assert.deepEqual(JSON.parse(patch.body).building_ids, ['b-old-1', 'b-old-2', 'b-new'],
    'the two buildings they already held survive the assignment');
  assert.equal(c.state.bcAssignedTo, 'Marcus Hale');
});

test('assigning the same building twice does not duplicate it', async () => {
  handlers['GET /backend/ops-intelligence/api/admin/users'] = { users: [{ id: 'u-7', full_name: 'Marcus Hale', email: 'm@h.c', buildings: [{ id: 'b-new' }] }] };
  handlers['PATCH /backend/ops-intelligence/api/admin/users/u-7'] = { ok: true };
  c.setState({ bcResult: { buildingId: 'b-new', name: 'Bishopsgate Tower' }, bcStep: 2 });
  await c.bcUsersLoad();
  c.bcSetAssignUser('u-7');
  await c.bcAssignSubmit();
  await settle();
  const patch = calls.find((x) => x.key.startsWith('PATCH'));
  assert.deepEqual(JSON.parse(patch.body).building_ids, ['b-new']);
});
```

- [ ] **Step 2: Run them to watch them fail**

Run: `cd apps/frontend && node --test test/buildingsCrud.test.mjs 2>&1 | grep -E "^not ok|# fail" | head`
Expected: `bcIsHoistRequest is not a function`, and the chat test fails because nothing routes the phrase. The two assignment tests should PASS on the existing implementation — they are regression cover for behaviour that is already right but was untested.

- [ ] **Step 3: Add the matcher**

In `logic/buildingsCrud.js`, beside `HOIST_ROLES`:

```js
// The whole message, or it is a question. "hoist a building in Dubai with 24 floors" is an
// instruction the form cannot take — it is sent to the orchestrator, which can ask about it.
const BC_HOIST = new Set(['hoist a building', 'hoist building', 'add a building', 'add building', 'new building', 'hoist a new building']);
export const bcIsHoistRequest = (text) => BC_HOIST.has(String(text || '').trim().toLowerCase().replace(/[.!?]+$/, ''));
```

- [ ] **Step 4: Keep the dock shut when the conversation is the page**

`bcOpenForm` calls `orchWith`, which opens the dock. On the chat page the card belongs in the transcript:

```js
  bcOpenForm() {
    const patch = {
      bcMode: 'create', bcTarget: null, bcStep: 0, bcResult: null,
      bcOpen: true, bcForm: Object.assign({}, BLANK), bcMix: BLANK_MIX.map((m) => Object.assign({}, m)),
      bcErrors: {}, bcWarnings: [], bcSaving: false, bcTopError: '', bcSiteOpen: false, bcSiteQuery: ''
    };
    // On the chat page the conversation IS the surface: the form renders in the transcript
    // and the dock stays shut. Everywhere else it opens the dock exactly as before.
    if (this.state.view === 'chat') {
      this.setState(Object.assign({ flow: 'declare', flowDone: '' }, patch));
    } else {
      this.ccChatReset();
      this.orchWith('Hoist building', this.ctxLabel(), 'declare', patch);
    }
    this.bcSitesLoad();
  },
```

- [ ] **Step 5: Route the phrase and render the card**

In `ccAsk`, beside the migrations rule from Task 4:

```js
    if (!files.length && bcIsHoistRequest(q)) {
      this.bcOpenForm();
      return this.setState({ ccBusy: false });
    }
```

In `screens/Chat.jsx`, import `HoistBuildingCard` and render it above `MigrationRun`:

```jsx
            {vals.bcOpen ? <HoistBuildingCard vals={vals} /> : null}
```

- [ ] **Step 6: Run the tests and the build**

Run: `cd apps/frontend && node --test test/buildingsCrud.test.mjs 2>&1 | tail -6 && npx vite build 2>&1 | tail -3`
Expected: green, build succeeds.

---

### Task 6: Delete the page

**Files:**
- Delete: `apps/frontend/src/screens/Migration.jsx`
- Modify: `apps/frontend/src/App.jsx:18,64`, `apps/frontend/src/logic/integrations.js:104-111`, `apps/frontend/src/logic/session.js:41`, `apps/frontend/src/logic/core.js:49,251`, `apps/frontend/src/logic/migration.js` (`mgVals.isMigration`, the upload-panel keys)
- Test: `apps/frontend/test/migration.test.mjs:282,445`

**Interfaces:**
- Produces: no `migration` view anywhere in the app; `navAdmin` is `['Integrations', 'Users & access', 'Audit trail']`.

- [ ] **Step 1: Rewrite the two tests that assert the page**

Replace `test/migration.test.mjs:282` and `:445`:

```js
test('Migration has no nav entry of its own — the conversation is where a run is answered', () => {
  const admin = c.renderVals().navAdmin.map((a) => a.label);
  assert.deepEqual(admin, ['Integrations', 'Users & access', 'Audit trail']);
  assert.ok(!c.renderVals().navSections.some((n) => n.label === 'Migration'));
});

test('a chat reply that started a migration opens it in the card, and a staged spreadsheet offers to migrate', async () => {
  handlers[STATUS] = doc();
  c.setState({ view: 'chat', ccChat: [{ role: 'you', text: 'migrate this' }, { role: 'bot', text: 'Paused at the primary-key gate.', migrations: [ID] }] });
  let v = c.renderVals();
  assert.equal(v.orchChat[1].migShow, 'flex');
  assert.equal(v.orchMigrateShow, 'none');

  c.ccAddFiles([new File(['a'], 'assets.csv'), new File(['b'], 'cert.pdf')]);
  v = c.renderVals();
  assert.equal(v.orchMigrateShow, 'flex');
  handlers['POST /backend/schema-mapper/api/migration/start-with-upload'] = { migration_id: ID };
  await v.orchMigrateHere();
  await settle();
  assert.equal(c.state.view, 'chat', 'there is no page to go to');
  assert.equal(c.state.mgId, ID);
  assert.deepEqual(c.state.ccFiles.map((f) => f.name), ['cert.pdf'], 'the PDF stays with the chat');
});
```

- [ ] **Step 2: Run them to watch them fail**

Run: `cd apps/frontend && node --test test/migration.test.mjs 2>&1 | grep -E "^not ok|# fail" | head`
Expected: the nav assertion fails — "Migration" is still in `navAdmin`.

- [ ] **Step 3: Delete the page and every reference**

```bash
cd apps/frontend
rm src/screens/Migration.jsx
```

- `App.jsx`: remove `import Migration from './screens/Migration.jsx';` and the `{vals.isMigration ? <Migration vals={vals} /> : null}` line.
- `logic/integrations.js`: remove the whole `{ label: "Migration", … }` object from `navAdmin`.
- `logic/session.js:41`: remove `'migration'` from `VIEWS`.
- `logic/core.js:49`: remove the `if (this.state.view === "migration") { … }` branch (the chat's own hook already re-polls). `core.js:251`: remove the `ctxLabel` branch.
- `logic/migration.js`: drop `isMigration` and the upload-panel-only keys (`mgFilesEmpty`, `mgFilesNote`, `mgPickFiles`, `mgDropFiles`, `mgCanStart`, `mgStartLabel`, `mgOrg`) from `mgVals`; keep `mgCmms`/`mgSetCmms` (the composer strip uses them) and every gate key.

- [ ] **Step 4: Prove nothing still points at the page**

Run:
```bash
cd apps/frontend && grep -rn "Migration.jsx\|isMigration\|view === 'migration'\|view === \"migration\"" src/ | grep -v node_modules
```
Expected: no output.

- [ ] **Step 5: Run everything**

Run: `cd apps/frontend && npm test 2>&1 | tail -6 && npx vite build 2>&1 | tail -3`
Expected: `fail 0` with the count at or above 777 (new tests added, two rewritten), and a clean build.

---

### Task 7: Live verification against the throwaway stack

**Files:**
- Create: `/private/tmp/claude-501/-Users-hussain-Desktop-hoist/28640ce5-22ad-4b5e-b913-482c72de66f0/scratchpad/live-migration.mjs` (a driver, not a repo file)
- Uses: `docker-compose.testenv.yml` stack already running on `:3010`

- [ ] **Step 1: Start the dev server against the throwaway stack**

```bash
cd apps/frontend && VITE_DEV_PROXY_TARGET=http://localhost:3010 PORT=5175 npm run dev
```

Never `:3000` — that gateway is pointed at the Azure production database.

- [ ] **Step 2: Build the fixture**

A five-sheet workbook matching the shapes the gate fixtures use (Sites, Assets, Vendors, Resources, WorkOrders), written to the scratchpad. 50 rows is enough to exercise every gate.

- [ ] **Step 3: Drive it with playwright-core against installed Chrome**

Sign in against the testenv stack (its `AUTH_JWT_SECRET` is a fixed dev value, documented at the top of `docker-compose.testenv.yml`), open the Orchestrator, attach the workbook, press send with nothing typed, then answer each gate as it appears — screenshot at every one.

- [ ] **Step 4: Confirm the write**

At the final gate, arm and confirm. This writes into the throwaway postgres, which is the point of using it.

- [ ] **Step 5: Hoist a building and assign it, in the same conversation**

Type `hoist a building`, fill the form, save, reach step 3, pick a user, assign. Confirm the user's other buildings survive by re-reading `/api/admin/users`.

- [ ] **Step 6: Report with evidence**

Screenshots per gate, the run id, the row counts written, and the user's `building_ids` before and after. Anything that failed is reported as failed, with the output.
