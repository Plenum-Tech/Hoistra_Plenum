// reportCharts — the numbers behind the pictures on a report card.
//
// The answer arrives as prose plus a few structured lists. Read as sentences it takes a
// minute to learn what one glance could have said: that a life-safety duty has been
// unevidenced for twenty years, that three of six failures share one source document, that
// two of them belong to no building at all. These are the pure functions that turn the
// payload into something a chart can draw.
//
// GROUNDED ONLY. Every series here comes from a real field — `overdue.buildings[].days` is a
// number, `certificates[].status` and `.company` are strings the engine set. Nothing is
// parsed out of a prose sentence: an expiry year scraped from a `reason` string would render
// as a precise dot on a time axis while being a guess, and on a compliance page a confident
// wrong picture is worse than the paragraph it replaced. Duration comes from `days`, which
// is given, not from the dates written in the text.
//
// Tested in test/reportCharts.test.mjs.

const arr = (v) => (Array.isArray(v) ? v : []);
const num = (v) => (typeof v === 'number' && isFinite(v) ? v : null);
const txt = (v) => String(v == null ? '' : v).trim();

export const TONE = {
  critical: 'var(--st-risk)', warning: 'var(--st-warn)', ok: 'var(--st-ok)',
  info: 'var(--color-accent)', none: 'var(--color-neutral-500)'
};
export const toneOf = (severity) => TONE[String(severity || '').toLowerCase()] || TONE.none;

// A year is the unit a reader thinks in. 7288 days is "twenty years", and that is the whole
// finding; 7288 is a number they have to convert first.
export const yearsFrom = (days) => (num(days) === null ? null : days / 365.25);

export function yearsLabel(days) {
  const y = yearsFrom(days);
  if (y === null) return '';
  if (y >= 1.5) return Math.round(y) + ' yrs';
  if (y >= 0.9) return '1 yr';
  const m = Math.round(y * 12);
  return m <= 0 ? 'today' : m + (m === 1 ? ' mo' : ' mo');
}

// The engine labels an overdue row "Building — AN Other (Holdings) Limited". The prefix is
// the same on every row, so it is noise in a chart where every row is a building.
export function cleanLabel(label) {
  return txt(label).replace(/^\s*(building|vendor|site)\s*[—–-]\s*/i, '') || txt(label);
}

// The duration series — the hero. One row per overdue duty, longest first, with its share of
// the worst so a bar can be drawn without a second pass. `pct` on the payload is the engine's
// own and is kept when present; otherwise it is computed against the longest row.
export function durationSeries(rich) {
  const rows = arr(rich && rich.overdue && rich.overdue.buildings)
    .concat(arr(rich && rich.overdue && rich.overdue.vendors))
    .map((r) => ({ label: cleanLabel(r.label), days: num(r.days), severity: r.severity, rawPct: r.pct }))
    .filter((r) => r.days !== null && r.days > 0)
    .sort((a, b) => b.days - a.days);
  if (!rows.length) return [];
  const worst = rows[0].days;
  return rows.map((r) => ({
    label: r.label,
    days: r.days,
    years: yearsFrom(r.days),
    yearsLabel: yearsLabel(r.days),
    pct: Math.max(2, Math.round((r.days / worst) * 100)),
    color: toneOf(r.severity),
    severity: r.severity || 'none'
  }));
}

// One stacked strip: how the certificates in scope divide by status. Ordered worst-first so
// the eye lands on the exposure, not on whatever the engine happened to list first.
const STATUS_RANK = { lapsed: 0, expired: 0, overdue: 1, suspect: 1, expiring: 2, pending: 3, valid: 4, current: 4 };
const STATUS_TONE = { lapsed: 'critical', expired: 'critical', overdue: 'critical', suspect: 'warning', expiring: 'warning', pending: 'warning', valid: 'ok', current: 'ok' };

export function statusMix(certificates) {
  const rows = arr(certificates);
  if (!rows.length) return [];
  const by = {};
  rows.forEach((c) => {
    const k = txt(c.status) || 'Unknown';
    by[k] = (by[k] || 0) + 1;
  });
  const total = rows.length;
  return Object.keys(by)
    .sort((a, b) => (STATUS_RANK[a.toLowerCase()] ?? 9) - (STATUS_RANK[b.toLowerCase()] ?? 9) || by[b] - by[a])
    .map((status) => ({
      status: status,
      n: by[status],
      pct: (by[status] / total) * 100,
      color: toneOf(STATUS_TONE[status.toLowerCase()] || 'none')
    }));
}

