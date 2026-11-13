// What a user with an access role sees on Home, and which buttons they are offered
// (12 Nov 2026). Home is the full platform's Home with everything an unticked page feeds left
// out - no card, no bar, no figure, and no sample figure standing in for one. A button that
// would write into a page they do not hold is not offered. Hussain: "the data should be
// restricted accordingly".
import { test } from 'node:test';
import assert from 'node:assert/strict';

const A = await import('../src/logic/accessRoles.js');

const role = (pages) => ({ id: 'u', role: 'user', access_role: 'general_user', pages });
const NORMAL = { id: 'n', role: 'user', access_role: null, pages: null };
const OFFICER = role(['buildings', 'compliance']);

const LIVE = () => ({
  heroLive: true,
  hoistBars: [{ key: 'contracts', short: 'Contracts' }, { key: 'assets', short: 'Assets' },
    { key: 'meters', short: 'Meter consent' }, { key: 'certificates', short: 'Certificates' }],
  pvNote: 'Live · detected this year', pvRows: [{ head: 'Compliance', detected: '£1k' }],
  pnlNote: 'Live actuals · no budget ledger is connected yet', pnlTop: [{ head: 'Compliance' }],
  pinned: [{ label: 'My saved card' }, { label: 'Which buildings put me at risk this month?' },
    { label: 'What needs my approval today?' }, { label: 'Which vendors are blocked right now?' }]
});
const HERO = { buildings: 3, certificates: 12, countries: ['GB'], vendors: 7 };

test('an account with every page gets no override at all', () => {
  assert.equal(A.roleHome(NORMAL, LIVE(), HERO), null);
  assert.equal(A.roleHome({ role: 'admin' }, LIVE(), HERO), null);
});

test('only the bars of ticked pages are drawn', () => {
  assert.deepEqual(A.roleHome(OFFICER, LIVE(), HERO).hoistBars.map((b) => b.key), ['certificates']);
  assert.deepEqual(A.roleHome(role(['vendors', 'energy']), LIVE(), HERO).hoistBars.map((b) => b.key), ['contracts', 'meters']);
  assert.equal(A.roleHome(role(['buildings']), LIVE(), HERO).homeTiles.score, false);
  assert.equal(A.roleHome(OFFICER, LIVE(), HERO).homeTiles.score, true);
});

test('a tile with nothing of theirs in it is not drawn', () => {
  const v = LIVE();
  v.pvRows = []; v.pnlTop = [];
  const out = A.roleHome(role(['buildings']), v, HERO);
  assert.deepEqual(out.homeTiles, { score: false, pnl: false, value: false });
  assert.deepEqual(A.roleHome(OFFICER, LIVE(), HERO).homeTiles, { score: true, pnl: true, value: true });
});

test('sample figures never stand in for a read that has not answered', () => {
  const seed = {
    heroLive: false,
    hoistBars: [{ short: 'Contracts', note: 'seed' }, { short: 'Certificates', note: 'seed' }],
    pvNote: 'Seed figures · the value read has not answered yet', pvRows: [{ head: 'Energy', detected: '£410k' }],
    pnlNote: 'Seed figures · no budget ledger is connected yet', pnlTop: [{ head: 'Energy' }],
    pinned: []
  };
  const out = A.roleHome(OFFICER, seed, HERO);
  assert.deepEqual(out.hoistBars, []);
  assert.deepEqual(out.pvRows, []);
  assert.deepEqual(out.pnlTop, []);
  assert.deepEqual(out.homeTiles, { score: false, pnl: false, value: false });
  assert.deepEqual(out.heroStats, []);
});

test('nor are sample totals left in a tile that is not drawn', () => {
  const seed = {
    heroLive: false, hoistBars: [{ short: 'Contracts', note: 'seed' }],
    hoistScore: { value: '78', band: 'Supervised autonomy', gap: 'Meter consent lowest at 71%', note: 'x' }, hoistScoreNote: 'Seed coverage',
    pvNote: 'Seed figures · the value read has not answered yet', pvRows: [{ head: 'Energy' }], pvTotal: '£970k',
    pnlNote: 'Seed figures · no budget ledger is connected yet', pnlTop: [{ head: 'Energy' }], pnlSaved: '£390k', pinned: []
  };
  const out = A.roleHome(OFFICER, seed, HERO);
  assert.equal(out.pvTotal, '—');
  assert.equal(out.pvNote, '');
  assert.equal(out.pnlSaved, '—');
  assert.equal(out.pnlNote, '');
  assert.deepEqual(out.hoistScore, { value: '—', band: '', gap: '', note: '' });
  assert.equal(out.hoistScoreNote, '');
});

