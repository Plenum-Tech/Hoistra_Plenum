// taxonomy — pure helpers and view models for Trace Lab's failure modes: the mode drawer and its dialogs, reviewers'
// unfiled notes, the requirement register, the failure-mode map and the Progress tab's failure-mode rows. No
// requests and no state of its own: the screens draw what taxonomyModesVals(s, a) returns, where `s` is the store
// state and `a` the actions (taxonomyActions.js) its buttons call, and the actions use the helpers here.
//
// The server decides who may do what (D7); this module only hides what a role cannot use. Changing failure modes,
// setting notes aside, deciding grouping drafts, running proposals, editing requirements and accepting or rejecting
// a company's proposed failure mode belong to the platform team (logic/taxCommon.js owns()). Labels, filing notes and
// deciding suggestions belong to a company's reviewers (their own answers) and to the platform team (any answer). A
// company's reviewers also PROPOSE failure modes from their own unfiled notes (taxCommon.js proposes()) and read how
// each proposal was decided. Agent and level choices are the server's (the failure mode list's `agents` and
// `levels`), so this module names no agent.
import { PLAN_STEP_LABELS, samplingOf } from './sampling.js';
import {
  DATA_FAULT_TEXT, OWNER_ONLY_HINT, SAME_FIX_TEXT, STATUS_TEXT, STATUS_TONE, acting, clip, hasCompany, metaOf, owns, pct, proposes, taxOf, val, when
} from './taxCommon.js';

export const NAME_RE = /^[a-z][a-z0-9_]{2,63}$/;
export const CONTRACT_ITEMS = ['boundary', 'evaluator_type', 'requirement', 'positives', 'close_negatives'];
export const CONTRACT_TEXT = {
  boundary: 'A boundary: how it differs from the nearest failure mode',
  evaluator_type: 'How it will be checked: a code check or a judge',
  requirement: 'Cites an active requirement',
  positives: 'Three answers where it is present, each confirmed one at a time',
  close_negatives: 'Three close answers where it is absent, or a reason there are not three'
};
export const NOTICE = { create: 'Failure mode created as a candidate.', edit: 'Saved.', rename: 'Renamed.', merge: 'Merged.',
  split: 'Split into new candidates.', retire: 'Retired.', freeze: 'Frozen.', file: 'Note filed.', dispose: 'Note set aside.',
  propose: 'Proposed. The platform team will accept or reject it.', reject: 'Rejected. The company sees your reason.' };
export const ACCEPT_NOTICE = 'Accepted. It is now a candidate failure mode and its notes are filed under it.';
export const STALE_MODE = 'This failure mode changed since you opened it. Close this and open it again to see the current version.';
export const STALE_PROPOSAL = 'This proposal changed since you loaded it. Refresh the list and look again.';
export const REASON_MAX = 500;
const PROPOSE_NOTE = 'The platform team will read it and accept or reject it. If it accepts, these notes are filed under it. How it is checked and which requirements it cites are the platform team’s to set.';
const REJECT_NOTE = 'The company reads your reason. The proposal stays on record as rejected, and its name is free to use again.';
export const UNFILED_PAGE = 50;
const EVALUATOR_TEXT = { code: 'A code check', judge: 'A judge: an AI reader with a fixed prompt' };
const REQ_STATUS_TEXT = { draft: 'Draft', active: 'Active', superseded: 'Replaced by a newer revision', withdrawn: 'Withdrawn' };
const QUOTE_STATUS_TEXT = { ok: 'The quote still matches its document', drift: 'The document no longer contains this quote',
  unverifiable_in_image: 'Cannot be checked here' };
const DISPOSE_TEXT = { no_mode_fits: 'No failure mode fits: too vague to group', not_a_failure: 'Not a failure: the answer was right after all' };
const NAME_PROBLEM = 'Name: lower-case words joined by underscores, 3 to 64 characters, starting with a letter.';
const MAP_W = 640;
const MAP_H = 220;
const CURVE_W = 560;
const CURVE_H = 160;
const CURVE_PAD = 28;

export const modeById = (t, id) => (t.tlModes || []).find((m) => m.id === id) || null;
export const blankDraft = (d) => Object.assign({ name: '', definition: '', boundary: '', nearest_mode_id: '', level: '', agent: '',
  evaluator_type: '', requirement_ids: [] }, d || {});

// What a draft failure mode still lacks, in words. The server checks the same rules again.
export function draftProblems(d, withName) {
  const out = [];
  if (withName && !NAME_RE.test(String(d.name || ''))) out.push(NAME_PROBLEM);
  const def = String(d.definition || '').trim();
  if (def.length < 10 || def.length > 1000) out.push('Definition: one sentence of 10 to 1,000 characters that starts "Present when".');
  if (String(d.boundary || '').length > 1000) out.push('Boundary: at most 1,000 characters.');
  if (!d.level) out.push('Choose who decides it (the level).');
  if (!d.agent) out.push('Choose where the failure lives.');
  return out;
}

// The notes the open mode came from (the drawer's mode). `t` is the taxonomy slice.
export function splitOrigin(t) {
  const m = t.tlModesDrawer && t.tlModesDrawer.mode;
  return m && Array.isArray(m.created_from) ? m.created_from : [];
}

