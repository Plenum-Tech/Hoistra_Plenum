// judgesVals — the Judges tab's view models (logic/judges.js judgesVals): what a company admin reads (the table, rates
// with their intervals, labels against the minimum, nothing to click), what the platform team gets on top (failure
// modes, a readiness card, the new-version form, a judge's page with its previews, runs, triage, decision, threshold
// and iteration log, the failure report), the one-way freeze dialog, and the buttons, each of which calls the action
// of that name. A missing rate reads "—"; nothing on screen comes from anywhere but the state.
import { test, beforeEach } from 'node:test';
import assert from 'node:assert/strict';

import { jfresh, installDefaults, S, SUMMARY_BRIEF, SUMMARY_FULL, DRAFT, RUN_DEV, RUN_TEST, DETAIL, DETAIL_TRIAGED, DETAIL_TESTED, SPLIT, READY, READY_THIN,
  CHECKS, REPORT } from './judges-helper.mjs';
const A = await import('../judgesActions.js');
const V = await import('../logic/judges.js');
const { fmtWhen } = await import('../../logic/tracesPage.js');

beforeEach(installDefaults);

const NOW = Date.parse('2026-10-15T12:00:00Z');
const vals = (a) => V.judgesVals(S(), a || A, NOW);
// An actions stand-in that records which action a button called, with its arguments.
const recorder = () => {
  const calls = [];
  const a = new Proxy({}, { get: (_, name) => (...args) => { calls.push([name].concat(args)); return Promise.resolve({ ok: true }); } });
  return { a, calls };
};

test('a company admin reads the table: rates with intervals, labels against the minimum, nothing to click', () => {
  jfresh({ tlJudgesRows: [SUMMARY_BRIEF], tlJudgesModes: [{ id: 'm-1', name: 'invented_codes', status: 'confirmed' }],
    tlJudgesChecks: CHECKS, tlJudgesLoadedAt: 1, tlJudgeId: 'j-2', tlJudge: DETAIL }, { owner: false });
  const v = vals();
  assert.equal(v.tljOwner, false);
  assert.equal(v.tljReadOnlyNote, 'Read only. The platform team builds, runs and freezes judges; this list shows how each one scores.');
  assert.deepEqual(v.tljRows.map((r) => [r.mode, r.kind, r.version, r.status, r.devTpr, r.devTnr, r.testTpr, r.testTnr, r.labels, r.inUse, r.decision]), [
    ['invented_codes', 'LLM judge', 'v1', 'Tested', '0.949 (0.831–0.986)', '0.909 (0.623–0.984)', '0.947 (0.827–0.985)', '0.833 (0.552–0.953)',
      '30 Fail · 30 Pass (minimum 30 each)', true, 'Use for monitoring']]);
  assert.equal(v.tljRows[0].open, null);
  assert.deepEqual(v.tljModes, []);
  assert.deepEqual([v.tljReady, v.tljDraft, v.tljJudge, v.tljRun, v.tljReport], [null, null, null, null, null]);
  assert.deepEqual(v.tljChecks.map((x) => [x.label, x.runsOn, x.version]),
    [['Gives no answer', 'Every answer', 'v1'], ['Misses an expected figure', 'Golden and eval cases', 'v1']]);
});

test('a superadmin viewing a company is read only too, even if the list that names the platform team was read before', () => {
  jfresh({ tlJudgesRows: [SUMMARY_BRIEF], tlJudgesLoadedAt: 1, tlJudgeId: 'j-2', tlJudge: DETAIL }, { owner: true });
  assert.equal(vals().tljOwner, true);
  // View as a company: the page switches company (seq moves) and the list is read again; until then nothing owner-only shows.
  jfresh({ tlJudgesRows: [SUMMARY_BRIEF], tlJudgesLoadedAt: 1, tlJudgeId: 'j-2', tlJudge: DETAIL }, { owner: null });
  const v = vals();
  assert.equal(v.tljOwner, false);
  assert.equal(v.tljRows[0].open, null);
  assert.equal(v.tljJudge, null);
});

