// judgesActions — everything the Judges tab does: read the judges table and the code checks (everyone's), and for the
// platform team a failure mode's readiness and split, one judge's page and its runs (read again every 5 seconds
// while one is going), the failure report, and the writes: split, new version, preview, a run from its estimate,
// disagreement triage, the one-way freeze, the decision and the monitoring threshold. Every request goes through
// judgesApi.js. State lives in the store's `judges` slice (logic/judgesCommon.js), tagged with the store's seq: a
// company switch (resetForCompany bumps the seq) leaves the old slice unread, a reply issued before the switch is
// dropped and writes nothing, a reply overtaken by a newer read of the same thing is dropped, and the app's
// stale-company error stays quiet. Every loader other than the table's returns at once for anyone but the platform
// team, and so does every write: the service refuses them anyway, and nothing here asks it to.
//
// Every write goes through save(), so a failure stays on screen: an inline error with Retry for a network failure;
// the server's own sentence, with no Retry, for a rule it refused (over the cost cap, disagreements left, the test
// already run, a frozen judge, an unchanged prompt); and, for a stale base, "Changed in another tab" with a reload
// offered. Retry runs the whole action again (act()), so what the action does after a success (close the form, read
// the lists again) happens on a retry too. The pure helpers are in logic/judgesText.js and the view models in
// logic/judges.js; the buttons the view models carry call the actions below by name (the screen passes this module
// as `a`).
import { tlStore } from './store.js';
import { isStaleScope } from './api.js';
import { judgesApi } from './judgesApi.js';
import { modesEnsure } from './taxonomyActions.js';
import { isJudgeWrite, isOwner, judgePage, judgeWrite, judgesOf, modeCard } from './logic/judgesCommon.js';
import {
  LIVE_RUN, TLJ_POLL_MS, TLJ_ROLES, TLJ_STOP_REASON_MAX, TLJ_TRIAGE, exampleVerdict, latest, saveFile, tljStopReasonCheck
} from './logic/judgesText.js';

const get = () => tlStore.get();
const J = () => judgesOf(get());
const setJ = (patch) => tlStore.set((s) => {
  const j = judgesOf(s);
  return { judges: Object.assign({}, j, typeof patch === 'function' ? patch(j) : patch) };
});
const live = (seq) => seq === get().seq;
const owner = () => isOwner(get());
const msg = (e) => (e && e.message) || String(e);
const asks = { ready: 0, judge: 0, run: 0 };
let poll = null;

// ── reading ──────────────────────────────────────────────────────────────────────────────────────────────────────
// Opening the tab: who the person is arrives with the failure-mode list (read once, shared with the other tabs), and
// the table and the code checks are everyone's. What the platform team had open is read again by reopen().
export function openTab() {
  modesEnsure();
  return load();
}

// The platform team coming back to the tab (or just recognised as the platform team): the failure mode and the judge
// that were open are read again, on the run the reader had open; a run that was going is read at once and watched
// again (leaving the tab stopped the watch). Anyone else reads nothing more.
export function reopen() {
  if (!owner()) return;
  const j = J();
  if (j.tlJudgeModeId) openMode(j.tlJudgeModeId);
  if (j.tlJudgeId) openJudge(j.tlJudgeId, { runId: j.tlJudgeRunId });
  if (j.tlJudgeRun && LIVE_RUN.has(j.tlJudgeRun.status)) openRun(j.tlJudgeRunId, { quiet: true });
}

export async function load() {
  const seq = get().seq;
  setJ({ tlJudgesLoading: true, tlJudgesErr: '' });
  const [list, checks] = await Promise.allSettled([judgesApi.judges(), judgesApi.checks()]);
  if (!live(seq)) return;
  if ((list.status === 'rejected' && isStaleScope(list.reason)) || (checks.status === 'rejected' && isStaleScope(checks.reason))) return;
  const patch = { tlJudgesLoading: false };
  if (list.status === 'fulfilled') {
    patch.tlJudgesRows = (list.value && list.value.judges) || [];
    patch.tlJudgesModes = (list.value && list.value.modes) || [];
    patch.tlJudgesLoadedAt = Date.now();
  } else patch.tlJudgesErr = msg(list.reason);
  if (checks.status === 'fulfilled') {
    patch.tlJudgesChecks = (checks.value && checks.value.checks) || [];
    patch.tlJudgesChecksErr = '';
  } else patch.tlJudgesChecksErr = msg(checks.reason);
  setJ(patch);
}

