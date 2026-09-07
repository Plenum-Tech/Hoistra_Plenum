import { useState } from 'react'
import { submitGatePreSemantic } from '../../api'
import type { PreSemanticPayload } from '../../types'
import { CheckCircle, XCircle, GitBranch, ChevronDown, ChevronUp } from 'lucide-react'

interface Props {
  migrationId: string
  payload: PreSemanticPayload
  onSubmitted: () => void
}

interface FieldDecision {
  action: 'approve' | 'semantic'
}

export default function GatePreSemantic({ migrationId, payload, onSubmitted }: Props) {
  const tables = Object.keys(payload.review_items_by_table ?? {})

  // Build initial decisions — default all to 'approve'
  const initial: Record<string, FieldDecision[]> = {}
  for (const [table, items] of Object.entries(payload.review_items_by_table ?? {})) {
    initial[table] = items.map(() => ({ action: 'approve' as const }))
  }

  const [decisions, setDecisions] = useState(initial)
  const [expandedTables, setExpandedTables] = useState<Set<string>>(new Set(tables))
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  function toggleTable(table: string) {
    setExpandedTables(prev => {
      const next = new Set(prev)
      next.has(table) ? next.delete(table) : next.add(table)
      return next
    })
  }

  function setFieldAction(table: string, idx: number, action: 'approve' | 'semantic') {
    setDecisions(prev => {
      const copy = { ...prev }
      copy[table] = copy[table].map((d, i) => i === idx ? { action } : d)
      return copy
    })
  }

  function approveAll() {
    const next: Record<string, FieldDecision[]> = {}
    for (const [table, items] of Object.entries(payload.review_items_by_table ?? {})) {
      next[table] = items.map(() => ({ action: 'approve' as const }))
    }
    setDecisions(next)
  }

  async function handleSubmit() {
    setLoading(true)
    setError(null)
    try {
      // Build decisions as dict keyed by table — backend expects
      // { table_name: [{source_field, decision}] }
      const byTable: Record<string, Array<{ source_field: string; decision: string }>> = {}
      for (const [table, items] of Object.entries(payload.review_items_by_table ?? {})) {
        byTable[table] = items.map((item, idx) => ({
          source_field: item.source_field,
          decision: decisions[table]?.[idx]?.action ?? 'approve',
        }))
      }
      await submitGatePreSemantic(migrationId, { decisions: byTable })
      onSubmitted()
    } catch (err: any) {
      setError(err.message ?? 'Failed to submit review')
      setLoading(false)
    }
  }

  const totalFields = Object.values(payload.review_items_by_table ?? {}).reduce(
    (sum, items) => sum + items.length, 0
  )
  const semanticCount = Object.values(decisions).flat().filter(d => d.action === 'semantic').length

  return (
    <div className="max-w-4xl">
      {/* Header */}
      <div className="flex items-start gap-4 mb-6">
        <div className="w-10 h-10 rounded-xl bg-amber-100 flex items-center justify-center shrink-0">
          <GitBranch size={20} className="text-amber-600" />
        </div>
        <div className="flex-1">
          <h2 className="text-lg font-bold text-slate-900">Human review gate (Semantic)</h2>
          <p className="text-sm text-slate-500 mt-0.5">
            Review {totalFields} field mappings before semantic analysis. Approve confident matches or
            send uncertain ones to Tier 2 semantic mapping.
          </p>
        </div>
        <div className="text-right shrink-0">
          <div className="text-2xl font-bold font-mono text-slate-800">{totalFields}</div>
          <div className="text-xs text-slate-500">fields to review</div>
        </div>
      </div>

      {/* Summary bar */}
      <div className="grid grid-cols-3 gap-4 mb-6">
        <div className="card p-4 text-center">
          <div className="text-xl font-bold font-mono text-green-600">{totalFields - semanticCount}</div>
          <div className="text-xs text-slate-500 mt-0.5">Approved</div>
        </div>
        <div className="card p-4 text-center">
          <div className="text-xl font-bold font-mono text-blue-600">{semanticCount}</div>
          <div className="text-xs text-slate-500 mt-0.5">Sent to Semantic</div>
        </div>
        <div className="card p-4 text-center">
          <div className="text-xl font-bold font-mono text-slate-700">{tables.length}</div>
          <div className="text-xs text-slate-500 mt-0.5">Tables</div>
        </div>
      </div>

      {/* Approve all shortcut */}
      <div className="flex justify-end mb-4">
        <button className="btn-secondary text-sm" onClick={approveAll}>
          <CheckCircle size={14} />
          Approve all
        </button>
      </div>

      {/* Tables */}
      <div className="space-y-3 mb-6">
        {tables.map(table => {
          const items = payload.review_items_by_table[table]
          const isOpen = expandedTables.has(table)
          const tableApproved = decisions[table]?.filter(d => d.action === 'approve').length ?? 0

          return (
            <div key={table} className="card overflow-hidden">
              {/* Table header */}
              <button
                className="w-full flex items-center justify-between px-5 py-3.5 hover:bg-slate-50 transition-colors text-left"
                onClick={() => toggleTable(table)}
              >
                <div className="flex items-center gap-3">
                  <span className="font-semibold text-slate-800 text-sm">{table}</span>
                  <span className="badge bg-slate-100 text-slate-600">{items.length} fields</span>
                  {tableApproved === items.length && (
                    <span className="badge bg-green-100 text-green-700">All approved</span>
                  )}
                </div>
                {isOpen ? <ChevronUp size={16} className="text-slate-400" /> : <ChevronDown size={16} className="text-slate-400" />}
              </button>

              {/* Field rows */}
              {isOpen && (
                <div className="border-t border-slate-100 divide-y divide-slate-100">
                  {items.map((item, idx) => {
                    const decision = decisions[table]?.[idx]?.action ?? 'approve'
                    return (
                      <div key={idx} className="px-5 py-3 flex items-center gap-4">
                        {/* Source → Target */}
                        <div className="flex-1 min-w-0">
                          <div className="flex items-center gap-2 flex-wrap">
                            <span className="font-mono text-xs bg-slate-100 text-slate-700 px-2 py-0.5 rounded">
                              {item.source_field}
                            </span>
                            <span className="text-slate-300 text-xs">→</span>
                            <span className="font-mono text-xs bg-indigo-50 text-indigo-700 px-2 py-0.5 rounded">
                              {item.target_field}
                            </span>
                            <ConfidencePill confidence={item.confidence} />
                            <TierPill tier={item.tier} />
                          </div>
                          {item.rationale && (
                            <p className="text-xs text-slate-400 mt-1 truncate">{item.rationale}</p>
                          )}
                          {(item.sample_values?.length ?? 0) > 0 && (
                            <div className="flex gap-1 mt-1 flex-wrap">
                              {item.sample_values!.slice(0, 3).map((v, i) => (
                                <span key={i} className="text-xs bg-slate-50 text-slate-500 px-1.5 py-0.5 rounded border border-slate-200">
                                  {String(v).slice(0, 20)}
                                </span>
                              ))}
                            </div>
                          )}
                        </div>

                        {/* Decision toggle */}
                        <div className="flex gap-2 shrink-0">
                          <button
                            onClick={() => setFieldAction(table, idx, 'approve')}
                            className={`flex items-center gap-1.5 text-xs font-medium px-3 py-1.5 rounded-lg transition-colors ${
                              decision === 'approve'
                                ? 'bg-green-600 text-white'
                                : 'bg-slate-100 text-slate-600 hover:bg-slate-200'
                            }`}
                          >
                            <CheckCircle size={12} />
                            Approve
                          </button>
                          <button
                            onClick={() => setFieldAction(table, idx, 'semantic')}
                            className={`flex items-center gap-1.5 text-xs font-medium px-3 py-1.5 rounded-lg transition-colors ${
                              decision === 'semantic'
                                ? 'bg-blue-600 text-white'
                                : 'bg-slate-100 text-slate-600 hover:bg-slate-200'
                            }`}
                          >
                            <GitBranch size={12} />
                            Semantic
                          </button>
                        </div>
                      </div>
                    )
                  })}
                </div>
              )}
            </div>
          )
        })}
      </div>

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
            Submit Review ({totalFields - semanticCount} approved, {semanticCount} to semantic)
          </>
        )}
      </button>
    </div>
  )
}

function ConfidencePill({ confidence }: { confidence: number }) {
  const pct = Math.round(confidence * 100)
  const color = pct >= 90 ? 'bg-green-100 text-green-700' : pct >= 75 ? 'bg-amber-100 text-amber-700' : 'bg-red-100 text-red-700'
  return <span className={`badge ${color}`}>{pct}%</span>
}

function TierPill({ tier }: { tier: string }) {
  const map: Record<string, string> = {
    t1: 'bg-indigo-100 text-indigo-700',
    t2: 'bg-purple-100 text-purple-700',
  }
  return <span className={`badge ${map[tier] ?? 'bg-slate-100 text-slate-600'}`}>{(tier ?? '').toUpperCase()}</span>
}
