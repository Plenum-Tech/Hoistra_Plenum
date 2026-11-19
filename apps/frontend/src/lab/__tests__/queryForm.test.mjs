// queryForm — the records query as fields a person can change (19 Nov 2026, Hussain and Bala: "every part of the query,
// not only the states, the building and the dates"). The plan is Hoistra's own (ontology_qa PLAN_SCHEMA); the form holds
// it as fields and gives the same plan back, so nothing the person did not touch can change. Nothing here reads the
// service.
import { test } from 'node:test';
import assert from 'node:assert/strict';

const Q = await import('../logic/queryForm.js');

// "How many floors are there in Building ABC?" (R62): the building states 25 floors, and no floor record exists.
const R62 = { question_type: 'count', focus: { concept: 'Floor', match: null, states: [], filters: [], via: [] },
  constraints: [{ concept: 'Building', match: 'ABC', via: [], states: [], filters: [] }], include: [], aggregate: null };
// "How many open work orders does each building have?"
const AGG = { question_type: 'count',
  focus: { concept: 'WorkOrder', match: null, states: ['open'], filters: [{ attribute: 'raised_at', op: 'within', value: 'last_month' }], via: [] },
  constraints: [{ concept: 'Building', match: null, via: [], states: [], filters: [] }], include: [],
  aggregate: { count_by: [{ concept: 'Building', attribute: 'name' }] } };
// Blocked certificates in a building, with the work orders they block (newest first), and the vendor.
const INC = { question_type: 'list',
  focus: { concept: 'ComplianceCertificate', match: null, states: ['blocked', 'lapsed'], via: [], filters: [
    { attribute: 'expiry_date', op: 'between', value: ['today', 'today+60d'] },
    { attribute: 'cert_type', op: 'in', value: ['Gas', 'Electrical'] },
    { attribute: 'notes', op: 'is_null', value: true },
    { attribute: 'issuer', op: 'contains', value: 'Reg' },
    { attribute: 'renewal_cost', op: 'gte', value: 250 }] },
  constraints: [{ concept: 'Building', match: 'Bishopsgate Tower', via: [], states: [], filters: [{ attribute: 'city', op: 'eq', value: 'London' }] },
    { concept: 'Vendor', match: null, via: ['SERVICES'], states: ['live'], filters: [] }],
  include: [{ name: 'affected_work_orders', concept: 'WorkOrder', via: ['BLOCKS'], states: ['open'], filters: [], sort: { attribute: 'raised_at', dir: 'desc' }, limit: 5 },
    { name: 'vendor', concept: 'Vendor', via: [], states: [], filters: [], sort: null, limit: 50 }],
  aggregate: null };

test('every key of the plan comes back the way it went in', () => {
  for (const p of [R62, AGG, INC]) assert.deepEqual(Q.planOf(Q.formOf(p)), p);
});

test('a plan that lacks keys comes back without them, and a key Trace Lab does not know is kept', () => {
  const small = { question_type: 'single', focus: { concept: 'Vendor', match: 'Apex', states: [], filters: [] }, constraints: [], include: [] };
  assert.deepEqual(Q.planOf(Q.formOf(small)), small, 'no via on the focus, no aggregate');
  const extra = { question_type: 'list', note: 'kept', focus: { concept: 'Vendor', match: null, states: [], via: [], note: 'also kept',
    filters: [{ attribute: 'vendor_name', op: 'contains', value: 'Apex', pinned: true }] }, constraints: [], include: [], aggregate: null };
  assert.deepEqual(Q.planOf(Q.formOf(extra)), extra);
  assert.deepEqual(Q.planOf(Q.formOf({ focus: { concept: 'Vendor' } })).focus.concept, 'Vendor');
});

test('the form does not share anything with the plan it was made from', () => {
  const p = JSON.parse(JSON.stringify(INC));
  const form = Q.formOf(p);
  form.focus.states.push('valid');
  form.focus.conditions[0].value[0] = 'tomorrow';
  form.limits[0].match = 'Other';
  assert.deepEqual(p, INC, 'the plan is untouched');
  assert.deepEqual(Q.planOf(Q.formOf(p)), INC);
});

test('the form holds each part of the query as a field', () => {
  const f = Q.formOf(INC);
  assert.equal(f.kind, 'list');
  assert.equal(f.focus.concept, 'ComplianceCertificate');
  assert.equal(f.focus.match, '');
  assert.deepEqual(f.focus.states, ['blocked', 'lapsed']);
  assert.equal(f.focus.conditions.length, 5);
  assert.deepEqual(f.focus.conditions[1], { attribute: 'cert_type', op: 'in', value: ['Gas', 'Electrical'] });
  assert.deepEqual(f.limits.map((l) => [l.concept, l.match, l.states.length, l.conditions.length]), [['Building', 'Bishopsgate Tower', 0, 1], ['Vendor', '', 1, 0]]);
  assert.deepEqual(f.alsoShow.map((i) => [i.concept, i.limit, i.sort]), [['WorkOrder', 5, { attribute: 'raised_at', dir: 'desc' }], ['Vendor', 50, null]]);
  assert.equal(f.groupBy, null);
  assert.deepEqual(Q.formOf(AGG).groupBy, [{ concept: 'Building', attribute: 'name' }]);
  assert.equal(Q.formOf(R62).kind, 'count');
});

