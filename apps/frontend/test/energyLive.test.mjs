// shapeLiveAnomaly — the Energy page's anomaly rows, joined against the Buildings table
// and meters list already loaded elsewhere. Fixtures mirror the real shape seen from
// GET /api/energy/anomalies and GET /api/energy/meters against the running service
// (11 Sep 2026): open anomalies carry raw site_id/meter_id/asset_id UUIDs and no names.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { shapeLiveAnomaly, moneyGBP, IMPLEMENTED_RULE_IDS } from '../src/logic/energyLive.js';

const SITE = 'e2a55dc1-8d76-5fa2-93e8-f585cb31d6f4';
const METER = 'b814f978-23a4-4a0b-89ba-cf3e48df9852';
const EQUIP = '11111111-1111-1111-1111-111111111111';

const buildingsByUuid = {
  [SITE]: { name: 'Marina Heights', cc: 'AE', uuid: SITE }
};
const metersById = {
  [METER]: { id: METER, meter_type: 'electricity', mpan: '1200023306013', mprn: null }
};

test('a site+meter anomaly with no asset resolves the building and meter but not an asset', () => {
  const raw = {
    id: 'anom-1', anomaly_type: 'weekend_spike', status: 'open',
    metric_pct: 226.02, financial_gbp: 14967.99, annualised_excess_kwh: 53457.09,
    meter_id: METER, asset_id: null, site_id: SITE, detected_at: '2026-08-31T18:05:54Z',
    pm_action: null, pm_reason: null
  };
  const a = shapeLiveAnomaly(raw, buildingsByUuid, metersById, {});
  assert.equal(a.live, true);
  assert.equal(a.building, 'Marina Heights');
  assert.equal(a.cc, 'AE');
  assert.equal(a.assetResolved, false);
  assert.match(a.asset, /electricity meter/);
  assert.equal(a.type, 'Weekend / non-occupancy spike');
  assert.equal(a.ruleId, 'calendar');
  assert.equal(a.status, 'New');
  assert.equal(a.impact, '£15k');
  assert.equal(a.impactN, 14967.99);
});

test('an anomaly with a resolved equipment name uses it instead of the meter', () => {
  const raw = {
    id: 'anom-2', anomaly_type: 'asset_spike', status: 'open',
    metric_pct: 180, financial_gbp: 6100, annualised_excess_kwh: 9000,
    meter_id: METER, asset_id: EQUIP, site_id: SITE, detected_at: '2026-09-01T08:00:00Z',
    pm_action: null, pm_reason: null
  };
  const equipmentByUuid = { [EQUIP]: { equipment_id: EQUIP, asset_id: 'A0000042', name: 'Chiller CH-2' } };
  const a = shapeLiveAnomaly(raw, buildingsByUuid, metersById, equipmentByUuid);
  assert.equal(a.assetResolved, true);
  assert.equal(a.asset, 'Chiller CH-2');
  assert.equal(a.ruleId, 'spike');
});

test('an anomaly whose site_id matches no loaded building reads as unattributed, not a crash', () => {
  const raw = {
    id: 'anom-3', anomaly_type: 'baseline_drift', status: 'open',
    metric_pct: 109, financial_gbp: 2900, annualised_excess_kwh: 4100,
    meter_id: null, asset_id: null, site_id: 'unknown-site-uuid', detected_at: null,
    pm_action: 'watching', pm_reason: 'within tolerance'
  };
  const a = shapeLiveAnomaly(raw, {}, {}, {});
  assert.equal(a.building, 'Unattributed site');
  assert.equal(a.cc, '—');
  assert.equal(a.asset, 'Whole building');
  assert.equal(a.days, null);
  assert.equal(a.ruleId, 'drift');
  assert.equal(a.pmAction, 'watching');
});

test('an unrecognised anomaly_type still renders a readable label instead of throwing', () => {
  const raw = { id: 'anom-4', anomaly_type: 'peak_demand', status: 'ack', financial_gbp: 100, site_id: null };
  const a = shapeLiveAnomaly(raw, {}, {}, {});
  assert.equal(a.type, 'peak demand');
  assert.equal(a.ruleId, null);
  assert.equal(a.status, 'Ack');
});

