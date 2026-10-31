// taxProgressScreens — the failure-mode part of the Progress tab (views/ProgressTaxonomy.jsx, views/Requirements.jsx) and
// how it sits inside the Phase 4 Progress tab (views/Progress.jsx). Rendered to markup each says what its view model
// says; with the screens sharing the test's store, a press calls the action it is for.
import { test, beforeEach, afterEach, after } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { taxonomyApi, A, S, X, settle, fresh, loaded, SERVER, installDefaults, restoreApis } from './tax-helper.mjs';
import { button, cleanTaxScreens, find, findAll, taxScreen, text, view } from './tax-screen-helper.mjs';

const React = (await import('react')).default;
const { renderToStaticMarkup } = await import('react-dom/server');
const PT = await taxScreen('views/ProgressTaxonomy.jsx');
const RQ = await taxScreen('views/Requirements.jsx');
const PR = await taxScreen('views/Progress.jsx');
const { progressView } = await import('../logic/sampling.js');

after(cleanTaxScreens);
beforeEach(installDefaults);
afterEach(restoreApis);

// Built at run time, so this file itself passes the app's internal-names scan.
const INTERNAL = ['wo_engine', ['doc', 'rag'].join('_'), 'energy_intelligence', 'contract_performance', 'orchestrator'];
const DOC = 'apps/backend/x/' + ['skills', 'compliance'].join('/') + '/' + ['SKILL', 'md'].join('.');
const markup = (C, props) => renderToStaticMarkup(React.createElement(C, props));
const here = (rel) => readFileSync(new URL('../' + rel, import.meta.url), 'utf8');

const PRG = { ok: true, block: 10, open_batches: 1, batches: [{ id: 'b-1', plan_step: 'initial_uniform', strategy: 'uniform', counts_for_prevalence: true, selected: 15, requested: 15 }],
  open_coding: { reviewed: 12, wrong: 4, right: 8, missing: 3, missing_turns: [], missing_more: 0, reviewers: [] },
  fair_sample: { label: 'From fair (uniform) batches only', rate: null, ci_low: null, ci_high: null, wrong: 0, right: 0, disputed: 0 }, agreement: null, coverage: null,
  completeness: { reviewed_turns: 12, modes: 1, required: 12, done: 9, missing: 3, missing_preview: [{ turn_id: 't1', question: 'How many open?', missing: 1 }], more: 0 },
  modes: [{ mode_id: 'm-1', name: 'counts_cancelled_jobs_as_open', status: 'confirmed', level: 'technical', first_failure: 3, any_instance: 6, decided: 12,
    sample_fraction: { present: 6, decided: 12, fraction: 0.5, kind: 'sample_fraction' }, pending_suggestions: 0 }],
  discovery: [{ block: 1, reviews_through: 10, new_modes: 1, cumulative_modes: 1 }, { block: 2, reviews_through: 12, new_modes: 0, cumulative_modes: 1 }],
  discovery_by_batch: [], saturation: { hint: false, text: '', final_uniform_text: '' }, re_review: { count: 0, text: '', turn_ids: [] }, unfiled_notes: 1 };
// The Progress tab as the platform team (owner, the default) or a company admin reads it; `tax` is the taxonomy slice.
const prog = (tax, owner, progress) => fresh(loaded(tax || {}, owner), { tab: 'progress', sampling: { progress: progress || PRG, progressLoadedAt: 1 } });

test('the new screens name no internal service, and Requirements keeps document paths inside the technical panel', () => {
  for (const rel of ['views/ProgressTaxonomy.jsx', 'views/Requirements.jsx']) {
    const src = here(rel);
    for (const internal of INTERNAL) assert.equal(src.includes(internal), false, rel + ' names ' + internal);
  }
  assert.ok(here('views/Requirements.jsx').includes("import RawPanel from './RawPanel.jsx';"));
});

