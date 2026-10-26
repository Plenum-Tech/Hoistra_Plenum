// judgesText — the Judges tab's pure text and rules (logic/judgesText.js): rates with their 95% intervals, label counts
// against the minimum, readiness, next steps, run states, the button rules (the held-out test runs once, dev runs stop
// at freeze, freeze needs a finished dev run with every disagreement triaged and a stop reason), an example's verdict
// and the file save. The rules follow the service's: a verdict matches its label, a test run once and a stopped test
// resuming its own run, a freeze refused until the dev run is done and every disagreement is triaged.
import { test } from 'node:test';
import assert from 'node:assert/strict';

const J = await import('../logic/judgesText.js');

test('rates read with three decimals and their 95% interval; a missing one is a dash', () => {
  assert.equal(J.tljMetric(0.9487, [0.8311, 0.9858]), '0.949 (0.831–0.986)');
  assert.equal(J.tljMetric(0.8333, [null, null]), '0.833');
  assert.equal(J.tljMetric(null, [0.1, 0.2]), '—');
  assert.equal(J.tljMetric(undefined), '—');
  assert.equal(J.tljRate(''), '—');
  assert.equal(J.tljRate(0), '0.000');
  assert.equal(J.tljInterval([0.552, 0.953]), '(0.552–0.953)');
  assert.equal(J.tljInterval(null), '');
  assert.equal(J.tljInterval([0.5, null]), '');
});

test('label counts read against the minimum, and a count below it is flagged', () => {
  assert.equal(J.tljLabelsText({ fail: 30, pass: 30 }, 30), '30 Fail · 30 Pass (minimum 30 each)');
  assert.equal(J.tljLabelsText({ fail: 12, pass: 31 }, 12), '12 Fail · 31 Pass (minimum 12 each)');
  assert.equal(J.tljLabelsText({ fail: 30 }, null), '30 Fail · — Pass (minimum 30 each)');
  assert.equal(J.tljLabelsText(null, 30), '—');
  assert.equal(J.tljBelowMinimum({ fail: 29, pass: 30 }, 30), true);
  assert.equal(J.tljBelowMinimum({ fail: 30, pass: 30 }, 30), false);
  assert.equal(J.tljBelowMinimum({ fail: 30 }, 30), false);
  assert.equal(J.tljBelowMinimum({ fail: 11, pass: 40 }, 12), true, 'the server\'s own minimum wins over 30');
});

test('readiness text, the split line and the next step', () => {
  assert.equal(J.tljReadinessText({ labels: { groups_fail: 31, groups_pass: 40 }, minimum: { per_class: 30 } }),
    'Fail 31 · Pass 40 in separate conversations (minimum 30 each)');
  assert.equal(J.tljReadinessText(null), '—');
  assert.equal(J.tljNextStepText('label_more', 30), 'Label more answers: each class needs 30 answers from separate conversations.');
  assert.equal(J.tljNextStepText('create_split'), 'Enough labels: create the train, dev and test split.');
  assert.equal(J.tljNextStepText('run_test'), 'Run the held-out test (once).');
  assert.equal(J.tljNextStepText('something_new'), '—');
  assert.equal(J.tljSplitText({ version: 2, counts: { train: { fail: 6, pass: 6 }, dev: { fail: 12, pass: 12 }, test: { fail: 12, pass: 12 } } }),
    'Split v2 (Fail / Pass): train 6 / 6 · dev 12 / 12 · test 12 / 12');
  assert.equal(J.tljSplitText(null), 'No split yet');
});

test('the hard floor is said openly: dev and test never go under it, override or not; the server\'s own number wins, else 10', () => {
  const floor = (n) => 'Never fewer than ' + n + ' per class in dev and test, even with an override.';
  assert.equal(J.tljFloorText({ per_class: 30, eval_per_class: 10 }), floor(10));
  assert.equal(J.tljFloorText({ per_class: 30, eval_per_class: 12 }), floor(12), 'the number the service sent');
  assert.equal(J.tljFloorText({ per_class: 30 }), floor(10), 'no number sent: 10');
  for (const none of [null, undefined, {}, 30, { eval_per_class: null }, { eval_per_class: '' }, { eval_per_class: 'x' }, { eval_per_class: 0 }, { eval_per_class: -3 }]) {
    assert.equal(J.tljFloorText(none), floor(10), JSON.stringify(none) + ' falls back to 10');
  }
  assert.equal(J.TLJ_EVAL_FLOOR, 10);
});

