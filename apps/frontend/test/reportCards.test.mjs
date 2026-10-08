// reportCards — the answer, cut into cards a reader can curate, and the hidden set that
// remembers which ones they put in the tray.
//
// The load-bearing property is the STABLE KEY: a report card re-runs on a cadence, so the
// answer is rebuilt every refresh. A key tied to array position would hide a different
// section next time — which in a compliance product means hiding a lapsed certificate
// someone meant to keep.
import { test } from 'node:test';
import assert from 'node:assert/strict';

globalThis.window = globalThis.window || { location: { origin: 'http://test.local' } };

const {
  richCards, markdownCards, answerCards, loadHidden, saveHidden,
  hiddenFor, withHidden, withNoneHidden, ownerKey, HIDDEN_KEY
} = await import('../src/logic/reportCards.js');

const RICH = {
  narrative: 'Six building certificates have lapsed.',
  kpis: [{ label: 'Expiring this month', count: 0 }, { label: 'Building duties already lapsed', count: 6 }],
  overdue: { buildings: [{ label: 'AN Other House', days: 7288, pct: '100%' }], vendors: [] },
  actions: [{ title: 'Instruct a competent fire risk assessor', severity: 'critical', scope: 'building' }],
  groups: [{ owner: 'AN Other House', scope: 'building', cert_ids: ['c1'], points: ['FRA lapsed 2006'] },
           { owner: 'Building 5', scope: 'building', cert_ids: ['c2'], points: ['EICR lapsed 2015'] }],
  insights: [{ type: 'anomaly', text: 'Three of six lapsed rows are near-identical EICR extracts.' }],
  certificates: [{ id: 'c1', name: 'Fire Risk Assessment', company: 'AN Other', scope: 'Building', status: 'Lapsed' }],
  pending: [{ name: 'EICR 00568607', what_is_pending: 'awaiting PM confirmation', cert_id: 'c2' }],
  offers: [{ kind: 'verify_now', label: 'Verify now', cert_id: 'c1', owner: 'AN Other House' }]
};

const memStorage = () => {
  const m = {};
  return { getItem: (k) => (k in m ? m[k] : null), setItem: (k, v) => { m[k] = String(v); }, removeItem: (k) => { delete m[k]; }, _m: m };
};

test('a structured answer becomes one card per thing worth looking at', () => {
  const cards = richCards(RICH);
  const kinds = cards.map((c) => c.kind);
  assert.equal(kinds.filter((k) => k === 'kpi').length, 2);
  assert.equal(kinds.filter((k) => k === 'group').length, 2);
  assert.ok(kinds.includes('narrative'));
  assert.ok(kinds.includes('action'));
  assert.ok(kinds.includes('insight'));
  assert.ok(kinds.includes('certificates'));
  assert.ok(kinds.includes('pending'));
  // Offers follow their subject onto its card, so removing a group removes its actions too.
  const g = cards.find((c) => c.key === 'group:an-other-house');
  assert.equal(g.data.offers.length, 1);
  assert.equal(cards.find((c) => c.key === 'group:building-5').data.offers.length, 0);
});

test('the charts lead: duration is the hero, and it comes first', () => {
  const cards = richCards(RICH);
  assert.equal(cards[0].kind, 'duration', 'the finding this data carries is how long, so it opens the report');
  assert.equal(cards[0].span, 3);
  assert.equal(cards[0].data.rows.length, 1, 'one overdue row in this fixture');
  assert.equal(cards[0].data.worst.days, 7288);
  const kinds = cards.map((c) => c.kind);
  assert.ok(kinds.includes('status'), 'the register becomes a strip, not a sentence');
  assert.ok(!kinds.includes('overdue'), 'the old bare bar card is gone — the duration chart replaced it');
  // A KPI carries its dial so the count can be drawn as an arc rather than typed out.
  const kpi = cards.find((c) => c.kind === 'kpi');
  assert.ok(kpi.data.dial, 'every KPI card carries its ring geometry');
});

