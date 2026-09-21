// Confirming a contract's terms, and correcting one before you do.
//
// Until now the Vendors page could only read. The chat told the reader to "review and
// confirm the extracted parameters in Vendors → Contract Performance" and that screen had
// no control to do it with — the two endpoints existed on the server and nothing called
// them. These are the writes, so the tests mock fetch per route: this stack's backend is
// production, and confirming is what makes a number binding on a real vendor.
//
// The case that drove the design: a 16-page university contract where 5 of 17 terms were
// platform defaults, including a £350/day labour rate the contract never stated (it is
// priced by the hour). Confirming it would have made that rate the agreed figure every
// future invoice is checked against. So confirm is never a bare button.
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

let handlers, calls;
globalThis.fetch = async (url, opts) => {
  const u = new URL(String(url));
  const method = (opts && opts.method) || 'GET';
  let body = null;
  try { body = opts && opts.body ? JSON.parse(opts.body) : null; } catch { body = opts.body; }
  calls.push({ method, path: u.pathname, body, query: u.search });
  const h = handlers[method + ' ' + u.pathname];
  if (!h) throw new TypeError('Failed to fetch: no handler for ' + method + ' ' + u.pathname);
  const [status, out] = typeof h === 'function' ? h(u, opts, body) : h;
  return {
    ok: status >= 200 && status < 300, status, statusText: String(status),
    text: async () => JSON.stringify(out)
  };
};

const { HoistraLogic } = await import('../src/logic/HoistraLogic.js');

const B = '/backend/ops-intelligence/api/contract-performance';
const CID = '5c072859-b992-497e-8aa8-60ddfa7eb75c';
const USER_UUID = '9b60f4e8-2f11-4d3e-8c7a-11d2a5f6b3c4';

let c;
beforeEach(() => {
  calls = [];
  handlers = {};
  c = new HoistraLogic();
  c.setState({ signedIn: true, account: { id: USER_UUID, email: 'pm@test.local', role: 'admin' } });
  // The page re-reads after a successful write; a stub keeps that off the network.
  c.vpLoad = async () => { c._reloaded = (c._reloaded || 0) + 1; };
});
const cleanup = () => { clearTimeout(c._tt); };
const okConfirm = () => { handlers['POST ' + B + '/contracts/' + CID + '/confirm'] = [200, { ok: true, parameters: { id: CID, status: 'confirmed' } }]; };
const okPatch = () => { handlers['PATCH ' + B + '/contracts/' + CID] = [200, { ok: true, parameters: { id: CID, status: 'draft' } }]; };

// ───────────────────────────────────────────────────────────── confirm

test('confirming posts to the contract it was given', async () => {
  okConfirm();
  await c.vpConfirmContract(CID);
  const post = calls.find((x) => x.method === 'POST');
  assert.ok(post, 'nothing was sent');
  assert.equal(post.path, B + '/contracts/' + CID + '/confirm');
  cleanup();
});

test('a uuid account is named as the person who confirmed', async () => {
  okConfirm();
  await c.vpConfirmContract(CID);
  const post = calls.find((x) => x.method === 'POST');
  assert.equal(post.body.confirmed_by, USER_UUID,
    'confirming is the act that makes the numbers binding — the audit has to name who did it');
  cleanup();
});

test('a non-uuid account id is left out rather than sent and rejected', async () => {
  // The auth tables were integer-keyed at one point. An integer in a uuid field is a 422,
  // and a refused confirm looks to the reader exactly like a broken button.
  c.setState({ account: { id: 41, email: 'pm@test.local' } });
  okConfirm();
  await c.vpConfirmContract(CID);
  const post = calls.find((x) => x.method === 'POST');
  assert.ok(post, 'the confirm must still be sent');
  assert.ok(!('confirmed_by' in (post.body || {})),
    'an id a uuid column cannot hold is omitted, not guessed at');
  cleanup();
});