test('nothing is invented: Reading… before the first read, plain words when empty, a dash for a missing rate', () => {
  jfresh({ tlJudgesLoading: true });
  const first = vals();
  assert.deepEqual([first.tljLoading, first.tljChecksLoading, first.tljEmpty, first.tljRows], [true, true, false, []]);
  jfresh({ tlJudgesRows: [], tlJudgesModes: [], tlJudgesChecks: [], tlJudgesLoadedAt: 1 });
  const empty = vals();
  assert.deepEqual([empty.tljEmpty, empty.tljModesEmpty, empty.tljChecksEmpty], [true, true, true]);
  assert.equal(empty.tljEmptyText, 'No judges yet. Pick a failure mode below to start one.');
  jfresh({ tlJudgesRows: [], tlJudgesLoadedAt: 1 }, { owner: false });
  assert.equal(vals().tljEmptyText, 'No judges yet.');
  jfresh({ tlJudgesRows: [Object.assign({}, SUMMARY_FULL, { dev: null, test: null })], tlJudgesLoadedAt: 1 });
  const row = vals().tljRows[0];
  assert.deepEqual([row.devTpr, row.devTnr, row.testTpr, row.testTnr], ['—', '—', '—', '—']);
  jfresh({ tlJudgesLoading: true, tlJudgesRows: [SUMMARY_FULL], tlJudgesLoadedAt: 1 });
  const refreshing = vals();
  assert.deepEqual([refreshing.tljLoading, refreshing.tljRows.length], [false, 1]);
  assert.equal(typeof refreshing.tljRows[0].open, 'function');
});

test('a row below the minimum says so; a build below it shows the badge the service sent; a code check reads as one', () => {
  jfresh({ tlJudgesRows: [Object.assign({}, SUMMARY_BRIEF, { labels: { fail: 12, pass: 30 }, minimum: 30, min_labels_override: 'Built below the label minimum: Pilot volume.',
    kind: 'code', display_status: 'draft', decision: 'do_not_use', in_use: false })], tlJudgesLoadedAt: 1 });
  const r = vals().tljRows[0];
  assert.equal(r.belowMinimum, true);
  assert.equal(r.badge, 'Built below the label minimum: Pilot volume.');
  assert.deepEqual([r.kind, r.status, r.decision, r.inUse], ['Code check', 'Draft', 'Do not use', false]);
});

test('the readiness card: where the mode stands, the next step, the split controls and the versions', () => {
  jfresh({ tlJudgeModeId: 'm-1', tlJudgeReady: READY_THIN, tlJudgeReadyLoadedAt: 1 });
  const thin = vals().tljReady;
  assert.equal(thin.name, 'invented_codes');
  assert.equal(thin.labels, 'Fail 29 · Pass 40 in separate conversations (minimum 30 each)');
  assert.equal(thin.nextStep, 'Label more answers: each class needs 30 answers from separate conversations.');
  assert.deepEqual([thin.canSplit, thin.splitBelowMin, thin.splitReady, thin.canRegister, thin.split], [true, true, false, false, null]);
  jfresh({ tlJudgeModeId: 'm-1', tlJudgeReady: READY_THIN, tlJudgeReadyLoadedAt: 1, tlJudgeSplitForm: { override: true, reason: 'Pilot volume; approved by Hussain.' } });
  assert.equal(vals().tljReady.splitReady, true);
  jfresh({ tlJudgeModeId: 'm-1', tlJudgeReady: READY_THIN, tlJudgeReadyLoadedAt: 1, tlJudgeSplitForm: { override: true, reason: '   ' } });
  assert.equal(vals().tljReady.splitReady, false, 'an override needs its reason');
  jfresh({ tlJudgeModeId: 'm-1', tlJudgeReady: READY, tlJudgeReadyLoadedAt: 1, tlJudgeId: 'j-2' });
  const ok = vals().tljReady;
  assert.equal(ok.split.text, 'Split v1 (Fail / Pass): train 6 / 6 · dev 12 / 12 · test 12 / 12');
  assert.equal(ok.nextStep, 'Triage the dev disagreements.');
  assert.equal(ok.excluded, '2 labelled answers left out: another answer from the same conversation already counts, or the answer can no longer be read.');
  assert.equal(ok.trainOnly, '3 labels approved in bulk can join the train answers only.');
  assert.deepEqual([ok.canSplit, ok.canRegister, ok.nextVersion], [false, true, 3]);
  assert.deepEqual(ok.versions.map((x) => [x.label, x.on]), [['v1 · LLM judge · Tested', false], ['v2 · LLM judge · On dev', true]]);
});

