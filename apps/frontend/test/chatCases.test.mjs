// Answering a held document, in the chat that held it.
//
// When the validation gate holds an upload, the reply reads:
//
//     "Reply with your reason and I will put it to the check, or confirm to file it as it
//      is — either way the decision is recorded against your name."
//
// On 21 Sep 2026 that promise was not kept. A WKU contract was held against Bishopsgate
// Tower, the reader typed "test upload, file it anyway", and the orchestrator treated it as
// a brand-new question — routed it to the MIGRATION sub-agent, which looked for a file path
// and answered "the file cannot be found in the system". The case was never touched.
//
// Nothing was missing from the API: deepAgents.js already carries listCases, getCase,
// clarify, reassign and decide, and the Ingestion page uses all of them. The chat simply
// printed the question and forgot the case existed.
//
// So the routing is taken away from the model. A session with an open case sends the next
// message to that case, deterministically — routing is precisely what failed.
import { test, beforeEach } from 'node:test';
import assert from 'node:assert/strict';

const mem = {};
globalThis.window = {
  location: { origin: 'http://test.local' }, scrollTo: () => {},
  addEventListener: () => {}, removeEventListener: () => {},
  localStorage: {
    getItem: (k) => (k in mem ? mem[k] : null),
    setItem: (k, v) => { mem[k] = String(v); },
    removeItem: (k) => { delete mem[k]; }
  }
};
globalThis.WebSocket = class { constructor() { throw new Error('no sockets in tests'); } };

let handlers, calls;
globalThis.fetch = async (url, opts) => {
  const u = new URL(String(url));
  const method = (opts && opts.method) || 'GET';
  let body = null;
  try { body = opts && opts.body && typeof opts.body === 'string' ? JSON.parse(opts.body) : opts && opts.body; }
  catch { body = opts && opts.body; }
  calls.push({ method, path: u.pathname, body });
  const h = handlers[method + ' ' + u.pathname] || handlers[u.pathname];
  if (!h) throw new TypeError('Failed to fetch: no handler for ' + method + ' ' + u.pathname);
  const [status, out] = typeof h === 'function' ? h(u, opts, body) : h;
  return { ok: status >= 200 && status < 300, status, statusText: String(status), text: async () => JSON.stringify(out) };
};

const { HoistraLogic } = await import('../src/logic/HoistraLogic.js');

const DA = '/backend/deep-agents/api';
const CASE = 'c8984f1b-b6e3-469f-8b89-e8fef562746e';
const HELD = {
  success: true, answer: 'Held for a check — nothing has been filed yet.', tool_calls: [],
  validation_held: true,
  validation_cases: [{
    id: CASE, ok: true, verdict: 'held',
    question: 'Can you tell me why this document should be filed against Bishopsgate Tower?',
    document_name: 'c0e8adad_04_WKU_Facilities-Management-SLA-2022.pdf'
  }]
};

let c;
beforeEach(() => {
  calls = []; handlers = {};
  c = new HoistraLogic();
  c.setState({ signedIn: true, view: 'chat', account: { id: 'u1', email: 'pm@test.local', role: 'admin' } });
});
const cleanup = () => { clearTimeout(c._tt); clearInterval(c._ccTick); };
const settle = () => new Promise((r) => setTimeout(r, 40));
const stage = (n) => c.setState({ ccFiles: [new File(['%PDF-1.4'], n, { type: 'application/pdf' })] });
const heldUpload = () => { handlers['POST ' + DA + '/workflow/run-stateful-with-files'] = [200, HELD]; };

// ───────────────────────────────────────────────── the case is remembered

test('a held upload leaves the case open on the session', async () => {
  heldUpload(); stage('wku.pdf');
  await c.ccAsk('ingest');
  await settle();
  assert.equal(c.state.ccCaseId, CASE, 'the question was printed and the case forgotten');
  cleanup();
});

