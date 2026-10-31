// judgesWiring — the Judges tab wired into Trace Lab's own page: it is the eighth tab, after Labels (in the tab list, the
// labels, the ?tl= link parser, the reopen link and the address bar); the page frame mounts it without the conversation
// filters or the shared refresh line; the page notice that asks a platform superadmin to name a company does not hide
// it (judges are read across companies by the platform team), while every other refusal still stops the page; opening
// it sends nothing from the page's own reads (the tab reads for itself); and the stylesheet has every class it uses.
import { test, beforeEach, afterEach, after } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

import { net, S, reqs, jfresh, installDefaults, tlStore, initialState, SUMMARY_BRIEF, CHECKS, DETAIL, RUN_DEV } from './judges-helper.mjs';
import { judgesScreen, cleanJudgesScreens } from './judges-screen-helper.mjs';

const React = (await import('react')).default;
const { renderToStaticMarkup } = await import('react-dom/server');
const { Shell } = await judgesScreen('TraceLabApp.jsx');
const A = await import('../judgesActions.js');
const act = await import('../actions.js');
const { TABS, linkOpenPatch } = await import('../nav.js');
const { TAB_LABELS, POLLED_TABS, parseLink, tabsView, linkSearch } = await import('../logic/shell.js');
const N = await import('../logic/tabNotice.js');

after(cleanJudgesScreens);
beforeEach(installDefaults);
afterEach(() => A.stopWatch());

const NOW = Date.parse('2026-10-15T12:00:00Z');
const page = () => renderToStaticMarkup(React.createElement(Shell, { s: S(), now: NOW }));
const here = (rel) => readFileSync(new URL('../' + rel, import.meta.url), 'utf8');
const refused = (reason) => ({ convs: Object.assign(initialState().convs, { reason, error: 'refused' }) });

test('Judges is the eighth tab, after Labels: in the list, the labels, the link parser, the reopen link and the address bar', () => {
  assert.deepEqual(TABS.slice(0, 8), ['conversations', 'runs', 'golden', 'models', 'map', 'progress', 'labels', 'judges']);
  assert.equal(TAB_LABELS.judges, 'Judges');
  assert.deepEqual(tabsView(Object.assign(initialState(), { tab: 'judges' })).slice(-3, -1), [{ key: 'labels', label: 'Labels', on: false }, { key: 'judges', label: 'Judges', on: true }]);
  assert.equal(parseLink('?tl=judges&run=live').tab, 'judges');
  assert.deepEqual(linkOpenPatch({ signedIn: true, role: 'admin', view: 'home' }, '?tl=judges'), { view: 'tracelab', role: 'admin', navOpen: true, detail: null });
  assert.equal(linkSearch('', { tab: 'judges', run: 'live' }), '?tl=judges&run=live');
  assert.equal(parseLink('?tl=nowhere'), null);
  assert.deepEqual(POLLED_TABS, ['conversations', 'runs'], 'the 45-second conversation check does not run on Judges');
});

test('a ?tl=judges link opens the tab, and opening it sends nothing from the page\'s own reads', () => {
  act.applyLink(parseLink('?tl=judges&run=live'));
  assert.equal(S().tab, 'judges');
  net.requests = [];
  tlStore.set({ tab: 'runs' });
  act.setTab('judges');
  assert.equal(S().tab, 'judges');
  assert.deepEqual(net.requests, [], 'the tab reads for itself, when it mounts');
  act.syncUrl();
});

test('the Judges tab shows the table with no conversation filters and no shared refresh line; its reload is its own', () => {
  jfresh({ tlJudgesRows: [SUMMARY_BRIEF], tlJudgesChecks: CHECKS, tlJudgesModes: [], tlJudgesLoadedAt: 1 }, { owner: false });
  const html = page();
  assert.match(html, /<button[^>]*role="tab"[^>]*aria-selected="true"[^>]*>Judges<\/button>/);
  assert.ok(html.includes('0.949 (0.831–0.986)'));
  assert.ok(html.includes('Read only.'));
  assert.equal(html.includes('Search questions and answers'), false, 'the conversation filters do not belong here');
  assert.equal(html.includes('Refresh now'), false);
  assert.equal(html.includes('role="dialog"'), false, 'no mode drawer or dialog is open');
});

test('the platform team, which must name no company, still reaches Judges; every other refusal still stops the page', () => {
  assert.deepEqual(N.PLATFORM_TABS.slice(0, 3), ['progress', 'labels', 'judges']);
  const named = refused('name_a_company');
  const text = 'Trace Lab reviews one company at a time. Open the Super Admin console, choose View as for a company, then open Trace Lab again.';
  assert.equal(N.shellNotice(Object.assign(initialState(), { tab: 'judges' }, named)), '');
  assert.equal(N.shellNotice(Object.assign(initialState(), { tab: 'conversations' }, named)), text, 'the conversation tabs are unchanged');
  assert.equal(N.shellNotice(Object.assign(initialState(), { tab: 'judges' }, refused('reviewers_only'))), 'Trace Lab is for company admins.');
  assert.equal(N.shellNotice(Object.assign(initialState(), { tab: 'judges' }, refused('bad_company'))), 'Trace Lab can only show your own company.');
  assert.equal(N.shellNotice(Object.assign(initialState(), { tab: 'judges' })), '');
});

