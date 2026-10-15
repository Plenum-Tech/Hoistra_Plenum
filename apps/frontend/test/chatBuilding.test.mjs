// Filing an attachment against a building, from the chat composer.
//
// `run-stateful-with-files` has always taken a building_id, and the backend gates its whole
// validation half on it:
//
//     if building:
//         # each file is checked against the building that was selected first, and a file
//         # that does not clearly belong there is HELD: registered, indexed, not bound,
//         # and put to the uploader as a question
//
// The chat only ever sent `declForId`, which one path sets — "hoist a building → ingest now".
// Attach a PDF from the composer and no building went with it, so the file was indexed, no
// validation ran, nothing reached the audit trail, and the contract hung off no building.
// That is why the Audit trail read empty and why an ingested contract sits attached to none
// of the company's 623 buildings.
//
// The control appears only when files are staged: a question with no attachment is not a
// filing, and a picker on every turn would be noise.
import { test, beforeEach } from 'node:test';
import assert from 'node:assert/strict';

const mem = {};
globalThis.window = {
  location: { origin: 'http://test.local' }, scrollTo: () => {},
  addEventListener: () => {}, removeEventListener: () => {},
  localStorage: {
    getItem: (k) => (k in mem ? mem[k] : null),
    setItem: (k, v) => { mem[k] = String(v); },
    removeItem: (k) => { delete mem[k]; }
  }
};
globalThis.WebSocket = class { constructor() { throw new Error('no sockets in tests'); } };

let sent;
globalThis.fetch = async (url, opts) => {
  const u = new URL(String(url));
  sent.push({ path: u.pathname, form: opts && opts.body });
  throw new TypeError('Failed to fetch');   // the turn's outcome is not what these test
};

const { HoistraLogic } = await import('../src/logic/HoistraLogic.js');

const B1 = 'b0000000-0000-0000-0000-000000000001';
const B2 = 'b0000000-0000-0000-0000-000000000002';
const BUILDINGS = [
  { id: B1, name: 'Bishopsgate Tower', building_code: 'B-01' },
  { id: B2, name: 'MixedUse 004', building_code: 'MU-004' },
  { id: 'b0000000-0000-0000-0000-000000000003', name: 'MixedUse 004', building_code: 'MU-009' }
];

let c;
beforeEach(() => {
  sent = [];
  c = new HoistraLogic();
  c.setState({
    signedIn: true, view: 'chat',
    account: { id: 'u1', email: 'pm@test.local', role: 'admin', buildings: BUILDINGS }
  });
});
const cleanup = () => { clearTimeout(c._tt); clearInterval(c._ccTick); };
const v = () => c.renderVals();
// Real File objects: ccFiles holds what the <input type="file"> handed over, and FormData
// refuses anything else — a plain stand-in would make the upload silently not happen.
const stage = (...names) => c.setState({
  ccFiles: names.map((n) => new File(['%PDF-1.4 test'], n, { type: 'application/pdf' }))
});

// ─────────────────────────────────────────────────────── when it appears

test('with no attachment there is no building control at all', () => {
  assert.equal(v().cbShow, 'none', 'a question with no file is not a filing');
  cleanup();
});

test('staging a file reveals the control', () => {
  stage('04_WKU_Facilities-Management-SLA-2022.pdf');
  assert.equal(v().cbShow, 'flex');
  cleanup();
});

test('with a file and no building chosen, the consequence is stated', () => {
  stage('contract.pdf');
  assert.equal(v().cbChosen, false);
  assert.match(v().cbWarn, /not be validated|not validated/i);
  assert.match(v().cbWarn, /no building/i);
  cleanup();
});

// ─────────────────────────────────────────────────────── choosing

test('the picker searches name AND code, because names are not unique here', () => {
  stage('contract.pdf');
  v().cbOpen();
  v().cbSetQuery({ target: { value: 'MU-009' } });
  const rows = v().cbMatches;
  assert.equal(rows.length, 1, 'three buildings share the name "MixedUse 004"; the code tells them apart');
  assert.equal(rows[0].code, 'MU-009');
  cleanup();
});

test('searching by name still works', () => {
  stage('contract.pdf');
  v().cbOpen();
  v().cbSetQuery({ target: { value: 'bishops' } });
  assert.deepEqual(v().cbMatches.map((r) => r.name), ['Bishopsgate Tower']);
  cleanup();
});

test('picking a building names it and closes the picker', () => {
  stage('contract.pdf');
  v().cbOpen();
  assert.equal(v().cbPickerOpen, true);
  v().cbMatches.find((r) => r.code === 'B-01').pick();
  assert.equal(v().cbPickerOpen, false);
  assert.equal(v().cbChosen, true);
  assert.match(v().cbLabel, /Bishopsgate Tower/);
  cleanup();
});

test('the choice can be cleared back to unfiled', () => {
  stage('contract.pdf');
  v().cbOpen();
  v().cbMatches[0].pick();
  v().cbClear();
  assert.equal(v().cbChosen, false);
  cleanup();
});

// ─────────────────────────────────────────────────────── what gets sent

test('the chosen building rides with the upload', async () => {
  stage('contract.pdf');
  v().cbOpen();
  v().cbMatches.find((r) => r.code === 'B-01').pick();
  await c.ccAsk('ingest');
  await new Promise((r) => setTimeout(r, 30));
  const up = sent.find((x) => /run-stateful-with-files/.test(x.path));
  assert.ok(up, 'no upload was attempted');
  assert.equal(up.form.get('building_id'), B1,
    'without this the backend skips validation, the audit trail stays empty, and the document is filed against nothing');
  cleanup();
});

