// The vendor drawer on the Assets page: which vendor an asset is assigned to, and — for an
// admin — moving it to another of the company's vendors (Hussain, 28 Sep 2026: a drawer with a
// Change control; direct, confirmed, admins only). The vendor decides who receives the asset's
// real work-order, inspection and records emails, so the drawer says so before the change, the
// change is confirmed, and a refusal is shown where it happened.
import { test, beforeEach, afterEach } from 'node:test';
import assert from 'node:assert/strict';

globalThis.window = { location: { origin: 'http://test.local' }, scrollTo: () => {} };

const { HoistraLogic } = await import('../src/logic/HoistraLogic.js');
const { energyApi } = await import('../src/api/energy.js');

const BLD = { buildingId: 'b1', id: 'b1', uuid: 'b1', name: 'Bishopsgate Tower', euiN: 214, benchN: 180, deviation: 19,
  std: 'CIBSE TM46', mix: [], route: 'sub-meter', granularity: 'sub-metered', metersActive: 4, cc: 'UK' };
const BOILER = { asset_id: 'a1', asset_name: 'Boiler 1', asset_code: 'B-301-BOILER-01', active: true, building_id: 'b1',
  vendor_id: 'v1' };
const seed = (c) => c.setState({
  signedIn: true, view: 'module', module: 'assets', filter: 'All', account: {},
  bldLive: [BLD], asLive: [BOILER], asLiveWos: [], asLocations: [], asSections: [], asAnoms: [], asInspections: [],
  asCondBands: [{ asset_id: 'a1', band: 'watch', reasons: [], vendor: 'Apex Mechanical' }], asOpenB: ['b1'], asOpenS: []
});

const VIEW = {
  ok: true, can_change: true,
  asset: { id: 'a1', name: 'Boiler 1', code: 'B-301-BOILER-01' },
  vendor: { id: 'v1', name: 'Apex Mechanical', code: 'APEX', trade: 'HVAC', accreditation: 'SafeContractor',
    block_state: 'Clear', block_reason: null, phone: '020 7946 0000', status: 'active' },
  contacts: { email: 'ops@apex.co.uk', candidates: [] },
  score: { score: 82.5, month: '2026-09-01' },
  last_change: { at: '2026-09-20T10:00:00', by: 'hussain@plenum-tech.com', from: 'Mitie', to: 'Apex Mechanical', note: 'contract moved' },
  choices: [
    { id: 'v1', name: 'Apex Mechanical', trade: 'HVAC', assignable: true, why: null, current: true },
    { id: 'v2', name: 'Mitie', trade: 'Multi-trade', assignable: true, why: null, current: false },
    { id: 'v3', name: 'Blocked Co', trade: null, assignable: false, why: 'blocked — insurance lapsed', current: false }
  ],
  unreadable: []
};

const row = (c) => {
  for (const g of c.renderVals().asGroups || []) {
    for (const sec of g.sections || []) for (const r of sec.rows || []) if (r.name === 'Boiler 1') return r;
    for (const r of g.rows || []) if (r.name === 'Boiler 1') return r;
  }
  throw new Error('Boiler 1 row not rendered');
};
const E = { stopPropagation() {} };
const settle = async () => { for (let i = 0; i < 6; i++) await Promise.resolve(); };

const real = { view: energyApi.assetVendor, change: energyApi.changeAssetVendor, intel: energyApi.assetIntelligence };
let c;
beforeEach(() => {
  c = new HoistraLogic(); seed(c);
  energyApi.assetIntelligence = () => Promise.reject(Object.assign(new Error('nf'), { status: 404 }));
});
afterEach(() => {
  energyApi.assetVendor = real.view; energyApi.changeAssetVendor = real.change; energyApi.assetIntelligence = real.intel;
  clearTimeout(c._tt);
});

test('the vendor on an asset row opens its drawer with what the record holds', async () => {
  let asked = null;
  energyApi.assetVendor = async (id) => { asked = id; return VIEW; };
  await row(c).vendorOpen(E);
  assert.equal(asked, 'a1');
  const v = c.renderVals();
  assert.equal(v.vendorDrawerOpen, true);
  const d = v.vd;
  assert.equal(d.title, 'Boiler 1 · vendor');
  assert.equal(d.vendorName, 'Apex Mechanical');
  assert.equal(d.vendorLine, 'APEX · HVAC · SafeContractor');
  assert.equal(d.blockText, 'Clear — not blocked');
  assert.equal(d.contactLine, 'ops@apex.co.uk');
  assert.equal(d.scoreLine, '82.5/100 · Sep 2026');
  assert.match(d.lastChange, /^Changed from Mitie to Apex Mechanical by hussain@plenum-tech\.com on 20 Sep 2026 — contract moved$/);
  assert.equal(d.canChange, true);
  assert.deepEqual(d.options.map((o) => [o.label, o.disabled]), [
    ['Apex Mechanical · HVAC (current)', true],
    ['Mitie · Multi-trade', false],
    ['Blocked Co — blocked — insurance lapsed', true]
  ]);
});

