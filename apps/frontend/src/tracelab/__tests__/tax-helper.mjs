// A helper for the taxonomy tests (not a test file itself: node --test runs only *.test.mjs). Importing it sets
// the browser globals the module needs and a fetch that answers 404 for anything a test did not stub, so a stray
// request fails inside its own state instead of reaching a network. It hands back the real actions and view
// models, a way to put the store at a new seq with the taxonomy slice set as a test needs, and the fixtures the
// four taxonomy test files share.
globalThis.window = { location: { origin: 'http://test.local', pathname: '/', search: '' }, history: { replaceState: () => {} }, scrollTo: () => {} };
globalThis.document = { visibilityState: 'visible' };
export const net = { requests: [], handlers: {} };
globalThis.fetch = async (url, opts) => {
  const u = new URL(String(url));
  net.requests.push({ method: (opts && opts.method) || 'GET', url: u, body: opts && typeof opts.body === 'string' ? JSON.parse(opts.body) : undefined });
  const out = net.handlers[u.pathname] || [404, { detail: { reason: 'not_mocked', error: 'not mocked' } }];
  return { ok: out[0] >= 200 && out[0] < 300, status: out[0], statusText: String(out[0]), text: async () => JSON.stringify(out[1]), headers: { get: () => null } };
};

export const { taxonomyApi } = await import('../taxonomyApi.js');
export const { reviewApi } = await import('../reviewApi.js');
export const A = await import('../taxonomyActions.js');
export const C = await import('../logic/taxCommon.js');
export const { samplingInitial } = await import('../logic/sampling.js');
export const { reviewInitial } = await import('../logic/annotate.js');
export const { tlStore, initialState, resetForCompany } = await import('../store.js');
export const { setActingOrg } = await import('../../api/client.js');
export const P = '/backend/trace-lab/api/trace-lab';

export const S = () => tlStore.get();
export const X = () => C.taxOf(S());
export const tick = () => new Promise((r) => setTimeout(r, 0));
export const settle = async () => { for (let i = 0; i < 8; i += 1) await tick(); };

// A fresh store at a new seq with the taxonomy slice set as the test needs, and (through `over`) the sampling and
// review slices and any store key.
export function fresh(tax, over) {
  const seq = S().seq + 1;
  const o = Object.assign({}, over);
  const sampling = o.sampling;
  const review = o.review;
  delete o.sampling;
  delete o.review;
  tlStore.set(Object.assign(initialState(), { seq, tab: 'progress' }, o, {
    taxonomy: Object.assign(C.taxonomyInitial(seq), tax || {}),
    sampling: Object.assign(samplingInitial(seq), sampling || {}),
    review: Object.assign(reviewInitial(seq), review || {}) }));
}

export const SERVER = { ok: true, is_owner: true, fix_test: 'Does fixing one fix the other?',
  fix_test_text: 'Merge when one product change would fix both. Split when the examples need different fixes, even if they use similar words.',
  grouping_note: 'Proposed by AI from reviewers’ notes. Read every supporting note before accepting.',
  agents: [{ key: 'wo_engine', label: 'Work orders' }, { key: 'udr', label: 'Records lookup' }],
  levels: [{ key: 'technical', label: 'Technical: the records decide' }, { key: 'data', label: 'Data fault: the data owner fixes the records' }] };
export const META = (isOwner) => ({ agents: SERVER.agents, levels: SERVER.levels, fixTest: SERVER.fix_test, fixTestText: SERVER.fix_test_text,
  groupingNote: SERVER.grouping_note, isOwner });
export const MODE = { id: 'm-1', name: 'counts_cancelled_jobs_as_open', status: 'candidate', version: 3, level: 'technical', agent: 'wo_engine',
  definition: 'Present when the answer counts cancelled jobs as open.', boundary: '', requirement_ids: [], positive_turn_ids: ['t1'],
  close_negative_turn_ids: [], created_from: ['a-1', 'a-2'], level_label: 'Technical: the records decide', agent_label: 'Work orders',
  counts: { present: 6, absent: 2, unchecked: 4 }, contract_missing: ['boundary', 'positives'] };
// What the platform team (isOwner, the default), a company admin or a superadmin viewing as a company has loaded.
export const loaded = (extra, isOwner) => Object.assign({ tlModes: [MODE], tlModesMeta: META(isOwner !== false), tlModesLoadedAt: 1 }, extra || {});
export const refusal = (status, reason, error, extra) => Object.assign(new Error(error), { status, reason, body: { detail: Object.assign({ ok: false, error, reason }, extra || {}) } });

const apiBefore = Object.assign({}, taxonomyApi);
const reviewBefore = Object.assign({}, reviewApi);
export const reads = { progress: 0 };

// Reads a dialog or a refresh fires on its own answer with something empty; a test replaces the ones it cares about.
export function installDefaults() {
  setActingOrg(null);
  net.requests = [];
  net.handlers = {};
  reads.progress = 0;
  taxonomyApi.modes = async () => Object.assign({ modes: [MODE] }, SERVER);
  taxonomyApi.mode = async () => ({ ok: true, mode: MODE });
  taxonomyApi.modeProposals = async () => ({ ok: true, proposals: [] });
  taxonomyApi.unfiledNotes = async () => ({ ok: true, notes: [], total: 0, hidden: 0 });
  taxonomyApi.requirements = async () => ({ ok: true, requirements: [], areas: [], levels: [] });
  taxonomyApi.labelsMatrix = async () => ({ ok: true, modes: [], rows: [] });
  taxonomyApi.proposals = async () => ({ ok: true, served_at: 's', total: 0, hidden: 0, proposals: [] });
  taxonomyApi.proposalRuns = async () => ({ ok: true, runs: [] });
  taxonomyApi.turnLabels = async () => ({ ok: true, hidden: false, cells: [] });
  reviewApi.progress = async () => { reads.progress += 1; return { ok: true }; };
  fresh();
}
export function restoreApis() {
  Object.assign(taxonomyApi, apiBefore);
  Object.assign(reviewApi, reviewBefore);
  A.stopPolls();
}
