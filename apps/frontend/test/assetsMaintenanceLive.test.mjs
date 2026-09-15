// Integration check for the wiring done in assetsLive.js / maintenanceLive.js: renderVals()
// must never throw for the assets or ops module regardless of whether the live register has
// loaded, and once it has, the page vals must actually reflect it (not silently keep
// rendering the seed) — this is the one thing the pure shaping-function tests cannot show,
// since they never go through mxVals()/renderVals() the way the screens do.
//
// For assets the seed is gone entirely: the condition-scan section read a hardcoded fixture
// that no allocation or company scope could reach, so every tenant saw the same nine
// buildings. renderVals must now produce an assets page out of the live register alone.
import { test, beforeEach } from 'node:test';
import assert from 'node:assert/strict';
import { NOT_METERED } from '../src/logic/assetsCondition.js';

// One building shaped the way shapeLiveBuildings() shapes them — bldRow() reads mix/route
// on every row, so a fixture missing them fails in the buildings page, not in this one.
const BLD = { buildingId: 'b1', id: 'b1', name: 'Tenant Tower', euiN: 220, benchN: 180, deviation: 22,
  std: 'CIBSE TM46', mix: [], route: 'utility account', granularity: 'none', metersActive: 0, cc: 'UK' };

globalThis.window = { location: { origin: 'http://test.local' }, scrollTo: () => {} };

const { HoistraLogic } = await import('../src/logic/HoistraLogic.js');

let c;
beforeEach(() => {
  c = new HoistraLogic();
  c.setState({ signedIn: true, view: 'module', module: 'assets', filter: 'All' });
});

test('the assets module renders with no register loaded yet — empty, and no fixture behind it', () => {
  const vals = c.renderVals();
  assert.deepEqual(vals.asGroups, [], 'nothing renders until the backend answers');
  assert.equal(vals.asEmpty, 'block');
  assert.match(vals.asEmptyText, /No assets are in scope/);
  assert.deepEqual(vals.iotCards, [], 'no instrumented assets without readings on file');
  assert.deepEqual(vals.modMetrics.map((m) => m.v), ['0', '0', '—', '0 of 0']);
  assert.match(vals.modMetrics[2].s, /Not computable/, 'value at risk is not computable, not zero');
});

test('the condition tree is built from the register, the buildings list and the anomalies', () => {
  c.bldLive = null;
  c.setState({
    bldLive: [BLD],
    asLive: [
      { asset_id: 'a1', asset_name: 'AHU-04', active: true, health_score: 80, building_id: 'b1', location_id: 'L1' },
      { asset_id: 'a2', asset_name: 'Pump-2', active: true, health_score: 90, building_id: 'b1', location_id: 'L1' }
    ],
    asLiveWos: [],
    asLocations: [{ location_id: 'L1', building_id: 'b1', name: 'Level 4 East' }],
    // Only a1 is named by an anomaly — a2 shares the building but nothing attributes to it.
    asSections: [
      { section_id: 's1', building_id: 'b1', name: 'Central plant', section_type: 'basement', eui_kwh_per_m2: 57.4, reference_eui_kwh_m2: 180, deviation_pct: -68.1, measured: true, meters: 1, reference_source: 'CIBSE TM46' },
      { section_id: 's2', building_id: 'b1', name: 'Car park', section_type: 'parking', eui_kwh_per_m2: null, reference_eui_kwh_m2: 45, deviation_pct: null, measured: false, meters: 0 }
    ],
    asAnoms: [{ id: 'x1', asset_id: 'a1', status: 'open', anomaly_type: 'Excess load', detected_at: new Date(Date.now() - 30 * 86400000).toISOString(), financial_gbp: 4200 }],
    asOpenB: ['b1'], asOpenS: ['b1|L1']
  });
  const vals = c.renderVals();
  assert.equal(vals.asGroups.length, 1);
  const g = vals.asGroups[0];
  assert.equal(g.name, 'Tenant Tower', 'the building name is the real one');
  assert.equal(g.eui, '220 vs 180 kWh/m²/yr');
  assert.equal(g.delta, '+22%');
  assert.equal(g.std, 'CIBSE TM46');
  assert.equal(g.state, 'above reference');
  // Real sections come first with their own reference; the assets sit on the location row
  // beneath, because AssetResponse still does not return section_id to link them.
  const secRow = g.sections.find((x) => x.name.startsWith('Central plant'));
  assert.ok(secRow, 'the real section renders');
  assert.equal(secRow.eui, '57.4 vs 180 kWh/m²/yr', 'judged against its OWN reference');
  assert.equal(secRow.delta, '-68%');
  assert.deepEqual(secRow.rows, [], 'no assets can be placed in it yet');
  const notMetered = g.sections.find((x) => x.name.startsWith('Car park'));
  assert.equal(notMetered.eui, 'Not metered', 'a section with no sub-meter is not metered, not zero');
  assert.equal(notMetered.meter, NOT_METERED, 'and the line beside it says why that is not zero consumption');
  assert.equal(notMetered.state, 'not metered');

  const locRow = g.sections.find((x) => x.name.indexOf('Level 4 East') === 0);
  assert.match(locRow.name, /Not yet linked to a section/);
  const rows = locRow.rows;
  const ahu = rows.find((r) => r.name === 'AHU-04');
  const pump = rows.find((r) => r.name === 'Pump-2');
  assert.equal(ahu.cond, 'Threat', 'over reference + an anomaly attributed to it');
  assert.equal(pump.cond, 'Watch', 'over reference, but nothing attributed to it');
  assert.match(ahu.anomText, /Excess load/);
  assert.equal(pump.anomText, 'No anomaly attributed');
  assert.equal(vals.asThreatN, 1);
  assert.equal(c.renderVals().navSections.find((i) => i.label === 'Assets').count, 1);
});

