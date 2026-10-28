// openuiRender — what the orchestrator's OpenUI answers look like once drawn (src/openui).
//
// The library is bundled with esbuild and rendered through OpenUI's own <Renderer> with
// react-dom/server, as test/vendorDeleteDialog.test.mjs does for its dialog. Each program here
// is what the model writes; the assertions are about what a reader would get.
import { test, after } from 'node:test';
import assert from 'node:assert/strict';
import { build } from 'esbuild';
import { writeFileSync, rmSync, readFileSync } from 'node:fs';
import { fileURLToPath, pathToFileURL } from 'node:url';
import path from 'node:path';

const here = path.dirname(fileURLToPath(import.meta.url));
const outFile = path.join(here, '.openui-render.bundle.mjs');
const res = await build({
  stdin: {
    contents: "export { library } from '../src/openui/library.jsx';",
    resolveDir: here, loader: 'js'
  },
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

const render = (program, streaming = false) => renderToStaticMarkup(
  React.createElement(Renderer, { library, response: program, isStreaming: streaming, publishObservability: false })
);
const count = (html, re) => (html.match(re) || []).length;
const prog = (...lines) => lines.join('\n');

test('a Headline marks its bold figures with the highlighter, not plain bold', () => {
  const html = render(prog('root = Answer([h])', 'h = Headline("**7 work orders** are overdue at Building A.")'));
  assert.match(html, /<mark class="oui-fig">7 work orders<\/mark>/);
  assert.match(html, /are overdue at Building A\./);
});

test('a Metric carries its tone as a class and its value as written', () => {
  const html = render(prog('root = Answer([k])', 'k = Metrics([a, b])', 'a = Metric("Overdue", 7, "+3 on last week", "risk")', 'b = Metric("SLA met", "91%")'));
  assert.match(html, /oui-metric oui-tone-risk/);
  assert.match(html, /oui-metric oui-tone-neutral/);
  assert.match(html, />7</);
  assert.match(html, />91%</);
  assert.match(html, /\+3 on last week/);
});

test('a long table shows 8 rows and offers the rest, inside its own scrolling box', () => {
  const rows = Array.from({ length: 30 }, (_, i) => `["WO-${1000 + i}", "Job ${i}", ${i + 1}]`).join(', ');
  const html = render(prog('root = Answer([t])', `t = Table(["Work order", "Job", "Days overdue"], [${rows}], "All open jobs")`));
  assert.equal(count(html, /<tr class="oui-row">/g), 8);
  assert.match(html, /Show all 30 rows/);
  assert.match(html, /class="oui-scroll"/);
  assert.match(html, /All open jobs/);
});

test('a numeric column is right-aligned and a code column is set in mono', () => {
  const html = render(prog('root = Answer([t])', 't = Table(["Work order", "Days overdue", "Cost"], [["WO-1184", 9, "£1,200"], ["WO-1179", 6, "£80"]])'));
  assert.equal(count(html, /<td class="oui-num">/g), 4);
  assert.equal(count(html, /<td class="oui-code">/g), 2);
});

test('a BarChart draws one bar per value and accents none of them', () => {
  // 27 Oct 2026: the largest bar took the accent even when the answer was about another item.
  const html = render(prog('root = Answer([c])', 'c = BarChart("Overdue by trade", ["HVAC", "Electrical", "Plumbing"], [3, 2, 1], "jobs")'));
  assert.equal(count(html, /class="oui-bar-fill/g), 3);
  assert.equal(count(html, /is-top/g), 0);
  assert.match(html, /Overdue by trade/);
  // A count of one reads in the singular: "1 job", not "1 jobs".
  assert.match(html, />3 jobs</);
  assert.match(html, />1 job</);
});

test('a LineChart draws one line per series', () => {
  const html = render(prog('root = Answer([c])',
    'c = LineChart("Energy use", ["W1", "W2", "W3", "W4"], [a, b], "kWh/m²")',
    'a = Series("Building A", [4.1, 4.3, 4.6, 5.0])',
    'b = Series("Building B", [3.4, 3.3, 3.5, 3.4])'));
  assert.equal(count(html, /<polyline/g), 2);
  assert.match(html, /Building A/);
  assert.match(html, /Building B/);
  // The latest value is labelled at the series' own precision: 5.0 among 4.1 and 4.3, not 5.
  assert.match(html, />5\.0</);
});

// Paired lists never shift (found in review, 26 Oct 2026): a value that is not a plain number
// stays in its place, so no figure lands under another item's label.
test('a bar chart keeps every item in place: numeric labels, figures written as text, missing values', () => {
  const years = render(prog('root = Answer([c])', 'c = BarChart("EUI by year", ["2023", 2024, 2025], [241, 230, 225])'));
  assert.equal(count(years, /class="oui-bar-fill/g), 3);
  assert.match(years, />2024</);
  assert.match(years, />2025</);
  const spend = render(prog('root = Answer([c])', 'c = BarChart("Spend", ["Acme", "Brightline", "Coolair"], ["£1,200", 800, null])'));
  assert.equal(count(spend, /class="oui-bar-fill/g), 2, 'no bar for a missing value');
  assert.match(spend, /Acme[\s\S]*£1,200[\s\S]*Brightline[\s\S]*800[\s\S]*Coolair[\s\S]*—/);
});

test('a missing point breaks the line instead of dropping it to zero', () => {
  const html = render(prog('root = Answer([c])', 'c = LineChart("Trend", ["Jan", "Feb", "Mar", "Apr"], [a])', 'a = Series("A", [4.1, 4.3, null, 3.9])'));
  assert.equal(count(html, /<polyline/g), 2, 'two runs either side of the gap');
});

test('a numeric column heading keeps its column', () => {
  const html = render(prog('root = Answer([t])', 't = Table(["Year", 2024, "Change"], [["EUI", 230, "-4%"]])'));
  assert.equal(count(html, /<th[ >]/g), 3);
  assert.match(html, />2024<\/th>/);
  assert.match(html, /<td>EUI<\/td><td class="oui-num">230<\/td>/);
});

test('a collapsed Section shows its title and summary and keeps its detail closed', () => {
  const html = render(prog('root = Answer([s])', 's = Section("Overdue work orders", [t], "7 past their due date", true)', 't = Text("hidden detail")'));
  assert.match(html, /Overdue work orders/);
  assert.match(html, /7 past their due date/);
  assert.match(html, /aria-expanded="false"/);
  assert.doesNotMatch(html, /hidden detail/);
});

test('an open Section shows its detail', () => {
  const html = render(prog('root = Answer([s])', 's = Section("Why", [t])', 't = Text("visible detail")'));
  assert.match(html, /visible detail/);
});

test('a link to an unsafe scheme renders as text, never as an href', () => {
  const html = render(prog('root = Answer([t])', 't = Text("Open [this](javascript:alert(1)) now")'));
  assert.doesNotMatch(html, /href="javascript/);
  assert.match(html, /this/);
});

test('model text is scrubbed of internal names', () => {
  const html = render(prog('root = Answer([h, t])', 'h = Headline("Read from UDR")', 't = Text("Checked with UDR")'));
  assert.doesNotMatch(html, /\bUDR\b/);
  assert.match(html, /building data/);
});

test('an EmailDraft shows subject, recipients and body, with Copy', () => {
  const html = render(prog('root = Answer([e])', 'e = EmailDraft("Gas safety inspection", "ops@example.com", "Hello,\\nPlease book.", "pm@example.com")'));
  assert.match(html, /Gas safety inspection/);
  assert.match(html, /ops@example\.com/);
  assert.match(html, /pm@example\.com/);
  assert.match(html, /Please book\./);
  assert.match(html, />Copy</);
});

test('buttons and follow-ups are disabled while the answer is still streaming', () => {
  const p = prog('root = Answer([b, f])', 'b = Buttons([x])', 'x = Button("Show the backlog", @ToAssistant("Show the backlog"))', 'f = FollowUps(["Why?"])');
  const live = render(p, true);
  assert.equal(count(live, /<button[^>]*disabled=""/g), 2);
  const done = render(p, false);
  assert.equal(count(done, /<button[^>]*disabled=""/g), 0);
});

test('a long button label wraps inside its box rather than widening the dock', () => {
  // The dock gives an answer about 190px; one long label scrolled it sideways (review, 26 Oct 2026).
  const css = readFileSync(new URL('../src/openui/openui.css', import.meta.url), 'utf8');
  const btn = /\.oui-btn\s*\{[^}]*\}/.exec(css)[0];
  assert.match(btn, /white-space:\s*normal/);
  assert.match(btn, /overflow-wrap:\s*anywhere/);
  assert.match(btn, /max-width:\s*100%/);
});

test('steps are numbered and carry their button', () => {
  const html = render(prog('root = Answer([s])', 's = Steps([a, b])', 'a = Step("Book the inspection", "Expires Friday.", x)', 'x = Button("Draft the email", @ToAssistant("Draft it"), "primary")', 'b = Step("Chase the vendor")'));
  assert.match(html, /<span class="oui-step-n" aria-hidden="true">1<\/span>/);
  assert.match(html, /<span class="oui-step-n" aria-hidden="true">2<\/span>/);
  assert.match(html, /Draft the email/);
  assert.match(html, /oui-btn is-primary/);
});

test('a step button that only repeats its step is a compact arrow with the same name', () => {
  const html = render(prog('root = Answer([s])', 's = Steps([a])', 'a = Step("Reassess the FRA now", "Close the riser remedial.", x)', 'x = Button("Reassess the FRA now", @ToAssistant("Reassess the FRA now"))'));
  assert.match(html, /class="oui-btn oui-btn-icon"[^>]*aria-label="Reassess the FRA now"/);
  assert.equal((html.match(/Reassess the FRA now/g) || []).length, 3, 'the title once, the button name twice (aria-label and title)');
});

test('a step button whose words are all in its step is a compact arrow too, a different ask stays a button', () => {
  // 27 Oct 2026: "Review Apex Mechanical's September jobs first" with a button "Review Apex Mechanical's September jobs".
  const html = render(prog('root = Answer([s])', 's = Steps([a])',
    'a = Step("Review Apex Mechanical\'s September jobs first", "And ask for a recovery plan.", x)',
    'x = Button("Review Apex Mechanical\'s September jobs", @ToAssistant("Show Apex Mechanical\'s September jobs"))'));
  assert.match(html, /class="oui-btn oui-btn-icon"[^>]*aria-label="Review Apex Mechanical(&#x27;|')s September jobs"/);
  const kept = render(prog('root = Answer([s])', 's = Steps([a])', 'a = Step("Reassess the FRA now", "", x)', 'x = Button("Draft the email to Corvane", @ToAssistant("Draft it"))'));
  assert.doesNotMatch(kept, /oui-btn-icon/);
  assert.match(kept, />Draft the email to Corvane</);
});

test('a collapsed Section holding the answer\'s only table or chart is drawn open', () => {
  // 27 Oct 2026: the vendors' SLA scores sat closed under "SLA delivery" with nothing else showing them.
  const html = render(prog('root = Answer([h, s])', 'h = Headline("Apex Mechanical is slipping most.")',
    's = Section("SLA delivery", [t], "September scores", true)',
    't = Table(["Vendor", "SLA completion"], [["Apex Mechanical", "21.88 / 25"], ["Apex Lifts", "23.21 / 25"]])'));
  assert.match(html, /aria-expanded="true"/);
  assert.match(html, /Apex Lifts/);
  // supporting detail stays closed when the answer's figures already show outside it
  const shut = render(prog('root = Answer([c, s])', 'c = BarChart("Jobs", ["HVAC", "Electrical"], [3, 2])',
    's = Section("All jobs", [t], "5 jobs", true)', 't = Table(["Job", "Trade"], [["WO-1", "HVAC"]])'));
  assert.match(shut, /aria-expanded="false"/);
  assert.doesNotMatch(shut, /WO-1/);
});

test('an exact repeat is drawn once: a chart and its own two columns, a section and its one chart', () => {
  const html = render(prog('root = Answer([sec])',
    'sec = Section("Overdue by trade", [c, t])',
    'c = BarChart("Overdue by trade", ["HVAC", "Electrical"], [3, 2])',
    't = Table(["Item", "Value"], [["HVAC", 3], ["Electrical", 2]])'));
  assert.equal((html.match(/class="oui-table"/g) || []).length, 0);
  assert.equal((html.match(/Overdue by trade/g) || []).length, 1);
  // a table that differs in any figure is kept
  const kept = render(prog('root = Answer([c, t])', 'c = BarChart("Jobs", ["HVAC", "Electrical"], [3, 2])', 't = Table(["Trade", "Jobs"], [["HVAC", 3], ["Electrical", 4]])'));
  assert.equal((kept.match(/class="oui-table"/g) || []).length, 1);
});
