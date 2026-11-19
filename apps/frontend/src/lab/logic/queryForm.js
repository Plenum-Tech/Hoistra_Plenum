// queryForm — the records query as fields a person can change (19 Nov 2026, Hussain and Bala: "every part of the query,
// not only the states, the building and the dates"). A plan is Hoistra's own (svc-deepagents ontology_qa PLAN_SCHEMA):
//   {question_type, focus: {concept, match, states, filters}, constraints: [{concept, match, via, states, filters}],
//    include: [{name, concept, via, states, filters, sort: {attribute, dir} | null, limit}], aggregate: null | {count_by}}
// `formOf(plan)` holds it as the fields the editor shows and `planOf(form)` gives the plan back: a plan nobody touched
// comes back exactly as it went in (keys it lacks stay lacking, keys Trace Lab does not know are kept), so what the
// person did not change cannot change. Every name a field offers comes from Hoistra's map of the records (the `map`
// GET /lab/turns/{id}/expected/query returns); nothing here reads the service, and no SQL is ever typed.

const list = (x) => (Array.isArray(x) ? x : []);
const isObj = (x) => !!x && typeof x === 'object' && !Array.isArray(x);
const clone = (x) => (x === undefined ? undefined : JSON.parse(JSON.stringify(x)));
const words = (x) => String(x == null ? '' : x).replace(/_/g, ' ');
const trim = (x) => String(x == null ? '' : x).trim();
const blank = (v) => v == null || (typeof v === 'string' && !v.trim());

// ── Words ───────────────────────────────────────────────────────────────────────────────────────────────────────────
// The conditions the records engine knows (ontology_qa OPS), in plain words.
export const OP_WORDS = {
  eq: 'is', ne: 'is not', in: 'is one of', not_in: 'is not one of', lt: 'is before', lte: 'is on or before', gt: 'is after',
  gte: 'is on or after', between: 'is between', within: 'is within', contains: 'contains', is_null: 'is empty'
};
// "is empty" with the answer no: the editor offers it as a condition of its own.
export const NOT_EMPTY_WORDS = 'is not empty';
const NUMBER_WORDS = { lt: 'is less than', lte: 'is at most', gt: 'is more than', gte: 'is at least' };
// Which conditions make sense for which kind of column, in the order they are offered.
const KIND_OPS = {
  date: ['eq', 'ne', 'lt', 'lte', 'gt', 'gte', 'between', 'within', 'is_null'],
  number: ['eq', 'ne', 'lt', 'lte', 'gt', 'gte', 'between', 'is_null'],
  text: ['eq', 'ne', 'in', 'not_in', 'contains', 'is_null']
};
export const KIND_WORDS = { list: 'A list of records', count: 'A count', single: 'One record' };

// Why a turn has no query to check (Ruling 18, 19 Nov 2026), in the words the page uses.
export const NO_QUERY_WORDS = {
  not_about_records: 'This question is not about records, so there is no query to check.',
  records_plan_unreadable: 'Hoistra read the records, but its query could not be read back from the trace.',
  planner_unavailable: 'No query could be proposed right now; it is tried again later.',
  no_map: "Hoistra's map of the records is not ready yet; it is tried again later.",
  read_failed: 'The records could not be read; it is tried again later.',
  plan_does_not_fit: 'The query does not fit the records any more.',
  no_trace: 'The conversation is not filed yet; it is tried again later.',
  asker_has_no_buildings: 'The person who asked has no buildings, so nothing can be read for them.',
  no_company: 'This question has no company, so nothing can be read.',
  no_question: 'There is no question text to plan from.'
};
export function noQueryWords(reason) {
  return typeof reason === 'string' && Object.prototype.hasOwnProperty.call(NO_QUERY_WORDS, reason) ? NO_QUERY_WORDS[reason] : 'No query to check.';
}