test('an asset the value engine cannot compute says so, and is never shown as zero', () => {
  c.setState({
    bldLive: [BLD],
    asLive: [{ asset_id: 'a1', asset_name: 'AHU-04', active: true, health_score: 80, building_id: 'b1' }],
    asLiveWos: [], asLocations: [], asAnoms: [], asOpenB: ['b1'], asOpenS: ['b1|__none']
  });
  const r = c.renderVals().asGroups[0].sections.find((x) => x.rows.length).rows[0];
  assert.equal(r.valNow, '—', 'this asset is absent from value-at-risk');
  assert.equal(r.valLoss, '—');
  assert.match(r.valLine, /Not computable/, 'excluded, never counted as worth nothing');
});

// A read that failed is not a read that came back empty. Saying "none on record" when the
// service 404'd or 500'd is the same dishonesty as showing a fixture: the page states a fact
// about the data that it has no basis for.
test('a section read that failed says so, and never reports the building as having none', () => {
  c.setState({
    bldLive: [BLD],
    asLive: [{ asset_id: 'a1', asset_name: 'AHU-04', active: true, health_score: 80, building_id: 'b1' }],
    asLiveWos: [], asLocations: [], asAnoms: [],
    asSections: [], asSectionsError: 'HTTP 404', asOpenB: ['b1']
  });
  const vals = c.renderVals();
  assert.match(vals.asGroups[0].secLine, /unreachable|404/i, 'the failure is named');
  assert.equal(/no sections on record/i.test(vals.asGroups[0].secLine), false, 'and never reported as none on record');
  assert.match(vals.asSummary, /sections unreachable/i);
});

test('with the section read healthy, a building genuinely without sections says so', () => {
  c.setState({
    bldLive: [BLD],
    asLive: [{ asset_id: 'a1', asset_name: 'AHU-04', active: true, health_score: 80, building_id: 'b1' }],
    asLiveWos: [], asLocations: [], asAnoms: [],
    asSections: [], asSectionsError: '', asOpenB: ['b1']
  });
  assert.match(c.renderVals().asGroups[0].secLine, /No sections on record/);
});

// The buildings register carries three keys per row and which one is filled depends on
// whether the graph is rooted on `buildings` or on `sites`: building_id is null for a
// site-rooted row, and the energy/compliance tables key on the site UUID where the CMMS
// tables key on site_id. assets.building_id can therefore match any of them, so indexing
// on building_id alone leaves real, named, in-scope buildings rendering as "Building
// 95649fde…" — a name the user has never seen, on a building they do own.
test('an asset resolves its building name through site_uuid and site_id, not only building_id', () => {
  c.setState({
    bldLive: [
      { ...BLD, buildingId: 'bid-1', uuid: null, siteId: null, id: 'bid-1', name: 'Town Hall' },
      { ...BLD, buildingId: null, uuid: 'uuid-2', siteId: 'S2', id: 'S2', name: 'Harbour View' },
      { ...BLD, buildingId: null, uuid: null, siteId: 'S3', id: 'S3', name: 'Sky Plaza' }
    ],
    asLive: [
      { asset_id: 'a1', asset_name: 'AHU-1', active: true, health_score: 80, building_id: 'bid-1' },
      { asset_id: 'a2', asset_name: 'AHU-2', active: true, health_score: 80, building_id: 'uuid-2' },
      { asset_id: 'a3', asset_name: 'AHU-3', active: true, health_score: 80, building_id: 'S3' }
    ],
    asLiveWos: [], asLocations: [], asAnoms: [], asSections: []
  });
  const names = c.renderVals().asGroups.map((g) => g.name).sort();
  assert.deepEqual(names, ['Harbour View', 'Sky Plaza', 'Town Hall']);
  names.forEach((n) => assert.equal(/^Building [0-9a-f]{8}/.test(n), false, n + ' should not be a raw uuid'));
});

test('an asset whose building really is not in the register still says so plainly', () => {
  c.setState({
    bldLive: [{ ...BLD, buildingId: 'bid-1', uuid: null, siteId: null, id: 'bid-1', name: 'Town Hall' }],
    asLive: [{ asset_id: 'a9', asset_name: 'Orphan', active: true, health_score: 80, building_id: 'nowhere-1234' }],
    asLiveWos: [], asLocations: [], asAnoms: [], asSections: []
  });
  const g = c.renderVals().asGroups[0];
  assert.match(g.name, /not in your buildings register/i, 'names the condition rather than printing a uuid');
});

