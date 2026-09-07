import React, { useState } from 'react'
import { advancePipeline } from '../api'
import type { StepPayload } from '../types'
import {
  ArrowRight, CheckCircle, FileJson, FileText, Database, FileBarChart,
  Table2, AlertTriangle, ChevronDown, ChevronUp, Zap, Hash, Search,
  TrendingUp, CircleDot,
} from 'lucide-react'

interface Props {
  migrationId: string
  stepKey: string
  payload: StepPayload
  onAdvanced: () => void
}

const STEP_LABELS: Record<string, { node: string; label: string }> = {
  step_1_ingest: { node: '1', label: 'File ingestion' },
  step_2_deterministic_mapping: { node: '2', label: 'Deterministic Mapping' },
  step_3_semantic_mapping: { node: '3', label: 'Semantic Mapping' },
  step_5_preprocess: { node: '4', label: 'Data Pre processing' },
  step_6_hierarchy: { node: '5', label: 'Hierarchy Detection & Confirmation' },
  step_8_output_generation: { node: '6', label: 'Data Artifacts' },
}

export default function StepPause({ migrationId, stepKey, payload, onAdvanced }: Props) {
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const display = STEP_LABELS[stepKey] ?? { node: String(payload.node ?? '?'), label: String(payload.label ?? 'Step') }

  async function handleAdvance() {
    setLoading(true)
    setError(null)
    try {
      await advancePipeline(migrationId)
      onAdvanced()
    } catch (err: any) {
      setError(String(err?.message ?? 'Failed to advance pipeline'))
      setLoading(false)
    }
  }

  return (
    <div className="max-w-4xl">
      {/* Header */}
      <div className="flex items-center gap-3 mb-6">
        <div className="w-10 h-10 rounded-xl bg-blue-100 flex items-center justify-center">
          <CheckCircle size={20} className="text-blue-600" />
        </div>
        <div>
          <h2 className="text-lg font-bold text-slate-900">
            Node {display.node} Complete — {display.label}
          </h2>
          <p className="text-sm text-slate-500">Review the results below, then continue to the next step.</p>
        </div>
      </div>

      {/* Node-specific content */}
      <div className="mb-6">
        <StepBody stepKey={stepKey} payload={payload} />
      </div>

      {error && (
        <div className="mb-4 rounded-lg bg-red-50 border border-red-200 px-4 py-3 text-sm text-red-700">
          {error}
        </div>
      )}

      <button
        className="btn-primary px-8 py-3 text-base"
        onClick={handleAdvance}
        disabled={loading}
      >
        {loading ? (
          <>
            <span className="animate-spin inline-block w-4 h-4 border-2 border-white border-t-transparent rounded-full" />
            Starting next node…
          </>
        ) : (
          <>
            Next Node
            <ArrowRight size={18} />
          </>
        )}
      </button>
    </div>
  )
}

// ── Route to the right display ────────────────────────────────────────────────
function StepBody({ stepKey, payload }: { stepKey: string; payload: StepPayload }) {
  switch (stepKey) {
    case 'step_1_ingest':      return <Node1Ingest payload={payload} />
    case 'step_2_deterministic_mapping': return <Node2Deterministic payload={payload} />
    case 'step_3_semantic_mapping':      return <Node3Semantic payload={payload} />
    case 'step_5_preprocess':  return <Node5Preprocess payload={payload} />
    case 'step_6_hierarchy':   return <Node6Hierarchy payload={payload} />
    case 'step_8_output_generation': return <Node8Output payload={payload} />
    default:
      return (
        <pre className="text-xs text-slate-600 bg-slate-50 rounded-lg p-4 overflow-auto">
          {JSON.stringify(payload, null, 2)}
        </pre>
      )
  }
}

