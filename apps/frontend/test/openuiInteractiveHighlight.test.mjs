// openuiInteractiveHighlight — what an OpenUI answer marks, and that marking never changes a word.
//
// 28 Oct 2026, Hussain: "the way u highlight stuff make it better currently its shabby". Every bold
// went under a yellow band with a darker line along its foot, so a long step title read as one
// smear, and every record code sat in a grey box. Now only a key figure (bold words with a digit or
// a currency sign, 28 characters or fewer: shared.jsx isFigureText) gets the marker chip; a bold
// phrase is heavier type and nothing more (.oui-key); a code is ink text with a faint underline.
//
// The library is drawn through OpenUI's own <Renderer> with react-dom/server, as openuiRender does.
// The same bundle can be switched to the marking as it was before (every bold in a headline marked,
// globalThis.__ouiAllFigures), so the words of each answer are compared with what they were.
import { test, after } from 'node:test';
import assert from 'node:assert/strict';
import { build } from 'esbuild';
import { readFileSync, writeFileSync, rmSync } from 'node:fs';
import { fileURLToPath, pathToFileURL } from 'node:url';
import path from 'node:path';

const here = path.dirname(fileURLToPath(import.meta.url));
const outFile = path.join(here, '.openui-highlight.bundle.mjs');
// The marking as it was: every bold in a headline, step title or callout is a figure.
const asBefore = {
  name: 'as-before',
  setup(b) {
    b.onLoad({ filter: /src[\\/]openui[\\/]shared\.jsx$/ }, (a) => ({
      loader: 'jsx',
      contents: readFileSync(a.path, 'utf8').replace('export function isFigureText(s) {',
        'export function isFigureText(s) {\n  if (globalThis.__ouiAllFigures) return true;')
    }));
  }
};
const res = await build({
  stdin: {
    contents: [
      "export { library } from '../src/openui/library.jsx';",
      "export { EXAMPLES } from '../src/openui/spec.js';",
      "export { isFigureText, marked, refs } from '../src/openui/shared.jsx';"
    ].join('\n'),
    resolveDir: here, loader: 'js'
  },
  bundle: true, write: false, format: 'esm', platform: 'node', jsx: 'automatic',
  external: ['react', 'react/*', 'react-dom', 'react-dom/*', '@openuidev/react-lang', 'zod'],
  plugins: [asBefore], loader: { '.css': 'empty' }, logLevel: 'silent'
});
writeFileSync(outFile, res.outputFiles[0].text);
after(() => rmSync(outFile, { force: true }));

const { library, EXAMPLES, isFigureText } = await import(pathToFileURL(outFile).href);
const React = (await import('react')).default;
const { renderToStaticMarkup } = await import('react-dom/server');
const { Renderer } = await import('@openuidev/react-lang');

