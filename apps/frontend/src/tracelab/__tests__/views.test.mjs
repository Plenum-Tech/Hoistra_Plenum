// views — the pure view models of the three tabs: the list pages and labels its groupings, the open conversation
// reads as chips, answers, notes, the asker's rating and timeline cards, runs name the live pseudo-run, and model
// calls read "unavailable" wherever nothing was recorded.
import { test } from 'node:test';
import assert from 'node:assert/strict';

const { groupingChip, mergeByKey, flagNotes, FLAG_NOTES, listView, convView, runsView, runOptions, callsView, callPanelView, pageNotice, isOpen } = await import('../logic/views.js');
const { initialState } = await import('../store.js');
const NOW = Date.parse('2026-10-15T12:00:00Z');
const st = (over) => Object.assign(initialState(), over || {});
const part = (key, over) => Object.assign(initialState()[key], over);
const row = (key, over) => Object.assign({ key, session_id: key.slice(2), session_kind: 'observed', session_label: 'Chat ' + key, turns_in_window: 2,
  first_question: 'Question ' + key, last_turn_at: '2026-10-15T11:00:00Z', failed_turns: 0, asker: { email: 'fm@example.com' }, flags: [] }, over || {});

test('synthetic and legacy groupings carry a chip; an observed conversation does not', () => {
  assert.equal(groupingChip({ key: 's:report-1', session_id: 'report-1', session_kind: 'synthetic' }), 'Grouped by report run');
  assert.equal(groupingChip({ key: 'r:turn-1', session_id: 'rerun', session_kind: 'synthetic' }), 'Grouped by re-run');
  assert.equal(groupingChip({ key: 's:golden-x', session_id: 'golden-x', session_kind: 'synthetic' }), 'Grouped by golden run');
  assert.equal(groupingChip({ key: 's:docs-1', session_id: 'docs-1', session_kind: 'synthetic' }), 'Grouped by document upload');
  assert.equal(groupingChip({ key: 's:skilllab-1', session_id: 'skilllab-1', session_kind: 'synthetic' }), 'Grouped by skill lab replay');
  assert.equal(groupingChip({ key: 's:hs-1', session_id: 'hs-1', session_kind: 'inferred' }), 'Legacy grouping');
  assert.equal(groupingChip({ key: 't:turn-9', session_id: null, session_kind: 'inferred' }), 'Single answer, no conversation recorded');
  assert.equal(groupingChip({ key: 's:abc', session_id: 'abc', session_kind: 'observed' }), null);
});

test('merging page one puts fresh rows first, replaces by key, keeps the rest, and never passes a page', () => {
  assert.deepEqual(mergeByKey([row('s:a'), row('s:b')], [row('s:c'), row('s:a', { turns_in_window: 3 })]).map((r) => [r.key, r.turns_in_window]),
    [['s:c', 2], ['s:a', 3], ['s:b', 2]]);
  assert.deepEqual(mergeByKey([row('s:x')], []).map((r) => r.key), ['s:x']);
  const page = Array.from({ length: 25 }, (_, i) => row('s:o' + i));
  const merged = mergeByKey(page, [row('s:new')].concat(page.slice(0, 24)));
  assert.equal(merged.length, 25);
  assert.equal(merged[0].key, 's:new');
  assert.equal(merged.some((r) => r.key === 's:o24'), false, 'the row pushed to page two leaves page one');
  assert.equal(mergeByKey([row('s:a'), row('s:b')], [row('s:c')], 2).length, 2);
});

test('flags become notes, once each, and unknown flags say nothing', () => {
  assert.deepEqual(flagNotes(['legacy_provenance', 'legacy_provenance', 'inferred_rewrite', 'unknown_flag']),
    [FLAG_NOTES.legacy_provenance, FLAG_NOTES.inferred_rewrite]);
  assert.deepEqual(flagNotes(null), []);
});