// ── NODE 1: Ingest & Configure ────────────────────────────────────────────────
function Node1Ingest({ payload }: { payload: StepPayload }) {
  const tableHealth: Record<string, any> = payload.table_health ?? {}
  const tables = Object.keys(tableHealth)
  const [expandedTable, setExpandedTable] = useState<string | null>(tables[0] ?? null)

  return (
    <div className="space-y-4">
      {/* Summary row */}
      <div className="grid grid-cols-3 gap-4">
        <Metric label="Total rows" value={(payload.rows ?? 0).toLocaleString()} accent="indigo" />
        <Metric label="Total columns" value={payload.columns ?? 0} accent="indigo" />
        <Metric label="Format" value={(payload.format ?? '—').toUpperCase()} accent="indigo" />
      </div>

      {/* Table health */}
      {tables.length > 0 && (
        <div className="space-y-3">
          <p className="text-xs font-semibold text-slate-600 uppercase tracking-wider flex items-center gap-1.5">
            <Table2 size={13} /> Table Health
          </p>
          {tables.map(tbl => {
            const h = tableHealth[tbl]
            const isOpen = expandedTable === tbl
            const avgNull = h?.avg_null_percentage ?? 0
            const nullPcts: Record<string, number> = h?.null_percentages ?? {}
            const cols = Object.keys(nullPcts)
            const healthColor = avgNull < 5 ? 'text-green-600' : avgNull < 20 ? 'text-amber-600' : 'text-red-600'
            const healthBg   = avgNull < 5 ? 'bg-green-50 border-green-200' : avgNull < 20 ? 'bg-amber-50 border-amber-200' : 'bg-red-50 border-red-200'

            return (
              <div key={tbl} className="card overflow-hidden">
                <button
                  className="w-full flex items-center justify-between px-5 py-3.5 hover:bg-slate-50 transition-colors text-left"
                  onClick={() => setExpandedTable(isOpen ? null : tbl)}
                >
                  <div className="flex items-center gap-3 min-w-0">
                    <span className="font-semibold text-slate-800 text-sm truncate">{tbl}</span>
                    <span className="badge bg-slate-100 text-slate-600 shrink-0">
                      {(h?.row_count ?? 0).toLocaleString()} rows × {h?.column_count ?? 0} cols
                    </span>
                  </div>
                  <div className="flex items-center gap-3 shrink-0">
                    <span className={`text-xs font-semibold ${healthColor}`}>
                      {(100 - avgNull).toFixed(1)}% complete
                    </span>
                    {isOpen ? <ChevronUp size={15} className="text-slate-400" /> : <ChevronDown size={15} className="text-slate-400" />}
                  </div>
                </button>

                {isOpen && cols.length > 0 && (
                  <div className="border-t border-slate-100 px-5 py-4">
                    {/* Completeness bar overall */}
                    <div className="mb-4">
                      <div className="flex justify-between text-xs text-slate-500 mb-1">
                        <span>Overall completeness</span>
                        <span className={`font-semibold ${healthColor}`}>{(100 - avgNull).toFixed(1)}%</span>
                      </div>
                      <div className="h-2 bg-slate-100 rounded-full overflow-hidden">
                        <div
                          className={`h-full rounded-full ${avgNull < 5 ? 'bg-green-500' : avgNull < 20 ? 'bg-amber-500' : 'bg-red-500'}`}
                          style={{ width: `${Math.max(0, 100 - avgNull)}%` }}
                        />
                      </div>
                    </div>

                    {/* Per-column null breakdown */}
                    <div className="space-y-1.5">
                      {cols.slice(0, 30).map(col => {
                        const nullPct = nullPcts[col] ?? 0
                        const completePct = 100 - nullPct
                        const barColor = nullPct < 5 ? 'bg-green-400' : nullPct < 30 ? 'bg-amber-400' : 'bg-red-400'
                        return (
                          <div key={col} className="flex items-center gap-3">
                            <span className="font-mono text-xs text-slate-500 w-40 truncate shrink-0" title={col}>{col}</span>
                            <div className="flex-1 h-1.5 bg-slate-100 rounded-full overflow-hidden">
                              <div className={`h-full rounded-full ${barColor}`} style={{ width: `${completePct}%` }} />
                            </div>
                            <span className={`text-xs font-mono w-10 text-right shrink-0 ${nullPct > 30 ? 'text-red-500' : nullPct > 5 ? 'text-amber-600' : 'text-green-600'}`}>
                              {completePct.toFixed(0)}%
                            </span>
                          </div>
                        )
                      })}
                      {cols.length > 30 && (
                        <p className="text-xs text-slate-400 pt-1">…and {cols.length - 30} more columns</p>
                      )}
                    </div>
                  </div>
                )}
              </div>
            )
          })}
        </div>
      )}
    </div>
  )
}

