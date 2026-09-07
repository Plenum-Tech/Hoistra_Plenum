import { useState } from 'react'
import { submitSchemaGateArtifacts } from '../../../api'
import {
  FileJson, FileText, Database, Download, CheckCircle,
  Hash, Layers, GitMerge, ArrowRight, AlertCircle,
} from 'lucide-react'

// ── Types ────────────────────────────────────────────────────────────────────

interface ArtifactsSummary {
  canonical_fields_count?: number
  total_source_fields?: number
  tier1_auto_mapped?: number
  tier2_auto_mapped?: number
  tier2_flagged?: number
  unmappable?: number
  mapping_coverage_pct?: number
  detected_fk_count?: number
  max_hierarchy_depth?: number
  junction_table_count?: number
}

interface ArtifactsPayload {
  suggested_schema_name?: string
  external_cmms_name?: string
  output_json_url?: string
  output_csv_url?: string
  output_sql_url?: string
  summary?: ArtifactsSummary
  instructions?: string
  action_required?: string
}

interface Props {
  sessionId: string
  payload: ArtifactsPayload
  onSubmitted: () => void
}

// ── Artifact link row ────────────────────────────────────────────────────────

function ArtifactRow({
  icon,
  label,
  description,
  url,
}: {
  icon: React.ReactNode
  label: string
  description: string
  url: string
}) {
  if (!url) {
    return (
      <div className="flex items-center gap-4 px-5 py-3.5 opacity-40">
        {icon}
        <div className="flex-1 min-w-0">
          <p className="text-sm font-medium text-slate-700">{label}</p>
          <p className="text-xs text-slate-400">{description}</p>
        </div>
        <span className="text-xs text-slate-400 italic shrink-0">Not generated</span>
      </div>
    )
  }
  return (
    <a
      href={url}
      target="_blank"
      rel="noreferrer"
      className="flex items-center gap-4 px-5 py-3.5 hover:bg-slate-50 transition-colors group"
    >
      {icon}
      <div className="flex-1 min-w-0">
        <p className="text-sm font-medium text-slate-700 group-hover:text-indigo-700">{label}</p>
        <p className="text-xs text-slate-400">{description}</p>
      </div>
      <Download size={15} className="text-indigo-400 shrink-0 group-hover:text-indigo-600" />
    </a>
  )
}

// ── Component ─────────────────────────────────────────────────────────────────

