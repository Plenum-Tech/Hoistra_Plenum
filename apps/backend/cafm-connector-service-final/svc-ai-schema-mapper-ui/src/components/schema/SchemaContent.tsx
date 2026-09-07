import type { SchemaMappingState, PreSemanticPayload, SchemaGate1Payload, SchemaGate2Payload } from '../../types'
import SchemaResultsPanel from './SchemaResultsPanel'
import SchemaGatePreSemantic from './gates/SchemaGatePreSemantic'
import SchemaGateFieldMapping from './gates/SchemaGateFieldMapping'
import SchemaGateHierarchy from './gates/SchemaGateHierarchy'
import SchemaGateArtifacts from './gates/SchemaGateArtifacts'
import SchemaStepPause from './SchemaStepPause'
import { Loader, XCircle, RotateCcw } from 'lucide-react'

interface Props {
  session: SchemaMappingState | null
  sessionId: string
  onRefresh: () => void
  onReset: () => void
}

export default function SchemaContent({ session, sessionId, onRefresh, onReset }: Props) {
  if (!session) {
    return (
      <div className="flex items-center justify-center h-64">
        <div className="flex items-center gap-3 text-slate-500">
          <Loader size={20} className="animate-spin" />
          <span className="text-sm">Loading schema mapping status…</span>
        </div>
      </div>
    )
  }

  const { status, pending_gate_type, pending_gate_payload } = session

  // ── Error ─────────────────────────────────────────────────────────────────
  if (status === 'error' || status === 'ddl_failed') {
    return (
      <div className="max-w-2xl">
        <div className="card p-6">
          <div className="flex items-start gap-4">
            <div className="w-10 h-10 rounded-xl bg-red-100 flex items-center justify-center shrink-0">
              <XCircle size={20} className="text-red-600" />
            </div>
            <div className="flex-1">
              <h2 className="text-lg font-bold text-slate-900 mb-1">
                Schema mapping {status === 'ddl_failed' ? 'DDL error' : 'failed'}
              </h2>
              {status === 'ddl_failed' && (
                <p className="text-sm text-slate-500 mb-3">
                  DDL execution failed. Check the error below, correct the field definitions, and restart.
                </p>
              )}
              {session.error_message && (
                <pre className="text-sm text-red-700 bg-red-50 rounded-lg p-4 mt-2 overflow-auto whitespace-pre-wrap">
                  {session.error_message}
                </pre>
              )}
              <button className="btn-secondary mt-4 gap-2" onClick={onReset}>
                <RotateCcw size={14} />
                New schema mapping
              </button>
            </div>
          </div>
        </div>
      </div>
    )
  }

  // ── Complete ──────────────────────────────────────────────────────────────
  if (status === 'complete') {
    return <SchemaResultsPanel session={session} onReset={onReset} />
  }

  // ── Step pauses (interrupt_after) ────────────────────────────────────────
  if (status === 'step_paused' && pending_gate_type && pending_gate_payload) {
    return (
      <SchemaStepPause
        sessionId={sessionId}
        stepKey={pending_gate_type}
        payload={pending_gate_payload}
        onAdvanced={onRefresh}
      />
    )
  }

  // ── HITL gate pauses ──────────────────────────────────────────────────────
  if (status === 'awaiting_review') {
    if (pending_gate_type === 'pre_semantic') {
      return (
        <SchemaGatePreSemantic
          sessionId={sessionId}
          payload={(pending_gate_payload ?? {}) as unknown as PreSemanticPayload}
          onSubmitted={onRefresh}
        />
      )
    }
    if (pending_gate_type === 'field_mapping') {
      return (
        <SchemaGateFieldMapping
          sessionId={sessionId}
          payload={(pending_gate_payload ?? {}) as unknown as SchemaGate1Payload}
          onSubmitted={onRefresh}
        />
      )
    }
    if (pending_gate_type === 'hierarchy') {
      return (
        <SchemaGateHierarchy
          sessionId={sessionId}
          payload={(pending_gate_payload ?? {}) as unknown as SchemaGate2Payload}
          onSubmitted={onRefresh}
        />
      )
    }
    if (pending_gate_type === 'artifacts_review') {
      return (
        <SchemaGateArtifacts
          sessionId={sessionId}
          payload={(pending_gate_payload ?? {}) as any}
          onSubmitted={onRefresh}
        />
      )
    }
    // Unknown gate — show raw payload
    return (
      <div className="max-w-2xl">
        <div className="card p-6">
          <h2 className="text-base font-bold text-slate-800 mb-2">
            Waiting for review: <code className="font-mono text-indigo-600">{pending_gate_type}</code>
          </h2>
          <pre className="text-xs text-slate-600 bg-slate-50 rounded-lg p-4 overflow-auto max-h-80">
            {JSON.stringify(pending_gate_payload, null, 2)}
          </pre>
        </div>
      </div>
    )
  }

  // ── Running / ingest ──────────────────────────────────────────────────────
  const nodeLabels: Record<number, string> = {
    0: 'Fetching canonical schema…',
    1: 'Ingesting external schema…',
    2: 'Running deterministic mapping…',
    3: 'Running semantic mapping…',
    4: 'Processing field mapping decisions…',
    5: 'Detecting hierarchy…',
    6: 'Processing hierarchy decisions…',
    7: 'Generating output…',
    8: 'Writing to database…',
  }

  return (
    <div className="max-w-2xl">
      <div className="card p-8 flex flex-col items-center text-center gap-4">
        <div className="w-14 h-14 rounded-2xl bg-indigo-50 flex items-center justify-center">
          <Loader size={28} className="text-indigo-500 animate-spin" />
        </div>
        <div>
          <h2 className="text-lg font-semibold text-slate-800">Schema mapping running…</h2>
          <p className="text-sm text-slate-500 mt-1">
            {nodeLabels[session.current_node] ?? `Node ${session.current_node} · ${Math.round(session.progress_pct)}% complete`}
          </p>
        </div>
        <div className="w-full max-w-sm h-2 bg-slate-100 rounded-full overflow-hidden">
          <div
            className="h-full bg-indigo-500 rounded-full transition-all duration-500"
            style={{ width: `${session.progress_pct}%` }}
          />
        </div>
        <p className="text-xs text-slate-400">Auto-refreshing every 2 seconds…</p>
      </div>
    </div>
  )
}