// Assets, sections and the buildings register do not agree on which of a building's three
// keys they carry. An asset can arrive keyed on the site UUID while that building's sections
// arrive keyed on building_id — grouping each on its own raw key puts them in different
// buckets, so a building shows "No sections on record" while its sections sit one bucket
// away. Both sides resolve through the register to one canonical key.
test('a section reaches its building even when it and the assets carry different keys', () => {
  c.setState({
    bldLive: [{ ...BLD, buildingId: 'bid-1', uuid: 'uuid-1', siteId: 'S1', id: 'bid-1',
                name: 'Harbour View', euiN: 240, benchN: 200, deviation: 20 }],
    // The asset names the building by its site UUID; the section names it by building_id.
    asLive: [{ asset_id: 'a1', asset_name: 'AHU-1', active: true, health_score: 80, building_id: 'uuid-1' }],
    asLiveWos: [], asLocations: [], asAnoms: [],
    asSections: [{ section_id: 's1', building_id: 'bid-1', name: 'Central plant', eui_kwh_per_m2: 300,
                   reference_eui_kwh_m2: 180, deviation_pct: 66.7, measured: true, meters: 1 }],
    asOpenB: ['bid-1']
  });
  const groups = c.renderVals().asGroups;
  assert.equal(groups.length, 1, 'one building, not two buckets of the same one');
  assert.equal(groups[0].name, 'Harbour View');
  assert.match(groups[0].secLine, /1 of 1 metered sections over reference/);
  assert.ok(groups[0].sections.some((x) => x.name.startsWith('Central plant')), 'the section is on its building');
});

// The same null lookup that produced the uuid names also starved the energy rule: with no
// building row there was no deviation, so every asset in it read "No EUI on record" and
// could never be judged over reference.
test('an asset keyed on the site UUID still gets its building EUI and can trip the rule', () => {
  c.setState({
    bldLive: [{ ...BLD, buildingId: null, uuid: 'uuid-2', siteId: 'S2', id: 'S2',
                name: 'Sky Plaza', euiN: 240, benchN: 200, deviation: 20 }],
    asLive: [{ asset_id: 'a2', asset_name: 'Chiller-1', active: true, health_score: 80, building_id: 'uuid-2' }],
    asLiveWos: [], asLocations: [], asAnoms: [], asSections: [], asOpenB: ['uuid-2'], asOpenS: ['uuid-2|__none']
  });
  const g = c.renderVals().asGroups[0];
  assert.equal(g.name, 'Sky Plaza');
  assert.equal(g.eui, '240 vs 200 kWh/m²/yr', 'the EUI is read, not reported missing');
  assert.equal(g.delta, '+20%');
  assert.equal(g.state, 'above reference');
  const r = g.sections.find((x) => x.rows.length).rows[0];
  assert.equal(r.cond, 'Watch', 'over reference with no anomaly attributed');
});

// AssetResponse still does not return section_id, but the per-asset intelligence read does.
// Any asset whose intelligence has come back can therefore sit in its real section, which is
// the tree the design draws. The rest fall back to the location row rather than guessing.
test('an asset whose intelligence has been read sits under its real section', () => {
  c.setState({
    bldLive: [{ ...BLD, buildingId: 'b1', uuid: null, siteId: null, id: 'b1', name: 'Marlowe House' }],
    asLive: [
      { asset_id: 'a1', asset_name: 'Booster pump 6', active: true, health_score: 30, building_id: 'b1' },
      { asset_id: 'a2', asset_name: 'Unread asset', active: true, health_score: 90, building_id: 'b1' }
    ],
    asLiveWos: [], asLocations: [], asAnoms: [],
    asSections: [{ section_id: 'sec-1', building_id: 'b1', name: 'Central plant', eui_kwh_per_m2: 300,
                   reference_eui_kwh_m2: 180, deviation_pct: 66.7, measured: true, meters: 1 }],
    asIntel: { a1: { asset: { section_id: 'sec-1', section: 'Central plant', vendor: 'Halden' } } },
    asOpenB: ['b1'], asOpenS: ['b1|sec|sec-1', 'b1|__none']
  });
  const g = c.renderVals().asGroups[0];
  const real = g.sections.find((x) => x.name.startsWith('Central plant'));
  assert.deepEqual(real.rows.map((r) => r.name), ['Booster pump 6'], 'placed in its real section');
  const fallback = g.sections.find((x) => /Not yet linked to a section/.test(x.name));
  assert.deepEqual(fallback.rows.map((r) => r.name), ['Unread asset'], 'the unread one is not guessed into a section');
});

// The two figures on the header measure different things and were reading as one. The
// condition rule is the BUILDING's EUI against its reference; the value engine ages an asset
// on the worst ANOMALY attributed to it (asset_intelligence.py passes deviation_pct=
// anom["worst_pct"]). A portfolio under reference everywhere can therefore be "in control"
// and carry money at risk at the same time, which is true but reads as a contradiction
// unless the card says which deviation it used.
test('the value-at-risk card names the deviation it is built on', () => {
  c.setState({
    bldLive: [BLD],
    asLive: [{ asset_id: 'a1', asset_name: 'AHU-1', active: true, health_score: 90, building_id: 'b1' }],
    asLiveWos: [], asLocations: [], asAnoms: [], asSections: [],
    asVar: { value_at_risk: 21000, assets_counted: 53, assets_contributing: 6, assets_not_computable: 0, assets: [] }
  });
  const card = c.renderVals().modMetrics.find((m) => /value at risk/i.test(m.l));
  assert.equal(card.v, '£21k');
  assert.match(card.s, /6 of 53/);
  assert.match(card.s, /anomaly/i, 'says the figure comes from anomalies, not the building EUI');
});