// A failure mode's readiness card (labels against the minimum, the split, its judge versions, the next step).
export async function openMode(modeId) {
  if (!owner() || !modeId) return;
  const seq = get().seq;
  const ask = (asks.ready += 1);
  const fresh = J().tlJudgeModeId === modeId ? {} : modeCard();
  setJ(Object.assign(fresh, { tlJudgeModeId: modeId, tlJudgeReadyLoading: true, tlJudgeReadyErr: '' }));
  try {
    const out = await judgesApi.judgeReadiness(modeId);
    if (!live(seq) || ask !== asks.ready) return;
    setJ({ tlJudgeReady: out || null, tlJudgeReadyLoading: false, tlJudgeReadyLoadedAt: Date.now() });
  } catch (e) {
    if (isStaleScope(e) || !live(seq) || ask !== asks.ready) return;
    setJ({ tlJudgeReadyLoading: false, tlJudgeReadyErr: msg(e) });
  }
}

export function closeMode() { setJ(Object.assign(modeCard(), { tlJudgeModeId: null })); }

// The active split with its members: the train answers a new version's examples are picked from.
export async function loadSplit(modeId) {
  if (!owner() || !modeId) return;
  const seq = get().seq;
  setJ({ tlJudgeSplitLoading: true, tlJudgeSplitErr: '' });
  try {
    const out = await judgesApi.activeSplit(modeId);
    if (!live(seq) || J().tlJudgeModeId !== modeId) return;
    setJ({ tlJudgeSplit: (out && out.split) || null, tlJudgeSplitLoading: false });
  } catch (e) {
    if (isStaleScope(e) || !live(seq)) return;
    setJ({ tlJudgeSplitLoading: false, tlJudgeSplitErr: msg(e) });
  }
}

// One judge's page. Then the run asked for, else one still going, else the latest dev run.
export async function openJudge(judgeId, opts) {
  if (!owner() || !judgeId) return;
  const o = opts || {};
  const seq = get().seq;
  const ask = (asks.judge += 1);
  if (J().tlJudgeId !== judgeId) { stopWatch(); dropJudgeWrites(); setJ(judgePage()); }
  setJ({ tlJudgeId: judgeId, tlJudgeLoading: true, tlJudgeErr: '' });
  try {
    const out = await judgesApi.judge(judgeId);
    if (!live(seq) || ask !== asks.judge || J().tlJudgeId !== judgeId) return;
    const runs = (out && out.runs) || [];
    const pick = (o.runId && runs.find((r) => r.id === o.runId)) || runs.find((r) => LIVE_RUN.has(r.status)) || latest(runs, 'dev') || runs[0] || null;
    setJ({ tlJudge: out || null, tlJudgeLoading: false, tlJudgeLoadedAt: Date.now() });
    if (pick && (pick.id !== J().tlJudgeRunId || !J().tlJudgeRun)) openRun(pick.id);
  } catch (e) {
    if (isStaleScope(e) || !live(seq) || ask !== asks.judge) return;
    setJ({ tlJudgeLoading: false, tlJudgeErr: msg(e) });
  }
}

export function closeJudge() { stopWatch(); dropJudgeWrites(); setJ(Object.assign(judgePage(), { tlJudgeId: null })); }

// One run: its metrics, disagreements and job. While it is queued or running it is read again every TLJ_POLL_MS;
// when it finishes, the judge (runs, iteration log) and the table are read again.
export async function openRun(runId, opts) {
  if (!owner() || !runId) return;
  const o = opts || {};
  const seq = get().seq;
  const ask = (asks.run += 1);
  const before = J().tlJudgeRunId === runId ? J().tlJudgeRun : null;
  setJ(Object.assign({ tlJudgeRunId: runId, tlJudgeRunErr: '' }, o.quiet ? {} : { tlJudgeRunLoading: true },
    before ? {} : { tlJudgeRun: null, tlJudgeRunLoadedAt: null }));
  try {
    const out = await judgesApi.judgeRun(runId);
    if (!live(seq) || ask !== asks.run || J().tlJudgeRunId !== runId) return;
    const run = (out && out.run) || null;
    setJ({ tlJudgeRun: run, tlJudgeRunLoading: false, tlJudgeRunLoadedAt: Date.now() });
    if (run && LIVE_RUN.has(run.status)) watchRun();
    else if (before && LIVE_RUN.has(before.status)) {
      load();
      if (J().tlJudgeId) openJudge(J().tlJudgeId, { runId });
    }
  } catch (e) {
    if (isStaleScope(e) || !live(seq) || ask !== asks.run) return;
    setJ({ tlJudgeRunLoading: false, tlJudgeRunErr: msg(e) });
    if (o.quiet) watchRun();
  }
}

