// chatMigration — a CSV / Excel migration started, listed and answered in the conversation.
//
// The Migration page is gone. What used to be "attach a file, press send, follow a link to
// a page" is now "attach a file, press send, answer the gates where you are", so the paths
// that decide WHAT a message is have to be tested where they now live: in the composer,
// not in a router.
//
// Two rules are asserted here more than once, because both were learned the hard way in
// logic/chatCases.js:
//
//   A SPREADSHEET IS NOT A QUESTION. Sending one with nothing typed starts a migration
//   directly. No model is asked whether a .xlsx is a migration — there is nothing to judge,
//   and the one time routing was left to the model it answered "the file cannot be found".
//
//   A WORD IS NOT AN INSTRUCTION. "migrations" lists the runs; "why did that migration
//   fail?" contains the word and is a question. Every typed intent here matches the WHOLE
//   normalised message, never a substring.
import { test, beforeEach, afterEach } from 'node:test';
import assert from 'node:assert/strict';

const mem = {};
globalThis.window = {
  location: { origin: 'http://test.local' }, scrollTo: () => {},
  addEventListener: () => {}, removeEventListener: () => {},
  localStorage: { getItem: (k) => (k in mem ? mem[k] : null), setItem: (k, v) => { mem[k] = String(v); }, removeItem: (k) => { delete mem[k]; } },
  sessionStorage: { getItem: () => null, setItem: () => {}, removeItem: () => {} }
};
globalThis.WebSocket = class { constructor() { throw new Error('no sockets in tests'); } };

let calls, handlers;
globalThis.fetch = async (url, opts) => {
  const u = new URL(String(url));
  const method = (opts && opts.method) || 'GET';
  const key = method + ' ' + u.pathname;
  calls.push({ key: key, body: opts && opts.body, query: u.search });
  const h = handlers[key];
  if (!h) return { ok: false, status: 404, statusText: '404', text: async () => '{"detail":"not mocked"}' };
  const out = typeof h === 'function' ? h(opts) : h;
  return { ok: true, status: 200, statusText: 'OK', text: async () => JSON.stringify(out) };
};

const { HoistraLogic } = await import('../src/logic/HoistraLogic.js');
const { mgIsListRequest } = await import('../src/logic/migration.js');

const settle = () => new Promise((r) => setTimeout(r, 40));
const ID = '2f9f3738-016d-4a5b-bd7b-0971c4b13e6d';
const STATUS = 'GET /backend/schema-mapper/api/migration/' + ID + '/status';
const START = 'POST /backend/schema-mapper/api/migration/start-with-upload';
const RUN_FILES = 'POST /backend/deep-agents/api/workflow/run-stateful-with-files';

const doc = (over) => Object.assign({
  migration_id: ID, status: 'awaiting_review', progress_pct: 0, current_step: 1,
  cmms_name: 'Custom', source_filename: 'cmms_clean_test.xlsx', started_at: '2026-09-21T09:29:01Z',
  t1_mapped_count: 26, t2_auto_count: 0, t2_human_count: 2, unmapped_count: 0, total_fields: 29,
  pending_gate_type: 'pk_approval',
  pending_gate_payload: { gate: 'pk_approval', pk_confirmation: { Sites: { kind: 'natural', surrogate: false, confidence: 1, detected_pk: ['site_id'], columns: [{ column: 'site_id', uniqueness: 1, null_rate: 0, qualifies: true }] } } },
  error_message: null,
  nodes: [{ node_id: 1, node_name: 'File Ingestion', status: 'complete', outcome: 'Ingested 5 tables, 50 rows', duration_ms: 5412, logs: [] }]
}, over || {});

let c;
beforeEach(() => {
  Object.keys(mem).forEach((k) => { delete mem[k]; });
  calls = []; handlers = {};
  c = new HoistraLogic();
  c.setState({ signedIn: true, role: 'admin', view: 'chat', account: { id: 'u1', email: 'a@b.c', role: 'admin', status: 'active' } });
});
afterEach(() => {
  clearTimeout(c._mgTimer); clearTimeout(c._tt);
  clearInterval(c._orchTick); clearInterval(c._ccTick);
});

// ── starting a run ──────────────────────────────────────────────────────────────────

