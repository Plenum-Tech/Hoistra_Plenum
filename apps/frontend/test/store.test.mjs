// Sessions, spaces and reports on the real controller, in Node with a stub window and a dead
// backend: every question fails the way an unreachable orchestrator fails, which is exactly
// the path the records have to survive. Storage is an in-memory localStorage so persistence
// and reload are exercised too.
import { test, beforeEach } from 'node:test';
import assert from 'node:assert/strict';

const mem = {};
globalThis.window = {
  location: { origin: 'http://test.local' }, scrollTo: () => {},
  addEventListener: () => {}, removeEventListener: () => {},
  localStorage: { getItem: (k) => (k in mem ? mem[k] : null), setItem: (k, v) => { mem[k] = String(v); }, removeItem: (k) => { delete mem[k]; } }
};
globalThis.fetch = () => Promise.reject(new TypeError('Failed to fetch'));
globalThis.WebSocket = class { constructor() { throw new Error('no sockets in tests'); } };

const { HoistraLogic } = await import('../src/logic/HoistraLogic.js');
const { SESSIONS_KEY } = await import('../src/logic/sessions.js');
const { REPORTS_KEY } = await import('../src/logic/reports.js');

const settle = (ms) => new Promise((r) => setTimeout(r, ms || 30));
let c;
const fresh = () => { const x = new HoistraLogic(); x.setState({ signedIn: true, view: 'home' }); return x; };
beforeEach(() => { Object.keys(mem).forEach((k) => { delete mem[k]; }); c = fresh(); });
const cleanup = (x) => {
  const k = x || c;
  clearInterval(k._orchTick); clearTimeout(k._tt); clearTimeout(k._homeRetry); clearTimeout(k._ccRetry);
  clearTimeout(k._spRetry); k.rpStop();
};

test('the first question opens a session keyed by the orchestrator thread and stores the transcript', async () => {
  await c.askScoped('Which vendors are blocked right now?');
  await settle();
  assert.ok(c.state.sessionId, 'an id is minted for the thread');
  assert.equal(c.state.sessions.length, 1);
  const s = c.state.sessions[0];
  assert.equal(s.id, c.state.sessionId);
  assert.equal(s.kind, 'chat');
  assert.equal(s.title, 'Which vendors are blocked right now?');
  assert.equal(s.page, 'Home');
  assert.equal(s.turns.length, 2, 'the question and the failure reply');
  assert.equal(s.turns[1].error, true);
  assert.ok(JSON.parse(mem[SESSIONS_KEY])[0].id === s.id, 'persisted');
  // A question from Home is the main orchestrator's: it answers on the chat page, dock closed.
  assert.equal(c.state.view, 'chat');
  assert.equal(c.state.orchOpen, false);
  cleanup();
});

test('a follow-up stays in the same session; New query starts another', async () => {
  await c.askScoped('first');
  await settle();
  await c.askScoped('second');
  await settle();
  assert.equal(c.state.sessions.length, 1);
  assert.equal(c.state.sessions[0].turns.length, 4);
  const firstId = c.state.sessionId;
  c.newQuery();
  assert.equal(c.state.sessionId, null);
  assert.equal(c.state.view, 'chat', 'New query opens the main orchestrator');
  assert.deepEqual(c.state.ccChat, []);
  assert.equal(c.state.orchOpen, false, 'and not the side dock');
  assert.equal(c.state.orchTask, null);
  assert.equal(c.renderVals().orchPlaceholder, 'Message the orchestrator…');
  await c.askScoped('third');
  await settle();
  assert.equal(c.state.sessions.length, 2);
  assert.notEqual(c.state.sessionId, firstId);
  assert.equal(c.state.sessions[0].title, 'third', 'newest first');
  cleanup();
});

