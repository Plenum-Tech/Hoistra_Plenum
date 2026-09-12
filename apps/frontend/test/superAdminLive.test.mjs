// shapeLiveCompany — the Super Admin console's company rows and usage tiles, shaped from
// GET /api/superadmin/companies, GET /api/superadmin/companies/{id} and
// GET /api/superadmin/credits. Fixtures mirror the backend contract EXACTLY as transcribed
// from the route handlers (scratchpad contract.md §8–§12, 12 Sep 2026): canonical
// country codes UK/US/AE/SG, lowercase lifecycle, float credits, raw udr_data_bytes,
// admin_email on the detail card only — never on the list row.
import { test, afterEach } from 'node:test';
import assert from 'node:assert/strict';
import { shapeLiveCompany, fmtBytes, fmtCount, ccLabel, lifecycleLabel, CC_API } from '../src/logic/superAdminLive.js';

const ORG1 = '7f0c2c3a-4b1d-4e5f-8a90-1b2c3d4e5f60';
const ORG2 = '0a1b2c3d-4e5f-6071-8293-a4b5c6d7e8f9';
const NEW_ORG = 'c0ffee00-1234-4abc-9def-000000000001';
const NOW = '2026-09-12T14:00:00Z';

// §8 — list route: ordered credits DESC, thin rows, no admin_email, no tile numbers.
const COMPANIES = {
  ok: true, count: 2,
  companies: [
    { organization_id: ORG1, name: 'Plenum Group', country_code: 'UK', lifecycle: 'active', status: 'active', created_at: '2026-05-02T09:00:00Z', credits_this_month: 8420.0, last_activity: '2026-09-12T13:48:00Z' },
    { organization_id: ORG2, name: 'Gulf Estates FZ', country_code: 'AE', lifecycle: 'created', status: 'active', created_at: '2026-09-01T08:00:00Z', credits_this_month: 2040.4, last_activity: null }
  ],
  buildings_without_company: 0
};

// §10 — the usage card: everything the 8 tiles need, admin_email on company, raw bytes.
const CARD1 = {
  ok: true,
  company: { id: ORG1, name: 'Plenum Group', country_code: 'UK', country: 'United Kingdom', lifecycle: 'active', status: 'active', admin_email: 'ops@plenum.co', created_at: '2026-05-02T09:00:00Z', updated_at: '2026-09-12T13:48:00Z' },
  buildings_created: 12, hoist_graphs: 9, last_activity: '2026-09-12T13:48:00Z',
  udr_data_bytes: 5260000000, udr_documents: 233,
  compliance_certificates: 40, certificate_countries: ['AE', 'UK'], api_requests_30d: 1210000,
  queries: 1042, ingests: 77, credits_this_month: 8420.0, credits_total: 20110.5,
  users: { total: 6, active: 4, invited: 1, can_ingest: 3 },
  tariff: { query: 1.0, ingest: 5.0, api_request: 0.1 }, counted_from: {}, pending_invitations: 1
};
const CARD2 = {
  ok: true,
  company: { id: ORG2, name: 'Gulf Estates FZ', country_code: 'AE', country: 'United Arab Emirates', lifecycle: 'created', status: 'active', admin_email: null, created_at: '2026-09-01T08:00:00Z', updated_at: '2026-09-01T08:00:00Z' },
  buildings_created: 0, hoist_graphs: 0, last_activity: null,
  udr_data_bytes: 0, udr_documents: 0,
  compliance_certificates: 0, certificate_countries: [], api_requests_30d: 0,
  queries: 0, ingests: 0, credits_this_month: 2040.4, credits_total: 2040.4,
  users: { total: 0, active: 0, invited: 0, can_ingest: 0 },
  tariff: { query: 1.0, ingest: 5.0, api_request: 0.1 }, counted_from: {}, pending_invitations: 0
};

// §12 — credits: the bar chart's per-company figures, credits DESC.
const CREDITS = {
  ok: true, month_total: 10460.4,
  tariff: { query: 1.0, ingest: 5.0, api_request: 0.1 },
  companies: [
    { organization_id: ORG1, name: 'Plenum Group', credits_this_month: 8420.0 },
    { organization_id: ORG2, name: 'Gulf Estates FZ', credits_this_month: 2040.4 }
  ],
  billing_note: 'Usage-led: platform activity, API requests and credits, with no seat limit.'
};

