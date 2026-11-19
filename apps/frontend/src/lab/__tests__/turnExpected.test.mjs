// turnExpected — a live question's expected answer on the session page (14 Nov 2026, Hussain): drafted from SPEC.md
// by the system when the question is asked on the live platform, accepted or edited by a person. Live turns only:
// nothing in the Codex flow changes.
import { test, after } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { bundle, cleanBundles } from '../../tracelab/__tests__/esbuild-helper.mjs';

globalThis.window = globalThis.window || { location: { origin: 'http://test.local', pathname: '/lab/', search: '' } };
after(cleanBundles);
const React = (await import('react')).default;
const { renderToStaticMarkup } = await import('react-dom/server');
const TE = await bundle('../lab/TurnExpected.jsx');
const QE = await bundle('../lab/QueryEditor.jsx');
const QF = await import('../logic/queryForm.js');
const R2 = await import('../logic/recordsValues.js');
const html = (p) => renderToStaticMarkup(React.createElement(TE.ExpectedView, p || {}));
const src = (f) => readFileSync(new URL('../' + f, import.meta.url), 'utf-8');

const DRAFT = { question: 'R53-3', live: true, draft: true, from_draft: false, decision: null, spec_version: 3, rules: ['S-1', 'S-4'],
  expected: { id: 'e1', expected: 'Lists each overdue certificate with its days overdue, as a list.', source: 'coding_agent' } };

test('a live turn shows its draft from SPEC.md with Accept and Edit', () => {
  const out = html({ data: DRAFT, onAccept() {}, onEdit() {} });
  assert.ok(out.includes('Expected answer') && out.includes('Lists each overdue certificate'));
  assert.ok(out.includes('Drafted from SPEC.md v3 · rules S-1, S-4 · not accepted yet'));
  assert.match(out, />Accept</);
  assert.match(out, />Edit</);
});

test('an accepted draft and a person’s own version show who decided, with Edit only', () => {
  const took = html({ data: Object.assign({}, DRAFT, { draft: false, from_draft: true, rules: ['S-1'],
    expected: { id: 'e2', expected: 'Lists each overdue certificate.', source: 'manual', author_email: 'me@x.test' } }) });
  assert.ok(took.includes('Accepted from the SPEC.md draft · rule S-1 · me@x.test') && !/>Accept</.test(took) && />Edit</.test(took));
  const mine = html({ data: Object.assign({}, DRAFT, { draft: false, from_draft: false, rules: [],
    expected: { id: 'e3', expected: 'Typed.', source: 'manual', author_email: 'me@x.test' } }) });
  assert.ok(mine.includes('Written by me@x.test') && !mine.includes('SPEC.md'));
});

test('none yet offers Add expected answer; a draft that names no rule says so', () => {
  assert.match(html({ data: Object.assign({}, DRAFT, { expected: null, draft: false }) }), />Add expected answer</);
  assert.ok(html({ data: Object.assign({}, DRAFT, { rules: [] }) }).includes('Drafted from SPEC.md v3 · no rule cited · not accepted yet'));
});

test('Codex turns and turns with no question show nothing', () => {
  assert.equal(html({ data: Object.assign({}, DRAFT, { live: false }) }), '');
  assert.equal(html({ data: Object.assign({}, DRAFT, { question: null }) }), '');
  assert.equal(html({ data: null }), '');
});

test('editing shows the text box with Save and Cancel', () => {
  const out = html({ data: DRAFT, editing: true, draftText: 'My version', onSave() {}, onCancel() {}, onChange() {} });
  assert.ok(out.includes('My version') && />Save expected answer</.test(out) && />Cancel</.test(out));
});

test('Accept saves the draft as the person’s own version, citing its rule', () => {
  assert.deepEqual(TE.acceptBody(DRAFT), { expected: DRAFT.expected.expected,
    evidence: { spec_rule: 'S-1', drafted_from: 'SPEC.md', spec_version: 3 } });
  assert.deepEqual(TE.acceptBody(Object.assign({}, DRAFT, { rules: [] })).evidence, { drafted_from: 'SPEC.md', spec_version: 3 });
});

test('the panel sits under the turn, after why it failed, and reads its own route', () => {
  const page = src('SessionPage.jsx');
  assert.ok(page.indexOf('<WhyFailed') < page.indexOf('<TurnExpected') && page.includes('<TurnExpected turnId={trace.id}'));
  assert.match(src('labApi.js'), /turnExpected: \(id\) => call\('GET', '\/lab\/turns\/' \+ enc\(id\) \+ '\/expected'\)/);
});

// ── Values read from the records (16 Nov 2026, Hussain and Bala): every value can be clicked to see the query that
// found it, its filled-in values and the rows it read; anyone can copy it into psql to check, run it again, and mark
// the value right or wrong. Nobody edits the database: checks are saved in Trace Lab. ──────────────────────────────
const KEYED = { question: 'R53-3', live: true, draft: true, kind: 'answer_key', company: 'org-1', rules: [],
  expected: { id: 'k1', source: 'coding_agent', expected: '5 vendors are blocked: Pennard Fire Services, Apex Mechanical.',
    evidence: { drafted_from: 'answer_key', key: 'vendors_blocked', title: 'Blocked vendors', read_at: '2026-11-16T09:30:02+00:00',
      facts: [{ id: 'f1', label: 'Blocked vendors', value: 5, receipt: 'q1' }, { id: 'f2', label: '', value: 'Pennard Fire Services', receipt: 'q2' }],
      receipts: [
        { id: 'q1', label: 'count', sql: 'SELECT count(*) AS n FROM "vendors" AS f WHERE f.block_state IN (:p0)', params: { p0: 'Blocked' },
          row_count: 1, rows: [{ n: 5 }], psql: "BEGIN READ ONLY;\nSELECT count(*) ...;\nROLLBACK;" },
        { id: 'q2', label: 'focus Vendor', sql: 'SELECT f.vendor_name ...', params: {}, row_count: 5, psql: "BEGIN READ ONLY;\nSELECT f.vendor_name ...;\nROLLBACK;",
          rows: [{ vendor_name: 'Pennard Fire Services', block_state: 'Blocked' }, { vendor_name: 'Apex Mechanical', block_state: 'Blocked' }] }] } } };

test('values read from the records are shown as buttons, with a plain hint', () => {
  const out = html({ data: KEYED });
  assert.ok(out.includes('5 vendors are blocked'));
  assert.ok(out.includes('Read from the records') && out.includes('Click a value to see exactly how it was found.'));
  assert.match(out, /aria-label="See how Blocked vendors: 5 was found"/);
  assert.ok(!out.includes('See how Pennard Fire Services was found'), 'a list answer’s records open from its count (16 Nov 2026)');
  assert.match(out, />Accept</);
});

test('a clicked value shows its query, the values filled in and the rows it read, in plain words', () => {
  const out = html({ data: KEYED, openFact: 'f1', onRerun: () => {} });
  assert.ok(out.includes('How this value was found'));
  assert.ok(out.includes('These are the 5 records the query below found.'), 'the count opens the records (16 Nov 2026)');
  assert.ok(out.includes('SELECT f.vendor_name'));
  assert.ok(out.includes('5 rows') && out.includes('<td>Pennard Fire Services</td>') && out.includes('<td>Apex Mechanical</td>'));
  for (const b of ['Copy to check in the database', 'Run again now', 'This is right', 'This is wrong']) assert.match(out, new RegExp('>' + b + '<'));
  assert.ok(out.includes('It only reads: it cannot change anything.'));
});

