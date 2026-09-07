import { useState, useEffect, useRef } from 'react'
import { Link2, AlertCircle, ChevronDown, ChevronUp, Play, Table2, Database, Upload, CheckSquare, Square, CheckCircle2, XCircle } from 'lucide-react'
import { matchDocumentRows, matchDocumentRowsFromFile, listRowIndexTables, confirmDocumentMatches } from '../../api'
import type { ConfirmMatchesResult } from '../../api'
import type { DocRagDocument, DocRagMatchedRow, DocRagMatchRowsResponse, RowIndexTable } from '../../types'

// ── Helpers ───────────────────────────────────────────────────────────────────

function confClass(c: number) {
  if (c >= 0.6) return { border: 'border-emerald-200', bg: 'bg-emerald-50', badge: 'bg-emerald-100 text-emerald-700', bar: 'bg-emerald-500' }
  if (c >= 0.3) return { border: 'border-amber-200', bg: 'bg-amber-50', badge: 'bg-amber-100 text-amber-700', bar: 'bg-amber-400' }
  return { border: 'border-slate-200', bg: 'bg-white', badge: 'bg-slate-100 text-slate-600', bar: 'bg-slate-400' }
}

function ConfBar({ value }: { value: number }) {
  const cls = confClass(value)
  return (
    <div className="flex items-center gap-2 mt-1">
      <div className="flex-1 h-1 bg-slate-200 rounded-full overflow-hidden">
        <div className={`h-full rounded-full ${cls.bar}`} style={{ width: `${Math.round(value * 100)}%` }} />
      </div>
      <span className="text-xs text-slate-500 tabular-nums">{(value * 100).toFixed(0)}%</span>
    </div>
  )
}

function MethodBadge({ method }: { method: string }) {
  const colors: Record<string, string> = {
    exact_key:       'bg-indigo-100 text-indigo-700',
    normalized_key:  'bg-blue-100 text-blue-700',
    semantic:        'bg-purple-100 text-purple-700',
    metadata_match:  'bg-teal-100 text-teal-700',
    bm25:            'bg-orange-100 text-orange-700',
    hybrid:          'bg-slate-100 text-slate-600',
  }
  return (
    <span className={`px-1.5 py-0.5 rounded text-xs font-medium ${colors[method] ?? 'bg-slate-100 text-slate-600'}`}>
      {method.replace(/_/g, ' ')}
    </span>
  )
}

// ── Weights used by the backend match service ─────────────────────────────────
const W_SEM  = 0.40
const W_BM25 = 0.30
const W_META = 0.30

// ── Score pill pair: shows semantic vs BM25 side by side ─────────────────────

function ScorePair({
  semantic,
  bm25,
  metadata,
  size = 'md',
}: {
  semantic: number
  bm25: number
  metadata?: number
  size?: 'sm' | 'md'
}) {
  const winner = semantic >= bm25 ? 'sem' : 'bm25'
  const numSz  = size === 'sm' ? 'text-xs font-bold' : 'text-sm font-bold'
  return (
    <div className="flex items-center gap-1">
      {/* Semantic */}
      <div
        className={`flex items-center gap-0.5 px-1.5 py-0.5 rounded ${winner === 'sem' ? 'bg-purple-100' : 'bg-slate-100'}`}
        title="Semantic score — meaning similarity (weight 40%)"
      >
        <span className={`text-xs ${winner === 'sem' ? 'text-purple-500' : 'text-slate-400'}`}>sem</span>
        <span className={`${numSz} tabular-nums ${winner === 'sem' ? 'text-purple-700' : 'text-slate-500'}`}>
          {(semantic * 100).toFixed(0)}
        </span>
      </div>
      <span className="text-slate-300 text-xs">vs</span>
      {/* BM25 */}
      <div
        className={`flex items-center gap-0.5 px-1.5 py-0.5 rounded ${winner === 'bm25' ? 'bg-orange-100' : 'bg-slate-100'}`}
        title="BM25 score — exact keyword overlap (weight 30%)"
      >
        <span className={`text-xs ${winner === 'bm25' ? 'text-orange-500' : 'text-slate-400'}`}>bm25</span>
        <span className={`${numSz} tabular-nums ${winner === 'bm25' ? 'text-orange-700' : 'text-slate-500'}`}>
          {(bm25 * 100).toFixed(0)}
        </span>
      </div>
      {metadata != null && (
        <>
          <span className="text-slate-300 text-xs">·</span>
          <div
            className="flex items-center gap-0.5 px-1.5 py-0.5 rounded bg-slate-100"
            title="Metadata score — field value matches (weight 30%)"
          >
            <span className="text-xs text-slate-400">meta</span>
            <span className="text-xs font-bold tabular-nums text-slate-500">{(metadata * 100).toFixed(0)}</span>
          </div>
        </>
      )}
    </div>
  )
}