// What a status means, in plain words. The ontology's description also carries notes for the model ("USE THIS for …"),
// row counts and asides in brackets; none of that helps a person ticking a box.
export function stateHelp(c) {
  let t = String((c || {}).description || '').split(/\bUSE THIS\b/i)[0];
  t = t.replace(/\([^()]*\)/g, ' ').replace(/\([^)]*$/, ' ').replace(/_/g, ' ').replace(/\s+/g, ' ').replace(/[\s.,;:—-]+$/, '').trim();
  const named = t.match(/^[\w\s/,]+?\s+-\s+(.+)$/);
  if (named) t = named[1];
  if (/\s\/\s/.test(t) && !/\s-\s/.test(t)) {
    const parts = t.split(/\s*\/\s*/);
    t = parts.length < 2 ? t : parts.slice(0, -1).join(', ') + ' or ' + parts[parts.length - 1];
  }
  t = t.replace(/\s=\s/g, ' is ');
  if (t.length > 90) t = t.slice(0, 90).replace(/\s+\S*$/, '') + '…';
  return t;
}

// ── The map: concepts, their columns (with a kind), states and links ────────────────────────────────────────────────
export const conceptOf = (map, name) => list(map && map.concepts).find((c) => c && c.name === name) || null;

function pluralWords(name) {
  const w = String(name || 'record').replace(/([a-z0-9])([A-Z])/g, '$1 $2').toLowerCase();
  return w.endsWith('y') && !w.endsWith('ey') ? w.slice(0, -1) + 'ies' : w.endsWith('s') ? w : w + 's';
}
export function conceptWords(map, name) {
  const c = conceptOf(map, name);
  return c && c.words ? String(c.words) : pluralWords(name);
}
export const attributesOf = (map, concept) => list((conceptOf(map, concept) || {}).attributes)
  .map((a) => (typeof a === 'string' ? { name: a, kind: 'text' } : { name: a && a.name, kind: (a && a.kind) || 'text' })).filter((a) => a.name);
export function kindOf(map, concept, attribute) {
  const a = attributesOf(map, concept).find((x) => x.name === attribute);
  return a ? a.kind : 'text';
}
export const statesOf = (map, concept) => list((conceptOf(map, concept) || {}).states)
  .map((s) => (typeof s === 'string' ? { id: s, description: '' } : { id: s && s.id, description: (s && s.description) || '' })).filter((s) => s.id);
export const linksOf = (map, concept) => list((conceptOf(map, concept) || {}).links).slice();

// A date column is named as a date: "SLA due date", "expiry date", "raised date".
export function columnWords(attr, kind) {
  const a = String(attr || '');
  if (/^sla_due(_at)?$/.test(a)) return 'SLA due date';
  if (kind === 'date') {
    const w = words(a.replace(/_(at|date|on)$/, ''));
    return /(^|\s)date$/.test(w) ? w : w + ' date';
  }
  return words(a);
}

const ROLLING = /^(next|last)_(\d*)_days$/;
export function rollingOf(value) {
  const m = ROLLING.exec(String(value == null ? '' : value));
  return m ? { way: m[1], n: m[2] } : null;
}
const PERIODS = ['today', 'yesterday', 'this_week', 'last_week', 'this_month', 'last_month', 'next_month', 'this_quarter', 'last_quarter',
  'this_year', 'last_year', 'year_to_date'];
export function periodWords(value) {
  const r = rollingOf(value);
  return r ? 'the ' + r.way + ' ' + r.n + ' days' : words(value);
}
// The calendar periods the engine takes; the rolling ones (next_60_days) are asked for as a number of days.
export function periodsOf(map) {
  const given = list(map && map.periods).map((p) => (typeof p === 'string' ? { id: p } : p)).filter((p) => p && typeof p.id === 'string');
  const all = given.length ? given : PERIODS.map((id) => ({ id }));
  return all.filter((p) => !rollingOf(p.id) && !/[<>]/.test(p.id)).map((p) => ({ id: p.id, words: p.words || words(p.id) }));
}