test('running it again says whether the records changed since the question was asked', () => {
  const same = html({ data: KEYED, openFact: 'f1', rerun: { facts: [{ id: 'f1', label: 'Blocked vendors', value: 5 }], read_at: '2026-11-16T10:00:00Z' } });
  assert.ok(same.includes('Now: 5') && same.includes('the same as when the question was asked'));
  const moved = html({ data: KEYED, openFact: 'f1', rerun: { facts: [{ id: 'f1', label: 'Blocked vendors', value: 6 }], read_at: '2026-11-16T10:00:00Z' } });
  assert.ok(moved.includes('Now: 6') && moved.includes('was 5 when the question was asked: the records have changed since'));
});

test('marking a value wrong asks what it should be and why, and the checks are saved as the person’s version', () => {
  const out = html({ data: KEYED, openFact: 'f1', marks: { f1: { check: 'wrong', corrected: '4', note: '' } } });
  assert.ok(out.includes('What should it be?') && out.includes('Why is it wrong?') && />Save my checks</.test(out));
  const body = TE.checksBody(KEYED, { f1: { check: 'wrong', corrected: '4', note: 'Apex was cleared' }, f2: { check: 'right' } });
  assert.equal(body.expected, KEYED.expected.expected);
  assert.equal(body.evidence.drafted_from, 'answer_key');
  assert.deepEqual(body.evidence.facts.map((f) => [f.id, f.check, f.corrected || null, f.note || null]),
    [['f1', 'wrong', '4', 'Apex was cleared'], ['f2', 'right', null, null]]);
  assert.equal(body.evidence.receipts.length, 2, 'the receipts are kept with the person’s version');
});

test('a checked answer shows each value’s mark and who checked it', () => {
  const checked = Object.assign({}, KEYED, { draft: false, from_draft: true, expected: Object.assign({}, KEYED.expected, {
    source: 'manual', author_email: 'me@x.test',
    evidence: Object.assign({}, KEYED.expected.evidence, { facts: [
      { id: 'f1', label: 'Blocked vendors', value: 5, receipt: 'q1', check: 'wrong', corrected: '4', note: 'Apex was cleared' },
      { id: 'f2', label: '', value: 'Pennard Fire Services', receipt: 'q2', check: 'right' }] }) }) });
  const out = html({ data: checked });
  assert.ok(out.includes('Checked by me@x.test') && !/>Accept</.test(out));
  assert.ok(out.includes('should be 4'));
});

// ── Values read from the answer key (16 Nov 2026) ─────────────────────────────────────────────────────────────────────
test('a list answer shows one value, its count, and clicking it lists the records', () => {
  const ev = { drafted_from: 'answer_key', facts: [
      { id: 'f1', label: 'Certificates expiring within the next 60 days', value: 2, receipt: 'q1' },
      { id: 'f2', label: '', value: 'Gas SYN-GAS-1 (expiry 2026-12-28)', receipt: 'q2' },
      { id: 'f3', label: '', value: 'EL SYN-EL-2 (expiry 2026-12-21)', receipt: 'q2' }],
    receipts: [{ id: 'q1', label: 'count', sql: 'SELECT count(*)', row_count: 1, rows: [{ n: 2 }] },
      { id: 'q2', label: 'focus ComplianceCertificate', sql: 'SELECT ...', row_count: 2,
        rows: [{ id: 'u1', __key: 'u1', __sort: '2026-12-28', issuer: 'Reg', status: 'Valid', certificate_number: 'SYN-GAS-1', cert_type: 'Gas', expiry_date: '2026-12-28', certificate_ref: null },
          { id: 'u2', __key: 'u2', __sort: '2026-12-21', issuer: 'Ins', status: 'Valid', certificate_number: 'SYN-EL-2', cert_type: 'EL', expiry_date: '2026-12-21' }] }] };
  const data = Object.assign({}, KEYED, { expected: Object.assign({}, KEYED.expected, { evidence: ev }) });
  const shut = html({ data });
  assert.equal((shut.match(/class="lab-xp-chip[ "]/g) || []).length, 1, 'only the count is a value to click');
  const out = html({ data, openFact: 'f1' });
  assert.ok(out.includes('SYN-GAS-1') && out.includes('SYN-EL-2'), 'the records themselves');
  assert.ok(out.includes('These are the 2 records the query below found.'));
  assert.ok(!out.includes('>__key<') && !out.includes('>__sort<') && !out.includes('>id<'), 'no internal columns');
  assert.ok(out.indexOf('>certificate_number<') < out.indexOf('>issuer<'), 'the readable columns first');
  assert.ok(!out.includes('>certificate_ref<'), 'a column empty in every row is left out');
});

test('the SQL is tucked away behind a plain explanation and the records', () => {
  const out = html({ data: KEYED, openFact: 'f1' });
  assert.ok(out.includes('<details') && out.includes('Show the query (technical)'));
  assert.ok(out.indexOf('What it found') < out.indexOf('Show the query (technical)'), 'the records come before the query');
});

// ── Review fixes before commit (16 Nov 2026) ──
test('Run again now shows only where there is a key to run again', () => {
  const corrected = Object.assign({}, KEYED, { expected: Object.assign({}, KEYED.expected, {
    evidence: Object.assign({}, KEYED.expected.evidence, { key: undefined, source: 'corrected' }) }) });
  assert.ok(!/>Run again now</.test(html({ data: corrected, openFact: 'f1', onRerun: () => {} })));
  assert.match(html({ data: KEYED, openFact: 'f1', onRerun: () => {} }), />Run again now</);
});

// ── Values read from the records, with their source (19 Nov 2026, Hussain and Bala): one block per query Hoistra (or Trace
// Lab) read - where the query came from, whose access it was read with, the sentence, a button per value that opens the
// receipt (the rows, the query, Copy to check, Run again now), and how the query was read. ────────────────────────────
const RQ1 = { id: 'q1', label: 'count', sql: 'SELECT count(*) AS n FROM "vendors" AS f WHERE f.block_state IN (:p0)', params: { p0: 'Blocked' },
  row_count: 1, rows: [{ n: 2 }], psql: 'BEGIN READ ONLY;\nSELECT count(*) ...;\nROLLBACK;' };
const RQ2 = { id: 'q2', label: 'focus Vendor', sql: 'SELECT f.vendor_name FROM "vendors" AS f', params: {}, row_count: 2,
  rows: [{ vendor_name: 'Pennard Fire Services', block_state: 'Blocked' }, { vendor_name: 'Apex Mechanical', block_state: 'Blocked' }],
  psql: 'BEGIN READ ONLY;\nSELECT f.vendor_name ...;\nROLLBACK;' };
