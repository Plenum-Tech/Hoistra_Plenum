// goldenActions — everything the Golden set tab, the golden runs and the expected-outcome panel do: read and
// upload golden sets, start, step, pause, resume, cancel and re-grade runs, compare two runs, and reveal the
// expected outcome of a golden answer. Every request goes through goldenApi.js. State lives in the module store
// under `golden` (store.js goldenInitial); a reply issued before a company switch (the store's seq, bumped by
// resetForCompany) is dropped, and the app's stale-scope error stays quiet.
//
// The step loop (design D9): the page that started (or resumed) a run keeps it going. Every GOLDEN_STEP_MS it
// asks the service to answer the next case when the run is idle, and reads the run while a case is being answered.
// Closing the tab (pagehide) or leaving Trace Lab sends one keepalive pause, so the run waits for Resume.
import { tlStore } from './store.js';
import { isStaleScope } from './api.js';
import { goldenApi, pauseOnClose } from './goldenApi.js';
import { getActingOrg } from '../api/client.js';
import { CASES_PAGE, GOLDEN_STEP_MS, UUID_RE, stepDecision } from './logic/golden.js';

const get = () => tlStore.get();
const G = () => tlStore.get().golden;
const setG = (patch) => tlStore.set((s) => ({ golden: Object.assign({}, s.golden, typeof patch === 'function' ? patch(s.golden) : patch) }));
const part = (key, p) => setG((g) => ({ [key]: Object.assign({}, g[key], p) }));
const live = (seq) => seq === get().seq;
const msg = (e) => (e && e.message) || String(e);
const reasonOf = (e) => (e && typeof e.reason === 'string' ? e.reason : '');
const detailOf = (e) => (e && e.body && e.body.detail && typeof e.body.detail === 'object' && !Array.isArray(e.body.detail) ? e.body.detail : {});
const asks = { sets: 0, cases: 0, runs: 0, run: 0, estimate: 0, compare: 0 };

// ── the Golden set tab ────────────────────────────────────────────────────────────────────────────────────────
export async function loadSets() {
  const seq = get().seq;
  const ask = (asks.sets += 1);
  part('sets', { loading: true, error: '', reason: '' });
  try {
    const out = await goldenApi.sets();
    if (!live(seq) || ask !== asks.sets) return;
    const versions = (out && out.versions) || [];
    const keep = versions.some((v) => v.id === G().setId) ? G().setId : null;
    const pick = keep || (out && out.active && out.active.id) || (versions[0] && versions[0].id) || null;
    setG((g) => ({ sets: { data: out || null, loading: false, error: '', reason: '', loadedAt: Date.now() }, setId: pick,
      cases: pick ? g.cases : Object.assign({}, g.cases, { data: null }) }));
    if (pick) await loadCases();
  } catch (e) {
    if (isStaleScope(e) || !live(seq) || ask !== asks.sets) return;
    part('sets', { loading: false, error: msg(e), reason: reasonOf(e) });
  }
}

export function pickSet(setId) {
  if (!setId || setId === G().setId) return;
  setG((g) => ({ setId, shown: {}, cases: Object.assign({}, g.cases, { page: 0, data: null }) }));
  loadCases();
}

export async function loadCases() {
  const g = G();
  if (!g.setId) return;
  const seq = get().seq;
  const ask = (asks.cases += 1);
  const q = { limit: CASES_PAGE, offset: (g.cases.page || 0) * CASES_PAGE };
  if (g.cases.q) q.q = g.cases.q;
  if (g.cases.flag) q.flag = g.cases.flag;
  part('cases', { loading: true, error: '' });
  try {
    const out = await goldenApi.cases(g.setId, q);
    if (!live(seq) || ask !== asks.cases) return;
    part('cases', { data: out || null, loading: false, error: '' });
  } catch (e) {
    if (isStaleScope(e) || !live(seq) || ask !== asks.cases) return;
    part('cases', { loading: false, error: msg(e) });
  }
}

export function setCasesDraft(text) { setG({ casesDraft: String(text || '').slice(0, 200) }); }

export function searchCases() {
  part('cases', { q: String(G().casesDraft || '').trim().slice(0, 200), page: 0 });
  return loadCases();
}

export function setCasesFlag(flag) {
  part('cases', { flag: flag || '', page: 0 });
  return loadCases();
}

export function pageCases(delta) {
  const c = G().cases;
  const page = (c.page || 0) + delta;
  const total = (c.data && c.data.total) || 0;
  if (page < 0 || page * CASES_PAGE >= total) return;
  part('cases', { page });
  loadCases();
}