// The map's words win (svc-trace-lab records_form.query_map says them: `words`, and `number_words` for a comparison on a
// number column); ours are for a page with no map.
export function opWords(map, id, kind) {
  if (id === 'not_null') return NOT_EMPTY_WORDS;
  const given = list(map && map.ops).find((o) => o && o.id === id);
  if (kind === 'number' && ((given && given.number_words) || NUMBER_WORDS[id])) return (given && given.number_words) || NUMBER_WORDS[id];
  return (given && given.words) || OP_WORDS[id] || words(id);
}
const allowedOps = (map, kind) => {
  const base = KIND_OPS[kind] || KIND_OPS.text;
  const known = list(map && map.ops).map((o) => o && o.id);
  return known.length ? base.filter((id) => known.includes(id)) : base;
};
// The conditions a column offers, in words. A condition the query already has is always offered, whatever the column.
export function opsFor(map, kind, current) {
  let ids = [];
  allowedOps(map, kind).forEach((id) => { ids.push(id); if (id === 'is_null') ids.push('not_null'); });
  if (current && !ids.includes(current)) ids = ids.concat([current]);
  return ids.map((id) => ({ id, words: opWords(map, id, kind) }));
}
const isNullTrue = (v) => v === true || v === 1 || v === 'true' || v === 'True';
// What the condition select shows: "is empty" with the answer no is "is not empty".
export const opChoice = (c) => (c && c.op === 'is_null' && !isNullTrue(c.value) ? 'not_null' : (c && c.op) || '');

// ── The form ────────────────────────────────────────────────────────────────────────────────────────────────────────
const NODE_KEYS = ['concept', 'match', 'states', 'filters', 'via'];
const INCLUDE_KEYS = ['name', 'sort', 'limit'];
const PLAN_KEYS = ['question_type', 'focus', 'constraints', 'include', 'aggregate'];
export const INCLUDE_LIMIT = { min: 1, max: 200, fallback: 20 };   // ontology_qa DEFAULT_INCLUDE_LIMIT, MAX_INCLUDE_LIMIT

function extras(x, known) {
  const out = {};
  Object.keys(x).forEach((k) => { if (!known.includes(k)) out[k] = clone(x[k]); });
  return out;
}
// `_had` remembers which keys the plan had, so a key it lacked stays lacking; `_extra` keeps keys we do not know.
function nodeIn(n, include) {
  const x = isObj(n) ? n : {};
  const node = {
    concept: typeof x.concept === 'string' ? x.concept : '',
    match: x.match == null ? '' : String(x.match),
    states: list(x.states).slice(),
    conditions: list(x.filters).map(clone),
    via: list(x.via).slice(),
    _had: Object.keys(x),
    _extra: extras(x, include ? NODE_KEYS.concat(INCLUDE_KEYS) : NODE_KEYS)
  };
  if (include) {
    node.name = x.name == null ? '' : String(x.name);
    node.sort = isObj(x.sort) ? clone(x.sort) : null;
    node.limit = x.limit;
  }
  return node;
}
export function formOf(plan) {
  const p = isObj(plan) ? plan : {};
  const ag = isObj(p.aggregate) ? p.aggregate : null;
  return {
    kind: typeof p.question_type === 'string' ? p.question_type : '',
    focus: nodeIn(p.focus, false),
    limits: list(p.constraints).map((c) => nodeIn(c, false)),
    alsoShow: list(p.include).map((i) => nodeIn(i, true)),
    groupBy: ag ? list(ag.count_by).map(clone) : null,
    _aggExtra: ag ? extras(ag, ['count_by']) : {},
    _had: Object.keys(p),
    _extra: extras(p, PLAN_KEYS)
  };
}

