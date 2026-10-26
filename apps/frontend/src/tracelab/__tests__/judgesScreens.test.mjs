// judgesScreens — the rendered Judges tab (views/Judges.jsx): what a company admin sees (the table and the code checks,
// read only), what the platform team sees (the judge's page with its prompt, the train answers its examples come from,
// previews, runs, triage, decision, threshold, the iteration log, the failure report), the one-way freeze dialog, the
// labels on every control, plain words for loading and empty, and what the screen does that markup cannot show: its
// buttons act, it reads when it opens and again for another company, Escape closes the dialog, and leaving the tab
// stops the run watch.
import { test, beforeEach, afterEach, after } from 'node:test';
import assert from 'node:assert/strict';

import { net, S, Jx, reqs, route, flush, jfresh, installDefaults, SUMMARY_BRIEF, SUMMARY_FULL, DRAFT, RUN_DEV, RUN_TEST, DETAIL, DETAIL_TRIAGED, FROZEN,
  DETAIL_TESTED, SPLIT, READY, READY_THIN, CHECKS, REPORT, tlStore } from './judges-helper.mjs';
import { judgesScreen, cleanJudgesScreens, view, find, findAll, text, button } from './judges-screen-helper.mjs';

const React = (await import('react')).default;
const { renderToStaticMarkup } = await import('react-dom/server');
const J = await judgesScreen('views/Judges.jsx');
const A = await import('../judgesActions.js');

after(cleanJudgesScreens);
beforeEach(installDefaults);
afterEach(() => A.stopWatch());

const NOW = Date.parse('2026-10-15T12:00:00Z');
const render = () => renderToStaticMarkup(React.createElement(J.default, { s: S(), now: NOW }));
const h = (method, path, reply) => { net.handlers[route(method, path)] = reply; };
const owner = (judges, opts) => jfresh(judges, opts);
const admin = (judges) => jfresh(judges, { owner: false });

test('a company admin sees the table and the code checks, read only, with no judge controls', () => {
  admin({ tlJudgesRows: [SUMMARY_BRIEF], tlJudgesChecks: CHECKS, tlJudgesModes: [], tlJudgesLoadedAt: 1 });
  const html = render();
  assert.ok(html.includes('Read only. The platform team builds, runs and freezes judges; this list shows how each one scores.'));
  assert.match(html, /<section[^>]*aria-label="Judges"/);
  for (const t of ['0.949 (0.831–0.986)', '0.833 (0.552–0.953)', '30 Fail · 30 Pass (minimum 30 each)', 'In use', 'Gives no answer', 'Golden and eval cases']) {
    assert.ok(html.includes(t), t);
  }
  for (const t of ['Freeze', 'Run on dev', 'Failure report', 'Failure modes', 'Write version', 'Preview']) assert.equal(html.includes(t), false, t);
  assert.equal(/<button[^>]*>invented_codes<\/button>/.test(html), false);
});

test('the platform team sees the judge page: prompt, examples with their source answers, previews, runs, triage and the iteration log', () => {
  owner({ tlJudgesRows: [SUMMARY_FULL, DRAFT], tlJudgesChecks: CHECKS, tlJudgesModes: [{ id: 'm-1', name: 'invented_codes', status: 'confirmed' }],
    tlJudgesLoadedAt: 1, tlJudgeId: 'j-2', tlJudge: DETAIL, tlJudgeLoadedAt: 1, tlJudgeRunId: 'r-dev', tlJudgeRun: RUN_DEV, tlJudgeRunLoadedAt: 1 });
  const html = render();
  assert.match(html, /<button[^>]*>invented_codes<\/button>/);
  for (const t of ['invented_codes · v2 · LLM judge', 'Fail when', 'The answer names a code that no step returned.', 'Few-shot examples, from the train answers',
    'Clear Fail', 'Source answer', 'Dev answer 5', 'Preview', 'Run on dev', 'Freeze the judge first.', 'Triage every disagreement first (1 left).',
    'Missed failure: people said Fail, the judge said Pass', 'Judge wrong', 'Label wrong', 'Definition unclear', 'Iteration log', 'Sharper Pass rule.',
    'Failure report', 'Read the failure report', 'Download (Markdown)', 'Failure modes']) {
    assert.ok(html.includes(t), t);
  }
  assert.match(html, /role="group" aria-label="Triage"/);
  assert.match(html, /<button[^>]*disabled=""[^>]*>Freeze…<\/button>/);
  assert.match(html, /<button[^>]*disabled=""[^>]*>Run held-out test \(once\)<\/button>/);
});

