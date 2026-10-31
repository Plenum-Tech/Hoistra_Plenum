// taxModeProposals — a company's reviewers PROPOSE failure modes and the platform team accepts or rejects them with a
// reason (G0 addendum 4(3)): the Propose dialog on the unfiled notes, the platform team's queue of what companies
// proposed (Accept, Reject with a reason) and a company's own list of what it proposed and how each was decided.
// The service has the routes (routes_mode_proposals.py); this pins what the browser sends, who is offered what, and
// what the screens say. A company reviewer never gets the platform team's create / merge / split / retire buttons.
import { test, beforeEach, afterEach, after } from 'node:test';
import assert from 'node:assert/strict';
import { taxonomyApi, A, C, S, X, settle, fresh, loaded, refusal, resetForCompany, setActingOrg, installDefaults, restoreApis } from './tax-helper.mjs';
import { button, cleanTaxScreens, findAll, taxScreen, text, view } from './tax-screen-helper.mjs';

const React = (await import('react')).default;
const { renderToStaticMarkup } = await import('react-dom/server');
const PT = await taxScreen('views/ProgressTaxonomy.jsx');
const MG = await taxScreen('views/ModeDialogs.jsx');
const T = await import('../logic/taxonomy.js');
const vals = () => T.taxonomyModesVals(S(), A);
const markup = (Comp, props) => renderToStaticMarkup(React.createElement(Comp, props));

after(cleanTaxScreens);
beforeEach(installDefaults);
afterEach(restoreApis);

const NOTE = { annotation_id: 'a-1', turn_id: 't1', conversation_key: 's:c1', note: 'Counted the cancelled jobs.', quote: '3 cancelled', step_label: 'Work orders',
  reviewer_email: 'ada@example.com', created_at: '2026-10-01T09:20:00' };
const FIELDS = { name: 'counts_cancelled_work_orders', definition: 'Present when the answer counts cancelled jobs as open.', level: 'technical', agent: 'wo_engine' };
const PROP = { id: 'p-1', name: 'counts_cancelled_work_orders', status: 'proposed', version: 2, definition: 'Present when the answer counts cancelled jobs as open.',
  boundary: 'Not the same as jobs on hold.', level: 'technical', agent: 'wo_engine', level_label: 'Technical: the records decide', agent_label: 'Work orders',
  nearest_mode_id: 'm-1', created_by_email: 'ada@example.com', created_at: '2026-10-01T09:30:00', created_from: ['a-1'], notes: [NOTE] };
const REJECTED = Object.assign({}, PROP, { id: 'p-2', name: 'vague_failure', status: 'rejected', version: 3, rejected_reason: 'Too vague to apply twice.' });
const ACCEPTED = Object.assign({}, PROP, { id: 'p-3', name: 'counts_on_hold_jobs', status: 'candidate', version: 4 });
const asCompany = (extra) => fresh(loaded(Object.assign({ tlModesProposedLoadedAt: 1 }, extra || {}), false));
const asPlatform = (extra) => fresh(loaded(Object.assign({ tlModesProposedLoadedAt: 1 }, extra || {}), true));

// ── who may propose ──────────────────────────────────────────────────────────────────────────────────────────

test('a company reviewer and a superadmin viewing as a company may propose; the platform team and a page the service has not answered may not', () => {
  fresh();
  assert.equal(C.proposes(S()), false, 'nobody proposes until the service says who the person is');
  asPlatform();
  assert.equal(C.proposes(S()), false, 'the platform team creates, it does not propose');
  asCompany();
  assert.equal(C.proposes(S()), true);
  setActingOrg('org-1');
  asPlatform();
  assert.equal(C.proposes(S()), true, 'viewing as a company wins over the list that said platform team');
  setActingOrg(null);
});

test('the Propose dialog opens for a company reviewer only, and the platform team’s dialogs stay closed to them', () => {
  asCompany({ tlUnfiled: [NOTE], tlUnfiledLoadedAt: 1 });
  assert.equal(A.noteProposeMode(['a-1']), true);
  assert.equal(X().tlModesDialog.kind, 'propose');
  assert.deepEqual(X().tlModesDialog.createdFrom, ['a-1']);
  A.modeDialogClose();
  for (const kind of ['create', 'edit', 'merge', 'split', 'retire', 'freeze', 'dispose', 'reject']) {
    assert.equal(A.modeDialog(kind, { modeId: 'm-1', createdFrom: ['a-1'], annotationId: 'a-1' }), false, kind + ' is the platform team’s');
  }
  asPlatform({ tlUnfiled: [NOTE], tlUnfiledLoadedAt: 1 });
  assert.equal(A.noteProposeMode(['a-1']), false, 'the platform team creates a failure mode directly');
  fresh();
  assert.equal(A.noteProposeMode(['a-1']), false);
  assert.equal(X().tlModesDialog, null);
});

