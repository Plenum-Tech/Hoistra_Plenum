// The migration card's step rail — the ten pipeline steps in the order svc-ai-schema-mapper
// runs them (graph/migration_graph.py), and which of them is on screen.
//
// CAFM Web's panel (migration-content.tsx) decides what the run is waiting for; it reports
// that here as a small descriptor — { kind, node, sub, auto } — and this model turns it into
// the rail: done steps that can be reopened, the current step with its sub-step counter, the
// steps still to come, and the one-line status the header shows. Pure: no React, no DOM.

export const WIZARD_STEPS = [
  { n: 1, id: 'ingest', short: 'Ingest', title: 'File ingestion' },
  { n: 2, id: 'analyse', short: 'Analyse', title: 'Table & column analysis' },
  { n: 3, id: 'presemantic', short: 'Pre-semantic', title: 'Pre-semantic review' },
  { n: 4, id: 'semantic', short: 'Semantic', title: 'Semantic mapping' },
  { n: 5, id: 'fields', short: 'Fields', title: 'Field mapping review' },
  { n: 6, id: 'preprocess', short: 'Preprocess', title: 'Data preprocessing' },
  { n: 7, id: 'hierarchy', short: 'Hierarchy', title: 'Hierarchy detection' },
  { n: 8, id: 'verify', short: 'Verify', title: 'Hierarchy verification' },
  { n: 9, id: 'output', short: 'Output', title: 'Output generation' },
  { n: 10, id: 'write', short: 'Write', title: 'Write to database' }
];

// Human stops inside one pipeline node, in the order the service raises them.
export const SUB_STEPS = {
  2: ['pk', 'unique'],
  3: ['routing', 'classification', 'column_mapping', 'columns']
};

export const SUB_STEP_TITLES = {
  pk: 'Confirm primary keys',
  unique: 'Confirm unique tables',
  routing: 'Confirm table routing',
  classification: 'Keys & shared attributes',
  column_mapping: 'Confirm column mapping',
  columns: 'Confirm column matching',
  semantic_review: 'Review semantic matches',
  unknown: 'Review'
};

// The gate a node stops at when it has only one.
const NODE_GATE_TITLES = {
  4: 'Review semantic matches',
  5: 'Field mapping decisions',
  8: 'Confirm hierarchy',
  10: 'Confirm & write'
};

const lower = (s) => (s ? s.charAt(0).toLowerCase() + s.slice(1) : s);

// Which pre-semantic pass a gate payload belongs to: the service splits the gate into a
// table-routing pass and a column-matching pass (locked_phase / gate_step).
export function preSemanticSub(payload) {
  const p = payload && typeof payload === 'object' ? payload : {};
  if (String(p.locked_phase || '') === 'columns' || String(p.gate_step || '') === 'column_matching') return 'columns';
  return 'routing';
}

// The current step must not slip backwards while the service is working. After a gate answer
// the service reports the step it last completed — current_step 2 while it builds step 3's
// column analysis (run 4cfd9b86, 30 Sep 2026) — and a rail that had shown step 3 would jump
// back to 2 for minutes. A reported gate, pause, failure, completion or restart is taken as is.
export function settleActive(prev, next) {
  if (!next || next.kind !== 'running' || !prev) return next;
  const held = prev.kind === 'gate' || prev.kind === 'pause' || prev.kind === 'running';
  if (!held) return next;
  const before = Number(prev.node);
  const now = Number(next.node);
  if (Number.isFinite(before) && Number.isFinite(now) && now < before) return { ...next, node: before };
  return next;
}

export function fmtDuration(ms) {
  if (typeof ms !== 'number' || !Number.isFinite(ms) || ms < 0) return null;
  if (ms < 60000) return (Math.round(ms / 100) / 10) + ' s';
  const mins = Math.floor(ms / 60000);
  const secs = Math.round((ms % 60000) / 1000);
  return mins + ' min ' + secs + ' s';
}

const STATUS = {
  loading: { tone: 'neutral', label: 'Starting' },
  running: { tone: 'accent', label: 'Running' },
  gate: { tone: 'accent', label: 'Needs your review' },
  complete: { tone: 'ok', label: 'Complete' },
  failed: { tone: 'risk', label: 'Failed' },
  cancelled: { tone: 'neutral', label: 'Cancelled' },
  restarting: { tone: 'accent', label: 'Restarting' }
};

function activeNodeOf(active) {
  const kind = active && active.kind;
  if (kind === 'loading') return 1;
  if (kind === 'complete') return 10;
  const n = Number(active && active.node);
  return Number.isFinite(n) && n >= 1 && n <= 10 ? n : 1;
}

/**
 * @param {{kind: string, node?: number, sub?: string, auto?: boolean}} active
 * @param {number[]} reviewable  node numbers whose output the card still holds
 * @param {Array<{node_id: number, duration_ms?: number|null}>} nodes  the service's node timings
 */
