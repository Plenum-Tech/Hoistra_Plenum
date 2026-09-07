import { useState, useEffect, useRef } from 'react'
import {
  Database, Upload, Trash2, AlertCircle, CheckCircle2, RefreshCw,
  FileSpreadsheet, ChevronDown, ChevronUp, Server, Download,
} from 'lucide-react'
import {
  listRowIndexTables, uploadRowIndexFile, deleteRowIndexTable,
  listDbTables, getDbTableColumns, importDbTable,
} from '../../api'
import type { RowIndexTable, RowIndexUploadResponse, DbTable, DbTableColumn } from '../../types'

// ── Upload form ────────────────────────────────────────────────────────────────

function UploadForm({ onSuccess }: { onSuccess: () => void }) {
  const [file, setFile] = useState<File | null>(null)
  const [tableName, setTableName] = useState('')
  const [pkColumn, setPkColumn] = useState('')
  const [uploading, setUploading] = useState(false)
  const [result, setResult] = useState<RowIndexUploadResponse | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [dragging, setDragging] = useState(false)
  const inputRef = useRef<HTMLInputElement>(null)

  function pickFile(f: File) {
    setFile(f)
    setResult(null)
    setError(null)
    // Auto-fill table name from filename (strip extension)
    if (!tableName) {
      const base = f.name.replace(/\.[^.]+$/, '').replace(/[^a-zA-Z0-9_]/g, '_')
      setTableName(base)
    }
  }

  async function handleUpload() {
    if (!file || !tableName.trim() || !pkColumn.trim()) return
    setUploading(true)
    setError(null)
    setResult(null)
    try {
      const r = await uploadRowIndexFile(file, tableName.trim(), pkColumn.trim())
      setResult(r)
      onSuccess()
    } catch (e: any) {
      setError(e.message ?? 'Upload failed')
    } finally {
      setUploading(false)
    }
  }

  const canUpload = file && tableName.trim() && pkColumn.trim() && !uploading

  return (
    <div className="space-y-4">
      {/* File drop zone */}
      <div
        onDragOver={e => { e.preventDefault(); setDragging(true) }}
        onDragLeave={() => setDragging(false)}
        onDrop={e => { e.preventDefault(); setDragging(false); const f = e.dataTransfer.files[0]; if (f) pickFile(f) }}
        onClick={() => inputRef.current?.click()}
        className={`border-2 border-dashed rounded-xl p-6 text-center cursor-pointer transition-all ${
          dragging ? 'border-indigo-400 bg-indigo-50' : 'border-slate-200 hover:border-indigo-300 hover:bg-slate-50'
        }`}
      >
        <input
          ref={inputRef}
          type="file"
          className="hidden"
          accept=".csv,.xlsx,.xls"
          onChange={e => { if (e.target.files?.[0]) pickFile(e.target.files[0]) }}
        />
        <FileSpreadsheet size={22} className="mx-auto text-slate-400 mb-2" />
        {file ? (
          <div>
            <p className="text-sm font-medium text-slate-700">{file.name}</p>
            <p className="text-xs text-slate-400 mt-0.5">{(file.size / 1024).toFixed(1)} KB — click to change</p>
          </div>
        ) : (
          <div>
            <p className="text-sm font-medium text-slate-600">Drop a CSV or Excel file, or click to browse</p>
            <p className="text-xs text-slate-400 mt-0.5">.csv · .xlsx · .xls</p>
          </div>
        )}
      </div>

      {/* Form fields */}
      <div className="grid grid-cols-2 gap-3">
        <div>
          <label className="block text-xs font-medium text-slate-600 mb-1">
            Table name <span className="text-red-400">*</span>
          </label>
          <input
            className="input w-full text-sm"
            placeholder="e.g. assets, equipment"
            value={tableName}
            onChange={e => setTableName(e.target.value)}
          />
          <p className="text-xs text-slate-400 mt-1">Logical name for this dataset in the index</p>
        </div>
        <div>
          <label className="block text-xs font-medium text-slate-600 mb-1">
            Primary key column <span className="text-red-400">*</span>
          </label>
          <input
            className="input w-full text-sm"
            placeholder="e.g. asset_code, id"
            value={pkColumn}
            onChange={e => setPkColumn(e.target.value)}
          />
          <p className="text-xs text-slate-400 mt-1">Column that uniquely identifies each row</p>
        </div>
      </div>

      {error && (
        <div className="flex items-start gap-2 rounded-lg bg-red-50 border border-red-200 px-3 py-2.5">
          <AlertCircle size={14} className="text-red-500 shrink-0 mt-0.5" />
          <span className="text-sm text-red-700">{error}</span>
        </div>
      )}

      {result && (
        <div className="rounded-xl border border-emerald-200 bg-emerald-50 px-4 py-3">
          <div className="flex items-center gap-2 mb-2">
            <CheckCircle2 size={15} className="text-emerald-600" />
            <span className="text-sm font-semibold text-emerald-800">Index updated</span>
          </div>
          <div className="grid grid-cols-2 gap-x-4 gap-y-1 text-xs text-emerald-700">
            <span>Table: <span className="font-medium">{result.table_name}</span></span>
            <span>PK column: <span className="font-medium">{result.pk_column}</span></span>
            <span>Inserted: <span className="font-medium">{result.rows_inserted}</span></span>
            <span>Updated: <span className="font-medium">{result.rows_updated}</span></span>
            <span className="col-span-2">
              Total rows in index: <span className="font-medium">{result.total_rows_in_index}</span>
            </span>
            <span className="col-span-2 text-emerald-600">
              Columns: {result.columns_detected.join(', ')}
            </span>
          </div>
        </div>
      )}

      <button
        onClick={handleUpload}
        disabled={!canUpload}
        className="btn-primary flex items-center gap-2 px-5 py-2.5 disabled:opacity-40 disabled:cursor-not-allowed"
      >
        {uploading ? (
          <span className="animate-spin inline-block w-4 h-4 border-2 border-white border-t-transparent rounded-full" />
        ) : (
          <Upload size={15} />
        )}
        {uploading ? 'Uploading & indexing…' : 'Upload & index'}
      </button>
    </div>
  )
}