test('a list-only row shapes to the seed vocabulary with null tile numbers until its card lands', () => {
  const r = shapeLiveCompany(COMPANIES.companies[1], null, NOW);
  assert.equal(r.live, true);
  assert.equal(r.id, ORG2);
  assert.equal(r.name, 'Gulf Estates FZ');
  assert.equal(r.cc, 'UAE', 'canonical AE renders as the seed display "UAE"');
  assert.equal(r.status, 'Created');
  assert.equal(r.credits, 2040, 'float credits round to the seed integer');
  assert.equal(r.last, '—');
  assert.equal(r.buildings, null);
  assert.equal(r.graphs, null);
  assert.equal(r.certs, null);
  assert.equal(r.users, null);
  assert.equal(r.udr, '—');
  assert.equal(r.api, '—');
  assert.equal(r.certCc, '—');
  assert.equal(r.invited, false, 'lifecycle created = no administrator invited yet');
});

test('lifecycle onboarding/active infers invited on a list-only row, and last_activity humanises', () => {
  const r = shapeLiveCompany(COMPANIES.companies[0], null, NOW);
  assert.equal(r.status, 'Active');
  assert.equal(r.invited, true, 'onboarding/active only happen after an invitation');
  assert.equal(r.last, '12 min ago');
});

test('a row with its usage card carries all eight tile figures in the seed formats', () => {
  const r = shapeLiveCompany(COMPANIES.companies[0], CARD1, NOW);
  assert.equal(r.id, ORG1);
  assert.equal(r.buildings, 12);
  assert.equal(r.graphs, 9);
  assert.equal(r.udr, '5.3 GB', 'raw udr_data_bytes format client-side');
  assert.equal(r.certs, 40);
  assert.equal(r.certCc, 'UAE, UK', 'certificate country codes join as display names');
  assert.equal(r.api, '1.21M');
  assert.equal(r.credits, 8420);
  assert.equal(r.users, 6);
  assert.equal(r.invited, true, 'admin_email on the record');
  assert.equal(r.last, '12 min ago');
});

test('the card alone can shape a full row — the merge path after saLiveLoadCompany', () => {
  const r = shapeLiveCompany(null, CARD2, NOW);
  assert.equal(r.id, ORG2);
  assert.equal(r.name, 'Gulf Estates FZ');
  assert.equal(r.cc, 'UAE');
  assert.equal(r.status, 'Created');
  assert.equal(r.udr, '0 MB');
  assert.equal(r.api, '0');
  assert.equal(r.certCc, '—');
  assert.equal(r.invited, false, 'no admin_email and no pending invitation');
});

test('fmtBytes matches the seed vocabulary: MB under a gigabyte, one-decimal GB above', () => {
  assert.equal(fmtBytes(0), '0 MB');
  assert.equal(fmtBytes(90e6), '90 MB');
  assert.equal(fmtBytes(760e6), '760 MB');
  assert.equal(fmtBytes(1.8e9), '1.8 GB');
  assert.equal(fmtBytes(4.9e9), '4.9 GB');
  assert.equal(fmtBytes(5260000000), '5.3 GB');
  assert.equal(fmtBytes(2e9), '2 GB');
  assert.equal(fmtBytes(500), '1 MB', 'no KB tier in the seed — a trickle still reads as data');
  assert.equal(fmtBytes(null), '—');
  assert.equal(fmtBytes(undefined), '—');
});

test('fmtCount matches the seed vocabulary: bare, k, then M with trimmed decimals', () => {
  assert.equal(fmtCount(0), '0');
  assert.equal(fmtCount(612), '612');
  assert.equal(fmtCount(9000), '9k');
  assert.equal(fmtCount(112000), '112k');
  assert.equal(fmtCount(460000), '460k');
  assert.equal(fmtCount(1210000), '1.21M');
  assert.equal(fmtCount(2000000), '2M');
  assert.equal(fmtCount(999999), '1M', 'no "1000k" at the boundary');
  assert.equal(fmtCount(null), '—');
});

test('country and lifecycle vocabularies map both ways', () => {
  assert.equal(ccLabel('AE'), 'UAE');
  assert.equal(ccLabel('SG'), 'Singapore');
  assert.equal(ccLabel('UK'), 'UK');
  assert.equal(ccLabel(null), '—');
  assert.equal(ccLabel('FR'), 'FR', 'an unmapped code passes through rather than lying');
  assert.equal(CC_API.UAE, 'AE');
  assert.equal(CC_API.Singapore, 'SG');
  assert.equal(CC_API.UK, 'UK');
  assert.equal(lifecycleLabel('created'), 'Created');
  assert.equal(lifecycleLabel('onboarding'), 'Onboarding');
  assert.equal(lifecycleLabel('active'), 'Active');
  assert.equal(lifecycleLabel('suspended'), 'Suspended', 'a future token capitalises, never throws');
});