const SENTENCE = 'Vendor records found: 2. Vendors: Pennard Fire Services, Apex Mechanical.';
const RECORDS = { question: 'R53-3', live: true, draft: true, from_draft: false, kind: 'records', company: 'org-1', rules: [],
  expected: { id: 'r1', source: 'coding_agent', expected: SENTENCE + ' Not on record: ABC',
    evidence: { drafted_from: 'records', draft: 3, read_at: '2026-11-19T09:30:02+00:00', queries: [
      { source: 'hoistra', question: 'Which vendors are blocked?', plan: { focus: { concept: 'Vendor' } }, found: true, unresolved: [],
        notes: ['Read "blocked" as block_state = Blocked.', 'Also read, as always for this kind of record: block_reason'],
        read_at: '2026-11-19T09:30:01+00:00', sentence: SENTENCE, scope: 'asker', scope_email: 'me@x.test',
        changed: [{ label: 'focus Vendor', then: 3, now: 2 }],
        facts: [{ id: 'f1', kind: 'count', label: 'Vendor records found', value: 2, receipt: 'q1' },
          { id: 'f2', kind: 'record', label: '', value: 'Pennard Fire Services', receipt: 'q2' },
          { id: 'f3', kind: 'record', label: '', value: 'Apex Mechanical', receipt: 'q2' }], receipts: [RQ1, RQ2] },
      { source: 'proposed', question: 'Which vendors are blocked?', plan: { focus: { concept: 'Vendor' } }, found: false, unresolved: ['ABC'], notes: [],
        read_at: '2026-11-19T09:30:02+00:00', sentence: 'Not on record: ABC', scope: 'company', scope_email: null, changed: [],
        facts: [{ id: 'f1', kind: 'missing', label: 'Not on record', value: 'ABC', receipt: 'q1' }],
        receipts: [{ id: 'q1', label: 'lookup Vendor', sql: 'SELECT f.vendor_name FROM "vendors" AS f WHERE f.vendor_name = :p0', params: { p0: 'ABC' }, row_count: 0, rows: [], psql: 'BEGIN READ ONLY;' }] }] } } };

test('values read from the records are one block per query, each with where it came from', () => {
  const out = html({ data: RECORDS });
  assert.ok(out.includes('Hoistra’s query') && out.includes('Proposed for this question - check it before accepting'));
  assert.equal((out.match(/class="lab-xp-query[ "]/g) || []).length, 2, 'one block per query');
  assert.ok(out.includes(SENTENCE));
  assert.match(out, /Read \d{1,2} [A-Z][a-z]{2}, \d{2}:\d{2}/, 'the time the block was read');
  assert.ok(out.includes('Read from the records') && out.includes('· not accepted yet'));
  assert.ok(out.includes('Click a value to see exactly how it was found.'));
  assert.match(out, />Accept</);
  assert.match(out, />Edit</);
});

test('every value of a block is a button, and the block says how its query was read', () => {
  const out = html({ data: RECORDS });
  for (const text of ['Vendor records found: 2', 'Pennard Fire Services', 'Apex Mechanical', 'Not on record: ABC']) {
    assert.ok(out.includes('aria-label="See how ' + text + ' was found"'), text);
  }
  assert.ok(out.includes('How the query was read'));
  assert.ok(out.includes('Read &quot;blocked&quot; as block_state = Blocked.') && out.includes('Also read, as always for this kind of record: block_reason'));
  assert.equal((out.match(/How the query was read/g) || []).length, 1, 'a block with no notes has no such section');
});

test('each block says whose access it was read with, and when the records changed since Hoistra answered', () => {
  const out = html({ data: RECORDS });
  assert.ok(out.includes("Read with me@x.test&#x27;s access") && out.includes('Read for the whole company'));
  assert.ok(out.includes('The records have changed since Hoistra answered: Hoistra read 3 rows, now 2'));
  assert.equal((out.match(/The records have changed since/g) || []).length, 1, 'only the block whose rows changed');
});

test('a proposed query is marked as one to check before it is accepted', () => {
  const out = html({ data: RECORDS });
  assert.match(out, /class="lab-xp-source lab-xp-proposed"[^>]*>Proposed for this question - check it before accepting</);
  assert.match(out, /class="lab-xp-source"[^>]*>Hoistra’s query</);
});

test('a value that is not on record reads that way', () => {
  const out = html({ data: RECORDS });
  assert.ok(out.includes('Not on record: ABC'));
  assert.ok(out.includes('>Not on record: ABC<'), 'as one piece of text');
});

test('a value is opened by clicking it: the query’s rows, the query, copy it, run it again, and the values filled in', () => {
  const out = html({ data: RECORDS, openFact: '0:f2', onRerun: () => {}, onCopy: () => {} });
  assert.ok(out.includes('How this value was found') && out.includes('This is one of the records the query below found.'));
  assert.ok(out.includes('2 rows') && out.includes('<td>Pennard Fire Services</td>') && out.includes('<td>Apex Mechanical</td>'));
  assert.ok(out.includes('SELECT f.vendor_name FROM'));
  for (const b of ['Copy to check in the database', 'Run again now']) assert.match(out, new RegExp('>' + b + '<'));
  assert.ok(out.includes('aria-expanded="true"'));
  const count = html({ data: RECORDS, openFact: '0:f1' });
  assert.ok(count.includes('This number is how many records the query below found.') && count.includes('f.block_state IN (:p0)') && count.includes('<th>p0</th><td>Blocked</td>'));
});

test('two queries’ values share ids but open their own receipts', () => {
  const second = html({ data: RECORDS, openFact: '1:f1', onRerun: () => {} });
  assert.ok(second.includes('The records do not hold ABC. The query below looked for it.') && second.includes('WHERE f.vendor_name = :p0'));
  assert.ok(!second.includes('<td>Pennard Fire Services</td>'), 'not the first query’s rows');
});

test('a value with no query to show has no link and says so', () => {
  const data = JSON.parse(JSON.stringify(RECORDS));
  data.expected.evidence.queries[1].facts[0].receipt = null;
  const out = html({ data, openFact: '1:f1' });
  assert.ok(!out.includes('aria-label="See how Not on record: ABC was found"'));
  assert.ok(out.includes('Not on record: ABC') && out.includes('no query to show'));
  assert.ok(!out.includes('How this value was found'));
});

test('Run again now shows where there is a query to read again, and says whether the records changed', () => {
  assert.ok(!/>Run again now</.test(html({ data: RECORDS, openFact: '0:f1' })), 'no action to run it: no button');
  const none = JSON.parse(JSON.stringify(RECORDS));
  none.expected.evidence.queries[0].plan = null;
  assert.ok(!/>Run again now</.test(html({ data: none, openFact: '0:f1', onRerun: () => {} })), 'no stored query: nothing to run');
  const moved = html({ data: RECORDS, openFact: '0:f1', rerun: { index: 0, facts: [{ id: 'f1', kind: 'count', label: 'Vendor records found', value: 3 }], read_at: '2026-11-19T10:00:00Z' } });
  assert.ok(moved.includes('Now: 3') && moved.includes('was 2 when the question was asked: the records have changed since'));
  const other = html({ data: RECORDS, openFact: '1:f1', rerun: { index: 0, facts: [{ id: 'f1', kind: 'count', label: 'Vendor records found', value: 3 }] } });
  assert.ok(!other.includes('Now: 3') && !other.includes('no longer found'), 'a re-run of another query says nothing here');
});