test('a platform superadmin who has not chosen a company sees the judges instead of the notice', () => {
  jfresh({ tlJudgesRows: [SUMMARY_BRIEF], tlJudgesChecks: CHECKS, tlJudgesModes: [{ id: 'm-1', name: 'invented_codes', status: 'confirmed' }], tlJudgesLoadedAt: 1 },
    { owner: true, over: refused('name_a_company') });
  const html = page();
  assert.ok(html.includes('0.949 (0.831–0.986)'));
  assert.ok(html.includes('Failure modes'), 'and the platform team\'s own controls');
  assert.equal(html.includes('Trace Lab reviews one company at a time'), false);
  jfresh({}, { owner: true, over: Object.assign({ tab: 'conversations' }, refused('name_a_company')) });
  assert.ok(page().includes('Trace Lab reviews one company at a time'));
  jfresh({}, { owner: false, over: refused('reviewers_only') });
  assert.ok(page().includes('Trace Lab is for company admins.'), 'a person the service refuses gets the notice, not the tab');
});

test('the page frame mounts the Judges tab once, and the golden-set and taxonomy lines of its close are untouched', () => {
  const src = here('TraceLabApp.jsx');
  assert.ok(src.includes("import Judges from './views/Judges.jsx';"));
  assert.ok(/UNFILTERED = \['golden', 'map', 'progress', 'labels', 'judges'[,\]]/.test(src), 'no conversation filters on Judges');
  assert.ok(/OWN_REFRESH = \['map', 'progress', 'labels', 'judges'[,\]]/.test(src), 'Judges refreshes itself');
  assert.equal((src.match(/<Judges /g) || []).length, 1);
  assert.ok(src.includes("{s.tab === 'judges' ? <Judges s={s} now={now} /> : null}"));
  assert.ok(src.includes('React.useEffect(() => () => { tact.stopPolls(); }, []);'), 'the taxonomy close is as it was');
  assert.ok(src.includes('return () => { clearInterval(timer); gact.leavePage(); resetForCompany(); act.scrubUrl(); };'), 'the golden set close is as it was');
});

test('the tab\'s own reads need no line in the shared load: the page names no Judges read', () => {
  assert.equal(/judges/i.test(here('actions.js')), false, 'actions.js is untouched');
  assert.equal(/judges/i.test(here('store.js')), false, 'store.js is untouched: the slice is made under the store\'s seq');
  assert.equal(/judges/i.test(here('wiring.js')), false);
});

test('every class the Judges screen uses is in the stylesheet', () => {
  const css = here('styles.css');
  const jsx = here('views/Judges.jsx');
  const used = new Set();
  for (const m of jsx.matchAll(/className=(?:"([^"]+)"|\{'([^']*)'|\{"([^"]*)")/g)) (m[1] || m[2] || m[3]).split(/\s+/).filter((t) => t.startsWith('tl-')).forEach((t) => used.add(t));
  assert.ok(used.size > 20, [...used].join(' '));
  const missing = [...used].filter((t) => !new RegExp('\\.' + t.replace(/[-]/g, '\\-') + '(?![\\w-])').test(css));
  assert.deepEqual(missing, []);
  assert.equal((css.match(/\.tl-dialog \{/g) || []).length, 1, 'the golden run dialog\'s own rule is untouched');
  assert.ok(css.lastIndexOf('prefers-reduced-motion') > css.indexOf('.tl-hl-sug {'));
});

test('the Judges screen reads only through its own actions and names no company', () => {
  const jsx = here('views/Judges.jsx');
  const imports = [...jsx.matchAll(/^import .* from '([^']+)';$/gm)].map((m) => m[1]);
  assert.deepEqual(imports, ['react', 'react-dom', '../judgesActions.js', '../logic/judges.js']);
  assert.equal(/fetch\(|apiFetch|currentOrgId|ORG_ID|organization_id/.test(jsx), false);
  for (const f of ['judgesActions.js', 'logic/judges.js', 'logic/judgesText.js', 'logic/judgesCommon.js', 'judgesApi.js']) {
    assert.equal(/currentOrgId|ORG_ID/.test(here(f)), false, f + ' names no company of its own');
  }
});

test('a company switch while the tab is open empties it and the new company starts from nothing', () => {
  jfresh({ tlJudgesRows: [SUMMARY_BRIEF], tlJudgesLoadedAt: 1, tlJudgeId: 'j-2', tlJudge: DETAIL, tlJudgeRunId: 'r-dev', tlJudgeRun: RUN_DEV }, { owner: true });
  assert.ok(page().includes('0.949'));
  tlStore.set((s) => Object.assign(initialState(), { seq: s.seq + 1, tab: s.tab }));
  const html = page();
  assert.equal(html.includes('0.949'), false, 'nothing of the last company is on screen');
  assert.equal(html.includes('aria-label="Judge"'), false);
  assert.deepEqual(reqs(), []);
});
