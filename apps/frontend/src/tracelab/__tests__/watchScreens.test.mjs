// watchScreens — the rendered Watch tab (views/Watch.jsx): what the platform team sees (every company's batches, Run now with
// its plan, the batch with its flagged answers and the review-batch button, the charts with their threshold line, the
// code-check rates, the automatic-vs-people trend, and a plain line saying eval cases and release checks belong to one
// company), what a superadmin viewing a company sees (Run now and Resume for that company's own batches, and its eval cases
// and release checks), what a company admin sees (the same reads for the company, no Run now, and the eval cases and release
// checks with their buttons), plain words for loading, empty and failing, a name on every control, and what the screen does that
// markup cannot show: its buttons act on the store, it reads when it opens and again for another company, and a company
// reader's cases and release checks are read once the service has said who the person is.
import { test, beforeEach, after } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

import { net, S, Wx, route, reqs, flush, wfresh, installDefaults, setActingOrg,
  BATCHES, BATCH_DETAIL, CHART_M1, CHART_M2, CHECKS, AUTO, PLAN, CASE_PROPOSED, CASE_DRAFT, CASE_ACTIVE, CASE_CAPABILITY, RELEASES } from './watch-helper.mjs';
import { watchScreen, cleanWatchScreens, view, find, findAll, text, button } from './watch-screen-helper.mjs';

const React = (await import('react')).default;
const { renderToStaticMarkup } = await import('react-dom/server');
const W = await watchScreen('views/Watch.jsx');

after(cleanWatchScreens);
beforeEach(installDefaults);

const NOW = Date.parse('2026-10-15T12:00:00Z');
const render = (company) => renderToStaticMarkup(React.createElement(W.default, { s: S(), now: NOW, company }));
const tree = (company) => view(W.default, { s: S(), now: NOW, company });
const h = (method, path, reply) => { net.handlers[route(method, path)] = reply; };
const here = (rel) => readFileSync(new URL('../' + rel, import.meta.url), 'utf8');
const admin = (watch) => wfresh(watch, { owner: false });
const platform = (watch) => wfresh(watch, { owner: true });
const LOADED = { tlMonitorLoadedAt: 1, tlMonitorBatches: BATCHES, tlMonitorCharts: { m1: { chart: CHART_M1 }, m2: { chart: CHART_M2 } },
  tlMonitorChecks: CHECKS.checks, tlAuto: AUTO, tlAutoLoadedAt: 1 };
const COMPANY = { tlCasesLoadedAt: 1, tlCases: [CASE_PROPOSED, CASE_DRAFT, CASE_ACTIVE, CASE_CAPABILITY], tlReleaseLoadedAt: 1, tlRelease: RELEASES };
function readsOk() {
  h('GET', '/monitor/batches', [200, BATCHES]);
  h('GET', '/monitor/code-checks', [200, CHECKS]);
  h('GET', '/monitor/automation', [200, AUTO]);
  h('GET', '/monitor/chart', (u) => [200, u.searchParams.get('mode_id') === 'm1' ? CHART_M1 : CHART_M2]);
  h('GET', '/eval-cases', [200, { ok: true, cases: [CASE_ACTIVE] }]);
  h('GET', '/release-checks', [200, RELEASES]);
}

test('the platform team sees the batches, the charts, the code checks and the trend, with Run now and a line about cases', () => {
  platform(LOADED);
  const html = render('Plenum');
  for (const t of ['All companies', 'Due: 2026-W41 has not been checked yet', 'Run now', 'Monitoring batches', '2026-W41', 'Done', 'Above threshold', '$1.23',
    'Interrupted, resume it', 'Corrected failure rate by week', 'wrong_period', 'invented_codes', 'threshold 0.15', 'Code-check rates', 'Gives no answer',
    'validated', 'raw rate only', 'Eval cases', 'Release checks', 'Checked automatically vs by people', '40% automatic · 60% people', 'No answer was checked this week',
    'Eval cases and release checks belong to one company: view as a company to work on them.',
    'Automatic: golden-case checks and judges that passed their held-out test.']) {
    assert.ok(html.includes(t), t);
  }
  assert.equal(html.includes('Baseline (5 runs)'), false, 'no eval-case actions for the platform team');
  assert.equal(html.includes('Run a release check'), false);
  assert.equal(/>Resume</.test(html), false, 'across every company a company\'s interrupted batch has no Resume: it is resumed from that company');
});

