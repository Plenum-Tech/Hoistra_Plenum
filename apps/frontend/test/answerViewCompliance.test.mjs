// answerViewCompliance — an answer with cards (ComplianceAnswer.jsx) keeps all of its words.
//
// Findings 1 and 2: a planned or loop answer that read a card tool carries `compliance_response`
// whose narrative is only the answer's first paragraph (planner._lead). Drawn from the narrative
// alone, the rest of the answer — its tables, later sections, caveats, and every word of a
// bullet-only answer — never reached the screen. The full Markdown answer (`fallbackText`, the
// message's text) goes to the converter beside the cards (programFromCompliance(rich, answer)),
// and the VITE_OPENUI_ALL_ANSWERS=0 path shows it too. The compliance engine, whose narrative is
// its own and whose plain answer repeats its zones, keeps its narrative.
//
// Finding 48: the live draft is cleaned of half-written Markdown (fromMarkdown.draftSafe) before
// it is drawn, so a lone '**' or a table header row with no divider yet never shows.
// Finding 30: the legacy bars say how many were cut.
//
// ComplianceAnswer is bundled with esbuild (as Vite would) with the lazy OpenUI wrapper mapped to
// the renderer itself, so the markup is what a reader sees once the chunk has loaded.
import { test, after } from 'node:test';
import assert from 'node:assert/strict';
import { build } from 'esbuild';
import { writeFileSync, rmSync, readFileSync } from 'node:fs';
import { fileURLToPath, pathToFileURL } from 'node:url';
import path from 'node:path';

globalThis.window = globalThis.window || { location: { origin: 'http://test.local' }, addEventListener: () => {}, removeEventListener: () => {} };

