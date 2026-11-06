// What the reader set on an answer's table - its sort and "Show all" - remembered, so a new
// question, a re-run, switching conversations or a reload never puts it back to the default
// (2 Nov 2026, feedback "Preserve conversation position": do not force the reader to repeat
// sorting and scrolling).
//
// A table is known by the conversation and turn it is in (AnswerKey, provided by the chat page
// and the dock around each answer) and its column headings. Kept for the tab in sessionStorage
// (a reload keeps it; another tab or a sign-out on a shared machine does not carry it), at most
// MAX tables, the oldest dropped first. Storage can be missing or full: then the view lasts as
// long as the table is on screen, as it did before.
import { createContext } from 'react';

export const AnswerKey = createContext('');

const STORE = 'hoistra.tableView.v1';
const MAX = 300;
let memo = null;

function storage() {
  try { return typeof window !== 'undefined' && window.sessionStorage ? window.sessionStorage : null; } catch (e) { return null; }
}

function load() {
  if (memo) return memo;
  memo = new Map();
  try {
    const raw = storage() && storage().getItem(STORE);
    const list = raw ? JSON.parse(raw) : [];
    if (Array.isArray(list)) list.forEach((e) => { if (Array.isArray(e) && typeof e[0] === 'string') memo.set(e[0], e[1]); });
  } catch (e) { /* unreadable: start empty */ }
  return memo;
}

// The key of one table in one answer of one conversation; '' when the answer has no
// conversation yet (a question still being asked in a new thread), which keeps nothing.
export function tableViewKey(answerKey, columns) {
  return answerKey ? answerKey + '|' + (columns || []).map((c) => String(c)).join('¦') : '';
}

export function getView(key) {
  if (!key) return null;
  return load().get(key) || null;
}

export function setView(key, view) {
  if (!key) return;
  const m = load();
  m.delete(key);
  m.set(key, view);
  while (m.size > MAX) m.delete(m.keys().next().value);
  try { const s = storage(); if (s) s.setItem(STORE, JSON.stringify([...m])); } catch (e) { /* full or blocked: kept in memory */ }
}

// ── sorting ──────────────────────────────────────────────────────────────────────────────────

const EMPTY = (v) => v === null || v === undefined || v === '' || v === '—' || v === '-';
const bare = (v) => String(v).trim().replace(/^(\*\*|__)(.+)\1$/, '$2');

// A figure as a number: "£1,200" is 1200, "126 days" is 126, "-7" is -7.
function figure(v) {
  if (typeof v === 'number') return v;
  const m = bare(v).replace(/[£$€,\s]/g, '').match(/^[-+−]?\d+(?:\.\d+)?/);
  return m ? Number(m[0].replace('−', '-')) : NaN;
}

const words = typeof Intl !== 'undefined' && Intl.Collator ? new Intl.Collator(undefined, { numeric: true, sensitivity: 'base' }) : null;

// The rows in the order `sort` asks for ({ col, dir: 'asc' | 'desc' }), or as given when there
// is none. Figures compare as numbers in a column of figures, everything else as words (an ISO
// date sorts as a date that way). A blank cell goes last whichever way; rows that tie keep the
// answer's own order.
export function sortRows(rows, kinds, sort) {
  if (!sort || typeof sort.col !== 'number') return rows;
  const j = sort.col;
  const sign = sort.dir === 'desc' ? -1 : 1;
  const num = (kinds || [])[j] === 'num';
  return rows
    .map((r, i) => [r, i])
    .sort((a, b) => {
      const x = a[0][j];
      const y = b[0][j];
      const ex = EMPTY(x);
      const ey = EMPTY(y);
      if (ex || ey) return ex === ey ? a[1] - b[1] : (ex ? 1 : -1);
      let c;
      if (num) {
        const fx = figure(x);
        const fy = figure(y);
        c = Number.isNaN(fx) || Number.isNaN(fy) ? 0 : fx - fy;
      } else {
        const sx = bare(x);
        const sy = bare(y);
        c = words ? words.compare(sx, sy) : (sx < sy ? -1 : sx > sy ? 1 : 0);
      }
      return c ? c * sign : a[1] - b[1];
    })
    .map((p) => p[0]);
}

// Click a heading: by that column ascending, then descending, then back to the answer's order.
export function nextSort(sort, col) {
  if (!sort || sort.col !== col) return { col: col, dir: 'asc' };
  return sort.dir === 'asc' ? { col: col, dir: 'desc' } : null;
}
