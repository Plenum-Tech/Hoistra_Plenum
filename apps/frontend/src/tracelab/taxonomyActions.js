// taxonomyActions — everything Trace Lab's failure-mode work does: read and change failure modes (the mode drawer and
// its dialogs), propose failure modes (a company's reviewers) and accept or reject the proposals (the platform team),
// file reviewers' unfiled notes, keep the requirement register, read and write the Labels grid, decide
// AI suggestions (the queue, bulk approval with its audit, spot-checks, proposal runs) and decide the suggestions on
// the open answer in Conversations. Every request goes through taxonomyApi.js. State lives in the store's
// `taxonomy` slice (logic/taxCommon.js), tagged with the store's seq: a company switch (resetForCompany bumps the
// seq) leaves the old slice unread, a reply issued before the switch is dropped and writes nothing, a reply
// overtaken by a newer read of the same thing is dropped, and the app's stale-company error stays quiet.
//
// Every write goes through write(), so a failure stays on screen with Retry; a refusal the server explains (a name
// taken, a contract incomplete, a bulk refused by its audit) comes back from explained() as {refusal, message,
// detail} and is shown where the person acted. Nothing in a bulk queue is selected for the reviewer:
// tlProposalsSelected starts empty, "Select all" takes the visible page only when clicked, and a refetch keeps only
// the selected ids that are still on the page. The pure helpers and the view models are in logic/tax*.js; the
// buttons the view models carry call the actions below by name (the screens pass this module as `a`).
import { tlStore } from './store.js';
import { isStaleScope, tl } from './api.js';
import { taxonomyApi } from './taxonomyApi.js';
import { openInConversations, setTab } from './actions.js';
import { loadProgress } from './samplingActions.js';
import {
  errText, explained, has, hasCompany, metaOf, owns, proposes, taxOf
} from './logic/taxCommon.js';
import { MATRIX_FILTER, MATRIX_PAGE, cellOf, nextValue } from './logic/labels.js';
import { suggestionMarks } from './logic/suggest.js';
import {
  ACTIVE, QUEUE_KINDS, QUEUE_PAGE, RUNS_POLL_MS, checkable, draftEdits, keepSelected, runForm, startProblem, formKey
} from './logic/proposals.js';
import {
  ACCEPT_NOTICE, CONTRACT_TEXT, NOTICE, STALE_MODE, STALE_PROPOSAL, UNFILED_PAGE, blankDraft, dialogProblems, draftProblems, latestReq, modeById, reqProblems
} from './logic/taxonomy.js';

const get = () => tlStore.get();
const T = () => taxOf(get());
const setT = (patch) => tlStore.set((s) => {
  const t = taxOf(s);
  return { taxonomy: Object.assign({}, t, typeof patch === 'function' ? patch(t) : patch) };
});
const live = (seq) => seq === get().seq;
const enc = encodeURIComponent;
const asks = { mode: 0, modeProps: 0, matrix: 0, proposals: 0, sug: 0 };

// ── writes never fail silently: an inline error with Retry; a 409 says another tab changed it ────────────────
const writeFns = {};

// Runs one write. Resolves to {ok: true, out}, {ok: false, stale: true} (the company changed meanwhile: nothing is
// shown) or {ok: false, error, conflict}; never throws. A failure is kept under tlWrites[key] with Retry (not for a
// 409, which says to reload, nor with `retry: false`). `again` is the action that made the write: Retry runs it
// whole, so what the action does after a success (the grid cell, the lists to read again) happens on a retry too.
export async function write(fn, opts) {
  const o = opts || {};
  const key = o.key || 'write';
  const retry = o.retry !== false;
  writeFns[key] = { fn, opts: o };
  const seq = get().seq;
  const drop = () => setT((t) => { const w = Object.assign({}, t.tlWrites); delete w[key]; return { tlWrites: w }; });
  setT((t) => ({ tlWrites: Object.assign({}, t.tlWrites, { [key]: { busy: true, error: '', conflict: false, canRetry: false } }) }));
  try {
    const out = await fn();
    if (!live(seq)) { drop(); return { ok: false, stale: true }; }
    delete writeFns[key];
    drop();
    return { ok: true, out };
  } catch (e) {
    if (isStaleScope(e) || !live(seq)) { drop(); return { ok: false, stale: true }; }
    const conflict = !!(e && e.status === 409);
    const error = conflict ? 'Changed in another tab — reload this answer.' : 'Could not save — ' + errText(e);
    setT((t) => ({ tlWrites: Object.assign({}, t.tlWrites, { [key]: { busy: false, error, conflict, canRetry: retry && !conflict } }) }));
    return { ok: false, error, conflict };
  }
}

export function retryWrite(key) {
  const w = writeFns[key];
  if (!w) return Promise.resolve({ ok: false });
  if (typeof w.opts.again === 'function') return Promise.resolve(w.opts.again()).then((ok) => ({ ok: !!ok }));
  return write(w.fn, w.opts);
}

// ── failure modes ─────────────────────────────────────────────────────────────────────────────────────────────
export async function modesLoad() {
  const seq = get().seq;
  setT({ tlModesLoading: true, tlModesErr: '' });
  try {
    const out = await taxonomyApi.modes({ include_retired: true });
    if (!live(seq)) return;
    setT({ tlModes: (out && out.modes) || [], tlModesMeta: metaOf(out), tlModesLoading: false, tlModesLoadedAt: Date.now() });
  } catch (e) {
    if (isStaleScope(e) || !live(seq)) return;
    setT({ tlModesLoading: false, tlModesErr: errText(e) });
  }
}

export function modesEnsure() {
  const t = T();
  if (!t.tlModesLoading && !t.tlModesLoadedAt) modesLoad();
}

export async function modeShow(modeId) {
  if (!modeId) return;
  const seq = get().seq;
  const ask = (asks.mode += 1);
  setT((t) => ({ tlModesDrawerId: modeId, tlModesDrawerLoading: true, tlModesDrawerErr: '',
    tlModesDrawer: t.tlModesDrawerId === modeId ? t.tlModesDrawer : null }));
  modesEnsure();
  try {
    const out = await taxonomyApi.mode(modeId);
    if (!live(seq) || ask !== asks.mode) return;
    setT({ tlModesDrawer: out || null, tlModesDrawerLoading: false });
  } catch (e) {
    if (isStaleScope(e) || !live(seq) || ask !== asks.mode) return;
    setT({ tlModesDrawerLoading: false, tlModesDrawerErr: errText(e) });
  }
}

export function modeClose() { setT({ tlModesDrawerId: null, tlModesDrawer: null, tlModesDrawerErr: '' }); }

// After a taxonomy write: the list, the open drawer, and whatever else on screen shows modes, read again.
export function modesRefresh(openId) {
  modesLoad();
  const id = openId || T().tlModesDrawerId;
  if (id) modeShow(id);
  if (T().tlUnfiledLoadedAt) unfiledLoad();
  if (T().tlMatrixLoadedAt) matrixLoad();
  if (get().tab === 'progress') loadProgress();
}

