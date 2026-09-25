// reports — a custom report card is a pinned prompt, re-run on a cadence by the server
// (svc-operations-intelligence's /api/reports). The pure core here: the fallback preset
// list, the day labels, the exported markdown, the status badge, and flattening reports
// into cards.
import { test } from 'node:test';
import assert from 'node:assert/strict';

globalThis.window = globalThis.window || { location: { origin: 'http://test.local' } };

const { FALLBACK_PRESETS, DAYS, cardStatusBadge, flattenCards, cardMarkdown } = await import('../src/logic/reports.js');

test('the fallback preset list is what the navigator menu offers before the backend answers', () => {
  assert.deepEqual(FALLBACK_PRESETS.map((c) => c.badge), ['30 min', '1 hr', '6 hr', '12 hr', '24 hr', 'Daily', 'Days']);
  assert.deepEqual(DAYS, ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat']);
  assert.equal(FALLBACK_PRESETS.find((c) => c.key === 'days').pick_days, true);
});

test('flattenCards pulls every card out of every report, keeping the parent report name', () => {
  const reports = [
    { id: 'r1', name: 'My report', cards: [{ id: 'c1', name: 'A' }, { id: 'c2', name: 'B' }] },
    { id: 'r2', name: 'Other', cards: [{ id: 'c3', name: 'C' }] }
  ];
  const cards = flattenCards(reports);
  assert.equal(cards.length, 3);
  assert.deepEqual(cards.map((c) => c.id), ['c1', 'c2', 'c3']);
  assert.equal(cards[0].report_name, 'My report');
  assert.equal(cards[2].report_name, 'Other');
  assert.deepEqual(flattenCards(null), []);
  assert.deepEqual(flattenCards([{ id: 'r1', name: 'n' }]), [], 'a report with no cards field contributes nothing');
});

test('cardStatusBadge reads the in-flight states, and falls back to the refresh label once ready', () => {
  assert.equal(cardStatusBadge({ status: 'pending' }), 'Pending');
  assert.equal(cardStatusBadge({ status: 'running' }), 'Running');
  assert.equal(cardStatusBadge({ status: 'error' }), 'Failed');
  assert.equal(cardStatusBadge({ status: 'paused' }), 'Paused');
  assert.equal(cardStatusBadge({ status: 'ready', refresh_label: 'Refresh every 1 hour' }), 'Refresh every 1 hour');
  assert.equal(cardStatusBadge(null), '');
});

test('the exported markdown carries the title, the pinned question, the refresh and the answer', () => {
  const card = { name: 'Risky buildings', prompt: 'Which buildings put me at risk?', refresh_label: 'Refresh every 30 minutes' };
  const run = { ran_at: '2026-09-08T14:00:00Z', duration_ms: 42000, ok: true, answer: '## Findings\n\nTwo buildings.', tool_calls: [{ tool: 'compliance_scan' }, { tool: 'compliance_scan' }, { tool: 'get_sites' }] };
  const md = cardMarkdown(card, run);
  assert.match(md, /^# Risky buildings\n/);
  assert.match(md, /Which buildings put me at risk\?/);
  assert.match(md, /Refresh every 30 minutes/);
  assert.match(md, /## Findings\n\nTwo buildings\./);
  assert.match(md, /compliance_scan, get_sites/);
  assert.doesNotMatch(md, /compliance_scan, compliance_scan/);
});

test('a structured compliance answer exports its narrative, figures and certificates', () => {
  const card = { name: 'Lapses', prompt: 'p', refresh_label: 'Refresh daily at 02:00' };
  const rich = {
    narrative: 'Six building certificates have lapsed.',
    kpis: [{ label: 'Lapsed', count: 6, unit: 'certificates' }, { label: 'Drafts', count: 5 }],
    insights: [{ text: 'The FRA at Building 5 expired in 2006.' }],
    actions: [{ title: 'Book the FRA renewal', severity: 'critical', scope: 'building' }],
    groups: [{ owner: 'Building 5', scope: 'building', headline: 'One lapsed statutory certificate', points: ['FRA lapsed 2006-10-01'] }],
    certificates: [{ name: 'Fire Risk Assessment', company: 'Building 5', scope: 'building', status: 'Lapsed', reason: 'expired 2006-10-01' }]
  };
  const md = cardMarkdown(card, { ran_at: '2026-09-08T14:00:00Z', duration_ms: 1000, ok: true, answer: '', tool_calls: [], rich });
  assert.match(md, /Six building certificates have lapsed\./);
  assert.match(md, /- \*\*Lapsed\*\* 6 certificates/);
  assert.match(md, /The FRA at Building 5/);
  assert.match(md, /Book the FRA renewal/);
  assert.match(md, /### Building 5/);
  assert.match(md, /- FRA lapsed 2006-10-01/);
  assert.match(md, /\| Fire Risk Assessment \(expired 2006-10-01\) \| Building 5 \| building \| Lapsed \|/);
});

test('a failed run exports as a failure, not as an empty report', () => {
  const card = { name: 'Lapses', prompt: 'p', refresh_label: 'Refresh every 30 minutes' };
  const md = cardMarkdown(card, { ran_at: '2026-09-08T14:00:00Z', ok: false, error: 'svc-deepagents is not reachable' });
  assert.match(md, /did not complete/i);
  assert.match(md, /svc-deepagents is not reachable/);
  assert.match(cardMarkdown(card, null), /has not run yet/i);
});
