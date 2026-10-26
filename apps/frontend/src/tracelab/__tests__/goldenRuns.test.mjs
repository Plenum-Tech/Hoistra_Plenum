// The Runs tab with golden runs, rendered to markup: the live row and the golden table (tick to compare, open a
// run), the start dialog that cannot start before its estimate and refuses with a building selected, one run
// (banner, tiles with the interval as a title, Pause, reps with Open answer), two runs compared (flips in words),
// and the page that pauses its run when it closes or the company changes.
import { test, after } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { bundle, cleanBundles } from './esbuild-helper.mjs';

after(cleanBundles);
const React = (await import('react')).default;
const { renderToStaticMarkup } = await import('react-dom/server');
const { initialState, goldenInitial } = await import('../store.js');
const { default: Runs } = await bundle('views/Runs.jsx');

const NOW = Date.parse('2026-10-15T12:00:00Z');
const R1 = '6f1c2d3e-0000-4000-8000-000000000001';
const R2 = '7f1c2d3e-0000-4000-8000-000000000002';
const LIVE = { rows: [{ run_id: 'live', kind: 'live', name: 'All live traffic', conversation_count: 4, turn_count: 9, cost_usd: 0.5 },
  { run_id: R1, kind: 'golden', name: 'run-b' }], goldenAvailable: true, loading: false, error: '', reason: '', loadedAt: 1 };
const GRUNS = [{ id: R1, label: 'run-b', status: 'running', mine: true, k: 3, cases: 29, items_done: 12, items_total: 87, usd_spent: 1.31,
  pass_rate: { passes: 9, graded: 11, rate: 0.818, wilson: [0.523, 0.949] }, purpose: 'baseline', set: { version: 2 }, started_by_email: 'admin@example.com', started_at: '2026-10-15T11:00:00Z' },
  { id: R2, label: 'run-a', status: 'done', k: 1, cases: 29, usd_spent: 3.1, purpose: 'adhoc', set: { version: 2 }, started_at: '2026-10-14T11:00:00Z' }];
const golden = (over) => Object.assign(goldenInitial(), { runs: { rows: GRUNS, loading: false, error: '', reason: '', loadedAt: 1 } }, over || {});
const page = (g) => renderToStaticMarkup(React.createElement(Runs, { s: Object.assign(initialState(), { tab: 'runs', runs: LIVE, golden: g }), now: NOW }));

test('the live run and the golden runs, with pass rates, progress and the compare pick', () => {
  const html = page(golden({ pick: [R1] }));
  assert.ok(html.includes('All live traffic'));
  assert.equal((html.match(/>run-b</g) || []).length, 1, 'a golden run is listed once, in the golden table');
  assert.match(html, /title="95% interval 52–95%">9\/11 pass \(82%\)</);
  assert.ok(html.includes('12/87'));
  assert.ok(html.includes('Running · this page'));
  assert.ok(html.includes('Tick one more run to compare.'));
  assert.match(html, /<button type="button" class="tl-btn" disabled="">Compare the two ticked<\/button>/);
  assert.match(html, /<input type="checkbox" aria-label="Compare run-b" checked=""/);
  assert.ok(html.includes('Start a golden run'));
  assert.ok(page(golden({ runs: { rows: [], loading: false, error: '', reason: '', loadedAt: 1 } })).includes('No golden runs yet.'));
  assert.match(page(golden({ runs: { rows: null, loading: false, error: 'x', reason: 'name_a_company', loadedAt: null } })), /role="status">Trace Lab reviews one company at a time/);
});

const DIALOG = { k: 1, purpose: 'adhoc', setId: 's2', note: '', usdCap: '', estimate: null, estimating: true, starting: false, error: '', problems: [] };
const EST = { cases: 29, k: 1, messages: 29, est_total_usd: 1.2, est_minutes: 10, usd_cap_default: 1.8, max_k: 20, preflight: { ok: true, problems: [] }, building_selected: false };

test('the start dialog estimates first, then can start; preflight problems and a selected building refuse it', () => {
  let html = page(golden({ dialog: DIALOG }));
  assert.match(html, /role="dialog" aria-modal="true" aria-label="Start a golden run"/);
  assert.ok(html.includes('Estimating…'));
  assert.match(html, /<button type="button" class="tl-btn tl-btn-primary" disabled="">Start<\/button>/);
  html = page(golden({ dialog: Object.assign({}, DIALOG, { estimating: false, estimate: EST, usdCap: '1.8' }) }));
  assert.ok(html.includes('29 cases × k=1 → 29 questions'));
  assert.match(html, /<button type="button" class="tl-btn tl-btn-primary">Start<\/button>/);
  assert.match(html, /aria-pressed="true"[^>]*>once</);
  assert.ok(html.includes('5 times'));
  html = page(golden({ dialog: Object.assign({}, DIALOG, { estimating: false, estimate: Object.assign({}, EST, { preflight: { ok: false, problems: ['The activity log is on.'] } }) }) }));
  assert.ok(html.includes('Trace Lab cannot run safely right now:'));
  assert.ok(html.includes('<li>The activity log is on.</li>'));
  assert.match(html, /disabled="">Start</);
  html = page(golden({ dialog: Object.assign({}, DIALOG, { estimating: false, estimate: Object.assign({}, EST, { building_selected: true }) }) }));
  assert.ok(html.includes('Choose &quot;All buildings&quot; in the building picker'));
  assert.match(html, /disabled="">Start</);
});

