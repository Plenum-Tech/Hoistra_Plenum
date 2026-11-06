// An answer's table can be sorted, and keeps its sort (2 Nov 2026, feedback "Preserve conversation
// position": "Do not ... force them to repeat sorting and scrolling"). A table's header sorts by
// its column (ascending, descending, then the answer's own order); what the reader set - the sort
// and "Show all" - is remembered per conversation, answer and table (openui/tableView.js), so a new
// question, a re-run, another conversation or a reload never puts it back to the default.
import { test, after } from 'node:test';
import assert from 'node:assert/strict';
import { build } from 'esbuild';
import { writeFileSync, rmSync, readFileSync } from 'node:fs';
import { fileURLToPath, pathToFileURL } from 'node:url';
import path from 'node:path';

const stored = {};
globalThis.window = {
  location: { origin: 'http://test.local' },
  sessionStorage: { getItem: (k) => (k in stored ? stored[k] : null), setItem: (k, v) => { stored[k] = String(v); } }
};

const here = path.dirname(fileURLToPath(import.meta.url));
const outFile = path.join(here, '.answer-tables-sort.bundle.mjs');
const res = await build({
  stdin: {
    contents: "export { library } from '../src/openui/library.jsx';\nexport * from '../src/openui/tableView.js';",
    resolveDir: here, loader: 'js'
  },
  bundle: true, write: false, format: 'esm', platform: 'node', jsx: 'automatic',
  external: ['react', 'react/*', 'react-dom', 'react-dom/*', '@openuidev/react-lang', 'zod'],
  loader: { '.css': 'empty' }, logLevel: 'silent'
});
writeFileSync(outFile, res.outputFiles[0].text);
after(() => rmSync(outFile, { force: true }));

const { library, AnswerKey, getView, setView, sortRows, tableViewKey } = await import(pathToFileURL(outFile).href);
const React = (await import('react')).default;
const { renderToStaticMarkup } = await import('react-dom/server');
const { Renderer } = await import('@openuidev/react-lang');
const src = (p) => readFileSync(new URL('../src/' + p, import.meta.url), 'utf8');

const ROWS = [['Bishopsgate Tower', 126, '2026-06-29'], ['Manchester Town Hall', 5, '2026-10-28'], ['RT5, Crossrail', 15, '2026-10-18'],
  ['Vasiliki tower 3', '£1,200', ''], ['Apex House', 4, '2026-10-29'], ['Brent Cross', 61, '2026-09-02'], ['Canary Wharf', 9, '2026-10-24'],
  ['Docklands', 33, '2026-09-30'], ['Euston', 2, '2026-10-31'], ['Farringdon', 77, '2026-08-17']];
const PROGRAM = 'root = Answer([t])\nt = Table(["Building", "Days overdue", "Expired"], ' + JSON.stringify(ROWS) + ')';
const draw = (answerKey) => renderToStaticMarkup(React.createElement(AnswerKey.Provider, { value: answerKey || '' },
  React.createElement(Renderer, { library, response: PROGRAM, isStreaming: false, publishObservability: false })));
const firstCells = (html) => [...html.matchAll(/<tr class="oui-row"[^>]*><td[^>]*>(?:<[^>]+>)*([^<]+)/g)].map((m) => m[1]);

test('each heading is a button that sorts by its column, and says how it is sorted', () => {
  const html = draw('');
  assert.equal((html.match(/<th[ >]/g) || []).length, 3);
  assert.match(html, /<th aria-sort="none"><button type="button" class="oui-sort" title="Sort by Building">Building<i class="ph ph-caret-up-down" aria-hidden="true"><\/i><\/button><\/th>/);
  assert.match(html, /<th class="oui-num" aria-sort="none"><button type="button" class="oui-sort" title="Sort by Days overdue">/);
  // the answer's own order until the reader sorts
  assert.deepEqual(firstCells(html).slice(0, 3), ['Bishopsgate Tower', 'Manchester Town Hall', 'RT5, Crossrail']);
});

test('figures sort as numbers, text as words, blanks last, ties in the answer\'s order', () => {
  const kinds = ['text', 'num', 'text'];
  const by = (col, dir) => sortRows(ROWS, kinds, { col, dir }).map((r) => r[0]);
  assert.deepEqual(by(1, 'desc').slice(0, 4), ['Vasiliki tower 3', 'Bishopsgate Tower', 'Farringdon', 'Brent Cross']);
  assert.deepEqual(by(1, 'asc').slice(0, 3), ['Euston', 'Apex House', 'Manchester Town Hall']);
  assert.deepEqual(by(0, 'asc').slice(0, 3), ['Apex House', 'Bishopsgate Tower', 'Brent Cross']);
  const dates = by(2, 'asc');
  assert.equal(dates[0], 'Bishopsgate Tower');
  assert.equal(dates[dates.length - 1], 'Vasiliki tower 3', 'a blank cell sorts last either way');
  assert.equal(by(2, 'desc')[ROWS.length - 1], 'Vasiliki tower 3');
  assert.deepEqual(sortRows(ROWS, kinds, null), ROWS);
});

test('a table drawn again in the same answer comes back as the reader left it', () => {
  const key = tableViewKey('session-7:3', ['Building', 'Days overdue', 'Expired']);
  setView(key, { sort: { col: 1, dir: 'desc' }, all: true });
  const html = draw('session-7:3');
  assert.match(html, /<th class="oui-num" aria-sort="descending">/);
  assert.deepEqual(firstCells(html).slice(0, 3), ['Vasiliki tower 3', 'Bishopsgate Tower', 'Farringdon']);
  assert.equal(firstCells(html).length, ROWS.length, '"Show all" is remembered too');
  assert.match(html, />Show fewer</);
  // another answer, or another conversation, starts from its own default
  assert.deepEqual(firstCells(draw('session-7:5')).slice(0, 1), ['Bishopsgate Tower']);
  assert.deepEqual(firstCells(draw('session-8:3')).slice(0, 1), ['Bishopsgate Tower']);
  // a table with no conversation to belong to keeps its view only while it is on screen
  assert.equal(tableViewKey('', ['Building']), '');
});

test('what the reader set is kept for the tab, so a reload keeps it', () => {
  setView('session-9:1|Building', { sort: { col: 0, dir: 'asc' }, all: false });
  const saved = JSON.parse(stored['hoistra.tableView.v1']);
  assert.ok(saved.some(([k, v]) => k === 'session-9:1|Building' && v.sort.dir === 'asc'));
  assert.deepEqual(getView('session-9:1|Building'), { sort: { col: 0, dir: 'asc' }, all: false });
  assert.equal(getView(''), null);
});

test('the chat page and the dock tell each answer which conversation and turn it is', () => {
  for (const file of ['screens/Chat.jsx', 'components/shell/OrchestratorDock.jsx']) {
    const s = src(file);
    assert.match(s, /import \{ AnswerKey \} from '[./]+\/openui\/tableView\.js'/, file);
    assert.match(s, /<AnswerKey\.Provider value=\{vals\.chatSessionId \? vals\.chatSessionId \+ ":" \+ m\.key : ""\}>/, file);
  }
});