// One row's expected answer, shown or hidden again. Nothing is shown until the reader asks, row by row.
export function toggleExpected(caseId) {
  if (!caseId) return;
  setG((g) => ({ shown: Object.assign({}, g.shown, { [caseId]: !g.shown[caseId] }) }));
}

// Check a file (dryRun: nothing saved) or save it as a new version. Row errors arrive as a 422 with detail.errors.
export async function upload(file, opts) {
  const o = opts || {};
  if (!file) return;
  const seq = get().seq;
  const base = { dryRun: !!o.dryRun, fileName: file.name || '', error: '', errors: [], warnings: [], report: null };
  setG({ upload: Object.assign({}, base, { busy: true }) });
  try {
    const out = await goldenApi.upload(file, { sheet: o.sheet || '', name: o.name || '', note: o.note || '', dryRun: !!o.dryRun });
    if (!live(seq)) return;
    setG({ upload: Object.assign({}, base, { busy: false, report: out || null }) });
    if (!o.dryRun) {
      setG((g) => ({ setId: (out && out.set && out.set.id) || g.setId, shown: {}, cases: Object.assign({}, g.cases, { page: 0 }) }));
      await loadSets();
    }
  } catch (e) {
    if (isStaleScope(e) || !live(seq)) return;
    const d = detailOf(e);
    setG({ upload: Object.assign({}, base, { busy: false, error: msg(e),
      errors: Array.isArray(d.errors) ? d.errors : [], warnings: Array.isArray(d.warnings) ? d.warnings : [] }) });
  }
}

async function setAction(kind, setId) {
  if (!setId || G().setBusy) return;
  const seq = get().seq;
  setG({ setBusy: kind + ':' + setId, setError: '' });
  try {
    await (kind === 'activate' ? goldenApi.activate(setId) : goldenApi.retire(setId));
    if (!live(seq)) return;
    setG({ setBusy: '' });
    await loadSets();
  } catch (e) {
    if (isStaleScope(e) || !live(seq)) return;
    setG({ setBusy: '', setError: msg(e) });
  }
}
export function activateSet(setId) { return setAction('activate', setId); }
export function retireSet(setId) { return setAction('retire', setId); }

