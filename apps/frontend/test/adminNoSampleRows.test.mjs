// Users & access and the Super Admin console never show sample people or companies, and never
// claim an invitation went out when the server was never reached (Hussain, 17 Nov 2026, UI audit:
// "Invite and Create company add local rows and flash 'Invitation sent to …' though nothing was
// sent. The table fills with sample people at @plenum-tech.com").
import { test, beforeEach } from 'node:test';
import assert from 'node:assert/strict';

globalThis.window = { location: { origin: 'http://test.local' }, scrollTo: () => {}, addEventListener: () => {}, removeEventListener: () => {} };
globalThis.fetch = () => Promise.reject(new TypeError('Failed to fetch'));
globalThis.WebSocket = class { constructor() { throw new Error('no sockets in tests'); } };

const { HoistraLogic } = await import('../src/logic/HoistraLogic.js');

let c, flashes;
beforeEach(() => {
  c = new HoistraLogic({});
  flashes = [];
  c.flash = (m) => flashes.push(m);
  c.setState({ signedIn: true, role: 'admin', account: { email: 'ada@example.com', role: 'admin' } });
});
const cleanup = () => { clearInterval(c._orchTick); clearTimeout(c._tt); clearTimeout(c._homeRetry); clearTimeout(c._ccRetry); };

// ── Users & access ──────────────────────────────────────────────────────────

test('no sample people or sample buildings before the users read answers', () => {
  c.setState({ view: 'users' });
  const v = c.renderVals();
  assert.deepEqual(v.axUsers, [], 'no @plenum-tech.com sample rows');
  assert.deepEqual(v.usFormBlds, [], 'no sample building chips to allocate to');
  cleanup();
});

test('a failed users read says so in plain words, with dashes in the tiles', () => {
  c.setState({ view: 'users', usLiveError: 'HTTP 503' });
  const v = c.renderVals();
  assert.deepEqual(v.axUsers, []);
  assert.ok(v.usTiles.slice(0, 3).every((t) => t.value === '—'), 'never a count of sample people');
  assert.match(v.usLiveSourceLabel, /could not be read — the server can't be reached right now/);
  assert.doesNotMatch(v.usLiveSourceLabel, /sample|503/i);
  cleanup();
});

test('Invite while the server has not answered sends nothing and says so', () => {
  c.setState({ view: 'users', usName: 'Sam', usEmail: 'sam@example.com', usInviteOpen: true });
  const before = c.state.users.length;
  c.renderVals().usSend();
  assert.equal(c.state.users.length, before, 'no local row minted');
  assert.equal(flashes.length, 1);
  assert.match(flashes[0], /nothing was sent/i);
  assert.doesNotMatch(flashes[0], /Invitation sent/);
  cleanup();
});

// ── Super Admin ─────────────────────────────────────────────────────────────

test('the console shows no sample companies before the companies read answers', () => {
  c.setState({ account: { email: 'sa@example.com', role: 'superadmin' }, saOn: true });
  const v = c.renderVals();
  assert.deepEqual(v.saCompanies, []);
  assert.deepEqual(v.saCredits, []);
  cleanup();
});

test('Create company while the platform has not answered creates nothing and says so', () => {
  c.setState({ account: { email: 'sa@example.com', role: 'superadmin' }, saOn: true, saName: 'Acme Estates', saEmail: 'admin@acme.example' });
  const before = c.state.saCompanies.length;
  c.renderVals().saCreate();
  assert.equal(c.state.saCompanies.length, before, 'no local company minted');
  assert.match(flashes[0], /nothing was created/i);
  assert.doesNotMatch(flashes[0], /invitation sent/i);
  cleanup();
});

test('a failed companies read is plain words, without "sample data"', () => {
  c.setState({ account: { email: 'sa@example.com', role: 'superadmin' }, saOn: true, saLiveError: 'HTTP 503' });
  const v = c.renderVals();
  assert.match(v.saLiveError, /could not be read — the server can't be reached right now/);
  assert.doesNotMatch(v.saLiveError, /sample|503/i);
  cleanup();
});
