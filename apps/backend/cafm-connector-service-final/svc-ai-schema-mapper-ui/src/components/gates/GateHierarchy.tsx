import { useState } from 'react'
import { submitGateHierarchy } from '../../api'
import type { HierarchyPayload } from '../../types'
import { CheckCircle, XCircle, Edit3, GitBranch, AlertTriangle, Users } from 'lucide-react'

interface Props {
  migrationId: string
  payload: HierarchyPayload
  onSubmitted: () => void
}

type HierarchyAction = 'confirm' | 'reject' | 'modify'

interface Decision {
  action: HierarchyAction
  modified_target?: string
}

export default function GateHierarchy({ migrationId, payload, onSubmitted }: Props) {
  const items = payload.review_items ?? []

  const [decisions, setDecisions] = useState<Decision[]>(
    items.map(item => ({
      action: item.suggested_action === 'reject' ? 'reject' : 'confirm',
    }))
  )
  const [modifyTargets, setModifyTargets] = useState<Record<number, string>>({})
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [treeExpanded, setTreeExpanded] = useState(true)

  function setAction(idx: number, action: HierarchyAction) {
    setDecisions(prev => prev.map((d, i) => i === idx ? { ...d, action } : d))
  }

  function setModifyTarget(idx: number, val: string) {
    setModifyTargets(prev => ({ ...prev, [idx]: val }))
  }

  async function handleSubmit() {
    setLoading(true)
    setError(null)
    try {
      const flat = items.map((item, idx) => ({
        id: item.id,
        type: item.type,
        action: decisions[idx]?.action ?? 'confirm',
        modified_target: decisions[idx]?.action === 'modify' ? modifyTargets[idx] : undefined,
      }))
      await submitGateHierarchy(migrationId, { decisions: flat })
      onSubmitted()
    } catch (err: any) {
      setError(err.message ?? 'Failed to submit hierarchy review')
      setLoading(false)
    }
  }

  const confirmed = decisions.filter(d => d.action === 'confirm').length
  const rejected = decisions.filter(d => d.action === 'reject').length
  const modified = decisions.filter(d => d.action === 'modify').length

  return (
    <div className="max-w-4xl">
      {/* Header */}
      <div className="flex items-start gap-4 mb-6">
        <div className="w-10 h-10 rounded-xl bg-purple-100 flex items-center justify-center shrink-0">
          <GitBranch size={20} className="text-purple-600" />
        </div>
        <div>
          <h2 className="text-lg font-bold text-slate-900">Hierarchy Detection &amp; Confirmation</h2>
          <p className="text-sm text-slate-500 mt-0.5">
            Verify the detected FK relationships and hierarchy structure. Confirm, reject, or modify each relationship.
          </p>
        </div>
      </div>

      {/* Stats */}
      <div className="grid grid-cols-4 gap-4 mb-6">
        <StatCard label="FK relationships" value={payload.total_hierarchies ?? 0} color="text-indigo-600" bg="bg-indigo-50" />
        <StatCard
          label="Cycles detected"
          value={payload.total_cycles ?? 0}
          color={(payload.total_cycles ?? 0) > 0 ? 'text-red-600' : 'text-green-600'}
          bg={(payload.total_cycles ?? 0) > 0 ? 'bg-red-50' : 'bg-green-50'}
        />
        <StatCard
          label="Orphaned records"
          value={payload.total_orphans ?? 0}
          color={(payload.total_orphans ?? 0) > 0 ? 'text-amber-600' : 'text-green-600'}
          bg={(payload.total_orphans ?? 0) > 0 ? 'bg-amber-50' : 'bg-green-50'}
        />
        <StatCard label="To review" value={items.length} color="text-slate-700" bg="bg-slate-100" />
      </div>

      {/* Decision summary */}
      <div className="grid grid-cols-3 gap-3 mb-6">
        <div className="rounded-lg bg-green-50 border border-green-200 px-4 py-3 text-center">
          <div className="text-lg font-bold font-mono text-green-700">{confirmed}</div>
          <div className="text-xs text-green-600">Confirmed</div>
        </div>
        <div className="rounded-lg bg-blue-50 border border-blue-200 px-4 py-3 text-center">
          <div className="text-lg font-bold font-mono text-blue-700">{modified}</div>
          <div className="text-xs text-blue-600">Modified</div>
        </div>
        <div className="rounded-lg bg-red-50 border border-red-200 px-4 py-3 text-center">
          <div className="text-lg font-bold font-mono text-red-700">{rejected}</div>
          <div className="text-xs text-red-600">Rejected</div>
        </div>
      </div>

      {/* Hierarchy tree (optional preview) */}
      {payload.hierarchy_tree && (
        <div className="card mb-6 overflow-hidden">
          <button
            className="w-full flex items-center justify-between px-5 py-3.5 hover:bg-slate-50 transition-colors text-left"
            onClick={() => setTreeExpanded(v => !v)}
          >
            <div className="flex items-center gap-2">
              <Users size={16} className="text-slate-500" />
              <span className="text-sm font-semibold text-slate-700">Detected hierarchy tree</span>
            </div>
            <span className="text-xs text-indigo-600">{treeExpanded ? 'Hide' : 'Show'}</span>
          </button>
          {treeExpanded && (
            <div className="border-t border-slate-100">
              <pre className="text-xs text-slate-600 bg-slate-50 p-5 overflow-auto max-h-60 leading-relaxed font-mono">
                {typeof payload.hierarchy_tree === 'string'
                  ? payload.hierarchy_tree
                  : JSON.stringify(payload.hierarchy_tree, null, 2)}
              </pre>
            </div>
          )}
        </div>
      )}

      {/* Relationship review list */}
      {items.length > 0 ? (
        <div className="space-y-3 mb-6">
          {items.map((item, idx) => {
            const decision = decisions[idx]?.action ?? 'confirm'
            const isCycle = item.type === 'cycle'
            const isOrphan = item.type === 'orphan'

            return (
              <div key={idx} className={`card p-5 ${isCycle ? 'border-red-200' : isOrphan ? 'border-amber-200' : ''}`}>
                <div className="flex items-start gap-4">
                  {/* Left: relationship info */}
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2 mb-2 flex-wrap">
                      <TypeBadge type={item.type} />
                      <span className="font-mono text-sm font-semibold text-slate-800">
                        {item.source_table}.{item.source_column}
                      </span>
                      <span className="text-slate-400 text-xs">→</span>
                      <span className="font-mono text-sm text-slate-700">
                        {item.target_table}
                      </span>
                    </div>

                    {item.description && (
                      <p className="text-xs text-slate-500 mb-2">{item.description}</p>
                    )}

                    {isCycle && (
                      <div className="flex items-center gap-1.5 text-xs text-red-700 bg-red-50 px-3 py-1.5 rounded-lg mb-2 w-fit">
                        <AlertTriangle size={12} />
                        Circular reference detected — consider rejecting
                      </div>
                    )}

                    {decision === 'modify' && (
                      <div className="mt-2">
                        <label className="text-xs font-medium text-slate-600 block mb-1">
                          Override target table
                        </label>
                        <input
                          className="input text-xs py-1.5 max-w-xs"
                          value={modifyTargets[idx] ?? item.target_table ?? ''}
                          onChange={e => setModifyTarget(idx, e.target.value)}
                          placeholder="new_target_table"
                        />
                      </div>
                    )}
                  </div>

                  {/* Right: action buttons */}
                  <div className="flex gap-1.5 shrink-0">
                    {(['confirm', 'modify', 'reject'] as HierarchyAction[]).map(a => (
                      <button
                        key={a}
                        onClick={() => setAction(idx, a)}
                        className={`flex items-center gap-1 text-xs font-medium px-2.5 py-1.5 rounded-lg transition-colors ${
                          decision === a
                            ? a === 'confirm' ? 'bg-green-600 text-white'
                              : a === 'modify' ? 'bg-blue-600 text-white'
                              : 'bg-red-600 text-white'
                            : 'bg-slate-100 text-slate-600 hover:bg-slate-200'
                        }`}
                      >
                        {a === 'confirm' && <CheckCircle size={11} />}
                        {a === 'modify' && <Edit3 size={11} />}
                        {a === 'reject' && <XCircle size={11} />}
                        {a.charAt(0).toUpperCase() + a.slice(1)}
                      </button>
                    ))}
                  </div>
                </div>
              </div>
            )
          })}
        </div>
      ) : (
        <div className="card p-6 text-center text-slate-500 text-sm mb-6">
          No relationships require manual review. All hierarchies were auto-detected with high confidence.
        </div>
      )}

      {error && (
        <div className="mb-4 rounded-lg bg-red-50 border border-red-200 px-4 py-3 text-sm text-red-700">
          {error}
        </div>
      )}

      <button
        className="btn-primary px-8 py-3 text-base"
        onClick={handleSubmit}
        disabled={loading}
      >
        {loading ? (
          <>
            <span className="animate-spin inline-block w-4 h-4 border-2 border-white border-t-transparent rounded-full" />
            Submitting…
          </>
        ) : (
          <>
            <CheckCircle size={18} />
            Confirm Hierarchy ({confirmed} confirmed, {rejected} rejected)
          </>
        )}
      </button>
    </div>
  )
}

function StatCard({ label, value, color, bg }: { label: string; value: number; color: string; bg: string }) {
  return (
    <div className={`card p-4 ${bg}`}>
      <div className={`text-2xl font-bold font-mono ${color}`}>{value}</div>
      <div className="text-xs text-slate-500 mt-0.5">{label}</div>
    </div>
  )
}

function TypeBadge({ type }: { type: string }) {
  const map: Record<string, string> = {
    fk: 'bg-indigo-100 text-indigo-700',
    cycle: 'bg-red-100 text-red-700',
    orphan: 'bg-amber-100 text-amber-700',
    implicit: 'bg-purple-100 text-purple-700',
  }
  return (
    <span className={`badge uppercase text-xs font-mono ${map[type] ?? 'bg-slate-100 text-slate-600'}`}>
      {type}
    </span>
  )
}
