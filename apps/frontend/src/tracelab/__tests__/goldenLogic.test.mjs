// logic/golden — the pure side of golden sets and golden runs: the state defaults, labels and rates, the run row,
// the step decision, the estimate lines, the upload report, and the view models (Golden set with expected answers
// hidden row by row, golden runs and the start dialog, one run, two runs compared, and the expected outcome that
// stays closed until revealed).
import { test } from 'node:test';
import assert from 'node:assert/strict';

const L = await import('../logic/golden.js');
const { initialState, goldenInitial } = await import('../store.js');
const NOW = Date.parse('2026-10-15T12:00:00Z');
const R1 = '6f1c2d3e-0000-4000-8000-000000000001';
const R2 = '7f1c2d3e-0000-4000-8000-000000000002';
const st = (golden, over) => Object.assign(initialState(), over || {}, { golden: Object.assign(goldenInitial(), golden || {}) });

test('the golden state is fresh every time and part of the store\'s initial state', () => {
  const a = goldenInitial();
  const b = goldenInitial();
  assert.notEqual(a.shown, b.shown);
  assert.notEqual(a.own, b.own);
  assert.notEqual(a.expected, b.expected);
  assert.deepEqual(Object.keys(a).sort(), ['action', 'cases', 'casesDraft', 'compare', 'detail', 'dialog', 'expected', 'loop', 'own',
    'pick', 'resumeCap', 'runs', 'setBusy', 'setError', 'setId', 'sets', 'shown', 'upload'].sort());
  assert.deepEqual(initialState().golden, goldenInitial());
  assert.equal(L.GOLDEN_STEP_MS, 5000);
  assert.deepEqual(L.KS, [1, 3, 5]);
  assert.equal(L.HARD_MAX_K, 20);
});

test('rates read as passes over graded with the Wilson interval as a title', () => {
  assert.equal(L.fmtRate({ passes: 9, graded: 11, rate: 0.818, wilson: [0.523, 0.949] }), '9/11 pass (82%)');
  assert.equal(L.rateTitle({ passes: 9, graded: 11, rate: 0.818, wilson: [0.523, 0.949] }), '95% interval 52–95%');
  assert.equal(L.fmtRate({ passes: 0, graded: 0, rate: null }), '—');
  assert.equal(L.fmtRate(null), '—');
  assert.equal(L.signedPts(-0.0345), '−3 pts');
  assert.equal(L.signedUsd(-0.2), '−$0.200');
  assert.equal(L.paramLabel('model'), 'Chat model');
  assert.equal(L.paramLabel('flags.hitl'), 'Chat setting');
  assert.equal(L.paramLabel('some_new_key'), 'Some new key');
});

test('a run row says its status in words, its progress, and whether this page steps it', () => {
  const run = { id: R1, label: 'run-x', status: 'paused', paused_reason: 'page_closed', set: { version: 3 }, k: 3, cases: 29,
    items_done: 12, items_total: 87, usd_spent: 1.31, pass_rate: { passes: 9, graded: 11, rate: 0.818 }, purpose: 'adhoc',
    started_by_email: 'admin@example.com', started_at: '2026-10-15T11:00:00Z' };
  const row = L.runRow(run, NOW, {});
  assert.deepEqual([row.status, row.set, row.k, row.cases, row.progress, row.cost, row.pass, row.purpose, row.who, row.mine, row.resumable],
    ['Paused: the page was closed', 'Version 3', 'k=3', '29 cases', '12/87', '$1.31', '9/11 pass (82%)', 'Ad hoc', 'admin@example.com', false, true]);
  assert.equal(L.runRow(Object.assign({}, run, { mine: true }), NOW, {}).mine, true);
  assert.equal(L.runRow(run, NOW, { [R1]: true }).mine, true, 'a run this page started or resumed is its own');
  assert.equal(L.runRow({ id: R2, status: 'running' }, NOW, {}).status, 'Running');
  assert.equal(L.runRow({ id: R2, status: 'running', set: { version: 0, kind: 'regression' } }, NOW, {}).set, 'Regression cases');
});

test('the step decision: step when idle, read while a case is answered, stop otherwise', () => {
  const run = { id: R1, status: 'running', mine: true, current: null };
  assert.equal(L.stepDecision(run, {}), 'step');
  assert.equal(L.stepDecision(Object.assign({}, run, { current: { case_key: '1', rep: 1 } }), {}), 'poll');
  assert.equal(L.stepDecision(Object.assign({}, run, { status: 'paused' }), {}), 'stop');
  assert.equal(L.stepDecision(Object.assign({}, run, { mine: false }), {}), 'stop', 'only the starter\'s page steps');
  assert.equal(L.stepDecision(Object.assign({}, run, { mine: undefined }), { [R1]: true }), 'step');
  assert.equal(L.stepDecision(null, {}), 'stop');
});

