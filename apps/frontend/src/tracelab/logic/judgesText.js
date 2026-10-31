// judgesText — the Judges tab's pure text and rules: what a reader sees (rates with their 95% intervals, label counts
// against the minimum, readiness, next steps, run states), the button rules (dev runs stop at freeze, the held-out test
// runs once and a stopped test resumes its own run, freeze waits for a finished dev run with every disagreement
// triaged and a stop reason of 10 to 2,000 characters), an example's verdict and the file save. No requests and no
// store access; judgesCommon.js holds the state, judgesActions.js acts, judges.js shapes what is drawn.
//
// The texts follow the service's rules: a verdict matches its label (an example is Fail when its answer was labelled
// Fail), the held-out test runs once per version, a freeze is refused until the latest dev run is done and every
// disagreement is triaged. Every number on screen is the server's; a missing one reads "—", never zero.
import { fmtUsd } from '../../logic/tracesPage.js';

export const TLJ_POLL_MS = 5000;           // a running judge run is read again this often (as a golden run is)
export const TLJ_MIN_LABELS = 30;          // the minimum per class; the server's own number wins when it sends one
export const TLJ_EVAL_FLOOR = 10;          // the hard floor per class in dev and test, override or not; the server's own number wins
export const TLJ_STOP_REASON_MIN = 10;     // a stop reason of 10 to 2,000 characters
export const TLJ_STOP_REASON_MAX = 2000;
export const TLJ_TEXT_MIN = 10;            // the service's minimum for a rule or an example's critique
export const TLJ_STATUS_LABELS = Object.freeze({ draft: 'Draft', dev: 'On dev', frozen: 'Frozen', tested: 'Tested', retired: 'Retired' });
export const TLJ_TRIAGE = Object.freeze([
  { key: 'judge_wrong', label: 'Judge wrong' },
  { key: 'label_wrong', label: 'Label wrong' },
  { key: 'definition_unclear', label: 'Definition unclear' }
]);
export const TLJ_ROLES = Object.freeze([
  { key: 'clear_fail', label: 'Clear Fail' },
  { key: 'clear_pass', label: 'Clear Pass' },
  { key: 'borderline', label: 'Borderline' }
]);
const RUN_STATUS = Object.freeze({
  queued: 'Waiting to start', running: 'Running', done: 'Done',
  incomplete: 'Finished with errors — start it again to retry them',
  capped: 'Stopped at its cost cap — start it again to continue',
  interrupted: 'Interrupted — start it again to continue', failed: 'Failed'
});
const NEXT_STEPS = Object.freeze({
  create_split: 'Enough labels: create the train, dev and test split.',
  register_v0: 'Write version 0 of the judge.',
  run_dev: 'Run the judge on the dev answers.',
  triage: 'Triage the dev disagreements.',
  freeze_or_revise: 'Freeze this version, or write a revision.',
  run_test: 'Run the held-out test (once).',
  decide: 'Record whether to use this judge.',
  in_use: 'In use for monitoring.',
  revise: 'Not used: write a revision.'
});
export const LIVE_RUN = new Set(['queued', 'running']);
export const RESUMABLE_RUN = new Set(['interrupted', 'capped', 'incomplete']);

export const nil = (v) => v === null || v === undefined || v === '';
export const num = (v) => (nil(v) || !isFinite(Number(v)) ? null : Number(v));
export const len = (t) => String(t || '').trim().length;
const minOf = (m) => (num(m) === null ? TLJ_MIN_LABELS : num(m));
export const shown = (v) => (num(v) === null ? '—' : String(num(v)));
// The service lists a judge's runs newest first, so the first of a split is its latest.
export const latest = (runs, split) => (runs || []).find((r) => r && r.split === split) || null;

export function tljRate(v) { const n = num(v); return n === null ? '—' : n.toFixed(3); }

export function tljInterval(iv) {
  const lo = num(iv && iv[0]);
  const hi = num(iv && iv[1]);
  return lo === null || hi === null ? '' : '(' + lo.toFixed(3) + '–' + hi.toFixed(3) + ')';
}

// "0.949 (0.831–0.986)": the server's rate and its Wilson interval; "—" when there is no rate.
export function tljMetric(rate, iv) {
  const r = tljRate(rate);
  if (r === '—') return '—';
  const i = tljInterval(iv);
  return i ? r + ' ' + i : r;
}

export function tljLabelsText(labels, minimum) {
  const f = num(labels && labels.fail);
  const p = num(labels && labels.pass);
  if (f === null && p === null) return '—';
  return shown(f) + ' Fail · ' + shown(p) + ' Pass (minimum ' + minOf(minimum) + ' each)';
}

export function tljBelowMinimum(labels, minimum) {
  const f = num(labels && labels.fail);
  const p = num(labels && labels.pass);
  return f !== null && p !== null && (f < minOf(minimum) || p < minOf(minimum));
}

// The hard floor, said openly beside the label minimum: a judge may be built below the minimum with a reason, but dev and
// test never hold fewer than this per class. `minimum` is the readiness reply's `minimum` ({per_class, eval_per_class});
// no usable number from the service reads as the standing floor of 10.
export function tljFloorText(minimum) {
  const n = num(minimum && minimum.eval_per_class);
  return 'Never fewer than ' + (n !== null && n > 0 ? n : TLJ_EVAL_FLOOR) + ' per class in dev and test, even with an override.';
}

export function tljNextStepText(step, minimum) {
  if (step === 'label_more') return 'Label more answers: each class needs ' + minOf(minimum) + ' answers from separate conversations.';
  return NEXT_STEPS[step] || '—';
}

export function tljReadinessText(ready) {
  const l = ready && ready.labels;
  if (!l) return '—';
  return 'Fail ' + shown(l.groups_fail) + ' · Pass ' + shown(l.groups_pass) + ' in separate conversations (minimum '
    + minOf(ready.minimum && ready.minimum.per_class) + ' each)';
}