test('the held-out test button is disabled after one run and says so', () => {
  owner({ tlJudgeId: 'j-2', tlJudge: DETAIL_TESTED, tlJudgeLoadedAt: 1, tlJudgeRunId: 'r-test', tlJudgeRun: RUN_TEST, tlJudgeRunLoadedAt: 1 });
  const html = render();
  assert.match(html, /<button[^>]*disabled=""[^>]*>Held-out test already run<\/button>/);
  assert.ok(html.includes('The held-out test runs once per version. Write a new version to test again.'));
  assert.ok(html.includes('Held-out numbers are final for this version.'));
  assert.ok(html.includes('Record decision'));
  assert.equal(html.includes('Freeze…'), false);
  assert.equal(html.includes('aria-label="Triage"'), false);
});

test('the freeze dialog is a modal dialog that says freezing is one-way; Freeze stays off without a stop reason', () => {
  owner({ tlJudgeId: 'j-2', tlJudge: DETAIL_TRIAGED, tlJudgeLoadedAt: 1, tlJudgeFreeze: { reason: '' } });
  const html = render();
  assert.match(html, /role="dialog" aria-modal="true" aria-labelledby="tlj-freeze-title" aria-describedby="tlj-freeze-desc"/);
  assert.match(html, /<h2 id="tlj-freeze-title"[^>]*>Freeze this judge\?<\/h2>/);
  assert.ok(html.includes('Freezing is one-way.'));
  assert.match(html, /<label[^>]*for="tlj-freeze-reason"[^>]*>Why stop revising\?<\/label>/);
  assert.match(html, /<button[^>]*disabled=""[^>]*>Freeze<\/button>/);
  owner({ tlJudgeId: 'j-2', tlJudge: DETAIL_TRIAGED, tlJudgeLoadedAt: 1, tlJudgeFreeze: { reason: 'Stable on dev twice.' } });
  const ready = render();
  assert.equal(/<button[^>]*disabled=""[^>]*>Freeze<\/button>/.test(ready), false);
  assert.match(ready, /<button[^>]*>Freeze<\/button>/);
  assert.ok(ready.includes('1,980 characters left'));
});

test('the hard floor of 10 per class is on the readiness card, and again beside the override checkbox', () => {
  const say = (n) => 'Never fewer than ' + n + ' per class in dev and test, even with an override.';
  const times = (html, t) => html.split(t).length - 1;
  owner({ tlJudgeModeId: 'm-1', tlJudgeReady: READY_THIN, tlJudgeReadyLoadedAt: 1, tlJudgeSplitForm: { override: false, reason: '' } });
  const thin = render();
  assert.equal(times(thin, say(10)), 2, 'once on the card, once beside the override');
  assert.ok(thin.indexOf('Build it below the label minimum') < thin.lastIndexOf(say(10)), 'the second one follows the checkbox');
  assert.ok(thin.indexOf(say(10)) < thin.indexOf('Build it below the label minimum'), 'the first one is on the card above it');
  owner({ tlJudgeModeId: 'm-1', tlJudgeReady: READY, tlJudgeReadyLoadedAt: 1 });
  const ok = render();
  assert.equal(times(ok, say(10)), 1, 'with no override on offer it is said once, on the card');
  owner({ tlJudgeModeId: 'm-1', tlJudgeReady: Object.assign({}, READY_THIN, { minimum: { per_class: 30, eval_per_class: 12 } }), tlJudgeReadyLoadedAt: 1 });
  assert.equal(times(render(), say(12)), 2, 'the service\'s number is the one shown');
  owner({ tlJudgeModeId: 'm-1', tlJudgeReadyLoading: true });
  assert.equal(render().includes('Never fewer than'), false, 'nothing before the readiness is read');
});