// ── Weighted contribution bar ──────────────────────────────────────────────────
// Shows what % of the final confidence each signal actually drove, based on
// the backend weights: semantic=40%, BM25=30%, metadata=30%.

function ContributionBar({
  semantic,
  bm25,
  metadata,
}: {
  semantic: number
  bm25: number
  metadata: number
}) {
  const semC  = W_SEM  * semantic
  const bm25C = W_BM25 * bm25
  const metaC = W_META * metadata
  const total = semC + bm25C + metaC || 1

  const semPct  = (semC  / total) * 100
  const bm25Pct = (bm25C / total) * 100
  const metaPct = (metaC / total) * 100

  const driver     = semPct >= bm25Pct && semPct >= metaPct ? 'semantic' : bm25Pct >= metaPct ? 'keyword' : 'metadata'
  const driverPct  = Math.max(semPct, bm25Pct, metaPct)
  const driverColor = driver === 'semantic' ? 'text-purple-600' : driver === 'keyword' ? 'text-orange-600' : 'text-teal-600'

  return (
    <div className="flex items-center gap-2 mt-1">
      {/* Segmented bar */}
      <div
        className="flex h-2 rounded-full overflow-hidden w-28 shrink-0"
        title={`Semantic ${semPct.toFixed(0)}% · BM25 ${bm25Pct.toFixed(0)}% · Meta ${metaPct.toFixed(0)}%`}
      >
        <div className="bg-purple-400" style={{ width: `${semPct}%` }} />
        <div className="bg-orange-400" style={{ width: `${bm25Pct}%` }} />
        <div className="bg-teal-400"   style={{ width: `${metaPct}%` }} />
      </div>
      {/* "driven by" label */}
      <span className="text-xs text-slate-400 whitespace-nowrap">
        driven by{' '}
        <span className={`font-semibold ${driverColor}`}>
          {driver} ({driverPct.toFixed(0)}%)
        </span>
      </span>
    </div>
  )
}

// ── Row card ──────────────────────────────────────────────────────────────────