test('an ordinary turn opens no case', async () => {
  handlers['POST ' + DA + '/workflow/run-stateful'] = [200, { success: true, answer: 'fine', tool_calls: [] }];
  handlers['POST ' + DA + '/workflow/run-stateful-with-files'] = [200, { success: true, answer: 'fine', tool_calls: [] }];
  stage('x.pdf');
  await c.ccAsk('ingest');
  await settle();
  assert.ok(!c.state.ccCaseId);
  cleanup();
});

// ───────────────────────────────────────────── the next message answers it

test('the next message goes to the case, not to the orchestrator', async () => {
  heldUpload(); stage('wku.pdf');
  await c.ccAsk('ingest');
  await settle();
  calls = [];
  handlers['POST ' + DA + '/ingestion/cases/' + CASE + '/clarify'] =
    [200, { ok: true, question: 'Which building should it be filed against instead?', verdict: 'held' }];
  await c.ccAsk('test upload, file it anyway');
  await settle();
  const toCase = calls.find((x) => x.path.includes('/clarify'));
  assert.ok(toCase, 'the reply never reached the case');
  assert.equal(toCase.body.explanation, 'test upload, file it anyway');
  assert.ok(!calls.some((x) => x.path.includes('run-stateful')),
    'the orchestrator routed this to the migration sub-agent last time; it must not see it at all');
  cleanup();
});

test('the agent’s assessment lands in the transcript', async () => {
  heldUpload(); stage('wku.pdf');
  await c.ccAsk('ingest');
  await settle();
  handlers['POST ' + DA + '/ingestion/cases/' + CASE + '/clarify'] =
    [200, { ok: true, question: 'Which building should it be filed against instead?', verdict: 'held' }];
  await c.ccAsk('a reason');
  await settle();
  const last = (c.state.ccChat || []).filter((m) => m.role === 'bot').pop();
  assert.match(String(last.text), /Which building should it be filed against instead\?/);
  cleanup();
});

// ─────────────────────────────────────────────────────── it closes properly

test('a decided case stops intercepting', async () => {
  heldUpload(); stage('wku.pdf');
  await c.ccAsk('ingest');
  await settle();
  handlers['POST ' + DA + '/ingestion/cases/' + CASE + '/clarify'] =
    [200, { ok: true, verdict: 'accepted', bound: { documents: 1 } }];
  // Was 'file it', which is now read as consent and routed to decide — see "yes is sent as
  // a decision" below. This test is about a CLARIFY that comes back settled, so it needs a
  // message that is genuinely a reason. The decide path closing the case is covered by
  // 'an approved case closes and says what was filed'.
  await c.ccAsk('it is the same campus under its former name');
  await settle();
  assert.ok(!c.state.ccCaseId, 'a settled case must not swallow the next question');
  cleanup();
});

test('a new query drops the case', async () => {
  heldUpload(); stage('wku.pdf');
  await c.ccAsk('ingest');
  await settle();
  c.newQuery();
  assert.ok(!c.state.ccCaseId);
  cleanup();
});

test('a second held upload replaces the first rather than leaving two open', async () => {
  heldUpload(); stage('a.pdf');
  await c.ccAsk('ingest');
  await settle();
  const second = JSON.parse(JSON.stringify(HELD));
  second.validation_cases[0].id = 'second-case-id';
  handlers['POST ' + DA + '/workflow/run-stateful-with-files'] = [200, second];
  // The open case must not swallow an upload — a message WITH files is a new ingest.
  stage('b.pdf');
  await c.ccAsk('ingest another');
  await settle();
  assert.equal(c.state.ccCaseId, 'second-case-id',
    'ambiguity about which document is being answered is worse than losing the thread');
  cleanup();
});

test('an upload is never mistaken for an answer to the open case', async () => {
  heldUpload(); stage('a.pdf');
  await c.ccAsk('ingest');
  await settle();
  calls = [];
  handlers['POST ' + DA + '/workflow/run-stateful-with-files'] = [200, { success: true, answer: 'ok', tool_calls: [] }];
  stage('b.pdf');
  await c.ccAsk('and this one');
  await settle();
  assert.ok(calls.some((x) => x.path.includes('run-stateful-with-files')), 'a file is an ingest, not a reason');
  assert.ok(!calls.some((x) => x.path.includes('/clarify')));
  cleanup();
});

