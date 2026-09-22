// buildingsCrud — hoisting a building, editing one field by field (PATCH, only what
// changed), and removing one. The real controller in Node, fetch mocked per route so no
// call ever reaches a real host: creating, editing and deleting a building are live writes
// against svc-operations-intelligence, and this stack's backend is production — a test must
// prove the request bodies and the state transitions, never actually perform one.
import { test, beforeEach } from 'node:test';
import assert from 'node:assert/strict';

const mem = {};
let calls;
globalThis.window = {
  location: { origin: 'http://test.local' }, scrollTo: () => {},
  addEventListener: () => {}, removeEventListener: () => {},
  localStorage: { getItem: (k) => (k in mem ? mem[k] : null), setItem: (k, v) => { mem[k] = String(v); }, removeItem: (k) => { delete mem[k]; } }
};
globalThis.WebSocket = class { constructor() { throw new Error('no sockets in tests'); } };

// One handler per test, keyed by METHOD + the path after the origin. Anything not
// registered fails loudly rather than silently reaching the network.
let handlers;
globalThis.fetch = async (url, opts) => {
  const u = new URL(String(url));
  const method = (opts && opts.method) || 'GET';
  calls.push(method + ' ' + u.pathname);
  const h = handlers[method + ' ' + u.pathname] || handlers[u.pathname];
  if (!h) throw new TypeError('Failed to fetch: no handler for ' + method + ' ' + u.pathname);
  const [status, body] = typeof h === 'function' ? h(u, opts) : h;
  return {
    ok: status >= 200 && status < 300, status: status, statusText: String(status),
    text: async () => JSON.stringify(body)
  };
};

const { HoistraLogic } = await import('../src/logic/HoistraLogic.js');
const { shapeLiveBuilding } = await import('../src/logic/buildingsLive.js');
const { USE_TYPES } = await import('../src/logic/buildingsCrud.js');

// A row as buildingsLive.js shapes it from one GET /api/energy/buildings entry — building_id
// present (this is the graph-rooted shape create/edit/delete address), a recorded mix, a
// route, and updated_at for the PATCH stale check.
const ROW = shapeLiveBuilding({
  building_id: 'b0000000-0000-0000-0000-000000000001',
  site_id: 'S-01', code: 'B-01', name: 'Bishopsgate Tower',
  country_code: 'UK', region: 'London', city: 'London', postcode: 'EC2N 4AY',
  site_type: 'Commercial', use_type: 'office',
  use_mix: [{ use: 'office', pct: 100 }],
  floors: 24, gfa_sqm: 40000,
  metering_granularity: 'building-level', metering_route: 'HH data collector · LoA',
  updated_at: '2026-09-08 11:22:19.022251+00'
}, 0);

const BUILDINGS_ENVELOPE = (rows) => [200, { ok: true, count: rows.length, buildings: rows }];

let c;
beforeEach(() => {
  Object.keys(mem).forEach((k) => { delete mem[k]; });
  calls = [];
  handlers = { '/backend/ops-intelligence/api/energy/graph/shape': () => [404, { error: 'not found' }] };
  c = new HoistraLogic();
  c.setState({ signedIn: true, view: 'buildings', role: 'user' });
});
const cleanup = () => { clearInterval(c._orchTick); clearTimeout(c._tt); clearTimeout(c._bldRetry); clearTimeout(c._bcCodeTimer); };

// ── who may hoist / edit / remove ──

test('admin and superadmin can hoist, edit and remove; a plain user and a signed-out session cannot', () => {
  c.setState({ role: 'user' });
  assert.equal(c.renderVals().bcCanHoist, false);
  c.setState({ role: 'admin' });
  assert.equal(c.renderVals().bcCanHoist, true);
  c.setState({ role: 'superadmin' });
  assert.equal(c.renderVals().bcCanHoist, true);
  c.setState({ signedIn: false });
  assert.equal(c.renderVals().bcCanHoist, false);
  cleanup();
});

test('an admin previewing "User view" still keeps Hoist — the toggle relabels the screen, not the account', () => {
  // auth.js's toggle only ever sets s.role to 'user' for a real admin account; it never
  // touches s.account. bcCanHoist must follow the account, not the preview label.
  c.setState({ account: { role: 'admin' }, role: 'user' });
  assert.equal(c.renderVals().bcCanHoist, true);
  c.setState({ account: { role: 'superadmin' }, role: 'user' });
  assert.equal(c.renderVals().bcCanHoist, true);
  cleanup();
});