test('a failure mode card and the new-version form label every control', () => {
  owner({ tlJudgeModeId: 'm-1', tlJudgeReady: READY_THIN, tlJudgeReadyLoadedAt: 1, tlJudgeSplitForm: { override: true, reason: '' } });
  const card = render();
  assert.ok(card.includes('Fail 29 · Pass 40 in separate conversations (minimum 30 each)'));
  assert.ok(card.includes('Build it below the label minimum'));
  assert.match(card, /<label[^>]*for="tlj-split-reason"/);
  assert.match(card, /<button[^>]*disabled=""[^>]*>Create the split<\/button>/);
  owner({ tlJudgeModeId: 'm-1', tlJudgeReady: READY, tlJudgeReadyLoadedAt: 1, tlJudgeSplit: SPLIT, tlJudgesChecks: CHECKS,
    tlJudgeDraft: { kind: 'llm', task: '', fail_rule: '', pass_rule: '', edge_cases: '', change_note: '', check_key: '',
      examples: { clear_fail: { turn_id: '', critique: '' }, clear_pass: { turn_id: '', critique: '' }, borderline: { turn_id: '', critique: '' } } } });
  const form = render();
  for (const id of ['tlj-fail', 'tlj-pass', 'tlj-ex-clear_fail', 'tlj-crit-borderline', 'tlj-note']) assert.match(form, new RegExp('for="' + id + '"'), id);
  assert.ok(form.includes('Version 3 · LLM judge'));
  assert.ok(form.includes('What changed from the previous version'));
  assert.ok(form.includes('Pass · Which pumps failed?'));
  assert.match(form, /<button[^>]*disabled=""[^>]*>Register this version<\/button>/);
});

test('every input on the page has a label or an aria-label, so no control is unnamed', () => {
  const pages = [
    () => owner({ tlJudgeModeId: 'm-1', tlJudgeReady: READY_THIN, tlJudgeReadyLoadedAt: 1, tlJudgeSplitForm: { override: true, reason: '' } }),
    () => owner({ tlJudgeModeId: 'm-1', tlJudgeReady: READY, tlJudgeReadyLoadedAt: 1, tlJudgeSplit: SPLIT, tlJudgesChecks: CHECKS,
      tlJudgeDraft: { kind: 'llm', task: '', fail_rule: '', pass_rule: '', edge_cases: '', change_note: '', check_key: '',
        examples: { clear_fail: { turn_id: '', critique: '' }, clear_pass: { turn_id: '', critique: '' }, borderline: { turn_id: '', critique: '' } } } }),
    () => owner({ tlJudgeModeId: 'm-1', tlJudgeReady: READY, tlJudgeReadyLoadedAt: 1, tlJudgesChecks: CHECKS,
      tlJudgeDraft: { kind: 'code', task: '', fail_rule: '', pass_rule: '', edge_cases: '', change_note: '', check_key: '', examples: {} } }),
    () => owner({ tlJudgeId: 'j-2', tlJudge: Object.assign({}, DETAIL_TESTED, { judge: Object.assign({}, DETAIL_TESTED.judge, { in_use: true, monitor_threshold: 0.2 }) }),
      tlJudgeLoadedAt: 1, tlJudgeRunId: 'r-dev', tlJudgeRun: RUN_DEV, tlJudgeRunLoadedAt: 1,
      tlJudgeEstimate: { split: 'dev', loading: false, error: '', cap: '', data: { items: 24, cached: 0, to_call: 24, usd_estimate: 0.29 } } }),
    () => owner({ tlJudgeId: 'j-2', tlJudge: DETAIL_TRIAGED, tlJudgeLoadedAt: 1, tlJudgeFreeze: { reason: '' } })
  ];
  for (const page of pages) {
    page();
    const html = render();
    const controls = [...html.matchAll(/<(input|textarea|select)\b([^>]*)>/g)];
    assert.ok(controls.length > 0);
    for (const c of controls) {
      if (/type="radio"|type="checkbox"/.test(c[2])) continue;
      const id = /\bid="([^"]+)"/.exec(c[2]);
      const named = /aria-label="[^"]+"/.test(c[2]) || (id && new RegExp('for="' + id[1] + '"').test(html));
      assert.ok(named, c[0]);
    }
  }
});