test('a successful confirm re-reads the page rather than trusting local state', async () => {
  okConfirm();
  await c.vpConfirmContract(CID);
  assert.equal(c._reloaded, 1);
  cleanup();
});

test('a refused confirm says so and does not claim success', async () => {
  handlers['POST ' + B + '/contracts/' + CID + '/confirm'] = [409, { ok: false, error: 'already confirmed' }];
  await c.vpConfirmContract(CID);
  assert.match(String(c.state.toast || c.state.flowDone || ''), /could not|not confirmed|already/i);
  assert.ok(!c._reloaded, 'a failed write must not report a refresh it did not earn');
  cleanup();
});

test('an ok:false body is a failure even though the status was 200', async () => {
  handlers['POST ' + B + '/contracts/' + CID + '/confirm'] = [200, { ok: false, error: 'not_found' }];
  await c.vpConfirmContract(CID);
  assert.match(String(c.state.toast || c.state.flowDone || ''), /could not|not_found/i);
  assert.ok(!c._reloaded);
  cleanup();
});

test('with no contract there is nothing to confirm and nothing is sent', async () => {
  await c.vpConfirmContract(null);
  assert.equal(calls.length, 0, 'a missing id must not become a request to /contracts/null/confirm');
  assert.match(String(c.state.toast || ''), /no contract/i);
  cleanup();
});

// ───────────────────────────────────────────────────────────── editing a term

test('editing a term patches only the field that was touched', async () => {
  okPatch();
  await c.vpEditTerm(CID, 'labour_day_rate', '412.50');
  const patch = calls.find((x) => x.method === 'PATCH');
  assert.ok(patch, 'nothing was sent');
  assert.equal(patch.path, B + '/contracts/' + CID);
  assert.deepEqual(Object.keys(patch.body.updates), ['labour_day_rate'],
    'sending the whole row would overwrite fields nobody edited');
  cleanup();
});

test('a numeric term is sent as a number, not as the string from the box', async () => {
  okPatch();
  await c.vpEditTerm(CID, 'sla_response_p1_hours', '2');
  const patch = calls.find((x) => x.method === 'PATCH');
  assert.strictEqual(patch.body.updates.sla_response_p1_hours, 2);
  cleanup();
});

test('a free-text term keeps its text', async () => {
  okPatch();
  await c.vpEditTerm(CID, 'payment_terms', 'Net 45');
  const patch = calls.find((x) => x.method === 'PATCH');
  assert.strictEqual(patch.body.updates.payment_terms, 'Net 45');
  cleanup();
});

test('a value that is not a number is refused before it reaches a numeric column', async () => {
  okPatch();
  await c.vpEditTerm(CID, 'labour_day_rate', 'about three fifty');
  assert.ok(!calls.some((x) => x.method === 'PATCH'), 'the write must not be attempted');
  assert.match(String(c.state.toast || ''), /number/i);
  cleanup();
});

test('a failed edit reports it and re-reads nothing', async () => {
  handlers['PATCH ' + B + '/contracts/' + CID] = [500, { ok: false, error: 'boom' }];
  await c.vpEditTerm(CID, 'labour_day_rate', '412.50');
  assert.match(String(c.state.toast || ''), /could not|boom/i);
  assert.ok(!c._reloaded, 'the old value must stay on screen rather than a value that was never stored');
  cleanup();
});

test('a successful edit re-reads so the screen shows what the server stored', async () => {
  okPatch();
  await c.vpEditTerm(CID, 'labour_day_rate', '412.50');
  assert.equal(c._reloaded, 1);
  cleanup();
});

test('a structured term is not editable as a line of text', async () => {
  okPatch();
  await c.vpEditTerm(CID, 'kpi_clauses_json', '2 clauses');
  assert.ok(!calls.some((x) => x.method === 'PATCH'),
    'KPI clauses, PPM obligations and parts pricing are objects — a text box would flatten them');
  cleanup();
});

// ── the affordances the panel actually offers ────────────────────────────────────────
// The rules above are only real if the screen enforces them. These drive renderVals the way
// the JSX does, so a regression here shows up as a missing or wrongly-enabled control.