export function dialogProblems(d, t) {
  if (!d) return [];
  if (d.kind === 'create' || d.kind === 'propose') return draftProblems(d, true).concat((d.createdFrom || []).length ? [] : ['Start a failure mode from at least one reviewer note.']);
  if (d.kind === 'edit') return draftProblems(d, false);
  if (d.kind === 'rename') return NAME_RE.test(String(d.name || '')) ? [] : [NAME_PROBLEM];
  if (d.kind === 'merge') {
    const out = [];
    if (d.target === 'existing') {
      if (!d.sources.length) out.push('Choose at least one failure mode to merge.');
      if (!d.into) out.push('Choose the failure mode to merge into.');
      if (d.into && d.sources.indexOf(d.into) > -1) out.push('A failure mode cannot merge into itself.');
    } else {
      if (d.sources.length < 2) out.push('Merging into a new failure mode takes at least two.');
      draftProblems(d.draft, true).forEach((p) => out.push(p));
    }
    return out;
  }
  if (d.kind === 'split') {
    const out = [];
    if (d.children.length < 2 || d.children.length > 5) out.push('Split into 2 to 5 failure modes.');
    const names = d.children.map((c) => c.name);
    if (new Set(names).size !== names.length) out.push('Each new failure mode needs its own name.');
    const origin = splitOrigin(t);
    d.children.forEach((c, i) => {
      draftProblems(c, true).forEach((p) => out.push('Mode ' + (i + 1) + ': ' + p));
      if (!origin.some((id) => d.noteChild[id] === i)) out.push('Mode ' + (i + 1) + ': give it at least one of the notes the mode came from.');
    });
    return out;
  }
  if (d.kind === 'retire') return String(d.reason || '').trim() ? [] : ['Say why it is retired.'];
  if (d.kind === 'reject') {
    const why = String(d.reason || '').trim();
    return !why ? ['Say why it is rejected.'] : why.length > REASON_MAX ? ['The reason is at most ' + REASON_MAX + ' characters.'] : [];
  }
  if (d.kind === 'file') return d.modeId ? [] : ['Choose a failure mode.'];
  if (d.kind === 'dispose') return String(d.note || '').trim() ? [] : ['Say why.'];
  return [];
}

export function latestReq(rows, code) {
  const revs = (rows || []).filter((r) => r.code === code).sort((a, b) => b.revision - a.revision);
  return revs.find((r) => r.status === 'active') || revs[0] || null;
}

export function reqProblems(f) {
  if (f.kind === 'withdraw') return String(f.note || '').trim().length >= 3 ? [] : ['Say why it is withdrawn (at least 3 characters).'];
  const out = [];
  if (f.kind === 'create' && !f.area) out.push('Choose an area.');
  if (f.kind === 'create' && !f.level) out.push('Choose a level.');
  const st = String(f.statement || '').trim();
  if (st.length < 10 || st.length > 600) out.push('Statement: 10 to 600 characters.');
  if (f.kind === 'revise' && String(f.note || '').trim().length < 3) out.push('Say what changed (at least 3 characters).');
  (f.impls || []).forEach((i, n) => {
    if (!String(i.doc || '').trim()) out.push('Source ' + (n + 1) + ': name the document.');
    if (String(i.quote || '').trim().length < 8) out.push('Source ' + (n + 1) + ': quote at least 8 characters from it.');
    if (i.line && !(Number(i.line) >= 1)) out.push('Source ' + (n + 1) + ': the line is a number from 1.');
  });
  return out;
}

// A squarified treemap (Bruls et al.): items [{id, value > 0}] laid out in a width × height box, largest first.
export function treemap(items, width, height) {
  const list = (items || []).filter((i) => Number(i.value) > 0)
    .sort((a, b) => (b.value - a.value) || String(a.id).localeCompare(String(b.id)));
  if (!list.length || !(width > 0) || !(height > 0)) return [];
  const total = list.reduce((n, i) => n + Number(i.value), 0);
  const scale = (width * height) / total;
  const r1 = (v) => Math.round(v * 10) / 10;
  const out = [];
  let x = 0; let y = 0; let w = width; let h = height;
  const worst = (row, side) => {
    const sum = row.reduce((n, b) => n + b.area, 0);
    const max = Math.max(...row.map((b) => b.area));
    const min = Math.min(...row.map((b) => b.area));
    return Math.max((side * side * max) / (sum * sum), (sum * sum) / (side * side * min));
  };
  const lay = (row) => {
    const sum = row.reduce((n, b) => n + b.area, 0);
    if (w >= h) {
      const cw = sum / h; let cy = y;
      row.forEach((b) => { const bh = b.area / cw; out.push({ id: b.id, x: r1(x), y: r1(cy), w: r1(cw), h: r1(bh) }); cy += bh; });
      x += cw; w -= cw;
    } else {
      const rh = sum / w; let cx = x;
      row.forEach((b) => { const bw = b.area / rh; out.push({ id: b.id, x: r1(cx), y: r1(y), w: r1(bw), h: r1(rh) }); cx += bw; });
      y += rh; h -= rh;
    }
  };
  let row = [];
  list.forEach((i) => {
    const b = { id: i.id, area: Number(i.value) * scale };
    const side = Math.min(w, h);
    if (!row.length || worst(row.concat([b]), side) <= worst(row, side)) { row.push(b); return; }
    lay(row);
    row = [b];
  });
  if (row.length) lay(row);
  return out;
}

