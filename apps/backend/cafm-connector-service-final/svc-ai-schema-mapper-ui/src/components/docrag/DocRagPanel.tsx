import { useState, useEffect, useRef, type ReactNode } from 'react'
import { FileText, Search, Link2, Upload, Trash2, ChevronDown, ChevronUp, AlertCircle, CheckCircle2, Clock, WifiOff, Database, Layers } from 'lucide-react'
import { uploadDocument, listDocuments, deleteDocument, getDocumentChunks } from '../../api'
import type { DocRagDocument, DocRagUploadResponse, DocRagChunk } from '../../types'
import DocQueryTab from './DocQueryTab'
import DocMatchTab from './DocMatchTab'
import DocIndexTab from './DocIndexTab'

type Tab = 'documents' | 'query' | 'match' | 'index'

const TABS: { id: Tab; label: string; icon: ReactNode }[] = [
  { id: 'documents', label: 'Documents',  icon: <FileText size={14} /> },
  { id: 'query',     label: 'RAG Query',  icon: <Search size={14} /> },
  { id: 'match',     label: 'Match Rows', icon: <Link2 size={14} /> },
  { id: 'index',     label: 'Data Index', icon: <Database size={14} /> },
]

const STATUS_COLOR: Record<string, string> = {
  indexed:     'bg-emerald-100 text-emerald-700',
  processing:  'bg-blue-100 text-blue-700',
  extracting:  'bg-amber-100 text-amber-700',
  error:       'bg-red-100 text-red-700',
}

const DOC_TYPE_COLOR: Record<string, string> = {
  inspection_report: 'bg-purple-100 text-purple-700',
  asset_manual:      'bg-blue-100 text-blue-700',
  work_order:        'bg-amber-100 text-amber-700',
  invoice:           'bg-green-100 text-green-700',
  contract:          'bg-slate-100 text-slate-700',
  sla:               'bg-indigo-100 text-indigo-700',
  policy:            'bg-orange-100 text-orange-700',
  unknown:           'bg-slate-100 text-slate-500',
}

function Badge({ label, colorClass }: { label: string; colorClass: string }) {
  return (
    <span className={`inline-flex items-center px-2 py-0.5 rounded text-xs font-medium ${colorClass}`}>
      {label}
    </span>
  )
}

function formatDate(iso: string) {
  return new Date(iso).toLocaleString(undefined, {
    month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit',
  })
}

const BLOCK_TYPE_COLOR: Record<string, string> = {
  text:       'bg-slate-100 text-slate-600',
  table_row:  'bg-blue-100 text-blue-700',
  image:      'bg-purple-100 text-purple-700',
  heading:    'bg-amber-100 text-amber-700',
  list_item:  'bg-teal-100 text-teal-700',
  caption:    'bg-indigo-100 text-indigo-600',
  metadata:   'bg-pink-100 text-pink-700',
}

