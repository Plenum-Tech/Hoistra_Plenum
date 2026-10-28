// openuiAnswer — one OpenUI answer in a chat bubble (src/openui/OpenUIAnswer.jsx): telling a
// program from Markdown, where a click goes, and never a blank bubble.
import { test, after } from 'node:test';
import assert from 'node:assert/strict';
import { build } from 'esbuild';
import { writeFileSync, rmSync } from 'node:fs';
import { fileURLToPath, pathToFileURL } from 'node:url';
import path from 'node:path';

const here = path.dirname(fileURLToPath(import.meta.url));
const outFile = path.join(here, '.openui-answer.bundle.mjs');
const res = await build({
  stdin: {
    contents: [
      "export { default as OpenUIAnswer } from '../src/openui/OpenUIAnswer.jsx';",
      "export { isOpenUI } from '../src/openui/parse.js';",
      "export { looksLikeOpenUIStart, plainTextOf, splitProgram } from '../src/openui/detect.js';",
      "export { routeAction } from '../src/openui/actions.js';",
      "export { OpenUIHostContext } from '../src/openui/host.js';",
      "export { EXAMPLES } from '../src/openui/spec.js';"
    ].join('\n'),
    resolveDir: here, loader: 'js'
  },
  bundle: true, write: false, format: 'esm', platform: 'node', jsx: 'automatic',
  external: ['react', 'react/*', 'react-dom', 'react-dom/*', '@openuidev/react-lang', '@openuidev/lang-core', 'zod'],
  loader: { '.css': 'empty' }, logLevel: 'silent'
});
writeFileSync(outFile, res.outputFiles[0].text);
after(() => rmSync(outFile, { force: true }));

const M = await import(pathToFileURL(outFile).href);
const React = (await import('react')).default;
const { renderToStaticMarkup } = await import('react-dom/server');
const html = (props) => renderToStaticMarkup(React.createElement(M.OpenUIAnswer, props));

test('isOpenUI: a program is one, Markdown with an equals sign is not', () => {
  assert.equal(M.isOpenUI(M.EXAMPLES[0]), true);
  assert.equal(M.isOpenUI('The backlog is long.\n\nTotal = 42 overdue jobs.'), false);
  assert.equal(M.isOpenUI('Overdue = Metric(7)'), false);
  assert.equal(M.isOpenUI(''), false);
  assert.equal(M.isOpenUI(null), false);
  assert.equal(M.isOpenUI('```\nroot = Answer([h])\nh = Headline("Fenced")\n```'), true);
});

test('looksLikeOpenUIStart: the first line of a program, before the rest has streamed', () => {
  assert.equal(M.looksLikeOpenUIStart('root = Answer([h'), true);
  assert.equal(M.looksLikeOpenUIStart('\n  root = Answer([head, kpis])\nhead = Head'), true);
  assert.equal(M.looksLikeOpenUIStart('Looking at the backlog for you…'), false);
  assert.equal(M.looksLikeOpenUIStart('root cause: the chiller'), false);
  assert.equal(M.looksLikeOpenUIStart(''), false);
});

// The draft is every delta of the turn, so a sentence the model wrote before a tool call runs
// straight into the program, with no line break between (found in review, 26 Oct 2026).
const PREAMBLE = "I'll pull the overdue work orders.";
const SHORT = 'root = Answer([h])\nh = Headline("Seven are overdue")';

test('a program is found wherever it starts, even run on from an earlier sentence', () => {
  assert.equal(M.looksLikeOpenUIStart(PREAMBLE + SHORT), true);
  assert.deepEqual(M.splitProgram(PREAMBLE + SHORT), { lead: PREAMBLE, program: SHORT });
  assert.deepEqual(M.splitProgram(SHORT), { lead: '', program: SHORT });
  assert.deepEqual(M.splitProgram('Prose only.'), { lead: 'Prose only.', program: '' });
});

test('fence lines around a program are not part of it, nor of its lead', () => {
  assert.deepEqual(M.splitProgram('Here it is:\n```openui\n' + SHORT + '\n```'), { lead: 'Here it is:', program: SHORT });
});