test('a change made in the form becomes a change in the plan, and only that', () => {
  const f = Q.formOf(R62);
  f.focus.concept = 'Building';
  f.focus.match = ' ABC ';
  f.focus.states = ['valid'];
  f.limits = [];
  f.kind = 'list';
  const p = Q.planOf(f);
  assert.equal(p.question_type, 'list');
  assert.deepEqual(p.focus, { concept: 'Building', match: 'ABC', states: ['valid'], filters: [], via: [] });
  assert.deepEqual(p.constraints, []);
  assert.equal(p.aggregate, null);
  const g = Q.formOf(AGG);
  g.groupBy = null;
  assert.equal(Q.planOf(g).aggregate, null);
  g.groupBy = [{ concept: 'WorkOrder', attribute: 'priority' }];
  assert.deepEqual(Q.planOf(g).aggregate, { count_by: [{ concept: 'WorkOrder', attribute: 'priority' }] });
  const h = Q.formOf(R62);
  h.focus.match = '';
  assert.equal(Q.planOf(h).focus.match, null, 'no name or code is null, as Hoistra writes it');
});

test('a list typed in a box is read as a list when the plan is made', () => {
  const f = Q.formOf(INC);
  f.focus.conditions[1].value = 'Gas, Electrical, ,Fire ';
  assert.deepEqual(Q.planOf(f).focus.filters[1].value, ['Gas', 'Electrical', 'Fire']);
  f.focus.conditions[3].value = '  Reg  ';
  assert.equal(Q.planOf(f).focus.filters[3].value, 'Reg');
});

test('what the editor sends to run is the query number and the plan', () => {
  assert.deepEqual(Q.runBody(2, Q.formOf(R62)), { index: 2, plan: R62 });
  assert.deepEqual(Object.keys(Q.runBody(0, Q.formOf(R62))).sort(), ['index', 'plan']);
});

// ── The map: the words and the choices every field offers ──────────────────────────────────────────────────────────
const MAP = {
  concepts: [
    { name: 'Floor', words: 'floors', attributes: [{ name: 'floor_number', kind: 'number' }, { name: 'name', kind: 'text' }], states: [], links: ['Building'] },
    { name: 'Building', words: 'buildings', attributes: [{ name: 'name', kind: 'text' }, { name: 'floors', kind: 'number' }, { name: 'city', kind: 'text' }],
      states: [], links: ['Floor', 'WorkOrder', 'ComplianceCertificate'] },
    { name: 'WorkOrder', words: 'work orders', attributes: [{ name: 'raised_at', kind: 'date' }, { name: 'sla_due_at', kind: 'date' }, { name: 'status', kind: 'text' },
      { name: 'cost', kind: 'number' }],
    states: [{ id: 'open', description: 'not finished (status not in Completed)' }, { id: 'overdue', description: 'sla_due_at passed and completed_at empty' },
      { id: 'closed', description: 'finished (status in Completed)' }], links: ['Building', 'Vendor'] },
    { name: 'ComplianceCertificate', words: 'compliance certificates', attributes: [{ name: 'expiry_date', kind: 'date' }, { name: 'cert_type', kind: 'text' }],
      states: [{ id: 'blocked', description: 'blocked / suspended / failed' }, { id: 'lapsed', description: 'lapsed / expired' }], links: ['Building', 'WorkOrder'] },
    { name: 'Vendor', words: 'vendors', attributes: [{ name: 'vendor_name', kind: 'text' }], states: [{ id: 'live', description: 'status = Live' }], links: ['WorkOrder'] }],
  ops: [{ id: 'eq', words: 'is' }, { id: 'ne', words: 'is not' }, { id: 'in', words: 'is one of' }, { id: 'not_in', words: 'is none of' },
    { id: 'lt', words: 'is before' }, { id: 'lte', words: 'is on or before' }, { id: 'gt', words: 'is after' }, { id: 'gte', words: 'is on or after' },
    { id: 'between', words: 'is between' }, { id: 'within', words: 'is within' }, { id: 'contains', words: 'contains' }, { id: 'is_null', words: 'is empty' }],
  periods: ['today', 'this_week', 'this_month', 'last_month', 'next_month', 'this_year', 'next_30_days', 'last_7_days']
};