// The failure-mode discovery curve: new failure modes named in each block of reviews.
export function modeCurve(points, opts) {
  const o = Object.assign({ w: CURVE_W, h: CURVE_H, pad: CURVE_PAD }, opts || {});
  const list = points || [];
  if (!list.length) return { path: '', dots: [], max: 0, width: o.w, height: o.h, empty: true };
  const max = Math.max(1, ...list.map((p) => Number(p.new_modes) || 0));
  const step = list.length > 1 ? (o.w - 2 * o.pad) / (list.length - 1) : 0;
  const dots = list.map((p, i) => {
    const k = Number(p.new_modes) || 0;
    return { x: Math.round((o.pad + i * step) * 10) / 10, y: Math.round((o.h - o.pad - (k / max) * (o.h - 2 * o.pad)) * 10) / 10,
      label: 'Reviews up to ' + p.reviews_through + ': ' + k + (k === 1 ? ' new failure mode' : ' new failure modes') + ' (' + (Number(p.cumulative_modes) || 0) + ' in all)' };
  });
  return { path: dots.map((d, i) => (i ? 'L' : 'M') + d.x + ' ' + d.y).join(' '), dots, max, width: o.w, height: o.h, empty: false };
}

// ── view models ──────────────────────────────────────────────────────────────────────────────────────────────

function eventText(e) {
  const d = e.detail || {};
  if (e.action === 'create') return 'Created';
  if (e.action === 'rename') return Array.isArray(d.name) ? 'Renamed from ' + d.name[0] + ' to ' + d.name[1] : 'Renamed';
  if (e.action === 'retire') return 'Retired' + (d.reason ? ': ' + d.reason : '');
  if (e.action === 'restore') return 'Restored';
  if (e.action === 'merge') return d.role === 'source' ? 'Merged into another failure mode' : 'Took in merged failure modes';
  if (e.action === 'split') return d.role === 'parent' ? 'Split into new failure modes' : 'Split from a broader failure mode';
  if (Array.isArray(d.status)) return 'Status: ' + (STATUS_TEXT[d.status[0]] || d.status[0]) + ' → ' + (STATUS_TEXT[d.status[1]] || d.status[1]);
  if (d.rescope) return 'Meaning changed: earlier judgments no longer count';
  return 'Edited';
}

function listItem(a, m, p, field, owner, company) {
  const live = !!m && m.status !== 'retired';
  const state = p.ok ? '✓ Counts' : p.state === null || p.state === undefined ? 'Not judged yet'
    : p.approval === 'bulk' ? 'Approved in bulk: does not count' : 'Now ' + p.state + ': does not count';
  return { turnId: p.turn_id, question: clip(p.question || 'Answer', 120), ok: !!p.ok, state,
    remove: owner && live ? () => a.modeList(m.id, field, p.turn_id, false) : null,
    open: company ? () => a.taxOpenAnswer(p.turn_id) : null };
}

function drawerVals(a, s, t) {
  if (!t.tlModesDrawerId) return null;
  const body = t.tlModesDrawer;
  const m = body && body.mode;
  const owner = owns(s);
  const company = hasCompany(s);
  const names = {};
  (t.tlModes || []).forEach((x) => { names[x.id] = x.name; });
  const missing = body && body.contract ? body.contract.missing || [] : null;
  const w = t.tlWrites['mode:' + t.tlModesDrawerId] || {};
  const live = !!m && m.status !== 'retired';
  return {
    loading: !!t.tlModesDrawerLoading && !body, error: t.tlModesDrawerErr || w.error || '', busy: !!w.busy,
    retry: w.canRetry ? () => a.retryWrite('mode:' + t.tlModesDrawerId) : null,
    ownerHint: acting(s) ? OWNER_ONLY_HINT : '', notice: t.tlModesNotice || '',
    close: () => a.modeClose(),
    mode: m ? {
      id: m.id, name: m.name, status: m.status, statusText: STATUS_TEXT[m.status] || m.status, tone: STATUS_TONE[m.status] || 'dormant',
      level: m.level_label || '—', agent: m.agent_label || '—', definition: m.definition || '—', boundary: m.boundary || 'None written yet.',
      nearest: m.nearest_mode_id ? names[m.nearest_mode_id] || 'Another failure mode' : '—',
      evaluator: EVALUATOR_TEXT[m.evaluator_type] || 'Not chosen yet', requirements: (m.requirement_ids || []).join(', ') || 'None yet',
      flags: (m.requirement_flags || []).map((f) => f.text), dataFault: m.level === 'data' ? DATA_FAULT_TEXT : '',
      counts: m.positive_count !== undefined ? 'This company has ' + (m.positive_turn_ids || []).length + ' of the ' + m.positive_count + ' listed answers where it is present.' : ''
    } : null,
    contract: missing ? {
      items: CONTRACT_ITEMS.map((k) => ({ key: k, text: CONTRACT_TEXT[k], ok: missing.indexOf(k) < 0 })),
      missingTexts: missing.map((k) => CONTRACT_TEXT[k] || k), ready: !missing.length
    } : null,
    positives: (body && body.positives ? body.positives : []).map((p) => listItem(a, m, p, 'positive_turn_ids', owner, company)),
    closeNegatives: (body && body.close_negatives ? body.close_negatives : []).map((p) => listItem(a, m, p, 'close_negative_turn_ids', owner, company)),
    candidates: (body && body.close_negative_candidates ? body.close_negative_candidates : []).map((p) => ({
      turnId: p.turn_id, question: clip(p.question || 'Answer', 120),
      add: owner && live ? () => a.modeList(m.id, 'close_negative_turn_ids', p.turn_id, true) : null,
      open: company ? () => a.taxOpenAnswer(p.turn_id) : null })),
    notes: (body && body.notes ? body.notes : []).map((n) => ({
      id: n.annotation_id, note: n.note || '', quote: clip(n.quote, 120), step: n.step_label || 'Answer', who: n.reviewer_email || 'A reviewer', when: when(n.created_at),
      usePositive: owner && live && (m.positive_turn_ids || []).indexOf(n.turn_id) < 0 ? () => a.modeList(m.id, 'positive_turn_ids', n.turn_id, true) : null,
      open: company ? () => a.taxOpenAnswer(n.turn_id) : null })),
    events: (body && body.events ? body.events : []).map((e) => ({ key: String(e.seq), text: eventText(e), who: e.actor_email || '', when: when(e.at) })),
    actions: owner && m ? {
      edit: live ? () => a.modeDialog('edit', { modeId: m.id }) : null,
      rename: live ? () => a.modeDialog('rename', { modeId: m.id }) : null,
      merge: live && m.status !== 'frozen' ? () => a.modeDialog('merge', { modeId: m.id }) : null,
      split: live && m.status !== 'frozen' ? () => a.modeDialog('split', { modeId: m.id }) : null,
      retire: live ? () => a.modeDialog('retire', { modeId: m.id }) : null,
      restore: m.status === 'retired' && !m.merged_into ? () => a.modeRestore(m.id) : null,
      confirm: m.status === 'candidate' ? { enabled: !!missing && !missing.length, run: () => a.modeConfirm(m.id) } : null,
      toCandidate: m.status === 'confirmed' ? () => a.modeToCandidate(m.id) : null,
      freeze: m.status === 'confirmed' ? () => a.modeDialog('freeze', { modeId: m.id }) : null
    } : null
  };
}

