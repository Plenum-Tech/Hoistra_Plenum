// shell — the page frame's pure helpers: the three tabs, the ?tl= link read strictly and written back without
// touching other parameters, the filters as a query, the update line, the Source options and the page notices.
import { test } from 'node:test';
import assert from 'node:assert/strict';

const shell = await import('../logic/shell.js');
const { initialState } = await import('../store.js');
const { parseLink, linkSearch, listQuery, tabsView, updateLine, channelOptions, channelLabel, noticeFor, sinceFor, isRunId,
  POLL_MS, POLLED_TABS, PAGE_SIZE, CALLS_PAGE_SIZE, TAB_LABELS } = shell;
const RUN = '3fa2c1d0-1111-4111-8111-111111111111';
const NOW = Date.parse('2026-10-15T12:00:00Z');
const hm = (ms) => { const d = new Date(ms); return String(d.getHours()).padStart(2, '0') + ':' + String(d.getMinutes()).padStart(2, '0'); };

test('the constants: 45 seconds, two polled tabs, page sizes, nine tab labels', () => {
  assert.equal(POLL_MS, 45000);
  assert.deepEqual(POLLED_TABS, ['conversations', 'runs']);
  assert.equal(PAGE_SIZE, 25);
  assert.equal(CALLS_PAGE_SIZE, 50);
  assert.deepEqual(TAB_LABELS, { conversations: 'Conversations', runs: 'Runs', golden: 'Golden set', models: 'Model calls', map: 'Map', progress: 'Progress', labels: 'Labels', judges: 'Judges', watch: 'Watch' });
});

test('the tab bar lists Conversations, Runs, Golden set, Model calls, Map, Progress, Labels, Judges and Watch with the open one marked', () => {
  assert.deepEqual(tabsView(Object.assign(initialState(), { tab: 'runs' })), [
    { key: 'conversations', label: 'Conversations', on: false }, { key: 'runs', label: 'Runs', on: true }, { key: 'golden', label: 'Golden set', on: false },
    { key: 'models', label: 'Model calls', on: false }, { key: 'map', label: 'Map', on: false }, { key: 'progress', label: 'Progress', on: false }, { key: 'labels', label: 'Labels', on: false }, { key: 'judges', label: 'Judges', on: false }, { key: 'watch', label: 'Watch', on: false }]);
});

test('a link is read strictly: a known tab, a live or uuid run, a well-formed key and answer', () => {
  assert.deepEqual(parseLink('?tl=conversations&run=live&conv=s%3Aabc&turn=turn-1'), { tab: 'conversations', run: 'live', conv: 's:abc', turn: 'turn-1' });
  assert.equal(parseLink('?tl=runs&run=' + RUN).run, RUN);
  assert.equal(parseLink('?tl=runs&run=yesterday').run, 'live');
  assert.equal(parseLink('?tl=conversations&conv=x:evil').conv, null);
  assert.equal(parseLink('?tl=conversations&conv=s%3Aa&turn=' + encodeURIComponent('<script>')).turn, null);
  assert.equal(parseLink('?tl=nowhere'), null);
  assert.equal(parseLink(''), null);
  assert.equal(isRunId('live'), true);
  assert.equal(isRunId(RUN), true);
  assert.equal(isRunId('drop table'), false);
});

test('the address bar carries tab, run, conversation and answer, keeps other parameters, and scrubs back', () => {
  const st = { tab: 'conversations', run: 'live', convKey: 's:a/b', turnId: 'turn-1' };
  assert.equal(linkSearch('?x=1', st), '?x=1&tl=conversations&run=live&conv=s%3Aa%2Fb&turn=turn-1');
  assert.equal(linkSearch('?x=1', Object.assign({}, st, { tab: 'runs' })), '?x=1&tl=runs&run=live');
  assert.equal(linkSearch('?tl=runs&run=live&x=1', null), '?x=1');
  assert.equal(linkSearch('?tl=runs', null), '');
  assert.equal(linkSearch('', { tab: 'conversations', run: RUN, convKey: null, turnId: 't9' }), '?tl=conversations&run=' + RUN);
});

test('the filters become the list query: since from the range, the run, and only the filters that are set', () => {
  const s = Object.assign(initialState(), { run: RUN, filters: { range: '30d', channel: 'upload', status: 'error', q: 'boilers' } });
  const q = listQuery(s, NOW);
  assert.equal(q.run, RUN);
  assert.equal(q.channel, 'upload');
  assert.equal(q.status, 'error');
  assert.equal(q.q, 'boilers');
  assert.equal(q.since, sinceFor('30d', NOW));
  assert.deepEqual(Object.keys(listQuery(initialState(), NOW)).sort(), ['run', 'since']);
  const d = new Date(NOW); d.setDate(d.getDate() - 7);
  assert.equal(sinceFor('7d', NOW), d.toISOString());
  assert.equal(sinceFor('nonsense', NOW), d.toISOString());
  const m = new Date(NOW); m.setHours(0, 0, 0, 0);
  assert.equal(sinceFor('today', NOW), m.toISOString());
});

test('the update line says when it last checked, how many are new, and when a check failed', () => {
  assert.equal(updateLine(initialState()), '');
  const at = NOW;
  assert.equal(updateLine(Object.assign(initialState(), { poll: { updatedAt: at, newTurns: 3, error: '' } })), 'Updated ' + hm(at) + ' · 3 new');
  assert.equal(updateLine(Object.assign(initialState(), { poll: { updatedAt: at, newTurns: 0, error: '' } })), 'Updated ' + hm(at));
  assert.equal(updateLine(Object.assign(initialState(), { poll: { updatedAt: at, newTurns: 0, error: 'down' } })), "Couldn't refresh — retry · last updated " + hm(at));
});

test('the Source filter offers the channels the period has, labelled, plus the one picked', () => {
  const s = Object.assign(initialState(), { facets: { channels: [{ value: 'chat', turns: 3 }, { value: 'report', turns: 1 }, 'ui_action'] } });
  assert.deepEqual(channelOptions(s), [{ value: '', label: 'Every source' }, { value: 'chat', label: 'Asked in the chat' },
    { value: 'report', label: 'Report' }, { value: 'ui_action', label: 'From a page' }]);
  const picked = Object.assign(initialState(), { filters: Object.assign(initialState().filters, { channel: 'upload' }) });
  assert.deepEqual(channelOptions(picked).map((o) => o.value), ['', 'upload']);
  assert.equal(channelLabel('new_kind'), 'New kind');
});

test('a refusal reason becomes a plain page notice; anything else keeps its own message', () => {
  assert.match(noticeFor('name_a_company', 'x'), /one company at a time/);
  assert.equal(noticeFor('reviewers_only', 'x'), 'Trace Lab is for company admins.');
  assert.equal(noticeFor('bad_company', 'x'), 'Trace Lab can only show your own company.');
  assert.equal(noticeFor('', 'Trace Lab is not available right now.'), 'Trace Lab is not available right now.');
});
