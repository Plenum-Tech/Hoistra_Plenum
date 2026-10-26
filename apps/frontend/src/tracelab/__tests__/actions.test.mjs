// actions — the page's reads against a mocked service: cursor paging, the 45-second check that merges news and
// never closes what is open, replies dropped after a company switch, the app's stale-scope error kept quiet, a
// refusal kept as a reason for the page notice, the model-call panel, runs, and the address bar. Every request
// is a GET to /backend/trace-lab/api/trace-lab/...
import { test, beforeEach } from 'node:test';
import assert from 'node:assert/strict';

const replaced = [];
globalThis.window = {
  location: { origin: 'http://test.local', pathname: '/', search: '' },
  history: { replaceState: (st, t, url) => { replaced.push(url); const u = new URL(url, 'http://test.local'); window.location.pathname = u.pathname; window.location.search = u.search; } }
};
globalThis.document = { visibilityState: 'visible' };
let handlers = {};
let requests = [];
globalThis.fetch = async (url, opts) => {
  const u = new URL(String(url));
  requests.push({ method: (opts && opts.method) || 'GET', url: u });
  const h = handlers[u.pathname];
  const out = typeof h === 'function' ? await h(u) : (h || [404, { detail: 'not mocked' }]);
  return { ok: out[0] >= 200 && out[0] < 300, status: out[0], statusText: String(out[0]), text: async () => JSON.stringify(out[1]), headers: { get: () => null } };
};

const act = await import('../actions.js');
const { tlStore, initialState, resetForCompany } = await import('../store.js');
const { setActingOrg } = await import('../../api/client.js');
const P = '/backend/trace-lab/api/trace-lab';
const tick = () => new Promise((r) => setTimeout(r, 0));
const settle = async () => { for (let i = 0; i < 5; i += 1) await tick(); };
const S = () => tlStore.get();
const row = (key, over) => Object.assign({ key, session_id: key.slice(2), session_kind: 'observed', session_label: 'Chat ' + key, turns_in_window: 2,
  first_question: 'Question ' + key, last_turn_at: '2026-10-15T11:00:00Z', failed_turns: 0, asker: { email: 'fm@example.com' }, flags: [] }, over || {});
const fresh = (over) => tlStore.set(Object.assign(initialState(), { seq: S().seq + 1 }, over || {}));

beforeEach(() => {
  handlers = {}; requests = []; replaced.length = 0;
  window.location.search = ''; window.location.pathname = '/';
  document.visibilityState = 'visible';
  setActingOrg(null);
  fresh();
});

test('the list pages by cursor, 25 at a time, and every request is a GET under the service prefix', async () => {
  handlers[P + '/conversations'] = (u) => {
    assert.equal(u.searchParams.get('limit'), '25');
    assert.equal(u.searchParams.get('run'), 'live');
    assert.ok(u.searchParams.get('since'));
    return u.searchParams.get('cursor') === 'c2'
      ? [200, { ok: true, total: 30, next_cursor: null, watermark: 'w', conversations: Array.from({ length: 5 }, (_, i) => row('s:p2-' + i)) }]
      : [200, { ok: true, total: 30, next_cursor: 'c2', watermark: 'w', conversations: Array.from({ length: 25 }, (_, i) => row('s:p1-' + i)) }];
  };
  await act.loadConversations();
  assert.equal(S().convs.rows.length, 25);
  assert.deepEqual(S().convs.cursors, [null, 'c2']);
  assert.equal(S().convs.watermark, 'w');
  act.pageConversations(1);
  await settle();
  assert.equal(requests.at(-1).url.searchParams.get('cursor'), 'c2');
  assert.equal(S().convs.page, 1);
  assert.equal(S().convs.rows[0].key, 's:p2-0');
  act.pageConversations(1);
  await settle();
  assert.equal(S().convs.page, 1, 'no page past the last');
  act.pageConversations(-1);
  await settle();
  assert.equal(requests.at(-1).url.searchParams.has('cursor'), false);
  assert.equal(S().convs.page, 0);
  assert.ok(requests.every((r) => r.method === 'GET' && r.url.pathname.startsWith(P + '/')));
});

