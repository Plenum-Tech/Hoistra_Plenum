import { useState, useRef } from 'react'
import { startMigration } from '../api'
import { UploadCloud, FileSpreadsheet, AlertCircle } from 'lucide-react'

interface Props {
  orgId: string
  onStarted: (migrationId: string) => void
}

export default function UploadPanel({ orgId, onStarted }: Props) {
  const [file, setFile] = useState<File | null>(null)
  const [cmmsName, setCmmsName] = useState('Custom')
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [dragging, setDragging] = useState(false)
  const inputRef = useRef<HTMLInputElement>(null)

  function handleFile(f: File) {
    setFile(f)
    setError(null)
    // Auto-detect CMMS name from filename
    const lower = f.name.toLowerCase()
    if (lower.includes('maximo')) setCmmsName('Maximo')
    else if (lower.includes('fiix')) setCmmsName('Fiix')
    else if (lower.includes('archibus')) setCmmsName('Archibus')
    else if (lower.includes('sap')) setCmmsName('SAP PM')
  }

  async function handleSubmit() {
    if (!file) return
    setLoading(true)
    setError(null)
    try {
      const result = await startMigration({ file, orgId, cmmsName })
      onStarted(result.migration_id)
    } catch (err: any) {
      setError(err.message ?? 'Failed to start migration')
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="max-w-2xl mx-auto pt-16">
      {/* Header */}
      <div className="text-center mb-10">
        <div className="inline-flex items-center justify-center w-16 h-16 rounded-2xl bg-indigo-100 mb-4">
          <UploadCloud size={32} className="text-indigo-600" />
        </div>
        <h1 className="text-2xl font-bold text-slate-900 mb-2">
          AI Schema Mapper
        </h1>
        <p className="text-slate-500 text-sm max-w-md mx-auto">
          Upload any CMMS export (CSV or Excel). The AI pipeline maps your fields,
          detects hierarchies, and produces a validated IntermediateSchema ready for ingestion.
        </p>
      </div>

      <div className="card p-8 space-y-6">
        {/* Drop zone */}
        <div
          className={`relative rounded-xl border-2 border-dashed transition-colors cursor-pointer
            ${dragging ? 'border-indigo-400 bg-indigo-50' : 'border-slate-300 hover:border-slate-400 bg-slate-50'}
            ${file ? 'border-green-400 bg-green-50' : ''}`}
          onClick={() => inputRef.current?.click()}
          onDragOver={e => { e.preventDefault(); setDragging(true) }}
          onDragLeave={() => setDragging(false)}
          onDrop={e => {
            e.preventDefault()
            setDragging(false)
            const f = e.dataTransfer.files[0]
            if (f) handleFile(f)
          }}
        >
          <input
            ref={inputRef}
            type="file"
            className="hidden"
            accept=".csv,.xlsx,.xls"
            onChange={e => { const f = e.target.files?.[0]; if (f) handleFile(f) }}
          />
          <div className="p-8 text-center">
            {file ? (
              <div className="flex items-center justify-center gap-3">
                <FileSpreadsheet size={24} className="text-green-600" />
                <div className="text-left">
                  <p className="text-sm font-semibold text-slate-800">{file.name}</p>
                  <p className="text-xs text-slate-500">{(file.size / 1024 / 1024).toFixed(2)} MB</p>
                </div>
              </div>
            ) : (
              <>
                <UploadCloud size={28} className="text-slate-400 mx-auto mb-2" />
                <p className="text-sm text-slate-600 font-medium">
                  Drop your CMMS export here, or <span className="text-indigo-600">browse</span>
                </p>
                <p className="text-xs text-slate-400 mt-1">CSV, XLSX or XLS · up to 100 MB</p>
              </>
            )}
          </div>
        </div>

        {/* CMMS Name */}
        <div>
          <label className="label">Source CMMS</label>
          <select
            className="input"
            value={cmmsName}
            onChange={e => setCmmsName(e.target.value)}
          >
            {['Maximo', 'Fiix', 'SAP PM', 'Archibus', 'Hippo CMMS', 'eMaint', 'Infor EAM', 'Custom'].map(n => (
              <option key={n} value={n}>{n}</option>
            ))}
          </select>
        </div>

        {/* Error */}
        {error && (
          <div className="flex items-start gap-2 rounded-lg bg-red-50 border border-red-200 px-4 py-3 text-sm text-red-700">
            <AlertCircle size={16} className="mt-0.5 shrink-0" />
            {error}
          </div>
        )}

        {/* Submit */}
        <button
          className="btn-primary w-full py-3 text-base"
          onClick={handleSubmit}
          disabled={!file || loading}
        >
          {loading ? (
            <>
              <span className="animate-spin inline-block w-4 h-4 border-2 border-white border-t-transparent rounded-full" />
              Starting pipeline…
            </>
          ) : (
            <>
              <UploadCloud size={18} />
              Start AI Migration Pipeline
            </>
          )}
        </button>
      </div>

      {/* Pipeline overview */}
      <div className="mt-8 grid grid-cols-5 gap-2 text-center">
        {[
          { n: '1–3', label: 'Field\nMapping' },
          { n: 'G0–G1', label: 'Human\nReview' },
          { n: '5–6', label: 'Preprocess\n& Hierarchy' },
          { n: 'G2', label: 'Hierarchy\nApproval' },
          { n: '8–G3', label: 'Output\n& Write' },
        ].map(step => (
          <div key={step.n} className="rounded-lg bg-white border border-slate-200 p-3">
            <div className="text-xs font-mono font-bold text-indigo-600 mb-1">{step.n}</div>
            <div className="text-xs text-slate-500 whitespace-pre-line leading-tight">{step.label}</div>
          </div>
        ))}
      </div>
    </div>
  )
}