test('a real user-role account has no Hoist even with an account object present', () => {
  c.setState({ account: { role: 'user' }, role: 'user' });
  assert.equal(c.renderVals().bcCanHoist, false);
  cleanup();
});

// ── opening the form ──

test('Hoist a building opens a blank create form', () => {
  c.setState({ bcOpen: true, bcMode: 'edit', bcForm: { site_name: 'stale' } }); // as if a prior edit was left open
  c.renderVals().addBuilding();
  const v = c.renderVals();
  assert.equal(v.bcMode, 'create');
  assert.equal(v.bcTitle, 'Hoist a building');
  assert.equal(v.bcForm.site_name, '');
  assert.equal(v.bcForm.country_code, 'UK');
  assert.equal(v.bcSubmitLabel, 'Write the record');
  cleanup();
});

// ── the building code fills in live, from country/region/use ──

const wait = (ms) => new Promise((r) => setTimeout(r, ms));

test('the building code fills in on open, and again as country/region/use change', async () => {
  let seen = null;
  handlers['/backend/ops-intelligence/api/energy/buildings/next-code'] = (u) => {
    seen = Object.fromEntries(u.searchParams);
    return [200, { ok: true, building_code: 'TC-FMGMT-UK-01-LON-COM' }];
  };
  c.renderVals().addBuilding(); // BLANK's own defaults: country_code UK, use_type Commercial
  await wait(320);
  assert.equal(c.state.bcForm.building_code, 'TC-FMGMT-UK-01-LON-COM', 'previewed as soon as the form opens');
  assert.equal(seen.country_code, 'UK');
  assert.equal(seen.use_type, 'Commercial');

  handlers['/backend/ops-intelligence/api/energy/buildings/next-code'] = () => [200, { ok: true, building_code: 'TC-FMGMT-AE-01-DUB-RET' }];
  c.bcSet('country_code', 'AE');
  c.bcSet('state', 'Dubai');
  c.bcSet('use_type', 'Retail');
  await wait(320); // one debounced call for the three, not three
  assert.equal(c.state.bcForm.building_code, 'TC-FMGMT-AE-01-DUB-RET');
  cleanup();
});

test('typing into the code field by hand stops it from being overwritten', async () => {
  handlers['/backend/ops-intelligence/api/energy/buildings/next-code'] = () => [200, { ok: true, building_code: 'TC-FMGMT-UK-01-LON-COM' }];
  c.renderVals().addBuilding();
  await wait(320);
  c.bcSet('building_code', 'MY-OWN-CODE');
  c.bcSet('state', 'Manchester'); // would otherwise preview a different code
  await wait(320);
  assert.equal(c.state.bcForm.building_code, 'MY-OWN-CODE', 'a typed code is never overwritten by a later preview');
  cleanup();
});

test('editing an existing building never previews a new code for it', async () => {
  let called = false;
  handlers['/backend/ops-intelligence/api/energy/buildings/next-code'] = () => { called = true; return [200, { ok: true, building_code: 'X' }]; };
  c.bcOpenEdit(ROW);
  c.bcSet('state', 'Manchester');
  await wait(320);
  assert.equal(called, false, 'no preview call at all while editing');
  assert.equal(c.state.bcForm.building_code, ROW.code, 'the stored code is left exactly as it was');
  cleanup();
});

// ── the dock card: hoisting is a three-step flow in the orchestrator ──

test('Hoist a building opens in the orchestrator dock as step 1 of 3 and records the task', () => {
  c.renderVals().addBuilding();
  const v = c.renderVals();
  assert.equal(c.state.orchOpen, true, 'the dock opens');
  assert.equal(c.state.flow, 'declare', 'the dock shows the hoist card');
  assert.equal(v.bcOpen, true);
  assert.equal(v.bcStep, 0);
  assert.equal(v.bcStepLabel, 'Step 1 of 3 · building record');
  assert.equal(v.bcTitle, 'Hoist a building');
  assert.match(c.state.orchTask.steps[0].t, /hoist building/, 'the trace plays for this task');
  assert.match(c.state.sessions[0].title, /^Hoist building/, 'recorded as a task session');
  cleanup();
});

test('Cancel closes the card and clears the dock flow', () => {
  c.renderVals().addBuilding();
  c.renderVals().bcClose();
  assert.equal(c.renderVals().bcOpen, false);
  assert.equal(c.state.flow, null);
  cleanup();
});