test('the list pages 25 at a time and says which page of how many', () => {
  const rows = Array.from({ length: 25 }, (_, i) => row('s:p' + i));
  const v = listView(st({ convs: part('convs', { rows, total: 30, cursors: [null, 'c2'], page: 0, loadedAt: 1 }), convKey: 's:p1' }), NOW);
  assert.equal(v.rows.length, 25);
  assert.equal(v.totalLabel, '30 conversations');
  assert.equal(v.pageLabel, 'Page 1 of 2');
  assert.equal(v.canNext, true);
  assert.equal(v.canPrev, false);
  assert.deepEqual(v.rows[1], { key: 's:p1', question: 'Question s:p1', answers: '2 answers', who: 'fm@example.com', when: v.rows[1].when, chip: null, failed: false, on: true });
  assert.match(v.rows[1].when, /^today \d\d:\d\d$/);
  const p2 = listView(st({ convs: part('convs', { rows: [row('s:z', { turns_in_window: 1, failed_turns: 1 })], total: 1, cursors: [null, 'c2'], page: 1, loadedAt: 1 }) }), NOW);
  assert.equal(p2.pageLabel, 'Page 2 of 1');
  assert.equal(p2.canPrev, true);
  assert.equal(p2.totalLabel, '1 conversation');
  assert.equal(p2.rows[0].answers, '1 answer');
  assert.equal(p2.rows[0].failed, true);
  assert.equal(listView(st({ convs: part('convs', { rows: [], loadedAt: 1 }) }), NOW).empty, true);
  assert.equal(listView(st({ convs: part('convs', { loading: true }) }), NOW).loading, true);
});

const OPEN = { key: 's:k', session_kind: 'observed', session_label: 'Boilers', turns: [
  { turn_id: 'turn-6', turn_index: 1, question: 'First?', ok: true }, { turn_id: 'turn-7', turn_index: 2, question: 'Second?', ok: false }],
  active: { id: 'turn-7', timestamp: '2026-10-15T11:00:00Z', models: ['claude-sonnet-5'], usage: { cost_usd: 0.02, latency_ms: 900, tool_calls: 1, cost_complete: false },
    meta: { channel: 'ui_action', channel_source: 'inferred', turn_index: 2, turn_count: 2, attempt: 2, asker: { email: 'fm@example.com' } },
    feedback: { rating: 'down', comment: 'Wrong period', by_asker: true }, flags: ['legacy_provenance'], cot_boundary: 'B',
    messages: [{ id: 'turn-7:r:user', role: 'user', text: 'Second?' }, { id: 'turn-7:r:answer', role: 'assistant', subtype: 'answer', text: 'Four.' }] } };

test('the open conversation reads as chips, answers, notes, the asker\'s rating and the timeline', () => {
  const c = convView(st({ convKey: 's:k', conv: { data: OPEN, loading: false, error: '', reason: '' } }), NOW);
  assert.equal(c.open, true);
  const v = c.view;
  assert.equal(v.title, 'Boilers');
  assert.deepEqual(v.chips, ['From a page', 'Answer 2 of 2', 'Attempt 2', 'Source inferred', 'claude-sonnet-5', '1 tool']);
  assert.deepEqual(v.answers.map((a) => [a.id, a.label, a.on, a.failed]), [['turn-6', 'Answer 1', false, false], ['turn-7', 'Answer 2', true, true]]);
  assert.equal(v.rating.text, 'The asker rated this answer down');
  assert.equal(v.rating.comment, 'Wrong period');
  assert.deepEqual(v.notes, [FLAG_NOTES.legacy_provenance]);
  assert.equal(v.cost, '$0.020 (partial)');
  assert.equal(v.latency, '900 ms');
  assert.deepEqual(v.cards.map((k) => k.kind), ['user', 'answer']);
});

test('a conversation that is not the selected one is not shown, and a reading or failed one says so', () => {
  assert.equal(convView(st({ convKey: 's:other', conv: { data: OPEN, loading: false, error: '', reason: '' } }), NOW).view, null);
  assert.equal(convView(st({ convKey: 's:k', conv: { data: null, loading: true, error: '', reason: '' } }), NOW).loading, true);
  assert.equal(convView(st({ convKey: 's:k', conv: { data: null, loading: false, error: 'gone', reason: '' } }), NOW).error, 'gone');
  assert.equal(convView(st(), NOW).open, false);
});

