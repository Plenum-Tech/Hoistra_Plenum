// taxonomy — Trace Lab's failure modes in the browser: the mode drawer and its dialogs, reviewers' unfiled notes, the
// requirement register and the Progress rows (logic/taxonomy.js view models, taxonomyActions.js actions). The Labels
// grid is pinned in taxonomyLabels.test.mjs, the AI suggestion queue and proposal runs in taxonomyProposals.test.mjs
// and the suggestions on an answer in taxonomySuggest.test.mjs.
import { test, beforeEach, afterEach } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync, readdirSync } from 'node:fs';

import { net, taxonomyApi, reviewApi, A, C, samplingInitial, tlStore, initialState, resetForCompany, setActingOrg, P, S, X, tick, fresh,
  SERVER, META, MODE, loaded, refusal, reads, installDefaults, restoreApis } from './tax-helper.mjs';
const T = await import('../logic/taxonomy.js');
const vals = () => T.taxonomyModesVals(S(), A);

beforeEach(installDefaults);
afterEach(restoreApis);

test('agent and level choices come from the server, and the module names no agent of its own', async () => {
  taxonomyApi.modes = async (q) => { assert.deepEqual(q, { include_retired: true }); return Object.assign({ modes: [MODE] }, SERVER); };
  await A.modesLoad();
  assert.equal(X().tlModesMeta.isOwner, true, 'the service says who the person is');
  assert.equal(A.modeDialog('create', { createdFrom: ['a-9'] }), true);
  const d = vals().tlTaxDialog;
  assert.deepEqual(d.agents, [{ value: 'wo_engine', label: 'Work orders' }, { value: 'udr', label: 'Records lookup' }]);
  assert.deepEqual(d.levels.map((l) => l.value), ['technical', 'data']);
  const dir = new URL('..', import.meta.url);
  const files = ['taxonomyActions.js', 'taxonomyApi.js'].concat(readdirSync(new URL('logic/', dir)).filter((f) => /^tax/.test(f)).map((f) => 'logic/' + f));
  assert.ok(files.length >= 4, files.join(','));
  for (const f of files) {
    const src = readFileSync(new URL(f, dir), 'utf8');
    // Built at run time, so this file itself stays clean of the app's internal-names scan.
    const agents = ['wo_engine', 'udr', ['doc', 'rag'].join('_'), 'energy_intelligence', 'contract_performance'];
    for (const agent of agents) assert.equal(src.includes("'" + agent + "'"), false, f + ' ' + agent);
  }
});

test('only the platform team opens the taxonomy dialogs; a superadmin viewing as a company is told to stop', () => {
  fresh(loaded({}, false));
  assert.equal(A.modeDialog('create', { createdFrom: ['a-1'] }), false);
  assert.equal(A.modeDialog('merge', { modeId: 'm-1' }), false);
  assert.equal(A.modeDialog('file', { annotationId: 'a-1', turnId: 't1' }), true);
  assert.equal(vals().tlTaxOwnerHint, '');
  assert.equal(vals().tlTaxOwner, false);
  setActingOrg('org-1');
  fresh(loaded({}, false));
  assert.equal(A.modeDialog('edit', { modeId: 'm-1' }), false);
  assert.equal(vals().tlTaxOwnerHint, 'Stop viewing as a company to change failure modes.');
  // A failure-mode list read before the person began viewing as a company still says "platform team": viewing wins.
  fresh(loaded({}, true));
  assert.equal(vals().tlTaxOwner, false);
  assert.equal(A.modeDialog('edit', { modeId: 'm-1' }), false);
  assert.equal(C.hasCompany(S()), true);
  setActingOrg(null);
  fresh(loaded());
  assert.equal(vals().tlTaxOwner, true);
  assert.equal(C.hasCompany(S()), false);
  assert.equal(A.modeDialog('edit', { modeId: 'm-1' }), true);
  fresh();
  assert.equal(A.modeDialog('create', { createdFrom: ['a-1'] }), false, 'nobody is the platform team until the service says so');
});

test('merge and split copy carries the fix test and the same-fix guidance', () => {
  fresh(loaded({ tlModesDrawerId: 'm-1', tlModesDrawer: { mode: MODE, notes: [] } }));
  for (const kind of ['merge', 'split']) {
    A.modeDialog(kind, { modeId: 'm-1' });
    const d = vals().tlTaxDialog;
    assert.equal(d.showGuidance, true, kind);
    assert.equal(d.guidance, 'Two notes share a mode only if the same fix clears both.');
    assert.equal(d.showFixTest, true, kind);
    assert.equal(d.fixTest, 'Does fixing one fix the other?');
    assert.match(d.fixTestText, /Merge when one product change would fix both/);
  }
});

test('Confirm stays disabled while the contract lists missing items, and names them', () => {
  const body = { mode: MODE, contract: { ready: false, missing: ['boundary', 'positives'] }, positives: [], close_negatives: [],
    close_negative_candidates: [], notes: [], events: [] };
  fresh(loaded({ tlModesDrawerId: 'm-1', tlModesDrawer: body }));
  const d = vals().tlTaxDrawer;
  assert.equal(d.actions.confirm.enabled, false);
  assert.deepEqual(d.contract.missingTexts, ['A boundary: how it differs from the nearest failure mode',
    'Three answers where it is present, each confirmed one at a time']);
  assert.deepEqual(d.contract.items.map((i) => [i.key, i.ok]), [['boundary', false], ['evaluator_type', true], ['requirement', true],
    ['positives', false], ['close_negatives', true]]);
  fresh(loaded({ tlModesDrawerId: 'm-1', tlModesDrawer: Object.assign({}, body, { contract: { ready: true, missing: [] } }) }));
  assert.equal(vals().tlTaxDrawer.actions.confirm.enabled, true);
  fresh(loaded({ tlModesDrawerId: 'm-1', tlModesDrawer: body }, false));
  assert.equal(vals().tlTaxDrawer.actions, null);
});

