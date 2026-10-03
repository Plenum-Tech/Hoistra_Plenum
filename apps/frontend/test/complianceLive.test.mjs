// shapeLiveCompliance's per-row documentId — the real file behind a certificate, so a
// download link can be built without threading a whole raw API row through the UI.
import { test } from 'node:test';
import assert from 'node:assert/strict';

const { shapeLiveCompliance } = await import('../src/logic/complianceLive.js');

test('a certificate with a real document_id exposes it as documentId', () => {
  const out = shapeLiveCompliance({
    certificates: [{ id: 'c1', certificate_type_code: 'EICR', document_id: 'd-123', country_code: 'UK' }]
  });
  const row = out.certs.find((r) => r.id === 'c1');
  assert.equal(row.documentId, 'd-123');
  assert.equal(row.doc, true, 'doc stays true when a document is present');
});

test('linked_documents is used when document_id itself is absent', () => {
  const out = shapeLiveCompliance({
    certificates: [{
      id: 'c2', certificate_type_code: 'FRA', country_code: 'UK',
      linked_documents: [{ document_id: 'd-456' }]
    }]
  });
  const row = out.certs.find((r) => r.id === 'c2');
  assert.equal(row.documentId, 'd-456');
});

test('a certificate with no document behind it exposes documentId as null', () => {
  const out = shapeLiveCompliance({
    certificates: [{ id: 'c3', certificate_type_code: 'DEC', country_code: 'UK' }]
  });
  const row = out.certs.find((r) => r.id === 'c3');
  assert.equal(row.documentId, null);
  assert.equal(row.doc, false);
});