test('Run again now pairs a value by what it is, and says when it is no longer found', () => {
  // A new figure first moves every id after it: the record that was f2 is f3 now.
  const shifted = { index: 0, read_at: '2026-11-19T10:00:00Z', facts: [{ id: 'f1', kind: 'count', label: 'Vendor records found', value: 2, receipt: 'q1' },
    { id: 'f2', kind: 'stated', label: 'Pennard states vendors', value: 9, receipt: 'q1' }, { id: 'f3', kind: 'record', label: '', value: 'Pennard Fire Services', receipt: 'q2' }] };
  const same = html({ data: RECORDS, openFact: '0:f2', rerun: shifted });
  assert.ok(same.includes('Now: Pennard Fire Services · the same as when the question was asked'), 'not the figure that took its place');
  const gone = html({ data: RECORDS, openFact: '0:f3', rerun: shifted });
  assert.ok(gone.includes('Now: no longer found') && !gone.includes('Now: Pennard'), 'Apex Mechanical is not in the fresh read');
  const count = html({ data: KEYED, openFact: 'f1', rerun: { facts: [{ id: 'f1', label: 'Some other figure', value: 5 }] } });
  assert.ok(count.includes('Now: no longer found'));
});

test('Accept saves the values as the person’s version, with the evidence as it was read', () => {
  const body = TE.acceptBody(RECORDS);
  assert.equal(body.expected, RECORDS.expected.expected);
  assert.deepEqual(body.evidence, Object.assign({}, RECORDS.expected.evidence, { drafted_from: 'records' }));
  assert.equal(body.evidence.queries.length, 2, 'every query with its receipts');
});

test('the answer’s source line says it was read from the records, then who accepted it', () => {
  const took = Object.assign({}, RECORDS, { draft: false, from_draft: true, expected: Object.assign({}, RECORDS.expected, { source: 'manual', author_email: 'me@x.test' }) });
  const out = html({ data: took });
  assert.ok(out.includes('Accepted from the records · me@x.test') && !/>Accept</.test(out) && />Edit</.test(out));
  assert.ok(out.includes('aria-label="See how Pennard Fire Services was found"'), 'its values still open');
});

test('values can be marked right or wrong, each in its own query, and the checks are saved with the evidence', () => {
  const out = html({ data: RECORDS, openFact: '1:f1', marks: { '1:f1': { check: 'wrong', corrected: 'ABC Ltd', note: '' } }, onMark: () => {} });
  assert.ok(out.includes('This is right') && out.includes('This is wrong') && out.includes('What should it be?') && />Save my checks</.test(out));
  assert.ok(out.includes('Wrong, should be ABC Ltd'));
  const body = TE.checksBody(RECORDS, { '1:f1': { check: 'wrong', corrected: 'ABC Ltd', note: 'Typo' }, '0:f2': { check: 'right' } });
  assert.equal(body.expected, RECORDS.expected.expected);
  assert.equal(body.evidence.drafted_from, 'records');
  assert.deepEqual(body.evidence.queries[0].facts.map((f) => [f.id, f.check || null]), [['f1', null], ['f2', 'right'], ['f3', null]]);
  assert.deepEqual(body.evidence.queries[1].facts.map((f) => [f.id, f.check, f.corrected, f.note]), [['f1', 'wrong', 'ABC Ltd', 'Typo']]);
  assert.equal(body.evidence.queries[0].receipts.length, 2, 'the receipts are kept with the person’s version');
});

test('a checked records answer shows each value’s mark and who checked it', () => {
  const checked = JSON.parse(JSON.stringify(RECORDS));
  Object.assign(checked, { draft: false, from_draft: true });
  Object.assign(checked.expected, { source: 'manual', author_email: 'me@x.test' });
  checked.expected.evidence.queries[0].facts[1].check = 'right';
  const out = html({ data: checked });
  assert.ok(out.includes('Checked by me@x.test') && out.includes('· Right'));
});

test('a draft with one query and one sentence shows the sentence once', () => {
  const one = JSON.parse(JSON.stringify(RECORDS));
  one.expected.evidence.queries = one.expected.evidence.queries.slice(0, 1);
  one.expected.expected = SENTENCE;
  assert.equal(html({ data: one }).split(SENTENCE).length - 1, 1);
});

test('the answer-key view and the SPEC.md draft are not changed by the records view', () => {
  assert.ok(!html({ data: KEYED }).includes('lab-xp-query'));
  assert.ok(!html({ data: DRAFT }).includes('lab-xp-query'));
});

// ── Fix round 1 (19 Nov 2026): Run again now on the first query must say so. `?index=0` is not "no index": an answer key's
// re-run sends none, a records draft's re-run always names the query it reads again. ─────────────────────────────────
test('Run again now names the query it reads again, the first one included', async () => {
  const { labApi } = await import('../labApi.js');
  const calls = [];
  const real = globalThis.fetch;
  globalThis.fetch = async (url, opts) => {
    calls.push({ url: new URL(String(url)), method: (opts && opts.method) || 'GET' });
    return { ok: true, status: 200, statusText: 'OK', text: async () => '{"found":true}', headers: { get: () => null } };
  };
  try {
    await labApi.turnExpectedCheck('t1', 0);
    await labApi.turnExpectedCheck('t1', 2);
    await labApi.turnExpectedCheck('t1');
  } finally {
    globalThis.fetch = real;
  }
  assert.equal(calls.length, 3);
  for (const c of calls) {
    assert.equal(c.method, 'POST');
    assert.equal(c.url.pathname, '/backend/trace-lab/api/trace-lab/lab/turns/t1/expected/check');
  }
  assert.equal(calls[0].url.searchParams.get('index'), '0', 'the first query is named too');
  assert.equal(calls[1].url.searchParams.get('index'), '2');
  assert.equal(calls[2].url.searchParams.has('index'), false, 'the answer key’s re-run names none');
});

// ── The query editor (19 Nov 2026, Hussain and Bala): an editor over every part of the records query. A curator changes
// what was read, runs it, sees Hoistra's values beside their own, and uses the result as the expected answer. Only
// curators and the platform team may run a query (`can_run`); everyone else sees the values and their receipts. ────────
const MAPE = {
  concepts: [
    { name: 'Building', words: 'buildings', attributes: [{ name: 'name', kind: 'text' }, { name: 'city', kind: 'text' }], states: [], links: ['WorkOrder'] },
    { name: 'WorkOrder', words: 'work orders', attributes: [{ name: 'raised_at', kind: 'date' }, { name: 'status', kind: 'text' }, { name: 'cost', kind: 'number' }],
      states: [{ id: 'open', description: 'not finished (status not in Completed)' }, { id: 'overdue', description: 'sla_due_at passed and completed_at empty' },
        { id: 'closed', description: 'finished (status in Completed)' }], links: ['Building', 'Vendor'] },
    { name: 'Vendor', words: 'vendors', attributes: [{ name: 'vendor_name', kind: 'text' }], states: [{ id: 'live', description: 'status = Live' }], links: ['WorkOrder'] }],
  ops: [{ id: 'eq', words: 'is' }, { id: 'ne', words: 'is not' }, { id: 'in', words: 'is one of' }, { id: 'not_in', words: 'is not one of' }, { id: 'lt', words: 'is before' },
    { id: 'lte', words: 'is on or before' }, { id: 'gt', words: 'is after' }, { id: 'gte', words: 'is on or after' }, { id: 'between', words: 'is between' },
    { id: 'within', words: 'is within' }, { id: 'contains', words: 'contains' }, { id: 'is_null', words: 'is empty' }],
  periods: ['today', 'this_month', 'last_month', 'next_month', 'next_30_days']
};
const PLAN_A = { question_type: 'list', aggregate: null,
  focus: { concept: 'WorkOrder', match: null, states: ['open', 'overdue'], via: [], filters: [
    { attribute: 'raised_at', op: 'within', value: 'next_60_days' }, { attribute: 'status', op: 'in', value: ['Open', 'Late'] },
    { attribute: 'cost', op: 'between', value: [10, 20] }, { attribute: 'status', op: 'is_null', value: false }] },
  constraints: [{ concept: 'Building', match: 'Bishopsgate Tower', via: [], states: [], filters: [] }],
  include: [{ name: 'vendor', concept: 'Vendor', via: [], states: [], filters: [], sort: { attribute: 'vendor_name', dir: 'asc' }, limit: 5 }] };