test('the readiness card says the hard floor: never fewer than 10 per class in dev and test, even with an override', () => {
  const say = (n) => 'Never fewer than ' + n + ' per class in dev and test, even with an override.';
  jfresh({ tlJudgeModeId: 'm-1', tlJudgeReady: READY, tlJudgeReadyLoadedAt: 1 });
  assert.equal(vals().tljReady.floor, say(10), 'the service sends eval_per_class 10');
  jfresh({ tlJudgeModeId: 'm-1', tlJudgeReady: READY_THIN, tlJudgeReadyLoadedAt: 1 });
  assert.equal(vals().tljReady.floor, say(10), 'it is said while the label count is below the minimum too');
  jfresh({ tlJudgeModeId: 'm-1', tlJudgeReady: Object.assign({}, READY_THIN, { minimum: { per_class: 30, eval_per_class: 12 } }), tlJudgeReadyLoadedAt: 1 });
  assert.equal(vals().tljReady.floor, say(12), 'the service\'s number wins');
  jfresh({ tlJudgeModeId: 'm-1', tlJudgeReady: Object.assign({}, READY_THIN, { minimum: { per_class: 30 } }), tlJudgeReadyLoadedAt: 1 });
  assert.equal(vals().tljReady.floor, say(10), 'a reply with no floor falls back to 10');
  jfresh({ tlJudgeModeId: 'm-1', tlJudgeReadyLoading: true });
  assert.equal(vals().tljReady.floor, '', 'nothing is said before the readiness is read');
});

test('a card still being read says so, and takes its name from the failure-mode list until the readiness arrives', () => {
  jfresh({ tlJudgeModeId: 'm-1', tlJudgeReadyLoading: true, tlJudgesModes: [{ id: 'm-1', name: 'invented_codes', status: 'confirmed' }] });
  const c = vals().tljReady;
  assert.deepEqual([c.loading, c.name, c.labels, c.nextStep, c.canSplit, c.canRegister], [true, 'invented_codes', '—', '—', false, false]);
  jfresh({ tlJudgeModeId: 'm-1', tlJudgeReadyErr: 'down', tlJudgeReadyLoadedAt: 1 });
  assert.equal(vals().tljReady.error, 'down');
});

test('the judge page: prompt parts, examples with their train answers, five previews, the run buttons', () => {
  jfresh({ tlJudgeId: 'j-2', tlJudge: DETAIL, tlJudgeLoadedAt: 1, tlJudgesChecks: CHECKS,
    tlJudgePreview: { 'td-1': { verdict: 'Pass', critique: 'Every code came from a step.', usd: 0, truncated: false } } });
  const j = vals().tljJudge;
  assert.equal(j.title, 'invented_codes · v2 · LLM judge');
  assert.equal(j.status, 'On dev');
  assert.deepEqual(j.parts.map((p) => p.label), ['Fail when', 'Pass when', 'Edge cases']);
  assert.deepEqual(j.examples.map((e) => [e.role, e.verdict]), [['Clear Fail', 'Fail'], ['Clear Pass', 'Pass'], ['Borderline', 'Fail']]);
  assert.ok(j.examples[0].source.startsWith('QUESTION\nWhich AHUs are overdue?'));
  assert.deepEqual(j.preview.map((p) => p.label), ['Dev answer 1', 'Dev answer 2', 'Dev answer 3', 'Dev answer 4', 'Dev answer 5']);
  assert.equal(j.preview[0].result, 'Pass — Every code came from a step. ($0)');
  assert.equal(j.preview[1].result, '');
  assert.deepEqual([j.dev.label, j.dev.enabled], ['Run on dev', true]);
  assert.deepEqual([j.test.label, j.test.enabled, j.test.note], ['Run held-out test (once)', false, 'Freeze the judge first.']);
  assert.deepEqual([j.freeze.can, j.freeze.note], [false, 'Triage every disagreement first (1 left).']);
  assert.deepEqual([j.decision, j.threshold], [null, null]);
  assert.deepEqual(j.iterations[1], { version: 'v2', change: 'Sharper Pass rule.', devRuns: '1', devTpr: '1.000 (0.757–1.000)',
    devTnr: '0.917 (0.646–0.985)', disagreements: '1 (1 to triage)', flips: '1', inputChanged: '0', frozen: 'No', test: '—' });
  assert.equal(j.iterations[0].test, '0.947 (0.827–0.985) / 0.833 (0.552–0.953)');
});