// ── Import from database ───────────────────────────────────────────────────────

function ImportFromDbForm({ onSuccess }: { onSuccess: () => void }) {
  const [dbTables, setDbTables]       = useState<DbTable[]>([])
  const [loadingTables, setLoadingTables] = useState(false)
  const [tablesError, setTablesError] = useState<string | null>(null)

  const [selectedTable, setSelectedTable] = useState('')
  const [columns, setColumns]         = useState<DbTableColumn[]>([])
  const [loadingCols, setLoadingCols] = useState(false)
  const [pkColumn, setPkColumn]       = useState('')

  const [importing, setImporting]     = useState(false)
  const [result, setResult]           = useState<RowIndexUploadResponse | null>(null)
  const [error, setError]             = useState<string | null>(null)

  async function loadTables() {
    setLoadingTables(true)
    setTablesError(null)
    try {
      const t = await listDbTables()
      setDbTables(t)
    } catch (e: any) {
      setTablesError(e.message ?? 'Could not load database tables')
    } finally {
      setLoadingTables(false)
    }
  }

  useEffect(() => { loadTables() }, [])

  async function handleTableSelect(tbl: string) {
    setSelectedTable(tbl)
    setPkColumn('')
    setColumns([])
    setResult(null)
    setError(null)
    if (!tbl) return
    setLoadingCols(true)
    try {
      const cols = await getDbTableColumns(tbl)
      setColumns(cols)
      // Auto-select first column that looks like a PK
      const guess = cols.find(c =>
        /id$|_code$|_key$|_pk$|^id$/.test(c.name.toLowerCase())
      )
      if (guess) setPkColumn(guess.name)
    } catch (e: any) {
      setError(e.message ?? 'Could not fetch columns')
    } finally {
      setLoadingCols(false)
    }
  }

  async function handleImport() {
    if (!selectedTable || !pkColumn) return
    setImporting(true)
    setError(null)
    setResult(null)
    try {
      const r = await importDbTable(selectedTable, pkColumn)
      setResult(r)
      onSuccess()
    } catch (e: any) {
      setError(e.message ?? 'Import failed')
    } finally {
      setImporting(false)
    }
  }

  return (
    <div className="space-y-4">
      <p className="text-xs text-slate-500">
        Import a table directly from the connected database into the row index — no CSV export needed.
        Rows are upserted so you can re-import to refresh the index.
      </p>

      {/* Table picker */}
      <div>
        <label className="block text-xs font-medium text-slate-600 mb-1">
          Database table <span className="text-red-400">*</span>
        </label>
        {tablesError ? (
          <div className="flex items-center gap-2 text-xs text-red-600 py-1">
            <AlertCircle size={12} /> {tablesError}
            <button onClick={loadTables} className="underline ml-1">Retry</button>
          </div>
        ) : (
          <div className="flex gap-2">
            <select
              className="input flex-1 text-sm"
              value={selectedTable}
              onChange={e => handleTableSelect(e.target.value)}
              disabled={loadingTables}
            >
              <option value="">{loadingTables ? 'Loading tables…' : '— select a table —'}</option>
              {dbTables.map(t => (
                <option key={t.table_name} value={t.table_name}>
                  {t.table_name}{t.row_count != null ? ` (${t.row_count.toLocaleString()} rows)` : ''}
                </option>
              ))}
            </select>
            <button
              onClick={loadTables}
              disabled={loadingTables}
              className="p-2 rounded hover:bg-slate-100 text-slate-400 hover:text-slate-600 transition-colors"
              title="Refresh table list"
            >
              <RefreshCw size={14} className={loadingTables ? 'animate-spin' : ''} />
            </button>
          </div>
        )}
      </div>

      {/* Column picker — only shown once a table is selected */}
      {selectedTable && (
        <div>
          <label className="block text-xs font-medium text-slate-600 mb-1">
            Primary key column <span className="text-red-400">*</span>
          </label>
          {loadingCols ? (
            <p className="text-xs text-slate-400">Loading columns…</p>
          ) : (
            <>
              <select
                className="input w-full text-sm"
                value={pkColumn}
                onChange={e => setPkColumn(e.target.value)}
              >
                <option value="">— select PK column —</option>
                {columns.map(c => (
                  <option key={c.name} value={c.name}>
                    {c.name} <span className="text-slate-400">({c.type})</span>
                  </option>
                ))}
              </select>
              {columns.length > 0 && (
                <p className="text-xs text-slate-400 mt-1">
                  {columns.length} columns detected · choose the one that uniquely identifies each row
                </p>
              )}
            </>
          )}
        </div>
      )}

      {error && (
        <div className="flex items-start gap-2 rounded-lg bg-red-50 border border-red-200 px-3 py-2.5">
          <AlertCircle size={14} className="text-red-500 shrink-0 mt-0.5" />
          <span className="text-sm text-red-700">{error}</span>
        </div>
      )}

      {result && (
        <div className="rounded-xl border border-emerald-200 bg-emerald-50 px-4 py-3">
          <div className="flex items-center gap-2 mb-2">
            <CheckCircle2 size={15} className="text-emerald-600" />
            <span className="text-sm font-semibold text-emerald-800">Import complete</span>
          </div>
          <div className="grid grid-cols-2 gap-x-4 gap-y-1 text-xs text-emerald-700">
            <span>Table: <span className="font-medium">{result.table_name}</span></span>
            <span>PK column: <span className="font-medium">{result.pk_column}</span></span>
            <span>Inserted: <span className="font-medium">{result.rows_inserted}</span></span>
            <span>Updated: <span className="font-medium">{result.rows_updated}</span></span>
            <span className="col-span-2">
              Total rows in index: <span className="font-medium">{result.total_rows_in_index}</span>
            </span>
          </div>
        </div>
      )}

      <button
        onClick={handleImport}
        disabled={!selectedTable || !pkColumn || importing}
        className="btn-primary flex items-center gap-2 px-5 py-2.5 disabled:opacity-40 disabled:cursor-not-allowed"
      >
        {importing ? (
          <span className="animate-spin inline-block w-4 h-4 border-2 border-white border-t-transparent rounded-full" />
        ) : (
          <Download size={15} />
        )}
        {importing ? 'Importing & indexing…' : 'Import & index'}
      </button>
    </div>
  )
}

