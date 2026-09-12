// ingestionLive — the ingestion validation agent's live protocol. Fixtures mirror the
// contract transcribed from the route handlers on 12 Sep 2026 (scratchpad contract.md /
// docs/api/ingestion-validation-api.md): run-stateful-with-files answers with
// validation_cases + validation_held, the case object carries verdict / findings /
// candidates / assessment, and decide returns the case with its recorded outcome plus
// bound: {documents, certificates, created}.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import {
  mapCaseToPhase, checksFromFindings, sampleDocFile, sampleDocText, vendorOf, auditRowFromCase
} from '../src/logic/ingestionLive.js';
import { ING_DOCS } from '../src/data/hoistra-access.js';

const B_SEL = 'f12e9629-4a10-4c5f-9d3a-1b2c3d4e5f60'; // Riverside Court
const B_SUG = '4791b70b-8c21-4c5f-9d3a-1b2c3d4e5f61'; // Bishopsgate Tower

// One §13 case as the validate response shapes it (ok, …case, suggestion, message).
const CASE_MISMATCH = {
  ok: true,
  id: 'c-6b7a0d2e-1111-2222-3333-444455556666', organization_id: 'org-1',
  actor_user_id: 'u-1', actor_role: 'user', session_id: 'sess-1',
  document_name: 'Fire alarm service certificate — Sentinel Fire Systems.txt',
  document_id: 'd-1', document_sha256: 'ab12', doc_type: 'certificate',
  selected_building_id: B_SEL, suggested_building_id: B_SUG, final_building_id: null,
  verdict: 'mismatch', confidence: 0.75, status: 'needs_clarification', outcome: null,
  question: 'The document names “Bishopsgate Tower”; this building is “Riverside Court”. Should it be filed there instead?',
  explanation: null, assessment: {},
  findings: [
    { check: 'building_name', direction: 'conflicts', weight: 3.0, claimed: 'Bishopsgate Tower', known: 'Riverside Court', message: 'The document names “Bishopsgate Tower”; this building is “Riverside Court”.' },
    { check: 'vendor', direction: 'unknown', weight: 1.0, claimed: 'Sentinel Fire Systems', known: null, message: 'No vendors on record to check against.' }
  ],
  claims: { buildings: ['Bishopsgate Tower'], codes: [], countries: [], vendors: ['Sentinel Fire Systems'] },
  ontology: { name: 'Riverside Court', vendors: [], counts: {}, is_new: false },
  candidates: [{ building_id: B_SUG, name: 'Bishopsgate Tower', score: 3.0, country_code: 'UK', region: null, selected: false }],
  suggestion: { building_id: B_SUG, name: 'Bishopsgate Tower', score: 3.0, country_code: 'UK', region: null, selected: false },
  message: 'The document names “Bishopsgate Tower”; this building is “Riverside Court”. Should it be filed there instead?',
  events: [], decided_by: null, decided_at: null, decision_note: null,
  created_at: '2026-09-12T10:00:00Z', updated_at: '2026-09-12T10:00:00Z',
  open: true, may_ingest: false
};

// The same case after decide {approve:true, note} over the standing warning.
const CASE_DECIDED = {
  ...CASE_MISMATCH,
  status: 'overridden', outcome: 'overridden', open: false, may_ingest: true,
  final_building_id: B_SEL, decided_by: 'u-1', decided_at: '2026-09-12T10:05:00Z',
  decision_note: 'New vendor — contract onboarding completes next week',
  explanation: 'New vendor — contract onboarding completes next week',
  assessment: { resolves: false, addressed: ['vendor'], unaddressed: ['building_name'], reason: 'The explanation accounts for the vendor but not the property the document names.', method: 'llm:gpt-4o' },
  message: 'Filed against Riverside Court as an explicit override.',
  bound: { documents: 1, certificates: 0, created: 0 }
};

const RUN_RESPONSE = {
  session_id: 'sess-1',
  answer: '**Held for a check — nothing has been filed yet.** Fire alarm service certificate…',
  tool_calls: [], success: true, error: null, interrupted: false, interrupt_payload: null,
  route_metadata: null, workspace_status: null,
  batch_id: null, batch_status: null, batch_progress_pct: null,
  ingested_document_ids: [], ingested_migration_ids: [], ingested_schema_mapping_ids: [],
  citations: [],
  validation_cases: [CASE_MISMATCH],
  validation_held: [CASE_MISMATCH.id]
};

