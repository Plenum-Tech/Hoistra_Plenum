// The Golden set tab rendered to markup (esbuild + react-dom/server): the tab in the tab bar, no filter bar on it,
// the upload panel (check first, save after), both template downloads, versions with Make active / Retire, the
// cases with every expected answer hidden until its own row is opened, a refusal as a notice, and empty and
// failure states in words.
import { test, after } from 'node:test';
import assert from 'node:assert/strict';
import { bundle, cleanBundles } from './esbuild-helper.mjs';

after(cleanBundles);
const React = (await import('react')).default;
const { renderToStaticMarkup } = await import('react-dom/server');
const { initialState, goldenInitial } = await import('../store.js');
const { Shell } = await bundle('TraceLabApp.jsx');
const { default: GoldenSet } = await bundle('views/GoldenSet.jsx');

const NOW = Date.parse('2026-10-15T12:00:00Z');
const SETS = { active: { id: 's2', version: 2, status: 'active', name: 'Tower', case_count: 29, stale_count: 3, unverified_count: 1,
  created_by_email: 'admin@example.com', created_at: '2026-10-15T09:00:00Z', source_filename: 'tower.xlsx' },
  versions: [{ id: 's2', version: 2, status: 'active', case_count: 29, created_at: '2026-10-15T09:00:00Z', source_filename: 'tower.xlsx' },
    { id: 's1', version: 1, status: 'retired', case_count: 28, created_at: '2026-10-01T09:00:00Z', source_filename: 'tower.xlsx' }] };
const CASES = { total: 2, cases: [
  { id: 'c1', case_key: '21', question: 'How many work orders were raised in August 2026?', followups: ['And reactive?'], evaluation: 'objective',
    verified_on: '2026-10-13', stale: false, requirement_ids: ['REQ-7'], flags: [], expected_answer: 'Seven hundred and fifty-nine.', expected_values: [{ raw: '759' }] },
  { id: 'c2', case_key: '22', question: 'Which vendor holds the lift contract?', followups: [], evaluation: 'judgment', verified_on: null, stale: true,
    requirement_ids: [], flags: ['write_intent'], expected_answer: 'Apex Lifts Ltd.', expected_values: [] }] };
const golden = (over) => Object.assign(goldenInitial(), { sets: { data: SETS, loading: false, error: '', reason: '', loadedAt: 1 }, setId: 's2',
  cases: { data: CASES, loading: false, error: '', q: '', flag: '', page: 0 } }, over || {});
const state = (g, over) => Object.assign(initialState(), { tab: 'golden' }, over || {}, { golden: g });
const tab = (g) => renderToStaticMarkup(React.createElement(GoldenSet, { s: state(g), now: NOW }));

test('the Golden set tab sits in the tab bar and has no conversation filters', () => {
  const html = renderToStaticMarkup(React.createElement(Shell, { s: state(golden()), now: NOW }));
  assert.match(html, /role="tab" aria-selected="true"[^>]*>Golden set</);
  assert.equal(html.includes('>7 days<'), false, 'no range chips on this tab');
  assert.equal(html.includes('Every source'), false);
  assert.equal(html.includes('Search questions and answers'), false);
  assert.ok(html.includes('Version 2 is active · Tower'));
});

test('the active version, the versions table and its actions', () => {
  const html = tab(golden());
  assert.ok(html.includes('29 cases · 3 stale · 1 not verified'));
  assert.ok(html.includes('from tower.xlsx'));
  assert.match(html, /aria-pressed="true"[^>]*>Version 2</);
  assert.equal((html.match(/>Make active</g) || []).length, 1, 'only the retired version can be made active');
  assert.equal((html.match(/>Retire</g) || []).length, 1, 'only the active version can be retired');
});