// ── controller-level: the loaders against the real HoistraLogic ──────────────────────
// window + fetch stubbed BEFORE the dynamic import; the mixin is Object.assign'd here the
// way the integrator will in HoistraLogic.js, so these pass without that wiring.
globalThis.window = {
  location: { origin: 'http://test.local' }, scrollTo: () => {},
  addEventListener: () => {}, removeEventListener: () => {},
  localStorage: { getItem: () => null, setItem: () => {}, removeItem: () => {} }
};

let routes = {};
let calls = [];
globalThis.fetch = (url, opts) => {
  const u = new URL(url);
  const key = ((opts && opts.method) || 'GET') + ' ' + u.pathname;
  calls.push({ key, search: u.search, body: opts && opts.body ? JSON.parse(opts.body) : null });
  const hit = routes[key];
  if (!hit) {
    return Promise.resolve({
      ok: false, status: 404, statusText: 'Not Found',
      text: () => Promise.resolve(JSON.stringify({ detail: { ok: false, error: 'no stub for ' + key, reason: '' } }))
    });
  }
  if (hit.__status) {
    return Promise.resolve({
      ok: false, status: hit.__status, statusText: 'Stubbed',
      text: () => Promise.resolve(JSON.stringify(hit.__body || {}))
    });
  }
  return Promise.resolve({ ok: true, status: 200, statusText: 'OK', text: () => Promise.resolve(JSON.stringify(hit)) });
};

const { HoistraLogic } = await import('../src/logic/HoistraLogic.js');
const { superAdminLiveMethods } = await import('../src/logic/superAdminLive.js');
Object.assign(HoistraLogic.prototype, superAdminLiveMethods);

const B = '/backend/ops-intelligence/api/superadmin';
let c;
function drop() {
  if (!c) return;
  clearTimeout(c._tt); clearTimeout(c._saLiveRetry); clearTimeout(c._saLiveRefresh);
  c = null;
}
function fresh(r) {
  drop();
  routes = r || {};
  calls = [];
  c = new HoistraLogic();
  return c;
}
afterEach(drop);

test('saLiveLoad replaces the seed in place, re-points the selection and pulls the selected card', async () => {
  fresh({
    ['GET ' + B + '/companies']: COMPANIES,
    ['GET ' + B + '/credits']: CREDITS,
    ['GET ' + B + '/companies/' + ORG1]: CARD1
  });
  await c.saLiveLoad();
  assert.equal(c.state.saCompanies.length, 2, 'the four seed companies are gone');
  assert.ok(c.state.saCompanies.every((r) => r.live), 'every row is a live row');
  assert.equal(c.state.saSel, ORG1, 'the dead seed selection ("c1") re-points to the top row');
  assert.equal(c.state.saLiveError, '');
  assert.ok(c.state.saLiveLoadedAt);
  await c.saLiveLoadCompany(ORG1); // dedups with the load's own trailing card fetch
  const v = c.saVals(c.state);
  assert.equal(v.saCo.name, 'Plenum Group');
  assert.equal(v.saCo.inviteLabel, 'Re-send admin invitation');
  assert.equal(v.saTiles[0].value, '12', 'buildings created from the card');
  assert.equal(v.saTiles[3].value, '5.3 GB', 'UDR bytes formatted');
  assert.equal(v.saTiles[4].hint, 'countries: UAE, UK');
  assert.equal(v.saTiles[5].value, '1.21M');
  assert.equal(v.saTiles[6].value, '8,420');
  assert.equal(v.saTiles[7].value, '6');
  assert.ok(v.saCredits.every((r) => r.bar.endsWith('%')), 'the credits bars still derive from saCompanies');
  assert.ok(calls.every((x) => x.search === ''), 'no organization_id ever rides on a superadmin call');
});

test('a live row whose card has not landed shows "…" placeholders, never "null"', async () => {
  fresh({
    ['GET ' + B + '/companies']: COMPANIES,
    ['GET ' + B + '/credits']: CREDITS,
    ['GET ' + B + '/companies/' + ORG1]: CARD1
  });
  await c.saLiveLoad();
  c.saVals(c.state).saCompanies[1].pick(); // ORG2 — its card route is not stubbed
  const v = c.saVals(c.state);
  assert.equal(v.saCo.name, 'Gulf Estates FZ');
  assert.equal(v.saCo.inviteLabel, 'Invite company admin');
  assert.equal(v.saTiles[0].value, '…');
  assert.equal(v.saTiles[3].value, '—');
  assert.equal(v.saTiles[6].value, '2,040', 'credits ride on the list row, card or not');
  assert.equal(v.saTiles[7].value, '…');
});