test('the Due marker is a red pill when a week is due and a green one when it is up to date', () => {
  platform(Object.assign({}, LOADED, { tlMonitorBatches: { batches: [], due: { period_label: '2026-W41', is_due: true } } }));
  assert.match(render(), /class="tl-pill tl-pill-risk">Due: 2026-W41 has not been checked yet</);
  platform(Object.assign({}, LOADED, { tlMonitorBatches: { batches: [], due: { period_label: '2026-W41', is_due: false } } }));
  assert.match(render(), /class="tl-pill tl-pill-ok">Up to date: 2026-W41 checked</);
});

test('a company admin sees the same reads for the company, with no Run now and no Resume', () => {
  admin(Object.assign({}, LOADED, COMPANY));
  const html = render('Northbridge Estates Ltd');
  assert.ok(html.includes('Company: Northbridge Estates Ltd'));
  assert.equal(html.includes('Run now'), false);
  assert.equal(/>Resume</.test(html), false);
  assert.ok(html.includes('Monitoring batches'));
  assert.ok(html.includes('Corrected failure rate by week'));
  assert.equal(html.includes('view as a company'), false);
});

test('the chart draws the interval, the raw rate, the corrected rate and a dashed threshold line, with a title for each week', () => {
  platform(LOADED);
  const html = render();
  assert.match(html, /<svg[^>]*role="img"[^>]*aria-label="Corrected failure rate by week for wrong_period"/);
  assert.match(html, /stroke-dasharray="5 4"/);
  assert.ok(html.includes('threshold 0.15'));
  assert.ok(html.includes('2026-W40: 10% corrected (2% to 20%), 12% flagged in the sample'));
  assert.ok(html.includes('2026-W41: 17% corrected (6% to 28%), 19% flagged in the sample, above the threshold'));
  assert.ok(html.includes('Filled dot: the corrected failure rate, with its 95% interval as the line through it. Hollow dot: the share flagged in the sample. Dashed line: the threshold.'));
  assert.equal((html.match(/<svg/g) || []).length, 2, 'one chart per failure mode');
  assert.equal((html.match(/aria-label="Corrected failure rate by week for /g) || []).length, 2);
  assert.ok(html.includes('0%') && html.includes('17.5%') && html.includes('35%'), 'the scale is labelled');
});

test('a chart still reading, failing or with no points says so in words and draws nothing', () => {
  platform(Object.assign({}, LOADED, { tlMonitorCharts: { m1: { loading: true }, m2: { error: 'no chart for this mode' } } }));
  const html = render();
  assert.ok(html.includes('Reading…'));
  assert.ok(html.includes('no chart for this mode'));
  assert.equal(html.includes('aria-label="Corrected failure rate by week for'), false);
  platform(Object.assign({}, LOADED, { tlMonitorCharts: { m1: { chart: Object.assign({}, CHART_M1, { points: [] }) } } }));
  assert.ok(render().includes('No batches yet'));
});

test('the empty states use plain words and never invent a row', () => {
  admin({ tlMonitorLoadedAt: 1, tlMonitorBatches: { batches: [], due: { period_label: '2026-W41', is_due: false } }, tlMonitorChecks: [],
    tlAuto: { weeks: [{ week: 'w', checked_any: 0 }] }, tlAutoLoadedAt: 1, tlCasesLoadedAt: 1, tlCases: [], tlReleaseLoadedAt: 1, tlRelease: { checks: [] } });
  const html = render();
  for (const t of ['No batches yet. Monitoring has not run for this scope.', 'Up to date: 2026-W41 checked',
    'No eval cases yet. A case starts from an answer marked wrong, or from an answer a batch flagged.', 'No release check yet.', 'No answer has been checked yet.']) {
    assert.ok(html.includes(t), t);
  }
  assert.equal(html.includes('<table'), false, 'no table without a row');
  assert.equal(html.includes('aria-label="Corrected failure rate by week for'), false);
});

test('loading says Reading…, and a failed read says what failed with a Retry', () => {
  admin({ tlMonitorLoading: true, tlCasesLoading: true, tlReleaseLoading: true, tlAutoLoading: true });
  const html = render();
  assert.ok((html.match(/Reading…/g) || []).length >= 3);
  assert.equal(html.includes('No batches yet. Monitoring'), false, 'not empty, only not read yet');
  admin({ tlMonitorErr: 'Trace Lab is not available right now.', tlMonitorChecksErr: 'No answer for the checks.', tlAutoErr: 'trend is down',
    tlCasesLoadedAt: 0, tlCasesErr: 'cases are down', tlReleaseErr: 'releases are down' });
  const bad = render();
  for (const t of ['Trace Lab is not available right now.', 'No answer for the checks.', 'trend is down', 'cases are down', 'releases are down']) assert.ok(bad.includes(t), t);
  assert.match(bad, /role="alert"/);
  assert.match(bad, />Retry<\/button>/);
  assert.equal(bad.includes('No batches yet. Monitoring'), false, 'a failed read is not "no batches"');
});

const PLATFORM_INTERRUPTED = { id: 'b5', period_label: '2026-W39', status: 'interrupted', scope: 'platform', usd: 0.2, crossed: false, results: {} };

test('the batches table has a row per batch, a link to open it, and Resume only on an interrupted platform-wide one across every company', async () => {
  platform(Object.assign({}, LOADED, { tlMonitorBatches: { batches: [...BATCHES.batches, PLATFORM_INTERRUPTED], due: BATCHES.due } }));
  const t = tree();
  const rows = findAll(t, (n) => n.type === 'tr');
  assert.ok(rows.length >= 4);
  assert.equal(findAll(t, (n) => n.type === 'button' && text(n) === 'Resume').length, 1, 'b0 is a company\'s: not resumed from here');
  h('GET', '/monitor/batches/b1', [200, BATCH_DETAIL]);
  find(t, (n) => n.type === 'button' && text(n) === '2026-W41').props.onClick();
  await flush();
  assert.equal(Wx().tlMonitorBatchId, 'b1');
  assert.ok(reqs().includes('GET /monitor/batches/b1'));
  h('POST', '/monitor/batches/b5/resume', [202, { ok: true }]);
  readsOk();
  platform(Object.assign({}, LOADED, { tlMonitorBatches: { batches: [...BATCHES.batches, PLATFORM_INTERRUPTED], due: BATCHES.due } }));
  button(tree(), 'Resume').props.onClick();
  await flush();
  assert.ok(reqs().includes('POST /monitor/batches/b5/resume'));
});

test('a superadmin viewing a company starts monitoring for it, resumes its interrupted batch and opens its flagged answers', async () => {
  setActingOrg('org-a');
  admin(Object.assign({}, LOADED, COMPANY, { tlMonitorBatches: { batches: [Object.assign({}, BATCHES.batches[0], { scope: 'company' }), BATCHES.batches[1]], due: BATCHES.due } }));
  const html = render('Northbridge');
  for (const t of ['Company: Northbridge', 'Run now', 'Interrupted, resume it', 'One company', 'Eval cases', 'Baseline (5 runs)']) assert.ok(html.includes(t), t);
  assert.equal(findAll(tree('Northbridge'), (n) => n.type === 'button' && text(n) === 'Resume').length, 1);
  h('GET', '/monitor/plan', [200, PLAN]);
  button(tree('Northbridge'), 'Run now').props.onClick();
  await flush();
  assert.ok(render('Northbridge').includes('Covers Northbridge only.'));
  assert.equal(net.requests.find((r) => r.url.pathname.endsWith('/monitor/plan')).url.searchParams.get('organization_id'), 'org-a');
  h('POST', '/monitor/batches/b0/resume', [202, { ok: true }]);
  readsOk();
  button(tree('Northbridge'), 'Resume').props.onClick();
  await flush();
  assert.ok(reqs().includes('POST /monitor/batches/b0/resume'));
  admin(Object.assign({}, LOADED, { tlMonitorBatchId: 'b1', tlMonitorBatch: BATCH_DETAIL }));
  assert.ok(render('Northbridge').includes('Open flagged answers as a review batch'));
});

test('the plan of the platform team viewing every company says it covers every company', async () => {
  h('GET', '/monitor/plan', [200, PLAN]);
  platform(LOADED);
  button(tree('Plenum'), 'Run now').props.onClick();
  await flush();
  assert.ok(render('Plenum').includes('Covers every company.'));
});

test('a company admin reads the company\'s own batches, its Due marker and its charts, and is offered nothing to start', () => {
  const own = Object.assign({}, BATCHES.batches[0], { scope: 'company' });
  admin(Object.assign({}, LOADED, COMPANY, { tlMonitorBatches: { batches: [own, BATCHES.batches[1]], due: { period_label: '2026-W41', is_due: false } },
    tlMonitorBatchId: 'b1', tlMonitorBatch: BATCH_DETAIL }));
  const html = render('Northbridge');
  for (const t of ['Company: Northbridge', 'Up to date: 2026-W41 checked', 'Monitoring batches', 'One company', 'Interrupted, resume it', 'wrong_period',
    'Corrected failure rate by week', 'threshold 0.15', 'Code-check rates']) assert.ok(html.includes(t), t);
  for (const gone of ['Run now', 'Open flagged answers as a review batch', 'Run monitoring now']) assert.equal(html.includes(gone), false, gone);
  assert.equal(/>Resume</.test(html), false);
  admin({ tlMonitorLoadedAt: 1, tlMonitorBatches: { batches: [], due: { period_label: '2026-W41', is_due: true } }, tlMonitorCharts: {} });
  const empty = render('Northbridge');
  assert.ok(empty.includes('No batches yet. Monitoring has not run for this scope.'));
  assert.match(empty, /class="tl-pill tl-pill-risk">Due: 2026-W41 has not been checked yet</);
  assert.equal(empty.includes('Run now'), false);
});

test('an open batch shows its failure modes with rates, intervals and the threshold, and its flagged answers', () => {
  platform(Object.assign({}, LOADED, { tlMonitorBatchId: 'b1', tlMonitorBatch: BATCH_DETAIL }));
  const html = render();
  for (const t of ['Batch 2026-W41', 'Corrected rate', '95% interval', 'Flagged in the sample', 'Answers judged', '17%', '6% to 28%', '19%', 'threshold 0.15',
    'Open flagged answers as a review batch', 'Flagged answers', 'How many work orders in July?', 'wrong_period · uniform sample', 'Used June.', 'List the overdue AHUs']) {
    assert.ok(html.includes(t), t);
  }
  assert.match(html, /aria-label="Close batch"/);
  assert.equal((html.match(/Open flagged answers as a review batch/g) || []).length, 1, 'only for a failure mode with flagged answers');
});

test('a batch with no flagged answers says so, and one still reading says Reading…', () => {
  platform({ tlMonitorBatchId: 'b1', tlMonitorBatch: { batch: BATCHES.batches[1], flagged: [] } });
  assert.ok(render().includes('No flagged answers in this batch.'));
  platform({ tlMonitorBatchId: 'b1', tlMonitorBatchLoading: true });
  assert.ok(render().includes('Reading…'));
  platform({ tlMonitorBatchId: 'b1', tlMonitorBatchErr: 'No such batch.' });
  assert.match(render(), /role="alert"[^>]*>No such batch\./);
});

test('the review-batch button opens the review batches, then the batch says how many were opened', async () => {
  h('POST', '/monitor/batches/b1/review-batch', [201, { ok: true, review_batch_ids: ['rb-1', 'rb-2'] }]);
  platform(Object.assign({}, LOADED, { tlMonitorBatchId: 'b1', tlMonitorBatch: BATCH_DETAIL }));
  button(tree(), 'Open flagged answers as a review batch').props.onClick();
  await flush();
  assert.deepEqual(net.requests.filter((r) => r.method === 'POST').map((r) => r.body), [{ mode_id: 'm1' }]);
  const html = render();
  assert.ok(html.includes('Review batch opened (2)'));
  assert.equal(html.includes('Open flagged answers as a review batch'), false, 'it cannot be opened twice from here');
  admin(Object.assign({}, LOADED, { tlMonitorBatchId: 'b1', tlMonitorBatch: BATCH_DETAIL }));
  assert.equal(render().includes('Open flagged answers as a review batch'), false, 'a company admin does not open review batches');
});

test('a flagged answer is a link into its conversation for a company reader, and plain text with the reason for the platform team', async () => {
  h('GET', '/turns/t1', [200, { ok: true, trace: {}, conversation_key: 's:abc' }]);
  admin({ tlMonitorBatchId: 'b1', tlMonitorBatch: BATCH_DETAIL });
  find(tree(), (n) => n.type === 'button' && text(n) === 'How many work orders in July?').props.onClick();
  await flush();
  assert.equal(S().tab, 'conversations');
  platform({ tlMonitorBatchId: 'b1', tlMonitorBatch: BATCH_DETAIL });
  const html = render();
  assert.ok(html.includes('How many work orders in July?'));
  assert.equal(findAll(tree(), (n) => n.type === 'button' && text(n) === 'How many work orders in July?').length, 0, 'no link without a company');
  assert.ok(html.includes('View as a company to open this answer.'));
});

test('closing the batch closes it', () => {
  platform(Object.assign({}, LOADED, { tlMonitorBatchId: 'b1', tlMonitorBatch: BATCH_DETAIL }));
  button(tree(), 'Close').props.onClick();
  assert.equal(Wx().tlMonitorBatchId, null);
});

test('Run now opens the plan: its numbers, the judges in use, the warnings, and a rate and a cost cap to type', async () => {
  h('GET', '/monitor/plan', [200, PLAN]);
  platform(LOADED);
  button(tree(), 'Run now').props.onClick();
  await flush();
  assert.equal(Wx().tlMonitorPlanOpen, true);
  const html = render();
  for (const t of ['Run monitoring now', '2026-W41: 412 chat answers. Every one gets the code checks; 100 are drawn at random for the judges, plus 20 from the risk groups.',
    'Judges in use: wrong_period, invented_codes-v1. Estimated cost: $1.50.', 'Two modes have no judge yet.']) {
    assert.ok(html.includes(t), t);
  }
  assert.match(html, /<label[^>]*>Sample rate <input/);
  assert.match(html, /<label[^>]*>Cost cap \(\$\) <input/);
  assert.match(html, /<button[^>]*>Start<\/button>/);
});

test('the plan has Start off until it is read, shows a refused rate, and Start sends the batch and closes the plan', async () => {
  platform(Object.assign({}, LOADED, { tlMonitorPlanOpen: true, tlMonitorPlanLoading: true }));
  assert.match(render(), /<button[^>]*disabled=""[^>]*>Start<\/button>/);
  platform(Object.assign({}, LOADED, { tlMonitorPlanOpen: true, tlMonitorPlan: PLAN, tlMonitorPlanErr: 'Sample rate is a number above 0 and up to 1, for example 0.25.' }));
  assert.ok(render().includes('Sample rate is a number above 0 and up to 1, for example 0.25.'));
  h('POST', '/monitor/batches', [202, { ok: true, batch: { id: 'b9' } }]);
  readsOk();
  platform(Object.assign({}, LOADED, { tlMonitorPlanOpen: true, tlMonitorPlan: PLAN, tlMonitorPlanDraft: { rate: '0.5', cap: '3' } }));
  const t = tree();
  assert.equal(find(t, (n) => n.type === 'input' && n.props.placeholder === '0.25').props.value, '0.5');
  button(t, 'Start').props.onClick();
  await flush();
  assert.deepEqual(net.requests.find((r) => r.method === 'POST').body, { sample_rate: 0.5, usd_cap: 3 });
  assert.equal(Wx().tlMonitorPlanOpen, false);
  platform(Object.assign({}, LOADED, { tlMonitorPlanOpen: true, tlMonitorPlan: PLAN, tlWatchWrites: { 'watch:start': { busy: true, error: '', conflict: false, canRetry: false } } }));
  assert.match(render(), /<button[^>]*disabled=""[^>]*>Starting…<\/button>/);
});

test('typing a rate and a cap, Close and the rate box losing focus all reach the plan', async () => {
  h('GET', '/monitor/plan', [200, PLAN]);
  platform(Object.assign({}, LOADED, { tlMonitorPlanOpen: true, tlMonitorPlan: PLAN }));
  find(tree(), (n) => n.type === 'input' && n.props.placeholder === '0.25').props.onChange({ target: { value: '0.4' } });
  find(tree(), (n) => n.type === 'input' && n.props.placeholder === '5').props.onChange({ target: { value: '2' } });
  assert.deepEqual(Wx().tlMonitorPlanDraft, { rate: '0.4', cap: '2' });
  find(tree(), (n) => n.type === 'input' && n.props.placeholder === '0.25').props.onBlur();
  await flush();
  assert.equal(net.requests.find((r) => r.url.pathname.endsWith('/monitor/plan')).url.searchParams.get('rate'), '0.4');
  button(tree(), 'Close').props.onClick();
  assert.equal(Wx().tlMonitorPlanOpen, false);
});

test('the errors of the writes show on the tab, with Retry or Reload where they apply', () => {
  platform({ tlWatchWrites: {
    'watch:start': { busy: false, error: 'Could not save — over the cost cap', conflict: false, canRetry: true },
    'watch:retire:c3': { busy: false, error: 'Changed in another tab — reload to see the latest.', conflict: true, canRetry: false } } });
  const html = render();
  assert.ok(html.includes('Could not save — over the cost cap'));
  assert.ok(html.includes('Changed in another tab — reload to see the latest.'));
  assert.match(html, />Retry<\/button>/);
  assert.match(html, />Reload<\/button>/);
});

test('eval cases show their status, kind, pass rates and only the buttons their status allows', () => {
  admin(Object.assign({}, LOADED, COMPANY));
  const html = render();
  for (const t of ['e-7f3a19c2 v1', 'How many work orders in July?', 'Proposed: confirm the failure', 'Not baselined', 'Ready for a baseline', 'Active', 'Regression',
    'Capability (baseline 40%)', 'pass@1', 'pass@3', 'pass@5', 'pass^k', 'Confirm the failure', 'Baseline (5 runs)', 'Repeat', 'Retire', '100%', '90%', '60%']) {
    assert.ok(html.includes(t), t);
  }
  assert.equal((html.match(/Confirm the failure/g) || []).length, 1, 'only the proposed case');
  assert.equal((html.match(/Baseline \(5 runs\)/g) || []).length, 1, 'only the draft case');
  assert.equal(html.includes('title="pass^'), false, 'no title on a rate that has no run length');
  assert.match(html, /<textarea[^>]*aria-label="What a right answer says"/);
  assert.match(html, /<select[^>]*aria-label="How the answer is checked"/);
  assert.ok(html.includes('Against this exact answer') && html.includes('Against this as a criterion'));
  assert.match(html, /<input[^>]*aria-label="Times to repeat"/);
  assert.match(html, /<input[^>]*aria-label="Why retire it"/);
});

test('the case buttons act: confirm needs the typed answer, baseline and repeat open their run, retire needs a reason', async () => {
  admin(Object.assign({}, LOADED, COMPANY));
  button(tree(), 'Confirm the failure').props.onClick();
  await flush();
  assert.match(Wx().tlCasesError || Wx().tlCasesErr, /right answer/);
  assert.match(render(), /Write what a right answer says before confirming\./);
  find(tree(), (n) => n.type === 'textarea').props.onChange({ target: { value: 'There were 786.' } });
  find(tree(), (n) => n.type === 'select').props.onChange({ target: { value: 'judgment' } });
  h('POST', '/eval-cases/c1/confirm', [200, { ok: true }]);
  h('GET', '/eval-cases', [200, { ok: true, cases: [] }]);
  button(tree(), 'Confirm the failure').props.onClick();
  await flush();
  assert.deepEqual(net.requests.find((r) => r.method === 'POST').body, { base: 2, expected_answer: 'There were 786.', evaluation: 'judgment' });
  admin(Object.assign({}, LOADED, COMPANY));
  h('POST', '/eval-cases/c2/baseline', [202, { ok: true, golden_run: { id: '44444444-4444-4444-8444-444444444444' } }]);
  h('GET', '/golden-runs/44444444-4444-4444-8444-444444444444', [200, { ok: true, run: { id: '44444444-4444-4444-8444-444444444444', status: 'queued' } }]);
  h('GET', '/runs', [200, { ok: true, runs: [] }]);
  h('GET', '/golden-runs', [200, { ok: true, runs: [] }]);
  button(tree(), 'Baseline (5 runs)').props.onClick();
  await flush();
  assert.equal(S().tab, 'runs');
  assert.equal(S().golden.detail.runId, '44444444-4444-4444-8444-444444444444');
  admin(Object.assign({}, LOADED, COMPANY));
  h('POST', '/eval-cases/c2/repeat', [202, { ok: true, golden_run: { id: '44444444-4444-4444-8444-444444444444' } }]);
  h('POST', '/eval-cases/c3/retire', [200, { ok: true }]);
  const repeatButtons = findAll(tree(), (n) => n.type === 'button' && text(n) === 'Repeat');
  assert.equal(repeatButtons.length, 3, 'a draft, an active and a capability case with a golden twin');
  const retireButtons = findAll(tree(), (n) => n.type === 'button' && text(n) === 'Retire');
  retireButtons[2].props.onClick();
  await flush();
  assert.match(Wx().tlCasesErr, /why the case is retired/);
});

test('release checks list the decision, the reason, the runs per case, and offer the JSON and Markdown report and the run', async () => {
  admin(Object.assign({}, LOADED, COMPANY));
  const html = render('Northbridge Estates Ltd');
  for (const t of ['release-1', '✓ Pass', '✗ Blocked', '… Incomplete', 'Two regression cases failed a run.', 'Download JSON', 'Download Markdown', 'Run a release check',
    'Include capability cases', 'Runs per case', 'Regression cases must pass every run; capability cases never block. Attach the JSON report to the deploy.']) {
    assert.ok(html.includes(t), t);
  }
  assert.match(html, /<input[^>]*type="checkbox"[^>]*checked/);
  h('GET', '/release-checks/11111111-1111-4111-8111-111111111111', [200, '{"ok":true}']);
  const keep = globalThis.document;
  globalThis.document = { visibilityState: 'visible', body: { appendChild() {} }, createElement: () => ({ click() {}, remove() {} }) };
  try {
    const downloads = findAll(tree('Northbridge Estates Ltd'), (n) => n.type === 'button' && text(n) === 'Download Markdown');
    assert.equal(downloads.length, 3, 'one per release check');
    downloads[0].props.onClick();
    await flush();
  } finally {
    globalThis.document = keep;
  }
  assert.equal(net.requests[0].url.searchParams.get('company_name'), 'Northbridge Estates Ltd');
  assert.equal(net.requests[0].url.searchParams.get('format'), 'md');
  h('POST', '/release-checks', [202, { ok: true, golden_run: { id: '44444444-4444-4444-8444-444444444444' } }]);
  h('GET', '/golden-runs/44444444-4444-4444-8444-444444444444', [200, { ok: true, run: { id: '44444444-4444-4444-8444-444444444444', status: 'queued' } }]);
  h('GET', '/runs', [200, { ok: true, runs: [] }]);
  h('GET', '/golden-runs', [200, { ok: true, runs: [] }]);
  button(tree(), 'Run a release check').props.onClick();
  await flush();
  assert.deepEqual(net.requests.find((r) => r.method === 'POST').body, { k: 5, include_capability: true });
  assert.equal(S().tab, 'runs');
});

test('the release form types its count, switches capability cases off, and says why a count was refused', () => {
  admin(Object.assign({}, LOADED, COMPANY, { tlReleaseErr: 'A release check runs each case 1 to 10 times.' }));
  assert.ok(render().includes('A release check runs each case 1 to 10 times.'));
  find(tree(), (n) => n.type === 'input' && n.props.value === '5' && !n.props.type).props.onChange({ target: { value: '3' } });
  find(tree(), (n) => n.type === 'input' && n.props.type === 'checkbox').props.onChange();
  assert.deepEqual(Wx().tlReleaseDraft, { k: '3', include_capability: false });
});

test('the platform team is told, in the two company sections, why they have no buttons there', () => {
  platform(Object.assign({}, LOADED, COMPANY));
  const html = render();
  assert.equal((html.match(/Eval cases and release checks belong to one company/g) || []).length, 2);
  assert.equal(html.includes('release-1'), false, 'no release rows without a company');
  assert.equal(html.includes('e-7f3a19c2'), false, 'no case rows without a company');
});

test('until the service has said who the person is, the company sections read Reading… and offer nothing', () => {
  wfresh(Object.assign({}, LOADED), { owner: null });
  const html = render();
  assert.equal(html.includes('Run now'), false);
  assert.equal(html.includes('Run a release check'), false);
  assert.equal(html.includes('view as a company'), false);
  assert.ok(html.includes('Reading…'));
});

test('when who the person is cannot be read, the page says so with a Retry in place of a Reading… that never ends', async () => {
  readsOk();
  wfresh(Object.assign({}, LOADED), { owner: null, tax: { tlModesErr: 'modes are down' } });
  const html = render();
  assert.ok(html.includes('Could not tell who you are — modes are down'));
  assert.match(html, /role="alert"/);
  assert.match(html, />Retry<\/button>/);
  assert.equal(html.includes('Reading…'), false, 'no endless Reading… in the company sections');
  assert.equal(html.includes('Run now'), false, 'nothing role-only is offered meanwhile');
  assert.equal(html.includes('view as a company'), false);
  assert.equal((html.match(/Not shown until the page can tell who you are\./g) || []).length, 2, 'the two company sections say why they are empty');
  h('GET', '/modes', [200, { ok: true, modes: [], is_owner: true }]);
  button(tree(), 'Retry').props.onClick();
  await flush();
  assert.ok(reqs().includes('GET /modes'), 'Retry reads who the person is again');
  assert.equal(S().taxonomy.tlModesErr, '');
  const after = render();
  assert.equal(after.includes('Could not tell who you are'), false, 'a good read takes the message away');
  assert.ok(after.includes('Run now'), 'the platform team now has Run now');
  assert.ok(after.includes('All companies'));
});

test('while the identity read is being retried the page reads Reading…, not the old error', () => {
  wfresh(Object.assign({}, LOADED), { owner: null, tax: { tlModesErr: '', tlModesLoading: true } });
  const html = render();
  assert.equal(html.includes('Could not tell who you are'), false);
  assert.ok(html.includes('Reading…'));
});

test('the trend shows a bar per week split between automatic and people, with a table of the numbers', () => {
  platform(LOADED);
  const html = render();
  assert.match(html, /role="img"[^>]*aria-label="2026-W41: 40% checked automatically, 60% by people"/);
  for (const t of ['Answers checked', 'Golden answers right', 'Fair-sample share right (sample fraction)', 'Failure modes automated', '50% of 2 (10% to 90%)', '1 of 3']) {
    assert.ok(html.includes(t), t);
  }
  assert.match(html, /width:40%/);
  assert.match(html, /width:60%/);
});

test('the Refresh button reads everything again', async () => {
  readsOk();
  platform(LOADED);
  button(tree(), 'Refresh').props.onClick();
  await flush();
  for (const r of ['GET /monitor/batches', 'GET /monitor/code-checks', 'GET /monitor/automation']) assert.ok(reqs().includes(r), r);
});

// Renders the screen once with its effects held back, then runs them.
function mount(props) {
  const saved = React.useEffect;
  const effects = [];
  const deps = [];
  React.useEffect = (fn, d) => { effects.push(fn); deps.push(d); };
  try { renderToStaticMarkup(React.createElement(W.default, props)); } finally { React.useEffect = saved; }
  const cleanups = effects.map((fn) => fn());
  return { deps, cleanups };
}

test('the tab reads the batches, the code checks, the trend and the charts when it opens, tied to the company', async () => {
  readsOk();
  platform({});
  const m = mount({ s: S(), now: NOW });
  await flush();
  assert.deepEqual(reqs().sort(), ['GET /monitor/automation', 'GET /monitor/batches', 'GET /monitor/chart', 'GET /monitor/chart', 'GET /monitor/code-checks']);
  assert.deepEqual(m.deps[0], [S().seq], 'a new seq reads again');
  assert.equal(Wx().tlMonitorBatches.batches.length, 2);
});

test('a company reader\'s cases and release checks are read once the service has said who the person is, tied to who and to the company', async () => {
  readsOk();
  admin({});
  const m = mount({ s: S(), now: NOW });
  await flush();
  assert.deepEqual(m.deps[1], [true, S().seq]);
  for (const r of ['GET /eval-cases', 'GET /release-checks']) assert.ok(reqs().includes(r), r);
  net.requests = [];
  platform({});
  const p = mount({ s: S(), now: NOW });
  await flush();
  assert.deepEqual(p.deps[1], [false, S().seq]);
  assert.equal(reqs().some((r) => /eval-cases|release-checks/.test(r)), false, 'the platform team reads neither');
});

test('every class the Watch screen uses is in the stylesheet', () => {
  const css = here('styles.css');
  const jsx = here('views/Watch.jsx');
  const used = new Set();
  for (const m of jsx.matchAll(/className=(?:"([^"]+)"|\{'([^']*)'|\{"([^"]*)")/g)) (m[1] || m[2] || m[3]).split(/\s+/).filter((t) => t.startsWith('tl-')).forEach((t) => used.add(t));
  // And every other string that names classes (a conditional, a lookup table).
  for (const m of jsx.matchAll(/'(tl-[\w -]+)'/g)) m[1].split(/\s+/).forEach((t) => used.add(t));
  assert.ok(used.size > 20 && used.has('tl-pill-warn') && used.has('tl-chart-dot-risk'), [...used].join(' '));
  const missing = [...used].filter((t) => !new RegExp('\\.' + t.replace(/[-]/g, '\\-') + '(?![\\w-])').test(css));
  assert.deepEqual(missing, []);
});

test('every input on the page has a label or an aria-label, so no control is unnamed', () => {
  const pages = [
    () => platform(Object.assign({}, LOADED, { tlMonitorPlanOpen: true, tlMonitorPlan: PLAN })),
    () => admin(Object.assign({}, LOADED, COMPANY))
  ];
  for (const page of pages) {
    page();
    const html = render();
    const controls = [...html.matchAll(/<(input|textarea|select)\b([^>]*)>/g)];
    assert.ok(controls.length > 0);
    for (const c of controls) {
      const id = /\bid="([^"]+)"/.exec(c[2]);
      const labelled = /aria-label="[^"]+"/.test(c[2]) || (id && new RegExp('for="' + id[1] + '"').test(html));
      const wrapped = new RegExp('<label[^>]*>[^<]*(?:<[^/][^>]*>[^<]*)*' + c[0].replace(/[.*+?^${}()|[\]\\]/g, '\\$&')).test(html);
      assert.ok(labelled || wrapped, c[0]);
    }
  }
});

