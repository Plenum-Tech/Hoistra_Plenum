// taxCommon — what every part of the taxonomy work shares: the `taxonomy` slice of the store (failure modes,
// unfiled notes, requirements, the Labels grid, AI proposals and the open answer's suggestions), who may do what,
// and the small text helpers. Pure: no requests and no store access; taxonomyActions.js acts, the other tax*.js and
// the logic files shape what is drawn.
//
// The state is the `taxonomy` slice. The store merges patches and resetForCompany() only resets the keys store.js
// lists, so the slice carries the store's `seq` it was made under (taxonomyInitial(seq)); taxOf(s) hands back a
// slice from an earlier seq, or none, as an untouched one. A company switch therefore empties it without store.js
// having to know it exists. The slice's keys keep the names the plan gave them (tlModes…, tlUnfiled…, tlReqs…,
// tlMatrix…, tlProposals…, tlSug…, tlWrites).
//
// Who may do what (D7) is the service's decision; this module only hides what a role cannot use. The platform team
// (a superadmin who is not viewing as a company) changes failure modes, sets notes aside, decides grouping drafts,
// runs proposals and edits requirements; the service says who the person is on the failure-mode list (`is_owner`).
// Everyone else reviews inside one company: a company admin, or a superadmin viewing as a company (the app's
// acting company, api/client.js getActingOrg). Those reviewers do not create failure modes: they PROPOSE them (from
// their own company's unfiled notes) and the platform team accepts or rejects each with a reason (G0 addendum 4(3)).
import { getActingOrg } from '../../api/client.js';

export const OWNER_ONLY_HINT = 'Stop viewing as a company to change failure modes.';
export const DATA_FAULT_TEXT = 'Data fault: routed to the data owner. Not used for prompt fixes or judges.';
export const SAME_FIX_TEXT = 'Two notes share a mode only if the same fix clears both.';
export const STATUS_TEXT = { candidate: 'Candidate', confirmed: 'Confirmed', frozen: 'Frozen', retired: 'Retired' };
export const STATUS_TONE = { candidate: 'dormant', confirmed: 'ok', frozen: 'focus', retired: 'dormant' };

export const errText = (e) => (e && e.message) || String(e);
export const clip = (v, n) => { const t = String(v || ''); return t.length > n ? t.slice(0, n - 1) + '…' : t; };
export const when = (iso) => (iso ? String(iso).slice(0, 16).replace('T', ' ') : '');
export const pct = (v) => (v === null || v === undefined || Number.isNaN(Number(v)) ? '—' : Math.round(Number(v) * 100) + '%');
export const usd = (v) => (v === null || v === undefined || v === '' || Number.isNaN(Number(v)) ? '—' : '$' + Number(v).toFixed(2));
export const val = (e) => (e && e.target ? (e.target.type === 'checkbox' ? e.target.checked : e.target.value) : e);
export const has = (o, k) => Object.prototype.hasOwnProperty.call(o || {}, k);