test('a preview that was cut, or that gave no verdict, says so; a code check names its check', () => {
  jfresh({ tlJudgeId: 'j-2', tlJudge: Object.assign({}, DETAIL, { judge: Object.assign({}, DRAFT, { kind: 'code', check_key: 'no_answer', check_version: 1, prompt_parts: null }) }),
    tlJudgeLoadedAt: 1, tlJudgesChecks: CHECKS, tlJudgePreview: { 'td-1': { verdict: 'Fail', critique: 'Empty.', usd: 0.002, truncated: true }, 'td-2': { status: 'undecidable' } } });
  const j = vals().tljJudge;
  assert.equal(j.preview[0].result, 'Fail — Empty. ($0.0020) · judged on cut evidence');
  assert.equal(j.preview[1].result, 'No verdict');
  assert.deepEqual(j.check, { label: 'Gives no answer', version: 'v1' });
  assert.deepEqual(j.parts, []);
});

test('the held-out test button is disabled after one run; the decision and the threshold appear when they apply', () => {
  jfresh({ tlJudgeId: 'j-2', tlJudge: DETAIL_TESTED, tlJudgeLoadedAt: 1 });
  const j = vals().tljJudge;
  assert.deepEqual([j.test.label, j.test.enabled], ['Held-out test already run', false]);
  assert.equal(j.test.note, 'The held-out test runs once per version. Write a new version to test again.');
  assert.deepEqual([j.dev.label, j.dev.enabled], ['Run on dev', false]);
  assert.equal(j.freeze, null);
  assert.ok(j.frozenText.startsWith('Frozen') && j.frozenText.endsWith('— Dev agreement is high; no third revision.'), j.frozenText);
  assert.deepEqual(j.decision.options.map((o) => [o.key, o.label, o.on]), [['use', 'Use for monitoring', false], ['do_not_use', 'Do not use', false]]);
  assert.equal(j.decision.canSave, false);
  assert.equal(j.threshold, null);
  const inUse = Object.assign({}, DETAIL_TESTED, { judge: Object.assign({}, DETAIL_TESTED.judge, { decision: 'use', in_use: true, monitor_threshold: 0.15 }) });
  jfresh({ tlJudgeId: 'j-2', tlJudge: inUse, tlJudgeLoadedAt: 1, tlJudgeThresholdDraft: '2' });
  const t = vals().tljJudge;
  assert.equal(t.decision.current, 'Use for monitoring');
  assert.equal(t.threshold.current, '0.15');
  assert.equal(t.threshold.invalid, 'The threshold is a share between 0 and 1, for example 0.15.');
  assert.equal(t.threshold.canSave, false);
  jfresh({ tlJudgeId: 'j-2', tlJudge: inUse, tlJudgeLoadedAt: 1, tlJudgeThresholdDraft: '0.2' });
  assert.deepEqual([vals().tljJudge.threshold.invalid, vals().tljJudge.threshold.canSave], ['', true]);
  jfresh({ tlJudgeId: 'j-2', tlJudge: Object.assign({}, inUse, { judge: Object.assign({}, inUse.judge, { monitor_threshold: null }) }), tlJudgeLoadedAt: 1 });
  assert.equal(vals().tljJudge.threshold.current, 'not set');
});

test('the freeze dialog confirms only with a stop reason; Freeze is offered once every disagreement is triaged', () => {
  jfresh({ tlJudgeId: 'j-2', tlJudge: DETAIL_TRIAGED, tlJudgeLoadedAt: 1 });
  const ready = vals().tljJudge;
  assert.deepEqual([ready.freeze.can, ready.freeze.note, ready.freezeDialog], [true, '', null]);
  const open = (reason, writes) => {
    jfresh({ tlJudgeId: 'j-2', tlJudge: DETAIL_TRIAGED, tlJudgeLoadedAt: 1, tlJudgeFreeze: { reason }, tlJudgeWrites: writes || {} });
    return vals().tljJudge.freezeDialog;
  };
  assert.deepEqual([open('').canConfirm, open('').hint], [false, 'Say why you stopped revising (at least 10 characters).']);
  assert.equal(open('too short').canConfirm, false);
  assert.deepEqual([open('Stable on dev twice.').canConfirm, open('Stable on dev twice.').hint], [true, '1,980 characters left']);
  assert.equal(open('Stable on dev twice.', { 'judges:freeze:j-2': { busy: true, error: '', conflict: false, canRetry: false } }).canConfirm, false);
  const stale = open('Stable on dev twice.', { 'judges:freeze:j-2': { busy: false, error: 'Changed in another tab — reload to see the latest.', conflict: true, canRetry: false } });
  assert.equal(stale.error, 'Changed in another tab — reload to see the latest.');
  assert.equal(typeof stale.reload, 'function', 'a stale base offers a reload');
  assert.equal(open('Stable on dev twice.').reload, null);
});

