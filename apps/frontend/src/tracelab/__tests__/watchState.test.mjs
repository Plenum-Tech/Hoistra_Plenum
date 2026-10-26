// watchState — the `watch` slice of Trace Lab's store (logic/watchCommon.js): every Watch key at its default, fresh objects
// each call, tagged with the store's seq so a company switch leaves the old slice unread (that is the reset the source
// planned as tlWatchResetPatch: no key can survive a switch); the write keys; and who the person is without the module
// knowing the account: the failure-mode list's `is_owner`, with no View as.
import { test, beforeEach } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

import { C, S, Wx, META, wfresh, installDefaults, setActingOrg, tlStore, initialState, taxonomyInitial } from './watch-helper.mjs';

beforeEach(installDefaults);
const here = (rel) => readFileSync(new URL('../' + rel, import.meta.url), 'utf8');

test('the slice is fresh every time and holds only Watch keys', () => {
  const a = C.watchInitial(3);
  const b = C.watchInitial(3);
  assert.equal(a.seq, 3);
  for (const k of ['tlMonitorCharts', 'tlMonitorPlanDraft', 'tlMonitorReviewIds', 'tlCasesDrafts', 'tlReleaseDraft', 'tlWatchWrites']) assert.notEqual(a[k], b[k], k);
  assert.ok(Object.keys(a).filter((k) => k !== 'seq').every((k) => /^tl(Monitor|Cases|Release|Auto|Watch)/.test(k)), Object.keys(a).join(','));
  assert.equal(a.tlMonitorBatches, null);
  assert.equal(a.tlMonitorLoadedAt, 0);
  assert.equal(a.tlCases, null);
  assert.equal(a.tlRelease, null);
  assert.equal(a.tlAuto, null);
  assert.equal(a.tlMonitorPlanOpen, false);
  assert.deepEqual(a.tlReleaseDraft, { k: 5, include_capability: true });
  assert.deepEqual(a.tlWatchWrites, {});
  assert.equal(C.watchInitial().seq, 0);
});

test('every key the Watch files read or write is in the slice at its default (so a switch clears all of them)', () => {
  const slice = C.watchInitial(1);
  const used = new Set();
  for (const f of ['watchActions.js', 'logic/watch.js', 'logic/watchCommon.js']) {
    for (const m of here(f).matchAll(/\btl(?:Monitor|Cases|Release|Auto|Watch)[A-Za-z]*\b/g)) used.add(m[0]);
  }
  assert.ok(used.size > 25, [...used].join(' '));
  for (const key of used) assert.ok(Object.prototype.hasOwnProperty.call(slice, key), key);
});

test('a write carries its own key: one per button, with the case or batch it was made on', () => {
  assert.equal(C.watchWrite.start, 'watch:start');
  assert.equal(C.watchWrite.release, 'watch:release');
  assert.equal(C.watchWrite.resume('b1'), 'watch:resume:b1');
  assert.equal(C.watchWrite.review('b1', 'm1'), 'watch:review:b1:m1');
  for (const kind of ['confirm', 'baseline', 'repeat', 'retire']) {
    assert.equal(C.watchWrite[kind]('c1'), 'watch:' + kind + ':c1');
    assert.notEqual(C.watchWrite[kind]('c1'), C.watchWrite[kind]('c2'), kind);
  }
  assert.notEqual(C.watchWrite.review('b1', 'm1'), C.watchWrite.review('b1', 'm2'));
  const all = [C.watchWrite.start, C.watchWrite.release, C.watchWrite.resume('b'), C.watchWrite.review('b', 'm'), C.watchWrite.confirm('c'),
    C.watchWrite.baseline('c'), C.watchWrite.repeat('c'), C.watchWrite.retire('c')];
  assert.equal(new Set(all).size, all.length, 'no two buttons share a key');
  assert.ok(all.every((k) => k.startsWith('watch:')));
});