// A queued or running run is read again after TLJ_POLL_MS, only while the Judges tab is open and that run is the open one.
export function watchRun() {
  stopWatch();
  const j = J();
  const runId = j.tlJudgeRunId;
  if (!j.tlJudgeRun || !runId || !LIVE_RUN.has(j.tlJudgeRun.status)) return;
  const seq = get().seq;
  poll = setTimeout(() => {
    poll = null;
    if (!live(seq) || get().tab !== 'judges' || J().tlJudgeRunId !== runId) return;
    openRun(runId, { quiet: true });
  }, TLJ_POLL_MS);
}

export function stopWatch() { clearTimeout(poll); poll = null; }
export const watching = () => poll !== null;

export async function loadReport() {
  if (!owner()) return;
  const seq = get().seq;
  setJ({ tlJudgeReportLoading: true, tlJudgeReportErr: '' });
  try {
    const out = await judgesApi.failureReport();
    if (!live(seq)) return;
    setJ({ tlJudgeReport: out || null, tlJudgeReportLoading: false, tlJudgeReportLoadedAt: Date.now() });
  } catch (e) {
    if (isStaleScope(e) || !live(seq)) return;
    setJ({ tlJudgeReportLoading: false, tlJudgeReportErr: msg(e) });
  }
}

// After a write meets a stale base: read the open judge, run and failure mode again.
export function reloadOpen() {
  const j = J();
  if (j.tlJudgeId) openJudge(j.tlJudgeId, { runId: j.tlJudgeRunId });
  if (j.tlJudgeRunId) openRun(j.tlJudgeRunId);
  if (j.tlJudgeModeId) openMode(j.tlJudgeModeId);
}

// ── writes ───────────────────────────────────────────────────────────────────────────────────────────────────────
const saves = {};
// A reply that says another tab changed the thing: the base the write sent is behind.
const staleReason = (e) => !e.reason || e.reason === 'stale_base' || e.reason === 'stale';

// Runs one write. Resolves to {ok: true, out}, {ok: false, stale: true} (the company changed meanwhile: nothing is
// shown) or {ok: false, error, conflict, reason?}; never throws. The state of the write is kept under
// tlJudgeWrites[key] — { busy, error, conflict, canRetry } — until it succeeds. `again` is the action that made it.
export async function save(key, fn, again) {
  saves[key] = { fn, again };
  const seq = get().seq;
  const put = (w) => setJ((j) => ({ tlJudgeWrites: Object.assign({}, j.tlJudgeWrites, { [key]: w }) }));
  const drop = () => setJ((j) => { const w = Object.assign({}, j.tlJudgeWrites); delete w[key]; return { tlJudgeWrites: w }; });
  put({ busy: true, error: '', conflict: false, canRetry: false });
  try {
    const out = await Promise.resolve().then(fn);
    if (!live(seq)) { drop(); return { ok: false, stale: true }; }
    delete saves[key];
    drop();
    return { ok: true, out };
  } catch (e) {
    if (isStaleScope(e) || !live(seq)) { drop(); return { ok: false, stale: true }; }
    if (e && e.status === 409) {
      const conflict = staleReason(e);
      const error = conflict ? 'Changed in another tab — reload to see the latest.' : msg(e);
      put({ busy: false, error, conflict, canRetry: false });
      return { ok: false, error, conflict, reason: e.reason || '' };
    }
    const error = 'Could not save — ' + msg(e);
    put({ busy: false, error, conflict: false, canRetry: true });
    return { ok: false, error, conflict: false };
  }
}

export function retry(key) {
  const w = saves[key];
  if (!w) return Promise.resolve({ ok: false });
  return w.again ? Promise.resolve(w.again()) : save(key, w.fn);
}

// A refused write's error belongs to the dialog, panel or form that showed it: when that is closed or opened again it
// goes, and so does its Retry. A write still in flight is left alone.
function clearWrite(key) {
  const w = J().tlJudgeWrites[key];
  if (!w || w.busy) return;
  delete saves[key];
  setJ((j) => { const rest = Object.assign({}, j.tlJudgeWrites); delete rest[key]; return { tlJudgeWrites: rest }; });
}