// A building the buildings register does not return is not necessarily nameless: the
// locations register and the sections response both carry the building's name beside its id.
// Falling back to those turns "Building not in your buildings register · 95649fde…" into a
// name, while still saying that its EUI and benchmark are unavailable — which is the part
// that is genuinely missing.
test('a building missing from the register is still named from locations or sections', () => {
  c.setState({
    bldLive: [{ ...BLD, buildingId: 'b1', uuid: null, siteId: null, id: 'b1', name: 'Town Hall' }],
    asLive: [
      { asset_id: 'a1', asset_name: 'AHU-1', active: true, health_score: 80, building_id: 'b1' },
      { asset_id: 'a2', asset_name: 'AHU-2', active: true, health_score: 80, building_id: 'b2', location_id: 'L2' },
      { asset_id: 'a3', asset_name: 'AHU-3', active: true, health_score: 80, building_id: 'b3' }
    ],
    asLiveWos: [],
    asLocations: [{ location_id: 'L2', building_id: 'b2', name: 'Level 1', building: 'Garden Square' }],
    asSections: [{ section_id: 's3', building_id: 'b3', building: 'Sky Plaza', name: 'Plant room',
                   eui_kwh_per_m2: null, reference_eui_kwh_m2: null, measured: false, meters: 0 }],
    asAnoms: []
  });
  const names = c.renderVals().asGroups.map((g) => g.name).sort();
  assert.deepEqual(names, ['Garden Square', 'Sky Plaza', 'Town Hall']);
});

test('a named-but-unregistered building still says its EUI is unavailable, not that it is fine', () => {
  c.setState({
    bldLive: [],
    asLive: [{ asset_id: 'a2', asset_name: 'AHU-2', active: true, health_score: 80, building_id: 'b2', location_id: 'L2' }],
    asLiveWos: [],
    asLocations: [{ location_id: 'L2', building_id: 'b2', name: 'Level 1', building: 'Garden Square' }],
    asSections: [], asAnoms: []
  });
  const g = c.renderVals().asGroups[0];
  assert.equal(g.name, 'Garden Square');
  assert.equal(g.eui, 'No EUI on record');
  assert.match(g.std, /not in the buildings register/i, 'says why there is no benchmark');
});

// Assets whose building cannot be identified from ANY source — the register, the sections
// response or the locations register — are not spread through the tree as if each were a
// building. Either the register is incomplete or the asset read is over-returning, and until
// that is settled these cannot be attributed to the portfolio. They collapse into one row
// that states the count, so the scale of the discrepancy is the first thing visible rather
// than something you infer from eleven near-identical rows.
test('unidentifiable buildings collapse into one row that names the discrepancy', () => {
  c.setState({
    bldLive: [{ ...BLD, buildingId: 'b1', uuid: null, siteId: null, id: 'b1', name: 'Town Hall' }],
    asLive: [
      { asset_id: 'a1', asset_name: 'Known', active: true, health_score: 80, building_id: 'b1' },
      { asset_id: 'a2', asset_name: 'Orphan A', active: true, health_score: 80, building_id: 'ghost-1' },
      { asset_id: 'a3', asset_name: 'Orphan B', active: true, health_score: 80, building_id: 'ghost-1' },
      { asset_id: 'a4', asset_name: 'Orphan C', active: true, health_score: 80, building_id: 'ghost-2' }
    ],
    asLiveWos: [], asLocations: [], asSections: [], asAnoms: []
  });
  const groups = c.renderVals().asGroups;
  assert.equal(groups.length, 2, 'one real building, one collapsed row — not four');
  assert.equal(groups[0].name, 'Town Hall');
  const orphan = groups[1];
  assert.match(orphan.name, /2 buildings not in your buildings register/);
  assert.match(orphan.counts, /3 assets/);
  assert.equal(orphan.unidentified, true);
  // Nothing is hidden: each unidentified building is a tier beneath, with its assets.
  assert.equal(orphan.sections.length, 2);
  assert.deepEqual(orphan.sections.flatMap((x) => x.rows.map((r) => r.name)).sort(), ['Orphan A', 'Orphan B', 'Orphan C']);
});

test('the collapsed row is last, and absent entirely when every building is identified', () => {
  c.setState({
    bldLive: [{ ...BLD, buildingId: 'b1', uuid: null, siteId: null, id: 'b1', name: 'Town Hall' }],
    asLive: [{ asset_id: 'a1', asset_name: 'Known', active: true, health_score: 80, building_id: 'b1' }],
    asLiveWos: [], asLocations: [], asSections: [], asAnoms: []
  });
  const groups = c.renderVals().asGroups;
  assert.equal(groups.length, 1);
  assert.equal(groups.some((g) => g.unidentified), false);
});

// The collapsed row should carry the evidence, not just the complaint: how many buildings
// each route returned for the same caller. That is the number that says whether the register
// is incomplete or the asset read is over-returning, and it belongs where someone will see it.
test('the collapsed row reports what each route returned for this caller', () => {
  c.setState({
    bldLive: [{ ...BLD, buildingId: 'b1', uuid: null, siteId: null, id: 'b1', name: 'Town Hall' }],
    asLive: [
      { asset_id: 'a1', asset_name: 'Known', active: true, health_score: 80, building_id: 'b1' },
      { asset_id: 'a2', asset_name: 'Orphan A', active: true, health_score: 80, building_id: 'ghost-1' },
      { asset_id: 'a3', asset_name: 'Orphan B', active: true, health_score: 80, building_id: 'ghost-2' }
    ],
    asLiveWos: [], asLocations: [], asSections: [], asAnoms: []
  });
  const orphan = c.renderVals().asGroups.find((g) => g.unidentified);
  assert.match(orphan.secLine, /buildings register returned 1/i);
  assert.match(orphan.secLine, /assets name 3/i);
});

