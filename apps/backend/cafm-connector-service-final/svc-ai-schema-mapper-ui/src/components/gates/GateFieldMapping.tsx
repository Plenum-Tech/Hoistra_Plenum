import { useState } from 'react'
import { submitGateFieldMapping } from '../../api'
import type { FieldMappingPayload } from '../../types'
import {
  CheckCircle, XCircle, Archive, ChevronDown, ChevronUp,
  AlertTriangle, GitMerge, PenLine, PlusCircle, Trash2, Table2,
} from 'lucide-react'

interface Props {
  migrationId: string
  payload: FieldMappingPayload
  onSubmitted: () => void
}

// ── Types ─────────────────────────────────────────────────────────────────────
type FlaggedAction = 'accept' | 'reject' | 'override'
type UnmappedAction = 'raw_metadata' | 'skip' | 'custom'

interface FlaggedDecision {
  action: FlaggedAction
  override_target: string
}

interface CustomDDL {
  source_field: string        // can be user-typed for manually added rows
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

const DATA_TYPES = [
  'VARCHAR(255)', 'VARCHAR(100)', 'VARCHAR(50)',
  'TEXT', 'INTEGER', 'BIGINT', 'DECIMAL(10,2)',
  'BOOLEAN', 'TIMESTAMPTZ', 'DATE', 'JSONB', 'UUID',
]

function emptyCustomDDL(sourceField: string, canonicalTables: string[]): CustomDDL {
  return {
    source_field: sourceField,
    target_table: canonicalTables[0] ?? '',
    custom_column_name: sourceField.toLowerCase().replace(/\s+/g, '_'),
    data_type: 'VARCHAR(255)',
    is_new_table: false,
    new_table_name: '',
    new_table_pk: 'id',
    nullable: true,
  }
}

// ── New-table definition (multi-column CREATE TABLE) ─────────────────────────
interface NewTableColumn {
  column_name: string
  data_type: string
  nullable: boolean
}

interface NewTableDef {
  id: string           // local key only
  table_name: string
  pk_col: string
  columns: NewTableColumn[]
}

function emptyNewTable(): NewTableDef {
  return {
    id: `nt_${Date.now()}`,
    table_name: '',
    pk_col: 'id',
    columns: [{ column_name: '', data_type: 'VARCHAR(255)', nullable: true }],
  }
}

// ─────────────────────────────────────────────────────────────────────────────

export default function GateFieldMapping({ migrationId, payload, onSubmitted }: Props) {
  const flaggedTables = Object.keys(payload.review_items_by_table ?? {})
  const unmappedTables = Object.keys(payload.unmappable_items_by_table ?? {})
  const canonicalTables = payload.existing_canonical_tables ?? []

  // ── Initial decisions ─────────────────────────────────────────────────────
  function buildInitFlagged(): Record<string, FlaggedDecision[]> {
    const out: Record<string, FlaggedDecision[]> = {}
    for (const [t, items] of Object.entries(payload.review_items_by_table ?? {})) {
      out[t] = items.map(item => ({
        action: 'accept' as FlaggedAction,
        // pre-fill override_target from whichever field the backend used
        override_target: String(item.target_field ?? (item as any).suggested_target ?? ''),
      }))
    }
    return out
  }

  function buildInitUnmapped(): Record<string, UnmappedDecision[]> {
    const out: Record<string, UnmappedDecision[]> = {}
    for (const [t, items] of Object.entries(payload.unmappable_items_by_table ?? {})) {
      out[t] = items.map(item => ({
        action: 'raw_metadata' as UnmappedAction,
        source_field: String(item.source_field ?? ''),
        custom: null,
      }))
    }
    return out
  }

  const [flaggedDecisions, setFlaggedDecisions] = useState<Record<string, FlaggedDecision[]>>(buildInitFlagged)
  const [unmappedDecisions, setUnmappedDecisions] = useState<Record<string, UnmappedDecision[]>>(buildInitUnmapped)
  const [expandedFlagged, setExpandedFlagged] = useState<Set<string>>(new Set(flaggedTables))
  const [expandedUnmapped, setExpandedUnmapped] = useState<Set<string>>(new Set(unmappedTables))
  const [newTableDefs, setNewTableDefs] = useState<NewTableDef[]>([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  // ── New-table helpers ─────────────────────────────────────────────────────
  function addNewTable() {
    setNewTableDefs(prev => [...prev, emptyNewTable()])
  }
  function removeNewTable(id: string) {
    setNewTableDefs(prev => prev.filter(t => t.id !== id))
  }
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
  function updateNewTableColumn(id: string, colIdx: number, patch: Partial<NewTableColumn>) {
    setNewTableDefs(prev => prev.map(t => {
      if (t.id !== id) return t
      const cols = t.columns.map((c, i) => i === colIdx ? { ...c, ...patch } : c)
      return { ...t, columns: cols }
    }))
  }
  function removeNewTableColumn(id: string, colIdx: number) {
    setNewTableDefs(prev => prev.map(t => {
      if (t.id !== id) return t
      return { ...t, columns: t.columns.filter((_, i) => i !== colIdx) }
    }))
  }

  // ── Flagged helpers ───────────────────────────────────────────────────────
  function setFlaggedAction(table: string, idx: number, action: FlaggedAction) {
    setFlaggedDecisions(prev => {
      const tableRows = [...(prev[table] ?? [])]
      tableRows[idx] = { ...tableRows[idx], action }
      return { ...prev, [table]: tableRows }
    })
  }
  function setOverrideTarget(table: string, idx: number, val: string) {
    setFlaggedDecisions(prev => {
      const tableRows = [...(prev[table] ?? [])]
      tableRows[idx] = { ...tableRows[idx], override_target: val }
      return { ...prev, [table]: tableRows }
    })
  }

  // ── Unmapped helpers ──────────────────────────────────────────────────────
  function setUnmappedAction(table: string, idx: number, action: UnmappedAction) {
    setUnmappedDecisions(prev => {
      const tableRows = [...(prev[table] ?? [])]
      const row = tableRows[idx]
      tableRows[idx] = {
        ...row,
        action,
        custom: action === 'custom'
          ? (row.custom ?? emptyCustomDDL(row.source_field, canonicalTables))
          : null,
      }
      return { ...prev, [table]: tableRows }
    })
  }

  function updateCustom(table: string, idx: number, patch: Partial<CustomDDL>) {
    setUnmappedDecisions(prev => {
      const tableRows = [...(prev[table] ?? [])]
      const row = tableRows[idx]
      tableRows[idx] = {
        ...row,
        custom: { ...(row.custom ?? emptyCustomDDL(row.source_field, canonicalTables)), ...patch },
      }
      return { ...prev, [table]: tableRows }
    })
  }

  // ── Add / remove extra manually-defined column rows per table ─────────────
  function addExtraColumn(table: string) {
    setUnmappedDecisions(prev => {
      const tableRows = [...(prev[table] ?? [])]
      const newRow: UnmappedDecision = {
        action: 'custom',
        source_field: '',
        custom: emptyCustomDDL('', canonicalTables),
      }
      return { ...prev, [table]: [...tableRows, newRow] }
    })
  }

  function removeRow(table: string, idx: number) {
    setUnmappedDecisions(prev => {
      const tableRows = [...(prev[table] ?? [])]
      tableRows.splice(idx, 1)
      return { ...prev, [table]: tableRows }
    })
  }

  function setRowSourceField(table: string, idx: number, val: string) {
    setUnmappedDecisions(prev => {
      const tableRows = [...(prev[table] ?? [])]
      tableRows[idx] = { ...tableRows[idx], source_field: val }
      if (tableRows[idx].custom) {
        tableRows[idx] = {
          ...tableRows[idx],
          custom: { ...tableRows[idx].custom!, source_field: val },
        }
      }
      return { ...prev, [table]: tableRows }
    })
  }

  // ── Submit ────────────────────────────────────────────────────────────────
  async function handleSubmit() {
    setLoading(true)
    setError(null)
    try {
      // flagged: { table: [{source_field, action, target_field?}] }
      const flaggedByTable: Record<string, Array<Record<string, any>>> = {}
      for (const [table, items] of Object.entries(payload.review_items_by_table ?? {})) {
        flaggedByTable[table] = items.map((item, idx) => {
          const d = flaggedDecisions[table]?.[idx]
          const action = d?.action ?? 'accept'
          const sf = String(item.source_field ?? '')
          const suggestedTarget = String(item.target_field ?? (item as any).suggested_target ?? '')
          if (action === 'accept') {
            return { source_field: sf, action: 'accept', target_field: suggestedTarget }
          }
          if (action === 'override') {
            return { source_field: sf, action: 'override', target_field: d?.override_target ?? suggestedTarget }
          }
          return { source_field: sf, action: 'reject' }
        })
      }

      // unmapped: { table: [{source_field, action, ...custom?}] }
      const unmappedByTable: Record<string, Array<Record<string, any>>> = {}
      const allUnmappedTables = new Set([...unmappedTables, ...Object.keys(unmappedDecisions)])
      for (const table of allUnmappedTables) {
        const rows = unmappedDecisions[table] ?? []
        unmappedByTable[table] = rows.map(row => {
          if (row.action === 'custom' && row.custom) {
            const c = row.custom
            const effectiveTable = c.is_new_table ? c.new_table_name : c.target_table
            return {
              source_field: row.source_field,
              action: 'custom',
              target_table: effectiveTable,
              custom_column_name: c.custom_column_name,
              data_type: c.data_type,
              is_new_table: c.is_new_table,
              new_table_pk: c.is_new_table ? c.new_table_pk : undefined,
              nullable: c.nullable,
            }
          }
          return { source_field: row.source_field, action: row.action }
        })
      }

      // new tables → inject each column as a custom entry under a synthetic source key
      for (const nt of newTableDefs) {
        if (!nt.table_name.trim()) continue
        const key = `_new_table_${nt.table_name}`
        unmappedByTable[key] = nt.columns
          .filter(c => c.column_name.trim())
          .map(c => ({
            source_field: c.column_name,
            action: 'custom',
            target_table: nt.table_name,
            custom_column_name: c.column_name,
            data_type: c.data_type,
            is_new_table: true,
            new_table_pk: nt.pk_col || 'id',
            nullable: c.nullable,
          }))
      }

      await submitGateFieldMapping(migrationId, {
        decisions: { flagged: flaggedByTable, unmapped: unmappedByTable },
      })
      onSubmitted()
    } catch (err: any) {
      setError(String(err?.message ?? 'Failed to submit review'))
      setLoading(false)
    }
  }

  const totalFlagged = Object.values(payload.review_items_by_table ?? {}).reduce((s, i) => s + i.length, 0)
  const totalUnmapped = Object.values(payload.unmappable_items_by_table ?? {}).reduce((s, i) => s + i.length, 0)

  // ── Render ────────────────────────────────────────────────────────────────
  return (
    <div className="max-w-4xl">
      {/* Header */}
      <div className="flex items-start gap-4 mb-6">
        <div className="w-10 h-10 rounded-xl bg-amber-100 flex items-center justify-center shrink-0">
          <GitMerge size={20} className="text-amber-600" />
        </div>
        <div>
          <h2 className="text-lg font-bold text-slate-900">Human review gate (Table Structure Confirmation)</h2>
          <p className="text-sm text-slate-500 mt-0.5">
            Finalise every field mapping. For unmapped fields, create a new column, keep in raw_metadata, or skip.
          </p>
        </div>
      </div>

      {/* Confidence alert */}
      {payload.confidence_alert && (
        <div className="mb-5 flex items-start gap-3 rounded-xl bg-amber-50 border border-amber-200 px-4 py-3">
          <AlertTriangle size={16} className="text-amber-600 mt-0.5 shrink-0" />
          <p className="text-sm text-amber-800">{payload.confidence_alert.message}</p>
        </div>
      )}

      {/* Counters */}
      <div className="grid grid-cols-2 gap-4 mb-6">
        <div className="card p-4 flex items-center gap-3">
          <div className="w-8 h-8 rounded-lg bg-amber-50 flex items-center justify-center">
            <AlertTriangle size={16} className="text-amber-600" />
          </div>
          <div>
            <div className="text-xl font-bold font-mono text-slate-800">{totalFlagged}</div>
            <div className="text-xs text-slate-500">Flagged — accept / override / reject</div>
          </div>
        </div>
        <div className="card p-4 flex items-center gap-3">
          <div className="w-8 h-8 rounded-lg bg-red-50 flex items-center justify-center">
            <XCircle size={16} className="text-red-500" />
          </div>
          <div>
            <div className="text-xl font-bold font-mono text-slate-800">{totalUnmapped}</div>
            <div className="text-xs text-slate-500">Unmapped — DDL / raw_metadata / skip</div>
          </div>
        </div>
      </div>

      {/* ── FLAGGED ──────────────────────────────────────────────────────────── */}
      {flaggedTables.length > 0 && (
        <section className="mb-6">
          <h3 className="text-sm font-semibold text-slate-700 mb-3 flex items-center gap-2">
            <AlertTriangle size={14} className="text-amber-500" />
            Flagged mappings
          </h3>
          <div className="space-y-3">
            {flaggedTables.map(table => {
              const items = payload.review_items_by_table![table] ?? []
              const isOpen = expandedFlagged.has(table)
              const allAccepted = flaggedDecisions[table]?.every(d => d.action === 'accept')

              return (
                <div key={table} className="card overflow-hidden">
                  <button
                    className="w-full flex items-center justify-between px-5 py-3.5 hover:bg-slate-50 transition-colors text-left"
                    onClick={() => setExpandedFlagged(prev => { const n = new Set(prev); n.has(table) ? n.delete(table) : n.add(table); return n })}
                  >
                    <div className="flex items-center gap-3">
                      <span className="font-semibold text-slate-800 text-sm">{table}</span>
                      <span className="badge bg-amber-100 text-amber-700">{items.length} flagged</span>
                      {allAccepted && <span className="badge bg-green-100 text-green-700">All accepted</span>}
                    </div>
                    {isOpen ? <ChevronUp size={16} className="text-slate-400" /> : <ChevronDown size={16} className="text-slate-400" />}
                  </button>

                  {isOpen && (
                    <div className="border-t border-slate-100 divide-y divide-slate-100">
                      {items.map((item, idx) => {
                        const d = flaggedDecisions[table]?.[idx] ?? { action: 'accept' as FlaggedAction, override_target: '' }
                        const suggestedTarget = String(item.target_field ?? (item as any).suggested_target ?? '')
                        const suggestions: string[] = Array.isArray(item.suggestions) ? item.suggestions : []

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
                                {/* Source → target */}
                                <div className="flex items-center gap-2 flex-wrap mb-1">
                                  <span className="font-mono text-xs bg-slate-100 text-slate-700 px-2 py-0.5 rounded">
                                    {String(item.source_field ?? '')}
                                  </span>
                                  <span className="text-slate-300 text-xs">→</span>
                                  <span className={`font-mono text-xs px-2 py-0.5 rounded ${
                                    d.action === 'reject'
                                      ? 'bg-red-100 text-red-500 line-through'
                                      : 'bg-indigo-50 text-indigo-700'
                                  }`}>
                                    {d.action === 'override' && d.override_target
                                      ? d.override_target
                                      : suggestedTarget || '—'}
                                  </span>
                                  {item.confidence != null && <ConfidencePill confidence={item.confidence} />}
                                </div>
                                {item.rationale && (
                                  <p className="text-xs text-slate-400 mb-2">{item.rationale}</p>
                                )}

                                {/* Accept confirmation */}
                                {d.action === 'accept' && suggestedTarget && (
                                  <div className="flex items-center gap-1.5 text-xs text-green-700 mt-1">
                                    <CheckCircle size={12} />
                                    Mapped to <span className="font-mono font-semibold">{suggestedTarget}</span>
                                  </div>
                                )}

                                {/* Override input */}
                                {d.action === 'override' && (
                                  <div className="mt-2 flex items-center gap-2 max-w-sm">
                                    <label className="text-xs font-medium text-slate-600 shrink-0">Map to</label>
                                    {suggestions.length > 0 ? (
                                      <select
                                        className="input text-xs py-1 flex-1"
                                        value={d.override_target}
                                        onChange={e => setOverrideTarget(table, idx, e.target.value)}
                                      >
                                        <option value="">— select canonical field —</option>
                                        {suggestions.map(s => (
                                          <option key={s} value={s}>{s}</option>
                                        ))}
                                      </select>
                                    ) : (
                                      <input
                                        className="input text-xs py-1 flex-1 font-mono"
                                        value={d.override_target}
                                        onChange={e => setOverrideTarget(table, idx, e.target.value)}
                                        placeholder="canonical_field_name"
                                      />
                                    )}
                                  </div>
                                )}

                                {/* Reject note */}
                                {d.action === 'reject' && (
                                  <p className="text-xs text-red-500 mt-1">Field will be discarded</p>
                                )}
                              </div>

                              {/* Action buttons */}
                              <div className="flex gap-1.5 shrink-0">
                                <ActionBtn label="Accept"   icon={<CheckCircle size={11} />} active={d.action === 'accept'}   activeColor="bg-green-600" onClick={() => setFlaggedAction(table, idx, 'accept')} />
                                <ActionBtn label="Override" icon={<PenLine size={11} />}     active={d.action === 'override'} activeColor="bg-blue-600"  onClick={() => setFlaggedAction(table, idx, 'override')} />
                                <ActionBtn label="Reject"   icon={<XCircle size={11} />}     active={d.action === 'reject'}   activeColor="bg-red-600"   onClick={() => setFlaggedAction(table, idx, 'reject')} />
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
        </section>
      )}

      {/* ── UNMAPPED ─────────────────────────────────────────────────────────── */}
      {(unmappedTables.length > 0 || Object.keys(unmappedDecisions).length > 0) && (
        <section className="mb-6">
          <h3 className="text-sm font-semibold text-slate-700 mb-1 flex items-center gap-2">
            <PlusCircle size={14} className="text-indigo-500" />
            Unmapped fields &amp; additional columns
          </h3>
          <p className="text-xs text-slate-400 mb-3">
            Define how to store unmapped fields. Use <strong>+ Add column</strong> to create extra columns
            in any source table — including brand-new tables.
          </p>

          <div className="space-y-4">
            {Array.from(new Set([...unmappedTables, ...Object.keys(unmappedDecisions)])).map(table => {
              const rows = unmappedDecisions[table] ?? []
              const isOpen = expandedUnmapped.has(table)
              const customCount = rows.filter(r => r.action === 'custom').length

              return (
                <div key={table} className="card overflow-hidden">
                  <button
                    className="w-full flex items-center justify-between px-5 py-3.5 hover:bg-slate-50 transition-colors text-left"
                    onClick={() => setExpandedUnmapped(prev => { const n = new Set(prev); n.has(table) ? n.delete(table) : n.add(table); return n })}
                  >
                    <div className="flex items-center gap-3">
                      <span className="font-semibold text-slate-800 text-sm">{table}</span>
                      <span className="badge bg-slate-100 text-slate-600">{rows.length} fields</span>
                      {customCount > 0 && (
                        <span className="badge bg-indigo-100 text-indigo-700">{customCount} new column{customCount > 1 ? 's' : ''}</span>
                      )}
                    </div>
                    {isOpen ? <ChevronUp size={16} className="text-slate-400" /> : <ChevronDown size={16} className="text-slate-400" />}
                  </button>

                  {isOpen && (
                    <div className="border-t border-slate-100">
                      {rows.map((row, idx) => (
                        <div key={idx} className="border-b border-slate-100 last:border-b-0">
                          <div className="px-5 py-3 flex items-center gap-4">
                            {/* Source field name — editable for manually added rows */}
                            <div className="flex-1 min-w-0">
                              {/* If it's an auto-detected unmapped field, show as fixed label */}
                              {idx < (payload.unmappable_items_by_table?.[table]?.length ?? 0) ? (
                                <span className="font-mono text-sm font-semibold text-slate-800">
                                  {row.source_field}
                                </span>
                              ) : (
                                /* Manually added row — allow naming it */
                                <input
                                  className="input text-xs py-1 font-mono max-w-[200px]"
                                  value={row.source_field}
                                  onChange={e => setRowSourceField(table, idx, e.target.value)}
                                  placeholder="source_field_name"
                                />
                              )}
                            </div>

                            {/* Strategy buttons */}
                            <div className="flex gap-1.5 shrink-0">
                              <ActionBtn label="New column"   icon={<PlusCircle size={11} />}  active={row.action === 'custom'}       activeColor="bg-indigo-600" onClick={() => setUnmappedAction(table, idx, 'custom')} />
                              <ActionBtn label="raw_metadata" icon={<Archive size={11} />}      active={row.action === 'raw_metadata'} activeColor="bg-slate-600"  onClick={() => setUnmappedAction(table, idx, 'raw_metadata')} />
                              <ActionBtn label="Skip"         icon={<XCircle size={11} />}      active={row.action === 'skip'}         activeColor="bg-red-600"    onClick={() => setUnmappedAction(table, idx, 'skip')} />

                              {/* Allow removing manually-added rows */}
                              {idx >= (payload.unmappable_items_by_table?.[table]?.length ?? 0) && (
                                <button
                                  onClick={() => removeRow(table, idx)}
                                  className="p-1.5 rounded-lg text-slate-400 hover:text-red-500 hover:bg-red-50 transition-colors"
                                  title="Remove this row"
                                >
                                  <Trash2 size={13} />
                                </button>
                              )}
                            </div>
                          </div>

                          {/* Custom DDL form */}
                          {row.action === 'custom' && row.custom && (
                            <div className="px-5 pb-4">
                              <CustomDDLForm
                                ddl={row.custom}
                                canonicalTables={canonicalTables}
                                onChange={patch => updateCustom(table, idx, patch)}
                              />
                            </div>
                          )}
                        </div>
                      ))}

                      {/* + Add column button */}
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
      )}

      {/* If no unmapped tables from API, still show the "+ Add column to table" affordance */}
      {unmappedTables.length === 0 && Object.keys(unmappedDecisions).length === 0 && (
        <section className="mb-6">
          <h3 className="text-sm font-semibold text-slate-700 mb-2 flex items-center gap-2">
            <PlusCircle size={14} className="text-indigo-500" />
            Additional columns
          </h3>
          <div className="card p-4">
            <p className="text-xs text-slate-500 mb-3">No unmapped fields. You can still add custom columns to any table.</p>
            <div className="flex flex-wrap gap-2">
              {canonicalTables.slice(0, 8).map(t => (
                <button
                  key={t}
                  onClick={() => {
                    setUnmappedDecisions(prev => ({
                      ...prev,
                      [t]: [
                        ...(prev[t] ?? []),
                        { action: 'custom', source_field: '', custom: emptyCustomDDL('', canonicalTables) },
                      ],
                    }))
                    setExpandedUnmapped(prev => new Set([...prev, t]))
                  }}
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

      {/* ── CREATE NEW TABLES ─────────────────────────────────────────────── */}
      <section className="mb-6">
        <div className="flex items-center justify-between mb-2">
          <h3 className="text-sm font-semibold text-slate-700 flex items-center gap-2">
            <Table2 size={14} className="text-violet-500" />
            Create new tables
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
            Click <strong>New table</strong> to define a brand-new table in <code className="bg-slate-100 px-1 rounded">plenum_cafm</code>
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
            Confirm Field Mapping Decisions
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

  // Build full SQL preview
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
      {/* Header */}
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
        <button
          onClick={onRemove}
          className="ml-auto p-1.5 rounded-lg text-slate-400 hover:text-red-500 hover:bg-red-50 transition-colors"
          title="Remove this table"
        >
          <Trash2 size={14} />
        </button>
      </div>

      {/* Columns */}
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
        <button
          onClick={onAddColumn}
          className="flex items-center gap-1.5 text-xs text-indigo-600 hover:text-indigo-800 transition-colors mt-1"
        >
          <PlusCircle size={12} />
          Add column
        </button>
      </div>

      {/* SQL preview */}
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

      {/* Nullable */}
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

function ConfidencePill({ confidence }: { confidence: number }) {
  const pct = Math.round(confidence * 100)
  const color = pct >= 85 ? 'bg-green-100 text-green-700' : pct >= 65 ? 'bg-amber-100 text-amber-700' : 'bg-red-100 text-red-700'
  return <span className={`badge ${color}`}>{pct}%</span>
}