test('a successful write moves to step 2 with the code the service allocated, then Next steps reaches the documents step', async () => {
  const created = Object.assign({}, ROW, { building_id: 'b-new', name: 'Marina Mall' });
  handlers['POST /backend/ops-intelligence/api/energy/buildings'] = () => [201, { building_id: 'b-new', building_code: 'B-02', stored_as: 'Retail', warnings: ['Stored as Retail — the register has no Mall category.'], building: created }];
  handlers['GET /backend/ops-intelligence/api/energy/buildings'] = () => BUILDINGS_ENVELOPE([created]);

  c.renderVals().addBuilding();
  c.bcSet('site_name', 'Marina Mall');
  c.bcSet('use_type', 'Mall');
  await c.bcSubmit();
  let v = c.renderVals();
  assert.equal(v.bcOpen, true, 'the card stays up — the write is the middle of the flow, not its end');
  assert.equal(v.bcStep, 1);
  assert.equal(v.bcStepLabel, 'Step 2 of 3 · schema written');
  assert.equal(v.bcResultName, 'Marina Mall');
  assert.equal(v.bcResultCode, 'B-02');
  assert.deepEqual(v.bcResultWarnings, ['Stored as Retail — the register has no Mall category.']);

  v.bcNext();
  v = c.renderVals();
  assert.equal(v.bcStep, 2);
  assert.equal(v.bcStepLabel, 'Step 3 of 3 · documents');
  cleanup();
});

test('Ingest documents now hands the new building to the ingest flow', async () => {
  const created = Object.assign({}, ROW, { building_id: 'b-new', name: 'Marina Mall' });
  handlers['POST /backend/ops-intelligence/api/energy/buildings'] = () => [201, { building_id: 'b-new', building_code: 'B-02', warnings: [], building: created }];
  handlers['GET /backend/ops-intelligence/api/energy/buildings'] = () => BUILDINGS_ENVELOPE([created]);
  c.renderVals().addBuilding();
  c.bcSet('site_name', 'Marina Mall');
  await c.bcSubmit();
  c.renderVals().bcNext();
  c.renderVals().bcIngestNow();
  assert.equal(c.state.flow, 'ingest');
  assert.equal(c.state.declFor, 'Marina Mall', 'the ingest card opens on the building just hoisted');
  assert.equal(c.renderVals().bcOpen, false);
  cleanup();
});

test('Do it later closes the card and leaves the keyed-as line in the dock', async () => {
  const created = Object.assign({}, ROW, { building_id: 'b-new', name: 'Marina Mall' });
  handlers['POST /backend/ops-intelligence/api/energy/buildings'] = () => [201, { building_id: 'b-new', building_code: 'B-02', warnings: [], building: created }];
  handlers['GET /backend/ops-intelligence/api/energy/buildings'] = () => BUILDINGS_ENVELOPE([created]);
  c.renderVals().addBuilding();
  c.bcSet('site_name', 'Marina Mall');
  await c.bcSubmit();
  c.renderVals().bcNext();
  c.renderVals().bcLater();
  assert.equal(c.state.flow, null);
  assert.equal(c.renderVals().bcOpen, false);
  assert.match(c.state.flowDone, /Marina Mall is hoisted and keyed as B-02/);
  assert.match(c.state.flowDone, /Hoist Score stays at 0%/);
  cleanup();
});

test('Edit opens in the dock as a single-step card and closes on save', async () => {
  handlers['PATCH /backend/ops-intelligence/api/energy/buildings/b0000000-0000-0000-0000-000000000001'] = () => [200, { changed: ['floors'], relocated: false, warnings: [], building: Object.assign({}, ROW, { floors: 30 }) }];
  handlers['GET /backend/ops-intelligence/api/energy/buildings'] = () => BUILDINGS_ENVELOPE([ROW]);
  c.bcOpenEdit(ROW);
  let v = c.renderVals();
  assert.equal(c.state.orchOpen, true);
  assert.equal(c.state.flow, 'declare');
  assert.equal(v.bcTitle, 'Edit building');
  assert.doesNotMatch(v.bcStepLabel, /^Step/, 'an edit has no steps — one write and done');
  assert.match(c.state.orchTask.steps[0].t, /edit building/);
  c.bcSet('floors', '30');
  await c.bcSubmit();
  assert.equal(c.renderVals().bcOpen, false);
  assert.equal(c.state.flow, null);
  cleanup();
});

test('routing the "Hoist building" action through runAction reopens the hoist card rather than only playing the trace', () => {
  c.runAction('Hoist building', 'Buildings');
  const v = c.renderVals();
  assert.equal(v.bcOpen, true);
  assert.equal(v.bcMode, 'create');
  assert.equal(c.state.flow, 'declare');
  cleanup();
});

