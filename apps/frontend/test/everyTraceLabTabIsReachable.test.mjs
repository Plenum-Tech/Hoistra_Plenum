// Every Trace Lab tab can be seen and opened.
//
// Found on the deployed page (3 Nov 2026): the Synthetic questions tab was added to nav.js's
// TABS, and the tab bar showed nothing between "Golden set" and "Model calls". tabsView maps
// TABS through TAB_LABELS, so a tab with no label renders with `label: undefined` — a button
// with no text, which looks like a gap rather than a mistake. The page was deployed, the route
// worked, and there was no way to reach it.
//
// Two lists and a renderer have to agree: TABS says what exists, TAB_LABELS says what it is
// called, and TraceLabApp decides what to draw for it. A tab missing from any of the three is
// invisible, unnamed, or an empty panel.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

const read = (p) => readFileSync(new URL(p, import.meta.url), 'utf-8');
const { TABS } = await import('../src/tracelab/nav.js');
const { TAB_LABELS, tabsView } = await import('../src/tracelab/logic/shell.js');
const APP = read('../src/tracelab/TraceLabApp.jsx');

test('every tab has a label', () => {
  const unnamed = TABS.filter((t) => !TAB_LABELS[t]);
  assert.deepEqual(unnamed, [],
    'these tabs render as a button with no text: ' + unnamed.join(', '));
});

test('every label belongs to a tab', () => {
  const orphans = Object.keys(TAB_LABELS).filter((k) => TABS.indexOf(k) < 0);
  assert.deepEqual(orphans, [], 'a label for a tab that does not exist: ' + orphans.join(', '));
});

test('every tab is drawn by something', () => {
  for (const tab of TABS) {
    assert.ok(APP.includes("s.tab === '" + tab + "'"),
      'nothing renders the ' + tab + ' tab, so opening it shows an empty page');
  }
});

test('the bar names each tab in order', () => {
  const view = tabsView({ tab: 'runs' });
  assert.equal(view.length, TABS.length);
  assert.ok(view.every((t) => typeof t.label === 'string' && t.label.length > 0));
  assert.deepEqual(view.map((t) => t.key), TABS);
});

test('exactly one tab is marked current', () => {
  const view = tabsView({ tab: 'golden' });
  assert.deepEqual(view.filter((t) => t.on).map((t) => t.key), ['golden']);
});

test('synthetic question generation is not in this Trace Lab', () => {
  // It lives in the lab app at /lab/ (src/lab/Synthetic.jsx), which is the Trace Lab a reviewer
  // signs into. It was built here first by mistake; a tab here would be a second copy of the
  // same page, and the two would drift.
  assert.ok(!TABS.includes('scenarios'), 'the admin Trace Lab grew a synthetic tab again');
  assert.ok(!APP.includes('Scenarios'), 'TraceLabApp still renders the removed page');
});