test('runs name the live pseudo-run and say when there are no golden runs yet', () => {
  const s = st({ run: 'live', runs: part('runs', { loadedAt: 1, goldenAvailable: false, rows: [{ run_id: 'live', kind: 'live', name: 'All live traffic', recorded: false,
    started_at: '2026-10-14T09:00:00Z', ended_at: '2026-10-15T11:00:00Z', conversation_count: 412, turn_count: 1290, models: ['claude-sonnet-5'], cost_usd: 61.42 }] }) });
  const v = runsView(s, NOW);
  assert.deepEqual([v.rows[0].name, v.rows[0].conversations, v.rows[0].answers, v.rows[0].cost, v.rows[0].status, v.rows[0].on], ['All live traffic', '412', '1290', '$61.42', 'Live', true]);
  assert.equal(v.goldenNote, 'No golden runs yet.');
  assert.deepEqual(runOptions(s), [{ value: 'live', label: 'All live traffic' }]);
  assert.deepEqual(runOptions(st()), [{ value: 'live', label: 'All live traffic' }]);
});

test('model calls read with an aggregate header and "unavailable" where nothing was recorded', () => {
  const s = st({ calls: part('calls', { loadedAt: 1, retentionDays: 90, cursors: [null], page: 0,
    aggregate: { calls: 3120, errors: 14, latency_p50_ms: 2210, latency_p95_ms: 9140, input_tokens: 120000, output_tokens: 3000, cost_usd: 48.11, unpriced_calls: 6 },
    rows: [
      { span_id: 'sp1', turn_id: 't1', started_at: '2026-10-15T11:00:00Z', model: 'claude-sonnet-5', label: 'tool decision', latency_ms: 1880, input_tokens: 900, output_tokens: 40, cost_usd: 0.0041, ok: true, question_excerpt: 'Boilers?' },
      { span_id: 'sp2', turn_id: 't1', started_at: '2026-10-15T11:00:01Z', model: null, label: 'planner', latency_ms: null, input_tokens: null, output_tokens: null, cost_usd: null, ok: false, question_excerpt: '' }] }),
    call: { spanId: 'sp2', loading: false, error: '', data: { model: null, label: 'planner', usage: {}, cost_status: 'unavailable', withheld: [], cot_note: 'N' }, turnId: 't1', conversationKey: 's:a' } });
  const v = callsView(s, NOW);
  assert.equal(v.header, '3,120 calls · p50 2.21 s · p95 9.14 s · 120k in · 3.0k out · $48.11 · 14 failed · 6 unpriced');
  assert.equal(v.note, 'Model calls are kept for 90 days; older calls are not in these figures.');
  assert.deepEqual([v.rows[0].purpose, v.rows[0].tokens, v.rows[0].cost, v.rows[0].on], ['tool decision', '900 in · 40 out', '$0.0041', false]);
  assert.deepEqual([v.rows[1].model, v.rows[1].purpose, v.rows[1].latency, v.rows[1].tokens, v.rows[1].cost, v.rows[1].question, v.rows[1].ok, v.rows[1].on],
    ['unavailable', 'Planner', 'unavailable', 'unavailable', 'unavailable', '—', false, true]);
  const panel = callPanelView(s);
  assert.equal(panel.card.header, 'Model call · unavailable · unavailable · unavailable · unavailable');
  assert.equal(panel.conversationKey, 's:a');
  assert.equal(callPanelView(st()), null);
});

test('a refusal on any tab becomes the page notice; other errors stay on their tab', () => {
  assert.match(pageNotice(st({ convs: part('convs', { error: 'x', reason: 'name_a_company' }) })), /one company at a time/);
  assert.equal(pageNotice(st({ calls: part('calls', { error: 'x', reason: 'reviewers_only' }) })), 'Trace Lab is for company admins.');
  assert.equal(pageNotice(st({ convs: part('convs', { error: 'timed out', reason: '' }) })), '');
});

test('what is open is read from the store, with a default for cards that start open', () => {
  const s = st({ open: { a: true, b: false } });
  assert.equal(isOpen(s, 'a', false), true);
  assert.equal(isOpen(s, 'b', true), false);
  assert.equal(isOpen(s, 'c', true), true);
  assert.equal(isOpen(s, 'c', false), false);
});
