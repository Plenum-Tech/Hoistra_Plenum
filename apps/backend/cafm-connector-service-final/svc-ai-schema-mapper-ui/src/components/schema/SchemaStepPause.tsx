import React, { useState } from 'react'
import { advanceSchemaMapping, submitSchemaGatePreSemantic } from '../../api'
import {
  ArrowRight, CheckCircle, Table2,
  AlertTriangle, ChevronDown, ChevronUp, Zap, Hash,
  TrendingUp, CircleDot, GitBranch, FileBarChart,
  FileText, Eye, EyeOff, Archive, PlusCircle, XCircle, Search,
  Key, RotateCcw, Link,
} from 'lucide-react'

interface Props {
  sessionId: string
  stepKey: string
  payload: Record<string, any>
  onAdvanced: () => void
}

// step_2_deterministic is informational-only — no decisions needed; advance just triggers Node 2a
const GATE_STEPS = new Set(['step_4_human_review', 'step_6_verify_hierarchy'])

export default function SchemaStepPause({ sessionId, stepKey, payload, onAdvanced }: Props) {
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  // Gate steps collect decisions before submitting
  const [decisions, setDecisions] = useState<any[]>([])

  const isGate = GATE_STEPS.has(stepKey)

  async function handleAdvance() {
    setLoading(true)
    setError(null)
    try {
      if (isGate) {
        if (stepKey === 'step_6_verify_hierarchy') {
          // Hierarchy gate: decisions[0] holds { approved_foreign_keys, rejected_foreign_keys }
          // Advance endpoint reads these top-level keys and passes them in Command(resume={...})
          const hierarchyDecisions = decisions[0] as {
            approved_foreign_keys: any[]
            rejected_foreign_keys: any[]
          } | undefined
          await advanceSchemaMapping(sessionId, {
            approved_foreign_keys: hierarchyDecisions?.approved_foreign_keys ?? [],
            rejected_foreign_keys: hierarchyDecisions?.rejected_foreign_keys ?? [],
          })
        } else {
          // For custom DDL decisions: resolve target_table from new_table_name when is_new_table=true
          const finalDecisions = decisions.map((d: any) => {
            if (d.action === 'custom' && d.is_new_table && d.new_table_name) {
              return { ...d, target_table: d.new_table_name }
            }
            return d
          })
          await advanceSchemaMapping(sessionId, { decisions: finalDecisions })
        }
      } else {
        await advanceSchemaMapping(sessionId)
      }
      setLoading(false)
      onAdvanced()
    } catch (err: any) {
      setError(String(err?.message ?? 'Failed to advance'))
      setLoading(false)
    }
  }

  return (
    <div className="max-w-4xl">
      {/* Header */}
      <div className="flex items-center gap-3 mb-6">
        <div className={`w-10 h-10 rounded-xl flex items-center justify-center ${isGate ? 'bg-amber-100' : 'bg-blue-100'}`}>
          {isGate
            ? <AlertTriangle size={20} className="text-amber-600" />
            : <CheckCircle size={20} className="text-blue-600" />
          }
        </div>
        <div>
          <h2 className="text-lg font-bold text-slate-900">
            {isGate ? 'Review Required — ' : `Node ${payload.node} Complete — `}{payload.title ?? stepKey}
          </h2>
          <p className="text-sm text-slate-500">
            {isGate
              ? 'Make your decisions below, then submit to continue.'
              : 'Review the output below, then continue to the next step.'}
          </p>
        </div>
      </div>

      {/* Node-specific body */}
      <div className="mb-6">
        <SchemaStepBody stepKey={stepKey} payload={payload} decisions={decisions} setDecisions={setDecisions} />
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
            {isGate ? 'Submitting decisions…' : 'Starting next node…'}
          </>
        ) : (
          <>
            {isGate
              ? stepKey === 'step_6_verify_hierarchy'
                ? 'Confirm Hierarchy'
                : `Submit Decisions (${decisions.length})`
              : stepKey === 'step_2_deterministic'
                ? 'Proceed to Pre-Semantic Review'
                : 'Next Node'
            }
            <ArrowRight size={18} />
          </>
        )}
      </button>
    </div>
  )
}

// ── Route to correct node body ────────────────────────────────────────────────

interface BodyProps {
  stepKey: string
  payload: Record<string, any>
  decisions: any[]
  setDecisions: React.Dispatch<React.SetStateAction<any[]>>
}

function SchemaStepBody({ stepKey, payload, decisions, setDecisions }: BodyProps) {
  switch (stepKey) {
    case 'step_0_canonical':         return <Step0Canonical payload={payload} />
    case 'step_1_ingest':            return <Step1Ingest payload={payload} />
    case 'step_2_deterministic':     return <Step2Deterministic payload={payload} decisions={decisions} setDecisions={setDecisions} />
    case 'step_3_semantic':          return <Step3Semantic payload={payload} />
    case 'step_4_human_review':      return <Step4HumanReview payload={payload} decisions={decisions} setDecisions={setDecisions} />
    case 'step_5_hierarchy':         return <Step5Hierarchy payload={payload} />
    case 'step_6_verify_hierarchy':  return <Step6VerifyHierarchy payload={payload} setDecisions={setDecisions} />
    case 'step_7_output':            return <Step7Output payload={payload} />
    default:                         return <GenericStepView payload={payload} />
  }
}

// ── Step 0: Canonical Schema ──────────────────────────────────────────────────

