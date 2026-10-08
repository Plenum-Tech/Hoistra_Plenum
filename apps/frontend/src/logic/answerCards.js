// answerCards — any structured answer, arranged as the dashboard the compliance answers use.
//
// The backend sends card payloads (compliance_response) only on some routes: the compliance and
// vendor analysts, and the tools the card builders know. A general-loop answer that read the
// dashboard stats, the PPM contracts and the inspections came back as a wall of headings,
// "Label: value" bullets and numbered lists that restart at 1 (5 Oct 2026), while the answer above
// it in the same chat was KPI tiles and cards. This arranges the answer's OWN text the same way,
// so every answer reads as one system, including answers saved before today and reopened chats.
//
// Presentation only: nothing is computed, nothing is invented, and nothing is dropped. Every
// block the rules do not recognise is kept, in order, inside its section's card. Prose alone is
// the Overall line; only an empty answer returns null.
import { parseBlocks } from './markdownBlocks.js';

const unbold = (s) => String(s == null ? '' : s).replace(/\*\*|__/g, '').trim();

// "**Label:** value" · "**Label**: value" · "**Label** — value" · "Label: value"
export function keyValue(item) {
  const s = String(item || '');
  const m = /^\s*(?:\*\*|__)([^*_]{1,80}?):(?:\*\*|__)\s*(.*)$/.exec(s)
    || /^\s*(?:\*\*|__)([^*_]{1,80}?)(?:\*\*|__)\s*(?::|\s[-–—]\s)\s*(.*)$/.exec(s)
    || /^\s*([A-Za-z][^:*\[\]]{0,60}?):\s+(\S.*)$/.exec(s);
  if (!m) return null;
  const key = unbold(m[1]).replace(/:\s*$/, '');
  const value = String(m[2] || '').trim();
  if (!key || !value) return null;
  return { key: key, value: value };
}

// A list item that is only a bold title: "**Display Energy Certificate (DEC)**" (optionally "…:").
export function titleOf(item) {
  const m = /^\s*(?:\*\*|__)([^*_]{2,120}?)(?:\*\*|__)\s*:?\s*$/.exec(String(item || ''));
  if (!m || /^\[[^\]]*\]\([^)]*\)$/.test(m[1].trim())) return null;   // a bold link is a field, not a title
  return m[1].replace(/:\s*$/, '').trim();
}

// "61" · "£230,160.70 (for those with available costs)" · "156.8%" · "16 (8 flagged by energy)"
// · "500 items in the approvals queue." A date, an id or a code is not a figure.
const FIGURE = /^((?:[£$€]\s?)?[-+]?\d[\d,]*(?:\.\d+)?(?:\s?%|[kKmM]\b)?)(.*)$/;
export function kpiOf(kv) {
  if (!kv || kv.key.length > 48) return null;
  const v = unbold(kv.value);
  if (/^\d{4}-\d{2}-\d{2}/.test(v)) return null;
  const m = FIGURE.exec(v);
  if (!m) return null;
  const rest = m[2].trim();
  if (/^[-/.:]\d|^[A-Za-z]{0,3}\d/.test(rest)) return null;          // 9920-1010-…, B-301, 3.4.1
  // The rest of the line is kept exactly as written: "(8 flagged by energy)", "items in the queue."
  if (rest.length > 80) return null;
  return { value: m[1].trim(), label: kv.key, sub: rest };
}

// Whole words only: "Next actions by vendor", "Corrective action plan" and "Recommended next steps"
// are next steps; "Recent Transactions" and "High priority sites" are not.
const ACTIONS = /\b(?:actions?|next steps?|what to do|how to fix|to[- ]?dos?|follow[- ]?ups?)\b|^(?:\d+[.)]\s*)?recommend/i;
const SUMMARY = /^(summary|conclusion|overall|in summary|bottom line|in short|key takeaways?)\b/i;
const RISK = /risk|lapsed|overdue|blocked|threat|critical|fail|missed|\blate\b|breach|non[- ]?compliant|anomal|behind|poor|expired/i;
const WARN = /watch|expir|due\b|pending|warning|attention|approval|queue/i;
const GOOD = /\bcompliant\b|in control|healthy|on track|completed/i;

export function toneOf(title) {
  const t = String(title || '');
  return RISK.test(t) ? 'critical' : WARN.test(t) ? 'warning' : GOOD.test(t) ? 'ok' : 'neutral';
}

// Heading text as written, without the markdown marks and the trailing colon:
// "### 2. **PPM**:" -> "2. PPM". The answer's own numbering stays.
export function cleanTitle(s) {
  return unbold(String(s || '')).replace(/:\s*$/, '').trim();
}

// An ordered or bullet list written as "title, then its fields": the analyst's "1. **DEC**
// 2. **Building**: Manchester Town Hall 3. **Expiry Date**: …" that renders as one numbered list
// per item. Returns the items, or null when the list is not shaped that way.
export function entitiesOf(items) {
  const ents = [];
  let cur = null;
  let fields = 0;
  for (const it of items) {
    const title = titleOf(it);
    if (title) { cur = { title: title, fields: [] }; ents.push(cur); continue; }
    if (!cur) return null;
    const kv = keyValue(it);
    if (kv) { cur.fields.push(kv); fields += 1; } else cur.fields.push({ key: '', value: String(it).trim() });
  }
  return ents.length && fields >= 2 ? ents : null;
}