test('the estimate reads as lines, and a missing figure is left out rather than invented', () => {
  const est = { cases: 29, k: 3, messages: 87, est_answers_usd: 2.61, est_grading_usd: 0.64, est_total_usd: 3.25, est_minutes: 30,
    usd_cap_default: 4.88, run_max_usd: 25, month_spent_usd: 0, month_cap_usd: 50 };
  assert.deepEqual(L.estimateLines(est), ['29 cases × k=3 → 87 questions', 'About $3.25 ($2.61 answers + $0.640 grading)',
    'About 30 minutes', 'Spending cap $4.88 (at most $25.00)', 'This month: $0 spent of $50.00']);
  assert.deepEqual(L.estimateLines({ cases: 1, k: 1 }), ['1 case × k=1 → 1 question']);
  assert.deepEqual(L.estimateLines(null), []);
});

test('an upload report: a check says nothing is saved yet, a save names its version and the change', () => {
  const dry = L.uploadReport({ cases: 29, sheet_name: 'Golden set', header_row: 1, flags: { unverified: 1, write_intent: 2 },
    preview: [{ case_key: '1', question: 'How many floors?', evaluation: 'objective' }] }, true);
  assert.equal(dry.saved, false);
  assert.equal(dry.lines[0], '29 cases read from the sheet "Golden set", header on row 1. Nothing is saved yet.');
  assert.ok(dry.lines.includes('1 case has no Verified on date.'));
  assert.ok(dry.lines.some((l) => l.startsWith('2 cases ask for an action')));
  assert.deepEqual(dry.preview, [{ key: '1', question: 'How many floors?', evaluation: 'Objective' }]);
  const saved = L.uploadReport({ set: { id: 's3', version: 3, case_count: 29 }, diff: { previous_version: 2, added: ['30'], changed: [], removed: [] } }, false);
  assert.deepEqual(saved.lines, ['Saved as version 3 with 29 cases.', 'Against version 2: 1 added, 0 changed, 0 removed.']);
  assert.deepEqual(L.issueLines([{ row: 4, column: 'Question', message: 'The question is empty.' }, { row: null, column: null, message: 'No cases.' }]),
    ['Row 4, Question: The question is empty.', 'No cases.']);
});

const SETS = { active: { id: 's2', version: 2, status: 'active', name: 'Tower', case_count: 29, stale_count: 3, unverified_count: 1,
  created_by_email: 'admin@example.com', created_at: '2026-10-15T09:00:00Z', source_filename: 'tower.xlsx' },
  versions: [{ id: 's2', version: 2, status: 'active', case_count: 29, created_at: '2026-10-15T09:00:00Z', source_filename: 'tower.xlsx' },
    { id: 's1', version: 1, status: 'retired', case_count: 28, created_at: '2026-10-01T09:00:00Z', source_filename: 'tower.xlsx' }] };
const CASES = { total: 51, cases: [{ id: 'c1', case_key: '21', question: 'How many work orders were raised in August 2026?', followups: ['And reactive?'],
  evaluation: 'objective', verified_on: '2026-10-13', stale: false, requirement_ids: ['REQ-7'], flags: ['write_intent'],
  expected_answer: '759 raised.', expected_values: [{ raw: '759' }] }] };

test('the Golden set view: the active version, versions with their actions, and expected answers hidden row by row', () => {
  const s = st({ sets: { data: SETS, loading: false, error: '', reason: '', loadedAt: 1 }, setId: 's2',
    cases: { data: CASES, loading: false, error: '', q: '', flag: 'stale', page: 0 } });
  let v = L.goldenSetView(s, NOW);
  assert.deepEqual([v.active.version, v.active.cases, v.active.stale, v.active.unverified, v.active.file], [2, 29, 3, 1, 'tower.xlsx']);
  assert.deepEqual(v.versions.map((x) => [x.label, x.status, x.on, x.canActivate, x.canRetire]),
    [['Version 2', 'Active', true, false, true], ['Version 1', 'Retired', false, true, false]]);
  assert.equal(v.cases.rows[0].expected, null);
  assert.equal(v.cases.rows[0].values, null);
  assert.equal(JSON.stringify(v).includes('759'), false, 'nothing of the expected answer is in the view until asked');
  assert.equal(v.cases.rows[0].writeIntent, true);
  assert.deepEqual([v.cases.totalLabel, v.cases.pageLabel, v.cases.canNext, v.cases.canPrev], ['51 cases', 'Page 1 of 2', true, false]);
  assert.equal(v.cases.flags.find((f) => f.on).key, 'stale');
  s.golden.shown = { c1: true };
  v = L.goldenSetView(s, NOW);
  assert.equal(v.cases.rows[0].expected, '759 raised.');
  assert.deepEqual(v.cases.rows[0].values, ['759']);
});