function dialogTitle(d) {
  return { create: 'New failure mode', propose: 'Propose a failure mode', reject: 'Reject ' + d.name, edit: 'Edit ' + d.name, rename: 'Rename ' + d.oldName,
    merge: 'Merge failure modes', split: 'Split ' + d.name, retire: 'Retire ' + d.name, freeze: 'Freeze ' + d.name,
    file: 'File this note under a failure mode', dispose: 'Set this note aside' }[d.kind] || '';
}

// The reviewer notes a new failure mode (or a proposal) starts from, as the dialog lists them.
function fromNotesOf(d, t) {
  const notes = (t.tlUnfiled || []).filter((n) => (d.createdFrom || []).indexOf(n.annotation_id) > -1);
  return { count: (d.createdFrom || []).length, notes: notes.map((n) => ({ id: n.annotation_id, note: n.note || '', who: n.reviewer_email || 'A reviewer' })) };
}

function dialogVals(a, s, t) {
  const d = t.tlModesDialog;
  if (!d) return null;
  const meta = t.tlModesMeta || metaOf(null);
  const live = (t.tlModes || []).filter((m) => m.status !== 'retired');
  const w = t.tlWrites['mode-dialog'] || {};
  const reqs = (t.tlReqs || []).filter((r) => r.status === 'active');
  const set = (field) => (e) => a.modeDialogSet({ [field]: val(e) });
  const problems = dialogProblems(d, t);
  const out = {
    kind: d.kind, title: dialogTitle(d), busy: !!w.busy, error: t.tlModesDialogErr || w.error || '',
    retry: w.canRetry ? () => a.retryWrite('mode-dialog') : null,
    missing: (t.tlModesDialogMissing || []).map((k) => CONTRACT_TEXT[k] || k),
    guidance: SAME_FIX_TEXT, showGuidance: ['create', 'propose', 'merge', 'split', 'file'].indexOf(d.kind) > -1,
    fixTest: meta.fixTest, fixTestText: meta.fixTestText, showFixTest: ['create', 'propose', 'edit', 'merge', 'split'].indexOf(d.kind) > -1,
    agents: meta.agents.map((x) => ({ value: x.key, label: x.label })),
    levels: meta.levels.map((l) => ({ value: l.key, label: l.label })),
    evaluators: [{ value: '', label: 'Not chosen yet' }, { value: 'code', label: EVALUATOR_TEXT.code }, { value: 'judge', label: EVALUATOR_TEXT.judge }],
    problems, canSubmit: !problems.length && !w.busy,
    submitLabel: { create: 'Create as a candidate', propose: 'Send the proposal', reject: 'Reject it', edit: 'Save', rename: 'Rename', merge: 'Merge',
      split: 'Split', retire: 'Retire', freeze: 'Freeze for good', file: 'File it', dispose: 'Set it aside' }[d.kind] || 'Save',
    submit: () => a.modeDialogSubmit(), close: () => a.modeDialogClose(), set
  };
  if (d.kind === 'propose') {
    // A proposal carries the draft only: how it is checked and the requirements it cites are the platform team's.
    Object.assign(out, {
      nameEditable: true, frozen: false, frozenText: '',
      draft: { name: d.name, definition: d.definition, boundary: d.boundary, nearest: d.nearest_mode_id, level: d.level, agent: d.agent },
      nearestOptions: [{ value: '', label: 'None' }].concat(live.map((m) => ({ value: m.id, label: m.name }))),
      fromNotes: fromNotesOf(d, t), note: PROPOSE_NOTE
    });
  } else if (d.kind === 'reject') {
    Object.assign(out, { reason: d.reason, note: REJECT_NOTE });
  } else if (d.kind === 'create' || d.kind === 'edit') {
    Object.assign(out, {
      nameEditable: d.kind === 'create', frozen: d.status === 'frozen',
      frozenText: d.status === 'frozen' ? 'Frozen: its meaning cannot change. A new meaning needs a new failure mode.' : '',
      draft: { name: d.name, definition: d.definition, boundary: d.boundary, nearest: d.nearest_mode_id, level: d.level, agent: d.agent, evaluator: d.evaluator_type },
      nearestOptions: [{ value: '', label: 'None' }].concat(live.filter((m) => m.id !== d.modeId).map((m) => ({ value: m.id, label: m.name }))),
      requirements: reqs.map((r) => ({ value: r.code, label: r.code + ' — ' + clip(r.statement, 90), on: (d.requirement_ids || []).indexOf(r.code) > -1,
        toggle: () => a.modeDialogToggle('requirement_ids', r.code) })),
      fromNotes: d.kind === 'create' ? fromNotesOf(d, t) : null,
      waiver: d.kind === 'edit' ? d.close_negatives_waiver : '', rescope: !!d.rescope, toggleRescope: () => a.modeDialogSet({ rescope: !d.rescope }),
      rescopeText: 'The meaning changed: earlier judgments on this failure mode stop counting, and a confirmed mode goes back to candidate.'
    });
  } else if (d.kind === 'rename') {
    Object.assign(out, { name: d.name, note: 'Only the name changes. Its meaning, judgments and suggestions stay as they are.' });
  } else if (d.kind === 'merge') {
    const pool = live.filter((m) => m.status !== 'frozen');
    Object.assign(out, {
      sources: pool.map((m) => ({ id: m.id, name: m.name, on: d.sources.indexOf(m.id) > -1, toggle: () => a.modeDialogToggle('sources', m.id) })),
      target: d.target, setTarget: (x) => a.modeDialogSet({ target: x === 'new' ? 'new' : 'existing' }),
      into: d.into, intoOptions: [{ value: '', label: 'Choose…' }].concat(pool.filter((m) => d.sources.indexOf(m.id) < 0).map((m) => ({ value: m.id, label: m.name }))),
      newDraft: Object.assign({}, d.draft, { set: (field) => (e) => a.modeDialogDraft({ [field]: val(e) }) }),
      note: 'Judgments are not rewritten: each merged mode keeps its labels, and they count toward the target from now on.'
    });
  } else if (d.kind === 'split') {
    const origin = splitOrigin(t);
    const notes = (t.tlModesDrawer && t.tlModesDrawer.notes) || [];
    Object.assign(out, {
      children: d.children.map((ch, i) => Object.assign({ index: i, label: 'New failure mode ' + (i + 1) }, ch, {
        set: (field) => (e) => a.modeDialogChild(i, { [field]: val(e) }),
        remove: d.children.length > 2 ? () => a.modeDialogRemoveChild(i) : null })),
      addChild: d.children.length < 5 ? () => a.modeDialogAddChild() : null,
      childOptions: [{ value: '', label: 'None of them' }].concat(d.children.map((ch, i) => ({ value: String(i), label: ch.name || 'New failure mode ' + (i + 1) }))),
      notes: notes.map((n) => ({ id: n.annotation_id, note: n.note || '', who: n.reviewer_email || 'A reviewer', origin: origin.indexOf(n.annotation_id) > -1,
        child: typeof d.noteChild[n.annotation_id] === 'number' ? String(d.noteChild[n.annotation_id]) : '',
        setChild: (e) => a.modeDialogNote(n.annotation_id, val(e)) })),
      note: 'Each note goes to the new failure mode that fits it, and its answer is marked present there. Answers you leave out read missing on the new modes until someone judges them.'
    });
  } else if (d.kind === 'retire') {
    Object.assign(out, { reason: d.reason, note: 'Its judgments are kept. It leaves label completeness and the suggestion queues.' });
  } else if (d.kind === 'freeze') {
    Object.assign(out, { note: 'Freezing is one-way: the definition, boundary, level, where it lives and how it is checked can no longer change, and it cannot be merged or split. A new meaning needs a new failure mode.' });
  } else if (d.kind === 'file') {
    Object.assign(out, {
      fileModes: live.map((m) => ({ id: m.id, name: m.name, definition: m.definition || '', on: d.modeId === m.id, pick: () => a.modeDialogSet({ modeId: m.id, override: false, conflict: '' }) })),
      conflict: d.conflict || '', fileAnyway: d.conflict ? () => { a.modeDialogSet({ override: true }); return a.modeDialogSubmit(); } : null
    });
  } else if (d.kind === 'dispose') {
    Object.assign(out, {
      outcomes: Object.keys(DISPOSE_TEXT).map((k) => ({ value: k, label: DISPOSE_TEXT[k], on: d.outcome === k, pick: () => a.modeDialogSet({ outcome: k }) })),
      noteText: d.note
    });
  }
  return out;
}