test('a refused confirm says what is missing; a data-fault mode says where it goes', async () => {
  taxonomyApi.editMode = async () => { throw refusal(422, 'contract_incomplete', 'This failure mode is not ready to confirm yet.', { missing: ['requirement'] }); };
  fresh(loaded({ tlModesDrawerId: 'm-1', tlModesDrawer: { mode: MODE } }));
  assert.equal(await A.modeConfirm('m-1'), false);
  assert.equal(X().tlModesDrawerErr, 'This failure mode is not ready to confirm yet. Missing: Cites an active requirement.');
  fresh(loaded({ tlModesDrawerId: 'm-2', tlModesDrawer: { mode: Object.assign({}, MODE, { id: 'm-2', level: 'data' }) } }));
  assert.equal(vals().tlTaxDrawer.mode.dataFault, 'Data fault: routed to the data owner. Not used for prompt fixes or judges.');
});

test('a new mode is created from the chosen notes; a taken name keeps the dialog open with the server’s sentence', async () => {
  const sent = [];
  taxonomyApi.createMode = async (body) => { sent.push(body); if (sent.length === 1) throw refusal(409, 'name_taken', 'A live failure mode already has this name.'); return { ok: true, mode: { id: 'm-9' }, labels_written: 2 }; };
  fresh(loaded({ tlUnfiledChosen: ['a-1', 'a-2'], tlUnfiledLoadedAt: 1 }));
  A.noteNewMode(['a-1', 'a-2']);
  assert.match(vals().tlTaxDialog.problems[0], /^Name:/);
  A.modeDialogSet({ name: 'counts_cancelled_jobs_as_open', definition: 'Present when the answer counts cancelled jobs as open.', level: 'technical', agent: 'wo_engine' });
  assert.deepEqual(vals().tlTaxDialog.problems, []);
  assert.equal(await A.modeDialogSubmit(), false);
  assert.equal(X().tlModesDialogErr, 'A live failure mode already has this name.');
  assert.ok(X().tlModesDialog);
  assert.equal(await A.modeDialogSubmit(), true);
  assert.deepEqual(sent[1], { name: 'counts_cancelled_jobs_as_open', definition: 'Present when the answer counts cancelled jobs as open.', boundary: '',
    nearest_mode_id: null, level: 'technical', agent: 'wo_engine', evaluator_type: null, requirement_ids: [], created_from: ['a-1', 'a-2'] });
  assert.equal(X().tlModesDialog, null);
  assert.equal(X().tlModesNotice, 'Failure mode created as a candidate.');
  assert.deepEqual(X().tlUnfiledChosen, []);
  assert.equal(X().tlModesDrawerId, 'm-9');
});

test('File under sends the cell’s current head, and a reviewer’s absent needs File anyway', async () => {
  const files = [];
  taxonomyApi.turnLabels = async (t) => { assert.equal(t, 't7'); return { ok: true, hidden: false, cells: [{ mode_id: 'm-1', state: 'absent', base: 'L-5' }] }; };
  taxonomyApi.fileNote = async (id, body) => {
    files.push([id, body]);
    if (!body.override_label) throw refusal(409, 'conflicts_with_label', 'A reviewer marked this failure absent on this answer. File it only if the note shows the failure.');
    return { ok: true, cell: { state: 'present' }, label: { id: 'L-6' } };
  };
  fresh(loaded({ tlUnfiled: [{ annotation_id: 'a-7', turn_id: 't7', note: 'Counted the cancelled jobs.' }], tlUnfiledLoadedAt: 1 }, false));
  A.noteFile('a-7');
  let d = vals().tlTaxDialog;
  assert.equal(d.kind, 'file');
  assert.equal(d.guidance, 'Two notes share a mode only if the same fix clears both.');
  d.fileModes[0].pick();
  assert.equal(await A.modeDialogSubmit(), false);
  d = vals().tlTaxDialog;
  assert.match(d.conflict, /^A reviewer marked this failure absent/);
  assert.equal(await d.fileAnyway(), true);
  assert.deepEqual(files, [['a-7', { mode_id: 'm-1', base: 'L-5', override_label: false }], ['a-7', { mode_id: 'm-1', base: 'L-5', override_label: true }]]);
  assert.equal(X().tlModesNotice, 'Note filed.');
});

