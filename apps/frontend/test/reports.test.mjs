// reports — a custom report is a pinned prompt re-run on a cadence. The pure core: when the
// next run falls, how a cadence reads back, the exported markdown, and the stored shape.
import { test } from 'node:test';
import assert from 'node:assert/strict';

globalThis.window = globalThis.window || { location: { origin: 'http://test.local' } };

const {
  CADENCES, DAYS, cadenceLabel, cadenceBadge, nextRunAt, reportMarkdown,
  makeReport, loadReports, saveReports, REPORTS_KEY
} = await import('../src/logic/reports.js');

// Tuesday 08 Sep 2026, 14:00 local.
const TUE_1400 = new Date(2026, 8, 8, 14, 0, 0).getTime();
const at = (y, mo, d, h, mi) => new Date(y, mo, d, h, mi, 0).getTime();

const memStorage = () => {
  const m = {};
  return { getItem: (k) => (k in m ? m[k] : null), setItem: (k, v) => { m[k] = String(v); }, removeItem: (k) => { delete m[k]; }, _m: m };
};

test('the cadence list is the one the navigator menu offers', () => {
  assert.deepEqual(CADENCES.map((c) => c.badge), ['30 min', '1 hr', '6 hr', '12 hr', '24 hr', 'Daily', 'Days']);
  assert.deepEqual(DAYS, ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat']);
});

test('an interval cadence runs again after its interval', () => {
  assert.equal(nextRunAt({ i: 0 }, TUE_1400), TUE_1400 + 30 * 60000);
  assert.equal(nextRunAt({ i: 2 }, TUE_1400), TUE_1400 + 6 * 3600000);
  assert.equal(nextRunAt({ i: 4 }, TUE_1400), TUE_1400 + 24 * 3600000);
});

test('the daily cadence runs at 02:00, today if that is still ahead', () => {
  assert.equal(nextRunAt({ i: 5 }, TUE_1400), at(2026, 8, 9, 2, 0));
  assert.equal(nextRunAt({ i: 5 }, at(2026, 8, 8, 1, 0)), at(2026, 8, 8, 2, 0));
  assert.equal(nextRunAt({ i: 5 }, at(2026, 8, 8, 2, 0)), at(2026, 8, 9, 2, 0), 'exactly on time means the next one');
});

test('chosen days run on the next chosen weekday at the chosen time', () => {
  const cad = { i: 6, days: [1, 4], time: '14:00' };            // Mon, Thu
  assert.equal(nextRunAt(cad, TUE_1400), at(2026, 8, 10, 14, 0));  // Thu
  assert.equal(nextRunAt(cad, at(2026, 8, 10, 15, 0)), at(2026, 8, 14, 14, 0)); // Mon
  assert.equal(nextRunAt({ i: 6, days: [2], time: '09:30' }, TUE_1400), at(2026, 8, 15, 9, 30), 'same weekday, already past → next week');
  assert.equal(nextRunAt({ i: 6, days: [], time: '14:00' }, TUE_1400), null);
  assert.equal(nextRunAt({ i: 6, days: [1], time: 'garbage' }, TUE_1400), at(2026, 8, 14, 14, 0), 'an unreadable time falls back to 14:00');
});

test('a cadence reads back as the user chose it', () => {
  assert.equal(cadenceLabel({ i: 1 }), 'Refresh every 1 hour');
  assert.equal(cadenceBadge({ i: 1 }), '1 hr');
  assert.equal(cadenceLabel({ i: 6, days: [1, 4], time: '14:00' }), 'Refresh Mon, Thu at 14:00');
  assert.equal(cadenceBadge({ i: 6, days: [1, 4], time: '14:00' }), 'Mon · Thu');
  assert.equal(cadenceLabel({ i: 6, days: [0, 1, 2, 3, 4, 5, 6], time: '02:00' }), 'Refresh every day at 02:00');
  assert.equal(cadenceBadge({ i: 6, days: [0, 1, 2, 3, 4, 5, 6], time: '02:00' }), 'Daily');
  assert.equal(cadenceLabel({ i: 6, days: [], time: '14:00' }), 'Refresh on chosen days — pick at least one');
  assert.equal(cadenceLabel({ i: 99 }), CADENCES[1].label, 'an unknown index reads as the default');
});

test('makeReport is pending, due now, with no runs', () => {
  const r = makeReport({ key: 'r1', name: 'Risky buildings', prompt: 'Which buildings put me at risk?', sessionId: 'abc', page: 'Home', cad: { i: 0 }, now: TUE_1400 });
  assert.equal(r.status, 'pending');
  assert.equal(r.nextRunAt, TUE_1400);
  assert.equal(r.lastRunAt, null);
  assert.deepEqual(r.runs, []);
  assert.equal(r.createdAt, TUE_1400);
  assert.equal(r.error, '');
});

test('the exported markdown carries the title, the source question, the refresh and the answer', () => {
  const r = makeReport({ key: 'r1', name: 'Risky buildings', prompt: 'Which buildings put me at risk?', sessionId: 'abc', page: 'Home', cad: { i: 0 }, now: TUE_1400 });
  const md = reportMarkdown(r, { at: TUE_1400, ms: 42000, answer: '## Findings\n\nTwo buildings.', calls: ['compliance_scan', 'compliance_scan', 'get_sites'] });
  assert.match(md, /^# Risky buildings\n/);
  assert.match(md, /Which buildings put me at risk\?/);
  assert.match(md, /Refresh every 30 minutes/);
  assert.match(md, /## Findings\n\nTwo buildings\./);
  assert.match(md, /compliance_scan, get_sites/);
  assert.doesNotMatch(md, /compliance_scan, compliance_scan/);
});

test('a structured compliance answer exports its narrative, figures and certificates', () => {
  const r = makeReport({ key: 'r1', name: 'Lapses', prompt: 'p', sessionId: 'abc', page: 'Compliance', cad: { i: 5 }, now: TUE_1400 });
  const rich = {
    narrative: 'Six building certificates have lapsed.',
    kpis: [{ label: 'Lapsed', count: 6, unit: 'certificates' }, { label: 'Drafts', count: 5 }],
    insights: [{ text: 'The FRA at Building 5 expired in 2006.' }],
    actions: [{ title: 'Book the FRA renewal', severity: 'critical', scope: 'building' }],
    groups: [{ owner: 'Building 5', scope: 'building', headline: 'One lapsed statutory certificate', points: ['FRA lapsed 2006-10-01'] }],
    certificates: [{ name: 'Fire Risk Assessment', company: 'Building 5', scope: 'building', status: 'Lapsed', reason: 'expired 2006-10-01' }]
  };
  const md = reportMarkdown(r, { at: TUE_1400, ms: 1000, answer: '', calls: [], rich });
  assert.match(md, /Six building certificates have lapsed\./);
  assert.match(md, /- \*\*Lapsed\*\* 6 certificates/);
  assert.match(md, /The FRA at Building 5/);
  assert.match(md, /Book the FRA renewal/);
  assert.match(md, /### Building 5/);
  assert.match(md, /- FRA lapsed 2006-10-01/);
  assert.match(md, /\| Fire Risk Assessment \(expired 2006-10-01\) \| Building 5 \| building \| Lapsed \|/);
});

test('a failed run exports as a failure, not as an empty report', () => {
  const r = makeReport({ key: 'r1', name: 'Lapses', prompt: 'p', sessionId: 'abc', page: 'Home', cad: { i: 0 }, now: TUE_1400 });
  const md = reportMarkdown(r, { at: TUE_1400, error: 'svc-deepagents is not reachable' });
  assert.match(md, /did not complete/i);
  assert.match(md, /svc-deepagents is not reachable/);
  assert.match(reportMarkdown(r, null), /has not run yet/i);
});

test('reports round-trip through storage and only the last three runs are kept', () => {
  const st = memStorage();
  const r = makeReport({ key: 'r1', name: 'n', prompt: 'p', sessionId: 'abc', page: 'Home', cad: { i: 0 }, now: TUE_1400 });
  r.runs = [4, 3, 2, 1].map((n) => ({ at: TUE_1400 + n, answer: 'a' + n, calls: [] }));
  assert.equal(saveReports([r], st), true);
  const back = loadReports(st);
  assert.equal(back.length, 1);
  assert.equal(back[0].runs.length, 3);
  assert.equal(back[0].runs[0].answer, 'a4');
  // A report interrupted mid-run comes back as pending so the scheduler picks it up.
  st._m[REPORTS_KEY] = JSON.stringify([Object.assign({}, r, { status: 'running' }), { name: 'no key' }, 'junk']);
  const again = loadReports(st);
  assert.equal(again.length, 1);
  assert.equal(again[0].status, 'pending');
  assert.deepEqual(loadReports({ getItem: () => { throw new Error('blocked'); } }), []);
});
