import type { MigrationState } from '../types'
import { CheckCircle, FileJson, FileText, Database, FileBarChart, RotateCcw, Download } from 'lucide-react'

interface Props {
  migration: MigrationState
  onReset: () => void
}

export default function ResultsPanel({ migration, onReset }: Props) {
  const downloads = [
    { label: 'JSON',       url: migration.output_json_url,       icon: <FileJson size={16} />,    desc: 'IntermediateSchema JSON' },
    { label: 'Excel',      url: migration.output_csv_url,        icon: <FileText size={16} />,    desc: 'All tables as sheets' },
    { label: 'SQL',        url: migration.output_sql_url,        icon: <Database size={16} />,    desc: 'INSERT statements' },
    { label: 'PDF Report', url: migration.migration_report_url,  icon: <FileBarChart size={16} />, desc: 'Migration summary' },
  ].filter(d => d.url)

  return (
    <div className="max-w-2xl">
      {/* Success header */}
      <div className="card p-8 text-center mb-6">
        <div className="w-16 h-16 rounded-2xl bg-green-100 flex items-center justify-center mx-auto mb-4">
          <CheckCircle size={32} className="text-green-600" />
        </div>
        <h2 className="text-2xl font-bold text-slate-900 mb-2">Migration Complete</h2>
        <p className="text-slate-500 text-sm">
          {migration.cmms_name} data has been mapped, validated, and written to the platform.
        </p>
        <p className="font-mono text-xs text-slate-400 mt-3">{migration.migration_id}</p>
      </div>

      {/* Stats */}
      {migration.total_fields > 0 && (
        <div className="card p-6 mb-6">
          <h3 className="text-sm font-semibold text-slate-700 mb-4">Mapping summary</h3>
          <div className="grid grid-cols-2 gap-3">
            <Stat label="T1 deterministic" value={migration.t1_mapped_count} color="text-green-600" bg="bg-green-50" />
            <Stat label="T2 semantic" value={migration.t2_auto_count} color="text-blue-600" bg="bg-blue-50" />
            <Stat label="T2 human-reviewed" value={migration.t2_human_count} color="text-amber-600" bg="bg-amber-50" />
            <Stat label="Unmapped (raw)" value={migration.unmapped_count} color="text-slate-600" bg="bg-slate-100" />
          </div>
          {migration.total_fields > 0 && (
            <div className="mt-4 pt-4 border-t border-slate-100">
              <div className="flex items-center justify-between text-sm mb-1.5">
                <span className="text-slate-500">Coverage</span>
                <span className="font-semibold text-slate-800 font-mono">
                  {Math.round(((migration.total_fields - migration.unmapped_count) / migration.total_fields) * 100)}%
                </span>
              </div>
              <div className="h-2 bg-slate-100 rounded-full overflow-hidden">
                <div
                  className="h-full bg-green-500 rounded-full"
                  style={{ width: `${((migration.total_fields - migration.unmapped_count) / migration.total_fields) * 100}%` }}
                />
              </div>
              <p className="text-xs text-slate-400 mt-1.5">
                {migration.total_fields - migration.unmapped_count} of {migration.total_fields} fields mapped
              </p>
            </div>
          )}
        </div>
      )}

      {/* Downloads */}
      {downloads.length > 0 && (
        <div className="card p-6 mb-6">
          <h3 className="text-sm font-semibold text-slate-700 mb-4 flex items-center gap-2">
            <Download size={14} />
            Download outputs
          </h3>
          <div className="space-y-2">
            {downloads.map(d => (
              <a
                key={d.label}
                href={d.url ?? undefined}
                target="_blank"
                rel="noopener noreferrer"
                className="flex items-center gap-3 p-3 rounded-lg border border-slate-200 hover:border-indigo-300 hover:bg-indigo-50 transition-colors group"
              >
                <div className="w-8 h-8 rounded-lg bg-indigo-100 flex items-center justify-center text-indigo-600 group-hover:bg-indigo-200 transition-colors">
                  {d.icon}
                </div>
                <div className="flex-1 min-w-0">
                  <p className="text-sm font-semibold text-slate-800">{d.label}</p>
                  <p className="text-xs text-slate-500">{d.desc}</p>
                </div>
                <Download size={14} className="text-slate-400 group-hover:text-indigo-600 transition-colors" />
              </a>
            ))}
          </div>
        </div>
      )}

      {/* New migration */}
      <button className="btn-secondary w-full py-3" onClick={onReset}>
        <RotateCcw size={16} />
        Start new migration
      </button>
    </div>
  )
}

function Stat({ label, value, color, bg }: { label: string; value: number; color: string; bg: string }) {
  return (
    <div className={`rounded-lg px-4 py-3 ${bg}`}>
      <div className={`text-xl font-bold font-mono ${color}`}>{value}</div>
      <div className="text-xs text-slate-500 mt-0.5">{label}</div>
    </div>
  )
}