// A value typed in a box is read the way the engine wants it: a list for "one of", trimmed text otherwise.
function conditionOut(c) {
  if (!isObj(c)) return c;
  const out = clone(c);
  if ((c.op === 'in' || c.op === 'not_in') && typeof c.value === 'string') out.value = c.value.split(',').map((s) => s.trim()).filter(Boolean);
  else if (Array.isArray(c.value)) out.value = c.value.map((v) => (typeof v === 'string' ? v.trim() : v));
  else if (typeof c.value === 'string') out.value = c.value.trim();
  return out;
}
function nodeOut(n, include) {
  const x = n || {};
  const keep = (key, empty) => !x._had || x._had.includes(key) || !empty;
  const out = Object.assign({}, clone(x._extra));
  out.concept = x.concept || '';
  const match = trim(x.match);
  if (keep('match', !match)) out.match = match || null;
  if (keep('states', !list(x.states).length)) out.states = list(x.states).slice();
  if (keep('filters', !list(x.conditions).length)) out.filters = list(x.conditions).map(conditionOut);
  if (keep('via', !list(x.via).length)) out.via = list(x.via).slice();
  if (include) {
    if (keep('name', !x.name)) out.name = x.name || '';
    if (keep('sort', !x.sort)) out.sort = x.sort ? clone(x.sort) : null;
    const lim = x.limit;
    if (keep('limit', lim == null || lim === '')) out.limit = typeof lim === 'string' && lim.trim() !== '' && Number.isFinite(Number(lim)) ? Number(lim) : lim;
  }
  return out;
}
export function planOf(form) {
  const f = form || {};
  const keep = (key, empty) => !f._had || f._had.includes(key) || !empty;
  const out = Object.assign({}, clone(f._extra));
  if (keep('question_type', !f.kind)) out.question_type = f.kind || 'list';
  out.focus = nodeOut(f.focus, false);
  if (keep('constraints', !list(f.limits).length)) out.constraints = list(f.limits).map((n) => nodeOut(n, false));
  if (keep('include', !list(f.alsoShow).length)) out.include = list(f.alsoShow).map((n) => nodeOut(n, true));
  if (keep('aggregate', f.groupBy == null)) out.aggregate = f.groupBy == null ? null : Object.assign({}, clone(f._aggExtra), { count_by: clone(f.groupBy) });
  return out;
}
// Whether the form is no longer the plan it started from (changed and changed back is not a change).
export const isChanged = (plan, form) => JSON.stringify(planOf(formOf(plan))) !== JSON.stringify(planOf(form));
// What Run sends: the number of the query and the plan (POST /lab/turns/{id}/expected/query).
export const runBody = (index, form) => ({ index: index || 0, plan: planOf(form) });

// ── Changing a field changes what depends on it ─────────────────────────────────────────────────────────────────────
const snake = (w) => String(w || '').trim().replace(/\s+/g, '_');
export const autoName = (concept, map) => snake(conceptWords(map, concept));
function uniqueName(name, others) {
  if (!others.includes(name)) return name;
  let i = 2;
  while (others.includes(name + '_' + i)) i += 1;
  return name + '_' + i;
}
export function blankNode(concept, include, map) {
  const n = { concept, match: '', states: [], conditions: [], via: [] };
  if (include) Object.assign(n, { name: autoName(concept, map), sort: null, limit: INCLUDE_LIMIT.fallback });
  return n;
}
// A different kind of record: the path to it, the states and columns of the old one no longer apply, the rest stays.
export function changeConcept(node, concept, map) {
  if (node.concept === concept) return node;
  const attrs = attributesOf(map, concept).map((a) => a.name);
  const states = statesOf(map, concept).map((s) => s.id);
  const out = Object.assign({}, node, { concept, via: [], states: list(node.states).filter((s) => states.includes(s)),
    conditions: list(node.conditions).filter((c) => attrs.includes(c && c.attribute)) });
  if ('sort' in node) {
    out.sort = node.sort && attrs.includes(node.sort.attribute) ? node.sort : null;
    out.name = autoName(concept, map);
  }
  return out;
}
// A grouping counts by a column of the focus or of a record it is limited to: when one of those records changes, a grouping
// that counted by it moves to the focus (its first column), where the person sees it and can change it.
function fixGroups(form, map) {
  if (!form.groupBy) return form;
  const allowed = groupConcepts(form);
  const first = attributesOf(map, form.focus.concept)[0];
  return Object.assign({}, form, { groupBy: form.groupBy.map((g) => (allowed.includes(g.concept) ? g
    : { concept: form.focus.concept, attribute: first ? first.name : '' })) });
}
// The same, for one node of the form (the others' names are taken into account, so two of a kind do not share one).
export function changeNodeConcept(form, section, i, concept, map) {
  const nodes = list(form[section]).slice();
  const next = changeConcept(nodes[i], concept, map);
  if (section === 'alsoShow' && next !== nodes[i]) next.name = uniqueName(next.name, nodes.filter((_, j) => j !== i).map((n) => n.name));
  nodes[i] = next;
  return fixGroups(Object.assign({}, form, { [section]: nodes }), map);
}
// What is read changes: what hung on the old kind of record goes, and so does a grouping by it.
export function changeFocusConcept(form, concept, map) {
  return fixGroups(Object.assign({}, form, { focus: changeConcept(form.focus, concept, map) }), map);
}
export function setNode(form, section, i, change) {
  const nodes = list(form[section]).slice();
  nodes[i] = Object.assign({}, nodes[i], change);
  return Object.assign({}, form, { [section]: nodes });
}
export function removeAt(form, section, i, map) {
  const next = Object.assign({}, form, { [section]: list(form[section]).filter((_, j) => j !== i) });
  return section === 'limits' ? fixGroups(next, map) : next;
}

