// taxLabelsScreens — the Labels tab, the failure-mode drawer and the dialogs that change failure modes
// (views/Labels.jsx, views/ModeDrawer.jsx, views/ModeDialogs.jsx). Each is checked three ways: its source is scanned
// for internal names; rendered to markup it says what the view model says (a labelled dialog, solid and dashed cells,
// the contract checklist with Confirm off and the missing items named); and with the screen sharing the test's store
// (tax-screen-helper.mjs) a press calls the action it is for, which a mocked service then shows.
import { test, beforeEach, afterEach, after } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { taxonomyApi, A, S, X, settle, fresh, loaded, MODE, installDefaults, restoreApis, setActingOrg } from './tax-helper.mjs';
import { button, byLabel, cleanTaxScreens, find, findAll, taxScreen, text, view } from './tax-screen-helper.mjs';

const React = (await import('react')).default;
const { renderToStaticMarkup } = await import('react-dom/server');
const LB = await taxScreen('views/Labels.jsx');
const MD = await taxScreen('views/ModeDrawer.jsx');
const MG = await taxScreen('views/ModeDialogs.jsx');

after(cleanTaxScreens);
beforeEach(installDefaults);
afterEach(restoreApis);

// Built at run time, so this file itself passes the app's internal-names scan.
const INTERNAL = ['wo_engine', ['doc', 'rag'].join('_'), 'energy_intelligence', 'contract_performance', 'orchestrator'];
const markup = (C, props) => renderToStaticMarkup(React.createElement(C, props));
const here = (rel) => readFileSync(new URL('../' + rel, import.meta.url), 'utf8');

const M1 = { id: 'm-1', name: 'counts_cancelled_jobs_as_open', status: 'confirmed', level: 'technical', definition: 'Present when cancelled jobs count as open.' };
const M2 = { id: 'm-2', name: 'invents_asset_codes', status: 'frozen', level: 'technical', definition: 'Present when an asset code is made up.' };
const cell = (state, extra) => Object.assign({ state, label_id: null, source: null, human_origin: false, via: null, via_name: null,
  decided_by: null, decided_at: null, approval: null, base: null }, extra || {});
const MATRIX = { ok: true, available: true, served_at: '2026-10-15T10:00:00+00:00', modes: [M1, M2],
  rows: [
    { turn_id: 't1', conversation_key: 's:chat-1', organization_id: 'org-1', question: 'How many work orders are open?', started_at: '2026-10-14T09:00:00Z',
      batch: { id: 'b-1', plan_step: 'initial_uniform' }, verdicts: { right: 0, wrong: 1, mine: 'wrong' },
      cells: { 'm-1': cell('present', { label_id: 'L-1', source: 'human_open_code', human_origin: true, decided_by: 'ada@example.com', approval: 'single', base: 'L-1' }),
        'm-2': cell('unchecked', { proposal: { id: 'p-1', suggestion: 'absent', evidence: 'Every code it names is in the asset register.', quote: '' } }) } },
    { turn_id: 't2', conversation_key: 's:chat-2', organization_id: 'org-1', question: 'Which certificates expire?', started_at: '2026-10-14T10:00:00Z',
      batch: null, verdicts: { right: 1, wrong: 0, mine: 'right' },
      cells: { 'm-1': cell('absent', { label_id: 'L-2', source: 'human_structured_label', human_origin: true, via: 'm-0', via_name: 'old_wording', approval: 'single', base: null }),
        'm-2': cell('unchecked') } }],
  hidden_rows: 2, total_rows: 2, totals: { reviewed_turns: 2, required: 4, done: 2, missing: 2 }, missing_preview: [],
  sample_fractions: { 'm-1': { present: 1, decided: 2, fraction: 0.5, kind: 'sample_fraction' } },
  fair_sample: { 'm-1': { present: 1, n: 1, rate: 1, ci_low: 0.2065, ci_high: 1, kind: 'uniform_batches_only' } },
  fraction_copy: 'Share of reviewed answers — not a rate for all answers: targeted batches change the mix.',
  batches: [{ id: 'b-1', plan_step: 'initial_uniform' }] };
const grid = (extra, owner) => fresh(loaded(Object.assign({ tlMatrix: MATRIX, tlMatrixLoadedAt: 1 }, extra || {}), !!owner), { tab: 'labels' });