// Another judge's page, or none, starts clean: whatever the last judge's page had refused (a preview, a triage, the
// decision, the threshold, the freeze, a run) goes with it, and so does the Retry saved for it, whose closure holds
// that judge's id and would spend a model call on a judge the reader has left. A write still on its way is left alone
// (it keeps its busy state); its key carries its judge's id, so its outcome can only show on that judge's page.
function dropJudgeWrites() {
  const writes = J().tlJudgeWrites || {};
  const kept = {};
  Object.keys(writes).forEach((key) => {
    if (!isJudgeWrite(key) || (writes[key] && writes[key].busy)) kept[key] = writes[key];
    else delete saves[key];
  });
  setJ({ tlJudgeWrites: kept });
}

// One write and what follows it: `after(out)` runs on success, and Retry runs all of it again.
function act(key, fn, after) {
  const go = async () => {
    const res = await save(key, fn, go);
    if (res.ok && after) after(res.out);
    return res;
  };
  return go();
}

const no = () => Promise.resolve({ ok: false });

export function setSplitForm(patch) { setJ((j) => ({ tlJudgeSplitForm: Object.assign({}, j.tlJudgeSplitForm, patch || {}) })); }

export function createSplit() {
  const s = get();
  const j = judgesOf(s);
  const modeId = j.tlJudgeModeId;
  if (!isOwner(s) || !modeId) return no();
  const f = j.tlJudgeSplitForm || {};
  const body = f.override ? { override_min_labels: true, override_reason: String(f.reason || '').trim() } : {};
  return act('judges:split:' + modeId, () => judgesApi.createSplit(modeId, body), (out) => {
    setJ({ tlJudgeSplitForm: { override: false, reason: '' }, tlJudgeSplit: (out && out.split) || null });
    openMode(modeId);
    load();
  });
}

// The new-version form. A revision starts from the open judge's parts and examples when it is this mode's; the train
// answers the examples are picked from come from the active split.
export function startDraft(kind) {
  const s = get();
  const j = judgesOf(s);
  const open = j.tlJudge && j.tlJudge.judge;
  const modeId = j.tlJudgeModeId || (open && open.mode_id);
  if (!isOwner(s) || !modeId) return;
  clearWrite('judges:register:' + modeId);
  const from = open && open.mode_id === modeId ? open : null;
  const parts = (from && from.prompt_parts) || {};
  const examples = {};
  TLJ_ROLES.forEach((r) => {
    const e = (parts.examples || []).find((x) => x.role === r.key);
    examples[r.key] = { turn_id: e ? e.turn_id : '', critique: e ? e.critique || '' : '' };
  });
  setJ({ tlJudgeModeId: modeId, tlJudgeWarnings: [], tlJudgeDraft: { kind: kind === 'code' ? 'code' : 'llm', task: parts.task || '',
    fail_rule: parts.fail_rule || '', pass_rule: parts.pass_rule || '', edge_cases: parts.edge_cases || '', change_note: '', check_key: '', examples } });
  if (kind !== 'code') loadSplit(modeId);
}

export function revise() {
  const j = J().tlJudge && J().tlJudge.judge;
  if (!owner() || !j) return;
  if (J().tlJudgeModeId !== j.mode_id) openMode(j.mode_id);
  startDraft(j.kind === 'code' ? 'code' : 'llm');
}

export function setDraft(patch) { setJ((j) => (j.tlJudgeDraft ? { tlJudgeDraft: Object.assign({}, j.tlJudgeDraft, patch || {}) } : {})); }

export function setExample(role, patch) {
  setJ((j) => {
    if (!j.tlJudgeDraft) return {};
    const examples = Object.assign({}, j.tlJudgeDraft.examples);
    examples[role] = Object.assign({}, examples[role], patch || {});
    return { tlJudgeDraft: Object.assign({}, j.tlJudgeDraft, { examples }) };
  });
}

export function cancelDraft() {
  if (J().tlJudgeModeId) clearWrite('judges:register:' + J().tlJudgeModeId);
  setJ({ tlJudgeDraft: null });
}