const defaultCondition = (attribute, kind) => (kind === 'date' ? { attribute, op: 'within', value: 'this_month' } : { attribute, op: 'eq', value: '' });
export function blankCondition(map, concept) {
  const first = attributesOf(map, concept)[0];
  return first ? defaultCondition(first.name, first.kind) : { attribute: '', op: 'eq', value: '' };
}
// A different column: the condition stays when it makes sense for the new column, else it starts again.
export function changeColumn(cond, attribute, map, concept) {
  const kind = kindOf(map, concept, attribute);
  if (allowedOps(map, kind).includes(cond.op)) return Object.assign({}, cond, { attribute });
  return Object.assign({}, cond, defaultCondition(attribute, kind));
}
function coerceValue(value, from, to) {
  const reset = ['within', 'is_null', 'not_null'].includes(from);
  const first = Array.isArray(value) ? (value[0] == null ? '' : value[0]) : value;
  const text = typeof first === 'string' || typeof first === 'number' ? first : '';
  if (to === 'is_null') return true;
  if (to === 'not_null') return false;
  if (to === 'within') return from === 'within' ? value : 'this_month';
  if (to === 'between') return Array.isArray(value) ? [value[0] == null ? '' : value[0], value[1] == null ? '' : value[1]] : [reset ? '' : text, ''];
  if (to === 'in' || to === 'not_in') return Array.isArray(value) ? value : reset ? '' : text;
  return reset ? '' : text;
}
export function changeOp(cond, choice) {
  const op = choice === 'not_null' ? 'is_null' : choice;
  return Object.assign({}, cond, { op, value: coerceValue(cond.value, opChoice(cond), choice) });
}

export function addLimit(form, map) {
  const concept = linksOf(map, form.focus.concept)[0] || '';
  return Object.assign({}, form, { limits: list(form.limits).concat([blankNode(concept, false, map)]) });
}
export function addInclude(form, map) {
  const concept = linksOf(map, form.focus.concept)[0] || '';
  const node = blankNode(concept, true, map);
  node.name = uniqueName(node.name, list(form.alsoShow).map((n) => n.name));
  return Object.assign({}, form, { alsoShow: list(form.alsoShow).concat([node]) });
}
// What a grouping may count by: the focus or one of the records it is limited to.
export function groupConcepts(form) {
  const all = [form.focus.concept].concat(list(form.limits).map((n) => n.concept)).filter(Boolean);
  return all.filter((c, i) => all.indexOf(c) === i);
}
export function addGroup(form, map) {
  const concept = form.focus.concept;
  const first = attributesOf(map, concept)[0];
  return Object.assign({}, form, { groupBy: list(form.groupBy).concat([{ concept, attribute: first ? first.name : '' }]) });
}
export function setGroup(form, i, change) {
  return Object.assign({}, form, { groupBy: list(form.groupBy).map((g, j) => (j === i ? Object.assign({}, g, change) : g)) });
}
// No grouping left is no aggregate at all.
export function removeGroup(form, i) {
  const rest = list(form.groupBy).filter((_, j) => j !== i);
  return Object.assign({}, form, { groupBy: rest.length ? rest : null });
}