// Who holds them. The signal this carries is CONCENTRATION — several failures against one
// holder is a different problem from one each — and, when the engine could not attribute a
// row, how much of the register belongs to nobody.
const UNATTRIBUTED = /^(not recorded|unknown|unattributed|n\/?a|-|—)?$/i;

export function holderMix(certificates) {
  const rows = arr(certificates);
  if (!rows.length) return [];
  const by = {};
  rows.forEach((c) => {
    const raw = txt(c.company);
    const k = UNATTRIBUTED.test(raw) ? 'Unattributed' : raw;
    by[k] = (by[k] || 0) + 1;
  });
  const total = rows.length;
  const max = Math.max.apply(null, Object.keys(by).map((k) => by[k]));
  return Object.keys(by)
    .sort((a, b) => by[b] - by[a] || a.localeCompare(b))
    .map((company) => ({
      company: company,
      n: by[company],
      pct: Math.max(3, Math.round((by[company] / max) * 100)),
      share: (by[company] / total) * 100,
      unattributed: company === 'Unattributed',
      // A holder with more than one failing duty is the concentration worth seeing.
      concentrated: by[company] > 1
    }));
}

// A KPI is a count, sometimes against a total the reader can infer from the rest of the
// payload. Where there is no meaningful denominator the ring is not drawn — a full circle
// that means nothing is a decoration, not a chart.
export function kpiDial(kpi, rich) {
  const count = num(kpi && kpi.count);
  if (count === null) return { count: kpi && kpi.count, pct: null, color: TONE.none };
  const certs = arr(rich && rich.certificates).length;
  const denom = certs > 0 && count <= certs ? certs : null;
  const tone = count === 0 ? 'ok' : (kpi.severity || 'critical');
  return {
    count: count,
    denom: denom,
    pct: denom ? Math.round((count / denom) * 100) : null,
    color: toneOf(tone)
  };
}

// ── charts out of plain markdown ─────────────────────────────────────────────
// Not every engine answers with the compliance payload. Ask "what needs my approval today"
// and the orchestrator replies in markdown: a bullet list of figures and a wide table. That
// answer is just as chartable — the figures ARE a bar chart and the table's categorical
// columns ARE distributions — but only if something reads them out of the text. Without
// this, every non-compliance report falls back to a wall of prose, which is the whole
// complaint.

// "- **Critical Priority:** 6 work orders" → {label: "Critical Priority", value: 6, unit: "work orders"}
const FIGURE_LINE = /^\s*[-*+]?\s*(?:\*\*|__)?\s*([^:*_|]{2,60}?)\s*(?:\*\*|__)?\s*:\s*(?:\*\*)?\s*([\d][\d,]*(?:\.\d+)?)\s*(?:\*\*)?\s*([^\n]{0,24})$/;

// A date is not a quantity. "Expiry: 2027-03-01" and "Issued: 2019-02-02" were being drawn
// as bars of height 2027 and 2019 — a chart that is not merely useless but wrong.
const LOOKS_LIKE_DATE = /^\s*\d{4}[-/]\d{1,2}([-/]\d{1,2})?|^\s*\d{1,2}[-/]\d{1,2}[-/]\d{2,4}/;
const DATE_LABEL = /\b(date|expiry|expires?|issued|created|updated|due|start|end|since|as of)\b/i;

export function parseFigures(text) {
  const out = [];
  String(text == null ? '' : text).split(/\r?\n/).forEach((line) => {
    if (/^\s*\|/.test(line)) return;                       // table rows are not figures
    const m = FIGURE_LINE.exec(line);
    if (!m) return;
    const rawValue = String(m[2]);
    // A thousands separator is a comma with three digits behind it; "3,5 %" is a decimal
    // comma and stripping it silently turned 3.5 into 35.
    if (/,\d{1,2}(?!\d)/.test(rawValue)) return;
    const rest = rawValue + ' ' + (m[3] || '');
    if (LOOKS_LIKE_DATE.test(rest) || DATE_LABEL.test(m[1])) return;
    const value = Number(rawValue.replace(/,/g, ''));
    if (!isFinite(value)) return;
    const label = m[1].trim();
    if (!label || /^https?$/i.test(label)) return;
    out.push({ label: label, value: value, unit: (m[3] || '').trim().replace(/[.*_]+$/, '') });
  });
  if (out.length < 2) return [];                            // one number is a sentence, not a chart
  const max = out.reduce((m, f) => Math.max(m, f.value), 0) || 1;
  return out.map((f) => Object.assign({}, f, { pct: Math.max(2, Math.round((f.value / max) * 100)) }));
}

