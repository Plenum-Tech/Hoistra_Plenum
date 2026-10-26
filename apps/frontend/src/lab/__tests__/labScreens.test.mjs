// labScreens — the lab's own screens rendered to markup: the sign-in in Hoistra's words with labelled fields, the
// code step, the not-allowed screen, the platform team's picker (Platform view first, the chosen place checked), the
// workspace menu grouped by the loop with Home first, and Home's next step and stage cards.
import { test, after } from 'node:test';
import assert from 'node:assert/strict';
import { bundle, cleanBundles } from '../../tracelab/__tests__/esbuild-helper.mjs';

globalThis.window = globalThis.window || { location: { origin: 'http://test.local', pathname: '/lab/', search: '' }, history: { replaceState: () => {} } };
after(cleanBundles);
const React = (await import('react')).default;
const { renderToStaticMarkup } = await import('react-dom/server');
const L = await bundle('../lab/LabApp.jsx');
const H = await bundle('../lab/Home.jsx');
const { homeView, summarize } = await import('../logic/home.js');
const html = (C, p) => renderToStaticMarkup(React.createElement(C, p || {}));

test('sign in: Hoistra\'s account, labelled fields, a link back to Hoistra, and the error said aloud', () => {
  const out = html(L.SignIn, { onSubmit: () => {}, onCode: () => {}, error: 'That email or password is not right.' });
  assert.ok(out.includes('Sign in to Trace Lab'));
  assert.ok(out.includes('Use your Hoistra email and password. There is no separate Trace Lab account.'));
  assert.match(out, /<label class="lab-field"><span>Email<\/span><input type="email" autoComplete="username"/);
  assert.match(out, /type="password" autoComplete="current-password"/);
  assert.match(out, /role="alert"[^>]*>That email or password is not right\./);
  assert.ok(out.includes('Open Hoistra'));
  const code = html(L.SignIn, { code: true, email: 'a@x.com', onSubmit: () => {}, onCode: () => {} });
  assert.ok(code.includes('We sent a code to a@x.com'));
  assert.match(code, /autoComplete="one-time-code"/);
});

test('not allowed: who signed in, why, and the way out', () => {
  const out = html(L.Denied, { user: { email: 'maria@northbridge.example' }, onSignOut: () => {} });
  assert.ok(out.includes('Trace Lab is for company admins and the platform team'));
  assert.ok(out.includes('maria@northbridge.example'));
  assert.ok(out.includes('Open Hoistra') && out.includes('Sign out'));
});

test('the picker: Platform view first, then each company, the chosen one checked', () => {
  const out = html(L.Picker, { companies: [{ id: 'o1', name: 'Plenum Technologies' }, { id: 'o2', name: 'Northbridge Estates Ltd' }],
    chosen: { id: 'o2', name: 'Northbridge Estates Ltd' }, onChoose: () => {} });
  const names = [...out.matchAll(/<strong>([^<]*)<\/strong>/g)].map((m) => m[1]);
  assert.deepEqual(names, ['Platform view', 'Plenum Technologies', 'Northbridge Estates Ltd']);
  assert.match(out, /role="radiogroup" aria-label="Where to work"/);
  assert.equal((out.match(/aria-checked="true"/g) || []).length, 1);
  assert.match(out, /aria-checked="true"[^>]*><span><strong>Northbridge Estates Ltd/);
  assert.ok(out.includes('Open Northbridge Estates Ltd'));
  assert.ok(html(L.Picker, { companies: null, onChoose: () => {} }).includes('Reading the companies'));
});

test('the menu: Runs, Labels, Progress and Notes, plus People for admins; a question code names its run', () => {
  assert.deepEqual(L.navFor({ manages: false }).map((x) => x[0]), ['runs', 'labels', 'progress', 'notes']);
  assert.deepEqual(L.navFor({ manages: true }).map((x) => x[0]), ['runs', 'labels', 'progress', 'notes', 'people']);
  assert.deepEqual(L.navFor(null).map((x) => x[1]), ['Runs', 'Labels', 'Progress', 'Notes']);
  assert.equal(L.runOf('r3-12'), 'R3');
  assert.equal(L.runOf('R3'), null);
});

test('the top bar: every run the person may see, newest first, and Sync now', () => {
  const out = html(L.RunPicker, { runs: [{ code: 'R4', title: 'ann@a.test · 2026-10-21', n_questions: 3 },
    { code: 'R3', title: 'support batch', n_questions: 250 }], run: 'R3', onRun() {}, onSync() {}, syncing: false, synced: '2 new questions' });
  assert.ok(out.includes('All runs') && out.includes('R4 · ann@a.test · 2026-10-21 · 3 questions') && out.includes('R3 · support batch · 250 questions'));
  assert.match(out, /<option value="R3" selected="">/);
  assert.ok(out.includes('Sync now') && out.includes('2 new questions'));
});

test('no access: who signed in and that their company admin turns Trace Lab on in Hoistra; a turned-off person is told so', () => {
  const out = html(L.Waiting, { me: { email: 'tester@x.test', status: 'none' }, onSignOut: () => {}, onRetry: () => {} });
  assert.ok(out.includes('You do not have access to Trace Lab') && out.includes('tester@x.test') && out.includes('Users &amp; access'));
  assert.ok(out.includes('Check again') && out.includes('Sign out'));
  const gone = html(L.Waiting, { me: { email: 'x@y', status: 'removed' }, onSignOut: () => {}, onRetry: () => {} });
  assert.ok(gone.includes('turned off') && !gone.includes('Check again'));
});

test('the platform team picks a company only (no platform view) when labelling', () => {
  const out = html(L.Picker, { companies: [{ id: 'o1', name: 'Plenum Technologies' }], companiesOnly: true, onChoose: () => {} });
  const names = [...out.matchAll(/<strong>([^<]*)<\/strong>/g)].map((m) => m[1]);
  assert.deepEqual(names, ['Plenum Technologies']);
});

test('Home: the company, the next step as one button, five stage cards', () => {
  const v = homeView(Object.assign(summarize({}), { hasGolden: true, missing: 7, reviewed: 13, wrong: 7 }));
  const out = html(H.HomeView, { company: 'Plenum Technologies', v, onOpen: () => {} });
  assert.ok(out.includes('<h1>Plenum Technologies</h1>'));
  assert.ok(out.includes('7 answers are waiting for your verdict.'));
  assert.ok(out.includes('Start reviewing'));
  assert.equal((out.match(/class="lab-card"/g) || []).length, 5);
  assert.ok(out.includes('13 reviewed'));
});