test('loading and empty states use plain words, never invented rows', () => {
  owner({ tlJudgesLoading: true });
  assert.ok(render().includes('Reading…'));
  owner({ tlJudgesRows: [], tlJudgesModes: [], tlJudgesChecks: [], tlJudgesLoadedAt: 1 });
  const empty = render();
  for (const t of ['No judges yet. Pick a failure mode below to start one.', 'No confirmed failure modes yet.', 'No code checks.']) assert.ok(empty.includes(t), t);
  assert.equal(empty.includes('<table'), false);
  admin({ tlJudgesRows: [], tlJudgesChecks: [], tlJudgesLoadedAt: 1 });
  assert.ok(render().includes('No judges yet.'));
  assert.equal(render().includes('Pick a failure mode'), false);
});

test('a read that failed says so with a Retry; a failed code-check read says so under its own table', () => {
  admin({ tlJudgesErr: 'Trace Lab is not available right now.', tlJudgesChecksErr: 'No answer for the checks.' });
  const html = render();
  assert.ok(html.includes('Trace Lab is not available right now.'));
  assert.ok(html.includes('No answer for the checks.'));
  assert.match(html, /role="alert"/);
  assert.match(html, />Retry<\/button>/);
});

test('the report section reads and downloads; it shows each failure mode with its sample fraction, never as prevalence', () => {
  owner({ tlJudgeReport: REPORT, tlJudgeReportLoadedAt: 1 });
  const html = render();
  assert.ok(html.includes('Read it again'));
  assert.ok(html.includes('12 Fail · 38 Pass · sample fraction (not prevalence) 0.240'));
  assert.ok(html.includes('Labelled counts are shares of the reviewed sample, not rates for all answers.'));
  assert.ok(html.includes('Evaluation case candidates: 21, 7'));
});

// ── what the screen does ─────────────────────────────────────────────────────────────────────────────────────────

test('a judge in the table opens its page for the platform team', async () => {
  h('GET', '/judges/j-1', [200, Object.assign({}, DETAIL, { judge: SUMMARY_FULL, runs: [] })]);
  owner({ tlJudgesRows: [SUMMARY_FULL], tlJudgesModes: [], tlJudgesChecks: CHECKS, tlJudgesLoadedAt: 1 });
  const tree = view(J.default, { s: S(), now: NOW });
  find(tree, (n) => n.type === 'button' && text(n) === 'invented_codes').props.onClick();
  await flush();
  assert.deepEqual(reqs(), ['GET /judges/j-1']);
  assert.equal(Jx().tlJudgeId, 'j-1');
});

test('a triage choice writes it and a refused one stays on screen under its answer', async () => {
  h('PATCH', '/judge-runs/r-dev/disagreements/turn-9', [409, { detail: { ok: false, error: 'This run is not a dev run.', reason: 'not_dev_run' } }]);
  owner({ tlJudgeId: 'j-2', tlJudge: DETAIL, tlJudgeLoadedAt: 1, tlJudgeRunId: 'r-dev', tlJudgeRun: RUN_DEV, tlJudgeRunLoadedAt: 1 });
  find(view(J.default, { s: S(), now: NOW }), (n) => n.type === 'button' && text(n) === 'Judge wrong').props.onClick();
  await flush();
  assert.deepEqual(net.requests.map((r) => r.method + ' ' + r.url.pathname.split('/api/trace-lab')[1]), ['PATCH /judge-runs/r-dev/disagreements/turn-9']);
  const html = render();
  assert.ok(html.includes('This run is not a dev run.'));
  assert.equal(html.includes('>Retry<'), false, 'the server\'s own rule has no Retry');
});

test('Freeze… opens the dialog, the stop reason is typed into it, Freeze sends it and Cancel closes it', async () => {
  h('POST', '/judges/j-2/freeze', [200, { ok: true, judge: FROZEN }]);
  h('GET', '/judges/j-2', [200, Object.assign({}, DETAIL_TRIAGED, { judge: FROZEN })]);
  h('GET', '/judges', [200, { ok: true, judges: [], modes: [] }]);
  h('GET', '/checks', [200, { ok: true, checks: CHECKS }]);
  owner({ tlJudgeId: 'j-2', tlJudge: DETAIL_TRIAGED, tlJudgeLoadedAt: 1, tlJudgeRunId: 'r-dev', tlJudgeRun: RUN_DEV, tlJudgeRunLoadedAt: 1 });
  button(view(J.default, { s: S(), now: NOW }), 'Freeze…').props.onClick();
  assert.deepEqual(Jx().tlJudgeFreeze, { reason: '' });
  find(view(J.default, { s: S(), now: NOW }), (n) => n.type === 'textarea' && n.props.id === 'tlj-freeze-reason').props.onChange({ target: { value: 'Dev agreement is high; no third revision.' } });
  button(view(J.default, { s: S(), now: NOW }), 'Freeze').props.onClick();
  await flush();
  assert.equal(net.requests.find((r) => r.method === 'POST').body.stop_reason, 'Dev agreement is high; no third revision.');
  assert.equal(Jx().tlJudgeFreeze, null);
  A.freezeOpen();
  button(view(J.default, { s: S(), now: NOW }), 'Cancel').props.onClick();
  assert.equal(Jx().tlJudgeFreeze, null);
});

