import { useState } from 'react'
import type { ReactNode } from 'react'
import { startSchemaMapping } from '../../api'
import { Database, Upload, Zap, FileText, Code, Link } from 'lucide-react'

interface Props {
  orgId: string
  onStarted: (sessionId: string) => void
}

type ConnectorType = 'fiix' | 'upload'
type UploadFormat = 'yaml' | 'json' | 'sql' | 'db_url'

const FORMAT_LABELS: Record<UploadFormat, { label: string; icon: ReactNode; placeholder: string }> = {
  yaml:   { label: 'YAML',       icon: <FileText size={14} />, placeholder: '# YAML schema\ntables:\n  - name: assets\n    columns: ...' },
  json:   { label: 'JSON',       icon: <Code size={14} />,     placeholder: '{\n  "tables": [...]\n}' },
  sql:    { label: 'SQL DDL',    icon: <Database size={14} />, placeholder: 'CREATE TABLE assets (\n  id INT PRIMARY KEY,\n  ...\n);' },
  db_url: { label: 'DB URL',     icon: <Link size={14} />,     placeholder: 'postgresql://user:pass@host:5432/dbname' },
}

export default function SchemaStartPanel({ orgId, onStarted }: Props) {
  const [connectorType, setConnectorType] = useState<ConnectorType>('fiix')
  const [cmmsName, setCmmsName] = useState('')
  const [uploadFormat, setUploadFormat] = useState<UploadFormat>('yaml')
  const [schemaContent, setSchemaContent] = useState('')
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  // Fiix credentials
  const [fiixSubdomain, setFiixSubdomain] = useState('')
  const [fiixAppKey, setFiixAppKey] = useState('')
  const [fiixAccessKey, setFiixAccessKey] = useState('')
  const [fiixSecretKey, setFiixSecretKey] = useState('')

  async function handleSubmit() {
    setLoading(true)
    setError(null)
    try {
      const result = await startSchemaMapping({
        connectorType,
        externalCmmsName: cmmsName || (connectorType === 'fiix' ? 'Fiix' : 'Custom'),
        organizationId: orgId,
        ...(connectorType === 'fiix' && {
          fiixSubdomain,
          fiixAppKey,
          fiixAccessKey,
          fiixSecretKey,
        }),
        ...(connectorType === 'upload' && uploadFormat !== 'db_url' && {
          schemaContent,
          schemaFormat: uploadFormat,
          schemaSource: `${uploadFormat}_file`,
        }),
        ...(connectorType === 'upload' && uploadFormat === 'db_url' && {
          schemaContent: schemaContent,
          schemaFormat: 'json',
          schemaSource: 'db_introspection',
          dbUrl: schemaContent,
        }),
      })
      onStarted(result.schema_mapping_id)
    } catch (err: any) {
      setError(err.message ?? 'Failed to start schema mapping')
      setLoading(false)
    }
  }

  const fiixCredentialsValid =
    fiixSubdomain.trim().length > 0 &&
    fiixAppKey.trim().length > 0 &&
    fiixAccessKey.trim().length > 0 &&
    fiixSecretKey.trim().length > 0

  const isValid =
    (connectorType === 'fiix' && fiixCredentialsValid) ||
    (connectorType === 'upload' && schemaContent.trim().length > 0)

  return (
    <div className="max-w-2xl">
      <div className="mb-6">
        <h2 className="text-xl font-bold text-slate-900">Schema Mapper</h2>
        <p className="text-sm text-slate-500 mt-1">
          Map an external CMMS schema to the Plenum CAFM canonical fields using
          an 8-node AI pipeline with HITL review gates.
        </p>
      </div>

      <div className="card p-6 space-y-5">
        {/* CMMS name */}
        <div>
          <label className="block text-sm font-medium text-slate-700 mb-1.5">CMMS name</label>
          <input
            className="input w-full"
            placeholder="e.g. Fiix, Maximo, SAP PM, ServiceNow…"
            value={cmmsName}
            onChange={e => setCmmsName(e.target.value)}
          />
        </div>

        {/* Source type */}
        <div>
          <label className="block text-sm font-medium text-slate-700 mb-2">Schema source</label>
          <div className="grid grid-cols-2 gap-3">
            <button
              onClick={() => setConnectorType('fiix')}
              className={`flex items-start gap-3 p-4 rounded-xl border-2 text-left transition-all ${
                connectorType === 'fiix'
                  ? 'border-indigo-500 bg-indigo-50'
                  : 'border-slate-200 hover:border-slate-300'
              }`}
            >
              <div className={`w-8 h-8 rounded-lg flex items-center justify-center shrink-0 ${
                connectorType === 'fiix' ? 'bg-indigo-100' : 'bg-slate-100'
              }`}>
                <Zap size={16} className={connectorType === 'fiix' ? 'text-indigo-600' : 'text-slate-500'} />
              </div>
              <div>
                <div className="text-sm font-semibold text-slate-800">Fiix CMMS API</div>
                <div className="text-xs text-slate-500 mt-0.5">Live fetch from connected Fiix instance</div>
              </div>
            </button>

            <button
              onClick={() => setConnectorType('upload')}
              className={`flex items-start gap-3 p-4 rounded-xl border-2 text-left transition-all ${
                connectorType === 'upload'
                  ? 'border-indigo-500 bg-indigo-50'
                  : 'border-slate-200 hover:border-slate-300'
              }`}
            >
              <div className={`w-8 h-8 rounded-lg flex items-center justify-center shrink-0 ${
                connectorType === 'upload' ? 'bg-indigo-100' : 'bg-slate-100'
              }`}>
                <Upload size={16} className={connectorType === 'upload' ? 'text-indigo-600' : 'text-slate-500'} />
              </div>
              <div>
                <div className="text-sm font-semibold text-slate-800">Custom schema</div>
                <div className="text-xs text-slate-500 mt-0.5">Paste YAML, JSON, SQL DDL, or DB URL</div>
              </div>
            </button>
          </div>
        </div>

        {/* Upload format picker + content */}
        {connectorType === 'upload' && (
          <div className="space-y-3">
            <div>
              <label className="block text-sm font-medium text-slate-700 mb-2">Format</label>
              <div className="flex gap-2">
                {(Object.keys(FORMAT_LABELS) as UploadFormat[]).map(fmt => (
                  <button
                    key={fmt}
                    onClick={() => setUploadFormat(fmt)}
                    className={`flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium transition-colors ${
                      uploadFormat === fmt
                        ? 'bg-indigo-600 text-white'
                        : 'bg-slate-100 text-slate-600 hover:bg-slate-200'
                    }`}
                  >
                    {FORMAT_LABELS[fmt].icon}
                    {FORMAT_LABELS[fmt].label}
                  </button>
                ))}
              </div>
            </div>

            <div>
              <label className="block text-sm font-medium text-slate-700 mb-1.5">
                {uploadFormat === 'db_url' ? 'Database URL' : 'Schema content'}
              </label>
              <textarea
                className="input w-full font-mono text-xs resize-none"
                rows={uploadFormat === 'db_url' ? 2 : 10}
                placeholder={FORMAT_LABELS[uploadFormat].placeholder}
                value={schemaContent}
                onChange={e => setSchemaContent(e.target.value)}
              />
            </div>
          </div>
        )}

        {/* Fiix credentials */}
        {connectorType === 'fiix' && (
          <div className="space-y-3">
            <label className="block text-sm font-medium text-slate-700">Fiix credentials</label>
            <input
              className="input w-full"
              placeholder="Subdomain (e.g. plenumtechnology)"
              value={fiixSubdomain}
              onChange={e => setFiixSubdomain(e.target.value)}
            />
            <input
              className="input w-full font-mono text-sm"
              placeholder="App Key"
              value={fiixAppKey}
              onChange={e => setFiixAppKey(e.target.value)}
            />
            <input
              className="input w-full font-mono text-sm"
              placeholder="Access Key"
              value={fiixAccessKey}
              onChange={e => setFiixAccessKey(e.target.value)}
            />
            <input
              className="input w-full font-mono text-sm"
              type="password"
              placeholder="Secret Key"
              value={fiixSecretKey}
              onChange={e => setFiixSecretKey(e.target.value)}
            />
          </div>
        )}

        {error && (
          <div className="rounded-lg bg-red-50 border border-red-200 px-4 py-3 text-sm text-red-700">
            {error}
          </div>
        )}

        <button
          className="btn-primary px-8 py-3 text-base w-full"
          onClick={handleSubmit}
          disabled={loading || !isValid}
        >
          {loading ? (
            <>
              <span className="animate-spin inline-block w-4 h-4 border-2 border-white border-t-transparent rounded-full" />
              Starting…
            </>
          ) : (
            <>
              <Database size={18} />
              Start schema mapping
            </>
          )}
        </button>
      </div>

      {/* Pipeline overview */}
      <div className="mt-6 card p-5">
        <h3 className="text-sm font-semibold text-slate-700 mb-3">8-node pipeline</h3>
        <div className="space-y-2">
          {[
            { n: 0, label: 'Canonical schema fetch', desc: 'Load plenum_cafm target schema' },
            { n: 1, label: 'Schema ingestion',       desc: 'Parse & normalise external schema' },
            { n: 2, label: 'Deterministic mapping',  desc: 'Exact → alias → regex → Haiku' },
            { n: 3, label: 'Semantic mapping',       desc: 'Embedding cosine similarity' },
            { n: 4, label: 'Field mapping review',   desc: 'GATE 1 — HITL approval', isGate: true },
            { n: 5, label: 'Hierarchy detection',    desc: 'FK relationships & tree' },
            { n: 6, label: 'Hierarchy verification', desc: 'GATE 2 — HITL approval', isGate: true },
            { n: 7, label: 'Output generation',      desc: 'JsonMapperConfig → DB write' },
          ].map(step => (
            <div key={step.n} className="flex items-center gap-3">
              <div className={`w-6 h-6 rounded-full flex items-center justify-center text-xs font-bold shrink-0 ${
                step.isGate ? 'bg-amber-100 text-amber-700' : 'bg-slate-100 text-slate-600'
              }`}>
                {step.n}
              </div>
              <div className="flex-1 min-w-0">
                <span className={`text-xs font-medium ${step.isGate ? 'text-amber-700' : 'text-slate-700'}`}>
                  {step.label}
                </span>
                <span className="text-xs text-slate-400 ml-2">{step.desc}</span>
              </div>
            </div>
          ))}
        </div>
      </div>
    </div>
  )
}
