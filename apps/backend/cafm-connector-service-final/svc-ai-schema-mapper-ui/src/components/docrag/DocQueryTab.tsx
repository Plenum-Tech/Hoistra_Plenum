import { useState } from 'react'
import { Search, AlertCircle, ChevronDown, ChevronUp, BookOpen, Link2, BarChart2 } from 'lucide-react'
import { ragQuery } from '../../api'
import type { DocRagQueryResponse, DocRagCitation, DocRagMatchedRow, DocRagRetrievedChunk } from '../../types'

function ConfidenceBar({ value, compact = false }: { value: number; compact?: boolean }) {
  const pct = Math.round(value * 100)
  const color = value >= 0.7 ? 'bg-emerald-500' : value >= 0.4 ? 'bg-amber-400' : 'bg-red-400'
  return (
    <div className={`flex items-center gap-2 ${compact ? '' : 'flex-1'}`}>
      <div className={`${compact ? 'w-16' : 'flex-1'} h-1.5 bg-slate-200 rounded-full overflow-hidden`}>
        <div className={`h-full rounded-full ${color}`} style={{ width: `${pct}%` }} />
      </div>
      <span className="text-xs text-slate-500 tabular-nums w-8 text-right">{pct}%</span>
    </div>
  )
}

function CitationCard({ c }: { c: DocRagCitation }) {
  return (
    <div className="rounded-lg border border-slate-200 px-3 py-2.5 bg-white">
      <div className="flex items-start gap-2">
        <BookOpen size={13} className="text-indigo-400 shrink-0 mt-0.5" />
        <div className="min-w-0">
          <div className="flex items-center gap-2 flex-wrap">
            <span className="text-xs font-semibold text-slate-700 truncate">{c.file_name}</span>
            {c.page_start != null && (
              <span className="text-xs text-slate-400">p.{c.page_start}</span>
            )}
            {c.section && (
              <span className="text-xs bg-slate-100 text-slate-500 px-1.5 py-0.5 rounded">
                {c.section}
              </span>
            )}
          </div>
          <p className="text-xs text-slate-500 mt-1 italic">"{c.quote}"</p>
        </div>
      </div>
    </div>
  )
}

function ScorePill({
  semantic,
  bm25,
  metadata,
}: {
  semantic: number
  bm25: number
  metadata: number
}) {
  const semWins = semantic >= bm25
  return (
    <div className="flex items-center gap-1 flex-wrap">
      <div className={`flex items-center gap-0.5 px-1.5 py-0.5 rounded ${semWins ? 'bg-purple-100' : 'bg-slate-100'}`}>
        <span className={`text-xs ${semWins ? 'text-purple-500' : 'text-slate-400'}`}>sem</span>
        <span className={`text-xs font-bold tabular-nums ${semWins ? 'text-purple-700' : 'text-slate-500'}`}>{(semantic * 100).toFixed(0)}</span>
      </div>
      <span className="text-slate-300 text-xs">vs</span>
      <div className={`flex items-center gap-0.5 px-1.5 py-0.5 rounded ${!semWins ? 'bg-orange-100' : 'bg-slate-100'}`}>
        <span className={`text-xs ${!semWins ? 'text-orange-500' : 'text-slate-400'}`}>bm25</span>
        <span className={`text-xs font-bold tabular-nums ${!semWins ? 'text-orange-700' : 'text-slate-500'}`}>{(bm25 * 100).toFixed(0)}</span>
      </div>
      <div className="flex items-center gap-0.5 px-1.5 py-0.5 rounded bg-slate-100">
        <span className="text-xs text-slate-400">meta</span>
        <span className="text-xs font-bold tabular-nums text-slate-500">{(metadata * 100).toFixed(0)}</span>
      </div>
    </div>
  )
}

