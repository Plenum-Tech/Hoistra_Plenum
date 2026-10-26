// labels — pure helpers and view models for Trace Lab's Labels tab: reviewed answers × failure modes. A decided cell
// is solid (present / absent), an inherited one says "via <mode>", a pending AI proposal is a dashed "AI: present |
// absent" whose tooltip is its evidence, and an undecided cell reads "? missing". One press writes the cell's next
// value (taxonomyActions.js matrixToggle) with the cell's own head as `base`. No requests and no state of its own.
import { PLAN_STEP_LABELS } from './sampling.js';
import { STATUS_TEXT, clip, hasCompany, pct, taxOf, val, when } from './taxCommon.js';

export const MATRIX_PAGE = 50;
export const LABELS_HEADER = 'Counts here are shares of the reviewed sample, not rates for all traffic.';
export const MATRIX_FILTER = { batch_id: '', state: 'all', offset: 0 };
const SOURCE_TEXT = { human_structured_label: 'Marked by a reviewer', human_open_code: 'From a reviewer’s note',
  accepted_suggestion: 'Accepted AI suggestion', rejected_suggestion: 'Dismissed AI suggestion', accepted_label_proposal: 'Accepted AI proposal' };

export function sourceText(source, approval) {
  const t = SOURCE_TEXT[source] || '';
  return t && approval === 'bulk' ? t + ' (approved in bulk)' : t;
}

// What one cell shows. A decided cell is solid (present / absent) and an inherited one adds "via <mode>"; a pending
// AI proposal is dashed, with its evidence as the tooltip; an undecided cell reads "? missing".
export function cellView(cell, modeName) {
  const c = cell || { state: 'unchecked' };
  const name = modeName || 'This failure mode';
  if (c.state === 'unchecked' && c.proposal) {
    const v = c.proposal.suggestion === 'present' ? 'present' : 'absent';
    return { kind: 'ai', style: 'dashed', mark: 'AI: ' + v, via: '', withdrawn: false,
      title: 'AI suggestion, not a judgment: ' + v + '. ' + (c.proposal.evidence || 'No evidence given.') + (c.proposal.quote ? ' “' + clip(c.proposal.quote, 160) + '”' : ''),
      label: name + ': AI suggests ' + v + '. Press to accept.' };
  }
  if (c.state === 'present' || c.state === 'absent') {
    const via = c.via ? 'via ' + (c.via_name || 'a merged failure mode') : '';
    return { kind: c.state, style: 'solid', mark: c.state === 'present' ? '■ present' : '□ absent', via, withdrawn: !!c.note_withdrawn,
      title: [sourceText(c.source, c.approval), c.decided_by ? 'by ' + c.decided_by : '', via].filter(Boolean).join(' · ')
        + (c.note_withdrawn ? '. The note this label came from was withdrawn.' : ''),
      label: name + ': ' + c.state + (via ? ', ' + via : '') + '. Press to change.' };
  }
  return { kind: 'missing', style: 'missing', mark: '? missing', via: '', withdrawn: false, title: 'No judgment yet.',
    label: name + ': missing. Press to mark absent.' };
}

// The value one press writes: an AI cell takes the AI's value (the server records an accepted proposal), an undecided
// cell becomes absent (the common judgment), and a decided cell flips.
export function nextValue(cell) {
  const c = cell || { state: 'unchecked' };
  if (c.state === 'unchecked' && c.proposal) return c.proposal.suggestion === 'present';
  return c.state === 'absent';
}

// One cell of the grid as last read, or null.
export function cellOf(t, turnId, modeId) {
  const row = ((t.tlMatrix && t.tlMatrix.rows) || []).find((r) => r.turn_id === turnId);
  return row && row.cells ? row.cells[modeId] || null : null;
}

function cellPanel(a, t) {
  const open = t.tlMatrixCell;
  if (!open) return null;
  const m = t.tlMatrix || {};
  const row = (m.rows || []).find((r) => r.turn_id === open.turnId) || null;
  const md = (m.modes || []).find((x) => x.id === open.modeId) || null;
  const cell = row && row.cells ? row.cells[open.modeId] || null : null;
  const decided = !!cell && (cell.state === 'present' || cell.state === 'absent');
  const prop = cell && cell.state === 'unchecked' && cell.proposal ? cell.proposal : null;
  const w = t.tlWrites['label:' + open.turnId + ':' + open.modeId] || {};
  return {
    question: row ? row.question || 'Answer' : 'Answer', modeName: md ? md.name : '', definition: md ? md.definition || '' : '',
    view: cellView(cell, md ? md.name : ''), source: cell ? sourceText(cell.source, cell.approval) : '',
    decidedBy: cell && cell.decided_by ? cell.decided_by : '', decidedAt: when(cell && cell.decided_at),
    proposal: prop ? { suggestion: prop.suggestion === 'present' ? 'present' : 'absent', evidence: prop.evidence || '', quote: prop.quote || '' } : null,
    presentLabel: prop && prop.suggestion === 'present' ? 'Accept: present' : 'Mark present',
    absentLabel: prop && prop.suggestion !== 'present' ? 'Accept: absent' : 'Mark absent',
    history: (t.tlMatrixHistory || []).map((h) => ({ key: h.id, when: when(h.created_at), who: h.decided_by_email || '',
      text: h.tombstone ? 'Cleared' : h.present ? 'Present' : 'Absent', source: sourceText(h.source, h.approval), current: !h.superseded_by })),
    historyLoading: t.tlMatrixHistory === null && !t.tlMatrixHistoryErr, historyError: t.tlMatrixHistoryErr || '',
    busy: !!w.busy, error: w.error || '',
    markPresent: () => a.matrixSet(open.turnId, open.modeId, true),
    markAbsent: () => a.matrixSet(open.turnId, open.modeId, false),
    clear: decided ? () => a.matrixClear(open.turnId, open.modeId) : null,
    close: () => a.matrixCloseCell()
  };
}