test('mapCaseToPhase maps the three verdicts onto the existing phase names', () => {
  assert.equal(mapCaseToPhase({ ...CASE_MISMATCH, verdict: 'matched' }).phase, 'valid');
  assert.equal(mapCaseToPhase(CASE_MISMATCH).phase, 'mismatch');
  assert.equal(mapCaseToPhase({ ...CASE_MISMATCH, verdict: 'uncertain' }).phase, 'uncertain');
});

test('a mismatch speaks in the case’s own words: the question, then the conflicting findings', () => {
  const m = mapCaseToPhase(CASE_MISMATCH);
  assert.equal(m.bubbles[0], CASE_MISMATCH.question);
  assert.equal(m.bubbles.length, 1, 'a finding message identical to the question is not repeated');
  const twoLines = mapCaseToPhase({ ...CASE_MISMATCH, question: 'Should it be filed at Bishopsgate Tower instead?' });
  assert.equal(twoLines.bubbles.length, 2);
  assert.match(twoLines.bubbles[1], /names “Bishopsgate Tower”/);
  assert.doesNotMatch(twoLines.bubbles[1], /No vendors on record/, 'an unknown direction is not a warning');
});

test('the suggestion comes from the suggestion candidate, the candidates list, or the bare id', () => {
  const a = mapCaseToPhase(CASE_MISMATCH);
  assert.equal(a.suggestedId, B_SUG);
  assert.equal(a.suggestedName, 'Bishopsgate Tower');
  const b = mapCaseToPhase({ ...CASE_MISMATCH, suggestion: null });
  assert.equal(b.suggestedId, B_SUG, 'falls back to candidates[]');
  const c = mapCaseToPhase({ ...CASE_MISMATCH, suggestion: null, candidates: [] });
  assert.equal(c.suggestedId, B_SUG, 'falls back to suggested_building_id');
  assert.equal(c.suggestedName, '', 'id-only — the controller resolves the name');
});

test('checksFromFindings lands every engine check on its UI pill and warns only on conflicts', () => {
  const slots = checksFromFindings(CASE_MISMATCH.findings);
  assert.equal(slots.length, 6);
  assert.equal(slots[0].warn, true, 'building_name conflict tints pill 1');
  assert.equal(slots[2].warn, false, 'an unknown vendor direction never warns');
  assert.equal(slots[2].seen, true);
  const future = checksFromFindings([{ check: 'graph_shape', direction: 'conflicts', message: 'x' }]);
  assert.equal(future[5].warn, true, 'an unrecognised check lands on the last pill rather than vanishing');
  assert.deepEqual(checksFromFindings(null).map((s) => s.warn), [false, false, false, false, false, false]);
});

test('vendorOf reads the contractor out of the sample file names', () => {
  assert.equal(vendorOf('Fire alarm service certificate — Sentinel Fire Systems.pdf'), 'Sentinel Fire Systems');
  assert.equal(vendorOf('Vendor invoice — Apex Lifts · INV-30412.pdf'), 'Apex Lifts');
  assert.equal(vendorOf('no-dash.pdf'), '');
});

test('the sample docs reproduce their scenarios as labelled claims the live checker reads', async () => {
  // the mismatch doc names its true home building
  const mm = sampleDocText(ING_DOCS[0], 'Riverside Court');
  assert.match(mm, /^Property: Bishopsgate Tower$/m);
  assert.match(mm, /^Contractor: Sentinel Fire Systems$/m);
  // the clean doc names the CURRENT selection, whatever it is — that is why it is
  // generated at run time rather than shipped as a fixture
  const clean = sampleDocText(ING_DOCS[1], 'Al Fattan Currency House');
  assert.match(clean, /^Property: Al Fattan Currency House$/m);
  // the uncertain doc names NO property at all
  const un = sampleDocText(ING_DOCS[2], 'Riverside Court');
  assert.doesNotMatch(un, /Property:|Site:/);
  assert.match(un, /^Contractor: Apex Lifts$/m);
  // and the File itself is text/plain with a .txt name, so the claims are parsed as text
  const f = sampleDocFile(ING_DOCS[0], 'Riverside Court');
  assert.equal(f.type, 'text/plain');
  assert.match(f.name, /\.txt$/);
  assert.equal(await f.text(), mm);
});

