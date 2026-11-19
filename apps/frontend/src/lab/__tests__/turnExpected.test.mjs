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
  const same = html({ data: KEYED, openFact: 'f1', rerun: { facts: [{ id: 'f1', value: 5 }], read_at: '2026-11-16T10:00:00Z' } });
  assert.ok(same.includes('Now: 5') && same.includes('the same as when the question was asked'));
  const moved = html({ data: KEYED, openFact: 'f1', rerun: { facts: [{ id: 'f1', value: 6 }], read_at: '2026-11-16T10:00:00Z' } });
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

// ── Correcting Hoistra's query (16 Nov 2026, Hussain): "the user must be able to change the date in the query if the
// query by the orchestrator is wrong and see the correct answer" - only the expected answer is corrected. ─────────
const FIELDS = { concept: 'ComplianceCertificate', what: 'compliance certificates', states: ['expiring_soon'], building: 'Bishopsgate',
  state_choices: [{ id: 'lapsed', description: 'lapsed / expired' }, { id: 'expiring_soon', description: 'status = Expiring soon' }],
  dates: [{ attribute: 'expiry_date', op: 'within', value: 'next_30_days' }], date_attributes: ['expiry_date', 'issue_date'] };

test('Hoistra’s query is shown as plain fields anyone can change', () => {
  const out = renderToStaticMarkup(React.createElement(TE.QueryFix, { query: { ready: true, plans: [{ index: 0, fields: FIELDS }] },
    edit: TE.editOf(FIELDS) }));
  assert.ok(out.includes('Hoistra looked for') && out.includes('compliance certificates'));
  assert.match(out, /<input type="checkbox"[^>]*checked=""[^>]*aria-label="expiring soon"|aria-label="expiring soon"[^>]*checked=""/);
  assert.ok(out.includes('value="Bishopsgate"') && out.includes('every building'));
  assert.ok(out.includes('expiry date') && out.includes('within the next') && out.includes('value="30"'));
  assert.match(out, />Run with my changes</);
  assert.match(out, />Add a date</);
});

test('the changes become the request, and next or last N days a period', () => {
  const edit = TE.editOf(FIELDS);
  edit.dates[0] = Object.assign({}, edit.dates[0], { n: '60' });
  assert.deepEqual(TE.bodyOf(0, edit), { index: 0, states: ['expiring_soon'], building: 'Bishopsgate',
    dates: [{ attribute: 'expiry_date', op: 'within', value: 'next_60_days' }] });
  const range = TE.editOf(Object.assign({}, FIELDS, { dates: [{ attribute: 'expiry_date', op: 'between', value: ['2026-11-16', '2027-01-15'] }] }));
  assert.deepEqual(TE.bodyOf(0, range).dates, [{ attribute: 'expiry_date', op: 'between', value: ['2026-11-16', '2027-01-15'] }]);
  const month = TE.editOf(Object.assign({}, FIELDS, { dates: [{ attribute: 'issue_date', op: 'within', value: 'this_month' }] }));
  assert.deepEqual(TE.bodyOf(0, month).dates, [{ attribute: 'issue_date', op: 'within', value: 'this_month' }]);
});

test('the corrected answer shows what changed and can become the expected answer', () => {
  const result = { found: true, sentence: '3 compliance certificates: A, B, C.', changes: ['expiry_date: within next_30_days → within next_60_days'],
    facts: [{ id: 'f1', label: 'Compliance certificates found', value: 3, receipt: 'q1' }],
    receipts: [{ id: 'q1', label: 'count', sql: 'SELECT count(*) ...', params: {}, row_count: 1, rows: [{ n: 3 }] }], read_at: '2026-11-16T10:00:00Z' };
  const out = renderToStaticMarkup(React.createElement(TE.QueryFix, { query: { ready: true, plans: [{ index: 0, fields: FIELDS }] },
    edit: TE.editOf(FIELDS), result }));
  assert.ok(out.includes('Your version finds') && out.includes('3 compliance certificates: A, B, C.'));
  assert.ok(out.includes('expiry_date: within next_30_days → within next_60_days'));
  assert.match(out, />Use as the expected answer</);
  const body = TE.correctedBody(result);
  assert.equal(body.expected, '3 compliance certificates: A, B, C.');
  assert.equal(body.evidence.drafted_from, 'answer_key');
  assert.deepEqual(body.evidence.changes, result.changes);
  assert.equal(body.evidence.receipts.length, 1);
});

