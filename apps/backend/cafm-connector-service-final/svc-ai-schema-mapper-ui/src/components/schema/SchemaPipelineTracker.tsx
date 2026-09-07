import { useState } from 'react'
import type { SchemaMappingState } from '../../types'
import {
  CheckCircle, Circle, Clock, AlertCircle, GitMerge, Loader, PauseCircle,
  ChevronDown, ChevronRight,
} from 'lucide-react'

interface Props {
  session: SchemaMappingState
}

interface Step {
  label: string
  sublabel: string
  isGate: boolean
  gateKey?: string
  stepKey?: string   // matches pending_gate_type when status === 'step_paused'
  nodeNum?: number   // value that current_node is set to when this node runs
  logNodeId: number  // node_id in session.nodes (from _SCHEMA_PIPELINE)
}

const STEPS: Step[] = [
  { label: 'Canonical Schema',       sublabel: 'Node 0',  isGate: false, nodeNum: 0,  logNodeId: 0,  stepKey: 'step_0_canonical' },
  { label: 'Schema Ingestion',       sublabel: 'Node 1',  isGate: false, nodeNum: 1,  logNodeId: 1,  stepKey: 'step_1_ingest' },
  { label: 'Deterministic Mapping',  sublabel: 'Node 2',  isGate: false, nodeNum: 2,  logNodeId: 2,  stepKey: 'step_2_deterministic' },
  { label: 'Pre-Semantic Review',    sublabel: 'Gate 0',  isGate: true,  gateKey: 'pre_semantic',     logNodeId: 3 },
  { label: 'Semantic Mapping',       sublabel: 'Node 3',  isGate: false, nodeNum: 3,  logNodeId: 4,  stepKey: 'step_3_semantic' },
  { label: 'Field Mapping Review',   sublabel: 'Gate 1',  isGate: true,  gateKey: 'field_mapping',    logNodeId: 5 },
  { label: 'Hierarchy Detection',    sublabel: 'Node 5',  isGate: false, nodeNum: 5,  logNodeId: 6,  stepKey: 'step_5_hierarchy' },
  { label: 'Hierarchy Verification', sublabel: 'Gate 2',  isGate: true,  gateKey: 'hierarchy',        logNodeId: 7 },
  { label: 'Output Generation',      sublabel: 'Node 7',  isGate: false, nodeNum: 7,  logNodeId: 8,  stepKey: 'step_7_output' },
  { label: 'Artifacts Review',       sublabel: 'Gate 4',  isGate: true,  gateKey: 'artifacts_review', logNodeId: 9 },
  { label: 'Write to Database',      sublabel: 'Node 10', isGate: false, nodeNum: 10, logNodeId: 10 },
]

type StepState = 'waiting' | 'running' | 'paused' | 'active' | 'complete' | 'error'

function getStepState(step: Step, session: SchemaMappingState): StepState {
  const { status, current_node, pending_gate_type } = session

  if (status === 'error' || status === 'ddl_failed') return 'error'
  if (status === 'complete') return 'complete'

  // HITL gate paused
  if (status === 'awaiting_review' && step.gateKey === pending_gate_type) return 'active'

  // Node step paused — highlight the node that just finished
  if (status === 'step_paused' && step.stepKey && step.stepKey === pending_gate_type) return 'paused'

  const node = current_node ?? 0

  if (step.isGate) {
    const gatePassedNode: Record<string, number> = {
      pre_semantic:     2,
      field_mapping:    4,
      hierarchy:        6,
      artifacts_review: 9,
    }
    const passedAt = gatePassedNode[step.gateKey ?? ''] ?? 999
    if (node > passedAt) return 'complete'
    return 'waiting'
  }

  const n = step.nodeNum ?? 999
  if (node > n) return 'complete'
  if (node === n && (status === 'running' || status === 'ingest')) return 'running'
  return 'waiting'
}