// The Progress tab's failure-mode rows. They read the extra keys the service adds to the progress figures
// (completeness, modes, discovery as a list of blocks, discovery_by_batch, saturation, re_review, unfiled_notes) and
// show "—" until those arrive; the open-coding figures the same reply carries in the older shape are not these.
function progressVals(a, s, t) {
  const sm = samplingOf(s);
  const prg = sm.progress || null;
  const comp = prg && prg.completeness && typeof prg.completeness === 'object' ? prg.completeness : null;
  const modes = prg && Array.isArray(prg.modes) ? prg.modes : [];
  const points = prg && Array.isArray(prg.discovery) ? prg.discovery : [];
  const sat = prg && prg.saturation && typeof prg.saturation === 'object' && !('available' in prg.saturation) ? prg.saturation : null;
  const rr = prg && prg.re_review && typeof prg.re_review === 'object' ? prg.re_review : null;
  const stepOf = {};
  ((prg && prg.batches) || []).forEach((b) => { stepOf[b.id] = b.plan_step; });
  const block = (prg && prg.block) || sm.progressBlock || 10;
  const company = hasCompany(s);
  return {
    ensure: () => a.taxEnsure(),
    ready: !!comp,
    completeness: comp ? {
      text: comp.required ? comp.done + ' of ' + comp.required + ' judgments made' : 'No judgments needed yet',
      missing: comp.missing ? comp.missing + ' missing' : 'None missing',
      detail: comp.reviewed_turns + ' reviewed answers × ' + comp.modes + ' confirmed or frozen failure modes',
      preview: (comp.missing_preview || []).slice(0, 10).map((r) => ({ key: r.turn_id, text: clip(r.question || 'Answer', 90) + ' · ' + r.missing + ' missing' })),
      more: comp.more ? '…and ' + comp.more + ' more answers' : '',
      showMissing: comp.missing ? () => a.matrixShowMissing() : null
    } : null,
    perMode: modes.map((m) => ({
      id: m.mode_id, name: m.name, statusText: STATUS_TEXT[m.status] || m.status, dataFault: m.level === 'data',
      first: String(m.first_failure), any: String(m.any_instance), decided: String(m.decided),
      fraction: m.sample_fraction && m.sample_fraction.decided ? pct(m.sample_fraction.fraction) + ' of ' + m.sample_fraction.decided + ' judged' : '—',
      pending: m.pending_suggestions ? String(m.pending_suggestions) : '—', open: () => a.modeShow(m.mode_id) })),
    perModeNote: 'First failure counts reviewers’ notes filed under the mode; any instance counts every answer marked present. They differ. Shares are of the reviewed sample, not rates for all answers.',
    curve: Object.assign(modeCurve(points), { caption: 'New failure modes named in each block of ' + block + ' reviews.' }),
    byBatch: ((prg && prg.discovery_by_batch) || []).map((b) => ({ key: String(b.batch_id),
      text: (b.batch_id ? (PLAN_STEP_LABELS[stepOf[b.batch_id]] || 'A review batch') : 'Answers outside any batch') + ': ' + b.new_modes + (b.new_modes === 1 ? ' new failure mode' : ' new failure modes') })),
    saturation: sat ? (sat.text || (points.length ? 'New failure modes are still appearing.' : '—')) : '—',
    finalUniform: sat && sat.final_uniform_text ? sat.final_uniform_text : '',
    reReview: rr && rr.text ? { text: rr.text, turns: (rr.turn_ids || []).map((id, i) => ({ key: id, label: 'Answer ' + (i + 1), open: company ? () => a.taxOpenAnswer(id) : null })) } : null,
    unfiled: prg && prg.unfiled_notes !== undefined && prg.unfiled_notes !== null ? String(prg.unfiled_notes) : '—'
  };
}