// ── Table row ──────────────────────────────────────────────────────────────────

function TableRow({
  table,
  onDeleted,
}: {
  table: RowIndexTable
  onDeleted: () => void
}) {
  const [confirming, setConfirming] = useState(false)
  const [deleting, setDeleting] = useState(false)
  const [error, setError] = useState<string | null>(null)

  async function handleDelete() {
    setDeleting(true)
    setError(null)
    try {
      await deleteRowIndexTable(table.source_table)
      onDeleted()
    } catch (e: any) {
      setError(e.message ?? 'Delete failed')
      setDeleting(false)
      setConfirming(false)
    }
  }

  return (
    <div className="flex items-center gap-3 px-4 py-3 hover:bg-slate-50 transition-colors">
      <div className="w-8 h-8 rounded-lg bg-indigo-50 flex items-center justify-center shrink-0">
        <Database size={14} className="text-indigo-500" />
      </div>
      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-2">
          <span className="text-sm font-semibold text-slate-800 font-mono">{table.source_table}</span>
          <span className="text-xs bg-slate-100 text-slate-500 px-2 py-0.5 rounded-full">
            {table.row_count} row{table.row_count !== 1 ? 's' : ''}
          </span>
        </div>
        {error && <p className="text-xs text-red-600 mt-0.5">{error}</p>}
      </div>
      <div className="shrink-0">
        {!confirming ? (
          <button
            onClick={() => setConfirming(true)}
            className="p-1.5 rounded hover:bg-red-50 text-slate-400 hover:text-red-500 transition-colors"
            title="Remove table from index"
          >
            <Trash2 size={14} />
          </button>
        ) : (
          <div className="flex items-center gap-2">
            <span className="text-xs text-slate-500">Remove?</span>
            <button
              onClick={handleDelete}
              disabled={deleting}
              className="px-2 py-1 rounded text-xs font-medium bg-red-600 text-white hover:bg-red-700 disabled:opacity-50"
            >
              {deleting ? '…' : 'Yes'}
            </button>
            <button
              onClick={() => setConfirming(false)}
              className="px-2 py-1 rounded text-xs font-medium bg-slate-100 text-slate-600 hover:bg-slate-200"
            >
              No
            </button>
          </div>
        )}
      </div>
    </div>
  )
}