test('a payload with nothing to chart draws no empty charts', () => {
  const kinds = richCards({ narrative: 'Just words.', kpis: [], certificates: [], groups: [] }).map((c) => c.kind);
  assert.deepEqual(kinds, ['narrative'], 'no duration, no status strip, no holder bars invented to fill space');
});

test('one holder holding everything is not a chart worth drawing', () => {
  // A bar chart where one bar is 100% tells the reader nothing the count did not.
  const one = richCards({ certificates: [{ name: 'a', company: 'Solo Ltd', status: 'Lapsed' }] });
  assert.ok(!one.map((c) => c.kind).includes('holders'));
  const two = richCards({ certificates: [{ company: 'A', status: 'Lapsed' }, { company: 'B', status: 'Lapsed' }] });
  assert.ok(two.map((c) => c.kind).includes('holders'));
});

test('keys are derived from identity, so they survive a refresh that reorders everything', () => {
  const before = richCards(RICH).map((c) => c.key);
  // The next refresh: fewer KPIs, groups in the other order, one new action, new counts.
  const after = richCards(Object.assign({}, RICH, {
    kpis: [{ label: 'Building duties already lapsed', count: 9 }],
    groups: [RICH.groups[1], RICH.groups[0]],
    actions: [{ title: 'Something new entirely', severity: 'warning' }, RICH.actions[0]]
  })).map((c) => c.key);
  assert.ok(before.includes('group:an-other-house') && after.includes('group:an-other-house'));
  assert.ok(before.includes('group:building-5') && after.includes('group:building-5'));
  assert.ok(after.includes('kpi:building-duties-already-lapsed'), 'the same KPI keeps its key though its count changed');
  assert.ok(!after.includes('kpi:expiring-this-month'), 'a KPI that is gone simply has no card');
  assert.ok(after.includes('action:instruct-a-competent-fire-risk-assessor'), 'and an action keeps its key though it moved');
});

test('a plain markdown answer is cut on its headings', () => {
  const cards = markdownCards('Lead paragraph.\n\n## First thing\n\n- a\n- b\n\n## Second thing\n\ntext');
  assert.deepEqual(cards.map((c) => c.title), ['', 'First thing', 'Second thing']);
  assert.equal(cards[0].data.text, 'Lead paragraph.');
  assert.deepEqual(cards.map((c) => c.key), ['md:part-0', 'md:first-thing', 'md:second-thing']);
});

test('the orchestrator writes bold lead-ins, not headings — those start cards too', () => {
  // This is the exact shape of the answer on the report page today.
  const cards = markdownCards([
    '**AN Other House** — No valid Fire Risk Assessment for nearly twenty years',
    '- The Fire Risk Assessment expired on 1 October 2006.',
    '',
    '**Building 5** — Electrical condition report lapsed since 2015',
    '- The EICR expired on 7 September 2015.',
    '',
    'Nothing in the register expires this month.'
  ].join('\n'));
  assert.deepEqual(cards.map((c) => c.title), ['AN Other House', 'Building 5']);
  assert.ok(cards[0].data.text.includes('1 October 2006'));
  assert.ok(cards[1].data.text.includes('Nothing in the register'), 'trailing prose stays with the last card rather than vanishing');
  assert.deepEqual(cards.map((c) => c.key), ['md:an-other-house', 'md:building-5']);
});

test('an answer with no structure at all is one card, not zero', () => {
  const cards = markdownCards('Just one flat paragraph with no headings.');
  assert.equal(cards.length, 1);
  assert.equal(cards[0].kind, 'markdown');
  assert.equal(cards[0].data.text, 'Just one flat paragraph with no headings.');
  // The key is stable across refreshes of the same shapeless answer, which is all it has to be.
  assert.equal(markdownCards('Just one flat paragraph with no headings.')[0].key, cards[0].key);
  assert.deepEqual(markdownCards(''), []);
});