test('a company reviewer can tick notes to propose from; a page that does not know who the person is cannot', () => {
  asCompany({ tlUnfiled: [NOTE], tlUnfiledLoadedAt: 1 });
  A.unfiledChoose('a-1');
  assert.deepEqual(X().tlUnfiledChosen, ['a-1']);
  fresh();
  A.unfiledChoose('a-1');
  assert.deepEqual(X().tlUnfiledChosen, []);
});

// ── proposing ────────────────────────────────────────────────────────────────────────────────────────────────

test('a proposal is sent with only the fields the service takes; a taken name keeps the dialog open; success clears the notes and re-reads the list', async () => {
  const sent = [];
  let listed = 0;
  taxonomyApi.proposeMode = async (body) => {
    sent.push(body);
    if (sent.length === 1) throw refusal(409, 'name_taken', 'A live failure mode already has this name.');
    return { ok: true, mode: { id: 'p-9', status: 'proposed' }, labels_written: 0 };
  };
  taxonomyApi.modeProposals = async () => { listed += 1; return { ok: true, proposals: [PROP] }; };
  asCompany({ tlUnfiled: [NOTE], tlUnfiledLoadedAt: 1, tlUnfiledChosen: ['a-1'] });
  A.noteProposeMode(['a-1']);
  let d = vals().tlTaxDialog;
  assert.equal(d.kind, 'propose');
  assert.equal(d.title, 'Propose a failure mode');
  assert.equal(d.submitLabel, 'Send the proposal');
  assert.equal(d.showGuidance, true, 'the same-fix guidance is shown');
  assert.equal(d.showFixTest, true);
  assert.equal(d.nameEditable, true);
  assert.deepEqual(d.fromNotes.notes.map((n) => n.note), ['Counted the cancelled jobs.']);
  assert.match(d.problems[0], /^Name:/);
  assert.equal(d.canSubmit, false);
  assert.match(d.note, /platform team will read it and accept or reject it/);
  assert.equal(await A.modeDialogSubmit(), false, 'a problem listed means nothing is sent');
  assert.equal(sent.length, 0);
  A.modeDialogSet(FIELDS);
  assert.deepEqual(vals().tlTaxDialog.problems, []);
  assert.equal(await A.modeDialogSubmit(), false);
  assert.equal(X().tlModesDialogErr, 'A live failure mode already has this name.');
  assert.ok(X().tlModesDialog, 'the dialog stays open with what was typed');
  assert.equal(await A.modeDialogSubmit(), true);
  assert.deepEqual(sent[1], { name: 'counts_cancelled_work_orders', definition: 'Present when the answer counts cancelled jobs as open.', boundary: '',
    nearest_mode_id: null, level: 'technical', agent: 'wo_engine', created_from: ['a-1'] });
  assert.equal(X().tlModesDialog, null);
  assert.equal(X().tlModesNotice, 'Proposed. The platform team will accept or reject it.');
  assert.deepEqual(X().tlUnfiledChosen, []);
  assert.equal(X().tlModesDrawerId, null, 'a proposal is not a failure mode yet: no drawer opens');
  await settle();
  assert.equal(listed, 1, 'the list of proposals is read again');
});

test('a proposal needs at least one note', () => {
  asCompany();
  A.noteProposeMode([]);
  A.modeDialogSet(FIELDS);
  assert.deepEqual(vals().tlTaxDialog.problems, ['Start a failure mode from at least one reviewer note.']);
});

test('the Propose dialog offers the nearest failure mode and nothing the platform team decides', () => {
  asCompany({ tlUnfiled: [NOTE], tlUnfiledLoadedAt: 1 });
  A.noteProposeMode(['a-1']);
  const d = vals().tlTaxDialog;
  assert.deepEqual(d.nearestOptions.map((o) => o.value), ['', 'm-1']);
  assert.equal(d.requirements, undefined, 'no requirements: the platform team cites them');
  assert.equal(d.waiver, undefined);
});