function ChunkList({ documentId }: { documentId: string }) {
  const [chunks, setChunks]     = useState<DocRagChunk[]>([])
  const [loading, setLoading]   = useState(true)
  const [error, setError]       = useState<string | null>(null)
  const [expanded, setExpanded] = useState<Set<number>>(new Set())

  useEffect(() => {
    setLoading(true)
    getDocumentChunks(documentId, 200)
      .then(setChunks)
      .catch(e => setError(e.message ?? 'Failed to load chunks'))
      .finally(() => setLoading(false))
  }, [documentId])

  if (loading) return (
    <div className="px-4 py-4 flex items-center gap-2 text-xs text-slate-400">
      <span className="animate-spin inline-block w-3.5 h-3.5 border-2 border-indigo-400 border-t-transparent rounded-full" />
      Loading chunks…
    </div>
  )

  if (error) return (
    <div className="px-4 py-3 flex items-center gap-2 text-xs text-red-600">
      <AlertCircle size={12} /> {error}
    </div>
  )

  if (!chunks.length) return (
    <div className="px-4 py-3 text-xs text-slate-400">No chunks found.</div>
  )

  // Group counts for the summary bar
  const byType = chunks.reduce<Record<string, number>>((acc, c) => {
    acc[c.block_type] = (acc[c.block_type] ?? 0) + 1
    return acc
  }, {})

  function toggleChunk(idx: number) {
    setExpanded(prev => {
      const next = new Set(prev)
      next.has(idx) ? next.delete(idx) : next.add(idx)
      return next
    })
  }

  return (
    <div className="border-t border-slate-100">
      {/* Summary bar */}
      <div className="flex items-center gap-3 px-4 py-2 bg-slate-50 flex-wrap">
        <span className="text-xs font-semibold text-slate-500">{chunks.length} chunks</span>
        {Object.entries(byType).map(([type, count]) => (
          <span
            key={type}
            className={`inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-xs font-medium ${BLOCK_TYPE_COLOR[type] ?? 'bg-slate-100 text-slate-600'}`}
          >
            {type.replace('_', ' ')} <span className="opacity-70">{count}</span>
          </span>
        ))}
      </div>

      {/* Chunk rows */}
      <div className="divide-y divide-slate-50">
        {chunks.map(c => {
          const isOpen = expanded.has(c.chunk_index)
          const preview = c.text_content.length > 120
            ? c.text_content.slice(0, 120) + '…'
            : c.text_content

          return (
            <div key={c.chunk_index} className="px-4 py-2 hover:bg-slate-50 transition-colors">
              <button
                onClick={() => toggleChunk(c.chunk_index)}
                className="w-full text-left flex items-start gap-3"
              >
                {/* Index */}
                <span className="shrink-0 w-7 text-right text-xs font-mono text-slate-400 pt-0.5">
                  #{c.chunk_index}
                </span>

                {/* Page badge */}
                <span className="shrink-0 text-xs text-slate-400 pt-0.5 w-14">
                  {c.page_start != null
                    ? c.page_start === c.page_end
                      ? `p.${c.page_start}`
                      : `p.${c.page_start}–${c.page_end}`
                    : '—'}
                </span>

                {/* Block type */}
                <span className={`shrink-0 inline-flex items-center px-1.5 py-0.5 rounded text-xs font-medium ${
                  BLOCK_TYPE_COLOR[c.block_type] ?? 'bg-slate-100 text-slate-600'
                }`}>
                  {c.block_type.replace('_', ' ')}
                </span>

                {/* Section + text */}
                <div className="flex-1 min-w-0">
                  {c.section_label && (
                    <div className="text-xs text-indigo-600 font-medium mb-0.5 truncate">{c.section_label}</div>
                  )}
                  <p className="text-xs text-slate-600 leading-relaxed">
                    {isOpen ? c.text_content : preview}
                  </p>
                </div>

                {/* Expand toggle */}
                {c.text_content.length > 120 && (
                  <span className="shrink-0 mt-0.5">
                    {isOpen
                      ? <ChevronUp size={12} className="text-slate-400" />
                      : <ChevronDown size={12} className="text-slate-400" />}
                  </span>
                )}
              </button>
            </div>
          )
        })}
      </div>
    </div>
  )
}