test('auditRowFromCase shapes a decided case into the exact trail row the seed uses', () => {
  const row = auditRowFromCase(CASE_DECIDED, {
    who: 'Husain Kalabhai', role: 'Admin',
    building: 'Riverside Court', finalB: 'Riverside Court',
    suggested: 'Bishopsgate Tower', explanation: 'New vendor — contract onboarding completes next week'
  });
  assert.equal(row.outcome, 'Overridden');
  assert.equal(row.tone, 'warn');
  assert.equal(row.approval, 'Yes — explicit override');
  assert.equal(row.checks, '2 run · 1 mismatched · 1 inconclusive');
  assert.equal(row.finalB, 'Riverside Court');
  assert.equal(row.suggested, 'Bishopsgate Tower');
  assert.match(row.assessment, /accounts for the vendor/);
  assert.equal(row.when, 'Just now');
  assert.equal(row.live, true);
});

test('a rejected case files no building, and a stored uuid_ document prefix is not shown', () => {
  const row = auditRowFromCase({
    ...CASE_DECIDED, outcome: 'rejected', status: 'rejected', may_ingest: false,
    document_name: '0a1b2c3d-0000-1111-2222-333344445555_Fire alarm service certificate.txt'
  }, { who: 'x', role: 'User', building: 'Riverside Court', finalB: 'Riverside Court' });
  assert.equal(row.finalB, '—');
  assert.equal(row.outcome, 'Rejected');
  assert.equal(row.tone, 'risk');
  assert.equal(row.approval, 'No');
  assert.equal(row.doc, 'Fire alarm service certificate.txt');
});

// ── controller-level: the live run against a stubbed transport ────────────
// Same harness as energyLive.test.mjs: window + fetch stubbed BEFORE HoistraLogic is
// imported; ingestionLiveMethods assigned manually so these pass before the integrator
// wires the mixin into HoistraLogic.js.
globalThis.window = {
  location: { origin: 'http://test.local' }, scrollTo: () => {},
  addEventListener: () => {}, removeEventListener: () => {},
  localStorage: { getItem: () => null, setItem: () => {}, removeItem: () => {} }
};
const calls = [];
let routes = {};
const mk = (status, body) => ({ ok: status < 400, status, statusText: '', text: async () => JSON.stringify(body) });
globalThis.fetch = async (url, opts) => {
  const u = String(url);
  calls.push({ url: u, opts });
  for (const k of Object.keys(routes)) {
    if (u.includes(k)) {
      const r = routes[k];
      if (r instanceof Error) throw r;
      return mk(r.status || 200, r.body);
    }
  }
  throw new TypeError('fetch failed');
};

const { HoistraLogic } = await import('../src/logic/HoistraLogic.js');
const { ingestionLiveMethods } = await import('../src/logic/ingestionLive.js');
Object.assign(HoistraLogic.prototype, ingestionLiveMethods);
// auditLiveMethods now rides the same prototype (HoistraLogic.js), so ingLiveFinish's
// defensive this.auLiveLoad() is real — stub the trail refresh so a decision test
// exercises the ingestion protocol only, not the audit read and its retry timers.
HoistraLogic.prototype.auLiveLoad = async () => {};

const AX_LIVE = [
  { id: B_SEL, name: 'Riverside Court', building_code: 'B-002' },
  { id: B_SUG, name: 'Bishopsgate Tower', building_code: 'B-001' }
];
const ACCOUNT = { id: 'u-1', email: 'husain.kalabhai@plenum-tech.com', full_name: 'Husain Kalabhai', role: 'admin', status: 'active', organization_id: 'org-1', email_verified: true };

