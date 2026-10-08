// reportCards — a report's answer, broken into cards a person can curate.
//
// A refreshed card carries one answer. Rendered as one long column it reads as a letter:
// you cannot see the shape of it, and you cannot put the part you do not care about away.
// This module turns that answer into discrete cards — one per KPI, per owner group, per
// action, per insight, plus the narrative, the overdue bars, the certificate table and the
// pending list — and each one can be put in the tray and brought back.
//
// STABLE KEYS ARE THE WHOLE TRICK. A card re-runs on a cadence, so the answer is rebuilt
// from scratch every refresh: array positions move, counts change, wording shifts. A key
// derived from the position would silently hide a DIFFERENT section after the next refresh —
// in a compliance product that could hide a lapsed certificate someone meant to keep. So a
// key is derived from what the section IS (its owner, its label, its heading), never where
// it sat. A section that disappears takes nothing with it; if it comes back, it comes back
// hidden, which is what the person asked for.
//
// Pure functions only — tested in test/reportCards.test.mjs.

import { chartsFor, kpiDial, parseFigures, parseMarkdownTables, chartableColumns } from './reportCharts.js';

export const HIDDEN_KEY = 'hoistra.reportCards.hidden.v1';

// A short, stable digest of the original text. Two labels that flatten to the same ascii
// slug — "Acme Ltd." and "Acme, Ltd", or any two non-latin names, which reduce to nothing at
// all — must not end up sharing a key, because hiding one would hide the other. The digest
// is appended only when the slug alone cannot carry the difference.
function digest(s) {
  let h = 5381;
  for (let i = 0; i < s.length; i++) h = (((h << 5) + h) ^ s.charCodeAt(i)) >>> 0;
  return h.toString(36).slice(0, 6);
}

const slug = (v) => {
  const raw = String(v == null ? '' : v);
  const base = raw.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-+|-+$/g, '');
  // Nothing survived — punctuation only, or a name written in a script with no ascii at
  // all (CJK, Arabic, Cyrillic). Every one of those used to flatten to the same 'x'.
  if (!base) return raw.trim() ? 'x-' + digest(raw) : 'x';
  // Two different long names can share their first 48 characters; the digest separates them.
  if (base.length > 48) return base.slice(0, 48) + '-' + digest(raw);
  return base;
};

// A key that is unique within one answer WITHOUT being positional. A positional suffix
// ("-2") moves to a different section the moment a refresh drops or reorders one, which
// would silently hide the wrong thing; a digest of the label stays with the label.
function uniqueKey(seen, base, label) {
  if (!seen[base]) { seen[base] = 1; return base; }
  const withDigest = base + '-' + digest(String(label == null ? '' : label));
  if (!seen[withDigest]) { seen[withDigest] = 1; return withDigest; }
  // Genuinely the same label twice in one answer: only now is order the only thing left.
  seen[withDigest] += 1;
  return withDigest + '-' + seen[withDigest];
}

const arr = (v) => (Array.isArray(v) ? v : []);
const txt = (v) => String(v == null ? '' : v).trim();