// ── failure modes companies proposed ─────────────────────────────────────────────────────────────────────────────
// The platform team reads every company's waiting proposals; a company reads all of its own (waiting, rejected with the
// reason, accepted). The service answers each from who the person is; the status asked for follows the same list.
export async function modePropsLoad() {
  const s = get();
  const seq = s.seq;
  const ask = (asks.modeProps += 1);
  setT({ tlModesProposedLoading: true, tlModesProposedErr: '' });
  try {
    const out = await taxonomyApi.modeProposals({ status: owns(s) ? 'proposed' : 'all' });
    if (!live(seq) || ask !== asks.modeProps) return;
    setT({ tlModesProposed: (out && out.proposals) || [], tlModesProposedLoading: false, tlModesProposedLoadedAt: Date.now() });
  } catch (e) {
    if (isStaleScope(e) || !live(seq) || ask !== asks.modeProps) return;
    setT({ tlModesProposedLoading: false, tlModesProposedErr: errText(e) });
  }
}

// Accept: the proposal becomes a candidate and its notes are filed under it. Only the platform team; the version sent is
// the one the row was read at, so a proposal someone else changed meanwhile is refused as stale.
export async function modePropAccept(modeId) {
  const s = get();
  if (!owns(s)) return false;
  const p = (taxOf(s).tlModesProposed || []).find((x) => x.id === modeId && x.status === 'proposed');
  if (!p) return false;
  setT({ tlModesProposedNotice: '' });
  const r = await write(explained(() => taxonomyApi.acceptModeProposal(modeId, { base_version: p.version }), STALE_PROPOSAL),
    { key: 'mode-prop:' + modeId, again: () => modePropAccept(modeId) });
  if (!r.ok) return false;
  if (r.out && r.out.refusal) { setT({ tlModesProposedErr: p.name + ': ' + r.out.message }); return false; }
  setT({ tlModesProposedNotice: ACCEPT_NOTICE, tlModesProposedErr: '' });
  modePropsLoad();
  modesRefresh();
  return true;
}

// Opens a dialog. 'file' is any reviewer's and 'propose' a company reviewer's; every other kind belongs to the platform
// team (D7), and for anyone else nothing opens.
export function modeDialog(kind, opts) {
  const s = get();
  const t = taxOf(s);
  const o = opts || {};
  const allowed = kind === 'file' || (kind === 'propose' ? proposes(s) : owns(s));
  if (!allowed) return false;
  const dm = t.tlModesDrawer && t.tlModesDrawer.mode;
  const m = o.modeId ? (dm && dm.id === o.modeId ? dm : modeById(t, o.modeId)) : null;
  const pending = kind === 'reject' && o.modeId ? (t.tlModesProposed || []).find((x) => x.id === o.modeId && x.status === 'proposed') : null;
  let d = null;
  if (kind === 'create' || kind === 'propose') d = Object.assign(blankDraft(), { kind, createdFrom: (o.createdFrom || []).slice(0, 50) });
  else if (kind === 'reject' && pending) d = { kind, modeId: pending.id, base: pending.version, name: pending.name, reason: '' };
  else if (kind === 'edit' && m) {
    d = { kind, modeId: m.id, base: m.version, status: m.status, name: m.name, definition: m.definition || '', boundary: m.boundary || '',
      nearest_mode_id: m.nearest_mode_id || '', level: m.level || '', agent: m.agent || '', evaluator_type: m.evaluator_type || '',
      requirement_ids: (m.requirement_ids || []).slice(), close_negatives_waiver: m.close_negatives_waiver || '', rescope: false };
  } else if (kind === 'rename' && m) d = { kind, modeId: m.id, base: m.version, name: m.name, oldName: m.name };
  else if (kind === 'merge' && m) d = { kind, modeId: m.id, sources: [m.id], target: 'existing', into: '', draft: blankDraft({ level: m.level, agent: m.agent }) };
  else if (kind === 'split' && m) {
    d = { kind, modeId: m.id, base: m.version, name: m.name, noteChild: {},
      children: [blankDraft({ level: m.level, agent: m.agent }), blankDraft({ level: m.level, agent: m.agent })] };
  } else if ((kind === 'retire' || kind === 'freeze') && m) d = { kind, modeId: m.id, base: m.version, name: m.name, reason: '' };
  else if (kind === 'file' && o.annotationId) d = { kind, annotationId: o.annotationId, turnId: o.turnId || null, modeId: '', override: false, conflict: '' };
  else if (kind === 'dispose' && o.annotationId) d = { kind, annotationId: o.annotationId, outcome: o.outcome === 'not_a_failure' ? 'not_a_failure' : 'no_mode_fits', note: '' };
  if (!d) return false;
  setT({ tlModesDialog: d, tlModesDialogErr: '', tlModesDialogMissing: [], tlModesNotice: '', tlModesProposedNotice: '' });
  modesEnsure();
  if (kind === 'create' || kind === 'edit') reqsEnsure();
  return true;
}

export function modeDialogSet(patch) {
  setT((t) => (t.tlModesDialog ? { tlModesDialog: Object.assign({}, t.tlModesDialog, patch), tlModesDialogErr: '' } : {}));
}

export function modeDialogToggle(field, id) {
  setT((t) => {
    const d = t.tlModesDialog;
    if (!d || !Array.isArray(d[field])) return {};
    const list = d[field].indexOf(id) > -1 ? d[field].filter((x) => x !== id) : d[field].concat([id]);
    return { tlModesDialog: Object.assign({}, d, { [field]: list }), tlModesDialogErr: '' };
  });
}

export function modeDialogDraft(patch) {
  setT((t) => (t.tlModesDialog && t.tlModesDialog.draft
    ? { tlModesDialog: Object.assign({}, t.tlModesDialog, { draft: Object.assign({}, t.tlModesDialog.draft, patch) }), tlModesDialogErr: '' } : {}));
}

export function modeDialogChild(i, patch) {
  setT((t) => {
    const d = t.tlModesDialog;
    if (!d || d.kind !== 'split' || !d.children[i]) return {};
    const children = d.children.slice();
    children[i] = Object.assign({}, children[i], patch);
    return { tlModesDialog: Object.assign({}, d, { children }), tlModesDialogErr: '' };
  });
}

export function modeDialogAddChild() {
  setT((t) => {
    const d = t.tlModesDialog;
    if (!d || d.kind !== 'split' || d.children.length >= 5) return {};
    const first = d.children[0] || {};
    return { tlModesDialog: Object.assign({}, d, { children: d.children.concat([blankDraft({ level: first.level, agent: first.agent })]) }) };
  });
}

export function modeDialogRemoveChild(i) {
  setT((t) => {
    const d = t.tlModesDialog;
    if (!d || d.kind !== 'split' || d.children.length <= 2) return {};
    const noteChild = {};
    Object.keys(d.noteChild).forEach((id) => {
      const k = d.noteChild[id];
      if (k !== i) noteChild[id] = k > i ? k - 1 : k;
    });
    return { tlModesDialog: Object.assign({}, d, { children: d.children.filter((c, k) => k !== i), noteChild }) };
  });
}

export function modeDialogNote(annotationId, child) {
  setT((t) => {
    const d = t.tlModesDialog;
    if (!d || d.kind !== 'split') return {};
    const noteChild = Object.assign({}, d.noteChild);
    const k = Number(child);
    if (child === '' || child === null || child === undefined || !(k >= 0)) delete noteChild[annotationId];
    else noteChild[annotationId] = k;
    return { tlModesDialog: Object.assign({}, d, { noteChild }) };
  });
}