test('ingRunLive sends the file with a minted session and the resolved building_id, and lands on the case’s phase', async () => {
  routes = { '/api/workflow/run-stateful-with-files': { body: RUN_RESPONSE } };
  calls.length = 0;
  const c = new HoistraLogic();
  c.setState({ account: ACCOUNT, axBldsLive: AX_LIVE, ingOn: true, ingB: 'Riverside Court', ingDoc: 0 });
  await c.ingRunLive();
  const form = calls[0].opts.body;
  assert.equal(form.get('building_id'), B_SEL);
  assert.ok(String(form.get('session_id')).length >= 8, 'a session id was minted');
  assert.equal(form.get('message'), 'Validate and ingest this document.');
  const sent = form.get('files');
  assert.match(sent.name, /\.txt$/);
  assert.match(await sent.text(), /Property: Bishopsgate Tower/);
  assert.equal(c.state.ingPhase, 'mismatch');
  assert.equal(c.state.ingCase.id, CASE_MISMATCH.id);
  assert.equal(c.state.ingChecked, 6, 'the pills resolved, they did not fake-complete early');
  const v = c.ingestionVals(c.state);
  assert.equal(v.ingChecks[0].fg, 'var(--st-warn)', 'the conflicting building_name finding tints pill 1');
  assert.equal(v.ingChecks[2].fg, 'var(--st-ok)', 'the unknown vendor direction does not');
  assert.ok(v.ingActions.some((a) => a.label === 'Switch to Bishopsgate Tower and re-validate'));
  assert.ok(v.ingActions.some((a) => a.label === 'Keep Riverside Court'));
});

test('ingDecideLive records the decision, displays the case’s own outcome and prepends the real audit row', async () => {
  routes = { '/decide': { body: CASE_DECIDED } };
  calls.length = 0;
  const c = new HoistraLogic();
  c.setState({
    account: ACCOUNT, axBldsLive: AX_LIVE, ingOn: true,
    ingB: 'Riverside Court', ingB0: 'Riverside Court', ingCase: CASE_MISMATCH,
    ingPhase: 'assess', ingReason: 'New vendor — contract onboarding completes next week'
  });
  const before = c.state.audit.length;
  await c.ingDecideLive(true);
  const body = JSON.parse(calls[0].opts.body);
  assert.equal(body.approve, true);
  assert.equal(body.note, 'New vendor — contract onboarding completes next week');
  assert.equal(c.state.ingPhase, 'done');
  assert.equal(c.state.ingOutcome.outcome, 'Overridden', 'display outcome comes from the case, not a local guess');
  assert.match(c.state.ingOutcome.msg, /Filed against Riverside Court/);
  assert.match(c.state.ingOutcome.msg, /Bound 1 document/);
  assert.equal(c.state.audit.length, before + 1);
  const row = c.state.audit[0];
  assert.equal(row.who, 'Husain Kalabhai');
  assert.equal(row.role, 'Admin');
  assert.equal(row.outcome, 'Overridden');
  assert.equal(row.suggested, 'Bishopsgate Tower');
  const done = c.ingestionVals(c.state).ingMsgs;
  assert.match(done[done.length - 1].text, /^Overridden — /);
});

test('a 403 rights problem is an agent bubble with Close and no Retry', async () => {
  routes = { '/api/workflow/run-stateful-with-files': { status: 403, body: { detail: { ok: false, error: 'You do not have the right to ingest documents.', reason: 'cannot_ingest' } } } };
  const c = new HoistraLogic();
  c.setState({ account: ACCOUNT, axBldsLive: AX_LIVE, ingOn: true, ingB: 'Riverside Court' });
  await c.ingRunLive();
  assert.match(c.state.ingLiveErr, /does not have the right to ingest/);
  assert.equal(c.state.ingLiveRetriable, false);
  assert.equal(c.state.ingOffline, false, 'a rights problem is not an offline fallback');
  const v = c.ingestionVals(c.state);
  assert.ok(!v.ingActions.some((a) => a.label === 'Retry'));
  assert.ok(v.ingActions.some((a) => a.label === 'Close'));
  assert.match(v.ingMsgs[v.ingMsgs.length - 1].text, /can ingest/);
});

test('any other API failure keeps the case path and offers a Retry', async () => {
  routes = { '/api/workflow/run-stateful-with-files': { status: 500, body: { detail: 'validation engine crashed' } } };
  const c = new HoistraLogic();
  c.setState({ account: ACCOUNT, axBldsLive: AX_LIVE, ingOn: true, ingB: 'Riverside Court' });
  await c.ingRunLive();
  assert.equal(c.state.ingOffline, false);
  assert.equal(c.state.ingLiveRetriable, true);
  const v = c.ingestionVals(c.state);
  assert.ok(v.ingActions.some((a) => a.label === 'Retry'));
});