// ── the answer, as cards ─────────────────────────────────────────────────────
// Each card: {key, kind, title, span, data}. `span` is how many grid columns it wants —
// a figure is small, a table is wide — and the grid honours it where there is room.
export function richCards(rich) {
  if (!rich) return [];
  const out = [];
  const ch = chartsFor(rich);
  // Two owners called "Acme Ltd." and "Acme, Ltd", two KPIs both counting 0, two groups
  // named in a non-latin script: all collided on one key, so hiding one hid the other and
  // React saw duplicate keys. Minted through uniqueKey() now, content-derived not positional.
  const seen = {};

  // The hero. Duration is the finding this data actually carries — one duty unevidenced for
  // twenty years does not read as worse than one lapsed last month until they are drawn on
  // the same axis. Grounded in `overdue[].days`, a real number, not a date scraped from prose.
  if (ch.duration.length) {
    out.push({
      key: 'duration', kind: 'duration', title: 'How long each duty has been unevidenced', span: 3,
      data: { rows: ch.duration, worst: ch.worst }
    });
  }

  arr(rich.kpis).forEach((k) => {
    out.push({
      key: uniqueKey(seen, 'kpi:' + slug(k.label || k.sublabel || k.count), k.label || k.sublabel || k.count),
      kind: 'kpi', title: txt(k.label) || 'Figure', span: 1,
      data: Object.assign({}, k, { dial: kpiDial(k, rich) })
    });
  });

  if (ch.status.length) {
    out.push({
      key: 'status', kind: 'status', title: 'The register', span: 1,
      data: { segments: ch.status, total: arr(rich.certificates).length }
    });
  }

  if (ch.holders.length > 1) {
    out.push({ key: 'holders', kind: 'holders', title: 'Who holds them', span: 1, data: { rows: ch.holders } });
  }

  if (txt(rich.narrative)) {
    out.push({ key: 'overall', kind: 'narrative', title: 'Assessment', span: 2, data: { text: txt(rich.narrative) } });
  }

  arr(rich.actions).forEach((a) => {
    out.push({
      key: uniqueKey(seen, 'action:' + slug(a.title), a.title), kind: 'action',
      title: txt(a.title) || 'Action', span: 2, data: a
    });
  });

  arr(rich.groups).forEach((g) => {
    out.push({
      key: uniqueKey(seen, 'group:' + slug(g.owner), g.owner), kind: 'group',
      title: txt(g.owner) || 'Group', span: 2,
      data: Object.assign({}, g, { offers: arr(rich.offers).filter((o) => arr(g.cert_ids).indexOf(o.cert_id) > -1 || o.owner === g.owner) })
    });
  });

  arr(rich.insights).forEach((x) => {
    out.push({
      key: uniqueKey(seen, 'insight:' + slug(x.type || 'insight') + ':' + slug(txt(x.text || x).slice(0, 40)), txt(x.text || x)),
      kind: 'insight', title: txt(x.type) || 'Insight', span: 2, data: x
    });
  });

  if (arr(rich.certificates).length) {
    out.push({
      key: 'certificates', kind: 'certificates',
      title: 'Certificates in scope · ' + rich.certificates.length, span: 3, data: { rows: rich.certificates }
    });
  }

  arr(rich.pending).forEach((p) => {
    out.push({
      key: uniqueKey(seen, 'pending:' + slug(p.name || p.cert_id), p.name || p.cert_id), kind: 'pending',
      title: txt(p.name) || 'Waiting on you', span: 2,
      data: Object.assign({}, p, { offers: arr(rich.offers).filter((o) => o.cert_id === p.cert_id) })
    });
  });

  return out;
}