test('opening a session restores its transcript without counting as activity', async () => {
  await c.askScoped('older');
  await settle();
  const older = c.state.sessions[0];
  c.newQuery();
  await c.askScoped('newer');
  await settle();
  c.openSession(older.id);
  assert.equal(c.state.view, 'chat');
  assert.equal(c.state.sessionId, older.id);
  assert.deepEqual(c.state.ccChat, older.turns);
  assert.equal(c.state.sessions[0].title, 'newer', 'the order is unchanged');
  assert.equal(c.state.sessions[1].at, older.at);
  cleanup();
});

test('deleting the active session clears the page', async () => {
  await c.askScoped('gone');
  await settle();
  const id = c.state.sessionId;
  c.deleteSession(id);
  assert.equal(c.state.sessions.length, 0);
  assert.equal(c.state.sessionId, null);
  assert.deepEqual(c.state.ccChat, []);
  cleanup();
});

test('a question from a side dock is one chat session, filed under the page it came from', async () => {
  c.setState({ view: 'cc' });
  await c.askScoped('Which lapses void insurance?');
  await settle();
  assert.equal(c.state.sessions.filter((s) => s.kind === 'chat').length, 1);
  assert.equal(c.state.sessions.filter((s) => s.kind === 'task').length, 0, 'the dock title is not a second record');
  assert.equal(c.state.sessions[0].page, 'Compliance');
  assert.equal(c.state.orchOpen, true);
  cleanup();
});

test('an orchestrator task is a session too, and reopens the dock on its chain', () => {
  c.orch('Export compliance pack', 'Compliance');
  const t = c.state.sessions[0];
  assert.equal(t.kind, 'task');
  assert.equal(t.title, 'Export compliance pack — Compliance');
  assert.equal(t.steps.length, 4);
  assert.ok(t.at > 0);
  c.closeOrch();
  c.openSession(t.id);
  assert.equal(c.state.orchOpen, true);
  assert.equal(c.state.orchTask, t);
  assert.equal(c.state.orchDone, 4);
  cleanup();
});

test('a reload brings the sessions and the open transcript back', async () => {
  await c.askScoped('persist me');
  await settle();
  const id = c.state.sessionId;
  const c2 = new HoistraLogic();
  assert.equal(c2.state.signedIn, true);
  assert.equal(c2.state.view, 'chat', 'the conversation reopens as the page it was');
  assert.equal(c2.state.orchOpen, false, 'with no dock beside it');
  assert.equal(c2.state.sessionId, id);
  assert.equal(c2.state.sessions.length, 1);
  assert.equal(c2.state.ccChat.length, 2, 'the transcript is restored from the record');
  assert.equal(c2.state.ccChat[0].text, 'persist me');
  cleanup(); cleanup(c2);
});

test('filing a session in a space and opening the list filtered to it', async () => {
  await c.askScoped('file me');
  await settle();
  const id = c.state.sessionId;
  c.fileSession(id, 'space-uuid');
  assert.equal(c.state.sessions[0].spaceId, 'space-uuid');
  c.openSessions('space-uuid');
  assert.equal(c.state.view, 'sessions');
  assert.equal(c.state.sessionsFilter, 'space-uuid');
  c.fileSession(id, null);
  assert.equal(c.state.sessions[0].spaceId, null);
  cleanup();
});

test('the space view survives a reload with its key', () => {
  c.openSpace('compliance');
  assert.equal(c.state.view, 'space');
  const c2 = new HoistraLogic();
  assert.equal(c2.state.view, 'space');
  assert.equal(c2.state.spaceKey, 'compliance');
  cleanup(); cleanup(c2);
});

test('a dead svc-udr leaves the built-in spaces in place and reports the failure', async () => {
  await c.spLoad();
  assert.equal(c.state.spaces, null);
  assert.match(c.state.spError, /Failed to fetch/);
  const m = c.spModel();
  assert.equal(m.builtin.length, 4);
  assert.equal(m.savedLive, false);
  assert.equal(m.builtin[0].badge, '—', 'no seed figure while nothing has answered');
  c.setState({ spNewName: 'Tower 3' });
  await c.spCreate();
  assert.match(c.state.toast, /unavailable/);
  cleanup();
});

