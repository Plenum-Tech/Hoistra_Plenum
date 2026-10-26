// watchCommon — what the Watch tab's parts share: the `watch` slice of the store (monitoring batches and their charts, the
// plan, the open batch, eval cases, release checks, the automatic-vs-people trend, and the writes in flight) and who the
// person is. Pure: no requests and no store access; watchActions.js acts, watch.js shapes what is drawn.
//
// The state is the `watch` slice, made under the store's `seq` exactly as the taxonomy and judges slices are
// (logic/taxCommon.js, judgesCommon.js): the store merges patches and resetForCompany() resets only the keys store.js lists,
// so the slice carries the seq it was made under, and watchOf(s) hands back a slice from an earlier seq, or none, as an
// untouched one. A company switch or a sign-out therefore empties every Watch key without store.js having to know the slice
// exists (the reset the source planned as a patch of its own). Keys keep the names the plan gave them, in its four groups:
// monitoring, cases, release and auto.
//
// Who the person is: the platform team (a superadmin who is viewing no company) reads every company's batches; a company
// admin, or a superadmin viewing a company, reads that company's batches and owns its eval cases and release checks. Any
// superadmin starts monitoring: viewing no company the batch covers every company, viewing one it is that company's own
// batch, which that company's admins then read (Phase 8); a company admin never starts one. The module never learns the
// account, so, as for the other platform-wide tabs, the service says who the person is on the failure-mode list
// (`is_owner`, which it answers false for a superadmin it was told to act for a company) and a View as company wins
// (logic/taxCommon.js owns and acting). Until that list is read nobody is the platform team or a company reader, and
// nothing owner-only or company-only is drawn or asked for.
import { acting, hasCompany, owns, taxOf } from './taxCommon.js';

// The keys of the writes, one per button, each carrying what it was made on (a batch, a case) so a refusal, a busy state
// or a Retry made on one row can never show on another.
export const watchWrite = Object.freeze({
  start: 'watch:start',
  release: 'watch:release',
  resume: (batchId) => 'watch:resume:' + batchId,
  review: (batchId, modeId) => 'watch:review:' + batchId + ':' + modeId,
  confirm: (caseId) => 'watch:confirm:' + caseId,
  baseline: (caseId) => 'watch:baseline:' + caseId,
  repeat: (caseId) => 'watch:repeat:' + caseId,
  retire: (caseId) => 'watch:retire:' + caseId
});

// The slice at its default, made under the store's `seq`. A fresh object (and fresh objects inside) every call.
export function watchInitial(seq) {
  return {
    seq: seq || 0,
    // monitoring: the batches with their Due marker, the code-check trend, one chart per failure mode, the plan to run now
    tlMonitorBatches: null, tlMonitorLoading: false, tlMonitorErr: '', tlMonitorLoadedAt: 0, tlMonitorChecks: null, tlMonitorChecksErr: '',
    tlMonitorCharts: {}, tlMonitorPlanOpen: false, tlMonitorPlanLoading: false, tlMonitorPlanErr: '', tlMonitorPlan: null,
    tlMonitorPlanDraft: {},
    // the open batch and the review batches opened from it (by 'batchId:modeId')
    tlMonitorBatchId: null, tlMonitorBatch: null, tlMonitorBatchLoading: false, tlMonitorBatchErr: '', tlMonitorReviewIds: {},
    // eval cases (a company's), with what is typed into each row
    tlCases: null, tlCasesLoading: false, tlCasesErr: '', tlCasesLoadedAt: 0, tlCasesDrafts: {},
    // release checks (a company's) and the form that starts one
    tlRelease: null, tlReleaseLoading: false, tlReleaseErr: '', tlReleaseLoadedAt: 0, tlReleaseDraft: { k: 5, include_capability: true },
    // the automatic-vs-people trend
    tlAuto: null, tlAutoLoading: false, tlAutoErr: '', tlAutoLoadedAt: 0,
    // writes in flight or failed, by key: { busy, error, conflict, canRetry }
    tlWatchWrites: {}
  };
}

// The slice in force for this state: the one made under the store's current seq, else an untouched one.
export function watchOf(s) {
  const seq = (s && s.seq) || 0;
  return s && s.watch && s.watch.seq === seq ? s.watch : watchInitial(seq);
}

// Whether the service has said who the person is (the failure-mode list has been read for this company).
export const known = (s) => !!s && !!taxOf(s).tlModesMeta;
// The platform team: the service said so on the failure-mode list, and no company is being viewed.
export const isPlatform = (s) => !!s && owns(s);
// A superadmin viewing one company (the app's View as), once the service has said who the person is. The service names no
// owner for a company it acts for, so this is the one test that tells such a person from a company admin.
export const viewsCompany = (s) => known(s) && acting(s);
// Who may preview, start and resume monitoring and open its review batches: any superadmin, viewing every company or one.
// Never a company admin, and nobody until the service has said who the person is.
export const canStart = (s) => known(s) && (isPlatform(s) || viewsCompany(s));
// A reader inside one company (a company admin, or a superadmin viewing one), once the service has said who the person is.
export const inCompany = (s) => known(s) && hasCompany(s);
// Why the service has not said who the person is: the failure-mode list's own error, while the person is still not known.
// Empty while the list is being read, once it is read, and when it has not been asked for yet.
export const identityError = (s) => (!s || known(s) ? '' : taxOf(s).tlModesErr || '');
