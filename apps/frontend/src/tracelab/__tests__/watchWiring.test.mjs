// watchWiring — the Watch tab wired into Trace Lab's own page: it is the ninth tab, after Judges (in the tab list, the labels,
// the ?tl= link parser, the reopen link and the address bar); the page frame mounts it without the conversation filters or the
// shared refresh line, and hands it the company's name; the page notice that asks a platform superadmin to name a company
// does not hide it (the platform team reads every company's batches), while every other refusal still stops the page;
// opening it sends nothing from the page's own reads (the tab reads for itself); and a company switch empties it. The files the
// slice must not touch (store.js, actions.js, wiring.js) say nothing of it: the slice is made under the store's seq.
import { test, beforeEach, after } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

import { net, S, reqs, wfresh, installDefaults, tlStore, initialState, BATCHES, CHART_M1, CHART_M2, CHECKS, AUTO, CASES, RELEASES } from './watch-helper.mjs';
import { watchScreen, cleanWatchScreens } from './watch-screen-helper.mjs';

const React = (await import('react')).default;
const { renderToStaticMarkup } = await import('react-dom/server');
const { Shell } = await watchScreen('TraceLabApp.jsx');
const act = await import('../actions.js');
const { TABS, linkOpenPatch } = await import('../nav.js');
const { TAB_LABELS, POLLED_TABS, parseLink, tabsView, linkSearch } = await import('../logic/shell.js');
const N = await import('../logic/tabNotice.js');

after(cleanWatchScreens);
beforeEach(installDefaults);

const NOW = Date.parse('2026-10-15T12:00:00Z');
const page = (company) => renderToStaticMarkup(React.createElement(Shell, { s: S(), now: NOW, company }));
const here = (rel) => readFileSync(new URL('../' + rel, import.meta.url), 'utf8');
const refused = (reason) => ({ convs: Object.assign(initialState().convs, { reason, error: 'refused' }) });
const LOADED = { tlMonitorLoadedAt: 1, tlMonitorBatches: BATCHES, tlMonitorCharts: { m1: { chart: CHART_M1 }, m2: { chart: CHART_M2 } }, tlMonitorChecks: CHECKS.checks,
  tlAuto: AUTO, tlAutoLoadedAt: 1 };

test('Watch is the ninth tab, after Judges: in the list, the labels, the link parser, the reopen link and the address bar', () => {
  assert.deepEqual(TABS, ['conversations', 'runs', 'golden', 'models', 'map', 'progress', 'labels', 'judges', 'watch']);
  assert.equal(TAB_LABELS.watch, 'Watch');
  assert.deepEqual(tabsView(Object.assign(initialState(), { tab: 'watch' })).slice(-2), [{ key: 'judges', label: 'Judges', on: false }, { key: 'watch', label: 'Watch', on: true }]);
  assert.equal(parseLink('?tl=watch&run=live').tab, 'watch');
  assert.deepEqual(linkOpenPatch({ signedIn: true, role: 'admin', view: 'home' }, '?tl=watch'), { view: 'tracelab', role: 'admin', navOpen: true, detail: null });
  assert.equal(linkSearch('', { tab: 'watch', run: 'live' }), '?tl=watch&run=live');
  assert.equal(parseLink('?tl=nowhere'), null);
  assert.deepEqual(POLLED_TABS, ['conversations', 'runs'], 'the 45-second conversation check does not run on Watch');
});

test('a ?tl=watch link opens the tab, and opening it sends nothing from the page\'s own reads', () => {
  act.applyLink(parseLink('?tl=watch&run=live'));
  assert.equal(S().tab, 'watch');
  net.requests = [];
  tlStore.set({ tab: 'runs' });
  act.setTab('watch');
  assert.equal(S().tab, 'watch');
  assert.deepEqual(net.requests, [], 'the tab reads for itself, when it mounts');
  act.syncUrl();
});

test('the Watch tab shows its batches with no conversation filters and no shared refresh line; its refresh is its own', () => {
  wfresh(LOADED, { owner: false });
  const html = page('Northbridge Estates Ltd');
  assert.match(html, /<button[^>]*role="tab"[^>]*aria-selected="true"[^>]*>Watch<\/button>/);
  assert.ok(html.includes('Company: Northbridge Estates Ltd'));
  assert.ok(html.includes('Monitoring batches'));
  assert.equal(html.includes('Search questions and answers'), false, 'the conversation filters do not belong here');
  assert.equal(html.includes('Refresh now'), false);
  assert.equal((html.match(/>Refresh<\/button>/g) || []).length, 1);
});