function RowCard({ row }: { row: DocRagMatchedRow }) {
  const [expanded, setExpanded] = useState(false)
  const cls = confClass(row.confidence)

  return (
    <div className={`rounded-xl border ${cls.border} ${cls.bg} overflow-hidden`}>
      {/* Header row */}
      <button
        onClick={() => setExpanded(e => !e)}
        className="w-full flex items-start gap-3 px-4 py-3 text-left hover:bg-black/5 transition-colors"
      >
        <div className="flex-1 min-w-0">
          {/* Top line: PK + method + chunk count */}
          <div className="flex items-center gap-2 flex-wrap">
            <span className="font-mono text-sm font-bold text-slate-800">{row.row_pk}</span>
            <span className="text-xs text-slate-400">{row.source_table}</span>
            <MethodBadge method={row.match_method} />
            {(row.chunk_count ?? 0) > 0 && (
              <span className="text-xs text-slate-400">{row.chunk_count} chunk{row.chunk_count !== 1 ? 's' : ''}</span>
            )}
          </div>
          {/* Score line: confidence bar + sem vs bm25 */}
          <div className="flex items-center gap-3 mt-1.5 flex-wrap">
            <div className="flex items-center gap-2 flex-1 min-w-0">
              <div className="flex-1 h-1 bg-slate-200 rounded-full overflow-hidden max-w-[80px]">
                <div className={`h-full rounded-full ${cls.bar}`} style={{ width: `${Math.round(row.confidence * 100)}%` }} />
              </div>
              <span className="text-xs font-bold text-slate-600 tabular-nums">{(row.confidence * 100).toFixed(0)}%</span>
            </div>
            {row.match_details && (
              <div>
                <ScorePair
                  semantic={row.match_details.semantic_score}
                  bm25={row.match_details.bm25_overlap}
                  metadata={row.match_details.metadata_overlap}
                />
                <ContributionBar
                  semantic={row.match_details.semantic_score}
                  bm25={row.match_details.bm25_overlap}
                  metadata={row.match_details.metadata_overlap ?? 0}
                />
              </div>
            )}
          </div>
        </div>
        {expanded ? <ChevronUp size={14} className="text-slate-400 shrink-0 mt-1" /> : <ChevronDown size={14} className="text-slate-400 shrink-0 mt-1" />}
      </button>

      {expanded && (
        <div className="border-t border-slate-200 px-4 pb-4 pt-3 space-y-4">
          {/* Row data */}
          <div>
            <h4 className="text-xs font-semibold text-slate-500 uppercase tracking-wider mb-2">Row data</h4>
            <div className="grid grid-cols-2 gap-x-6 gap-y-1">
              {Object.entries(row.row_data).map(([k, v]) => (
                <div key={k} className="text-xs">
                  <span className="text-slate-400">{k}</span>
                  <span className="mx-1 text-slate-300">·</span>
                  <span className="text-slate-700 font-medium">{String(v ?? '—')}</span>
                </div>
              ))}
            </div>
          </div>

          {/* Key match flags */}
          {row.match_details && (row.match_details.exact_key_match || row.match_details.normalized_key_match) && (
            <div className="flex gap-2">
              {row.match_details.exact_key_match && (
                <span className="text-xs bg-indigo-50 text-indigo-600 px-2 py-0.5 rounded">✓ exact key</span>
              )}
              {row.match_details.normalized_key_match && (
                <span className="text-xs bg-blue-50 text-blue-600 px-2 py-0.5 rounded">✓ normalized key</span>
              )}
            </div>
          )}

          {/* Matched metadata fields */}
          {row.matched_metadata_fields && row.matched_metadata_fields.length > 0 && (
            <div>
              <h4 className="text-xs font-semibold text-slate-500 uppercase tracking-wider mb-2">Matched fields</h4>
              <div className="flex flex-wrap gap-1.5">
                {row.matched_metadata_fields.map((f, i) => (
                  <span key={i} className="text-xs bg-teal-50 text-teal-700 border border-teal-200 px-2 py-0.5 rounded-full">{f}</span>
                ))}
              </div>
            </div>
          )}

          {/* Evidence */}
          {row.evidence && (
            <div>
              <h4 className="text-xs font-semibold text-slate-500 uppercase tracking-wider mb-1">Evidence</h4>
              <p className="text-xs text-slate-500 italic">"{row.evidence}"</p>
            </div>
          )}

          {/* Chunk matches — each shows sem vs bm25 inline */}
          {row.chunk_matches && row.chunk_matches.length > 0 && (
            <div>
              <h4 className="text-xs font-semibold text-slate-500 uppercase tracking-wider mb-2">
                Chunk matches ({row.chunk_matches.length})
              </h4>
              <div className="space-y-2">
                {row.chunk_matches.map((cm, i) => (
                  <div key={i} className="rounded-lg bg-white border border-slate-200 px-3 py-2.5">
                    {/* Chunk header: index + page + conf + sem vs bm25 */}
                    <div className="flex items-center gap-3 flex-wrap mb-1.5">
                      <span className="text-xs text-slate-400 font-mono">chunk #{cm.chunk_index}</span>
                      {cm.page_number != null && (
                        <span className="text-xs text-slate-400">p.{cm.page_number}</span>
                      )}
                      <span className="text-xs font-bold text-slate-700 tabular-nums">
                        {(cm.confidence * 100).toFixed(0)}% conf
                      </span>
                      <ScorePair
                        semantic={cm.semantic_score}
                        bm25={cm.bm25_score}
                        metadata={cm.metadata_score}
                        size="sm"
                      />
                    </div>
                    {cm.matched_fields.length > 0 && (
                      <div className="flex flex-wrap gap-1 mb-1.5">
                        {cm.matched_fields.map((f, fi) => (
                          <span key={fi} className="bg-teal-50 text-teal-700 px-1.5 py-0.5 rounded text-xs">{f}</span>
                        ))}
                      </div>
                    )}
                    <p className="text-xs text-slate-500">{cm.chunk_text_preview}</p>
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  )
}

// ── Table summary pill ────────────────────────────────────────────────────────

function TablePill({ name, count, active, onClick }: { name: string; count: number; active: boolean; onClick: () => void }) {
  return (
    <button
      onClick={onClick}
      className={`flex items-center gap-1.5 px-3 py-1.5 rounded-full text-xs font-medium transition-colors ${
        active ? 'bg-indigo-600 text-white' : 'bg-slate-100 text-slate-600 hover:bg-slate-200'
      }`}
    >
      <Table2 size={11} />
      {name}
      <span className={`px-1.5 py-0.5 rounded-full text-xs font-bold ${active ? 'bg-indigo-500 text-white' : 'bg-slate-200 text-slate-600'}`}>
        {count}
      </span>
    </button>
  )
}

// ── Main tab ─────────────────────────────────────────────────────────────────

type RowSource = 'database' | 'file'

interface Props {
  docs: DocRagDocument[]
}

export default function DocMatchTab({ docs }: Props) {
  const [selectedDocId, setSelectedDocId] = useState<string>('')
  const [threshold, setThreshold] = useState(0.25)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [result, setResult] = useState<DocRagMatchRowsResponse | null>(null)
  const [activeTable, setActiveTable] = useState<string | null>(null)

  // DB-based row source state
  const [indexTables, setIndexTables] = useState<RowIndexTable[]>([])
  const [selectedTable, setSelectedTable] = useState<string>('')

  // Source toggle
  const [rowSource, setRowSource] = useState<RowSource>('database')

  // File-based row source state
  const fileInputRef = useRef<HTMLInputElement>(null)
  const [uploadedFile, setUploadedFile] = useState<File | null>(null)
  const [pkColumn, setPkColumn] = useState<string>('')
  const [fileTableLabel, setFileTableLabel] = useState<string>('')

  // Confirm state — set of "source_table::row_pk" keys the user has checked
  const [checkedKeys, setCheckedKeys] = useState<Set<string>>(new Set())
  const [confirming, setConfirming] = useState(false)
  const [confirmResult, setConfirmResult] = useState<ConfirmMatchesResult | null>(null)
  const [confirmError, setConfirmError] = useState<string | null>(null)

  const indexedDocs = docs.filter(d => d.status === 'indexed')

  useEffect(() => {
    listRowIndexTables()
      .then(t => setIndexTables(t))
      .catch(() => { /* ignore — empty list shown */ })
  }, [])

  async function handleRun() {
    if (!selectedDocId) return
    setLoading(true)
    setError(null)
    setResult(null)
    setActiveTable(null)
    setCheckedKeys(new Set())
    setConfirmResult(null)
    setConfirmError(null)
    try {
      let r: DocRagMatchRowsResponse
      if (rowSource === 'file') {
        if (!uploadedFile) {
          setError('Please select a CSV or Excel file to match against.')
          setLoading(false)
          return
        }
        r = await matchDocumentRowsFromFile(selectedDocId, uploadedFile, {
          pk_column: pkColumn || undefined,
          source_table: fileTableLabel || undefined,
          confidence_threshold: threshold,
          group_by_table: true,
        })
      } else {
        r = await matchDocumentRows(selectedDocId, {
          confidence_threshold: threshold,
          group_by_table: true,
          source_table: selectedTable || undefined,
        })
      }
      setResult(r)
      const tables = Object.keys(r.by_table)
      if (tables.length > 0) setActiveTable(tables[0])
      // Default: check all rows
      const keys = new Set(r.matched_rows.map(row => `${row.source_table}::${row.row_pk}`))
      setCheckedKeys(keys)
    } catch (e: any) {
      setError(e.message ?? 'Match failed')
    } finally {
      setLoading(false)
    }
  }

  function toggleRow(row: DocRagMatchedRow) {
    const key = `${row.source_table}::${row.row_pk}`
    setCheckedKeys(prev => {
      const next = new Set(prev)
      next.has(key) ? next.delete(key) : next.add(key)
      return next
    })
  }

  function toggleAll(rows: DocRagMatchedRow[], checked: boolean) {
    setCheckedKeys(prev => {
      const next = new Set(prev)
      for (const row of rows) {
        const key = `${row.source_table}::${row.row_pk}`
        checked ? next.add(key) : next.delete(key)
      }
      return next
    })
  }

  async function handleConfirm() {
    if (!result || !selectedDocId || checkedKeys.size === 0) return
    setConfirming(true)
    setConfirmError(null)
    setConfirmResult(null)
    try {
      const confirmedRows = result.matched_rows
        .filter(row => checkedKeys.has(`${row.source_table}::${row.row_pk}`))
        .map(row => ({ source_table: row.source_table, row_pk: row.row_pk }))
      const r = await confirmDocumentMatches(selectedDocId, confirmedRows)
      setConfirmResult(r)
    } catch (e: any) {
      setConfirmError(e.message ?? 'Confirmation failed')
    } finally {
      setConfirming(false)
    }
  }

  const allMatchedRows: DocRagMatchedRow[] = result?.matched_rows ?? []
  const displayRows: DocRagMatchedRow[] = result
    ? (activeTable && result.matched_rows_by_table?.[activeTable])
        ? result.matched_rows_by_table[activeTable]
        : allMatchedRows
    : []

  const displayAllChecked = displayRows.length > 0 && displayRows.every(r => checkedKeys.has(`${r.source_table}::${r.row_pk}`))
  const displaySomeChecked = displayRows.some(r => checkedKeys.has(`${r.source_table}::${r.row_pk}`))
  const selectedRows = allMatchedRows.filter(r => checkedKeys.has(`${r.source_table}::${r.row_pk}`))
  const selectedByTable = selectedRows.reduce<Record<string, number>>((acc, row) => {
    acc[row.source_table] = (acc[row.source_table] ?? 0) + 1
    return acc
  }, {})

  return (
    <div className="space-y-6">
      {/* Config panel */}
      <div className="rounded-xl border border-slate-200 bg-white p-5 space-y-4">
        <h3 className="text-sm font-semibold text-slate-700">Match document to rows</h3>

        {/* Document selector */}
        <div>
          <label className="block text-xs font-medium text-slate-600 mb-1.5">Document</label>
          {indexedDocs.length === 0 ? (
            <p className="text-sm text-slate-400">
              No indexed documents found — upload and index a document first in the Documents tab.
            </p>
          ) : (
            <select
              className="input w-full text-sm"
              value={selectedDocId}
              onChange={e => setSelectedDocId(e.target.value)}
            >
              <option value="">— Select a document —</option>
              {indexedDocs.map(d => (
                <option key={d.id} value={d.id}>
                  {d.file_name} ({d.num_chunks} chunks{d.document_type ? `, ${d.document_type.replace(/_/g, ' ')}` : ''})
                </option>
              ))}
            </select>
          )}
        </div>

        {/* Row source toggle */}
        <div>
          <label className="block text-xs font-medium text-slate-600 mb-1.5">Row source</label>
          <div className="flex gap-1 bg-slate-100 rounded-lg p-1 w-fit">
            <button
              onClick={() => setRowSource('database')}
              className={`flex items-center gap-1.5 px-3 py-1.5 rounded-md text-xs font-medium transition-colors ${
                rowSource === 'database' ? 'bg-white text-slate-800 shadow-sm' : 'text-slate-500 hover:text-slate-700'
              }`}
            >
              <Database size={12} />
              Database rows
            </button>
            <button
              onClick={() => setRowSource('file')}
              className={`flex items-center gap-1.5 px-3 py-1.5 rounded-md text-xs font-medium transition-colors ${
                rowSource === 'file' ? 'bg-white text-slate-800 shadow-sm' : 'text-slate-500 hover:text-slate-700'
              }`}
            >
              <Upload size={12} />
              Upload file
            </button>
          </div>
        </div>

        {/* Threshold */}
        <div className="flex items-center gap-4">
          <label className="text-xs font-medium text-slate-600 whitespace-nowrap">
            Confidence threshold
          </label>
          <input
            type="range" min={0} max={1} step={0.05}
            value={threshold}
            onChange={e => setThreshold(Number(e.target.value))}
            className="flex-1 accent-indigo-600"
          />
          <span className="text-xs font-bold text-slate-700 tabular-nums w-10 text-right">
            {(threshold * 100).toFixed(0)}%
          </span>
        </div>

        {/* ── Database source options ── */}
        {rowSource === 'database' && (
          <div>
            <label className="block text-xs font-medium text-slate-600 mb-1.5">
              Match against table
            </label>
            {indexTables.length === 0 ? (
              <div className="flex items-center gap-2 text-xs text-slate-400 rounded-lg border border-dashed border-slate-200 px-3 py-2">
                <Database size={13} />
                <span>No indexed tables found — upload data in the <span className="font-semibold">Data Index</span> tab first.</span>
              </div>
            ) : (
              <select
                className="input w-full text-sm"
                value={selectedTable}
                onChange={e => setSelectedTable(e.target.value)}
              >
                <option value="">— All tables —</option>
                {indexTables.map(t => (
                  <option key={t.source_table} value={t.source_table}>
                    {t.source_table} ({t.row_count} row{t.row_count !== 1 ? 's' : ''})
                  </option>
                ))}
              </select>
            )}
            <p className="text-xs text-slate-400 mt-1">
              Select a specific table to narrow matching, or leave as "All tables" to search the full index.
            </p>
          </div>
        )}

        {/* ── File source options ── */}
        {rowSource === 'file' && (
          <div className="space-y-3">
            <div>
              <label className="block text-xs font-medium text-slate-600 mb-1.5">
                CSV or Excel file <span className="text-slate-400 font-normal">.csv · .xlsx</span>
              </label>
              <input
                ref={fileInputRef}
                type="file"
                accept=".csv,.xlsx,.xls,.xlsm"
                className="hidden"
                onChange={e => {
                  const f = e.target.files?.[0] ?? null
                  setUploadedFile(f)
                  if (f) setFileTableLabel(f.name.replace(/\.[^.]+$/, ''))
                }}
              />
              <button
                onClick={() => fileInputRef.current?.click()}
                className="flex items-center gap-2 px-3 py-2 rounded-lg border border-slate-200 bg-slate-50 hover:bg-slate-100 text-sm text-slate-600 transition-colors"
              >
                <Upload size={13} />
                {uploadedFile ? uploadedFile.name : 'Choose file…'}
              </button>
            </div>

            <div className="grid grid-cols-2 gap-3">
              <div>
                <label className="block text-xs font-medium text-slate-600 mb-1">
                  PK column <span className="text-slate-400 font-normal">(optional)</span>
                </label>
                <input
                  type="text"
                  placeholder="e.g. asset_code"
                  className="input w-full text-sm"
                  value={pkColumn}
                  onChange={e => setPkColumn(e.target.value)}
                />
                <p className="text-xs text-slate-400 mt-0.5">Defaults to first column</p>
              </div>
              <div>
                <label className="block text-xs font-medium text-slate-600 mb-1">
                  Table label <span className="text-slate-400 font-normal">(optional)</span>
                </label>
                <input
                  type="text"
                  placeholder="e.g. equipment"
                  className="input w-full text-sm"
                  value={fileTableLabel}
                  onChange={e => setFileTableLabel(e.target.value)}
                />
                <p className="text-xs text-slate-400 mt-0.5">Defaults to filename</p>
              </div>
            </div>
            <p className="text-xs text-slate-400">
              Rows are matched using BM25 keyword overlap and metadata field matching. Semantic scoring
              is not applied since file rows have no pre-computed embeddings.
            </p>
          </div>
        )}

        {error && (
          <div className="flex items-start gap-2 rounded-lg bg-red-50 border border-red-200 px-3 py-2.5">
            <AlertCircle size={14} className="text-red-500 shrink-0 mt-0.5" />
            <span className="text-sm text-red-700">{error}</span>
          </div>
        )}

        <button
          onClick={handleRun}
          disabled={loading || !selectedDocId || (rowSource === 'file' && !uploadedFile)}
          className="btn-primary flex items-center gap-2 px-5 py-2.5"
        >
          {loading ? (
            <span className="animate-spin inline-block w-4 h-4 border-2 border-white border-t-transparent rounded-full" />
          ) : (
            <Play size={14} />
          )}
          {loading ? 'Matching…' : 'Run match'}
        </button>
      </div>

      {/* Results */}
      {result && (
        <div className="space-y-4">
          {/* Summary bar */}
          <div className="flex items-center gap-4 flex-wrap px-1">
            <div className="flex items-center gap-1.5">
              <Link2 size={13} className="text-slate-400" />
              <span className="text-sm font-bold text-slate-800">{result.unique_rows_matched}</span>
              <span className="text-sm text-slate-500">rows matched</span>
            </div>
            <span className="text-slate-300">·</span>
            <span className="text-sm text-slate-500">{result.total_chunks_analyzed} chunks analyzed</span>
            <span className="text-slate-300">·</span>
            <span className="text-sm text-slate-500">{result.latency_ms} ms</span>
            {(result as any).source_file && (
              <>
                <span className="text-slate-300">·</span>
                <span className="flex items-center gap-1 text-sm text-slate-500">
                  <Upload size={11} className="text-slate-400" />
                  {(result as any).source_file}
                </span>
              </>
            )}
          </div>

          {/* Table filter pills */}
          {Object.keys(result.by_table).length > 1 && (
            <div className="flex items-center gap-2 flex-wrap">
              <TablePill
                name="All"
                count={result.unique_rows_matched}
                active={activeTable === null}
                onClick={() => setActiveTable(null)}
              />
              {Object.entries(result.by_table).map(([tbl, cnt]) => (
                <TablePill
                  key={tbl}
                  name={tbl}
                  count={cnt}
                  active={activeTable === tbl}
                  onClick={() => setActiveTable(tbl)}
                />
              ))}
            </div>
          )}

          {/* Row cards */}
          {displayRows.length === 0 ? (
            <div className="rounded-xl border border-dashed border-slate-200 py-10 text-center">
              <Link2 size={24} className="mx-auto text-slate-300 mb-2" />
              <p className="text-sm text-slate-400">No rows matched above the threshold</p>
              <p className="text-xs text-slate-400 mt-1">
                Try lowering the threshold to 20-25%. If still zero, check that the row index is populated.
              </p>
            </div>
          ) : (
            <>
              {/* Write preview (before confirm) */}
              <div className="rounded-xl border border-indigo-200 bg-indigo-50 px-4 py-3 space-y-2">
                <div className="flex items-center justify-between gap-3 flex-wrap">
                  <span className="text-sm font-semibold text-indigo-900">
                    Preview: rows that will receive this document ID
                  </span>
                  <span className="text-xs font-mono bg-white border border-indigo-200 text-indigo-700 px-2 py-0.5 rounded">
                    {result.document_id}
                  </span>
                </div>
                <p className="text-xs text-indigo-700">
                  On confirm, each selected row will append this value to its <span className="font-mono">document_ids</span> column.
                </p>
                {selectedRows.length === 0 ? (
                  <p className="text-xs text-indigo-600">No rows selected yet.</p>
                ) : (
                  <>
                    <div className="flex flex-wrap gap-x-4 gap-y-1 text-xs text-indigo-700">
                      {Object.entries(selectedByTable).map(([tbl, cnt]) => (
                        <span key={tbl}>{tbl}: {cnt} row{cnt !== 1 ? 's' : ''}</span>
                      ))}
                    </div>
                    <div className="max-h-44 overflow-auto rounded-lg border border-indigo-100 bg-white">
                      <div className="divide-y divide-indigo-50">
                        {selectedRows.map((row, i) => (
                          <div key={`${row.source_table}-${row.row_pk}-preview-${i}`} className="px-3 py-2 text-xs flex items-center gap-2">
                            <span className="font-mono text-slate-700">
                              {row.source_table}.{row.row_pk}
                            </span>
                            <span className="text-slate-300">→</span>
                            <span className="text-indigo-700 font-mono">
                              document_ids += "{result.document_id}"
                            </span>
                          </div>
                        ))}
                      </div>
                    </div>
                  </>
                )}
              </div>

              {/* Select-all + confirm toolbar */}
              <div className="flex items-center justify-between bg-slate-50 border border-slate-200 rounded-xl px-4 py-2.5">
                <button
                  onClick={() => toggleAll(displayRows, !displayAllChecked)}
                  className="flex items-center gap-2 text-sm text-slate-600 hover:text-slate-900 transition-colors"
                >
                  {displayAllChecked
                    ? <CheckSquare size={16} className="text-indigo-600" />
                    : displaySomeChecked
                      ? <CheckSquare size={16} className="text-indigo-400" />
                      : <Square size={16} className="text-slate-400" />
                  }
                  <span>
                    {checkedKeys.size === 0
                      ? 'Select rows to confirm'
                      : `${checkedKeys.size} of ${allMatchedRows.length} selected`}
                  </span>
                </button>

                <button
                  onClick={handleConfirm}
                  disabled={confirming || checkedKeys.size === 0}
                  className={`flex items-center gap-2 px-4 py-1.5 rounded-lg text-sm font-medium transition-colors ${
                    checkedKeys.size === 0
                      ? 'bg-slate-200 text-slate-400 cursor-not-allowed'
                      : 'bg-indigo-600 text-white hover:bg-indigo-700'
                  }`}
                >
                  {confirming
                    ? <span className="animate-spin inline-block w-3.5 h-3.5 border-2 border-white border-t-transparent rounded-full" />
                    : <CheckCircle2 size={14} />
                  }
                  {confirming ? 'Confirming…' : `Confirm ${checkedKeys.size} match${checkedKeys.size !== 1 ? 'es' : ''}`}
                </button>
              </div>

              {/* Confirm error */}
              {confirmError && (
                <div className="flex items-start gap-2 rounded-lg bg-red-50 border border-red-200 px-3 py-2.5">
                  <XCircle size={14} className="text-red-500 shrink-0 mt-0.5" />
                  <span className="text-sm text-red-700">{confirmError}</span>
                </div>
              )}

              {/* Confirm success */}
              {confirmResult && (
                <div className="rounded-xl border border-emerald-200 bg-emerald-50 px-4 py-3 space-y-1.5">
                  <div className="flex items-center gap-2">
                    <CheckCircle2 size={15} className="text-emerald-600 shrink-0" />
                    <span className="text-sm font-semibold text-emerald-800">
                      {confirmResult.rows_updated} row{confirmResult.rows_updated !== 1 ? 's' : ''} confirmed
                      — document_id written to CMMS tables
                    </span>
                  </div>
                  <div className="flex flex-wrap gap-x-4 gap-y-0.5 text-xs text-emerald-700 pl-5">
                    {Object.entries(confirmResult.by_table).map(([tbl, cnt]) => (
                      <span key={tbl}>{tbl}: {cnt} row{cnt !== 1 ? 's' : ''}</span>
                    ))}
                  </div>
                  {confirmResult.columns_created.length > 0 && (
                    <p className="text-xs text-emerald-600 pl-5">
                      document_ids column auto-created on: {confirmResult.columns_created.join(', ')}
                    </p>
                  )}
                  {confirmResult.rows_not_found > 0 && (
                    <p className="text-xs text-amber-600 pl-5">
                      {confirmResult.rows_not_found} row{confirmResult.rows_not_found !== 1 ? 's' : ''} not found in CMMS table (pk_column may not be indexed yet)
                    </p>
                  )}
                </div>
              )}

              {/* Row cards with checkboxes */}
              <div className="space-y-2">
                {displayRows.map((row, i) => {
                  const key = `${row.source_table}::${row.row_pk}`
                  const checked = checkedKeys.has(key)
                  return (
                    <div key={`${row.source_table}-${row.row_pk}-${i}`} className="flex items-start gap-2">
                      <button
                        onClick={() => toggleRow(row)}
                        className="mt-3.5 shrink-0"
                        title={checked ? 'Deselect' : 'Select for confirmation'}
                      >
                        {checked
                          ? <CheckSquare size={16} className="text-indigo-600" />
                          : <Square size={16} className="text-slate-300 hover:text-slate-500" />
                        }
                      </button>
                      <div className="flex-1 min-w-0">
                        <RowCard row={row} />
                      </div>
                    </div>
                  )
                })}
              </div>
            </>
          )}
        </div>
      )}
    </div>
  )
}
