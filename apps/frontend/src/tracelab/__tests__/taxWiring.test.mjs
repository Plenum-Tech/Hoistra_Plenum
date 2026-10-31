// taxWiring — the Labels tab and the failure-mode screens wired into Trace Lab's own page: the tab list, labels and
// link, the page frame mounting the Labels view, the mode drawer and the mode dialogs, the page notice that must not
// hide the platform-wide tabs, the older Progress view stepping aside for the new discovery list, and the page
// closing what the taxonomy work keeps running.
import { test, beforeEach, afterEach, after } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { A, S, fresh, loaded, installDefaults, restoreApis } from './tax-helper.mjs';
import { cleanTaxScreens, taxScreen } from './tax-screen-helper.mjs';

const React = (await import('react')).default;
const { renderToStaticMarkup } = await import('react-dom/server');
const { Shell } = await taxScreen('TraceLabApp.jsx');
const { TABS, linkOpenPatch } = await import('../nav.js');
const { TAB_LABELS, parseLink, tabsView, linkSearch } = await import('../logic/shell.js');
const { initialState } = await import('../store.js');
const { samplingInitial, progressView } = await import('../logic/sampling.js');
const N = await import('../logic/tabNotice.js');

after(cleanTaxScreens);
beforeEach(installDefaults);
afterEach(restoreApis);

const NOW = Date.parse('2026-10-15T12:00:00Z');
const page = (s) => renderToStaticMarkup(React.createElement(Shell, { s, now: NOW }));
const here = (rel) => readFileSync(new URL('../' + rel, import.meta.url), 'utf8');

test('Labels is the seventh tab, after Progress: in the list, the labels, the link parser and the reopen link', () => {
  assert.deepEqual(TABS.slice(0, 7), ['conversations', 'runs', 'golden', 'models', 'map', 'progress', 'labels']);
  assert.equal(TAB_LABELS.labels, 'Labels');
  assert.deepEqual(tabsView(Object.assign(initialState(), { tab: 'labels' })).slice(5, 7), [{ key: 'progress', label: 'Progress', on: false }, { key: 'labels', label: 'Labels', on: true }]);
  assert.equal(parseLink('?tl=labels&run=live').tab, 'labels');
  assert.deepEqual(linkOpenPatch({ signedIn: true, role: 'admin', view: 'home' }, '?tl=labels'), { view: 'tracelab', role: 'admin', navOpen: true, detail: null });
  assert.equal(linkSearch('', { tab: 'labels', run: 'live' }), '?tl=labels&run=live');
});

test('the Labels tab shows the grid with no conversation filters and no shared refresh line; a reload is the grid’s own', () => {
  fresh(loaded({ tlMatrix: { ok: true, modes: [], rows: [], totals: null }, tlMatrixLoadedAt: 1 }), { tab: 'labels' });
  const html = page(S());
  assert.match(html, /role="tab"[^>]*aria-selected="true"[^>]*>Labels<\/button>/);
  assert.ok(html.includes('Counts here are shares of the reviewed sample, not rates for all traffic.'));
  assert.ok(html.includes('No confirmed failure modes yet.'));
  assert.equal(html.includes('Search questions and answers'), false, 'the conversation filters do not belong here');
  assert.equal(html.includes('Refresh now'), false);
});

test('the page mounts the mode drawer and the mode dialogs once, whichever tab opened them', () => {
  fresh(loaded({ tlModesDrawerId: 'm-1', tlModesDrawer: { mode: { id: 'm-1', name: 'counts_cancelled_jobs_as_open', status: 'candidate', level: 'technical', version: 1 },
    contract: { missing: [] }, events: [], positives: [], close_negatives: [], close_negative_candidates: [], notes: [] } }), { tab: 'progress', sampling: samplingInitial(1) });
  let html = page(S());
  assert.match(html, /aria-label="Failure mode counts_cancelled_jobs_as_open"/);
  A.modeDialog('retire', { modeId: 'm-1' });
  html = page(S());
  assert.match(html, /aria-label="Retire counts_cancelled_jobs_as_open"/);
  fresh(loaded({ tlModesDrawerId: null }), { tab: 'runs' });
  assert.equal(page(S()).includes('role="dialog"'), false, 'nothing is open, nothing is drawn');
});

test('Progress mounts the failure-mode rows below the open-coding ones', () => {
  fresh(loaded(), { tab: 'progress', sampling: { progress: { ok: true, open_batches: 0, batches: [], open_coding: { reviewed: 0, wrong: 0, right: 0, missing: 0, missing_turns: [], missing_more: 0, reviewers: [] },
    discovery: [], saturation: { hint: false, text: '' } }, progressLoadedAt: 1 } });
  const html = page(S());
  assert.ok(html.includes('Label completeness'));
  assert.ok(html.indexOf('aria-label="Batches"') < html.indexOf('Label completeness'));
});