// ── unfiled notes: who is offered what ──────────────────────────────────────────────────────────────────────

test('unfiled notes give a company reviewer Propose and the platform team New failure mode, never both', () => {
  asCompany({ tlUnfiled: [NOTE], tlUnfiledLoadedAt: 1, tlUnfiledChosen: ['a-1'] });
  let u = vals().tlTaxUnfiled;
  assert.equal(u.canPropose, true);
  assert.equal(u.canCreate, false);
  assert.equal(typeof u.rows[0].proposeMode, 'function');
  assert.equal(typeof u.rows[0].choose, 'function');
  assert.equal(u.rows[0].newMode, null);
  assert.equal(u.rows[0].noModeFits, null);
  assert.equal(typeof u.proposeFromChosen, 'function');
  assert.equal(u.newFromChosen, null);
  asPlatform({ tlUnfiled: [NOTE], tlUnfiledLoadedAt: 1, tlUnfiledChosen: ['a-1'] });
  u = vals().tlTaxUnfiled;
  assert.equal(u.canPropose, false);
  assert.equal(u.canCreate, true);
  assert.equal(u.rows[0].proposeMode, null);
  assert.equal(typeof u.rows[0].newMode, 'function');
  assert.equal(u.proposeFromChosen, null);
  assert.equal(typeof u.newFromChosen, 'function');
});

// ── the platform team's queue ───────────────────────────────────────────────────────────────────────────────

test('the queue lists what companies proposed, with who, the notes and Accept and Reject', () => {
  asPlatform({ tlModesProposed: [PROP] });
  const q = vals().tlTaxModeProposed;
  assert.equal(q.audience, 'platform');
  assert.equal(q.title, 'Failure modes proposed by companies');
  assert.equal(q.rows.length, 1);
  const r = q.rows[0];
  assert.deepEqual([r.name, r.who, r.when, r.level, r.agent, r.nearest, r.boundary], ['counts_cancelled_work_orders', 'ada@example.com', '2026-10-01 09:30',
    'Technical: the records decide', 'Work orders', 'counts_cancelled_jobs_as_open', 'Not the same as jobs on hold.']);
  assert.equal(r.statusText, 'Waiting for the platform team');
  assert.deepEqual(r.notes.map((n) => [n.note, n.quote, n.step, n.who]), [['Counted the cancelled jobs.', '3 cancelled', 'Work orders', 'ada@example.com']]);
  assert.equal(typeof r.accept, 'function');
  assert.equal(typeof r.reject, 'function');
});

test('Accept sends the version the row was read at, says so, and reads the modes and the queue again', async () => {
  const calls = [];
  let modes = 0;
  let listed = 0;
  taxonomyApi.acceptModeProposal = async (id, body) => { calls.push([id, body]); return { ok: true, mode: { id, status: 'candidate' }, labels_written: 1 }; };
  taxonomyApi.modeProposals = async () => { listed += 1; return { ok: true, proposals: [] }; };
  taxonomyApi.modes = async () => { modes += 1; return { ok: true, is_owner: true, modes: [], agents: [], levels: [] }; };
  asPlatform({ tlModesProposed: [PROP] });
  assert.equal(await vals().tlTaxModeProposed.rows[0].accept(), true);
  assert.deepEqual(calls, [['p-1', { base_version: 2 }]]);
  assert.equal(X().tlModesProposedNotice, 'Accepted. It is now a candidate failure mode and its notes are filed under it.');
  await settle();
  assert.equal(listed, 1);
  assert.equal(modes, 1);
});

test('a refused Accept names the proposal and keeps it listed; a stale one says to read the list again', async () => {
  taxonomyApi.acceptModeProposal = async () => { throw refusal(409, 'name_taken', 'A live failure mode already has this name.'); };
  asPlatform({ tlModesProposed: [PROP] });
  assert.equal(await vals().tlTaxModeProposed.rows[0].accept(), false);
  assert.equal(vals().tlTaxModeProposed.error, 'counts_cancelled_work_orders: A live failure mode already has this name.');
  assert.equal(X().tlModesProposed.length, 1, 'nothing leaves the list on a refusal');
  taxonomyApi.acceptModeProposal = async () => { throw refusal(409, 'stale', 'Changed.'); };
  asPlatform({ tlModesProposed: [PROP] });
  assert.equal(await vals().tlTaxModeProposed.rows[0].accept(), false);
  assert.equal(vals().tlTaxModeProposed.error, 'counts_cancelled_work_orders: This proposal changed since you loaded it. Refresh the list and look again.');
});