export function modeDialogClose() { setT({ tlModesDialog: null, tlModesDialogErr: '', tlModesDialogMissing: [] }); }

function draftBody(x) {
  return { name: x.name, definition: String(x.definition || '').trim(), boundary: String(x.boundary || '').trim(),
    nearest_mode_id: x.nearest_mode_id || null, level: x.level, agent: x.agent, evaluator_type: x.evaluator_type || null,
    requirement_ids: (x.requirement_ids || []).slice(0, 10) };
}

// A proposal carries only the draft the service accepts: how it is checked and the requirements it cites are the
// platform team's to set.
function proposalBody(x) {
  const b = draftBody(x);
  return { name: b.name, definition: b.definition, boundary: b.boundary, nearest_mode_id: b.nearest_mode_id, level: b.level, agent: b.agent,
    created_from: x.createdFrom };
}

// The call one dialog sends, built from the dialog's own state. null when there is nothing to send.
function dialogCall(d, t) {
  if (d.kind === 'create') return () => taxonomyApi.createMode(Object.assign(draftBody(d), { created_from: d.createdFrom }));
  if (d.kind === 'propose') return () => taxonomyApi.proposeMode(proposalBody(d));
  if (d.kind === 'reject') return () => taxonomyApi.rejectModeProposal(d.modeId, { reason: String(d.reason || '').trim(), base_version: d.base });
  if (d.kind === 'edit') {
    const b = draftBody(d);
    return () => taxonomyApi.editMode(d.modeId, { base_version: d.base, definition: b.definition, boundary: b.boundary,
      nearest_mode_id: b.nearest_mode_id, level: b.level, agent: b.agent, evaluator_type: b.evaluator_type,
      requirement_ids: b.requirement_ids, close_negatives_waiver: String(d.close_negatives_waiver || '').trim() || null, rescope: !!d.rescope });
  }
  if (d.kind === 'rename') return () => taxonomyApi.renameMode(d.modeId, { name: d.name, base_version: d.base });
  if (d.kind === 'merge') {
    const ids = d.sources.concat(d.target === 'existing' && d.into ? [d.into] : []);
    const bases = {};
    ids.forEach((id) => { const m = modeById(t, id); if (m) bases[id] = m.version; });
    return () => taxonomyApi.mergeModes({ source_ids: d.sources, into: d.target === 'existing' ? d.into : null,
      new: d.target === 'new' ? draftBody(d.draft) : null, base_versions: bases });
  }
  if (d.kind === 'split') {
    const dm = t.tlModesDrawer && t.tlModesDrawer.mode;
    const origin = new Set(dm && Array.isArray(dm.created_from) ? dm.created_from : []);
    const notes = (t.tlModesDrawer && t.tlModesDrawer.notes) || [];
    const children = d.children.map((c) => Object.assign(draftBody(c), { created_from: [] }));
    const assignments = {};
    notes.forEach((n) => {
      const i = d.noteChild[n.annotation_id];
      if (typeof i !== 'number' || i < 0 || !children[i]) return;
      if (origin.has(n.annotation_id)) children[i].created_from.push(n.annotation_id);
      const list = assignments[n.turn_id] || [];
      if (list.indexOf(i) < 0) list.push(i);
      assignments[n.turn_id] = list;
    });
    return () => taxonomyApi.splitMode(d.modeId, { base_version: d.base, children, assignments });
  }
  if (d.kind === 'retire') return () => taxonomyApi.retireMode(d.modeId, { reason: String(d.reason || '').trim(), base_version: d.base });
  if (d.kind === 'freeze') return () => taxonomyApi.editMode(d.modeId, { base_version: d.base, status: 'frozen' });
  if (d.kind === 'file') {
    return async () => {
      let base = null;
      if (d.turnId) {
        const cells = await taxonomyApi.turnLabels(d.turnId);
        const cell = ((cells && cells.cells) || []).find((x) => x.mode_id === d.modeId);
        base = cell ? cell.base || null : null;
      }
      return taxonomyApi.fileNote(d.annotationId, { mode_id: d.modeId, base, override_label: !!d.override });
    };
  }
  if (d.kind === 'dispose') return () => taxonomyApi.disposeNote(d.annotationId, { outcome: d.outcome, note: String(d.note || '').trim() });
  return null;
}

export async function modeDialogSubmit() {
  const t = T();
  const d = t.tlModesDialog;
  if (!d) return false;
  const problems = dialogProblems(d, t);
  if (problems.length) { setT({ tlModesDialogErr: problems[0] }); return false; }
  const call = dialogCall(d, t);
  if (!call) return false;
  const r = await write(explained(call, STALE_MODE), { key: 'mode-dialog', again: modeDialogSubmit });
  if (!r.ok) return false;
  const out = r.out || {};
  if (out.refusal) {
    const patch = { tlModesDialogErr: out.message, tlModesDialogMissing: (out.detail && out.detail.missing) || [] };
    if (d.kind === 'file' && out.refusal === 'conflicts_with_label') patch.tlModesDialog = Object.assign({}, d, { conflict: out.message });
    setT(patch);
    return false;
  }
  const openId = (d.kind === 'create' || d.kind === 'merge') ? (out.mode && out.mode.id) || null
    : (d.kind === 'split' || d.kind === 'file' || d.kind === 'dispose' || d.kind === 'propose' || d.kind === 'reject') ? null : d.modeId;
  setT((p) => Object.assign({ tlModesDialog: null, tlModesDialogErr: '', tlModesDialogMissing: [],
    tlUnfiledChosen: d.kind === 'create' || d.kind === 'propose' ? [] : (p.tlUnfiledChosen || []), tlUnfiledUndo: d.kind === 'dispose' ? d.annotationId : null },
  // A rejection is told beside the proposals; every other outcome on the line the unfiled notes show.
  d.kind === 'reject' ? { tlModesProposedNotice: NOTICE.reject } : { tlModesNotice: NOTICE[d.kind] || 'Saved.' }));
  if (d.kind === 'split') setT({ tlModesDrawerId: null, tlModesDrawer: null });
  // Proposing or rejecting changes no failure mode and files no note: only the list of proposals is read again.
  if (d.kind === 'propose' || d.kind === 'reject') { modePropsLoad(); return true; }
  modesRefresh(openId);
  if ((d.kind === 'file' || d.kind === 'dispose') && !T().tlUnfiledLoadedAt) unfiledLoad();
  return true;
}

// A status change from the drawer: confirm (the contract gate), back to candidate. Freezing goes through its dialog.
export async function modeStatus(modeId, status) {
  const s = get();
  const t = taxOf(s);
  if (!owns(s)) return false;
  const dm = t.tlModesDrawer && t.tlModesDrawer.mode;
  const m = dm && dm.id === modeId ? dm : modeById(t, modeId);
  if (!m) return false;
  const r = await write(explained(() => taxonomyApi.editMode(modeId, { base_version: m.version, status }), STALE_MODE), { key: 'mode:' + modeId, again: () => modeStatus(modeId, status) });
  if (!r.ok) return false;
  if (r.out && r.out.refusal) {
    const missing = (r.out.detail && r.out.detail.missing) || [];
    setT({ tlModesDrawerErr: r.out.message + (missing.length ? ' Missing: ' + missing.map((k) => CONTRACT_TEXT[k] || k).join('; ') + '.' : '') });
    return false;
  }
  modesRefresh(modeId);
  return true;
}