// ─────────────────────────────────────────────────────────── the escape hatch

test('the reader can drop the case and go back to asking questions', async () => {
  heldUpload(); stage('wku.pdf');
  await c.ccAsk('ingest');
  await settle();
  c.renderVals().ccCaseDrop();
  assert.ok(!c.state.ccCaseId);
  cleanup();
});

test('the composer says where the next sentence is going', async () => {
  heldUpload(); stage('wku.pdf');
  await c.ccAsk('ingest');
  await settle();
  const v = c.renderVals();
  assert.equal(v.ccCaseShow, 'flex');
  assert.match(v.ccCaseLabel, /held/i);
  assert.match(v.ccCaseLabel, /WKU/, 'which document, not just that there is one');
  cleanup();
});

test('with no open case the marker is hidden', () => {
  assert.equal(c.renderVals().ccCaseShow, 'none');
  cleanup();
});

test('a marker with no values hides rather than showing an empty box', () => {
  // Vite can update one module and not another: on 21 Sep the chat page rendered the new
  // strip while renderVals was still the old one, so `display: vals.ccCaseShow` was
  // `display: undefined` — which is not "none", it is the element's default. The reader got
  // an empty bordered box with a button that did nothing. A missing value must hide.
  const v = c.renderVals();
  assert.equal(v.ccCaseShow, 'none');
  assert.equal(v.ccCaseLabel, '');
  cleanup();
});

test('an open case survives a page reload', async () => {
  // A held case lives on the SESSION, and sessionId is already persisted. On 21 Sep the
  // case was not: a hard refresh — the very thing needed to pick up new code — dropped it,
  // and the marker vanished while the document stayed held on the server with nothing in
  // the UI pointing at it.
  const { buildSliceForTest } = await import('../src/logic/session.js');
  const slice = buildSliceForTest({
    signedIn: true, email: 'pm@test.local', sessionId: 'sess-1',
    ccCaseId: CASE, ccCaseDoc: 'abc_04_WKU.pdf', ccCaseQuestion: 'Why this building?'
  });
  assert.equal(slice.ccCaseId, CASE, 'the reader would have no way back to a held document');
  assert.equal(slice.ccCaseDoc, 'abc_04_WKU.pdf');
  assert.equal(slice.ccCaseQuestion, 'Why this building?');
  cleanup();
});

test('a session with no case persists none', async () => {
  const { buildSliceForTest } = await import('../src/logic/session.js');
  const slice = buildSliceForTest({ signedIn: true, email: 'x' });
  assert.equal(slice.ccCaseId, null);
  cleanup();
});

test('the case survives the whole save → reload → restore round trip', async () => {
  // The first attempt at this only fixed buildSlice. readSlice validates every key it
  // knows and DROPS everything else — by design, so a hand-edited entry cannot wedge the
  // UI — so the three keys were written and thrown away on the way back. Writing a value
  // is not persisting it, and only the round trip proves it.
  const S = await import('../src/logic/session.js');
  const saved = { signedIn: true, email: 'pm@t', refreshToken: 'r',
    account: { id: 'u1', email: 'pm@t', role: 'admin' },
    sessionId: 'sess-1', ccCaseId: CASE, ccCaseDoc: 'abc_04_WKU.pdf', ccCaseQuestion: 'Why?' };
  S.saveSession(saved, { signedIn: false });
  const back = S.loadSession();
  assert.equal(back.ccCaseId, CASE, 'stored, then dropped on the way back');
  assert.equal(back.ccCaseDoc, 'abc_04_WKU.pdf');
  cleanup();
});

test('a case without its session does not restore on its own', async () => {
  const S = await import('../src/logic/session.js');
  S.saveSession({ signedIn: true, email: 'pm@t', refreshToken: 'r',
    account: { id: 'u1', email: 'pm@t', role: 'admin' },
    sessionId: null, ccCaseId: CASE }, { signedIn: false });
  const back = S.loadSession();
  assert.ok(!back.ccCaseId, 'a case id with no conversation points the composer at nothing');
  cleanup();
});