const VID = '00000000-0000-0000-0000-0000000000a1';
const params = (over) => Object.assign({
  id: CID, vendor_id: VID, vendor_name: 'Western Kentucky University DFM',
  document_id: 'doc1', document_name: 'WKU_SLA_2022.pdf',
  contract_ref: 'Facilities Management Service Level Agreement 2022',
  signed_date: '2022-02-01', status: 'draft',
  sla_response_p1_hours: 1, sla_completion_p1_hours: 24,
  labour_day_rate: 350, overtime_rate: 525, call_out_rate: 150, payment_terms: 'Net 30',
  kpi_clauses_json: { a: 1, b: 2 },
  field_sources: {
    contract_ref: 'contract', sla_response_p1_hours: 'contract', sla_completion_p1_hours: 'contract',
    kpi_clauses_json: 'contract',
    labour_day_rate: 'default', overtime_rate: 'default', call_out_rate: 'default', payment_terms: 'default'
  }
}, over || {});

const seed = (over) => {
  c.setState({
    vpVendor: VID,
    vpRaw: {
      summary: { ok: true, scorecards: [], weights: {}, approvals: [], unapproved_criticalities: [], kpis: {} },
      contracts: { ok: true, count: 1, parameters: [params(over)] },
      weights: { ok: true, weights: {} },
      approvals: { ok: true, approvals: [] },
      certificates: [],
      coverage: { UK: [] },
      fetchedAt: Date.now()
    }
  });
  return c.renderVals().vp;
};

test('a draft offers the confirm bar, and says scoring is blocked until it is used', () => {
  const vp = seed();
  assert.equal(vp.confirmShow, 'flex');
  assert.equal(vp.confirmStatus, 'draft');
  assert.equal(vp.confirmDone, false);
  assert.match(vp.confirmNote, /scoring is blocked/i);
  cleanup();
});

test('the confirmation step states how many terms are platform defaults', () => {
  const vp = seed();
  // 4 of this contract's 8 present terms came from the document; the rest are defaults —
  // including the £350/day rate the contract never stated. Confirming makes them agreed.
  assert.match(vp.confirmWarn, /4 of 8 terms are platform defaults/);
  assert.match(vp.confirmWarn, /agreed values/i);
  assert.match(vp.confirmWarn, /Correct anything wrong first/i);
  cleanup();
});

test('confirming is two steps: arming shows the warning, and only then is there a go button', () => {
  seed();
  assert.equal(c.renderVals().vp.confirmArmed, false, 'the warning must not be pre-dismissed');
  c.renderVals().vp.confirmArm();
  assert.equal(c.renderVals().vp.confirmArmed, true);
  c.renderVals().vp.confirmCancel();
  assert.equal(c.renderVals().vp.confirmArmed, false, '"Not yet" must actually back out');
  cleanup();
});

test('a confirmed set shows as confirmed, offers no button, and locks its terms', () => {
  const vp = seed({ status: 'confirmed' });
  assert.equal(vp.confirmDone, true);
  assert.equal(vp.confirmStatus, 'confirmed');
  assert.equal(vp.confirmArmed, false);
  assert.ok(vp.terms.every((t) => t.canEdit === false),
    'scores have already been published against these values — editing one moves the goalposts underneath them');
  cleanup();
});

test('on a draft, scalar terms are editable and structured ones are not', () => {
  const vp = seed();
  assert.equal(vp.terms.find((t) => t.label === 'Labour rate — day').canEdit, true);
  assert.equal(vp.terms.find((t) => t.label === 'P1 response').canEdit, true);
  assert.equal(vp.terms.find((t) => t.label === 'KPI clauses').canEdit, false);
  cleanup();
});

test('editing starts from the stored value, not the formatted one', () => {
  const vp = seed();
  vp.terms.find((t) => t.label === 'Labour rate — day').startEdit();
  const row = c.renderVals().vp.terms.find((t) => t.label === 'Labour rate — day');
  assert.equal(row.editing, true);
  assert.equal(row.draft, '350', 'a box prefilled with "£350 / day" cannot be saved back');
  cleanup();
});

