import { useState } from 'react'
import { submitSchemaGatePreSemantic } from '../../../api'
import {
  GitBranch, CheckCircle, ChevronDown, ChevronUp,
  ArrowRight, Zap, Hash, CircleDot, TrendingUp,
  AlertTriangle, Search,
} from 'lucide-react'

// ── Types ────────────────────────────────────────────────────────────────────

interface MappedItem {
  source_field: string
  target_field: string
  confidence: number
  tier: string
  rationale?: string
  sample_values?: string[]
}

interface PreSemanticPayload {
  total_reviewable?: number
  // Schema Mapper gate: backend sends items_by_table (all T1-matched reviewable fields)
  items_by_table?: Record<string, MappedItem[]>
  // Fallback (migration-style)
  mappings_by_table?: Record<string, MappedItem[]>
  // Unresolved fields going to semantic (optional, sent by some backend versions)
  unresolved_by_table?: Record<string, string[]>
}

interface Props {
  sessionId: string
  payload: PreSemanticPayload
  onSubmitted: () => void
}

// ── Tier badge metadata ───────────────────────────────────────────────────────

const TIER_META: Record<string, { label: string; color: string; icon: React.ReactNode }> = {
  T1_exact:    { label: 'Exact',    color: 'bg-green-100 text-green-800',   icon: <CheckCircle size={10} /> },
  T1_alias:    { label: 'Alias',    color: 'bg-blue-100 text-blue-800',     icon: <Zap size={10} /> },
  T1_regex:    { label: 'Regex',    color: 'bg-purple-100 text-purple-800', icon: <CircleDot size={10} /> },
  T1_registry: { label: 'Registry', color: 'bg-teal-100 text-teal-800',     icon: <Hash size={10} /> },
  T1_llm:      { label: 'LLM',      color: 'bg-indigo-100 text-indigo-800', icon: <TrendingUp size={10} /> },
}

// Backend expects exactly these two values: "approve" | "semantic"
type FieldDecision = 'approve' | 'semantic'

// ── Component ─────────────────────────────────────────────────────────────────

