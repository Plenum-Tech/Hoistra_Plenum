// openui-fixtures — what OpenUI's own parser makes of a set of programs, for svc-deepagents to
// check its Python reader against (agents/openui_view.py, tests/test_openui_view.py).
//
//   node scripts/openui-fixtures.mjs
//
// The server turns the orchestrator's OpenUI answer back into Markdown for everything that reads
// `answer` as text. That rendition must contain what the chat draws and nothing it drops, so the
// Python reader is held to @openuidev/lang-core's results on the same input: the tree it builds,
// the references it could not resolve and the statements nothing reached.
// Re-run after changing src/openui/spec.js or any case below.
import { writeFileSync, mkdirSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import * as core from '@openuidev/lang-core';
import { z } from 'zod';
import { makeLibrary, EXAMPLES, ROOT } from '../src/openui/spec.js';

const dir = fileURLToPath(new URL('../../backend/cafm-connector-service-final/svc-deepagents/tests/fixtures/openui/', import.meta.url));
const library = makeLibrary(core, z);
const schema = library.toJSONSchema();
const parser = core.createParser(schema, ROOT);

// An action prop arrives as the expression it was written as; summarise it as its call and args.
function ast(n) {
  if (!n || typeof n !== 'object') return n;
  if (n.k === 'Str' || n.k === 'Num' || n.k === 'Bool') return n.v;
  if (n.k === 'Null') return null;
  if (n.k === 'Arr') return (n.els || []).map(ast);
  if (n.k === 'Obj') return Object.fromEntries((n.entries || []).map(([k, v]) => [k, ast(v)]));
  if (n.k === 'Comp') return { action: n.name, args: (n.args || []).map(ast) };
  return { ast: n.k };
}
// The rendered tree, as data: components by type and props (null props left out, as a reader
// sees nothing for them), arrays and their nulls kept as they are.
function summary(v) {
  if (Array.isArray(v)) return v.map(summary);
  if (v && typeof v === 'object') {
    if (v.type === 'element') {
      const props = {};
      for (const [k, p] of Object.entries(v.props || {})) if (p !== null && p !== undefined) props[k] = summary(p);
      return { type: v.typeName, props };
    }
    if (typeof v.k === 'string') return ast(v);
    return Object.fromEntries(Object.entries(v).map(([k, p]) => [k, summary(p)]));
  }
  return v;
}

const P = (...lines) => lines.join('\n');
const CASES = {
  'example-dashboard': EXAMPLES[0],
  'example-clarifying-question': EXAMPLES[1],
  'statements-in-reverse-order': P('h = Headline("Late first")', 'root = Answer([h, m])', 'm = Metrics([a])', 'a = Metric("Open", 4)'),
  'inline-nesting': P('root = Answer([Headline("Inline"), Metrics([Metric("A", 1), Metric("B", "2%", "up 1")])])'),
  'optional-and-trailing-commas': P('root = Answer([h m,])', 'h = Headline("No commas")', 'm = Metrics([Metric("A" 1) Metric("B" 2),])'),
  'comments': P('# the answer', 'root = Answer([h]) // root first', 'h = Headline("Has # and // inside")  # trailing'),
  'escapes': P('root = Answer([h, t])', 'h = Headline("Say \\"yes\\" \\u00e9 \\\\ done")', 't = Text("Line one\\nLine two\\ttabbed 🙂")'),
  'single-quoted': P("root = Answer([h])", "h = Headline('It\\'s single')"),
  'null-skips-an-optional': P('root = Answer([m])', 'm = Metrics([a])', 'a = Metric("Overdue", 7, null, "risk")'),
  'fenced-with-prose': P('Here is the answer:', '```openui', 'root = Answer([h])', 'h = Headline("Fenced")', '```', 'Thanks.'),
  'unresolved-reference': P('root = Answer([h, missing])', 'h = Headline("Half there")'),
  'orphan-statement': P('root = Answer([h])', 'h = Headline("Kept")', 'lost = Headline("Never reached")'),
  'duplicate-name-later-wins': P('root = Answer([h])', 'h = Headline("First")', 'h = Headline("Second")'),
  'type-mismatches': P('root = Answer([h, m, c, t, ok])', 'h = Headline(42)', 'm = Metrics([Metric("x", 7, null, "weird")])',
    'c = BarChart("T", ["a", "b"], ["3", 2])', 't = Table(["a", 1, "b"], [["x", null, true], "notarow", [{k: 1}, 2]])', 'ok = Text("survivor")'),
  'unknown-component-and-data-in-slot': P('root = Answer([bad, 5, "str", null, h])', 'bad = Nope(1)', 'h = Headline("Only me")'),
  'entry-without-root': P('page = Answer([h])', 'h = Headline("Fallback entry")'),
  'no-entry-at-all': P('h = Headline("Lonely")'),
  'excess-args': P('root = Answer([h])', 'h = Headline("Kept", "dropped", 3)'),
  'multi-line-array': P('root = Answer([', '  h,', '  m', '])', 'h = Headline("Spread out")', 'm = Metrics([', '  Metric("A", 1),', '  Metric("B", 2)', '])'),
  'raw-newline-in-string': P('root = Answer([t])', 't = Text("first line', 'second line")'),
  'actions-one-and-many': P('root = Answer([b, s])', 'b = Buttons([x, y])', 'x = Button("Go", Action([@ToAssistant("a"), @OpenUrl("/compliance")]), "primary")',
    'y = Button("Open", @OpenUrl("https://example.com/x"))', 's = Steps([Step("Do it", null, Button("Ask", @ToAssistant("q", {site: "B-101"})))])'),
  'numbers': P('root = Answer([c])', 'c = LineChart("Trend", ["a", "b", "c", "d"], [Series("S", [-2, 3.5, 1e3, 0.5])], "kWh")'),
  'numeric-labels-and-columns': P('root = Answer([c, t])', 'c = BarChart("EUI by year", ["2023", 2024, 2025], [241, 230, 225])',
    't = Table(["Year", 2024, "Change"], [["EUI", 230, "-4%"]])'),
  'figures-as-text-and-nulls': P('root = Answer([c, l])', 'c = BarChart("Spend", ["Acme", "Brightline", "Coolair"], ["£1,200", 800, null])',
    'l = LineChart("Trend", ["Jan", "Feb", "Mar"], [Series("A", [4.1, null, 3.9])])'),
  'nulls-and-booleans-in-lists': P('root = Answer([t, k])', 't = Table([null, "a", true], [null, ["x", null], "bad"])',
    'k = Metrics([Metric("Compliant", true), null])'),
  // 27 Oct 2026: a bare signed number in a row, a row longer than its columns, chart values past
  // their last label, and root written last.
  'unary-plus-in-a-row': P('root = Answer([t])', 't = Table(["Vendor", "Change", "Score"], [["Corvane", +0.45, "22.45/25"]])'),
  'ragged-row': P('root = Answer([t])', 't = Table(["Vendor", "Score"], [["Apex", "23.44 / 25", "Primary slippage"], ["Corvane"]])'),
  'chart-values-past-labels': P('root = Answer([c])', 'c = BarChart("Jobs", ["HVAC", "Lifts"], [3, 2, 9])'),
  'root-written-last': P('h = Headline("Root comes last")', 'm = Metrics([Metric("Open", 4)])', 'root = Answer([h, m])'),
  // 28 Oct 2026: Finding(title, text, tone?, code?), one issue of a list, in an Answer and in a
  // Section; a tone skipped with null before a code; and one cut off while it streams.
  'finding-plain': P('root = Answer([h, f])', 'h = Headline("One thing to watch")', 'f = Finding("Boiler 2", "Pressure dropped twice this week.\\n- 1.1 bar on Monday\\n- 0.9 bar on Thursday")'),
  'finding-with-code-and-tone': P('root = Answer([f])',
    'f = Finding("Fire Risk Assessment", "Lapsed, expired 2026-06-29 (about 4 months ago). The building has **no current FRA** on record.", "risk", "FRA-B-301-2025")'),
  'finding-null-tone-with-code': P('root = Answer([f])', 'f = Finding("Lift 4", "Examination booked for 4 Nov.", null, "LIFT-B-04")'),
  'findings-inside-a-section': P('root = Answer([h, s])', 'h = Headline("Two certificates need you.")', 's = Section("Risk to life", [a, b], "1 lapsed, 1 due")',
    'a = Finding("Fire Risk Assessment", "Lapsed on 29 Jun.", "risk", "FRA-B-2025")', 'b = Finding("Gas safety", "Expires on Friday.", "warn")'),
  'finding-partial-streaming': P('root = Answer([a, b])', 'a = Finding("Gas safety", "Expires on Friday.", "warn")', 'b = Finding("Fire Risk Assessment", "Lapsed, exp'),
  'prose-only': 'The backlog is long. Total = 42 overdue jobs.',
  'empty': '',
  'everything': P(
    'root = Answer([head, kpis, warn, sec, rec, chart, steps, mail, more])',
    'head = Headline("All **12 assets** at Building A are covered.")',
    'kpis = Metrics([Metric("Assets", 12, "all covered", "ok"), Metric("Open jobs", 0, null, "risk")])',
    'warn = Callout("warn", "The lift certificate expires in 9 days.", "Lift")',
    'sec = Section("Asset detail", [tbl], "12 assets, 3 types", true)',
    'tbl = Table(["Asset", "Type", "Age"], [["AHU-1", "Air handling", 9], ["CH-1", "Chiller", 4]], "2 shown")',
    'rec = Details("AHU-1", [Field("Location", "Roof"), Field("Installed", 2017)], "Air handling unit")',
    'chart = LineChart("Energy", ["W1", "W2"], [Series("A", [4.1, 4.3]), Series("B", [3.4, 3.3])], "kWh/m²")',
    'steps = Steps([Step("Book the lift inspection", "Expires in 9 days.", Button("Draft email", @ToAssistant("Draft it"), "primary")), Step("Nothing else")])',
    'mail = EmailDraft("Lift inspection", "ops@example.com", "Hello,\\n\\nPlease book.", "pm@example.com")',
    'more = FollowUps(["What else is due?", "Show the lift history"])'
  )
};

const props = (name) => {
  const d = schema.$defs[name];
  const req = new Set(d.required || []);
  return Object.keys(d.properties || {}).map((k) => [k, req.has(k)]);
};
const components = Object.keys(library.components);
const out = Object.entries(CASES).map(([name, program]) => {
  const r = parser.parse(program);
  return {
    name, program,
    root: r.root ? summary(r.root) : null,
    unresolved: [...r.meta.unresolved].sort(),
    orphaned: [...r.meta.orphaned].sort()
  };
});

mkdirSync(dir, { recursive: true });
writeFileSync(dir + 'cases.json', JSON.stringify(out, null, 1) + '\n');
writeFileSync(dir + 'schema.json', JSON.stringify(Object.fromEntries(components.map((c) => [c, props(c)])), null, 1) + '\n');
console.log('wrote ' + out.length + ' cases and the schema of ' + components.length + ' components to ' + dir);
