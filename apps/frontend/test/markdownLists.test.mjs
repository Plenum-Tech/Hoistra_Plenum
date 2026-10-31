// markdownLists — a numbered list keeps the answer's own numbers (src/components/shell/Markdown.jsx).
//
// 27 Oct 2026: "1. … 2. … - a detail … 3. …" was drawn as five numbered items, so the answer's
// "3." showed as 5. Sub-items sit under the item they belong to and each numbered item keeps
// the number the answer gave it.
import { test, after } from 'node:test';
import assert from 'node:assert/strict';
import { build } from 'esbuild';
import { writeFileSync, rmSync } from 'node:fs';
import { fileURLToPath, pathToFileURL } from 'node:url';
import path from 'node:path';

const here = path.dirname(fileURLToPath(import.meta.url));
const outFile = path.join(here, '.markdown-lists.bundle.mjs');
const res = await build({
  stdin: { contents: "export { default as Markdown } from '../src/components/shell/Markdown.jsx';", resolveDir: here, loader: 'js' },
  bundle: true, write: false, format: 'esm', platform: 'node', jsx: 'automatic',
  external: ['react', 'react/*', 'react-dom', 'react-dom/*'], loader: { '.css': 'empty' }, logLevel: 'silent'
});
writeFileSync(outFile, res.outputFiles[0].text);
after(() => rmSync(outFile, { force: true }));

const { Markdown } = await import(pathToFileURL(outFile).href);
const React = (await import('react')).default;
const { renderToStaticMarkup } = await import('react-dom/server');
const draw = (text) => renderToStaticMarkup(React.createElement(Markdown, { text }));
const tops = (html) => (html.match(/<li/g) || []).length;

test('sub-bullets under a numbered item stay under it, and "3." stays 3', () => {
  const html = draw('1. Chase **Apex**\n2. Review HVAC\n   - 2 were HVAC\n   - 1 was lifts\n3. **Corvane** recovery plan');
  assert.match(html, /<ol[^>]*>/);
  assert.match(html, /<li[^>]*value="3"[^>]*>.*Corvane/);
  // the two details are a list inside item 2, not items 3 and 4
  assert.match(html, /Review HVAC<ul[^>]*><li[^>]*>2 were HVAC<\/li><li[^>]*>1 was lifts<\/li><\/ul><\/li>/);
  assert.equal(tops(html), 5);
});

test('a list that starts at its own number keeps it', () => {
  const html = draw('4. Fourth\n5. Fifth');
  assert.match(html, /<ol[^>]*start="4"/);
  assert.match(html, /value="5"[^>]*>Fifth/);
});

test('a plain bullet list draws as before', () => {
  const html = draw('- one\n- two');
  assert.match(html, /<ul[^>]*><li[^>]*>one<\/li><li[^>]*>two<\/li><\/ul>/);
  assert.doesNotMatch(html, /value=/);
});