test('split gives each note to its new mode and marks that note’s answer present there', async () => {
  const notes = [{ annotation_id: 'a-1', turn_id: 't1', note: 'Cancelled counted' }, { annotation_id: 'a-2', turn_id: 't2', note: 'On hold counted' },
    { annotation_id: 'a-3', turn_id: 't3', note: 'Filed later' }];
  let sent = null;
  taxonomyApi.splitMode = async (id, body) => { sent = [id, body]; return { ok: true, parent: { id: 'm-1' }, children: [], labels_written: 3 }; };
  fresh(loaded({ tlModesDrawerId: 'm-1', tlModesDrawer: { mode: MODE, notes } }));
  A.modeDialog('split', { modeId: 'm-1' });
  A.modeDialogChild(0, { name: 'counts_cancelled_jobs_as_open', definition: 'Present when cancelled jobs are counted as open.' });
  A.modeDialogChild(1, { name: 'counts_on_hold_jobs_as_open', definition: 'Present when jobs on hold are counted as open.' });
  A.modeDialogNote('a-1', '0');
  assert.match(vals().tlTaxDialog.problems.join(' '), /Mode 2: give it at least one of the notes/);
  A.modeDialogNote('a-2', '1');
  A.modeDialogNote('a-3', '1');
  assert.deepEqual(vals().tlTaxDialog.problems, []);
  assert.equal(await A.modeDialogSubmit(), true);
  assert.equal(sent[0], 'm-1');
  assert.equal(sent[1].base_version, 3);
  assert.deepEqual(sent[1].children.map((k) => [k.name, k.created_from, k.level, k.agent]), [
    ['counts_cancelled_jobs_as_open', ['a-1'], 'technical', 'wo_engine'], ['counts_on_hold_jobs_as_open', ['a-2'], 'technical', 'wo_engine']]);
  assert.deepEqual(sent[1].assignments, { t1: [0], t2: [1], t3: [1] });
  assert.equal(X().tlModesDrawerId, null);
});

test('the treemap squarifies inside its box, and the map lists unseen and retired modes', () => {
  const rects = T.treemap([{ id: 'a', value: 1 }, { id: 'b', value: 1 }, { id: 'c', value: 1 }, { id: 'd', value: 1 }], 100, 100);
  assert.deepEqual(rects.map((r) => [r.w, r.h]), [[50, 50], [50, 50], [50, 50], [50, 50]]);
  const wide = T.treemap([{ id: 'a', value: 6 }, { id: 'b', value: 3 }, { id: 'c', value: 1 }], 640, 220);
  const area = wide.reduce((n, r) => n + r.w * r.h, 0);
  assert.ok(Math.abs(area - 640 * 220) < 640 * 220 * 0.01);
  wide.forEach((r) => { assert.ok(r.x >= 0 && r.y >= 0 && r.x + r.w <= 640.2 && r.y + r.h <= 220.2, JSON.stringify(r)); });
  assert.deepEqual(T.treemap([{ id: 'z', value: 0 }], 10, 10), []);
  const modes = [MODE, Object.assign({}, MODE, { id: 'm-2', name: 'invents_asset_codes', counts: { present: 0 }, status: 'confirmed' }),
    Object.assign({}, MODE, { id: 'm-3', name: 'old_mode', status: 'retired', merged_into: 'm-1', live_mode_id: 'm-1', counts: null })];
  fresh(loaded({ tlModes: modes }));
  const map = vals().tlTaxMap;
  assert.deepEqual(map.blocks.map((b) => [b.name, b.count, b.statusText]), [['counts_cancelled_jobs_as_open', 6, 'Candidate']]);
  assert.deepEqual(map.unseen.map((u) => [u.name, u.statusText]), [['invents_asset_codes', 'Confirmed']]);
  assert.deepEqual(map.retired.map((r) => [r.name, r.text]), [['old_mode', 'merged into counts_cancelled_jobs_as_open']]);
});

test('the Progress rows read the taxonomy keys, and show "—" until they arrive', () => {
  const none = vals().tlTaxProgress;
  assert.equal(none.completeness, null);
  assert.equal(none.saturation, '—');
  assert.equal(none.unfiled, '—');
  assert.equal(none.curve.empty, true);
  fresh({}, { sampling: { progress: { discovery: { block: 10, points: [{ wrong: 3 }] }, saturation: { available: false } } } });
  const open = vals().tlTaxProgress;
  assert.equal(open.curve.empty, true, 'the open-coding figures of the Progress tab are not the failure-mode ones');
  assert.equal(open.saturation, '—');
  const prg = { block: 10, batches: [{ id: 'b-1', plan_step: 'initial_uniform' }], unfiled_notes: 4,
    completeness: { reviewed_turns: 12, modes: 2, required: 24, done: 20, missing: 4, missing_preview: [{ turn_id: 't1', question: 'How many open?', missing: 2 }], more: 0 },
    modes: [{ mode_id: 'm-1', name: 'counts_cancelled_jobs_as_open', status: 'confirmed', level: 'technical', first_failure: 3, any_instance: 7, decided: 12,
      sample_fraction: { present: 7, decided: 12, fraction: 0.5833, kind: 'sample_fraction' }, pending_suggestions: 2 }],
    discovery: [{ block: 1, reviews_through: 10, new_modes: 2, cumulative_modes: 2 }, { block: 2, reviews_through: 20, new_modes: 0, cumulative_modes: 2 }],
    discovery_by_batch: [{ batch_id: 'b-1', new_modes: 2 }], saturation: { hint: false, text: '', final_uniform_text: '' },
    re_review: { count: 2, text: 'You have found 2 failure modes since you reviewed your first answers. Worth a second pass.', turn_ids: ['t1', 't2'] } };
  fresh({}, { sampling: { progress: prg } });
  const v = vals().tlTaxProgress;
  assert.equal(v.completeness.text, '20 of 24 judgments made');
  assert.equal(v.completeness.missing, '4 missing');
  assert.deepEqual(v.perMode.map((m) => [m.first, m.any, m.fraction, m.pending]), [['3', '7', '58% of 12 judged', '2']]);
  assert.equal(v.curve.dots.length, 2);
  assert.equal(v.curve.dots[0].label, 'Reviews up to 10: 2 new failure modes (2 in all)');
  assert.deepEqual(v.byBatch.map((b) => b.text), ['First fair batch: 2 new failure modes']);
  assert.equal(v.saturation, 'New failure modes are still appearing.');
  assert.equal(v.reReview.turns.length, 2);
  assert.equal(v.unfiled, '4');
  assert.deepEqual(T.modeCurve([{ new_modes: 0, reviews_through: 10 }, { new_modes: 2, reviews_through: 20 }], { w: 100, h: 100, pad: 10 }).path, 'M10 90 L90 10');
});