test('a lead sentence is drawn above the program, and no code is ever shown', () => {
  const done = html({ text: PREAMBLE + SHORT });
  assert.match(done, /I&#x27;ll pull the overdue work orders\./);
  assert.match(done, /Seven are overdue/);
  assert.doesNotMatch(done, /root =/);
  const live = html({ text: PREAMBLE + 'root = Answer([h])\nh = Headline("Seven are', streaming: true });
  assert.doesNotMatch(live, /root =/);
});

test('plainTextOf: the words of a program, never its code', () => {
  const t = M.plainTextOf('root = Answer([h, m])\nh = Headline("Seven are \\"late\\".")\nm = Metric("Overdue", 7, null, "risk")');
  assert.match(t, /Seven are "late"\./);
  assert.match(t, /Overdue/);
  assert.doesNotMatch(t, /root =|Answer\(|risk/);
});

function host() {
  const calls = [];
  return { calls, ask: (q) => calls.push(['ask', q]), open: (t) => calls.push(['open', t]) };
}

test('routeAction: a follow-up asks, an app page opens in the app, a web page opens', () => {
  let h = host();
  assert.equal(M.routeAction({ type: 'continue_conversation', humanFriendlyMessage: 'Show the backlog', params: {} }, h), 'ask');
  assert.deepEqual(h.calls, [['ask', 'Show the backlog']]);
  h = host();
  assert.equal(M.routeAction({ type: 'open_url', params: { url: '/compliance' } }, h), 'open');
  assert.deepEqual(h.calls, [['open', { page: 'compliance' }]]);
  h = host();
  assert.equal(M.routeAction({ type: 'open_url', params: { url: '/maintenance' } }, h), 'open');
  assert.deepEqual(h.calls, [['open', { page: 'ops' }]]);
  h = host();
  assert.equal(M.routeAction({ type: 'open_url', params: { url: 'https://www.gov.uk/gas-safety' } }, h), 'open');
  assert.deepEqual(h.calls, [['open', { url: 'https://www.gov.uk/gas-safety' }]]);
});

test('routeAction: a follow-up\'s context is asked with it, never dropped', () => {
  // @ToAssistant("Show details", {wo: "WO-1184"}): asking "Show details" alone would answer
  // about the wrong record or ask which one (review, 26 Oct 2026).
  const h = host();
  M.routeAction({ type: 'continue_conversation', humanFriendlyMessage: 'Show details', params: { context: { wo: 'WO-1184', site: 'B-101' } } }, h);
  assert.deepEqual(h.calls, [['ask', 'Show details (wo: WO-1184, site: B-101)']]);
  const s = host();
  M.routeAction({ type: 'continue_conversation', humanFriendlyMessage: 'Compare them', params: { context: 'B-101 and B-102' } }, s);
  assert.deepEqual(s.calls, [['ask', 'Compare them (B-101 and B-102)']]);
});

test('routeAction: unsafe or empty targets do nothing', () => {
  for (const url of ['javascript:alert(1)', '//evil.example/x', 'data:text/html,hi', '', '/admin/secret', 'vbscript:x']) {
    const h = host();
    assert.equal(M.routeAction({ type: 'open_url', params: { url } }, h), 'ignored', url);
    assert.deepEqual(h.calls, []);
  }
  const h = host();
  assert.equal(M.routeAction({ type: 'continue_conversation', humanFriendlyMessage: '  ' }, h), 'ignored');
  assert.equal(M.routeAction({ type: 'something_else' }, h), 'ignored');
  assert.equal(M.routeAction(null, h), 'ignored');
  assert.deepEqual(h.calls, []);
});

test('a finished answer renders its program', () => {
  const out = html({ text: M.EXAMPLES[0] });
  assert.match(out, /class="oui"/);
  assert.match(out, /oui-fig/);
});

test('a program that renders nothing falls back to its words, never a blank bubble', () => {
  const out = html({ text: 'root = Answer([h])\nh = Headline(42)\nt = Text("Some words survive")' });
  assert.match(out, /Some words survive/);
  assert.doesNotMatch(out, /root =/);
});

test('the tail a later step added is drawn under the view', () => {
  const out = html({ text: M.EXAMPLES[1], tail: '_Check the figures before quoting them._' });
  assert.match(out, /Which building do you mean\?/);
  assert.match(out, /Check the figures before quoting them\./);
  // A note a later check added (or words the chat could not draw) reads as a caution, not as
  // muted small print (review, 26 Oct 2026).
  assert.match(out, /class="oui-tail" role="note"><i class="ph ph-warning"/);
});

test('while streaming, controls are disabled and a partial program is not replaced by the fallback', () => {
  const out = html({ text: 'root = Answer([h, f])\nh = Headline("Working', streaming: true });
  assert.doesNotMatch(out, /root =/);
  const live = html({ text: M.EXAMPLES[1], streaming: true });
  assert.match(live, /<button[^>]*disabled=""/);
});