test('a company reviewer cannot accept: nothing is sent', async () => {
  let sent = 0;
  taxonomyApi.acceptModeProposal = async () => { sent += 1; return { ok: true }; };
  asCompany({ tlModesProposed: [PROP] });
  assert.equal(await A.modePropAccept('p-1'), false);
  assert.equal(sent, 0);
});

test('Reject opens a dialog that needs a reason, sends it with the version, and tells the platform team the company will read it', async () => {
  const calls = [];
  taxonomyApi.rejectModeProposal = async (id, body) => { calls.push([id, body]); return { ok: true, mode: { id, status: 'rejected' } }; };
  let listed = 0;
  taxonomyApi.modeProposals = async () => { listed += 1; return { ok: true, proposals: [] }; };
  asPlatform({ tlModesProposed: [PROP] });
  vals().tlTaxModeProposed.rows[0].reject();
  let d = vals().tlTaxDialog;
  assert.equal(d.kind, 'reject');
  assert.equal(d.title, 'Reject counts_cancelled_work_orders');
  assert.equal(d.submitLabel, 'Reject it');
  assert.deepEqual(d.problems, ['Say why it is rejected.']);
  assert.equal(d.canSubmit, false);
  assert.match(d.note, /company reads your reason/);
  assert.equal(await A.modeDialogSubmit(), false);
  assert.deepEqual(calls, [], 'no reason, nothing sent');
  A.modeDialogSet({ reason: 'x'.repeat(501) });
  assert.deepEqual(vals().tlTaxDialog.problems, ['The reason is at most 500 characters.']);
  A.modeDialogSet({ reason: '  Too vague to apply twice.  ' });
  assert.deepEqual(vals().tlTaxDialog.problems, []);
  assert.equal(await A.modeDialogSubmit(), true);
  assert.deepEqual(calls, [['p-1', { reason: 'Too vague to apply twice.', base_version: 2 }]]);
  assert.equal(X().tlModesDialog, null);
  assert.equal(X().tlModesProposedNotice, 'Rejected. The company sees your reason.');
  assert.equal(X().tlModesDrawerId, null);
  await settle();
  assert.equal(listed, 1);
});

test('a reject for a proposal that is not on screen opens nothing', () => {
  asPlatform({ tlModesProposed: [PROP] });
  assert.equal(A.modeDialog('reject', { modeId: 'nope' }), false);
  assert.equal(X().tlModesDialog, null);
});

// ── a company's own list ─────────────────────────────────────────────────────────────────────────────────────

test('a company reads its own proposals, waiting first, with how each was decided and no way to decide', () => {
  asCompany({ tlModesProposed: [ACCEPTED, REJECTED, PROP] });
  const q = vals().tlTaxModeProposed;
  assert.equal(q.audience, 'company');
  assert.equal(q.title, 'Your proposed failure modes');
  assert.deepEqual(q.rows.map((r) => [r.name, r.statusText, r.reason]), [
    ['counts_cancelled_work_orders', 'Waiting for the platform team', ''],
    ['vague_failure', 'Rejected', 'Too vague to apply twice.'],
    ['counts_on_hold_jobs', 'Accepted · Candidate', '']]);
  q.rows.forEach((r) => { assert.equal(r.accept, null); assert.equal(r.reject, null); assert.equal(r.who, ''); });
});

test('neither a stranger nor a page the service has not answered gets a proposals section', () => {
  fresh();
  assert.equal(vals().tlTaxModeProposed, null);
  asCompany();
  assert.equal(vals().tlTaxModeProposed.empty, true);
  assert.equal(vals().tlTaxModeProposed.emptyText, 'You have not proposed any failure modes yet.');
  asPlatform();
  assert.equal(vals().tlTaxModeProposed.emptyText, 'No proposals waiting.');
});

// ── reading ──────────────────────────────────────────────────────────────────────────────────────────────────

test('the platform team reads the waiting proposals, a company reads all of its own', async () => {
  const asked = [];
  taxonomyApi.modeProposals = async (q) => { asked.push(q); return { ok: true, proposals: [PROP] }; };
  asPlatform({ tlModesProposedLoadedAt: null });
  await A.modePropsLoad();
  asCompany({ tlModesProposedLoadedAt: null });
  await A.modePropsLoad();
  assert.deepEqual(asked, [{ status: 'proposed' }, { status: 'all' }]);
  assert.equal(X().tlModesProposed.length, 1);
  assert.ok(X().tlModesProposedLoadedAt);
});