test('requirements: the starting set reports what it added; a revision keeps its answers and names the modes to recheck', async () => {
  const sent = [];
  taxonomyApi.seedRequirements = async () => ({ ok: true, inserted: ['GEN-1', 'GEN-2'], skipped: ['COMP-1'], drift: [{ code: 'GEN-1' }] });
  taxonomyApi.requirements = async () => ({ ok: true, requirements: [{ id: 'r1', code: 'GEN-1', revision: 1, status: 'active', area: 'GEN', area_label: 'General',
    level: 'technical', statement: 'Answer from the records the question names.', implementations: [{ doc: 'docs/x.md', section: 'Answers', line: 4, quote: 'answer from the records', quote_status: 'ok' }],
    motivating_turn_ids: ['t1'], citing_modes: [{ id: 'm-1', name: 'counts_cancelled_jobs_as_open', status: 'confirmed' }] }], areas: [{ key: 'GEN', label: 'General' }], levels: SERVER.levels });
  taxonomyApi.reviseRequirement = async (code, body) => { sent.push([code, body]); return { ok: true, requirement: { code }, flagged_modes: [{ name: 'counts_cancelled_jobs_as_open' }] }; };
  fresh({ tlModesMeta: META(true) });
  assert.equal(await A.reqsSeed(), true);
  assert.equal(X().tlReqsNotice, 'Added 2 requirements; 1 already there. 1 quote no longer matches its document.');
  await A.reqsLoad();
  const reqs = vals().tlTaxReqs;
  assert.deepEqual(reqs.groups.map((g) => [g.label, g.rows.map((r) => r.code)]), [['General', ['GEN-1']]]);
  assert.equal(reqs.groups[0].rows[0].citing, 'counts_cancelled_jobs_as_open');
  assert.equal(reqs.groups[0].rows[0].sources[0].check, 'The quote still matches its document');
  assert.equal(A.reqsOpen('revise', 'GEN-1'), true);
  A.reqsSet({ statement: 'Answer only from the records the question names.' });
  assert.equal(await A.reqsSave(), false);
  assert.equal(X().tlReqsFormErr, 'Say what changed (at least 3 characters).');
  A.reqsSet({ note: 'Narrowed to the named records.' });
  assert.equal(await A.reqsSave(), true);
  assert.deepEqual(sent[0][1].motivating_turn_ids, ['t1']);
  assert.deepEqual(sent[0][1].implementations, [{ doc: 'docs/x.md', section: 'Answers', line: 4, quote: 'answer from the records' }]);
  assert.equal(X().tlReqsNotice, 'Revised GEN-1. Recheck the failure modes that cite it: counts_cancelled_jobs_as_open.');
  fresh({ tlModesMeta: META(false) });
  assert.equal(A.reqsOpen('create'), false);
});

test('the modes reset covers every modes, notes and requirements key the module writes', () => {
  const dir = new URL('..', import.meta.url);
  const src = ['taxonomyActions.js', 'logic/taxonomy.js', 'logic/taxCommon.js'].map((f) => readFileSync(new URL(f, dir), 'utf8')).join('\n');
  const keys = new Set([...src.matchAll(/\b(tl(?:Modes|Unfiled|Reqs)[A-Za-z]*)\s*:/g)].map((m) => m[1]));
  const patch = C.taxonomyInitial(1);
  assert.ok(keys.size >= 25, [...keys].join(','));
  for (const k of keys) assert.ok(Object.prototype.hasOwnProperty.call(patch, k), k);
  assert.notEqual(C.taxonomyInitial(1).tlUnfiledChosen, patch.tlUnfiledChosen, 'each reset gets fresh arrays');
});

test('a company switch empties the taxonomy slice, and a reply from before it is dropped', async () => {
  let release = null;
  taxonomyApi.modes = () => new Promise((resolve) => { release = () => resolve(Object.assign({ modes: [MODE] }, SERVER)); });
  fresh();
  const pending = A.modesLoad();
  resetForCompany();
  release();
  await pending;
  assert.equal(X().tlModes, null, 'the old company’s modes never land');
  fresh(loaded());
  assert.equal(X().tlModes.length, 1);
  resetForCompany();
  assert.equal(X().tlModes, null);
  assert.equal(X().tlModesMeta, null);
  await tick();
});