export const modeConfirm = (modeId) => modeStatus(modeId, 'confirmed');
export const modeToCandidate = (modeId) => modeStatus(modeId, 'candidate');

export async function modeRestore(modeId) {
  const s = get();
  const t = taxOf(s);
  if (!owns(s)) return false;
  const m = modeById(t, modeId) || (t.tlModesDrawer && t.tlModesDrawer.mode);
  if (!m) return false;
  const r = await write(explained(() => taxonomyApi.restoreMode(modeId, { base_version: m.version }), STALE_MODE), { key: 'mode:' + modeId, again: () => modeRestore(modeId) });
  if (!r.ok) return false;
  if (r.out && r.out.refusal) { setT({ tlModesDrawerErr: r.out.message }); return false; }
  modesRefresh(modeId);
  return true;
}

// Adds or removes one answer from the mode's listed positives or close negatives (the contract's examples).
export async function modeList(modeId, field, turnId, add) {
  const s = get();
  const t = taxOf(s);
  if (!owns(s) || (field !== 'positive_turn_ids' && field !== 'close_negative_turn_ids')) return false;
  const dm = t.tlModesDrawer && t.tlModesDrawer.mode;
  const m = dm && dm.id === modeId ? dm : modeById(t, modeId);
  if (!m) return false;
  const list = (m[field] || []).filter((x) => x !== turnId);
  if (add) list.push(turnId);
  const r = await write(explained(() => taxonomyApi.editMode(modeId, { base_version: m.version, [field]: list.slice(0, 50) }), STALE_MODE),
    { key: 'mode:' + modeId, again: () => modeList(modeId, field, turnId, add) });
  if (!r.ok) return false;
  if (r.out && r.out.refusal) { setT({ tlModesDrawerErr: r.out.message }); return false; }
  modesRefresh(modeId);
  return true;
}

// ── opening an answer ─────────────────────────────────────────────────────────────────────────────────────────
// An answer named by its turn only: the service says which conversation it is in.
export async function openTurnById(turnId) {
  if (!turnId) return false;
  const seq = get().seq;
  try {
    const out = await tl.get('/turns/' + enc(turnId));
    if (!live(seq)) return false;
    const key = out && out.conversation_key;
    if (!key) throw new Error('This answer is not part of a conversation you can open.');
    openInConversations(key, turnId);
    return true;
  } catch (e) {
    if (isStaleScope(e) || !live(seq)) return false;
    tlStore.set((p) => ({ tab: 'conversations', conv: { data: null, loading: false, error: errText(e), reason: '' }, convKey: null, turnId: null, open: p.open }));
    return false;
  }
}

// Opens an answer in Conversations, when the person works inside one company (the platform team cannot).
export function taxOpenAnswer(turnId, conversationKey) {
  if (!turnId || !hasCompany(get())) return false;
  setT({ tlModesDrawerId: null, tlModesDrawer: null });
  if (conversationKey) openInConversations(conversationKey, turnId);
  else openTurnById(turnId);
  return true;
}

// ── reviewers' unfiled notes ──────────────────────────────────────────────────────────────────────────────────
export async function unfiledLoad() {
  const seq = get().seq;
  setT({ tlUnfiledLoading: true, tlUnfiledErr: '' });
  try {
    const out = await taxonomyApi.unfiledNotes({ limit: UNFILED_PAGE, offset: 0 });
    if (!live(seq)) return;
    const rows = (out && out.notes) || [];
    setT((t) => ({ tlUnfiled: rows, tlUnfiledTotal: out && out.total !== undefined ? out.total : null, tlUnfiledHidden: (out && out.hidden) || 0,
      tlUnfiledLoading: false, tlUnfiledLoadedAt: Date.now(),
      tlUnfiledChosen: (t.tlUnfiledChosen || []).filter((id) => rows.some((r) => r.annotation_id === id)) }));
  } catch (e) {
    if (isStaleScope(e) || !live(seq)) return;
    setT({ tlUnfiledLoading: false, tlUnfiledErr: errText(e) });
  }
}

// Notes chosen for one new failure mode (or one proposal). Nothing is chosen until the reviewer ticks it.
export function unfiledChoose(annotationId) {
  if (!owns(get()) && !proposes(get())) return;
  setT((t) => {
    const list = t.tlUnfiledChosen || [];
    return { tlUnfiledChosen: list.indexOf(annotationId) > -1 ? list.filter((x) => x !== annotationId) : list.concat([annotationId]) };
  });
}

export function unfiledClearChosen() { setT({ tlUnfiledChosen: [] }); }
export function noteNewMode(annotationIds) { return modeDialog('create', { createdFrom: [].concat(annotationIds || []) }); }
// A company reviewer's way to the same dialog: the draft is sent as a proposal for the platform team to decide.
export function noteProposeMode(annotationIds) { return modeDialog('propose', { createdFrom: [].concat(annotationIds || []) }); }

export function noteFile(annotationId) {
  const n = (T().tlUnfiled || []).find((x) => x.annotation_id === annotationId);
  return modeDialog('file', { annotationId, turnId: n ? n.turn_id : null });
}

export function noteDispose(annotationId, outcome) { return modeDialog('dispose', { annotationId, outcome }); }

export async function noteUndispose(annotationId) {
  if (!owns(get()) || !annotationId) return false;
  const r = await write(explained(() => taxonomyApi.undisposeNote(annotationId)), { key: 'undispose', again: () => noteUndispose(annotationId) });
  if (!r.ok) return false;
  if (r.out && r.out.refusal) { setT({ tlUnfiledErr: r.out.message }); return false; }
  setT({ tlUnfiledUndo: null, tlModesNotice: '' });
  unfiledLoad();
  if (get().tab === 'progress') loadProgress();
  return true;
}

// ── the requirement register ─────────────────────────────────────────────────────────────────────────────────
export async function reqsLoad() {
  const seq = get().seq;
  setT({ tlReqsLoading: true, tlReqsErr: '' });
  try {
    const out = await taxonomyApi.requirements({});
    if (!live(seq)) return;
    setT({ tlReqs: (out && out.requirements) || [], tlReqsMeta: { areas: (out && out.areas) || [], levels: (out && out.levels) || [] },
      tlReqsLoading: false, tlReqsLoadedAt: Date.now() });
  } catch (e) {
    if (isStaleScope(e) || !live(seq)) return;
    setT({ tlReqsLoading: false, tlReqsErr: errText(e) });
  }
}

export function reqsEnsure() {
  const t = T();
  if (!t.tlReqsLoading && !t.tlReqsLoadedAt) reqsLoad();
}