test('auto-refresh merges new rows by key and never closes the open conversation, its panels or the model-call panel', async () => {
  const open = { key: 's:a', session_label: 'Chat a', turns: [], active: { id: 't1', meta: {}, messages: [], usage: {}, flags: [], cot_boundary: 'x' } };
  const call = { spanId: 'sp1', loading: false, error: '', data: { label: 'tool decision' }, turnId: 't1', conversationKey: 's:a' };
  fresh({ convs: Object.assign(initialState().convs, { rows: [row('s:a'), row('s:b')], watermark: 'w0', loadedAt: 1 }), convKey: 's:a', turnId: 't1',
    conv: { data: open, loading: false, error: '', reason: '' }, open: { 't1:root:user': true }, call });
  handlers[P + '/conversations/updates'] = (u) => { assert.equal(u.searchParams.get('watermark'), 'w0'); return [200, { ok: true, new_turns: 2, new_conversations: 1 }]; };
  handlers[P + '/conversations'] = [200, { ok: true, total: 3, next_cursor: null, watermark: 'w1', conversations: [row('s:c'), row('s:a', { turns_in_window: 3 })] }];
  await act.poll(true);
  await settle();
  assert.deepEqual(S().convs.rows.map((r) => r.key), ['s:c', 's:a', 's:b']);
  assert.equal(S().convs.rows[1].turns_in_window, 3);
  assert.equal(S().conv.data, open);
  assert.equal(S().convKey, 's:a');
  assert.deepEqual(S().open, { 't1:root:user': true });
  assert.equal(S().call, call);
  assert.equal(S().poll.newTurns, 2);
  assert.equal(S().convs.watermark, 'w1');
  assert.equal(requests.some((r) => r.url.pathname.startsWith(P + '/conversations/s')), false, 'the open conversation is not read again');
});

test('the check runs only on a visible Conversations or Runs tab, and not past page one', async () => {
  fresh({ convs: Object.assign(initialState().convs, { watermark: 'w0', page: 1, cursors: [null, 'c2'] }) });
  handlers[P + '/conversations/updates'] = [200, { ok: true, new_turns: 4 }];
  await act.poll(false);
  assert.deepEqual(requests.map((r) => r.url.pathname), [P + '/conversations/updates'], 'not on page one: no list read');
  requests = [];
  document.visibilityState = 'hidden';
  await act.poll(false);
  assert.equal(requests.length, 0);
  document.visibilityState = 'visible';
  tlStore.set({ tab: 'models' });
  await act.poll(false);
  assert.equal(requests.length, 0);
  tlStore.set({ tab: 'runs' });
  handlers[P + '/runs'] = [200, { ok: true, golden_available: false, runs: [{ run_id: 'live', kind: 'live' }] }];
  handlers[P + '/golden-runs'] = [200, { ok: true, runs: [] }];
  await act.poll(false);
  assert.deepEqual(requests.map((r) => r.url.pathname).sort(), [P + '/golden-runs', P + '/runs']);
  assert.ok(S().poll.updatedAt);
  handlers[P + '/runs'] = [503, { detail: { reason: 'unavailable', error: 'Trace Lab is not available right now.' } }];
  tlStore.set({ tab: 'conversations', convs: Object.assign(initialState().convs, { watermark: 'w0' }) });
  handlers[P + '/conversations/updates'] = [503, { detail: { reason: 'unavailable', error: 'Trace Lab is not available right now.' } }];
  await act.poll(true);
  assert.equal(S().poll.error, 'Trace Lab is not available right now.');
});

test('a reply that lands after a company switch is dropped, and the app\'s stale-scope error stays quiet', async () => {
  let release;
  handlers[P + '/conversations'] = () => new Promise((r) => { release = () => r([200, { ok: true, total: 1, conversations: [row('s:old')] }]); });
  const pending = act.loadConversations();
  await tick();
  resetForCompany();
  release();
  await pending;
  assert.equal(S().convs.rows, null);
  const pending2 = act.loadConversations();
  await tick();
  setActingOrg('org-2');
  release();
  await pending2;
  assert.equal(S().convs.error, '');
  assert.equal(S().convs.rows, null);
});

test('a refusal keeps its reason for the page notice; a superadmin viewing as a company names it', async () => {
  handlers[P + '/conversations'] = [400, { detail: { reason: 'name_a_company', error: 'Name a company.' } }];
  await act.loadConversations();
  assert.equal(S().convs.reason, 'name_a_company');
  assert.equal(S().convs.error, 'Name a company.');
  setActingOrg('org-viewed');
  handlers[P + '/runs'] = [200, { ok: true, runs: [] }];
  await act.loadRuns();
  assert.equal(requests.at(-1).url.searchParams.get('organization_id'), 'org-viewed');
});

