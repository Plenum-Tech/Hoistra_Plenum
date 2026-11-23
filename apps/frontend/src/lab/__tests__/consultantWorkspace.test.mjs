// A Hoistra consultant in Trace Lab (23 Nov 2026, Hussain): "i need to see everything of the tracelab here but the
// question should be consultant account specific".
//
// The service says who they are (/lab/me own_only) and narrows every page to the questions they asked; these pin the
// page's half: the consultant gets the workspace every member gets (Runs, Labels, Progress, Loop, Notes, Learning; no
// People, which is the company admin's), the top bar names them a consultant who sees only their questions, no
// separate "My sessions" screen is left, the expected-answer panel is back on the session page, and Hoistra's Users &
// access row says what they get.
import { test, after } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync, existsSync } from 'node:fs';
import { bundle, cleanBundles } from '../../tracelab/__tests__/esbuild-helper.mjs';

globalThis.window = globalThis.window || { location: { origin: 'http://test.local', pathname: '/lab/', search: '' }, history: { replaceState: () => {} } };
after(cleanBundles);
const React = (await import('react')).default;
const { renderToStaticMarkup } = await import('react-dom/server');
const L = await bundle('../lab/LabApp.jsx');
const T = await bundle('../lab/TraceLabAccess.jsx');
const LV = await bundle('../lab/LabelsView.jsx');
const html = (C, p) => renderToStaticMarkup(React.createElement(C, p || {}));
const here = (p) => new URL(p, import.meta.url);

const ME = { user_id: 'u-9', email: 'cara@x.test', organization_id: 'org-1', role: 'user', status: 'active', manages: false,
  all_companies: false, own_only: true };
const USER = { email: 'cara@x.test', organization_name: 'Northbridge' };

test('a consultant has every page a member has, and not People', () => {
  assert.deepEqual(L.navFor(ME).map(([k]) => k), ['runs', 'labels', 'progress', 'loop', 'notes', 'learning']);
});

test('the top bar names them a consultant who sees only their own questions', () => {
  assert.equal(L.whoText(USER, ME), 'cara@x.test · Consultant · your questions only');
  // anyone else reads as before
  assert.equal(L.whoText({ email: 'a@x.test' }, { role: 'user' }), 'a@x.test · User');
  assert.equal(L.whoText({ email: 'a@x.test' }, null), 'a@x.test');
});

test('their workspace is the members\' workspace, tabs and all', () => {
  const page = html(L.Workspace, { user: USER, me: ME, view: 'runs', onView() {}, onSignOut() {} });
  for (const tab of ['>Runs<', '>Labels<', '>Progress<', '>Loop<', '>Notes<', '>Learning<']) assert.ok(page.includes(tab), tab);
  assert.ok(!page.includes('>People<'));
  assert.ok(page.includes('cara@x.test · Consultant · your questions only'));
  assert.ok(!page.includes('My sessions'));
});

test('no separate consultant screen is left, and the session page shows the expected answer to them too', () => {
  assert.ok(!existsSync(here('../MySessions.jsx')));
  const app = readFileSync(here('../LabApp.jsx'), 'utf8');
  assert.ok(!app.includes('MySessions'));
  const session = readFileSync(here('../SessionPage.jsx'), 'utf8');
  assert.ok(session.includes('<TurnExpected turnId={trace.id} />'));
  assert.ok(!session.includes('own_only'));
});

test('a consultant adds their own failure modes on Labels, said to be theirs alone', () => {
  const data = { run: { code: 'R67', title: 'Live questions' }, questions: [{ id: 'q1', code: 'R67-12', question: 'Which buildings put me at risk this month?' }],
    modes: [], cells: {}, me: 'u-9' };
  const page = html(LV.LabelsMatrix, { run: 'R67', data, me: ME, loading: false, error: '', onCell() {}, onAddMode() {} });
  assert.ok(page.includes('Your failure modes are your own: only you see them'));
  assert.ok(page.includes('<form') || page.includes('Add'), 'the add form is offered to them');
  // a plain member still cannot add one
  const plain = html(LV.LabelsMatrix, { run: 'R67', data, me: Object.assign({}, ME, { own_only: false }), loading: false, error: '',
    onCell() {}, onAddMode() {} });
  assert.ok(plain.includes('The platform team or the consultant adds them here'));
});

test('a consultant\'s Users & access row says Trace Lab is theirs, own questions only, with no choice to make', () => {
  const row = html(T.AccessView, { admin: false, consultant: true, inputId: 'x' });
  assert.ok(row.includes('Consultants always have Trace Lab access, to their own questions only'));
  assert.ok(!row.includes('<select'));
  // anyone else still has the choice, and an admin still reads as one
  assert.ok(html(T.AccessView, { admin: false, consultant: false, member: { status: 'none' }, loading: false, saving: false,
    error: '', onChange() {}, inputId: 'x' }).includes('<select'));
  assert.ok(html(T.AccessView, { admin: true, inputId: 'x' }).includes('Company admins always have Trace Lab access'));
});