// ── cases the server still holds, that this browser has never seen ───────────────────────
// Five documents were held on 21 Sep with nothing in the UI pointing at any of them:
// listCases was wired in api/deepAgents.js and called from nowhere. Storage alone cannot
// fix that — a case held in another browser, or before this code existed, is invisible.
// The server is the authority, so the marker asks it.

test('an open case on the server is adopted when the session has none', async () => {
  handlers['GET ' + DA + '/ingestion/cases'] = [200, { ok: true, cases: [
    { id: 'server-case-1', document_name: 'abc_04_WKU.pdf', question: 'Why this building?', verdict: 'held' }
  ] }];
  await c.ccCaseSync();
  assert.equal(c.state.ccCaseId, 'server-case-1', 'five held documents had nothing pointing at them');
  assert.match(c.renderVals().ccCaseLabel, /WKU/);
  cleanup();
});

test('the session’s own case is never replaced by one off the server', async () => {
  c.setState({ ccCaseId: 'mine', ccCaseDoc: 'mine.pdf' });
  handlers['GET ' + DA + '/ingestion/cases'] = [200, { ok: true, cases: [{ id: 'other', document_name: 'other.pdf' }] }];
  await c.ccCaseSync();
  assert.equal(c.state.ccCaseId, 'mine', 'the reader is mid-answer; swapping the document under them is worse than silence');
  cleanup();
});

test('no open cases leaves the composer alone', async () => {
  handlers['GET ' + DA + '/ingestion/cases'] = [200, { ok: true, cases: [] }];
  await c.ccCaseSync();
  assert.ok(!c.state.ccCaseId);
  assert.equal(c.renderVals().ccCaseShow, 'none');
  cleanup();
});

test('an unreachable service is not an error the reader has to see', async () => {
  handlers['GET ' + DA + '/ingestion/cases'] = [503, { ok: false }];
  await c.ccCaseSync();
  assert.ok(!c.state.ccCaseId);
  assert.ok(!(c.state.ccChat || []).length, 'a background check must not put anything in the transcript');
  cleanup();
});

test('a case that is no longer held is not adopted', async () => {
  handlers['GET ' + DA + '/ingestion/cases'] = [200, { ok: true, cases: [
    { id: 'done', document_name: 'x.pdf', verdict: 'matched', may_ingest: true }
  ] }];
  await c.ccCaseSync();
  assert.ok(!c.state.ccCaseId, 'a settled case is not waiting on anybody');
  cleanup();
});

// ──────────────────────────────────────── a yes is an answer, not another reason
//
// 21 Sep 2026, after the routing above was fixed. A WKU contract was held against
// Bishopsgate Tower. The reader typed "file it as it is", then "yes", then "yes", and got
// back the same sentence all three times:
//
//     "That tells me you want it filed against Bishopsgate Tower, but not why the document
//      looks like it belongs elsewhere — so the warning stands. If you still want it filed
//      against Bishopsgate Tower, say yes and I will record the override against your name."
//
// Saying yes was exactly what it asked for. ccCaseAnswer only ever called clarify, and
// clarify by design never releases a document — validation.py returns requires_confirmation
// on every reply, meaning "a person still answers this". decideCase was the thing that
// answers it, sat in deepAgents.js, and was called from nowhere in the chat. So the loop had
// no exit: the promise named an action the code could not take.
//
// A reply is classified before it is sent. A clear yes or no is a decision; everything else
// is still a reason. Matching is on the whole normalised message, never a substring, so an
// ambiguous message falls to clarify — which only ever asks again, and is the safe side.

const DECIDE = 'POST ' + DA + '/ingestion/cases/' + CASE + '/decide';
const CLARIFY = 'POST ' + DA + '/ingestion/cases/' + CASE + '/clarify';
const held = async () => { heldUpload(); stage('wku.pdf'); await c.ccAsk('ingest'); await settle(); calls = []; };

