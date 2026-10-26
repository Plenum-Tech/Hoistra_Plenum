// judgesState — the `judges` slice of Trace Lab's store (logic/judgesCommon.js): every Judges key at its default, fresh
// objects each call, tagged with the store's seq so a company switch leaves the old slice unread; and who the person
// is (the platform team) without the module knowing the account: the failure-mode list's `is_owner`, with no View as.
import { test, beforeEach } from 'node:test';
import assert from 'node:assert/strict';

import { C, S, Jx, META, jfresh, installDefaults, setActingOrg, tlStore, initialState, taxonomyInitial } from './judges-helper.mjs';

beforeEach(installDefaults);

test('the slice is fresh every time and holds only Judges keys', () => {
  const a = C.judgesInitial(3);
  const b = C.judgesInitial(3);
  assert.equal(a.seq, 3);
  assert.notEqual(a.tlJudgeSplitForm, b.tlJudgeSplitForm);
  assert.notEqual(a.tlJudgePreview, b.tlJudgePreview);
  assert.notEqual(a.tlJudgeDecisionDraft, b.tlJudgeDecisionDraft);
  assert.notEqual(a.tlJudgeWrites, b.tlJudgeWrites);
  assert.ok(Object.keys(a).filter((k) => k !== 'seq').every((k) => k.startsWith('tlJudge')), Object.keys(a).join(','));
  assert.equal(a.tlJudgesRows, null);
  assert.equal(a.tlJudgesChecks, null);
  assert.equal(a.tlJudgeId, null);
  assert.equal(a.tlJudgeModeId, null);
  assert.deepEqual(a.tlJudgeDecisionDraft, { decision: '', note: '' });
  assert.deepEqual(a.tlJudgeSplitForm, { override: false, reason: '' });
  assert.deepEqual(a.tlJudgeWrites, {});
  assert.equal(C.judgesInitial().seq, 0);
});

test('a write that belongs to one judge\'s page carries that judge\'s id (a triage, its run\'s), and nothing else does', () => {
  assert.equal(C.judgeWrite.preview('j-2', 'td-1'), 'judges:preview:j-2:td-1');
  assert.equal(C.judgeWrite.triage('r-dev', 'turn-9'), 'judges:triage:r-dev:turn-9');
  for (const kind of ['decision', 'threshold', 'freeze', 'run']) {
    assert.equal(C.judgeWrite[kind]('j-2'), 'judges:' + kind + ':j-2');
    assert.notEqual(C.judgeWrite[kind]('j-2'), C.judgeWrite[kind]('j-9'), kind + ' differs from one judge to the next');
  }
  assert.notEqual(C.judgeWrite.preview('j-2', 'td-1'), C.judgeWrite.preview('j-9', 'td-1'), 'the same dev answer on two judges');
  assert.notEqual(C.judgeWrite.triage('r-1', 'turn-9'), C.judgeWrite.triage('r-2', 'turn-9'), 'the same answer in two runs');
  const page = [C.judgeWrite.preview('j-2', 'td-1'), C.judgeWrite.triage('r-dev', 'turn-9'), C.judgeWrite.decision('j-2'),
    C.judgeWrite.threshold('j-2'), C.judgeWrite.freeze('j-2'), C.judgeWrite.run('j-2')];
  assert.deepEqual(page.map(C.isJudgeWrite), page.map(() => true));
  // The failure mode's own writes and the report file are not a judge page's: they survive opening another judge.
  assert.deepEqual(['judges:split:m-1', 'judges:register:m-1', 'judges:report-file', 'judges:probe'].map(C.isJudgeWrite), [false, false, false, false]);
});

test('a page and a card reset to their own defaults and nothing else', () => {
  const page = C.judgePage();
  const card = C.modeCard();
  const all = C.judgesInitial(1);
  Object.keys(page).concat(Object.keys(card)).forEach((k) => assert.ok(k in all, k));
  assert.equal(page.tlJudge, null);
  assert.deepEqual(page.tlJudgePreview, {});
  assert.equal(card.tlJudgeSplit, null);
  assert.deepEqual(card.tlJudgeWarnings, []);
  assert.equal('tlJudgeId' in page || 'tlJudgeModeId' in card, false, 'which judge or mode is open is the caller\'s to set');
});

test('judgesOf hands back the slice made under the store\'s seq, and an untouched one for an older seq or none', () => {
  jfresh({ tlJudgeId: 'j-1' });
  assert.equal(Jx().tlJudgeId, 'j-1');
  const seq = S().seq;
  assert.equal(C.judgesOf(S()).seq, seq);
  tlStore.set({ seq: seq + 1 });
  assert.equal(C.judgesOf(S()).tlJudgeId, null, 'a company switch (the seq moved) empties it');
  assert.equal(C.judgesOf(S()).seq, seq + 1);
  assert.equal(C.judgesOf(Object.assign(initialState(), { judges: undefined })).tlJudgeId, null);
  assert.equal(C.judgesOf(null).tlJudgeId, null);
});

test('the platform team is the failure-mode list\'s owner with no View as; nobody is until the list is read', () => {
  jfresh({}, { owner: true });
  assert.equal(C.isOwner(S()), true);
  jfresh({}, { owner: false });
  assert.equal(C.isOwner(S()), false);
  jfresh({}, { owner: null });
  assert.equal(C.isOwner(S()), false, 'not read yet');
  setActingOrg('org-viewed');
  jfresh({}, { owner: true });
  assert.equal(C.isOwner(S()), false, 'viewing a company wins over a list read before it began');
  setActingOrg(null);
  assert.equal(C.isOwner(S()), true);
  const seq = S().seq;
  assert.equal(C.isOwner(Object.assign(initialState(), { seq, taxonomy: Object.assign(taxonomyInitial(seq), { tlModesMeta: META(true) }) })), true);
  assert.equal(C.isOwner(Object.assign(initialState(), { seq: seq + 1, taxonomy: Object.assign(taxonomyInitial(seq), { tlModesMeta: META(true) }) })), false,
    'a list read under an earlier company is not this company\'s answer');
  assert.equal(C.isOwner(null), false);
});