function MatchedRowCard({ row }: { row: DocRagMatchedRow }) {
  const [expanded, setExpanded] = useState(false)
  const conf = row.confidence
  const confColor = conf >= 0.7 ? 'border-emerald-200 bg-emerald-50' : conf >= 0.4 ? 'border-amber-200 bg-amber-50' : 'border-slate-200 bg-white'
  const badgeColor = conf >= 0.7 ? 'bg-emerald-100 text-emerald-700' : conf >= 0.4 ? 'bg-amber-100 text-amber-700' : 'bg-slate-100 text-slate-600'

  return (
    <div className={`rounded-lg border ${confColor} overflow-hidden`}>
      <button
        onClick={() => setExpanded(e => !e)}
        className="w-full flex items-start gap-3 px-3 py-2.5 text-left hover:bg-black/5 transition-colors"
      >
        <div className="flex-1 min-w-0">
          {/* Top line */}
          <div className="flex items-center gap-2 flex-wrap">
            <span className="text-xs font-mono font-semibold text-slate-800">{row.row_pk}</span>
            <span className="text-xs text-slate-400">{row.source_table}</span>
            <span className={`text-xs px-1.5 py-0.5 rounded font-medium ${badgeColor}`}>
              {row.match_method}
            </span>
          </div>
          {/* Score line: bar + sem vs bm25 */}
          <div className="flex items-center gap-3 mt-1.5 flex-wrap">
            <ConfidenceBar value={conf} compact />
            {row.match_details && (
              <ScorePill
                semantic={row.match_details.semantic_score}
                bm25={row.match_details.bm25_overlap}
                metadata={row.match_details.metadata_overlap}
              />
            )}
          </div>
        </div>
        {expanded ? <ChevronUp size={14} className="text-slate-400 shrink-0 mt-1" /> : <ChevronDown size={14} className="text-slate-400 shrink-0 mt-1" />}
      </button>

      {expanded && (
        <div className="px-3 pb-3 border-t border-slate-100 space-y-2 pt-2">
          {/* Row data */}
          <div className="grid grid-cols-2 gap-x-4 gap-y-1">
            {Object.entries(row.row_data).map(([k, v]) => (
              <div key={k} className="text-xs">
                <span className="text-slate-400">{k}: </span>
                <span className="text-slate-700 font-medium">{String(v ?? '—')}</span>
              </div>
            ))}
          </div>
          {/* Evidence */}
          {row.evidence && (
            <p className="text-xs text-slate-500 italic border-t border-slate-100 pt-2">
              "{row.evidence}"
            </p>
          )}
          {/* Key flags */}
          {row.match_details && (row.match_details.exact_key_match || row.match_details.normalized_key_match) && (
            <div className="flex gap-2 border-t border-slate-100 pt-2">
              {row.match_details.exact_key_match && (
                <span className="text-xs bg-indigo-50 text-indigo-600 px-2 py-0.5 rounded">✓ exact key</span>
              )}
              {row.match_details.normalized_key_match && (
                <span className="text-xs bg-blue-50 text-blue-600 px-2 py-0.5 rounded">✓ normalized key</span>
              )}
            </div>
          )}
        </div>
      )}
    </div>
  )
}