test('yes is sent as a decision, not as another explanation', async () => {
  await held();
  handlers[DECIDE] = [200, { ok: true, verdict: 'accepted', may_ingest: true, bound: { documents: 1 } }];
  handlers[CLARIFY] = [200, { ok: true, question: 'asked yet again', verdict: 'held' }];
  await c.ccAsk('yes');
  await settle();
  assert.ok(calls.some((x) => x.path.endsWith('/decide')), 'saying yes asked again instead of deciding');
  assert.ok(!calls.some((x) => x.path.endsWith('/clarify')), 'a yes is not a reason');
  cleanup();
});

test('the decision carries approval, so the document is actually released', async () => {
  await held();
  handlers[DECIDE] = [200, { ok: true, verdict: 'accepted', may_ingest: true, bound: { documents: 1 } }];
  await c.ccAsk('yes');
  await settle();
  const d = calls.find((x) => x.path.endsWith('/decide'));
  assert.equal(d.body.approve, true);
  cleanup();
});

test('the reader’s own words are recorded with the decision', async () => {
  await held();
  handlers[DECIDE] = [200, { ok: true, verdict: 'accepted', may_ingest: true, bound: { documents: 1 } }];
  await c.ccAsk('file it as it is');
  await settle();
  const d = calls.find((x) => x.path.endsWith('/decide'));
  assert.equal(d.body.note, 'file it as it is',
    'the audit row must say what the person actually typed, not a paraphrase');
  cleanup();
});

test('"file it as it is" on the very first reply is a confirmation', async () => {
  // The hold message itself offers "or confirm to file it as it is". This was the exact
  // sentence the reader typed, and it was sent as an explanation.
  await held();
  handlers[DECIDE] = [200, { ok: true, verdict: 'accepted', may_ingest: true, bound: { documents: 1 } }];
  handlers[CLARIFY] = [200, { ok: true, question: 'asked again', verdict: 'held' }];
  await c.ccAsk('file it as it is');
  await settle();
  assert.ok(calls.some((x) => x.path.endsWith('/decide')));
  cleanup();
});

test('a refusal is a decision too, and is not an approval', async () => {
  await held();
  handlers[DECIDE] = [200, { ok: true, verdict: 'rejected' }];
  await c.ccAsk('no');
  await settle();
  const d = calls.find((x) => x.path.endsWith('/decide'));
  assert.ok(d, 'no is an answer to the question, not a new reason');
  assert.equal(d.body.approve, false);
  cleanup();
});

test('an actual reason is still put to the check', async () => {
  await held();
  handlers[CLARIFY] = [200, { ok: true, question: 'and which building?', verdict: 'held' }];
  await c.ccAsk('the campus was rebranded last year and the contract predates it');
  await settle();
  assert.ok(calls.some((x) => x.path.endsWith('/clarify')), 'a reason must still reach the assessment');
  assert.ok(!calls.some((x) => x.path.endsWith('/decide')), 'nothing may be filed on a reason alone');
  cleanup();
});

test('a question that mentions filing is not a yes', async () => {
  await held();
  handlers[CLARIFY] = [200, { ok: true, question: 'still asking', verdict: 'held' }];
  handlers[DECIDE] = [200, { ok: true, verdict: 'accepted' }];
  await c.ccAsk('why should I file it as it is?');
  await settle();
  assert.ok(!calls.some((x) => x.path.endsWith('/decide')),
    'substring matching would file a document because the reader asked a question about filing');
  cleanup();
});

test('an approved case closes and says what was filed', async () => {
  await held();
  handlers[DECIDE] = [200, { ok: true, verdict: 'accepted', may_ingest: true, bound: { documents: 1 } }];
  await c.ccAsk('yes');
  await settle();
  assert.ok(!c.state.ccCaseId, 'the case is answered; it must stop intercepting');
  const last = (c.state.ccChat || []).filter((m) => m.role === 'bot').pop();
  assert.match(String(last.text), /1 documents/, 'the reader is told what the yes actually did');
  cleanup();
});

test('a decision that fails leaves the case open rather than losing it', async () => {
  await held();
  handlers[DECIDE] = [503, { ok: false }];
  await c.ccAsk('yes');
  await settle();
  assert.equal(c.state.ccCaseId, CASE, 'a decision that never landed must not close the case');
  cleanup();
});
