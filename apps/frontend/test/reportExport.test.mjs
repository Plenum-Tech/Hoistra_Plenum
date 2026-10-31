// The report page's Export button saves a PDF (8 Oct 2026), built from the same cards the page
// draws - including any put in the tray, which hides a card from the page, not from the answer.
import { test } from 'node:test';
import assert from 'node:assert/strict';

const mem = {};
const saved = [];
let lastBlob = null;
globalThis.window = {
  location: { origin: 'http://test.local' }, scrollTo: () => {}, addEventListener: () => {}, removeEventListener: () => {},
  localStorage: { getItem: (k) => (k in mem ? mem[k] : null), setItem: (k, v) => { mem[k] = String(v); }, removeItem: (k) => { delete mem[k]; } },
  URL: { createObjectURL: (b) => { lastBlob = b; return 'blob:test'; }, revokeObjectURL: () => {} }
};
globalThis.document = {
  body: { appendChild: () => {} },
  createElement: () => { const a = { click: () => saved.push({ name: a.download, blob: lastBlob }), remove: () => {} }; return a; }
};
globalThis.fetch = () => Promise.reject(new TypeError('Failed to fetch'));
globalThis.WebSocket = class { constructor() { throw new Error('no sockets in tests'); } };

const { HoistraLogic } = await import('../src/logic/HoistraLogic.js');
const ME = 'husain@plenum.example';
const RUN = {
  ran_at: '2026-10-08T12:52:00Z', duration_ms: 45000, ok: true, answer: '', tool_calls: [{ tool: 'list_compliance_approvals' }],
  rich: {
    narrative: 'Three vendors are blocked right now.',
    kpis: [{ label: 'Vendors blocked', count: 3, sublabel: 'Blocked on a lapsed accreditation' }],
    certificates: [{ name: 'NICEIC Approved Contractor', company: 'Ostley Power Services', scope: 'vendor', status: 'Lapsed', severity: 'critical' }]
  }
};
const CARD = { id: 'card-1', name: 'Which vendors are blocked right now?', prompt: 'Which vendors are blocked right now?', refresh_label: 'every 1 hour', runs: [RUN], latest_run: RUN };

test('Export saves a .pdf of the refresh in view, tray cards included, tool names left out', async () => {
  const c = new HoistraLogic({});
  c.setState({ signedIn: true, view: 'report', account: { email: ME, full_name: 'Husain Kalabhai', organization_name: 'Plenum Technologies' },
    reportsOwner: ME, reports: [{ id: 'r1', name: 'Mine', cards: [CARD] }], reportKey: CARD.id });
  const v = c.renderVals();
  const narrative = v.reportBlocks.find((b) => b.kind === 'narrative');
  narrative.hide();
  assert.ok(!c.renderVals().reportBlocks.some((b) => b.kind === 'narrative'), 'put in the tray');
  c.renderVals().exportReport();
  assert.equal(saved.length, 1);
  assert.equal(saved[0].name, 'which-vendors-are-blocked-right-now.pdf');
  assert.equal(saved[0].blob.type, 'application/pdf');
  const text = Buffer.from(await saved[0].blob.arrayBuffer()).toString('latin1');
  assert.ok(text.startsWith('%PDF-1.4'));
  assert.match(text, /\(Three\) Tj/, 'the assessment put in the tray is still in the export');
  assert.match(text, /\(NICEIC Approved Contractor\) Tj/);
  assert.doesNotMatch(text, /list_compliance_approvals/);
  clearInterval(c._orchTick); clearTimeout(c._tt);
});