const PLAN_B = { question_type: 'count', focus: { concept: 'Building', match: 'ABC', states: [], filters: [], via: [] }, constraints: [], include: [],
  aggregate: { count_by: [{ concept: 'Building', attribute: 'city' }] } };
const QRY = { ok: true, ready: true, can_run: true, none: null, map: MAPE, queries: [
  { index: 0, source: 'hoistra', source_words: 'Hoistra’s query', question: 'Which work orders are open in Bishopsgate Tower?', plan: PLAN_A },
  { index: 1, source: 'proposed', source_words: 'Proposed for this question - check it before accepting', question: 'How many buildings are there?', plan: PLAN_B }] };
const el = (out, label) => (new RegExp('<(?:input|select|button|textarea)[^>]*aria-label="' + label.replace(/[.*+?^${}()|[\]\\]/g, '\\$&') + '"[^>]*>').exec(out) || [''])[0];
const sel = (out, label) => (new RegExp('<select[^>]*aria-label="' + label.replace(/[.*+?^${}()|[\]\\]/g, '\\$&') + '"[^>]*>(.*?)</select>', 's').exec(out) || ['', ''])[1];
const view = (p) => renderToStaticMarkup(React.createElement(QE.QueryEditorView, Object.assign({ query: QRY }, p || {})));
const editor = (p) => renderToStaticMarkup(React.createElement(QE.default, Object.assign({ query: QRY }, p || {})));

test('a turn with several queries has one tab for each, and shows the fields of the one open', () => {
  const out = view();
  assert.equal((out.match(/role="tab"/g) || []).length, 2);
  assert.match(out, /role="tablist"/);
  assert.ok(out.includes('Query 1') && out.includes('Query 2') && out.includes('Hoistra’s query') && out.includes('Proposed for this question - check it before accepting'));
  assert.equal((out.match(/aria-selected="true"/g) || []).length, 1);
  assert.match(out, /<button[^>]*aria-selected="true"[^>]*>[^]*?Query 1/);
  assert.ok(out.includes('Which work orders are open in Bishopsgate Tower?'), 'the question the query was made for');
  assert.ok(el(out, 'Name or code').includes('placeholder="none"') && sel(out, 'What').includes('<option value="WorkOrder" selected="">work orders</option>'));
  const second = view({ tab: 1 });
  assert.match(second, /<button[^>]*aria-selected="true"[^>]*>[^]*?Query 2/);
  assert.ok(sel(second, 'What').includes('<option value="Building" selected="">buildings</option>'), 'the second query’s own fields');
});

test('a turn with one query has no tabs', () => {
  const out = view({ query: Object.assign({}, QRY, { queries: QRY.queries.slice(0, 1) }) });
  assert.ok(!out.includes('role="tablist"') && !out.includes('role="tab"'));
  assert.ok(out.includes('Hoistra’s query') && out.includes('aria-label="What"'));
});

test('the fields are every part of the query: kind of answer, what, name or code, states, conditions, limits, grouping and linked records', () => {
  const out = view();
  for (const legend of ['Kind of answer', 'States', 'Conditions', 'Limited to', 'Grouped by', 'Also show']) assert.ok(out.includes('>' + legend + '<'), legend);
  assert.ok(out.includes('>What<') && out.includes('>Name or code<'));
  assert.ok(sel(out, 'Kind of answer').includes('<option value="list" selected="">A list of records</option>') && sel(out, 'Kind of answer').includes('>A count<') && sel(out, 'Kind of answer').includes('>One record<'));
  assert.ok(sel(out, 'What').includes('<option value="WorkOrder" selected="">work orders</option>') && sel(out, 'What').includes('>buildings<') && sel(out, 'What').includes('>vendors<'));
  assert.ok(el(out, 'Name or code').includes('placeholder="none"'));
  assert.ok(el(out, 'Limited to 1: name or code').includes('value="Bishopsgate Tower"'));
});

test('the states are tick boxes with what each means, in the map’s order, and the ones in the query are ticked', () => {
  const out = view();
  assert.match(el(out, 'open'), /checked=""/);
  assert.match(el(out, 'overdue'), /checked=""/);
  assert.doesNotMatch(el(out, 'closed'), /checked=""/);
  assert.ok(out.includes('not finished') && out.includes('sla due at passed and completed at empty'), 'what a status means, in plain words');
  assert.ok(out.indexOf('aria-label="open"') < out.indexOf('aria-label="overdue"') && out.indexOf('aria-label="overdue"') < out.indexOf('aria-label="closed"'));
  assert.ok(out.includes('Tick none to include every state'));
});

test('a condition is a column, a condition in words and a value that fits the column', () => {
  const out = view();
  assert.ok(sel(out, 'Condition 1: column').includes('<option value="raised_at" selected="">raised date</option>') && sel(out, 'Condition 1: column').includes('>status<'));
  const how = sel(out, 'Condition 1: how');
  for (const w of ['is before', 'is after', 'is between', 'is within', 'is empty', 'is not empty']) assert.ok(how.includes('>' + w + '<'), w);
  assert.ok(how.includes('<option value="within" selected="">is within</option>'));
  assert.ok(sel(out, 'Condition 1: period').includes('<option value="next" selected="">the next</option>') && sel(out, 'Condition 1: period').includes('>this month<'));
  assert.ok(el(out, 'Condition 1: days').includes('value="60"') && out.includes('>days<'));
  assert.ok(el(out, 'Condition 2: value').includes('value="Open, Late"'), 'a list is typed with commas');
  assert.ok(sel(out, 'Condition 2: how').includes('<option value="in" selected="">is one of</option>'));
  assert.ok(el(out, 'Condition 3: from').includes('value="10"') && el(out, 'Condition 3: to').includes('value="20"'));
  assert.ok(el(out, 'Condition 3: from').includes('type="number"'), 'a number column takes a number');
  assert.ok(sel(out, 'Condition 4: how').includes('<option value="not_null" selected="">is not empty</option>'));
  assert.equal(el(out, 'Condition 4: value'), '', 'nothing to type for is empty');
  assert.match(el(out, 'Remove condition 1'), /<button/);
  assert.match(out, />Add a condition</);
  assert.ok(out.includes('list="lab-xe-dates"') === false, 'no date column is typed in this query');
  const dated = QF.formOf(PLAN_A);
  dated.focus.conditions = [{ attribute: 'raised_at', op: 'gt', value: 'today+30d' }];
  const date = view({ forms: { 0: dated } });
  assert.ok(el(date, 'Condition 1: value').includes('value="today+30d"') && el(date, 'Condition 1: value').includes('list="lab-xe-dates"'), 'a date takes a date, today+30d or a period');
  assert.ok(date.includes('<datalist id="lab-xe-dates"><option value="today"></option><option value="today+30d"></option>'));
});