test('an answer opens in Conversations by its key, or by its turn when only that is known; the platform team cannot open one', async () => {
  net.handlers[P + '/turns/t7'] = [200, { ok: true, conversation_key: 's:chat-7' }];
  fresh(loaded({ tlModesDrawerId: 'm-1', tlModesDrawer: { mode: MODE } }, false));
  assert.equal(A.taxOpenAnswer('t7', 's:chat-1'), true);
  assert.deepEqual([S().tab, S().convKey, S().turnId], ['conversations', 's:chat-1', 't7']);
  assert.equal(X().tlModesDrawerId, null, 'the drawer closes behind it');
  fresh(loaded({}, false));
  assert.equal(A.taxOpenAnswer('t7'), true);
  for (let i = 0; i < 6; i += 1) await tick();
  assert.ok(net.requests.some((r) => r.url.pathname === P + '/turns/t7'));
  assert.deepEqual([S().tab, S().convKey], ['conversations', 's:chat-7']);
  fresh(loaded({}, true));
  assert.equal(A.taxOpenAnswer('t7', 's:chat-1'), false);
  assert.equal(S().tab, 'progress');
  fresh(loaded({}, false));
  assert.equal(await A.openTurnById('t-unknown'), false);
  assert.equal(S().tab, 'conversations');
  assert.match(S().conv.error, /not mocked|not part of a conversation|404/i);
});

test('a failed write keeps a visible error with Retry; a stale base says another tab changed it; a company switch says nothing', async () => {
  let attempts = 0;
  const fn = () => { attempts += 1; return attempts === 1 ? Promise.reject(new Error('network down')) : Promise.resolve({ ok: true }); };
  assert.equal((await A.write(fn, { key: 'k1' })).ok, false);
  assert.equal(X().tlWrites.k1.error, 'Could not save — network down');
  assert.equal(X().tlWrites.k1.canRetry, true);
  assert.equal((await A.retryWrite('k1')).ok, true);
  assert.equal(attempts, 2);
  assert.equal(X().tlWrites.k1, undefined);
  await A.write(() => Promise.reject(Object.assign(new Error('stale'), { status: 409 })), { key: 'k2' });
  assert.equal(X().tlWrites.k2.error, 'Changed in another tab — reload this answer.');
  assert.equal(X().tlWrites.k2.canRetry, false);
  await A.write(() => Promise.reject(new Error('x')), { key: 'k4', retry: false });
  assert.equal(X().tlWrites.k4.canRetry, false);
  const quiet = await A.write(() => Promise.reject(Object.assign(new Error('moved'), { staleScope: true })), { key: 'k3' });
  assert.equal(quiet.stale, true);
  assert.equal(X().tlWrites.k3, undefined);
  assert.deepEqual(await A.retryWrite('never-run'), { ok: false });
});

// ── the rules each dialog and form enforces before anything is sent (the service checks them again) ──────────────

test('a failure-mode name is lower-case words joined by underscores, 3 to 64 characters, starting with a letter', () => {
  for (const bad of ['', 'ab', 'Counts_jobs', '1counts', '_counts', 'has space', 'has-dash', 'a' + 'b'.repeat(64)]) assert.equal(T.NAME_RE.test(bad), false, JSON.stringify(bad));
  for (const good of ['abc', 'a_b_c1', 'counts_cancelled_jobs_as_open', 'a' + 'b'.repeat(63)]) assert.equal(T.NAME_RE.test(good), true, good);
});

test('a draft failure mode lists what it lacks: definition length, boundary length, a level and where it lives', () => {
  const ok = { name: 'counts_jobs', definition: 'Present when cancelled jobs count as open.', boundary: '', level: 'technical', agent: 'wo_engine' };
  assert.deepEqual(T.draftProblems(ok, true), []);
  assert.deepEqual(T.draftProblems(Object.assign({}, ok, { name: 'No' }), false), [], 'an edit keeps its name');
  assert.equal(T.draftProblems(Object.assign({}, ok, { name: 'No' }), true).length, 1);
  assert.match(T.draftProblems(Object.assign({}, ok, { definition: 'too short' }), false)[0], /^Definition:/);
  assert.equal(T.draftProblems(Object.assign({}, ok, { definition: ' '.repeat(5) + 'Present  ' }), false).length, 1, 'spaces do not count');
  assert.match(T.draftProblems(Object.assign({}, ok, { definition: 'x'.repeat(1001) }), false)[0], /^Definition:/);
  assert.equal(T.draftProblems(Object.assign({}, ok, { definition: 'x'.repeat(1000) }), false).length, 0);
  assert.deepEqual(T.draftProblems(Object.assign({}, ok, { boundary: 'x'.repeat(1001) }), false), ['Boundary: at most 1,000 characters.']);
  assert.deepEqual(T.draftProblems(Object.assign({}, ok, { level: '', agent: '' }), false), ['Choose who decides it (the level).', 'Choose where the failure lives.']);
});

