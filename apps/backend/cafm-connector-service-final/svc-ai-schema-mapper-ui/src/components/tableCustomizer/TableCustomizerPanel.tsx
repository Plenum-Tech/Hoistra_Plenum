import { useState, useEffect, useCallback } from 'react'
import {
  listPlenumTables,
  getPlenumTableColumns,
  getPlenumTableRows,
  createPlenumRow,
  updatePlenumRow,
  deletePlenumRow,
  addPlenumColumn,
  dropPlenumColumn,
  type TableInfo,
  type ColumnDef,
  type TableRowsResponse,
} from '../../api'
import { Plus, Trash2, RefreshCw, ChevronLeft, ChevronRight, AlertTriangle, Pencil, Check, X } from 'lucide-react'

const PAGE_SIZE = 20

type EditState = { rowId: string; col: string; value: string }
type NewRow = Record<string, string>

export default function TableCustomizerPanel() {
  const [tables, setTables] = useState<TableInfo[]>([])
  const [selectedTable, setSelectedTable] = useState<string | null>(null)
  const [columns, setColumns] = useState<ColumnDef[]>([])
  const [rowData, setRowData] = useState<TableRowsResponse | null>(null)
  const [offset, setOffset] = useState(0)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  // Inline editing
  const [editing, setEditing] = useState<EditState | null>(null)
  const [editValue, setEditValue] = useState('')

  // New row
  const [addingRow, setAddingRow] = useState(false)
  const [newRow, setNewRow] = useState<NewRow>({})

  // Add column dialog
  const [showAddCol, setShowAddCol] = useState(false)
  const [newColName, setNewColName] = useState('')
  const [newColType, setNewColType] = useState('text')
  const [newColNullable, setNewColNullable] = useState(true)

  // Drop column confirm
  const [dropColTarget, setDropColTarget] = useState<string | null>(null)

  // Delete row confirm
  const [deleteRowTarget, setDeleteRowTarget] = useState<string | null>(null)

  useEffect(() => {
    listPlenumTables()
      .then(setTables)
      .catch(e => setError(String(e)))
  }, [])

  const loadTable = useCallback(async (table: string, off = 0) => {
    setLoading(true)
    setError(null)
    try {
      const [cols, rows] = await Promise.all([
        getPlenumTableColumns(table),
        getPlenumTableRows(table, PAGE_SIZE, off),
      ])
      setColumns(cols)
      setRowData(rows)
      setOffset(off)
    } catch (e) {
      setError(String(e))
    } finally {
      setLoading(false)
    }
  }, [])

  function selectTable(table: string) {
    setSelectedTable(table)
    setAddingRow(false)
    setNewRow({})
    setEditing(null)
    setError(null)
    loadTable(table, 0)
  }

  function extractError(e: unknown): string {
    return e instanceof Error ? e.message : String(e)
  }

  async function commitEdit() {
    if (!editing || !selectedTable) return
    try {
      await updatePlenumRow(selectedTable, editing.rowId, { [editing.col]: editValue })
      setEditing(null)
      loadTable(selectedTable, offset)
    } catch (e) {
      setError(extractError(e))
    }
  }

  async function commitNewRow() {
    if (!selectedTable) return
    const cleaned = Object.fromEntries(
      Object.entries(newRow).filter(([, v]) => v.trim() !== ''),
    )
    try {
      await createPlenumRow(selectedTable, cleaned)
      setAddingRow(false)
      setNewRow({})
      loadTable(selectedTable, offset)
    } catch (e) {
      setError(extractError(e))
    }
  }

  async function confirmDeleteRow() {
    if (!deleteRowTarget || !selectedTable) return
    try {
      await deletePlenumRow(selectedTable, deleteRowTarget)
      setDeleteRowTarget(null)
      loadTable(selectedTable, offset)
    } catch (e) {
      setError(extractError(e))
    }
  }

  async function confirmAddColumn() {
    if (!selectedTable || !newColName.trim()) return
    try {
      await addPlenumColumn(selectedTable, {
        column_name: newColName.trim(),
        data_type: newColType,
        nullable: newColNullable,
      })
      setShowAddCol(false)
      setNewColName('')
      setNewColType('text')
      loadTable(selectedTable, offset)
    } catch (e) {
      setError(extractError(e))
    }
  }

  async function confirmDropColumn() {
    if (!dropColTarget || !selectedTable) return
    try {
      await dropPlenumColumn(selectedTable, dropColTarget)
      setDropColTarget(null)
      loadTable(selectedTable, offset)
    } catch (e) {
      setError(extractError(e))
    }
  }

  const visibleCols = columns.filter(c => c.name !== 'id')
  const totalPages = rowData ? Math.ceil(rowData.total / PAGE_SIZE) : 0
  const currentPage = Math.floor(offset / PAGE_SIZE) + 1

  return (
    <div className="flex h-full gap-0">
      {/* ── Table list sidebar ── */}
      <aside className="w-56 border-r border-slate-200 bg-slate-50 overflow-y-auto shrink-0">
        <div className="px-3 py-3 border-b border-slate-200">
          <span className="text-xs font-semibold text-slate-500 uppercase tracking-wide">
            Tables
          </span>
        </div>
        {tables.map(t => (
          <button
            key={t.table}
            onClick={() => selectTable(t.table)}
            className={`w-full text-left px-3 py-2 text-xs flex items-center justify-between transition-colors ${
              selectedTable === t.table
                ? 'bg-indigo-50 text-indigo-700 font-medium'
                : 'text-slate-600 hover:bg-slate-100'
            }`}
          >
            <span className="truncate">{t.table}</span>
            <span className="text-slate-400 ml-1 shrink-0">
              {t.row_estimate > 0 ? t.row_estimate.toLocaleString() : ''}
            </span>
          </button>
        ))}
      </aside>

      {/* ── Main content ── */}
      <div className="flex-1 flex flex-col overflow-hidden">
        {error && !selectedTable && (
          <div className="m-4 rounded-lg bg-red-50 border border-red-200 px-4 py-3 text-sm text-red-700 space-y-1">
            <div className="font-medium">Cannot reach Connector Service</div>
            <div className="text-xs text-red-600">{error}</div>
            <div className="text-xs text-red-500 pt-1">
              Open <span className="font-semibold">Settings ⚙</span> (top-right) and set the{' '}
              <span className="font-semibold">Connector Service URL</span> to your Azure container URL, e.g.{' '}
              <span className="font-mono">https://your-app.azurecontainer.io</span>
            </div>
          </div>
        )}
        {!selectedTable && !error ? (
          <div className="flex-1 flex items-center justify-center text-slate-400 text-sm">
            Select a table to browse and edit its data.
          </div>
        ) : selectedTable ? (
          <>
            {/* Toolbar */}
            <div className="border-b border-slate-200 px-4 py-2 flex items-center gap-2 bg-white shrink-0">
              <span className="font-mono text-sm font-semibold text-slate-700">
                {selectedTable}
              </span>
              {rowData && (
                <span className="text-xs text-slate-400">
                  {rowData.total.toLocaleString()} rows
                </span>
              )}
              <div className="flex-1" />
              <button
                onClick={() => setShowAddCol(true)}
                className="flex items-center gap-1 px-2 py-1 text-xs rounded-md bg-slate-100 hover:bg-slate-200 text-slate-600 transition-colors"
              >
                <Plus size={12} /> Add column
              </button>
              <button
                onClick={() => { setAddingRow(true); setNewRow({}) }}
                className="flex items-center gap-1 px-2 py-1 text-xs rounded-md bg-indigo-600 hover:bg-indigo-700 text-white transition-colors"
              >
                <Plus size={12} /> Add row
              </button>
              <button
                onClick={() => loadTable(selectedTable, offset)}
                className="p-1.5 rounded hover:bg-slate-100 transition-colors"
                title="Refresh"
              >
                <RefreshCw size={13} className="text-slate-400" />
              </button>
            </div>

            {error && (
              <div className="mx-4 mt-2 rounded-lg bg-red-50 border border-red-200 px-3 py-2 text-xs text-red-700 flex items-start justify-between gap-2">
                <span className="break-all">{error}</span>
                <button
                  onClick={() => setError(null)}
                  className="shrink-0 text-red-400 hover:text-red-700 transition-colors"
                  title="Dismiss"
                >
                  <X size={13} />
                </button>
              </div>
            )}

            {/* Table */}
            <div className="flex-1 overflow-auto">
              {loading ? (
                <div className="flex items-center justify-center h-32 text-slate-400 text-sm">
                  Loading…
                </div>
              ) : rowData ? (
                <table className="w-full text-xs border-collapse">
                  <thead className="sticky top-0 bg-slate-50 z-10">
                    <tr>
                      <th className="px-2 py-2 border-b border-slate-200 text-left text-slate-400 font-medium w-8">
                        #
                      </th>
                      {visibleCols.map(col => (
                        <th
                          key={col.name}
                          className="px-2 py-2 border-b border-slate-200 text-left text-slate-600 font-medium whitespace-nowrap group"
                        >
                          <div className="flex items-center gap-1">
                            <span>{col.name}</span>
                            <span className="text-slate-400 font-normal text-xs">({col.type})</span>
                            {col.name !== 'id' && (
                              <button
                                onClick={() => setDropColTarget(col.name)}
                                className="opacity-0 group-hover:opacity-100 flex items-center text-red-400 hover:text-red-600 transition-opacity ml-1"
                                title={`Drop column '${col.name}'`}
                              >
                                <Trash2 size={10} />
                              </button>
                            )}
                          </div>
                        </th>
                      ))}
                      <th className="px-2 py-2 border-b border-slate-200 text-left text-slate-400 font-medium text-xs w-24">
                        Actions
                      </th>
                    </tr>
                  </thead>
                  <tbody>
                    {/* New row input */}
                    {addingRow && (
                      <tr className="bg-indigo-50">
                        <td className="px-2 py-1 border-b border-slate-200 text-slate-400">*</td>
                        {visibleCols.map(col => (
                          <td key={col.name} className="px-1 py-1 border-b border-slate-200">
                            <input
                              className="w-full border border-indigo-300 rounded px-1.5 py-0.5 text-xs focus:outline-none focus:ring-1 focus:ring-indigo-500 bg-white"
                              placeholder={col.nullable ? 'null' : ''}
                              value={newRow[col.name] ?? ''}
                              onChange={e => setNewRow(r => ({ ...r, [col.name]: e.target.value }))}
                            />
                          </td>
                        ))}
                        <td className="px-2 py-1 border-b border-slate-200">
                          <div className="flex gap-1">
                            <button
                              onClick={commitNewRow}
                              className="text-indigo-600 hover:text-indigo-800 font-medium"
                            >
                              Save
                            </button>
                            <button
                              onClick={() => setAddingRow(false)}
                              className="text-slate-400 hover:text-slate-600"
                            >
                              ×
                            </button>
                          </div>
                        </td>
                      </tr>
                    )}

                    {/* Data rows */}
                    {rowData.rows.map((row, idx) => {
                      const rowId = row['id'] ? String(row['id']) : null
                      const isRowEditing = editing?.rowId === rowId
                      return (
                        <tr
                          key={rowId ?? idx}
                          className={`transition-colors ${isRowEditing ? 'bg-indigo-50' : 'hover:bg-slate-50'}`}
                        >
                          <td className="px-2 py-1.5 border-b border-slate-100 text-slate-300 text-center">
                            {offset + idx + 1}
                          </td>
                          {visibleCols.map(col => {
                            const isEditingCell = isRowEditing && editing?.col === col.name
                            const cellVal = row[col.name]
                            return (
                              <td
                                key={col.name}
                                className="px-2 py-1.5 border-b border-slate-100 max-w-xs"
                                onClick={() => {
                                  if (!isEditingCell && rowId && !editing) {
                                    setEditing({ rowId, col: col.name, value: String(cellVal ?? '') })
                                    setEditValue(String(cellVal ?? ''))
                                  }
                                }}
                              >
                                {isEditingCell ? (
                                  <input
                                    autoFocus
                                    className="w-full border border-indigo-400 rounded px-1 py-0.5 text-xs focus:outline-none focus:ring-1 focus:ring-indigo-500 bg-white"
                                    value={editValue}
                                    onChange={e => setEditValue(e.target.value)}
                                    onKeyDown={e => {
                                      if (e.key === 'Enter') commitEdit()
                                      if (e.key === 'Escape') setEditing(null)
                                    }}
                                  />
                                ) : (
                                  <span
                                    className={`block truncate ${
                                      rowId && !editing ? 'cursor-pointer' : ''
                                    } ${cellVal == null ? 'text-slate-300 italic' : 'text-slate-700'}`}
                                    title={String(cellVal ?? '')}
                                  >
                                    {cellVal == null ? 'null' : String(cellVal)}
                                  </span>
                                )}
                              </td>
                            )
                          })}
                          {/* Actions column */}
                          <td className="px-2 py-1 border-b border-slate-100 whitespace-nowrap">
                            {isRowEditing ? (
                              <div className="flex items-center gap-1">
                                <button
                                  onClick={commitEdit}
                                  className="flex items-center gap-0.5 px-2 py-0.5 rounded text-xs bg-indigo-600 text-white hover:bg-indigo-700 transition-colors"
                                  title="Save (Enter)"
                                >
                                  <Check size={11} /> Save
                                </button>
                                <button
                                  onClick={() => { setEditing(null); setError(null) }}
                                  className="flex items-center gap-0.5 px-2 py-0.5 rounded text-xs border border-slate-300 text-slate-600 hover:bg-slate-100 transition-colors"
                                  title="Cancel (Esc)"
                                >
                                  <X size={11} /> Cancel
                                </button>
                              </div>
                            ) : (
                              <div className="flex items-center gap-1">
                                {rowId && (
                                  <>
                                    <button
                                      onClick={() => {
                                        if (visibleCols[0]) {
                                          setEditing({ rowId, col: visibleCols[0].name, value: String(row[visibleCols[0].name] ?? '') })
                                          setEditValue(String(row[visibleCols[0].name] ?? ''))
                                        }
                                      }}
                                      className="p-1 rounded text-slate-400 hover:text-indigo-600 hover:bg-indigo-50 transition-colors"
                                      title="Edit row (click any cell)"
                                    >
                                      <Pencil size={12} />
                                    </button>
                                    <button
                                      onClick={() => setDeleteRowTarget(rowId)}
                                      className="p-1 rounded text-slate-400 hover:text-red-600 hover:bg-red-50 transition-colors"
                                      title="Delete row"
                                    >
                                      <Trash2 size={12} />
                                    </button>
                                  </>
                                )}
                              </div>
                            )}
                          </td>
                        </tr>
                      )
                    })}

                    {rowData.rows.length === 0 && !addingRow && (
                      <tr>
                        <td
                          colSpan={visibleCols.length + 2}
                          className="text-center py-8 text-slate-400"
                        >
                          No rows found.
                        </td>
                      </tr>
                    )}
                  </tbody>
                </table>
              ) : null}
            </div>

            {/* Pagination */}
            {rowData && rowData.total > PAGE_SIZE && (
              <div className="border-t border-slate-200 px-4 py-2 flex items-center gap-3 bg-white shrink-0">
                <button
                  disabled={offset === 0}
                  onClick={() => loadTable(selectedTable, offset - PAGE_SIZE)}
                  className="p-1 rounded hover:bg-slate-100 disabled:opacity-40 transition-colors"
                >
                  <ChevronLeft size={14} />
                </button>
                <span className="text-xs text-slate-500">
                  Page {currentPage} of {totalPages}
                </span>
                <button
                  disabled={offset + PAGE_SIZE >= rowData.total}
                  onClick={() => loadTable(selectedTable, offset + PAGE_SIZE)}
                  className="p-1 rounded hover:bg-slate-100 disabled:opacity-40 transition-colors"
                >
                  <ChevronRight size={14} />
                </button>
              </div>
            )}
          </>
        ) : null}
      </div>

      {/* ── Add column dialog ── */}
      {showAddCol && (
        <div className="fixed inset-0 bg-black/30 flex items-center justify-center z-50">
          <div className="bg-white rounded-xl shadow-xl p-6 w-80 space-y-4">
            <h3 className="font-semibold text-slate-800">Add Column</h3>
            <div>
              <label className="block text-xs font-medium text-slate-500 mb-1">Column name</label>
              <input
                className="w-full border border-slate-300 rounded-lg px-3 py-1.5 text-sm focus:outline-none focus:ring-1 focus:ring-indigo-500"
                value={newColName}
                onChange={e => setNewColName(e.target.value)}
                placeholder="e.g. description"
              />
            </div>
            <div>
              <label className="block text-xs font-medium text-slate-500 mb-1">Data type</label>
              <select
                className="w-full border border-slate-300 rounded-lg px-3 py-1.5 text-sm focus:outline-none focus:ring-1 focus:ring-indigo-500"
                value={newColType}
                onChange={e => setNewColType(e.target.value)}
              >
                <option value="text">text</option>
                <option value="varchar(255)">varchar(255)</option>
                <option value="integer">integer</option>
                <option value="bigint">bigint</option>
                <option value="boolean">boolean</option>
                <option value="numeric(18,2)">numeric(18,2)</option>
                <option value="date">date</option>
                <option value="timestamptz">timestamptz</option>
                <option value="uuid">uuid</option>
                <option value="jsonb">jsonb</option>
              </select>
            </div>
            <label className="flex items-center gap-2 text-sm text-slate-600">
              <input
                type="checkbox"
                checked={newColNullable}
                onChange={e => setNewColNullable(e.target.checked)}
                className="rounded"
              />
              Nullable
            </label>
            <div className="flex gap-2 pt-1">
              <button
                onClick={confirmAddColumn}
                disabled={!newColName.trim()}
                className="flex-1 px-3 py-2 rounded-lg bg-indigo-600 text-white text-sm font-medium hover:bg-indigo-700 disabled:opacity-40 transition-colors"
              >
                Add Column
              </button>
              <button
                onClick={() => setShowAddCol(false)}
                className="px-3 py-2 rounded-lg border border-slate-200 text-sm text-slate-600 hover:bg-slate-50 transition-colors"
              >
                Cancel
              </button>
            </div>
          </div>
        </div>
      )}

      {/* ── Drop column confirm ── */}
      {dropColTarget && (
        <div className="fixed inset-0 bg-black/30 flex items-center justify-center z-50">
          <div className="bg-white rounded-xl shadow-xl p-6 w-80 space-y-4">
            <div className="flex items-start gap-3">
              <AlertTriangle size={20} className="text-red-500 shrink-0 mt-0.5" />
              <div>
                <h3 className="font-semibold text-slate-800">Drop Column</h3>
                <p className="text-sm text-slate-500 mt-1">
                  Permanently drop <span className="font-mono font-semibold">{dropColTarget}</span>{' '}
                  from <span className="font-mono font-semibold">{selectedTable}</span>? This
                  cannot be undone.
                </p>
              </div>
            </div>
            <div className="flex gap-2">
              <button
                onClick={confirmDropColumn}
                className="flex-1 px-3 py-2 rounded-lg bg-red-600 text-white text-sm font-medium hover:bg-red-700 transition-colors"
              >
                Drop Column
              </button>
              <button
                onClick={() => setDropColTarget(null)}
                className="px-3 py-2 rounded-lg border border-slate-200 text-sm text-slate-600 hover:bg-slate-50 transition-colors"
              >
                Cancel
              </button>
            </div>
          </div>
        </div>
      )}

      {/* ── Delete row confirm ── */}
      {deleteRowTarget && (
        <div className="fixed inset-0 bg-black/30 flex items-center justify-center z-50">
          <div className="bg-white rounded-xl shadow-xl p-6 w-72 space-y-4">
            <div className="flex items-start gap-3">
              <AlertTriangle size={20} className="text-red-500 shrink-0 mt-0.5" />
              <div>
                <h3 className="font-semibold text-slate-800">Delete Row</h3>
                <p className="text-sm text-slate-500 mt-1">
                  Delete row{' '}
                  <span className="font-mono text-xs">{deleteRowTarget.slice(0, 8)}…</span>?
                </p>
              </div>
            </div>
            <div className="flex gap-2">
              <button
                onClick={confirmDeleteRow}
                className="flex-1 px-3 py-2 rounded-lg bg-red-600 text-white text-sm font-medium hover:bg-red-700 transition-colors"
              >
                Delete
              </button>
              <button
                onClick={() => setDeleteRowTarget(null)}
                className="px-3 py-2 rounded-lg border border-slate-200 text-sm text-slate-600 hover:bg-slate-50 transition-colors"
              >
                Cancel
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
