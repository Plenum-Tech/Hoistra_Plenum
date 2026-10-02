// documentsRegister — Manage documents: the rows, the actions, personal uploads flagged.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { dmVals } from '../src/logic/documentsRegister.js';

const TYPES = [{ key: 'staff_list', label: 'Staff / operatives list', group: 'People on site', personal: false },
  { key: 'visa', label: 'Visa', group: 'People on site', personal: true }, { key: 'other', label: 'Other', group: 'Other', personal: false }];

test('the dialog lists every document with how it reached the building, and personal uploads are flagged', () => {
  const calls = [];
  const c = {
    state: { dmBuildingId: 'b1', dmBuildingName: 'Bishopsgate Tower', dmCanManage: true, dmTypes: TYPES, dmUploadType: 'visa',
      dmRows: [{ document_id: 'd1', title: 'Access list', type: 'staff_list', personal: false, indexed: false, how: 'linked here' },
               { document_id: 'd2', title: 'Visa — A. Khan', type: 'visa', personal: true, indexed: false, how: 'filed here' }],
      bldLive: [{ buildingId: 'b1', name: 'Bishopsgate Tower' }, { buildingId: 'b2', name: 'Harbour Point', code: 'B-101' }],
      dmLinkTo: { d1: 'b2' } },
    dmSetType: (...a) => calls.push(['type', ...a]), dmLink: (...a) => calls.push(['link', ...a]),
    dmUnlink: (...a) => calls.push(['unlink', ...a]), dmIndex: (...a) => calls.push(['index', ...a]),
    dmSetPersonal: (...a) => calls.push(['personal', ...a])
  };
  const v = dmVals(c);
  assert.equal(v.dmShow, true);
  assert.equal(v.dmUploadPersonal, true, 'a visa upload is filed personal');
  assert.deepEqual(v.dmOthers, [{ id: 'b2', name: 'Harbour Point · B-101' }], 'link targets exclude this building');
  assert.equal(v.dmRows[0].linkedHere, true);
  v.dmRows[0].link(); v.dmRows[0].unlink(); v.dmRows[0].index(); v.dmRows[1].togglePersonal();
  v.dmRows[0].setType({ target: { value: 'other' } });
  assert.deepEqual(calls, [['link', 'd1', 'b2'], ['unlink', 'd1'], ['index', 'd1'], ['personal', 'd2', false], ['type', 'd1', 'other']]);
});