export function register() {
  const s = get();
  const j = judgesOf(s);
  const d = j.tlJudgeDraft;
  const modeId = j.tlJudgeModeId;
  if (!isOwner(s) || !d || !modeId) return no();
  const body = { mode_id: modeId, kind: d.kind, change_note: String(d.change_note || '').trim() };
  if (d.kind === 'code') body.check_key = d.check_key || null;
  else {
    const members = (j.tlJudgeSplit && j.tlJudgeSplit.members) || [];
    body.parts = { task: String(d.task || '').trim() || null, fail_rule: String(d.fail_rule || '').trim(),
      pass_rule: String(d.pass_rule || '').trim(), edge_cases: String(d.edge_cases || '').trim() || null };
    body.examples = TLJ_ROLES.map((r) => {
      const e = d.examples[r.key] || {};
      return { turn_id: e.turn_id || '', role: r.key, critique: String(e.critique || '').trim(),
        verdict: exampleVerdict(r.key, members.find((m) => m.turn_id === e.turn_id)) };
    });
  }
  return act('judges:register:' + modeId, () => judgesApi.createJudge(body), (out) => {
    const judge = out && out.judge;
    setJ({ tlJudgeDraft: null, tlJudgeWarnings: (out && out.warnings) || [] });
    load();
    openMode(modeId);
    if (judge && judge.id) openJudge(judge.id);
  });
}

// Preview: one judgement of one dev answer (never a held-out one; the service refuses those). A cached decision costs
// nothing.
export function preview(turnId) {
  const s = get();
  const judgeId = judgesOf(s).tlJudgeId;
  if (!isOwner(s) || !judgeId || !turnId) return no();
  return act(judgeWrite.preview(judgeId, turnId), () => judgesApi.previewJudge(judgeId, turnId), (out) => {
    if (J().tlJudgeId === judgeId) setJ((p) => ({ tlJudgePreview: Object.assign({}, p.tlJudgePreview, { [turnId]: out || {} }) }));
  });
}

// A run starts from its estimate: the panel shows items, cached, to judge and the cost, and Start sends the split with an
// optional cost cap. The held-out test runs once; the service refuses a second.
export async function estimate(split) {
  const s = get();
  const judgeId = judgesOf(s).tlJudgeId;
  if (!isOwner(s) || !judgeId) return;
  const sp = split === 'test' ? 'test' : 'dev';
  const seq = s.seq;
  clearWrite(judgeWrite.run(judgeId));
  setJ({ tlJudgeEstimate: { split: sp, loading: true, error: '', data: null, cap: '' } });
  const keep = (patch) => setJ((j) => (j.tlJudgeEstimate && j.tlJudgeEstimate.split === sp
    ? { tlJudgeEstimate: Object.assign({}, j.tlJudgeEstimate, patch) } : {}));
  try {
    const out = await judgesApi.estimateJudgeRun(judgeId, sp);
    if (!live(seq) || J().tlJudgeId !== judgeId) return;
    keep({ loading: false, data: out || null });
  } catch (e) {
    if (isStaleScope(e) || !live(seq)) return;
    keep({ loading: false, error: msg(e) });
  }
}

export function setCap(text) { setJ((j) => (j.tlJudgeEstimate ? { tlJudgeEstimate: Object.assign({}, j.tlJudgeEstimate, { cap: String(text || '').slice(0, 12) }) } : {})); }
export function closeEstimate() {
  if (J().tlJudgeId) clearWrite(judgeWrite.run(J().tlJudgeId));
  setJ({ tlJudgeEstimate: null });
}

export function startRun() {
  const s = get();
  const j = judgesOf(s);
  const judgeId = j.tlJudgeId;
  const est = j.tlJudgeEstimate;
  if (!isOwner(s) || !judgeId || !est || !est.data) return no();
  const body = { split: est.split };
  const cap = Number(String(est.cap || '').trim());
  if (String(est.cap || '').trim() && isFinite(cap) && cap > 0) body.usd_cap = cap;
  return act(judgeWrite.run(judgeId), () => judgesApi.startJudgeRun(judgeId, body), (out) => {
    const run = out && out.run;
    setJ(Object.assign({ tlJudgeEstimate: null },
      run && run.id ? { tlJudgeRunId: run.id, tlJudgeRun: run, tlJudgeRunErr: '', tlJudgeRunLoadedAt: Date.now() } : {}));
    if (run && run.id) watchRun();
    openJudge(judgeId, { runId: run && run.id });
  });
}

