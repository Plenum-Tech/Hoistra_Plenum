// openuiInteractive — every piece of a finished OpenUI answer that leads somewhere (src/openui/ask.jsx).
//
// 28 Oct 2026, Hussain: "i need the what to do arrow … more clickable option and interactive". A step
// the model gave no button drew no arrow, and record codes, table rows, bars and findings were plain
// text. Each is now a real <button> that asks a question built from the answer's own words, through
// OpenUI's own action runner, and is disabled while its answer is still being written.
//
// The library is drawn through OpenUI's own <Renderer> with react-dom/server, as openuiRender does.
// A server render keeps no click handlers, so the JSX runtime the components are built with is
// wrapped (jsxSpy below) to keep every element's onClick; calling one runs the real handler against
// the real Renderer, whose onAction receives exactly what the chat would.
import { test, after, beforeEach } from 'node:test';
import assert from 'node:assert/strict';
import { build } from 'esbuild';
import { writeFileSync, rmSync } from 'node:fs';
import { fileURLToPath, pathToFileURL } from 'node:url';
import path from 'node:path';

const here = path.dirname(fileURLToPath(import.meta.url));
const outFile = path.join(here, '.openui-interactive.bundle.mjs');
// Every element built with an onClick is recorded in globalThis.__ouiClicks, in render order.
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
      "export { routeAction } from '../src/openui/actions.js';",
      "export { aboutQuestion, barQuestion, codeQuestion, findingQuestion, plainOf, rowClick, stepQuestion } from '../src/openui/ask.jsx';",
      "export { Finding } from '../src/openui/components/Finding.jsx';"
    ].join('\n'),
    resolveDir: here, loader: 'js'
  },
  bundle: true, write: false, format: 'esm', platform: 'node', jsx: 'automatic',
  external: ['react', 'react/*', 'react-dom', 'react-dom/*', '@openuidev/react-lang', 'zod'],
  plugins: [jsxSpy], loader: { '.css': 'empty' }, logLevel: 'silent'
});
writeFileSync(outFile, res.outputFiles[0].text);
after(() => rmSync(outFile, { force: true }));

const { library, routeAction, aboutQuestion, barQuestion, codeQuestion, findingQuestion, plainOf, rowClick, stepQuestion, Finding } =
  await import(pathToFileURL(outFile).href);
const React = (await import('react')).default;
const { renderToStaticMarkup } = await import('react-dom/server');
const { Renderer } = await import('@openuidev/react-lang');