test('a failed read says so with Retry; a reply from before a company switch lands nowhere', async () => {
  taxonomyApi.modeProposals = async () => { throw new Error('network down'); };
  asPlatform({ tlModesProposedLoadedAt: null });
  await A.modePropsLoad();
  assert.equal(vals().tlTaxModeProposed.error, 'network down');
  assert.equal(typeof vals().tlTaxModeProposed.reload, 'function');
  let release = null;
  taxonomyApi.modeProposals = () => new Promise((resolve) => { release = () => resolve({ ok: true, proposals: [PROP] }); });
  asPlatform({ tlModesProposedLoadedAt: null });
  const pending = A.modePropsLoad();
  resetForCompany();
  release();
  await pending;
  assert.equal(X().tlModesProposed, null, 'the old company’s proposals never land');
});

test('Progress reads the proposals once the service has said who the person is, once, for both roles', async () => {
  const asked = [];
  let owner = false;
  taxonomyApi.modes = async () => ({ ok: true, is_owner: owner, modes: [], agents: [], levels: [] });
  taxonomyApi.modeProposals = async (q) => { asked.push(q.status); return { ok: true, proposals: [] }; };
  fresh();
  await A.taxEnsure();
  await settle();
  assert.deepEqual(asked, ['all'], 'a company reads its own, decided or not');
  await A.taxEnsure();
  assert.deepEqual(asked, ['all'], 'already read: not read again');
  owner = true;
  fresh();
  await A.taxEnsure();
  await settle();
  assert.deepEqual(asked, ['all', 'proposed'], 'the status follows who the list said the person is, not a guess made before it arrived');
  A.stopPolls();
});

test('nothing is asked of the proposals route when the failure-mode list could not say who the person is', async () => {
  let asked = 0;
  taxonomyApi.modes = async () => { throw new Error('down'); };
  taxonomyApi.modeProposals = async () => { asked += 1; return { ok: true, proposals: [] }; };
  fresh();
  await A.taxEnsure();
  await settle();
  assert.equal(asked, 0);
});

// ── the screens ──────────────────────────────────────────────────────────────────────────────────────────────

const PRG = { ok: true, block: 10, open_batches: 0, batches: [], open_coding: { reviewed: 1, wrong: 1, right: 0, missing: 0, missing_turns: [], missing_more: 0, reviewers: [] } };
const onProgress = (extra, owner) => fresh(loaded(Object.assign({ tlModesProposedLoadedAt: 1, tlUnfiled: [NOTE], tlUnfiledTotal: 1, tlUnfiledLoadedAt: 1 }, extra), owner),
  { tab: 'progress', sampling: { progress: PRG, progressLoadedAt: 1 } });

test('the platform team’s Progress shows the proposals with Accept and Reject… and keeps New failure mode on the notes', () => {
  onProgress({ tlModesProposed: [PROP] }, true);
  const html = markup(PT.default, { s: S() });
  assert.ok(html.includes('aria-label="Failure modes proposed by companies"'));
  assert.ok(html.includes('counts_cancelled_work_orders'));
  assert.ok(html.includes('Proposed by ada@example.com'));
  assert.ok(html.includes('Present when the answer counts cancelled jobs as open.'));
  assert.ok(html.includes('Not the same as jobs on hold.'));
  assert.ok(html.includes('Counted the cancelled jobs.'));
  assert.ok(html.includes('Waiting for the platform team'));
  assert.ok(html.includes('>Accept</button>') && html.includes('>Reject…</button>'));
  assert.ok(html.includes('>New failure mode</button>'));
  assert.equal(html.includes('>Propose failure mode</button>'), false);
  onProgress({}, true);
  assert.ok(markup(PT.default, { s: S() }).includes('No proposals waiting.'));
});