// ── sources ──────────────────────────────────────────────────────────────────────────────────────────────────
test('the three screens name no agent or service, and each dialog is portalled, labelled and closes on Escape', () => {
  for (const rel of ['views/Labels.jsx', 'views/ModeDrawer.jsx', 'views/ModeDialogs.jsx']) {
    const src = here(rel);
    for (const internal of INTERNAL) assert.equal(src.includes(internal), false, rel + ' names ' + internal);
    assert.ok(src.includes("e.key === 'Escape'"), rel + ' closes on Escape');
    assert.ok(src.includes('createPortal(node, target)') && src.includes('role="dialog"') && src.includes('aria-modal="true"'), rel);
  }
});

// ── Labels ───────────────────────────────────────────────────────────────────────────────────────────────────
test('Labels renders solid, dashed and missing cells, the "not rates" header and a ⋯ per cell', () => {
  grid();
  const html = markup(LB.default, { s: S() });
  assert.ok(html.includes('Counts here are shares of the reviewed sample, not rates for all traffic.'));
  assert.ok(html.includes('Labels — 2 answers × 2 modes · 2 missing judgments'));
  assert.match(html, /class="tl-cell tl-cell-present" data-style="solid"/);
  assert.match(html, /class="tl-cell tl-cell-ai" data-style="dashed"/);
  assert.match(html, /class="tl-cell tl-cell-absent" data-style="solid"/);
  assert.match(html, /class="tl-cell tl-cell-missing" data-style="missing"/);
  assert.ok(html.includes('via old_wording'));
  assert.ok(html.includes('aria-label="invents_asset_codes: AI suggests absent. Press to accept."'));
  assert.equal((html.match(/class="tl-cell-more"/g) || []).length, 4);
  assert.ok(html.includes('aria-pressed="false">Only missing</button>'));
  assert.ok(html.includes('Share of reviewed answers — not a rate for all answers: targeted batches change the mix.'));
  assert.ok(html.includes('2 answers stay hidden until you have judged it yourself.'));
  assert.equal(html.includes('role="dialog"'), false, 'no cell panel until a cell is opened');
});

test('Labels says why it is empty, and the cell panel is a dialog with history and an explicit clear', () => {
  grid({ tlMatrix: Object.assign({}, MATRIX, { modes: [], rows: [], totals: null }) });
  assert.ok(markup(LB.default, { s: S() }).includes('No confirmed failure modes yet. Labels start once a failure mode is confirmed.'));
  fresh(loaded({ tlMatrixLoading: true }), { tab: 'labels' });
  assert.ok(markup(LB.default, { s: S() }).includes('Reading…'));
  grid({ tlMatrixCell: { turnId: 't1', modeId: 'm-1' }, tlMatrixHistory: [
    { id: 'L-1', present: true, tombstone: false, source: 'human_open_code', approval: 'single', decided_by_email: 'ada@example.com', created_at: '2026-10-14T09:00:00Z', superseded_by: null }] });
  const html = markup(LB.default, { s: S() });
  assert.match(html, /role="dialog" aria-modal="true" aria-label="Judgment for counts_cancelled_jobs_as_open"/);
  assert.ok(html.includes('aria-label="Clear this judgment">× Clear</button>'));
  assert.ok(html.includes('Present (now) · From a reviewer’s note · ada@example.com'));
});

test('Labels shows a failed read with the reason, and a failed press with Retry', () => {
  grid({ tlMatrixErr: 'The service did not answer.', tlWrites: { 'label:t1:m-1': { busy: false, error: 'Could not save — try again.', conflict: false, canRetry: true } } });
  const html = markup(LB.default, { s: S() });
  assert.ok(html.includes('Could not read the labels: The service did not answer.'));
  assert.ok(html.includes('Could not save — try again.'));
  assert.match(html, />Retry<\/button>/);
  grid({ tlWrites: { 'label:t1:m-1': { busy: false, error: 'Changed in another tab — reload this answer.', conflict: true, canRetry: false } } });
  assert.match(markup(LB.default, { s: S() }), />Reload<\/button>/);
});