export default function SchemaGateArtifacts({ sessionId, payload, onSubmitted }: Props) {
  const {
    suggested_schema_name = '',
    external_cmms_name = '',
    output_json_url = '',
    output_csv_url = '',
    output_sql_url = '',
    summary = {},
  } = payload

  const [schemaName, setSchemaName] = useState(suggested_schema_name)
  const [loading, setLoading]       = useState(false)
  const [error, setError]           = useState<string | null>(null)

  const coveragePct = Number(summary.mapping_coverage_pct ?? 0).toFixed(1)
  const coverageColor =
    Number(coveragePct) >= 90 ? 'text-green-600' :
    Number(coveragePct) >= 70 ? 'text-amber-600' : 'text-red-500'

  async function handleConfirm() {
    const trimmed = schemaName.trim()
    if (!trimmed) {
      setError('Schema name cannot be empty.')
      return
    }
    setLoading(true)
    setError(null)
    try {
      await submitSchemaGateArtifacts(sessionId, { new_schema_name: trimmed })
      onSubmitted()
    } catch (err: any) {
      setError(err?.message ?? 'Failed to submit.')
      setLoading(false)
    }
  }

  return (
    <div className="max-w-3xl">
      {/* ── Header ──────────────────────────────────────────────────────────── */}
      <div className="flex items-start gap-4 mb-6">
        <div className="w-10 h-10 rounded-xl bg-indigo-100 flex items-center justify-center shrink-0">
          <CheckCircle size={20} className="text-indigo-600" />
        </div>
        <div className="flex-1">
          <h2 className="text-lg font-bold text-slate-900">Review Artifacts &amp; Confirm</h2>
          <p className="text-sm text-slate-500 mt-0.5">
            Mapping for <strong>{external_cmms_name || 'external CMMS'}</strong> is complete.
            Review the generated files, set a schema name, then write to the database.
          </p>
        </div>
      </div>

      {/* ── Summary stats ───────────────────────────────────────────────────── */}
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 mb-5">
        <div className="card p-4 text-center">
          <div className={`text-2xl font-bold font-mono ${coverageColor}`}>{coveragePct}%</div>
          <div className="text-xs text-slate-500 mt-0.5">Coverage</div>
        </div>
        <div className="card p-4 text-center">
          <div className="text-2xl font-bold font-mono text-slate-800">
            {summary.total_source_fields ?? '—'}
          </div>
          <div className="text-xs text-slate-500 mt-0.5">Source fields</div>
        </div>
        <div className="card p-4 text-center">
          <div className="text-2xl font-bold font-mono text-green-700">
            {((summary.tier1_auto_mapped ?? 0) + (summary.tier2_auto_mapped ?? 0))}
          </div>
          <div className="text-xs text-slate-500 mt-0.5">Auto-mapped</div>
        </div>
        <div className="card p-4 text-center">
          <div className="text-2xl font-bold font-mono text-red-500">
            {summary.unmappable ?? '—'}
          </div>
          <div className="text-xs text-slate-500 mt-0.5">Unmappable</div>
        </div>
      </div>

      {/* ── Secondary stats ─────────────────────────────────────────────────── */}
      <div className="flex flex-wrap gap-3 mb-5">
        {(summary.detected_fk_count ?? 0) > 0 && (
          <span className="flex items-center gap-1.5 text-xs font-medium px-3 py-1.5 rounded-full bg-blue-100 text-blue-700">
            <GitMerge size={11} />
            {summary.detected_fk_count} FK relationships
          </span>
        )}
        {(summary.max_hierarchy_depth ?? 0) > 0 && (
          <span className="flex items-center gap-1.5 text-xs font-medium px-3 py-1.5 rounded-full bg-purple-100 text-purple-700">
            <Layers size={11} />
            Max depth: {summary.max_hierarchy_depth}
          </span>
        )}
        {(summary.junction_table_count ?? 0) > 0 && (
          <span className="flex items-center gap-1.5 text-xs font-medium px-3 py-1.5 rounded-full bg-teal-100 text-teal-700">
            <Hash size={11} />
            {summary.junction_table_count} junction tables
          </span>
        )}
        {(summary.tier2_flagged ?? 0) > 0 && (
          <span className="flex items-center gap-1.5 text-xs font-medium px-3 py-1.5 rounded-full bg-amber-100 text-amber-700">
            <AlertCircle size={11} />
            {summary.tier2_flagged} flagged for review
          </span>
        )}
      </div>

      {/* ── Artifact download links ─────────────────────────────────────────── */}
      <div className="card overflow-hidden mb-5">
        <div className="px-5 py-3 border-b border-slate-100 bg-slate-50/60">
          <p className="text-sm font-semibold text-slate-700">Generated artifacts</p>
          <p className="text-xs text-slate-400 mt-0.5">Download and inspect before writing to the database</p>
        </div>
        <div className="divide-y divide-slate-100">
          <ArtifactRow
            icon={<FileJson size={20} className="text-indigo-500 shrink-0" />}
            label="JSON Config"
            description="Full mapper config with field mappings, types, and FK relationships"
            url={output_json_url}
          />
          <ArtifactRow
            icon={<FileText size={20} className="text-green-500 shrink-0" />}
            label="CSV Mappings"
            description="Flat CSV export of all source → canonical field mappings"
            url={output_csv_url}
          />
          <ArtifactRow
            icon={<Database size={20} className="text-blue-500 shrink-0" />}
            label="SQL DDL Preview"
            description="PostgreSQL DDL statements that will be executed on confirm"
            url={output_sql_url}
          />
        </div>
      </div>

      {/* ── Schema name input ───────────────────────────────────────────────── */}
      <div className="card p-5 mb-5">
        <label className="block text-sm font-semibold text-slate-700 mb-1">
          New PostgreSQL schema name
        </label>
        <p className="text-xs text-slate-400 mb-3">
          A new schema will be cloned from <code className="font-mono text-slate-600">plenum_cafm</code> under this name.
          Use lowercase letters, numbers, and underscores only (max 63 characters).
        </p>
        <input
          type="text"
          value={schemaName}
          onChange={e => setSchemaName(e.target.value)}
          placeholder={suggested_schema_name}
          maxLength={63}
          className="w-full font-mono text-sm px-3 py-2 rounded-lg border border-slate-300 focus:outline-none focus:ring-2 focus:ring-indigo-400 bg-white text-slate-800"
          spellCheck={false}
        />
        <p className="text-xs text-slate-400 mt-1.5 text-right">{schemaName.length}/63</p>
      </div>

      {/* ── Error ───────────────────────────────────────────────────────────── */}
      {error && (
        <div className="mb-4 rounded-lg bg-red-50 border border-red-200 px-4 py-3 text-sm text-red-700">
          {error}
        </div>
      )}

      {/* ── Confirm button ──────────────────────────────────────────────────── */}
      <button
        className="btn-primary px-8 py-3 text-base"
        onClick={handleConfirm}
        disabled={loading || !schemaName.trim()}
      >
        {loading ? (
          <>
            <span className="animate-spin inline-block w-4 h-4 border-2 border-white border-t-transparent rounded-full" />
            Writing to database…
          </>
        ) : (
          <>
            <ArrowRight size={18} />
            Confirm &amp; Write to Database
          </>
        )}
      </button>
    </div>
  )
}
