// openuiInteractiveMore — the step arrow, the metric tile's ask, record codes and the email draft's
// way into the app's own email card (src/openui/ask.jsx, components/Data.jsx, components/Actions.jsx).
//
// 28 Oct 2026, Hussain: "i need the what to do arrow … draft a mail should work and more clickable
// option and interactive". Every control is a real <button> whose name is the question it asks, built
// from the answer's own words; each is disabled while its answer is still being written. Edit in
// Hoistra only opens the chat's email card (host.email): it sends nothing.
//
// Drawn through OpenUI's own <Renderer> with react-dom/server. A server render keeps no click
// handlers, so the JSX runtime is wrapped (jsxSpy, as in openuiInteractive) to keep every element's
// onClick; calling one runs the real handler against the real Renderer.
import { test, after, beforeEach } from 'node:test';
import assert from 'node:assert/strict';
import { build } from 'esbuild';
import { writeFileSync, rmSync } from 'node:fs';
import { fileURLToPath, pathToFileURL } from 'node:url';
import path from 'node:path';

const here = path.dirname(fileURLToPath(import.meta.url));
const outFile = path.join(here, '.openui-interactive-more.bundle.mjs');
const jsxSpy = {
  name: 'jsx-spy',
  setup(b) {
    b.onResolve({ filter: /^react\/jsx-runtime$/ }, (a) => (a.namespace === 'jsx-spy'
      ? { path: 'react/jsx-runtime', external: true }
      : { path: 'jsx-spy', namespace: 'jsx-spy' }));
    b.onLoad({ filter: /.*/, namespace: 'jsx-spy' }, () => ({
      loader: 'js',
      contents: [
        "import * as rt from 'react/jsx-runtime';",
        'export const Fragment = rt.Fragment;',
        'const seen = () => (globalThis.__ouiClicks = globalThis.__ouiClicks || []);',
        "const wrap = (f) => (type, props, key) => { if (typeof type === 'string' && props && typeof props.onClick === 'function') seen().push({ type, props }); return f(type, props, key); };",
        'export const jsx = wrap(rt.jsx);',
        'export const jsxs = wrap(rt.jsxs);'
      ].join('\n')
    }));
  }
};
const res = await build({
  stdin: {
    contents: [
      "export { library } from '../src/openui/library.jsx';",
      "export { OpenUIHostContext } from '../src/openui/host.js';",
      "export { Metric } from '../src/openui/components/Data.jsx';",
      "export { CodeTag, PlainCodes } from '../src/openui/ask.jsx';"
    ].join('\n'),
    resolveDir: here, loader: 'js'
  },
  bundle: true, write: false, format: 'esm', platform: 'node', jsx: 'automatic',
  external: ['react', 'react/*', 'react-dom', 'react-dom/*', '@openuidev/react-lang', 'zod'],
  plugins: [jsxSpy], loader: { '.css': 'empty' }, logLevel: 'silent'
});
writeFileSync(outFile, res.outputFiles[0].text);
after(() => rmSync(outFile, { force: true }));

const { library, OpenUIHostContext, Metric, CodeTag, PlainCodes } = await import(pathToFileURL(outFile).href);
const React = (await import('react')).default;
const { renderToStaticMarkup } = await import('react-dom/server');
const { Renderer } = await import('@openuidev/react-lang');