test('live totals are passed through as they are', () => {
  const v = Object.assign(LIVE(), { pvTotal: '£1k', pnlSaved: '£2k', hoistScore: { value: '—', band: 'b', gap: 'g', note: 'n' }, hoistScoreNote: 'Live · 1 of 4 sources' });
  const out = A.roleHome(OFFICER, v, HERO);
  assert.equal(out.pvTotal, '£1k');
  assert.equal(out.pnlSaved, '£2k');
  assert.equal(out.pvNote, v.pvNote);
  assert.deepEqual(out.hoistScore, v.hoistScore);
  assert.equal(out.hoistScoreNote, 'Live · 1 of 4 sources');
});

test('the line under the title counts only what their pages hold', () => {
  assert.deepEqual(A.roleHome(OFFICER, LIVE(), HERO).heroStats, ['3 buildings hoisted', '12 certificates on record', 'UK']);
  assert.deepEqual(A.roleHome(role(['energy']), LIVE(), HERO).heroStats, ['3 buildings hoisted', 'UK']);
  const noCountry = Object.assign({}, HERO, { countries: [], buildings: 1, certificates: 1 });
  assert.deepEqual(A.roleHome(role(['vendors']), LIVE(), noCountry).heroStats, ['1 building hoisted', '7 vendors']);
  assert.deepEqual(A.roleHome(role(['compliance']), LIVE(), noCountry).heroStats, ['1 building hoisted', '1 certificate on record']);
});

test('a pinned question about a page they do not hold is not offered', () => {
  const labels = (acct) => A.roleHome(acct, LIVE(), HERO).pinned.map((p) => p.label);
  assert.deepEqual(labels(OFFICER), ['My saved card', 'Which buildings put me at risk this month?', 'What needs my approval today?', 'Which vendors are blocked right now?']);
  assert.deepEqual(labels(role(['energy'])), ['My saved card', 'What needs my approval today?']);
  assert.deepEqual(labels(role(['vendors'])), ['My saved card', 'What needs my approval today?', 'Which vendors are blocked right now?']);
});

// ── buttons that write into another page ────────────────────────────────────────────────
test('raising a work order from an asset needs the maintenance page', () => {
  assert.equal(A.canAct(role(['assets']), 'assetWorkOrder'), false);
  assert.equal(A.canAct(role(['assets']), 'assetInspection'), false);
  assert.equal(A.canAct(role(['assets', 'ops']), 'assetWorkOrder'), true);
  assert.equal(A.canAct(role(['assets', 'ops']), 'assetInspection'), true);
  assert.equal(A.canAct(role(['assets']), 'assetInvestigate'), true);
  assert.equal(A.canAct(NORMAL, 'assetWorkOrder'), true);
  assert.equal(A.canAct({ role: 'admin' }, 'assetWorkOrder'), true);
});

test('creating a vendor from a certificate needs the vendors page', () => {
  assert.equal(A.canAct(OFFICER, 'certificateCreateVendor'), false);
  assert.equal(A.canAct(role(['compliance', 'vendors']), 'certificateCreateVendor'), true);
});

test('an action nobody listed is not offered to a role user', () => {
  assert.equal(A.canAct(role(['assets', 'ops', 'vendors', 'compliance', 'energy', 'buildings']), 'somethingNew'), false);
  assert.equal(A.canAct(NORMAL, 'somethingNew'), true);
});

test('an asset\'s certificate lines are left out without the compliance page', () => {
  const chain = [{ a: 'location', t: 'x' }, { a: 'certificates', t: 'Reading…' }, { a: 'vendor accreditation', t: 'Reading…' }, { a: 'work history', t: 'y' }];
  assert.deepEqual(A.assetChain(role(['assets']), chain).map((c) => c.a), ['location', 'work history']);
  assert.equal(A.assetChain(role(['assets', 'compliance']), chain), chain);
  assert.equal(A.assetChain(NORMAL, chain), chain);
});