test('each dev disagreement offers the three triage choices; a held-out run offers none', () => {
  jfresh({ tlJudgeId: 'j-2', tlJudge: DETAIL, tlJudgeLoadedAt: 1, tlJudgeRunId: 'r-dev', tlJudgeRun: RUN_DEV, tlJudgeRunLoadedAt: 1 });
  const dev = vals().tljRun;
  assert.equal(dev.untriaged, 1);
  assert.equal(dev.disagreements[0].kind, 'Missed failure: people said Fail, the judge said Pass');
  assert.equal(dev.disagreements[0].triageLabel, 'Not triaged');
  assert.deepEqual(dev.disagreements[0].choices.map((x) => [x.key, x.label, x.on]),
    [['judge_wrong', 'Judge wrong', false], ['label_wrong', 'Label wrong', false], ['definition_unclear', 'Definition unclear', false]]);
  assert.deepEqual([dev.tpr, dev.tnr, dev.agreement, dev.n, dev.counts],
    ['1.000 (0.757–1.000)', '0.917 (0.646–0.985)', '0.958', '24', 'TP 12 · FN 0 · TN 11 · FP 1']);
  assert.equal(dev.truncated, '1 answer was judged on cut evidence.');
  assert.equal(dev.finalNote, '', 'a dev run\'s numbers are not final: only the held-out test is');
  jfresh({ tlJudgeId: 'j-2', tlJudge: DETAIL_TESTED, tlJudgeLoadedAt: 1, tlJudgeRunId: 'r-test', tlJudgeRun: RUN_TEST, tlJudgeRunLoadedAt: 1 });
  const held = vals().tljRun;
  assert.deepEqual([held.disagreements[0].choices, held.untriaged, held.disagreements[0].triageLabel], [[], 0, '']);
  assert.equal(held.finalNote, 'Held-out numbers are final for this version.');
  jfresh({ tlJudgeId: 'j-2', tlJudge: DETAIL, tlJudgeRunId: 'r-dev', tlJudgeRunLoadedAt: 1,
    tlJudgeRun: Object.assign({}, RUN_DEV, { status: 'running', disagreements: [], job: { status: 'running', progress: { done: 12, total: 24, errors: 0, usd: 0.15 } } }) });
  const live = vals().tljRun;
  assert.deepEqual([live.status, live.progress, live.live, live.emptyText], ['Running', '12 of 24 judged · $0.150 so far', true, '']);
});

test('a triaged disagreement shows who chose what and the note; a run that agreed everywhere says so; errors and cuts are counted', () => {
  const triaged = Object.assign({}, RUN_DEV.disagreements[0], { triage: 'label_wrong', note: 'The label missed it.', by_email: 'team@plenum.example' });
  jfresh({ tlJudgeId: 'j-2', tlJudge: DETAIL, tlJudgeRunId: 'r-dev', tlJudgeRunLoadedAt: 1, tlJudgeTriageNotes: { 'turn-9': 'typing…' },
    tlJudgeRun: Object.assign({}, RUN_DEV, { disagreements: [triaged], n_error: 2, n_truncated: 0 }) });
  const run = vals().tljRun;
  assert.deepEqual([run.untriaged, run.disagreements[0].triageLabel, run.disagreements[0].by, run.disagreements[0].note, run.disagreements[0].noteDraft],
    [0, 'Label wrong', 'team@plenum.example', 'The label missed it.', 'typing…']);
  assert.deepEqual(run.disagreements[0].choices.map((c) => c.on), [false, true, false]);
  assert.equal(run.errors, '2 answers could not be judged.');
  assert.equal(run.truncated, '');
  jfresh({ tlJudgeId: 'j-2', tlJudge: DETAIL, tlJudgeRunId: 'r-dev', tlJudgeRunLoadedAt: 1, tlJudgeRun: Object.assign({}, RUN_DEV, { disagreements: [], n_error: 1 }) });
  const agreed = vals().tljRun;
  assert.equal(agreed.emptyText, 'The judge agreed with people on every answer in this run.');
  assert.equal(agreed.errors, '1 answer could not be judged.');
  jfresh({ tlJudgeId: 'j-2', tlJudge: DETAIL, tlJudgeRunId: 'r-dev' });
  const reading = vals().tljRun;
  assert.deepEqual([reading.loading, reading.status, reading.tpr, reading.n, reading.cost], [false, '—', '—', '—', '—']);
});