export function reqsOpen(kind, code) {
  const s = get();
  const t = taxOf(s);
  if (!owns(s)) return false;
  const latest = code ? latestReq(t.tlReqs, code) : null;
  let f = null;
  if (kind === 'create') f = { kind, area: '', level: '', statement: '', impls: [], note: '', motivating: [] };
  else if (kind === 'revise' && latest) {
    f = { kind, code, statement: latest.statement, note: '', motivating: (latest.motivating_turn_ids || []).slice(),
      impls: (latest.implementations || []).map((i) => ({ doc: i.doc || '', section: i.section || '', line: i.line ? String(i.line) : '', quote: i.quote || '' })) };
  } else if (kind === 'withdraw' && latest) f = { kind, code, statement: latest.statement, note: '' };
  if (!f) return false;
  setT({ tlReqsForm: f, tlReqsFormErr: '', tlReqsNotice: '' });
  reqsEnsure();
  return true;
}

export function reqsSet(patch) { setT((t) => (t.tlReqsForm ? { tlReqsForm: Object.assign({}, t.tlReqsForm, patch), tlReqsFormErr: '' } : {})); }

export function reqsImpl(i, patch) {
  setT((t) => {
    const f = t.tlReqsForm;
    if (!f || !f.impls || !f.impls[i]) return {};
    const impls = f.impls.slice();
    impls[i] = Object.assign({}, impls[i], patch);
    return { tlReqsForm: Object.assign({}, f, { impls }), tlReqsFormErr: '' };
  });
}

export function reqsAddImpl() {
  setT((t) => (t.tlReqsForm && t.tlReqsForm.impls && t.tlReqsForm.impls.length < 10
    ? { tlReqsForm: Object.assign({}, t.tlReqsForm, { impls: t.tlReqsForm.impls.concat([{ doc: '', section: '', line: '', quote: '' }]) }) } : {}));
}

export function reqsRemoveImpl(i) {
  setT((t) => (t.tlReqsForm && t.tlReqsForm.impls
    ? { tlReqsForm: Object.assign({}, t.tlReqsForm, { impls: t.tlReqsForm.impls.filter((x, k) => k !== i) }) } : {}));
}

export function reqsClose() { setT({ tlReqsForm: null, tlReqsFormErr: '' }); }

export async function reqsSave() {
  const s = get();
  const f = taxOf(s).tlReqsForm;
  if (!f || !owns(s)) return false;
  const problems = reqProblems(f);
  if (problems.length) { setT({ tlReqsFormErr: problems[0] }); return false; }
  const impls = (f.impls || []).map((i) => ({ doc: String(i.doc).trim(), section: String(i.section || '').trim(), line: i.line ? Number(i.line) : null, quote: String(i.quote).trim() }));
  const call = f.kind === 'create'
    ? () => taxonomyApi.createRequirement({ area: f.area, statement: String(f.statement).trim(), level: f.level, implementations: impls, motivating_turn_ids: [], activate: true })
    : f.kind === 'revise'
      ? () => taxonomyApi.reviseRequirement(f.code, { statement: String(f.statement).trim(), implementations: impls, motivating_turn_ids: f.motivating || [], note: String(f.note).trim(), activate: true })
      : () => taxonomyApi.withdrawRequirement(f.code, { note: String(f.note).trim() });
  const r = await write(explained(call), { key: 'requirement', again: reqsSave });
  if (!r.ok) return false;
  const out = r.out || {};
  if (out.refusal) { setT({ tlReqsFormErr: out.message }); return false; }
  const flagged = (out.flagged_modes || []).map((m) => m.name);
  const code = out.requirement ? out.requirement.code : f.code;
  const done = f.kind === 'create' ? 'Added ' + code + '.' : f.kind === 'revise' ? 'Revised ' + code + '.' : 'Withdrew ' + code + '.';
  setT({ tlReqsForm: null, tlReqsFormErr: '', tlReqsNotice: done + (flagged.length ? ' Recheck the failure modes that cite it: ' + flagged.join(', ') + '.' : '') });
  reqsLoad();
  if (flagged.length && T().tlModesLoadedAt) modesLoad();
  return true;
}

export async function reqsSeed() {
  if (!owns(get())) return false;
  const r = await write(explained(() => taxonomyApi.seedRequirements()), { key: 'requirement-seed', again: reqsSeed });
  if (!r.ok) return false;
  const out = r.out || {};
  if (out.refusal) { setT({ tlReqsErr: out.message }); return false; }
  const ins = (out.inserted || []).length;
  const skip = (out.skipped || []).length;
  const drift = (out.drift || []).length;
  setT({ tlReqsNotice: 'Added ' + ins + (ins === 1 ? ' requirement' : ' requirements') + '; ' + skip + ' already there.'
    + (drift ? ' ' + drift + (drift === 1 ? ' quote no longer matches its document.' : ' quotes no longer match their documents.') : '') });
  reqsLoad();
  return true;
}

// ── the Labels grid ────────────────────────────────────────────────────────────────────────────────────────────
export async function matrixLoad() {
  const seq = get().seq;
  const ask = (asks.matrix += 1);
  const f = Object.assign({}, MATRIX_FILTER, T().tlMatrixFilter || {});
  setT({ tlMatrixLoading: true, tlMatrixErr: '' });
  try {
    const out = await taxonomyApi.labelsMatrix({ batch_id: f.batch_id || undefined, state: f.state, limit: MATRIX_PAGE, offset: f.offset });
    if (!live(seq) || ask !== asks.matrix) return;
    setT({ tlMatrix: out || null, tlMatrixLoading: false, tlMatrixLoadedAt: Date.now() });
  } catch (e) {
    if (isStaleScope(e) || !live(seq) || ask !== asks.matrix) return;
    setT({ tlMatrixLoading: false, tlMatrixErr: errText(e) });
  }
}

export function matrixEnsure() {
  const t = T();
  if (!t.tlMatrixLoading && !t.tlMatrixLoadedAt) matrixLoad();
  modesEnsure();
}

export function matrixSetFilter(patch) {
  const f = Object.assign({}, MATRIX_FILTER, T().tlMatrixFilter || {}, patch || {});
  if (!patch || patch.offset === undefined) f.offset = 0;
  setT({ tlMatrixFilter: f, tlMatrixCell: null, tlMatrixHistory: null });
  return matrixLoad();
}

export function matrixPage(delta) {
  const f = Object.assign({}, MATRIX_FILTER, T().tlMatrixFilter || {});
  const total = (T().tlMatrix && T().tlMatrix.total_rows) || 0;
  const lastPage = Math.max(0, Math.floor((total - 1) / MATRIX_PAGE)) * MATRIX_PAGE;
  const offset = Math.max(0, Math.min(f.offset + delta * MATRIX_PAGE, lastPage));
  if (offset !== f.offset) matrixSetFilter({ offset });
}

// One cell replaced in place, and the missing count with it, so the grid never jumps.
function replaceCell(turnId, modeId, fresh) {
  setT((t) => {
    const m = t.tlMatrix;
    if (!m) return {};
    let delta = 0;
    const rows = (m.rows || []).map((row) => {
      if (row.turn_id !== turnId || !row.cells || !row.cells[modeId]) return row;
      const before = row.cells[modeId].state;
      if (before === 'unchecked' && fresh.state !== 'unchecked') delta -= 1;
      if (before !== 'unchecked' && fresh.state === 'unchecked') delta += 1;
      return Object.assign({}, row, { cells: Object.assign({}, row.cells, { [modeId]: fresh }) });
    });
    const totals = m.totals ? Object.assign({}, m.totals, { missing: Math.max(0, (m.totals.missing || 0) + delta),
      done: Math.max(0, (m.totals.done || 0) - delta) }) : m.totals;
    return { tlMatrix: Object.assign({}, m, { rows, totals }) };
  });
}