function DocumentRow({
  doc,
  onDelete,
}: {
  doc: DocRagDocument
  onDelete: (id: string) => void
}) {
  const [confirming, setConfirming]   = useState(false)
  const [deleting, setDeleting]       = useState(false)
  const [showChunks, setShowChunks]   = useState(false)

  async function handleDelete() {
    setDeleting(true)
    try {
      await deleteDocument(doc.id)
      onDelete(doc.id)
    } catch {
      setDeleting(false)
      setConfirming(false)
    }
  }

  return (
    <div>
      <div className="flex items-center gap-3 px-4 py-3 hover:bg-slate-50 transition-colors">
        <div className="w-8 h-8 rounded-lg bg-slate-100 flex items-center justify-center shrink-0">
          <FileText size={15} className="text-slate-500" />
        </div>
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2 flex-wrap">
            <span className="text-sm font-medium text-slate-800 truncate max-w-xs">{doc.file_name}</span>
            <Badge
              label={doc.status}
              colorClass={STATUS_COLOR[doc.status] ?? 'bg-slate-100 text-slate-600'}
            />
            {doc.document_type && (
              <Badge
                label={doc.document_type.replace(/_/g, ' ')}
                colorClass={DOC_TYPE_COLOR[doc.document_type] ?? 'bg-slate-100 text-slate-600'}
              />
            )}
          </div>
          <div className="flex items-center gap-3 mt-0.5">
            <span className="text-xs text-slate-400">{doc.num_pages} pages</span>
            <span className="text-xs text-slate-300">·</span>
            <button
              onClick={() => setShowChunks(v => !v)}
              className="flex items-center gap-1 text-xs text-indigo-500 hover:text-indigo-700 transition-colors"
              title={showChunks ? 'Hide chunks' : 'Show chunks'}
            >
              <Layers size={11} />
              {doc.num_chunks} chunks
              {showChunks
                ? <ChevronUp size={10} />
                : <ChevronDown size={10} />}
            </button>
            <span className="text-xs text-slate-300">·</span>
            <span className="text-xs text-slate-400">{formatDate(doc.created_at)}</span>
          </div>
        </div>
        <div className="shrink-0">
          {!confirming ? (
            <button
              onClick={() => setConfirming(true)}
              className="p-1.5 rounded hover:bg-red-50 text-slate-400 hover:text-red-500 transition-colors"
              title="Delete document"
            >
              <Trash2 size={14} />
            </button>
          ) : (
            <div className="flex items-center gap-2">
              <span className="text-xs text-slate-500">Delete?</span>
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
      {showChunks && <ChunkList documentId={doc.id} />}
    </div>
  )
}

function UploadArea({ onUploaded }: { onUploaded: (doc: DocRagUploadResponse) => void }) {
  const [dragging, setDragging] = useState(false)
  const [uploading, setUploading] = useState(false)
  const [result, setResult] = useState<DocRagUploadResponse | null>(null)
  const [error, setError] = useState<string | null>(null)
  const inputRef = useRef<HTMLInputElement>(null)

  async function handleFile(file: File) {
    setUploading(true)
    setError(null)
    setResult(null)
    try {
      const r = await uploadDocument(file)
      setResult(r)
      onUploaded(r)
    } catch (e: any) {
      setError(e.message ?? 'Upload failed')
    } finally {
      setUploading(false)
    }
  }

  function onDrop(e: React.DragEvent) {
    e.preventDefault()
    setDragging(false)
    const file = e.dataTransfer.files[0]
    if (file) handleFile(file)
  }

  return (
    <div className="space-y-3">
      <div
        onDragOver={e => { e.preventDefault(); setDragging(true) }}
        onDragLeave={() => setDragging(false)}
        onDrop={onDrop}
        onClick={() => inputRef.current?.click()}
        className={`border-2 border-dashed rounded-xl p-8 text-center cursor-pointer transition-all ${
          dragging
            ? 'border-indigo-400 bg-indigo-50'
            : 'border-slate-200 hover:border-indigo-300 hover:bg-slate-50'
        }`}
      >
        <input
          ref={inputRef}
          type="file"
          className="hidden"
          accept=".pdf,.docx,.txt"
          onChange={e => { if (e.target.files?.[0]) handleFile(e.target.files[0]) }}
        />
        {uploading ? (
          <div className="flex flex-col items-center gap-2">
            <span className="animate-spin inline-block w-6 h-6 border-2 border-indigo-500 border-t-transparent rounded-full" />
            <p className="text-sm text-slate-500">Processing document…</p>
          </div>
        ) : (
          <div className="flex flex-col items-center gap-2">
            <Upload size={24} className="text-slate-400" />
            <p className="text-sm font-medium text-slate-600">Drop a file or click to browse</p>
            <p className="text-xs text-slate-400">PDF, DOCX, TXT — max 32 MB</p>
          </div>
        )}
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
            <span className="text-sm font-semibold text-emerald-800">Indexed successfully</span>
            <span className="text-xs text-emerald-600 ml-auto">{result.processing_time_ms} ms</span>
          </div>
          <div className="grid grid-cols-2 gap-x-4 gap-y-1 text-xs text-emerald-700">
            <span>File: <span className="font-medium">{result.file_name}</span></span>
            <span>Type: <span className="font-medium">{result.document_type ?? 'unknown'}</span></span>
            <span>Pages: <span className="font-medium">{result.num_pages}</span></span>
            <span>Chunks: <span className="font-medium">{result.num_chunks}</span></span>
          </div>
          <p className="text-xs text-emerald-600 mt-2 font-mono">{result.document_id}</p>
        </div>
      )}
    </div>
  )
}