test('limits, groupings and linked records each have their own fields, with add and remove', () => {
  const out = view();
  assert.ok(sel(out, 'Limited to 1: kind of record').includes('<option value="Building" selected="">buildings</option>'));
  assert.match(el(out, 'Remove limit 1'), /<button/);
  assert.match(out, />Add a limit</);
  assert.match(out, />Add a grouping</);
  assert.ok(sel(out, 'Also show 1: kind of record').includes('<option value="Vendor" selected="">vendors</option>'));
  assert.ok(sel(out, 'Also show 1: sort by').includes('<option value="vendor_name" selected="">vendor name</option>') && sel(out, 'Also show 1: sort by').includes('>Not sorted<'));
  assert.ok(sel(out, 'Also show 1: order').includes('<option value="asc" selected="">lowest or oldest first</option>'));
  assert.ok(el(out, 'Also show 1: how many').includes('value="5"'));
  assert.match(el(out, 'Remove linked records 1'), /<button/);
  assert.match(out, />Add linked records</);
  const grouped = view({ tab: 1 });
  assert.ok(sel(grouped, 'Grouped by 1: kind of record').includes('<option value="Building" selected="">buildings</option>'));
  assert.ok(sel(grouped, 'Grouped by 1: column').includes('<option value="city" selected="">city</option>'));
  assert.match(el(grouped, 'Remove grouping 1'), /<button/);
});

test('the query is read out in words, and so is the person’s version once it differs', () => {
  const out = view();
  assert.ok(out.includes('Hoistra looked for a list of work orders that are open or overdue where the raised date is within the next 60 days'));
  assert.ok(!out.includes('Your version'));
  const mine = QF.formOf(PLAN_A);
  mine.focus.states = ['overdue'];
  const changed = view({ forms: { 0: mine } });
  assert.ok(changed.includes('Your version looks for a list of work orders that are overdue where'));
  assert.ok(changed.includes('Hoistra looked for a list of work orders that are open or overdue'));
  const proposed = view({ tab: 1 });
  assert.ok(proposed.includes('The proposed query looks for a count of buildings matching “ABC”'));
  const fixed = view({ query: Object.assign({}, QRY, { queries: [Object.assign({}, QRY.queries[0], { source: 'answer_key' })] }) });
  assert.ok(fixed.includes('Trace Lab’s fixed query looks for a list of work orders'));
});

test('Run reads the query as it stands; with something missing it cannot run and says what', () => {
  const out = view();
  assert.match(out, /<button[^>]*>Run with my changes</);
  assert.doesNotMatch(out.match(/<button[^>]*>Run with my changes</)[0], /disabled=""/);
  const bad = QF.formOf(PLAN_A);
  bad.focus.conditions[0] = { attribute: 'raised_at', op: 'gt', value: '' };
  const held = view({ forms: { 0: bad } });
  assert.match(held.match(/<button[^>]*>Run with my changes</)[0], /disabled=""/);
  assert.ok(held.includes('What, condition 1: give a value.'));
  assert.match(view({ busy: 0 }).match(/<button[^>]*>Reading…</)[0], /disabled=""/);
  assert.match(out, />Start again</);
});

const SIDE_B = { found: true, sentence: 'Work order records found: 14.', unresolved: [], notes: ['Deleted vendors are left out.'], read_at: '2026-11-19T10:00:00+00:00',
  facts: [{ id: 'f1', kind: 'count', label: 'Work order records found', value: 14, receipt: 'q1' }, { id: 'f2', kind: 'record', label: '', value: 'WO-4527', receipt: 'q2' }],
  receipts: [{ id: 'q1', label: 'count', sql: 'SELECT count(*) AS n FROM "work_orders" AS f', params: {}, row_count: 1, rows: [{ n: 14 }], psql: 'BEGIN READ ONLY;' },
    { id: 'q2', label: 'focus WorkOrder', sql: 'SELECT f.title FROM "work_orders" AS f', params: {}, row_count: 1, rows: [{ title: 'WO-4527' }], psql: 'BEGIN READ ONLY;' }] };
const SIDE_A = { found: true, sentence: 'Work order records found: 9.', unresolved: [], notes: [], read_at: '2026-11-19T10:00:05+00:00',
  facts: [{ id: 'f1', kind: 'count', label: 'Work order records found', value: 9, receipt: 'q1' }],
  receipts: [{ id: 'q1', label: 'count', sql: 'SELECT count(*) AS n FROM "work_orders" AS f WHERE f.status = :p0', params: { p0: 'Open' }, row_count: 1, rows: [{ n: 9 }], psql: 'BEGIN READ ONLY;' }] };
const RAN = { before: SIDE_B, after: SIDE_A, changes: ['States: open, overdue → overdue'], plan: PLAN_A };

test('Hoistra’s side that could not be read says why, not that it found nothing', () => {
  const failed = { before: { found: false, facts: [], receipts: [], read_at: null, unresolved: [], notes: [], sentence: '', plan: PLAN_A,
    error: 'The records could not be read just now. Try again in a minute.' }, after: SIDE_A, changes: [], plan: PLAN_A };
  const out = view({ results: { 0: failed } });
  assert.match(out, /<p[^>]*role="alert"[^>]*>The records could not be read just now\. Try again in a minute\.<\/p>/);
  assert.ok(out.includes('Work order records found: 9.'), 'the person’s version still shows what it found');
});

test('the result puts what Hoistra’s query found beside what the person’s finds, each value a button to its receipt', () => {
  const out = view({ results: { 0: RAN } });
  assert.ok(out.includes('Hoistra’s query found') && out.includes('Your version finds'));
  assert.ok(out.indexOf('Hoistra’s query found') < out.indexOf('Your version finds'));
  assert.ok(out.includes('Work order records found: 14.') && out.includes('Work order records found: 9.'));
  assert.match(out, /class="lab-xe-cols"/);
  assert.equal((out.match(/aria-label="See how Work order records found: (14|9) was found"/g) || []).length, 2);
  assert.ok(out.includes('aria-label="See how WO-4527 was found"'));
  assert.ok(out.includes('States: open, overdue → overdue'), 'what changed, in words');
  assert.match(out, />Use as the expected answer</);
  assert.ok(out.includes('Deleted vendors are left out.'), 'how Hoistra’s query was read');
  assert.ok(!/\b(pass|fail|correct|wrong|right)\b/i.test(out.slice(out.indexOf('lab-xe-result'))), 'Trace Lab does not judge the values');
});