// ── What stops a query from running, in words ───────────────────────────────────────────────────────────────────────
function conditionProblem(c) {
  const x = c || {};
  if (!x.attribute) return 'choose a column.';
  if (x.op === 'is_null') return '';
  if (x.op === 'between') return list(x.value).length === 2 && !blank(x.value[0]) && !blank(x.value[1]) ? '' : 'give both ends.';
  if (x.op === 'in' || x.op === 'not_in') {
    const items = Array.isArray(x.value) ? x.value : String(x.value == null ? '' : x.value).split(',');
    return items.some((v) => !blank(v)) ? '' : 'give at least one value.';
  }
  if (x.op === 'within') {
    const r = rollingOf(x.value);
    if (r) return r.n && Number(r.n) >= 1 ? '' : 'give a number of days.';
    return blank(x.value) ? 'choose a period.' : '';
  }
  return blank(x.value) ? 'give a value.' : '';
}
export function formProblems(form) {
  const f = form || {};
  const out = [];
  if (!f.focus || !f.focus.concept) out.push('Choose what to look for.');
  const parts = [['What', f.focus]].concat(list(f.limits).map((n, i) => ['Limited to ' + (i + 1), n]), list(f.alsoShow).map((n, i) => ['Also show ' + (i + 1), n]));
  parts.forEach(([label, n]) => {
    if (!n) return;
    if (label !== 'What' && !n.concept) out.push(label + ': choose a kind of record.');
    list(n.conditions).forEach((c, i) => { const m = conditionProblem(c); if (m) out.push(label + ', condition ' + (i + 1) + ': ' + m); });
    if (label.startsWith('Also show')) {
      const lim = Number(n.limit);
      if (!Number.isInteger(lim) || lim < INCLUDE_LIMIT.min || lim > INCLUDE_LIMIT.max) {
        out.push(label + ': how many must be a number from ' + INCLUDE_LIMIT.min + ' to ' + INCLUDE_LIMIT.max + '.');
      }
    }
  });
  return out;
}

// ── The query in words ──────────────────────────────────────────────────────────────────────────────────────────────
const either = (l) => (l.length < 2 ? l[0] || '' : l.slice(0, -1).join(', ') + ' or ' + l[l.length - 1]);

function valueWords(c) {
  const v = c.value;
  if (c.op === 'between') return list(v).join(' and ');
  if (c.op === 'in' || c.op === 'not_in') return Array.isArray(v) ? v.join(', ') : String(v == null ? '' : v);
  if (c.op === 'within') return periodWords(v);
  if (c.op === 'contains') return '“' + String(v == null ? '' : v) + '”';
  return String(v == null ? '' : v);
}
// "the expiry date is within the next 60 days".
export function conditionWords(c, kind, map) {
  const x = c || {};
  const op = opWords(map, opChoice(x), kind);
  const tail = x.op === 'is_null' ? '' : ' ' + valueWords(x);
  return 'the ' + columnWords(x.attribute, kind) + ' ' + op + tail;
}
function nodePhrase(n, map) {
  let s = conceptWords(map, n.concept);
  const match = trim(n.match);
  if (match) s += ' matching “' + match + '”';
  const states = list(n.states).map(words);
  if (states.length) s += ' that are ' + either(states);
  const conds = list(n.conditions).map((c) => conditionWords(c, kindOf(map, n.concept, c && c.attribute), map));
  if (conds.length) s += ' where ' + conds.join(' and ');
  return s;
}
function sortWords(i, map) {
  const s = i.sort;
  if (!s || !s.attribute) return '';
  const kind = kindOf(map, i.concept, s.attribute);
  const desc = s.dir === 'desc';
  if (kind === 'date') return ', ' + (desc ? 'newest' : 'oldest') + ' first';
  if (kind === 'number') return ', ' + (desc ? 'highest' : 'lowest') + ' first';
  return ', sorted by ' + columnWords(s.attribute, kind) + (desc ? ' Z to A' : ' A to Z');
}
// The query as one plain sentence: what, which ones, where, how counted, what else is shown.
export function describePlan(plan, map, lead) {
  const f = formOf(plan);
  const kind = f.kind === 'count' ? 'a count of ' : f.kind === 'single' ? 'one of the ' : 'a list of ';
  let s = kind + nodePhrase(f.focus, map);
  if (f.limits.length) s += ', limited to ' + f.limits.map((n) => nodePhrase(n, map)).join(', and ');
  if (f.groupBy && f.groupBy.length) {
    s += ', counted by ' + f.groupBy.map((g) => columnWords(g.attribute, kindOf(map, g.concept, g.attribute)) + ' (' + conceptWords(map, g.concept) + ')').join(' and ');
  }
  if (f.alsoShow.length) {
    s += ', also showing ' + f.alsoShow.map((i) => nodePhrase(i, map) + ' for each' + sortWords(i, map)
      + (i.limit != null && i.limit !== '' ? ', up to ' + i.limit : '')).join(', and ');
  }
  return (lead == null ? 'Hoistra looked for' : lead) + ' ' + s + '.';
}

