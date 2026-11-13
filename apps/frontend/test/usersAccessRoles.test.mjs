// Users & access: giving a user a role and ticking their pages (12 Nov 2026).
//
// Hussain: "i can create a normal user or i can assign a role; if i am assigning a role i need
// to have an option to select which pages i need to allow the user to access". And: "none
// should be ticked" - a role is a label, the admin ticks every page by hand.
//
// The real controller in Node, with the admin API's two writes replaced so what would be
// sent is what is checked.
import { test, beforeEach } from 'node:test';
import assert from 'node:assert/strict';

const mem = {};
globalThis.window = {
  location: { origin: 'http://test.local' }, scrollTo: () => {},
  addEventListener: () => {}, removeEventListener: () => {},
  localStorage: { getItem: (k) => (k in mem ? mem[k] : null), setItem: (k, v) => { mem[k] = String(v); }, removeItem: (k) => { delete mem[k]; } },
  sessionStorage: { getItem: () => null, setItem: () => {}, removeItem: () => {} }
};
globalThis.WebSocket = class { constructor() { throw new Error('no sockets in tests'); } };
globalThis.fetch = async (url) => { throw new TypeError('no network in tests: ' + url); };

const { HoistraLogic } = await import('../src/logic/HoistraLogic.js');
const { adminApi } = await import('../src/api/admin.js');
const { shapeLiveUser } = await import('../src/logic/usersLive.js');

const B1 = '11111111-1111-4111-8111-111111111111';
const U1 = 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa';
const U2 = 'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb';
const ADMIN = { id: 'adm', email: 'ada@example.com', full_name: 'Ada Admin', organization_id: 'org-1', role: 'admin', pages: null, pages_known: true };
const row = (over) => Object.assign({ id: U1, name: 'Sam Okafor', email: 'sam@example.com', title: 'FM', buildings: ['Harbour Point'], allB: false, ingest: false, status: 'Active', role: 'user', queries: 0, ingests: 0, last: '—', live: true, accessRole: null, accessRoleLabel: null, pages: null }, over || {});

let sent;
const settle = () => new Promise((r) => setTimeout(r, 20));

function page(users) {
  const x = new HoistraLogic();
  ['loadLiveData', 'resetLiveData', 'authLoadScope', 'usLiveLoad', 'auLiveLoad', 'saLiveLoad', 'ccCaseSync', 'cpOpenLinked', 'authStop', 'crEnsureRun'].forEach((k) => { x[k] = () => {}; });
  x.flashes = [];
  x.flash = (m) => { x.flashes.push(String(m)); };
  x.setState({ signedIn: true, account: ADMIN, role: 'admin', view: 'users', usLiveLoadedAt: Date.now(),
    axBldsLive: [{ id: B1, name: 'Harbour Point' }], users: users || [row()], usInviteOpen: true });
  return x;
}
const form = (x) => x.renderVals();
const chip = (list, label) => list.find((c) => c.label === label || c.name === label);
const on = (list) => list.filter((c) => c.on).map((c) => c.label);
const person = (x, id) => x.renderVals().axUsers.find((u) => u.id === (id || U1));

beforeEach(() => {
  sent = { invite: [], patch: [] };
  adminApi.inviteUser = async (body) => { sent.invite.push(body); return { ok: true, user_id: U2, email: body.email, can_ingest: !!body.can_ingest, buildings: [], access_role: body.access_role || null, pages: body.pages || null }; };
  adminApi.patchUser = async (id, body) => { sent.patch.push([id, body]); return { ok: true, user_id: id, changed: body }; };
});

// ── inviting ────────────────────────────────────────────────────────────────────────────
test('the invite form offers No role and the four roles, with No role chosen', () => {
  const v = form(page());
  assert.deepEqual(v.usRoleChips.map((c) => c.label), ['No role — normal user', 'Portfolio Management', 'Property Manager', 'General User', 'Compliance Officer']);
  assert.deepEqual(on(v.usRoleChips), ['No role — normal user']);
  assert.equal(v.usRoleOn, false);
  assert.equal(v.usSendOff, false);
});