test('Edit prefills every field from the row, including the primary-use enum member rather than the resolved category', () => {
  c.bcOpenEdit(ROW);
  const v = c.renderVals();
  assert.equal(v.bcMode, 'edit');
  assert.equal(v.bcTitle, 'Edit building');
  assert.equal(v.bcForm.site_name, 'Bishopsgate Tower');
  assert.equal(v.bcForm.country_code, 'UK');
  assert.equal(v.bcForm.state, 'London');
  assert.equal(v.bcForm.postcode, 'EC2N 4AY');
  // site_type ("Commercial"), not the resolved use_type ("office") — editing off the resolved
  // category would silently rewrite the building's use on save.
  assert.equal(v.bcForm.use_type, 'Commercial');
  assert.equal(v.bcForm.floors, '24');
  assert.equal(v.bcForm.gfa_sqm, '40000');
  assert.equal(v.bcForm.metering_granularity, 'building-level');
  assert.equal(v.bcForm.metering_route, 'HH data collector · LoA');
  assert.equal(v.bcForm.building_code, 'B-01');
  assert.equal(v.bcForm.site_id, 'S-01');
  assert.deepEqual(v.bcMix.map((m) => [m.use, m.pct]), [['office', 100]]);
  assert.equal(v.bcSubmitLabel, 'Save changes');
  cleanup();
});

test('a row with no building_id cannot be edited here, and nothing opens', () => {
  const oldStyle = Object.assign({}, ROW, { buildingId: null });
  c.bcOpenEdit(oldStyle);
  assert.equal(c.renderVals().bcOpen, false);
  assert.match(c.state.toast, /cannot be edited here yet/);
  cleanup();
});

test('the enum offered includes Laboratory, matching the database\'s primary_use enum', () => {
  assert.ok(USE_TYPES.includes('Laboratory'));
  assert.ok(USE_TYPES.includes('Mall'), 'Mall stays offered — the service maps it to Retail and says so');
});

// ── create ──

test('creating sends the form as POST, refreshes the table on success, and never sends a stale org id when none is configured', async () => {
  const created = Object.assign({}, ROW, { building_id: 'b-new' });
  let sentBody = null;
  handlers['POST /backend/ops-intelligence/api/energy/buildings'] = (u, opts) => { sentBody = JSON.parse(opts.body); return [201, { building_id: 'b-new', building_code: 'B-02', stored_as: 'Commercial', warnings: [], building: created }]; };
  handlers['GET /backend/ops-intelligence/api/energy/buildings'] = () => BUILDINGS_ENVELOPE([created]);

  c.renderVals().addBuilding();
  c.bcSet('site_name', 'Marina Mall');
  c.bcSet('country_code', 'AE');
  c.bcSet('state', 'Dubai');
  c.bcSet('floors', '6');
  await c.bcSubmit();

  assert.equal(sentBody.site_name, 'Marina Mall');
  assert.equal(sentBody.country_code, 'AE');
  assert.equal(sentBody.state, 'Dubai');
  assert.equal(sentBody.floors, 6);
  assert.equal(sentBody.source, 'hoistra-ui');
  assert.ok(!('organization_id' in sentBody), 'no tenant configured — nothing sent');
  assert.equal(c.state.bcStep, 1, 'the card moves on to "schema written" rather than closing');
  assert.equal(c.state.bldLive.length, 1, 'the table refreshed from the response');
  assert.match(c.state.toast, /Hoisted Marina Mall as B-02/);
  cleanup();
});

test('a rejected create keeps the form open with field-keyed errors, and a 409 says the code is taken', async () => {
  handlers['POST /backend/ops-intelligence/api/energy/buildings'] = () => [400, { errors: { country_code: 'Required. One of AE, SG, UK, US.', use_mix: 'Percentages must sum to 100 — these sum to 90.' } }];
  c.renderVals().addBuilding();
  c.bcSet('site_name', 'X');
  await c.bcSubmit();
  let v = c.renderVals();
  assert.equal(v.bcOpen, true, 'a rejected create stays open — nothing to refresh from');
  assert.equal(v.bcStep, 0, 'and stays on the record step');
  assert.equal(v.bcErr('country_code'), 'Required. One of AE, SG, UK, US.');
  assert.equal(v.bcErr('use_mix'), 'Percentages must sum to 100 — these sum to 90.');

  handlers['POST /backend/ops-intelligence/api/energy/buildings'] = () => [409, { errors: { building_code: 'B-01 already exists.' }, conflict_building_id: 'b0000000-…-0001' }];
  c.bcSet('building_code', 'B-01');
  await c.bcSubmit();
  v = c.renderVals();
  assert.equal(v.bcErr('building_code'), 'B-01 already exists.');
  assert.match(c.state.toast, /already in use — nothing was overwritten/);
  cleanup();
});

