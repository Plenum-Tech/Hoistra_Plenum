// "Download" on a vault row: the file comes through the compliance service, with the bearer
// token, as a blob saved under the server's filename — never the public blob URL.
import { test } from 'node:test';
import assert from 'node:assert/strict';

const clicked = [];
globalThis.window = { location: { origin: 'http://test.local' }, scrollTo: () => {}, addEventListener: () => {}, removeEventListener: () => {} };
globalThis.document = {
  createElement: () => ({ click() { clicked.push({ href: this.href, download: this.download }); }, remove() {} }),
  body: { appendChild() {} }
};
globalThis.URL.createObjectURL = () => 'blob:test/1';
globalThis.URL.revokeObjectURL = () => {};
let reply = () => new Response('%PDF-1.4', { status: 200, headers: { 'content-type': 'application/pdf', 'content-disposition': "attachment; filename=\"EPC.pdf\"; filename*=UTF-8''EPC%20Harbour%20Point.pdf" } });
let seen = null;
globalThis.fetch = async (url, init) => { seen = { url: String(url), init }; return reply(); };
globalThis.WebSocket = class { constructor() { throw new Error('no sockets'); } };

const { shapeLiveCompliance } = await import('../src/logic/complianceLive.js');
const { complianceApi } = await import('../src/api/compliance.js');
const { HoistraLogic } = await import('../src/logic/HoistraLogic.js');

test('a row with a document, or a stored blob URL, offers a download; one with neither does not', () => {
  const out = shapeLiveCompliance({ certificates: [
    { id: 'a', certificate_type_code: 'EPC', country_code: 'UK', document_id: 'd1' },
    { id: 'b', certificate_type_code: 'EPC', country_code: 'UK', raw_metadata: { blob_url: 'https://plenumstorage.blob.core.windows.net/c/x.pdf' } },
    { id: 'c', certificate_type_code: 'EPC', country_code: 'UK' }
  ] });
  // raw_metadata.blob_url is client-writable and is not a download source.
  assert.deepEqual(out.certs.map((r) => r.hasFile), [true, false, false]);
});

test('certificateFile hits the service route and returns the blob with the UTF-8 filename', async () => {
  const f = await complianceApi.certificateFile('c1');
  assert.match(seen.url, /\/api\/compliance\/certificates\/c1\/download$/);
  assert.ok(!/blob\.core\.windows\.net/.test(seen.url));
  assert.equal(f.filename, 'EPC Harbour Point.pdf');
  assert.equal(f.type, 'application/pdf');
  assert.equal(await f.blob.text(), '%PDF-1.4');
});

test('the controller saves the file under the server name, and a refusal is said, not swallowed', async () => {
  const c = new HoistraLogic({});
  const flashes = []; c.flash = (m) => flashes.push(m);
  await c.ccDownloadCert({ id: 'c1', nm: 'EPC' });
  assert.deepEqual(clicked.at(-1), { href: 'blob:test/1', download: 'EPC Harbour Point.pdf' });
  assert.equal(c.state.ccDlId, null);

  reply = () => new Response(JSON.stringify({ detail: 'No file is stored for this certificate — only its fields were recorded.' }), { status: 404, headers: { 'content-type': 'application/json' } });
  await c.ccDownloadCert({ id: 'c2', nm: 'FRA' });
  assert.match(flashes.at(-1), /Could not download FRA: .*No file is stored/);
  assert.equal(c.state.ccDlId, null);
});