// A GitHub-flavoured markdown table → {headers, rows}. Returns every table in the text.
export function parseMarkdownTables(text) {
  const lines = String(text == null ? '' : text).split(/\r?\n/);
  const cells = (line) => line.trim().replace(/^\||\|$/g, '').split('|').map((c) => c.trim());
  const tables = [];
  for (let i = 0; i < lines.length; i++) {
    if (!/^\s*\|/.test(lines[i]) || !/^\s*\|?[\s:-]*-[\s:|-]*$/.test(lines[i + 1] || '')) continue;
    const headers = cells(lines[i]);
    const rows = [];
    let j = i + 2;
    while (j < lines.length && /^\s*\|/.test(lines[j])) {
      const r = cells(lines[j]);
      if (r.length) rows.push(r);
      j++;
    }
    if (rows.length) tables.push({ headers: headers, rows: rows });
    i = j - 1;
  }
  return tables;
}

// Columns worth drawing. A column of ids, emails, timestamps or free-text descriptions has a
// distinct value per row — charting it draws ten bars of one, which says nothing. A column
// that repeats is a category, and its shape is the finding ("nine of ten are the same
// chiller").
// Two tiers, because they are not equally worth the reader's eye. What something IS
// (priority, status, severity) is the column a person acts on; what it is ATTACHED to
// (asset, vendor, location) is context. A tie on distinct-count should not let the context
// column push the decision column off the page.
const DECISION_HINT = /priority|status|severity|state|risk|urgency|criticality|outcome|result/i;
const CONTEXT_HINT = /type|category|asset|vendor|location|site|building|owner|assignee|team|trade|discipline|scope|source/i;
const NOISE_HINT = /^(id|.*\bid\b.*|.*e-?mail.*|.*\bat\b|date|time|created|updated|description|notes?|comment)/i;

// How many distinct values a column really holds, uncapped — chartableColumns needs the
// true figure to judge whether the column is a category at all.
export function distinctCount(table, index) {
  const rows = (table && table.rows) || [];
  const seen = {};
  let n = 0;
  rows.forEach((r) => {
    const v = txt(r[index]) || '—';
    if (!seen[v]) { seen[v] = 1; n += 1; }
  });
  return n;
}

export function columnDistribution(table, index) {
  const rows = (table && table.rows) || [];
  const by = {};
  rows.forEach((r) => {
    const v = txt(r[index]) || '—';
    by[v] = (by[v] || 0) + 1;
  });
  const keys = Object.keys(by);
  if (!keys.length) return [];
  const max = Math.max.apply(null, keys.map((k) => by[k]));
  return keys
    .sort((a, b) => by[b] - by[a] || a.localeCompare(b))
    .slice(0, 8)
    .map((value) => ({
      value: value, n: by[value],
      pct: Math.max(3, Math.round((by[value] / max) * 100)),
      share: (by[value] / rows.length) * 100
    }));
}

export function chartableColumns(table, limit) {
  const headers = (table && table.headers) || [];
  const rows = (table && table.rows) || [];
  if (rows.length < 3) return [];
  const scored = [];
  headers.forEach((h, i) => {
    const dist = columnDistribution(table, i);
    // The TRUE distinct count, not the charted top-8. Reading it off the sliced list capped
    // it at 8, so the "too many distinct values to be a category" guard below could never
    // fire on a table of 14 rows or more — and an id or free-text column was charted as
    // eight bars of one.
    const distinct = distinctCount(table, i);
    if (distinct < 2 || distinct > Math.max(2, Math.floor(rows.length * 0.6))) return;
    const head = txt(h);
    // "Asset ID" / "Vendor ID" matched the context hint and escaped the id rule; an explicit
    // id suffix is noise whatever else the header says.
    if (/\bid\b|_id$/i.test(head)) return;
    if (NOISE_HINT.test(head) && !DECISION_HINT.test(head) && !CONTEXT_HINT.test(head)) return;
    // Fewer distinct values reads faster; what a row IS outranks what it is attached to.
    const tier = DECISION_HINT.test(head) ? 200 : CONTEXT_HINT.test(head) ? 100 : 0;
    scored.push({ index: i, header: head || 'Column ' + (i + 1), dist: dist, score: tier - distinct });
  });
  return scored.sort((a, b) => b.score - a.score).slice(0, limit || 2);
}

// Everything a report's charts need, in one pass.
export function chartsFor(rich) {
  if (!rich) return { duration: [], status: [], holders: [], worst: null };
  const duration = durationSeries(rich);
  return {
    duration: duration,
    status: statusMix(rich.certificates),
    holders: holderMix(rich.certificates),
    // The single row that carries the headline — what the hero annotation points at.
    worst: duration.length ? duration[0] : null
  };
}