test('a figures-and-table answer becomes charts, and the prose shrinks to a caption', () => {
  // The exact shape the approvals report comes back in.
  const cards = markdownCards([
    '### Findings',
    'You have several work orders pending your approval today.',
    '',
    '- **Total Pending Approvals:** 10 work orders',
    '- **Critical Priority:** 6 work orders',
    '- **Urgent Priority:** 2 work orders',
    '',
    '### Detailed Work Orders',
    '',
    '| Work Order ID | Asset | Priority |',
    '|---|---|---|',
    '| WO-1 | Painting | Medium |',
    '| WO-2 | Chiller | Urgent |',
    '| WO-3 | Chiller | Critical |',
    '| WO-4 | Chiller | Critical |',
    '',
    'Please review these and approve.'
  ].join('\n'));
  const kinds = cards.map((c) => c.kind);
  assert.ok(kinds.includes('figures'), 'the bullet figures became a chart');
  assert.ok(kinds.includes('distribution'), 'a repeating table column became a chart');
  assert.ok(kinds.includes('table'), 'the rows are still there, in a table that stays in its card');
  // The two filler sentences ride along as captions rather than taking a card each.
  assert.equal(kinds.filter((k) => k === 'markdown').length, 0, 'no prose card survived');
  assert.match(cards.find((c) => c.kind === 'figures').data.note, /pending your approval/);
  assert.equal(cards.find((c) => c.kind === 'figures').data.figures.length, 3);
});

test('two sections sharing a heading get distinct keys', () => {
  const cards = markdownCards('## Notes\n\nfirst\n\n## Notes\n\nsecond');
  assert.equal(cards.length, 2);
  assert.notEqual(cards[0].key, cards[1].key);
});

test('answerCards prefers the structured payload and falls back to the prose', () => {
  assert.ok(answerCards(RICH, 'ignored').some((c) => c.kind === 'kpi'));
  assert.equal(answerCards(null, '## Only text').length, 1);
  assert.deepEqual(answerCards(null, ''), []);
  assert.deepEqual(answerCards({ kpis: [], groups: [] }, ''), [], 'an empty payload is empty, not a bogus card');
});

test('the hidden set is scoped per account and per report card', () => {
  const me = { email: 'Hussain@Plenum-Tech.com' };
  const them = { email: 'other@plenum.co' };
  let map = {};
  map = withHidden(map, me, 'card-1', 'group:building-5', true);
  map = withHidden(map, me, 'card-2', 'certificates', true);
  map = withHidden(map, them, 'card-1', 'overall', true);
  assert.deepEqual(hiddenFor(map, me, 'card-1'), ['group:building-5']);
  assert.deepEqual(hiddenFor(map, me, 'card-2'), ['certificates']);
  assert.deepEqual(hiddenFor(map, them, 'card-1'), ['overall'], "another account's tray is its own");
  assert.deepEqual(hiddenFor(map, me, 'card-3'), []);
  assert.equal(ownerKey(me), 'hussain@plenum-tech.com', 'case and spacing cannot fork one person into two');
});

test('restoring removes the key, and restoring the last one cleans the entry out', () => {
  const me = { email: 'a@b.c' };
  let map = withHidden(withHidden({}, me, 'card-1', 'k1', true), me, 'card-1', 'k2', true);
  assert.deepEqual(hiddenFor(map, me, 'card-1'), ['k1', 'k2']);
  map = withHidden(map, me, 'card-1', 'k1', false);
  assert.deepEqual(hiddenFor(map, me, 'card-1'), ['k2']);
  map = withHidden(map, me, 'card-1', 'k2', false);
  assert.deepEqual(map, {}, 'no empty husks left behind');
  map = withHidden(withHidden({}, me, 'c', 'k1', true), me, 'c', 'k2', true);
  assert.deepEqual(withNoneHidden(map, me, 'c'), {});
});

test('hiding the same key twice does not duplicate it', () => {
  const me = { email: 'a@b.c' };
  const map = withHidden(withHidden({}, me, 'c', 'k', true), me, 'c', 'k', true);
  assert.deepEqual(hiddenFor(map, me, 'c'), ['k']);
});