test('a network-level failure (no status) falls back to the canned run, prefixed offline demo', async () => {
  routes = {}; // every fetch throws TypeError('fetch failed')
  const c = new HoistraLogic();
  c.setState({ account: ACCOUNT, axBldsLive: AX_LIVE, ingOn: true, ingB: 'Riverside Court', ingDoc: 0 });
  await c.ingRunLive();
  assert.equal(c.state.ingOffline, true);
  assert.equal(c.state.ingPhase, 'run');
  const v = c.ingestionVals(c.state);
  assert.match(v.ingMsgs[1].text, /^\(offline demo\)/);
  clearInterval(c._ingT); // the canned walkthrough's timer — stop it so the runner exits
});

test('with no live building register at all, the run is the offline demo, not a guessed id', async () => {
  routes = { '/api/workflow/run-stateful-with-files': { body: RUN_RESPONSE } };
  calls.length = 0;
  const c = new HoistraLogic();
  c.setState({ ingOn: true, ingB: 'Riverside Court' }); // seed chips only — no axBldsLive, no account allocation
  await c.ingRunLive();
  assert.equal(calls.length, 0, 'nothing was sent — there is no id to bind against');
  assert.equal(c.state.ingOffline, true);
  clearInterval(c._ingT);
});

test('the assess buttons read as confirmation when the assessment resolves, override when it stands', () => {
  const c = new HoistraLogic();
  const base = { account: ACCOUNT, ingOn: true, ingB: 'Riverside Court', ingPhase: 'assess', ingReason: 'took over the contract in May' };
  c.setState({ ...base, ingCase: { ...CASE_MISMATCH, explanation: 'took over the contract in May', assessment: { resolves: true, addressed: ['building_name'], unaddressed: [], reason: 'That accounts for the mismatch.', method: 'llm:x' }, message: 'Confirm to file it here?' } });
  let v = c.ingestionVals(c.state);
  assert.equal(v.ingActions[0].label, 'Yes — confirm and ingest');
  assert.match(v.ingMsgs[v.ingMsgs.length - 1].text, /That accounts for the mismatch\. Confirm to file it here\?/);
  c.setState({ ingCase: { ...CASE_MISMATCH, explanation: 'x', assessment: { resolves: false, addressed: [], unaddressed: ['building_name'], reason: 'The concern stands.', method: 'llm:x' }, message: 'Override?' } });
  v = c.ingestionVals(c.state);
  assert.equal(v.ingActions[0].label, 'Yes — override and ingest');
});

test('the setup chips come from the live register when it is loaded, the account allocation next, the seed last', () => {
  const c = new HoistraLogic();
  c.setState({ axBldsLive: AX_LIVE });
  assert.deepEqual(c.ingBldList(), ['Riverside Court', 'Bishopsgate Tower']);
  c.setState({ axBldsLive: null, account: { ...ACCOUNT, buildings: [{ id: B_SUG, name: 'Bishopsgate Tower' }] } });
  assert.deepEqual(c.ingBldList(), ['Bishopsgate Tower']);
  assert.equal(c.ingBldIdOf('Bishopsgate Tower'), B_SUG, 'the allocation also resolves name → id');
  c.setState({ account: ACCOUNT });
  assert.equal(c.ingBldList().length, 8, 'the AX seed names remain the last resort');
});

test('picking a sample doc puts a chosen real file aside, and the picker exposes its name', () => {
  const c = new HoistraLogic();
  c.setState({ ingOn: true, ingPhase: 'setup' });
  const f = new File(['hello'], 'my-cert.pdf', { type: 'application/pdf' });
  c.ingestionVals(c.state).ingFilePick({ target: { files: [f], value: '' } });
  let v = c.ingestionVals(c.state);
  assert.equal(v.ingFileName, 'my-cert.pdf');
  assert.equal(v.ingFileTick, 'ph-radio-button');
  assert.equal(v.ingDocs[0].tick, 'ph-circle', 'no sample doc reads selected while a file is picked');
  v.ingDocs[1].pick();
  v = c.ingestionVals(c.state);
  assert.equal(v.ingFileName, '');
  assert.equal(v.ingDocs[1].tick, 'ph-radio-button');
});