// A template as a file download: 'plain' (the header row) or 'compliance' (the compliance question templates).
export async function download(kind) {
  try {
    const out = await goldenApi.template(kind);
    if (!out || !out.blob || typeof document === 'undefined' || typeof URL === 'undefined' || !URL.createObjectURL) return;
    const url = URL.createObjectURL(out.blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = out.filename || (kind === 'compliance' ? 'golden-set-compliance-questions.csv' : 'golden-set-template.csv');
    document.body.appendChild(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  } catch (e) {
    if (isStaleScope(e)) return;
    setG({ setError: msg(e) });
  }
}

// ── golden runs ───────────────────────────────────────────────────────────────────────────────────────────────
export async function loadGoldenRuns() {
  const seq = get().seq;
  const ask = (asks.runs += 1);
  part('runs', { loading: true, error: '', reason: '' });
  try {
    const out = await goldenApi.runs({ limit: 50 });
    if (!live(seq) || ask !== asks.runs) return;
    setG({ runs: { rows: (out && out.runs) || [], loading: false, error: '', reason: '', loadedAt: Date.now() } });
  } catch (e) {
    if (isStaleScope(e) || !live(seq) || ask !== asks.runs) return;
    part('runs', { loading: false, error: msg(e), reason: reasonOf(e) });
  }
}

// Read the open run again. Returns the run, or null.
export async function pollRun() {
  const id = G().detail.runId;
  if (!id) return null;
  const seq = get().seq;
  const ask = (asks.run += 1);
  part('detail', { loading: true });
  try {
    const out = await goldenApi.run(id);
    if (!live(seq) || ask !== asks.run || G().detail.runId !== id) return null;
    setG({ detail: { runId: id, data: out || null, loading: false, error: '', loadedAt: Date.now() } });
    return (out && out.run) || null;
  } catch (e) {
    if (isStaleScope(e) || !live(seq) || ask !== asks.run || G().detail.runId !== id) return null;
    part('detail', { loading: false, error: msg(e) });
    return null;
  }
}

export async function openRun(runId) {
  if (!runId || !UUID_RE.test(String(runId))) return null;
  const same = G().detail.runId === runId;
  setG((g) => ({ compare: null, action: { busy: '', error: '' },
    detail: same ? g.detail : { runId, data: null, loading: true, error: '', loadedAt: null } }));
  const run = await pollRun();
  maybeLoop(run);
  return run;
}

export function closeRun() {
  setG({ detail: { runId: null, data: null, loading: false, error: '', loadedAt: null }, action: { busy: '', error: '' } });
}

// The page keeps a running run going only when it is this page's to step and no refusal stopped it.
export function maybeLoop(run) {
  const g = G();
  if (stepDecision(run, g.own) === 'stop') return;
  if (g.loop.error && g.loop.runId === null && g.loop.lastRunId === run.id) return;
  startLoop(run.id);
}

export function openStart() {
  const sets = G().sets.data;
  const active = sets && sets.active;
  setG({ dialog: { k: 1, purpose: 'adhoc', setId: active ? active.id : null, note: '', usdCap: '', estimate: null,
    estimating: false, starting: false, error: '', reason: '', problems: [] } });
  if (!sets) loadSets();
  return estimate();
}

export function closeStart() { setG({ dialog: null }); }

// A change to the dialog; a new k or a new version is estimated again (and its cap re-proposed).
export function setDialog(patch) {
  const d = G().dialog;
  if (!d) return null;
  const p = Object.assign({}, patch || {});
  if ('usdCap' in p) p.usdCap = String(p.usdCap || '').replace(/[^0-9.]/g, '').slice(0, 8);
  if ('note' in p) p.note = String(p.note || '').slice(0, 500);
  const again = 'k' in p || 'setId' in p;
  setG({ dialog: Object.assign({}, d, p, again ? { estimate: null, usdCap: '' } : {}) });
  return again ? estimate() : null;
}

export async function estimate() {
  const d = G().dialog;
  if (!d) return null;
  const seq = get().seq;
  const ask = (asks.estimate += 1);
  setG({ dialog: Object.assign({}, d, { estimating: true, error: '' }) });
  try {
    const out = await goldenApi.estimate({ set_id: d.setId || null, k: Number(d.k) || 1 });
    if (!live(seq) || ask !== asks.estimate || !G().dialog) return null;
    setG((g) => ({ dialog: Object.assign({}, g.dialog, { estimating: false, estimate: out || null,
      usdCap: g.dialog.usdCap || (out && out.usd_cap_default ? String(out.usd_cap_default) : '') }) }));
    return out;
  } catch (e) {
    if (isStaleScope(e) || !live(seq) || ask !== asks.estimate || !G().dialog) return null;
    setG((g) => ({ dialog: Object.assign({}, g.dialog, { estimating: false, error: msg(e), reason: reasonOf(e) }) }));
    return null;
  }
}

// Start: only after the estimate is on screen. The started run is this page's to step.
export async function startRun() {
  const d = G().dialog;
  if (!d || !d.estimate || d.starting || d.estimating) return null;
  const seq = get().seq;
  setG({ dialog: Object.assign({}, d, { starting: true, error: '', reason: '', problems: [] }) });
  const body = { set_id: d.setId || null, k: Number(d.k) || 1, purpose: d.purpose || 'adhoc', note: d.note ? String(d.note) : null };
  const cap = Number(d.usdCap);
  if (isFinite(cap) && cap > 0) body.usd_cap = cap;
  try {
    const out = await goldenApi.start(body);
    if (!live(seq)) return null;
    const run = out && out.run;
    setG((g) => ({ dialog: null, own: run && run.id ? Object.assign({}, g.own, { [run.id]: true }) : g.own }));
    loadGoldenRuns();
    if (run && run.id) await openRun(run.id);
    return run || null;
  } catch (e) {
    if (isStaleScope(e) || !live(seq)) return null;
    const det = detailOf(e);
    const problems = Array.isArray(det.problems) ? det.problems : (Array.isArray(det.cases) ? det.cases.map((c) => (c && c.message) || String(c)) : []);
    setG((g) => ({ dialog: g.dialog ? Object.assign({}, g.dialog, { starting: false, error: msg(e), reason: reasonOf(e), problems }) : null }));
    return null;
  }
}

async function runAction(kind, body) {
  const id = G().detail.runId;
  if (!id || G().action.busy) return null;
  const seq = get().seq;
  part('action', { busy: kind, error: '' });
  const calls = { pause: () => goldenApi.pause(id), resume: () => goldenApi.resume(id, body), cancel: () => goldenApi.cancel(id),
    regrade: () => goldenApi.regrade(id, body) };
  try {
    if (kind === 'pause' || kind === 'cancel') stopLoop();
    const out = await calls[kind]();
    if (!live(seq)) return null;
    setG((g) => Object.assign({ action: { busy: '', error: '' } },
      kind === 'resume' ? { own: Object.assign({}, g.own, { [id]: true }), resumeCap: '', loop: Object.assign({}, g.loop, { error: null, lastRunId: null }) } : {}));
    const run = await pollRun();
    if (kind === 'resume') maybeLoop(run);
    loadGoldenRuns();
    return out;
  } catch (e) {
    if (isStaleScope(e) || !live(seq)) return null;
    part('action', { busy: '', error: msg(e) });
    return null;
  }
}
export function pauseRun() { return runAction('pause'); }
export function resumeRun() {
  const cap = Number(G().resumeCap);
  return runAction('resume', isFinite(cap) && cap > 0 ? { usd_cap: cap } : {});
}
export function setResumeCap(text) { setG({ resumeCap: String(text || '').replace(/[^0-9.]/g, '').slice(0, 8) }); }
export function cancelRun() { return runAction('cancel'); }
export function regradeRun(only) { return runAction('regrade', { only: only === 'all' ? 'all' : 'pending' }); }

// ── two runs compared ─────────────────────────────────────────────────────────────────────────────────────────
export function togglePick(runId) {
  if (!runId) return;
  setG((g) => {
    const pick = g.pick.slice();
    const i = pick.indexOf(runId);
    if (i > -1) pick.splice(i, 1); else pick.push(runId);
    return { pick: pick.slice(-2) };
  });
}

// The older of the two picked runs is the base.
export async function compareRuns() {
  const g = G();
  if (g.pick.length !== 2) return null;
  const rows = g.runs.rows || [];
  const at = (id) => { const r = rows.find((x) => x.id === id); return r ? Date.parse(r.started_at) || 0 : 0; };
  const [base, head] = at(g.pick[0]) <= at(g.pick[1]) ? [g.pick[0], g.pick[1]] : [g.pick[1], g.pick[0]];
  const seq = get().seq;
  const ask = (asks.compare += 1);
  setG({ compare: { base, head, data: null, loading: true, error: '' }, detail: { runId: null, data: null, loading: false, error: '', loadedAt: null } });
  try {
    const out = await goldenApi.compare(base, head);
    if (!live(seq) || ask !== asks.compare || !G().compare) return null;
    setG({ compare: { base, head, data: out || null, loading: false, error: '' } });
    return out;
  } catch (e) {
    if (isStaleScope(e) || !live(seq) || ask !== asks.compare || !G().compare) return null;
    setG({ compare: { base, head, data: null, loading: false, error: msg(e) } });
    return null;
  }
}

export function closeCompare() { setG({ compare: null, pick: [] }); }

// ── the step loop ─────────────────────────────────────────────────────────────────────────────────────────────
let timer = null;
let loopOrg = null;
let onHide = null;
const TRANSIENT = new Set(['token_expiring', 'in_flight', 'busy']);

function listen(runId) {
  if (typeof window === 'undefined' || typeof window.addEventListener !== 'function') return;
  unlisten();
  onHide = () => { pauseOnClose(runId, loopOrg); };
  window.addEventListener('pagehide', onHide);
}
function unlisten() {
  if (onHide && typeof window !== 'undefined' && typeof window.removeEventListener === 'function') window.removeEventListener('pagehide', onHide);
  onHide = null;
}

export function loopActive() { return !!timer; }

export function startLoop(runId) {
  if (!runId) return;
  if (timer && G().loop.runId === runId) return;
  stopLoop();
  loopOrg = getActingOrg();
  setG({ loop: { runId, busy: false, error: null, note: '', lastRunId: null, run: null } });
  listen(runId);
  timer = setInterval(() => { tick(); }, GOLDEN_STEP_MS);
  tick();
}

export function stopLoop() {
  if (timer) clearInterval(timer);
  timer = null;
  unlisten();
  const l = G().loop;
  if (l.runId || l.busy) setG({ loop: Object.assign({}, l, { runId: null, busy: false, note: '', lastRunId: l.runId || l.lastRunId || null }) });
}

// Leaving Trace Lab (the page unmounts) or switching company while this page steps a run: stop and pause it.
export function leavePage() {
  const id = G().loop.runId;
  const org = loopOrg;
  stopLoop();
  if (id) pauseOnClose(id, org);
}

const stopWith = (reason, message, resumable) => {
  stopLoop();
  setG((g) => ({ loop: Object.assign({}, g.loop, { error: { reason, message, resumable: !!resumable } }) }));
};

export async function tick() {
  const g = G();
  const id = g.loop.runId;
  if (!id) { stopLoop(); return; }
  if (g.loop.busy) return;
  const run = loopRun(g, id);
  const decision = run ? stepDecision(run, g.own) : 'poll';
  if (decision === 'stop') { stopLoop(); return; }
  const seq = get().seq;
  setG((x) => ({ loop: Object.assign({}, x.loop, { busy: true }) }));
  try {
    if (decision === 'step') await stepOnce(id, seq);
    else await refreshLoopRun(id, seq);
  } finally {
    if (live(seq) && G().loop.runId === id) setG((x) => ({ loop: Object.assign({}, x.loop, { busy: false }) }));
  }
}

// The run behind the loop: the open run's detail when that is it, else the loop's own last read.
function loopRun(g, id) {
  const d = g.detail;
  if (d.runId === id && d.data && d.data.run && d.data.run.id === id) return d.data.run;
  return g.loop.run && g.loop.run.id === id ? g.loop.run : null;
}

async function refreshLoopRun(id, seq) {
  if (G().detail.runId === id) { await pollRun(); return; }
  try {
    const out = await goldenApi.run(id);
    if (!live(seq) || G().loop.runId !== id) return;
    setG((x) => ({ loop: Object.assign({}, x.loop, { run: (out && out.run) || null }) }));
  } catch (e) { /* the next tick reads it again */ }
}

async function stepOnce(id, seq) {
  try {
    const out = await goldenApi.step(id);
    if (!live(seq) || G().loop.runId !== id) return;
    setG((x) => ({ loop: Object.assign({}, x.loop, { note: out && out.started === false && (out.reason === 'in_flight' || out.reason === 'busy') ? 'A case is being answered…' : '' }) }));
    if (out && out.done) {
      stopLoop();
      loadGoldenRuns();
    }
    await refreshLoopRun(id, seq);
  } catch (e) {
    if (isStaleScope(e) || !live(seq) || G().loop.runId !== id) return;
    const reason = reasonOf(e);
    if (TRANSIENT.has(reason)) {
      setG((x) => ({ loop: Object.assign({}, x.loop, { note: reason === 'token_expiring' ? 'Waiting for your sign-in to renew…' : 'A case is being answered…' }) }));
      return;
    }
    if (e && e.status && e.status < 500) {
      stopWith(reason, msg(e), reason === 'run_cap' || reason === 'monthly_cap' || reason === 'paused');
      if (G().detail.runId === id) await pollRun();
      loadGoldenRuns();
      return;
    }
    setG((x) => ({ loop: Object.assign({}, x.loop, { note: "Couldn't reach Trace Lab — trying again." }) }));
  }
}

// ── the expected outcome of a golden answer ───────────────────────────────────────────────────────────────────
const expPart = (turnId, p) => setG((g) => ({ expected: Object.assign({}, g.expected, { [turnId]: Object.assign({}, g.expected[turnId] || {}, p) }) }));

// Read the panel for an answer: blind (no expected answer, no grade) unless this reviewer has revealed it before.
export async function loadExpected(turnId) {
  if (!turnId) return null;
  const x = G().expected[turnId];
  if (x && (x.loading || x.data)) return x.data || null;
  const seq = get().seq;
  expPart(turnId, { loading: true, error: '' });
  try {
    const out = await goldenApi.expected(turnId);
    if (!live(seq)) return null;
    expPart(turnId, { loading: false, data: out || null });
    return out;
  } catch (e) {
    if (isStaleScope(e) || !live(seq)) return null;
    expPart(turnId, { loading: false, error: msg(e) });
    return null;
  }
}

// The reviewer chose to see the expected outcome: the service records it and returns it. Already revealed: show it.
export async function revealExpected(turnId) {
  if (!turnId) return null;
  const x = G().expected[turnId];
  if (x && x.data && x.data.revealed) { expPart(turnId, { hidden: false }); return x.data; }
  if (x && x.revealing) return null;
  const seq = get().seq;
  expPart(turnId, { revealing: true, error: '' });
  try {
    const out = await goldenApi.reveal(turnId);
    if (!live(seq)) return null;
    expPart(turnId, { revealing: false, hidden: false, data: out || null });
    return out;
  } catch (e) {
    if (isStaleScope(e) || !live(seq)) return null;
    expPart(turnId, { revealing: false, error: msg(e) });
    return null;
  }
}

export function hideExpected(turnId) { if (turnId) expPart(turnId, { hidden: true }); }