// The Labels tab and its cell panel: `s` is the store state, `a` the actions the buttons call.
export function taxonomyLabelsVals(s, a) {
  const t = taxOf(s);
  const m = t.tlMatrix;
  const f = Object.assign({}, MATRIX_FILTER, t.tlMatrixFilter || {});
  const modes = (m && m.modes) || [];
  const rows = (m && m.rows) || [];
  const totals = (m && m.totals) || null;
  const writes = t.tlWrites;
  const company = hasCompany(s);
  const fractions = (m && m.sample_fractions) || {};
  const fair = (m && m.fair_sample) || {};
  const total = (m && m.total_rows) || 0;
  const failed = Object.keys(writes).filter((k) => k.indexOf('label:') === 0 && writes[k] && writes[k].error);
  return {
    tlTaxLabels: {
      ensure: () => a.matrixEnsure(), reload: () => a.matrixLoad(),
      loading: !!t.tlMatrixLoading && !t.tlMatrixLoadedAt, error: t.tlMatrixErr || '',
      title: m ? 'Labels — ' + (totals ? totals.reviewed_turns : rows.length) + ' answers × ' + modes.length + ' modes · '
        + (totals ? totals.missing : 0) + ' missing judgments' : 'Labels',
      header: LABELS_HEADER, fractionCopy: (m && m.fraction_copy) || '',
      hiddenNote: m && m.hidden_rows ? m.hidden_rows + (m.hidden_rows === 1 ? ' answer stays' : ' answers stay') + ' hidden until you have judged it yourself.' : '',
      empty: !!t.tlMatrixLoadedAt && !rows.length,
      emptyText: !modes.length ? 'No confirmed failure modes yet. Labels start once a failure mode is confirmed.'
        : f.state === 'missing' ? 'No missing judgments here.' : 'No reviewed answers here yet.',
      legend: ['■ present', '□ absent', 'AI: … dashed — a suggestion until someone accepts it', '? missing'],
      modes: modes.map((md) => {
        const fr = fractions[md.id];
        const fs = fair[md.id];
        return { id: md.id, name: md.name, statusText: STATUS_TEXT[md.status] || md.status, definition: md.definition || '', dataFault: md.level === 'data',
          open: () => a.modeShow(md.id),
          fraction: fr && fr.decided ? fr.present + ' of ' + fr.decided + ' judged (' + pct(fr.fraction) + ')' : '—',
          fair: fs && fs.n ? pct(fs.rate) + ' in fair batches (' + pct(fs.ci_low) + '–' + pct(fs.ci_high) + ', ' + fs.n + ' answers)' : '—' };
      }),
      rows: rows.map((r) => ({
        turnId: r.turn_id, question: clip(r.question || 'Answer', 90),
        batch: r.batch ? PLAN_STEP_LABELS[r.batch.plan_step] || 'A review batch' : 'Not in a batch',
        verdicts: '✓ ' + ((r.verdicts && r.verdicts.right) || 0) + ' · ✗ ' + ((r.verdicts && r.verdicts.wrong) || 0)
          + (r.verdicts && r.verdicts.mine ? ' · you: ' + r.verdicts.mine : ''),
        open: company && r.conversation_key ? () => a.taxOpenAnswer(r.turn_id, r.conversation_key) : null,
        cells: modes.map((md) => {
          const key = 'label:' + r.turn_id + ':' + md.id;
          const w = writes[key] || {};
          return Object.assign({ modeId: md.id, busy: !!w.busy, error: w.error || '',
            toggle: () => a.matrixToggle(r.turn_id, md.id), more: () => a.matrixOpenCell(r.turn_id, md.id) },
          cellView(((r.cells || {})[md.id]) || null, md.name));
        })
      })),
      filter: {
        batches: [{ value: '', label: 'All reviewed answers' }].concat(((m && m.batches) || []).map((b) => ({
          value: b.id, label: (PLAN_STEP_LABELS[b.plan_step] || 'A review batch') + ' · ' + String(b.id).slice(0, 8) }))),
        batchId: f.batch_id || '', setBatch: (e) => a.matrixSetFilter({ batch_id: val(e) || '' }),
        onlyMissing: f.state === 'missing', toggleMissing: () => a.matrixSetFilter({ state: f.state === 'missing' ? 'all' : 'missing' })
      },
      pager: { text: total ? 'Answers ' + (f.offset + 1) + '–' + Math.min(f.offset + MATRIX_PAGE, total) + ' of ' + total : '',
        canPrev: f.offset > 0, canNext: f.offset + MATRIX_PAGE < total, prev: () => a.matrixPage(-1), next: () => a.matrixPage(1) },
      failures: failed.map((k) => ({ key: k, text: writes[k].error, conflict: !!writes[k].conflict,
        retry: writes[k].canRetry ? () => a.retryWrite(k) : null, reload: () => a.matrixLoad() }))
    },
    tlTaxCell: cellPanel(a, t)
  };
}
