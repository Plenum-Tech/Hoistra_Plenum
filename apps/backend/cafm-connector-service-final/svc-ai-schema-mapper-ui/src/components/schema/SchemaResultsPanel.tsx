import type { SchemaMappingState } from '../../types'
import { CheckCircle, RotateCcw, BarChart3 } from 'lucide-react'

interface Props {
  session: SchemaMappingState
  onReset: () => void
}

export default function SchemaResultsPanel({ session, onReset }: Props) {
  const stats = session.stats

  return (
    <div className="max-w-2xl">
      <div className="card p-8 flex flex-col items-center text-center gap-4 mb-6">
        <div className="w-14 h-14 rounded-2xl bg-green-50 flex items-center justify-center">
          <CheckCircle size={32} className="text-green-500" />
        </div>
        <div>
          <h2 className="text-xl font-bold text-slate-900">Schema mapping complete</h2>
          <p className="text-sm text-slate-500 mt-1">
            {session.external_cmms_name} schema has been mapped to Plenum CAFM canonical fields
            and written to the database.
          </p>
        </div>
      </div>

      {/* Stats */}
      {stats && (
        <div className="card p-5 mb-5">
          <div className="flex items-center gap-2 mb-4">
            <BarChart3 size={16} className="text-indigo-600" />
            <h3 className="text-sm font-semibold text-slate-700">Mapping summary</h3>
          </div>
          <div className="grid grid-cols-2 gap-4">
            {[
              { label: 'Total tables',        value: stats.total_tables },
              { label: 'Total fields',        value: stats.total_fields },
              { label: 'Tier 1 mapped',       value: stats.tier1_mapped },
              { label: 'Tier 2 auto-mapped',  value: stats.tier2_auto_mapped },
              { label: 'Flagged for review',  value: stats.tier2_flagged },
              { label: 'Unmapped',            value: stats.unmapped },
              { label: 'FK relationships',    value: stats.detected_fk_count },
              { label: 'Hierarchy depth',     value: stats.hierarchy_depth },
            ].map(({ label, value }) =>
              value != null ? (
                <div key={label} className="flex justify-between items-center py-1.5 border-b border-slate-100 last:border-0">
                  <span className="text-sm text-slate-600">{label}</span>
                  <span className="font-mono font-bold text-slate-800">{value}</span>
                </div>
              ) : null
            )}
            {stats.mapping_coverage_pct != null && (
              <div className="col-span-2 mt-2">
                <div className="flex justify-between text-sm mb-1.5">
                  <span className="text-slate-600">Mapping coverage</span>
                  <span className="font-mono font-bold text-indigo-600">
                    {Math.round(stats.mapping_coverage_pct)}%
                  </span>
                </div>
                <div className="h-2 bg-slate-100 rounded-full overflow-hidden">
                  <div
                    className="h-full bg-indigo-500 rounded-full"
                    style={{ width: `${Math.round(stats.mapping_coverage_pct)}%` }}
                  />
                </div>
              </div>
            )}
          </div>
        </div>
      )}

      <div className="flex items-center gap-3 text-xs text-slate-400 mb-5">
        <span>Session: <code className="font-mono">{session.schema_mapping_id.slice(0, 8)}…</code></span>
        {session.completed_at && (
          <span>Completed: {new Date(session.completed_at).toLocaleString()}</span>
        )}
      </div>

      <button className="btn-secondary gap-2" onClick={onReset}>
        <RotateCcw size={14} />
        New schema mapping
      </button>
    </div>
  )
}