test('Progress shows completeness, the treemap with status pills, first failure beside any instance, and the new-mode curve', () => {
  prog();
  const html = markup(PT.default, { s: S() });
  assert.ok(html.includes('9 of 12 judgments made'));
  assert.ok(html.includes('>Show missing judgments</button>'));
  assert.match(html, /class="tl-tree-block tl-tone-dormant"[^>]*>.*counts_cancelled_jobs_as_open.*6 present.*class="tl-pill tl-pill-dormant">Candidate</);
  assert.ok(html.includes('>First failure</th><th') && html.includes('>Any instance</th>'));
  assert.ok(html.includes('50% of 12 judged'));
  assert.match(html, /<svg[^>]*role="img" aria-label="New failure modes named in each block of 10 reviews\."/);
  assert.ok(html.includes('New failure modes are still appearing.'));
  assert.ok(html.includes('Requirements the failure modes cite'));
  assert.ok(html.includes('Shares are of the reviewed sample, not rates for all answers.'));
});

test('missing figures read a dash, never zero, before the service has sent the failure-mode rows', () => {
  prog({}, true, { ok: true, block: 10, open_batches: 0, batches: [], open_coding: { reviewed: 0, wrong: 0, right: 0, missing: 0, missing_turns: [], missing_more: 0, reviewers: [] } });
  const html = markup(PT.default, { s: S() });
  assert.ok(html.includes('Shown once a failure mode is confirmed.'));
  assert.equal(html.includes('judgments made'), false);
  assert.equal(html.includes('>Show missing judgments</button>'), false);
  assert.ok(html.includes('>—</div>'), 'the curve and the figures say so with a dash');
});

test('unfiled notes offer File under to a reviewer and New mode or Not a failure to the platform team only', () => {
  const notes = [{ annotation_id: 'a-7', turn_id: 't7', conversation_key: 's:c7', note: 'Counted the cancelled jobs.', quote: '3 cancelled', step_label: 'Work orders', reviewer_email: 'ada@example.com' }];
  const unfiled = { tlUnfiled: notes, tlUnfiledTotal: 1, tlUnfiledLoadedAt: 1 };
  prog(unfiled, true);
  const owner = markup(PT.default, { s: S() });
  for (const b of ['File under…', 'New failure mode', 'Not a failure', 'No mode fits']) assert.ok(owner.includes('>' + b + '</button>'), b);
  assert.equal(owner.includes('>Open the answer</button>'), false, 'the platform team has no company to open it in');
  prog(unfiled, false);
  const admin = markup(PT.default, { s: S() });
  assert.ok(admin.includes('>File under…</button>'));
  for (const b of ['New failure mode', 'Not a failure', 'No mode fits']) assert.equal(admin.includes('>' + b + '</button>'), false, b);
  assert.ok(admin.includes('>Open the answer</button>'));
  assert.equal(admin.includes('AI proposal runs'), false);
});