test('saCreate goes to the API once live: canonical country code, admin_email, new org selected', async () => {
  fresh({
    ['GET ' + B + '/companies']: COMPANIES,
    ['GET ' + B + '/credits']: CREDITS,
    ['GET ' + B + '/companies/' + ORG1]: CARD1,
    ['POST ' + B + '/companies']: {
      ok: true, organization_id: NEW_ORG, name: 'Acme FM', country_code: 'AE', lifecycle: 'onboarding',
      admin_invitation: { ok: true, invitation_id: 'i-1', user_id: 'u-1', email: 'boss@acme.com', role: 'admin', can_ingest: true, building_ids: [], expires_at: '2026-09-19T14:00:00Z', email_sent: { ok: true, status: 'sent' } }
    }
  });
  await c.saLiveLoad();
  c.setState({ saName: 'Acme FM', saCc: 'UAE', saEmail: 'boss@acme.com' });
  await c.saVals(c.state).saCreate();
  const post = calls.find((x) => x.key === 'POST ' + B + '/companies');
  assert.ok(post, 'the create went to the API, not local state');
  assert.deepEqual(post.body, { name: 'Acme FM', country_code: 'AE', admin_email: 'boss@acme.com' }, 'the UI chip "UAE" maps to the contract\'s AE');
  assert.equal(c.state.saSel, NEW_ORG, 'the new org is selected');
  const row = c.state.saCompanies.find((r) => r.id === NEW_ORG);
  assert.equal(row.status, 'Onboarding');
  assert.equal(row.invited, true);
  assert.equal(c.state.saName, '', 'the form cleared');
  assert.match(c.state.toast, /admin invitation sent to boss@acme\.com/);
  assert.ok(c._saLiveRefresh, 'a re-read is scheduled to swap the optimistic row');
});

test('saCreate stays local-only while the platform has never answered — the offline demo keeps working', () => {
  fresh({});
  c.setState({ saName: 'Local Demo Co', saCc: 'UK', saEmail: '' });
  c.saVals(c.state).saCreate();
  assert.equal(calls.length, 0, 'no API call for a pure-seed console');
  assert.ok(c.state.saCompanies.some((r) => r.name === 'Local Demo Co'));
  assert.match(c.state.toast, /Invite its administrator when ready/);
});

test('invite on a company with no admin_email on record instructs instead of calling the API', async () => {
  fresh({
    ['GET ' + B + '/companies']: COMPANIES,
    ['GET ' + B + '/credits']: CREDITS,
    ['GET ' + B + '/companies/' + ORG1]: CARD1,
    ['GET ' + B + '/companies/' + ORG2]: CARD2
  });
  await c.saLiveLoad();
  c.setState({ saSel: ORG2 });
  await c.saVals(c.state).saCo.invite();
  assert.ok(!calls.some((x) => x.key.startsWith('POST')), 'no invite-admin POST without an email to send to');
  assert.match(c.state.toast, /No administrator email on record for Gulf Estates FZ/);
});

test('invite re-sends to the admin_email on the record and reports the send', async () => {
  fresh({
    ['GET ' + B + '/companies']: COMPANIES,
    ['GET ' + B + '/credits']: CREDITS,
    ['GET ' + B + '/companies/' + ORG1]: CARD1,
    ['POST ' + B + '/companies/' + ORG1 + '/invite-admin']: {
      ok: true, invitation_id: 'i-2', user_id: 'u-2', email: 'ops@plenum.co', role: 'admin', can_ingest: true, building_ids: [], expires_at: '2026-09-19T14:00:00Z', email_sent: { ok: true, status: 'sent' }
    }
  });
  await c.saLiveLoad();
  await c.saLiveLoadCompany(ORG1);
  await c.saVals(c.state).saCo.invite();
  const post = calls.find((x) => x.key === 'POST ' + B + '/companies/' + ORG1 + '/invite-admin');
  assert.ok(post, 'the invite went to the API');
  assert.deepEqual(post.body, { email: 'ops@plenum.co' }, 'the email comes off the company record, not a form');
  assert.match(c.state.toast, /Admin invitation sent for Plenum Group/);
});

test('a failed load keeps the seed, surfaces the error and schedules a retry — except on 403', async () => {
  fresh({});
  await c.saLiveLoad();
  assert.equal(c.state.saCompanies[0].id, 'c1', 'the seed survives the outage');
  assert.match(c.state.saLiveError, /no stub/);
  assert.ok(c._saLiveRetry, 'a retry timer is running');
  assert.match(c.saVals(c.state).saLiveError, /Showing sample data/);

  fresh({ ['GET ' + B + '/companies']: { __status: 403, __body: { detail: { ok: false, error: 'Requires the superadmin role.', reason: 'forbidden', required_role: 'superadmin', your_role: 'admin' } } } });
  await c.saLiveLoad();
  assert.match(c.state.saLiveError, /superadmin role/);
  assert.ok(!c._saLiveRetry, 'a role refusal is not retried on a timer');
});
