// cronsPage — Administration › Hoist Crons: the next-runs preview, Edit schedule's form,
// and what the page shows from the service's answers.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { nextRuns, formFromJob, fmtWhen, cronsPageVals } from '../src/logic/cronsPage.js';

test('the preview lands on the cadence the service keeps', () => {
  const now = new Date(2026, 9, 1, 10, 40);            // Thu 1 Oct 2026 10:40
  assert.deepEqual(nextRuns({ every_minutes: 60 }, 3, now).map((d) => d.getHours() + ':' + d.getMinutes()), ['11:40', '12:40', '13:40']);
  assert.deepEqual(nextRuns({ daily_at: '09:00' }, 2, now).map((d) => d.getDate()), [2, 3], 'today 09:00 has passed');
  assert.deepEqual(nextRuns({ days: [1], time: '09:00' }, 2, now).map((d) => d.getDate() + '/' + (d.getMonth() + 1)), ['5/10', '12/10']);
  assert.deepEqual(nextRuns({ days: [], time: '09:00' }, 2, now), []);
  assert.equal(fmtWhen(new Date(2026, 9, 1, 11, 40), now), 'today 11:40');
  assert.equal(fmtWhen(new Date(2026, 9, 2, 9, 0), now), 'tomorrow 09:00');
});

test('Edit schedule opens on the job as it is', () => {
  assert.deepEqual(formFromJob({ job_key: 'question', refresh: { days: [1, 4], time: '09:00' }, params: { prompt: 'Give me a summary.', email: true } }),
    { jobs: ['question'], freq: 'days', time: '09:00', days: [1, 4], prompt: 'Give me a summary.', email: true, recipients: [], recipientDraft: '', runNow: false });
  assert.equal(formFromJob({ job_key: 'energy_anomaly_scan', refresh: { every_minutes: 360 } }).freq, '6h');
  assert.equal(formFromJob({ job_key: 'energy_benchmarks', refresh: { daily_at: '02:00' } }).time, '02:00');
});

function ctl(state) {
  const calls = [];
  const c = { state: Object.assign({ signedIn: true, view: 'crons' }, state) };
  ['cpOpenJob', 'cronRun', 'cronPause', 'cpEdit', 'cronRemove', 'cpBack', 'cpNew', 'cpSetFilter', 'cpSetSearch', 'cpSaveEdit', 'cronCreate', 'cpModalClose']
    .forEach((m) => { c[m] = (...a) => calls.push([m, ...a]); });
  return { c, calls };
}

const JOBS = [
  { id: 'j1', name: 'Energy anomaly scan', label: 'Energy anomaly scan', module: 'Energy', enabled: true, status: 'ready', refresh_label: 'every 1 hour',
    next_run_at: new Date(Date.now() + 3600e3).toISOString(), created_by: { email: 'admin@example.com' },
    runs: [{ ok: true, finished_at: new Date().toISOString(), summary: { meters_scanned: 58 } }] },
  { id: 'j2', name: 'Meter gap check', label: 'Meter gap check', module: 'Energy', enabled: false, status: 'paused', refresh_label: 'every 6 hours',
    created_by: { email: 'other@example.com' }, runs: [] }
];

test('the list, its filters and search, and the tiles', () => {
  const { c, calls } = ctl({ cronJobs: JOBS, cronLoadedAt: 1, cronCanManage: true, cronStats: { runs_today: 3, failed_today: 1, emails_week: 2 } });
  let v = cronsPageVals(c);
  assert.equal(v.isCrons, true);
  assert.equal(v.cpRows.length, 2);
  assert.match(v.cpRows[0].last, /58 meters scanned/);
  assert.equal(v.cpRows[1].status, 'Paused');
  assert.equal(v.cpTiles[1].hint, '2 ok · 1 failed');
  assert.equal(v.cpTiles[3].value, '2');
  v.cpRows[0].run(); v.cpRows[1].pause();
  assert.deepEqual(calls.slice(0, 2), [['cronRun', 'j1'], ['cronPause', 'j2', true]]);
  c.state.cpFilter = 'Paused';
  assert.deepEqual(cronsPageVals(c).cpRows.map((r) => r.id), ['j2']);
  c.state.cpFilter = 'All'; c.state.cpSearch = 'admin@';
  assert.deepEqual(cronsPageVals(c).cpRows.map((r) => r.id), ['j1'], 'search covers who scheduled it');
});

