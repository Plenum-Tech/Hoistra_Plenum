// shared — the small pieces every OpenUI answer component uses: values as they read, the
// headline's highlighted figures, tones, and the memo comparison that keeps a streaming answer
// cheap.
//
// Everything a model wrote goes through inline() (components/shell/markdownInline.jsx), which is
// what scrubs internal names and decides which link targets are safe, exactly as the Markdown
// answers do. Nothing here builds HTML from a string.
import React from 'react';
import { inline } from '../components/shell/markdownInline.jsx';
import { CodeTag } from './ask.jsx';

export const arr = (v) => (Array.isArray(v) ? v : []);

// A figure exactly as the answer wrote it (27 Oct 2026: an install year 2004 read "2,004", a leak
// rate 0.004 read "0"). A string is shown as written; a number as its plain digits, never rounded
// and never grouped. Where grouping matters the answer writes the figure as text ("£84,000").
// Only float noise is cleaned: 0.1 + 0.2 reads 0.3, and -0 reads 0.
const plain = (v) => {
  const n = Number(v.toPrecision(15));
  return Object.is(n, -0) ? 0 : n;
};
export const fmt = (v) => (typeof v === 'number' && Number.isFinite(v) ? String(plain(v)) : String(v == null ? '' : v));

// One table or field cell. An empty cell reads as a dash, not as nothing, so a gap in the data
// is visible as a gap.
export function cell(v, key) {
  if (v === null || v === undefined || v === '') return '—';
  if (typeof v === 'boolean') return v ? 'Yes' : 'No';
  if (typeof v === 'number') return fmt(v);
  return inline(String(v), key);
}

