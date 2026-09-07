import { useState } from 'react'
import { submitSchemaGateFieldMapping } from '../../../api'
import type { SchemaGate1Payload, SchemaLowConfidenceItem, SchemaUnmappedItem } from '../../../types'
import type { SchemaFieldMappingDecision } from '../../../api'
import {
  CheckCircle, XCircle, Edit3, AlertTriangle, Archive,
  ChevronDown, ChevronUp, PlusCircle, Trash2, Table2,
  ArrowRight, Database, ToggleLeft, ToggleRight, Wand2,
  Eye, EyeOff,
} from 'lucide-react'

interface Props {
  sessionId: string
  payload: SchemaGate1Payload
  onSubmitted: () => void
}

// ── Types ─────────────────────────────────────────────────────────────────────
type LowConfAction = 'accept' | 'reject' | 'override'
type UnmappedAction = 'custom' | 'raw_metadata' | 'skip'

interface LowConfDecision {
  action: LowConfAction
  overrideTarget: string
}

interface CustomDDL {
  source_field: string
  target_table: string
  custom_column_name: string
  data_type: string
  is_new_table: boolean
  new_table_name: string
  new_table_pk: string
  nullable: boolean
}

interface UnmappedDecision {
  action: UnmappedAction
  source_field: string
  custom: CustomDDL | null
}

interface NewTableColumn {
  column_name: string
  data_type: string
  nullable: boolean
}

interface NewTableDef {
  id: string
  table_name: string
  pk_col: string
  columns: NewTableColumn[]
}

const DATA_TYPES = [
  'VARCHAR(255)', 'VARCHAR(100)', 'VARCHAR(50)',
  'TEXT', 'INTEGER', 'BIGINT', 'DECIMAL(10,2)',
  'BOOLEAN', 'TIMESTAMPTZ', 'DATE', 'JSONB', 'UUID',
]

function smartMatchCanonicalTable(sourceTable: string, canonicalTables: string[]): string {
  if (!sourceTable || canonicalTables.length === 0) return ''
  const norm = (s: string) => s.toLowerCase().replace(/[^a-z0-9]/g, '')
  const src = norm(sourceTable)
  // Exact normalized match only — substring matching causes false positives
  // e.g. Fiix "Account" would incorrectly match canonical "accounts"
  return canonicalTables.find(t => norm(t) === src) ?? ''
}

function emptyCustomDDL(sourceField: string, canonicalTables: string[], sourceTable?: string): CustomDDL {
  return {
    source_field: sourceField,
    target_table: sourceTable ? smartMatchCanonicalTable(sourceTable, canonicalTables) : '',
    custom_column_name: sourceField.toLowerCase().replace(/\s+/g, '_'),
    data_type: 'VARCHAR(255)',
    is_new_table: false,
    new_table_name: '',
    new_table_pk: 'id',
    nullable: true,
  }
}

function emptyNewTable(): NewTableDef {
  return {
    id: `nt_${Date.now()}_${Math.random().toString(36).slice(2, 6)}`,
    table_name: '',
    pk_col: 'id',
    columns: [{ column_name: '', data_type: 'VARCHAR(255)', nullable: true }],
  }
}

// ─────────────────────────────────────────────────────────────────────────────