// ── NODE 2: Deterministic Mapping ─────────────────────────────────────────────
const TIER_META: Record<string, { label: string; color: string; icon: React.ReactNode }> = {
  T1_exact:     { label: 'Exact',     color: 'bg-green-100 text-green-800',  icon: <CheckCircle size={10} /> },
  T1_variation: { label: 'Variation', color: 'bg-teal-100 text-teal-800',    icon: <Hash size={10} /> },
  T1_alias:     { label: 'Alias',     color: 'bg-blue-100 text-blue-800',    icon: <Zap size={10} /> },
  T1_regex:     { label: 'Regex',     color: 'bg-purple-100 text-purple-800',icon: <CircleDot size={10} /> },
  T1_llm:       { label: 'LLM',       color: 'bg-indigo-100 text-indigo-800',icon: <TrendingUp size={10} /> },
}

function Node2Deterministic({ payload }: { payload: StepPayload }) {
  const mappingsByTable: Record<string, any[]> = payload.mappings_by_table ?? {}
  const unresolvedByTable: Record<string, string[]> = payload.unresolved_by_table ?? {}
  const tables = Array.from(new Set([...Object.keys(mappingsByTable), ...Object.keys(unresolvedByTable)]))
  const [expandedTable, setExpandedTable] = useState<string | null>(tables[0] ?? null)

  // Tier distribution totals
  const allMappings = Object.values(mappingsByTable).flat()
  const tierCounts: Record<string, number> = {}
  for (const m of allMappings) {
    const t = m.tier ?? 'unknown'
    tierCounts[t] = (tierCounts[t] ?? 0) + 1
  }

  return (
    <div className="space-y-4">
      {/* Summary */}
      <div className="grid grid-cols-2 gap-4">
        <Metric label="T1 mapped" value={payload.t1_mapped ?? 0} accent="green" />
        <Metric label="Sent to Semantic" value={payload.unresolved ?? 0} accent="amber" />
      </div>

      {/* Tier breakdown pills */}
      {Object.keys(tierCounts).length > 0 && (
        <div className="flex flex-wrap gap-2">
          {Object.entries(tierCounts).map(([tier, count]) => {
            const meta = TIER_META[tier] ?? { label: tier, color: 'bg-slate-100 text-slate-600', icon: null }
            return (
              <span key={tier} className={`flex items-center gap-1.5 badge ${meta.color}`}>
                {meta.icon}{meta.label}: {count}
              </span>
            )
          })}
        </div>
      )}

      {/* Per-table field list */}
      <div className="space-y-3">
        <p className="text-xs font-semibold text-slate-600 uppercase tracking-wider">Field mapping results</p>
        {tables.map(tbl => {
          const mapped: any[] = mappingsByTable[tbl] ?? []
          const unresolved: string[] = unresolvedByTable[tbl] ?? []
          const isOpen = expandedTable === tbl
          const total = mapped.length + unresolved.length

          return (
            <div key={tbl} className="card overflow-hidden">
              <button
                className="w-full flex items-center justify-between px-5 py-3.5 hover:bg-slate-50 transition-colors text-left"
                onClick={() => setExpandedTable(isOpen ? null : tbl)}
              >
                <div className="flex items-center gap-3">
                  <span className="font-semibold text-slate-800 text-sm">{tbl}</span>
                  <span className="badge bg-green-100 text-green-700">{mapped.length} mapped</span>
                  {unresolved.length > 0 && (
                    <span className="badge bg-amber-100 text-amber-700">{unresolved.length} unresolved</span>
                  )}
                </div>
                <div className="flex items-center gap-2 shrink-0">
                  <span className="text-xs text-slate-400">{total} fields</span>
                  {isOpen ? <ChevronUp size={15} className="text-slate-400" /> : <ChevronDown size={15} className="text-slate-400" />}
                </div>
              </button>

              {isOpen && (
                <div className="border-t border-slate-100">
                  {/* Mapped fields */}
                  {mapped.map((m, i) => {
                    const meta = TIER_META[m.tier] ?? { label: m.tier ?? '?', color: 'bg-slate-100 text-slate-600', icon: null }
                    const conf = m.confidence != null ? Math.round(m.confidence * 100) : null
                    return (
                      <div key={i} className="flex items-center gap-3 px-5 py-2.5 border-b border-slate-50 last:border-b-0 hover:bg-slate-50/50">
                        <CheckCircle size={13} className="text-green-500 shrink-0" />
                        <span className="font-mono text-xs text-slate-700 w-40 truncate shrink-0" title={m.source_field}>{m.source_field}</span>
                        <span className="text-slate-300 text-xs shrink-0">→</span>
                        <span className="font-mono text-xs text-indigo-700 flex-1 truncate" title={m.target_field}>{m.target_field}</span>
                        <span className={`flex items-center gap-1 badge shrink-0 ${meta.color}`}>{meta.icon}{meta.label}</span>
                        {conf != null && (
                          <span className={`text-xs font-mono shrink-0 ${conf >= 90 ? 'text-green-600' : conf >= 80 ? 'text-amber-600' : 'text-slate-500'}`}>
                            {conf}%
                          </span>
                        )}
                      </div>
                    )
                  })}

                  {/* Unresolved fields */}
                  {unresolved.map((field, i) => (
                    <div key={`u-${i}`} className="flex items-center gap-3 px-5 py-2.5 border-b border-slate-50 last:border-b-0 bg-amber-50/40">
                      <Search size={13} className="text-amber-500 shrink-0" />
                      <span className="font-mono text-xs text-slate-700 w-40 truncate shrink-0" title={field}>{field}</span>
                      <span className="text-slate-300 text-xs shrink-0">→</span>
                      <span className="text-xs text-amber-600 italic">Sent to semantic matching</span>
                    </div>
                  ))}
                </div>
              )}
            </div>
          )
        })}
      </div>
    </div>
  )
}