// A list block, arranged: entity cards, KPI tiles (plus the items that are not figures), action
// cards, or the list as it was.
function arrangeList(b, inActions) {
  const items = b.items || [];
  if (inActions) {
    // A deeper-indented item is a sub-step of the action above it, not an action of its own.
    const actions = [];
    const base = Math.min(...items.map((_, j) => (b.indents || [])[j] || 0));
    items.forEach((x, j) => {
      const text = String(x).trim();
      if (!text) return;
      const deeper = ((b.indents || [])[j] || 0) > base;
      if (deeper && actions.length) actions[actions.length - 1].subs.push(text);
      else actions.push({ text: text, subs: [] });
    });
    return [{ t: 'actions', actions: actions }];
  }
  const ents = entitiesOf(items);
  if (ents) return [{ t: 'entities', entities: ents }];
  const figs = items.map((it, j) => ((b.indents || [])[j] ? null : kpiOf(keyValue(it))));
  const count = figs.filter(Boolean).length;
  if (count < 2 || count < Math.ceil(items.length * 0.4)) return [{ t: 'block', block: b }];
  // Runs in the list's own order: figures as tiles, everything else as the list it was.
  const out = [];
  items.forEach((it, j) => {
    const last = out[out.length - 1];
    if (figs[j]) {
      if (last && last.t === 'kpis') last.kpis.push(figs[j]);
      else out.push({ t: 'kpis', kpis: [figs[j]] });
    } else if (last && last.t === 'block') {
      last.block.items.push(it);
      last.block.indents.push((b.indents || [])[j] || 0);
    } else {
      out.push({ t: 'block', block: { t: 'list', ordered: b.ordered, start: (b.start || 1) + j, items: [it], indents: [(b.indents || [])[j] || 0] } });
    }
  });
  return out;
}

// A paragraph that is nothing but a short bold line reads as a heading: "**Contracts Behind Plan:**".
function asHeading(b) {
  if (b.t !== 'p' || b.body.indexOf('\n') > -1) return null;
  const t = titleOf(b.body);
  return t && t.length <= 90 ? { t: 'h', level: 4, body: t } : null;
}

export function answerCards(text) {
  const raw = parseBlocks(text);
  // A bold line reads as a heading only when content follows it; a closing bold sentence
  // ("**Would you like me to raise these?**") is the answer's text, not a section title.
  const blocks = raw.map((b, i) => {
    const h = asHeading(b);
    const next = raw[i + 1];
    return h && next && next.t !== 'h' && !asHeading(next) ? h : b;
  });
  // Every answer gets the layout (5 Oct 2026: "always render all the answers in card layout"):
  // prose alone is the Overall line, as the dashboard's narrative is. Only an empty answer has none.
  if (!blocks.length) return null;

  const overall = [];
  const sections = [];
  let cur = null;
  let kicker = '';
  for (const b of blocks) {
    if (b.t === 'h') {
      // A heading with nothing under it before the next one is the next section's group label
      // (several in a row join: "Portfolio · Compliance").
      if (cur && !cur.raw.length) { kicker = [cur.kicker, cur.title].filter(Boolean).join(' · '); sections.pop(); }
      cur = { title: cleanTitle(b.body), kicker: kicker, raw: [], src: b.body };
      kicker = '';
      sections.push(cur);
      continue;
    }
    if (!cur) {
      if (b.t === 'p' && !sections.length) { overall.push(b.body); continue; }
      cur = { title: '', kicker: '', raw: [] };
      sections.push(cur);
    }
    cur.raw.push(b);
  }
  // A heading with nothing after it at the very end is kept as text, never dropped.
  if (cur && !cur.raw.length) {
    sections.pop();
    if (!sections.length && !overall.length) {
      if (cur.kicker) overall.push(cur.kicker);
      overall.push(cur.src);
    } else {
      sections.push({ title: '', kicker: cur.kicker, raw: [{ t: 'p', body: cur.src }] });
    }
  }

  const out = sections.map((s) => {
    const inActions = ACTIONS.test(s.title);
    const onlyText = s.raw.every((b) => b.t === 'p');
    const kind = inActions ? 'actions' : (SUMMARY.test(s.title) && onlyText) ? 'summary' : 'card';
    const parts = [];
    for (const b of s.raw) {
      if (b.t === 'list') parts.push(...arrangeList(b, inActions));
      else if (inActions && b.t === 'p') parts.push({ t: 'actions', actions: [{ text: b.body.trim(), subs: [] }] });
      else parts.push({ t: 'block', block: b });
    }
    // adjacent action runs read as one list
    const merged = [];
    for (const p of parts) {
      const last = merged[merged.length - 1];
      if (p.t === 'actions' && last && last.t === 'actions') last.actions.push(...p.actions);
      else merged.push(p);
    }
    return { title: s.title, kicker: s.kicker, kind: kind, tone: toneOf(s.title + ' ' + s.kicker), parts: merged };
  });
  return { overall: overall, sections: out };
}