function mapVals(a, s, t) {
  const modes = t.tlModes || [];
  const live = modes.filter((m) => m.status !== 'retired');
  const names = {};
  modes.forEach((m) => { names[m.id] = m.name; });
  const byId = {};
  live.forEach((m) => { byId[m.id] = m; });
  const present = (m) => (m.counts && Number(m.counts.present)) || 0;
  const rects = treemap(live.filter((m) => present(m) > 0).map((m) => ({ id: m.id, value: present(m) })), MAP_W, MAP_H);
  return {
    loading: !!t.tlModesLoading && !t.tlModesLoadedAt, error: t.tlModesErr || '', reload: () => a.modesLoad(),
    width: MAP_W, height: MAP_H, empty: !!t.tlModesLoadedAt && !live.length,
    emptyText: 'No failure modes yet. They start from reviewers’ notes on wrong answers.',
    blocks: rects.map((r) => {
      const m = byId[r.id];
      return Object.assign({}, r, { name: m.name, count: present(m), statusText: STATUS_TEXT[m.status] || m.status, tone: STATUS_TONE[m.status] || 'dormant',
        dataFault: m.level === 'data', title: m.name + ': ' + present(m) + ' answers marked present · ' + (STATUS_TEXT[m.status] || m.status), open: () => a.modeShow(m.id) });
    }),
    unseen: live.filter((m) => present(m) === 0).map((m) => ({ id: m.id, name: m.name, statusText: STATUS_TEXT[m.status] || m.status,
      tone: STATUS_TONE[m.status] || 'dormant', open: () => a.modeShow(m.id) })),
    retired: modes.filter((m) => m.status === 'retired').map((m) => ({ id: m.id, name: m.name, open: () => a.modeShow(m.id),
      text: m.merged_into ? 'merged into ' + (names[m.live_mode_id] || names[m.merged_into] || 'another failure mode')
        : modes.some((x) => x.split_from === m.id) ? 'split into new failure modes' : 'retired' })),
    caption: 'Block size: answers marked present among the reviewed answers you can see.'
  };
}