// ── edit (PATCH) ──

test('editing sends only the changed fields — an untouched form has nothing to send', async () => {
  c.bcOpenEdit(ROW);
  const before = calls.length;
  const sent = await c.bcSubmit();
  assert.equal(sent, false);
  assert.equal(calls.length, before, 'nothing was sent over the network');
  assert.match(c.state.bcTopError, /Nothing to change/);
  cleanup();
});

test('changing one field sends only that field, plus expected_updated_at, as PATCH', async () => {
  let sentBody = null, sentId = null;
  handlers['PATCH /backend/ops-intelligence/api/energy/buildings/b0000000-0000-0000-0000-000000000001'] = (u, opts) => {
    sentId = u.pathname; sentBody = JSON.parse(opts.body);
    return [200, { changed: ['floors'], relocated: false, warnings: [], building: Object.assign({}, ROW, { floors: 30 }) }];
  };
  handlers['GET /backend/ops-intelligence/api/energy/buildings'] = () => BUILDINGS_ENVELOPE([Object.assign({}, ROW, { floors: 30 })]);

  c.bcOpenEdit(ROW);
  c.bcSet('floors', '30');
  await c.bcSubmit();

  assert.match(sentId, /b0000000-0000-0000-0000-000000000001$/);
  assert.deepEqual(sentBody, { floors: 30, expected_updated_at: '2026-09-08 11:22:19.022251+00' });
  assert.equal(c.state.bcOpen, false);
  assert.match(c.state.toast, /Saved Bishopsgate Tower — floors changed/);
  cleanup();
});

test('clearing an optional field sends it as an empty string — a clear, not an omission', async () => {
  let sentBody = null;
  handlers['PATCH /backend/ops-intelligence/api/energy/buildings/b0000000-0000-0000-0000-000000000001'] = (u, opts) => {
    sentBody = JSON.parse(opts.body);
    return [200, { changed: ['postcode'], relocated: false, warnings: [], building: ROW }];
  };
  handlers['GET /backend/ops-intelligence/api/energy/buildings'] = () => BUILDINGS_ENVELOPE([ROW]);
  c.bcOpenEdit(ROW);
  c.bcSet('postcode', '');
  await c.bcSubmit();
  assert.equal(sentBody.postcode, '');
  assert.ok(!('floors' in sentBody), 'an untouched field is not sent at all');
  cleanup();
});

test('changing the primary use sends use_type and use_mix together', async () => {
  let sentBody = null;
  handlers['PATCH /backend/ops-intelligence/api/energy/buildings/b0000000-0000-0000-0000-000000000001'] = (u, opts) => {
    sentBody = JSON.parse(opts.body);
    return [200, { changed: ['use_type', 'use_mix'], relocated: false, warnings: [], building: ROW }];
  };
  handlers['GET /backend/ops-intelligence/api/energy/buildings'] = () => BUILDINGS_ENVELOPE([ROW]);
  c.bcOpenEdit(ROW);
  c.bcSet('use_type', 'Retail');
  await c.bcSubmit();
  assert.equal(sentBody.use_type, 'Retail');
  assert.deepEqual(sentBody.use_mix, [{ use: 'office', pct: 100 }]);
  cleanup();
});

test('changing the country sends country_code, and a relocation is reported', async () => {
  let sentBody = null;
  handlers['PATCH /backend/ops-intelligence/api/energy/buildings/b0000000-0000-0000-0000-000000000001'] = (u, opts) => {
    sentBody = JSON.parse(opts.body);
    return [200, { changed: ['country_code'], relocated: true, warnings: [], building: ROW }];
  };
  handlers['GET /backend/ops-intelligence/api/energy/buildings'] = () => BUILDINGS_ENVELOPE([ROW]);
  c.bcOpenEdit(ROW);
  c.bcSet('country_code', 'AE');
  await c.bcSubmit();
  assert.equal(sentBody.country_code, 'AE');
  assert.match(c.state.toast, /relocated to its new market/);
  cleanup();
});