test('the held-out test runs once: freeze first, then once; a stopped test resumes its own run; a finished one is final', () => {
  const draft = { status: 'draft' };
  const frozen = { status: 'frozen' };
  assert.deepEqual(J.tljTestButton(draft, []), { label: 'Run held-out test (once)', enabled: false, note: 'Freeze the judge first.' });
  assert.deepEqual(J.tljTestButton(frozen, [{ split: 'dev', status: 'done' }]), { label: 'Run held-out test (once)', enabled: true, note: '' });
  assert.deepEqual(J.tljTestButton(frozen, [{ split: 'test', status: 'running' }]), { label: 'Held-out test running…', enabled: false, note: '' });
  for (const status of ['interrupted', 'capped', 'incomplete']) {
    const b = J.tljTestButton(frozen, [{ split: 'test', status }]);
    assert.equal(b.label, 'Resume the held-out test', status);
    assert.equal(b.enabled, true, status);
  }
  for (const status of ['done', 'failed']) {
    assert.deepEqual(J.tljTestButton(frozen, [{ split: 'test', status }]), { label: 'Held-out test already run', enabled: false,
      note: 'The held-out test runs once per version. Write a new version to test again.' }, status);
  }
  assert.equal(J.tljTestButton(null, []).enabled, false);
});

test('dev runs stop at freeze; a dev run going blocks another; a stopped one resumes', () => {
  assert.deepEqual(J.tljDevButton({ status: 'frozen' }, []), { label: 'Run on dev', enabled: false, note: 'Frozen: dev runs have stopped.' });
  assert.deepEqual(J.tljDevButton({ status: 'draft' }, []), { label: 'Run on dev', enabled: true, note: '' });
  assert.equal(J.tljDevButton({ status: 'draft' }, [{ split: 'dev', status: 'queued' }]).enabled, false);
  assert.equal(J.tljDevButton({ status: 'draft' }, [{ split: 'dev', status: 'capped' }, { split: 'dev', status: 'done' }]).label, 'Resume the dev run');
  assert.equal(J.tljDevButton(null, []).enabled, false);
});

test('freeze waits for a finished dev run with every disagreement triaged, and wants a stop reason of 10 to 2,000 characters', () => {
  const draft = { status: 'draft' };
  assert.deepEqual(J.tljFreezeState(draft, []), { can: false, note: 'Finish a dev run before freezing.' });
  assert.deepEqual(J.tljFreezeState(draft, [{ split: 'dev', status: 'incomplete' }, { split: 'dev', status: 'done', untriaged: 0 }]),
    { can: false, note: 'Finish a dev run before freezing.' });
  assert.deepEqual(J.tljFreezeState(draft, [{ split: 'dev', status: 'done', untriaged: 2 }]), { can: false, note: 'Triage every disagreement first (2 left).' });
  assert.deepEqual(J.tljFreezeState(draft, [{ split: 'dev', status: 'done', untriaged: 0 }]), { can: true, note: '' });
  assert.deepEqual(J.tljFreezeState({ status: 'frozen' }, [{ split: 'dev', status: 'done', untriaged: 0 }]), { can: false, note: '' });
  assert.equal(J.tljStopReasonCheck('').ok, false);
  assert.deepEqual(J.tljStopReasonCheck('  too short '), { ok: false, text: 'Say why you stopped revising (at least 10 characters).' });
  assert.deepEqual(J.tljStopReasonCheck('Stable on dev twice.'), { ok: true, text: '' });
  assert.deepEqual(J.tljStopReasonCheck('x'.repeat(2001)), { ok: false, text: 'Keep it to 2,000 characters.' });
  assert.deepEqual(J.tljStopReasonCheck('x'.repeat(2000)), { ok: true, text: '' });
});

test('run status, progress and the estimate in plain words', () => {
  assert.equal(J.tljRunStatusText({ status: 'capped' }), 'Stopped at its cost cap — start it again to continue');
  assert.equal(J.tljRunStatusText(null), '—');
  assert.equal(J.tljRunStatusText({ status: 'new_state' }), 'new_state');
  assert.equal(J.tljProgressText({ job: { progress: { done: 12, total: 24, errors: 1, usd: 0.12 } } }), '12 of 24 judged · 1 error · $0.120 so far');
  assert.equal(J.tljProgressText({ job: { progress: { done: 3, total: 24, errors: 2 } } }), '3 of 24 judged · 2 errors');
  assert.equal(J.tljProgressText({ job: { progress: {} } }), '');
  assert.equal(J.tljEstimateText({ items: 24, cached: 0, to_call: 24, usd_estimate: 0.288 }, 'dev'),
    '24 answers: 0 already judged, 24 to judge. Estimated cost $0.288.');
  assert.equal(J.tljEstimateText({ items: 24, cached: 24, to_call: 0, usd_estimate: 0 }, 'test'),
    '24 answers: 24 already judged, 0 to judge. Estimated cost $0. The held-out test runs once for this version; its numbers are final.');
  assert.equal(J.tljEstimateText({ items: 1, cached: 0, to_call: 1, usd_estimate: 0.01 }, 'dev'), '1 answer: 0 already judged, 1 to judge. Estimated cost $0.010.');
  assert.equal(J.tljEstimateText(null, 'dev'), '');
  assert.equal(J.tljDisagreementText({ kind: 'missed_failure' }), 'Missed failure: people said Fail, the judge said Pass');
  assert.equal(J.tljDisagreementText({ kind: 'false_alarm' }), 'False alarm: people said Pass, the judge said Fail');
});