export function wizardModel(active, reviewable, nodes) {
  const kind = (active && active.kind) || 'loading';
  const activeNode = activeNodeOf(active);
  const kept = new Set((reviewable || []).map(Number));
  const durationOf = (n) => {
    const hit = (nodes || []).find((x) => Number(x && x.node_id) === n);
    const d = hit ? hit.duration_ms : null;
    return typeof d === 'number' && Number.isFinite(d) ? d : null;
  };

  const steps = WIZARD_STEPS.map((s) => {
    let state;
    if (kind === 'complete') state = 'done';
    else if (kind === 'cancelled') state = s.n < activeNode ? 'done' : 'pending';
    else if (s.n < activeNode) state = 'done';
    else if (s.n === activeNode) state = kind === 'failed' ? 'error' : 'active';
    else state = 'pending';
    const current = s.n === activeNode && kind !== 'cancelled';
    return {
      ...s,
      state,
      current,
      reviewable: state === 'done' && !current && kept.has(s.n),
      durationMs: durationOf(s.n)
    };
  });

  const step = WIZARD_STEPS[activeNode - 1];
  const subs = SUB_STEPS[activeNode] || null;
  let subTitle = null;
  let subIndex = null;
  if (kind === 'gate') {
    if (subs && active.sub && subs.includes(active.sub)) {
      subTitle = SUB_STEP_TITLES[active.sub];
      subIndex = [subs.indexOf(active.sub) + 1, subs.length];
    } else {
      subTitle = SUB_STEP_TITLES[active.sub] || NODE_GATE_TITLES[activeNode] || SUB_STEP_TITLES.unknown;
    }
  }

  let status;
  let context;
  const here = 'Step ' + activeNode + ' of 10 — ' + step.title;
  switch (kind) {
    case 'gate':
      status = STATUS.gate;
      context = here + ': ' + lower(subTitle) + (subIndex ? ' (' + subIndex[0] + ' of ' + subIndex[1] + ')' : '');
      break;
    case 'pause':
      status = active.auto ? STATUS.running : { tone: 'neutral', label: 'Paused' };
      context = here + ' finished: ' + (active.auto ? 'continuing' : 'review it, then continue');
      break;
    case 'running':
      status = STATUS.running;
      context = here + ' is running';
      break;
    case 'complete':
      status = STATUS.complete;
      context = 'All 10 steps complete — the data is in the database';
      break;
    case 'failed':
      status = STATUS.failed;
      context = here + ' failed';
      break;
    case 'cancelled':
      status = STATUS.cancelled;
      context = 'Migration cancelled';
      break;
    case 'restarting':
      status = STATUS.restarting;
      context = 'Restarting from step ' + activeNode + ' — ' + step.title;
      break;
    default:
      status = STATUS.loading;
      context = 'Starting the migration';
  }

  return { activeNode, steps, status, context, subTitle, subIndex };
}

// ── Live engine progress ──────────────────────────────────────────────────────────────────────
// A Go-engine run reports where its running step is (the status poll's engine_progress, written
// by the service at most every half second, UTC with no zone). progressLine puts it in words for
// the one line under the step's context; a report older than 30 s says nothing — the step has
// moved on, or the worker has gone and the card's stall check speaks instead.

const STALE_MS = 30000;

function reportTime(at) {
  if (typeof at !== 'string' || !at) return NaN;
  const zoned = /(Z|[+-]\d{2}:?\d{2})$/.test(at) ? at : at + 'Z';
  return Date.parse(zoned);
}

const count = (n) => Number(n).toLocaleString('en-GB');

/**
 * @param {{step?: string, stage?: string|null, table?: string|null, done?: number, total?: number, at?: string}|null|undefined} p
 * @param {number} nowMs
 * @returns {string|null}
 */
export function progressLine(p, nowMs) {
  if (!p || typeof p !== 'object') return null;
  const at = reportTime(p.at);
  if (!Number.isFinite(at) || nowMs - at > STALE_MS) return null;
  const table = typeof p.table === 'string' && p.table ? p.table : null;
  const done = Number(p.done) || 0;
  const total = Number(p.total) || 0;
  const of = (unit) => (total > 0 ? ' — ' + count(done) + ' of ' + count(total) + (unit ? ' ' + unit : '') : '');
  switch (p.step) {
    case 'parse':
      return table ? 'Reading the workbook — ' + table : 'Reading the file';
    case 'combine':
      return table ? 'Combining the uploads — ' + table : 'Combining the uploads';
    case 'preprocess':
      return table ? 'Cleaning ' + table + (total > 1 ? of('tables') : '') : 'Cleaning the tables';
    case 'outputs':
      return table ? 'Writing the output files — ' + table : 'Writing the output files';
    case 'write_plan':
      return 'Checking what the write will do' + (table ? ' — ' + table : '');
    case 'write':
      if (!table) return 'Writing to the database';
      return (p.stage === 'resolve' ? 'Matching references in ' : 'Writing ') + table + of('rows');
    case 'udr':
      return 'Checking relationships' + of('');
    default:
      return null;
  }
}
