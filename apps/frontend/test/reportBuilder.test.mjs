// New report (Hussain, 7 Oct 2026): every session can be made a report — not the newest five
// conversations — from a panel that tells them apart, with the question it will re-ask shown
// and editable. A task, which has no question of its own, is asked about.
import { test, afterEach } from 'node:test';
import assert from 'node:assert/strict';

globalThis.window = { location: { origin: 'http://test.local' }, scrollTo: () => {}, addEventListener: () => {}, removeEventListener: () => {} };
globalThis.fetch = () => Promise.reject(new TypeError('Failed to fetch'));
globalThis.WebSocket = class { constructor() { throw new Error('no sockets in tests'); } };

const { HoistraLogic } = await import('../src/logic/HoistraLogic.js');
const { reportsApi } = await import('../src/api/reports.js');
const { makeSession } = await import('../src/logic/sessions.js');
const { reportQuestionFor } = await import('../src/logic/reports.js');

const real = { create: reportsApi.createCard, list: reportsApi.list };
let c = null;
afterEach(() => {
  Object.assign(reportsApi, { createCard: real.create, list: real.list });
  if (c) { clearInterval(c._orchTick); clearTimeout(c._tt); clearTimeout(c._homeRetry); clearTimeout(c._ccRetry); clearTimeout(c._vpRetry); clearTimeout(c._qTimer); clearTimeout(c._homeRefresh); }
  c = null;
});
const OWNER = 'pm@northbridge.co.uk';
const sessions = () => {
  const out = [];
  for (let i = 0; i < 9; i++) out.push(makeSession({ id: 'c' + i, title: 'What needs my approval today?', page: i % 2 ? 'Compliance' : 'Home', owner: OWNER, at: 1000 - i }));
  out.push(makeSession({ id: 't1', title: 'Request evidence — Apex Mechanical', page: 'Vendors', kind: 'task', owner: OWNER, at: 2000 }));
  return out;
};
const boot = () => {
  c = new HoistraLogic({});
  c.setState({ signedIn: true, account: { email: OWNER }, sessions: sessions() });
  return c;
};

test('the question: a conversation keeps its own, a task is asked about', () => {
  assert.equal(reportQuestionFor({ kind: 'chat', title: 'Which buildings put me at risk?' }), 'Which buildings put me at risk?');
  assert.equal(reportQuestionFor({ kind: 'task', title: 'Request evidence — Apex Mechanical' }), 'What is the latest on: Request evidence — Apex Mechanical?');
});

test('every session is offered, told apart by where and when, and can be narrowed', () => {
  boot();
  c.rpOpenBuilder(null);
  let v = c.renderVals();
  assert.equal(v.reportMenu, true);
  assert.equal(v.reportSources.length, 10, 'all ten — the five-row cap is gone, tasks included');
  assert.match(v.reportSources[1].meta, /from (Home|Compliance)/);
  assert.deepEqual(v.reportFilters.map((f) => [f.label, f.count]), [['All', 10], ['Conversations', 9], ['Tasks', 1]]);
  v.reportFilters[2].pick();
  assert.deepEqual(c.renderVals().reportSources.map((r) => r.id), ['t1']);
  c.setState({ reportFilter: 'all', reportQuery: 'apex' });
  assert.deepEqual(c.renderVals().reportSources.map((r) => r.id), ['t1']);
  c.setState({ reportQuery: 'nothing like this' });
  assert.equal(c.renderVals().reportNoMatch, true);
  c.setState({ reportSrcId: null, reportQuestion: null });
  assert.equal(c.renderVals().reportCanCreate, false, 'nothing picked, nothing to create');
});

test('from the navigator\'s "+" the newest conversation is already chosen, as the old form had it', () => {
  boot();
  // The "+" itself (Navigator.jsx calls toggleReportMenu), not rpOpenBuilder directly (8 Oct 2026).
  c.renderVals().toggleReportMenu();
  const v = c.renderVals();
  assert.equal(v.reportSources.find((r) => r.on).id, 'c0', 'the newest conversation, not the newer task');
  assert.equal(v.reportCanCreate, true);
  v.toggleReportMenu();
  assert.equal(c.renderVals().reportMenu, false, 'pressed again, it closes');
});

test('a task becomes a report that asks where it stands, with no thread to point back to', async () => {
  let body = null;
  reportsApi.createCard = async (b) => { body = b; return { ok: true, card: { id: 'card-9' } }; };
  reportsApi.list = async () => ({ ok: true, reports: [] });
  boot();
  c.rpOpenBuilder('t1');
  const v = c.renderVals();
  assert.equal(v.reportQuestion, 'What is the latest on: Request evidence — Apex Mechanical?');
  assert.equal(v.reportPickedTask, true);
  c.setState({ reportQuestion: 'Has Apex Mechanical sent the evidence we asked for?' });
  await c.rpCreate();
  assert.equal(body.prompt, 'Has Apex Mechanical sent the evidence we asked for?', 'the reworded question is what is re-asked');
  assert.equal('source_session_id' in body, false, 'a task was never a server thread');
  assert.equal(body.source_page, 'Vendors');
});

test('an older conversation — one whose transcript is on the server — is a report source too', async () => {
  let body = null;
  reportsApi.createCard = async (b) => { body = b; return { ok: true, card: { id: 'card-8' } }; };
  reportsApi.list = async () => ({ ok: true, reports: [] });
  boot();
  c.setState((p) => ({ sessions: p.sessions.map((r) => (r.id === 'c8' ? Object.assign({}, r, { turns: [], remote: true, turnCount: 3 }) : r)) }));
  c.rpOpenBuilder('c8');
  assert.equal(c.renderVals().reportSources.find((r) => r.id === 'c8').meta.indexOf('3 questions') > -1, true);
  await c.rpCreate();
  assert.equal(body.prompt, 'What needs my approval today?');
  assert.equal(body.source_session_id, 'c8');
});

test('Make a report on a session\'s ⋯ menu opens the panel with that session chosen', () => {
  boot();
  const row = c.renderVals().sessionsPage.groups[0].rows.find((r) => r.id === 'c3');
  row.makeReport();
  const v = c.renderVals();
  assert.equal(v.reportMenu, true);
  assert.equal(v.reportSources.find((r) => r.on).id, 'c3');
});