export function tljSplitText(split) {
  if (!split || !split.counts) return 'No split yet';
  const part = (k) => { const x = split.counts[k] || {}; return shown(x.fail) + ' / ' + shown(x.pass); };
  return 'Split v' + shown(split.version) + ' (Fail / Pass): train ' + part('train') + ' · dev ' + part('dev') + ' · test ' + part('test');
}

export function tljRunStatusText(run) {
  if (!run || !run.status) return '—';
  return RUN_STATUS[run.status] || run.status;
}

export function tljProgressText(run) {
  const p = (run && run.job && run.job.progress) || {};
  const done = num(p.done);
  const total = num(p.total);
  if (done === null || total === null) return '';
  const errors = num(p.errors) || 0;
  return done + ' of ' + total + ' judged' + (errors ? ' · ' + errors + (errors === 1 ? ' error' : ' errors') : '')
    + (num(p.usd) === null ? '' : ' · ' + fmtUsd(p.usd) + ' so far');
}

export function tljEstimateText(est, split) {
  if (!est || num(est.items) === null) return '';
  const items = num(est.items);
  const text = items + (items === 1 ? ' answer: ' : ' answers: ') + shown(est.cached) + ' already judged, ' + shown(est.to_call)
    + ' to judge. Estimated cost ' + fmtUsd(est.usd_estimate) + '.';
  return split === 'test' ? text + ' The held-out test runs once for this version; its numbers are final.' : text;
}

export function tljDevButton(judge, runs) {
  if (!judge) return { label: 'Run on dev', enabled: false, note: '' };
  if (judge.status === 'frozen') return { label: 'Run on dev', enabled: false, note: 'Frozen: dev runs have stopped.' };
  const dev = latest(runs, 'dev');
  if (dev && LIVE_RUN.has(dev.status)) return { label: 'Dev run going…', enabled: false, note: '' };
  if (dev && RESUMABLE_RUN.has(dev.status)) return { label: 'Resume the dev run', enabled: true, note: '' };
  return { label: 'Run on dev', enabled: true, note: '' };
}

// The held-out test: freeze first, then it runs once. A test run that stopped short (interrupted, capped, incomplete)
// resumes its own row, which is not a second attempt; done or failed is final.
export function tljTestButton(judge, runs) {
  if (!judge) return { label: 'Run held-out test (once)', enabled: false, note: '' };
  if (judge.status !== 'frozen') return { label: 'Run held-out test (once)', enabled: false, note: 'Freeze the judge first.' };
  const test = latest(runs, 'test');
  if (!test) return { label: 'Run held-out test (once)', enabled: true, note: '' };
  if (LIVE_RUN.has(test.status)) return { label: 'Held-out test running…', enabled: false, note: '' };
  if (RESUMABLE_RUN.has(test.status)) {
    return { label: 'Resume the held-out test', enabled: true, note: 'Resuming finishes the same test run; it is not a second attempt.' };
  }
  return { label: 'Held-out test already run', enabled: false, note: 'The held-out test runs once per version. Write a new version to test again.' };
}

export function tljFreezeState(judge, runs) {
  if (!judge || judge.status === 'frozen') return { can: false, note: '' };
  const dev = latest(runs, 'dev');
  if (!dev || dev.status !== 'done') return { can: false, note: 'Finish a dev run before freezing.' };
  const left = num(dev.untriaged) || 0;
  if (left) return { can: false, note: 'Triage every disagreement first (' + left + ' left).' };
  return { can: true, note: '' };
}

export function tljStopReasonCheck(text) {
  const n = len(text);
  if (n < TLJ_STOP_REASON_MIN) return { ok: false, text: 'Say why you stopped revising (at least ' + TLJ_STOP_REASON_MIN + ' characters).' };
  if (n > TLJ_STOP_REASON_MAX) return { ok: false, text: 'Keep it to ' + TLJ_STOP_REASON_MAX.toLocaleString('en-GB') + ' characters.' };
  return { ok: true, text: '' };
}

export function tljDisagreementText(d) {
  return d && d.kind === 'missed_failure' ? 'Missed failure: people said Fail, the judge said Pass'
    : 'False alarm: people said Pass, the judge said Fail';
}

// The question line of a judge's evidence ("QUESTION\n<question>"), for a picker or a heading.
export function tljQuestionOf(text) {
  const lines = String(text || '').split('\n').map((l) => l.trim());
  const at = lines.indexOf('QUESTION');
  const q = (at > -1 ? lines.slice(at + 1) : lines).find((l) => l) || '';
  if (!q) return '—';
  return q.length > 90 ? q.slice(0, 89) + '…' : q;
}

// An example's verdict must match its train answer's label: clear Fail and clear Pass by role, a borderline example by
// the label of the answer picked.
export function exampleVerdict(role, member) {
  if (role === 'clear_fail') return 'Fail';
  if (role === 'clear_pass') return 'Pass';
  if (!member) return '';
  return member.label_present ? 'Fail' : 'Pass';
}

// The browser saves a file the service sent ({blob, filename}); the object URL is let go a moment after the click, as the
// golden set's template download does. Returns whether a link was clicked.
export function saveFile(file, fallback) {
  if (!file || !file.blob || typeof document === 'undefined' || typeof document.createElement !== 'function'
    || typeof URL === 'undefined' || typeof URL.createObjectURL !== 'function') return false;
  const url = URL.createObjectURL(file.blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = file.filename || fallback;
  document.body.appendChild(a);
  a.click();
  a.remove();
  const t = setTimeout(() => URL.revokeObjectURL(url), 1000);
  if (t && t.unref) t.unref();
  return true;
}