test('spreadsheets staged with nothing typed start a run through the route that records it', async () => {
  handlers[RUN_FILES] = { session_id: 's1', answer: 'Paused at the primary-key gate.', tool_calls: [], success: true, ingested_migration_ids: [ID] };
  handlers[STATUS] = doc();
  c.ccAddFiles([new File(['a,b'], 'cmms_clean_test.xlsx')]);
  await c.orchSubmitNow();
  await settle();
  const post = calls.find((x) => x.key === RUN_FILES);
  assert.ok(post, 'the upload went through the orchestrator, which is what writes the audit row');
  assert.equal(post.body.get('interactive_migration'), 'true', 'and it stops at the first gate');
  assert.ok(!calls.some((x) => x.key === START), 'not straight to the mapper, which cannot record a building');
  assert.equal(c.state.mgId, ID, 'the card opens at gate 1');
  assert.deepEqual(c.state.ccFiles, [], 'the tray is cleared');
});

// The building the composer has chosen must reach the route, because that is the whole
// reason this goes through the orchestrator: bind_and_log / record_ingestion_audit /
// record_usage all take it, for a spreadsheet exactly as for a document.
test('the chosen building travels with a migration upload', async () => {
  handlers[RUN_FILES] = { session_id: 's1', answer: 'Paused.', tool_calls: [], success: true, ingested_migration_ids: [ID] };
  handlers[STATUS] = doc();
  c.setState({ cbBuildingId: 'b-77', account: { id: 'u1', role: 'admin', status: 'active', buildings: [{ id: 'b-77', name: 'Bishopsgate Tower' }] } });
  c.ccAddFiles([new File(['a,b'], 'cmms.xlsx')]);
  await c.orchSubmitNow();
  await settle();
  const post = calls.find((x) => x.key === RUN_FILES);
  assert.equal(post.body.get('building_id'), 'b-77');
});

test('an unreachable orchestrator falls back to the mapper and says what that costs', async () => {
  // A dead upstream, not a refusal: the browser never gets an answer at all. A 404 is a
  // reply and must NOT trigger the fallback — only "nothing happened" may.
  handlers[RUN_FILES] = () => { throw new Error('Failed to fetch'); };
  handlers[START] = { migration_id: ID, status: 'running' };
  handlers[STATUS] = doc();
  c.ccAddFiles([new File(['a,b'], 'cmms.xlsx')]);
  await c.orchSubmitNow();
  await settle();
  assert.ok(calls.some((x) => x.key === START), 'the run still starts');
  assert.equal(c.state.mgId, ID);
  const note = (c.state.ccChat || []).filter((m) => m.isNote).pop();
  assert.match(note.text, /ingestion audit trail/, 'and the missing audit row is stated, not hidden');
});

test('a TIMEOUT is never retried — a second upload would be a second migration', async () => {
  handlers[RUN_FILES] = () => { const e = new Error('the request timed out'); throw e; };
  handlers[START] = { migration_id: ID, status: 'running' };
  c.ccAddFiles([new File(['a,b'], 'cmms.xlsx')]);
  await c.orchSubmitNow();
  await settle();
  assert.ok(!calls.some((x) => x.key === START), 'the file was NOT sent again');
  assert.equal(c.state.mgId, null);
});

test('a PDF staged alone with nothing typed is still not a question — nothing is sent', async () => {
  c.ccAddFiles([new File(['x'], 'eicr.pdf')]);
  await c.orchSubmitNow();
  await settle();
  assert.deepEqual(calls, [], 'an empty send with no spreadsheet does nothing at all');
  assert.equal(c.state.ccFiles.length, 1, 'the PDF is still staged for the question that will come');
});

test('a mixed tray goes as one turn — the spreadsheet migrates, the document is indexed beside it', async () => {
  handlers[RUN_FILES] = { session_id: 's1', answer: 'Paused.', tool_calls: [], success: true, ingested_migration_ids: [ID] };
  handlers[STATUS] = doc();
  c.ccAddFiles([new File(['a,b'], 'assets.csv'), new File(['x'], 'eicr.pdf')]);
  await c.orchSubmitNow();
  await settle();
  const post = calls.find((x) => x.key === RUN_FILES);
  assert.equal(post.body.getAll('files').length, 2, 'both files ride the one route, which splits them by type');
  assert.equal(c.state.mgId, ID);
  assert.deepEqual(c.state.ccFiles, [], 'and the tray is empty afterwards');
});