test('pressing a cell writes its next value with the head the grid last read, and ⋯ opens its history', async () => {
  grid();
  const sent = [];
  taxonomyApi.setLabel = async (turn, mode, body) => { sent.push([turn, mode, body]); return { ok: true, cell: cell('absent', { label_id: 'L-3', base: 'L-3' }) }; };
  taxonomyApi.labelHistory = async () => ({ ok: true, chain: [] });
  const tree = view(LB.default, { s: S() });
  const btn = byLabel(tree, 'invents_asset_codes: AI suggests absent. Press to accept.');
  btn.props.onClick();
  await settle();
  assert.deepEqual(sent, [['t1', 'm-2', { present: false, base: null }]], 'an AI cell takes the AI’s value');
  const more = findAll(view(LB.default, { s: S() }), (n) => n.props.className === 'tl-cell-more')[0];
  more.props.onClick();
  await settle();
  assert.deepEqual(X().tlMatrixCell, { turnId: 't1', modeId: 'm-1' });
  const open = view(LB.default, { s: S() });
  assert.equal(find(open, (n) => n.props.role === 'dialog').props['aria-label'], 'Judgment for counts_cancelled_jobs_as_open');
  button(open, '× Clear').props.onClick();
  await settle();
  assert.deepEqual(sent[1], ['t1', 'm-1', { present: null, base: 'L-1' }]);
});

test('Only missing and Refresh are the grid’s own controls, and the batch picker lists the batches read', async () => {
  grid();
  const reads = [];
  taxonomyApi.labelsMatrix = async (q) => { reads.push(q); return MATRIX; };
  const tree = view(LB.default, { s: S() });
  assert.deepEqual(find(tree, (n) => n.props['aria-label'] === 'Which answers').props.children.map((o) => o.props.value), ['', 'b-1']);
  button(tree, 'Only missing').props.onClick();
  await settle();
  assert.equal(reads[0].state, 'missing');
  button(view(LB.default, { s: S() }), 'Refresh').props.onClick();
  await settle();
  assert.equal(reads.length, 2);
});

// ── ModeDrawer ───────────────────────────────────────────────────────────────────────────────────────────────
const DRAWER = { mode: MODE, contract: { ready: false, missing: ['boundary', 'positives'] },
  events: [{ seq: 2, action: 'edit', detail: { status: ['candidate', 'confirmed'] }, actor_email: 'team@plenum.example', at: '2026-10-15T09:00:00Z' }],
  positives: [{ turn_id: 't1', question: 'How many open?', state: 'present', approval: 'bulk', source: 'accepted_label_proposal', ok: false }],
  close_negatives: [], close_negative_candidates: [{ turn_id: 't5', question: 'How many closed?' }],
  notes: [{ annotation_id: 'a-1', turn_id: 't1', note: 'Counted the cancelled jobs.', quote: '3 cancelled', step_label: 'Work orders', reviewer_email: 'ada@example.com', created_at: '2026-10-14T09:00:00Z' }] };
const drawer = (body, owner) => fresh(loaded({ tlModesDrawerId: 'm-1', tlModesDrawer: body }, owner), { tab: 'progress' });

test('the drawer lists the contract, keeps Confirm disabled with the missing items named, and shows its history', () => {
  drawer(DRAWER);
  const html = markup(MD.default, { s: S() });
  assert.match(html, /role="dialog" aria-modal="true" aria-label="Failure mode counts_cancelled_jobs_as_open"/);
  assert.ok(html.includes('✗ A boundary: how it differs from the nearest failure mode'));
  assert.ok(html.includes('✓ How it will be checked: a code check or a judge'));
  assert.match(html, /<button type="button"[^>]*disabled=""[^>]*>Confirm<\/button>/);
  assert.ok(html.includes('Missing: A boundary: how it differs from the nearest failure mode; Three answers where it is present, each confirmed one at a time.'));
  assert.ok(html.includes('Approved in bulk: does not count'));
  assert.ok(html.includes('Status: Candidate → Confirmed'));
  assert.ok(html.includes('>List it</button>'));
  assert.equal(html.includes('>List as present</button>'), false, 'a note on an answer already listed offers no second listing');
  setActingOrg('org-1');
  drawer(DRAWER);
  const reader = markup(MD.default, { s: S() });
  assert.ok(reader.includes('Stop viewing as a company to change failure modes.'));
  assert.equal(reader.includes('>Confirm</button>'), false);
  setActingOrg(null);
  drawer(Object.assign({}, DRAWER, { mode: Object.assign({}, MODE, { level: 'data' }) }));
  assert.ok(markup(MD.default, { s: S() }).includes('Data fault: routed to the data owner. Not used for prompt fixes or judges.'));
});