test('picking a role shows the six pages and ticks nothing', () => {
  const x = page();
  chip(form(x).usRoleChips, 'Compliance Officer').pick();
  const v = form(x);
  assert.equal(v.usRoleOn, true);
  assert.deepEqual(v.usPageChips.map((c) => c.label), ['Buildings', 'Compliance', 'Energy', 'Vendors', 'Maintenance', 'Assets']);
  assert.deepEqual(v.usPageChips.map((c) => c.hint), ['', '', '', 'Vendor performance', 'Vendor operations', '']);
  assert.deepEqual(on(v.usPageChips), []);
  assert.equal(v.usSendOff, true);
  assert.equal(v.usRoleNote, 'Tick at least one page for this role.');
});

test('an invitation with a role and no page is not sent', async () => {
  const x = page();
  x.setState({ usName: 'Dana Field', usEmail: 'dana@example.com' });
  chip(form(x).usRoleChips, 'General User').pick();
  form(x).usSend();
  await settle();
  assert.deepEqual(sent.invite, []);
  assert.ok(x.flashes.some((m) => m.indexOf('Tick at least one page') > -1));
});

test('an invitation with a role sends the role and its pages, in page order', async () => {
  const x = page();
  x.setState({ usName: 'Dana Field', usEmail: 'dana@example.com' });
  chip(form(x).usRoleChips, 'Compliance Officer').pick();
  chip(form(x).usPageChips, 'Compliance').pick();
  chip(form(x).usPageChips, 'Buildings').pick();
  assert.deepEqual(on(form(x).usPageChips), ['Buildings', 'Compliance']);
  assert.equal(form(x).usSendOff, false);
  form(x).usSend();
  await settle();
  assert.equal(sent.invite.length, 1);
  assert.equal(sent.invite[0].access_role, 'compliance_officer');
  assert.deepEqual(sent.invite[0].pages, ['buildings', 'compliance']);
  // The new row carries the role, and the form is cleared for the next person.
  const added = person(x, U2);
  assert.equal(added.roleTag, 'Compliance Officer');
  assert.equal(x.state.usRole, '');
  assert.deepEqual(x.state.usPages, []);
});

test('an invitation without a role sends neither field', async () => {
  const x = page();
  x.setState({ usName: 'Dana Field', usEmail: 'dana@example.com' });
  form(x).usSend();
  await settle();
  assert.equal(sent.invite.length, 1);
  assert.equal('access_role' in sent.invite[0], false);
  assert.equal('pages' in sent.invite[0], false);
});

test('going back to No role drops the ticks', () => {
  const x = page();
  chip(form(x).usRoleChips, 'General User').pick();
  chip(form(x).usPageChips, 'Energy').pick();
  chip(form(x).usRoleChips, 'No role — normal user').pick();
  assert.equal(form(x).usRoleOn, false);
  chip(form(x).usRoleChips, 'Property Manager').pick();
  assert.deepEqual(on(form(x).usPageChips), []);
});

// ── the table ───────────────────────────────────────────────────────────────────────────
test('the server row carries the role and the pages', () => {
  const u = shapeLiveUser({ id: U1, full_name: 'Sam', email: 's@example.com', status: 'active', role: 'user', access_role: 'compliance_officer', access_role_label: 'Compliance Officer', pages: ['buildings', 'compliance'] }, {}, new Date().toISOString());
  assert.equal(u.accessRole, 'compliance_officer');
  assert.equal(u.accessRoleLabel, 'Compliance Officer');
  assert.deepEqual(u.pages, ['buildings', 'compliance']);
  const n = shapeLiveUser({ id: U1, full_name: 'Sam', email: 's@example.com', status: 'active', role: 'user', access_role: null, access_role_label: null, pages: null }, {}, new Date().toISOString());
  assert.equal(n.accessRole, null);
  assert.equal(n.pages, null);
});