test('a stale edit is refused with 409 and current_updated_at, and the target carries the fresh stamp for the retry', async () => {
  let firstBody = null, secondBody = null;
  let attempt = 0;
  handlers['PATCH /backend/ops-intelligence/api/energy/buildings/b0000000-0000-0000-0000-000000000001'] = (u, opts) => {
    attempt += 1;
    if (attempt === 1) { firstBody = JSON.parse(opts.body); return [409, { errors: { _: 'This building changed since you opened it.' }, current_updated_at: '2026-09-08 12:00:00.000000+00' }]; }
    secondBody = JSON.parse(opts.body);
    return [200, { changed: ['floors'], relocated: false, warnings: [], building: ROW }];
  };
  handlers['GET /backend/ops-intelligence/api/energy/buildings'] = () => BUILDINGS_ENVELOPE([ROW]);

  c.bcOpenEdit(ROW);
  c.bcSet('floors', '30');
  await c.bcSubmit();
  assert.equal(firstBody.expected_updated_at, '2026-09-08 11:22:19.022251+00');
  assert.match(c.state.bcTopError, /changed since you opened it/);
  assert.equal(c.state.bcOpen, true, 'stays open — the edit was not applied');
  assert.equal(c.state.bcTarget.updatedAt, '2026-09-08 12:00:00.000000+00', 're-read from the 409 body');

  await c.bcSubmit();
  assert.equal(secondBody.expected_updated_at, '2026-09-08 12:00:00.000000+00', 'the retry quotes the fresh stamp');
  assert.equal(c.state.bcOpen, false);
  cleanup();
});

test('a field the service will not let this route touch comes back "Not editable here" and renders on no input, so it surfaces as the banner', async () => {
  handlers['PATCH /backend/ops-intelligence/api/energy/buildings/b0000000-0000-0000-0000-000000000001'] = () => [400, { errors: { hoist_score: 'Not editable here.' } }];
  c.bcOpenEdit(ROW);
  c.bcSet('floors', '30');
  await c.bcSubmit();
  const v = c.renderVals();
  assert.equal(v.bcErr('hoist_score'), 'Not editable here.', 'still readable by field key, even with no matching input');
  cleanup();
});

// ── delete stays covered too, since it is a live write now ──

test('delete calls DELETE without confirm first, reports what is attached, then only deletes on confirm', async () => {
  let calledConfirm = [];
  handlers['DELETE /backend/ops-intelligence/api/energy/buildings/b0000000-0000-0000-0000-000000000001'] = (u) => {
    const confirm = u.searchParams.get('confirm');
    calledConfirm.push(confirm);
    if (confirm !== 'true') return [200, { dry_run: true, name: 'Bishopsgate Tower', building_code: 'B-01', attached: { assets: 1, documents: 1 }, attached_total: 2 }];
    return [200, { message: 'Deleted Bishopsgate Tower. 2 records were unlinked and kept.' }];
  };
  handlers['GET /backend/ops-intelligence/api/energy/buildings'] = () => BUILDINGS_ENVELOPE([]);

  await c.bcAskDelete(ROW);
  let v = c.renderVals();
  assert.equal(v.bcDelShow, 'flex');
  assert.match(v.bcDelAttachedText, /1 asset and 1 document will be unlinked and kept/);
  assert.deepEqual(calledConfirm, ['false']);

  await c.bcConfirmDelete();
  assert.deepEqual(calledConfirm, ['false', 'true']);
  assert.equal(c.state.bcDel, null);
  assert.match(c.state.toast, /Deleted Bishopsgate Tower/);
  cleanup();
});

// ── hoisting from the conversation, and the assignment that follows ──
//
// The hoist form was reachable from a button on the Buildings page and from a recorded
// task's chain. It is an instruction to the platform, so it is now also reachable by
// saying so in the Orchestrator — and the card renders in the transcript there rather
// than in a dock laid over the conversation that asked for it.
//
// The phrase is matched on the WHOLE message. "hoist a building in Dubai with 24 floors"
// is an instruction the form cannot take — it carries values no field was told about — so
// it goes to the orchestrator, which can ask about them.

test('"hoist a building" opens the form; a question that merely contains the words does not', async () => {
  const { bcIsHoistRequest } = await import('../src/logic/buildingsCrud.js');
  assert.ok(bcIsHoistRequest('hoist a building'));
  assert.ok(bcIsHoistRequest('Hoist Building'));
  assert.ok(bcIsHoistRequest('  add a building  '));
  assert.ok(bcIsHoistRequest('new building'));
  assert.ok(!bcIsHoistRequest('which building has the worst hoist score?'));
  assert.ok(!bcIsHoistRequest('hoist a building in Dubai with 24 floors'), 'a qualified instruction is a question');
  assert.ok(!bcIsHoistRequest(''), 'an empty message opens nothing');
  cleanup();
});