test('an example verdict comes from its role, a borderline one from its label; the question comes out of the evidence', () => {
  assert.equal(J.exampleVerdict('clear_fail', null), 'Fail');
  assert.equal(J.exampleVerdict('clear_pass', { label_present: true }), 'Pass');
  assert.equal(J.exampleVerdict('borderline', { label_present: true }), 'Fail');
  assert.equal(J.exampleVerdict('borderline', { label_present: false }), 'Pass');
  assert.equal(J.exampleVerdict('borderline', null), '');
  assert.equal(J.tljQuestionOf('EARLIER IN THIS CONVERSATION (oldest first)\nQ: hi\nA: hello\n\nQUESTION\nWhich AHUs are overdue?\n\nANSWER THE USER SAW\nAHU-09.'),
    'Which AHUs are overdue?');
  assert.equal(J.tljQuestionOf('QUESTION\n' + 'x'.repeat(120)).length, 90);
  assert.equal(J.tljQuestionOf(''), '—');
});

test('a file the service sent is saved through a link and its object URL is let go; with no page nothing happens', (t) => {
  assert.equal(J.saveFile(null, 'x.md'), false);
  assert.equal(J.saveFile({ blob: new Blob(['a']) }, 'x.md'), false, 'no page to save into under node');
  t.mock.timers.enable({ apis: ['setTimeout'] });
  const realDoc = globalThis.document;
  const realCreate = URL.createObjectURL;
  const realRevoke = URL.revokeObjectURL;
  const made = [];
  const revoked = [];
  const link = { click: () => made.push(['click', link.href, link.download]), remove: () => made.push(['remove']) };
  globalThis.document = { createElement: () => link, body: { appendChild: () => made.push(['append']) } };
  URL.createObjectURL = () => 'blob:judges';
  URL.revokeObjectURL = (u) => revoked.push(u);
  try {
    assert.equal(J.saveFile({ blob: new Blob(['a']), filename: 'failure-report.md' }, 'fallback.md'), true);
    assert.deepEqual(made, [['append'], ['click', 'blob:judges', 'failure-report.md'], ['remove']]);
    assert.deepEqual(revoked, []);
    t.mock.timers.tick(1000);
    assert.deepEqual(revoked, ['blob:judges']);
    made.length = 0;
    J.saveFile({ blob: new Blob(['a']) }, 'fallback.md');
    assert.equal(made[1][2], 'fallback.md', 'the fallback name when the service sent none');
  } finally {
    globalThis.document = realDoc;
    URL.createObjectURL = realCreate;
    URL.revokeObjectURL = realRevoke;
  }
});

test('the constants the tab and the service agree on', () => {
  assert.equal(J.TLJ_POLL_MS, 5000);
  assert.equal(J.TLJ_MIN_LABELS, 30);
  assert.equal(J.TLJ_STOP_REASON_MIN, 10);
  assert.equal(J.TLJ_STOP_REASON_MAX, 2000);
  assert.equal(J.TLJ_TEXT_MIN, 10);
  assert.deepEqual(J.TLJ_TRIAGE.map((x) => x.key), ['judge_wrong', 'label_wrong', 'definition_unclear']);
  assert.deepEqual(J.TLJ_ROLES.map((x) => x.key), ['clear_fail', 'clear_pass', 'borderline']);
  assert.deepEqual(J.TLJ_STATUS_LABELS, { draft: 'Draft', dev: 'On dev', frozen: 'Frozen', tested: 'Tested', retired: 'Retired' });
  assert.equal(Object.isFrozen(J.TLJ_TRIAGE) && Object.isFrozen(J.TLJ_ROLES) && Object.isFrozen(J.TLJ_STATUS_LABELS), true);
});
