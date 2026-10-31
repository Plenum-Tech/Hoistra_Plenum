// Attachment A3 and the evaluation line. A3 is exactly the two approved lines in PlatformCostPage.jsx; with the flag
// off the page renders byte for byte what it rendered without them (and with the flag on, before the line has read
// anything, too); the line imports nothing but React and the flag, loads its body lazily, contains its own
// failures, reads the month's spend without ever naming a company, and says "Evaluation $X, not included in the
// totals above" or nothing at all.
import { test, after, beforeEach } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync, mkdirSync, mkdtempSync, writeFileSync, rmSync } from 'node:fs';
import { join } from 'node:path';
import { pathToFileURL } from 'node:url';
import { build } from 'esbuild';
import { bundle, cleanBundles, FRONTEND, SRC } from './esbuild-helper.mjs';

const made = [];
after(() => { cleanBundles(); made.splice(0).forEach((d) => rmSync(d, { recursive: true, force: true })); });
const React = (await import('react')).default;
const { renderToStaticMarkup } = await import('react-dom/server');

globalThis.window = { location: { origin: 'http://test.local' }, addEventListener: () => {}, removeEventListener: () => {} };
let seen = [];
let reply = [200, { month: '2026-10', total_usd: 1.234, by_company: [], by_kind: {} }];
globalThis.fetch = async (url, opts) => {
  seen.push({ url: new URL(String(url)), method: (opts && opts.method) || 'GET' });
  return { ok: reply[0] < 300, status: reply[0], statusText: String(reply[0]), text: async () => JSON.stringify(reply[1]), headers: { get: () => null } };
};
beforeEach(() => { seen = []; });

const PAGE = join(SRC, 'components', 'shell', 'PlatformCostPage.jsx');
const A3_IMPORT = "import EvalSpendLine from '../../tracelab/EvalSpendLine.jsx';\n";
const A3_LINE = '      <EvalSpendLine monthLabel={st.showing || month.label} />\n';
const count = (s, sub) => s.split(sub).length - 1;

// Bundles page source text as if it sat in components/shell (so its own imports resolve), and imports it.
async function bundleSource(contents, flagOn) {
  const res = await build({
    stdin: { contents, resolveDir: join(SRC, 'components', 'shell'), sourcefile: 'PlatformCostPage.jsx', loader: 'jsx' },
    bundle: true, write: false, format: 'esm', platform: 'node', jsx: 'automatic',
    external: ['react', 'react/*', 'react-dom', 'react-dom/*'], loader: { '.css': 'empty' }, logLevel: 'silent',
    define: flagOn ? { 'import.meta.env': JSON.stringify({ VITE_TRACE_LAB: '1' }) } : {}
  });
  const cache = join(FRONTEND, 'node_modules', '.cache', 'tracelab-tests');
  mkdirSync(cache, { recursive: true });
  const dir = mkdtempSync(join(cache, 'pc-'));
  made.push(dir);
  writeFileSync(join(dir, 'bundle.mjs'), res.outputFiles[0].text);
  return import(pathToFileURL(join(dir, 'bundle.mjs')).href);
}

// The page's own view model for a real-shaped month (logic/platformCost.js pcVals), as the console renders it.
const { pcVals } = await import('../../logic/platformCost.js');
const DATA = { month: '2026-10',
  totals: { total_cost_usd: 595.86, chat_cost_usd: 5.86, migration_cost_usd: 0, infra_share_usd: 590, revenue_usd: 47.12, credits: 152, profit_usd: -548.74, margin: -11.646, cost_per_query_usd: 0.0652, chat_turns: 90, active_companies: 1 },
  companies: [{ organization_id: 'p', name: 'Company P', active: true, chat_turns: 67, chat_cost_usd: 5.08, infra_share_usd: 295, total_cost_usd: 300.08, credits: 42, revenue_usd: 13.02, profit_usd: -287.06, margin: -22.0476 },
    { organization_id: 'n', name: 'Company N', active: false, total_cost_usd: 0 }],
  by_model: [{ model: 'claude-sonnet-5', calls: 26, cost_usd: 2.34 }],
  assumptions: { infra_monthly_usd: 590, credit_usd: 0.31, infra_split: 'equally among companies active in the month' }, not_recorded: [], unreadable: [] };
const ctl = { state: { pcData: DATA, pcMonth: '2026-10', pcLoadedAt: Date.UTC(2026, 9, 5, 10, 12) }, pcLoad() {}, pcOpen() {}, pcClose() {}, pcSetTab() {} };
const VALS = pcVals(ctl, new Date(Date.UTC(2026, 9, 5, 10, 12)));
const render = (Page) => renderToStaticMarkup(React.createElement(Page, { vals: VALS }));