test('merge, split, retire, file, dispose and reject each say what they still lack', () => {
  const t = Object.assign(C.taxonomyInitial(1), { tlModesDrawer: { mode: { created_from: ['a-1', 'a-2'] } } });
  const p = (d) => T.dialogProblems(d, t);
  const draft = { name: 'counts_jobs', definition: 'Present when cancelled jobs count as open.', boundary: '', level: 'technical', agent: 'wo_engine' };
  // merge into an existing mode
  assert.deepEqual(p({ kind: 'merge', target: 'existing', sources: [], into: '', draft }), ['Choose at least one failure mode to merge.', 'Choose the failure mode to merge into.']);
  assert.deepEqual(p({ kind: 'merge', target: 'existing', sources: ['m-1'], into: 'm-1', draft }), ['A failure mode cannot merge into itself.']);
  assert.deepEqual(p({ kind: 'merge', target: 'existing', sources: ['m-1'], into: 'm-2', draft }), []);
  // merge into a new one: two or more, and a whole draft
  assert.deepEqual(p({ kind: 'merge', target: 'new', sources: ['m-1'], draft }), ['Merging into a new failure mode takes at least two.']);
  assert.deepEqual(p({ kind: 'merge', target: 'new', sources: ['m-1', 'm-2'], draft }), []);
  assert.equal(p({ kind: 'merge', target: 'new', sources: ['m-1', 'm-2'], draft: Object.assign({}, draft, { name: '' }) }).length, 1);
  // split: 2 to 5 children, each its own name, each given at least one of the notes the mode came from
  const kid = (name) => Object.assign({}, draft, { name });
  const split = (names, noteChild) => ({ kind: 'split', children: names.map(kid), noteChild });
  assert.deepEqual(p(split(['one_mode', 'two_mode'], { 'a-1': 0, 'a-2': 1 })), []);
  assert.ok(p(split(['one_mode'], { 'a-1': 0 })).includes('Split into 2 to 5 failure modes.'));
  assert.ok(p(split(['m_a1', 'm_a2', 'm_a3', 'm_a4', 'm_a5', 'm_a6'], { 'a-1': 0, 'a-2': 1 })).includes('Split into 2 to 5 failure modes.'));
  assert.deepEqual(p(split(['same_name', 'same_name'], { 'a-1': 0, 'a-2': 1 })), ['Each new failure mode needs its own name.']);
  assert.deepEqual(p(split(['one_mode', 'two_mode'], { 'a-1': 0, 'a-2': 0 })), ['Mode 2: give it at least one of the notes the mode came from.']);
  assert.deepEqual(p(split(['one_mode', 'two_mode'], { 'a-3': 1, 'a-1': 0 })), ['Mode 2: give it at least one of the notes the mode came from.'], 'a note the mode did not come from does not count');
  // the one-line dialogs
  assert.deepEqual(p({ kind: 'retire', reason: '  ' }), ['Say why it is retired.']);
  assert.deepEqual(p({ kind: 'retire', reason: 'Superseded.' }), []);
  assert.deepEqual(p({ kind: 'file', modeId: '' }), ['Choose a failure mode.']);
  assert.deepEqual(p({ kind: 'file', modeId: 'm-1' }), []);
  assert.deepEqual(p({ kind: 'dispose', note: '' }), ['Say why.']);
  assert.deepEqual(p({ kind: 'dispose', note: 'Vague.' }), []);
  assert.deepEqual(p({ kind: 'reject', reason: '' }), ['Say why it is rejected.']);
  assert.deepEqual(p({ kind: 'reject', reason: 'x'.repeat(501) }), ['The reason is at most 500 characters.']);
  assert.deepEqual(p({ kind: 'reject', reason: 'x'.repeat(500) }), []);
  assert.deepEqual(p({ kind: 'freeze' }), []);
  assert.deepEqual(p(null), []);
});

test('the requirement form says what it still lacks: area, level, statement, a reason, and each source', () => {
  const ok = { kind: 'create', area: 'GEN', level: 'technical', statement: 'Answer from the records the question names.', impls: [] };
  assert.deepEqual(T.reqProblems(ok), []);
  assert.deepEqual(T.reqProblems(Object.assign({}, ok, { area: '', level: '' })), ['Choose an area.', 'Choose a level.']);
  assert.deepEqual(T.reqProblems(Object.assign({}, ok, { statement: 'too short' })), ['Statement: 10 to 600 characters.']);
  assert.deepEqual(T.reqProblems(Object.assign({}, ok, { statement: 'x'.repeat(601) })), ['Statement: 10 to 600 characters.']);
  assert.deepEqual(T.reqProblems(Object.assign({}, ok, { statement: 'x'.repeat(600) })), []);
  const bad = T.reqProblems(Object.assign({}, ok, { impls: [{ doc: ' ', quote: 'short', line: '0' }] }));
  assert.deepEqual(bad, ['Source 1: name the document.', 'Source 1: quote at least 8 characters from it.', 'Source 1: the line is a number from 1.']);
  assert.deepEqual(T.reqProblems(Object.assign({}, ok, { impls: [{ doc: 'docs/x.md', quote: 'answer from the records', line: '4' }, { doc: 'docs/y.md', quote: 'answer from the records', line: '' }] })), []);
  assert.deepEqual(T.reqProblems({ kind: 'revise', statement: ok.statement, note: 'ab', impls: [] }), ['Say what changed (at least 3 characters).']);
  assert.deepEqual(T.reqProblems({ kind: 'revise', statement: ok.statement, note: 'abc', impls: [] }), []);
  assert.deepEqual(T.reqProblems({ kind: 'withdraw', note: ' a ' }), ['Say why it is withdrawn (at least 3 characters).']);
  assert.deepEqual(T.reqProblems({ kind: 'withdraw', note: 'No longer true.' }), []);
});