const prog = (...lines) => lines.join('\n');
const count = (html, re) => (html.match(re) || []).length;
// What a reader sees, in order: the markup's text with the tags taken out.
const textOf = (html) => html.replace(/<[^>]+>/g, ' ').replace(/&amp;/g, '&').replace(/&#x27;/g, "'").replace(/&quot;/g, '"')
  .replace(/\s+/g, ' ').trim();

// Draws a program; returns its markup, the actions it fired and its recorded click handlers.
function draw(program, streaming = false) {
  globalThis.__ouiClicks = [];
  const fired = [];
  const html = renderToStaticMarkup(React.createElement(Renderer, {
    library, response: program, isStreaming: streaming, publishObservability: false, onAction: (e) => fired.push(e)
  }));
  const clicks = globalThis.__ouiClicks.slice();
  const byName = (name) => clicks.find((c) => c.props['aria-label'] === name);
  const byClass = (cls) => clicks.filter((c) => String(c.props.className || '').split(' ').includes(cls));
  return { html, fired, clicks, byName, byClass };
}
// A click on the row itself, not on a control inside it, with nothing selected.
const onRow = { target: { closest: () => null } };
const asked = (fired) => fired.map((e) => (e.type === 'continue_conversation' ? e.humanFriendlyMessage : e.type));

beforeEach(() => { globalThis.__ouiClicks = []; });

const STEPS = prog('root = Answer([s])', 's = Steps([a, b])',
  'a = Step("Book the inspection", "Expires Friday.", x)', 'x = Button("Draft the email", @ToAssistant("Draft it"), "primary")',
  'b = Step("Commission a new **Fire Risk Assessment**", "FRA-B-301-2025 lapsed on 2026-06-29.")');

test('a step without a button of its own gets an arrow that asks how to carry it out', () => {
  const { html, fired, byName } = draw(STEPS);
  const q = 'Walk me through this step: Commission a new Fire Risk Assessment';
  // A real button on the step's row, named with the question it asks; the step's words unchanged.
  assert.match(html, new RegExp('<li class="oui-step is-ask">[\\s\\S]*?<button type="button" class="oui-btn oui-btn-icon" aria-label="' + q + '" title="' + q + '"><i class="ph ph-arrow-right" aria-hidden="true"></i></button>'));
  assert.match(textOf(html), /Commission a new Fire Risk Assessment FRA-B-301-2025 lapsed on 2026-06-29\./);
  // The step with a labelled button keeps it as its one control, and its row is not the arrow's.
  assert.match(html, /<li class="oui-step"><span class="oui-step-n"[^>]*>1<\/span>[\s\S]*?Draft the email/);
  byName(q).props.onClick();
  assert.deepEqual(asked(fired), [q]);
});

test('a click anywhere on an arrow row asks what its arrow asks; a control or a selection inside it does not', () => {
  const { fired, clicks } = draw(STEPS);
  const row = clicks.find((c) => c.type === 'li');
  assert.ok(row, 'the arrow row takes a click');
  row.props.onClick(onRow);
  assert.deepEqual(asked(fired), ['Walk me through this step: Commission a new Fire Risk Assessment']);
  row.props.onClick({ target: { closest: (sel) => (/button/.test(sel) ? {} : null) } });
  assert.equal(fired.length, 1, 'a click on a code or a link in the row is that control\'s own');
  // Exactly one row is clickable: the one whose only control is its arrow.
  assert.equal(clicks.filter((c) => c.type === 'li').length, 1);
});

test('an echoed step button still fires the model\'s own action, from the arrow and from its row', () => {
  const { fired, clicks } = draw(prog('root = Answer([s])', 's = Steps([a])',
    'a = Step("Reassess the FRA now", "Close the riser remedial.", x)', 'x = Button("Reassess the FRA now", @ToAssistant("Book the FRA reassessment"))'));
  clicks.find((c) => c.type === 'li').props.onClick(onRow);
  clicks.find((c) => c.type === 'button').props.onClick();
  assert.deepEqual(asked(fired), ['Book the FRA reassessment', 'Book the FRA reassessment']);
});

test('while the answer streams, every new control is disabled and no click asks anything', () => {
  const p = prog('root = Answer([s, t, c, f])', 's = Steps([a])', 'a = Step("Chase the vendor")',
    't = Table(["Vendor", "Certificate"], [["Apex Lifts", "LOLER-B-301-4471"]])',
    'c = BarChart("Overdue by trade", ["HVAC", "Electrical"], [3, 2], "jobs")',
    'f = Finding("Fire Risk Assessment", "Lapsed.", "risk", "FRA-B-301-2025")');
  const live = draw(p, true);
  // step arrow, table row arrow, code in the table, two bar arrows, finding code, finding arrow
  assert.equal(count(live.html, /<button[^>]*disabled=""/g), 7);
  for (const c of live.clicks) c.props.onClick(c.type === 'button' ? undefined : onRow);
  assert.deepEqual(live.fired, []);
  const done = draw(p, false);
  assert.equal(count(done.html, /<button[^>]*disabled=""/g), 0);
});

test('a record code is a tag that asks about its record, its characters unchanged', () => {
  const { html, fired, byName } = draw(prog('root = Answer([s])', 's = Section("Lifts", [t])', 't = Text("Booked under WO-1184 for 4 Nov.")'));
  assert.match(html, /Booked under <button type="button" class="oui-ref oui-code-ask" aria-label="Ask about WO-1184" title="Ask about WO-1184">WO-1184<\/button> for 4 Nov\./);
  byName('Ask about WO-1184').props.onClick();
  assert.deepEqual(asked(fired), ['Tell me about WO-1184']);
});

test('a table row asks about its item; its arrow sits in a last column of its own', () => {
  const { html, fired, byName, clicks } = draw(prog('root = Answer([t])',
    't = Table(["Vendor", "Certificate", "SLA"], [["Apex Lifts", "LOLER-B-301-4471", "91%"], ["Kestrel (KC-22)", "GAS-7", "85%"]])'));
  // The rows keep their markup; the heading row gains an empty cell, never a heading.
  assert.equal(count(html, /<tr class="oui-row">/g), 2);
  assert.equal(count(html, /<th[ >]/g), 3);
  assert.match(html, /<td class="oui-go-col" aria-hidden="true"><\/td><\/tr><\/thead>/);
  assert.match(html, /<td class="oui-go-col"><button type="button" class="oui-ask" aria-label="Tell me more about Apex Lifts"/);
  // The item's own code is part of its name (plain); a code elsewhere on the row asks on its own.
  assert.match(html, /<td>Kestrel <span class="oui-ref-group">\(<span class="oui-ref">KC-22<\/span>\)<\/span><\/td>/);
  assert.match(html, /<button type="button" class="oui-ref oui-code-ask"[^>]*>LOLER-B-301-4471<\/button>/);
  byName('Tell me more about Apex Lifts').props.onClick();
  clicks.filter((c) => c.type === 'tr')[1].props.onClick(onRow);
  assert.deepEqual(asked(fired), ['Tell me more about Apex Lifts', 'Tell me more about Kestrel (KC-22)']);
});

test('a table whose first column is figures has no row asks, and its markup is as it was', () => {
  const { html, clicks } = draw(prog('root = Answer([t])', 't = Table(["Year", "EUI"], [[2024, 230], [2025, 225]])'));
  assert.doesNotMatch(html, /oui-go-col|oui-ask/);
  assert.equal(clicks.filter((c) => c.type === 'tr').length, 0);
});

test('a bar asks about its item and the figure the chart shows for it', () => {
  const { html, fired, byName, clicks } = draw(prog('root = Answer([c])', 'c = BarChart("Overdue by trade", ["HVAC", "Electrical"], [3, 1], "jobs")'));
  assert.equal(count(html, /class="oui-bar-row is-ask"/g), 2);
  assert.equal(count(html, /class="oui-bar-fill/g), 2);
  assert.match(html, /<button type="button" class="oui-ask oui-bar-go" aria-label="Tell me more about HVAC: 3 jobs"/);
  byName('Tell me more about Electrical: 1 job').props.onClick();
  clicks.find((c) => c.type === 'span' && /oui-bar-row/.test(c.props.className)).props.onClick(onRow);
  assert.deepEqual(asked(fired), ['Tell me more about Electrical: 1 job', 'Tell me more about HVAC: 3 jobs']);
  // The chart's title is said once: never repeated in every bar's name.
  assert.equal(count(html, /Overdue by trade/g), 1);
});

test('a Finding asks what to do about it, from its arrow and from its card', () => {
  const { html, fired, byName, clicks } = draw(prog('root = Answer([f])',
    'f = Finding("Fire Risk Assessment", "Lapsed, expired 2026-06-29.", "risk", "FRA-B-301-2025")'));
  const q = 'What should I do about Fire Risk Assessment (FRA-B-301-2025)?';
  assert.match(html, new RegExp('<button type="button" class="oui-ask oui-card-go" aria-label="' + q.replace(/[()?]/g, '\\$&') + '"'));
  byName(q).props.onClick();
  clicks.find((c) => c.type === 'article').props.onClick(onRow);
  byName('Ask about FRA-B-301-2025').props.onClick();
  assert.deepEqual(asked(fired), [q, q, 'Tell me about FRA-B-301-2025']);
});

test('a finding paragraph asks about its lead; the brief\'s paragraphs do not', () => {
  const body = '**Fire Risk Assessment — Lapsed**, expired 2026-06-29.\\n\\nThe building has no FRA.\\n\\n**Lift 4 — LOLER past date.** Book it.';
  const { html, fired, byName } = draw(prog('root = Answer([h, s])', 'h = Headline("Two gaps.")', 's = Section("Risk to life", [t])', `t = Text("${body}")`));
  assert.equal(count(html, /class="oui-ask oui-card-go"/g), 2);
  // The words read exactly as before, in the same order; the arrow comes after them.
  // (textOf puts a space where a tag was: "Lapsed , expired" is "Lapsed</strong>, expired".)
  assert.equal(textOf(html.slice(html.indexOf('oui-section-body'))).replace(/^[^>]*> ?/, '').replace(/ ([,.])/g, '$1'),
    'Fire Risk Assessment — Lapsed, expired 2026-06-29. The building has no FRA. Lift 4 — LOLER past date. Book it.');
  assert.match(html, /<div class="md-p" data-lead=""[^>]*><strong[^>]*>Fire Risk Assessment — Lapsed<\/strong>, expired 2026-06-29\.<button type="button" class="oui-ask oui-card-go"/);
  byName('What should I do about Lift 4 — LOLER past date?').props.onClick();
  assert.deepEqual(asked(fired), ['What should I do about Lift 4 — LOLER past date?']);
  // In the brief the same paragraph is a summary: no card, no arrow.
  const brief = draw(prog('root = Answer([h, t, s])', 'h = Headline("Two gaps.")', `t = Text("${body}")`, 's = Section("More", [u])', 'u = Text("Detail.")'));
  assert.doesNotMatch(brief.html, /oui-card-go/);
});

test('no control adds a word to the answer', () => {
  const p = prog('root = Answer([s, t, c, f])', 's = Steps([a])', 'a = Step("Chase the vendor")',
    't = Table(["Vendor", "Certificate"], [["Apex Lifts", "LOLER-B-301-4471"]])',
    'c = BarChart("Overdue by trade", ["HVAC"], [3], "jobs")',
    'f = Finding("Fire Risk Assessment", "Lapsed.", "risk", "FRA-B-301-2025")');
  assert.equal(textOf(draw(p).html), textOf(draw(p).html.replace(/<button[^>]*class="oui-(?:ask|btn oui-btn-icon)[^"]*"[^>]*>[\s\S]*?<\/button>/g, '')));
  // "1" is the step's number (aria-hidden, as before).
  assert.equal(textOf(draw(p).html), '1 Chase the vendor Vendor Certificate Apex Lifts LOLER-B-301-4471 Overdue by trade HVAC 3 jobs Fire Risk Assessment FRA-B-301-2025 Lapsed.');
});

test('a Finding drawn outside an answer has no actions and its code is the plain tag', () => {
  const html = renderToStaticMarkup(React.createElement(Finding, { props: { title: 'Lift 4', text: 'Due.', code: 'B-101' } }));
  assert.match(html, /<span class="oui-ref">B-101<\/span>/);
  assert.doesNotMatch(html, /<button/);
});

test('the questions are the answer\'s own words: marks dropped, internal names scrubbed, trailing punctuation off', () => {
  assert.equal(stepQuestion('**Renew** FGAS-B-301-CH101.'), 'Walk me through this step: Renew FGAS-B-301-CH101');
  assert.equal(stepQuestion('  '), '');
  assert.equal(aboutQuestion('[Apex Lifts](https://example.com)'), 'Tell me more about Apex Lifts');
  assert.equal(codeQuestion('WO-1184'), 'Tell me about WO-1184');
  assert.equal(barQuestion('AHU-3 (B-301-AHU-03)', '£74,092'), 'Tell me more about AHU-3 (B-301-AHU-03): £74,092');
  assert.equal(barQuestion('Boiler 3', '—'), 'Tell me more about Boiler 3');
  assert.equal(findingQuestion('Fire Risk Assessment — Lapsed:', ''), 'What should I do about Fire Risk Assessment — Lapsed?');
  assert.equal(findingQuestion('', 'GEN-1'), 'What should I do about GEN-1?');
  assert.equal(findingQuestion('', ''), '');
  assert.doesNotMatch(plainOf('Read from UDR'), /\bUDR\b/);
});

test('rowClick ignores a click on a control inside the row and a click that ends a text selection', () => {
  let n = 0;
  const run = rowClick(() => { n += 1; });
  run(onRow);
  run({ target: { closest: () => ({ tagName: 'A' }) } });
  globalThis.window = { getSelection: () => ({ toString: () => '£114,716' }) };
  try { run(onRow); } finally { delete globalThis.window; }
  assert.equal(n, 1);
});

test('every question reaches the chat as a question asked in this conversation', () => {
  const { fired, byName } = draw(STEPS);
  byName('Walk me through this step: Commission a new Fire Risk Assessment').props.onClick();
  const said = [];
  assert.equal(routeAction(fired[0], { ask: (q) => said.push(q), open() {} }), 'ask');
  assert.deepEqual(said, ['Walk me through this step: Commission a new Fire Risk Assessment']);
});

test('Copy copies the draft even where the clipboard API is missing or refused', async () => {
  const p = prog('root = Answer([e])', 'e = EmailDraft("Gas safety inspection", "ops@example.com", "Hello,\\nPlease book.")');
  const copied = [];
  const box = () => ({ value: '', style: {}, setAttribute() {}, select() { copied.push(this.value); } });
  globalThis.document = { createElement: box, body: { appendChild() {}, removeChild() {} }, execCommand: (c) => c === 'copy' };
  const nav = Object.getOwnPropertyDescriptor(globalThis, 'navigator');
  try {
    const copy = draw(p).clicks.find((c) => c.type === 'button');
    // No clipboard API at all.
    Object.defineProperty(globalThis, 'navigator', { value: {}, configurable: true, writable: true });
    copy.props.onClick();
    // A clipboard that refuses.
    Object.defineProperty(globalThis, 'navigator', { value: { clipboard: { writeText: () => Promise.reject(new Error('denied')) } }, configurable: true, writable: true });
    copy.props.onClick();
    await new Promise((r) => setTimeout(r, 0));
  } finally {
    delete globalThis.document;
    if (nav) Object.defineProperty(globalThis, 'navigator', nav);
  }
  const full = 'Subject: Gas safety inspection\nTo: ops@example.com\n\nHello,\nPlease book.';
  assert.deepEqual(copied, [full, full]);
});