test('with no building chosen the field is omitted, not sent empty', async () => {
  stage('contract.pdf');
  await c.ccAsk('ingest');
  await new Promise((r) => setTimeout(r, 30));
  const up = sent.find((x) => /run-stateful-with-files/.test(x.path));
  assert.ok(up);
  assert.ok(!up.form.has('building_id'), 'an empty string is not a building');
  cleanup();
});

test('a building set by "hoist a building → ingest now" is still honoured', async () => {
  // The one path that worked before this control existed. It must keep working.
  stage('contract.pdf');
  c.setState({ declForId: B2, declFor: 'MixedUse 004' });
  await c.ccAsk('ingest');
  await new Promise((r) => setTimeout(r, 30));
  const up = sent.find((x) => /run-stateful-with-files/.test(x.path));
  assert.equal(up.form.get('building_id'), B2);
  cleanup();
});

test('an explicit pick wins over a stale hoist-a-building id', async () => {
  stage('contract.pdf');
  c.setState({ declForId: B2, declFor: 'MixedUse 004' });
  v().cbOpen();
  v().cbMatches.find((r) => r.code === 'B-01').pick();
  await c.ccAsk('ingest');
  await new Promise((r) => setTimeout(r, 30));
  const up = sent.find((x) => /run-stateful-with-files/.test(x.path));
  assert.equal(up.form.get('building_id'), B1, 'the reader just chose; that beats a leftover');
  cleanup();
});

// ─────────────────────────────────────────────────────── persistence

test('the choice survives the turn, so three certificates are not picked three times', async () => {
  stage('a.pdf');
  v().cbOpen();
  v().cbMatches.find((r) => r.code === 'B-01').pick();
  await c.ccAsk('first');
  await new Promise((r) => setTimeout(r, 30));
  assert.equal(c.state.cbBuildingId, B1, 'still filed against the same building');
  cleanup();
});

test('a new session does not inherit the last conversation’s building', () => {
  stage('a.pdf');
  v().cbOpen();
  v().cbMatches.find((r) => r.code === 'B-01').pick();
  c.newQuery();
  assert.ok(!c.state.cbBuildingId, 'a stale building is how a document gets filed in the wrong place');
  cleanup();
});

// ─────────────────────────────────────────────────────── hoisting a new one

test('"hoist a new one" opens the building form and keeps the file staged', () => {
  stage('contract.pdf');
  v().cbHoist();
  assert.equal(c.state.bcOpen, true, 'the existing hoist-a-building form, not a new one');
  assert.equal((c.state.ccFiles || []).length, 1, 'losing the attachment would mean re-picking the file');
  cleanup();
});

// ─────────────────────────────────────────────────────── scope is not a filing

test('the top-bar scope building is NOT used as a default', () => {
  // That picker sets what you are LOOKING AT. Filing a document against it because you
  // happened to be browsing there is the wrong-building binding the backend gate exists
  // to catch.
  c.setState({ account: Object.assign({}, c.state.account, { selected_building_id: B2 }) });
  stage('contract.pdf');
  assert.equal(v().cbChosen, false);
  cleanup();
});

// ── the same filing controls on the HOME ask bar ─────────────────────────────────────────
// `chatView()` already counts "home", so a question asked from the hero bar routes through
// ccAsk and carries whatever is staged. The plumbing worked; there was simply no way to
// attach a document from the one bar the reader sees first, so the whole ingest path was
// reachable only after opening the chat page.

test('the home view offers the same attach handler as the chat composer', () => {
  c.setState({ view: 'home' });
  assert.equal(typeof v().orchPickFiles, 'function',
    'the hero bar is the first thing on screen; ingest has to start there');
  cleanup();
});

test('a file staged from home shows the filing strip there too', () => {
  c.setState({ view: 'home' });
  stage('04_WKU_Facilities-Management-SLA-2022.pdf');
  assert.equal(v().cbShow, 'flex');
  assert.equal(v().cbChosen, false);
  assert.match(v().cbWarn, /no building/i);
  cleanup();
});

test('home shows no strip until something is attached', () => {
  c.setState({ view: 'home' });
  assert.equal(v().cbShow, 'none', 'the home page is the first thing seen; an idle picker is noise');
  cleanup();
});

test('the staged files are listed on home, each removable', () => {
  c.setState({ view: 'home' });
  stage('a.pdf', 'b.pdf');
  const rows = v().orchFiles || [];
  assert.equal(rows.length, 2);
  assert.equal(rows[0].name, 'a.pdf');
  assert.equal(typeof rows[0].drop, 'function');
  cleanup();
});

test('asking from home sends the attachment filed against the chosen building', async () => {
  c.setState({ view: 'home', query: 'ingest this' });
  stage('contract.pdf');
  v().cbOpen();
  v().cbMatches.find((r) => r.code === 'B-01').pick();
  v().runQuery();
  await new Promise((r) => setTimeout(r, 40));
  const up = sent.find((x) => /run-stateful-with-files/.test(x.path));
  assert.ok(up, 'Run from the hero bar must reach the ingest endpoint');
  assert.equal(up.form.get('building_id'), B1);
  cleanup();
});

test('Run with a file but an empty question still ingests rather than doing nothing', async () => {
  // An attachment IS the instruction. Making the reader type something first would be a
  // riddle: runQuery() with an empty box opens the chat and the file would sit unexplained.
  c.setState({ view: 'home', query: '' });
  stage('contract.pdf');
  v().runQuery();
  await new Promise((r) => setTimeout(r, 40));
  const up = sent.find((x) => /run-stateful-with-files/.test(x.path));
  assert.ok(up, 'a staged document with no typed question must still be ingested');
  cleanup();
});