test('opening an edit closes an armed confirmation, so the warning is never read against stale values', () => {
  seed();
  c.renderVals().vp.confirmArm();
  c.renderVals().vp.terms.find((t) => t.label === 'Labour rate — day').startEdit();
  assert.equal(c.renderVals().vp.confirmArmed, false);
  cleanup();
});

test('a vendor with no contract has no confirm bar at all', () => {
  c.setState({
    vpVendor: VID,
    vpRaw: {
      summary: { ok: true, scorecards: [{ id: 's1', vendor_id: VID, vendor_name: 'Nobody', score_month: '2026-08-01', overall_score: 70, component_breakdown: {} }], weights: {}, approvals: [], unapproved_criticalities: [], kpis: {} },
      contracts: { ok: true, count: 0, parameters: [] },
      weights: { ok: true, weights: {} }, approvals: { ok: true, approvals: [] },
      certificates: [], coverage: { UK: [] }, fetchedAt: Date.now()
    }
  });
  assert.equal(c.renderVals().vp.confirmShow, 'none');
  cleanup();
});

// ── (1) the confirmer on screen, (2) the read-nothing warning ────────────────────────────

test('a confirmed panel names who confirmed it and when', () => {
  const vp = seed({
    status: 'confirmed',
    confirmed_by: '00000000-0000-0000-0001-000000000017',
    confirmed_by_name: 'Aasim Shaik',
    confirmed_at: '2026-09-18T07:01:51+00:00'
  });
  assert.match(vp.confirmNote, /Aasim Shaik/, 'CONFIRMED alone is not a record of a decision');
  assert.match(vp.confirmNote, /18 Sep 2026/);
});

test('a confirmed panel with no resolvable name still gives the date, and no uuid', () => {
  const vp = seed({
    status: 'confirmed', confirmed_by: '00000000-0000-0000-0001-000000000017',
    confirmed_by_name: null, confirmed_at: '2026-09-18T07:01:51+00:00'
  });
  assert.match(vp.confirmNote, /18 Sep 2026/);
  assert.doesNotMatch(vp.confirmNote, /0000-0000/, 'a uuid on screen answers nothing');
});

test('a contract that read nothing warns before it can be confirmed', () => {
  // Every term defaulted — the Moreland case.
  const allDefault = {
    field_sources: Object.keys(params().field_sources).reduce((a, k) => { a[k] = 'default'; return a; }, {})
  };
  const vp = seed(allDefault);
  assert.match(vp.confirmWarn, /no contract terms/i);
  assert.match(vp.confirmWarn, /may not be a service contract/i,
    'a document that yielded nothing is a different event from one with gaps');
  cleanup();
});

test('a contract with a few gaps keeps the ordinary defaults warning', () => {
  const vp = seed();
  assert.match(vp.confirmWarn, /platform defaults/i);
  assert.doesNotMatch(vp.confirmWarn, /may not be a service contract/i);
  cleanup();
});

test('the read-nothing warning leads the panel, not only the confirm bar at the bottom', () => {
  // 17 default badges and a "0% source coverage" line is factually right and reads as a
  // contract with poor coverage. The reader meets the table first and the confirm bar last.
  const allDefault = {
    field_sources: Object.keys(params().field_sources).reduce((a, k) => { a[k] = 'default'; return a; }, {})
  };
  const vp = seed(allDefault);
  assert.match(vp.sourceNote, /no contract terms/i);
  assert.match(vp.sourceNote, /may not be a service contract/i);
  cleanup();
});

test('an ordinary partial read keeps the plain coverage line', () => {
  const vp = seed();
  assert.match(vp.sourceNote, /4 of 8 terms were read/i);
  assert.doesNotMatch(vp.sourceNote, /may not be a service contract/i);
  cleanup();
});
