// gateFinalPlan — the write gate on a Go-engine run shows what the write will do: per destination
// the rows, the merges, what is already on file, values that will not fit, rows that cannot be
// written, the columns it creates. A Python-engine run has no plan, and the gate looks as before.
// The component is bundled with esbuild (Vite's) and rendered to markup with react-dom/server.
import { test, after } from 'node:test';
import assert from 'node:assert/strict';
import { build } from 'esbuild';
import { writeFileSync, rmSync } from 'node:fs';
import { fileURLToPath, pathToFileURL } from 'node:url';
import path from 'node:path';

const here = path.dirname(fileURLToPath(import.meta.url));
const entry = path.join(here, '../src/cafm/features/ai/pipeline/migration/gates/gate-final-plan.tsx');
const outFile = path.join(here, '.gate-final-plan.bundle.mjs');

const res = await build({
  entryPoints: [entry], bundle: true, write: false, format: 'esm', platform: 'node', jsx: 'automatic',
  external: ['react', 'react/*', 'react-dom', 'react-dom/*'], logLevel: 'silent',
});
writeFileSync(outFile, res.outputFiles[0].text);
after(() => rmSync(outFile, { force: true }));
const { default: GateFinalPlan } = await import(pathToFileURL(outFile).href);
const React = (await import('react')).default;
const { renderToStaticMarkup } = await import('react-dom/server');

const render = (plan) => renderToStaticMarkup(React.createElement(GateFinalPlan, { plan }));
const text = (html) => html.replace(/<[^>]+>/g, ' ').replace(/\s+/g, ' ').trim();

test('no plan, nothing rendered: a Python-engine gate is unchanged', () => {
  assert.equal(render(undefined), '');
  assert.equal(render(null), '');
  assert.equal(render({ tables: [] }), '');
});

test('a plan with every section', () => {
  const plan = {
    ddl_statements: 2,
    tables: [
      { source: 'Assets', dest: 'assets', rows: 1200, merge_existing_assets: 30, already_present: 5,
        invalid_values: [{ column: 'install_date', count: 3, dest_type: 'date', sample: '31/02/2025' }],
        rows_cannot_write: [{ reason: 'no building for the meter', count: 2 }],
        new_columns: ['colour'], creates_table: false },
      { source: 'Meter_Readings', dest: 'meter_readings', rows: 277412, merge_existing_assets: 0, already_present: 0,
        invalid_values: [], rows_cannot_write: [], new_columns: [], creates_table: true },
    ],
  };
  const html = render(plan);
  const t = text(html);
  for (const want of ['What the write will do', 'assets', '1,200', '30', 'meter_readings', '277,412', 'new table',
                      '2 schema changes', 'install_date', 'date', '31/02/2025', 'no building for the meter', 'colour']) {
    assert.ok(t.includes(want), `missing ${JSON.stringify(want)} in: ${t}`);
  }
  // the details sit in collapsed <details>, one per destination that has any
  assert.equal((html.match(/<details/g) || []).length, 1);
  assert.ok(/<table/.test(html) && /<th[^>]*>Destination<\/th>/.test(html));
});

test('one of a kind is said in the singular', () => {
  const t = text(render({ tables: [{ source: 'A', dest: 'assets', rows: 1, merge_existing_assets: 0, already_present: 0,
    invalid_values: [{ column: 'c', count: 1, dest_type: 'date', sample: 'x' }], rows_cannot_write: [{ reason: 'r', count: 1 }],
    new_columns: ['n'] }], ddl_statements: 1 }));
  assert.ok(t.includes('1 value that does not fit'), t);
  assert.ok(t.includes('1 row that cannot be written'), t);
  assert.ok(t.includes('1 new column') && t.includes('1 schema change') && !t.includes('1 schema changes'), t);
});