// Every asset the register returns must appear somewhere in the tree. A row that is counted
// in the header but rendered nowhere is worse than a missing feature: the page says 68 and
// shows 60, and nothing tells you which eight went.
test('every asset returned is rendered somewhere in the tree', () => {
  const mk = (i, bid, loc) => ({ asset_id: 'a' + i, asset_name: 'Asset ' + i, active: true, health_score: 80,
                                 building_id: bid, location_id: loc });
  c.setState({
    bldLive: [{ ...BLD, buildingId: 'b1', uuid: null, siteId: null, id: 'b1', name: 'Town Hall' }],
    asLive: [
      mk(1, 'b1', 'L1'), mk(2, 'b1', null),
      mk(3, 'ghost-1', 'L9'), mk(4, 'ghost-1', null),
      mk(5, 'ghost-2', null),
      mk(6, null, null)            // no building at all
    ],
    asLiveWos: [], asLocations: [{ location_id: 'L1', building_id: 'b1', name: 'Level 1' }],
    asSections: [{ section_id: 'sec-1', building_id: 'b1', name: 'Plant', eui_kwh_per_m2: 100,
                   reference_eui_kwh_m2: 180, deviation_pct: -44, measured: true, meters: 1 }],
    asAnoms: []
  });
  const vals = c.renderVals();
  const rendered = vals.asGroups.flatMap((g) => g.sections.flatMap((sc) => sc.rows.map((r) => r.name)));
  assert.equal(rendered.length, 6, 'all six render; got ' + rendered.length + ': ' + rendered.join(', '));
  assert.deepEqual(rendered.slice().sort(), ['Asset 1', 'Asset 2', 'Asset 3', 'Asset 4', 'Asset 5', 'Asset 6']);
});

// GET /me returns the caller's own allocation as account.building_ids, and account.buildings
// as [{id, name}] for a restricted caller. That is the authority on what this person may see
// — the buildings register has disagreed with the asset read all along, so it is not.
//
// Presentation, not security: an over-returning API has already put the rows on the wire. The
// count withheld is therefore stated, or a real backend leak goes quiet exactly where someone
// was looking for it.
test('assets outside the caller allocation are withheld, and the count is stated', () => {
  c.setState({
    account: { id: 'u1', all_buildings: false, building_ids: ['b1'], buildings: [{ id: 'b1', name: 'Town Hall' }] },
    bldLive: [{ ...BLD, buildingId: 'b1', uuid: null, siteId: null, id: 'b1', name: 'Town Hall' }],
    asLive: [
      { asset_id: 'a1', asset_name: 'Mine', active: true, health_score: 80, building_id: 'b1' },
      { asset_id: 'a2', asset_name: 'Theirs A', active: true, health_score: 80, building_id: 'ghost-1' },
      { asset_id: 'a3', asset_name: 'Theirs B', active: true, health_score: 80, building_id: 'ghost-2' }
    ],
    asLiveWos: [], asLocations: [], asSections: [], asAnoms: []
  });
  const vals = c.renderVals();
  assert.deepEqual(vals.asGroups.map((g) => g.name), ['Town Hall']);
  assert.equal(vals.asGroups.some((g) => g.unidentified), false);
  assert.match(vals.asWithheldText, /2 assets/);
  assert.match(vals.asWithheldText, /scoping differently from your login/);
  assert.equal(vals.asWithheldShow, 'block');
  assert.match(vals.asSummary, /1 of 1 assets/, 'the header counts what the caller may see');
});

test('an admin with all_buildings is not filtered', () => {
  c.setState({
    account: { id: 'u1', all_buildings: true, building_ids: null, buildings: [] },
    bldLive: [{ ...BLD, buildingId: 'b1', uuid: null, siteId: null, id: 'b1', name: 'Town Hall' }],
    asLive: [
      { asset_id: 'a1', asset_name: 'Mine', active: true, health_score: 80, building_id: 'b1' },
      { asset_id: 'a2', asset_name: 'Other', active: true, health_score: 80, building_id: 'ghost-1' }
    ],
    asLiveWos: [], asLocations: [], asSections: [], asAnoms: []
  });
  const vals = c.renderVals();
  assert.equal(vals.asWithheldShow, 'none');
  assert.equal(vals.asGroups.length, 2);
});

test('an allocation that has not loaded yet filters nothing, rather than emptying the page', () => {
  c.setState({
    account: { id: 'u1' },
    bldLive: [{ ...BLD, buildingId: 'b1', uuid: null, siteId: null, id: 'b1', name: 'Town Hall' }],
    asLive: [{ asset_id: 'a1', asset_name: 'Mine', active: true, health_score: 80, building_id: 'b1' }],
    asLiveWos: [], asLocations: [], asSections: [], asAnoms: []
  });
  assert.equal(c.renderVals().asGroups.length, 1);
  assert.equal(c.renderVals().asWithheldShow, 'none');
});

test('the allocation names a building the register never returned', () => {
  c.setState({
    account: { id: 'u1', all_buildings: false, building_ids: ['b1', 'b2'],
               buildings: [{ id: 'b1', name: 'Town Hall' }, { id: 'b2', name: 'Harbour View' }] },
    bldLive: [{ ...BLD, buildingId: 'b1', uuid: null, siteId: null, id: 'b1', name: 'Town Hall' }],
    asLive: [
      { asset_id: 'a1', asset_name: 'Mine', active: true, health_score: 80, building_id: 'b1' },
      { asset_id: 'a2', asset_name: 'Also mine', active: true, health_score: 80, building_id: 'b2' }
    ],
    asLiveWos: [], asLocations: [], asSections: [], asAnoms: []
  });
  assert.deepEqual(c.renderVals().asGroups.map((g) => g.name).sort(), ['Harbour View', 'Town Hall']);
});