test('the new-version form registers only when its rules, three different train examples and the change note are written', () => {
  const draft = (patch, ex) => {
    const d = Object.assign({ kind: 'llm', task: '', fail_rule: 'The answer names a code that no step returned.',
      pass_rule: 'Every code in the answer was returned by a step.', edge_cases: '', change_note: 'Third revision.', check_key: '',
      examples: Object.assign({ clear_fail: { turn_id: 'tr-f', critique: 'Names AHU-09, which no step returned.' },
        clear_pass: { turn_id: 'tr-p', critique: 'Every code came from the query.' },
        borderline: { turn_id: 'tr-b', critique: 'The code is only implied.' } }, ex || {}) }, patch || {});
    jfresh({ tlJudgeModeId: 'm-1', tlJudgeReady: READY, tlJudgeReadyLoadedAt: 1, tlJudgeSplit: SPLIT, tlJudgeDraft: d, tlJudgesChecks: CHECKS });
    return vals().tljDraft;
  };
  const ok = draft();
  assert.deepEqual([ok.title, ok.noteNeeded, ok.canSubmit], ['Version 3 · LLM judge', true, true]);
  assert.deepEqual(ok.examples.map((x) => [x.label, x.verdict, x.options.length]), [['Clear Fail', 'Fail', 2], ['Clear Pass', 'Pass', 1], ['Borderline', 'Fail', 3]]);
  assert.deepEqual(ok.examples[1].options, [{ value: 'tr-p', label: 'Pass · Which pumps failed?' }]);
  assert.equal(draft({ change_note: '' }).canSubmit, false);
  assert.equal(draft({ fail_rule: 'Too short' }).canSubmit, false);
  assert.equal(draft({}, { borderline: { turn_id: 'tr-f', critique: 'The code is only implied.' } }).canSubmit, false);
  assert.equal(draft({}, { clear_pass: { turn_id: 'tr-p', critique: 'Short.' } }).canSubmit, false);
  const code = draft({ kind: 'code', check_key: 'no_answer' });
  assert.deepEqual([code.isLlm, code.title, code.canSubmit], [false, 'Version 3 · Code check', true]);
  assert.deepEqual(code.checks, [{ value: 'no_answer', label: 'Gives no answer' }]);
});

test('the first version needs no change note; an example with no train answer picked shows no verdict', () => {
  jfresh({ tlJudgeModeId: 'm-1', tlJudgeReady: READY_THIN, tlJudgeReadyLoadedAt: 1, tlJudgeSplit: SPLIT, tlJudgesChecks: CHECKS,
    tlJudgeDraft: { kind: 'code', task: '', fail_rule: '', pass_rule: '', edge_cases: '', change_note: '', check_key: 'no_answer', examples: {} } });
  const d = vals().tljDraft;
  assert.deepEqual([d.title, d.noteNeeded, d.canSubmit], ['Version 0 · Code check', false, true]);
  jfresh({ tlJudgeModeId: 'm-1', tlJudgeReady: READY, tlJudgeReadyLoadedAt: 1, tlJudgeSplit: SPLIT,
    tlJudgeDraft: { kind: 'llm', task: '', fail_rule: '', pass_rule: '', edge_cases: '', change_note: '',
      check_key: '', examples: { clear_fail: { turn_id: '', critique: '' }, clear_pass: { turn_id: '', critique: '' }, borderline: { turn_id: '', critique: '' } } } });
  const empty = vals().tljDraft;
  assert.deepEqual(empty.examples.map((x) => x.verdict), ['Fail', 'Pass', '—'], 'clear ones come from their role; a borderline one waits for its answer');
  assert.equal(empty.canSubmit, false);
  assert.equal(empty.trainEmpty, false);
});