test('a company’s Progress shows its own proposals and Propose failure mode, and no Accept, Reject or New failure mode', () => {
  onProgress({ tlModesProposed: [PROP, REJECTED], tlUnfiledChosen: ['a-1'] }, false);
  const html = markup(PT.default, { s: S() });
  assert.ok(html.includes('aria-label="Your proposed failure modes"'));
  assert.ok(html.includes('Waiting for the platform team'));
  assert.ok(html.includes('Rejected'));
  assert.ok(html.includes('Too vague to apply twice.'));
  assert.equal(html.includes('Proposed by ada@example.com'), false, 'a company is not told who on its own team proposed it');
  assert.ok(html.includes('>Propose failure mode</button>'));
  assert.ok(html.includes('>Propose a failure mode from these</button>'));
  assert.ok(html.includes('aria-label="Choose this note"'));
  for (const b of ['Accept', 'Reject…', 'New failure mode', 'New failure mode from these', 'Not a failure', 'No mode fits']) assert.equal(html.includes('>' + b + '</button>'), false, b);
  onProgress({}, false);
  assert.ok(markup(PT.default, { s: S() }).includes('You have not proposed any failure modes yet.'));
});

test('a page with no proposals section while the service has not said who the person is', () => {
  fresh({}, { tab: 'progress', sampling: { progress: PRG, progressLoadedAt: 1 } });
  const html = markup(PT.default, { s: S() });
  assert.equal(html.includes('proposed by companies'), false);
  assert.equal(html.includes('Your proposed failure modes'), false);
});

test('pressing Propose failure mode opens the dialog; pressing Accept and Reject… act on that proposal', async () => {
  onProgress({ tlModesProposed: [PROP] }, false);
  button(view(PT.default, { s: S() }), 'Propose failure mode').props.onClick();
  assert.equal(X().tlModesDialog.kind, 'propose');
  assert.deepEqual(X().tlModesDialog.createdFrom, ['a-1']);
  A.modeDialogClose();
  const calls = [];
  taxonomyApi.acceptModeProposal = async (id, body) => { calls.push([id, body]); return { ok: true }; };
  onProgress({ tlModesProposed: [PROP] }, true);
  const tree = view(PT.default, { s: S() });
  button(tree, 'Reject…').props.onClick();
  assert.equal(X().tlModesDialog.kind, 'reject');
  A.modeDialogClose();
  button(tree, 'Accept').props.onClick();
  await settle();
  assert.deepEqual(calls, [['p-1', { base_version: 2 }]]);
});

test('the Propose dialog draws the draft fields, the platform team’s checks left out, and Send stays off while a problem is listed', () => {
  asCompany({ tlUnfiled: [NOTE], tlUnfiledLoadedAt: 1 });
  A.noteProposeMode(['a-1']);
  let html = markup(MG.default, { s: S() });
  assert.ok(html.includes('aria-label="Propose a failure mode"'));
  assert.ok(html.includes('Two notes share a mode only if the same fix clears both.'));
  assert.ok(html.includes('From 1 reviewer note'));
  assert.ok(html.includes('Name (lower-case words joined by underscores)'));
  assert.ok(html.includes('Boundary'));
  assert.ok(html.includes('Nearest failure mode'));
  assert.equal(html.includes('How it will be checked'), false);
  assert.equal(html.includes('Requirements it cites'), false);
  assert.match(html, /<button type="button"[^>]*disabled=""[^>]*>Send the proposal<\/button>/);
  A.modeDialogSet(FIELDS);
  html = markup(MG.default, { s: S() });
  assert.equal(/<button type="button"[^>]*disabled=""[^>]*>Send the proposal<\/button>/.test(html), false);
  assert.ok(html.includes('>Send the proposal</button>'));
});

test('the Reject dialog asks why, shows what the company will read, and Reject it stays off until there is a reason', () => {
  asPlatform({ tlModesProposed: [PROP] });
  A.modeDialog('reject', { modeId: 'p-1' });
  let html = markup(MG.default, { s: S() });
  assert.ok(html.includes('aria-label="Reject counts_cancelled_work_orders"'));
  assert.ok(html.includes('Why it is rejected'));
  assert.ok(html.includes('company reads your reason'));
  assert.match(html, /<button type="button"[^>]*disabled=""[^>]*>Reject it<\/button>/);
  A.modeDialogSet({ reason: 'Too vague.' });
  html = markup(MG.default, { s: S() });
  assert.equal(/<button type="button"[^>]*disabled=""[^>]*>Reject it<\/button>/.test(html), false);
  const tree = view(MG.default, { s: S() });
  assert.equal(findAll(tree, (n) => n.type === 'textarea' && n.props.maxLength === 500).length, 1);
  assert.equal(text(button(tree, 'Reject it')), 'Reject it');
});