test('A3 is exactly the two approved lines, in their approved places', () => {
  const src = readFileSync(PAGE, 'utf8');
  assert.equal(count(src, A3_IMPORT), 1);
  assert.equal(count(src, A3_LINE), 1);
  assert.ok(src.includes("import PlatformCostPanel, { ToneIcon } from './PlatformCostPanel.jsx';\n" + A3_IMPORT));
  assert.ok(src.includes(A3_LINE + '      {vals.pcPanel ? <PlatformCostPanel panel={vals.pcPanel} bindTip={bindTip} /> : null}\n'));
  assert.deepEqual(src.split('\n').filter((l) => /tracelab|EvalSpend/i.test(l)).map((l) => l.trim()),
    [A3_IMPORT.trim(), A3_LINE.trim()]);
});

test('with the flag off the Platform cost page renders exactly what it rendered without A3', async () => {
  const src = readFileSync(PAGE, 'utf8');
  const without = src.replace(A3_IMPORT, '').replace(A3_LINE, '');
  assert.equal(without.length, src.length - A3_IMPORT.length - A3_LINE.length);
  const before = render((await bundleSource(without, false)).default);
  const off = render((await bundleSource(src, false)).default);
  assert.ok(before.includes('Platform cost') && before.includes('Company P'), 'the page really rendered');
  assert.equal(off, before);
  assert.equal(off.includes('Evaluation'), false);
  const on = render((await bundleSource(src, true)).default);
  assert.equal(on, before, 'flag on, the line adds nothing until it has read the month');
  assert.equal(seen.length, 0, 'rendering the page sends no Trace Lab request');
});

test('the line imports only React and the flag, loads its body lazily, and contains its failures', async () => {
  const src = readFileSync(new URL('../EvalSpendLine.jsx', import.meta.url), 'utf8');
  const imports = [...src.matchAll(/^import .* from '([^']+)';$/gm)].map((m) => m[1]);
  assert.deepEqual(imports, ['react', './flag.js']);
  assert.ok(src.includes("const Body = ON ? React.lazy(() => import('./views/EvalSpendBody.jsx')) : null;"));
  assert.ok(src.includes('getDerivedStateFromError'));
  const { default: Off } = await bundle('EvalSpendLine.jsx');
  assert.equal(renderToStaticMarkup(React.createElement(Off, { monthLabel: 'October 2026' })), '');
  const { default: On } = await bundle('EvalSpendLine.jsx', { flagOn: true });
  assert.equal(renderToStaticMarkup(React.createElement(On, { monthLabel: 'October 2026' })), '');
});

test('the month label becomes the route\'s key, and the line is the approved sentence or nothing', async () => {
  const { monthKeyOf, spendText } = await import('../logic/spend.js');
  assert.equal(monthKeyOf('October 2026'), '2026-10');
  assert.equal(monthKeyOf('January 2027'), '2027-01');
  assert.equal(monthKeyOf('2026-09'), '2026-09');
  assert.equal(monthKeyOf(''), null);
  assert.equal(monthKeyOf('Octember 2026'), null);
  assert.equal(spendText({ total_usd: 1.234 }), 'Evaluation $1.23, not included in the totals above');
  assert.equal(spendText({ total_usd: 0.004 }), 'Evaluation $0.0040, not included in the totals above');
  assert.equal(spendText({ total_usd: 0 }), 'Evaluation $0.00, not included in the totals above');
  assert.equal(spendText({ total_usd: null }), '');
  assert.equal(spendText(null), '');
  assert.equal(spendText({ total_usd: 'n/a' }), '');
});

test('the body reads the month\'s spend platform-wide, never naming a company, and draws one quiet line', async () => {
  const { readMonthSpend, SpendLine } = await bundle('views/EvalSpendBody.jsx');
  const { setActingOrg } = await import('../../api/client.js');
  setActingOrg('org-viewed');
  try {
    const out = await readMonthSpend('2026-10');
    assert.equal(out.total_usd, 1.234);
  } finally { setActingOrg(null); }
  assert.equal(seen.length, 1);
  assert.equal(seen[0].method, 'GET');
  assert.equal(seen[0].url.pathname, '/backend/trace-lab/api/trace-lab/spend');
  assert.equal(seen[0].url.searchParams.get('month'), '2026-10');
  assert.equal(seen[0].url.searchParams.has('organization_id'), false);
  assert.equal(renderToStaticMarkup(React.createElement(SpendLine, { text: 'Evaluation $1.23, not included in the totals above' })),
    '<p class="pc-quiet" role="status">Evaluation $1.23, not included in the totals above · <a href="/lab/">Open Trace Lab</a></p>');
  assert.equal(renderToStaticMarkup(React.createElement(SpendLine, { text: '' })), '');
  reply = [403, { detail: { reason: 'platform_only', error: 'Platform only.' } }];
  await assert.rejects(readMonthSpend('2026-10'));
  reply = [200, { month: '2026-10', total_usd: 1.234 }];
});