test('a facilities manager reads the drawer but cannot change the vendor', async () => {
  energyApi.assetVendor = async () => ({ ...VIEW, can_change: false });
  await row(c).vendorOpen(E);
  const d = c.renderVals().vd;
  assert.equal(d.canChange, false);
  assert.equal(d.changeShow, 'none');
  assert.equal(d.readOnlyNote, 'Only an admin can change the vendor an asset is assigned to.');
});

test('a change is confirmed first, says what moves, then sent and re-read', async () => {
  let views = 0, sent = null;
  energyApi.assetVendor = async () => { views += 1; return views === 1 ? VIEW
    : { ...VIEW, vendor: { ...VIEW.vendor, id: 'v2', name: 'Mitie' } }; };
  energyApi.changeAssetVendor = async (id, vid, note) => { sent = [id, vid, note];
    return { ok: true, changed: true, vendor: { id: 'v2', name: 'Mitie' }, previous: { id: 'v1', name: 'Apex Mechanical' } }; };
  await row(c).vendorOpen(E);
  let d = c.renderVals().vd;
  assert.equal(d.askShow, 'none', 'nothing to confirm until another vendor is chosen');
  d.pickChange({ target: { value: 'v2' } });
  c.renderVals().vd.noteChange({ target: { value: 'contract moved to Mitie' } });
  d = c.renderVals().vd;
  assert.equal(d.askShow, 'inline-flex');
  d.ask();
  d = c.renderVals().vd;
  assert.equal(d.confirmShow, 'block');
  assert.equal(d.confirmText, 'Move Boiler 1 from Apex Mechanical to Mitie? Future work-order, inspection and '
    + 'records emails go to Mitie’s contact. Past work orders and scores stay with Apex Mechanical.');
  assert.equal(sent, null, 'nothing is written before the confirmation');
  await d.confirm();
  await settle();
  assert.deepEqual(sent, ['a1', 'v2', 'contract moved to Mitie']);
  d = c.renderVals().vd;
  assert.equal(d.doneText, 'Boiler 1 is now assigned to Mitie. The change is in the audit log.');
  assert.equal(d.confirmShow, 'none');
  assert.equal(d.vendorName, 'Mitie', 'the drawer re-read the record');
  assert.equal(row(c).vendor, 'Mitie', 'the row names the new vendor');
});

test('a refusal is shown where it happened and nothing else moves', async () => {
  energyApi.assetVendor = async () => VIEW;
  energyApi.changeAssetVendor = () => Promise.reject(Object.assign(new Error('409'), { status: 409,
    body: { detail: { ok: false, reason: 'blocked', error: 'Mitie is blocked: insurance lapsed. A blocked vendor cannot be assigned.' } } }));
  await row(c).vendorOpen(E);
  c.renderVals().vd.pickChange({ target: { value: 'v2' } });
  c.renderVals().vd.ask();
  await c.renderVals().vd.confirm();
  const d = c.renderVals().vd;
  assert.equal(d.errorText, 'Mitie is blocked: insurance lapsed. A blocked vendor cannot be assigned.');
  assert.equal(d.vendorName, 'Apex Mechanical');
  assert.equal(row(c).vendor, 'Apex Mechanical');
});

test('a disabled choice cannot be picked, and the current vendor asks nothing', async () => {
  energyApi.assetVendor = async () => VIEW;
  await row(c).vendorOpen(E);
  c.renderVals().vd.pickChange({ target: { value: 'v3' } });
  assert.equal(c.renderVals().vd.askShow, 'none');
  c.renderVals().vd.pickChange({ target: { value: 'v1' } });
  assert.equal(c.renderVals().vd.askShow, 'none');
});

test('a drawer answer for an asset no longer open is dropped', async () => {
  let release;
  energyApi.assetVendor = () => new Promise((r) => { release = r; });
  row(c).vendorOpen(E);
  c.asVendorClose();
  release(VIEW);
  await settle();
  assert.equal(c.renderVals().vendorDrawerOpen, false);
  assert.equal(c.state.vd, null);
});

test('switching company closes the drawer', async () => {
  energyApi.assetVendor = async () => VIEW;
  await row(c).vendorOpen(E);
  c.resetLiveData();
  assert.equal(c.renderVals().vendorDrawerOpen, false);
});

test('a read that fails says so in the drawer', async () => {
  energyApi.assetVendor = () => Promise.reject(Object.assign(new Error('the vendor read timed out'), { status: 504 }));
  await row(c).vendorOpen(E);
  const d = c.renderVals().vd;
  assert.equal(d.errorText, 'The vendor could not be read — the vendor read timed out.');
});

