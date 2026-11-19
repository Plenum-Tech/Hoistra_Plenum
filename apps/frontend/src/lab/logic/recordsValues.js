// recordsValues — the values read from the records, as the session page shows them (19 Nov 2026, Hussain and Bala: the
// expected answer is exact values read from the records, each with where it came from). A draft's evidence holds one
// entry per query it read (`evidence.queries`); each is a block on the page: where the query came from, whose access it
// was read with, the sentence, every value with its receipt (the query, the values filled in, the rows), and how the
// query was read. Nothing here reads the service, and nothing here says whether Hoistra was right.

// Where a query came from, in the words Trace Lab stores (the same strings as svc-trace-lab records_queries.SOURCE_WORDS).
export const SOURCE_WORDS = {
  hoistra: 'Hoistra’s query',
  answer_key: 'Trace Lab’s fixed query',
  proposed: 'Proposed for this question - check it before accepting'
};

// A query a person changed and used as the expected answer is theirs: the query editor stores it with source "person".
export const PERSON_WORDS = 'Your version of the query';

const list = (x) => (Array.isArray(x) ? x : []);

// The blocks of a draft's evidence, in the order the queries were read.
export function blocksOf(evidence) {
  const ev = evidence || {};
  return list(ev.queries).map((q, index) => {
    const x = q || {};
    return {
      index,
      source: x.source || '',
      sourceWords: SOURCE_WORDS[x.source] || (x.source === 'person' ? PERSON_WORDS : ''),
      question: x.question || '',
      sentence: x.sentence || '',
      facts: list(x.facts),
      receipts: list(x.receipts),
      unresolved: list(x.unresolved),
      notes: list(x.notes),
      readAt: x.read_at || ev.read_at || null,
      found: x.found !== false,
      scope: x.scope || '',
      scopeEmail: x.scope_email || null,
      changed: list(x.changed),
      rerunnable: !!x.plan            // there is a query to read again
    };
  });
}

// Whose access the query was read with.
export function scopeWords(block) {
  const b = block || {};
  if (b.scope === 'asker') return 'Read with ' + (b.scopeEmail || 'the asker') + "'s access";
  if (b.scope === 'company') return 'Read for the whole company';
  return '';
}

const rows = (n) => n + (Number(n) === 1 ? ' row' : ' rows');

// What the records hold now, when it is not what Hoistra read: its row count then, and now.
export function changedWords(block) {
  const changed = list((block || {}).changed);
  if (!changed.length) return '';
  const several = changed.length > 1;
  return 'The records have changed since Hoistra answered: '
    + changed.map((c) => 'Hoistra read ' + rows(c.then) + ', now ' + c.now + (several && c.label ? ' (' + c.label + ')' : '')).join('; ');
}

// Values of different queries share ids (every query numbers its own f1, f2 ...), so a value is opened by its query's
// number and its own id.
export const factKey = (block, fact) => block.index + ':' + fact.id;

export function findFact(blocks, key) {
  const m = /^(\d+):(.+)$/.exec(String(key == null ? '' : key));
  if (!m) return null;
  const block = list(blocks).find((b) => b.index === Number(m[1]));
  const fact = block ? block.facts.find((f) => f.id === m[2]) : null;
  return fact ? { block, fact, receipt: receiptOf(block, fact) } : null;
}

// The query a value was read from (null when it has none to show).
export function receiptOf(block, fact) {
  if (!block || !fact || !fact.receipt) return null;
  return list(block.receipts).find((r) => r.id === fact.receipt) || null;
}

// The same value in a fresh read of the same query (Run again now). Ids are by place - one more figure, record or group
// moves every id after it - so a value is paired by what it is: its kind and label, and for a record, a name not on
// record, or an answer-key item (no kind, no label) its value too. null: the fresh read no longer has it.
export function sameFact(fact, facts) {
  const f = fact || {};
  const byValue = f.kind === 'record' || f.kind === 'missing' || (!f.kind && !f.label);
  return list(facts).find((x) => !!x && (x.kind || '') === (f.kind || '') && (x.label || '') === (f.label || '')
    && (!byValue || String(x.value) === String(f.value))) || null;
}

// How a value was found, in plain words.
export function explainOf(fact) {
  const f = fact || {};
  if (f.kind === 'count') return 'This number is how many records the query below found.';
  if (f.kind === 'record') return 'This is one of the records the query below found.';
  if (f.kind === 'group') return 'This number is how many records the query below counted for ' + f.label + '.';
  if (f.kind === 'missing') return 'The records do not hold ' + f.value + '. The query below looked for it.';
  return 'This value was read by the query below.';
}

// ── Long lists fold (19 Nov 2026, Hussain: "make this collapsable the 14 cert or anything like this") ────────────────
// A query's records, its counts by group, one record's details, its linked records and its open-item counts are each a
// list; past FOLD_AT items a list is behind one line that says how many ("Show the 14 records"). The single values (the
// count, a stated figure, a name not on record) stay in view. A sentence past SENTENCE_FOLD_AT characters shows its start,
// cut between two items, with the whole of it a click away.
export const FOLD_AT = 4;
export const SENTENCE_FOLD_AT = 160;
const LIST_WORDS = { record: 'records', group: 'counts', field: 'details', linked: 'linked records', pending: 'open-item counts' };

// { shown, folds }: the values left in view, in their order, and one fold per kind of list longer than FOLD_AT
// ({ kind, words, facts }, in the order its kind first appears).
export function foldFacts(facts) {
  const all = list(facts).filter(Boolean);
  const byKind = {};
  for (const f of all) if (LIST_WORDS[f.kind]) (byKind[f.kind] = byKind[f.kind] || []).push(f);
  const folded = Object.keys(byKind).filter((k) => byKind[k].length > FOLD_AT);
  const count = all.find((f) => f.kind === 'count');
  const folds = folded.map((kind) => {
    const n = byKind[kind].length;
    const total = kind === 'record' && count && typeof count.value === 'number' ? count.value : n;
    return { kind, facts: byKind[kind], words: total > n ? 'Show ' + n + ' of the ' + total + ' ' + LIST_WORDS[kind] : 'Show the ' + n + ' ' + LIST_WORDS[kind] };
  });
  return { shown: all.filter((f) => !folded.includes(f.kind)), folds };
}

// The start of a long sentence, ending " …"; null when the sentence is short enough to show whole.
export function foldSentence(sentence) {
  const s = String(sentence || '');
  if (s.length <= SENTENCE_FOLD_AT) return null;
  const head = s.slice(0, SENTENCE_FOLD_AT);
  const cut = Math.max(head.lastIndexOf(', '), head.lastIndexOf('. '));
  const at = cut > 0 ? cut : head.lastIndexOf(' ') > 0 ? head.lastIndexOf(' ') : SENTENCE_FOLD_AT;
  return s.slice(0, at) + ' …';
}