test('a spreadsheet sent WITH a question still goes to the orchestrator, and the run it starts opens in the card', async () => {
  handlers[RUN_FILES] = { session_id: 's1', answer: 'Paused at the primary-key gate.', tool_calls: [], success: true, error: null, ingested_migration_ids: [ID] };
  handlers[STATUS] = doc();
  c.setState({ sessionId: null });
  c.ccAddFiles([new File(['a,b'], 'assets.csv')]);
  await c.ccAsk('migrate this and tell me what it found');
  await settle();
  const post = calls.find((x) => x.key === RUN_FILES);
  assert.ok(post, 'the upload route was called');
  assert.equal(post.body.get('interactive_migration'), 'true');
  assert.equal(c.state.mgId, ID, 'the returned run opens in the transcript, not behind a link');
});

test('a refused upload is reported and nothing is started', async () => {
  // No RUN_FILES handler and no START handler: both refuse.
  c.ccAddFiles([new File(['a,b'], 'broken.csv')]);
  await c.orchSubmitNow();
  await settle();
  const turn = (c.state.ccChat || []).filter((m) => m.role === 'bot').pop();
  assert.ok(turn, 'an error is not silence');
  assert.ok(turn.error, 'the turn reads as an error');
  assert.equal(c.state.mgId, null);
  assert.equal(c.state.mgBusy, '');
});

// ── listing runs ────────────────────────────────────────────────────────────────────

test('"migrations" lists the runs; a question about a migration does not', () => {
  assert.ok(mgIsListRequest('migrations'));
  assert.ok(mgIsListRequest('  My Migrations  '));
  assert.ok(mgIsListRequest('show my migrations'));
  assert.ok(mgIsListRequest('recent migrations'));
  assert.ok(mgIsListRequest('list migrations'));
  assert.ok(!mgIsListRequest('why did that migration fail?'), 'a question is for the orchestrator');
  assert.ok(!mgIsListRequest('list the migrations that touched assets'), 'a qualified ask is a question');
  assert.ok(!mgIsListRequest(''), 'an empty message is not a request for anything');
});

test('asking for migrations answers from schema-mapper without calling the orchestrator', async () => {
  handlers['GET /backend/schema-mapper/api/migration'] = { total_count: 1, migrations: [{ migration_id: ID, cmms_name: 'Custom', status: 'awaiting_review', t1_count: 26, t2_count: 0, started_at: '2026-09-21T09:29:01Z' }] };
  await c.ccAsk('migrations');
  await settle();
  assert.ok(!calls.some((x) => x.key.includes('/workflow/run')), 'no model was asked; the list is read from schema-mapper');
  const turn = (c.state.ccChat || []).filter((m) => m.role === 'bot').pop();
  assert.ok(turn.mgList, 'the turn renders as the recent-runs card');
  assert.equal(c.state.ccBusy, false, 'the composer is released');
  const v = c.renderVals();
  assert.equal(v.mgRecent.length, 1);
  assert.equal(v.mgRecent[0].status, 'awaiting review');
});

test('a migrations list with nothing in it says so rather than showing an empty card', async () => {
  handlers['GET /backend/schema-mapper/api/migration'] = { total_count: 0, migrations: [] };
  await c.ccAsk('migrations');
  await settle();
  const turn = (c.state.ccChat || []).filter((m) => m.role === 'bot').pop();
  assert.match(turn.text, /No migrations yet/);
});

// ── the intent never steals a real question ─────────────────────────────────────────

test('a message that merely mentions a migration is answered by the orchestrator, not intercepted', async () => {
  handlers['POST /backend/deep-agents/api/workflow/run-stateful'] = { session_id: 's1', answer: 'It failed at the hierarchy gate.', tool_calls: [], success: true };
  await c.ccAsk('why did that migration fail?');
  await settle();
  const turn = (c.state.ccChat || []).filter((m) => m.role === 'bot').pop();
  assert.ok(!turn.mgList, 'not the list card');
  assert.ok(calls.some((x) => x.key.includes('/workflow/run')), 'the question reached the orchestrator');
});