test('Run on dev opens the estimate panel; Start sends the run; the cost cap goes with it', async () => {
  h('GET', '/judges/j-2/estimate', [200, { ok: true, items: 24, cached: 0, to_call: 24, usd_estimate: 0.288 }]);
  h('POST', '/judges/j-2/runs', [202, { ok: true, run: Object.assign({}, RUN_DEV, { id: 'r-new', status: 'done' }), job: { id: 'j1', status: 'done' } }]);
  h('GET', '/judges/j-2', [200, DETAIL]);
  owner({ tlJudgeId: 'j-2', tlJudge: DETAIL, tlJudgeLoadedAt: 1 });
  button(view(J.default, { s: S(), now: NOW }), 'Run on dev').props.onClick();
  await flush();
  const panel = renderToStaticMarkup(React.createElement(J.default, { s: S(), now: NOW }));
  assert.ok(panel.includes('24 answers: 0 already judged, 24 to judge. Estimated cost $0.288.'));
  assert.match(panel, /<label[^>]*for="tlj-cap"/);
  find(view(J.default, { s: S(), now: NOW }), (n) => n.type === 'input' && n.props.id === 'tlj-cap').props.onChange({ target: { value: '2' } });
  button(view(J.default, { s: S(), now: NOW }), 'Start').props.onClick();
  await flush();
  assert.deepEqual(net.requests.find((r) => r.method === 'POST').body, { split: 'dev', usd_cap: 2 });
});

test('Create the split and Write version buttons act on the failure mode that is open', async () => {
  h('POST', '/modes/m-1/splits', [201, { ok: true, split: SPLIT }]);
  h('GET', '/modes/m-1/judge-readiness', [200, READY]);
  h('GET', '/judges', [200, { ok: true, judges: [], modes: [] }]);
  h('GET', '/checks', [200, { ok: true, checks: CHECKS }]);
  h('GET', '/modes/m-1/splits/active', [200, { ok: true, split: SPLIT }]);
  owner({ tlJudgeModeId: 'm-1', tlJudgeReady: Object.assign({}, READY_THIN, { next_step: 'create_split', labels: READY.labels }), tlJudgeReadyLoadedAt: 1 });
  button(view(J.default, { s: S(), now: NOW }), 'Create the split').props.onClick();
  await flush();
  assert.ok(reqs().includes('POST /modes/m-1/splits'));
  assert.equal(Jx().tlJudgeReady.next_step, 'triage', 'the card was read again');
  button(view(J.default, { s: S(), now: NOW }), 'Write version 3').props.onClick();
  assert.equal(Jx().tlJudgeDraft.kind, 'llm');
  button(view(J.default, { s: S(), now: NOW }), 'Use a code check as version 3').props.onClick();
  assert.equal(Jx().tlJudgeDraft.kind, 'code');
});

// Renders the screen once with its effects held back, then runs them against a document that records its listeners.
function mount(props) {
  const saved = React.useEffect;
  const effects = [];
  const deps = [];
  React.useEffect = (fn, d) => { effects.push(fn); deps.push(d); };
  try { renderToStaticMarkup(React.createElement(J.default, props)); } finally { React.useEffect = saved; }
  const listeners = [];
  const removed = [];
  const before = globalThis.document;
  const stand = { visibilityState: 'visible', addEventListener: (t, fn) => listeners.push([t, fn]), removeEventListener: (t, fn) => removed.push([t, fn]) };
  globalThis.document = stand;
  const cleanups = effects.map((fn) => fn());
  globalThis.document = before;
  return {
    deps,
    press: (key) => listeners.filter((l) => l[0] === 'keydown').forEach((l) => l[1]({ key, stopPropagation: () => {} })),
    keyListeners: () => listeners.filter((l) => l[0] === 'keydown').length,
    unmount: () => {
      const keep = globalThis.document;
      globalThis.document = stand;
      try { cleanups.forEach((c) => { if (typeof c === 'function') c(); }); } finally { globalThis.document = keep; }
    },
    removed: () => removed.filter((r) => r[0] === 'keydown').length
  };
}