test('the suggestion queue renders with nothing ticked and the audit panel before any approval', () => {
  const props = [1, 2].map((i) => ({ id: 'p-' + i, kind: 'depth_scan', mode_id: 'm-1', mode_name: 'counts_cancelled_jobs_as_open', suggestion: 'present',
    evidence: 'Evidence ' + i, quote: 'job ' + i, question: 'Question ' + i, base: null, notes: [] }));
  prog({ tlProposals: props, tlProposalsLoadedAt: 1, tlProposalsTotal: 2, tlProposalsServedAt: 's' }, false);
  const html = markup(PT.default, { s: S() });
  assert.equal((html.match(/type="checkbox" aria-label="Tick: /g) || []).length, 2);
  assert.equal(/aria-label="Tick: [^"]*" checked=""/.test(html), false, 'nothing is ticked for the reviewer');
  assert.ok(html.includes('Nothing is ticked for you. Tick only what you have read. 0 ticked.'));
  prog({ tlProposals: props, tlProposalsLoadedAt: 1, tlProposalsServedAt: 's', tlProposalsSelected: ['p-1'], tlProposalsCheckIds: ['p-1'], tlProposalsCheckAction: 'accept',
    tlProposalsCheck: { allowed: false, refused: [{ rule: 2, detail: 'The suggestions repeat the same evidence; decide them one at a time.' }], flagged: [], warnings: [], stats: { write: 1 }, spot_check: { seed: 's', items: [] } } }, false);
  const checked = markup(PT.default, { s: S() });
  assert.ok(checked.includes('Rule 2 — The evidence repeats: The suggestions repeat the same evidence; decide them one at a time.'));
  assert.match(checked, /<button type="button"[^>]*disabled=""[^>]*>Approve 1<\/button>/);
});

test('the spot-check hides the AI’s suggestion and Approve stays off until every spot-check answer is judged', () => {
  const props = [{ id: 'p-1', kind: 'depth_scan', mode_id: 'm-1', mode_name: 'counts_cancelled_jobs_as_open', suggestion: 'present', evidence: 'Secret evidence', quote: 'secret quote', question: 'Question 1', base: null, notes: [] }];
  prog({ tlProposals: props, tlProposalsLoadedAt: 1, tlProposalsServedAt: 's', tlProposalsSelected: ['p-1'], tlProposalsCheckIds: ['p-1'], tlProposalsCheckAction: 'accept',
    tlProposalsCheck: { allowed: true, refused: [], flagged: [], warnings: [], stats: { write: 1 }, spot_check: { seed: 's', items: [{ proposal_id: 'p-1', mode_id: 'm-1', question: 'Question 1' }] } } }, false);
  const html = markup(PT.default, { s: S() });
  assert.equal(html.includes('Secret evidence') || html.includes('secret quote'), false);
  assert.ok(html.includes('Hidden while you do the spot-check'));
  assert.ok(html.includes('>Present</button>') && html.includes('>Absent</button>'));
  assert.match(html, /<button type="button"[^>]*disabled=""[^>]*>Approve 1<\/button>/);
});

test('the platform team’s proposal runs show Estimate before Start, and Start stays off until an estimate allows it', () => {
  prog({ tlProposalsRuns: [], tlProposalsRunsLoadedAt: 1 }, true);
  const html = markup(PT.default, { s: S() });
  assert.ok(html.includes('AI proposal runs') && html.includes('No AI proposal runs yet.'));
  assert.ok(html.includes('>Estimate</button>'));
  assert.match(html, /<button type="button"[^>]*disabled=""[^>]*>Start<\/button>/);
  assert.ok(html.includes('Group the unfiled notes'));
});

test('a press on Show missing judgments filters the grid, and an accepted suggestion is decided through the server', async () => {
  const props = [{ id: 'p-1', kind: 'depth_scan', mode_id: 'm-1', mode_name: 'counts_cancelled_jobs_as_open', suggestion: 'present', evidence: 'e', quote: 'q', question: 'Question 1', base: 'B', notes: [], turn_id: 't1' }];
  prog({ tlProposals: props, tlProposalsLoadedAt: 1, tlProposalsServedAt: 's' }, false);
  const decided = [];
  taxonomyApi.decideProposal = async (id, body) => { decided.push([id, body]); return { ok: true }; };
  taxonomyApi.labelsMatrix = async () => ({ ok: true, modes: [], rows: [] });
  const tree = view(PT.default, { s: S() });
  button(tree, 'Show missing judgments').props.onClick();
  await settle();
  assert.equal(X().tlMatrixFilter.state, 'missing');
  button(tree, 'Accept: it shows this').props.onClick();
  await settle();
  assert.deepEqual(decided, [['p-1', { decision: 'accept', present: null, base: 'B' }]]);
});

test('the block buttons set the reviews per block through the sampling actions', async () => {
  prog();
  const tree = view(PT.default, { s: S() });
  const group = find(tree, (n) => n.props['aria-label'] === 'Reviews per block');
  assert.deepEqual(findAll(group, (n) => n.type === 'button').map((b) => text(b)), ['5 per block', '10 per block', '20 per block']);
  assert.equal(progressView(S()).blocks.find((b) => b.n === 10).on, true);
  findAll(group, (n) => n.type === 'button')[0].props.onClick();
  await settle();
  assert.equal(progressView(S()).blocks.find((b) => b.n === 5).on, true);
});

// ── Requirements ─────────────────────────────────────────────────────────────────────────────────────────────
const REQS = [{ id: 'r1', code: 'GEN-1', revision: 2, status: 'active', area: 'GEN', area_label: 'General', level: 'technical', statement: 'Answer from the records the question names.',
  implementations: [{ doc: DOC, section: 'Answers', line: 4, quote: 'answer from the records', quote_status: 'drift' }],
  motivating_turn_ids: [], citing_modes: [] },
{ id: 'r0', code: 'GEN-1', revision: 1, status: 'superseded', area: 'GEN', level: 'technical', statement: 'Older wording.', implementations: [], citing_modes: [] }];

test('requirements show the quote and its check; document paths stay inside the technical panel', () => {
  prog({ tlReqs: REQS, tlReqsLoadedAt: 1, tlReqsMeta: { areas: [{ key: 'GEN', label: 'General' }], levels: SERVER.levels } }, true);
  const html = markup(RQ.default, { s: S() });
  assert.ok(html.includes('GEN-1</strong> · revision 2 · Active · Technical: the records decide'));
  assert.ok(html.includes('The document no longer contains this quote'));
  assert.ok(html.includes('Earlier revisions (1)'));
  const outside = html.replace(/<details class="tl-raw"[\s\S]*?<\/details>/g, '');
  assert.equal(outside.includes(DOC), false);
  assert.equal(html.includes(DOC), false, 'closed, the panel carries no path at all');
  assert.ok(html.includes('>Load the starting requirements</button>'));
  assert.ok(html.includes('>Revise</button>') && html.includes('>Withdraw</button>'));
  prog({ tlReqs: REQS, tlReqsLoadedAt: 1, tlReqsMeta: { areas: [], levels: SERVER.levels } }, false);
  const admin = markup(RQ.default, { s: S() });
  for (const b of ['Load the starting requirements', 'New requirement', 'Revise', 'Withdraw']) assert.equal(admin.includes('>' + b + '</button>'), false, b);
});

test('the requirement form validates through the view model and Save sends it', async () => {
  prog({ tlReqs: [], tlReqsLoadedAt: 1, tlReqsMeta: { areas: [{ key: 'GEN', label: 'General' }], levels: SERVER.levels } }, true);
  const tree = view(RQ.default, { s: S() });
  button(tree, 'New requirement').props.onClick();
  const open = view(RQ.default, { s: S() });
  const form = find(open, (n) => n.props.role === 'form');
  assert.equal(form.props['aria-label'], 'New requirement');
  A.reqsSet({ area: 'GEN', level: 'technical', statement: 'Answer only from the records.' });
  A.reqsImpl(0, { doc: 'docs/a.md', section: 'A', line: '3', quote: 'only from the records' });
  const sent = [];
  taxonomyApi.createRequirement = async (body) => { sent.push(body); return { ok: true, requirement: { id: 'r9' } }; };
  button(view(RQ.default, { s: S() }), 'Add it').props.onClick();
  await settle();
  assert.equal(sent.length, 1);
  assert.equal(sent[0].statement, 'Answer only from the records.');
});

// ── inside the Phase 4 Progress tab ──────────────────────────────────────────────────────────────────────────
test('inside the Progress tab the failure-mode rows replace the old discovery card', () => {
  prog();
  const html = markup(PR.default, { s: S() });
  assert.equal(html.includes('>Discovery</div>'), false, 'the wrong-per-block card steps aside once failure modes are counted');
  assert.ok(html.includes('Discovery of failure modes'));
  assert.ok(html.includes('Label completeness'));
  const m2Only = Object.assign({}, PRG, { discovery: { block: 10, points: [{ block: 1, reviews_through: 10, wrong: 3, partial: false }] }, saturation: { available: false, hint: false } });
  prog({}, true, m2Only);
  const old = markup(PR.default, { s: S() });
  assert.ok(old.includes('>Discovery</div>'));
  assert.equal(old.includes('Discovery of failure modes'), true, 'the failure-mode part is always there; only the older card depends on the shape');
  assert.equal((old.match(/ per block<\/button>/g) || []).length, 3, 'one set of block-size buttons while the older card shows them');
  prog();
  assert.equal((markup(PR.default, { s: S() }).match(/ per block<\/button>/g) || []).length, 3, 'and one set once they have moved to the failure-mode card');
});