const here = path.dirname(fileURLToPath(import.meta.url));
const src = (p) => readFileSync(new URL('../src/' + p, import.meta.url), 'utf8');
const lazyToReal = {
  name: 'lazy-to-real',
  setup(b) {
    b.onResolve({ filter: /LazyOpenUIAnswer\.jsx$/ }, () => ({ path: path.join(here, '../src/openui/OpenUIAnswer.jsx') }));
  }
};
async function bundle(name, define) {
  const out = path.join(here, '.answer-view-' + name + '.bundle.mjs');
  const res = await build({
    stdin: { contents: "export { default as ComplianceAnswer } from '../src/components/shell/ComplianceAnswer.jsx';", resolveDir: here, loader: 'js' },
    bundle: true, write: false, format: 'esm', platform: 'node', jsx: 'automatic',
    external: ['react', 'react/*', 'react-dom', 'react-dom/*', '@openuidev/react-lang', '@openuidev/lang-core', 'zod'],
    loader: { '.css': 'empty' }, logLevel: 'silent', plugins: [lazyToReal], define: define || {}
  });
  writeFileSync(out, res.outputFiles[0].text);
  after(() => rmSync(out, { force: true }));
  return (await import(pathToFileURL(out).href)).ComplianceAnswer;
}
const On = await bundle('on');
const Off = await bundle('off', { 'import.meta.env.VITE_OPENUI_ALL_ANSWERS': '"0"' });
const React = (await import('react')).default;
const { renderToStaticMarkup } = await import('react-dom/server');
const html = (C, props) => renderToStaticMarkup(React.createElement(C, Object.assign({ open: false, onToggle: () => {} }, props)));
// Words, not markup: a table cell and a heading read the same as prose here.
const words = (h) => h.replace(/<[^>]+>/g, ' ').replace(/&#x27;/g, "'").replace(/&amp;/g, '&').replace(/\s+/g, ' ');

const base = { narrative: '', sections: [], groups: [], kpis: [], actions: [], insights: [], certificates: [], pending: [], offers: [], validation: null, steps: [], cost: null };
const rich = (o) => Object.assign({}, base, o);

// finding 2's planned turn: the lead paragraph is the narrative; the table, the second section and
// the caveat are only in the answer.
const PLANNED_ANSWER = 'Seven work orders are overdue across the portfolio this month, mostly at Kingsway House and Elm Court.\n\n## Work orders\n\n| WO | Job | Days |\n|---|---|---|\n| WO-1184 | Boiler service | 12 |\n| WO-1190 | Lift inspection | 9 |\n\n## Energy\n\nBase load at Kingsway House sits at 41 kW overnight.\n\nCaveat: September meter data for Kingsway House is missing.';
const PLANNED = rich({
  narrative: 'Seven work orders are overdue across the portfolio this month, mostly at Kingsway House and Elm Court.',
  kpis: [{ count: 7, label: 'Overdue', severity: 'warning' }],
  actions: [{ title: 'Chase WO-1184 boiler service', severity: 'warning' }],
  steps: [{ stage: 'plan', label: 'Planned the read' }]
});
const BULLETS_ANSWER = '- Apex holds four of the overdue jobs\n- Brightline holds two\n- Corvane holds one';
const BULLETS = rich({ narrative: '', kpis: [{ count: 7, label: 'Overdue', severity: 'warning' }] });

const hasPlannedText = (w) => ['WO-1190', 'Lift inspection', '41 kW', 'September meter data'].filter((s) => !w.includes(s));

test('a planned answer shows its tables, later sections and caveat beside the cards (OpenUI on)', () => {
  const w = words(html(On, { rich: PLANNED, fallbackText: PLANNED_ANSWER }));
  assert.deepEqual(hasPlannedText(w), [], w);
  assert.match(w, /Chase WO-1184 boiler service/);
});

test('with VITE_OPENUI_ALL_ANSWERS=0 the planned answer is shown whole, not only its lead', () => {
  const w = words(html(Off, { rich: PLANNED, fallbackText: PLANNED_ANSWER }));
  assert.deepEqual(hasPlannedText(w), [], w);
  assert.equal(w.split('Seven work orders are overdue').length - 1, 1, 'the lead is shown once');
});

test('a bullet-only loop answer with cards still shows its bullets', () => {
  for (const [name, C] of [['on', On], ['off', Off]]) {
    const w = words(html(C, { rich: BULLETS, fallbackText: BULLETS_ANSWER }));
    assert.match(w, /Apex holds four of the overdue jobs/, name);
    assert.match(w, /Corvane holds one/, name);
  }
});

test('the compliance engine keeps its own narrative when its plain answer is only a part of it', () => {
  const narrative = 'Three certificates lapse in the next 30 days, two of them life-safety duties. Renew the fire alarm and LOLER paperwork first.';
  const answer = 'Three certificates lapse in the next 30 days, two of them life-safety duties.';
  for (const [name, C] of [['on', On], ['off', Off]]) {
    const w = words(html(C, { rich: rich({ narrative, kpis: [{ count: 3, label: 'Lapsing', severity: 'critical' }] }), fallbackText: answer }));
    assert.match(w, /Renew the fire alarm and LOLER paperwork first/, name);
  }
});

test('the live draft never shows a lone ** or a table row whose divider has not arrived', () => {
  for (const narrative of ['**25 work orders are o', 'Energy use fell.\n\n| Building | Aug kWh/m² | Sep kWh/m² ']) {
    const h = html(On, { rich: rich({ narrative }), live: true });
    assert.doesNotMatch(words(h), /\*\*|\| Building/, narrative);
  }
  // A finished answer is drawn from its text as it is.
  assert.match(words(html(On, { rich: rich({ narrative: '**25 work orders** are overdue.' }) })), /25 work orders/);
});

test('the legacy overdue bars say how many were cut', () => {
  const rows = Array.from({ length: 8 }, (_, i) => ({ label: 'B' + i + ' · Fire Risk Assessment', days: 20 - i, pct: '50%', severity: 'critical' }));
  const w = words(html(Off, { rich: rich({ narrative: 'n', overdue: { buildings: rows, vendors: [], more: { buildings: 4, vendors: 0 } } }) }));
  assert.match(w, /\+4 more/);
  assert.match(w, /B0 · Fire Risk Assessment/);
});

test('ComplianceAnswer hands the full answer to the converter and every OpenUI answer a fallback', () => {
  const s = src('components/shell/ComplianceAnswer.jsx');
  assert.match(s, /programFromCompliance\([^;]*, live \? '' : \(fallbackText \|\| ''\)\)/);
  assert.match(s, /\[rich, view, streamingProgram, live, fallbackText\]/);
  // Finding 52: a stored or live view that draws nothing falls back to the answer's own text.
  for (const file of ['components/shell/ComplianceAnswer.jsx', 'screens/Chat.jsx', 'components/shell/OrchestratorDock.jsx', 'openui/AnyAnswer.jsx']) {
    const tags = src(file).match(/<LazyOpenUIAnswer [^>]*\/>/g) || [];
    assert.ok(tags.length, file);
    for (const t of tags) assert.match(t, /fallbackText=\{/, file + ': ' + t);
  }
});