test('the set round-trips through storage and survives a corrupt one', () => {
  const st = memStorage();
  const me = { email: 'a@b.c' };
  const map = withHidden({}, me, 'card-1', 'group:building-5', true);
  assert.equal(saveHidden(map, st), true);
  assert.deepEqual(hiddenFor(loadHidden(st), me, 'card-1'), ['group:building-5']);
  st._m[HIDDEN_KEY] = 'not json';
  assert.deepEqual(loadHidden(st), {});
  st._m[HIDDEN_KEY] = JSON.stringify({ 'a@b.c': { 'card-1': ['ok', 7, null], 'card-2': [] }, bad: 'nope' });
  const back = loadHidden(st);
  assert.deepEqual(hiddenFor(back, me, 'card-1'), ['ok'], 'junk entries are dropped, good ones kept');
  assert.deepEqual(hiddenFor(back, me, 'card-2'), []);
  assert.equal(saveHidden(map, { setItem: () => { throw new Error('quota'); } }), false, 'a full quota is not a crash');
  assert.deepEqual(loadHidden({ getItem: () => { throw new Error('blocked'); } }), {});
});

// ── key collisions ──────────────────────────────────────────────────────────
// A key is what a hidden card is remembered by. Two different sections sharing one means
// hiding one hides the other; a key that MOVES between refreshes hides the wrong thing
// later. Both were real: richCards had no de-duplication at all, and markdownCards
// disambiguated positionally.
test('two owners whose names flatten to the same slug keep separate keys', () => {
  const cards = richCards({ groups: [
    { owner: 'Acme Ltd.', points: [] }, { owner: 'Acme, Ltd', points: [] }, { owner: 'ACME LTD', points: [] }
  ] });
  const keys = cards.map((c) => c.key);
  assert.equal(new Set(keys).size, 3, 'three distinct owners, three distinct keys: ' + keys.join(' '));
});

test('names in a script with no latin characters do not all collapse into one bucket', () => {
  const cards = richCards({ groups: [
    { owner: '東京タワー', points: [] }, { owner: 'Москва', points: [] }, { owner: 'القاهرة', points: [] }
  ] });
  const keys = cards.map((c) => c.key);
  assert.equal(new Set(keys).size, 3, 'each non-latin owner keeps its own key: ' + keys.join(' '));
  assert.ok(keys.every((k) => k !== 'group:x'), 'and none of them is the empty-slug fallback');
});

test('two long names sharing their first 48 characters stay apart', () => {
  const a = 'Bishopsgate Tower Mechanical and Electrical Services — North Wing';
  const b = 'Bishopsgate Tower Mechanical and Electrical Services — South Wing';
  const cards = richCards({ groups: [{ owner: a, points: [] }, { owner: b, points: [] }] });
  assert.notEqual(cards[0].key, cards[1].key);
});

test('two KPIs with the same count do not share a key', () => {
  const cards = richCards({ kpis: [{ count: 0 }, { count: 0 }] });
  assert.notEqual(cards[0].key, cards[1].key);
});

test('a section keeps its key no matter what else is in the answer', () => {
  // The property that matters: a key depends on the section's OWN identity, so a refresh
  // that adds or drops other sections cannot slide someone's hidden card onto a new one.
  const full = markdownCards('## Alpha\n\na\n\n## Beta\n\nb\n\n## Gamma\n\nc');
  const fewer = markdownCards('## Gamma\n\nc');
  const reordered = markdownCards('## Gamma\n\nc\n\n## Alpha\n\na');
  const keyOf = (cards, title) => cards.find((c) => c.title === title).key;
  assert.equal(keyOf(full, 'Gamma'), keyOf(fewer, 'Gamma'), 'dropping Alpha and Beta leaves Gamma\'s key alone');
  assert.equal(keyOf(full, 'Gamma'), keyOf(reordered, 'Gamma'), 'and reordering does too');
  assert.equal(keyOf(full, 'Alpha'), keyOf(reordered, 'Alpha'));
});

test('two sections that really do share a title still get separate keys', () => {
  // Distinct keys is the guarantee — with identical titles, order is the only thing left to
  // tell them apart, and that is stated rather than pretended otherwise.
  const both = markdownCards('## Notes\n\nfirst\n\n## Notes\n\nsecond');
  assert.equal(both.length, 2);
  assert.notEqual(both[0].key, both[1].key, 'hiding one must not hide the other');
});
