// "Download all" opens each file once.
//
// The building tree nests certificates under the documents they were read from, so a
// certificate's document_id is usually also a held row in the Documents branch. The two
// lists were concatenated, so one PDF that yielded one certificate opened twice and the
// flash said "Downloading 2 files" — two popups for one file, one of them blocked, and
// advice to allow pop-ups for a problem that did not exist.
import { test } from 'node:test';
import assert from 'node:assert/strict';

globalThis.window = { location: { origin: 'http://test.local' }, addEventListener: () => {}, removeEventListener: () => {}, localStorage: { getItem: () => null, setItem() {}, removeItem() {} } };
const { downloadIds } = await import('../src/logic/renderVals.js');

test('a certificate whose document is already a held row is not opened twice', () => {
  const files = [{ held: true, docId: 'doc-1' }, { held: false, docId: 'doc-2' }];
  const certs = [{ has_file: true, document_id: 'doc-1' }, { has_file: true, document_id: 'doc-3' }];
  assert.deepEqual(downloadIds(files, certs), ['doc-1', 'doc-3']);
});

test('rows with nothing stored contribute nothing', () => {
  assert.deepEqual(downloadIds([{ held: false, docId: 'doc-2' }], [{ has_file: false, document_id: 'doc-4' }, { has_file: true, document_id: null }]), []);
});

test('order is documents first, then certificate-only files, each once', () => {
  const files = [{ held: true, docId: 'a' }, { held: true, docId: 'b' }];
  const certs = [{ has_file: true, document_id: 'c' }, { has_file: true, document_id: 'b' }, { has_file: true, document_id: 'c' }];
  assert.deepEqual(downloadIds(files, certs), ['a', 'b', 'c']);
});