test('every condition word the records engine knows has plain words', () => {
  assert.deepEqual(Object.keys(Q.OP_WORDS).sort(), ['between', 'contains', 'eq', 'gt', 'gte', 'in', 'is_null', 'lt', 'lte', 'ne', 'not_in', 'within']);
  assert.equal(Q.OP_WORDS.eq, 'is');
  assert.equal(Q.OP_WORDS.ne, 'is not');
  assert.equal(Q.OP_WORDS.in, 'is one of');
  assert.equal(Q.OP_WORDS.lt, 'is before');
  assert.equal(Q.OP_WORDS.gt, 'is after');
  assert.equal(Q.OP_WORDS.between, 'is between');
  assert.equal(Q.OP_WORDS.within, 'is within');
  assert.equal(Q.OP_WORDS.contains, 'contains');
  assert.equal(Q.OP_WORDS.is_null, 'is empty');
});

test('the map is read for concepts, columns with their kind, states and links', () => {
  assert.equal(Q.conceptWords(MAP, 'WorkOrder'), 'work orders');
  assert.equal(Q.conceptWords(null, 'WorkOrder'), 'work orders', 'without a map the words come from the name');
  assert.equal(Q.conceptWords(null, 'ComplianceCertificate'), 'compliance certificates');
  assert.equal(Q.conceptWords(null, 'Facility'), 'facilities');
  assert.deepEqual(Q.attributesOf(MAP, 'Floor').map((a) => a.name), ['floor_number', 'name']);
  assert.equal(Q.kindOf(MAP, 'WorkOrder', 'raised_at'), 'date');
  assert.equal(Q.kindOf(MAP, 'WorkOrder', 'cost'), 'number');
  assert.equal(Q.kindOf(MAP, 'WorkOrder', 'nothing_like_it'), 'text');
  assert.deepEqual(Q.statesOf(MAP, 'WorkOrder').map((s) => s.id), ['open', 'overdue', 'closed']);
  assert.deepEqual(Q.linksOf(MAP, 'WorkOrder'), ['Building', 'Vendor']);
  assert.deepEqual(Q.statesOf(null, 'WorkOrder'), []);
});

test('each kind of column offers the conditions that make sense for it, in the engine’s words', () => {
  const ids = (kind) => Q.opsFor(MAP, kind).map((o) => o.id);
  assert.deepEqual(ids('date'), ['eq', 'ne', 'lt', 'lte', 'gt', 'gte', 'between', 'within', 'is_null', 'not_null']);
  assert.ok(ids('text').includes('in') && ids('text').includes('contains') && !ids('text').includes('within'));
  assert.ok(!ids('number').includes('contains') && ids('number').includes('between'));
  assert.equal(Q.opsFor(MAP, 'text').find((o) => o.id === 'not_in').words, 'is none of', 'the engine’s own words win');
  assert.equal(Q.opsFor(null, 'text').find((o) => o.id === 'not_in').words, 'is not one of', 'without a map, ours');
  assert.equal(Q.opsFor(MAP, 'number').find((o) => o.id === 'lt').words, 'is less than');
  assert.equal(Q.opsFor(MAP, 'date').find((o) => o.id === 'lt').words, 'is before');
  assert.equal(Q.opsFor(MAP, 'date').find((o) => o.id === 'not_null').words, 'is not empty');
  assert.ok(Q.opsFor(MAP, 'text', 'within').some((o) => o.id === 'within'), 'a condition already in the query is always offered');
});

test('periods come from the map, with the rolling ones asked for as a number of days', () => {
  const p = Q.periodsOf(MAP);
  assert.deepEqual(p.map((x) => x.id), ['today', 'this_week', 'this_month', 'last_month', 'next_month', 'this_year']);
  assert.equal(p.find((x) => x.id === 'last_month').words, 'last month');
  assert.ok(Q.periodsOf(null).length >= 10, 'without a map a fixed list');
  assert.deepEqual(Q.rollingOf('next_60_days'), { way: 'next', n: '60' });
  assert.deepEqual(Q.rollingOf('last_7_days'), { way: 'last', n: '7' });
  assert.equal(Q.rollingOf('this_month'), null);
  assert.equal(Q.periodWords('next_60_days'), 'the next 60 days');
  assert.equal(Q.periodWords('last_month'), 'last month');
  assert.equal(Q.periodWords('year_to_date'), 'year to date');
});

// ── Changing a field changes what depends on it ────────────────────────────────────────────────────────────────────
test('changing what is read drops the states and conditions that no longer fit, and keeps the ones that do', () => {
  const f = Q.formOf(INC);
  const g = Q.changeConcept(f.focus, 'WorkOrder', MAP);
  assert.equal(g.concept, 'WorkOrder');
  assert.deepEqual(g.states, [], 'blocked and lapsed are not work order states');
  assert.deepEqual(g.conditions, [], 'none of these columns is a work order column');
  assert.deepEqual(g.via, []);
  const keep = Q.changeConcept(Object.assign({}, f.focus, { states: ['open'], conditions: [{ attribute: 'status', op: 'eq', value: 'Open' }] }), 'WorkOrder', MAP);
  assert.deepEqual(keep.conditions, [{ attribute: 'status', op: 'eq', value: 'Open' }]);
  const same = Q.changeConcept(f.focus, 'ComplianceCertificate', MAP);
  assert.deepEqual(same, f.focus, 'choosing what is already chosen changes nothing');
});