export default function SchemaGateFieldMapping({ sessionId, payload, onSubmitted }: Props) {
  const canonicalTables = payload.existing_canonical_tables ?? []

  // ── Build table-grouped low-confidence items ──────────────────────────────
  const lowConfByTable: Record<string, SchemaLowConfidenceItem[]> = {}
  for (const [table, items] of Object.entries(payload.low_confidence_tier1 ?? {})) {
    lowConfByTable[table] = [...(lowConfByTable[table] ?? []), ...items]
  }
  for (const [table, items] of Object.entries(payload.low_confidence_tier2 ?? {})) {
    lowConfByTable[table] = [...(lowConfByTable[table] ?? []), ...items]
  }
  const lowConfTables = Object.keys(lowConfByTable)

  // ── Build table-grouped unmapped items ────────────────────────────────────
  const unmappedByTableInit: Record<string, SchemaUnmappedItem[]> = payload.unmapped_fields ?? {}
  const unmappedTables = Object.keys(unmappedByTableInit)

  // ── All source tables (union) ─────────────────────────────────────────────
  const allSourceTables = [...new Set([...lowConfTables, ...unmappedTables])]

  // ── Initial states ────────────────────────────────────────────────────────
  function buildInitLowConf(): Record<string, LowConfDecision[]> {
    const out: Record<string, LowConfDecision[]> = {}
    for (const [table, items] of Object.entries(lowConfByTable)) {
      out[table] = items.map(item => ({
        action: 'accept' as LowConfAction,
        overrideTarget: String(item.suggested_target ?? ''),
      }))
    }
    return out
  }

  function buildInitUnmapped(): Record<string, UnmappedDecision[]> {
    const out: Record<string, UnmappedDecision[]> = {}
    for (const [table, items] of Object.entries(unmappedByTableInit)) {
      out[table] = items.map(item => ({
        action: 'raw_metadata' as UnmappedAction,
        source_field: String(item.source_field ?? ''),
        custom: null,
      }))
    }
    return out
  }

  const [lowConfDecisions, setLowConfDecisions] = useState<Record<string, LowConfDecision[]>>(buildInitLowConf)
  const [unmappedDecisions, setUnmappedDecisions] = useState<Record<string, UnmappedDecision[]>>(buildInitUnmapped)
  const [tableInclusion, setTableInclusion] = useState<Record<string, boolean>>(() => {
    const init: Record<string, boolean> = {}
    for (const t of allSourceTables) init[t] = true
    return init
  })
  const [expandedLowConf, setExpandedLowConf] = useState<Set<string>>(new Set(lowConfTables))
  const [expandedUnmapped, setExpandedUnmapped] = useState<Set<string>>(new Set(unmappedTables))
  const [expandedTableCols, setExpandedTableCols] = useState<Set<string>>(new Set())
  const [newTableDefs, setNewTableDefs] = useState<NewTableDef[]>([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [activeTab, setActiveTab] = useState<'flagged' | 'unmapped' | 'tables'>('unmapped')

  // ── New-table helpers ─────────────────────────────────────────────────────
  function addNewTable() { setNewTableDefs(prev => [...prev, emptyNewTable()]) }
  function removeNewTable(id: string) { setNewTableDefs(prev => prev.filter(t => t.id !== id)) }
  function patchNewTable(id: string, patch: Partial<NewTableDef>) {
    setNewTableDefs(prev => prev.map(t => t.id === id ? { ...t, ...patch } : t))
  }
  function addNewTableColumn(id: string) {
    setNewTableDefs(prev => prev.map(t =>
      t.id === id
        ? { ...t, columns: [...t.columns, { column_name: '', data_type: 'VARCHAR(255)', nullable: true }] }
        : t
    ))
  }
  function updateNewTableColumn(id: string, ci: number, patch: Partial<NewTableColumn>) {
    setNewTableDefs(prev => prev.map(t => {
      if (t.id !== id) return t
      return { ...t, columns: t.columns.map((c, i) => i === ci ? { ...c, ...patch } : c) }
    }))
  }
  function removeNewTableColumn(id: string, ci: number) {
    setNewTableDefs(prev => prev.map(t => {
      if (t.id !== id) return t
      return { ...t, columns: t.columns.filter((_, i) => i !== ci) }
    }))
  }

  // ── Low-conf helpers ──────────────────────────────────────────────────────
  function setLowConfAction(table: string, idx: number, action: LowConfAction) {
    setLowConfDecisions(prev => {
      const rows = [...(prev[table] ?? [])]
      rows[idx] = { ...rows[idx], action }
      return { ...prev, [table]: rows }
    })
  }
  function setOverrideTarget(table: string, idx: number, val: string) {
    setLowConfDecisions(prev => {
      const rows = [...(prev[table] ?? [])]
      rows[idx] = { ...rows[idx], overrideTarget: val }
      return { ...prev, [table]: rows }
    })
  }

  // ── Unmapped helpers ──────────────────────────────────────────────────────
  function setUnmappedAction(table: string, idx: number, action: UnmappedAction) {
    setUnmappedDecisions(prev => {
      const rows = [...(prev[table] ?? [])]
      const row = rows[idx]
      rows[idx] = {
        ...row,
        action,
        custom: action === 'custom'
          ? (row.custom ?? emptyCustomDDL(row.source_field, canonicalTables, table))
          : null,
      }
      return { ...prev, [table]: rows }
    })
  }

  function updateCustom(table: string, idx: number, patch: Partial<CustomDDL>) {
    setUnmappedDecisions(prev => {
      const rows = [...(prev[table] ?? [])]
      const row = rows[idx]
      rows[idx] = {
        ...row,
        custom: { ...(row.custom ?? emptyCustomDDL(row.source_field, canonicalTables, table)), ...patch },
      }
      return { ...prev, [table]: rows }
    })
  }

  // ── Add column to any source table ───────────────────────────────────────
  function addExtraColumn(table: string) {
    setUnmappedDecisions(prev => ({
      ...prev,
      [table]: [
        ...(prev[table] ?? []),
        { action: 'custom', source_field: '', custom: emptyCustomDDL('', canonicalTables, table) },
      ],
    }))
    setExpandedUnmapped(prev => new Set([...prev, table]))
    setExpandedTableCols(prev => new Set([...prev, table]))
  }

  function removeRow(table: string, idx: number) {
    setUnmappedDecisions(prev => {
      const rows = [...(prev[table] ?? [])]
      rows.splice(idx, 1)
      return { ...prev, [table]: rows }
    })
  }

  function setRowSourceField(table: string, idx: number, val: string) {
    setUnmappedDecisions(prev => {
      const rows = [...(prev[table] ?? [])]
      rows[idx] = { ...rows[idx], source_field: val }
      if (rows[idx].custom) {
        rows[idx] = { ...rows[idx], custom: { ...rows[idx].custom!, source_field: val } }
      }
      return { ...prev, [table]: rows }
    })
  }

  // ── Add an extra column row to EVERY included source table ──────────────
  function addColumnToAllTables() {
    for (const table of allSourceTables) {
      if (tableInclusion[table] !== false) {
        addExtraColumn(table)
      }
    }
    setActiveTab('unmapped')
  }

  // ── Auto-select "Add column" for ALL unmapped fields ─────────────────────
  function autoSelectAddColumn() {
    setUnmappedDecisions(prev => {
      const next = { ...prev }
      for (const table of [...unmappedTables, ...Object.keys(prev)]) {
        if (!tableInclusion[table] && tableInclusion[table] !== undefined) continue
        next[table] = (prev[table] ?? []).map(row => ({
          ...row,
          action: 'custom' as UnmappedAction,
          custom: row.custom ?? emptyCustomDDL(row.source_field, canonicalTables, table),
        }))
      }
      return next
    })
  }

  // ── Table include/exclude toggle ─────────────────────────────────────────
  function toggleTable(table: string) {
    setTableInclusion(prev => ({ ...prev, [table]: !prev[table] }))
  }

  // ── Submit ────────────────────────────────────────────────────────────────
  async function handleSubmit() {
    // Pre-validate: custom columns must have a target table selected
    for (const [table, rows] of Object.entries(unmappedDecisions)) {
      if (tableInclusion[table] === false) continue
      for (const row of rows) {
        if (row.action === 'custom' && row.custom && !row.custom.is_new_table && !row.custom.target_table.trim()) {
          setError(`Table "${table}": a custom column has no target table selected. Please choose a target table for every custom column.`)
          return
        }
      }
    }
    setLoading(true)
    setError(null)
    try {
      const decisions: Array<Record<string, any>> = []

      // Low-confidence decisions (skip excluded tables)
      for (const [table, items] of Object.entries(lowConfByTable)) {
        const included = tableInclusion[table] !== false
        items.forEach((item, idx) => {
          const d = lowConfDecisions[table]?.[idx]
          const action = included ? (d?.action ?? 'accept') : 'reject'
          const suggestedTarget = String(item.suggested_target ?? '')
          const dec: Record<string, any> = {
            action,
            source_field: item.source_field,
            source_table: table,
          }
          if (action === 'accept') dec.target_field = suggestedTarget
          else if (action === 'override') dec.target_field = d?.overrideTarget ?? suggestedTarget
          decisions.push(dec)
        })
      }

      // Unmapped + extra columns (skip excluded tables)
      const allUnmappedTables = new Set([...unmappedTables, ...Object.keys(unmappedDecisions)])
      for (const table of allUnmappedTables) {
        const included = tableInclusion[table] !== false
        const rows = unmappedDecisions[table] ?? []
        for (const row of rows) {
          const action = included ? row.action : 'skip'
          if (action === 'custom' && row.custom) {
            const c = row.custom
            const effectiveTable = c.is_new_table ? c.new_table_name : c.target_table
            decisions.push({
              action: 'custom',
              source_field: row.source_field,
              source_table: table,
              target_table: effectiveTable,
              custom_column_name: c.custom_column_name,
              data_type: c.data_type,
              is_new_table: c.is_new_table,
              new_table_pk: c.is_new_table ? c.new_table_pk : undefined,
              nullable: c.nullable,
            })
          } else {
            decisions.push({
              action,
              source_field: row.source_field,
              source_table: table,
            })
          }
        }
      }

      // New table definitions (always included regardless of table exclusions)
      for (const nt of newTableDefs) {
        if (!nt.table_name.trim()) continue
        for (const col of nt.columns) {
          if (!col.column_name.trim()) continue
          decisions.push({
            action: 'custom',
            source_field: col.column_name,
            source_table: `_new_table_${nt.table_name}`,
            target_table: nt.table_name,
            custom_column_name: col.column_name,
            data_type: col.data_type,
            is_new_table: true,
            new_table_pk: nt.pk_col || 'id',
            nullable: col.nullable,
          })
        }
      }

      await submitSchemaGateFieldMapping(sessionId, decisions as SchemaFieldMappingDecision[])
      onSubmitted()
    } catch (err: any) {
      setError(err.message ?? 'Failed to submit decisions')
      setLoading(false)
    }
  }

  const totalLowConf = lowConfTables.reduce((s, t) => s + (lowConfByTable[t]?.length ?? 0), 0)
  const totalUnmapped = unmappedTables.reduce((s, t) => s + (unmappedByTableInit[t]?.length ?? 0), 0)
  const excludedCount = allSourceTables.filter(t => tableInclusion[t] === false).length
  const includedCount = allSourceTables.length - excludedCount

  // ── Render ────────────────────────────────────────────────────────────────
  return (
    <div className="max-w-4xl">
      {/* Header */}
      <div className="flex items-start gap-4 mb-6">
        <div className="w-10 h-10 rounded-xl bg-amber-100 flex items-center justify-center shrink-0">
          <Edit3 size={20} className="text-amber-600" />
        </div>
        <div>
          <h2 className="text-lg font-bold text-slate-900">Gate 1 — Field Mapping Review</h2>
          <p className="text-sm text-slate-500 mt-0.5">
            Review field mappings, include/exclude source tables, and define custom columns.
          </p>
        </div>
      </div>

      {/* Counters */}
      <div className="grid grid-cols-4 gap-3 mb-5">
        <div className="card p-3 bg-amber-50 flex items-center gap-3">
          <AlertTriangle size={16} className="text-amber-600 shrink-0" />
          <div>
            <div className="text-lg font-bold font-mono text-slate-800">{totalLowConf}</div>
            <div className="text-xs text-slate-500">Low-confidence</div>
          </div>
        </div>
        <div className="card p-3 bg-red-50 flex items-center gap-3">
          <XCircle size={16} className="text-red-500 shrink-0" />
          <div>
            <div className="text-lg font-bold font-mono text-slate-800">{totalUnmapped}</div>
            <div className="text-xs text-slate-500">Unmapped</div>
          </div>
        </div>
        <div className="card p-3 bg-green-50 flex items-center gap-3">
          <Eye size={16} className="text-green-600 shrink-0" />
          <div>
            <div className="text-lg font-bold font-mono text-slate-800">{includedCount}</div>
            <div className="text-xs text-slate-500">Tables included</div>
          </div>
        </div>
        <div className="card p-3 bg-slate-50 flex items-center gap-3">
          <EyeOff size={16} className="text-slate-400 shrink-0" />
          <div>
            <div className="text-lg font-bold font-mono text-slate-800">{excludedCount}</div>
            <div className="text-xs text-slate-500">Tables excluded</div>
          </div>
        </div>
      </div>

      {/* Tab switcher */}
      <div className="flex gap-1 mb-5 border-b border-slate-200">
        {[
          { key: 'flagged' as const, label: `Low-confidence (${totalLowConf})` },
          { key: 'unmapped' as const, label: `Unmapped (${totalUnmapped})` },
          { key: 'tables' as const, label: `Source tables (${allSourceTables.length})` },
        ].map(tab => (
          <button
            key={tab.key}
            onClick={() => setActiveTab(tab.key)}
            className={`px-4 py-2 text-sm font-medium border-b-2 -mb-px transition-colors ${
              activeTab === tab.key
                ? 'border-indigo-500 text-indigo-600'
                : 'border-transparent text-slate-500 hover:text-slate-700'
            }`}
          >
            {tab.label}
            {tab.key === 'tables' && excludedCount > 0 && (
              <span className="ml-1.5 badge bg-amber-100 text-amber-700 text-xs">{excludedCount} off</span>
            )}
          </button>
        ))}
      </div>

      {/* ── LOW-CONFIDENCE TAB ───────────────────────────────────────────────── */}
      {activeTab === 'flagged' && (
        <>
          {lowConfTables.length === 0 ? (
            <div className="card p-6 text-center text-slate-500 text-sm mb-6">
              No low-confidence mappings to review.
            </div>
          ) : (
            <div className="space-y-3 mb-6">
              {lowConfTables.map(table => {
                const items = lowConfByTable[table] ?? []
                const isOpen = expandedLowConf.has(table)
                const isIncluded = tableInclusion[table] !== false
                const allAccepted = lowConfDecisions[table]?.every(d => d.action === 'accept')

                return (
                  <div key={table} className={`card overflow-hidden ${!isIncluded ? 'opacity-50' : ''}`}>
                    <div className="flex items-center justify-between px-5 py-3.5 hover:bg-slate-50 transition-colors">
                      <button
                        className="flex items-center gap-3 flex-1 text-left"
                        onClick={() => setExpandedLowConf(prev => {
                          const n = new Set(prev); n.has(table) ? n.delete(table) : n.add(table); return n
                        })}
                      >
                        <span className={`font-semibold text-sm font-mono ${isIncluded ? 'text-slate-800' : 'text-slate-400 line-through'}`}>{table}</span>
                        <span className="badge bg-amber-100 text-amber-700">{items.length} flagged</span>
                        {allAccepted && isIncluded && <span className="badge bg-green-100 text-green-700">All accepted</span>}
                        {!isIncluded && <span className="badge bg-slate-200 text-slate-500">Excluded</span>}
                      </button>
                      <div className="flex items-center gap-2 shrink-0">
                        {/* Add column button */}
                        {isIncluded && (
                          <button
                            onClick={() => { addExtraColumn(table); setActiveTab('unmapped') }}
                            className="flex items-center gap-1 text-xs text-indigo-600 hover:text-indigo-800 bg-indigo-50 hover:bg-indigo-100 px-2.5 py-1.5 rounded-lg transition-colors"
                          >
                            <PlusCircle size={12} />
                            Add column
                          </button>
                        )}
                        {/* Include/exclude toggle */}
                        <button
                          onClick={() => toggleTable(table)}
                          className={`flex items-center gap-1 text-xs px-2.5 py-1.5 rounded-lg transition-colors ${
                            isIncluded
                              ? 'text-green-700 bg-green-50 hover:bg-green-100'
                              : 'text-slate-500 bg-slate-100 hover:bg-slate-200'
                          }`}
                          title={isIncluded ? 'Click to exclude table' : 'Click to include table'}
                        >
                          {isIncluded ? <ToggleRight size={14} /> : <ToggleLeft size={14} />}
                          {isIncluded ? 'Included' : 'Excluded'}
                        </button>
                        <button
                          onClick={() => setExpandedLowConf(prev => {
                            const n = new Set(prev); n.has(table) ? n.delete(table) : n.add(table); return n
                          })}
                        >
                          {isOpen ? <ChevronUp size={16} className="text-slate-400" /> : <ChevronDown size={16} className="text-slate-400" />}
                        </button>
                      </div>
                    </div>

                    {isOpen && isIncluded && (
                      <div className="border-t border-slate-100 divide-y divide-slate-100">
                        {items.map((item, idx) => {
                          const d = lowConfDecisions[table]?.[idx] ?? { action: 'accept' as LowConfAction, overrideTarget: '' }
                          const conf = item.confidence ?? 0
                          return (
                            <div
                              key={idx}
                              className={`px-5 py-4 transition-colors ${
                                d.action === 'accept' ? 'bg-green-50/40' :
                                d.action === 'reject' ? 'bg-red-50/40' : 'bg-blue-50/40'
                              }`}
                            >
                              <div className="flex items-start gap-4">
                                <div className="flex-1 min-w-0">
                                  <div className="flex items-center gap-2 flex-wrap mb-1">
                                    <span className="font-mono text-xs bg-slate-100 text-slate-700 px-2 py-0.5 rounded">
                                      {item.source_field}
                                    </span>
                                    <ArrowRight size={12} className="text-slate-300" />
                                    <span className={`font-mono text-xs px-2 py-0.5 rounded ${
                                      d.action === 'reject'
                                        ? 'bg-red-100 text-red-500 line-through'
                                        : 'bg-indigo-50 text-indigo-700'
                                    }`}>
                                      {d.action === 'override' && d.overrideTarget ? d.overrideTarget : (item.suggested_target || '—')}
                                    </span>
                                    <ConfidenceBadge value={conf} tier={item.tier} />
                                  </div>
                                  {item.rationale && <p className="text-xs text-slate-400 mb-2">{item.rationale}</p>}
                                  {d.action === 'accept' && item.suggested_target && (
                                    <div className="flex items-center gap-1.5 text-xs text-green-700 mt-1">
                                      <CheckCircle size={12} />
                                      Mapped to <span className="font-mono font-semibold">{item.suggested_target}</span>
                                    </div>
                                  )}
                                  {d.action === 'override' && (
                                    <div className="mt-2 flex items-center gap-2 max-w-sm">
                                      <label className="text-xs font-medium text-slate-600 shrink-0">Map to</label>
                                      <input
                                        className="input text-xs py-1 flex-1 font-mono"
                                        value={d.overrideTarget}
                                        onChange={e => setOverrideTarget(table, idx, e.target.value)}
                                        placeholder="canonical_field_name"
                                      />
                                    </div>
                                  )}
                                  {d.action === 'reject' && <p className="text-xs text-red-500 mt-1">Field will be discarded</p>}
                                </div>
                                <div className="flex gap-1.5 shrink-0">
                                  <ActionBtn label="Accept"   icon={<CheckCircle size={11} />} active={d.action === 'accept'}   activeColor="bg-green-600" onClick={() => setLowConfAction(table, idx, 'accept')} />
                                  <ActionBtn label="Override" icon={<Edit3 size={11} />}        active={d.action === 'override'} activeColor="bg-blue-600"  onClick={() => setLowConfAction(table, idx, 'override')} />
                                  <ActionBtn label="Reject"   icon={<XCircle size={11} />}      active={d.action === 'reject'}   activeColor="bg-red-600"   onClick={() => setLowConfAction(table, idx, 'reject')} />
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
        </>
      )}

      {/* ── UNMAPPED TAB ─────────────────────────────────────────────────────── */}
      {activeTab === 'unmapped' && (
        <>
          {/* Auto-select toolbar */}
          <div className="flex items-center justify-between mb-4 px-4 py-3 bg-indigo-50 rounded-xl border border-indigo-200">
            <div>
              <p className="text-sm font-semibold text-indigo-800">Batch action</p>
              <p className="text-xs text-indigo-600">Apply the same decision to all unmapped fields at once</p>
            </div>
            <div className="flex gap-2 shrink-0">
              <button
                onClick={autoSelectAddColumn}
                className="flex items-center gap-2 text-sm font-medium text-white bg-indigo-600 hover:bg-indigo-700 px-4 py-2 rounded-lg transition-colors"
              >
                <Wand2 size={14} />
                Auto-select "Add column" for all
              </button>
              <button
                onClick={addColumnToAllTables}
                className="flex items-center gap-2 text-sm font-medium text-indigo-700 bg-white hover:bg-indigo-50 border border-indigo-300 px-4 py-2 rounded-lg transition-colors"
              >
                <PlusCircle size={14} />
                Add column to all tables
              </button>
            </div>
          </div>

          {/* Unmapped fields section */}
          {(unmappedTables.length > 0 || Object.keys(unmappedDecisions).length > 0) ? (
            <section className="mb-6">
              <h3 className="text-sm font-semibold text-slate-700 mb-1 flex items-center gap-2">
                <PlusCircle size={14} className="text-indigo-500" />
                Unmapped fields &amp; additional columns
              </h3>
              <p className="text-xs text-slate-400 mb-3">
                Define how to store unmapped fields. Use <strong>+ Add column</strong> to create extra columns.
              </p>

              <div className="space-y-4">
                {Array.from(new Set([...unmappedTables, ...Object.keys(unmappedDecisions)])).map(table => {
                  const rows = unmappedDecisions[table] ?? []
                  const isOpen = expandedUnmapped.has(table)
                  const isIncluded = tableInclusion[table] !== false
                  const customCount = rows.filter(r => r.action === 'custom').length

                  return (
                    <div key={table} className={`card overflow-hidden ${!isIncluded ? 'opacity-50' : ''}`}>
                      <div className="flex items-center justify-between px-5 py-3.5 hover:bg-slate-50 transition-colors">
                        <button
                          className="flex items-center gap-3 flex-1 text-left"
                          onClick={() => setExpandedUnmapped(prev => {
                            const n = new Set(prev); n.has(table) ? n.delete(table) : n.add(table); return n
                          })}
                        >
                          <span className={`font-semibold text-sm font-mono ${isIncluded ? 'text-slate-800' : 'text-slate-400 line-through'}`}>{table}</span>
                          <span className="badge bg-slate-100 text-slate-600">{rows.length} fields</span>
                          {customCount > 0 && (
                            <span className="badge bg-indigo-100 text-indigo-700">{customCount} new col{customCount > 1 ? 's' : ''}</span>
                          )}
                          {!isIncluded && <span className="badge bg-slate-200 text-slate-500">Excluded</span>}
                        </button>
                        <div className="flex items-center gap-2 shrink-0">
                          <button
                            onClick={() => toggleTable(table)}
                            className={`flex items-center gap-1 text-xs px-2.5 py-1.5 rounded-lg transition-colors ${
                              isIncluded
                                ? 'text-green-700 bg-green-50 hover:bg-green-100'
                                : 'text-slate-500 bg-slate-100 hover:bg-slate-200'
                            }`}
                          >
                            {isIncluded ? <ToggleRight size={14} /> : <ToggleLeft size={14} />}
                            {isIncluded ? 'Included' : 'Excluded'}
                          </button>
                          <button onClick={() => setExpandedUnmapped(prev => {
                            const n = new Set(prev); n.has(table) ? n.delete(table) : n.add(table); return n
                          })}>
                            {isOpen ? <ChevronUp size={16} className="text-slate-400" /> : <ChevronDown size={16} className="text-slate-400" />}
                          </button>
                        </div>
                      </div>

                      {isOpen && (
                        <div className="border-t border-slate-100">
                          {rows.map((row, idx) => (
                            <div key={idx} className="border-b border-slate-100 last:border-b-0">
                              <div className="px-5 py-3 flex items-center gap-4">
                                <div className="flex-1 min-w-0">
                                  {idx < (unmappedByTableInit[table]?.length ?? 0) ? (
                                    <span className="font-mono text-sm font-semibold text-slate-800">{row.source_field}</span>
                                  ) : (
                                    <input
                                      className="input text-xs py-1 font-mono max-w-[200px]"
                                      value={row.source_field}
                                      onChange={e => setRowSourceField(table, idx, e.target.value)}
                                      placeholder="source_field_name"
                                    />
                                  )}
                                </div>
                                <div className="flex gap-1.5 shrink-0">
                                  <ActionBtn label="New column"   icon={<PlusCircle size={11} />}  active={row.action === 'custom'}       activeColor="bg-indigo-600" onClick={() => setUnmappedAction(table, idx, 'custom')} />
                                  <ActionBtn label="raw_metadata" icon={<Archive size={11} />}      active={row.action === 'raw_metadata'} activeColor="bg-slate-600"  onClick={() => setUnmappedAction(table, idx, 'raw_metadata')} />
                                  <ActionBtn label="Skip"         icon={<XCircle size={11} />}      active={row.action === 'skip'}         activeColor="bg-red-600"    onClick={() => setUnmappedAction(table, idx, 'skip')} />
                                  {idx >= (unmappedByTableInit[table]?.length ?? 0) && (
                                    <button onClick={() => removeRow(table, idx)} className="p-1.5 rounded-lg text-slate-400 hover:text-red-500 hover:bg-red-50 transition-colors">
                                      <Trash2 size={13} />
                                    </button>
                                  )}
                                </div>
                              </div>
                              {row.action === 'custom' && row.custom && (
                                <div className="px-5 pb-4">
                                  <CustomDDLForm ddl={row.custom} canonicalTables={canonicalTables} onChange={patch => updateCustom(table, idx, patch)} />
                                </div>
                              )}
                              {row.action === 'raw_metadata' && (
                                <div className="px-5 pb-3">
                                  <p className="text-xs text-slate-400">Stored in <code className="bg-slate-100 px-1 rounded">raw_metadata</code> JSONB — no schema changes.</p>
                                </div>
                              )}
                              {row.action === 'skip' && (
                                <div className="px-5 pb-3">
                                  <p className="text-xs text-slate-400">Field will be discarded — not stored anywhere.</p>
                                </div>
                              )}
                            </div>
                          ))}

                          <div className="px-5 py-3 bg-slate-50 border-t border-slate-100">
                            <button
                              onClick={() => addExtraColumn(table)}
                              className="flex items-center gap-2 text-xs font-medium text-indigo-600 hover:text-indigo-800 transition-colors"
                            >
                              <PlusCircle size={14} />
                              Add column to <span className="font-mono">{table}</span>
                            </button>
                          </div>
                        </div>
                      )}
                    </div>
                  )
                })}
              </div>
            </section>
          ) : (
            /* Affordance when no unmapped fields */
            <section className="mb-6">
              <div className="card p-4">
                <p className="text-xs text-slate-500 mb-3">No unmapped fields. Add custom columns to any table below.</p>
                <div className="flex flex-wrap gap-2">
                  {allSourceTables.filter(t => tableInclusion[t] !== false).slice(0, 8).map(t => (
                    <button
                      key={t}
                      onClick={() => addExtraColumn(t)}
                      className="flex items-center gap-1.5 text-xs font-medium text-indigo-600 bg-indigo-50 hover:bg-indigo-100 px-3 py-1.5 rounded-lg transition-colors"
                    >
                      <PlusCircle size={12} />
                      {t}
                    </button>
                  ))}
                </div>
              </div>
            </section>
          )}

          {/* Create new tables */}
          <section className="mb-6">
            <div className="flex items-center justify-between mb-2">
              <h3 className="text-sm font-semibold text-slate-700 flex items-center gap-2">
                <Table2 size={14} className="text-violet-500" />
                Create brand-new tables
                <span className="text-xs font-normal text-slate-400">(not from extracted data)</span>
              </h3>
              <button
                onClick={addNewTable}
                className="flex items-center gap-1.5 text-xs font-medium text-violet-600 bg-violet-50 hover:bg-violet-100 px-3 py-1.5 rounded-lg transition-colors"
              >
                <PlusCircle size={13} />
                New table
              </button>
            </div>
            {newTableDefs.length === 0 ? (
              <div className="rounded-xl border border-dashed border-slate-200 px-4 py-3 text-xs text-slate-400 text-center">
                Click <strong>New table</strong> to define a completely new table in <code className="bg-slate-100 px-1 rounded">plenum_cafm</code>
              </div>
            ) : (
              <div className="space-y-4">
                {newTableDefs.map(nt => (
                  <NewTableCard
                    key={nt.id}
                    def={nt}
                    onRemove={() => removeNewTable(nt.id)}
                    onPatch={patch => patchNewTable(nt.id, patch)}
                    onAddColumn={() => addNewTableColumn(nt.id)}
                    onUpdateColumn={(ci, patch) => updateNewTableColumn(nt.id, ci, patch)}
                    onRemoveColumn={ci => removeNewTableColumn(nt.id, ci)}
                  />
                ))}
              </div>
            )}
          </section>
        </>
      )}

      {/* ── TABLES TAB ───────────────────────────────────────────────────────── */}
      {activeTab === 'tables' && (
        <>
          <div className="flex items-center justify-between mb-4">
            <p className="text-sm text-slate-600">
              Toggle which source tables to include in the migration. Excluded tables have all their field decisions set to skip/reject.
            </p>
            <div className="flex gap-2 shrink-0">
              <button
                onClick={() => setTableInclusion(prev => {
                  const next = { ...prev }
                  for (const t of allSourceTables) next[t] = true
                  return next
                })}
                className="text-xs font-medium text-green-700 bg-green-50 hover:bg-green-100 px-3 py-1.5 rounded-lg transition-colors"
              >
                Include all
              </button>
              <button
                onClick={() => setTableInclusion(prev => {
                  const next = { ...prev }
                  for (const t of allSourceTables) next[t] = false
                  return next
                })}
                className="text-xs font-medium text-slate-600 bg-slate-100 hover:bg-slate-200 px-3 py-1.5 rounded-lg transition-colors"
              >
                Exclude all
              </button>
            </div>
          </div>

          <div className="space-y-3 mb-6">
            {allSourceTables.map(table => {
              const isIncluded = tableInclusion[table] !== false
              const lowConfCount = lowConfByTable[table]?.length ?? 0
              const unmappedCount = unmappedByTableInit[table]?.length ?? 0
              const extraColCount = (unmappedDecisions[table]?.length ?? 0) - unmappedCount
              const hasExtraCols = extraColCount > 0
              const isColsOpen = expandedTableCols.has(table)

              return (
                <div
                  key={table}
                  className={`card overflow-hidden transition-opacity ${!isIncluded ? 'opacity-60' : ''}`}
                >
                  <div className="flex items-center gap-4 px-5 py-4">
                    {/* Include/exclude big toggle */}
                    <button
                      onClick={() => toggleTable(table)}
                      className={`relative w-12 h-6 rounded-full transition-colors shrink-0 ${isIncluded ? 'bg-green-500' : 'bg-slate-300'}`}
                    >
                      <div className={`absolute top-1 w-4 h-4 bg-white rounded-full shadow transition-transform ${isIncluded ? 'translate-x-7' : 'translate-x-1'}`} />
                    </button>

                    {/* Table info */}
                    <div className="flex-1 min-w-0">
                      <div className="flex items-center gap-2 flex-wrap">
                        <span className={`font-mono text-sm font-bold ${isIncluded ? 'text-slate-800' : 'text-slate-400 line-through'}`}>
                          {table}
                        </span>
                        {isIncluded
                          ? <span className="badge bg-green-100 text-green-700 text-xs">Included</span>
                          : <span className="badge bg-slate-200 text-slate-500 text-xs">Excluded</span>
                        }
                        {lowConfCount > 0 && <span className="badge bg-amber-100 text-amber-700 text-xs">{lowConfCount} low-conf</span>}
                        {unmappedCount > 0 && <span className="badge bg-red-100 text-red-600 text-xs">{unmappedCount} unmapped</span>}
                        {hasExtraCols && <span className="badge bg-indigo-100 text-indigo-700 text-xs">+{extraColCount} extra col{extraColCount > 1 ? 's' : ''}</span>}
                      </div>
                    </div>

                    {/* Add column button */}
                    {isIncluded && (
                      <button
                        onClick={() => addExtraColumn(table)}
                        className="flex items-center gap-1.5 text-xs font-medium text-indigo-600 bg-indigo-50 hover:bg-indigo-100 px-3 py-1.5 rounded-lg transition-colors shrink-0"
                      >
                        <PlusCircle size={13} />
                        Add column
                      </button>
                    )}

                    {/* Show extra columns toggle */}
                    {hasExtraCols && (
                      <button
                        onClick={() => setExpandedTableCols(prev => {
                          const n = new Set(prev); n.has(table) ? n.delete(table) : n.add(table); return n
                        })}
                        className="text-xs text-slate-500 hover:text-slate-700"
                      >
                        {isColsOpen ? <ChevronUp size={15} /> : <ChevronDown size={15} />}
                      </button>
                    )}
                  </div>

                  {/* Inline extra columns (manually added) */}
                  {isColsOpen && hasExtraCols && (
                    <div className="border-t border-slate-100 px-5 pb-3 pt-2 bg-indigo-50/40">
                      <p className="text-xs font-medium text-slate-500 mb-2">Extra columns added</p>
                      {(unmappedDecisions[table] ?? []).slice(unmappedCount).map((row, i) => {
                        const actualIdx = unmappedCount + i
                        return (
                          <div key={i} className="flex items-center gap-2 mb-1.5">
                            <input
                              className="input text-xs py-1 font-mono flex-1 max-w-[160px]"
                              value={row.source_field}
                              onChange={e => setRowSourceField(table, actualIdx, e.target.value)}
                              placeholder="col_name"
                            />
                            {row.custom && (
                              <>
                                <span className="text-xs text-slate-400">→</span>
                                <span className="text-xs font-mono text-indigo-600 bg-indigo-50 border border-indigo-200 px-2 py-0.5 rounded">
                                  {row.custom.is_new_table ? row.custom.new_table_name || '(new)' : row.custom.target_table || '?'}
                                </span>
                                <span className="text-xs font-mono text-slate-500">{row.custom.data_type}</span>
                              </>
                            )}
                            <button onClick={() => removeRow(table, actualIdx)} className="p-1 text-slate-300 hover:text-red-500 transition-colors">
                              <Trash2 size={12} />
                            </button>
                          </div>
                        )
                      })}
                    </div>
                  )}
                </div>
              )
            })}
          </div>

          {/* Create new tables — also accessible from Tables tab */}
          <div className="flex items-center justify-between mb-2">
            <h3 className="text-sm font-semibold text-slate-700 flex items-center gap-2">
              <Table2 size={14} className="text-violet-500" />
              Create brand-new tables
              <span className="text-xs font-normal text-slate-400">(not from extracted data)</span>
            </h3>
            <button
              onClick={addNewTable}
              className="flex items-center gap-1.5 text-xs font-medium text-violet-600 bg-violet-50 hover:bg-violet-100 px-3 py-1.5 rounded-lg transition-colors"
            >
              <PlusCircle size={13} />
              New table
            </button>
          </div>
          {newTableDefs.length === 0 ? (
            <div className="rounded-xl border border-dashed border-slate-200 px-4 py-3 text-xs text-slate-400 text-center mb-6">
              Click <strong>New table</strong> to define a completely new table in <code className="bg-slate-100 px-1 rounded">plenum_cafm</code>
            </div>
          ) : (
            <div className="space-y-4 mb-6">
              {newTableDefs.map(nt => (
                <NewTableCard
                  key={nt.id}
                  def={nt}
                  onRemove={() => removeNewTable(nt.id)}
                  onPatch={patch => patchNewTable(nt.id, patch)}
                  onAddColumn={() => addNewTableColumn(nt.id)}
                  onUpdateColumn={(ci, patch) => updateNewTableColumn(nt.id, ci, patch)}
                  onRemoveColumn={ci => removeNewTableColumn(nt.id, ci)}
                />
              ))}
            </div>
          )}
        </>
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
            Submit field mapping decisions
          </>
        )}
      </button>
    </div>
  )
}

// ── New table card ────────────────────────────────────────────────────────────
function NewTableCard({ def, onRemove, onPatch, onAddColumn, onUpdateColumn, onRemoveColumn }: {
  def: NewTableDef
  onRemove: () => void
  onPatch: (p: Partial<NewTableDef>) => void
  onAddColumn: () => void
  onUpdateColumn: (ci: number, p: Partial<NewTableColumn>) => void
  onRemoveColumn: (ci: number) => void
}) {
  const tableName = def.table_name.trim() || '…'
  const pk = def.pk_col.trim() || 'id'

  const colDefs = [
    `  ${pk} UUID PRIMARY KEY DEFAULT gen_random_uuid()`,
    ...def.columns
      .filter(c => c.column_name.trim())
      .map(c => `  ${c.column_name} ${c.data_type}${c.nullable ? '' : ' NOT NULL'}`),
    '  created_at TIMESTAMPTZ NOT NULL DEFAULT now()',
  ]
  const sqlPreview = `CREATE TABLE plenum_cafm.${tableName} (\n${colDefs.join(',\n')}\n);`

  return (
    <div className="rounded-xl border border-violet-200 bg-violet-50/40 overflow-hidden">
      <div className="flex items-center gap-3 px-4 py-3 bg-violet-50 border-b border-violet-200">
        <Table2 size={14} className="text-violet-500 shrink-0" />
        <input
          className="input text-sm py-1 font-mono flex-1 max-w-xs bg-white"
          value={def.table_name}
          onChange={e => onPatch({ table_name: e.target.value })}
          placeholder="new_table_name"
        />
        <div className="flex items-center gap-1.5 shrink-0">
          <label className="text-xs text-slate-500">PK</label>
          <input
            className="input text-xs py-1 font-mono w-24 bg-white"
            value={def.pk_col}
            onChange={e => onPatch({ pk_col: e.target.value })}
            placeholder="id"
          />
        </div>
        <button onClick={onRemove} className="ml-auto p-1.5 rounded-lg text-slate-400 hover:text-red-500 hover:bg-red-50 transition-colors">
          <Trash2 size={14} />
        </button>
      </div>

      <div className="px-4 pt-3 pb-2 space-y-2">
        <p className="text-xs font-medium text-slate-500 uppercase tracking-wider">Columns</p>
        {def.columns.map((col, ci) => (
          <div key={ci} className="flex items-center gap-2">
            <input
              className="input text-xs py-1.5 font-mono flex-1"
              value={col.column_name}
              onChange={e => onUpdateColumn(ci, { column_name: e.target.value })}
              placeholder="column_name"
            />
            <select
              className="input text-xs py-1.5 font-mono w-36 shrink-0"
              value={col.data_type}
              onChange={e => onUpdateColumn(ci, { data_type: e.target.value })}
            >
              {DATA_TYPES.map(t => <option key={t} value={t}>{t}</option>)}
            </select>
            <label className="flex items-center gap-1.5 text-xs text-slate-500 shrink-0 cursor-pointer">
              <div
                className={`relative w-7 h-4 rounded-full transition-colors cursor-pointer ${col.nullable ? 'bg-indigo-500' : 'bg-slate-300'}`}
                onClick={() => onUpdateColumn(ci, { nullable: !col.nullable })}
              >
                <div className={`absolute top-0.5 w-3 h-3 bg-white rounded-full shadow transition-transform ${col.nullable ? 'translate-x-3' : 'translate-x-0.5'}`} />
              </div>
              Null
            </label>
            <button
              onClick={() => onRemoveColumn(ci)}
              disabled={def.columns.length === 1}
              className="p-1 rounded text-slate-300 hover:text-red-500 hover:bg-red-50 transition-colors disabled:opacity-30 disabled:cursor-not-allowed"
            >
              <Trash2 size={12} />
            </button>
          </div>
        ))}
        <button onClick={onAddColumn} className="flex items-center gap-1.5 text-xs text-indigo-600 hover:text-indigo-800 transition-colors mt-1">
          <PlusCircle size={12} />
          Add column
        </button>
      </div>

      <div className="px-4 pb-4">
        <pre className="rounded-lg bg-white border border-violet-200 px-3 py-2 font-mono text-xs text-slate-600 leading-relaxed overflow-auto mt-2">
          {sqlPreview}
        </pre>
      </div>
    </div>
  )
}

// ── Custom DDL form ───────────────────────────────────────────────────────────
function CustomDDLForm({
  ddl, canonicalTables, onChange,
}: {
  ddl: CustomDDL; canonicalTables: string[]; onChange: (p: Partial<CustomDDL>) => void
}) {
  const effectiveTable = ddl.is_new_table ? (ddl.new_table_name || '…') : (ddl.target_table || '…')
  const col = ddl.custom_column_name || '…'
  const dt = ddl.data_type
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
            <label className="label text-xs">Target table <span className="text-red-500">*</span></label>
            {canonicalTables.length > 0 ? (
              <select
                className={`input text-xs py-1.5 ${!ddl.target_table ? 'border-amber-400 text-slate-400' : ''}`}
                value={ddl.target_table}
                onChange={e => onChange({ target_table: e.target.value })}
              >
                <option value="">— select target table —</option>
                {canonicalTables.map(t => <option key={t} value={t}>{t}</option>)}
              </select>
            ) : (
              <input className="input text-xs py-1.5 font-mono" value={ddl.target_table} onChange={e => onChange({ target_table: e.target.value })} placeholder="assets" />
            )}
          </div>
        )}
        <div>
          <label className="label text-xs">Column name</label>
          <input className="input text-xs py-1.5 font-mono" value={ddl.custom_column_name} onChange={e => onChange({ custom_column_name: e.target.value })} placeholder="my_column" />
        </div>
        <div>
          <label className="label text-xs">Data type</label>
          <select className="input text-xs py-1.5 font-mono" value={ddl.data_type} onChange={e => onChange({ data_type: e.target.value })}>
            {DATA_TYPES.map(t => <option key={t} value={t}>{t}</option>)}
          </select>
        </div>
      </div>

      <label className="flex items-center gap-3 cursor-pointer">
        <div
          className={`relative w-9 h-5 rounded-full transition-colors ${ddl.nullable ? 'bg-indigo-600' : 'bg-slate-300'}`}
          onClick={() => onChange({ nullable: !ddl.nullable })}
        >
          <div className={`absolute top-0.5 w-4 h-4 bg-white rounded-full shadow transition-transform ${ddl.nullable ? 'translate-x-4' : 'translate-x-0.5'}`} />
        </div>
        <span className="text-xs font-medium text-slate-700">Nullable</span>
      </label>

      <pre className="rounded-lg bg-white border border-indigo-200 px-3 py-2 font-mono text-xs text-slate-600 leading-relaxed overflow-auto">
        {sqlPreview}
      </pre>
    </div>
  )
}

// ── Helpers ───────────────────────────────────────────────────────────────────
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

function ConfidenceBadge({ value, tier }: { value: number; tier: string }) {
  const pct = Math.round(value * 100)
  const color = pct >= 80 ? 'bg-green-100 text-green-700' : pct >= 65 ? 'bg-amber-100 text-amber-700' : 'bg-red-100 text-red-700'
  return <span className={`badge text-xs font-mono ${color}`}>{tier} · {pct}%</span>
}
