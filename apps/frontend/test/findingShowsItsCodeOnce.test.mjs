// A finding shows its record code once (2 Nov 2026: "WO-B-301-4560 WO-B-301-4560 L20 FCUs…" and
// "FRA-B-301-2025 FRA-B-301-2025 lapsed…" - the model gave the code as the title AND as the code,
// and the card drew the title, then the code beside it).
import { test, after } from 'node:test';
import assert from 'node:assert/strict';
import { build } from 'esbuild';
import { writeFileSync, rmSync } from 'node:fs';
import { fileURLToPath, pathToFileURL } from 'node:url';
import path from 'node:path';

globalThis.window = { location: { origin: 'http://test.local' } };
const here = path.dirname(fileURLToPath(import.meta.url));
const outFile = path.join(here, '.finding-code-once.bundle.mjs');
const res = await build({
  stdin: { contents: "export { library } from '../src/openui/library.jsx';", resolveDir: here, loader: 'js' },
  bundle: true, write: false, format: 'esm', platform: 'node', jsx: 'automatic',
  external: ['react', 'react/*', 'react-dom', 'react-dom/*', '@openuidev/react-lang', 'zod'],
  loader: { '.css': 'empty' }, logLevel: 'silent'
});
writeFileSync(outFile, res.outputFiles[0].text);
after(() => rmSync(outFile, { force: true }));
const { library } = await import(pathToFileURL(outFile).href);
const React = (await import('react')).default;
const { renderToStaticMarkup } = await import('react-dom/server');
const { Renderer } = await import('@openuidev/react-lang');

const draw = (finding) => renderToStaticMarkup(React.createElement(Renderer, {
  library, response: 'root = Answer([f])\nf = ' + finding, isStreaming: false, publishObservability: false }));
const shown = (html) => html.replace(/<[^>]+>/g, ' ').replace(/\s+/g, ' ');
const times = (html, code) => shown(html).split(code).length - 1;

test('a title that is the code is not followed by the code again', () => {
  const html = draw('Finding("WO-B-301-4560", "L20 FCUs with heating/cooling conflict, due 29 Sep (£650).", "warn", "WO-B-301-4560")');
  assert.equal(times(html, 'WO-B-301-4560'), 1, shown(html));
  assert.match(shown(html), /L20 FCUs with heating\/cooling conflict/);
});

test('a title that names the code keeps it there, once', () => {
  const html = draw('Finding("FRA-B-301-2025 lapsed", "on 29 Jun 2026.", "risk", "FRA-B-301-2025")');
  assert.equal(times(html, 'FRA-B-301-2025'), 1, shown(html));
});

test('a text that opens with the code does not repeat the code beside an empty title', () => {
  const html = draw('Finding("", "FRA-B-301-2025 lapsed 29 Jun 2026.", "risk", "FRA-B-301-2025")');
  assert.equal(times(html, 'FRA-B-301-2025'), 1, shown(html));
  assert.match(shown(html), /lapsed 29 Jun 2026/);
});

test('a title and a different code both show', () => {
  const html = draw('Finding("Fire Risk Assessment", "Lapsed, expired 2026-06-29.", "risk", "FRA-B-301-2025")');
  assert.equal(times(html, 'FRA-B-301-2025'), 1);
  assert.match(shown(html), /Fire Risk Assessment/);
});