test('a changed include keeps its sort only when the column is on the new kind of record', () => {
  const inc = Q.formOf(INC).alsoShow[0];
  const next = Q.changeConcept(inc, 'Vendor', MAP);
  assert.equal(next.sort, null);
  assert.equal(next.limit, 5);
  assert.equal(next.name, 'vendors', 'a name made from the kind of record follows it');
  assert.equal(Q.changeConcept(inc, 'WorkOrder', MAP).sort.attribute, 'raised_at');
});

test('adding a condition starts from the first column of the record, with a condition that fits it', () => {
  assert.deepEqual(Q.blankCondition(MAP, 'WorkOrder'), { attribute: 'raised_at', op: 'within', value: 'this_month' });
  assert.deepEqual(Q.blankCondition(MAP, 'Vendor'), { attribute: 'vendor_name', op: 'eq', value: '' });
  assert.deepEqual(Q.blankCondition(MAP, 'Building'), { attribute: 'name', op: 'eq', value: '' });
  assert.equal(Q.blankCondition(null, 'WorkOrder').attribute, '');
});

test('changing a condition’s column or words keeps what still fits', () => {
  const c = { attribute: 'raised_at', op: 'within', value: 'next_60_days' };
  assert.deepEqual(Q.changeColumn(c, 'sla_due_at', MAP, 'WorkOrder'), { attribute: 'sla_due_at', op: 'within', value: 'next_60_days' });
  assert.deepEqual(Q.changeColumn(c, 'status', MAP, 'WorkOrder'), { attribute: 'status', op: 'eq', value: '' }, 'a text column cannot be within a period');
  assert.deepEqual(Q.changeOp(c, 'between'), { attribute: 'raised_at', op: 'between', value: ['', ''] });
  assert.deepEqual(Q.changeOp({ attribute: 'x', op: 'between', value: ['2026-01-01', '2026-02-01'] }, 'gt'), { attribute: 'x', op: 'gt', value: '2026-01-01' });
  assert.deepEqual(Q.changeOp({ attribute: 'x', op: 'eq', value: 'Gas' }, 'in'), { attribute: 'x', op: 'in', value: 'Gas' });
  assert.deepEqual(Q.changeOp({ attribute: 'x', op: 'eq', value: 'Gas' }, 'is_null'), { attribute: 'x', op: 'is_null', value: true });
  assert.deepEqual(Q.changeOp({ attribute: 'x', op: 'eq', value: 'Gas' }, 'not_null'), { attribute: 'x', op: 'is_null', value: false });
  assert.deepEqual(Q.changeOp({ attribute: 'x', op: 'is_null', value: false }, 'eq'), { attribute: 'x', op: 'eq', value: '' });
  assert.deepEqual(Q.changeOp({ attribute: 'x', op: 'eq', value: 'a' }, 'within'), { attribute: 'x', op: 'within', value: 'this_month' });
  assert.equal(Q.opChoice({ op: 'is_null', value: false }), 'not_null');
  assert.equal(Q.opChoice({ op: 'is_null', value: true }), 'is_null');
  assert.equal(Q.opChoice({ op: 'gt', value: 3 }), 'gt');
});

test('lists of limits, groupings and linked records grow and shrink without touching the rest', () => {
  let f = Q.formOf(R62);
  f = Q.addLimit(f, MAP);
  assert.equal(f.limits.length, 2);
  assert.equal(f.limits[1].concept, 'Building', 'the first record the focus is linked to');
  assert.deepEqual(Q.planOf(f).constraints[1], { concept: 'Building', match: null, via: [], states: [], filters: [] });
  f = Q.removeAt(f, 'limits', 0);
  assert.deepEqual(f.limits.map((l) => l.match), ['']);
  f = Q.addGroup(f, MAP);
  assert.deepEqual(f.groupBy, [{ concept: 'Floor', attribute: 'floor_number' }]);
  f = Q.removeGroup(f, 0);
  assert.equal(f.groupBy, null, 'no grouping left is no aggregate');
  f = Q.addInclude(f, MAP);
  assert.deepEqual(Q.planOf(f).include, [{ name: 'buildings', concept: 'Building', match: null, via: [], states: [], filters: [], sort: null, limit: 20 }]);
  f = Q.addInclude(f, MAP);
  assert.deepEqual(f.alsoShow.map((i) => i.name), ['buildings', 'buildings_2'], 'two of one kind do not share a name');
  f = Q.removeAt(f, 'alsoShow', 0);
  assert.equal(f.alsoShow.length, 1);
});

