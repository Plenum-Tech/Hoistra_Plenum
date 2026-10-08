// publicText: text from a backend or an agent is shown without the platform's internal names
// (7 Oct 2026).
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { scrubInternal } from '../src/logic/publicText.js';

test('a backend error reads without its service, schema, route or source file', () => {
  assert.equal(scrubInternal('plenum_cafm.buildings is absent — run the migration.'), 'buildings is absent — run the migration.');
  assert.equal(scrubInternal('No completed work orders found in plenum_cafm.work_orders for filters'), 'No completed work orders found in work orders for filters');
  assert.equal(scrubInternal('Could not read (GET /api/contract-performance/wo-scores: 500)'), 'Could not read (the request: 500)');
  assert.equal(scrubInternal('svc-udr did not answer'), 'the platform did not answer');
  assert.equal(scrubInternal('see engines/auth/access.py'), 'see the platform');
  assert.equal(scrubInternal('UDR request failed (502)'), 'building data request failed (502)');
  assert.equal(scrubInternal('Ops-intelligence timed out'), 'the platform timed out');
});

test("an answer that cites the assistant's own skill documents does not name them", () => {
  assert.equal(scrubInternal('Per skills/compliance/tool-routing.md and SKILL.md, the FRA lapsed.'),
    "Per the assistant's guidance and the assistant's guidance, the FRA lapsed.");
  assert.equal(scrubInternal('as answering.md says'), "as the assistant's guidance says");
  // An address is not a document.
  assert.equal(scrubInternal('write to ops@site.md'), 'write to ops@site.md');
});

test('ordinary prose, numbers and product names pass through untouched', () => {
  const s = 'Hoist Graph: 32 certificates, EUI 241.0 kWh/m² — 4 inside 30 days (CIBSE TM46).';
  assert.equal(scrubInternal(s), s);
  assert.equal(scrubInternal(null), null);
  assert.equal(scrubInternal(''), '');
});

// 8 Oct 2026 review: the rules matched far more than the platform's own names.
test("a customer's own references are not taken for a service name", () => {
  for (const s of [
    'Part GEN-SVC-001 is needed for GEN-1',       // a part number the work-order engine returns
    'Work order SVC-2024-118 raised',
    'Contact svc-alerts@acme.co.uk about it',       // an address
    'Use the deep agent approach the vendor proposed'
  ]) assert.equal(scrubInternal(s), s);
  // The platform's own names still go.
  assert.equal(scrubInternal('svc-operations-intelligence timed out'), 'the platform timed out');
  assert.equal(scrubInternal('svc-deepagents and deepagents restarted'), 'the platform and the assistant restarted');
});

test('a link keeps working: a URL is passed through whole', () => {
  const u = 'Download it from https://hoistra.example/backend/doc-rag/api/v1/documents/1/download today.';
  assert.equal(scrubInternal(u), u);
});

test('code keeps valid identifiers: the schema goes, the table name stays as written', () => {
  assert.equal(scrubInternal('SELECT * FROM plenum_cafm.work_orders', { code: true }), 'SELECT * FROM work_orders');
  assert.equal(scrubInternal('SELECT * FROM plenum_cafm.work_orders'), 'SELECT * FROM work orders');
});

test('long unbroken text is scrubbed in linear time', () => {
  for (const s of ['a'.repeat(40000), 'ab/'.repeat(14000), 'x-'.repeat(20000)]) {
    const t0 = Date.now();
    scrubInternal(s);
    assert.ok(Date.now() - t0 < 150, 'took ' + (Date.now() - t0) + ' ms on a ' + s.length + '-char run');
  }
});

test("an API error is shown scrubbed but keeps the service's own words for the activity log", async () => {
  globalThis.window = globalThis.window || { location: { origin: 'http://test.local' } };
  const { apiFetch } = await import('../src/api/client.js');
  const prev = globalThis.fetch;
  globalThis.fetch = async () => ({ ok: false, status: 502, statusText: 'Bad Gateway',
    text: async () => JSON.stringify({ detail: 'svc-udr did not answer' }) });
  try {
    await assert.rejects(apiFetch('http://test.local/backend/x', '/api/y', { auth: false }), (e) => {
      assert.equal(e.message, 'the platform did not answer');
      assert.equal(e.rawMessage, 'svc-udr did not answer');
      return true;
    });
  } finally { globalThis.fetch = prev; }
});

// ── 8 Oct 2026 review ─────────────────────────────────────────────────────────────
test('a service named whole, in capitals or with its worker suffix, is not half-scrubbed', () => {
  assert.equal(scrubInternal('svc-ai-schema-mapper-worker crashed'), 'the platform crashed');
  assert.equal(scrubInternal('SVC-UDR is unreachable'), 'the platform is unreachable');
  assert.equal(scrubInternal('ai-schema-mapper failed'), 'the data importer failed');
  // Still not a customer's own reference.
  assert.equal(scrubInternal('Work order SVC-2024-118 raised'), 'Work order SVC-2024-118 raised');
});

test("a link to one of the platform's own hosts is not a link the reader can follow", () => {
  assert.equal(scrubInternal('Failed: http://svc-udr:8006/api/x returned 500'), 'Failed: the platform returned 500');
  assert.equal(scrubInternal('see http://localhost:8010/health'), 'see the platform');
  assert.equal(scrubInternal('at http://127.0.0.1:8003/migrations'), 'at the platform');
  // Real links still pass whole: a register's verify page, a signed document link.
  for (const u of ['Check https://www.gov.uk/check-gas-safe today', 'Open https://plenumstorage.blob.core.windows.net/c/a.pdf?sig=x'])
    assert.equal(scrubInternal(u), u);
});

test('the scrubber loads on browsers without regex lookbehind (Safari before 16.4)', async () => {
  // client.js imports it, so a syntax error there is a blank app, not one bad string.
  const { readFile } = await import('node:fs/promises');
  const src = await readFile(new URL('../src/logic/publicText.js', import.meta.url), 'utf8');
  assert.doesNotMatch(src.replace(/^\s*\/\/.*$/gm, ''), /\(\?<[!=]/);
});