// resetLiveData() clears every register on a company switch but deliberately leaves the
// account alone — the signed-in person does not change when a superadmin views as another
// company. So the account's OWN allocation must not reach the page while viewing as someone
// else: filtering by it would hide the viewed company's assets and keep only ids that
// collide with the viewer's, and naming from it would print the viewer's building names over
// another tenant's rows. That is a cross-tenant leak, whichever direction it runs.
test('viewing as another company ignores the viewer own allocation, for names and for filtering', () => {
  c.setState({
    viewOrgId: 'org-techcorp', viewOrgName: 'TechCorp Facilities LLC',
    account: { id: 'u1', all_buildings: false, building_ids: ['plenum-1'],
               buildings: [{ id: 'plenum-1', name: 'Town Hall' }] },
    bldLive: [],
    // TechCorp's assets. One of them collides with the viewer's own building id.
    asLive: [
      { asset_id: 'a1', asset_name: 'TechCorp asset', active: true, health_score: 80, building_id: 'tc-1' },
      { asset_id: 'a2', asset_name: 'Collides', active: true, health_score: 80, building_id: 'plenum-1' }
    ],
    asLiveWos: [], asLocations: [], asSections: [], asAnoms: []
  });
  const vals = c.renderVals();
  const blob = JSON.stringify(vals.asGroups);
  assert.equal(blob.includes('Town Hall'), false, "the viewer's own building name must not label another tenant's row");
  assert.equal(vals.asWithheldShow, 'none', 'the viewer allocation must not filter the viewed company');
  const rendered = vals.asGroups.flatMap((g) => g.sections.flatMap((sc) => sc.rows.map((r) => r.name))).sort();
  assert.deepEqual(rendered, ['Collides', 'TechCorp asset'], 'both of the viewed company assets still render');
});

// The drawer is a snapshot in state, not a live view: filling it from asIntel at open time
// and firing the read without awaiting bakes "Reading…" into every field permanently.
test('the asset drawer fills in once the intelligence read answers', async () => {
  c.setState({
    account: {}, bldLive: [{ ...BLD, buildingId: 'b1', uuid: null, siteId: null, id: 'b1', name: 'Town Hall' }],
    asLive: [{ asset_id: 'a1', asset_name: 'AHU-1', active: true, health_score: 80, building_id: 'b1' }],
    asLiveWos: [], asLocations: [], asSections: [], asAnoms: [], asOpenB: ['b1'], asOpenS: ['b1|__none']
  });
  // Pretend the read has already answered, which is the state after asCondLoadIntel resolves.
  c.setState({ asIntel: { a1: { asset: { vendor: 'Halden', section: 'Central plant' },
                                value: { value_at_risk: 5678, basis: 'straight line' } } } });
  const row = c.renderVals().asGroups[0].sections.find((x) => x.rows.length).rows[0];
  row.open();
  const chain = c.state.detail.chain;
  const get = (k) => (chain.find((x) => x.a === k) || {}).t;
  assert.equal(get('vendor'), 'Halden');
  assert.equal(get('section'), 'Central plant');
  assert.match(get('value at risk'), /5,678/);
  assert.equal(/Reading…/.test(JSON.stringify(chain.filter((x) => x.a !== 'work history'))), false,
    'nothing but work history is still pending once the read has answered');
});

// iotVals passes deviation: null for its remediate/replace actions, so an unguarded template
// wrote "(null%)" into an email addressed to a vendor.
test('a drafted vendor email never prints a null deviation', async () => {
  c.setState({
    account: {}, bldLive: [{ ...BLD, buildingId: 'b1', uuid: null, siteId: null, id: 'b1', name: 'Town Hall', euiN: 220, benchN: 180 }],
    asLive: [{ asset_id: 'a1', asset_name: 'AHU-1', active: true, health_score: 80, building_id: 'b1' }],
    asLiveWos: [], asLocations: [], asSections: [], asAnoms: [],
    asIntel: { a1: { asset: { vendor: 'Halden' } } }
  });
  await c.asCondAction({ a: { asset_id: 'a1', asset_name: 'AHU-1' }, b: { name: 'Town Hall', euiN: 220, benchN: 180 }, deviation: null }, 'wo');
  const body = c.state.emBody || '';
  assert.equal(body.includes('null'), false, 'no null reaches a message sent to a vendor: ' + body.slice(0, 200));
  assert.match(body, /deviation not on record|Town Hall/);
});

// api/client.js rejects a response whose company scope has changed under it. Catching that
// per-read would turn a disowned response into "unreachable" and write empty arrays over the
// new company's registers — the exact bug resetLiveData() exists to prevent. The loader must
// therefore abandon the whole load rather than soften the rejection.
test('a stale-scope rejection abandons the load instead of blanking the registers', async () => {
  const { isStaleScope } = await import('../src/api/client.js');
  const stale = Object.assign(new Error('scope changed'), { staleScope: true });
  assert.equal(isStaleScope(stale), true, 'the fixture matches what client.js rejects with');

  const kept = [{ section_id: 's1', building_id: 'b1', name: 'Kept' }];
  c.setState({ asSections: kept, asSectionsError: '' });
  const { energyApi } = await import('../src/api/energy.js');
  const realSections = energyApi.sections;
  energyApi.sections = () => Promise.reject(stale);
  try {
    await c.asCondLoad();
  } finally {
    energyApi.sections = realSections;
  }
  assert.deepEqual(c.state.asSections, kept, 'the previous rows are left alone, not blanked');
  assert.equal(c.state.asSectionsError, '', 'and no false "unreachable" is reported');
});

