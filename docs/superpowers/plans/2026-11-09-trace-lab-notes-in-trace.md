# Notes in the Trace View Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The question page shows the answer only as the Langfuse-style trace, and every note (on a step's input or output, quoting selected text, edit, delete, comment on the question) is written in the trace with today's step keys.

**Architecture:** A pure `logic/traceNotes.js` maps trace nodes ↔ today's note keys and indexes notes per node side. `TraceView` gets one optional `annotate` prop (note counts on rows, a notes slot under Input / Output, selection and double-click hooks). `QuestionPage` loads the tree with the question, drops the Answer view and the tab bar, and drives the existing note editor state through `annotate`. Then a Hoistra-theme design pass.

**Tech Stack:** React 18 JSX, `node --test` + esbuild helper + `react-dom/server`; CSS with the existing tokens.

**Spec:** `docs/superpowers/specs/2026-11-09-trace-lab-notes-in-trace-design.md` (and `2026-11-09-trace-lab-trace-tab-design.md` for the trace itself)

## Global Constraints

- Frontend only (`apps/frontend/src/lab/`). No backend change; no change to Labels / Notes / Learning / Loop / Progress / Runs list / People / Synthetic.
- Note keys exactly as the spec's Keys table: message id (input) / `+ '#output'`; agents `<turn>:<span>:agent`; `chat` input = the `user` message id, `chat` output = the answer (`assistant`) message id; question comment `<turn>:scenario`.
- New note titles: `Input · <name>` / `Output · <name>`; the question comment keeps `SCENARIO_TITLE`.
- Selecting text inside Input / Output opens the note editor with the quote (today's `blockUp`); double-click opens it without one (today's `blockDouble`); no Right / Wrong anywhere.
- No new dependencies; light theme; existing CSS variables. Nothing committed (Hussain commits on request).
- Test command: `cd apps/frontend && node --test src/lab/__tests__/<file>.test.mjs`; whole lab: `node --test src/lab/__tests__/*.test.mjs`.

## Review Focus

1. **A note whose key matches no node** (`<turn>:orchestrator`, an old agent key whose span is gone, a purged answer): it must still show, under `chat` → "Other notes". Test: Task 1 `unmatched notes go to other`.
2. **Two people's notes on the same side, one edited, one deleted (cleared)**: cleared ones vanish, the caller's first, then newest. Test: Task 1 `cleared notes are dropped and mine come first`.
3. **The answer can't be read (missing) or the tree fails to load**: the page still shows the question, expected answer, answer picker and comment box, with a plain message where the trace goes. Test: Task 3 `the page without a trace still works`.
4. **A step that has no message of its own** (the `turn` span is `chat`; an agent has its `:agent` key): `nodeKeys` never returns `undefined` for a node that can take notes, and returns null for neither side of `chat`. Test: Task 1 `every node has keys`.
5. **The Raw / Formatted switch while notes are open**: notes stay attached to their side. Test: Task 2 `notes stay under their side in raw mode`.

---

### Task 1: Note keys and the notes index for the trace

**Files:** Create `apps/frontend/src/lab/logic/traceNotes.js`; Test `apps/frontend/src/lab/__tests__/traceNotes.test.mjs`

**Interfaces:**
- Consumes: a normalized record (`active.trace`: `id`, `messages[{id, role, subtype?, span_id}]`, `groups[{span_id}]`), tree nodes (`id`, `parent_id`, `type`, `name`), stored labels (`step_key`, `cleared`, `reviewer_id`, `at`, …).
- Produces:
  - `nodeKeys(record, nodes) -> Map<nodeId, { in: string|null, out: string|null, inTitle: string, outTitle: string }>`: the root (`parent_id == null`) → `in` = the `user` message id, `out` = the answer message id; an `agent` node → `<turn>:<span>:agent` and `+ '#output'`; any other node → the first message with `span_id === node.id` and `+ '#output'`, else `{in: null, out: null}`.
  - `traceNotesIndex(labels, record, nodes, me, viewOf) -> { slots: { [nodeId + ':in' | nodeId + ':out']: note[] }, scenario: note[], other: note[], counts: { [nodeId]: number } }` where `viewOf(label, me)` is `QuestionPage`'s `noteView` (passed in, so this module imports nothing from the page). Placement: `<turn>:scenario` → scenario; strip `#output` → side out; message id → its span's node (user → root in, answer → root out); `<turn>:<span>:agent` → that node; anything else → other. Cleared labels dropped; each list mine first, then newest. `counts` counts both sides (and `other` on the root).
  - `keyTitles(keysMap) -> { [key]: title }` for the editor's target.