test('the Golden set view: a refusal is a notice, an empty company says so, an upload\'s row errors are listed', () => {
  const refused = L.goldenSetView(st({ sets: { data: null, loading: false, error: 'Name a company.', reason: 'name_a_company', loadedAt: null } }), NOW);
  assert.match(refused.notice, /one company at a time/);
  assert.equal(refused.error, '');
  assert.equal(L.goldenSetView(st({ sets: { data: { active: null, versions: [] }, loading: false, error: '', reason: '', loadedAt: 1 } }), NOW).empty, true);
  const up = L.goldenSetView(st({ upload: { busy: false, dryRun: true, fileName: 'x.csv', error: 'Some rows need fixing.',
    errors: [{ row: 4, column: 'Question', message: 'The question is empty.' }], warnings: [], report: null } }), NOW).upload;
  assert.deepEqual([up.error, up.errors], ['Some rows need fixing.', ['Row 4, Question: The question is empty.']]);
});

test('the start dialog cannot start before its estimate, nor while preflight or a building selection refuses', () => {
  const est = { cases: 29, k: 1, est_total_usd: 1, usd_cap_default: 1.5, max_k: 3, preflight: { ok: true, problems: [] }, building_selected: false };
  const dlg = (d) => L.dialogView(st({ sets: { data: SETS, loading: false, error: '', reason: '', loadedAt: 1 },
    dialog: Object.assign({ k: 1, purpose: 'adhoc', setId: 's2', note: '', usdCap: '', estimate: null, estimating: false, starting: false, error: '', problems: [] }, d) }));
  assert.equal(dlg({}).canStart, false);
  const ok = dlg({ estimate: est });
  assert.equal(ok.canStart, true);
  assert.deepEqual(ok.ks.map((k) => k.k), [1, 3], 'no k above the service\'s maximum');
  assert.deepEqual(ok.sets.map((x) => x.label), ['Version 2 (active)', 'Version 1']);
  assert.equal(dlg({ estimate: Object.assign({}, est, { preflight: { ok: false, problems: ['Not isolated.'] } }) }).canStart, false);
  const bld = dlg({ estimate: Object.assign({}, est, { building_selected: true }) });
  assert.equal(bld.canStart, false);
  assert.match(bld.building, /All buildings/);
  assert.equal(dlg({ estimate: Object.assign({}, est, { cases: 0 }) }).canStart, false);
  assert.equal(dlg({ estimate: est, starting: true }).canStart, false);
});

test('golden runs: rows, the comparison pick, and the start dialog when open', () => {
  const s = st({ runs: { rows: [{ id: R1, label: 'b', status: 'done' }, { id: R2, label: 'a', status: 'done' }], loading: false, error: '', reason: '', loadedAt: 1 },
    pick: [R1] });
  let v = L.goldenRunsView(s, NOW);
  assert.deepEqual(v.rows.map((r) => [r.label, r.picked]), [['b', true], ['a', false]]);
  assert.equal(v.canCompare, false);
  assert.equal(v.pickNote, 'Tick one more run to compare.');
  assert.equal(v.dialog, null);
  s.golden.pick = [R1, R2];
  v = L.goldenRunsView(s, NOW);
  assert.equal(v.canCompare, true);
  assert.equal(L.goldenRunsView(st({ runs: { rows: [], loading: false, error: '', reason: '', loadedAt: 1 } }), NOW).empty, true);
});

const RUN = { id: R1, label: 'run-a', status: 'running', mine: true, k: 3, purpose: 'baseline', set: { version: 2 }, items_done: 4, items_total: 87,
  current: { case_key: '21', rep: 2 }, usd_spent: 0.5, usd_cap: 5, estimate_usd: 3.25, pass_rate: { passes: 3, graded: 4, rate: 0.75, wilson: [0.3, 0.95] },
  pass_hat_k: 0.5, pass_at_k: 0.9, outcomes: { pass: 3, fail: 1, degraded: 0 }, params: { model: 'gpt-x', judge_model: 'claude-sonnet-5', flags: { a: 1 } },
  grader_validated: false };