// ── NODE 3: Semantic Mapping ──────────────────────────────────────────────────
function Node3Semantic({ payload }: { payload: StepPayload }) {
  const results: any[] = payload.semantic_results ?? []
  const [filter, setFilter] = useState<'all' | 'auto' | 'flagged' | 'unmappable'>('all')

  const auto       = results.filter(r => r.status === 'auto')
  const flagged    = results.filter(r => r.status === 'flagged')
  const unmappable = results.filter(r => r.status === 'unmappable')

  const visible = filter === 'all' ? results
    : filter === 'auto' ? auto
    : filter === 'flagged' ? flagged
    : unmappable

  // Group visible by table
  const byTable: Record<string, any[]> = {}
  for (const r of visible) {
    const tbl = r.table ?? '(unknown)'
    if (!byTable[tbl]) byTable[tbl] = []
    byTable[tbl].push(r)
  }

  return (
    <div className="space-y-4">
      {/* Summary */}
      <div className="grid grid-cols-3 gap-4">
        <Metric label="T2 auto-accepted" value={payload.t2_auto ?? 0} accent="green" />
        <Metric label="Flagged for review" value={payload.flagged ?? 0} accent="amber" />
        <Metric label="Unmappable" value={payload.unmappable ?? 0} accent="red" />
      </div>

      {results.length > 0 && (
        <>
          {/* Filter tabs */}
          <div className="flex gap-2">
            {([
              { key: 'all',       label: `All (${results.length})` },
              { key: 'auto',      label: `Auto (${auto.length})` },
              { key: 'flagged',   label: `Flagged (${flagged.length})` },
              { key: 'unmappable',label: `Unmappable (${unmappable.length})` },
            ] as const).map(tab => (
              <button
                key={tab.key}
                onClick={() => setFilter(tab.key)}
                className={`text-xs font-medium px-3 py-1.5 rounded-lg transition-colors ${
                  filter === tab.key
                    ? 'bg-indigo-600 text-white'
                    : 'bg-slate-100 text-slate-600 hover:bg-slate-200'
                }`}
              >
                {tab.label}
              </button>
            ))}
          </div>

          {/* Field results by table */}
          <div className="space-y-3">
            {Object.entries(byTable).map(([tbl, fields]) => (
              <div key={tbl} className="card overflow-hidden">
                <div className="px-5 py-3 bg-slate-50 border-b border-slate-100">
                  <span className="font-semibold text-slate-700 text-sm">{tbl}</span>
                  <span className="text-xs text-slate-400 ml-2">{fields.length} field{fields.length > 1 ? 's' : ''}</span>
                </div>
                <div>
                  {fields.map((r, i) => (
                    <SemanticRow key={i} result={r} />
                  ))}
                </div>
              </div>
            ))}
          </div>
        </>
      )}
    </div>
  )
}