function unfiledVals(a, s, t) {
  const owner = owns(s);
  const proposer = proposes(s);
  const rows = t.tlUnfiled || [];
  const chosen = t.tlUnfiledChosen || [];
  const company = hasCompany(s);
  return {
    loading: !!t.tlUnfiledLoading && !t.tlUnfiledLoadedAt, error: t.tlUnfiledErr || '', reload: () => a.unfiledLoad(),
    total: t.tlUnfiledTotal === null || t.tlUnfiledTotal === undefined ? '—' : String(t.tlUnfiledTotal),
    hiddenNote: t.tlUnfiledHidden ? t.tlUnfiledHidden + (t.tlUnfiledHidden === 1 ? ' note stays' : ' notes stay') + ' hidden until you judge those answers yourself.' : '',
    empty: !!t.tlUnfiledLoadedAt && !rows.length, emptyText: 'No unfiled notes.',
    canCreate: owner, canPropose: proposer, ownerHint: acting(s) ? OWNER_ONLY_HINT : '', notice: t.tlModesDialog ? '' : t.tlModesNotice || '',
    chosenCount: chosen.length,
    newFromChosen: owner && chosen.length ? () => a.noteNewMode(chosen) : null,
    proposeFromChosen: proposer && chosen.length ? () => a.noteProposeMode(chosen) : null,
    clearChosen: chosen.length ? () => a.unfiledClearChosen() : null,
    undo: owner && t.tlUnfiledUndo ? { text: 'Note set aside.', run: () => a.noteUndispose(t.tlUnfiledUndo) } : null,
    rows: rows.map((n) => ({
      id: n.annotation_id, note: n.note || '', quote: clip(n.quote, 140), step: n.step_label || 'Answer', who: n.reviewer_email || 'A reviewer', when: when(n.created_at),
      chosen: chosen.indexOf(n.annotation_id) > -1, choose: owner || proposer ? () => a.unfiledChoose(n.annotation_id) : null,
      fileUnder: () => a.noteFile(n.annotation_id),
      newMode: owner ? () => a.noteNewMode([n.annotation_id]) : null,
      proposeMode: proposer ? () => a.noteProposeMode([n.annotation_id]) : null,
      noModeFits: owner ? () => a.noteDispose(n.annotation_id, 'no_mode_fits') : null,
      notAFailure: owner ? () => a.noteDispose(n.annotation_id, 'not_a_failure') : null,
      open: company && n.conversation_key ? () => a.taxOpenAnswer(n.turn_id, n.conversation_key) : null }))
  };
}

// Where a company's proposal stands, in words. The platform team's list is only the waiting ones.
function proposalStatusText(m) {
  if (m.status === 'proposed') return 'Waiting for the platform team';
  if (m.status === 'rejected') return 'Rejected';
  return 'Accepted · ' + (STATUS_TEXT[m.status] || m.status);
}
const PROPOSAL_RANK = { proposed: 0, rejected: 1 };

// Failure modes companies proposed. The platform team reads every company's waiting proposals and decides each (Accept,
// or Reject with a reason); a company reads its own, waiting first, with the platform team's reason for a rejection.
// null for anyone else, and until the service has said who the person is.
function modeProposedVals(a, s, t) {
  const owner = owns(s);
  if (!owner && !proposes(s)) return null;
  const names = {};
  (t.tlModes || []).forEach((x) => { names[x.id] = x.name; });
  const rank = (m) => (PROPOSAL_RANK[m.status] === undefined ? 2 : PROPOSAL_RANK[m.status]);
  const rows = (t.tlModesProposed || []).map((m, i) => ({ m, i })).sort((x, y) => rank(x.m) - rank(y.m) || x.i - y.i).map((x) => x.m);
  return {
    audience: owner ? 'platform' : 'company',
    title: owner ? 'Failure modes proposed by companies' : 'Your proposed failure modes',
    intro: owner
      ? 'Company reviewers propose these from their own notes. Accept makes one a candidate and files its notes under it. Reject keeps it on record with your reason.'
      : 'You propose a failure mode from your unfiled notes. The platform team accepts or rejects it, and gives its reason when it rejects.',
    loading: !!t.tlModesProposedLoading && !t.tlModesProposedLoadedAt, error: t.tlModesProposedErr || '', reload: () => a.modePropsLoad(),
    notice: t.tlModesProposedNotice || '',
    empty: !!t.tlModesProposedLoadedAt && !rows.length, emptyText: owner ? 'No proposals waiting.' : 'You have not proposed any failure modes yet.',
    rows: rows.map((m) => {
      const w = t.tlWrites['mode-prop:' + m.id] || {};
      const waiting = m.status === 'proposed';
      return {
        id: m.id, name: m.name, definition: m.definition || '', boundary: m.boundary || '', level: m.level_label || m.level || '—', agent: m.agent_label || m.agent || '—',
        nearest: m.nearest_mode_id ? names[m.nearest_mode_id] || 'Another failure mode' : '',
        who: owner ? m.created_by_email || 'A company reviewer' : '', when: when(m.created_at),
        status: m.status, statusText: proposalStatusText(m), reason: m.status === 'rejected' ? m.rejected_reason || '' : '',
        notes: (m.notes || []).map((n) => ({ id: n.annotation_id, note: n.note || '', quote: clip(n.quote, 120), step: n.step_label || 'Answer',
          who: n.reviewer_email || 'A reviewer', when: when(n.created_at) })),
        busy: !!w.busy, error: w.error || '', retry: w.canRetry ? () => a.retryWrite('mode-prop:' + m.id) : null,
        accept: owner && waiting ? () => a.modePropAccept(m.id) : null,
        reject: owner && waiting ? () => a.modeDialog('reject', { modeId: m.id }) : null
      };
    })
  };
}