test('moneyGBP formats thousands and preserves sign', () => {
  assert.equal(moneyGBP(14967.99), '£15k');
  assert.equal(moneyGBP(620), '£620');
  assert.equal(moneyGBP(0), '£0');
  assert.equal(moneyGBP(-500), '−£500');
});

test('all 13 detection rules are marked live, now that detectors.py implements the rest', () => {
  assert.equal(IMPLEMENTED_RULE_IDS.size, 13);
  ['calendar', 'drift', 'spike', 'nonocc', 'schedule', 'baseload', 'peak', 'dataq',
    'tou', 'weather', 'fight', 'regress', 'cop'].forEach((id) => {
    assert.ok(IMPLEMENTED_RULE_IDS.has(id), id + ' should be marked live');
  });
});

test('a new-detector anomaly_type resolves to a readable label and its rule id', () => {
  const a = shapeLiveAnomaly({ id: 'anom-5', anomaly_type: 'chiller_efficiency', status: 'open', financial_gbp: 3000, site_id: null }, {}, {}, {});
  assert.equal(a.type, 'Chiller efficiency');
  assert.equal(a.ruleId, 'cop');
  assert.equal(a.tone, 'risk');
});

// enRatingsFromPosition — the Energy page's "Ratings and duties" cards, now assembled from
// GET /api/energy/ratings/position (engines/energy/ratings_position.py) instead of the old
// hand-written "not sourced" placeholders. Exercised on the real controller so the state
// shape (enPosByCc, written by energyLive.js's enPositionLoad()) matches what enVals()
// actually reads.
globalThis.window = {
  location: { origin: 'http://test.local' }, scrollTo: () => {},
  addEventListener: () => {}, removeEventListener: () => {},
  localStorage: { getItem: () => null, setItem: () => {}, removeItem: () => {} }
};
globalThis.fetch = () => Promise.reject(new TypeError('no network in this test'));
const { HoistraLogic } = await import('../src/logic/HoistraLogic.js');

test('before the ratings engine has answered, the cards show a loading placeholder, not a fabricated figure', () => {
  const c = new HoistraLogic();
  c.setState({ signedIn: true, view: 'module', module: 'energy', enPosByCc: { UK: { loading: true, tiles: [] } } });
  const rows = c.enVals(c.state).enRatings;
  assert.ok(rows.length > 0);
  assert.ok(rows.every((r) => r.v === '…' && r.badge === ''));
});

test('a real MEES tile from the backend renders with the certificate badge, not the old constant', () => {
  const c = new HoistraLogic();
  c.setState({
    signedIn: true, view: 'module', module: 'energy',
    enPosByCc: { UK: { loading: false, error: '', tiles: [
      { l: 'MEES — enforceable now', v: '1', s: 'below EPC E · 1 of 6 lettable', tone: 'risk', basis: 'certificate' },
      { l: 'EPCs on file', v: '6 / 6', s: 'current, none expiring inside 12 months', tone: 'ok', basis: 'certificate' }
    ] } }
  });
  const rows = c.enVals(c.state).enRatings;
  const mees = rows.find((r) => r.l === 'MEES — enforceable now');
  assert.equal(mees.v, '1');
  assert.equal(mees.badge, 'Actual · from certificate');
  assert.equal(mees.confShow, 'none');
});

test('a consumption tile under 3 months reads insufficient data, not a number dressed up as real', () => {
  const c = new HoistraLogic();
  c.setState({
    signedIn: true, view: 'module', module: 'energy', enRatingCc: 'US',
    enPosByCc: { US: { loading: false, error: '', tiles: [
      { l: 'Energy Star score', v: '—', s: 'no score estimated yet', tone: 'dormant', basis: 'consumption', months: 0 }
    ] } }
  });
  const rows = c.enVals(c.state).enRatings;
  const row = rows.find((r) => r.l === 'Energy Star score');
  assert.equal(row.v, '—');
  assert.equal(row.color, 'var(--color-neutral-500)');
  assert.equal(row.badge, 'Not computed yet');
});