test('the report: each failure mode with its evaluator, its sample fraction (never prevalence) and its case candidates', () => {
  jfresh({ tlJudgeReport: REPORT, tlJudgeReportLoadedAt: 1 });
  const r = vals().tljReport;
  assert.equal(r.loaded, true);
  assert.ok(r.generated, 'the time it was made is shown');
  assert.equal(r.generated, fmtWhen(REPORT.generated_at, NOW), 'in the page\'s own time format (local time, so not pinned to a clock string)');
  assert.deepEqual(r.entries[0], { name: 'invented_codes', definition: 'Present when the answer names a code no step returned.',
    evaluator: 'LLM judge invented_codes-v1: held-out TPR 0.947 (0.827–0.985), TNR 0.833 (0.552–0.953) · Use for monitoring',
    labelled: '12 Fail · 38 Pass · sample fraction (not prevalence) 0.240',
    prevalence: 'Prevalence not available: Monitoring has not run for this mode yet.', cases: '21, 7' });
  const more = Object.assign({}, REPORT.modes[0], { evaluator: null, evaluator_reason: 'No frozen judge yet.', prevalence: { corrected: 0.2, ci_low: 0.1, ci_high: 0.3 },
    evaluation_case_candidates: [], sample_fraction: null });
  jfresh({ tlJudgeReport: { generated_at: REPORT.generated_at, modes: [more] }, tlJudgeReportLoadedAt: 1 });
  const e = vals().tljReport.entries[0];
  assert.equal(e.evaluator, 'No evaluator yet: No frozen judge yet.');
  assert.equal(e.prevalence, 'Prevalence 0.200 (0.100–0.300)');
  assert.equal(e.cases, '—');
  assert.ok(e.labelled.endsWith('sample fraction (not prevalence) —'));
  jfresh({ tlJudgeReport: { modes: [] }, tlJudgeReportLoadedAt: 1 });
  assert.deepEqual([vals().tljReport.empty, vals().tljReport.entries], [true, []]);
  jfresh();
  assert.deepEqual([vals().tljReport.loaded, vals().tljReport.empty, vals().tljReport.generated], [false, false, '']);
});

test('a write in flight or failed reads on the control that made it: busy, the error, Retry for a network failure', () => {
  const w = (key, state) => { jfresh({ tlJudgeModeId: 'm-1', tlJudgeReady: READY_THIN, tlJudgeReadyLoadedAt: 1, tlJudgeWrites: { [key]: state } }); return vals(); };
  const busy = w('judges:split:m-1', { busy: true, error: '', conflict: false, canRetry: false }).tljReady;
  assert.deepEqual([busy.splitBusy, busy.splitError, busy.splitRetry], [true, '', null]);
  const failed = w('judges:split:m-1', { busy: false, error: 'Could not save — network down', conflict: false, canRetry: true }).tljReady;
  assert.equal(failed.splitError, 'Could not save — network down');
  assert.equal(typeof failed.splitRetry, 'function');
  const { a, calls } = recorder();
  jfresh({ tlJudgeModeId: 'm-1', tlJudgeReady: READY_THIN, tlJudgeReadyLoadedAt: 1, tlJudgeWrites: { 'judges:split:m-1': { busy: false, error: 'x', conflict: false, canRetry: true } } });
  vals(a).tljReady.splitRetry();
  assert.deepEqual(calls, [['retry', 'judges:split:m-1']]);
});

