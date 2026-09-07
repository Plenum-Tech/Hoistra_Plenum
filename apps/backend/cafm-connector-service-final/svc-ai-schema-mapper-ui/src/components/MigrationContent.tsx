import type { MigrationState, StepPayload, PreSemanticPayload, FieldMappingPayload, HierarchyPayload, FinalPayload } from '../types'
import StepPause from './StepPause'
import ResultsPanel from './ResultsPanel'
import GatePreSemantic from './gates/GatePreSemantic'
import GateFieldMapping from './gates/GateFieldMapping'
import GateHierarchy from './gates/GateHierarchy'
import GateFinal from './gates/GateFinal'
import { Loader, XCircle, RotateCcw } from 'lucide-react'

interface Props {
  migration: MigrationState | null
  migrationId: string
  onRefresh: () => void
  onReset: () => void
}

export default function MigrationContent({ migration, migrationId, onRefresh, onReset }: Props) {
  if (!migration) {
    return (
      <div className="flex items-center justify-center h-64">
        <div className="flex items-center gap-3 text-slate-500">
          <Loader size={20} className="animate-spin" />
          <span className="text-sm">Loading pipeline status…</span>
        </div>
      </div>
    )
  }

  const { status, pending_gate_type, pending_gate_payload } = migration

  // ── Error states ──────────────────────────────────────────────────────────
  if (status === 'failed' || status === 'ddl_failed') {
    return (
      <div className="max-w-2xl">
        <div className="card p-6">
          <div className="flex items-start gap-4">
            <div className="w-10 h-10 rounded-xl bg-red-100 flex items-center justify-center shrink-0">
              <XCircle size={20} className="text-red-600" />
            </div>
            <div className="flex-1">
              <h2 className="text-lg font-bold text-slate-900 mb-1">
                Pipeline {status === 'ddl_failed' ? 'DDL Error' : 'Failed'}
              </h2>
              {migration.error_message && (
                <pre className="text-sm text-red-700 bg-red-50 rounded-lg p-4 mt-3 overflow-auto whitespace-pre-wrap">
                  {migration.error_message}
                </pre>
              )}
              <button className="btn-secondary mt-4 gap-2" onClick={onReset}>
                <RotateCcw size={14} />
                Start new migration
              </button>
            </div>
          </div>
        </div>
      </div>
    )
  }

  // ── Complete ──────────────────────────────────────────────────────────────
  if (status === 'complete') {
    return <ResultsPanel migration={migration} onReset={onReset} />
  }

  // ── Step pause (interrupt_after) ──────────────────────────────────────────
  if (status === 'step_paused' && pending_gate_type && pending_gate_payload) {
    return (
      <StepPause
        migrationId={migrationId}
        stepKey={pending_gate_type}
        payload={pending_gate_payload as unknown as StepPayload}
        onAdvanced={onRefresh}
      />
    )
  }

  // ── HITL gate pauses ──────────────────────────────────────────────────────
  if (status === 'awaiting_review') {
    if (pending_gate_type === 'pre_semantic') {
      return (
        <GatePreSemantic
          migrationId={migrationId}
          payload={(pending_gate_payload ?? {}) as unknown as PreSemanticPayload}
          onSubmitted={onRefresh}
        />
      )
    }
    if (pending_gate_type === 'field_mapping') {
      return (
        <GateFieldMapping
          migrationId={migrationId}
          payload={(pending_gate_payload ?? {}) as unknown as FieldMappingPayload}
          onSubmitted={onRefresh}
        />
      )
    }
    if (pending_gate_type === 'hierarchy') {
      return (
        <GateHierarchy
          migrationId={migrationId}
          payload={(pending_gate_payload ?? {}) as unknown as HierarchyPayload}
          onSubmitted={onRefresh}
        />
      )
    }
    if (pending_gate_type === 'write' || pending_gate_type === 'final_confirmation') {
      return (
        <GateFinal
          migrationId={migrationId}
          payload={(pending_gate_payload ?? {}) as unknown as FinalPayload}
          onSubmitted={onRefresh}
          onReset={onReset}
        />
      )
    }
  }

  // ── Running ───────────────────────────────────────────────────────────────
  return (
    <div className="max-w-2xl">
      <div className="card p-8 flex flex-col items-center text-center gap-4">
        <div className="w-14 h-14 rounded-2xl bg-indigo-50 flex items-center justify-center">
          <Loader size={28} className="text-indigo-500 animate-spin" />
        </div>
        <div>
          <h2 className="text-lg font-semibold text-slate-800">Pipeline running…</h2>
          <p className="text-sm text-slate-500 mt-1">
            Node {migration.current_step} · {Math.round(migration.progress_pct)}% complete
          </p>
        </div>
        <div className="w-full max-w-sm h-2 bg-slate-100 rounded-full overflow-hidden">
          <div
            className="h-full bg-indigo-500 rounded-full transition-all duration-500"
            style={{ width: `${migration.progress_pct}%` }}
          />
        </div>
        <p className="text-xs text-slate-400">Auto-refreshing every 2 seconds…</p>
      </div>
    </div>
  )
}