const DETAIL = { run: RUN, cases: [{ case_key: '21', question: 'How many?', evaluation: 'objective', stale: false, passes: 1, graded: 2,
  reps: [{ rep: 1, outcome: 'pass', usd: 0.01, ms: 1800, session_id: 'golden-6f1c2d3e-21-r1', turn_id: 't1' },
    { rep: 2, outcome: 'degraded', refused_count: 1, usd: 0.02, ms: 900, session_id: 'golden-6f1c2d3e-21-r2', turn_id: 't2' },
    { rep: 3, blind: true }] }] };

test('one run: banner, progress, tiles, outcomes, actions, the loop and the cases', () => {
  const s = st({ detail: { runId: R1, data: DETAIL, loading: false, error: '', loadedAt: 1 }, loop: { runId: R1, busy: false, error: null, note: '' } });
  const v = L.runDetailView(s, NOW);
  assert.match(v.banner, /Keep this page open/);
  assert.equal(v.progress, '4 of 87 answered · now case 21, rep 2');
  assert.deepEqual(v.tiles.map((t) => t.value), ['3/4 pass (75%)', '—', '—', '50%', '90%', '$0.500 of $5.00']);
  assert.equal(v.tiles[0].title, '95% interval 30–95%');
  assert.deepEqual(v.outcomes, ['Pass: 3', 'Fail: 1']);
  assert.ok(v.graderNote.includes('unvalidated'));
  assert.deepEqual(v.params, [{ label: 'Chat model', value: 'gpt-x' }, { label: 'Grading model', value: 'claude-sonnet-5' }]);
  assert.deepEqual([v.actions.canPause, v.actions.canResume, v.actions.canCancel, v.actions.canRegrade], [true, false, true, false]);
  assert.equal(v.loop.stepping, true);
  assert.deepEqual(v.cases[0].reps.map((r) => [r.rep, r.outcome, r.tone, r.refused]),
    [[1, 'Pass', 'pass', ''], [2, 'Degraded — a step was refused', '', '1 step refused'], [3, 'Hidden until revealed', '', '']]);
  assert.equal(v.cases[0].tally, '1/2 pass');
  assert.equal(L.runDetailView(st(), NOW), null);
  assert.equal(L.runDetailView(st({ detail: { runId: R1, data: null, loading: true, error: '', loadedAt: null } }), NOW).loading, true);
  const other = L.runDetailView(st({ detail: { runId: R1, data: { run: Object.assign({}, RUN, { mine: false }) }, loading: false, error: '', loadedAt: 1 } }), NOW);
  assert.match(other.banner, /person who started/);
  const refused = L.runDetailView(st({ detail: { runId: R1, data: { run: Object.assign({}, RUN, { status: 'paused' }) }, loading: false, error: '', loadedAt: 1 },
    loop: { runId: null, busy: false, error: { reason: 'building_selected', message: 'A building is selected.', resumable: false }, note: '' } }), NOW);
  assert.match(refused.loop.hint, /All buildings/);
  assert.deepEqual([refused.actions.canResume, refused.actions.canRegrade], [true, true]);
});

test('a judgment answer waiting for grader validation reads unvalidated, never "Not started"', () => {
  const data = { run: RUN, cases: [{ case_key: 'BG-05', question: 'Which vendors?', evaluation: 'judgment', stale: false, passes: 0, graded: 0,
    reps: [{ rep: 1, state: 'done', outcome: 'unvalidated', usd: 0.099, ms: 46768 }, { rep: 2, state: 'in_flight' }, { rep: 3, state: 'not_started' }] }] };
  const v = L.runDetailView(st({ detail: { runId: R1, data, loading: false, error: '', loadedAt: 1 } }), NOW);
  assert.deepEqual(v.cases[0].reps.map((r) => r.outcome), ['Unvalidated — the grader\'s verdict is a suggestion', 'Answering…', 'Not started']);
});