// ── what an upgrade looks like for someone mid-review ───────────────────────────────
//
// A session saved by the OLD build says view: 'migration'. That view no longer exists, so
// the plain VIEWS check drops it and the reload lands on Home — with mgId restored, a gate
// waiting, no card rendered and no nav badge left to find it by. The stored view is
// therefore translated rather than dropped.
test('a session saved before the page was deleted reopens the run in the Orchestrator', async () => {
  const { loadSession, saveSession } = await import('../src/logic/session.js');
  const base = { signedIn: true, refreshToken: 'ref-1', email: 'a@b.c', role: 'admin', account: { id: 'u1', email: 'a@b.c', role: 'admin', status: 'active' } };
  // Written by the old build — saveSession would not produce this any more, so it is put
  // into storage exactly as the old one left it.
  window.localStorage.setItem('hoistra.session.v1', JSON.stringify(Object.assign({}, base, { view: 'migration', mgId: ID })));
  const back = loadSession();
  assert.equal(back.view, 'chat', 'the conversation is where the run is answered now');
  assert.equal(back.mgId, ID, 'and it is still the same run');
});

// ── the source system a run is labelled with ────────────────────────────────────────
//
// The deleted page had a "Source system" select beside the upload, and it is not cosmetic:
// schemaMapperApi.start sends it as cmms_name, the mapper uses it to pick its alias pack,
// and the run list shows it. Losing the control would silently label every run Custom.
test('the source system typed in the composer is sent with the upload', async () => {
  handlers[RUN_FILES] = { session_id: 's1', answer: 'Paused.', tool_calls: [], success: true, ingested_migration_ids: [ID] };
  handlers[STATUS] = doc();
  c.ccAddFiles([new File(['a,b'], 'maximo_export.xlsx')]);
  assert.equal(c.renderVals().mgCmms, 'Custom', 'Custom until it is told otherwise');
  c.renderVals().mgSetCmms({ target: { value: 'Maximo' } });
  await c.orchSubmitNow();
  await settle();
  const post = calls.find((x) => x.key === RUN_FILES);
  assert.equal(post.body.get('cmms_name'), 'Maximo', 'the run is labelled with what was typed');
});

test('an unnamed source system goes as Custom rather than empty', async () => {
  handlers[RUN_FILES] = { session_id: 's1', answer: 'Paused.', tool_calls: [], success: true, ingested_migration_ids: [ID] };
  handlers[STATUS] = doc();
  c.renderVals().mgSetCmms({ target: { value: '   ' } });
  c.ccAddFiles([new File(['a,b'], 'export.csv')]);
  await c.orchSubmitNow();
  await settle();
  assert.equal(calls.find((x) => x.key === RUN_FILES).body.get('cmms_name'), 'Custom');
});

test('a question with no attachment sends no source system at all', async () => {
  handlers['POST /backend/deep-agents/api/workflow/run-stateful'] = { session_id: 's1', answer: 'ok', tool_calls: [], success: true };
  await c.ccAsk('which buildings are at risk?');
  await settle();
  assert.ok(!calls.some((x) => x.key === RUN_FILES), 'no upload route was used');
});

// ── the building-filing question belongs to documents, not migrations ───────────────
//
// "No building selected — the documents will be indexed and searchable, but not validated,
// not filed against a building" is true of a PDF and meaningless for a CMMS export, whose
// rows go through the gates into plenum_cafm and are filed against nothing. With the chat
// as the migration surface the warning sat under every staged spreadsheet.
test('a tray of only spreadsheets IS asked which building to file against', () => {
  c.ccAddFiles([new File(['a,b'], 'cmms.xlsx')]);
  const v = c.renderVals();
  assert.equal(v.cbShow, 'flex', 'the migration is bound and audited against it, so it is asked');
  assert.match(v.cbWarn, /binds to no building and leaves no row in the ingestion audit trail/,
    'and the consequence named is the one a migration actually has');
});

test('a tray with a document in it is asked too, in the words that fit a document', () => {
  c.ccAddFiles([new File(['x'], 'eicr.pdf')]);
  const v = c.renderVals();
  assert.equal(v.cbShow, 'flex');
  assert.match(v.cbWarn, /indexed and searchable/);
});

test('a chosen building is described by what it does to each kind', () => {
  c.setState({ cbBuildingId: 'b-77', account: { id: 'u1', role: 'admin', status: 'active', buildings: [{ id: 'b-77', name: 'Bishopsgate Tower' }] } });
  c.ccAddFiles([new File(['a,b'], 'cmms.xlsx')]);
  assert.match(c.renderVals().cbWarn, /The migration is recorded against this building/);
  c.setState({ ccFiles: [] });
  c.ccAddFiles([new File(['x'], 'eicr.pdf')]);
  assert.match(c.renderVals().cbWarn, /checked against this building before it is bound/);
});