test('hoisting from the chat opens the card in the transcript and leaves the dock shut', async () => {
  handlers['GET /backend/udr/api/udr/sites'] = [200, { sites: [] }];
  handlers['GET /backend/deep-agents/api/tools'] = [200, { tools: [] }];
  c.setState({ view: 'chat', role: 'admin', account: { id: 'u1', role: 'admin' } });
  await c.ccAsk('hoist a building');
  assert.ok(c.state.bcOpen, 'the form is open');
  assert.equal(c.state.orchOpen, false, 'the dock is not laid over the conversation');
  assert.equal(c.state.view, 'chat', 'and the conversation is still the page');
  assert.equal(c.state.bcStep, 0, 'at step 1 of 3');
  assert.equal(c.state.bcMode, 'create');
  cleanup();
});

test('hoisting from a page keeps its dock, exactly as before', () => {
  handlers['GET /backend/udr/api/udr/sites'] = [200, { sites: [] }];
  c.setState({ view: 'buildings', role: 'admin', account: { id: 'u1', role: 'admin' } });
  c.bcOpenForm();
  assert.ok(c.state.bcOpen);
  assert.equal(c.state.orchOpen, true, 'the dock still opens where there is no transcript');
  cleanup();
});

// building_ids is a FULL replacement server side (adminApi.patchUser). Sending only the new
// building would silently strip every building the person already held — the bug this test
// exists to catch if the append is ever simplified away.
test('assigning the new building appends it to the user\'s allocation and never replaces it', async () => {
  let sent = null;
  handlers['GET /backend/ops-intelligence/api/admin/users'] = [200, { users: [
    { id: 'u-7', full_name: 'Marcus Hale', email: 'm@h.c', buildings: [{ id: 'b-old-1' }, { id: 'b-old-2' }] }
  ] }];
  handlers['PATCH /backend/ops-intelligence/api/admin/users/u-7'] = (u, opts) => {
    sent = JSON.parse(opts.body);
    return [200, { ok: true, user_id: 'u-7', changed: ['building_ids'] }];
  };
  c.setState({ bcResult: { buildingId: 'b-new', name: 'Bishopsgate Tower', code: 'B-01' }, bcStep: 2 });
  await c.bcUsersLoad();
  c.bcSetAssignUser('u-7');
  await c.bcAssignSubmit();
  assert.deepEqual(sent.building_ids, ['b-old-1', 'b-old-2', 'b-new'],
    'the two buildings they already held survive the assignment');
  assert.equal(c.state.bcAssignedTo, 'Marcus Hale');
  assert.match(c.state.toast, /Bishopsgate Tower is now assigned to Marcus Hale/);
  cleanup();
});

test('assigning a building the user already holds does not send it twice', async () => {
  let sent = null;
  handlers['GET /backend/ops-intelligence/api/admin/users'] = [200, { users: [
    { id: 'u-7', full_name: 'Marcus Hale', email: 'm@h.c', buildings: [{ id: 'b-new' }] }
  ] }];
  handlers['PATCH /backend/ops-intelligence/api/admin/users/u-7'] = (u, opts) => {
    sent = JSON.parse(opts.body);
    return [200, { ok: true }];
  };
  c.setState({ bcResult: { buildingId: 'b-new', name: 'Bishopsgate Tower' }, bcStep: 2 });
  await c.bcUsersLoad();
  c.bcSetAssignUser('u-7');
  await c.bcAssignSubmit();
  assert.deepEqual(sent.building_ids, ['b-new']);
  cleanup();
});

test('a refused assignment says whose it was and leaves the step usable', async () => {
  handlers['GET /backend/ops-intelligence/api/admin/users'] = [200, { users: [
    { id: 'u-7', full_name: 'Marcus Hale', email: 'm@h.c', buildings: [] }
  ] }];
  handlers['PATCH /backend/ops-intelligence/api/admin/users/u-7'] = [403, { detail: { ok: false, error: 'wrong_organization' } }];
  c.setState({ bcResult: { buildingId: 'b-new', name: 'Bishopsgate Tower' }, bcStep: 2 });
  await c.bcUsersLoad();
  c.bcSetAssignUser('u-7');
  await c.bcAssignSubmit();
  assert.match(c.state.toast, /Could not assign Bishopsgate Tower to Marcus Hale/);
  assert.equal(c.state.bcAssigning, false, 'the button is usable again');
  assert.equal(c.state.bcAssignedTo, '', 'and nothing claims it succeeded');
  cleanup();
});

