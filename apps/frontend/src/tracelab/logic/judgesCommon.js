// judgesCommon — what the Judges tab's parts share: the `judges` slice of the store (the judges table and code checks,
// a failure mode's readiness card, the new-version form, one judge's page and run, the failure report, and the
// writes in flight) and who the person is. Pure: no requests and no store access; judgesActions.js acts, judges.js
// shapes what is drawn.
//
// The state is the `judges` slice, made under the store's `seq` exactly as the taxonomy slice is (logic/taxCommon.js):
// the store merges patches and resetForCompany() resets only the keys store.js lists, so the slice carries the seq it
// was made under, and judgesOf(s) hands back a slice from an earlier seq, or none, as an untouched one. A company
// switch therefore empties it without store.js having to know it exists. Keys keep the `tlJudge…` names the plan gave
// them.
//
// Who the person is: the platform team (a superadmin who is viewing no company) builds, runs and freezes judges; a
// company admin, or a superadmin viewing a company, reads the table and the code checks. The module never learns the
// account, so, as for the other platform-wide tabs, the service says who the person is on the failure-mode list
// (`is_owner`) and a View as company wins (logic/taxCommon.js owns). Until that list is read nobody is the platform
// team and nothing owner-only is drawn or called.
import { owns } from './taxCommon.js';

// The keys of the writes that belong to one judge's page. Each carries the id of the judge it was made on (a triage, of
// its run), because judges of one failure mode share the same dev split, so the same answer is on every version's page:
// a refusal, a busy state or a Retry made on one judge can then never show on another judge's page, even for a write
// still on its way when the reader opened the other. The failure mode's own writes ('judges:split:<mode>',
// 'judges:register:<mode>') and the report file are not a judge page's.
export const judgeWrite = Object.freeze({
  preview: (judgeId, turnId) => 'judges:preview:' + judgeId + ':' + turnId,
  triage: (runId, turnId) => 'judges:triage:' + runId + ':' + turnId,
  decision: (judgeId) => 'judges:decision:' + judgeId,
  threshold: (judgeId) => 'judges:threshold:' + judgeId,
  freeze: (judgeId) => 'judges:freeze:' + judgeId,
  run: (judgeId) => 'judges:run:' + judgeId
});
export const isJudgeWrite = (key) => /^judges:(preview|triage|decision|threshold|freeze|run):/.test(String(key));

// One judge's page at its default (closing it, or opening another judge, resets these keys).
export function judgePage() {
  return {
    tlJudge: null, tlJudgeLoading: false, tlJudgeErr: '', tlJudgeLoadedAt: null, tlJudgePreview: {}, tlJudgeEstimate: null,
    tlJudgeRunId: null, tlJudgeRun: null, tlJudgeRunLoading: false, tlJudgeRunErr: '', tlJudgeRunLoadedAt: null, tlJudgeTriageNotes: {},
    tlJudgeFreeze: null, tlJudgeDecisionDraft: { decision: '', note: '' }, tlJudgeThresholdDraft: ''
  };
}

// One failure mode's card at its default (closing it, or opening another failure mode, resets these keys).
export function modeCard() {
  return {
    tlJudgeReady: null, tlJudgeReadyLoading: false, tlJudgeReadyErr: '', tlJudgeReadyLoadedAt: null,
    tlJudgeSplitForm: { override: false, reason: '' }, tlJudgeSplit: null, tlJudgeSplitLoading: false, tlJudgeSplitErr: '',
    tlJudgeDraft: null, tlJudgeWarnings: []
  };
}

// The slice at its default, made under the store's `seq`. A fresh object (and fresh objects inside) every call.
export function judgesInitial(seq) {
  return Object.assign({
    seq: seq || 0,
    tlJudgesRows: null, tlJudgesModes: null, tlJudgesChecks: null, tlJudgesChecksErr: '',
    tlJudgesLoading: false, tlJudgesErr: '', tlJudgesLoadedAt: null,
    tlJudgeModeId: null, tlJudgeId: null,
    tlJudgeReport: null, tlJudgeReportLoading: false, tlJudgeReportErr: '', tlJudgeReportLoadedAt: null,
    // writes in flight or failed, by key: { busy, error, conflict, canRetry }
    tlJudgeWrites: {}
  }, modeCard(), judgePage());
}

// The slice in force for this state: the one made under the store's current seq, else an untouched one.
export function judgesOf(s) {
  const seq = (s && s.seq) || 0;
  return s && s.judges && s.judges.seq === seq ? s.judges : judgesInitial(seq);
}

// The platform team: the service said so on the failure-mode list, and no company is being viewed.
export const isOwner = (s) => !!s && owns(s);
