import type { MigrationState } from '../types'
import { CheckCircle, Circle, Clock, AlertCircle, GitMerge, Loader } from 'lucide-react'

interface Props {
  migration: MigrationState
}

interface PipelineStep {
  label: string
  sublabel: string
  isGate: boolean
  stepKey?: string    // matches pending_gate_type when step_paused
  gateKey?: string    // matches pending_gate_type when awaiting_review
  gateKeys?: string[] // optional: treat these gates as part of this step
  nodeNum?: number
}

const STEPS: PipelineStep[] = [
  { label: 'File ingestion',          sublabel: 'Node 1',  isGate: false, stepKey: 'step_1_ingest',                nodeNum: 1  },
  { label: 'Deterministic Mapping',   sublabel: 'Node 2',  isGate: false, stepKey: 'step_2_deterministic_mapping', nodeNum: 2  },
  { label: 'Human review gate (Semantic)', sublabel: 'Gate', isGate: true, gateKey: 'pre_semantic'                               },
  { label: 'Semantic Mapping',        sublabel: 'Node 3',  isGate: false, stepKey: 'step_3_semantic_mapping',       nodeNum: 3  },
  { label: 'Human review gate ( Table Structure Confirmation)', sublabel: 'Gate', isGate: true, gateKey: 'field_mapping'        },
  { label: 'Data Pre processing',     sublabel: 'Node 4',  isGate: false, stepKey: 'step_5_preprocess',             nodeNum: 5  },
  { label: 'Hierarchy Detection & Confirmation', sublabel: 'Node 5', isGate: false, stepKey: 'step_6_hierarchy', gateKeys: ['hierarchy'], nodeNum: 6  },
  { label: 'Data Artifacts',          sublabel: 'Node 6',  isGate: false, stepKey: 'step_8_output_generation',      nodeNum: 8  },
  { label: 'Write to Target DB',      sublabel: 'Node 7',  isGate: true,  gateKey: 'write'                                      },
]

type StepState = 'waiting' | 'running' | 'active' | 'complete' | 'error'

function getStepState(step: PipelineStep, migration: MigrationState): StepState {
  const { status, current_step, pending_gate_type } = migration

  if (status === 'failed' || status === 'ddl_failed') {
    return 'error'
  }

  if (status === 'complete') {
    return 'complete'
  }

  // Is this the currently active step/gate?
  if (status === 'step_paused' && step.stepKey === pending_gate_type) return 'active'
  if (status === 'awaiting_review' && step.gateKeys?.includes(String(pending_gate_type))) return 'active'
  if (status === 'awaiting_review' && step.gateKey === pending_gate_type) return 'active'

  const currentNode = current_step || 0

  if (step.isGate) {
    // Gate is complete if we're past a certain node
    const gatePassedNode: Record<string, number> = {
      pre_semantic: 2,
      field_mapping: 3,
      hierarchy: 6,
      write: 8,
    }
    const passedAt = gatePassedNode[step.gateKey ?? ''] ?? 999
    if (currentNode > passedAt) return 'complete'
    if (currentNode === passedAt && status === 'running') return 'running'
    return 'waiting'
  }

  // Regular node
  const n = step.nodeNum ?? 999
  if (currentNode > n) return 'complete'
  if (currentNode === n && status === 'running') return 'running'
  return 'waiting'
}

export default function PipelineTracker({ migration }: Props) {
  return (
    <div className="p-4">
      <div className="mb-4">
        <p className="text-xs font-semibold text-slate-500 uppercase tracking-wider">Pipeline</p>
        <p className="text-sm font-semibold text-slate-800 mt-0.5">{migration.cmms_name}</p>
      </div>

      {/* Progress bar */}
      <div className="mb-6">
        <div className="flex justify-between text-xs text-slate-500 mb-1">
          <span>Progress</span>
          <span>{Math.round(migration.progress_pct)}%</span>
        </div>
        <div className="h-1.5 bg-slate-100 rounded-full overflow-hidden">
          <div
            className="h-full bg-indigo-500 rounded-full transition-all duration-500"
            style={{ width: `${migration.progress_pct}%` }}
          />
        </div>
      </div>

      {/* Steps */}
      <ol className="relative">
        {STEPS.map((step, i) => {
          const state = getStepState(step, migration)
          return (
            <li key={i} className="flex gap-3 pb-5 last:pb-0 relative">
              {/* Connector line */}
              {i < STEPS.length - 1 && (
                <div className="absolute left-3.5 top-7 bottom-0 w-px bg-slate-200" />
              )}

              {/* Icon */}
              <div className="relative z-10 shrink-0 mt-0.5">
                <StepIcon state={state} isGate={step.isGate} />
              </div>

              {/* Label */}
              <div className={`min-w-0 ${state === 'waiting' ? 'opacity-40' : ''}`}>
                <p className={`text-xs font-semibold leading-tight
                  ${state === 'active' ? 'text-indigo-700' : 'text-slate-800'}`}>
                  {step.label}
                </p>
                <p className={`text-xs mt-0.5
                  ${state === 'active' ? 'text-indigo-500' : 'text-slate-400'}`}>
                  {step.sublabel}
                  {state === 'active' && !step.isGate && ' — review'}
                  {state === 'active' && step.isGate && ' — awaiting'}
                  {state === 'running' && ' — running…'}
                </p>
              </div>
            </li>
          )
        })}
      </ol>

      {/* Stats */}
      {migration.total_fields > 0 && (
        <div className="mt-6 pt-4 border-t border-slate-200 space-y-2">
          <Stat label="T1 mapped" value={migration.t1_mapped_count} color="text-green-600" />
          <Stat label="T2 auto" value={migration.t2_auto_count} color="text-blue-600" />
          <Stat label="T2 human" value={migration.t2_human_count} color="text-amber-600" />
          <Stat label="Unmapped" value={migration.unmapped_count} color="text-red-500" />
          <Stat label="Total fields" value={migration.total_fields} color="text-slate-700" />
        </div>
      )}
    </div>
  )
}

function StepIcon({ state, isGate }: { state: StepState; isGate: boolean }) {
  if (state === 'complete') return <CheckCircle size={18} className="text-green-500" />
  if (state === 'error')    return <AlertCircle size={18} className="text-red-500" />
  if (state === 'running')  return <Loader size={18} className="text-indigo-500 animate-spin" />
  if (state === 'active')   return (
    isGate
      ? <GitMerge size={18} className="text-amber-500" />
      : <Clock size={18} className="text-blue-500" />
  )
  return <Circle size={18} className="text-slate-300" />
}

function Stat({ label, value, color }: { label: string; value: number; color: string }) {
  return (
    <div className="flex items-center justify-between text-xs">
      <span className="text-slate-500">{label}</span>
      <span className={`font-semibold font-mono ${color}`}>{value}</span>
    </div>
  )
}