test('the drawer offers only what a failure mode in its status can still do', () => {
  const at = (extra) => { fresh(loaded({ tlModesDrawerId: 'm-1', tlModesDrawer: { mode: Object.assign({}, MODE, extra) } })); return vals().tlTaxDrawer.actions; };
  const offered = (a) => Object.keys(a).filter((k) => (k === 'confirm' ? !!a.confirm : typeof a[k] === 'function')).sort();
  assert.deepEqual(offered(at({ status: 'candidate' })), ['confirm', 'edit', 'merge', 'rename', 'retire', 'split']);
  assert.deepEqual(offered(at({ status: 'confirmed' })), ['edit', 'freeze', 'merge', 'rename', 'retire', 'split', 'toCandidate']);
  assert.deepEqual(offered(at({ status: 'frozen' })), ['edit', 'rename', 'retire'], 'a frozen mode cannot be merged or split');
  assert.deepEqual(offered(at({ status: 'retired' })), ['restore'], 'a retired mode can only come back');
  assert.deepEqual(offered(at({ status: 'retired', merged_into: 'm-9' })), [], 'a merged mode cannot be restored');
  fresh(loaded({ tlModesDrawerId: 'm-1', tlModesDrawer: { mode: MODE } }, false));
  assert.equal(vals().tlTaxDrawer.actions, null, 'a company admin changes nothing here');
});

test('only the platform team takes an answer off a mode’s lists, and only a company can open one', () => {
  const body = { mode: Object.assign({}, MODE, { positive_turn_ids: ['t1'], positive_count: 4 }),
    positives: [{ turn_id: 't1', question: 'Q1', ok: true }],
    close_negatives: [{ turn_id: 't2', question: 'Q2', ok: false, state: 'present' }, { turn_id: 't5', question: 'Q5', ok: false, state: null }],
    close_negative_candidates: [{ turn_id: 't3', question: 'Q3' }],
    notes: [{ annotation_id: 'a-1', turn_id: 't4', note: 'n' }, { annotation_id: 'a-2', turn_id: 't1', note: 'm' }], events: [] };
  fresh(loaded({ tlModesDrawerId: 'm-1', tlModesDrawer: body }, true));
  let d = vals().tlTaxDrawer;
  assert.equal(typeof d.positives[0].remove, 'function');
  assert.equal(typeof d.closeNegatives[0].remove, 'function');
  assert.equal(typeof d.candidates[0].add, 'function');
  assert.equal(typeof d.notes[0].usePositive, 'function');
  assert.equal(d.notes[1].usePositive, null, 'an answer already listed as present is not offered again');
  assert.deepEqual([d.positives[0].open, d.candidates[0].open, d.notes[0].open], [null, null, null], 'the platform team has no company to open an answer in');
  assert.deepEqual(d.closeNegatives.map((x) => x.state), ['Now present: does not count', 'Not judged yet']);
  assert.equal(d.positives[0].state, '✓ Counts');
  assert.equal(d.mode.counts, 'This company has 1 of the 4 listed answers where it is present.');
  fresh(loaded({ tlModesDrawerId: 'm-1', tlModesDrawer: body }, false));
  d = vals().tlTaxDrawer;
  assert.deepEqual([d.positives[0].remove, d.closeNegatives[0].remove, d.candidates[0].add, d.notes[0].usePositive], [null, null, null, null]);
  assert.equal(typeof d.positives[0].open, 'function');
  assert.equal(typeof d.candidates[0].open, 'function');
  assert.equal(typeof d.notes[0].open, 'function');
  fresh(loaded({ tlModesDrawerId: 'm-1', tlModesDrawer: Object.assign({}, body, { mode: Object.assign({}, body.mode, { status: 'retired' }) }) }, true));
  d = vals().tlTaxDrawer;
  assert.deepEqual([d.positives[0].remove, d.candidates[0].add, d.notes[0].usePositive], [null, null, null], 'a retired mode’s lists are read-only');
});

test('answers go on or off a mode’s lists through an edit at most 50 long, and only the lists can be edited this way', async () => {
  const sent = [];
  taxonomyApi.editMode = async (id, body) => { sent.push([id, body]); return { ok: true, mode: MODE }; };
  const ids = Array.from({ length: 50 }, (_, i) => 't' + i);
  const big = Object.assign({}, MODE, { positive_turn_ids: ids, close_negative_turn_ids: ['c1'] });
  taxonomyApi.mode = async () => ({ ok: true, mode: big }); // the drawer is read again after each edit
  fresh(loaded({ tlModesDrawerId: 'm-1', tlModesDrawer: { mode: big } }));
  assert.equal(await A.modeList('m-1', 'positive_turn_ids', 'extra', true), true);
  assert.ok(sent[0][1].positive_turn_ids.length <= 50, 'the service takes at most 50');
  assert.equal(sent[0][1].base_version, 3);
  assert.equal(await A.modeList('m-1', 'positive_turn_ids', 't0', false), true);
  assert.equal(sent[1][1].positive_turn_ids.length, 49);
  assert.equal(sent[1][1].positive_turn_ids.includes('t0'), false);
  assert.equal(await A.modeList('m-1', 'close_negative_turn_ids', 'c2', true), true);
  assert.deepEqual(sent[2][1].close_negative_turn_ids, ['c1', 'c2']);
  assert.equal(await A.modeList('m-1', 'definition', 'x', true), false);
  assert.equal(await A.modeList('m-9', 'positive_turn_ids', 'x', true), false, 'a mode that is not loaded');
  assert.equal(sent.length, 3);
  fresh(loaded({ tlModesDrawerId: 'm-1', tlModesDrawer: { mode: MODE } }, false));
  assert.equal(await A.modeList('m-1', 'positive_turn_ids', 'x', true), false, 'not the platform team');
  assert.equal(sent.length, 3);
});