test('a value opens how it was found, on whichever side it is', () => {
  const mine = view({ results: { 0: RAN }, openKey: 'after:f1', onCopy: () => {} });
  assert.ok(mine.includes('How this value was found') && mine.includes('f.status = :p0') && mine.includes('<th>p0</th><td>Open</td>'));
  assert.ok(mine.includes('aria-expanded="true"'));
  assert.ok(!mine.includes('SELECT f.title'), 'only that value’s query');
  const theirs = view({ results: { 0: RAN }, openKey: 'before:f2' });
  assert.ok(theirs.includes('This is one of the records the query below found.') && theirs.includes('SELECT f.title FROM'));
  assert.ok(!/>Run again now</.test(theirs) && !/This is right/.test(theirs), 'no marks or re-runs inside the editor');
});

test('the person’s version can be used only as it was run', () => {
  assert.match(view({ results: { 0: RAN } }).match(/<button[^>]*>Use as the expected answer</)[0], /^(?!.*disabled)/s);
  const edited = QF.formOf(PLAN_A);
  edited.focus.match = 'changed after the run';
  const out = view({ results: { 0: RAN }, forms: { 0: edited } });
  assert.match(out.match(/<button[^>]*>Use as the expected answer</)[0], /disabled=""/);
  assert.ok(out.includes('You changed the query after this run. Run it again to see what it finds.'));
  assert.match(view({ results: { 0: RAN }, saving: true }).match(/<button[^>]*>Saving…</)[0], /disabled=""/);
  const missing = view({ results: { 0: Object.assign({}, RAN, { after: Object.assign({}, SIDE_A, { found: false, unresolved: ['ABC'], sentence: 'Not on record: ABC' }) }) } });
  assert.ok(!missing.includes('Use as the expected answer'));
  assert.ok(missing.includes('The records do not hold ABC. Check the spelling.'));
});

test('a result belongs to its own tab', () => {
  const out = view({ results: { 0: RAN }, tab: 1 });
  assert.ok(!out.includes('Your version finds'));
});

test('the words the records engine gives for a query it refuses sit beside the editor', () => {
  const out = view({ errors: { 0: { reason: 'bad_query', words: ['What: Floor has no state \'open\'.', 'Condition 2 names a column the records do not have.'] } } });
  assert.match(out, /<aside[^>]*role="alert"[^>]*class="lab-xe-side[^"]*"|<aside[^>]*class="lab-xe-side[^"]*"[^>]*role="alert"/);
  assert.ok(out.includes('This query cannot run') && out.includes('Floor has no state &#x27;open&#x27;.') && out.includes('Condition 2 names a column the records do not have.'));
  assert.ok(out.indexOf('lab-xe-fields') < out.indexOf('lab-xe-side'), 'the fields come first, the words beside them');
  const other = view({ errors: { 0: { reason: '', words: ['Could not run it: the service did not answer.'] } } });
  assert.ok(other.includes('Could not run it: the service did not answer.') && !other.includes('This query cannot run'));
  assert.ok(!view().includes('lab-xe-side'));
});

test('the editor can be folded away', () => {
  assert.match(view({ onHide: () => {} }), /<button[^>]*aria-expanded="true"[^>]*>Hide<\/button>/);
});

test('when the person may not run queries the editor is not shown, only the queries in words', () => {
  const out = editor({ query: Object.assign({}, QRY, { can_run: false }) });
  assert.ok(!out.includes('aria-label="What"') && !out.includes('Run with my changes') && !out.includes('Add a condition') && !out.includes('role="tablist"'));
  assert.ok(out.includes('Hoistra looked for a list of work orders that are open or overdue') && out.includes('Hoistra’s query'));
  assert.ok(out.includes('Proposed for this question - check it before accepting'));
  assert.ok(out.includes('Only curators and the platform team can change and run a query.'));
});

test('a turn with no query says why in plain words, and nothing else', () => {
  const none = (reason, more) => editor({ query: Object.assign({ ok: true, ready: true, can_run: true, queries: [], none: reason, map: MAPE }, more || {}) });
  assert.ok(none('not_about_records').includes('This question is not about records, so there is no query to check.'));
  assert.ok(none('records_plan_unreadable').includes('Hoistra read the records, but its query could not be read back from the trace.'));
  assert.ok(none('planner_unavailable').includes('No query could be proposed right now; it is tried again later.'));
  assert.ok(none('no_map').includes('Hoistra&#x27;s map of the records is not ready yet; it is tried again later.'));
  assert.ok(none('read_failed').includes('The records could not be read; it is tried again later.'));
  assert.ok(none('plan_does_not_fit').includes('The query does not fit the records any more.'));
  assert.ok(none('no_trace').includes('The conversation is not filed yet; it is tried again later.'));
  assert.ok(none('asker_has_no_buildings').includes('The person who asked has no buildings, so nothing can be read for them.'));
  assert.ok(none('no_company').includes('This question has no company, so nothing can be read.'));
  assert.ok(none('no_question').includes('There is no question text to plan from.'));
  assert.ok(none('something_else').includes('No query to check.') && none(null).includes('No query to check.'));
  const out = none('not_about_records');
  assert.ok(!out.includes('Run with my changes') && !out.includes('aria-label="What"'));
  assert.ok(none('x', { ready: false }).includes('Hoistra&#x27;s map of the records is not ready yet; it is tried again later.'), 'a map that is not ready says so, whatever else came back');
});

test('while the query is read the editor says so, and a failed read says what failed', () => {
  assert.ok(editor({ query: null }).includes('Reading the query…'));
  const out = editor({ query: null, error: 'Could not read the query: the service did not answer.' });
  assert.match(out, /role="alert"[^>]*>Could not read the query: the service did not answer\./);
  assert.ok(!out.includes('Reading the query…'));
});

test('the editor starts on the first query, with the fields and the words ready to change', () => {
  const out = editor();
  assert.ok(out.includes('role="tablist"') && out.includes('aria-label="Kind of answer"') && out.includes('Run with my changes'));
  assert.ok(out.includes('Hoistra looked for a list of work orders'));
  assert.match(editor({ onHide: () => {} }), />Hide</);
});

test('running a query sends its number and the plan, and keeps the plan that was run beside its result', async () => {
  const sent = [];
  const ok = await QF.tryRun(async (body) => { sent.push(body); return { before: SIDE_B, after: SIDE_A, changes: ['x'] }; }, 1, QF.formOf(PLAN_B));
  assert.deepEqual(sent, [{ index: 1, plan: PLAN_B }]);
  assert.equal(ok.ok, true);
  assert.deepEqual(ok.result.plan, PLAN_B, 'what Use will save is what was run');
  assert.deepEqual(ok.result.after, SIDE_A);
  const bad = await QF.tryRun(async () => { const e = new Error('Floor has no state open.'); e.reason = 'bad_query'; e.status = 422; e.body = { detail: { reason: 'bad_query', error: 'Floor has no state open.' } }; throw e; }, 0, QF.formOf(PLAN_B));
  assert.deepEqual(bad, { ok: false, reason: 'bad_query', words: ['Floor has no state open.'] });
  const several = await QF.tryRun(async () => { const e = new Error('x'); e.reason = 'bad_query'; e.body = { detail: { reason: 'bad_query', error: 'x', problems: ['One.', 'Two.'] } }; throw e; }, 0, QF.formOf(PLAN_B));
  assert.deepEqual(several.words, ['One.', 'Two.']);
  const none = await QF.tryRun(async () => { throw new Error(''); }, 0, QF.formOf(PLAN_B));
  assert.deepEqual(none.words, ['Could not run it: The service did not answer. Try again.'], 'a refusal that is not the map’s own is said as a failure to run');
  assert.deepEqual(none.ok, false);
});