const prog = (...lines) => lines.join('\n');
const count = (html, re) => (html.match(re) || []).length;
const textOf = (html) => html.replace(/<[^>]+>/g, ' ').replace(/&amp;/g, '&').replace(/&#x27;/g, "'").replace(/&quot;/g, '"')
  .replace(/\s+/g, ' ').trim();

// Draws a program, inside a chat host when one is given; returns its markup, the actions it fired
// and its recorded click handlers.
function draw(program, { streaming = false, host = null } = {}) {
  globalThis.__ouiClicks = [];
  const fired = [];
  const renderer = React.createElement(Renderer, {
    library, response: program, isStreaming: streaming, publishObservability: false, onAction: (e) => fired.push(e)
  });
  const html = renderToStaticMarkup(host ? React.createElement(OpenUIHostContext.Provider, { value: host }, renderer) : renderer);
  const clicks = globalThis.__ouiClicks.slice();
  const byName = (name) => clicks.find((c) => c.props['aria-label'] === name);
  return { html, fired, clicks, byName };
}
const onRow = { target: { closest: () => null } };
const asked = (fired) => fired.map((e) => (e.type === 'continue_conversation' ? e.humanFriendlyMessage : e.type));

beforeEach(() => { globalThis.__ouiClicks = []; });

test('a step without an action gets one arrow, named with its question, disabled while streaming', () => {
  const p = prog('root = Answer([s])', 's = Steps([a, b])', 'a = Step("Chase Apex Lifts for the **LOLER** report.", "Due Friday.")',
    'b = Step("Book the inspection", "", x)', 'x = Button("Draft the booking email", @ToAssistant("Draft it"))');
  const q = 'Walk me through this step: Chase Apex Lifts for the LOLER report';
  const done = draw(p);
  assert.equal(count(done.html, /class="oui-btn oui-btn-icon"/g), 1, 'one arrow: the labelled button keeps its own control');
  assert.match(done.html, new RegExp('<button type="button" class="oui-btn oui-btn-icon" aria-label="' + q + '" title="' + q + '">'));
  done.byName(q).props.onClick();
  assert.deepEqual(asked(done.fired), [q]);
  const live = draw(p, { streaming: true });
  assert.match(live.html, new RegExp('aria-label="' + q + '" title="' + q + '" disabled=""|disabled="" aria-label="' + q + '"'));
  live.byName(q).props.onClick();
  live.clicks.find((c) => c.type === 'li').props.onClick(onRow);
  assert.deepEqual(live.fired, []);
});

test('a metric tile asks about its label and figure, from its arrow and from the tile', () => {
  const p = prog('root = Answer([m])', 'm = Metrics([a, b])', 'a = Metric("Overdue work orders", "12", "+3 on last week", "risk")', 'b = Metric("SLA met", "91%")');
  const { html, fired, byName, clicks } = draw(p);
  assert.match(html, /<button type="button" class="oui-ask oui-metric-go" aria-label="Tell me more about Overdue work orders: 12" title="Tell me more about Overdue work orders: 12">/);
  byName('Tell me more about SLA met: 91%').props.onClick();
  clicks.find((c) => c.type === 'div' && /oui-metric oui-tone-risk/.test(c.props.className)).props.onClick(onRow);
  assert.deepEqual(asked(fired), ['Tell me more about SLA met: 91%', 'Tell me more about Overdue work orders: 12']);
  // The tile keeps its class and its words; the arrow adds none.
  assert.match(html, /<div class="oui-metric oui-tone-risk"><div class="oui-metric-value"/);
  assert.equal(textOf(html), '12 Overdue work orders +3 on last week 91% SLA met');
});

test('a metric tile asks nothing while streaming, and has no ask outside an answer', () => {
  const live = draw(prog('root = Answer([m])', 'm = Metrics([a])', 'a = Metric("Overdue work orders", "12")'), { streaming: true });
  assert.equal(count(live.html, /<button[^>]*disabled=""/g), 1);
  for (const c of live.clicks) c.props.onClick(c.type === 'button' ? undefined : onRow);
  assert.deepEqual(live.fired, []);
  const alone = renderToStaticMarkup(React.createElement(Metric, { props: { label: 'Overdue work orders', value: '12' } }));
  assert.doesNotMatch(alone, /<button|oui-metric-go/);
});

test('a record code is a button named "Ask about <code>"; inside a control that is the action it is plain', () => {
  const { html, fired, byName } = draw(prog('root = Answer([t])', 't = Text("Lift 4 (B-301-LIFT-4471) is overdue.")'));
  assert.match(html, /\(<button type="button" class="oui-ref oui-code-ask" aria-label="Ask about B-301-LIFT-4471" title="Ask about B-301-LIFT-4471">B-301-LIFT-4471<\/button>\)/);
  byName('Ask about B-301-LIFT-4471').props.onClick();
  assert.deepEqual(asked(fired), ['Tell me about B-301-LIFT-4471']);
  // A bar's label is already the bar's action: its code stays plain text.
  const bars = draw(prog('root = Answer([c])', 'c = BarChart("Spend", ["AHU-3 (B-301-AHU-03)"], ["£74,092"])'));
  assert.match(bars.html, /<span class="oui-ref">B-301-AHU-03<\/span>/);
  assert.doesNotMatch(bars.html, /oui-code-ask/);
  // Outside an answer, and inside PlainCodes, the same plain span.
  assert.equal(renderToStaticMarkup(React.createElement(CodeTag, { code: 'WO-1184' })), '<span class="oui-ref">WO-1184</span>');
  assert.equal(renderToStaticMarkup(React.createElement(PlainCodes.Provider, { value: true }, React.createElement(CodeTag, { code: 'WO-1184' }))),
    '<span class="oui-ref">WO-1184</span>');
});

const EMAIL = prog('root = Answer([e])',
  'e = EmailDraft("Gas safety inspection — Block B", "Apex Lifts <ops@apex.example>", "Hello,\\nPlease book the visit.", "fm@hoist.example")');

test('Edit in Hoistra shows only when the chat has an email card, first, and passes the fields as shown', () => {
  const sent = [];
  const host = { ask() {}, open() {}, email: (d) => sent.push(d) };
  const { html, clicks } = draw(EMAIL, { host });
  // First control; Copy and Open in email unchanged, Open in email still the primary.
  assert.match(html, /<div class="oui-email-actions"><button type="button" class="oui-btn" title="Open this draft in Hoistra&#x27;s email card to edit, then approve to send"><i class="ph ph-pencil-simple" aria-hidden="true"><\/i>Edit in Hoistra<\/button><button type="button" class="oui-btn">Copy<\/button><a class="oui-btn is-primary" href="mailto:/);
  clicks.find((c) => c.type === 'button').props.onClick();
  assert.deepEqual(sent, [{ to: 'Apex Lifts <ops@apex.example>', cc: 'fm@hoist.example', subject: 'Gas safety inspection — Block B', body: 'Hello,\nPlease book the visit.' }]);
});

test('Edit in Hoistra is absent with the default host and with a host that has no email', () => {
  for (const host of [null, { ask() {}, open() {} }, { ask() {}, open() {}, email: 'yes' }]) {
    const { html } = draw(EMAIL, { host });
    assert.doesNotMatch(html, /Edit in Hoistra|ph-pencil-simple/);
    assert.match(html, /<div class="oui-email-actions"><button type="button" class="oui-btn">Copy<\/button>/);
  }
});

test('Edit in Hoistra is disabled while the draft is still being written, and opens nothing', () => {
  const sent = [];
  const { html, clicks } = draw(EMAIL, { streaming: true, host: { ask() {}, open() {}, email: (d) => sent.push(d) } });
  assert.match(html, /<button type="button" class="oui-btn" disabled="" title="Open this draft[^"]*"><i class="ph ph-pencil-simple"/);
  clicks.find((c) => c.type === 'button').props.onClick();
  assert.deepEqual(sent, []);
});

test('follow-up chips, step arrows and tile arrows add no word to the answer', () => {
  const p = prog('root = Answer([m, s, f])', 'm = Metrics([a])', 'a = Metric("Overdue", "4")', 's = Steps([b])', 'b = Step("Chase the vendor")',
    'f = FollowUps(["Which vendor is slowest?"])');
  const { html } = draw(p);
  assert.equal(textOf(html), '4 Overdue 1 Chase the vendor Which vendor is slowest?');
});