test('a backend error for one market shows an honest failure, not the seed constants', () => {
  const c = new HoistraLogic();
  c.setState({
    signedIn: true, view: 'module', module: 'energy',
    enPosByCc: { UK: { loading: false, error: 'timed out after 20s', tiles: [] } }
  });
  const rows = c.enVals(c.state).enRatings;
  assert.equal(rows.length, 1);
  assert.match(rows[0].s, /could not reach the ratings engine/);
});

// Energy module buildings list — pagination and search. A live portfolio can put 600+
// buildings under one country group (UAE in production); rendering them all inline was
// the actual bug report. bldLive here stands in for buildingsLive.js's already-loaded rows.
function fakeBuildings(n, cc) {
  return Array.from({ length: n }, (_, i) => ({
    live: true, name: 'Tower ' + String(i + 1).padStart(2, '0'), cc: cc || 'UK',
    uuid: 'b-' + i, euiN: null, benchN: null, areaM2: null, route: null, gran: 'none'
  }));
}

test('25 buildings in one market page at 20, and the page-2 remainder still carries the full group meta', () => {
  const c = new HoistraLogic();
  c.setState({ signedIn: true, view: 'module', module: 'energy', bldLive: fakeBuildings(25, 'UK') });
  const bv1 = c.enBuildingVals(c.state);
  assert.equal(bv1.enGroups.length, 1);
  assert.equal(bv1.enGroups[0].buildings.length, 20);
  assert.match(bv1.enGroups[0].meta, /^25 buildings/, 'the header still says 25, not the 20 shown');
  assert.equal(bv1.enBldPagerShow, 'flex');
  assert.equal(bv1.enBldPageCount, 2);
  assert.equal(bv1.enBldPagePrevShow, false);
  assert.equal(bv1.enBldPageNextShow, true);

  c.setState({ enBldPage: 1 });
  const bv2 = c.enBuildingVals(c.state);
  assert.equal(bv2.enGroups[0].buildings.length, 5, 'the remaining 5 on page 2');
  assert.equal(bv2.enBldPageNextShow, false);
  assert.equal(bv2.enBldPagePrevShow, true);
});

test('9 buildings need no pager at all — the control only appears once there is a second page', () => {
  const c = new HoistraLogic();
  c.setState({ signedIn: true, view: 'module', module: 'energy', bldLive: fakeBuildings(9, 'UK') });
  const bv = c.enBuildingVals(c.state);
  assert.equal(bv.enBldPagerShow, 'none');
  assert.equal(bv.enGroups[0].buildings.length, 9);
});

test('a name search narrows the list and resets to page 1', () => {
  const c = new HoistraLogic();
  c.setState({ signedIn: true, view: 'module', module: 'energy', bldLive: fakeBuildings(25, 'UK'), enBldPage: 1 });
  c.enBuildingVals(c.state).setEnBldQuery({ target: { value: 'Tower 05' } });
  assert.equal(c.state.enBldPage, 0, 'a new search always lands back on page 1');
  const bv = c.enBuildingVals(c.state);
  assert.equal(bv.enGroups.length, 1);
  assert.equal(bv.enGroups[0].buildings.length, 1);
  assert.equal(bv.enGroups[0].buildings[0].name, 'Tower 05');
  assert.match(bv.enListSummary, /matching “Tower 05”/);
});

test('a search bar only shows once there are enough buildings to need one', () => {
  const c = new HoistraLogic();
  c.setState({ signedIn: true, view: 'module', module: 'energy', bldLive: fakeBuildings(9, 'UK') });
  assert.equal(c.enBuildingVals(c.state).enBldQueryShow, 'none');
  c.setState({ bldLive: fakeBuildings(25, 'UK') });
  assert.equal(c.enBuildingVals(c.state).enBldQueryShow, 'flex');
});