const prog = (...lines) => lines.join('\n');
const draw = (program) => renderToStaticMarkup(React.createElement(Renderer, {
  library, response: program, isStreaming: false, publishObservability: false
}));
function drawBefore(program) {
  globalThis.__ouiAllFigures = true;
  try { return draw(program); } finally { delete globalThis.__ouiAllFigures; }
}
// What a reader sees, in order: the markup's text with the tags taken out.
const textOf = (html) => html.replace(/<[^>]+>/g, '').replace(/&amp;/g, '&').replace(/&#x27;/g, "'").replace(/&quot;/g, '"')
  .replace(/\s+/g, ' ').trim();
// The markup with what marks a bold taken out: the element's name and its marking class.
const unmarked = (html) => html
  .replace(/<mark class="oui-fig">([\s\S]*?)<\/mark>/g, '<b>$1</b>')
  .replace(/<strong class="oui-key">([\s\S]*?)<\/strong>/g, '<b>$1</b>')
  .replace(/ class="oui-fig"/g, '');

const LONG = 'Apex Mechanical is slipping most on SLA response';

test('a key figure is a bold with a digit or a currency sign, 28 characters or fewer', () => {
  for (const s of ['£84,000', '7 work orders', '3 of 12', '23.44/25', '**£1.2m**', '$300', '€90', 'September 2026']) assert.equal(isFigureText(s), true, s);
  for (const s of ['', '   ', 'Lapsed', 'no October scorecard yet', LONG, 'Booked under 4 Nov after the riser check']) assert.equal(isFigureText(s), false, s);
  // A name's or a code's digits are not a figure (the bishopsgate lead "Standby generator GEN-1.").
  for (const s of ['Standby generator GEN-1.', 'Lift Asset-4471', 'SP203-1', 'Level L1']) assert.equal(isFigureText(s), false, s);
  assert.equal(isFigureText('2 open on GEN-1'), true);
  // 28 characters is the last that counts; a link counts as its words.
  assert.equal(isFigureText('x'.repeat(26) + ' 1'), true);
  assert.equal(isFigureText('x'.repeat(27) + ' 1'), false);
  assert.equal(isFigureText('[£84,000](https://example.com/a/very/long/path/to/a/page)'), true);
});

test('in a headline a bold figure gets the marker chip and a bold phrase only weight', () => {
  const html = draw(prog('root = Answer([h])', `h = Headline("**${LONG}**, down **0.73 points** to **23.44/25**.")`));
  assert.ok(LONG.length >= 40);
  assert.match(html, new RegExp('<strong class="oui-key">' + LONG + '</strong>'));
  assert.match(html, /<mark class="oui-fig">0\.73 points<\/mark>/);
  assert.match(html, /<mark class="oui-fig">23\.44\/25<\/mark>/);
  // A long phrase is never marked, so no band runs across a line.
  assert.doesNotMatch(html, new RegExp('oui-fig">' + LONG));
});

test('a step title marks its figure, not its phrase; its detail stresses words without a mark', () => {
  const html = draw(prog('root = Answer([s])', 's = Steps([a])',
    'a = Step("Commission a new **Fire Risk Assessment for the riser** by **£4,200**", "Quote **valid until Friday**.")'));
  assert.match(html, /<strong class="oui-key">Fire Risk Assessment for the riser<\/strong>/);
  assert.match(html, /<mark class="oui-fig">£4,200<\/mark>/);
  assert.match(html, /<span class="oui-step-detail">Quote <strong style="font-weight:600">valid until Friday<\/strong>\.<\/span>/);
});

test('a callout and the brief mark their figures only', () => {
  const html = draw(prog('root = Answer([t, c, u])',
    't = Text("Spend rose **£12,400** this quarter, led by **reactive plumbing callouts**.")',
    'c = Callout("risk", "Insurer deadline: **14 days** left, **no renewal booked**.")',
    'u = Text("Detail.")'));
  // Prose: a bold figure keeps its strong and gains the marker class; a bold phrase stays a plain strong.
  assert.match(html, /<strong style="font-weight:600" class="oui-fig">£12,400<\/strong>/);
  assert.match(html, /<strong style="font-weight:600">reactive plumbing callouts<\/strong>/);
  assert.match(html, /<mark class="oui-fig">14 days<\/mark>/);
  assert.match(html, /<strong class="oui-key">no renewal booked<\/strong>/);
});

test('the marking changes no word: every answer reads exactly as it did before', () => {
  const programs = [
    ...EXAMPLES,
    prog('root = Answer([h, t, s, c])', `h = Headline("**${LONG}** in **September 2026**.")`,
      't = Text("Its score fell **2.04 points** to **86.12**; **no October scorecard yet**. See WO-1184 and (B-301-AHU-03).")',
      's = Steps([a])', 'a = Step("Review **Apex Mechanical\'s September SLA misses first**", "Ask for **the work-order scoring**.")',
      'c = Callout("warn", "**3** certificates expire within **30 days**.")'),
    prog('root = Answer([t])', 't = Table(["Vendor", "Trend"], [["**Apex Lifts**", "**-2.04**"], ["Corvane", "No prior trend"]])')
  ];
  for (const p of programs) {
    const now = draw(p);
    const before = drawBefore(p);
    assert.ok(textOf(now).length > 0);
    assert.equal(textOf(now), textOf(before));
    // The same markup, too, once the marking itself is taken out: no element added or lost.
    assert.equal(unmarked(now), unmarked(before));
  }
});

test('a record code is ink text: no grey box, no tag ring, and it reads as written', () => {
  const html = draw(prog('root = Answer([s])', 's = Section("Lifts", [t])', 't = Text("Booked under WO-1184 for 4 Nov.")'));
  assert.match(html, /Booked under <button[^>]*>WO-1184<\/button> for 4 Nov\./);
  const css = readFileSync(path.join(here, '../src/openui/openui.css'), 'utf8');
  // The tokens of the old tag and the old band are gone, and nothing reads them.
  assert.doesNotMatch(css, /--oui-ref-bg|--oui-ref-ring|--oui-mark-edge/);
  assert.doesNotMatch(css, /user-select:\s*all/);
  const rule = (sel) => (css.match(new RegExp('(?:^|\\n)' + sel.replace(/[.*+?^${}()|[\]\\]/g, '\\$&') + ' \\{([^}]*)\\}')) || [])[1] || '';
  assert.match(rule('.oui-ref'), /background: none;/);
  assert.match(rule('.oui-ref'), /box-shadow: none;/);
  assert.match(rule('.oui-code-ask'), /text-decoration: underline;/);
  // The figure chip is flat: no line under it.
  assert.doesNotMatch(rule('.oui-fig'), /box-shadow/);
  // The brief's frame transitions paint only, never its padding.
  assert.doesNotMatch(css.match(/\.oui-host\.is-live \.oui-brief \{([^}]*)\}/)[1], /padding/);
});