function SemanticRow({ result }: { result: any }) {
  const isUnmappable = result.status === 'unmappable'

  // For auto/flagged use confidence; for unmappable use best_confidence
  const rawConf: number | null = isUnmappable
    ? (result.best_confidence ?? result.confidence ?? null)
    : (result.confidence ?? null)
  const confPct = rawConf != null ? Math.round(rawConf * 100) : null

  // Displayed target: for unmappable show best_target (greyed out), else target_field
  const displayTarget: string | null = isUnmappable
    ? (result.best_target ?? null)
    : (result.target_field ?? null)

  const statusMap: Record<string, { bg: string; icon: React.ReactNode; label: string; labelColor: string }> = {
    auto:       { bg: 'hover:bg-green-50/30',  icon: <CheckCircle size={13} className="text-green-500 shrink-0" />,  label: 'auto',       labelColor: 'bg-green-100 text-green-700' },
    flagged:    { bg: 'hover:bg-amber-50/30',  icon: <AlertTriangle size={13} className="text-amber-500 shrink-0" />, label: 'flagged',    labelColor: 'bg-amber-100 text-amber-700' },
    unmappable: { bg: 'hover:bg-red-50/30',    icon: <AlertTriangle size={13} className="text-red-400 shrink-0" />,   label: 'unmappable', labelColor: 'bg-red-100 text-red-600' },
  }
  const statusConfig = statusMap[result.status ?? 'flagged'] ?? { bg: '', icon: null, label: result.status as string, labelColor: 'bg-slate-100 text-slate-600' }

  return (
    <div className={`flex items-center gap-3 px-5 py-2.5 border-b border-slate-50 last:border-b-0 transition-colors ${statusConfig.bg}`}>
      {statusConfig.icon}
      <span className="font-mono text-xs text-slate-700 w-44 truncate shrink-0" title={result.source_field}>
        {result.source_field ?? '—'}
      </span>
      <span className="text-slate-300 text-xs shrink-0">→</span>

      {/* Target field — greyed + strikethrough for unmappable best-attempt */}
      <span
        className={`font-mono text-xs flex-1 truncate ${isUnmappable && displayTarget ? 'text-slate-400 line-through' : 'text-indigo-700'}`}
        title={displayTarget ?? undefined}
      >
        {displayTarget
          ? displayTarget
          : <span className="text-slate-400 italic not-italic">—</span>
        }
      </span>

      {/* Unmappable best-attempt label */}
      {isUnmappable && displayTarget && (
        <span className="text-xs text-slate-400 italic shrink-0">best attempt</span>
      )}

      {/* Confidence bar — shown for all statuses when available */}
      {confPct != null && (
        <div className="flex items-center gap-2 shrink-0 w-24">
          <div className="flex-1 h-1.5 bg-slate-100 rounded-full overflow-hidden">
            <div
              className={`h-full rounded-full ${confPct >= 85 ? 'bg-green-500' : confPct >= 65 ? 'bg-amber-500' : 'bg-red-400'}`}
              style={{ width: `${confPct}%` }}
            />
          </div>
          <span className={`text-xs font-mono w-8 text-right ${confPct >= 85 ? 'text-green-600' : confPct >= 65 ? 'text-amber-600' : 'text-red-500'}`}>
            {confPct}%
          </span>
        </div>
      )}

      <span className={`badge shrink-0 text-xs ${statusConfig.labelColor}`}>{statusConfig.label}</span>
    </div>
  )
}