// PUT /labels/{turn}/{mode} with base = the mode's own head the grid last read. present: true, false, or null to
// clear. On success the cell is replaced in place; a 409 says another tab changed it.
export async function matrixSet(turnId, modeId, present) {
  const c = cellOf(T(), turnId, modeId);
  const key = 'label:' + turnId + ':' + modeId;
  const r = await write(() => taxonomyApi.setLabel(turnId, modeId, { present, base: c ? c.base || null : null }),
    { key, again: () => matrixSet(turnId, modeId, present) });
  if (!r.ok) return false;
  const fresh = r.out && r.out.cell;
  if (fresh) replaceCell(turnId, modeId, fresh);
  const open = T().tlMatrixCell;
  if (open && open.turnId === turnId && open.modeId === modeId) matrixHistoryLoad(turnId, modeId);
  return true;
}

export const matrixToggle = (turnId, modeId) => matrixSet(turnId, modeId, nextValue(cellOf(T(), turnId, modeId)));
export const matrixClear = (turnId, modeId) => matrixSet(turnId, modeId, null);

export function matrixOpenCell(turnId, modeId) {
  setT({ tlMatrixCell: { turnId, modeId }, tlMatrixHistory: null, tlMatrixHistoryErr: '' });
  return matrixHistoryLoad(turnId, modeId);
}

export function matrixCloseCell() { setT({ tlMatrixCell: null, tlMatrixHistory: null, tlMatrixHistoryErr: '' }); }

export async function matrixHistoryLoad(turnId, modeId) {
  const seq = get().seq;
  try {
    const out = await taxonomyApi.labelHistory(turnId, modeId);
    const open = T().tlMatrixCell;
    if (!live(seq) || !open || open.turnId !== turnId || open.modeId !== modeId) return;
    setT({ tlMatrixHistory: (out && out.chain) || [], tlMatrixHistoryErr: '' });
  } catch (e) {
    if (isStaleScope(e) || !live(seq)) return;
    setT({ tlMatrixHistoryErr: errText(e) });
  }
}

// From Progress: the Labels tab with only the answers that still miss a judgment. (The tab opens once nav.js lists
// it; setTab ignores a tab it does not know.)
export function matrixShowMissing() {
  setT({ tlMatrixFilter: Object.assign({}, MATRIX_FILTER, { state: 'missing' }), tlMatrixCell: null, tlMatrixHistory: null });
  setTab('labels');
  return matrixLoad();
}


// ── AI proposals: the queue, bulk approval and its audit, spot-checks, proposal runs ───────────────────────────
let runsTimer = null;

// Stops what this module keeps running (the proposal runs' read every 5 seconds). The page calls it when it closes.
export function stopPolls() {
  if (runsTimer) clearTimeout(runsTimer);
  runsTimer = null;
}

// What the Progress tab's failure-mode part reads when it opens: modes (who the person is arrives with them),
// unfiled notes, the queue, the failure modes companies proposed and, for the platform team, the proposal runs.
export async function taxEnsure() {
  const t = T();
  const modes = !t.tlModesLoading && !t.tlModesLoadedAt ? modesLoad() : null;
  if (!t.tlUnfiledLoading && !t.tlUnfiledLoadedAt) unfiledLoad();
  if (!t.tlProposalsLoading && !t.tlProposalsLoadedAt) proposalsLoad();
  if (modes) await modes;
  // Which proposals are asked for depends on who the person is, which only the failure-mode list says.
  if (T().tlModesMeta && !T().tlModesProposedLoading && !T().tlModesProposedLoadedAt) modePropsLoad();
  if (owns(get())) {
    const u = T();
    if (!u.tlProposalsRunsLoading && !u.tlProposalsRunsLoadedAt) proposalsRunsLoad();
    else proposalsRunsWatch(u.tlProposalsRuns);
  }
}

// After a decision that wrote labels: the grid, the mode counts and Progress read again.
export function taxAfterLabels() {
  if (T().tlMatrixLoadedAt) matrixLoad();
  if (T().tlModesLoadedAt) modesLoad();
  if (get().tab === 'progress') loadProgress();
}

export async function proposalsLoad() {
  const s = get();
  const t = taxOf(s);
  const kind = t.tlProposalsKind || 'depth_scan';
  if (kind === 'group' && !owns(s)) return;
  const seq = s.seq;
  const ask = (asks.proposals += 1);
  setT({ tlProposalsLoading: true, tlProposalsErr: '' });
  try {
    const out = await taxonomyApi.proposals({ kind, status: 'pending', limit: QUEUE_PAGE, offset: t.tlProposalsOffset || 0 });
    if (!live(seq) || ask !== asks.proposals) return;
    const rows = (out && out.proposals) || [];
    setT((p) => ({ tlProposals: rows, tlProposalsTotal: out && out.total !== undefined ? out.total : null,
      tlProposalsHidden: (out && out.hidden) || 0, tlProposalsServedAt: (out && out.served_at) || null,
      tlProposalsSelected: keepSelected(p.tlProposalsSelected, rows), tlProposalsLoading: false, tlProposalsLoadedAt: Date.now() }));
  } catch (e) {
    if (isStaleScope(e) || !live(seq) || ask !== asks.proposals) return;
    setT({ tlProposalsLoading: false, tlProposalsErr: errText(e) });
  }
}

export function proposalsSetKind(kind) {
  const k = QUEUE_KINDS.find((x) => x.key === kind);
  if (!k || (k.owner && !owns(get())) || kind === T().tlProposalsKind) return;
  setT({ tlProposalsKind: kind, tlProposalsOffset: 0, tlProposals: null, tlProposalsLoadedAt: null, tlProposalsSelected: [],
    tlProposalsCheck: null, tlProposalsCheckIds: [], tlProposalsCheckServedAt: null, tlProposalsSpot: {}, tlProposalsResult: null, tlProposalsErr: '' });
  proposalsLoad();
}

export function proposalsPage(delta) {
  const t = T();
  const total = t.tlProposalsTotal || 0;
  const now = t.tlProposalsOffset || 0;
  const lastPage = Math.max(0, Math.floor((total - 1) / QUEUE_PAGE)) * QUEUE_PAGE;
  const offset = Math.max(0, Math.min(now + delta * QUEUE_PAGE, lastPage));
  if (offset === now || t.tlProposalsCheck) return;
  setT({ tlProposalsOffset: offset });
  proposalsLoad();
}

export function proposalsToggle(id) {
  const t = T();
  if (t.tlProposalsCheck) return;
  const row = (t.tlProposals || []).find((p) => p.id === id);
  if (!checkable(row)) return;
  setT((p) => {
    const sel = p.tlProposalsSelected || [];
    return { tlProposalsSelected: sel.indexOf(id) > -1 ? sel.filter((x) => x !== id) : sel.concat([id]) };
  });
}