function DocumentsTab({
  docs,
  loading,
  onDelete,
  onUploaded,
}: {
  docs: DocRagDocument[]
  loading: boolean
  onDelete: (id: string) => void
  onUploaded: (doc: DocRagUploadResponse) => void
}) {
  return (
    <div className="space-y-6">
      <div>
        <h3 className="text-sm font-semibold text-slate-700 mb-3">Upload document</h3>
        <UploadArea onUploaded={onUploaded} />
      </div>

      <div>
        <div className="flex items-center justify-between mb-3">
          <h3 className="text-sm font-semibold text-slate-700">Indexed documents</h3>
          {loading && (
            <span className="flex items-center gap-1.5 text-xs text-slate-400">
              <Clock size={11} />
              Loading…
            </span>
          )}
          {!loading && (
            <span className="text-xs text-slate-400">{docs.length} document{docs.length !== 1 ? 's' : ''}</span>
          )}
        </div>

        {!loading && docs.length === 0 ? (
          <div className="rounded-xl border border-dashed border-slate-200 py-10 text-center">
            <FileText size={24} className="mx-auto text-slate-300 mb-2" />
            <p className="text-sm text-slate-400">No documents yet — upload one above</p>
          </div>
        ) : (
          <div className="rounded-xl border border-slate-200 overflow-hidden divide-y divide-slate-100">
            {docs.map(doc => (
              <DocumentRow key={doc.id} doc={doc} onDelete={onDelete} />
            ))}
          </div>
        )}
      </div>
    </div>
  )
}

export default function DocRagPanel() {
  const [tab, setTab] = useState<Tab>('documents')
  const [docs, setDocs] = useState<DocRagDocument[]>([])
  const [loadingDocs, setLoadingDocs] = useState(false)
  const [fetchError, setFetchError] = useState<string | null>(null)

  async function fetchDocs() {
    setLoadingDocs(true)
    setFetchError(null)
    try {
      const list = await listDocuments()
      setDocs(list)
    } catch (e: any) {
      setFetchError(e.message ?? 'Could not reach Doc RAG service')
    } finally {
      setLoadingDocs(false)
    }
  }

  useEffect(() => { fetchDocs() }, [])

  function handleDelete(id: string) {
    setDocs(prev => prev.filter(d => d.id !== id))
  }

  function handleUploaded(result: DocRagUploadResponse) {
    // Refresh the list so the new doc appears
    fetchDocs()
  }

  return (
    <div className="max-w-4xl">
      {/* Header */}
      <div className="mb-6">
        <h2 className="text-xl font-bold text-slate-900">Document RAG</h2>
        <p className="text-sm text-slate-500 mt-1">
          Upload documents, run natural-language queries, and match content to database rows.
        </p>
      </div>

      {fetchError && (
        <div className="flex items-start gap-2 rounded-lg bg-amber-50 border border-amber-200 px-4 py-3 mb-6">
          <WifiOff size={15} className="text-amber-500 shrink-0 mt-0.5" />
          <div>
            <p className="text-sm font-medium text-amber-800">Doc RAG service unreachable</p>
            <p className="text-xs text-amber-600 mt-0.5">{fetchError} — check the Doc RAG API URL in Settings and ensure the service is running.</p>
          </div>
        </div>
      )}

      {/* Tab switcher */}
      <div className="flex gap-1 bg-slate-100 rounded-lg p-1 mb-6 w-fit">
        {TABS.map(t => (
          <button
            key={t.id}
            onClick={() => setTab(t.id)}
            className={`flex items-center gap-1.5 px-4 py-2 rounded-md text-sm font-medium transition-colors ${
              tab === t.id
                ? 'bg-white text-slate-900 shadow-sm'
                : 'text-slate-500 hover:text-slate-700'
            }`}
          >
            {t.icon}
            {t.label}
          </button>
        ))}
      </div>

      {/* Tab content — all tabs stay mounted to preserve state; visibility toggled via CSS */}
      <div className={tab === 'documents' ? '' : 'hidden'}>
        <DocumentsTab
          docs={docs}
          loading={loadingDocs}
          onDelete={handleDelete}
          onUploaded={handleUploaded}
        />
      </div>
      <div className={tab === 'query' ? '' : 'hidden'}><DocQueryTab /></div>
      <div className={tab === 'match' ? '' : 'hidden'}><DocMatchTab docs={docs} /></div>
      <div className={tab === 'index' ? '' : 'hidden'}><DocIndexTab /></div>
    </div>
  )
}