test('an account the backend scopes to nothing sees an empty page, never another tenant\'s buildings', () => {
  c.setState({ asLive: [], asLiveWos: [], asLocations: [], asAnoms: [], asReadings: [] });
  const vals = c.renderVals();
  assert.deepEqual(vals.asGroups, []);
  assert.equal(vals.navSections.find((i) => i.label === 'Assets').show, 'none', 'no badge on an empty register');
  // Scoped to the assets keys on purpose: renderVals() also computes the other modules'
  // view models on every call, and those still carry seed data of their own.
  const keys = Object.keys(vals).filter((k) => /^(as|iot|modMetrics|modFilters)/.test(k));
  const blob = JSON.stringify(keys.map((k) => vals[k]));
  ['Bishopsgate Tower', 'Kingsway House', 'Marina Heights', 'Meridian Quay', 'Northgate Mall', 'Raffles Link', 'Riverside Court', 'AS-1042', 'Apex Mechanical']
    .forEach((n) => assert.equal(blob.includes(n), false, n + ' must not reach a page with no assets in scope'));
});

test('the filter pills narrow the real tree, and a filter that matches nothing says so', () => {
  c.setState({
    bldLive: [BLD],
    asLive: [
      { asset_id: 'a1', asset_name: 'AHU-04', active: true, health_score: 22, building_id: 'b1' },
      { asset_id: 'a2', asset_name: 'Pump-2', active: true, health_score: 90, building_id: 'b1' }
    ],
    asLiveWos: [], asLocations: [], asAnoms: [], asOpenB: ['b1'], asOpenS: ['b1|__none']
  });
  c.setState({ filter: 'Threat' });
  let vals = c.renderVals();
  assert.deepEqual(vals.asGroups[0].sections[0].rows.map((r) => r.name), ['AHU-04']);

  c.setState({ filter: 'Not scored' });
  vals = c.renderVals();
  assert.deepEqual(vals.asGroups, [], 'no group survives a filter nothing matches');
  assert.equal(vals.asEmpty, 'block');
  assert.match(vals.asEmptyText, /Not scored/);
});

test('the ops module renders with nothing loaded — empty, and no seed behind it', () => {
  c.setState({ module: 'ops' });
  const vals = c.renderVals();
  assert.equal(vals.mxLiveOn, false);
  assert.deepEqual(vals.mxGroups, [], 'no decision renders until the backend answers');
  assert.deepEqual(vals.mxPpm, []);
  assert.equal(vals.mxEmptyShow, 'block');
  assert.equal(vals.mxBodyShow, 'none');
  assert.match(vals.mxEmptyNote, /svc-work-order-management/);
  // Every card is a dash, and the "Last run" stamp with them — a fixed "02:14 today" used
  // to sit there whether or not anything had been read.
  vals.mxCards.forEach((card) => assert.equal(card.v, '—', card.l + ' is not asserted'));
  assert.equal(vals.modLastRun, '—');
});