function reqsVals(a, s, t) {
  const owner = owns(s);
  const rows = t.tlReqs || [];
  const meta = t.tlReqsMeta || { areas: [], levels: [] };
  const areaLabel = {};
  (meta.areas || []).forEach((x) => { areaLabel[x.key] = x.label; });
  const levelLabel = {};
  (meta.levels || []).forEach((l) => { levelLabel[l.key] = l.label; });
  const codes = [];
  rows.forEach((r) => { if (codes.indexOf(r.code) < 0) codes.push(r.code); });
  const groups = [];
  codes.forEach((code) => {
    const revs = rows.filter((r) => r.code === code).sort((x, y) => y.revision - x.revision);
    const head = revs.find((r) => r.status === 'active') || revs[0];
    let g = groups.find((x) => x.area === head.area);
    if (!g) { g = { area: head.area, label: head.area_label || areaLabel[head.area] || head.area, rows: [] }; groups.push(g); }
    g.rows.push({
      code: head.code, revision: 'revision ' + head.revision, statement: head.statement, level: levelLabel[head.level] || head.level,
      status: head.status, statusText: REQ_STATUS_TEXT[head.status] || head.status,
      sources: (head.implementations || []).map((i, n) => ({ key: String(n), section: i.section || '', quote: i.quote || '',
        check: QUOTE_STATUS_TEXT[i.quote_status] || '', drift: i.quote_status === 'drift' })),
      raw: (head.implementations || []).map((i) => ({ doc: i.doc, section: i.section, line: i.line })),
      citing: (head.citing_modes || []).map((m) => m.name).join(', ') || '—',
      earlier: revs.filter((r) => r !== head).map((r) => ({ key: r.id, text: 'Revision ' + r.revision + ' · ' + (REQ_STATUS_TEXT[r.status] || r.status) + ': ' + r.statement })),
      revise: owner && head.status !== 'withdrawn' ? () => a.reqsOpen('revise', head.code) : null,
      withdraw: owner && (head.status === 'active' || head.status === 'draft') ? () => a.reqsOpen('withdraw', head.code) : null
    });
  });
  const f = t.tlReqsForm;
  const w = t.tlWrites.requirement || {};
  const seedW = t.tlWrites['requirement-seed'] || {};
  return {
    ensure: () => a.reqsEnsure(), reload: () => a.reqsLoad(),
    loading: !!t.tlReqsLoading && !t.tlReqsLoadedAt, error: t.tlReqsErr || seedW.error || '',
    empty: !!t.tlReqsLoadedAt && !rows.length, emptyText: owner ? 'No requirements yet. Load the starting set to begin.' : 'No requirements yet.',
    canEdit: owner, ownerHint: acting(s) ? OWNER_ONLY_HINT : '', notice: t.tlReqsNotice || '', groups,
    seed: owner ? () => a.reqsSeed() : null, seeding: !!seedW.busy, create: owner ? () => a.reqsOpen('create') : null,
    form: f ? {
      kind: f.kind, code: f.code || '', title: f.kind === 'create' ? 'New requirement' : f.kind === 'revise' ? 'Revise ' + f.code : 'Withdraw ' + f.code,
      area: f.area || '', areas: [{ value: '', label: 'Choose…' }].concat((meta.areas || []).map((x) => ({ value: x.key, label: x.label }))),
      level: f.level || '', levels: [{ value: '', label: 'Choose…' }].concat((meta.levels || []).map((l) => ({ value: l.key, label: l.label }))),
      statement: f.statement || '', note: f.note || '',
      impls: (f.impls || []).map((i, n) => Object.assign({ key: String(n) }, i, {
        set: (field) => (e) => a.reqsImpl(n, { [field]: val(e) }), remove: () => a.reqsRemoveImpl(n) })),
      addImpl: f.impls && f.impls.length < 10 ? () => a.reqsAddImpl() : null,
      set: (field) => (e) => a.reqsSet({ [field]: val(e) }), save: () => a.reqsSave(), close: () => a.reqsClose(),
      error: t.tlReqsFormErr || w.error || '', busy: !!w.busy,
      saveLabel: f.kind === 'withdraw' ? 'Withdraw it' : f.kind === 'revise' ? 'Save the new revision' : 'Add it'
    } : null
  };
}

// Every failure-mode view model: `s` is the store state, `a` the actions the buttons call.
export function taxonomyModesVals(s, a) {
  const t = taxOf(s);
  return {
    tlTaxOwner: owns(s),
    tlTaxOwnerHint: acting(s) ? OWNER_ONLY_HINT : '',
    tlTaxProgress: progressVals(a, s, t),
    tlTaxMap: mapVals(a, s, t),
    tlTaxDrawer: drawerVals(a, s, t),
    tlTaxDialog: dialogVals(a, s, t),
    tlTaxUnfiled: unfiledVals(a, s, t),
    tlTaxModeProposed: modeProposedVals(a, s, t),
    tlTaxReqs: reqsVals(a, s, t)
  };
}