test('a row shows the role beside the name, and nothing for a normal user', () => {
  const x = page([row({ accessRole: 'compliance_officer', accessRoleLabel: 'Compliance Officer', pages: ['buildings', 'compliance'] }), row({ id: U2, name: 'Nia', email: 'nia@example.com' })]);
  assert.equal(person(x, U1).roleTag, 'Compliance Officer');
  assert.equal(person(x, U1).roleTagShow, 'inline-block');
  assert.equal(person(x, U2).roleTag, '');
  assert.equal(person(x, U2).roleTagShow, 'none');
});

test('the open row offers the same role choice and ticks, showing what the person holds', () => {
  const x = page([row({ accessRole: 'compliance_officer', accessRoleLabel: 'Compliance Officer', pages: ['buildings', 'compliance'] })]);
  const u = person(x);
  assert.equal(u.accessShow, 'flex');
  assert.deepEqual(on(u.roleChips), ['Compliance Officer']);
  assert.deepEqual(on(u.pageChips), ['Buildings', 'Compliance']);
  assert.equal(u.pagesShow, 'flex');
});

test('an admin row has no role control', () => {
  const x = page([row({ allB: true, role: 'admin' })]);
  assert.equal(person(x).accessShow, 'none');
});

// ── editing ─────────────────────────────────────────────────────────────────────────────
test('ticking a page on a row saves the role and the whole list', async () => {
  const x = page([row({ accessRole: 'compliance_officer', accessRoleLabel: 'Compliance Officer', pages: ['compliance'] })]);
  chip(person(x).pageChips, 'Buildings').pick();
  await settle();
  assert.deepEqual(sent.patch, [[U1, { access_role: 'compliance_officer', pages: ['buildings', 'compliance'] }]]);
  assert.deepEqual(on(person(x).pageChips), ['Buildings', 'Compliance']);
});

test('giving a normal user a role saves nothing until a page is ticked', async () => {
  const x = page();
  chip(person(x).roleChips, 'Property Manager').pick();
  await settle();
  assert.deepEqual(sent.patch, []);
  assert.deepEqual(on(person(x).roleChips), ['Property Manager']);
  assert.deepEqual(on(person(x).pageChips), []);
  assert.equal(person(x).accessNote, 'Tick at least one page for this role. Nothing is saved until then.');
  chip(person(x).pageChips, 'Maintenance').pick();
  await settle();
  assert.deepEqual(sent.patch, [[U1, { access_role: 'property_manager', pages: ['ops'] }]]);
  assert.equal(person(x).roleTag, 'Property Manager');
});

test('choosing No role makes the person a normal user again', async () => {
  const x = page([row({ accessRole: 'general_user', accessRoleLabel: 'General User', pages: ['energy'] })]);
  chip(person(x).roleChips, 'No role — normal user').pick();
  await settle();
  assert.deepEqual(sent.patch, [[U1, { access_role: null }]]);
  assert.equal(person(x).roleTag, '');
  assert.equal(person(x).pagesShow, 'none');
});

test('unticking the last page saves nothing and says why', async () => {
  const x = page([row({ accessRole: 'general_user', accessRoleLabel: 'General User', pages: ['energy'] })]);
  chip(person(x).pageChips, 'Energy').pick();
  await settle();
  assert.deepEqual(sent.patch, []);
  assert.equal(person(x).accessNote, 'Tick at least one page for this role. Nothing is saved until then.');
});

test('a refused change is put back and the server\'s reason is shown', async () => {
  adminApi.patchUser = async () => { throw new Error('Roles are not available on this installation yet.'); };
  const x = page([row({ accessRole: 'general_user', accessRoleLabel: 'General User', pages: ['energy'] })]);
  chip(person(x).pageChips, 'Assets').pick();
  await settle();
  assert.deepEqual(on(person(x).pageChips), ['Energy']);
  assert.ok(x.flashes.some((m) => m.indexOf('Roles are not available on this installation yet.') > -1));
});