test('the tab reads the judges and the code checks when it opens, once, and again for another company', async () => {
  h('GET', '/judges', [200, { ok: true, judges: [SUMMARY_BRIEF], modes: [] }]);
  h('GET', '/checks', [200, { ok: true, checks: CHECKS }]);
  admin({});
  const m = mount({ s: S(), now: NOW });
  await flush();
  assert.deepEqual(reqs().sort(), ['GET /checks', 'GET /judges']);
  assert.deepEqual(m.deps[0], [S().seq], 'the read is tied to the company: a new seq reads again');
  assert.equal(Jx().tlJudgesRows.length, 1);
});

test('the platform team coming back to the tab reads again the failure mode and the judge that were open; a company admin reads neither', async () => {
  h('GET', '/judges', [200, { ok: true, judges: [], modes: [] }]);
  h('GET', '/checks', [200, { ok: true, checks: [] }]);
  h('GET', '/judges/j-2', [200, DETAIL]);
  h('GET', '/judge-runs/r-dev', [200, { ok: true, run: RUN_DEV }]);
  h('GET', '/modes/m-1/judge-readiness', [200, READY]);
  owner({ tlJudgeId: 'j-2', tlJudgeModeId: 'm-1' });
  const m = mount({ s: S(), now: NOW });
  await flush();
  assert.deepEqual(m.deps[1], [true, S().seq], 'tied to who the person is and to the company');
  for (const r of ['GET /judges/j-2', 'GET /modes/m-1/judge-readiness', 'GET /judges', 'GET /checks']) assert.ok(reqs().includes(r), r);
  net.requests = [];
  admin({ tlJudgeId: 'j-2', tlJudgeModeId: 'm-1' });
  mount({ s: S(), now: NOW });
  await flush();
  assert.deepEqual(reqs().sort(), ['GET /checks', 'GET /judges']);
});

test('leaving the tab, or the page, stops the run watch', () => {
  owner({ tlJudgeId: 'j-2', tlJudge: DETAIL, tlJudgeRunId: 'r-dev', tlJudgeRun: Object.assign({}, RUN_DEV, { status: 'running' }) });
  const m = mount({ s: S(), now: NOW });
  A.watchRun();
  assert.equal(A.watching(), true);
  m.unmount();
  assert.equal(A.watching(), false);
});

test('Escape closes the freeze dialog, but not while it is freezing, and the listener goes away with it', () => {
  owner({ tlJudgeId: 'j-2', tlJudge: DETAIL_TRIAGED, tlJudgeLoadedAt: 1, tlJudgeFreeze: { reason: 'Stable on dev twice.' } });
  const m = mount({ s: S(), now: NOW });
  assert.equal(m.keyListeners(), 1);
  m.press('Enter');
  assert.deepEqual(Jx().tlJudgeFreeze, { reason: 'Stable on dev twice.' });
  m.press('Escape');
  assert.equal(Jx().tlJudgeFreeze, null);
  m.unmount();
  assert.equal(m.removed(), 1);
  owner({ tlJudgeId: 'j-2', tlJudge: DETAIL_TRIAGED, tlJudgeLoadedAt: 1, tlJudgeFreeze: { reason: 'Stable on dev twice.' },
    tlJudgeWrites: { 'judges:freeze:j-2': { busy: true, error: '', conflict: false, canRetry: false } } });
  const busy = mount({ s: S(), now: NOW });
  busy.press('Escape');
  assert.deepEqual(Jx().tlJudgeFreeze, { reason: 'Stable on dev twice.' }, 'a freeze in flight is not abandoned');
  owner({ tlJudgeId: 'j-2', tlJudge: DETAIL_TRIAGED, tlJudgeLoadedAt: 1 });
  assert.equal(mount({ s: S(), now: NOW }).keyListeners(), 0, 'no dialog open, no key listener');
});