test('the split dialog adds up to five new modes, renumbers the notes when one is removed, and a note can be left out', () => {
  const notes = [{ annotation_id: 'a-1', turn_id: 't1', note: 'one' }, { annotation_id: 'a-2', turn_id: 't2', note: 'two' }, { annotation_id: 'a-3', turn_id: 't3', note: 'three' }];
  fresh(loaded({ tlModesDrawerId: 'm-1', tlModesDrawer: { mode: MODE, notes } }));
  A.modeDialog('split', { modeId: 'm-1' });
  const dlg = () => vals().tlTaxDialog;
  assert.equal(dlg().children.length, 2);
  assert.deepEqual([dlg().children[0].remove, typeof dlg().addChild], [null, 'function'], 'two is the least');
  A.modeDialogAddChild();
  assert.equal(X().tlModesDialog.children.length, 3);
  assert.deepEqual([X().tlModesDialog.children[2].level, X().tlModesDialog.children[2].agent], ['technical', 'wo_engine'], 'a new one starts as the mode it splits');
  assert.equal(typeof dlg().children[0].remove, 'function');
  A.modeDialogNote('a-1', '0');
  A.modeDialogNote('a-2', '1');
  A.modeDialogNote('a-3', '2');
  assert.deepEqual(X().tlModesDialog.noteChild, { 'a-1': 0, 'a-2': 1, 'a-3': 2 });
  A.modeDialogRemoveChild(1);
  assert.equal(X().tlModesDialog.children.length, 2);
  assert.deepEqual(X().tlModesDialog.noteChild, { 'a-1': 0, 'a-3': 1 }, 'its notes are unassigned and the later number moves up');
  A.modeDialogRemoveChild(0);
  assert.equal(X().tlModesDialog.children.length, 2, 'never below two');
  assert.deepEqual(X().tlModesDialog.noteChild, { 'a-1': 0, 'a-3': 1 });
  A.modeDialogNote('a-1', '');
  assert.deepEqual(X().tlModesDialog.noteChild, { 'a-3': 1 }, 'an empty choice leaves the note out');
  A.modeDialogNote('a-3', 'none');
  assert.deepEqual(X().tlModesDialog.noteChild, {});
  for (let i = 0; i < 6; i += 1) A.modeDialogAddChild();
  assert.equal(X().tlModesDialog.children.length, 5, 'five is the most');
  assert.equal(dlg().addChild, null);
  assert.deepEqual(dlg().childOptions.map((o) => o.value), ['', '0', '1', '2', '3', '4']);
});

test('unfiled notes say how many stay hidden, and a re-read keeps only the ticked notes still listed', async () => {
  taxonomyApi.unfiledNotes = async () => ({ ok: true, notes: [{ annotation_id: 'a-1', turn_id: 't1', note: 'n1' }], total: 5, hidden: 2 });
  fresh(loaded({ tlUnfiledChosen: ['a-1', 'a-9'] }, false));
  await A.unfiledLoad();
  assert.deepEqual(X().tlUnfiledChosen, ['a-1']);
  const u = vals().tlTaxUnfiled;
  assert.equal(u.hiddenNote, '2 notes stay hidden until you judge those answers yourself.');
  assert.equal(u.total, '5');
  assert.equal(u.chosenCount, 1);
  taxonomyApi.unfiledNotes = async () => ({ ok: true, notes: [], total: 1, hidden: 1 });
  await A.unfiledLoad();
  assert.equal(vals().tlTaxUnfiled.hiddenNote, '1 note stays hidden until you judge those answers yourself.');
  assert.deepEqual(X().tlUnfiledChosen, []);
  taxonomyApi.unfiledNotes = async () => ({ ok: true, notes: [], total: 0, hidden: 0 });
  await A.unfiledLoad();
  assert.equal(vals().tlTaxUnfiled.hiddenNote, '');
  assert.equal(vals().tlTaxUnfiled.emptyText, 'No unfiled notes.');
});

test('Progress reads the failure-mode saturation only in the service’s own shape, and re-review answers open for a company only', () => {
  const rr = { count: 1, text: 'You have found 1 failure mode since your first answers. Worth a second pass.', turn_ids: ['t1'] };
  const discovery = [{ block: 1, reviews_through: 10, new_modes: 1, cumulative_modes: 1 }];
  const withProgress = (p, tax) => fresh(tax || {}, { sampling: { progress: p } });
  withProgress({ discovery, re_review: rr, saturation: { available: false } });
  assert.equal(vals().tlTaxProgress.saturation, '—', 'the open-coding saturation figure is not this one');
  withProgress({ discovery, re_review: rr, saturation: { hint: false, text: '' } });
  assert.equal(vals().tlTaxProgress.saturation, 'New failure modes are still appearing.');
  withProgress({ discovery, re_review: rr, saturation: { hint: true, text: 'Few new failure modes now.', final_uniform_text: 'The last fair batch found none.' } });
  assert.equal(vals().tlTaxProgress.saturation, 'Few new failure modes now.');
  assert.equal(vals().tlTaxProgress.finalUniform, 'The last fair batch found none.');
  assert.equal(typeof vals().tlTaxProgress.reReview.turns[0].open, 'function', 'a company opens the answer');
  withProgress({ discovery, re_review: rr, saturation: { hint: false, text: '' } }, { tlModesMeta: META(true) });
  assert.equal(vals().tlTaxProgress.reReview.turns[0].open, null, 'the platform team has no company to open it in');
  assert.equal(vals().tlTaxProgress.reReview.turns[0].label, 'Answer 1');
  withProgress({ discovery, re_review: { count: 0, text: '', turn_ids: [] }, saturation: { text: '' } });
  assert.equal(vals().tlTaxProgress.reReview, null);
});