test('Confirm enables once the contract is complete and calls the status change; Escape and Close are wired', async () => {
  drawer(Object.assign({}, DRAWER, { contract: { ready: true, missing: [] } }));
  const sent = [];
  taxonomyApi.editMode = async (id, body) => { sent.push([id, body]); return { ok: true, mode: Object.assign({}, MODE, { status: 'confirmed' }) }; };
  const tree = view(MD.default, { s: S() });
  const confirm = button(tree, 'Confirm');
  assert.equal(confirm.props.disabled, false);
  confirm.props.onClick();
  await settle();
  assert.equal(sent.length, 1);
  assert.equal(sent[0][0], 'm-1');
  button(view(MD.default, { s: S() }), '×').props.onClick();
  assert.equal(X().tlModesDrawerId, null);
  assert.equal(view(MD.default, { s: S() }), null, 'nothing is drawn once it is closed');
});

// ── ModeDialogs ──────────────────────────────────────────────────────────────────────────────────────────────
test('the merge and split dialogs carry the fix test and the same-fix guidance, and choices come from the server', () => {
  drawer(DRAWER);
  A.modeDialog('merge', { modeId: 'm-1' });
  let html = markup(MG.default, { s: S() });
  assert.match(html, /role="dialog" aria-modal="true" aria-label="Merge failure modes"/);
  assert.ok(html.includes('Two notes share a mode only if the same fix clears both.'));
  assert.ok(html.includes('<strong>Does fixing one fix the other?</strong> Merge when one product change would fix both.'));
  A.modeDialog('split', { modeId: 'm-1' });
  html = markup(MG.default, { s: S() });
  assert.ok(html.includes('Does fixing one fix the other?'));
  assert.match(html, /<option value="wo_engine"[^>]*>Work orders<\/option>/);
  assert.match(html, /<option value="data"[^>]*>Data fault: the data owner fixes the records<\/option>/);
  assert.match(html, /<button type="button"[^>]*disabled=""[^>]*>Split<\/button>/);
  A.modeDialog('create', { createdFrom: ['a-1'] });
  html = markup(MG.default, { s: S() });
  assert.ok(html.includes('Definition — one sentence another reviewer can apply'));
  assert.ok(html.includes('<strong>Does fixing one fix the other?</strong>'), 'the boundary field carries the fix test');
});

test('Submit stays off while the dialog lists a problem, and the server’s refusal shows under the form', async () => {
  fresh(loaded());
  A.modeDialog('rename', { modeId: 'm-1' });
  A.modeDialogSet({ name: 'Bad Name' });
  let tree = view(MG.default, { s: S() });
  assert.equal(button(tree, 'Rename').props.disabled, true);
  assert.ok(text(tree).includes('Name: lower-case words joined by underscores'));
  A.modeDialogSet({ name: 'counts_open_jobs' });
  taxonomyApi.renameMode = async () => { throw Object.assign(new Error('That name is taken.'), { status: 422, reason: 'name_taken', body: { detail: { ok: false, reason: 'name_taken', error: 'That name is taken.' } } }); };
  tree = view(MG.default, { s: S() });
  assert.equal(button(tree, 'Rename').props.disabled, false);
  button(tree, 'Rename').props.onClick();
  await settle();
  const after = view(MG.default, { s: S() });
  assert.ok(text(findAll(after, (n) => n.props.role === 'alert')).includes('That name is taken.'));
  button(after, 'Cancel').props.onClick();
  assert.equal(X().tlModesDialog, null);
});

test('File under, Set aside and Retire show their own controls; the dialog is empty for the other kinds', () => {
  fresh(loaded());
  A.modeDialog('file', { annotationId: 'a-1', turnId: 't1' });
  let html = markup(MG.default, { s: S() });
  assert.match(html, /aria-label="File this note under a failure mode"/);
  assert.ok(html.includes('counts_cancelled_jobs_as_open'));
  A.modeDialog('dispose', { annotationId: 'a-1', outcome: 'not_a_failure' });
  html = markup(MG.default, { s: S() });
  assert.ok(html.includes('Not a failure: the answer was right after all') && html.includes('Note for the record'));
  A.modeDialog('retire', { modeId: 'm-1' });
  html = markup(MG.default, { s: S() });
  assert.ok(html.includes('Why it is retired') && html.includes('Its judgments are kept.'));
  A.modeDialog('freeze', { modeId: 'm-1' });
  assert.ok(markup(MG.default, { s: S() }).includes('Freezing is one-way'));
  A.modeDialogClose();
  assert.equal(markup(MG.default, { s: S() }), '');
});