const DETAIL = { run: Object.assign({}, GRUNS[0], { usd_cap: 5, current: { case_key: '21', rep: 2 }, outcomes: { pass: 9, fail: 2 }, pass_hat_k: 0.5, pass_at_k: 0.9 }),
  cases: [{ case_key: '21', question: 'How many work orders?', evaluation: 'objective', passes: 1, graded: 2,
    reps: [{ rep: 1, outcome: 'pass', usd: 0.01, ms: 1800, session_id: 'golden-6f1c2d3e-21-r1', turn_id: 't1' }, { rep: 2, outcome: 'fail', usd: 0.02, ms: 900, session_id: 'golden-6f1c2d3e-21-r2', turn_id: 't2' }] }] };

test('one run: the keep-open banner, tiles, Pause, and each rep with its outcome and Open answer', () => {
  const html = page(golden({ detail: { runId: R1, data: DETAIL, loading: false, error: '', loadedAt: 1 }, loop: { runId: R1, busy: false, error: null, note: 'A case is being answered…' } }));
  assert.ok(html.includes('‹ All runs'));
  assert.match(html, /role="status">Keep this page open while the run is going/);
  assert.ok(html.includes('A case is being answered…'));
  assert.ok(html.includes('12 of 87 answered · now case 21, rep 2'));
  assert.match(html, /title="95% interval 52–95%"><span class="tl-meta">Pass rate \(fresh cases\)<\/span><strong>9\/11 pass \(82%\)<\/strong>/);
  assert.ok(html.includes('pass^3 (every rep passes)'));
  assert.ok(html.includes('>Pause<'));
  assert.ok(html.includes('Cancel the run'));
  assert.equal(html.includes('>Resume<'), false);
  assert.ok(html.includes('Rep 1 · Pass · $0.010 · 1.80 s'));
  assert.ok(html.includes('Rep 2 · Fail'));
  assert.equal((html.match(/>Open answer</g) || []).length, 2);
  assert.equal(html.includes('Golden runs'), false, 'the open run replaces the list');
});

test('a stopped run offers Resume with a new cap and says why it stopped', () => {
  const run = Object.assign({}, DETAIL.run, { status: 'capped', paused_reason: 'run_cap', current: null });
  const html = page(golden({ detail: { runId: R1, data: { run, cases: [] }, loading: false, error: '', loadedAt: 1 },
    loop: { runId: null, busy: false, error: { reason: 'run_cap', message: 'This run has reached its spending cap.', resumable: true }, note: '' } }));
  assert.ok(html.includes('Stopped at its spending cap'));
  assert.match(html, /role="alert">This run has reached its spending cap\. Resume carries on\.</);
  assert.ok(html.includes('New cap ($)'));
  assert.ok(html.includes('>Resume<'));
  assert.ok(html.includes('Grade what is waiting'));
  assert.ok(html.includes('No case has been answered yet.'));
});

test('two runs compared: the like-for-like warning, rates with their change, and flips in words', () => {
  const data = { comparable: false, warnings: ['The two runs used different chat models.'],
    flips: { regressed: [{ case_key: '21', question: 'How many?', head: { results: [{ session_id: 'golden-6f1c2d3e-21-r1', turn_id: 't9' }] } }] },
    still_passing: 27, pass_rate: { base: { passes: 28, graded: 29, rate: 0.9655 }, head: { passes: 27, graded: 29, rate: 0.931 }, delta: -0.0345 },
    cost: { base_usd: 3.1, head_usd: 2.9, delta_usd: -0.2 }, params_diff: { model: ['gpt-a', 'gpt-b'] } };
  const html = page(golden({ compare: { base: R2, head: R1, data, loading: false, error: '' } }));
  assert.ok(html.includes('run-a → run-b'));
  assert.match(html, /role="status">These runs are not like for like:/);
  assert.ok(html.includes('−3 pts'));
  assert.ok(html.includes('<strong>↓ now failing</strong> · case 21 · How many?'));
  assert.ok(html.includes('27 cases passed in both.'));
  assert.ok(html.includes('gpt-a → gpt-b'));
});

test('the page pauses the run it steps when it closes or the company changes', () => {
  const src = readFileSync(new URL('../TraceLabApp.jsx', import.meta.url), 'utf8');
  assert.ok(src.includes("import * as gact from './goldenActions.js';"));
  assert.ok(src.includes('return () => { clearInterval(timer); gact.leavePage(); resetForCompany(); act.scrubUrl(); };'));
  assert.ok(src.includes('    gact.leavePage();\n    resetForCompany();\n    act.loadTab();\n'));
});