// ── the page notice ──────────────────────────────────────────────────────────────────────────────────────────
const refused = (reason) => ({ convs: Object.assign(initialState().convs, { reason, error: 'refused' }) });

test('the platform team, which must name no company, still reaches Progress and Labels; every other refusal still stops the page', () => {
  assert.deepEqual(N.PLATFORM_TABS.slice(0, 2), ['progress', 'labels']);
  const named = refused('name_a_company');
  const text = 'Trace Lab reviews one company at a time. Open the Super Admin console, choose View as for a company, then open Trace Lab again.';
  assert.equal(N.shellNotice(Object.assign(initialState(), { tab: 'conversations' }, named)), text);
  assert.equal(N.shellNotice(Object.assign(initialState(), { tab: 'progress' }, named)), '');
  assert.equal(N.shellNotice(Object.assign(initialState(), { tab: 'labels' }, named)), '');
  assert.equal(N.shellNotice(Object.assign(initialState(), { tab: 'map' }, named)), text);
  assert.equal(N.shellNotice(Object.assign(initialState(), { tab: 'labels' }, refused('reviewers_only'))), 'Trace Lab is for company admins.');
  assert.equal(N.shellNotice(Object.assign(initialState(), { tab: 'labels' })), '');
});

test('a platform superadmin who has not chosen a company sees the Labels grid instead of the notice', () => {
  fresh(loaded({ tlMatrix: { ok: true, modes: [], rows: [], totals: null }, tlMatrixLoadedAt: 1 }, true), Object.assign({ tab: 'labels' }, refused('name_a_company')));
  const html = page(S());
  assert.ok(html.includes('Counts here are shares of the reviewed sample'));
  assert.equal(html.includes('Trace Lab reviews one company at a time'), false);
  fresh(loaded({}, true), Object.assign({ tab: 'conversations' }, refused('name_a_company')));
  assert.ok(page(S()).includes('Trace Lab reviews one company at a time'));
});

// ── the older Progress view and the new discovery list ───────────────────────────────────────────────────────
const OLD = { ok: true, open_batches: 1, block: 10, open_coding: { reviewed: 12, wrong: 3, right: 9, missing: 0, missing_turns: [], missing_more: 0 },
  discovery: { block: 10, points: [{ block: 1, reviews_through: 10, wrong: 3, partial: false }] }, saturation: { available: false, hint: false } };
const MERGED = Object.assign({}, OLD, { discovery: [{ block: 1, reviews_through: 10, new_modes: 2, cumulative_modes: 2 }],
  saturation: { hint: true, text: 'No new failure mode in the last 20 reviews; consider moving to counting.' } });
const state = (progress) => Object.assign(initialState(), { sampling: Object.assign(samplingInitial(), { progress, progressLoadedAt: 1 }) });

test('the older progress view keeps its curve for its own shape and steps aside for the merged list of blocks', () => {
  const older = progressView(state(OLD));
  assert.equal(older.discoveryMoved, false);
  assert.equal(older.curve.dots.length, 1);
  assert.equal(older.saturation, 'Saturation: — (shown once failure modes are named)');
  const merged = progressView(state(MERGED));
  assert.equal(merged.discoveryMoved, true, 'discovery arrived as a list of blocks');
  assert.equal(merged.curve.empty, true, 'the list is never read as .points');
  assert.deepEqual(merged.tiles.map((t) => t.value), ['12', '3', '0', '1'], 'the rest of the older view is unaffected');
  assert.equal(progressView(state(null)).discoveryMoved, false);
});

// ── closing ──────────────────────────────────────────────────────────────────────────────────────────────────
test('closing the page, or switching company, stops what the taxonomy work keeps running, and the Golden set lines of the close are untouched', () => {
  const src = here('TraceLabApp.jsx');
  assert.ok(src.includes("import * as tact from './taxonomyActions.js';"));
  assert.ok(src.includes('React.useEffect(() => () => { tact.stopPolls(); }, []);'), 'on close');
  assert.ok(src.includes('React.useEffect(() => { tact.stopPolls(); }, [scope]);'), 'on a company switch');
  assert.ok(src.indexOf('tact.stopPolls(); }, [scope]') < src.indexOf('if (scopeRef.current === null || scopeRef.current === scope) return;'), 'it runs before the page is emptied');
  assert.ok(/UNFILTERED = \['golden', 'map', 'progress', 'labels'[,\]]/.test(src) && /OWN_REFRESH = \['map', 'progress', 'labels'[,\]]/.test(src));
});