test('every button calls the action of that name, with the ids and values it was drawn with', () => {
  const { a, calls } = recorder();
  const last = () => calls.splice(0);
  jfresh({ tlJudgesRows: [SUMMARY_FULL], tlJudgesModes: [{ id: 'm-1', name: 'invented_codes', status: 'confirmed' }], tlJudgesChecks: CHECKS, tlJudgesLoadedAt: 1,
    tlJudgeModeId: 'm-1', tlJudgeReady: READY, tlJudgeReadyLoadedAt: 1, tlJudgeId: 'j-2', tlJudge: DETAIL_TRIAGED, tlJudgeLoadedAt: 1,
    tlJudgeRunId: 'r-dev', tlJudgeRun: RUN_DEV, tlJudgeRunLoadedAt: 1, tlJudgeFreeze: { reason: 'Stable on dev twice.' },
    tlJudgeEstimate: { split: 'dev', loading: false, error: '', cap: '', data: { items: 24, cached: 0, to_call: 24, usd_estimate: 0.29 } },
    tlJudgeReport: REPORT, tlJudgeReportLoadedAt: 1 });
  const v = vals(a);
  v.tljReload();
  v.tljRows[0].open();
  v.tljModes[0].open();
  assert.deepEqual(last(), [['load'], ['openJudge', 'j-1'], ['openMode', 'm-1']]);
  const r = v.tljReady;
  r.reload(); r.close(); r.createSplit(); r.startLlm(); r.startCode(); r.versions[0].open();
  r.setSplitOverride({ target: { checked: true } }); r.setSplitOverride(false); r.setSplitReason({ target: { value: 'why' } });
  assert.deepEqual(last(), [['openMode', 'm-1'], ['closeMode'], ['createSplit'], ['startDraft', 'llm'], ['startDraft', 'code'], ['openJudge', 'j-1'],
    ['setSplitForm', { override: true }], ['setSplitForm', { override: false }], ['setSplitForm', { reason: 'why' }]]);
  const j = v.tljJudge;
  j.preview[0].run(); j.dev.start(); j.test.start(); j.freeze.open(); j.revise(); j.reload(); j.close(); j.runs[0].open();
  assert.deepEqual(last(), [['preview', 'td-1'], ['estimate', 'dev'], ['estimate', 'test'], ['freezeOpen'], ['revise'], ['openJudge', 'j-2'], ['closeJudge'], ['openRun', 'r-dev']]);
  const f = j.freezeDialog;
  f.setReason({ target: { value: 'because' } }); f.confirm(); f.cancel();
  const e = j.estimate;
  e.setCap('2'); e.start(); e.close();
  assert.deepEqual(last(), [['setFreezeReason', 'because'], ['freeze'], ['freezeClose'], ['setCap', '2'], ['startRun'], ['closeEstimate']]);
  const d = v.tljRun.disagreements[0];
  d.choices[1].pick(); d.setNote({ target: { value: 'a note' } });
  assert.deepEqual(last(), [['triage', 'turn-9', 'label_wrong'], ['setTriageNote', 'turn-9', 'a note']]);
  v.tljReport.load(); v.tljReport.download();
  assert.deepEqual(last(), [['loadReport'], ['downloadReport']]);
  const dr = { kind: 'llm', task: '', fail_rule: '', pass_rule: '', edge_cases: '', change_note: '', check_key: '',
    examples: { clear_fail: { turn_id: '', critique: '' }, clear_pass: { turn_id: '', critique: '' }, borderline: { turn_id: '', critique: '' } } };
  jfresh({ tlJudgeModeId: 'm-1', tlJudgeReady: READY, tlJudgeReadyLoadedAt: 1, tlJudgeSplit: SPLIT, tlJudgeDraft: dr });
  const df = vals(a).tljDraft;
  df.set('fail_rule')({ target: { value: 'rule' } });
  df.examples[0].setTurn({ target: { value: 'tr-f' } });
  df.examples[0].setCritique('crit');
  df.submit(); df.cancel();
  assert.deepEqual(last(), [['setDraft', { fail_rule: 'rule' }], ['setExample', 'clear_fail', { turn_id: 'tr-f' }], ['setExample', 'clear_fail', { critique: 'crit' }], ['register'], ['cancelDraft']]);
  const tested = Object.assign({}, DETAIL_TESTED, { judge: Object.assign({}, DETAIL_TESTED.judge, { in_use: true, decision: 'use', monitor_threshold: 0.2 }) });
  jfresh({ tlJudgeId: 'j-2', tlJudge: tested, tlJudgeLoadedAt: 1 });
  const k = vals(a).tljJudge;
  k.decision.options[1].pick(); k.decision.setNote({ target: { value: 'n' } }); k.decision.save();
  k.threshold.setDraft('0.3'); k.threshold.save();
  assert.deepEqual(last(), [['setDecisionDraft', { decision: 'do_not_use' }], ['setDecisionDraft', { note: 'n' }], ['decide'], ['setThresholdDraft', '0.3'], ['saveThreshold']]);
});