test('two runs compared: flips in words, rates with deltas, cost, what differs, and the like-for-like warning', () => {
  const data = { comparable: false, warnings: ['The two runs used different chat models.'],
    flips: { regressed: [{ case_key: '21', question: 'How many?', head: { results: [{ session_id: 'golden-x-21-r1', turn_id: 't9' }] } }], fixed: [{ case_key: '3', question: 'Postcode?' }] },
    still_passing: 27, pass_rate: { base: { passes: 28, graded: 29, rate: 0.9655 }, head: { passes: 27, graded: 29, rate: 0.931 }, delta: -0.0345 },
    pass_hat_k: { base: { k: 3, value: 0.8 }, head: { k: 3, value: 0.7 }, k_differs: false },
    cost: { base_usd: 3.1, head_usd: 2.9, delta_usd: -0.2 }, params_diff: { model: ['gpt-a', 'gpt-b'] } };
  const s = st({ runs: { rows: [{ id: R1, label: 'new' }, { id: R2, label: 'old' }], loading: false, error: '', reason: '', loadedAt: 1 },
    compare: { base: R2, head: R1, data, loading: false, error: '' } });
  const v = L.compareView(s);
  assert.deepEqual([v.base, v.head, v.comparable, v.stillPassing], ['old', 'new', false, 27]);
  assert.deepEqual(v.warnings, ['The two runs used different chat models.']);
  assert.deepEqual(v.flips.map((f) => [f.label, f.key, f.danger, f.sessionId]), [['↓ now failing', '21', true, 'golden-x-21-r1'], ['↑ now passing', '3', false, null]]);
  assert.deepEqual(v.rates.map((r) => [r.label, r.delta]), [['Pass rate (fresh cases)', '−3 pts'], ['pass^k (every rep passes)', '−10 pts'], ['Cost', '−$0.200']]);
  assert.deepEqual(v.differs, [{ label: 'Chat model', base: 'gpt-a', head: 'gpt-b' }]);
  assert.equal(L.compareView(st()), null);
});

const GOLDEN_CONV = { key: 's:golden-6f1c2d3e-21-r1', session_id: 'golden-6f1c2d3e-21-r1', turns: [], active: { id: 't9' } };
const BLIND = { ok: true, available: true, revealed: false, golden: { run: { id: R1, label: 'run-a' }, case_key: '21', rep: 1 } };
const FULL = { ok: true, available: true, revealed: true, golden: { run: { id: R1, label: 'run-a' }, case_key: '21', rep: 1, msg_idx: 0, messages_total: 2, is_final: false,
  case: { question: 'How many?', expected_answer: '759 raised.', expected_values: [{ raw: '759' }], evaluation: 'judgment', verified_on: '2026-10-13' },
  result: { outcome: 'fail', grade: { critique: 'Said 758.', validated: false, value_check: { status: 'mismatch', items: [{ raw: '759', ok: false, nearest: '758' }] } },
    refused: [{ service: 'oi', method: 'POST', path: '/x' }] } } };

test('the expected outcome: only on a golden conversation, closed and blind until revealed, then the case and the grade', () => {
  const conv = (over) => st(over && over.golden, { convKey: GOLDEN_CONV.key, turnId: 't9', conv: { data: GOLDEN_CONV, loading: false, error: '', reason: '' } });
  assert.equal(L.expectedView(st(null, { convKey: 's:chat-1', conv: { data: { key: 's:chat-1', session_id: 'chat-1', active: { id: 't1' } } } })), null);
  let v = L.expectedView(conv());
  assert.deepEqual([v.turnId, v.open, v.revealed, v.loading], ['t9', false, false, false]);
  v = L.expectedView(conv({ golden: { expected: { t9: { loading: false, data: BLIND } } } }));
  assert.deepEqual([v.open, v.run, v.caseKey], [false, 'run-a', '21']);
  assert.equal('expected' in v, false);
  v = L.expectedView(conv({ golden: { expected: { t9: { loading: false, data: FULL } } } }));
  assert.equal(v.open, true);
  assert.equal(v.expected, '759 raised.');
  assert.deepEqual(v.values, ['759']);
  assert.equal(v.outcome, 'Fail');
  assert.equal(v.critique, 'Said 758.');
  assert.deepEqual(v.checks, ['✗ 759 (closest: 758)']);
  assert.match(v.unvalidated, /not yet validated/);
  assert.match(v.finalNote, /answer 1 of 2/);
  assert.match(v.refusedLine, /^1 step that would have changed data was refused/);
  v = L.expectedView(conv({ golden: { expected: { t9: { loading: false, data: FULL, hidden: true } } } }));
  assert.deepEqual([v.open, v.revealed, 'expected' in v], [false, true, false]);
  assert.match(L.expectedView(conv({ golden: { expected: { t9: { data: { ok: true, available: false } } } } })).unavailable, /unavailable/);
});

test('four fidelity caveats, in plain words', () => {
  assert.equal(L.FIDELITY_CAVEATS.length, 4);
  assert.ok(L.FIDELITY_CAVEATS[0].startsWith('Company memories are not recalled'));
  assert.ok(L.FIDELITY_CAVEATS.every((c) => /^[A-Z].*\.$/.test(c)));
  assert.equal(L.isGoldenSession('golden-abc-1-r1'), true);
  assert.equal(L.isGoldenSession('chat-1'), false);
});