test('changing what is read, or a limit, keeps every grouping on a record the query still has', () => {
  const f = Q.formOf(AGG);
  const g = Q.changeFocusConcept(f, 'Building', MAP);
  assert.equal(g.focus.concept, 'Building');
  assert.deepEqual(g.groupBy, [{ concept: 'Building', attribute: 'name' }], 'it was by the building already, and the building is still there');
  const h = Q.changeFocusConcept(Q.formOf(Object.assign({}, AGG, { constraints: [] })), 'Floor', MAP);
  assert.deepEqual(h.groupBy, [{ concept: 'Floor', attribute: 'floor_number' }], 'a grouping by a record that is gone moves to what is read');
  const k = Q.changeNodeConcept(f, 'limits', 0, 'Vendor', MAP);
  assert.deepEqual(k.groupBy, [{ concept: 'WorkOrder', attribute: 'raised_at' }], 'the limit was the building: counting by it moves to what is read');
  assert.equal(k.limits[0].concept, 'Vendor');
  assert.equal(Q.changeFocusConcept(Q.formOf(R62), 'Floor', MAP).groupBy, null);
  const gone = Q.removeAt(f, 'limits', 0, MAP);
  assert.deepEqual(gone.groupBy, [{ concept: 'WorkOrder', attribute: 'raised_at' }], 'taking the limit away moves a grouping by it');
  assert.deepEqual(Q.removeAt(f, 'alsoShow', 0, MAP).groupBy, f.groupBy, 'nothing else touches the grouping');
});

test('a plan with a grouping can be grouped by a column of the focus or of a limit, and nothing else', () => {
  const f = Q.formOf(AGG);
  assert.deepEqual(Q.groupConcepts(f), ['WorkOrder', 'Building']);
  assert.deepEqual(Q.groupConcepts(Q.formOf({ focus: { concept: 'Vendor' } })), ['Vendor']);
});

test('what stops a query from running is said in words, next to the field', () => {
  assert.deepEqual(Q.formProblems(Q.formOf(INC), MAP), []);
  const f = Q.formOf(R62);
  f.focus.concept = '';
  assert.deepEqual(Q.formProblems(f, MAP), ['Choose what to look for.']);
  const g = Q.formOf(R62);
  g.focus.conditions = [{ attribute: 'floor_number', op: 'gt', value: '' }, { attribute: 'name', op: 'between', value: ['a', ''] },
    { attribute: 'name', op: 'in', value: ' , ' }, { attribute: '', op: 'eq', value: 'x' }, { attribute: 'name', op: 'is_null', value: false }];
  g.limits[0].conditions = [{ attribute: 'city', op: 'eq', value: '' }];
  g.alsoShow = [Object.assign(Q.blankNode('Vendor', true, MAP), { conditions: [{ attribute: 'vendor_name', op: 'contains', value: '' }] })];
  assert.deepEqual(Q.formProblems(g, MAP), [
    'What, condition 1: give a value.', 'What, condition 2: give both ends.', 'What, condition 3: give at least one value.', 'What, condition 4: choose a column.',
    'Limited to 1, condition 1: give a value.', 'Also show 1, condition 1: give a value.']);
  g.focus.conditions = [];
  g.limits[0].conditions = [];
  g.alsoShow[0].conditions = [];
  g.alsoShow[0].limit = '0';
  assert.deepEqual(Q.formProblems(g, MAP), ['Also show 1: how many must be a number from 1 to 200.']);
});

// ── The query in words ─────────────────────────────────────────────────────────────────────────────────────────────
test('the query reads as one plain sentence, beginning with who looked for it', () => {
  assert.equal(Q.describePlan(R62, MAP), 'Hoistra looked for a count of floors, limited to buildings matching “ABC”.');
  assert.equal(Q.describePlan(AGG, MAP), 'Hoistra looked for a count of work orders that are open where the raised date is within last month, limited to buildings, counted by name (buildings).');
  assert.equal(Q.describePlan(R62, MAP, 'Your version looks for'), 'Your version looks for a count of floors, limited to buildings matching “ABC”.');
  assert.equal(Q.describePlan({ question_type: 'single', focus: { concept: 'Vendor', match: 'Apex', states: [], filters: [] }, constraints: [], include: [] }, MAP),
    'Hoistra looked for one of the vendors matching “Apex”.');
});