// ── Main tab ───────────────────────────────────────────────────────────────────

export default function DocIndexTab() {
  const [tables, setTables]       = useState<RowIndexTable[]>([])
  const [loading, setLoading]     = useState(false)
  const [showUpload, setShowUpload] = useState(false)
  const [showImport, setShowImport] = useState(false)

  async function fetchTables() {
    setLoading(true)
    try {
      const t = await listRowIndexTables()
      setTables(t)
    } catch {
      // ignore — empty state shown
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => { fetchTables() }, [])

  return (
    <div className="space-y-6">

      {/* ── Import from database ── */}
      <div className="rounded-xl border border-indigo-200 bg-white p-5">
        <button
          onClick={() => setShowImport(v => !v)}
          className="w-full flex items-center justify-between text-left"
        >
          <div className="flex items-center gap-2">
            <Server size={16} className="text-indigo-500" />
            <span className="text-sm font-semibold text-slate-700">Import from database</span>
            <span className="text-xs bg-indigo-100 text-indigo-600 px-2 py-0.5 rounded-full font-medium">recommended</span>
          </div>
          {showImport
            ? <ChevronUp size={15} className="text-slate-400" />
            : <ChevronDown size={15} className="text-slate-400" />}
        </button>
        {showImport && (
          <div className="mt-4 border-t border-slate-100 pt-4">
            <ImportFromDbForm onSuccess={() => { fetchTables(); }} />
          </div>
        )}
      </div>

      {/* ── Upload CSV / Excel ── */}
      <div className="rounded-xl border border-slate-200 bg-white p-5">
        <button
          onClick={() => setShowUpload(v => !v)}
          className="w-full flex items-center justify-between text-left"
        >
          <div className="flex items-center gap-2">
            <Upload size={16} className="text-slate-500" />
            <span className="text-sm font-semibold text-slate-700">Upload CSV / Excel data file</span>
          </div>
          {showUpload
            ? <ChevronUp size={15} className="text-slate-400" />
            : <ChevronDown size={15} className="text-slate-400" />}
        </button>
        {showUpload && (
          <div className="mt-4 border-t border-slate-100 pt-4">
            <p className="text-xs text-slate-500 mb-4">
              Upload a CSV or Excel file to add its rows to the match index. Documents can then be
              matched against these rows to find which records are referenced in the text.
            </p>
            <UploadForm onSuccess={() => { fetchTables(); setShowUpload(false) }} />
          </div>
        )}
      </div>

      {/* ── Indexed tables list ── */}
      <div>
        <div className="flex items-center justify-between mb-3">
          <h3 className="text-sm font-semibold text-slate-700">Indexed tables</h3>
          <button
            onClick={fetchTables}
            className="p-1.5 rounded hover:bg-slate-100 text-slate-400 hover:text-slate-600 transition-colors"
            title="Refresh"
          >
            <RefreshCw size={13} className={loading ? 'animate-spin' : ''} />
          </button>
        </div>

        {tables.length === 0 ? (
          <div className="rounded-xl border border-dashed border-slate-200 py-10 text-center">
            <Database size={24} className="mx-auto text-slate-300 mb-2" />
            <p className="text-sm text-slate-400">No data tables indexed yet</p>
            <p className="text-xs text-slate-400 mt-1">
              Import a database table or upload a CSV/Excel file above to get started
            </p>
          </div>
        ) : (
          <div className="rounded-xl border border-slate-200 overflow-hidden divide-y divide-slate-100">
            {tables.map(t => (
              <TableRow
                key={t.source_table}
                table={t}
                onDeleted={fetchTables}
              />
            ))}
          </div>
        )}
      </div>
    </div>
  )
}
