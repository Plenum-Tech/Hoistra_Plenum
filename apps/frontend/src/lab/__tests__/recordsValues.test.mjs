// recordsValues — the values read from the records, as the page shows them (19 Nov 2026, Hussain and Bala: "the expected
// answer must be exact values from the records, each with where it came from"). One block per query: where the query came
// from, whose access it was read with, the sentence, each value with its receipt, and how the query was read.
import { test } from 'node:test';
import assert from 'node:assert/strict';

const R = await import('../logic/recordsValues.js');

const COUNT = { id: 'f1', kind: 'count', label: 'Vendor records found', value: 2, receipt: 'q1' };
const REC = { id: 'f2', kind: 'record', label: '', value: 'Pennard Fire Services', receipt: 'q2' };
const Q1 = { id: 'q1', label: 'count', sql: 'SELECT count(*) AS n FROM "vendors" AS f', params: {}, row_count: 1, rows: [{ n: 2 }], psql: 'BEGIN READ ONLY;' };
const Q2 = { id: 'q2', label: 'focus Vendor', sql: 'SELECT f.vendor_name FROM "vendors" AS f', params: {}, row_count: 2, rows: [{ vendor_name: 'Pennard Fire Services' }], psql: 'BEGIN READ ONLY;' };
const EV = { drafted_from: 'records', draft: 3, read_at: '2026-11-19T09:30:02+00:00', queries: [
  { source: 'hoistra', question: 'Which vendors are blocked?', plan: { focus: { concept: 'Vendor' } }, found: true, unresolved: [],
    notes: ['Read "blocked" as block_state = Blocked.'], read_at: '2026-11-19T09:30:01+00:00', sentence: 'Vendor records found: 2. Vendors: Pennard Fire Services.',
    scope: 'asker', scope_email: 'me@x.test', changed: [{ label: 'focus Vendor', then: 3, now: 2 }], facts: [COUNT, REC], receipts: [Q1, Q2] },
  { source: 'proposed', question: 'Which vendors are blocked?', plan: null, found: false, unresolved: ['ABC'], notes: [], sentence: 'Not on record: ABC',
    scope: 'company', scope_email: null, changed: [], facts: [{ id: 'f1', kind: 'missing', label: 'Not on record', value: 'ABC', receipt: null }], receipts: [] }] };

test('the words for where a query came from are the ones Trace Lab stores', () => {
  assert.deepEqual(R.SOURCE_WORDS, { hoistra: 'Hoistra’s query', answer_key: 'Trace Lab’s fixed query',
    proposed: 'Proposed for this question - check it before accepting' });
});

test('every query is a block, in the order it was read, with its own values and receipts', () => {
  const blocks = R.blocksOf(EV);
  assert.equal(blocks.length, 2);
  const [a, b] = blocks;
  assert.equal(a.index, 0);
  assert.equal(a.source, 'hoistra');
  assert.equal(a.sourceWords, 'Hoistra’s query');
  assert.equal(a.question, 'Which vendors are blocked?');
  assert.equal(a.sentence, 'Vendor records found: 2. Vendors: Pennard Fire Services.');
  assert.deepEqual(a.facts, [COUNT, REC]);
  assert.deepEqual(a.receipts, [Q1, Q2]);
  assert.deepEqual(a.notes, ['Read "blocked" as block_state = Blocked.']);
  assert.equal(a.readAt, '2026-11-19T09:30:01+00:00');
  assert.deepEqual(a.unresolved, []);
  assert.equal(b.index, 1);
  assert.equal(b.sourceWords, 'Proposed for this question - check it before accepting');
  assert.deepEqual(b.unresolved, ['ABC']);
});

test('a block with no time of its own reads the draft’s time; nothing stored gives no blocks', () => {
  assert.equal(R.blocksOf({ read_at: '2026-11-19T08:00:00Z', queries: [{ source: 'hoistra', facts: [] }] })[0].readAt, '2026-11-19T08:00:00Z');
  for (const none of [null, undefined, {}, { queries: [] }, { queries: 'x' }]) assert.deepEqual(R.blocksOf(none), []);
});

test('a block with missing parts still has every part, empty', () => {
  const [b] = R.blocksOf({ queries: [{ source: 'mystery' }] });
  assert.deepEqual([b.facts, b.receipts, b.notes, b.unresolved, b.changed], [[], [], [], [], []]);
  assert.equal(b.sentence, '');
  assert.equal(b.sourceWords, '', 'a source Trace Lab has no words for says nothing');
});

test('the block says whose access it was read with', () => {
  const [a, b] = R.blocksOf(EV);
  assert.equal(R.scopeWords(a), "Read with me@x.test's access");
  assert.equal(R.scopeWords(b), 'Read for the whole company');
  assert.equal(R.scopeWords({ scope: 'asker', scopeEmail: null }), "Read with the asker's access");
  assert.equal(R.scopeWords({}), '');
});

test('when the rows changed since Hoistra answered, it says how many then and now', () => {
  const [a, b] = R.blocksOf(EV);
  assert.equal(R.changedWords(a), 'The records have changed since Hoistra answered: Hoistra read 3 rows, now 2');
  assert.equal(R.changedWords(b), '');
  assert.equal(R.changedWords({ changed: [{ label: 'focus Vendor', then: 1, now: 4 }] }),
    'The records have changed since Hoistra answered: Hoistra read 1 row, now 4');
  assert.equal(R.changedWords({ changed: [{ label: 'count', then: 3, now: 4 }, { label: 'focus Vendor', then: 5, now: 2 }] }),
    'The records have changed since Hoistra answered: Hoistra read 3 rows, now 4 (count); Hoistra read 5 rows, now 2 (focus Vendor)');
});

test('values of different queries share ids, so each is opened by its query’s number and its own id', () => {
  const [a, b] = R.blocksOf(EV);
  assert.notEqual(R.factKey(a, a.facts[0]), R.factKey(b, b.facts[0]));
  const found = R.findFact(R.blocksOf(EV), R.factKey(a, REC));
  assert.equal(found.block.index, 0);
  assert.equal(found.fact.value, 'Pennard Fire Services');
  assert.equal(found.receipt.id, 'q2');
  assert.equal(R.findFact(R.blocksOf(EV), '7:f1'), null);
  assert.equal(R.findFact(R.blocksOf(EV), null), null);
});

test('a value is opened with the receipt it points at, and one with no receipt has none', () => {
  const [a, b] = R.blocksOf(EV);
  assert.equal(R.receiptOf(a, COUNT).id, 'q1');
  assert.equal(R.receiptOf(a, REC).id, 'q2');
  assert.equal(R.receiptOf(b, b.facts[0]), null);
  assert.equal(R.receiptOf(a, { id: 'f9', receipt: 'q9' }), null);
});

test('each kind of value says in plain words how it was found', () => {
  assert.equal(R.explainOf({ kind: 'count', label: 'Vendor records found', value: 2 }), 'This number is how many records the query below found.');
  assert.equal(R.explainOf({ kind: 'record', value: 'Pennard Fire Services' }), 'This is one of the records the query below found.');
  assert.equal(R.explainOf({ kind: 'group', label: 'Bishopsgate Tower', value: 9 }), 'This number is how many records the query below counted for Bishopsgate Tower.');
  assert.equal(R.explainOf({ kind: 'missing', label: 'Not on record', value: 'ABC' }), 'The records do not hold ABC. The query below looked for it.');
  assert.equal(R.explainOf({ kind: 'stated', label: 'Contract value', value: 900 }), 'This value was read by the query below.');
  assert.equal(R.explainOf({}), 'This value was read by the query below.');
});