test('conditions, states, limits and linked records are all in the sentence', () => {
  const out = Q.describePlan(INC, MAP);
  assert.ok(out.startsWith('Hoistra looked for a list of compliance certificates that are blocked or lapsed where '));
  assert.ok(out.includes('the expiry date is between today and today+60d'));
  assert.ok(out.includes('the cert type is one of Gas, Electrical'));
  assert.ok(out.includes('the notes is empty'));
  assert.ok(out.includes('the issuer contains “Reg”'));
  assert.ok(out.includes('the renewal cost is on or after 250'));
  assert.ok(out.includes('limited to buildings matching “Bishopsgate Tower” where the city is London, and vendors that are live'));
  assert.ok(out.includes('also showing work orders that are open for each, newest first, up to 5'));
  assert.ok(out.includes('and vendors for each, up to 50'));
  assert.ok(out.endsWith('.'));
});

test('a date column is named as a date, and a period in words', () => {
  assert.equal(Q.columnWords('sla_due_at', 'date'), 'SLA due date');
  assert.equal(Q.columnWords('expiry_date', 'date'), 'expiry date');
  assert.equal(Q.columnWords('completed_at', 'date'), 'completed date');
  assert.equal(Q.columnWords('cert_type', 'text'), 'cert type');
  assert.equal(Q.conditionWords({ attribute: 'raised_at', op: 'within', value: 'next_60_days' }, 'date'), 'the raised date is within the next 60 days');
  assert.equal(Q.conditionWords({ attribute: 'raised_at', op: 'lt', value: '2026-12-01' }, 'date'), 'the raised date is before 2026-12-01');
  assert.equal(Q.conditionWords({ attribute: 'cost', op: 'lt', value: 5 }, 'number'), 'the cost is less than 5');
  assert.equal(Q.conditionWords({ attribute: 'notes', op: 'is_null', value: false }, 'text'), 'the notes is not empty');
});

test('whether a form differs from the plan it started from', () => {
  assert.equal(Q.isChanged(R62, Q.formOf(R62)), false);
  const f = Q.formOf(R62);
  f.focus.match = 'x';
  assert.equal(Q.isChanged(R62, f), true);
  f.focus.match = '';
  assert.equal(Q.isChanged(R62, f), false, 'changed and changed back is not a change');
});

test('a status is explained in plain words, without the notes written for the model', () => {
  const lapsed = { id: 'lapsed', description: 'lapsed / expired - status in Expired, Lapsed, Overdue, or stored as in date with expiry_date passed (the expiry date decides). USE THIS for lapsed, expired or overdue certificates' };
  assert.equal(Q.stateHelp(lapsed), 'status in Expired, Lapsed, Overdue, or stored as in date with expiry date passed');
  assert.equal(Q.stateHelp({ id: 'valid', description: 'status = Valid' }), 'status is Valid');
  assert.equal(Q.stateHelp({ id: 'blocked', description: 'blocked / suspended / failed (outcome in Pass — 41 items failed and removed)' }), 'blocked, suspended or failed');
  assert.equal(Q.stateHelp({ id: 'blocking', description: 'lapsed, expired or blocked - dependent work cannot proceed (outcome in Pass — 41 items failed and remo' }), 'dependent work cannot proceed');
  const long = Q.stateHelp({ id: 'x', description: 'a '.repeat(80) });
  assert.ok(long.length <= 91 && long.endsWith('…'));
});

// ── No query: the reason, in words ─────────────────────────────────────────────────────────────────────────────────
test('a turn with no query says why, in the words the page uses', () => {
  const W = Q.NO_QUERY_WORDS;
  assert.equal(W.not_about_records, 'This question is not about records, so there is no query to check.');
  assert.equal(W.records_plan_unreadable, 'Hoistra read the records, but its query could not be read back from the trace.');
  assert.equal(W.planner_unavailable, 'No query could be proposed right now; it is tried again later.');
  assert.equal(W.no_map, "Hoistra's map of the records is not ready yet; it is tried again later.");
  assert.equal(W.read_failed, 'The records could not be read; it is tried again later.');
  assert.equal(W.plan_does_not_fit, 'The query does not fit the records any more.');
  assert.equal(W.no_trace, 'The conversation is not filed yet; it is tried again later.');
  assert.equal(W.asker_has_no_buildings, 'The person who asked has no buildings, so nothing can be read for them.');
  assert.equal(W.no_company, 'This question has no company, so nothing can be read.');
  assert.equal(W.no_question, 'There is no question text to plan from.');
  assert.equal(Q.noQueryWords('no_map'), W.no_map);
  assert.equal(Q.noQueryWords('something_new'), 'No query to check.');
  assert.equal(Q.noQueryWords(null), 'No query to check.');
  assert.equal(Q.noQueryWords(undefined), 'No query to check.');
});

// ── Use as the expected answer ─────────────────────────────────────────────────────────────────────────────────────
const SIDE = { found: true, facts: [{ id: 'f1', kind: 'count', label: 'Building records found', value: 1, receipt: 'q1' }],
  receipts: [{ id: 'q1', label: 'count', sql: 'SELECT count(*)', params: {}, row_count: 1, rows: [{ n: 1 }] }],
  read_at: '2026-11-19T10:00:00+00:00', unresolved: [], notes: ['Read "ABC" as a building name.'], sentence: 'Building records found: 1.' };
