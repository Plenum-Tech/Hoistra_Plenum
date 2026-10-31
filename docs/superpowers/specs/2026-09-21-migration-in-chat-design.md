# Migration in the chat — the page is deleted and the run is answered where it was started

**Date:** 2026-09-21
**Scope:** `apps/frontend` only. `svc-ai-schema-mapper`'s gate contract is taken exactly as `logic/migration.js` already implements it.

> Service changes were made *around* this work, not as part of it: three backend bugs surfaced
> only once the chat drove the pipeline end to end (the ARQ resume injecting a live session
> into checkpointed state, the worker never publishing its API clients, and an insert relying
> on a column default production does not have). They are listed in the plan's closing notes.

## Goal

A CMMS export is migrated from the Orchestrator conversation. The nine nodes, their human
gates and the write confirmation all render inside the transcript, under the message whose
attachment started them. `screens/Migration.jsx` and the nav entry that reached it are
deleted; nothing that page did is lost, because everything it did was already a view model
on the controller.

Hoisting a building is reachable from the same conversation — typed, not clicked from a
page — and the person who hoists it is offered the building's assignment to a user on the
step that already exists for it.

The reference is the Plenum CAFM orchestrator (`localhost:3000/ai`): completed nodes stay
on screen as collapsed snapshots, the open gate sits at the bottom of the card, and the run
is identified by its short id.

## Decisions taken with Hussain

| Question | Decision |
|---|---|
| Where the gates render | **The Chat page transcript only.** The side dock keeps a compact "migration open · continue in chat" card — a 29-column gate table is not answerable at dock width. |
| How a run is created | **Deterministically from the attachment.** Spreadsheets staged with no question start a run without a model deciding whether a `.xlsx` is one. (Which endpoint that goes to changed during the build — see *Starting a run*.) |
| Reopening a run | **Auto-restore plus a typed `migrations` list.** `mgId` is already persisted by `session.js`; the recent-runs list becomes a chat card. No nav entry survives. |
| Live verification | Planned against the throwaway stack on `:3010`; **that turned out to be impossible** — its schema-mapper has no Anthropic key, so `get_anthropic_client()` raises and a run never leaves node 0 (see [[testenv-stack-limits]]). Verified instead against a scripted stub backend in a real browser, plus 40 real production status documents replayed through the view model read-only. |
| Rendering model | **One card fed by the existing `mgVals` keys**, not a gate-per-transcript-turn. The reference needed three `sessionStorage` keys per run to keep gate snapshots across turns; `mgNodes` already carries them. |

## What is deleted

| File / site | Action |
|---|---|
| `src/screens/Migration.jsx` | Deleted (575 lines). Its gate tables move to `MigrationRun.jsx`. |
| `src/App.jsx:18,64` | Import and `vals.isMigration` branch removed. |
| `src/logic/integrations.js:105-110` | The "Migration" admin nav item removed, badge and all. |
| `src/logic/session.js:41` | `'migration'` removed from `VIEWS`; a stored session naming it restores onto the chat with its run open, rather than a view that no longer exists. |
| `src/logic/core.js:49,251` | The `view === "migration"` load hook and `ctxLabel` branch removed. |
| `src/logic/migration.js` — `mgVals.isMigration`, `mgRecentReload`'s page framing | The page-only keys go; every gate key stays. |

## What does not change