test('a report is built from a session, runs at once, and a dead backend leaves it failed but scheduled', async () => {
  await c.askScoped('Which buildings put me at risk this month?');
  await settle();
  c.setState({ reportName: 'Risky buildings', reportCad: 0 });
  c.rpCreate();
  assert.equal(c.state.view, 'report');
  assert.equal(c.state.reports.length, 1);
  const key = c.state.reportKey;
  assert.equal(c.state.reports[0].key, key);
  assert.equal(c.state.reports[0].prompt, 'Which buildings put me at risk this month?');
  assert.equal(c.state.reports[0].status, 'running');
  await settle(60);
  const r = c.state.reports[0];
  assert.equal(r.status, 'error');
  assert.match(r.runs[0].error, /Failed to fetch/);
  assert.equal(r.lastRunAt, null, 'no successful refresh yet');
  assert.ok(r.nextRunAt > r.lastTriedAt, 'still on its schedule');
  assert.equal(JSON.parse(mem[REPORTS_KEY])[0].key, key, 'persisted');
  c.rpDelete(key);
  assert.equal(c.state.reports.length, 0);
  assert.equal(c.state.view, 'home');
  cleanup();
});

test('a report cannot be created without a session to build it from', () => {
  c.setState({ reportName: 'Nothing' });
  c.rpCreate();
  assert.equal(c.state.reports.length, 0);
  assert.match(c.state.toast, /Ask something first/);
  cleanup();
});

test('the scheduler runs a due report and leaves the rest alone', async () => {
  await c.askScoped('q');
  await settle();
  c.setState({ reportName: 'A', reportCad: 0 });
  c.rpCreate();
  await settle(60);
  const key = c.state.reportKey;
  const before = c.state.reports[0].lastTriedAt;
  // Not due yet: nothing happens.
  c.rpTick();
  await settle(60);
  assert.equal(c.state.reports[0].lastTriedAt, before);
  // Due: it runs again.
  c.rpPatch(key, { nextRunAt: Date.now() - 1 });
  c.rpTick();
  await settle(60);
  assert.ok(c.state.reports[0].lastTriedAt > before);
  assert.equal(c.state.reports[0].runs.length, 2);
  cleanup();
});

test('the navigator behaves the same on Home as everywhere else: it stays as it was, and the dock sits beside it', () => {
  c.setState({ view: 'cc', navOpen: true });
  c.renderVals().goHome();
  assert.equal(c.state.view, 'home');
  assert.equal(c.state.navOpen, true, 'going Home does not collapse the navigator');
  c.newQuery();
  assert.equal(c.state.navOpen, true, 'nor does a new query');
  c.renderVals().goHome();
  c.setState({ orchOpen: true });   // the top-bar icon opens the dock beside Home
  let v = c.renderVals();
  assert.equal(v.navOverlay, undefined, 'no scrim over Home');
  assert.equal(v.orchLeft, '248px', 'the dock sits beside the open navigator');
  assert.equal(v.shellPad, (248 + 280) + 'px');
  c.setState({ navOpen: false });
  v = c.renderVals();
  assert.equal(v.orchLeft, '52px');
  assert.equal(v.shellPad, (52 + 280) + 'px');
  cleanup();
});

test('the navigator state survives a reload and sign-in opens it', () => {
  c.setState({ navOpen: true });
  const c2 = new HoistraLogic();
  assert.equal(c2.state.navOpen, true);
  c.setState({ navOpen: false });
  const c3 = new HoistraLogic();
  assert.equal(c3.state.navOpen, false);
  const c4 = new HoistraLogic();
  c4.setState({ signedIn: false });
  c4.renderVals().signIn();
  assert.equal(c4.state.navOpen, true);
  cleanup(); cleanup(c2); cleanup(c3); cleanup(c4);
});
