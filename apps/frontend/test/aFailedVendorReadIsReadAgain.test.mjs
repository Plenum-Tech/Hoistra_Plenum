// A Vendors read that failed while the rest of the page answered is read again on its own
// (2 Nov 2026). Pending tasks, Pending critical, Invoice lines held, Terms on default and
// Contracts sat on "—" after one timed-out read, and stayed there until the page's next refresh
// fifteen minutes later. vpReadMissing reads only what is missing, a few seconds later, and
// merges what answers into the page.
import { test, mock } from 'node:test';
import assert from 'node:assert/strict';

globalThis.window = { location: { origin: 'http://test.local' }, scrollTo: () => {}, innerWidth: 1470, addEventListener: () => {}, removeEventListener: () => {} };
globalThis.WebSocket = class { constructor() { throw new Error('no sockets in tests'); } };

const asked = [];
globalThis.fetch = (url) => {
  asked.push(new URL(url).pathname);
  const body = /contract-performance\/approvals/.test(url)
    ? { ok: true, count: 2, items: [{ id: 'a', severity: 'high', item_type: 'invoice_dispute', payload: {} }, { id: 'b', severity: 'low', item_type: 'sla_breach', payload: {} }] }
    : { ok: true, count: 0, parameters: [] };
  return Promise.resolve({ ok: true, status: 200, headers: { get: () => null }, text: async () => JSON.stringify(body) });
};

const { HoistraLogic } = await import('../src/logic/HoistraLogic.js');
const settle = async () => { for (let i = 0; i < 20; i++) await new Promise((r) => setImmediate(r)); };

test('only the missing read is asked for again, and its cards fill', async () => {
  mock.timers.enable({ apis: ['setTimeout'] });
  try {
    const c = new HoistraLogic();
    c.setState({ signedIn: true, account: { id: 'u', email: 'r@x.y', role: 'user' }, role: 'user',
      vpRaw: { fetchedAt: 'now', errors: { approvals: 'timed out after 20s' }, approvals: null, summary: null, contracts: { ok: true, count: 0, parameters: [] } } });
    assert.equal(c.vpModel().tiles.pending, null, 'before: the tile has nothing to show');
    asked.length = 0;
    c.vpReadMissing();
    mock.timers.tick(5000);
    await settle();
    assert.deepEqual(asked, ['/backend/ops-intelligence/api/contract-performance/approvals']);
    assert.equal(c.state.vpRaw.errors.approvals, undefined);
    assert.equal(c.vpModel().tiles.pending, 2);
    assert.equal(c.vpModel().tiles.critical, 1);
    clearTimeout(c._vpPartialTimer);
  } finally {
    mock.timers.reset();
  }
});

test('a full load that landed meanwhile is not overwritten by the late re-read', async () => {
  mock.timers.enable({ apis: ['setTimeout'] });
  try {
    const c = new HoistraLogic();
    c.setState({ signedIn: true, account: { id: 'u', email: 'r@x.y', role: 'user' }, role: 'user',
      vpRaw: { fetchedAt: 'old', errors: { approvals: 'timed out after 20s' }, approvals: null } });
    c.vpReadMissing();
    const newer = { fetchedAt: 'newer', errors: {}, approvals: { ok: true, count: 5, items: [] } };
    c.setState({ vpRaw: newer });
    mock.timers.tick(5000);
    await settle();
    assert.equal(c.state.vpRaw, newer);
  } finally {
    mock.timers.reset();
  }
});

test('nothing missing, nothing asked', () => {
  const c = new HoistraLogic();
  c.setState({ vpRaw: { fetchedAt: 'now', errors: {} } });
  c.vpReadMissing();
  assert.equal(c._vpPartialTimer, undefined);
});