test('when Hoistra did not read records for this question, it says so plainly', () => {
  const none = renderToStaticMarkup(React.createElement(TE.QueryFix, { query: { ready: true, plans: [] } }));
  assert.ok(none.includes('Hoistra did not read the records with a query for this question, so there is nothing to correct here.'));
  const wait = renderToStaticMarkup(React.createElement(TE.QueryFix, { query: { ready: false, plans: [] } }));
  assert.ok(wait.includes('not ready yet'));
});

// ── Very user friendly (16 Nov 2026, Hussain) ───────────────────────────────────────────────────────────────────────
const WO = { concept: 'WorkOrder', what: 'work orders', states: ['open', 'overdue'], building: null,
  state_choices: [{ id: 'open', description: 'not finished (status not in Completed)' }, { id: 'closed', description: 'finished (status in Completed)' },
    { id: 'scheduled', description: 'status = Scheduled (276 rows)' }, { id: 'overdue', description: 'sla_due_at passed and completed_at empty' }],
  dates: [], date_attributes: ['sla_due_at', 'completed_at', 'raised_at'] };

test('the query reads as one plain sentence, before and after the changes', () => {
  assert.equal(TE.describe(WO, TE.editOf(WO)), 'work orders that are open or overdue, in every building, with no date limit');
  const e = TE.editOf(WO);
  e.states = ['overdue']; e.building = 'Bishopsgate Tower';
  e.dates = [{ attribute: 'sla_due_at', how: 'next', n: '60' }];
  assert.equal(TE.describe(WO, e), 'work orders that are overdue, in Bishopsgate Tower, where the SLA due date is within the next 60 days');
  e.states = []; e.dates = [{ attribute: 'raised_at', how: 'between', from: '2026-11-01', to: '2026-11-30' }];
  assert.equal(TE.describe(WO, e), 'work orders in any state, in Bishopsgate Tower, where the raised date is between 2026-11-01 and 2026-11-30');
});

test('statuses and dates have plain names, and the ones in use come first', () => {
  assert.equal(TE.dateName('sla_due_at'), 'SLA due date');
  assert.equal(TE.dateName('expiry_date'), 'expiry date');
  assert.equal(TE.dateName('completed_at'), 'completed date');
  assert.equal(TE.stateHelp({ id: 'scheduled', description: 'status = Scheduled (276 rows)' }), 'status is Scheduled');
  const order = TE.stateOrder(WO, TE.editOf(WO)).map((c) => c.id);
  assert.deepEqual(order.slice(0, 2), ['open', 'overdue']);
  const out = renderToStaticMarkup(React.createElement(TE.QueryFix, { query: { ready: true, plans: [{ index: 0, fields: WO }], buildings: ['Bishopsgate Tower', 'Harbour Point'] },
    edit: TE.editOf(WO) }));
  assert.ok(out.includes('Hoistra looked for') && out.includes('work orders that are open or overdue, in every building, with no date limit'));
  for (const step of ['1. Check what Hoistra looked for', '2. Change what is wrong', '3. Run it', '4. Use it']) assert.ok(out.includes(step), step);
  assert.ok(out.includes('not finished'), 'what a status means is shown');
  assert.ok(out.includes('<option value="Bishopsgate Tower">') && out.includes('list="'), 'building names to pick from');
  assert.match(out, />Start again</);
});