// "Select all": the suggestions on this page, and only when clicked.
export function proposalsSelectPage() {
  const t = T();
  if (t.tlProposalsCheck) return;
  const ids = (t.tlProposals || []).filter(checkable).map((p) => p.id);
  setT((p) => {
    const sel = p.tlProposalsSelected || [];
    return { tlProposalsSelected: sel.concat(ids.filter((id) => sel.indexOf(id) < 0)) };
  });
}

export function proposalsClearSelection() { if (!T().tlProposalsCheck) setT({ tlProposalsSelected: [] }); }

export function proposalsEditDraft(id, patch) {
  setT((p) => ({ tlProposalsDrafts: Object.assign({}, p.tlProposalsDrafts, { [id]: Object.assign({}, (p.tlProposalsDrafts || {})[id], patch) }) }));
}

export function proposalsClearResult() { setT({ tlProposalsResult: null }); }

// One suggestion decided by a person: accept or dismiss; for a cell proposal, the other value (override).
export async function proposalsDecide(id, decision, present) {
  const t = T();
  const p = (t.tlProposals || []).find((x) => x.id === id);
  if (!p) return false;
  const body = { decision, present: present === undefined ? null : present, base: p.base || null };
  if (p.kind === 'group' && p.suggestion === 'new_mode' && decision === 'accept') {
    body.edits = draftEdits(p, (t.tlProposalsDrafts || {})[id]);
    const problems = draftProblems(body.edits, true);
    if (problems.length) { setT({ tlProposalsErr: problems[0] }); return false; }
  }
  const r = await write(explained(() => taxonomyApi.decideProposal(id, body)), { key: 'proposal:' + id, again: () => proposalsDecide(id, decision, present) });
  if (!r.ok) return false;
  if (r.out && r.out.refusal) { setT({ tlProposalsErr: r.out.message }); return false; }
  setT((prev) => ({ tlProposals: (prev.tlProposals || []).filter((x) => x.id !== id),
    tlProposalsSelected: (prev.tlProposalsSelected || []).filter((x) => x !== id), tlProposalsErr: '' }));
  proposalsLoad();
  if (p.kind === 'group') { modesLoad(); unfiledLoad(); }
  taxAfterLabels();
  return true;
}

// Step one of a bulk decision: the server's audit of exactly the ticked suggestions. Nothing is written here.
export async function proposalsBulkCheck(action) {
  const t = T();
  const rows = t.tlProposals || [];
  const selected = t.tlProposalsSelected || [];
  const ids = rows.filter((p) => checkable(p) && selected.indexOf(p.id) > -1).map((p) => p.id);
  if (!ids.length) { setT({ tlProposalsErr: 'Tick the suggestions to decide first.' }); return false; }
  if (action === 'dismiss' && rows.some((p) => ids.indexOf(p.id) > -1 && p.kind !== 'depth_scan')) {
    setT({ tlProposalsErr: 'Dismiss in bulk works on depth-scan suggestions only.' });
    return false;
  }
  const servedAt = t.tlProposalsServedAt;
  const same = t.tlProposalsCheckServedAt === servedAt && (t.tlProposalsCheckIds || []).join(',') === ids.join(',');
  const r = await write(explained(() => taxonomyApi.bulkCheck({ action, proposal_ids: ids, served_at: servedAt })), { key: 'bulk-check', again: () => proposalsBulkCheck(action) });
  if (!r.ok) return false;
  if (r.out && r.out.refusal) { setT({ tlProposalsErr: r.out.message }); return false; }
  setT({ tlProposalsCheck: r.out || null, tlProposalsCheckAction: action, tlProposalsCheckIds: ids, tlProposalsCheckServedAt: servedAt,
    tlProposalsSpot: same ? t.tlProposalsSpot || {} : {}, tlProposalsResult: null, tlProposalsErr: '' });
  return true;
}

export function proposalsRecheck() {
  const action = T().tlProposalsCheckAction;
  return action ? proposalsBulkCheck(action) : Promise.resolve(false);
}

// A spot-check answer, decided with the AI's suggestion hidden. The label is the person's own (spec §6.2).
export async function proposalsSpotDecide(proposalId, present) {
  const chk = T().tlProposalsCheck;
  const item = chk && chk.spot_check ? (chk.spot_check.items || []).find((i) => i.proposal_id === proposalId) : null;
  if (!item) return false;
  const r = await write(explained(() => taxonomyApi.setLabel(item.turn_id, item.mode_id,
    { present: !!present, base: item.base || null, spot_check_seed: chk.spot_check.seed })), { key: 'spot:' + proposalId, again: () => proposalsSpotDecide(proposalId, present) });
  if (!r.ok) return false;
  if (r.out && r.out.refusal) { setT({ tlProposalsErr: r.out.message }); return false; }
  setT((p) => ({ tlProposalsSpot: Object.assign({}, p.tlProposalsSpot, { [proposalId]: !!present }) }));
  return true;
}

// Step two: approve exactly what was checked, with the same list time (the spot-check's seed depends on it).
export async function proposalsBulkApprove() {
  const t = T();
  const chk = t.tlProposalsCheck;
  if (!chk) return false;
  const spot = (chk.spot_check && chk.spot_check.items) || [];
  if (spot.some((i) => !has(t.tlProposalsSpot, i.proposal_id))) {
    setT({ tlProposalsErr: 'Decide every spot-check answer before approving the rest.' });
    return false;
  }
  const ids = t.tlProposalsCheckIds || [];
  const bases = {};
  ids.forEach((id) => { const p = (t.tlProposals || []).find((x) => x.id === id); bases[id] = p ? p.base || null : null; });
  const r = await write(explained(() => taxonomyApi.bulkApprove({ action: t.tlProposalsCheckAction, proposal_ids: ids,
    served_at: t.tlProposalsCheckServedAt, bases })), { key: 'bulk-approve', again: proposalsBulkApprove });
  if (!r.ok) return false;
  const out = r.out || {};
  if (out.refusal) {
    const d = out.detail || {};
    setT({ tlProposalsErr: out.message, tlProposalsCheck: Object.assign({}, chk, { allowed: false,
      refused: d.refused || chk.refused || [], flagged: d.flagged || chk.flagged || [], warnings: d.warnings || chk.warnings || [] }) });
    return false;
  }
  setT({ tlProposalsResult: out, tlProposalsCheck: null, tlProposalsCheckIds: [], tlProposalsCheckServedAt: null,
    tlProposalsSpot: {}, tlProposalsErr: '' });
  proposalsLoad();
  taxAfterLabels();
  return true;
}

export function proposalsBulkCancel() {
  setT({ tlProposalsCheck: null, tlProposalsCheckIds: [], tlProposalsCheckServedAt: null, tlProposalsSpot: {} });
}

// ── proposal runs (the platform team's) ─────────────────────────────────────────────────────────────────────
export async function proposalsRunsLoad() {
  if (!owns(get())) return;
  const seq = get().seq;
  const before = (T().tlProposalsRuns || []).filter((r) => r.job && ACTIVE.indexOf(r.job.status) > -1).map((r) => r.id);
  setT({ tlProposalsRunsLoading: true, tlProposalsRunsErr: '' });
  try {
    const out = await taxonomyApi.proposalRuns();
    if (!live(seq)) return;
    const runs = (out && out.runs) || [];
    setT({ tlProposalsRuns: runs, tlProposalsRunsLoading: false, tlProposalsRunsLoadedAt: Date.now() });
    const settled = before.some((id) => !runs.some((r) => r.id === id && r.job && ACTIVE.indexOf(r.job.status) > -1));
    if (settled) { proposalsLoad(); unfiledLoad(); }
    proposalsRunsWatch(runs);
  } catch (e) {
    if (isStaleScope(e) || !live(seq)) return;
    setT({ tlProposalsRunsLoading: false, tlProposalsRunsErr: errText(e) });
  }
}

