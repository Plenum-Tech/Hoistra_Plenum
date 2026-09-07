import { useState } from 'react'
import { submitGateFinal } from '../../api'
import type { FinalPayload } from '../../types'
import { CheckCircle, XCircle, Database, FileText, BarChart2, RotateCcw } from 'lucide-react'

interface Props {
  migrationId: string
  payload: FinalPayload
  onSubmitted: () => void
  onReset: () => void
}

export default function GateFinal({ migrationId, payload, onSubmitted, onReset }: Props) {
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const summary = payload.summary ?? {}

  async function handleAction(action: 'confirm' | 'reject') {
    setLoading(true)
    setError(null)
    try {
      await submitGateFinal(migrationId, { decisions: { confirmed: action === 'confirm' } })
      if (action === 'confirm') {
        onSubmitted()
      } else {
        onReset()
      }
    } catch (err: any) {
      setError(err.message ?? 'Failed to submit confirmation')
      setLoading(false)
    }
  }

  const confidence = summary.overall_confidence ?? 0
  const confidencePct = Math.round(confidence * 100)
  const confidenceColor = confidencePct >= 85 ? 'text-green-600' : confidencePct >= 70 ? 'text-amber-600' : 'text-red-600'
  const confidenceBg = confidencePct >= 85 ? 'bg-green-50 border-green-200' : confidencePct >= 70 ? 'bg-amber-50 border-amber-200' : 'bg-red-50 border-red-200'

  const entityCounts: Record<string, number> = summary.entity_counts ?? {}

  return (
    <div className="max-w-2xl">
      {/* Header */}
      <div className="flex items-start gap-4 mb-6">
        <div className="w-10 h-10 rounded-xl bg-green-100 flex items-center justify-center shrink-0">
          <Database size={20} className="text-green-600" />
        </div>
        <div>
          <h2 className="text-lg font-bold text-slate-900">Write to Target DB</h2>
          <p className="text-sm text-slate-500 mt-0.5">
            Review the migration summary before writing to the platform. This action cannot be undone.
          </p>
        </div>
      </div>

      {/* Confidence banner */}
      <div className={`rounded-xl border px-5 py-4 mb-6 ${confidenceBg}`}>
        <div className="flex items-center justify-between">
          <div>
            <p className="text-xs font-semibold text-slate-500 uppercase tracking-wider">Overall confidence</p>
            <div className={`text-4xl font-bold font-mono mt-1 ${confidenceColor}`}>
              {confidencePct}%
            </div>
          </div>
          <BarChart2 size={40} className={`opacity-20 ${confidenceColor}`} />
        </div>
        <div className="mt-3 h-2 bg-white/60 rounded-full overflow-hidden">
          <div
            className={`h-full rounded-full transition-all duration-700 ${
              confidencePct >= 85 ? 'bg-green-500' : confidencePct >= 70 ? 'bg-amber-500' : 'bg-red-500'
            }`}
            style={{ width: `${confidencePct}%` }}
          />
        </div>
      </div>

      {/* Summary card */}
      <div className="card p-6 mb-6 space-y-5">
        {/* Source file */}
        {summary.source_filename && (
          <div className="flex items-center gap-3">
            <div className="w-8 h-8 rounded-lg bg-slate-100 flex items-center justify-center shrink-0">
              <FileText size={16} className="text-slate-500" />
            </div>
            <div>
              <p className="text-xs text-slate-500">Source file</p>
              <p className="text-sm font-semibold text-slate-800">{summary.source_filename}</p>
            </div>
          </div>
        )}

        {/* Entity totals */}
        {summary.total_entities != null && (
          <div>
            <p className="text-xs font-medium text-slate-500 mb-3">Entities ready to write</p>
            <div className="grid grid-cols-2 gap-3">
              <div className="rounded-lg bg-indigo-50 px-4 py-3">
                <div className="text-2xl font-bold font-mono text-indigo-700">
                  {summary.total_entities.toLocaleString()}
                </div>
                <div className="text-xs text-indigo-600 mt-0.5">Total entities</div>
              </div>
              {Object.entries(entityCounts).map(([entity, count]) => (
                <div key={entity} className="rounded-lg bg-slate-50 px-4 py-3">
                  <div className="text-xl font-bold font-mono text-slate-700">
                    {count.toLocaleString()}
                  </div>
                  <div className="text-xs text-slate-500 mt-0.5 capitalize">{entity}</div>
                </div>
              ))}
            </div>
          </div>
        )}

        {/* Extra summary fields */}
        {Object.entries(summary)
          .filter(([k]) => !['source_filename', 'overall_confidence', 'total_entities', 'entity_counts'].includes(k))
          .map(([k, v]) => (
            <div key={k} className="flex items-center justify-between text-sm">
              <span className="text-slate-500 capitalize">{k.replace(/_/g, ' ')}</span>
              <span className="font-semibold text-slate-800">{String(v)}</span>
            </div>
          ))
        }
      </div>

      {error && (
        <div className="mb-4 rounded-lg bg-red-50 border border-red-200 px-4 py-3 text-sm text-red-700">
          {error}
        </div>
      )}

      {/* Action buttons */}
      <div className="flex gap-3">
        <button
          className="btn-primary flex-1 py-3 text-base"
          onClick={() => handleAction('confirm')}
          disabled={loading}
        >
          {loading ? (
            <>
              <span className="animate-spin inline-block w-4 h-4 border-2 border-white border-t-transparent rounded-full" />
              Writing to platform…
            </>
          ) : (
            <>
              <CheckCircle size={18} />
              Confirm & Write to Platform
            </>
          )}
        </button>

        <button
          className="btn-secondary py-3 px-6"
          onClick={() => handleAction('reject')}
          disabled={loading}
          title="Reject and start a new migration"
        >
          <XCircle size={18} className="text-red-500" />
          <span className="text-red-600">Reject</span>
        </button>
      </div>

      <p className="text-xs text-slate-400 mt-3 text-center">
        Confirm writes the IntermediateSchema to svc-ingestion. Reject discards this migration.
      </p>
    </div>
  )
}