function Step0Canonical({ payload }: { payload: Record<string, any> }) {
  const tablesData: { table: string; columns: number; all_cols: string[] }[] = payload.tables_data ?? []
  const [expandedTable, setExpandedTable] = useState<string | null>(tablesData[0]?.table ?? null)

  return (
    <div className="space-y-4">
      <div className="grid grid-cols-2 gap-4">
        <Metric label="Canonical tables" value={payload.canonical_table_count ?? 0} accent="indigo" />
        <Metric label="Total columns" value={payload.canonical_column_count ?? 0} accent="indigo" />
      </div>

      {tablesData.length > 0 && (
        <div className="space-y-3">
          <p className="text-xs font-semibold text-slate-600 uppercase tracking-wider flex items-center gap-1.5">
            <Table2 size={13} /> plenum_cafm tables
          </p>
          {tablesData.map(t => {
            const isOpen = expandedTable === t.table
            return (
              <div key={t.table} className="card overflow-hidden">
                <button
                  className="w-full flex items-center justify-between px-5 py-3.5 hover:bg-slate-50 transition-colors text-left"
                  onClick={() => setExpandedTable(isOpen ? null : t.table)}
                >
                  <div className="flex items-center gap-3 min-w-0">
                    <span className="font-semibold text-slate-800 text-sm truncate">{t.table}</span>
                    <span className="badge bg-slate-100 text-slate-600 shrink-0">{t.columns} cols</span>
                  </div>
                  <div className="shrink-0">
                    {isOpen ? <ChevronUp size={15} className="text-slate-400" /> : <ChevronDown size={15} className="text-slate-400" />}
                  </div>
                </button>
                {isOpen && t.all_cols.length > 0 && (
                  <div className="border-t border-slate-100 px-5 py-4">
                    <div className="flex flex-wrap gap-1.5">
                      {t.all_cols.map(c => (
                        <span key={c} className="px-2 py-0.5 bg-slate-100 rounded text-xs font-mono text-slate-600">{c}</span>
                      ))}
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

// ── Step 1: Ingest ────────────────────────────────────────────────────────────

function Step1Ingest({ payload }: { payload: Record<string, any> }) {
  const tablesData: { table: string; columns: number; all_cols: string[] }[] = payload.tables_data ?? []
  const [expandedTable, setExpandedTable] = useState<string | null>(tablesData[0]?.table ?? null)

  return (
    <div className="space-y-4">
      <div className="grid grid-cols-3 gap-4">
        <Metric label="Source tables" value={payload.table_count ?? 0} accent="indigo" />
        <Metric label="Total columns" value={payload.total_columns ?? 0} accent="indigo" />
        <Metric label="CMMS" value={payload.external_cmms_name ?? '—'} accent="indigo" />
      </div>

      {tablesData.length > 0 && (
        <div className="space-y-3">
          <p className="text-xs font-semibold text-slate-600 uppercase tracking-wider flex items-center gap-1.5">
            <Table2 size={13} /> Ingested schema
          </p>
          {tablesData.map(t => {
            const isOpen = expandedTable === t.table
            return (
              <div key={t.table} className="card overflow-hidden">
                <button
                  className="w-full flex items-center justify-between px-5 py-3.5 hover:bg-slate-50 transition-colors text-left"
                  onClick={() => setExpandedTable(isOpen ? null : t.table)}
                >
                  <div className="flex items-center gap-3 min-w-0">
                    <span className="font-semibold text-slate-800 text-sm truncate">{t.table}</span>
                    <span className="badge bg-slate-100 text-slate-600 shrink-0">{t.columns} cols</span>
                  </div>
                  <div className="shrink-0">
                    {isOpen ? <ChevronUp size={15} className="text-slate-400" /> : <ChevronDown size={15} className="text-slate-400" />}
                  </div>
                </button>
                {isOpen && t.all_cols.length > 0 && (
                  <div className="border-t border-slate-100 px-5 py-4">
                    <div className="flex flex-wrap gap-1.5">
                      {t.all_cols.map(c => (
                        <span key={c} className="px-2 py-0.5 bg-slate-100 rounded text-xs font-mono text-slate-600">{c}</span>
                      ))}
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

// ── Step 2: Deterministic Mapping ─────────────────────────────────────────────

const TIER_META: Record<string, { label: string; color: string; icon: React.ReactNode }> = {
  T1_exact:     { label: 'Exact',     color: 'bg-green-100 text-green-800',   icon: <CheckCircle size={10} /> },
  T1_alias:     { label: 'Alias',     color: 'bg-blue-100 text-blue-800',     icon: <Zap size={10} /> },
  T1_regex:     { label: 'Regex',     color: 'bg-purple-100 text-purple-800', icon: <CircleDot size={10} /> },
  T1_registry:  { label: 'Registry',  color: 'bg-teal-100 text-teal-800',     icon: <Hash size={10} /> },
  T1_llm:       { label: 'LLM',       color: 'bg-indigo-100 text-indigo-800', icon: <TrendingUp size={10} /> },
}

// Step2Deterministic — shows T1-matched fields AND unresolved fields going to semantic.
function Step2Deterministic({
  payload,
}: {
  payload: Record<string, any>
  decisions: any[]
  setDecisions: React.Dispatch<React.SetStateAction<any[]>>
}) {
  const mappingsByTable: Record<string, any[]>      = payload.mappings_by_table ?? {}
  const unresolvedByTable: Record<string, string[]> = payload.unresolved_by_table ?? {}
  const tables = Array.from(new Set([...Object.keys(mappingsByTable), ...Object.keys(unresolvedByTable)]))
  const [expandedTable, setExpandedTable] = useState<string | null>(tables[0] ?? null)

  const allMappings = Object.values(mappingsByTable).flat()
  const tierCounts: Record<string, number> = {}
  for (const m of allMappings) {
    const t = m.tier ?? 'unknown'
    tierCounts[t] = (tierCounts[t] ?? 0) + 1
  }

  const totalMapped     = allMappings.length
  const totalUnresolved = Object.values(unresolvedByTable).flat().length

  return (
    <div className="space-y-4">
      <div className="grid grid-cols-2 gap-4">
        <Metric label="T1 matched"  value={totalMapped}     accent="green" />
        <Metric label="→ Semantic"  value={totalUnresolved} accent="amber" />
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

      {/* Per-table: matched + unresolved */}
      <div className="space-y-3">
        <p className="text-xs font-semibold text-slate-600 uppercase tracking-wider">Mapping preview</p>
        {tables.length === 0 && (
          <div className="rounded-lg bg-amber-50 border border-amber-200 px-4 py-3 text-sm text-amber-700">
            No deterministic matches found. All fields will proceed to semantic matching.
          </div>
        )}
        {tables.map(tbl => {
          const mapped: any[]        = mappingsByTable[tbl] ?? []
          const unresolved: string[] = unresolvedByTable[tbl] ?? []
          const isOpen = expandedTable === tbl
          const total  = mapped.length + unresolved.length

          return (
            <div key={tbl} className="card overflow-hidden">
              <button
                className="w-full flex items-center justify-between px-5 py-3.5 hover:bg-slate-50 transition-colors text-left"
                onClick={() => setExpandedTable(isOpen ? null : tbl)}
              >
                <div className="flex items-center gap-3">
                  <span className="font-semibold text-slate-800 text-sm font-mono">{tbl}</span>
                  {mapped.length > 0 && (
                    <span className="badge bg-green-100 text-green-700">{mapped.length} matched</span>
                  )}
                  {unresolved.length > 0 && (
                    <span className="badge bg-amber-100 text-amber-700">{unresolved.length} → semantic</span>
                  )}
                </div>
                <div className="flex items-center gap-2 shrink-0">
                  <span className="text-xs text-slate-400">{total} fields</span>
                  {isOpen ? <ChevronUp size={15} className="text-slate-400" /> : <ChevronDown size={15} className="text-slate-400" />}
                </div>
              </button>

              {isOpen && (
                <div className="border-t border-slate-100">
                  {/* T1 matched */}
                  {mapped.map((m, i) => {
                    const meta = TIER_META[m.tier] ?? { label: m.tier ?? '?', color: 'bg-slate-100 text-slate-600', icon: null }
                    const conf = m.confidence != null ? Math.round(m.confidence * 100) : null
                    return (
                      <div key={i} className="flex items-center gap-3 px-5 py-2.5 border-b border-slate-50 last:border-b-0 bg-green-50/20">
                        <CheckCircle size={12} className="text-green-500 shrink-0" />
                        <span className="font-mono text-xs text-slate-700 w-36 truncate shrink-0" title={m.source_field}>{m.source_field}</span>
                        <span className="text-slate-300 text-xs shrink-0">→</span>
                        <span className="font-mono text-xs text-indigo-700 flex-1 truncate min-w-0" title={m.target_field}>{m.target_field}</span>
                        <span className={`flex items-center gap-1 badge shrink-0 ${meta.color}`}>{meta.icon}{meta.label}</span>
                        {conf != null && (
                          <span className={`text-xs font-mono shrink-0 ${conf >= 90 ? 'text-green-600' : conf >= 80 ? 'text-amber-600' : 'text-red-500'}`}>
                            {conf}%
                          </span>
                        )}
                      </div>
                    )
                  })}

                  {/* Unresolved → going to semantic */}
                  {unresolved.map((field, i) => (
                    <div key={`u-${i}`} className="flex items-center gap-3 px-5 py-2.5 border-b border-slate-50 last:border-b-0 bg-amber-50/30">
                      <Search size={12} className="text-amber-400 shrink-0" />
                      <span className="font-mono text-xs text-slate-700 w-36 truncate shrink-0" title={field}>{field}</span>
                      <span className="text-slate-300 text-xs shrink-0">→</span>
                      <span className="text-xs text-amber-600 italic flex-1">going to semantic</span>
                      <span className="badge bg-blue-100 text-blue-600 shrink-0">T2</span>
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

// ── Step 3: Semantic Mapping ──────────────────────────────────────────────────

function Step3Semantic({ payload }: { payload: Record<string, any> }) {
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
              { key: 'all',        label: `All (${results.length})` },
              { key: 'auto',       label: `Auto (${auto.length})` },
              { key: 'flagged',    label: `Flagged (${flagged.length})` },
              { key: 'unmappable', label: `Unmappable (${unmappable.length})` },
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

  // For unmappable: show best_confidence (the closest attempt), else show confidence
  const rawConf: number | null = isUnmappable
    ? (result.best_confidence ?? result.confidence ?? null)
    : (result.confidence ?? null)
  const confPct = rawConf != null ? Math.round(rawConf * 100) : null

  // For unmappable: show best_target (greyed + strikethrough), else show target_field
  const displayTarget: string | null = isUnmappable
    ? (result.best_target ?? null)
    : (result.target_field ?? null)

  const statusMap: Record<string, { bg: string; icon: React.ReactNode; label: string; labelColor: string }> = {
    auto:       { bg: 'hover:bg-green-50/30',  icon: <CheckCircle size={13} className="text-green-500 shrink-0" />,   label: 'auto',       labelColor: 'bg-green-100 text-green-700' },
    flagged:    { bg: 'hover:bg-amber-50/30',  icon: <AlertTriangle size={13} className="text-amber-500 shrink-0" />,  label: 'flagged',    labelColor: 'bg-amber-100 text-amber-700' },
    unmappable: { bg: 'hover:bg-red-50/30',    icon: <AlertTriangle size={13} className="text-red-400 shrink-0" />,    label: 'unmappable', labelColor: 'bg-red-100 text-red-600' },
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

// ── Step 4: Human Review Gate ─────────────────────────────────────────────────

interface Step4Props {
  payload: Record<string, any>
  decisions: any[]
  setDecisions: React.Dispatch<React.SetStateAction<any[]>>
}

interface NewColDef {
  col_name: string
  data_type: string
  nullable: boolean
}
interface NewTableDef {
  id: string
  table_name: string
  pk_field: string
  columns: NewColDef[]
}

function Step4HumanReview({ payload, decisions, setDecisions }: Step4Props) {
  const unstructuredByTable: Record<string, any[]>  = payload.unstructured_candidates ?? {}
  const unmappedByTable: Record<string, any[]>      = payload.unmapped_fields ?? {}
  const tier1ByTable: Record<string, any[]>         = payload.low_confidence_tier1 ?? {}
  const tier2ByTable: Record<string, any[]>         = payload.low_confidence_tier2 ?? {}
  const canonicalTables: string[]                   = payload.existing_canonical_tables ?? []

  const totalUnstructured = Object.values(unstructuredByTable).flat().length
  const totalUnmapped     = Object.values(unmappedByTable).flat().length
  const totalLowConf      = Object.values(tier1ByTable).flat().length + Object.values(tier2ByTable).flat().length

  // Per-table inclusion toggle (true = included, false = excluded/skipped)
  const [tableInclusion, setTableInclusion] = useState<Record<string, boolean>>(() =>
    Object.fromEntries(Object.keys(unmappedByTable).map(tbl => [tbl, true]))
  )
  // Manually-added column UUIDs per table (columns added beyond the source fields)
  const [addedColIds, setAddedColIds] = useState<Record<string, string[]>>({})
  // Brand-new table definitions (tables that don't exist in the source schema)
  const [newTableDefs, setNewTableDefs] = useState<NewTableDef[]>([])

  // Track per-field decision: key = "table.field"
  const decisionMap = Object.fromEntries(decisions.map((d: any) => [`${d.source_table}.${d.source_field}`, d]))

  function setFieldDecision(d: any) {
    setDecisions(prev => {
      const key = `${d.source_table}.${d.source_field}`
      const filtered = prev.filter((x: any) => `${x.source_table}.${x.source_field}` !== key)
      return [...filtered, d]
    })
  }

  function patchFieldDecision(source_table: string, source_field: string, patch: any) {
    setDecisions(prev => {
      const key = `${source_table}.${source_field}`
      const exists = prev.some((d: any) => `${d.source_table}.${d.source_field}` === key)
      if (exists) {
        return prev.map((d: any) =>
          `${d.source_table}.${d.source_field}` === key ? { ...d, ...patch } : d
        )
      }
      return [...prev, { source_table, source_field, ...patch }]
    })
  }

  function toggleTableInclusion(tbl: string) {
    const currentlyIncluded = tableInclusion[tbl] ?? true
    setTableInclusion(prev => ({ ...prev, [tbl]: !currentlyIncluded }))
    const fields = unmappedByTable[tbl] ?? []
    if (currentlyIncluded) {
      // Excluding: force-skip every field in this table
      setDecisions(prev => {
        const filtered = prev.filter((d: any) => d.source_table !== tbl)
        const skips = fields.map((f: any) => ({ action: 'skip', source_field: f.source_field, source_table: tbl }))
        return [...filtered, ...skips]
      })
    } else {
      // Re-including: remove forced skips so fields revert to defaults
      setDecisions(prev => prev.filter((d: any) => d.source_table !== tbl))
    }
  }

  function addColumnToTable(tbl: string) {
    const id = crypto.randomUUID()
    setAddedColIds(prev => ({ ...prev, [tbl]: [...(prev[tbl] ?? []), id] }))
    setFieldDecision({
      action: 'custom',
      source_field: `__added_${id}`,
      source_table: tbl,
      target_table: canonicalTables[0] ?? '',
      custom_column_name: '',
      data_type: 'VARCHAR(255)',
      is_new_table: false,
      new_table_name: '',
      new_table_pk: 'id',
      nullable: true,
    })
  }

  function removeAddedColumn(tbl: string, id: string) {
    setAddedColIds(prev => ({ ...prev, [tbl]: (prev[tbl] ?? []).filter((x: string) => x !== id) }))
    setDecisions((prev: any[]) => prev.filter((d: any) => d.source_field !== `__added_${id}`))
  }

  function syncNewTableDefs(defs: NewTableDef[]) {
    setNewTableDefs(defs)
    setDecisions(prev => {
      const filtered = prev.filter((d: any) => !String(d.source_table ?? '').startsWith('_new_table_'))
      const newDecisions: any[] = []
      for (const def of defs) {
        if (!def.table_name) continue
        for (const col of def.columns) {
          if (!col.col_name) continue
          newDecisions.push({
            action: 'custom',
            source_field: `__new_col_${col.col_name}`,
            source_table: `_new_table_${def.table_name}`,
            target_table: def.table_name,
            custom_column_name: col.col_name,
            data_type: col.data_type,
            is_new_table: true,
            new_table_name: def.table_name,
            new_table_pk: def.pk_field || 'id',
            nullable: col.nullable,
          })
        }
      }
      return [...filtered, ...newDecisions]
    })
  }

  function addNewTable() {
    syncNewTableDefs([...newTableDefs, { id: crypto.randomUUID(), table_name: '', pk_field: 'id', columns: [] }])
  }

  function updateNewTable(id: string, patch: Partial<NewTableDef>) {
    syncNewTableDefs(newTableDefs.map(t => t.id === id ? { ...t, ...patch } : t))
  }

  function removeNewTable(id: string) {
    syncNewTableDefs(newTableDefs.filter(t => t.id !== id))
  }

  return (
    <div className="space-y-6">
      {/* Summary counts */}
      <div className="grid grid-cols-3 gap-4">
        <Metric label="Low confidence" value={totalLowConf}      accent="amber" />
        <Metric label="Unmapped fields" value={totalUnmapped}    accent="red" />
        <Metric label="Unstructured candidates" value={totalUnstructured} accent="indigo" />
      </div>

      {/* ── Unstructured candidates section ──────────────────────────── */}
      {totalUnstructured > 0 && (
        <div className="space-y-3">
          <div className="flex items-center gap-2">
            <FileText size={14} className="text-indigo-500" />
            <p className="text-xs font-semibold text-slate-700 uppercase tracking-wider">
              Unstructured / Free-text Candidates
            </p>
            <span className="badge bg-indigo-100 text-indigo-700">{totalUnstructured}</span>
          </div>
          <p className="text-xs text-slate-500">
            These columns appear to contain free-text data. Decide whether to keep their current mapping
            or store as unstructured data in <code className="bg-slate-100 px-1 rounded">raw_metadata</code>.
          </p>

          {Object.entries(unstructuredByTable).map(([tbl, candidates]) => (
            <UnstructuredTableCard
              key={tbl}
              tableName={tbl}
              candidates={candidates}
              decisionMap={decisionMap}
              setFieldDecision={setFieldDecision}
            />
          ))}
        </div>
      )}

      {/* ── Unmapped fields section ───────────────────────────────────── */}
      {totalUnmapped > 0 && (
        <div className="space-y-3">
          <div className="flex items-center gap-2">
            <Search size={14} className="text-red-500" />
            <p className="text-xs font-semibold text-slate-700 uppercase tracking-wider">
              Unmapped Fields
            </p>
            <span className="badge bg-red-100 text-red-700">{totalUnmapped}</span>
          </div>
          <p className="text-xs text-slate-500">
            Toggle whether to include each source table. For included tables, create a new column,
            store in <code className="bg-slate-100 px-1 rounded">raw_metadata</code> JSONB, or skip each field.
          </p>

          {Object.entries(unmappedByTable).map(([tbl, fields]) => {
            const included = tableInclusion[tbl] ?? true
            const customCount = fields.filter((f: any) => decisionMap[`${tbl}.${f.source_field}`]?.action === 'custom').length
            return (
              <div key={tbl} className={`card overflow-hidden transition-opacity ${!included ? 'opacity-60' : ''}`}>
                {/* Table header with include/exclude toggle */}
                <div className="px-5 py-3 bg-slate-50 border-b border-slate-100 flex items-center gap-3">
                  <span className="font-semibold text-slate-700 text-sm">{tbl}</span>
                  <span className="text-xs text-slate-400">{fields.length} field{fields.length !== 1 ? 's' : ''}</span>
                  {customCount > 0 && included && (
                    <span className="badge bg-indigo-100 text-indigo-700">{customCount} new column{customCount > 1 ? 's' : ''}</span>
                  )}
                  {!included && (
                    <span className="badge bg-slate-200 text-slate-500">Excluded — all fields skipped</span>
                  )}
                  <div className="ml-auto flex items-center gap-2 shrink-0">
                    <span className="text-xs text-slate-500">{included ? 'Include table' : 'Excluded'}</span>
                    <div
                      className={`relative w-9 h-5 rounded-full transition-colors cursor-pointer ${included ? 'bg-indigo-600' : 'bg-slate-300'}`}
                      onClick={() => toggleTableInclusion(tbl)}
                    >
                      <div className={`absolute top-0.5 w-4 h-4 bg-white rounded-full shadow transition-transform ${included ? 'translate-x-4' : 'translate-x-0.5'}`} />
                    </div>
                  </div>
                </div>

                {/* Per-field rows — only rendered when table is included */}
                {included && (
                  <div>
                    {fields.map((f: any, i: number) => {
                      const key = `${tbl}.${f.source_field}`
                      const current = decisionMap[key]?.action ?? 'raw_metadata'

                      function selectCustom() {
                        setFieldDecision({
                          action: 'custom',
                          source_field: f.source_field,
                          source_table: tbl,
                          target_table: canonicalTables[0] ?? '',
                          custom_column_name: f.source_field.toLowerCase().replace(/\s+/g, '_'),
                          data_type: 'VARCHAR(255)',
                          is_new_table: false,
                          new_table_name: '',
                          new_table_pk: 'id',
                          nullable: true,
                        })
                      }

                      return (
                        <div key={i} className="border-b border-slate-50 last:border-b-0">
                          <div className="flex items-center gap-3 px-5 py-3">
                            <span className="font-mono text-xs text-slate-700 w-44 truncate shrink-0" title={f.source_field}>{f.source_field}</span>
                            <span className="text-xs text-slate-400 shrink-0">{f.data_type_hint ?? 'unknown'}</span>
                            <div className="flex-1" />
                            <div className="flex gap-1 shrink-0">
                              <ActionBtn
                                label="New column"
                                icon={<PlusCircle size={11} />}
                                active={current === 'custom'}
                                activeColor="bg-indigo-600"
                                onClick={selectCustom}
                              />
                              <ActionBtn
                                label="raw_metadata"
                                icon={<Archive size={11} />}
                                active={current === 'raw_metadata'}
                                activeColor="bg-slate-600"
                                onClick={() => setFieldDecision({ action: 'raw_metadata', source_field: f.source_field, source_table: tbl })}
                              />
                              <ActionBtn
                                label="Skip"
                                icon={<XCircle size={11} />}
                                active={current === 'skip'}
                                activeColor="bg-red-600"
                                onClick={() => setFieldDecision({ action: 'skip', source_field: f.source_field, source_table: tbl })}
                              />
                            </div>
                          </div>
                          {/* Custom DDL form — shown when "New column" is active */}
                          {current === 'custom' && decisionMap[key] && (
                            <div className="px-5 pb-4">
                              <CustomDDLForm
                                ddl={decisionMap[key]}
                                canonicalTables={canonicalTables}
                                onChange={patch => patchFieldDecision(tbl, f.source_field, patch)}
                              />
                            </div>
                          )}
                        </div>
                      )
                    })}

                    {/* Manually added columns for this table */}
                    {(addedColIds[tbl] ?? []).map((id: string) => {
                      const sfKey = `__added_${id}`
                      const dmKey = `${tbl}.${sfKey}`
                      return (
                        <div key={id} className="border-b border-slate-50 last:border-b-0 bg-indigo-50/30">
                          <div className="flex items-center gap-3 px-5 py-3">
                            <PlusCircle size={13} className="text-indigo-500 shrink-0" />
                            <span className="font-mono text-xs text-indigo-600 italic flex-1">new column (manually added)</span>
                            <button
                              type="button"
                              onClick={() => removeAddedColumn(tbl, id)}
                              className="text-slate-300 hover:text-red-400 transition-colors shrink-0"
                              title="Remove this column"
                            >
                              <XCircle size={14} />
                            </button>
                          </div>
                          {decisionMap[dmKey] && (
                            <div className="px-5 pb-4">
                              <CustomDDLForm
                                ddl={decisionMap[dmKey]}
                                canonicalTables={canonicalTables}
                                onChange={patch => patchFieldDecision(tbl, sfKey, patch)}
                              />
                            </div>
                          )}
                        </div>
                      )
                    })}

                    {/* Add column footer */}
                    <div className="px-5 py-3 border-t border-slate-100 bg-slate-50/50">
                      <button
                        type="button"
                        onClick={() => addColumnToTable(tbl)}
                        className="flex items-center gap-1.5 text-xs font-medium text-indigo-600 hover:text-indigo-800 transition-colors"
                      >
                        <PlusCircle size={12} /> Add column to {tbl}
                      </button>
                    </div>
                  </div>
                )}
              </div>
            )
          })}

          {/* ── Create brand-new tables ──────────────────────────────── */}
          <div className="space-y-3 pt-2 border-t border-slate-200 mt-4">
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-2">
                <Table2 size={14} className="text-green-600" />
                <p className="text-xs font-semibold text-slate-700 uppercase tracking-wider">
                  Create Brand-new Tables
                </p>
                {newTableDefs.length > 0 && (
                  <span className="badge bg-green-100 text-green-700">{newTableDefs.length}</span>
                )}
              </div>
              <button
                type="button"
                onClick={addNewTable}
                className="flex items-center gap-1.5 text-xs font-medium px-3 py-1.5 rounded-lg bg-green-600 text-white hover:bg-green-700 transition-colors"
              >
                <PlusCircle size={12} /> Add new table
              </button>
            </div>
            {newTableDefs.length === 0 && (
              <p className="text-xs text-slate-400 italic">
                Need a target table that doesn't exist yet? Click "Add new table" to define its schema.
              </p>
            )}
            {newTableDefs.map(def => (
              <NewTableBuilderCard
                key={def.id}
                def={def}
                onUpdate={patch => updateNewTable(def.id, patch)}
                onRemove={() => removeNewTable(def.id)}
              />
            ))}
          </div>
        </div>
      )}

      {/* ── Low-confidence mappings section ──────────────────────────── */}
      {totalLowConf > 0 && (
        <div className="space-y-3">
          <div className="flex items-center gap-2">
            <AlertTriangle size={14} className="text-amber-500" />
            <p className="text-xs font-semibold text-slate-700 uppercase tracking-wider">
              Low-confidence Mappings
            </p>
            <span className="badge bg-amber-100 text-amber-700">{totalLowConf}</span>
          </div>

          {[...Object.entries(tier1ByTable), ...Object.entries(tier2ByTable)].map(([tbl, mappings]) => (
            <div key={tbl} className="card overflow-hidden">
              <div className="px-5 py-3 bg-slate-50 border-b border-slate-100">
                <span className="font-semibold text-slate-700 text-sm">{tbl}</span>
                <span className="text-xs text-slate-400 ml-2">{mappings.length} mapping{mappings.length !== 1 ? 's' : ''}</span>
              </div>
              <div>
                {mappings.map((m: any, i: number) => {
                  const key = `${tbl}.${m.source_field}`
                  const current = decisionMap[key]?.action ?? 'accept'
                  const conf = m.confidence != null ? Math.round(m.confidence * 100) : null
                  return (
                    <div key={i} className="flex items-center gap-3 px-5 py-3 border-b border-slate-50 last:border-b-0 flex-wrap">
                      <span className="font-mono text-xs text-slate-700 w-36 truncate shrink-0">{m.source_field}</span>
                      <span className="text-slate-300 text-xs shrink-0">→</span>
                      <span className="font-mono text-xs text-indigo-700 flex-1 truncate">{m.suggested_target}</span>
                      {conf != null && (
                        <span className={`text-xs font-mono shrink-0 ${conf >= 80 ? 'text-green-600' : 'text-amber-600'}`}>{conf}%</span>
                      )}
                      <div className="flex gap-1 shrink-0">
                        {(['accept', 'reject'] as const).map(action => (
                          <button
                            key={action}
                            onClick={() => setFieldDecision({ action, source_field: m.source_field, source_table: tbl })}
                            className={`text-xs px-2.5 py-1 rounded-md font-medium transition-colors ${
                              current === action
                                ? action === 'accept' ? 'bg-green-600 text-white' : 'bg-red-600 text-white'
                                : 'bg-slate-100 text-slate-500 hover:bg-slate-200'
                            }`}
                          >
                            {action === 'accept' ? 'Accept' : 'Reject'}
                          </button>
                        ))}
                      </div>
                    </div>
                  )
                })}
              </div>
            </div>
          ))}
        </div>
      )}

      {totalLowConf === 0 && totalUnmapped === 0 && totalUnstructured === 0 && (
        <div className="rounded-lg bg-green-50 border border-green-200 px-4 py-3 text-sm text-green-700">
          No review items — all mappings passed with high confidence.
        </div>
      )}
    </div>
  )
}

// Unstructured table card with sample preview toggle
function UnstructuredTableCard({
  tableName, candidates, decisionMap, setFieldDecision,
}: {
  tableName: string
  candidates: any[]
  decisionMap: Record<string, any>
  setFieldDecision: (d: any) => void
}) {
  const [expandedField, setExpandedField] = useState<string | null>(null)

  return (
    <div className="card overflow-hidden">
      <div className="px-5 py-3 bg-indigo-50/60 border-b border-slateigo-100 flex items-center justify-between">
        <div className="flex items-center gap-2">
          <span className="font-semibold text-slate-700 text-sm">{tableName}</span>
          <span className="text-xs text-slate-400">{candidates.length} column{candidates.length !== 1 ? 's' : ''}</span>
        </div>
      </div>

      <div>
        {candidates.map((c: any, i: number) => {
          const key = `${tableName}.${c.source_field}`
          const isMatched = c.match_status === 'matched'
          const actions = isMatched
            ? [{ action: 'keep_mapping', label: 'Keep mapping', activeClass: 'bg-green-600 text-white' },
               { action: 'treat_as_unstructured', label: 'Treat as unstructured', activeClass: 'bg-indigo-600 text-white' }]
            : [{ action: 'treat_as_unstructured', label: 'Store as metadata', activeClass: 'bg-indigo-600 text-white' },
               { action: 'skip', label: 'Skip', activeClass: 'bg-slate-600 text-white' }]
          const defaultAction = isMatched ? 'keep_mapping' : 'treat_as_unstructured'
          const current = decisionMap[key]?.action ?? defaultAction
          const isExpanded = expandedField === c.source_field

          return (
            <div key={i} className="border-b border-slate-50 last:border-b-0">
              {/* Main row */}
              <div className="flex items-start gap-3 px-5 py-3">
                <FileText size={13} className="text-indigo-400 shrink-0 mt-0.5" />
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2 flex-wrap">
                    <span className="font-mono text-xs text-slate-800 font-semibold">{c.source_field}</span>
                    <span className="badge bg-slate-100 text-slate-500 text-xs">{c.data_type}</span>
                    {c.avg_char_length > 0 && (
                      <span className="text-xs text-slate-400">avg {c.avg_char_length} chars</span>
                    )}
                    {isMatched && c.matched_target && (
                      <span className="text-xs text-slate-400">
                        currently → <span className="font-mono text-indigo-600">{c.matched_target}</span>
                      </span>
                    )}
                    {!isMatched && (
                      <span className="badge bg-red-100 text-red-600 text-xs">unmapped</span>
                    )}
                  </div>
                  <p className="text-xs text-slate-400 mt-0.5 line-clamp-1">{c.reason}</p>
                </div>

                {/* Sample toggle */}
                {c.sample_values?.length > 0 && (
                  <button
                    onClick={() => setExpandedField(isExpanded ? null : c.source_field)}
                    className="shrink-0 flex items-center gap-1 text-xs text-slate-400 hover:text-slate-600 transition-colors"
                  >
                    {isExpanded ? <EyeOff size={12} /> : <Eye size={12} />}
                    {isExpanded ? 'Hide' : 'Sample'}
                  </button>
                )}

                {/* Decision buttons */}
                <div className="flex gap-1 shrink-0">
                  {actions.map(({ action, label, activeClass }) => (
                    <button
                      key={action}
                      onClick={() => setFieldDecision({ action, source_field: c.source_field, source_table: tableName })}
                      className={`text-xs px-2.5 py-1 rounded-md font-medium transition-colors ${
                        current === action ? activeClass : 'bg-slate-100 text-slate-500 hover:bg-slate-200'
                      }`}
                    >
                      {label}
                    </button>
                  ))}
                </div>
              </div>

              {/* Sample values panel */}
              {isExpanded && c.sample_values?.length > 0 && (
                <div className="mx-5 mb-3 rounded-lg bg-slate-50 border border-slate-200 overflow-hidden">
                  <div className="px-3 py-1.5 bg-slate-100 border-b border-slate-200">
                    <span className="text-xs font-semibold text-slate-500 uppercase tracking-wider">Sample values</span>
                  </div>
                  <div className="divide-y divide-slate-100">
                    {c.sample_values.map((sv: string, si: number) => (
                      <div key={si} className="px-3 py-2 text-xs text-slate-600 font-mono break-words leading-relaxed">
                        {sv || <span className="text-slate-300 italic">empty</span>}
                      </div>
                    ))}
                  </div>
                </div>
              )}
            </div>
          )
        })}
      </div>
    </div>
  )
}

// ── New Table Builder card ────────────────────────────────────────────────────

function NewTableBuilderCard({
  def,
  onUpdate,
  onRemove,
}: {
  def: NewTableDef
  onUpdate: (patch: Partial<NewTableDef>) => void
  onRemove: () => void
}) {
  function addColumn() {
    onUpdate({ columns: [...def.columns, { col_name: '', data_type: 'VARCHAR(255)', nullable: true }] })
  }
  function updateColumn(idx: number, patch: Partial<NewColDef>) {
    onUpdate({ columns: def.columns.map((c, i) => i === idx ? { ...c, ...patch } : c) })
  }
  function removeColumn(idx: number) {
    onUpdate({ columns: def.columns.filter((_, i) => i !== idx) })
  }

  const tableName = def.table_name || '…'
  const pk = def.pk_field || 'id'
  const colLines = def.columns
    .filter(c => c.col_name)
    .map(c => `,\n  ${c.col_name} ${c.data_type}${c.nullable ? '' : ' NOT NULL'}`)
    .join('')
  const sqlPreview = `CREATE TABLE plenum_cafm.${tableName} (\n  ${pk} UUID PRIMARY KEY DEFAULT gen_random_uuid()${colLines}\n);`

  return (
    <div className="card overflow-hidden border border-green-200">
      <div className="px-5 py-3 bg-green-50 border-b border-green-200 flex items-center justify-between">
        <div className="flex items-center gap-2">
          <Table2 size={14} className="text-green-600" />
          <span className="font-semibold text-slate-700 text-sm">
            {def.table_name || <span className="text-slate-400 italic font-normal">Unnamed table</span>}
          </span>
          <span className="badge bg-green-100 text-green-700 text-xs">new</span>
        </div>
        <button
          type="button"
          onClick={onRemove}
          className="text-slate-400 hover:text-red-500 transition-colors"
        >
          <XCircle size={16} />
        </button>
      </div>

      <div className="p-5 space-y-4">
        {/* Table name + PK */}
        <div className="grid grid-cols-2 gap-3">
          <div>
            <label className="label text-xs">Table name</label>
            <input
              className="input text-xs py-1.5 font-mono"
              value={def.table_name}
              onChange={e => onUpdate({ table_name: e.target.value })}
              placeholder="my_new_table"
            />
          </div>
          <div>
            <label className="label text-xs">Primary key column</label>
            <input
              className="input text-xs py-1.5 font-mono"
              value={def.pk_field}
              onChange={e => onUpdate({ pk_field: e.target.value })}
              placeholder="id"
            />
          </div>
        </div>

        {/* Columns */}
        <div className="space-y-2">
          <div className="flex items-center justify-between">
            <p className="text-xs font-semibold text-slate-600">Columns</p>
            <button
              type="button"
              onClick={addColumn}
              className="flex items-center gap-1 text-xs text-indigo-600 hover:text-indigo-800 font-medium transition-colors"
            >
              <PlusCircle size={11} /> Add column
            </button>
          </div>
          {def.columns.length === 0 && (
            <p className="text-xs text-slate-400 italic">No columns added yet. Click "Add column" to define the table schema.</p>
          )}
          {def.columns.map((col, idx) => (
            <div key={idx} className="flex items-center gap-2">
              <input
                className="input text-xs py-1.5 font-mono flex-1 min-w-0"
                value={col.col_name}
                onChange={e => updateColumn(idx, { col_name: e.target.value })}
                placeholder="column_name"
              />
              <select
                className="input text-xs py-1.5 font-mono w-36 shrink-0"
                value={col.data_type}
                onChange={e => updateColumn(idx, { data_type: e.target.value })}
              >
                {DATA_TYPES.map(t => <option key={t} value={t}>{t}</option>)}
              </select>
              <label className="flex items-center gap-1 text-xs text-slate-500 shrink-0 cursor-pointer">
                <input
                  type="checkbox"
                  checked={col.nullable}
                  onChange={e => updateColumn(idx, { nullable: e.target.checked })}
                  className="rounded"
                />
                NULL
              </label>
              <button
                type="button"
                onClick={() => removeColumn(idx)}
                className="text-slate-300 hover:text-red-400 transition-colors shrink-0"
              >
                <XCircle size={14} />
              </button>
            </div>
          ))}
        </div>

        {/* SQL preview */}
        {(def.table_name || def.columns.length > 0) && (
          <pre className="rounded-lg bg-white border border-green-200 px-3 py-2 font-mono text-xs text-slate-600 leading-relaxed overflow-auto">
            {sqlPreview}
          </pre>
        )}
      </div>
    </div>
  )
}

// ── Step 5: Hierarchy Detection ───────────────────────────────────────────────

function Step5Hierarchy({ payload }: { payload: Record<string, any> }) {
  const fkPreview: { source: string; target: string; type: string; confidence: number }[] = payload.fk_preview ?? []
  const cycles: string[] = payload.cycles ?? []

  return (
    <div className="space-y-4">
      <div className="grid grid-cols-3 gap-4">
        <Metric label="FK relationships" value={payload.total_fks ?? 0} accent="indigo" />
        <Metric label="Hierarchy depth" value={payload.hierarchy_depth ?? 0} accent="indigo" />
        <Metric label="Implicit hierarchies" value={payload.implicit_hierarchies_count ?? 0} accent="indigo" />
      </div>

      {cycles.length > 0 && (
        <div className="rounded-lg bg-amber-50 border border-amber-200 px-4 py-3">
          <p className="text-xs font-semibold text-amber-700 mb-1 flex items-center gap-1.5">
            <AlertTriangle size={13} /> Cycles detected
          </p>
          {cycles.map((c, i) => (
            <p key={i} className="text-xs font-mono text-amber-600">{c}</p>
          ))}
        </div>
      )}

      {fkPreview.length > 0 && (
        <div className="card overflow-hidden">
          <div className="px-5 py-3 bg-slate-50 border-b border-slate-100">
            <span className="text-xs font-semibold text-slate-600 uppercase tracking-wider flex items-center gap-1.5">
              <GitBranch size={13} /> FK relationships (preview)
            </span>
          </div>
          <div className="max-h-48 overflow-y-auto">
            {fkPreview.map((fk, i) => (
              <div key={i} className="flex items-center gap-3 px-5 py-2.5 border-b border-slate-50 last:border-b-0">
                <span className="font-mono text-xs text-slate-600 truncate flex-1">{fk.source}</span>
                <ArrowRight size={12} className="text-slate-300 shrink-0" />
                <span className="font-mono text-xs text-blue-600 truncate flex-1">{fk.target}</span>
                <span className="text-xs text-slate-400 shrink-0">{Math.round((fk.confidence ?? 0) * 100)}%</span>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  )
}

// ── Step 6: Verify Hierarchy (HITL gate) ──────────────────────────────────────
// Backend resumes via the /advance endpoint with { approved_foreign_keys, rejected_foreign_keys }

type FKDecision = 'approve' | 'reject'

function Step6VerifyHierarchy({
  payload,
  setDecisions,
}: {
  payload: Record<string, any>
  setDecisions: React.Dispatch<React.SetStateAction<any[]>>
}) {
  const fks: any[] = payload.detected_foreign_keys ?? []
  const implicitHierarchies: Record<string, any> = payload.implicit_hierarchies ?? {}
  const summary = payload.summary ?? {}

  const [fkDecisions, setFkDecisions] = useState<FKDecision[]>(
    fks.map(fk => (fk.confidence ?? 1) >= 0.7 ? 'approve' : 'reject')
  )
  const [expandedSources, setExpandedSources] = useState<Set<string>>(
    new Set(fks.map(fk => fk.source_table))
  )
  const [showImplicit, setShowImplicit] = useState(false)

  // Sync decisions up to parent so handleAdvance can pick them up
  function syncDecisions(nextDecisions: FKDecision[]) {
    const approved = fks.filter((_, i) => nextDecisions[i] === 'approve').map(fk => ({ ...fk, user_confirmed: true }))
    const rejected = fks.filter((_, i) => nextDecisions[i] === 'reject')
    setDecisions([{ approved_foreign_keys: approved, rejected_foreign_keys: rejected }])
  }

  function setFkDecision(idx: number, val: FKDecision) {
    setFkDecisions(prev => {
      const next = prev.map((d, i) => i === idx ? val : d)
      syncDecisions(next)
      return next
    })
  }

  // Initialise parent decisions on mount
  useState(() => { syncDecisions(fkDecisions) })

  const approvedCount = fkDecisions.filter(d => d === 'approve').length
  const rejectedCount = fkDecisions.filter(d => d === 'reject').length
  const lowConfCount  = fks.filter(fk => (fk.confidence ?? 1) < 0.7).length
  const implicitKeys  = Object.keys(implicitHierarchies)

  // Group FKs by source table
  const byTable: Record<string, { fk: any; idx: number }[]> = {}
  fks.forEach((fk, idx) => {
    byTable[fk.source_table] = byTable[fk.source_table] ?? []
    byTable[fk.source_table].push({ fk, idx })
  })

  function toggleTable(tbl: string) {
    setExpandedSources(prev => {
      const next = new Set(prev)
      next.has(tbl) ? next.delete(tbl) : next.add(tbl)
      return next
    })
  }

  return (
    <div className="space-y-4">
      {/* Summary metrics */}
      <div className="grid grid-cols-4 gap-3">
        <Metric label="FK relationships" value={fks.length}       accent="indigo" />
        <Metric label="Approved"         value={approvedCount}    accent="green"  />
        <Metric label="Rejected"         value={rejectedCount}    accent="red"    />
        <Metric label="Low confidence"   value={lowConfCount}     accent="amber"  />
      </div>

      {lowConfCount > 0 && (
        <div className="flex items-start gap-3 px-4 py-3 rounded-xl bg-amber-50 border border-amber-200">
          <AlertTriangle size={14} className="text-amber-500 shrink-0 mt-0.5" />
          <p className="text-xs text-amber-700">
            <strong>{lowConfCount} low-confidence</strong> FK{lowConfCount !== 1 ? 's' : ''} detected (below 70%). Review these carefully before confirming.
          </p>
        </div>
      )}

      {/* FK list grouped by source table */}
      {fks.length === 0 ? (
        <div className="card p-6 text-center text-sm text-slate-500">
          No foreign key relationships detected. Click "Confirm Hierarchy" to continue.
        </div>
      ) : (
        <div className="space-y-2">
          {Object.entries(byTable).map(([sourceTable, entries]) => {
            const isOpen = expandedSources.has(sourceTable)
            const tableApproved = entries.filter(e => fkDecisions[e.idx] === 'approve').length
            return (
              <div key={sourceTable} className="card overflow-hidden">
                <button
                  className="w-full flex items-center justify-between px-5 py-3 hover:bg-slate-50 transition-colors text-left"
                  onClick={() => toggleTable(sourceTable)}
                >
                  <div className="flex items-center gap-3">
                    <span className="font-semibold text-slate-800 text-sm font-mono">{sourceTable}</span>
                    <span className="badge bg-slate-100 text-slate-600">{entries.length} FK{entries.length > 1 ? 's' : ''}</span>
                    {tableApproved === entries.length && (
                      <span className="badge bg-green-100 text-green-700">All approved</span>
                    )}
                  </div>
                  {isOpen ? <ChevronUp size={15} className="text-slate-400" /> : <ChevronDown size={15} className="text-slate-400" />}
                </button>

                {isOpen && (
                  <div className="border-t border-slate-100 divide-y divide-slate-100">
                    {entries.map(({ fk, idx }) => {
                      const dec      = fkDecisions[idx] ?? 'approve'
                      const conf     = fk.confidence ?? 1
                      const confPct  = Math.round(conf * 100)
                      const isSelf   = fk.source_table === fk.target_table
                      const isLow    = conf < 0.7
                      const confColor = confPct >= 90 ? 'bg-green-100 text-green-700' : confPct >= 70 ? 'bg-amber-100 text-amber-700' : 'bg-red-100 text-red-700'
                      return (
                        <div
                          key={idx}
                          className={`px-5 py-3.5 transition-colors ${dec === 'approve' ? 'bg-green-50/30' : 'bg-red-50/30'} ${isSelf ? 'border-l-2 border-violet-400' : ''} ${isLow ? 'border-l-2 border-amber-400' : ''}`}
                        >
                          <div className="flex items-start gap-4">
                            <div className="flex-1 min-w-0">
                              <div className="flex items-center gap-2 flex-wrap mb-1">
                                {fk.relationship_type && (
                                  <span className="badge text-xs font-mono bg-slate-100 text-slate-600 uppercase">{fk.relationship_type}</span>
                                )}
                                {isSelf && (
                                  <span className="badge text-xs font-mono bg-violet-100 text-violet-700 uppercase">SELF-REF</span>
                                )}
                                <code className="text-xs font-mono font-semibold text-slate-700 bg-slate-100 px-1.5 py-0.5 rounded">
                                  {fk.source_column}
                                </code>
                                <ArrowRight size={11} className="text-slate-400 shrink-0" />
                                <span className="font-mono text-xs font-semibold text-indigo-700">{fk.target_table}</span>
                                {fk.target_column && (
                                  <code className="text-xs font-mono text-indigo-600 bg-indigo-50 px-1.5 py-0.5 rounded">
                                    {fk.target_column}
                                  </code>
                                )}
                                <span className={`badge text-xs font-mono ${confColor}`}>{confPct}%</span>
                              </div>
                              {fk.reasoning && (
                                <p className="text-xs text-slate-400 mt-0.5">{fk.reasoning}</p>
                              )}
                              {isSelf && (
                                <div className="flex items-center gap-1.5 text-xs text-violet-700 bg-violet-50 px-2.5 py-1 rounded-lg mt-1 w-fit">
                                  <RotateCcw size={10} /> Self-referential — parent/child within same table
                                </div>
                              )}
                              {isLow && !isSelf && (
                                <div className="flex items-center gap-1.5 text-xs text-amber-700 bg-amber-50 px-2.5 py-1 rounded-lg mt-1 w-fit">
                                  <AlertTriangle size={10} /> Low confidence — review carefully
                                </div>
                              )}
                            </div>
                            <div className="flex gap-1.5 shrink-0">
                              <button
                                onClick={() => setFkDecision(idx, 'approve')}
                                className={`flex items-center gap-1 text-xs font-medium px-2.5 py-1.5 rounded-lg transition-colors ${dec === 'approve' ? 'bg-green-600 text-white' : 'bg-slate-100 text-slate-600 hover:bg-slate-200'}`}
                              >
                                <CheckCircle size={11} /> Approve
                              </button>
                              <button
                                onClick={() => setFkDecision(idx, 'reject')}
                                className={`flex items-center gap-1 text-xs font-medium px-2.5 py-1.5 rounded-lg transition-colors ${dec === 'reject' ? 'bg-red-600 text-white' : 'bg-slate-100 text-slate-600 hover:bg-slate-200'}`}
                              >
                                <XCircle size={11} /> Reject
                              </button>
                            </div>
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
      )}

      {/* Implicit hierarchies */}
      {implicitKeys.length > 0 && (
        <div className="card overflow-hidden">
          <button
            className="w-full flex items-center justify-between px-5 py-3.5 hover:bg-slate-50 transition-colors text-left"
            onClick={() => setShowImplicit(v => !v)}
          >
            <div className="flex items-center gap-2">
              <Link size={14} className="text-violet-500" />
              <span className="text-sm font-semibold text-slate-700">Implicit / code-based hierarchies</span>
              <span className="badge bg-violet-100 text-violet-700">{implicitKeys.length}</span>
            </div>
            {showImplicit ? <ChevronUp size={15} className="text-slate-400" /> : <ChevronDown size={15} className="text-slate-400" />}
          </button>
          {showImplicit && (
            <div className="border-t border-slate-100 divide-y divide-slate-100">
              {implicitKeys.map(key => (
                <div key={key} className="px-5 py-3">
                  <span className="badge bg-violet-100 text-violet-700 font-mono text-xs">{key}</span>
                </div>
              ))}
            </div>
          )}
        </div>
      )}

      {/* Summary stats from payload */}
      {summary.total_fks != null && (
        <div className="flex items-center gap-3 px-4 py-3 rounded-xl bg-slate-50 border border-slate-200">
          <Key size={14} className="text-slate-500 shrink-0" />
          <p className="text-xs text-slate-600">
            <strong>{summary.total_fks}</strong> total FKs detected —{' '}
            <strong>{summary.canonical_backed_fks ?? 0}</strong> canonically backed ·{' '}
            <strong>{summary.junction_table_count ?? 0}</strong> junction tables ·{' '}
            <strong>{summary.isolated_table_count ?? 0}</strong> isolated tables
          </p>
        </div>
      )}
    </div>
  )
}

// ── Step 7: Output Generation ─────────────────────────────────────────────────

function Step7Output({ payload }: { payload: Record<string, any> }) {
  const coverage = payload.mapping_coverage_pct ?? 0

  return (
    <div className="space-y-4">
      <div className="grid grid-cols-2 gap-4">
        <Metric label="Canonical fields" value={payload.canonical_fields_count ?? 0} accent="green" />
        <Metric label="Source fields" value={payload.total_source_fields ?? 0} accent="indigo" />
        <Metric label="T1 auto-mapped" value={payload.tier1_auto_mapped ?? 0} accent="indigo" />
        <Metric label="T2 auto-mapped" value={payload.tier2_auto_mapped ?? 0} accent="indigo" />
      </div>

      <div className="card p-4">
        <div className="flex items-center justify-between mb-2">
          <span className="text-sm font-semibold text-slate-700 flex items-center gap-1.5">
            <FileBarChart size={14} className="text-slate-500" /> Mapping coverage
          </span>
          <span className="text-sm font-bold text-indigo-600">{coverage.toFixed(1)}%</span>
        </div>
        <div className="w-full bg-slate-100 rounded-full h-3">
          <div
            className={`h-3 rounded-full transition-all ${
              coverage >= 80 ? 'bg-green-500' : coverage >= 60 ? 'bg-amber-500' : 'bg-red-500'
            }`}
            style={{ width: `${Math.min(coverage, 100)}%` }}
          />
        </div>
        <div className="flex justify-between mt-1 text-xs text-slate-400">
          <span>{payload.tier2_flagged ?? 0} flagged for review</span>
          <span>{payload.unmappable ?? 0} unmappable</span>
        </div>
      </div>

      <div className="rounded-lg bg-blue-50 border border-blue-200 px-4 py-3 text-sm text-blue-700">
        Output configuration generated. Click <strong>Next Node</strong> to write the final schema mapping to the database.
      </div>
    </div>
  )
}

// ── Generic fallback ──────────────────────────────────────────────────────────

function GenericStepView({ payload }: { payload: Record<string, any> }) {
  return (
    <div className="card p-4">
      <pre className="text-xs text-slate-600 whitespace-pre-wrap overflow-auto max-h-64">
        {JSON.stringify(payload, null, 2)}
      </pre>
    </div>
  )
}

// ── Shared helpers ────────────────────────────────────────────────────────────

function ActionBtn({ label, icon, active, activeColor, onClick }: {
  label: string; icon: React.ReactNode; active: boolean; activeColor: string; onClick: () => void
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={`flex items-center gap-1 text-xs font-medium px-2.5 py-1.5 rounded-lg transition-colors ${
        active ? `${activeColor} text-white` : 'bg-slate-100 text-slate-600 hover:bg-slate-200'
      }`}
    >
      {icon}{label}
    </button>
  )
}

const DATA_TYPES = [
  'VARCHAR(255)', 'VARCHAR(100)', 'VARCHAR(50)',
  'TEXT', 'INTEGER', 'BIGINT', 'DECIMAL(10,2)',
  'BOOLEAN', 'TIMESTAMPTZ', 'DATE', 'JSONB', 'UUID',
]

interface CustomDDLState {
  target_table: string
  custom_column_name: string
  data_type: string
  is_new_table: boolean
  new_table_name: string
  new_table_pk: string
  nullable: boolean
}

function CustomDDLForm({ ddl, canonicalTables, onChange }: {
  ddl: CustomDDLState & Record<string, any>
  canonicalTables: string[]
  onChange: (patch: Partial<CustomDDLState>) => void
}) {
  const effectiveTable = ddl.is_new_table ? (ddl.new_table_name || '…') : (ddl.target_table || '…')
  const col = ddl.custom_column_name || '…'
  const dt = ddl.data_type || 'VARCHAR(255)'
  const nullStr = ddl.nullable ? '' : ' NOT NULL'

  const sqlPreview = ddl.is_new_table
    ? `CREATE TABLE plenum_cafm.${effectiveTable} (\n  ${ddl.new_table_pk || 'id'} UUID PRIMARY KEY DEFAULT gen_random_uuid(),\n  ${col} ${dt}${nullStr}\n);`
    : `ALTER TABLE plenum_cafm.${effectiveTable}\n  ADD COLUMN ${col} ${dt}${nullStr};`

  return (
    <div className="rounded-xl bg-indigo-50 border border-indigo-200 p-4 space-y-3 mt-1">
      <div className="flex items-center justify-between">
        <p className="text-xs font-semibold text-indigo-700 flex items-center gap-1.5">
          <PlusCircle size={12} />
          Column definition
        </p>
        {/* New table toggle */}
        <label className="flex items-center gap-2 cursor-pointer">
          <span className="text-xs text-slate-600">{ddl.is_new_table ? 'New table' : 'Existing table'}</span>
          <div
            className={`relative w-9 h-5 rounded-full transition-colors cursor-pointer ${ddl.is_new_table ? 'bg-indigo-600' : 'bg-slate-300'}`}
            onClick={() => onChange({ is_new_table: !ddl.is_new_table })}
          >
            <div className={`absolute top-0.5 w-4 h-4 bg-white rounded-full shadow transition-transform ${ddl.is_new_table ? 'translate-x-4' : 'translate-x-0.5'}`} />
          </div>
        </label>
      </div>

      <div className="grid grid-cols-2 gap-3">
        {/* Target table / New table name */}
        {ddl.is_new_table ? (
          <div className="col-span-2 grid grid-cols-2 gap-3">
            <div>
              <label className="label text-xs">New table name</label>
              <input
                className="input text-xs py-1.5 font-mono"
                value={ddl.new_table_name}
                onChange={e => onChange({ new_table_name: e.target.value })}
                placeholder="my_new_table"
              />
            </div>
            <div>
              <label className="label text-xs">Primary key column</label>
              <input
                className="input text-xs py-1.5 font-mono"
                value={ddl.new_table_pk}
                onChange={e => onChange({ new_table_pk: e.target.value })}
                placeholder="id"
              />
            </div>
          </div>
        ) : (
          <div className="col-span-2">
            <label className="label text-xs">Target table</label>
            {canonicalTables.length > 0 ? (
              <select
                className="input text-xs py-1.5"
                value={ddl.target_table}
                onChange={e => onChange({ target_table: e.target.value })}
              >
                {canonicalTables.map(t => <option key={t} value={t}>{t}</option>)}
              </select>
            ) : (
              <input
                className="input text-xs py-1.5 font-mono"
                value={ddl.target_table}
                onChange={e => onChange({ target_table: e.target.value })}
                placeholder="assets"
              />
            )}
          </div>
        )}

        {/* Column name */}
        <div>
          <label className="label text-xs">Column name</label>
          <input
            className="input text-xs py-1.5 font-mono"
            value={ddl.custom_column_name}
            onChange={e => onChange({ custom_column_name: e.target.value })}
            placeholder="my_column"
          />
        </div>

        {/* Data type */}
        <div>
          <label className="label text-xs">Data type</label>
          <select
            className="input text-xs py-1.5 font-mono"
            value={ddl.data_type}
            onChange={e => onChange({ data_type: e.target.value })}
          >
            {DATA_TYPES.map(t => <option key={t} value={t}>{t}</option>)}
          </select>
        </div>
      </div>

      {/* Nullable toggle */}
      <label className="flex items-center gap-3 cursor-pointer">
        <div
          className={`relative w-9 h-5 rounded-full transition-colors ${ddl.nullable ? 'bg-indigo-600' : 'bg-slate-300'}`}
          onClick={() => onChange({ nullable: !ddl.nullable })}
        >
          <div className={`absolute top-0.5 w-4 h-4 bg-white rounded-full shadow transition-transform ${ddl.nullable ? 'translate-x-4' : 'translate-x-0.5'}`} />
        </div>
        <span className="text-xs font-medium text-slate-700">Nullable</span>
      </label>

      {/* SQL preview */}
      <pre className="rounded-lg bg-white border border-indigo-200 px-3 py-2 font-mono text-xs text-slate-600 leading-relaxed overflow-auto">
        {sqlPreview}
      </pre>
    </div>
  )
}

// ── Shared: Metric (identical to StepPause.tsx) ───────────────────────────────

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