`src/logic/migration.js` keeps its engine intact: `NODES`, `defaultGateBody` (whose shapes
are held to the service's handlers by `test/migration.test.mjs`), `pollDelay`, `runKind`,
`shapeNodes`, auto-advance, and the two-step arm/confirm on the write gate. `api/schemaMapper.js`
is untouched. This is deliberate — the migration's correctness lives there, and this change
is about where it is answered, not how.

Three edits only, all about where the run is shown:

1. `mgShow()` / `mgOpen(id)` open the Orchestrator chat instead of setting `view: 'migration'`.
2. The poll's continuation guard `this.state.view === 'migration'` becomes "the chat is
   showing", so a run keeps polling where it is now rendered. The guard exists so a large
   status document is not re-read for a page nobody is looking at; that reasoning is
   unchanged, only its subject.
3. `mgFromChat()` no longer navigates; it starts the run in place.

## Architecture

### New file — `src/components/shell/MigrationRun.jsx`

The run card, rendered by `Chat.jsx` under the last turn. It reads only keys `mgVals()`
already returns, so the view model is not extended for it:

```
head      mgIdShort · mgFile · mgCmmsName · mgPill · mgStepLabel · mgProgress
nodes     mgNodes[]  — complete ones collapsed with their outcome, current one live
gate      mgGateTitle · mgGateBlurb · mgGateCount, then the gate's own table
          (pk / unique-table / pre-semantic / classification / column-mapping /
           field-mapping / hierarchy), all from the ...g spread
action    mgPrimary, or the write gate's mgArm → mgConfirmWrite / mgRejectWrite
after     mgOutputs[] when the run is done; mgError whenever it is set
```

Layout follows the deployed Migration page rather than a stack: the nine-node pipeline sits
in a column beside the open gate, under a head carrying the run's identity, its progress and
the auto-continue and Refresh controls. The conversation column is ~930px, so the tracker is
240px here against the page's 280px and the gate's own tables scroll horizontally when a
mapping has many columns; below 900px the two stack (`.mg-run-grid`).

### `src/screens/Chat.jsx`

Renders `<MigrationRun vals={vals} />` when `vals.mgHasRun`, after the transcript and before
the streaming-answer block, so the open gate is what the follow-bottom scroll lands on. Also
renders `<HoistBuildingCard vals={vals} />` when `vals.bcOpen`, which until now only the dock
did.

### `src/components/shell/OrchestratorDock.jsx`

The "Open on the Migration page" strip becomes "Migrate it", alongside the source-system
field the deleted page carried. The dock does not render gates.

### Starting a run — `logic/migration.js` + `logic/complianceLive.js`

> **Revised during the build (22 Sep).** This section first specified a direct
> `schemaMapperApi.start()` for the no-question case. That was wrong and was changed:
> `/api/migration/start-with-upload` takes file, cmms_name and organization_id and has
> nowhere to put a building, while svc-deepagents' upload route calls `bind_and_log`,
> `record_ingestion_audit` and `record_usage` with one — for a spreadsheet exactly as for a
> document (`workers/ingest_batch_worker.py`). Going direct meant a migration bound to
> nothing and no row in the ingestion audit trail. What follows is what shipped.

Both entries take the same route, and converge on one card:

- **Spreadsheets staged, nothing typed, send pressed** — `orchSubmitNow()` refuses an empty
  message; with only spreadsheets staged it calls `mgStartFromChat()` instead, which posts
  to `svc-deepagents`' `/run-stateful-with-files` with `interactive_migration`, the chosen
  building and the source system. No model is consulted about whether a `.xlsx` is a
  migration — that route splits files by TYPE — for the reason `chatCases.js` gives about
  the held-document case: routing is what fails, and there is nothing here to judge.
- **Spreadsheets staged with a question** — the same route, carrying the question too.

Either way the reply's `ingested_migration_ids` opens the card inline instead of rendering a
link to a page that no longer exists.

If that upload fails because the orchestrator is **definitively unreachable** — a dead
upstream, never a 4xx and never a timeout, since a timed-out run may still be completing and
a second upload would be a second migration — the run is started directly against the mapper
so a broken orchestrator cannot stop a migration outright, and the transcript says plainly
that this one carries no audit row.

Mixed attachments ride the one turn: the route migrates the spreadsheets and indexes the
documents in parallel.

### Listing runs — `logic/migration.js`

`migrations`, `my migrations`, `show my migrations`, `recent migrations` matched on the
**whole** normalised message (never a substring — "why did that migration fail?" is a
question for the orchestrator) render `mgRecent[]` as a transcript card. Each row opens
that run in place.

### Hoisting from the chat — `logic/buildingsCrud.js` + `logic/core.js`

- `hoist a building`, `hoist building`, `add a building`, `new building` — again whole-message
  matches — call `bcOpenForm()` rather than the orchestrator.
- `bcOpenForm()` / `bcOpenEdit(row)` currently call `orchWith(...)`, which opens the dock. On
  the chat page the card belongs in the transcript, so the dock is not opened when
  `view === 'chat'`; everywhere else behaves exactly as before.
- Step 3's assignment (`bcUsersLoad` → `bcSetAssignUser` → `bcAssignSubmit`) is already
  correct — it reads the picked user's current `building_ids` and appends, because
  `adminApi.patchUser` replaces the list wholesale. It is not rewritten; it is made
  reachable from the chat and covered by a test that fails if the append ever becomes a
  replace.

## Error handling

- A gate that the service refuses keeps the reader's decisions (`mgDec` is cleared only when
  the gate itself changes) and renders `mgError` inside the card.
- A status read that fails re-tries on the existing 6 s cadence while the chat is showing,
  and says so in the card rather than a toast.
- A migration started from an attachment whose upload fails leaves an error note in the
  transcript naming the file — not a silent no-op, which is what an empty send would
  otherwise look like.
- `isStaleScope(e)` handling is unchanged: switching company mid-run abandons the reads
  without an error message, as everywhere else.

## Testing

**Unit** — `node --test`, against the existing mocked-`fetch` controller harness. Baseline is
777 passing; nothing in it may go red.

In `test/migration.test.mjs`:
- a run keeps polling when the view is `chat` (the guard's new subject)
- `mgOpen` from a chat reply does not set a `migration` view
- every existing gate-body assertion still holds — the engine is untouched

New `test/chatMigration.test.mjs`:
- spreadsheets staged with no text start a run through `schemaMapperApi.start` and never
  reach `deepAgentsApi`
- a question with a spreadsheet still goes to deepagents with `interactiveMigration: true`,
  and the returned id opens the card
- `migrations` lists; `why did that migration fail?` does not
- `hoist a building` opens the form; `which building has the worst hoist score?` does not
- the assignment appends to the user's existing `building_ids` and never replaces them

**Live** — the dev server pointed at the throwaway stack:

```
VITE_DEV_PROXY_TARGET=http://localhost:3010 npm run dev
```

Driven with playwright-core against installed Chrome: attach a CMMS export in the
Orchestrator, answer all nine gates in the transcript, confirm the write, then hoist a
building from the same conversation and assign it to a user. Screenshots at each gate.
Nothing in this pass touches the production-pointed gateway on `:3000`.

## Out of scope

- "Restart from Node 1" and saved versions, which the reference has and Hoistra has never had.
- Any change to `svc-ai-schema-mapper`.
- The dock rendering gates at dock width.