test('watchOf hands back the slice made under the store\'s seq, and an untouched one for an older seq or none', () => {
  wfresh({ tlMonitorBatchId: 'b1' });
  assert.equal(Wx().tlMonitorBatchId, 'b1');
  const seq = S().seq;
  assert.equal(C.watchOf(S()).seq, seq);
  tlStore.set({ seq: seq + 1 });
  assert.equal(C.watchOf(S()).tlMonitorBatchId, null, 'a company switch (the seq moved) empties it');
  assert.equal(C.watchOf(S()).seq, seq + 1);
  assert.equal(C.watchOf(Object.assign(initialState(), { watch: undefined })).tlMonitorBatchId, null);
  assert.equal(C.watchOf(null).tlMonitorBatchId, null);
});

test('the platform team is the failure-mode list\'s owner with no View as; a company is a reader the service has named; nobody is either until it has', () => {
  wfresh({}, { owner: true });
  assert.deepEqual([C.isPlatform(S()), C.inCompany(S())], [true, false]);
  wfresh({}, { owner: false });
  assert.deepEqual([C.isPlatform(S()), C.inCompany(S())], [false, true], 'a company admin');
  wfresh({}, { owner: null });
  assert.deepEqual([C.isPlatform(S()), C.inCompany(S())], [false, false], 'not read yet: nothing owner-only or company-only is asked for');
  setActingOrg('org-viewed');
  wfresh({}, { owner: true });
  assert.deepEqual([C.isPlatform(S()), C.inCompany(S())], [false, true], 'viewing a company wins over a list read before it began');
  wfresh({}, { owner: null });
  assert.deepEqual([C.isPlatform(S()), C.inCompany(S())], [false, false], 'but not before the list has been read for this company');
  setActingOrg(null);
  const seq = S().seq;
  const older = Object.assign(initialState(), { seq: seq + 1, taxonomy: Object.assign(taxonomyInitial(seq), { tlModesMeta: META(true) }) });
  assert.equal(C.isPlatform(older), false, 'a list read under an earlier company is not this company\'s answer');
  assert.equal(C.isPlatform(null), false);
  assert.equal(C.inCompany(null), false);
  wfresh({}, { owner: true });
  assert.equal(C.known(S()), true);
  wfresh({}, { owner: null });
  assert.equal(C.known(S()), false);
  assert.equal(C.known(null), false);
});

test('the platform team that may start monitoring is any superadmin, with or without a company in view; a company admin never is', () => {
  wfresh({}, { owner: true });
  assert.equal(C.canStart(S()), true, 'a superadmin viewing every company');
  wfresh({}, { owner: false });
  assert.equal(C.canStart(S()), false, 'a company admin');
  wfresh({}, { owner: null });
  assert.equal(C.canStart(S()), false, 'not read yet: nothing owner-only is offered');
  setActingOrg('org-viewed');
  wfresh({}, { owner: false });                       // the service answers is_owner false for a company it was told to act for
  assert.equal(C.canStart(S()), true, 'a superadmin viewing a company: the list says no owner, the View as says superadmin');
  wfresh({}, { owner: true });
  assert.equal(C.canStart(S()), true, 'a list read before the View as began does not take it away');
  wfresh({}, { owner: null });
  assert.equal(C.canStart(S()), false, 'but not before the service has said who the person is for this company');
  setActingOrg(null);
  assert.equal(C.canStart(null), false);
  const seq = S().seq;
  const older = Object.assign(initialState(), { seq: seq + 1, taxonomy: Object.assign(taxonomyInitial(seq), { tlModesMeta: META(true) }) });
  assert.equal(C.canStart(older), false, 'a list read under an earlier company is not this company\'s answer');
});

test('who is viewing one company is the View as, and a company admin is not', () => {
  assert.equal(C.viewsCompany(null), false);
  wfresh({}, { owner: false });
  assert.equal(C.viewsCompany(S()), false, 'a company admin works in their company without viewing it as anyone');
  setActingOrg('org-viewed');
  assert.equal(C.viewsCompany(S()), true);
  wfresh({}, { owner: null });
  assert.equal(C.viewsCompany(S()), false, 'not before the service has said who the person is');
});