// A chart value read for drawing: only to scale and place it, never to show it (what is shown is
// the figure as written). 800, "£1,200" → 1200, "4.1 kWh" → 4.1. The sign counts wherever it is
// written: "-£1,200", "£-300", "−950" (a typographic minus), "(1,200)" (accounts) are negative.
// A scale written onto the number counts too: "£1.2m" is 1,200,000 beside "£800k" (but "12m²"
// and "5 min" are 12 and 5). A ratio ("23.44 / 25") keeps what it is out of, so a chart can
// tell 9/10 from 10/40. kind is what the number is of (a currency, a percentage, a ratio): two
// figures of different kinds are never drawn against each other. A number has no kind of its own.
// Anything else (null, a word, true) is missing, drawn as missing in its own place, never as 0.
const FIGURE = /(\d[\d,]*(?:\.\d+)?|\.\d+)(?:(k|m|bn)(?![\p{L}²³]))?(?:\s*\/\s*(\d[\d,]*(?:\.\d+)?))?/iu;
const LEAD = /^\(?\s*([-+]?)\s*([£$€]?)\s*([-+]?)\s*$/;
const SCALE = { k: 1e3, m: 1e6, bn: 1e9 };
const digits = (s) => Number(s.replace(/,/g, ''));
export function readFigure(v) {
  if (typeof v === 'number') return Number.isFinite(v) ? { n: v, of: null, kind: null } : null;
  if (typeof v !== 'string') return null;
  const s = v.trim().replace(/[−–]/g, '-');
  const m = FIGURE.exec(s);
  if (!m) return null;
  const head = s.slice(0, m.index);
  const rest = s.slice(m.index + m[0].length).trim();
  // Only a sign or a currency before the number makes it signed: "WO-1184" is not minus 1184.
  const lead = LEAD.exec(head);
  const neg = !!lead && (lead[1] === '-' || lead[3] === '-' || (head.trimStart().startsWith('(') && rest.startsWith(')')));
  const n = digits(m[1]) * (m[2] ? SCALE[m[2].toLowerCase()] : 1) * (neg ? -1 : 1);
  const of = m[3] ? digits(m[3]) : null;
  if (!Number.isFinite(n)) return null;
  const currency = (/[£$€]/.exec(head) || [''])[0];
  return { n, of: of > 0 ? of : null, kind: currency + (rest.startsWith('%') ? '%' : '') + (of > 0 ? '/' : '') };
}
export function toNumber(v) {
  const f = readFigure(v);
  return f ? f.n : null;
}

// The numbers a chart draws its values to, all on one scale; null for the whole chart when its
// values are not one measure ("23.44 / 25" beside "86.12", "91%" beside "£300"). Those are shown
// as written and never drawn against each other. Ratios over one denominator are drawn by their
// numerators; over different ones, by what each works out to (10/40 is shorter than 9/10).
export function onOneScale(values) {
  const figs = arr(values).map(readFigure);
  const read = figs.filter(Boolean);
  if (new Set(read.filter((f) => f.kind !== null).map((f) => f.kind)).size > 1) return null;
  const outOf = new Set(read.filter((f) => f.of !== null).map((f) => f.of));
  if (outOf.size > 1 && read.some((f) => f.of === null)) return null;
  return figs.map((f) => (!f ? null : outOf.size > 1 ? f.n / f.of : f.n));
}

// A record code: WO-2026-1184, B-101, CERT-77.
export const CODE = /^[A-Z][A-Z0-9]{0,6}-\d[\w./-]*$/;

// A reference code in running text: an asset tag, a certificate or record number ("B-301-LIFT-4471",
// "FRA-B-301-2025", "GEN-1"): capitals and digits joined by hyphens, with a digit. Drawn as a tag
// (.oui-ref, like the plate on a piece of plant), so the reader finds which asset a finding is
// about without re-reading the sentence, and one click asks about the record (ask.jsx CodeTag;
// it selected the whole code until 28 Oct 2026). The characters are the answer's own, unchanged,
// so copy and find read the same text (28 Oct 2026). CODE above is
// for a whole cell and misses "FRA-B-301-2025"; this one finds a code inside a sentence. Not a
// code: "Asset-4471", "F-Gas", a date, "L1", "£228,000".
const REF = /\b[A-Z][A-Z0-9]*(?:-[A-Z0-9]+)+\b/g;
function refsIn(s, kb) {
  const out = [];
  let last = 0;
  let n = 0;
  let m;
  REF.lastIndex = 0;
  while ((m = REF.exec(s))) {
    if (!/\d/.test(m[0])) continue;
    let a = m.index;
    let b = a + m[0].length;
    // A code the answer put in brackets keeps its brackets beside it, so a label can set the
    // pair on a line of its own ("CHILLER-101" over "(B-301-CHILLER-101)").
    const grouped = s[a - 1] === '(' && s[b] === ')';
    if (grouped) { a -= 1; b += 1; }
    if (a > last) out.push(s.slice(last, a));
    // A tag that asks about its record when clicked (ask.jsx CodeTag, 28 Oct 2026); the plain tag
    // outside an answer and inside a control that is already the action.
    const tag = <CodeTag key={kb + 't' + n} code={m[0]} />;
    out.push(grouped ? <span key={kb + 'g' + n} className="oui-ref-group">{'('}{tag}{')'}</span> : tag);
    last = b;
    n += 1;
  }
  if (!n) return [s];
  if (last < s.length) out.push(s.slice(last));
  return out;
}
// The codes in what inline() drew, as tags. Only plain text is read: bold and italic are looked
// into, a link or inline code is left as it is. OpenUI only; inline() itself is shared with the
// reports, the PDF export and the buttons, which keep their codes as plain text.
//
// A bold figure among them (isFigureText) is marked as the headline marks one: the strong gains
// the marker class, its words untouched (28 Oct 2026). Bold words that are not a figure stay a
// plain strong, drawn heavier and nothing more.
export function refs(nodes, kb = 'r') {
  return (Array.isArray(nodes) ? nodes : [nodes]).flatMap((x, i) => {
    if (typeof x === 'string') return refsIn(x, kb + i);
    if (React.isValidElement(x) && (x.type === 'strong' || x.type === 'em')) {
      const fig = x.type === 'strong' && isFigureText(textOfNodes(x.props.children));
      const cls = fig ? [x.props.className, 'oui-fig'].filter(Boolean).join(' ') : undefined;
      return [React.cloneElement(x, cls ? { className: cls } : undefined, refs(x.props.children, kb + i + '-'))];
    }
    return [x];
  });
}

// A key figure: bold words that read as a figure ("£84,000", "7 work orders", "3 of 12") rather
// than a phrase. Only these get the marker (28 Oct 2026, Hussain: "the way u highlight stuff …
// its shabby": every bold went yellow, so a long step title read as one smeared band). A figure
// has a digit or a currency sign and is short; past 28 characters it is a sentence with a number
// in it, and reads as bold words, not a figure. The digits of a name or a record code ("GEN-1",
// "Asset-4471", "SP203-1", "L1": a word that starts with a letter) are not a figure: "Standby
// generator GEN-1" is what a finding is about, not how much.
const NAMED = /(?<![\p{L}\d])\p{L}[\p{L}\d]*(?:[-/][\p{L}\d]+)*/gu;
export function isFigureText(s) {
  const t = String(s == null ? '' : s)
    .replace(/\[([^\]]*)\]\([^)]*\)/g, '$1')
    .replace(/\*\*|__|`/g, '')
    .replace(/\s+/g, ' ')
    .trim();
  return t.length > 0 && t.length <= 28 && /[\d£$€]/.test(t.replace(NAMED, (w) => (/\d/.test(w) ? '' : w)));
}
// The plain text of what inline() drew (strings, and the strings inside its elements).
function textOfNodes(v) {
  if (v == null || typeof v === 'boolean') return '';
  if (typeof v === 'string' || typeof v === 'number') return String(v);
  if (Array.isArray(v)) return v.map(textOfNodes).join('');
  if (React.isValidElement(v)) return textOfNodes(v.props.children);
  return '';
}

// Bold in a headline, a step title or a callout. A figure the sentence turns on gets the marker
// tint (the way someone marks up a drawing): a flat, pale chip behind the figure alone. Bold words
// that are not a figure are a phrase the answer stressed: heavier type, no tint (.oui-key). The
// words are the same either way; only the element around them differs (28 Oct 2026).
export function marked(text, keyBase) {
  const s = String(text == null ? '' : text);
  const out = [];
  const re = /\*\*([^*]+)\*\*/g;
  let last = 0;
  let n = 0;
  let m;
  while ((m = re.exec(s))) {
    if (m.index > last) out.push(...inline(s.slice(last, m.index), keyBase + 'i' + n));
    out.push(isFigureText(m[1])
      ? <mark key={keyBase + 'm' + n} className="oui-fig">{inline(m[1], keyBase + 'f' + n)}</mark>
      : <strong key={keyBase + 'm' + n} className="oui-key">{inline(m[1], keyBase + 'f' + n)}</strong>);
    last = m.index + m[0].length;
    n += 1;
  }
  if (last < s.length) out.push(...inline(s.slice(last), keyBase + 'i' + n));
  return out;
}

export const TONES = ['risk', 'warn', 'ok', 'neutral'];
// A label that names something other than a count: what is left, how long until, a number of
// days or hours, a rate, a score, money. Zero there is often the bad outcome ("Days until expiry:
// 0", "SLA met: 0%"). A period the count is over ("this month") is not one of those.
const NOT_A_COUNT = /\b(?:left|remaining|until|before|to go|days?|hours?|hrs?|rate|ratio|score|budget|balance|spend|cost|value|saving|savings|margin|met|coverage|compliance|compliant|uptime|availability|capacity|headroom|stock|percent(?:age)?)\b/i;
// The tone a figure is drawn in: the one the model gave it. The one exception is a plain zero
// count of something bad ("Overdue work orders: 0"), which is good news and reads ok. A zero
// percentage, sum of money or count of days keeps its tone (27 Oct 2026: "Days until gas
// certificate expires: 0" was drawn green).
export function toneOf(value, tone, label) {
  const t = TONES.includes(tone) ? tone : 'neutral';
  const zero = String(value == null ? '' : value).trim() === '0';
  if ((t === 'risk' || t === 'warn') && zero && label && !NOT_A_COUNT.test(String(label))) return 'ok';
  return t;
}

// OpenUI hands every component fresh prop objects on each update of a streaming answer, so a
// plain memo never hits. Comparing the data lets a finished block sit still while the rest of
// the answer is still arriving.
export const sameData = (a, b) => a.props === b.props || JSON.stringify(a.props) === JSON.stringify(b.props);

// The props of a child element (a Step, a Series, a Field) when the parent draws it itself.
export const childProps = (v) => (v && typeof v === 'object' && v.props && typeof v.props === 'object' ? v.props : null);

// Blocks that say the same thing twice. A model laying out an answer sometimes draws a bar chart
// and then the same two columns again as a table, or titles a section with the name of the one
// chart inside it (27 Oct 2026). Only an exact repeat goes: a table whose every row is a bar of a
// chart beside it, label for label and figure for figure. Nothing that differs is ever hidden:
// a table with a caption, or whose value heading says something the chart does not ("Spend
// (£k, ex VAT)"), stays, and so does one whose figures are written differently (27 Oct 2026:
// "-£1,200" on the chart is not "£1,200" in the table).
const norm = (v) => String(v == null ? '' : v).replace(/\*\*|__/g, '').replace(/\s+/g, ' ').trim().toLowerCase();
const sameFigure = (a, b) => norm(fmt(a)) === norm(fmt(b));
const isNode = (n, type) => n && typeof n === 'object' && n.typeName === type && n.props;
function repeatsAChart(table, charts) {
  const cols = arr(table.props.columns);
  const rows = arr(table.props.rows).filter(Array.isArray);
  if (cols.length !== 2 || !rows.length || rows.some((r) => r.length > 2) || norm(table.props.caption)) return false;
  const head = norm(cols[1]);
  return charts.some((c) => {
    const labels = arr(c.props.labels);
    const values = arr(c.props.values);
    const headSaid = !head || /^values?$/.test(head) || norm(c.props.title).includes(head) || head === norm(c.props.unit);
    // A word in a figure's place is not drawn as a bar, so its table is kept (28 Oct 2026).
    return headSaid && labels.length === rows.length
      && rows.every((r, i) => norm(r[0]) === norm(labels[i]) && readFigure(values[i]) !== null && sameFigure(r[1], values[i]));
  });
}
export function withoutRepeats(children) {
  const list = arr(children);
  const charts = list.filter((n) => isNode(n, 'BarChart'));
  if (!charts.length) return children;
  return list.filter((n) => !(isNode(n, 'Table') && repeatsAChart(n, charts)));
}
// Tables and charts: the blocks that carry an answer's figures.
const isData = (n) => isNode(n, 'Table') || isNode(n, 'BarChart') || isNode(n, 'LineChart');
// The tables and charts the blocks show without the reader opening anything.
export function dataShown(children) {
  return arr(children).flatMap((n) => (isData(n) ? [n]
    : isNode(n, 'Section') && n.props.collapsed !== true ? dataShown(n.props.children) : []));
}
// Whether the blocks show a table or chart without the reader opening anything.
export const dataInView = (children) => dataShown(children).length > 0;
// Whether the blocks hold a block of a type (a table or chart by default) anywhere, open or closed.
export function holdsData(children, is = isData) {
  return arr(children).some((n) => is(n) || (isNode(n, 'Section') && holdsData(n.props.children, is)));
}
const tablesIn = (children) => arr(children).flatMap((n) => (isNode(n, 'Table') ? [n]
  : isNode(n, 'Section') ? tablesIn(n.props.children) : []));
// A chart drawn from one of these tables: every item it draws is a row of the table (its labels
// are the table's first column). It shows a column or two of that table, not the table.
function drawnFrom(chart, tables) {
  const labels = arr(chart.props.labels).map(norm).filter(Boolean);
  return !isNode(chart, 'Table') && labels.length > 0 && tables.some((t) => {
    const rows = new Set(arr(t.props.rows).filter(Array.isArray).map((r) => norm(r[0])));
    return labels.every((l) => rows.has(l));
  });
}
// Whether a closed section starts open. A caveat is never skipped: a section holding a Callout
// opens. A section holding a table or chart opens unless the answer already shows its figures
// outside it; a chart drawn from the section's own table does not count, since the chart is one
// column of what the section hides (27 Oct 2026, the vendors' SLA scorecard closed beside a bar
// chart of one of its columns). shown is dataShown() of the whole answer, null outside one.
export function startsOpen(children, shown) {
  if (holdsData(children, (n) => isNode(n, 'Callout'))) return true;
  if (!shown || !holdsData(children)) return false;
  const tables = tablesIn(children);
  return shown.every((n) => drawnFrom(n, tables));
}
// The one chart a section holds, when it already carries the section's title.
export function soleChartTitled(children, title) {
  const list = arr(children).filter(Boolean);
  const only = list.length === 1 ? list[0] : null;
  return !!(only && (isNode(only, 'BarChart') || isNode(only, 'LineChart')) && norm(only.props.title) === norm(title));
}