// A plain markdown answer, cut into the same card shape. Headings start a section; so does a
// paragraph that opens with a bold run, which is how the orchestrator writes its per-subject
// blocks ("**AN Other House** — no valid Fire Risk Assessment…"). Everything before the first
// one is the lead.
export function markdownCards(text) {
  const body = txt(text);
  if (!body) return [];
  const lines = body.split(/\r?\n/);
  const sections = [];
  let cur = null;
  const push = () => {
    if (!cur) return;
    const content = cur.lines.join('\n').trim();
    if (content || cur.title) sections.push({ title: cur.title, content: content });
    cur = null;
  };
  lines.forEach((line) => {
    const heading = /^\s{0,3}(#{1,6})\s+(.+?)\s*#*\s*$/.exec(line);
    const boldLead = /^\s{0,3}\*\*([^*]{2,80})\*\*\s*(?:[—–:-]\s*(.*))?$/.exec(line);
    if (heading) {
      push();
      cur = { title: heading[2].trim(), lines: [] };
      return;
    }
    if (boldLead && (!cur || cur.lines.join('').trim())) {
      push();
      cur = { title: boldLead[1].trim(), lines: boldLead[2] ? [boldLead[2]] : [] };
      return;
    }
    if (!cur) cur = { title: '', lines: [] };
    cur.lines.push(line);
  });
  push();
  const real = sections.filter((s) => s.title || s.content);
  if (!real.length) return [];

  // Each section is mined for things worth drawing before it is allowed to be prose. A
  // bullet list of figures is a bar chart; a table's repeating column is a distribution.
  // Whatever is left over — the actual sentences — comes last and comes clamped.
  const out = [];
  // Content-derived, not positional: a "-2" suffix would slide onto a different section as
  // soon as a refresh dropped or reordered one, hiding the wrong thing.
  const seen = {};
  const keyFor = (base, label) => uniqueKey(seen, base, label === undefined ? base : label);

  real.forEach((s, i) => {
    const stem = slug(s.title || 'part-' + i);
    const figures = parseFigures(s.content);
    const tables = parseMarkdownTables(s.content);

    const firstChart = out.length;

    if (figures.length) {
      out.push({ key: keyFor('fig:' + stem, s.title), kind: 'figures', title: s.title || 'Figures', span: 2,
                 data: { figures: figures } });
    }

    tables.forEach((t, ti) => {
      const total = t.rows.length;
      chartableColumns(t, 3).forEach((col) => {
        out.push({
          key: keyFor('dist:' + stem + ':' + slug(col.header), (s.title || '') + '|' + col.header),
          kind: 'distribution', title: col.header, span: 1,
          data: { rows: col.dist, total: total, column: col.header }
        });
      });
      out.push({
        key: keyFor('table:' + stem + (ti ? ':' + ti : ''), s.title), kind: 'table',
        title: (s.title || 'Rows') + ' · ' + total, span: 3,
        data: { headers: t.headers, rows: t.rows }
      });
    });

    // The prose that is left once the figures and tables have been lifted out of it.
    const prose = s.content
      .split(/\r?\n/)
      .filter((line) => !/^\s*\|/.test(line) && !FIGURE_ONLY.test(line))
      .join('\n').trim();
    // A sentence or two left over next to a chart is a caption, not a card. "You have
    // several work orders pending your approval today" earns a line under the figures it
    // introduces — giving it equal weight to the chart is how a page fills with prose again.
    if (prose && prose.length <= 220 && out.length > firstChart) {
      const host = out[firstChart];
      host.data = Object.assign({}, host.data, { note: prose });
    } else if (prose) {
      out.push({ key: keyFor('md:' + stem, s.title), kind: 'markdown', title: s.title, span: 2, data: { text: prose } });
    }
  });

  if (!out.length) return [{ key: 'answer', kind: 'markdown', title: '', span: 3, data: { text: body } }];
  return out;
}

// A line that carried nothing but a figure already drawn as a bar.
const FIGURE_ONLY = /^\s*[-*+]\s*(?:\*\*|__)?[^:*_|]{2,60}(?:\*\*|__)?\s*:\s*(?:\*\*)?\s*[\d][\d,]*(?:\.\d+)?\s*(?:\*\*)?[^\n]{0,24}$/;

// The cards for a run, whichever shape it came back in.
export function answerCards(rich, answerText) {
  const cards = richCards(rich);
  if (cards.length) return cards;
  return markdownCards(answerText);
}

// ── the hidden set ───────────────────────────────────────────────────────────
// Shape: { "<account email>": { "<report card id>": ["group:building-5", …] } }. Scoped by
// account because one browser is shared by every account that has ever signed in here — the
// same reason logic/sessions.js stamps an owner on a session.
const store = () => {
  try { return (typeof window !== 'undefined' && window.localStorage) || null; } catch (e) { return null; }
};

export function loadHidden(storage) {
  const st = storage || store();
  if (!st) return {};
  let raw = null;
  try { raw = st.getItem(HIDDEN_KEY); } catch (e) { return {}; }
  if (!raw) return {};
  let d = null;
  try { d = JSON.parse(raw); } catch (e) { return {}; }
  if (!d || typeof d !== 'object' || Array.isArray(d)) return {};
  const out = {};
  Object.keys(d).forEach((owner) => {
    const byCard = d[owner];
    if (!byCard || typeof byCard !== 'object' || Array.isArray(byCard)) return;
    const clean = {};
    Object.keys(byCard).forEach((cardId) => {
      const keys = arr(byCard[cardId]).filter((k) => typeof k === 'string' && k);
      if (keys.length) clean[cardId] = keys;
    });
    if (Object.keys(clean).length) out[owner] = clean;
  });
  return out;
}

export function saveHidden(map, storage) {
  const st = storage || store();
  if (!st) return false;
  try { st.setItem(HIDDEN_KEY, JSON.stringify(map || {})); return true; } catch (e) { return false; }
}

export const ownerKey = (account) =>
  (account && account.email ? String(account.email).trim().toLowerCase() : '') || 'anonymous';

export function hiddenFor(map, account, cardId) {
  const byCard = (map || {})[ownerKey(account)] || {};
  return arr(byCard[cardId]);
}

export function withHidden(map, account, cardId, key, hide) {
  const owner = ownerKey(account);
  const next = Object.assign({}, map);
  const byCard = Object.assign({}, next[owner] || {});
  const keys = arr(byCard[cardId]).filter((k) => k !== key);
  if (hide) keys.push(key);
  if (keys.length) byCard[cardId] = keys;
  else delete byCard[cardId];
  if (Object.keys(byCard).length) next[owner] = byCard;
  else delete next[owner];
  return next;
}

export function withNoneHidden(map, account, cardId) {
  const owner = ownerKey(account);
  const next = Object.assign({}, map);
  const byCard = Object.assign({}, next[owner] || {});
  delete byCard[cardId];
  if (Object.keys(byCard).length) next[owner] = byCard;
  else delete next[owner];
  return next;
}