function ChunksDebugTable({ chunks }: { chunks: DocRagRetrievedChunk[] }) {
  return (
    <div className="overflow-x-auto rounded-xl border border-slate-200">
      <table className="w-full text-xs">
        <thead>
          <tr className="bg-slate-50 border-b border-slate-200">
            <th className="text-left px-3 py-2 text-slate-500 font-medium">#</th>
            <th className="text-left px-3 py-2 text-slate-500 font-medium">Score</th>
            <th className="text-left px-3 py-2 text-slate-500 font-medium">Vec /100</th>
            <th className="text-left px-3 py-2 text-slate-500 font-medium">BM25 /100</th>
            <th className="text-left px-3 py-2 text-slate-500 font-medium">Type</th>
            <th className="text-left px-3 py-2 text-slate-500 font-medium">File</th>
            <th className="text-left px-3 py-2 text-slate-500 font-medium">Preview</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-slate-100">
          {chunks.map((c, i) => (
            <tr key={c.chunk_id} className="hover:bg-slate-50">
              <td className="px-3 py-2 text-slate-400 tabular-nums">{i + 1}</td>
              <td className="px-3 py-2 font-semibold text-slate-700 tabular-nums">{c.score.toFixed(3)}</td>
              <td className="px-3 py-2 text-slate-500 tabular-nums">{(c.vector_score * 100).toFixed(0)}</td>
              <td className="px-3 py-2 text-slate-500 tabular-nums">{(c.bm25_score * 100).toFixed(0)}</td>
              <td className="px-3 py-2">
                <span className="px-1.5 py-0.5 rounded bg-slate-100 text-slate-600">{c.block_type}</span>
              </td>
              <td className="px-3 py-2 text-slate-600 max-w-[120px] truncate">{c.file_name || '—'}</td>
              <td className="px-3 py-2 text-slate-500 max-w-[240px] truncate">{c.text_content.slice(0, 120)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

export default function DocQueryTab() {
  const [query, setQuery] = useState('')
  const [topK, setTopK] = useState(8)
  const [debugMode, setDebugMode] = useState(false)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [result, setResult] = useState<DocRagQueryResponse | null>(null)
  const [showChunks, setShowChunks] = useState(false)

  async function handleSubmit() {
    if (!query.trim()) return
    setLoading(true)
    setError(null)
    setResult(null)
    try {
      const r = await ragQuery({ query: query.trim(), top_k: topK, debug: debugMode })
      setResult(r)
    } catch (e: any) {
      setError(e.message ?? 'Query failed')
    } finally {
      setLoading(false)
    }
  }

  const confColor = result
    ? result.confidence >= 0.7 ? 'text-emerald-600' : result.confidence >= 0.4 ? 'text-amber-600' : 'text-red-500'
    : ''

  return (
    <div className="space-y-6">
      {/* Query input */}
      <div className="rounded-xl border border-slate-200 bg-white p-5 space-y-4">
        <div>
          <label className="block text-sm font-medium text-slate-700 mb-1.5">Question</label>
          <textarea
            className="input w-full resize-none text-sm"
            rows={3}
            placeholder="e.g. What maintenance was performed on AHU-017? Which assets have open work orders?"
            value={query}
            onChange={e => setQuery(e.target.value)}
            onKeyDown={e => { if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) handleSubmit() }}
          />
        </div>

        <div className="flex items-center gap-6 flex-wrap">
          {/* top_k slider */}
          <div className="flex items-center gap-3">
            <label className="text-xs font-medium text-slate-600 whitespace-nowrap">
              Top-K results
            </label>
            <input
              type="range" min={1} max={30} step={1}
              value={topK}
              onChange={e => setTopK(Number(e.target.value))}
              className="w-24 accent-indigo-600"
            />
            <span className="text-xs font-semibold text-slate-700 tabular-nums w-4">{topK}</span>
          </div>

          {/* Debug toggle */}
          <label className="flex items-center gap-2 cursor-pointer">
            <div
              onClick={() => setDebugMode(d => !d)}
              className={`relative w-8 h-4 rounded-full transition-colors ${debugMode ? 'bg-indigo-600' : 'bg-slate-300'}`}
            >
              <span className={`absolute top-0.5 w-3 h-3 rounded-full bg-white transition-transform shadow-sm ${debugMode ? 'translate-x-4' : 'translate-x-0.5'}`} />
            </div>
            <span className="text-xs font-medium text-slate-600">Debug mode</span>
          </label>
        </div>

        {error && (
          <div className="flex items-start gap-2 rounded-lg bg-red-50 border border-red-200 px-3 py-2.5">
            <AlertCircle size={14} className="text-red-500 shrink-0 mt-0.5" />
            <span className="text-sm text-red-700">{error}</span>
          </div>
        )}

        <button
          onClick={handleSubmit}
          disabled={loading || !query.trim()}
          className="btn-primary flex items-center gap-2 px-5 py-2.5"
        >
          {loading ? (
            <span className="animate-spin inline-block w-4 h-4 border-2 border-white border-t-transparent rounded-full" />
          ) : (
            <Search size={15} />
          )}
          {loading ? 'Searching…' : 'Search'}
        </button>
      </div>

      {/* Results */}
      {result && (
        <div className="space-y-5">
          {/* Metrics bar */}
          <div className="flex items-center gap-4 flex-wrap">
            <div className="flex items-center gap-1.5">
              <BarChart2 size={13} className="text-slate-400" />
              <span className="text-xs text-slate-500">Type:</span>
              <span className="text-xs font-semibold text-slate-700">{result.query_type}</span>
            </div>
            <div className="flex items-center gap-1.5">
              <span className="text-xs text-slate-500">Confidence:</span>
              <span className={`text-xs font-bold ${confColor}`}>{(result.confidence * 100).toFixed(0)}%</span>
            </div>
            <div className="flex items-center gap-1.5">
              <span className="text-xs text-slate-500">Latency:</span>
              <span className="text-xs font-semibold text-slate-700">{result.latency_ms} ms</span>
            </div>
            <div className="flex items-center gap-1.5">
              <span className="text-xs text-slate-500">Model:</span>
              <span className="text-xs font-semibold text-slate-700">{result.model_name}</span>
            </div>
          </div>

          {/* Answer */}
          <div className="rounded-xl border border-slate-200 bg-white p-5">
            <h3 className="text-sm font-semibold text-slate-700 mb-3">Answer</h3>
            <p className="text-sm text-slate-700 whitespace-pre-wrap leading-relaxed">{result.answer}</p>
          </div>

          {/* Citations */}
          {result.citations.length > 0 && (
            <div>
              <h3 className="text-sm font-semibold text-slate-700 mb-2">
                Citations <span className="text-slate-400 font-normal">({result.citations.length})</span>
              </h3>
              <div className="space-y-2">
                {result.citations.map((c, i) => <CitationCard key={i} c={c} />)}
              </div>
            </div>
          )}

          {/* Matched rows */}
          {result.matched_rows.length > 0 && (
            <div>
              <div className="flex items-center gap-2 mb-2">
                <Link2 size={14} className="text-slate-500" />
                <h3 className="text-sm font-semibold text-slate-700">
                  Matched rows <span className="text-slate-400 font-normal">({result.matched_rows.length})</span>
                </h3>
              </div>
              <div className="space-y-2">
                {result.matched_rows.map((r, i) => <MatchedRowCard key={i} row={r} />)}
              </div>
            </div>
          )}

          {/* Debug chunks */}
          {debugMode && result.retrieved_chunks && result.retrieved_chunks.length > 0 && (
            <div>
              <button
                onClick={() => setShowChunks(c => !c)}
                className="flex items-center gap-2 text-sm font-semibold text-slate-700 mb-2"
              >
                {showChunks ? <ChevronUp size={14} /> : <ChevronDown size={14} />}
                Retrieved chunks ({result.retrieved_chunks.length})
                {result.stages && (
                  <span className="text-xs font-normal text-slate-400 ml-2">
                    vec:{result.stages.vector_hits} bm25:{result.stages.bm25_hits} reranked:{result.stages.fused_reranked}
                  </span>
                )}
              </button>
              {showChunks && <ChunksDebugTable chunks={result.retrieved_chunks} />}
            </div>
          )}
        </div>
      )}
    </div>
  )
}