test('opening a conversation reads it with its answer; the same conversation keeps its open panels, another clears them', async () => {
  const conv = (key, turn) => ({ key, session_label: 'C', turns: [{ turn_id: 't1', turn_index: 1 }, { turn_id: 't2', turn_index: 2 }], active: { id: turn, messages: [] } });
  handlers[P + '/conversations/s%3Ak'] = (u) => [200, { ok: true, conversation: conv('s:k', u.searchParams.get('turn') || 't2') }];
  handlers[P + '/conversations/s%3Aother'] = [200, { ok: true, conversation: conv('s:other', 't1') }];
  await act.openConversation('s:k');
  assert.equal(S().convKey, 's:k');
  assert.equal(S().turnId, 't2');
  act.setOpen('t2:root:user', true);
  act.openTurn('t1');
  await settle();
  assert.equal(requests.at(-1).url.searchParams.get('turn'), 't1');
  assert.equal(S().turnId, 't1');
  assert.deepEqual(S().open, { 't2:root:user': true });
  await act.openConversation('s:other');
  assert.deepEqual(S().open, {});
  assert.equal(S().conv.data.key, 's:other');
});

test('a model call opens in the side panel, and "Open in Conversations" lands on its answer', async () => {
  handlers[P + '/model-calls'] = [200, { ok: true, next_cursor: null, retention_days: 90, aggregate: { calls: 1 }, calls: [{ span_id: 'sp1' }] }];
  handlers[P + '/model-calls/sp1'] = [200, { ok: true, model_call: { label: 'x' }, turn_id: 't7', conversation_key: 's:k' }];
  handlers[P + '/conversations'] = [200, { ok: true, total: 0, conversations: [], watermark: 'w' }];
  handlers[P + '/conversations/facets'] = [200, { ok: true, channels: [] }];
  handlers[P + '/runs'] = [200, { ok: true, runs: [] }];
  handlers[P + '/conversations/s%3Ak'] = (u) => { assert.equal(u.searchParams.get('turn'), 't7'); return [200, { ok: true, conversation: { key: 's:k', turns: [], active: { id: 't7', messages: [] } } }]; };
  tlStore.set({ tab: 'models' });
  await act.loadCalls();
  assert.equal(S().calls.retentionDays, 90);
  await act.openCall('sp1');
  assert.deepEqual(S().call, { spanId: 'sp1', loading: false, error: '', data: { label: 'x' }, turnId: 't7', conversationKey: 's:k' });
  act.openInConversations('s:k', 't7');
  await settle();
  assert.equal(S().tab, 'conversations');
  assert.equal(S().call, null);
  assert.equal(S().convKey, 's:k');
  assert.equal(S().turnId, 't7');
  act.closeCall();
  assert.equal(S().call, null);
});

test('picking a run opens its conversations; filters and the search reset paging', async () => {
  handlers[P + '/conversations'] = [200, { ok: true, total: 0, conversations: [], watermark: 'w' }];
  handlers[P + '/conversations/facets'] = [200, { ok: true, channels: [] }];
  handlers[P + '/runs'] = [200, { ok: true, runs: [] }];
  fresh({ tab: 'runs', convKey: 's:x', convs: Object.assign(initialState().convs, { page: 2, cursors: [null, 'a', 'b'] }) });
  act.pickRun('live');
  await settle();
  assert.equal(S().tab, 'conversations');
  assert.equal(S().run, 'live');
  assert.equal(S().convKey, null);
  assert.equal(S().convs.page, 0);
  act.setRun('not-a-run');
  assert.equal(S().run, 'live');
  act.setQueryDraft('boilers');
  assert.equal(S().filters.q, '');
  act.search();
  await settle();
  assert.equal(S().filters.q, 'boilers');
  assert.equal(requests.filter((r) => r.url.pathname === P + '/conversations').at(-1).url.searchParams.get('q'), 'boilers');
  act.setFilter({ range: '30d' });
  assert.equal(S().filters.range, '30d');
  assert.equal(S().filters.q, 'boilers');
});

test('the address bar follows the page and is scrubbed when it closes; a link is applied to the store', () => {
  window.location.search = '?keep=1';
  act.applyLink({ tab: 'conversations', run: 'live', conv: 's:abc', turn: 'turn-1' });
  act.syncUrl();
  assert.equal(window.location.search, '?keep=1&tl=conversations&run=live&conv=s%3Aabc&turn=turn-1');
  tlStore.set({ tab: 'runs' });
  act.syncUrl();
  assert.equal(window.location.search, '?keep=1&tl=runs&run=live');
  act.scrubUrl();
  assert.equal(window.location.search, '?keep=1');
  act.applyLink(null);
  assert.equal(S().tab, 'runs');
});

test('setTab reads the new tab and closes the model-call panel; an unknown tab is ignored', async () => {
  handlers[P + '/runs'] = [200, { ok: true, runs: [] }];
  tlStore.set({ call: { spanId: 'x' } });
  act.setTab('runs');
  await settle();
  assert.equal(S().tab, 'runs');
  assert.equal(S().call, null);
  assert.ok(requests.some((r) => r.url.pathname === P + '/runs'));
  act.setTab('nowhere');
  assert.equal(S().tab, 'runs');
});