// While a run waits or runs and Progress is on screen, the runs are read again every 5 seconds.
export function proposalsRunsWatch(runs) {
  stopPolls();
  if (typeof setTimeout !== 'function' || !(runs || []).some((r) => r.job && ACTIVE.indexOf(r.job.status) > -1)) return;
  const seq = get().seq;
  runsTimer = setTimeout(() => {
    runsTimer = null;
    const shown = typeof document === 'undefined' || !document.visibilityState || document.visibilityState === 'visible';
    if (live(seq) && get().tab === 'progress' && shown) proposalsRunsLoad();
  }, RUNS_POLL_MS);
  if (runsTimer && runsTimer.unref) runsTimer.unref();
}

export function proposalsSetRun(patch) {
  setT((p) => ({ tlProposalsRunForm: Object.assign(runForm(p), patch), tlProposalsEstimateErr: '' }));
}

export function proposalsSetResumeCap(runId, value) {
  setT((p) => ({ tlProposalsResumeCap: Object.assign({}, p.tlProposalsResumeCap, { [runId]: value }) }));
}

export async function proposalsEstimateRun() {
  const s = get();
  if (!owns(s)) return false;
  const f = runForm(taxOf(s));
  if (f.kind === 'depth_scan' && !f.mode_id) { setT({ tlProposalsEstimateErr: 'Choose the failure mode to look for.' }); return false; }
  const now = Date.now();
  const req = { kind: f.kind, mode_id: f.kind === 'depth_scan' ? f.mode_id : undefined,
    since: new Date(now - (Number(f.days) || 30) * 86400000).toISOString(), until: new Date(now + 3600000).toISOString(),
    limit: Number(f.limit) || 200, include_candidates: f.kind === 'cells' ? !!f.include_candidates : undefined };
  const seq = s.seq;
  setT({ tlProposalsEstimating: true, tlProposalsEstimateErr: '', tlProposalsEstimate: null });
  try {
    const out = await taxonomyApi.proposalEstimate(req);
    if (!live(seq)) return false;
    setT({ tlProposalsEstimating: false, tlProposalsEstimate: Object.assign({}, out, { request: req, form: formKey(f) }) });
    return true;
  } catch (e) {
    if (isStaleScope(e) || !live(seq)) return false;
    setT({ tlProposalsEstimating: false, tlProposalsEstimateErr: errText(e) });
    return false;
  }
}

// Starts the run that was estimated: the same request (window, mode, limit) and the cap typed in.
export async function proposalsStartRun() {
  const s = get();
  if (!owns(s)) return false;
  const t = taxOf(s);
  const f = runForm(t);
  const est = t.tlProposalsEstimate;
  const problem = startProblem(f, est);
  if (problem) { setT({ tlProposalsEstimateErr: problem }); return false; }
  const body = Object.assign({}, est.request, { usd_cap: Number(f.usd_cap) });
  const r = await write(explained(() => taxonomyApi.startProposalRun(body)), { key: 'run-start', retry: false });
  if (!r.ok) return false;
  if (r.out && r.out.refusal) { setT({ tlProposalsEstimateErr: r.out.message }); return false; }
  setT({ tlProposalsEstimate: null, tlProposalsEstimateErr: '' });
  proposalsRunsLoad();
  return true;
}

export async function proposalsPauseRun(runId) {
  if (!owns(get())) return false;
  const r = await write(explained(() => taxonomyApi.pauseProposalRun(runId)), { key: 'run:' + runId, again: () => proposalsPauseRun(runId) });
  if (!r.ok) return false;
  if (r.out && r.out.refusal) { setT({ tlProposalsRunsErr: r.out.message }); return false; }
  proposalsRunsLoad();
  return true;
}

export async function proposalsResumeRun(runId) {
  if (!owns(get())) return false;
  const cap = Number((T().tlProposalsResumeCap || {})[runId]);
  const r = await write(explained(() => taxonomyApi.resumeProposalRun(runId, cap > 0 ? { usd_cap: cap } : {})), { key: 'run:' + runId, retry: false });
  if (!r.ok) return false;
  if (r.out && r.out.refusal) { setT({ tlProposalsRunsErr: r.out.message }); return false; }
  proposalsRunsLoad();
  return true;
}

// ── AI suggestions on the open answer in Conversations ──────────────────────────────────────────────────────
// Reads the open answer's suggestions and failure-mode cells once per answer, and again once the reviewer has judged
// it (they were hidden until then). The platform team has no company to review in and reads nothing here.
export function sugEnsure(turnId, judged) {
  const t = T();
  if (!turnId || !hasCompany(get())) return;
  modesEnsure();
  if (t.tlSugTurnId === turnId && (t.tlSugLoading || t.tlSugLoadedAt) && !(judged && t.tlSugHidden && !t.tlSugLoading)) return;
  sugLoad(turnId);
}

export async function sugLoad(turnId) {
  if (!turnId) return;
  const seq = get().seq;
  const ask = (asks.sug += 1);
  setT((t) => (t.tlSugTurnId === turnId ? { tlSugLoading: true, tlSugErr: '' }
    : { tlSugTurnId: turnId, tlSug: null, tlSugCells: null, tlSugHidden: false, tlSugLoadedAt: null, tlSugLoading: true, tlSugErr: '' }));
  try {
    const both = await Promise.all([taxonomyApi.proposals({ turn_id: turnId }), taxonomyApi.turnLabels(turnId)]);
    if (!live(seq) || ask !== asks.sug || T().tlSugTurnId !== turnId) return;
    const props = both[0] || {};
    const cells = both[1] || {};
    setT({ tlSug: props.proposals || [], tlSugCells: cells.cells || [], tlSugHidden: !!(cells.hidden || props.hidden),
      tlSugLoading: false, tlSugLoadedAt: Date.now() });
  } catch (e) {
    if (isStaleScope(e) || !live(seq) || ask !== asks.sug) return;
    setT({ tlSugLoading: false, tlSugErr: errText(e) });
  }
}

export async function sugDecide(id, decision, present) {
  const p = (T().tlSug || []).find((x) => x.id === id);
  if (!p) return false;
  const r = await write(explained(() => taxonomyApi.decideProposal(id, { decision, present: present === undefined ? null : present, base: p.base || null })),
    { key: 'suggest:' + id, again: () => sugDecide(id, decision, present) });
  if (!r.ok) return false;
  if (r.out && r.out.refusal) { setT({ tlSugErr: r.out.message }); return false; }
  sugLoad(T().tlSugTurnId);
  taxAfterLabels();
  return true;
}

// The annotation layer's hook: the dashed highlights for one block of the open answer.
export function sugMarks(turnId, key, text) {
  return turnId && turnId === T().tlSugTurnId ? suggestionMarks(T().tlSug, key, text) : [];
}