test('the result says what Hoistra’s query found and what yours finds', () => {
  const result = { found: true, sentence: 'Counts: Bishopsgate Tower 9.', changes: ['States: open, overdue → overdue'],
    facts: [{ id: 'f1', label: 'Bishopsgate Tower', value: 9, receipt: 'q1' }], receipts: [{ id: 'q1', label: 'aggregate', sql: 'SELECT ...', row_count: 1, rows: [{ count: 9 }] }],
    before: { found: true, sentence: 'Counts: Bishopsgate Tower 13.', facts: [{ id: 'f1', label: 'Bishopsgate Tower', value: 13 }] } };
  const out = renderToStaticMarkup(React.createElement(TE.QueryFix, { query: { ready: true, plans: [{ index: 0, fields: WO }] }, edit: TE.editOf(WO), result }));
  assert.ok(out.includes('Hoistra’s query found') && out.includes('Counts: Bishopsgate Tower 13.'));
  assert.ok(out.includes('Your version finds') && out.includes('Counts: Bishopsgate Tower 9.'));
});

test('when Hoistra used another tool the first result is the question as asked, not Hoistra’s query', () => {
  const result = { found: true, sentence: '14 compliance certificates.', changes: [], facts: [], receipts: [],
    before: { found: true, sentence: '7 compliance certificates.', facts: [] } };
  const out = renderToStaticMarkup(React.createElement(TE.QueryFix, { query: { ready: true, source: 'answer_key', plans: [{ index: 0, fields: WO }] }, edit: TE.editOf(WO), result }));
  assert.ok(!out.includes('Hoistra’s query found'));
  assert.ok(out.includes('The question as asked finds') && out.includes('7 compliance certificates.'));
});

test('a status is explained in plain words, without the notes written for the model', () => {
  const lapsed = { id: 'lapsed', description: 'lapsed / expired - status in Expired, Lapsed, Overdue, or stored as in date with expiry_date passed (the expiry date decides). USE THIS for lapsed, expired or overdue certificates' };
  assert.equal(TE.stateHelp(lapsed), 'status in Expired, Lapsed, Overdue, or stored as in date with expiry date passed');
  assert.equal(TE.stateHelp({ id: 'valid', description: 'status = Valid' }), 'status is Valid');
  assert.equal(TE.stateHelp({ id: 'blocked', description: 'blocked / suspended / failed (outcome in Pass — 41 items failed and removed)' }), 'blocked, suspended or failed');
  assert.equal(TE.stateHelp({ id: 'blocking', description: 'lapsed, expired or blocked - dependent work cannot proceed (outcome in Pass — 41 items failed and remo' }), 'dependent work cannot proceed');
  const long = TE.stateHelp({ id: 'x', description: 'a '.repeat(80) });
  assert.ok(long.length <= 91 && long.endsWith('…'));
});

test('a query in any state has every state ticked, and unticking one narrows it', () => {
  const ANY = Object.assign({}, WO, { states: [] });
  const e = TE.editOf(ANY);
  assert.deepEqual(e.states, ['open', 'closed', 'scheduled', 'overdue'], 'every state the query includes is ticked');
  assert.equal(TE.describe(ANY, e), 'work orders in any state, in every building, with no date limit');
  assert.deepEqual(TE.bodyOf(0, e, ANY).states, [], 'all ticked is sent as any state');
  const most = Object.assign({}, e, { states: ['open', 'scheduled', 'overdue'] });
  assert.equal(TE.describe(ANY, most), 'work orders in any state except closed, in every building, with no date limit');
  const fewer = Object.assign({}, e, { states: ['open', 'scheduled'] });
  assert.equal(TE.describe(ANY, fewer), 'work orders that are open or scheduled, in every building, with no date limit');
  assert.deepEqual(TE.bodyOf(0, fewer, ANY).states, ['open', 'scheduled']);
  const out = renderToStaticMarkup(React.createElement(TE.QueryFix, { query: { ready: true, plans: [{ index: 0, fields: ANY }] }, edit: e }));
  assert.equal((out.match(/type="checkbox"[^>]*checked=""/g) || []).length, 4, 'the boxes show what the query uses');
});