test('the Watch screen reads only through its own actions and names no company or internal service', () => {
  const jsx = here('views/Watch.jsx');
  const imports = [...jsx.matchAll(/^import .* from '([^']+)';$/gm)].map((m) => m[1]);
  assert.deepEqual(imports, ['react', '../watchActions.js', '../logic/watch.js']);
  assert.equal(/fetch\(|apiFetch|currentOrgId|ORG_ID|organization_id/.test(jsx), false);
  for (const f of ['watchActions.js', 'logic/watch.js', 'logic/watchChart.js', 'logic/watchCommon.js', 'watchApi.js']) {
    assert.equal(/currentOrgId|ORG_ID/.test(here(f)), false, f + ' names no company of its own');
  }
  for (const f of ['views/Watch.jsx', 'logic/watch.js', 'logic/watchChart.js']) {
    for (const bad of [/\bsvc-[a-z]/i, /plenum_/, /\bUDR\b/, /deep-?agents/, /judge:/, /code:[a-z]/]) assert.doesNotMatch(here(f), bad, f);
  }
  for (const text of ['No batches yet', 'Run now', 'Open flagged answers as a review batch', 'Baseline (5 runs)', 'Run a release check',
    'Checked automatically vs by people', 'strokeDasharray']) assert.ok(jsx.includes(text), text);
});
