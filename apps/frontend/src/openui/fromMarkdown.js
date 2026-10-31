// fromMarkdown — any Markdown answer, written as the OpenUI program the chat draws.
//
// Only the general orchestrator loop writes OpenUI itself. Every other route answers in Markdown:
// the engine sub-agents (vendors, energy, contracts), the planner's synthesis, the compliance
// summary when its analyst fails, and every chat saved before OpenUI. This writes those answers'
// OWN text as a program, so every answer is drawn by the same components (27 Oct 2026: "I want
// it on all answers").
//
// Presentation only, like logic/answerCards.js, whose arrangement it reuses: nothing is computed,
// nothing invented, nothing dropped. Every block lands in some component, in order; a block no
// rule recognises is drawn as Markdown Text. Pure and dependency-free (no OpenUI code), so the
// chat's first load can carry it and node can test it.
import { answerCards, toneOf } from '../logic/answerCards.js';
import { isDivider } from '../logic/markdownBlocks.js';

// A string literal. JSON's escapes are OpenUI Lang's. A string that holds `root = Answer(` would
// read to detect.js as the start of a program (it takes the last one), so its `r` is escaped.
export const lit = (s) => JSON.stringify(String(s == null ? '' : s)).replace(/\broot(\s*=\s*Answer\s*\()/g, '\\u0072oot$1');

const unbold = (s) => String(s == null ? '' : s).replace(/\*\*|__/g, '').trim();
const TONE = { critical: 'risk', warning: 'warn', ok: 'ok' };
// A figure that is none: "0", "£0", "0.0", "0%".
const ZERO = /^[£$€]?\s?0(?:[.,]0+)?\s?%?$/;

// Statements in reading order, root first, so a program built from a streaming answer draws top
// to bottom while it grows.
export function programWriter() {
  const lines = [];
  const exprs = new Map();
  return {
    add(expr) { const id = 'n' + (lines.length + 1); lines.push(id + ' = ' + expr); exprs.set(id, expr); return id; },
    expr(id) { return exprs.get(id) || ''; },
    done(children) { return children.length ? ['root = Answer([' + children.join(', ') + '])', ...lines].join('\n') : ''; }
  };
}

// The lead: its first sentence is the headline when it is short enough to read as one, and the
// rest stays the paragraph it was. A sentence ends at . ! ? before a capital or a bold mark.
const SENTENCE_END = /[.!?](?:\*\*)?\s+(?=\*\*|[A-Z0-9£$€])/g;
export function headlineOf(text) {
  const s = String(text || '').trim();
  if (!s || s.length <= 240) return { head: s, rest: '' };
  for (const m of s.matchAll(SENTENCE_END)) {
    const end = m.index + m[0].trimEnd().length;
    if (end < 40) continue;
    if (end > 260) break;
    // Bold opened in the headline is closed there too, or the marks would split across blocks.
    const head = s.slice(0, end).trim();
    if ((head.match(/\*\*/g) || []).length % 2) continue;
    return { head: head, rest: s.slice(end).trim() };
  }
  // One long sentence: its opening clause, up to the first colon or semicolon, is the headline
  // ("Three certificates lapse within 30 days, two of them life-safety: Bishopsgate's …").
  const clause = /^([^:;]{40,240}?)[:;]\s+(\S[\s\S]*)$/.exec(s);
  if (clause && (clause[1].match(/\*\*/g) || []).length % 2 === 0) {
    const rest = clause[2].trim();
    return { head: clause[1].trim() + '.', rest: rest.charAt(0).toUpperCase() + rest.slice(1) };
  }
  return { head: '', rest: s };
}

// A block as Markdown again, for Text.
function blockMarkdown(b) {
  if (b.t === 'p') return b.body;
  if (b.t === 'quote') return '> ' + b.body;
  if (b.t === 'code') return '```\n' + b.body + '\n```';
  if (b.t === 'h') return '#'.repeat(Math.min(6, Math.max(1, b.level || 4))) + ' ' + b.body;
  if (b.t === 'list') {
    // Each item keeps the marker it was written with: renumbering by position turned a sub-bullet
    // under "1." into "2." and pushed "3." to 5 (27 Oct 2026).
    return (b.items || []).map((it, j) => ' '.repeat((b.indents || [])[j] || 0)
      + ((b.marks || [])[j] || (b.ordered ? ((b.start || 1) + j) + '.' : '-')) + ' ' + it).join('\n');
  }
  if (b.t === 'table') {
    const row = (r) => '| ' + r.map((c) => String(c).replace(/\|/g, '\\|')).join(' | ') + ' |';
    return [row(b.head), row(b.head.map(() => '---')), ...b.rows.map(row)].join('\n');
  }
  return '';
}

// A plain number as written in a table cell: "7", "1,200", "86.12", "-0.73". Units, currency and
// words are not: a cell like that stays text, in a table.
const PLAIN = /^[-+]?\d[\d,]*(?:\.\d+)?$/;
const asNumber = (s) => Number(String(s).replace(/,/g, ''));
const PERIOD = /^(period|month|week|day|date|year|quarter|time|hour)s?$/i;

// A figure as a table writes it: 23.44/25, £1,200, 91%, 12 days, 241.0 kWh/m². A signed figure
// (a change, "-0.73") is not a length, so it is never drawn as a bar.
const FIGURE = /^(?:[£$€]\s?)?\d[\d,]*(?:\.\d+)?(?:\s?(?:%|\/\s?\d+(?:\.\d+)?|days?|hrs?|h|kWh(?:\/m²)?|kW|MWh|m²|pts?|jobs?|°C))?$/i;
const MEASURE_FIRST = /score|total|overall|count|overdue|cost|spend|value|days|kwh|eui|energy|rate|%/i;
const EMPTY_CELL = (v) => !v || v === '—' || v === '-';
// A label a chart can show as written: no link or code marks (those stay in the table).
const PLAIN_LABEL = (l) => !!l && !/\]\(|`|<|\*/.test(l);

// 27 Oct 2026: a column that names or orders things (an id, a code, a year, a rank, a phone
// number) is not a measure. As bars, "1998" read as a length of 1,998, "004120" lost its zeros,
// and the last-ranked vendor got the longest bar. Its header says so, by a word anywhere ("WO
// id", "Phone number") or by its head noun, the last word with any bracketed unit aside ("Install
// year", "Cost centre", "Work order"); after "per" or "this" that noun is a measure's span again
// ("Spend this year", "Cost per job"), and "number of" is a count. Its values say so too: a
// leading zero is a code, and a column of years is years.
const ID_WORD = /\b(?:ids?|codes?|ref|reference|phone|tel|telephone|postcode|serial|rank|ranking)\b|\b(?:no|number)\b(?!\.?\s+of\b)/i;
const ID_NOUN = /^(?:year|date|centre|center|order|job|wo|po|invoice|tag|floor|priority|position|grade|tier|band|built|opened|account)$/i;
const SPAN = /^(?:per|this|last|next|each|a|the|that)$/i;
const YEAR = /^(?:1[5-9]|20)\d\d$/;
function notAMeasure(head, vals) {
  const h = String(head || '').replace(/\([^)]*\)|\[[^\]]*\]/g, ' ').replace(/[.:]+\s*$/, '').trim();
  const ws = h.split(/[\s/]+/).filter(Boolean);
  const noun = ws[ws.length - 1] || '';
  const spanned = ws.length > 1 && SPAN.test(ws[ws.length - 2]);
  const filled = vals.filter((v) => !EMPTY_CELL(v));
  return ID_WORD.test(h) || (ID_NOUN.test(noun) && !spanned)
    || filled.some((v) => /^0\d/.test(v)) || (filled.length > 0 && filled.every((v) => YEAR.test(v)));
}

// The unit a figure is written in: the figure itself goes and the rest stays, so "23.44/25" is
// "#/25" and "8/10" is "#/10" — fractions of different wholes are not one measure (27 Oct 2026:
// 4.5/5 drew shorter than 23.44/25).
const unitOf = (v) => v.replace(/\d[\d,]*(?:\.\d+)?/, '#').replace(/\s/g, '');

// The column of a records table worth seeing as bars beside it: every filled cell one figure in
// one unit, at least three of them, and not all the same. A column named like a measure ("Overall
// score", "Days overdue", "Spend") is preferred; otherwise the first such column.
function measureColumn(head, cellsOf, rows) {
  if (head.length < 3 || rows.length < 3 || rows.length > 15) return -1;
  if (!cellsOf(0).every(PLAIN_LABEL)) return -1;
  const found = [];
  for (let j = 1; j < head.length; j++) {
    if (!head[j] || notAMeasure(head[j], cellsOf(j))) continue;
    const filled = cellsOf(j).filter((v) => !EMPTY_CELL(v));
    if (filled.length < 3 || filled.length < rows.length - 1) continue;
    if (!filled.every((v) => FIGURE.test(v))) continue;
    if (new Set(filled.map(unitOf)).size > 1 || new Set(filled).size < 2) continue;
    found.push(j);
  }
  const pref = found.find((j) => MEASURE_FIRST.test(head[j]));
  return pref !== undefined ? pref : found.length ? found[0] : -1;
}

// A period label read as a point in time: [kind, key]. Labels of one kind compare by key; a label
// this cannot read gives null. "Oct 2026", "October 26", "Jul", "2026-10", "2026-10-01",
// "28 Oct 2026", "28/10/2026", "Q3 2026", "2026 Q3", "2026", "FY2026", "W3", "Week 3", "Mon",
// "09:00".
const MONTHS = ['january', 'february', 'march', 'april', 'may', 'june', 'july', 'august', 'september', 'october', 'november', 'december'];
const WEEKDAYS = ['monday', 'tuesday', 'wednesday', 'thursday', 'friday', 'saturday', 'sunday'];
const nameIn = (names, w) => { const i = names.findIndex((n) => w.length >= 3 && n.startsWith(w)); return i < 0 ? 0 : i + 1; };
const fullYear = (y) => (y.length === 2 ? 2000 + Number(y) : Number(y));
function periodKey(label) {
  const s = String(label || '').trim().toLowerCase().replace(/\s+/g, ' ');
  let m;
  if ((m = /^(\d{4})-(\d{1,2})(?:-(\d{1,2}))?$/.exec(s))) return [m[3] ? 'day' : 'month', +m[1] * 400 + +m[2] * 32 + (+m[3] || 0)];
  if ((m = /^(\d{1,2})\/(\d{1,2})\/(\d{4}|\d{2})$/.exec(s))) return ['day', fullYear(m[3]) * 400 + +m[2] * 32 + +m[1]];
  if ((m = /^(\d{1,2})(?:st|nd|rd|th)? ([a-z]+)\.?(?: '?(\d{4}|\d{2}))?$/.exec(s)) && nameIn(MONTHS, m[2])) {
    return [m[3] ? 'day' : 'day of year', (m[3] ? fullYear(m[3]) : 0) * 400 + nameIn(MONTHS, m[2]) * 32 + +m[1]];
  }
  if ((m = /^([a-z]+)\.?(?: '?(\d{4}|\d{2}))?$/.exec(s)) && nameIn(MONTHS, m[1])) {
    return [m[2] ? 'month' : 'month of year', (m[2] ? fullYear(m[2]) : 0) * 400 + nameIn(MONTHS, m[1]) * 32];
  }
  if ((m = /^q([1-4])(?: (\d{4}))?$/.exec(s))) return [m[2] ? 'quarter' : 'quarter of year', (+m[2] || 0) * 4 + +m[1]];
  if ((m = /^(\d{4}) ?q([1-4])$/.exec(s))) return ['quarter', +m[1] * 4 + +m[2]];
  if ((m = /^(?:fy ?)?(\d{4})$/.exec(s))) return ['year', +m[1]];
  if ((m = /^(?:w|wk|week) ?(\d{1,2})(?: (\d{4}))?$/.exec(s))) return [m[2] ? 'week' : 'week of year', (+m[2] || 0) * 64 + +m[1]];
  if ((m = /^([a-z]+)$/.exec(s)) && nameIn(WEEKDAYS, m[1])) return ['weekday', nameIn(WEEKDAYS, m[1])];
  if ((m = /^(\d{1,2}):(\d{2})$/.exec(s))) return ['time', +m[1] * 60 + +m[2]];
  // A plain ordinal under a period header: hour 9, day 14, week 3.
  if ((m = /^(\d{1,3})$/.exec(s))) return ['ordinal', +m[1]];
  return null;
}

// The rows in time order, oldest first: as written when they run forward, reversed when they run
// back. Null when a label cannot be read or the labels are out of order (27 Oct 2026: a table
// written newest first drew a rise as a fall, its oldest figure marked as the latest).
function periodOrder(labels) {
  const keys = labels.map(periodKey);
  if (keys.some((k) => !k) || new Set(keys.map((k) => k[0])).size > 1) return null;
  const v = keys.map((k) => k[1]);
  const idx = labels.map((_, i) => i);
  if (v.every((x, i) => i === 0 || x > v[i - 1])) return idx;
  if (v.every((x, i) => i === 0 || x < v[i - 1])) return idx.reverse();
  return null;
}

// The unit a column's header names: its bracketed unit ("Spend (£)", "SLA met (%)"), or a unit
// word in it ("Electricity kWh").
function headUnit(h) {
  const m = /\(([^)]*)\)/.exec(h);
  if (m) return m[1].trim().toLowerCase();
  const u = /£|\$|€|%|kwh\/m²|kwh|mwh|\bkw\b|m²|°c|\bdays?\b|\bhours?\b|\bhrs?\b|\bpts?\b/i.exec(h);
  return u ? u[0].toLowerCase().replace(/s$/, '') : '';
}

// A period table's figures as lines over time, above the table, which stays whole: a line shows
// only its last figure until it is hovered (27 Oct 2026: "Spend (£)" and "SLA met (%)" shared one
// 0–15,000 axis and the table was gone). Only measures are lines, and only in one unit: columns
// whose headers name different units, or whose sizes are more than ten times apart, are not drawn
// together; the first measure-like one is drawn alone.
function periodExprs(w, head, cellsOf, rows, title, table) {
  if (head.length > 6 || rows.length < 3) return [table];
  const order = periodOrder(cellsOf(0));
  if (!order) return [table];
  const cand = [];
  for (let j = 1; j < head.length; j++) {
    const vals = cellsOf(j);
    const filled = vals.filter((v) => !EMPTY_CELL(v));
    if (!head[j] || filled.length < 2 || !filled.every((v) => PLAIN.test(v)) || notAMeasure(head[j], vals)) continue;
    cand.push({ j: j, max: Math.max(...filled.map((v) => Math.abs(asNumber(v)))) });
  }
  if (!cand.length) return [table];
  const biggest = Math.max(...cand.map((c) => c.max));
  const oneUnit = new Set(cand.map((c) => headUnit(head[c.j]))).size === 1 && cand.every((c) => c.max * 10 >= biggest);
  const kept = oneUnit ? cand : [cand.find((c) => MEASURE_FIRST.test(head[c.j])) || cand[0]];
  const labels = cellsOf(0);
  const series = kept.map(({ j }) => {
    const vals = cellsOf(j);
    // The figures as the answer writes them ("12,400"): the chart reads each for its height and
    // prints it unchanged.
    return w.add('Series(' + lit(head[j]) + ', [' + order.map((i) => (PLAIN.test(vals[i]) ? lit(vals[i]) : 'null')).join(', ') + '])');
  });
  return ['LineChart(' + lit(title || kept.map(({ j }) => head[j]).join(' and ')) + ', [' + order.map((i) => lit(labels[i])).join(', ') + '], [' + series.join(', ') + '])', table];
}

// A table of figures is drawn as the chart it describes:
//   item | value                 (3 to 15 items, every value a positive number) → BarChart
//   period | series… | series     (3 or more periods in time order, numbers)    → LineChart, then the table
//   records with a column of figures                                             → its bars, then the table
// Anything else is a table. Returns the expressions in reading order. A chart stands in for its
// table only when it shows every cell as written: the bars carry each figure as its own string
// (27 Oct 2026: 0.1875 was drawn as 0.19), and the BarChart shows it verbatim.
function tableExprs(w, b, title) {
  const head = b.head.map(unbold);
  const rows = b.rows.filter((r) => r.some((c) => String(c).trim()));
  const width = head.length;
  const cellsOf = (j) => rows.map((r) => unbold(r[j] == null ? '' : r[j]));
  // A row with more cells than the header keeps them all, under unnamed columns.
  const wide = rows.some((r) => r.length > width);
  const cols = b.head.concat(Array(Math.max(0, ...rows.map((r) => r.length - width))).fill(''));
  const pad = (r) => cols.map((_, j) => lit(r[j] == null ? '' : String(r[j]).trim()));
  const table = 'Table([' + cols.map((h) => lit(h)).join(', ') + '], [' + rows.map((r) => '[' + pad(r).join(', ') + ']').join(', ') + '])';
  // A table over time is never bars: its line, if it reads in time order, then the table.
  if (PERIOD.test(head[0])) return wide ? [table] : periodExprs(w, head, cellsOf, rows, title, table);
  if (width === 2 && !wide && rows.length >= 3 && rows.length <= 15 && cellsOf(0).every(PLAIN_LABEL)) {
    const vals = cellsOf(1);
    if (vals.every((v) => PLAIN.test(v) && asNumber(v) >= 0) && !notAMeasure(head[1], vals)) {
      // The value column's name stays with the chart unless it is the rendition's own "Value".
      const measure = /^values?$/i.test(head[1]) ? '' : head[1];
      const label = title && measure && title.toLowerCase().indexOf(measure.toLowerCase()) < 0
        ? title + ' · ' + measure : title || measure || head[0];
      return ['BarChart(' + lit(label) + ', [' + cellsOf(0).map(lit).join(', ') + '], [' + vals.map(lit).join(', ') + '])'];
    }
  }
  // The bars repeat one column of the table, which stays whole beneath them: a value a bar cannot
  // read (a dash) keeps its row with no bar, never a bar at 0.
  const j = measureColumn(head, cellsOf, rows);
  if (j < 0) return [table];
  const vals = cellsOf(j).map((v) => (EMPTY_CELL(v) ? 'null' : lit(v)));
  return ['BarChart(' + lit(head[j]) + ', [' + cellsOf(0).map(lit).join(', ') + '], [' + vals.join(', ') + '])', table];
}

// The email the server's rendition writes for an EmailDraft (agents/openui_view.py), read back:
// a code block opening with Subject: and To: lines.
function emailOf(body) {
  const m = /^Subject:\s*(.*)\nTo:\s*(.*)\n(?:Cc:\s*(.*)\n)?(?:Email body:\n|\n)?([\s\S]*)$/.exec(String(body || ''));
  if (!m) return null;
  return 'EmailDraft(' + lit(m[1].trim()) + ', ' + lit(m[2].trim()) + ', ' + lit(m[4].replace(/^\n+/, '')) + (m[3] ? ', ' + lit(m[3].trim()) : '') + ')';
}

// "Ask next: one question · another" (the rendition of FollowUps) → the chips. The colon is what
// makes it the label: "Ask next week for the schedule." is a sentence, and a bare "Ask next:" or
// "**Ask next:**" has no question to make a chip of (27 Oct 2026: chips read ":" and "**").
const ASK_NEXT = /^\s*(?:\*\*)?ask next(?:\*\*)?\s*:\s*(?:\*\*)?\s*(.*?)\s*$/i;
function followUpsOf(text) {
  const m = ASK_NEXT.exec(String(text || ''));
  if (!m || !/[A-Za-z0-9]/.test(m[1]) || m[1].indexOf('\n') > -1) return null;
  const qs = m[1].split(/\s+·\s+/).map((q) => q.trim()).filter(Boolean);
  return qs.length ? 'FollowUps([' + qs.map(lit).join(', ') + '])' : null;
}

// One numbered thing to do. A bold title set off from its detail ("**Book the inspection** — The
// certificate expires Friday.", "**Book it:** today", "**Book it.** Today") keeps the two apart.
// A bold opening that runs on as one sentence ("**Reassess the FRA now** (FRA-1) and close…")
// is the title whole, its bold drawn as the highlight.
const STEP_SPLIT = /^\s*\*\*([^*]+?)\*\*(?:\s*[:.]\s*|\s+[—–-]\s+)([\s\S]+)$/;
const STEP_SPLIT_INSIDE = /^\s*\*\*([^*]+?[:.])\*\*\s+([\s\S]+)$/;
function stepExpr(w, a) {
  const m = STEP_SPLIT.exec(a.text) || STEP_SPLIT_INSIDE.exec(a.text);
  const title = m ? m[1].replace(/:$/, '').trim() : a.text.trim();
  const detail = [m ? m[2].trim() : '', ...a.subs].filter(Boolean).join(' · ');
  return w.add('Step(' + lit(title) + (detail ? ', ' + lit(detail) : '') + ')');
}

// The label some answers put on their opening line ("**Headline:** …", "**Summary:** …"): the
// Headline component says it, so the label goes and the sentence stays.
const LEAD_LABEL = /^\s*\*\*(?:headline|summary|bottom line|tl;?dr|in short|answer)\s*:\s*\*\*\s*|^\s*\*\*(?:headline|summary|bottom line|tl;?dr|in short|answer)\*\*\s*:\s*/i;
const unlabel = (s) => String(s || '').replace(LEAD_LABEL, '');

// A paragraph that is one labelled action: "**What to do:** Review Apex's misses first." Its label
// is the section title and its sentence the step.
const ACTION_PARA = /^\s*\*\*((?:what to do|next steps?|recommended actions?|recommendations?|actions?|to do)\s*):?\*\*:?\s+([\s\S]+)$/i;
function actionParaExpr(w, text) {
  const m = ACTION_PARA.exec(String(text || ''));
  if (!m) return null;
  const said = m[2].trim();
  const step = w.add('Step(' + lit(said.charAt(0).toUpperCase() + said.slice(1)) + ')');
  return w.add('Section(' + lit(m[1].replace(/:\s*$/, '').trim()) + ', [' + w.add('Steps([' + step + '])') + '])');
}

// Paragraphs as Text, a labelled action among them as its step, in their order.
function proseExprs(w, paras) {
  const out = [];
  let run = [];
  const flush = () => { if (run.length) { out.push(w.add('Text(' + lit(run.join('\n\n')) + ')')); run = []; } };
  for (const para of paras) {
    const action = ACTION_PARA.test(para) ? para : null;
    if (action) { flush(); out.push(actionParaExpr(w, action)); } else run.push(para);
  }
  flush();
  return out;
}

function leadExprs(w, para) {
  const { head, rest } = headlineOf(unlabel(para));
  const out = [];
  if (head) out.push(w.add('Headline(' + lit(head) + ')'));
  if (rest) out.push(w.add('Text(' + lit(rest) + ')'));
  return out;
}

// What a section holds, as blocks. Plain blocks that sit together are one Text, the way they were
// one card before.
function partsExprs(w, s, title, lead) {
  const out = [];
  let prose = [];
  const flush = () => { if (prose.length) { out.push(w.add('Text(' + lit(prose.join('\n\n')) + ')')); prose = []; } };
  // A chart takes its section's title when it opens the section ("**Overdue by trade**" over a
  // table of trades); elsewhere it is named by its own columns.
  const opening = s.parts.find((p) => !(p.t === 'block' && p.block.t === 'rule'));
  for (const [i, p] of s.parts.entries()) {
    const chartTitle = p === opening ? title : '';
    if (lead && i === 0 && p.t === 'block' && p.block.t === 'p') {
      out.push(...leadExprs(w, p.block.body));
    } else if (p.t === 'kpis') {
      flush();
      for (let i = 0; i < p.kpis.length; i += 6) {
        const items = p.kpis.slice(i, i + 6).map((k) => {
          // A good-news label over none of it is not good news: "Compliant buildings: 0 of 12"
          // and "Completed on time: 0%" were green (27 Oct 2026).
          const t = toneOf(k.label);
          const tone = t === 'ok' && ZERO.test(String(k.value).trim()) ? undefined : TONE[t];
          return w.add('Metric(' + lit(k.label) + ', ' + lit(k.value) + (k.sub || tone ? ', ' + (k.sub ? lit(k.sub) : 'null') : '') + (tone ? ', ' + lit(tone) : '') + ')');
        });
        out.push(w.add('Metrics([' + items.join(', ') + '])'));
      }
    } else if (p.t === 'entities') {
      flush();
      for (const e of p.entities) {
        const fields = e.fields.map((f) => w.add('Field(' + lit(f.key) + ', ' + lit(f.value) + ')'));
        out.push(w.add('Details(' + lit(e.title) + ', [' + fields.join(', ') + '])'));
      }
    } else if (p.t === 'actions') {
      flush();
      out.push(w.add('Steps([' + p.actions.filter((a) => a.text.trim()).map((a) => stepExpr(w, a)).join(', ') + '])'));
    } else if (p.block.t === 'table') {
      flush();
      out.push(...tableExprs(w, p.block, chartTitle).map((e) => w.add(e)));
    } else if (p.block.t === 'code' && emailOf(p.block.body)) {
      flush();
      out.push(w.add(emailOf(p.block.body)));
    } else if (p.block.t === 'quote') {
      flush();
      out.push(w.add('Callout("info", ' + lit(p.block.body) + ')'));
    } else if (p.block.t === 'p' && followUpsOf(p.block.body)) {
      flush();
      out.push(w.add(followUpsOf(p.block.body)));
    } else if (p.block.t === 'p' && ACTION_PARA.test(p.block.body)) {
      flush();
      // Untitled, the section's blocks sit in the answer itself, where the action gets its own
      // labelled section; a section cannot hold another, so inside one the step keeps its label.
      out.push(title ? w.add('Steps([' + w.add('Step(' + lit(p.block.body.trim()) + ')') + '])') : actionParaExpr(w, p.block.body));
    } else if (p.block.t !== 'rule') {
      prose.push(blockMarkdown(p.block));
    }
  }
  flush();
  return out;
}

// A section that is only a short warning in prose is drawn as the finding it is.
function calloutOf(s, title) {
  if (s.tone !== 'critical' && s.tone !== 'warning') return null;
  if (!s.parts.length || !s.parts.every((p) => p.t === 'block' && p.block.t === 'p')) return null;
  const text = s.parts.map((p) => p.block.body).join('\n\n');
  if (text.length > 420 || text.indexOf('\n') > -1) return null;
  // The tone is the heading's. A section that says there is none of it ("## Overdue work orders"
  // over "None — every work order is within its due date.") is good news, not a red finding (27
  // Oct 2026); it stays plain text, since "No response in 12 days" also opens with "No".
  if (/^\s*(?:\*\*)?(?:no|none|nothing|zero|0|nil)\b/i.test(text)) return null;
  return 'Callout(' + lit(TONE[s.tone]) + ', ' + lit(text) + (title ? ', ' + lit(title) : '') + ')';
}

// The children of Answer for an answer's text, written into `w`. Shared with fromCompliance.js,
// which draws a compliance narrative the same way.
export function markdownChildren(w, text) {
  const m = answerCards(text);
  if (!m) return [];
  const kids = [];
  const overall = m.overall.slice();
  // The closing "Ask next:" belongs to the whole answer, not to the section it was written under.
  let tailAsk = null;
  const last = m.sections[m.sections.length - 1];
  const lastPart = last && last.parts[last.parts.length - 1];
  // Under a "What to do" heading the paragraph was read as one more action.
  const lastAction = lastPart && lastPart.t === 'actions' ? lastPart.actions[lastPart.actions.length - 1] : null;
  if (lastPart && lastPart.t === 'block' && lastPart.block.t === 'p' && followUpsOf(lastPart.block.body)) {
    tailAsk = followUpsOf(lastPart.block.body);
    last.parts.pop();
  } else if (lastAction && !lastAction.subs.length && followUpsOf(lastAction.text)) {
    tailAsk = followUpsOf(lastAction.text);
    lastPart.actions.pop();
    if (!lastPart.actions.length) last.parts.pop();
  } else if (!m.sections.length && overall.length > 1 && followUpsOf(overall[overall.length - 1])) {
    tailAsk = followUpsOf(overall.pop());
  }
  if (overall.length) {
    kids.push(...leadExprs(w, overall[0]));
    kids.push(...proseExprs(w, overall.slice(1)));
  }
  for (const [i, s] of m.sections.entries()) {
    const title = [s.kicker, s.title].filter(Boolean).join(' · ');
    // An answer that opens with a title ("# Weekly summary") has its lead paragraph under it.
    const lead = i === 0 && !overall.length;
    const callout = lead ? null : calloutOf(s, title);
    if (callout) { kids.push(w.add(callout)); continue; }
    const inner = partsExprs(w, s, title, lead);
    // A heading with nothing drawable under it (only a rule) is still the answer's words.
    if (!inner.length) { if (title) kids.push(w.add('Text(' + lit('**' + title + '**') + ')')); continue; }
    // A chart already carries the section's title; a section around it would say it twice.
    // A line over time is followed by its table, which says no title of its own.
    const named = (kind) => w.expr(inner[0]).startsWith(kind + '(' + lit(title).slice(0, -1));
    const solo = (inner.length === 1 && (named('BarChart') || named('LineChart')))
      || (inner.length === 2 && named('LineChart') && w.expr(inner[1]).startsWith('Table('));
    if (!title || solo) kids.push(...inner);
    else kids.push(w.add('Section(' + lit(title) + ', [' + inner.join(', ') + '])'));
  }
  if (tailAsk) kids.push(w.add(tailAsk));
  return kids;
}

export function programFromMarkdown(text) {
  const w = programWriter();
  return w.done(markdownChildren(w, text));
}

// The live draft of an answer still being written, safe to draw: a table header whose divider has
// not arrived yet is held back (it would draw as a raw pipe row), and an unclosed "**" is dropped
// (it would draw as two asterisks) until its partner streams in (27 Oct 2026). A finished answer
// comes back unchanged. Only for the streaming draft; the finished turn is drawn from its own text.
export function draftSafe(text) {
  const lines = String(text == null ? '' : text).split('\n');
  let end = lines.length;
  while (end > 0 && !lines[end - 1].trim()) end -= 1;
  let start = end;
  while (start > 0 && /^\s*\|/.test(lines[start - 1])) start -= 1;
  const run = lines.slice(start, end);
  let out = run.length && !run.some(isDivider) ? lines.slice(0, start).join('\n') : lines.join('\n');
  // Bold pairs left to right, so the odd one out is the last.
  if ((out.match(/\*\*/g) || []).length % 2) {
    const k = out.lastIndexOf('**');
    out = out.slice(0, k) + out.slice(k + 2);
  }
  return out;
}