test('a plain user sees the jobs but no controls', () => {
  const { c } = ctl({ cronJobs: JOBS, cronLoadedAt: 1, cronCanManage: false });
  const v = cronsPageVals(c);
  assert.equal(v.cpManage, false);
  assert.equal(v.cpRows[0].manage, false);
});

test('the open job: history, who did what, the 30 days', () => {
  const { c } = ctl({ cronJobs: JOBS, cronLoadedAt: 1, cronCanManage: true, cpJobId: 'j1', cpDetail: {
    job: JOBS[0],
    runs: [{ ok: true, trigger: 'schedule', finished_at: new Date().toISOString(), duration_ms: 4200, summary: { meters_scanned: 58 } },
           { ok: false, trigger: 'manual', requested_by: 'admin@example.com', finished_at: new Date().toISOString(), duration_ms: 31000, error: 'register did not answer' }],
    events: [{ action: 'paused', user_email: 'admin@example.com', at: new Date().toISOString(), details: { enabled: false } },
             { action: 'created', user_email: 'admin@example.com', at: new Date().toISOString(), details: { refresh: { every_minutes: 60 }, source_session_id: 's1' } }],
    days: [{ date: '2026-10-01', runs: 2, failed: 1, status: 'partial' }]
  } });
  const d = cronsPageVals(c).cpDetail;
  assert.equal(d.okLine, '1 of 2 ok');
  assert.equal(d.runs[0].by, 'The schedule');
  assert.equal(d.runs[1].by, 'Run now · admin@example.com');
  assert.match(d.runs[1].result, /^Failed: register did not answer/);
  assert.deepEqual(d.events.map((e) => e.action), ['Paused', 'Created']);
  assert.match(d.events[1].detail, /from the chat/);
  assert.equal(d.days[0].label, '1');
});

test('recipients: typed text becomes addresses, a typo is kept for fixing, the draft counts on save', async () => {
  const { parseRecipients, recipientsFrom, recipientsOf, summaryLine, MAX_RECIPIENTS } = await import('../src/logic/crons.js');
  assert.deepEqual(parseRecipients('A@x.com; b@y.org, a@x.com  oops'), { add: ['a@x.com', 'b@y.org'], bad: ['oops'] });
  assert.deepEqual(recipientsFrom('Send me a compliance summary every Monday at 9 to fm@acme.co.uk and Ops@acme.com'),
    ['fm@acme.co.uk', 'ops@acme.com']);
  assert.deepEqual(recipientsOf({ recipients: ['a@x.com'], recipientDraft: 'b@y.org' }), { recipients: ['a@x.com', 'b@y.org'] });
  assert.match(recipientsOf({ recipients: [], recipientDraft: 'nope' }).error, /not an email address/);
  const many = Array.from({ length: MAX_RECIPIENTS }, (_, i) => 'u' + i + '@x.com');
  assert.match(recipientsOf({ recipients: many, recipientDraft: 'one@more.com' }).error, /at most/);
  assert.equal(formFromJob({ job_key: 'compliance_expiry_scan', refresh: { daily_at: '07:00' },
    params: { recipients: ['fm@x.com'] } }).recipients[0], 'fm@x.com');
  assert.match(summaryLine({ alerts_created: 25, emailed_to: 'a@x.com, b@y.com, c@z.com', email_status: 'sent' }), /emailed to 3 people/);
  assert.match(summaryLine({ emailed_to: 'a@x.com, b@y.com', email_status: 'partial', email_failed: 'b@y.com' }), /emailed 1 of 2/);
  assert.match(summaryLine({ emailed_to: 'a@x.com', email_status: 'sent' }), /emailed to a@x.com/);
});