// ── Use as the expected answer ──────────────────────────────────────────────────────────────────────────────────────
// What the page saves when the person uses their version of one query: the draft's evidence with that query replaced by
// theirs (their plan, the values and receipts their run read), every other query exactly as it was, and what changed in
// words. The expected answer's sentence is one sentence per query, in order, as a draft holds them.
export const DRAFT_VERSION = 3;
// The longest expected answer the service keeps (svc-trace-lab runs.EXPECTED_MAX; ExpectedIn.expected refuses a longer
// one): linked records can make a sentence long, so the answer is cut there, as drafting cuts it. The values keep theirs.
export const EXPECTED_MAX = 8000;
export function usedBody({ evidence, queries, index, plan, result }) {
  const ev = isObj(evidence) ? evidence : {};
  const had = list(ev.queries);
  const known = list(queries);
  const after = (result && result.after) || {};
  const total = Math.max(had.length, known.length, (index || 0) + 1);
  const out = [];
  for (let i = 0; i < total; i += 1) {
    const meta = known.find((k) => k && k.index === i);
    const base = had[i] ? clone(had[i]) : meta ? { source: meta.source, question: meta.question, plan: clone(meta.plan) } : {};
    if (i !== (index || 0)) { out.push(base); continue; }
    out.push(Object.assign(base, {
      source: 'person', edited_from: base.source || null, plan: clone(plan), found: after.found !== false, facts: list(after.facts), receipts: list(after.receipts),
      unresolved: list(after.unresolved), notes: list(after.notes), sentence: after.sentence || '', read_at: after.read_at || null, changed: []
    }));
  }
  return {
    expected: out.map((q) => (q && q.sentence) || '').filter(Boolean).join(' ').slice(0, EXPECTED_MAX),
    evidence: { drafted_from: 'records', draft: DRAFT_VERSION, read_at: after.read_at || null, queries: out, changed: list(result && result.changes) }
  };
}

// ── Running ─────────────────────────────────────────────────────────────────────────────────────────────────────────
// The words a refused request carries, for the side of the editor: a plan the map refuses answers `bad_query` with the
// problems in words (a list, or one sentence).
export function serverWords(e) {
  const d = e && e.body && e.body.detail;
  const given = isObj(d) ? (list(d.problems).length ? d.problems : list(d.words)) : [];
  const out = given.map((w) => trim(w)).filter(Boolean);
  if (out.length) return out;
  return [trim(e && e.message) || 'The service did not answer. Try again.'];
}
// Runs one query as the form has it. The result keeps the plan that was run, so Use saves what was read, not what has been
// typed since. `onRun` takes {index, plan} (labApi.turnExpectedTry).
export async function tryRun(onRun, index, form) {
  const body = runBody(index, form);
  try {
    const out = await onRun(body);
    return { ok: true, result: Object.assign({}, out, { plan: body.plan }) };
  } catch (e) {
    const reason = (e && e.reason) || '';
    const words = serverWords(e);
    return { ok: false, reason, words: reason === 'bad_query' ? words : words.map((w) => 'Could not run it: ' + w) };
  }
}
// A result can be used as the expected answer when it found its records and is still the query on the page.
export function canUse(result, form) {
  const r = result || {};
  return !!(r.after && r.after.found !== false && r.plan && !isChanged(r.plan, form));
}