export default function SchemaGatePreSemantic({ sessionId, payload, onSubmitted }: Props) {
  const mappingsByTable: Record<string, MappedItem[]> =
    payload.items_by_table ?? payload.mappings_by_table ?? {}
  const unresolvedByTable: Record<string, string[]> =
    payload.unresolved_by_table ?? {}

  const allTables = Array.from(
    new Set([...Object.keys(mappingsByTable), ...Object.keys(unresolvedByTable)])
  )
  const allMatchedItems = Object.values(mappingsByTable).flat()
  const totalMatched = allMatchedItems.length
  const totalUnresolved = Object.values(unresolvedByTable).flat().length

  // Per-field decisions: key = "table::field", value = 'approve' | 'send_to_semantic'
  const [decisions, setDecisions] = useState<Record<string, FieldDecision>>(() => {
    const initial: Record<string, FieldDecision> = {}
    for (const [tbl, items] of Object.entries(mappingsByTable)) {
      for (const item of items) {
        initial[`${tbl}::${item.source_field}`] = 'approve'  // default: approve
      }
    }
    return initial
  })

  const [expandedTables, setExpandedTables] = useState<Set<string>>(new Set(allTables))
  const [loading, setLoading]               = useState(false)
  const [error, setError]                   = useState<string | null>(null)

  function toggleTable(tbl: string) {
    setExpandedTables(prev => {
      const next = new Set(prev)
      next.has(tbl) ? next.delete(tbl) : next.add(tbl)
      return next
    })
  }

  function setDecision(tbl: string, field: string, decision: FieldDecision) {
    setDecisions(prev => ({ ...prev, [`${tbl}::${field}`]: decision }))
  }

  function selectAllDecisions(decision: FieldDecision) {
    setDecisions(prev => {
      const next: Record<string, FieldDecision> = {}
      for (const key of Object.keys(prev)) next[key] = decision
      return next
    })
  }

  const approvedCount       = Object.values(decisions).filter(d => d === 'approve').length
  const sendToSemanticCount = Object.values(decisions).filter(d => d === 'semantic').length

  async function handleProceed() {
    setLoading(true)
    setError(null)
    try {
      // Backend reads d.get("decision", "approve") expecting "approve" or "semantic"
      const decisionList = Object.entries(mappingsByTable).flatMap(([tbl, items]) =>
        items.map(item => ({
          source_field: item.source_field,
          source_table: tbl,
          decision: decisions[`${tbl}::${item.source_field}`] ?? 'approve',
        }))
      )
      await submitSchemaGatePreSemantic(sessionId, decisionList)
      onSubmitted()
    } catch (err: any) {
      setError(err?.message ?? 'Failed to proceed')
      setLoading(false)
    }
  }

  // Group by tier for the summary bar
  const tierCounts: Record<string, number> = {}
  for (const item of allMatchedItems) {
    tierCounts[item.tier] = (tierCounts[item.tier] ?? 0) + 1
  }

  return (
    <div className="max-w-4xl">
      {/* ── Header ──────────────────────────────────────────────────────────── */}
      <div className="flex items-start gap-4 mb-6">
        <div className="w-10 h-10 rounded-xl bg-amber-100 flex items-center justify-center shrink-0">
          <AlertTriangle size={20} className="text-amber-600" />
        </div>
        <div className="flex-1">
          <h2 className="text-lg font-bold text-slate-900">Pre-Semantic Review</h2>
          <p className="text-sm text-slate-500 mt-0.5">
            Node 2 found <strong>{totalMatched} deterministic matches</strong> across{' '}
            <strong>{Object.keys(mappingsByTable).length} tables</strong>.
            Review each match and decide to <strong>Approve</strong> it or{' '}
            <strong>Send to Semantic</strong> for embedding-based re-matching.
            {totalUnresolved > 0 && (
              <> An additional <strong>{totalUnresolved} unresolved fields</strong> will go directly to semantic matching.</>
            )}
          </p>
        </div>
      </div>

      {/* ── Summary bar ─────────────────────────────────────────────────────── */}
      <div className="grid grid-cols-3 gap-4 mb-5">
        <div className="card p-4 text-center bg-green-50/60">
          <div className="text-2xl font-bold font-mono text-green-700">{approvedCount}</div>
          <div className="text-xs text-slate-500 mt-0.5">Approved</div>
        </div>
        <div className="card p-4 text-center bg-blue-50/60">
          <div className="text-2xl font-bold font-mono text-blue-700">{sendToSemanticCount + totalUnresolved}</div>
          <div className="text-xs text-slate-500 mt-0.5">→ Semantic (Node 3)</div>
        </div>
        <div className="card p-4 text-center">
          <div className="text-2xl font-bold font-mono text-slate-700">{totalMatched}</div>
          <div className="text-xs text-slate-500 mt-0.5">T1 matches to review</div>
        </div>
      </div>

      {/* ── Tier breakdown ───────────────────────────────────────────────────── */}
      {Object.keys(tierCounts).length > 0 && (
        <div className="flex flex-wrap gap-2 mb-5">
          {Object.entries(tierCounts).map(([tier, count]) => {
            const meta = TIER_META[tier] ?? { label: tier, color: 'bg-slate-100 text-slate-600', icon: null }
            return (
              <span key={tier} className={`flex items-center gap-1.5 text-xs font-medium px-3 py-1.5 rounded-full ${meta.color}`}>
                {meta.icon}
                {meta.label}: {count}
              </span>
            )
          })}
        </div>
      )}

      {/* ── Bulk actions ─────────────────────────────────────────────────────── */}
      {totalMatched > 0 && (
        <div className="flex items-center justify-between mb-5 px-4 py-3 bg-slate-50 rounded-xl border border-slate-200">
          <div>
            <p className="text-sm font-semibold text-slate-700">Bulk actions</p>
            <p className="text-xs text-slate-500">Apply one decision to all {totalMatched} fields at once</p>
          </div>
          <div className="flex gap-2 shrink-0">
            <button
              onClick={() => selectAllDecisions('approve')}
              className="flex items-center gap-1.5 text-xs font-medium text-green-700 bg-green-50 hover:bg-green-100 border border-green-200 px-3 py-1.5 rounded-lg transition-colors"
            >
              <CheckCircle size={13} />
              Select all → Approve
            </button>
            <button
              onClick={() => selectAllDecisions('semantic')}
              className="flex items-center gap-1.5 text-xs font-medium text-blue-700 bg-blue-50 hover:bg-blue-100 border border-blue-200 px-3 py-1.5 rounded-lg transition-colors"
            >
              <Search size={13} />
              Select all → Semantic
            </button>
          </div>
        </div>
      )}

      {/* ── Table cards — per-field decisions ──────────────────────────────── */}
      <div className="space-y-3 mb-6">
        {allTables.map(tbl => {
          const items       = mappingsByTable[tbl] ?? []
          const unresolved  = unresolvedByTable[tbl] ?? []
          const isOpen      = expandedTables.has(tbl)
          const approvedInTable = items.filter(item =>
            (decisions[`${tbl}::${item.source_field}`] ?? 'approve') === 'approve'
          ).length
          const toSemanticInTable = items.length - approvedInTable + unresolved.length

          return (
            <div key={tbl} className="card overflow-hidden">
              {/* Table header */}
              <button
                className="w-full flex items-center justify-between px-5 py-3.5 hover:bg-slate-50 transition-colors text-left"
                onClick={() => toggleTable(tbl)}
              >
                <div className="flex items-center gap-3">
                  <span className="font-semibold text-slate-800 text-sm font-mono">{tbl}</span>
                  {approvedInTable > 0 && (
                    <span className="badge bg-green-100 text-green-700">{approvedInTable} approved</span>
                  )}
                  {toSemanticInTable > 0 && (
                    <span className="badge bg-blue-100 text-blue-700">{toSemanticInTable} → semantic</span>
                  )}
                </div>
                {isOpen
                  ? <ChevronUp size={16} className="text-slate-400 shrink-0" />
                  : <ChevronDown size={16} className="text-slate-400 shrink-0" />
                }
              </button>

              {isOpen && (
                <div className="border-t border-slate-100 divide-y divide-slate-100">

                  {/* T1-matched items with per-field decision buttons */}
                  {items.map((item, idx) => {
                    const key        = `${tbl}::${item.source_field}`
                    const decision   = decisions[key] ?? 'approve'
                    const conf       = Math.round(item.confidence * 100)
                    const tierMeta   = TIER_META[item.tier] ?? {
                      label: item.tier ?? '?', color: 'bg-slate-100 text-slate-600', icon: null,
                    }
                    const confColor  = conf >= 95 ? 'text-green-600' : conf >= 85 ? 'text-amber-600' : 'text-red-500'
                    const rowBg      = decision === 'semantic' ? 'bg-blue-50/30' : 'bg-green-50/30'

                    return (
                      <div key={idx} className={`px-5 py-3 flex items-start gap-4 ${rowBg}`}>
                        {/* Source → Target */}
                        <div className="flex-1 min-w-0">
                          <div className="flex items-center gap-2 flex-wrap">
                            <span className="font-mono text-xs bg-slate-100 text-slate-700 px-2 py-0.5 rounded">
                              {item.source_field}
                            </span>
                            <ArrowRight size={11} className="text-slate-300 shrink-0" />
                            <span className="font-mono text-xs bg-indigo-50 text-indigo-700 px-2 py-0.5 rounded">
                              {item.target_field}
                            </span>
                            <span className={`flex items-center gap-1 badge ${tierMeta.color}`}>
                              {tierMeta.icon}{tierMeta.label}
                            </span>
                            <span className={`text-xs font-mono font-semibold ${confColor}`}>
                              {conf}%
                            </span>
                          </div>
                          {item.rationale && (
                            <p className="text-xs text-slate-400 mt-1">{item.rationale}</p>
                          )}
                        </div>

                        {/* Decision buttons — values match backend: "approve" | "semantic" */}
                        <div className="flex gap-1.5 shrink-0">
                          <button
                            onClick={() => setDecision(tbl, item.source_field, 'approve')}
                            className={`text-xs px-3 py-1.5 rounded-lg font-medium transition-colors ${
                              decision === 'approve'
                                ? 'bg-green-600 text-white'
                                : 'bg-slate-100 text-slate-500 hover:bg-slate-200'
                            }`}
                          >
                            Approve
                          </button>
                          <button
                            onClick={() => setDecision(tbl, item.source_field, 'semantic')}
                            className={`text-xs px-3 py-1.5 rounded-lg font-medium transition-colors ${
                              decision === 'semantic'
                                ? 'bg-blue-600 text-white'
                                : 'bg-slate-100 text-slate-500 hover:bg-slate-200'
                            }`}
                          >
                            Send to Semantic
                          </button>
                        </div>
                      </div>
                    )
                  })}

                  {/* Unresolved fields — always going to semantic, read-only */}
                  {unresolved.map((field, i) => (
                    <div key={`u-${i}`} className="flex items-center gap-3 px-5 py-2.5 bg-slate-50/60">
                      <Search size={12} className="text-blue-400 shrink-0" />
                      <span className="font-mono text-xs text-slate-600 flex-1 truncate" title={field}>{field}</span>
                      <span className="text-xs text-blue-600 italic shrink-0">→ semantic</span>
                      <span className="badge bg-blue-100 text-blue-600 shrink-0">unresolved</span>
                    </div>
                  ))}

                </div>
              )}
            </div>
          )
        })}
      </div>

      {/* ── Error ───────────────────────────────────────────────────────────── */}
      {error && (
        <div className="mb-4 rounded-lg bg-red-50 border border-red-200 px-4 py-3 text-sm text-red-700">
          {error}
        </div>
      )}

      {/* ── Proceed button ───────────────────────────────────────────────────── */}
      <button
        className="btn-primary px-8 py-3 text-base"
        onClick={handleProceed}
        disabled={loading}
      >
        {loading ? (
          <>
            <span className="animate-spin inline-block w-4 h-4 border-2 border-white border-t-transparent rounded-full" />
            Submitting decisions…
          </>
        ) : (
          <>
            <GitBranch size={18} />
            Proceed — {approvedCount} approved, {sendToSemanticCount + totalUnresolved} to semantic
          </>
        )}
      </button>
    </div>
  )
}