test('a box stays where it is when it is ticked or unticked', () => {
  const order = (edit) => [...renderToStaticMarkup(React.createElement(TE.QueryFix, { query: { ready: true, plans: [{ index: 0, fields: WO }] }, edit }))
    .matchAll(/aria-label="([^"]+)" type="checkbox"|type="checkbox" aria-label="([^"]+)"/g)].map((m) => m[1] || m[2]);
  const start = order(TE.editOf(WO));
  assert.deepEqual(order(Object.assign({}, TE.editOf(WO), { states: ['overdue', 'closed'] })), start);
});

test('the query check can be folded away', () => {
  const out = renderToStaticMarkup(React.createElement(TE.QueryFix, { query: { ready: true, plans: [{ index: 0, fields: WO }] }, edit: TE.editOf(WO), onHide: () => {} }));
  assert.match(out, /<button[^>]*aria-expanded="true"[^>]*>Hide<\/button>/);
});

test('with no state ticked there is nothing to run, and the page says so', () => {
  const none = Object.assign({}, TE.editOf(WO), { states: [] });
  const out = renderToStaticMarkup(React.createElement(TE.QueryFix, { query: { ready: true, plans: [{ index: 0, fields: WO }] }, edit: none }));
  assert.ok(out.includes('Tick at least one'));
  assert.match(out, /<button[^>]*disabled=""[^>]*>Run with my changes</);
});

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

test('when Hoistra used another tool, the same question is offered as a records query to check', () => {
  const out = renderToStaticMarkup(React.createElement(TE.QueryFix, { query: { ready: true, source: 'answer_key', plans: [{ index: 0, fields: FIELDS }] },
    edit: TE.editOf(FIELDS) }));
  assert.ok(out.includes('Hoistra answered this with another tool, so here is the same question as a records query.'));
  assert.ok(out.includes('The question asks for') && !out.includes('Hoistra looked for'));
});

// ── Review fixes before commit (16 Nov 2026) ──
test('the query that is run is the one shown, by its own number', () => {
  assert.equal(TE.planIndex({ plans: [{ index: 2, fields: WO }] }), 2);
  assert.equal(TE.planIndex({ plans: [] }), 0);
  assert.equal(TE.planIndex(null), 0);
});

test('Run again now shows only where there is a key to run again', () => {
  const corrected = Object.assign({}, KEYED, { expected: Object.assign({}, KEYED.expected, {
    evidence: Object.assign({}, KEYED.expected.evidence, { key: undefined, source: 'corrected' }) }) });
  assert.ok(!/>Run again now</.test(html({ data: corrected, openFact: 'f1', onRerun: () => {} })));
  assert.match(html({ data: KEYED, openFact: 'f1', onRerun: () => {} }), />Run again now</);
});

test('while Hoistra’s query is read the check says so, not that there is nothing to correct', () => {
  const out = renderToStaticMarkup(React.createElement(TE.QueryFix, { query: null }));
  assert.ok(out.includes('Reading Hoistra’s query…') && !out.includes('nothing to correct'));
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
  const moved = html({ data: RECORDS, openFact: '0:f1', rerun: { index: 0, facts: [{ id: 'f1', value: 3 }], read_at: '2026-11-19T10:00:00Z' } });
  assert.ok(moved.includes('Now: 3') && moved.includes('was 2 when the question was asked: the records have changed since'));
  const other = html({ data: RECORDS, openFact: '1:f1', rerun: { index: 0, facts: [{ id: 'f1', value: 3 }] } });
  assert.ok(!other.includes('Now: 3'), 'a re-run of another query says nothing here');
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