- [ ] **Step 1: Write the failing tests** — `every node has keys`, `old and new notes land on their step and side`, `unmatched notes go to other`, `cleared notes are dropped and mine come first`, `counts per node`.
- [ ] **Step 2: Run** — Expected: `Cannot find module …/traceNotes.js`.
- [ ] **Step 3: Implement** the three exports.
- [ ] **Step 4: Run** — Expected: `pass 5`.
- [ ] **Step 5: Checkpoint** (no commit).

### Task 2: `annotate` in TraceView

**Files:** Modify `apps/frontend/src/lab/TraceTab.jsx`, `apps/frontend/src/lab/traceTab.css`; Test `apps/frontend/src/lab/__tests__/traceTab.test.mjs`

**Interfaces:**
- Consumes: Task 1 shapes.
- Produces: `TraceView({ tree, view, selectedId, pane, mode, annotate })` where `annotate` is optional `{ keyOf(nodeId, side) -> string|null, count(nodeId) -> number, render(nodeId, side) -> ReactNode, up(key, el, event), double(key, event), other() -> ReactNode }`. With it: a note-count badge (`lab-tt-ncount`, aria-label "N notes") on tree and timeline rows; under Input and under Output the `render(...)` output (when that side has a key); `onMouseUp` / `onDoubleClick` on each value container call `up` / `double` with that side's key; on the root, `other()` under Output. Without `annotate` the view is exactly today's.
- Remove the default `TraceTab` loader export (Task 3 loads the tree in the page).

- [ ] **Step 1: Failing tests** — `note counts show on rows`, `each side renders its notes slot and wires selection`, `notes stay under their side in raw mode`, `without annotate nothing changes`. Remove the old tab-switch tests and the "Reading…" assertion (their behaviour is deleted by this plan).
- [ ] **Step 2: Run** — Expected: the new tests fail. **Step 3: Implement.** **Step 4: Run** — whole `traceTab.test.mjs` passes. **Step 5: Checkpoint.**

### Task 3: The question page shows only the trace, with notes

**Files:** Modify `apps/frontend/src/lab/QuestionPage.jsx`; Test `apps/frontend/src/lab/__tests__/runs.test.mjs` (replace the two block-view tests), `traceTab.test.mjs` (page tests)

**Interfaces:**
- Consumes: Tasks 1–2; `labApi.qTree`; the page's existing handlers (`openEditor`, `blockUp`, `blockDouble`, `editorText`, `editorSave`, `editorCancel`, `editNote`, `askDelete`, `deleteNote`, `saveScenario`).
- Produces: `QuestionPage.load` also reads `labApi.qTree(code, turnId)` after `labApi.q` (for the active, readable answer) into `tree` state `{ loading, error, out }`; `QuestionView` takes `tree` (the `{turn_id, missing, tree}` response, or `{loading:true}` / `{error}`) and renders, in place of the old legend + block list + Other notes: `TraceView` with `annotate` built from `traceNotesIndex`, `nodeKeys`, the existing `NoteList` / `NoteEditor` (an **Add note** button per side, the editor where `editor.block === key`, errors by key). `targetOf(key)` reads `keyTitles`. Removed: `QTABS`, the tab bar, `initialTab` / `keepTab`, `LEGEND` use, `BlockRow` and anything only it used.

- [ ] **Step 1: Failing tests** — `a question: header, expected answer, then the trace with notes under their steps` (header/expected/links/comment-box assertions kept from the old test; notes from `LABELS` under the right node side; no `lab-tl`, no tab bar, no Right/Wrong), `the note editor opens under its side with the selected text`, `the page without a trace still works` (missing / loading / error).
- [ ] **Step 2: Run** — fail. **Step 3: Implement.** **Step 4: Run** the whole lab suite: all pass. **Step 5: Checkpoint.**

### Task 4: Hoistra-theme design pass

**Files:** `apps/frontend/src/lab/traceTab.css`, small markup touches in `TraceTab.jsx` / `QuestionPage.jsx` only.

- [ ] **Step 1:** Load the `impeccable` skill (polish), read Hoistra's tokens (`src/styles/tokens.css`, `base.css`) and Trace Lab's `lab.css` / `runs.css`.
- [ ] **Step 2:** Apply one batch of refinements (type scale, spacing rhythm, icon colours from tokens, selected/hover states, note badges, notes under Input / Output, focus rings), no behaviour change.
- [ ] **Step 3:** One screenshot round (desktop 1440 + phone 390) on the harness with the real R35-6 tree and its notes; fix what it shows; at most one more round.
- [ ] **Step 4:** Whole lab suite passes. **Step 5: Checkpoint.**

### Task 5: Verify

- [ ] Whole frontend suite (`npm test`): all pass except the known TZ test in `test/tracesPage.test.mjs`.
- [ ] The :5174 dev server serves the new `QuestionPage.jsx` (no rebuild needed; frontend only).
- [ ] Report to Hussain with screenshots; no commit.