// The `taxonomy` slice at its default, made under the store's `seq`. A fresh object (and fresh arrays) every call.
export function taxonomyInitial(seq) {
  return {
    seq: seq || 0,
    // failure modes, the open mode's drawer and its dialogs, unfiled notes, requirements
    tlModes: null, tlModesMeta: null, tlModesLoading: false, tlModesErr: '', tlModesLoadedAt: null,
    tlModesDrawerId: null, tlModesDrawer: null, tlModesDrawerLoading: false, tlModesDrawerErr: '',
    tlModesDialog: null, tlModesDialogErr: '', tlModesDialogMissing: [], tlModesNotice: '',
    tlUnfiled: null, tlUnfiledTotal: null, tlUnfiledHidden: 0, tlUnfiledLoading: false, tlUnfiledErr: '', tlUnfiledLoadedAt: null,
    tlUnfiledChosen: [], tlUnfiledUndo: null,
    // failure modes companies proposed (the platform team's queue; a company's own list) and what was said about them
    tlModesProposed: null, tlModesProposedLoading: false, tlModesProposedErr: '', tlModesProposedLoadedAt: null, tlModesProposedNotice: '',
    tlReqs: null, tlReqsMeta: null, tlReqsLoading: false, tlReqsErr: '', tlReqsLoadedAt: null,
    tlReqsForm: null, tlReqsFormErr: '', tlReqsNotice: '',
    // the Labels grid
    tlMatrix: null, tlMatrixLoading: false, tlMatrixErr: '', tlMatrixLoadedAt: null,
    tlMatrixFilter: { batch_id: '', state: 'all', offset: 0 }, tlMatrixCell: null, tlMatrixHistory: null, tlMatrixHistoryErr: '',
    // AI proposals: the queue, the audit before a bulk approval, proposal runs
    tlProposals: null, tlProposalsKind: 'depth_scan', tlProposalsTotal: null, tlProposalsHidden: 0, tlProposalsServedAt: null,
    tlProposalsOffset: 0, tlProposalsLoading: false, tlProposalsErr: '', tlProposalsLoadedAt: null,
    tlProposalsSelected: [], tlProposalsCheck: null, tlProposalsCheckAction: null, tlProposalsCheckIds: [], tlProposalsCheckServedAt: null,
    tlProposalsSpot: {}, tlProposalsResult: null, tlProposalsDrafts: {},
    tlProposalsRuns: null, tlProposalsRunsLoading: false, tlProposalsRunsErr: '', tlProposalsRunsLoadedAt: null,
    tlProposalsRunForm: { kind: 'depth_scan', mode_id: '', days: 30, usd_cap: '', limit: 200, include_candidates: false }, tlProposalsResumeCap: {},
    tlProposalsEstimate: null, tlProposalsEstimateErr: '', tlProposalsEstimating: false,
    // AI suggestions on the open answer in Conversations
    tlSugTurnId: null, tlSug: null, tlSugCells: null, tlSugHidden: false, tlSugLoading: false, tlSugErr: '', tlSugLoadedAt: null,
    // writes in flight or failed, by key: { busy, error, conflict, canRetry }
    tlWrites: {}
  };
}

// The slice in force for this state: the one made under the store's current seq, else an untouched one.
export function taxOf(s) {
  const seq = (s && s.seq) || 0;
  return s && s.taxonomy && s.taxonomy.seq === seq ? s.taxonomy : taxonomyInitial(seq);
}

// The platform team: the service said so on the failure-mode list, and no company is being viewed.
export const owns = (s) => !!(taxOf(s).tlModesMeta && taxOf(s).tlModesMeta.isOwner) && !getActingOrg();
// A reviewer who proposes failure modes: anyone who is not the platform team, once the service has said who the person
// is (a page that has not heard yet offers nothing). A superadmin viewing as a company proposes for that company.
export const proposes = (s) => !!taxOf(s).tlModesMeta && !owns(s);
// A superadmin viewing as a company: reads and reviews, but the platform team's changes are not theirs to make.
export const acting = (s) => !!getActingOrg();
// A company to review in: a company admin, or a superadmin viewing as one. Not the platform team.
export const hasCompany = (s) => !(taxOf(s).tlModesMeta && taxOf(s).tlModesMeta.isOwner) || !!getActingOrg();

export function metaOf(out) {
  return { agents: (out && out.agents) || [], levels: (out && out.levels) || [], fixTest: (out && out.fix_test) || '',
    fixTestText: (out && out.fix_test_text) || '', groupingNote: (out && out.grouping_note) || '', isOwner: !!(out && out.is_owner) };
}

// A refusal the server explains — any 422, and a 409 other than a stale base — resolves as {refusal, message, detail}
// so the place the person acted shows the server's own sentence. With `staleText`, a stale base is explained too
// (mode writes say "this failure mode changed", not "this answer"). Network failures and anything else reach write().
export function explained(fn, staleText) {
  return () => Promise.resolve().then(fn).catch((e) => {
    const stale = !!(e && e.status === 409 && (!e.reason || e.reason === 'stale'));
    if (e && (e.status === 422 || (e.status === 409 && !stale) || (stale && staleText))) {
      const d = e.body && e.body.detail && typeof e.body.detail === 'object' && !Array.isArray(e.body.detail) ? e.body.detail : {};
      return { refusal: e.reason || (stale ? 'stale' : 'invalid'), message: stale && staleText ? staleText : (e.message || 'That was refused.'), detail: d };
    }
    throw e;
  });
}
