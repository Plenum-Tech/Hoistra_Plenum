// "Terms not confirmed" in the vendor directory, and the jump to the Confirm terms button.
//
// 28 Sep 2026: Bishopsgate's six contracts were ingested as drafts on purpose, and scoring
// refuses a draft, so four of the six vendors read "not scored" with nothing on the row to
// say why. The only way to find out was to open each vendor and read to the bottom of its
// Contract terms tab. Now a draft says so on the directory row, and the note takes the reader
// straight to the button — without confirming, or arming the confirmation, on their behalf.
import { test, beforeEach } from 'node:test';
import assert from 'node:assert/strict';

globalThis.window = { location: { origin: 'http://test.local' }, scrollTo: () => {} };
let calls = 0;
globalThis.fetch = () => { calls += 1; return Promise.reject(new TypeError('Failed to fetch')); };
globalThis.WebSocket = class { constructor() { throw new Error('no sockets in tests'); } };
globalThis.window.addEventListener = () => {};
globalThis.window.removeEventListener = () => {};

const { HoistraLogic } = await import('../src/logic/HoistraLogic.js');

const DRAFT = '00000000-0000-0000-0000-0000000000d1';
const CONFIRMED = '00000000-0000-0000-0000-0000000000c1';
const NOTHING = '00000000-0000-0000-0000-0000000000e1';
const BARE = '00000000-0000-0000-0000-0000000000f1';
const WEIGHTS = { sla_response_pct: 25, sla_completion_pct: 25, first_fix_pct: 20, recall_pct: 15, accreditation_pct: 15, blocked_score_cap: 60 };

const params = (id, vendor_id, vendor_name, status, sources) => ({
  id, vendor_id, vendor_name, contract_ref: vendor_name + ' contract', signed_date: '2025-02-01', status,
  sla_response_p1_hours: 2, labour_hour_rate: 72, field_sources: sources
});
const raw = () => ({
  fetchedAt: '2026-09-28T07:00:00Z',
  errors: {},
  summary: {
    ok: true, pending_approvals: 0, weights: WEIGHTS,
    scorecards: [{
      id: 'card', vendor_id: BARE, vendor_name: 'Scored With No Terms', score_month: '2026-09-01', overall_score: 80, trend_delta: 0,
      component_breakdown: { weights_snapshot: WEIGHTS }, ppm_compliance_pct: null, matched_flagged_ratio: 1, block_capped: false, wo_score_ids: []
    }]
  },
  contracts: { ok: true, parameters: [
    params('p-draft', DRAFT, 'Northgate Electrical', 'draft', { contract_ref: 'contract', sla_response_p1_hours: 'contract', labour_hour_rate: 'default' }),
    params('p-conf', CONFIRMED, 'Apex Lifts', 'confirmed', { contract_ref: 'contract', sla_response_p1_hours: 'contract', labour_hour_rate: 'contract' }),
    params('p-none', NOTHING, 'Moreland', 'draft', { contract_ref: 'default', sla_response_p1_hours: 'default', labour_hour_rate: 'default' })
  ] },
  weights: { ok: true, weights: WEIGHTS },
  approvals: { ok: true, count: 0, items: [] },
  certificates: [], coverage: {}, packs: {}
});

let c;
beforeEach(() => {
  calls = 0;
  c = new HoistraLogic();
  c.setState({ signedIn: true, view: 'vp', vpRaw: raw(), vpVendor: CONFIRMED, vpTab: 0 });
});
const cleanup = () => { clearInterval(c._orchTick); clearTimeout(c._tt); clearTimeout(c._vpRetry); clearTimeout(c._vpRefresh); clearTimeout(c._vpFlashT); clearTimeout(c._vpJumpT); };
const row = (name) => c.renderVals().vpList.find((v) => v.name === name);

test('a draft contract says so on its directory row', () => {
  const r = row('Northgate Electrical');
  assert.equal(r.termsShow, 'inline-flex');
  assert.match(r.termsNote, /Terms not confirmed/);
  assert.match(r.termsTitle, /scoring is blocked/i);
  cleanup();
});

test('a confirmed contract, and a vendor with no contract at all, carry no note', () => {
  assert.equal(row('Apex Lifts').termsShow, 'none');
  assert.equal(row('Scored With No Terms').termsShow, 'none', 'with no contract there is nothing to confirm');
  cleanup();
});

test('a draft that read nothing from its document says that instead', () => {
  assert.match(row('Moreland').termsNote, /No terms read/);
  cleanup();
});

test('the note opens that vendor on Contract terms, with the confirm bar lit', () => {
  let stopped = false;
  row('Northgate Electrical').goConfirm({ stopPropagation: () => { stopped = true; } });
  assert.equal(stopped, true, 'the row underneath must not also handle the click and reset the tab');
  assert.equal(c.state.vpVendor, DRAFT);
  assert.equal(c.state.vpTab, 1);
  const vp = c.renderVals().vp;
  assert.equal(vp.confirmShow, 'flex');
  assert.equal(vp.confirmFlash, true);
  cleanup();
});

test('the jump never confirms, and never arms the confirmation', () => {
  row('Northgate Electrical').goConfirm({ stopPropagation: () => {} });
  assert.equal(c.renderVals().vp.confirmArmed, false, 'confirming stays two deliberate clicks on the panel');
  assert.equal(calls, 0, 'nothing is sent');
  cleanup();
});

test('the light belongs to the vendor it was lit for', () => {
  row('Northgate Electrical').goConfirm({ stopPropagation: () => {} });
  c.setState({ vpVendor: NOTHING });
  assert.equal(c.renderVals().vp.confirmFlash, false);
  cleanup();
});

test('a half-typed edit on the previous vendor is not carried across', () => {
  c.setState({ vpEditField: 'labour_hour_rate', vpEditValue: '99' });
  row('Northgate Electrical').goConfirm({ stopPropagation: () => {} });
  assert.equal(c.state.vpEditField, '');
  cleanup();
});