test('once the maintenance reads answer, the page is what they returned', () => {
  c.setState({
    module: 'ops',
    account: { id: 'u1', email: 'ada@example.com', role: 'admin', all_buildings: true },
    mxRaw: {
      fetchedAt: '2026-09-15T02:14:00Z',
      errors: {},
      overview: { ok: true, cards: {
        decisions_owed: { value: 10, blocked: 2, to_raise: 4, deviating: 2, caption: '2 blocked · 4 to raise · 2 deviating' },
        statutory: { value: 3, caption: 'certificate lapsed or inside 30 days' },
        recommendations_unconverted: { value: 7, answerable: true, caption: 'from 12 inspection reports since 2026-03-10' },
        ppm_to_plan: { value: 91.0, done: 134, plan: 148, missed: 7, caption: '134 of 148 visits · 7 missed · 127 reports' }
      }, last_read: { started_at: '2026-09-15T02:14:07+00:00', reports_read: 12 } },
      decisions: { ok: true, count: 2, total: 10, by_state: { Blocked: 2 }, by_source: { Vendors: 2 },
        available: { state: ['Blocked'], source: ['Vendors'] }, group_by: 'state',
        groups: [{ key: 'Blocked', count: 2, blocked: 2, to_raise: 0, deviating: 0, awaiting_approval: 0,
          estimated_cost: 1020, priced: 2, decisions: [
            { work_order: 'WO-4512', state: 'Blocked', source: 'Vendors', asset: 'Boiler-22',
              building: 'Town Hall', vendor: 'Meridian Heating Ltd', estimated_cost: 640,
              trigger: 'Vendor blocked · Gas Safe registration lapsed 31 Mar 2026',
              detail: 'Statutory CP12 due.', due: null,
              statutory_certificate: { name: 'Gas Safe', expiry_date: '2026-03-31', matched_on: 'vendor' } },
            { work_order: 'WO-4519', state: 'Blocked', source: 'Vendors', asset: 'Boiler-14',
              building: 'Meridian Quay', vendor: 'Meridian Heating Ltd', estimated_cost: 380,
              trigger: 'Vendor blocked · same accreditation lapse', detail: 'PPM visit.' }
          ] }],
        decisions: [] },
      intelligence: { ok: true, corpus: { reports: 12, assets: 10, since: '2026-03-10' }, cards: {
        unconverted_recommendations: { answerable: true, count: 7, headline: '7 recommendations never converted to orders — 5 on assets now flagged by energy' },
        corroborated_anomalies: { answerable: true, count: 6, open_anomalies: 8, headline: '6 of 8 open anomalies corroborated by an earlier finding' },
        warranted_findings: { answerable: false, count: null, reason: 'No fitting on record carries a warranty term' },
        poorly_graded: { answerable: true, count: 3, headline: '3 assets graded poor (4 of 5) by inspectors — all 2004–2009' }
      }, unanswerable: ['warranted_findings'] },
      ppm: { ok: true, rule: 'behind plan at 3+ missed visits', window: { year_to_date: true },
        summary: { contracts: 1, done: 12, plan: 18, missed: 4, late: 2, deferred: 2, reports: 10, completion_pct: 66.7 },
        contracts: [{ contract: 'Heating and gas', vendor: 'Meridian Heating Ltd', country_code: 'UK',
          service_scope: 'Boilers, DHW, gas safety', visits_to_plan: { done: 12, plan: 18, plan_is_committed: true },
          missed: 4, late: 2, deferred: 2, reports: 10, reports_to_done: { filed: 10, done: 12 },
          completion_pct: 66.7, next_due: null, state: 'behind plan' }] },
      lastRead: { ok: true, last_read: { started_at: '2026-09-15T02:14:07+00:00', reports_read: 12 } },
      chips: { ok: true, suggestions: [{ question: 'Which decisions are statutory?' }] }
    }
  });
  const vals = c.renderVals();
  assert.equal(vals.mxLiveOn, true);
  assert.equal(vals.mxBodyShow, 'block');

  // The four cards are the backend's values and its own captions.
  assert.equal(vals.mxCards[0].v, '10');
  assert.equal(vals.mxCards[0].s, '2 blocked · 4 to raise · 2 deviating');
  assert.equal(vals.mxCards[3].v, '91%');

  // The grid is the server's grouping, with its own totals.
  assert.equal(vals.mxGroups.length, 1);
  assert.equal(vals.mxGroups[0].name, 'Blocked');
  assert.equal(vals.mxGroups[0].n, '2');
  assert.equal(vals.mxGroups[0].total, '~£1k');
  assert.equal(vals.mxGroups[0].items[0].id, 'WO-4512');
  assert.equal(vals.mxGroups[0].items[0].est, '£640');
  assert.equal(vals.mxGroups[0].items[0].statShow, 'inline', 'the certificate forcing it is named');
  assert.match(vals.mxGroups[0].items[0].statNote, /Gas Safe/);

  // A card the database cannot answer is a dash with a reason, never a zero.
  const warr = vals.mxInsights[2];
  assert.equal(warr.n, '—');
  assert.match(warr.s, /No fitting on record/);
  assert.equal(warr.askShow, 'default', 'and it is not offered as a question');
  assert.equal(vals.mxInsights[1].n, '6 of 8');
  assert.equal(vals.mxInsightNoteShow, 'block');

  // PPM health is one row per contract, with the backend's state.
  assert.equal(vals.mxPpm.length, 1);
  assert.equal(vals.mxPpm[0].done, '12 / 18');
  assert.equal(vals.mxPpm[0].state, 'behind plan');
  assert.equal(vals.mxPpm[0].missed, '4');
  assert.match(vals.mxPpm[0].deferrals, /2 deferred/);
  assert.match(vals.mxPpmSummary, /12 of 18 planned visits done year to date/);

  // The filter row is what the backend says it holds, with counts.
  assert.deepEqual(vals.modFilters.map((f) => f.label), ['All', 'Blocked', 'Vendors']);
  assert.equal(vals.modFilters[1].n, '2');

  // And "Last run" is the recorded read, not a constant.
  assert.match(vals.modLastRun, /02:14|02:14 today|\d\d:\d\d/);
});

test('an admin is told the scope is the company; a user allocated to nothing is told that instead', () => {
  const raw = { fetchedAt: 'x', errors: {}, decisions: { ok: true, count: 0, total: 0, decisions: [], groups: [], by_state: {}, by_source: {}, available: { state: [], source: [] } } };
  c.setState({ module: 'ops', mxRaw: raw,
    account: { id: 'u1', email: 'a@b.c', role: 'admin', all_buildings: true, organization_name: 'Plenum Tech LLC' } });
  assert.match(c.renderVals().mxScope, /Every building in Plenum Tech LLC/);

  c.setState({ account: { id: 'u2', email: 'u@b.c', role: 'user', all_buildings: false, building_ids: [] } });
  const vals = c.renderVals();
  assert.match(vals.mxScope, /allocated to no buildings/);
  // The empty queue must not read as "nothing to do" for someone who may see nothing.
  assert.match(vals.mxDecEmptyNote, /allocated to no buildings/);

  c.setState({ account: { id: 'u3', email: 'v@b.c', role: 'user', all_buildings: false, building_ids: ['b1'] } });
  assert.match(c.renderVals().mxScope, /Your buildings only/);
  assert.match(c.renderVals().mxDecEmptyNote, /No decision is owed/);
});
