// fileAffordance — which controls a document-ish row is allowed to draw.
//
// The panel used to draw a working eye and a working download on every row. Most rows have
// nothing behind them, so both were decoration, and nothing on screen distinguished a
// document we can open from one we merely know about. Three states now, and the reason they
// are three rather than two is that "no document was ever filed against this certificate"
// is a gap in the register while "the document exists and nothing is stored for it" is a
// gap in storage — only the first is a compliance finding.
import { test } from 'node:test';
import assert from 'node:assert/strict';

const { fileAffordance } = await import('../src/logic/renderVals.js');

test('something to open: both controls, at full strength', () => {
  const a = fileAffordance(true, true);
  assert.equal(a.held, true);
  assert.equal(a.evidenced, true);
  assert.equal(a.dlShow, 'inline-block');
  assert.equal(a.viewCursor, 'pointer');
  assert.equal(a.note, '', 'a row that works needs no annotation');
});

test('a document with nothing stored: dimmed, and the download is not drawn', () => {
  const a = fileAffordance(false, true);
  assert.equal(a.held, false);
  assert.equal(a.evidenced, true, 'the row still names a document');
  assert.equal(a.dlShow, 'none', 'there is nothing to download');
  assert.equal(a.viewIcon, 'ph ph-eye-slash');
  assert.equal(a.viewCursor, 'default');
  assert.match(a.note, /no file/);
});

test('no document at all: a different icon, in the warn tone', () => {
  const a = fileAffordance(false, false);
  assert.equal(a.evidenced, false);
  assert.equal(a.viewIcon, 'ph ph-file-dashed');
  assert.equal(a.viewColor, 'var(--st-warn)');
  assert.equal(a.dlShow, 'none');
  assert.match(a.note, /no document/);
});

test('no document beats no file — the register gap is the finding worth naming', () => {
  // Both false is the common case: a certificate with no document also has no file. The
  // reader needs to know which question failed first.
  assert.equal(fileAffordance(false, false).viewIcon, 'ph ph-file-dashed');
  assert.equal(fileAffordance(false, true).viewIcon, 'ph ph-eye-slash');
});

test('the labels are per branch, because a certificate has no file of its own', () => {
  const a = fileAffordance(false, true, {
    noFile: ' · no scan', noFileTitle: 'nothing stored for its document'
  });
  assert.equal(a.note, ' · no scan');
  assert.equal(a.viewTitle, 'nothing stored for its document');
});

test('null is not false: a branch that cannot answer keeps its controls', () => {
  // has_file is three-valued. A branch with no document reference at all — floors, assets,
  // work orders — reports null, and drawing that as "no file" would be a new untruth in
  // place of the old one.
  const a = fileAffordance(null, null);
  assert.equal(a.held, false, 'null is not a claim that we hold it');
  assert.equal(a.dlShow, 'inline-block', 'nor a claim that we do not');
  assert.equal(a.note, '');
  assert.notEqual(a.viewIcon, 'ph ph-file-dashed');
});

test('undefined behaves as null — an older backend must not turn every row into a gap', () => {
  // A deployment running the previous engine sends neither field. Treating that as "no
  // document" would flag the entire register as unevidenced on a version skew.
  const a = fileAffordance(undefined, undefined);
  assert.equal(a.dlShow, 'inline-block');
  assert.equal(a.note, '');
});