test('the picker lists only the current vendor\'s trade, and says so', async () => {
  energyApi.assetVendor = async () => ({ ...VIEW,
    vendor: { ...VIEW.vendor, trade: 'Mechanical' },
    choices: [
      { id: 'v1', name: 'Apex Mechanical', trade: 'Mechanical', assignable: true, why: null, current: true },
      { id: 'v4', name: 'Coolair', trade: 'Mechanical Services', assignable: true, why: null, current: false }
    ],
    trade_filter: { trade: 'Mechanical', applied: true, hidden: 3 } });
  await row(c).vendorOpen(E);
  const d = c.renderVals().vd;
  assert.equal(d.tradeHint, 'Only Mechanical vendors are listed — the same trade as Apex Mechanical. '
    + "3 of the company's vendors are of another trade.");
  assert.equal(d.pickPrompt, 'Choose another Mechanical vendor…');
  assert.deepEqual(d.options.map((o) => o.label), ['Apex Mechanical · Mechanical (current)', 'Coolair · Mechanical Services']);
});

test('with no other vendor of the trade, the drawer says there is nowhere to move it', async () => {
  energyApi.assetVendor = async () => ({ ...VIEW, choices: [VIEW.choices[0]],
    trade_filter: { trade: 'HVAC', applied: true, hidden: 2 } });
  await row(c).vendorOpen(E);
  assert.equal(c.renderVals().vd.tradeHint,
    "No other of the company's vendors is HVAC, so there is no vendor to move this asset to.");
});

test('a current vendor with no trade lists every vendor and says why', async () => {
  energyApi.assetVendor = async () => ({ ...VIEW, vendor: { ...VIEW.vendor, trade: null },
    trade_filter: { trade: null, applied: false, hidden: 0 } });
  await row(c).vendorOpen(E);
  const d = c.renderVals().vd;
  assert.equal(d.tradeHint, "Apex Mechanical has no trade on record, so every one of the company's vendors is listed.");
  assert.equal(d.pickPrompt, "Choose another of the company's vendors…");
});

test('the asset drawer offers the vendor drawer', async () => {
  energyApi.assetVendor = async () => VIEW;
  row(c).open();
  const act = c.renderVals().detailActions.find((a) => a.label === 'Vendor');
  assert.ok(act, 'a Vendor action in the asset drawer');
  await act.click();
  assert.equal(c.renderVals().vendorDrawerOpen, true);
  assert.equal(c.renderVals().detailOpen, false, 'the asset drawer gives way to the vendor drawer');
});


// ── pre-push review, 29 Sep 2026 ─────────────────────────────────────────────────────────

test('signing out closes the vendor drawer — it never shows over the sign-in gate', async () => {
  energyApi.assetVendor = async () => VIEW;
  await row(c).vendorOpen(E);
  assert.equal(c.renderVals().vendorDrawerOpen, true);
  c.authSignedOut('');
  assert.equal(c.renderVals().vendorDrawerOpen, false);
  assert.equal(c.state.vdOpen, false);
});

test('a details read already in flight when the vendor changes does not bring the old vendor back', async () => {
  let release;
  energyApi.assetIntelligence = () => new Promise((r) => { release = r; });
  const inFlight = c.asCondLoadIntel('a1');
  energyApi.assetVendor = async () => VIEW;
  energyApi.changeAssetVendor = async () => ({ ok: true, changed: true, vendor: { id: 'v2', name: 'Mitie' } });
  await row(c).vendorOpen(E);
  c.renderVals().vd.pickChange({ target: { value: 'v2' } });
  c.renderVals().vd.ask();
  await c.renderVals().vd.confirm();
  await settle();
  release({ asset: { vendor: 'Apex Mechanical' } });
  await inFlight;
  await settle();
  assert.equal(row(c).vendor, 'Mitie', 'the answer read before the change is dropped');
});

test('a vendor change reaches an investigation open on the same asset', async () => {
  // Re-review, 29 Sep 2026: its Raise work order still drafted to the old vendor's contact.
  energyApi.assetVendor = async () => VIEW;
  energyApi.changeAssetVendor = async () => ({ ok: true, changed: true, vendor: { id: 'v2', name: 'Mitie' } });
  await row(c).vendorOpen(E);
  c.setState({ inv: { live: true, loading: false, error: '', kind: 'asset', assetId: 'a1', title: 'Boiler 1',
    sub: 'Apex Mechanical', vendorEmail: 'ops@apex.co.uk', vendorEmailCandidates: ['a@apex.co.uk'],
    query: '', plan: '', sources: [], findings: [], actions: [], replies: [], cause: '', costLine: '' } });
  c.renderVals().vd.pickChange({ target: { value: 'v2' } });
  c.renderVals().vd.ask();
  await c.renderVals().vd.confirm();
  await settle();
  assert.equal(c.state.inv.sub, 'Mitie');
  assert.equal(c.state.inv.vendorEmail, null, 'the old contact is not reused');
  assert.deepEqual(c.state.inv.vendorEmailCandidates, []);
});