test('the step-3 picker lists the company\'s people and is entirely optional', async () => {
  handlers['GET /backend/ops-intelligence/api/admin/users'] = [200, { users: [
    { id: 'u-7', full_name: 'Marcus Hale', email: 'm@h.c', buildings: [] },
    { id: 'u-8', full_name: '', email: 'dana@h.c', buildings: [] }
  ] }];
  c.setState({ bcOpen: true, bcResult: { buildingId: 'b-new', name: 'Bishopsgate Tower', code: 'B-01' }, bcStep: 1 });
  c.bcNext();
  await new Promise((r) => setTimeout(r, 20));
  assert.equal(c.state.bcStep, 2, 'step 3');
  const v = c.renderVals();
  assert.ok(v.bcStep3, 'the third step is the one showing');
  assert.equal((c.state.bcUsers || []).length, 2);
  // Both exits work without anybody having been picked.
  c.bcLater();
  assert.equal(c.state.bcOpen, false);
  assert.match(c.state.flowDone, /is hoisted and keyed as B-01/);
  cleanup();
});

// The typed phrase must not hand a form to an account whose screens deliberately omit it.
// HOIST_ROLES is an affordance, not a permission — the service authorises neither — but a
// chat that opens a flow the Buildings page hides from the same account is incoherent, and
// the person would be filling in a form on the strength of nothing.
test('a plain user typing "hoist a building" is told who can, and gets no form', async () => {
  handlers['GET /backend/deep-agents/api/tools'] = [200, { tools: [] }];
  c.setState({ view: 'chat', role: 'user', account: { id: 'u1', role: 'user', status: 'active' } });
  await c.ccAsk('hoist a building');
  assert.ok(!c.state.bcOpen, 'no form opened');
  const turn = (c.state.ccChat || []).filter((m) => m.role === 'bot').pop();
  assert.match(turn.text, /administrator/i, 'and it says who can do it');
  cleanup();
});

test('an admin previewing User view can still hoist from the chat — the account decides, not the label', async () => {
  handlers['GET /backend/udr/api/udr/sites'] = [200, { sites: [] }];
  handlers['GET /backend/deep-agents/api/tools'] = [200, { tools: [] }];
  c.setState({ view: 'chat', role: 'user', account: { id: 'u1', role: 'admin', status: 'active' } });
  await c.ccAsk('hoist a building');
  assert.ok(c.state.bcOpen, 'the form opens for a real admin account');
  cleanup();
});

// ── the step after a hoist: ingesting the documents, in the conversation ──
//
// Step 3 offers "Ingest documents now", which arms the ingest flow (flow: 'ingest') and
// closes the hoist card. The dock renders that flow; the Orchestrator did not, so on the
// chat page the button put the reader in front of nothing at all — the card vanished and
// no ingest panel replaced it.
test('ingest-now from a hoist arms the ingest flow and names the new building', () => {
  handlers['GET /backend/udr/api/udr/sites'] = [200, { sites: [] }];
  c.setState({
    view: 'chat', role: 'admin', account: { id: 'u1', role: 'admin' },
    bcOpen: true, bcStep: 2,
    bcResult: { buildingId: 'b-new', name: 'Orchestrator Test Tower', code: 'B-42' }
  });
  c.bcIngestNow();
  const v = c.renderVals();
  assert.equal(c.state.flow, 'ingest', 'the ingest flow is armed');
  assert.equal(c.state.bcOpen, false, 'and the hoist card steps aside');
  assert.ok(v.fIngest, 'so the conversation has an ingest panel to render');
  assert.equal(c.state.declFor, 'Orchestrator Test Tower', 'filed against what was just hoisted');
  cleanup();
});

test('"Do it later" closes the hoist without arming anything, and says where it left off', () => {
  c.setState({
    view: 'chat', bcOpen: true, bcStep: 2,
    bcResult: { buildingId: 'b-new', name: 'Orchestrator Test Tower', code: 'B-42' }
  });
  c.bcLater();
  assert.equal(c.state.flow, null);
  assert.equal(c.state.bcOpen, false);
  assert.match(c.state.flowDone, /hoisted and keyed as B-42/);
  cleanup();
});