const EVIDENCE = { drafted_from: 'records', draft: 3, read_at: '2026-11-19T09:30:02+00:00', queries: [
  { source: 'hoistra', question: 'How many floors does ABC have?', plan: R62, found: true, unresolved: [], notes: [], read_at: '2026-11-19T09:30:01+00:00',
    sentence: 'Floor records found: 0.', scope: 'asker', scope_email: 'me@x.test', changed: [{ label: 'count', then: 3, now: 0 }],
    facts: [{ id: 'f1', kind: 'count', label: 'Floor records found', value: 0, receipt: 'q1' }], receipts: [{ id: 'q1', label: 'count', sql: 'SELECT 1', rows: [] }] },
  { source: 'proposed', question: 'And the vendors?', plan: INC, found: true, unresolved: [], notes: [], sentence: 'Vendor records found: 2.',
    facts: [{ id: 'f1', kind: 'count', label: 'Vendor records found', value: 2, receipt: 'q1' }], receipts: [{ id: 'q1', label: 'count', sql: 'SELECT 2', rows: [] }] }] };

test('Use as the expected answer saves the person’s version of that one query and keeps every other query as it was', () => {
  const mine = Object.assign({}, R62, { focus: Object.assign({}, R62.focus, { concept: 'Building' }) });
  const body = Q.usedBody({ evidence: EVIDENCE, queries: [{ index: 0, source: 'hoistra', plan: R62, question: 'How many floors does ABC have?' },
    { index: 1, source: 'proposed', plan: INC, question: 'And the vendors?' }], index: 0, plan: mine, result: { after: SIDE, changes: ['What: Floor records → Building records'] } });
  assert.equal(body.expected, 'Building records found: 1. Vendor records found: 2.', 'one sentence per query, in order, as the draft holds them');
  assert.equal(body.evidence.drafted_from, 'records');
  assert.equal(body.evidence.draft, 3);
  assert.deepEqual(body.evidence.changed, ['What: Floor records → Building records']);
  assert.equal(body.evidence.queries.length, 2);
  const [a, b] = body.evidence.queries;
  assert.deepEqual(a.plan, mine);
  assert.deepEqual(a.facts, SIDE.facts);
  assert.deepEqual(a.receipts, SIDE.receipts);
  assert.equal(a.sentence, 'Building records found: 1.');
  assert.equal(a.read_at, '2026-11-19T10:00:00+00:00');
  assert.deepEqual(a.notes, SIDE.notes);
  assert.deepEqual(a.unresolved, []);
  assert.equal(a.found, true);
  assert.equal(a.question, 'How many floors does ABC have?', 'the question stays');
  assert.equal(a.source, 'person');
  assert.equal(a.edited_from, 'hoistra');
  assert.deepEqual(a.changed, [], 'the records have not "changed since Hoistra answered": this is a different query');
  assert.deepEqual(b, EVIDENCE.queries[1], 'the other query is exactly as it was');
  assert.deepEqual(EVIDENCE.queries[0].plan, R62, 'the stored evidence is not edited in place');
});

test('the saved sentence is the changed query’s, and with several queries each one’s sentence follows the other', () => {
  const body = Q.usedBody({ evidence: EVIDENCE, queries: [], index: 1, plan: INC, result: { after: SIDE, changes: [] } });
  assert.equal(body.expected, 'Floor records found: 0. Building records found: 1.');
  assert.equal(body.evidence.queries[0].sentence, 'Floor records found: 0.');
  assert.equal(body.evidence.queries[1].source, 'person');
  assert.equal(body.evidence.queries[1].edited_from, 'proposed');
  const one = Q.usedBody({ evidence: null, queries: [{ index: 0, source: 'hoistra', plan: R62, question: 'Q?' }], index: 0, plan: R62, result: { after: SIDE, changes: [] } });
  assert.equal(one.expected, SIDE.sentence);
  assert.equal(one.evidence.queries.length, 1, 'a draft with no values yet still gets its query back');
  assert.equal(one.evidence.queries[0].question, 'Q?');
  assert.deepEqual(Object.keys(one).sort(), ['evidence', 'expected']);
});