export function setTriageNote(turnId, text) {
  setJ((j) => ({ tlJudgeTriageNotes: Object.assign({}, j.tlJudgeTriageNotes, { [turnId]: String(text || '').slice(0, 2000) }) }));
}

// Triage of one dev disagreement: judge wrong / label wrong / definition unclear, with the run's base.
export function triage(turnId, choice) {
  const s = get();
  const j = judgesOf(s);
  const run = j.tlJudgeRun;
  if (!isOwner(s) || !run || !turnId || !TLJ_TRIAGE.some((t) => t.key === choice)) return no();
  const note = String((j.tlJudgeTriageNotes || {})[turnId] || '').trim() || null;
  return act(judgeWrite.triage(run.id, turnId), () => judgesApi.triageDisagreement(run.id, turnId, { triage: choice, note, base: run.rev }), (out) => {
    if (!(out && out.run) || J().tlJudgeRunId !== run.id) return;
    setJ((p) => {
      const notes = Object.assign({}, p.tlJudgeTriageNotes);
      delete notes[turnId];
      return { tlJudgeRun: out.run, tlJudgeTriageNotes: notes };
    });
    if (J().tlJudgeId) openJudge(J().tlJudgeId, { runId: run.id });
  });
}

// Freeze is one-way (the service refuses a return to draft). The dialog keeps its stop reason until the service says yes.
export function freezeOpen() {
  if (!owner() || !J().tlJudge) return;
  if (J().tlJudgeId) clearWrite(judgeWrite.freeze(J().tlJudgeId));
  setJ({ tlJudgeFreeze: { reason: '' } });
}
export function freezeClose() {
  if (J().tlJudgeId) clearWrite(judgeWrite.freeze(J().tlJudgeId));
  setJ({ tlJudgeFreeze: null });
}
export function setFreezeReason(text) {
  setJ((j) => (j.tlJudgeFreeze ? { tlJudgeFreeze: { reason: String(text || '').slice(0, TLJ_STOP_REASON_MAX + 100) } } : {}));
}

export function freeze() {
  const s = get();
  const x = judgesOf(s);
  const j = x.tlJudge && x.tlJudge.judge;
  const f = x.tlJudgeFreeze;
  if (!isOwner(s) || !j || !f || !tljStopReasonCheck(f.reason).ok) return no();
  return act(judgeWrite.freeze(j.id), () => judgesApi.freezeJudge(j.id, { stop_reason: String(f.reason).trim(), base: j.rev }), () => {
    setJ({ tlJudgeFreeze: null });
    openJudge(j.id);
    load();
    if (J().tlJudgeModeId) openMode(J().tlJudgeModeId);
  });
}

export function setDecisionDraft(patch) { setJ((j) => ({ tlJudgeDecisionDraft: Object.assign({}, j.tlJudgeDecisionDraft, patch || {}) })); }

export function decide() {
  const s = get();
  const x = judgesOf(s);
  const j = x.tlJudge && x.tlJudge.judge;
  const d = x.tlJudgeDecisionDraft || {};
  if (!isOwner(s) || !j || (d.decision !== 'use' && d.decision !== 'do_not_use')) return no();
  return act(judgeWrite.decision(j.id), () => judgesApi.setJudgeDecision(j.id, { decision: d.decision, note: String(d.note || '').trim() || null, base: j.rev }), () => {
    setJ({ tlJudgeDecisionDraft: { decision: '', note: '' } });
    openJudge(j.id);
    load();
  });
}

export function setThresholdDraft(text) { setJ({ tlJudgeThresholdDraft: String(text || '').slice(0, 12) }); }

export function saveThreshold() {
  const s = get();
  const x = judgesOf(s);
  const j = x.tlJudge && x.tlJudge.judge;
  const raw = String(x.tlJudgeThresholdDraft || '').trim();
  const t = Number(raw);
  if (!isOwner(s) || !j || !raw || !(isFinite(t) && t > 0 && t < 1)) return no();
  return act(judgeWrite.threshold(j.id), () => judgesApi.setJudgeThreshold(j.id, { threshold: t, base: j.rev }), () => {
    setJ({ tlJudgeThresholdDraft: '' });
    openJudge(j.id);
    load();
  });
}

export function downloadReport() {
  if (!owner()) return no();
  return act('judges:report-file', () => judgesApi.failureReport('md'), (out) => { saveFile(out, 'failure-report.md'); });
}