test('no attachment at all is not a filing question', () => {
  assert.equal(c.renderVals().cbShow, 'none');
});

// ── a run that has stopped moving says so ───────────────────────────────────────────
//
// On 21 Sep 2026 a real run sat at "Working… the pipeline is working on the current node"
// for five and a half hours. It was not working on anything: answering the primary-key gate
// enqueues resume_migration to ARQ, this stack runs no ARQ worker, and the job sat in redis
// for ever. The card reported the status document faithfully and the document said running,
// so nothing on screen distinguished "busy" from "abandoned".
//
// A node takes seconds and a gate answer resumes in seconds. Several minutes with no change
// to the status, the step or the node count is not slowness, and the card now says so
// rather than animating indefinitely.
test('a run whose document stops changing is reported as stalled, not as working', async () => {
  handlers[STATUS] = doc({ status: 'running', pending_gate_type: null, pending_gate_payload: {}, current_step: 1 });
  c.mgOpen(ID);
  await settle();
  assert.equal(c.renderVals().mgStallNote, '', 'nothing is said while it is fresh');

  // Five minutes of identical documents.
  c._mgMovedAt = Date.now() - 5 * 60 * 1000;
  await c.mgPoll(true);
  await settle();
  const v = c.renderVals();
  assert.match(v.mgStallNote, /has not moved/, 'the reader is told it has stopped');
  assert.match(v.mgStallNote, /worker/i, 'and what to look at');
});

test('a document that moves clears the stall note', async () => {
  handlers[STATUS] = doc({ status: 'running', pending_gate_type: null, pending_gate_payload: {}, current_step: 1 });
  c.mgOpen(ID);
  await settle();
  c._mgMovedAt = Date.now() - 5 * 60 * 1000;
  await c.mgPoll(true);
  await settle();
  assert.match(c.renderVals().mgStallNote, /has not moved/);

  // The pipeline gets on with it: a new step, and the clock restarts.
  handlers[STATUS] = doc({ status: 'running', pending_gate_type: null, pending_gate_payload: {}, current_step: 2 });
  await c.mgPoll(true);
  await settle();
  assert.equal(c.renderVals().mgStallNote, '', 'movement is movement');
});

test('a run waiting at a gate is never called stalled — it is waiting for a person', async () => {
  handlers[STATUS] = doc();   // awaiting_review at pk_approval
  c.mgOpen(ID);
  await settle();
  c._mgMovedAt = Date.now() - 60 * 60 * 1000;
  await c.mgPoll(true);
  await settle();
  assert.equal(c.renderVals().mgStallNote, '', 'a gate waits as long as the reader needs');
});

// ── a restored run must be read, not assumed ────────────────────────────────────────
//
// mgId is persisted, so a reload renders the card from it alone. The Migration page used
// to re-poll on arrival (core.js: `if (view === 'migration') … if (mgId) mgPoll(true)`);
// deleting the page deleted that line and left only its comment, so after a reload the
// card sat at "Not loaded" with every node hollow while its blurb claimed the pipeline was
// working — for ever, because the stall clock only starts inside mgPoll either.
test('arriving at the Orchestrator later reads a run restored but never fetched', async () => {
  handlers[STATUS] = doc();
  c.setState({ view: 'home', mgId: ID, mgStatus: null });
  c.openChat();
  await settle();
  assert.ok(calls.some((x) => x.key === STATUS), 'opening the conversation reads it');
});

test('a run with no document yet says so rather than claiming to be working', () => {
  c.setState({ mgId: ID, mgStatus: null, mgLoading: false });
  const v = c.renderVals();
  assert.ok(v.mgHasRun, 'the card renders');
  assert.doesNotMatch(v.mgGateBlurb, /working on the current node/i,
    'it must not claim the pipeline is working when nothing has been read');
  assert.match(v.mgGateBlurb, /not been read|Refresh/i, 'it says what is actually true');
});

test('the head does not print the run id twice when the file name is unknown', () => {
  c.setState({ mgId: ID, mgStatus: null });
  const v = c.renderVals();
  assert.equal(v.mgFile, '', 'no file name is known without a document');
  // MigrationRun renders mgFile only when there is one; the id chip carries the identity.
  assert.equal(v.mgIdShort, '2f9f3738');
});