test('Use is allowed for a result that found its records and is still the query on the page', () => {
  assert.equal(QF.canUse(RAN, QF.formOf(PLAN_A)), true);
  const edited = QF.formOf(PLAN_A);
  edited.focus.match = 'x';
  assert.equal(QF.canUse(RAN, edited), false);
  assert.equal(QF.canUse(Object.assign({}, RAN, { after: Object.assign({}, SIDE_A, { found: false }) }), QF.formOf(PLAN_A)), false);
  assert.equal(QF.canUse(null, QF.formOf(PLAN_A)), false);
  assert.equal(QF.canUse({ before: SIDE_B, after: null, plan: PLAN_A }, QF.formOf(PLAN_A)), false);
});

// ── The session page around the editor ──────────────────────────────────────────────────────────────────────────────
test('a draft from SPEC.md that has no values says why in plain words, and an accepted one does not', () => {
  const draft = (reason) => Object.assign({}, DRAFT, { expected: Object.assign({}, DRAFT.expected, { evidence: { drafted_from: 'SPEC.md', no_query: reason } }) });
  assert.ok(html({ data: draft('not_about_records') }).includes('This question is not about records, so there is no query to check.'));
  assert.ok(html({ data: draft('no_company') }).includes('This question has no company, so nothing can be read.'));
  assert.ok(html({ data: draft('brand_new_reason') }).includes('No query to check.'));
  assert.ok(!html({ data: DRAFT }).includes('no query to check') && !html({ data: DRAFT }).includes('No query to check'));
  assert.ok(!html({ data: Object.assign({}, draft('no_company'), { draft: false, from_draft: true }) }).includes('nothing can be read'));
  assert.ok(!html({ data: RECORDS }).includes('No query to check'));
});

test('an answer that uses the person’s own version of a query says so', () => {
  const mine = JSON.parse(JSON.stringify(RECORDS));
  Object.assign(mine, { draft: false, from_draft: false });
  Object.assign(mine.expected, { source: 'manual', author_email: 'me@x.test' });
  Object.assign(mine.expected.evidence.queries[0], { source: 'person', edited_from: 'hoistra' });
  const out = html({ data: mine });
  assert.ok(out.includes('Changed query used as the expected answer · me@x.test'));
  assert.ok(out.includes('Your version of the query') && out.includes('lab-xp-source'));
  assert.ok(!out.includes('lab-xp-proposed">Your version'));
});

test('the page offers Check and change the query, which opens the editor', () => {
  const page = src('TurnExpected.jsx');
  assert.ok(page.includes("import QueryEditor from './QueryEditor.jsx'") && page.includes('<QueryEditor ') && page.includes('Check and change the query'));
  assert.ok(page.includes('labApi.turnExpectedQuery(turnId)') && page.includes('labApi.turnExpectedTry(turnId, body)'));
  assert.ok(page.includes('usedBody(') && page.includes('save(usedBody('), 'Use as the expected answer saves the person’s version through the same save as Accept');
  for (const gone of ['QueryFix', 'editOf', 'bodyOf', 'planIndex', 'correctedBody', 'dateName', 'stateOrder']) assert.ok(!page.includes(gone), gone + ' is replaced by the editor');
});

test('the editor’s requests: the turn’s queries with the map, and one query run with a plan', async () => {
  const { labApi } = await import('../labApi.js');
  const calls = [];
  const real = globalThis.fetch;
  globalThis.fetch = async (url, opts) => {
    calls.push({ url: new URL(String(url)), method: (opts && opts.method) || 'GET', body: opts && opts.body ? JSON.parse(opts.body) : null });
    return { ok: true, status: 200, statusText: 'OK', text: async () => '{"ok":true}', headers: { get: () => null } };
  };
  try {
    await labApi.turnExpectedQuery('t1');
    await labApi.turnExpectedTry('t1', { index: 1, plan: PLAN_B });
    await labApi.turnExpectedTry('t1', { plan: PLAN_B });
  } finally {
    globalThis.fetch = real;
  }
  assert.equal(calls.length, 3);
  for (const c of calls) assert.equal(c.url.pathname, '/backend/trace-lab/api/trace-lab/lab/turns/t1/expected/query');
  assert.equal(calls[0].method, 'GET');
  assert.equal(calls[1].method, 'POST');
  assert.deepEqual(calls[1].body, { index: 1, plan: PLAN_B }, 'the number of the query and the whole plan, nothing else');
  assert.deepEqual(calls[2].body, { index: 0, plan: PLAN_B }, 'the first query is named too');
});

// ── long lists fold (19 Nov 2026, Hussain: "make this collapsable the 14 cert or anything like this") ──────────────────
const LONG = 'Compliance certificate records found: 14. Compliance certificates: '
  + Array.from({ length: 14 }, (_, i) => 'SYN-PL-' + (100000 + i)).join(', ') + '.';
const CERT_FACTS = [{ id: 'f1', kind: 'count', label: 'Compliance certificate records found', value: 14, receipt: 'q1' }]
  .concat(Array.from({ length: 14 }, (_, i) => ({ id: 'f' + (i + 2), kind: 'record', label: '', value: 'SYN-PL-' + (100000 + i), receipt: 'q2' })));
const CERTS_DATA = (() => {
  const d = JSON.parse(JSON.stringify(RECORDS));
  const q = d.expected.evidence.queries[0];
  Object.assign(q, { sentence: LONG, facts: CERT_FACTS });
  d.expected.evidence.queries = [q];
  d.expected.expected = LONG;
  return d;
})();

test('a long list of records folds behind one line that says how many, and the count stays in view', () => {
  const out = html({ data: CERTS_DATA });
  assert.match(out, /<details class="lab-xp-fold"><summary[^>]*>Show the 14 records<\/summary>/);
  const fold = out.slice(out.indexOf('<details class="lab-xp-fold">'));
  assert.equal((fold.match(/class="lab-xp-chip[ "]/g) || []).length, 14, 'every record is still a button, inside the fold');
  assert.ok(out.indexOf('Compliance certificate records found') < out.indexOf('<details class="lab-xp-fold">'), 'the count is outside it');
});

test('a long sentence shows its start, with Show all for the whole of it', () => {
  const out = html({ data: CERTS_DATA });
  assert.match(out, /<details class="lab-xp-more">/);
  assert.ok(out.includes(R2.foldSentence(LONG)) && out.includes('Show all') && out.includes(LONG));
  assert.equal((out.match(new RegExp(LONG.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'), 'g')) || []).length, 1, 'the whole sentence once');
});

test('the query editor folds a long list and a long sentence on each side too', () => {
  const side = { found: true, facts: CERT_FACTS, receipts: [RQ1, RQ2], read_at: '2026-11-19T09:30:01+00:00', unresolved: [], notes: [], sentence: LONG, plan: PLAN_A };
  const out = view({ results: { 0: { before: side, after: side } } });
  assert.equal((out.match(/<details class="lab-xp-fold">/g) || []).length, 2);
  assert.equal((out.match(/<details class="lab-xp-more">/g) || []).length, 2);
});