// ── The map as the service sends it (final review, 19 Nov 2026): a copy of svc-trace-lab records_form.query_map for the
// map its tests build (test_trace_lab_records_form.py::test_the_maps_words_are_the_ones_the_editor_reads pins the same
// words). Change both together. ─────────────────────────────────────────────────────────────────────────────────────
const SERVICE_MAP = {"concepts": [{"name": "Building", "words": "buildings", "attributes": [{"name": "id", "kind": "text"}, {"name": "name", "kind": "text"}, {"name": "floors", "kind": "number"}, {"name": "created_at", "kind": "date"}], "states": [], "links": ["Floor", "ComplianceCertificate"]}, {"name": "Floor", "words": "floors", "attributes": [{"name": "id", "kind": "text"}, {"name": "name", "kind": "text"}, {"name": "building_id", "kind": "text"}, {"name": "area_m2", "kind": "number"}], "states": [], "links": ["Building", "ComplianceCertificate"]}, {"name": "ComplianceCertificate", "words": "compliance certificates", "attributes": [{"name": "id", "kind": "text"}, {"name": "cert_type", "kind": "text"}, {"name": "building_id", "kind": "text"}, {"name": "expiry_date", "kind": "date"}, {"name": "next_due_date", "kind": "date"}, {"name": "status", "kind": "text"}, {"name": "cost", "kind": "number"}], "states": [{"id": "lapsed", "description": "lapsed / expired"}, {"id": "expiring_soon", "description": "status = Expiring soon"}], "links": ["Building", "Floor"]}, {"name": "Vendor", "words": "vendors", "attributes": [{"name": "id", "kind": "text"}, {"name": "vendor_name", "kind": "text"}], "states": [], "links": []}], "ops": [{"id": "eq", "words": "is"}, {"id": "ne", "words": "is not"}, {"id": "in", "words": "is one of"}, {"id": "not_in", "words": "is not one of"}, {"id": "lt", "words": "is before", "number_words": "is less than"}, {"id": "lte", "words": "is on or before", "number_words": "is at most"}, {"id": "gt", "words": "is after", "number_words": "is more than"}, {"id": "gte", "words": "is on or after", "number_words": "is at least"}, {"id": "between", "words": "is between"}, {"id": "within", "words": "is within"}, {"id": "contains", "words": "contains"}, {"id": "is_null", "words": "is empty"}], "periods": ["today", "yesterday", "this_week", "last_week", "this_month", "last_month", "next_month", "this_quarter", "last_quarter", "this_year", "last_year", "last_7_days", "last_30_days", "last_90_days", "year_to_date"]};

test('the map as the service sends it reads as plain sentences', () => {
  assert.equal(Q.describePlan(R62, SERVICE_MAP), 'Hoistra looked for a count of floors, limited to buildings matching “ABC”.');
  const certs = { question_type: 'list', aggregate: null, include: [],
    focus: { concept: 'ComplianceCertificate', match: null, states: ['lapsed'], via: [], filters: [
      { attribute: 'expiry_date', op: 'within', value: 'next_60_days' }, { attribute: 'cost', op: 'lt', value: 5 },
      { attribute: 'next_due_date', op: 'lte', value: '2026-12-01' }] },
    constraints: [{ concept: 'Building', match: 'Bishopsgate', via: [], states: [], filters: [] }] };
  assert.equal(Q.describePlan(certs, SERVICE_MAP), 'Hoistra looked for a list of compliance certificates that are lapsed where the expiry date is '
    + 'within the next 60 days and the cost is less than 5 and the next due date is on or before 2026-12-01, limited to buildings matching “Bishopsgate”.');
  assert.equal(Q.conceptWords(SERVICE_MAP, 'ComplianceCertificate'), 'compliance certificates');
  assert.equal(Q.opsFor(SERVICE_MAP, 'date').find((o) => o.id === 'within').words, 'is within');
  assert.equal(Q.opsFor(SERVICE_MAP, 'number').find((o) => o.id === 'gte').words, 'is at least');
  assert.equal(Q.opsFor(SERVICE_MAP, 'date').find((o) => o.id === 'gte').words, 'is on or after');
});

test('a comparison on a number column reads the number words the map gives', () => {
  const map = { ops: [{ id: 'lt', words: 'is before', number_words: 'is under' }] };
  assert.equal(Q.opWords(map, 'lt', 'number'), 'is under');
  assert.equal(Q.opWords(map, 'lt', 'date'), 'is before');
  assert.equal(Q.opWords(null, 'lt', 'number'), 'is less than', 'without a map, ours');
});

test('the expected answer saved from a query is cut to what the service keeps', () => {
  const long = Object.assign({}, SIDE, { sentence: 'Linked: ' + 'x, '.repeat(4000) });
  const body = Q.usedBody({ evidence: null, queries: [{ index: 0, source: 'hoistra', plan: R62, question: 'Q?' }], index: 0, plan: R62,
    result: { after: long, changes: [] } });
  assert.equal(Q.EXPECTED_MAX, 8000, 'the service refuses a longer one (ExpectedIn.expected)');
  assert.equal(body.expected.length, Q.EXPECTED_MAX);
  assert.equal(body.evidence.queries[0].sentence, long.sentence, 'the values keep their whole sentence');
});