// The page is drawn before its slow read (wo-scores) lands, and the read merges in afterwards
// (2 Nov 2026: Vendors sat on "Reading…" for forty seconds waiting on it).
test('the Vendors page is drawn before wo-scores answers, and wo-scores merges in when it does', async () => {
  let release;
  const gate = new Promise((r) => { release = r; });
  const prev = globalThis.fetch;
  globalThis.fetch = async (url) => {
    const path = new URL(url).pathname;
    if (/wo-scores/.test(path)) await gate;
    const body = /wo-scores/.test(path) ? { ok: true, count: 1, items: [] }
      : /contract-performance\/approvals/.test(path) ? { ok: true, count: 0, items: [] }
      : { ok: true, count: 0, parameters: [] };
    return { ok: true, status: 200, headers: { get: () => null }, text: async () => JSON.stringify(body) };
  };
  try {
    const c = new HoistraLogic();
    c.setState({ signedIn: true, account: { id: 'u', email: 'r@x.y', role: 'user' }, role: 'user' });
    await c.vpLoad();
    assert.ok(c.state.vpRaw, 'drawn without waiting for wo-scores');
    assert.equal(c.state.vpRaw.woScores, undefined);
    assert.equal(c.state.vpLoading, false);
    release();
    await settle();
    assert.deepEqual(c.state.vpRaw.woScores, { ok: true, count: 1, items: [] }, 'merged into the same load');
    clearTimeout(c._vpRefresh); clearTimeout(c._vpRetry);
  } finally {
    globalThis.fetch = prev;
  }
});

test('the loader for the page on screen is the one read first', () => {
  const c = new HoistraLogic();
  const called = [];
  c.vpLoad = () => called.push('vp');
  c.mxLiveLoad = () => called.push('mx');
  c.setState({ view: 'vp' });
  c.loaderOnScreen()();
  c.setState({ view: 'module', module: 'ops' });
  c.loaderOnScreen()();
  c.setState({ view: 'sessions' });
  assert.equal(c.loaderOnScreen(), null);
  assert.deepEqual(called, ['vp', 'mx']);
});

// A load discarded because the company changed mid-flight reads again when nothing else does
// (2 Nov 2026: Vendors sat on "Reading…" — the stale path returned with the guard still set).
test('a Vendors load discarded as stale reads again, and the page leaves "Reading…"', async () => {
  const { setActingOrg } = await import('../src/api/client.js');
  const prev = globalThis.fetch;
  let first = true;
  globalThis.fetch = async (url) => {
    // The first summary read lands after the company changed under it.
    if (first && /saved-space\/summary/.test(url)) { first = false; setActingOrg('org-b'); }
    return { ok: true, status: 200, headers: { get: () => null }, text: async () => JSON.stringify({ ok: true, count: 0, items: [], parameters: [] }) };
  };
  try {
    const c = new HoistraLogic();
    c.setState({ signedIn: true, account: { id: 'u', email: 'r@x.y', role: 'user' }, role: 'user' });
    await c.vpLoad();
    await settle();
    assert.ok(c.state.vpRaw, 'read again for the company now in force');
    assert.equal(c.state.vpLoading, false);
    assert.equal(c._vpLoading, false);
    clearTimeout(c._vpRefresh); clearTimeout(c._vpRetry);
  } finally {
    globalThis.fetch = prev;
    setActingOrg(null);
  }
});


// Pre-push review (3 Nov 2026): the partial re-read shared vpLoad's retry timer, so a late error
// that asked for a partial re-read cancelled a full load's pending retry.
test('a partial re-read does not cancel a full load retry', () => {
  const c = new HoistraLogic();
  let fullRetried = false;
  c._vpRetry = setTimeout(() => { fullRetried = true; }, 1);
  c.setState({ vpRaw: { fetchedAt: 'now', errors: { approvals: 'timed out after 20s' } } });
  c.vpReadMissing();
  assert.ok(c._vpPartialTimer, 'the partial re-read has its own timer');
  clearTimeout(c._vpPartialTimer);
  return new Promise((r) => setTimeout(r, 10)).then(() => assert.ok(fullRetried, 'the full retry still ran'));
});