export default function SchemaPipelineTracker({ session }: Props) {
  const progressPct = Math.round(session.progress_pct ?? 0)
  const [expandedLogs, setExpandedLogs] = useState<Set<number>>(new Set())

  function toggleLogs(logNodeId: number) {
    setExpandedLogs(prev => {
      const next = new Set(prev)
      next.has(logNodeId) ? next.delete(logNodeId) : next.add(logNodeId)
      return next
    })
  }

  return (
    <div className="p-4">
      <div className="mb-4">
        <p className="text-xs font-semibold text-slate-500 uppercase tracking-wider">Schema Mapper</p>
        <p className="text-sm font-semibold text-slate-800 mt-0.5">{session.external_cmms_name}</p>
      </div>

      {/* Progress bar */}
      <div className="mb-6">
        <div className="flex justify-between text-xs text-slate-500 mb-1">
          <span>Progress</span>
          <span>{progressPct}%</span>
        </div>
        <div className="h-2 bg-slate-100 rounded-full overflow-hidden">
          <div
            className="h-full bg-indigo-500 rounded-full transition-all duration-500"
            style={{ width: `${progressPct}%` }}
          />
        </div>
      </div>

      {/* Steps */}
      <div className="space-y-1">
        {STEPS.map((step, idx) => {
          const state = getStepState(step, session)
          const nodeInfo = session.nodes?.find(n => n.node_id === step.logNodeId)
          const logs: string[] = nodeInfo?.logs ?? []
          const hasLogs = logs.length > 0 && (state === 'complete' || state === 'running')
          const logsOpen = expandedLogs.has(step.logNodeId)

          return (
            <div key={idx}>
              <div className={`flex items-center gap-3 rounded-lg px-2 py-2 ${
                state === 'active'  ? 'bg-amber-50'
                : state === 'paused' ? 'bg-blue-50'
                : ''
              }`}>
                <div className="shrink-0">
                  {state === 'complete' && <CheckCircle size={16} className="text-green-500" />}
                  {state === 'running'  && <Loader      size={16} className="text-indigo-500 animate-spin" />}
                  {state === 'paused'   && <PauseCircle size={16} className="text-blue-500" />}
                  {state === 'active'   && (
                    step.isGate
                      ? <GitMerge size={16} className="text-amber-500" />
                      : <Clock    size={16} className="text-indigo-500" />
                  )}
                  {state === 'waiting'  && <Circle      size={16} className="text-slate-300" />}
                  {state === 'error'    && <AlertCircle size={16} className="text-red-500" />}
                </div>

                <div className="flex-1 min-w-0">
                  <p className={`text-xs font-medium truncate ${
                    state === 'paused'   ? 'text-blue-700'
                    : state === 'active' ? 'text-amber-700'
                    : state === 'complete' ? 'text-slate-700'
                    : state === 'running' ? 'text-slate-800'
                    : state === 'error'   ? 'text-red-600'
                    : 'text-slate-400'
                  }`}>{step.label}</p>
                  <p className="text-xs text-slate-400">
                    {state === 'paused' ? 'Waiting for review →' : step.sublabel}
                  </p>
                </div>

                {/* Log toggle */}
                {hasLogs && (
                  <button
                    onClick={() => toggleLogs(step.logNodeId)}
                    className="shrink-0 flex items-center gap-0.5 text-slate-400 hover:text-slate-600 transition-colors"
                    title={logsOpen ? 'Hide logs' : `${logs.length} log line${logs.length === 1 ? '' : 's'}`}
                  >
                    {logsOpen
                      ? <ChevronDown  size={12} />
                      : <ChevronRight size={12} />
                    }
                    <span className="text-xs font-mono">{logs.length}</span>
                  </button>
                )}
              </div>

              {/* Expanded logs */}
              {hasLogs && logsOpen && (
                <div className="mx-2 mb-1 rounded-md bg-slate-900 px-2.5 py-2 overflow-x-auto">
                  {logs.map((line, li) => (
                    <p key={li} className="text-xs font-mono text-slate-300 leading-relaxed whitespace-pre-wrap break-all">
                      {line}
                    </p>
                  ))}
                </div>
              )}
            </div>
          )
        })}
      </div>

      {/* Stats */}
      {session.stats && (
        <div className="mt-6 border-t border-slate-100 pt-4 space-y-2">
          <p className="text-xs font-semibold text-slate-500 uppercase tracking-wider mb-2">Stats</p>
          {[
            { label: 'Tables',     value: session.stats.total_tables },
            { label: 'Fields',     value: session.stats.total_fields },
            { label: 'T1 mapped',  value: session.stats.tier1_mapped },
            { label: 'T2 mapped',  value: session.stats.tier2_auto_mapped },
            { label: 'Flagged',    value: session.stats.tier2_flagged },
            { label: 'Unmapped',   value: session.stats.unmapped },
            { label: 'FKs',        value: session.stats.detected_fk_count },
          ].map(({ label, value }) =>
            value != null ? (
              <div key={label} className="flex justify-between text-xs">
                <span className="text-slate-500">{label}</span>
                <span className="font-mono font-semibold text-slate-700">{value}</span>
              </div>
            ) : null
          )}
          {session.stats.mapping_coverage_pct != null && (
            <div className="flex justify-between text-xs">
              <span className="text-slate-500">Coverage</span>
              <span className="font-mono font-semibold text-slate-700">
                {Math.round(session.stats.mapping_coverage_pct)}%
              </span>
            </div>
          )}
        </div>
      )}
    </div>
  )
}