test('the platform team, which must name no company, still reaches Watch; every other refusal still stops the page', () => {
  assert.deepEqual(N.PLATFORM_TABS, ['progress', 'labels', 'judges', 'watch']);
  const named = refused('name_a_company');
  const text = 'Trace Lab reviews one company at a time. Open the Super Admin console, choose View as for a company, then open Trace Lab again.';
  assert.equal(N.shellNotice(Object.assign(initialState(), { tab: 'watch' }, named)), '');
  assert.equal(N.shellNotice(Object.assign(initialState(), { tab: 'conversations' }, named)), text, 'the conversation tabs are unchanged');
  assert.equal(N.shellNotice(Object.assign(initialState(), { tab: 'watch' }, refused('reviewers_only'))), 'Trace Lab is for company admins.');
  assert.equal(N.shellNotice(Object.assign(initialState(), { tab: 'watch' }, refused('bad_company'))), 'Trace Lab can only show your own company.');
  assert.equal(N.shellNotice(Object.assign(initialState(), { tab: 'watch' })), '');
});

test('a platform superadmin who has not chosen a company sees the batches instead of the notice, and a refused person gets the notice', () => {
  wfresh(LOADED, { owner: true, over: refused('name_a_company') });
  const html = page('Plenum');
  assert.ok(html.includes('All companies'));
  assert.ok(html.includes('Run now'));
  assert.ok(html.includes('wrong_period'));
  assert.equal(html.includes('Trace Lab reviews one company at a time'), false);
  wfresh({}, { owner: true, over: Object.assign({ tab: 'conversations' }, refused('name_a_company')) });
  assert.ok(page().includes('Trace Lab reviews one company at a time'));
  wfresh({}, { owner: false, over: refused('reviewers_only') });
  assert.ok(page().includes('Trace Lab is for company admins.'), 'a person the service refuses gets the notice, not the tab');
});

test('the page frame mounts the Watch tab once with the company\'s name, and the earlier tabs\' lines are untouched', () => {
  const src = here('TraceLabApp.jsx');
  assert.ok(src.includes("import Watch from './views/Watch.jsx';"));
  assert.ok(/UNFILTERED = \['golden', 'map', 'progress', 'labels', 'judges', 'watch'\]/.test(src), 'no conversation filters on Watch');
  assert.ok(/OWN_REFRESH = \['map', 'progress', 'labels', 'judges', 'watch'\]/.test(src), 'Watch refreshes itself');
  assert.equal((src.match(/<Watch /g) || []).length, 1);
  assert.ok(src.includes("{s.tab === 'watch' ? <Watch s={s} now={now} company={company} /> : null}"));
  assert.ok(src.includes("{s.tab === 'judges' ? <Judges s={s} now={now} /> : null}"), 'the Judges line is as it was');
  assert.ok(src.includes('React.useEffect(() => () => { tact.stopPolls(); }, []);'), 'the taxonomy close is as it was');
  assert.ok(src.includes('return () => { clearInterval(timer); gact.leavePage(); resetForCompany(); act.scrubUrl(); };'), 'the golden set close is as it was');
  assert.ok(/<Shell s=\{s\} now=\{Date\.now\(\)\} company=\{\(vals && vals\.tenant\) \|\| ''\} chrome=\{chrome\} \/>/.test(src), 'the name comes from the host\'s own values');
});

test('the tab\'s own reads need no line in the shared load: the page names no Watch read', () => {
  assert.equal(/watch/i.test(here('actions.js')), false, 'actions.js is untouched');
  assert.equal(/watch/i.test(here('store.js')), false, 'store.js is untouched: the slice is made under the store\'s seq');
  assert.equal(/watch/i.test(here('wiring.js')), false);
  assert.equal(/watch/i.test(here('goldenActions.js')), false, 'the golden run code is untouched: Watch claims a run through the store\'s own `own` key');
});

test('Watch edits no earlier module beyond its registration, and only the page frame imports it', () => {
  for (const f of ['actions.js', 'store.js', 'wiring.js', 'goldenActions.js', 'taxonomyActions.js', 'judgesActions.js', 'samplingActions.js', 'annotateActions.js']) {
    assert.equal(/watchActions|watchApi|logic\/watch/.test(here(f)), false, f);
  }
  const importers = ['nav.js', 'logic/shell.js', 'logic/tabNotice.js', 'TraceLabApp.jsx', 'views/Conversations.jsx', 'views/Judges.jsx'].filter((f) => /views\/Watch|watchActions/.test(here(f)));
  assert.deepEqual(importers, ['TraceLabApp.jsx']);
});

test('a company switch while the tab is open empties it and the new company starts from nothing', () => {
  wfresh(Object.assign({}, LOADED, { tlCases: CASES.cases, tlCasesLoadedAt: 1, tlRelease: RELEASES, tlReleaseLoadedAt: 1 }), { owner: false });
  assert.ok(page('Northbridge').includes('wrong_period'));
  assert.ok(page('Northbridge').includes('release-1'));
  tlStore.set((s) => Object.assign(initialState(), { seq: s.seq + 1, tab: s.tab }));
  const html = page('Other Company');
  assert.equal(html.includes('wrong_period'), false, 'nothing of the last company is on screen');
  assert.equal(html.includes('release-1'), false);
  assert.equal(html.includes('e-7f3a19c2'), false);
  assert.deepEqual(reqs(), []);
});