// ── NODE 5: Preprocess ────────────────────────────────────────────────────────
function Node5Preprocess({ payload }: { payload: StepPayload }) {
  const [openTable, setOpenTable] = useState<string | null>(null)
  const previews = (payload.table_previews ?? {}) as Record<string, { columns: string[]; rows: string[][]; total_rows: number }>
  const tableNames = Object.keys(previews)

  // Auto-open first table
  const activeTable = openTable ?? tableNames[0] ?? null

  const warningMessages = (payload.warning_messages ?? []) as string[]

  return (
    <div className="space-y-4">
      {/* Summary metrics */}
      <div className="grid grid-cols-2 gap-4">
        <Metric label="Rows after dedup" value={(payload.rows_cleaned ?? 0).toLocaleString()} accent="green" />
        <Metric label="Quality warnings" value={payload.warnings ?? 0} accent={(payload.warnings ?? 0) > 0 ? 'amber' : 'green'} />
      </div>

      {/* Quality warnings list */}
      {warningMessages.length > 0 && (
        <div className="rounded-lg border border-amber-200 bg-amber-50 px-4 py-3 space-y-1">
          <p className="text-xs font-semibold text-amber-700 mb-1">Quality notes</p>
          {warningMessages.map((w, i) => (
            <p key={i} className="text-xs text-amber-700 flex gap-2">
              <span className="shrink-0 text-amber-400">•</span>{w}
            </p>
          ))}
        </div>
      )}

      {/* Table tabs + data preview */}
      {tableNames.length > 0 && (
        <div>
          {/* Tab row */}
          <div className="flex gap-1 mb-3 border-b border-slate-200">
            {tableNames.map(t => (
              <button
                key={t}
                onClick={() => setOpenTable(t)}
                className={`px-3 py-1.5 text-xs font-medium rounded-t transition-colors ${
                  activeTable === t
                    ? 'bg-white border border-b-white border-slate-200 text-indigo-700 -mb-px'
                    : 'text-slate-500 hover:text-slate-700'
                }`}
              >
                {t}
                <span className="ml-1.5 text-slate-400 font-normal">
                  {(previews[t]?.total_rows ?? 0).toLocaleString()}r
                </span>
              </button>
            ))}
          </div>

          {/* Table preview */}
          {activeTable && previews[activeTable] && (() => {
            const { columns, rows } = previews[activeTable]
            return (
              <div className="overflow-x-auto rounded-lg border border-slate-200">
                <table className="w-full text-xs">
                  <thead>
                    <tr className="bg-slate-50 border-b border-slate-200">
                      {columns.map(col => (
                        <th key={col} className="px-3 py-2 text-left font-semibold text-slate-600 whitespace-nowrap">
                          {col}
                        </th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {rows.map((row, ri) => (
                      <tr key={ri} className={`border-b border-slate-100 last:border-b-0 ${ri % 2 === 1 ? 'bg-slate-50/50' : ''}`}>
                        {row.map((cell, ci) => (
                          <td key={ci} className="px-3 py-2 text-slate-700 font-mono whitespace-nowrap max-w-[180px] truncate" title={cell}>
                            {cell === '' ? <span className="text-slate-300 italic">—</span> : cell}
                          </td>
                        ))}
                      </tr>
                    ))}
                    <tr className="bg-slate-50/40">
                      <td colSpan={columns.length} className="px-3 py-1.5 text-center text-slate-400 text-xs italic">
                        Showing first {rows.length} rows of {(previews[activeTable].total_rows).toLocaleString()} total
                      </td>
                    </tr>
                  </tbody>
                </table>
              </div>
            )
          })()}
        </div>
      )}
    </div>
  )
}

// ── NODE 6: Hierarchy ─────────────────────────────────────────────────────────
function Node6Hierarchy({ payload }: { payload: StepPayload }) {
  return (
    <div className="grid grid-cols-3 gap-4">
      <Metric label="FK relationships" value={payload.hierarchies ?? 0} accent="indigo" />
      <Metric label="Cycles detected" value={payload.cycles ?? 0} accent={payload.cycles > 0 ? 'red' : 'green'} />
      <Metric label="Orphaned records" value={payload.orphans ?? 0} accent={payload.orphans > 0 ? 'amber' : 'green'} />
    </div>
  )
}

// ── NODE 8: Output Generation ─────────────────────────────────────────────────
function Node8Output({ payload }: { payload: StepPayload }) {
  const links = [
    { label: 'JSON',       url: payload.json_url,   icon: <FileJson size={14} /> },
    { label: 'CSV',        url: payload.csv_url,    icon: <FileText size={14} /> },
    { label: 'SQL',        url: payload.sql_url,    icon: <Database size={14} /> },
    { label: 'PDF Report', url: payload.report_url, icon: <FileBarChart size={14} /> },
  ].filter(l => l.url)

  return (
    <div className="space-y-4">
      <div className="grid grid-cols-2 gap-4">
        <Metric label="Tables exported" value={payload.tables ?? 0} accent="indigo" />
        <Metric label="Artifacts uploaded" value={payload.artifacts_uploaded ?? 0} accent="green" />
      </div>
      {links.length > 0 && (
        <div>
          <p className="text-xs font-medium text-slate-500 mb-2">Download outputs</p>
          <div className="flex flex-wrap gap-2">
            {links.map(l => (
              <a key={l.label} href={l.url} target="_blank" rel="noopener noreferrer"
                className="flex items-center gap-1.5 text-xs font-medium text-indigo-600 hover:text-indigo-800 bg-indigo-50 hover:bg-indigo-100 px-3 py-1.5 rounded-lg transition-colors">
                {l.icon}{l.label}
              </a>
            ))}
          </div>
        </div>
      )}
    </div>
  )
}

// ── Shared ────────────────────────────────────────────────────────────────────
function Metric({ label, value, accent }: { label: string; value: any; accent: string }) {
  const colors: Record<string, string> = {
    indigo: 'text-indigo-600 bg-indigo-50',
    green:  'text-green-600 bg-green-50',
    amber:  'text-amber-600 bg-amber-50',
    red:    'text-red-600 bg-red-50',
  }
  return (
    <div className={`rounded-lg px-4 py-3 ${colors[accent] ?? colors.indigo}`}>
      <div className="text-2xl font-bold font-mono">{value}</div>
      <div className="text-xs font-medium mt-0.5 opacity-80">{label}</div>
    </div>
  )
}