test('every expected answer is hidden until its own row is opened', () => {
  let html = tab(golden());
  assert.equal(html.includes('Seven hundred'), false);
  assert.equal(html.includes('Apex Lifts'), false);
  assert.equal((html.match(/>Show expected answer</g) || []).length, 2);
  html = tab(golden({ shown: { c1: true } }));
  assert.ok(html.includes('Seven hundred and fifty-nine.'));
  assert.ok(html.includes('Values: 759'));
  assert.equal(html.includes('Apex Lifts'), false, 'the other row stays hidden');
  assert.match(html, /aria-expanded="true"[^>]*>Hide</);
  assert.ok(html.includes('Asks for an action'));
  assert.ok(html.includes('Not verified'));
  assert.ok(html.includes('2 cases · Page 1 of 1'));
});

test('the upload panel checks before it saves, and both templates can be downloaded', () => {
  const html = tab(golden());
  assert.match(html, /<input type="file" accept=".xlsx,.csv"/);
  assert.match(html, /<button type="button" class="tl-btn" disabled="">Check the file<\/button>/);
  assert.match(html, /<button type="button" class="tl-btn tl-btn-primary" disabled="">Save as a new version<\/button>/);
  assert.ok(html.includes('Download the blank template'));
  assert.ok(html.includes('Download the compliance question templates (161 questions)'));
  assert.ok(html.includes('Replace every {placeholder}'));
});

test('a check\'s report and an upload\'s row errors are shown in words', () => {
  const report = tab(golden({ upload: { busy: false, dryRun: true, fileName: 'tower.xlsx', error: '', errors: [], warnings: [],
    report: { saved: false, cases: 29, sheet_name: 'Golden set', header_row: 1, flags: { unverified: 1 }, preview: [{ case_key: '21', question: 'How many?', evaluation: 'objective' }] } } }));
  assert.ok(report.includes('29 cases read from the sheet &quot;Golden set&quot;, header on row 1. Nothing is saved yet.'));
  assert.ok(report.includes('1 case has no Verified on date.'));
  const bad = tab(golden({ upload: { busy: false, dryRun: true, fileName: 'x.csv', error: 'Some rows need fixing before this file can be saved.',
    errors: [{ row: 4, column: 'Question', message: 'The question is empty.' }], warnings: [], report: null } }));
  assert.match(bad, /role="alert">Some rows need fixing/);
  assert.ok(bad.includes('<li>Row 4, Question: The question is empty.</li>'));
});

test('a refusal is a notice, a company with no set is told how to start, and failures say so', () => {
  const refused = tab(Object.assign(goldenInitial(), { sets: { data: null, loading: false, error: 'x', reason: 'reviewers_only', loadedAt: null } }));
  assert.equal(refused, '<div class="tl-notice" role="status">Trace Lab is for company admins.</div>');
  const empty = tab(Object.assign(goldenInitial(), { sets: { data: { active: null, versions: [] }, loading: false, error: '', reason: '', loadedAt: 1 } }));
  assert.ok(empty.includes('No golden set yet.'));
  assert.equal(empty.includes('aria-label="Cases"'), false);
  assert.ok(tab(Object.assign(goldenInitial(), { sets: { data: null, loading: true, error: '', reason: '', loadedAt: null } })).includes('Reading the golden set…'));
  assert.ok(tab(Object.assign(goldenInitial(), { sets: { data: null, loading: false, error: 'timed out after 15s', reason: '', loadedAt: null } }))
    .includes('Couldn&#x27;t read the golden set: timed out after 15s'));
  assert.ok(tab(golden({ cases: { data: null, loading: false, error: 'gone', q: '', flag: '', page: 0 } })).includes('Couldn&#x27;t read the cases: gone'));
});

test('a failed action (a template download) is shown even when no version exists yet', () => {
  const emptySets = { data: { active: null, versions: [] }, loading: false, error: '', reason: '', loadedAt: 1 };
  const empty = tab(Object.assign(goldenInitial(), { sets: emptySets, setError: 'download timed out' }));
  assert.match(empty, /role="alert">download timed out</);
  assert.equal(empty.includes('aria-label="Versions"'), false);
  const withVersions = tab(golden({ setError: 'could not retire' }));
  assert.equal((withVersions.match(/could not retire/g) || []).length, 1, 'drawn once, not per section');
  assert.equal(tab(golden()).includes('role="alert"'), false, 'nothing when no action failed');
});
