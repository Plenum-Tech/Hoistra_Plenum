import { useState } from 'react'
import { setApiBase, setDocRagBase, setConnectorBase, setTableEditorBase } from './api'
import { useMigration } from './hooks/useMigration'
import { useSchemaMapping } from './hooks/useSchemaMapping'
import UploadPanel from './components/UploadPanel'
import PipelineTracker from './components/PipelineTracker'
import MigrationContent from './components/MigrationContent'
import SchemaStartPanel from './components/schema/SchemaStartPanel'
import SchemaPipelineTracker from './components/schema/SchemaPipelineTracker'
import SchemaContent from './components/schema/SchemaContent'
import DocRagPanel from './components/docrag/DocRagPanel'
import TableCustomizerPanel from './components/tableCustomizer/TableCustomizerPanel'
import WorkOrderPanel from './components/workorders/WorkOrderPanel'
import { Settings, RefreshCw, Database, Upload, FileSearch, Table, ClipboardList } from 'lucide-react'

type Section = 'migration' | 'schema' | 'docrag' | 'tabcustomizer' | 'workorders'

export default function App() {
  const [apiUrl, setApiUrl] = useState('http://127.0.0.1:8003')
  const [docRagUrl, setDocRagUrl] = useState('http://127.0.0.1:8004')
  const [connectorUrl, setConnectorUrl] = useState('http://127.0.0.1:8000')
  const [tableEditorUrl, setTableEditorUrl] = useState('http://127.0.0.1:8005')
  const [woServiceUrl, setWoServiceUrl] = useState('http://127.0.0.1:8007')
  const [orgId, setOrgId] = useState('00000000-0000-0000-0000-000000000001')
  const [showSettings, setShowSettings] = useState(false)
  const [section, setSection] = useState<Section>('migration')

  // Migration workflow state
  const [migrationId, setMigrationId] = useState<string | null>(null)
  const { data: migration, error: migrationPollError, refresh: migrationRefresh } = useMigration(migrationId)

  // Schema mapping workflow state
  const [schemaSessionId, setSchemaSessionId] = useState<string | null>(null)
  const { data: schemaSession, error: schemaPollError, refresh: schemaRefresh } = useSchemaMapping(schemaSessionId)

  setApiBase(apiUrl)
  setDocRagBase(docRagUrl)
  setConnectorBase(connectorUrl)
  setTableEditorBase(tableEditorUrl)

  const activeId = section === 'migration' ? migrationId : section === 'schema' ? schemaSessionId : null
  const pollError = section === 'migration' ? migrationPollError : section === 'schema' ? schemaPollError : null

  function handleReset() {
    if (section === 'migration') setMigrationId(null)
    else if (section === 'schema') setSchemaSessionId(null)
  }

  function handleRefresh() {
    if (section === 'migration') migrationRefresh()
    else if (section === 'schema') schemaRefresh()
  }

  const hasSidebar =
    (section === 'migration' && migrationId && migration) ||
    (section === 'schema' && schemaSessionId && schemaSession)

  return (
    <div className="flex flex-col h-full">
      {/* ── Top nav ── */}
      <header className="bg-slate-900 text-white px-6 py-3 flex items-center justify-between shrink-0">
        <div className="flex items-center gap-4">
          <div className="flex items-center gap-3">
            <div className="w-8 h-8 bg-indigo-500 rounded-lg flex items-center justify-center font-bold text-sm">P</div>
            <div>
              <span className="font-semibold text-sm">Plenum CAFM</span>
              <span className="text-slate-400 text-sm ml-2">— AI Tools</span>
            </div>
          </div>

          {/* Section switcher */}
          <div className="flex gap-1 ml-6 bg-slate-800 rounded-lg p-1">
            <button
              onClick={() => setSection('migration')}
              className={`flex items-center gap-1.5 px-3 py-1.5 rounded-md text-xs font-medium transition-colors ${
                section === 'migration'
                  ? 'bg-indigo-600 text-white'
                  : 'text-slate-400 hover:text-slate-200'
              }`}
            >
              <Upload size={13} />
              Migration
            </button>
            <button
              onClick={() => setSection('schema')}
              className={`flex items-center gap-1.5 px-3 py-1.5 rounded-md text-xs font-medium transition-colors ${
                section === 'schema'
                  ? 'bg-indigo-600 text-white'
                  : 'text-slate-400 hover:text-slate-200'
              }`}
            >
              <Database size={13} />
              Schema Mapper
            </button>
            <button
              onClick={() => setSection('docrag')}
              className={`flex items-center gap-1.5 px-3 py-1.5 rounded-md text-xs font-medium transition-colors ${
                section === 'docrag'
                  ? 'bg-indigo-600 text-white'
                  : 'text-slate-400 hover:text-slate-200'
              }`}
            >
              <FileSearch size={13} />
              Doc RAG
            </button>
            <button
              onClick={() => setSection('tabcustomizer')}
              className={`flex items-center gap-1.5 px-3 py-1.5 rounded-md text-xs font-medium transition-colors ${
                section === 'tabcustomizer'
                  ? 'bg-indigo-600 text-white'
                  : 'text-slate-400 hover:text-slate-200'
              }`}
            >
              <Table size={13} />
              Table Editor
            </button>
            <button
              onClick={() => setSection('workorders')}
              className={`flex items-center gap-1.5 px-3 py-1.5 rounded-md text-xs font-medium transition-colors ${
                section === 'workorders'
                  ? 'bg-indigo-600 text-white'
                  : 'text-slate-400 hover:text-slate-200'
              }`}
            >
              <ClipboardList size={13} />
              Work Orders
            </button>
          </div>
        </div>

        <div className="flex items-center gap-3">
          {activeId && (
            <span className="font-mono text-xs text-slate-400">
              {activeId.slice(0, 8)}…
            </span>
          )}
          {activeId && (
            <button
              onClick={handleRefresh}
              className="p-1.5 rounded hover:bg-slate-700 transition-colors"
              title="Force refresh"
            >
              <RefreshCw size={14} className="text-slate-400" />
            </button>
          )}
          <button
            onClick={() => setShowSettings(s => !s)}
            className="p-1.5 rounded hover:bg-slate-700 transition-colors"
            title="Settings"
          >
            <Settings size={16} className="text-slate-400" />
          </button>
        </div>
      </header>

      {/* ── Settings drawer ── */}
      {showSettings && (
        <div className="bg-slate-800 border-b border-slate-700 px-6 py-4 flex items-end gap-6 shrink-0">
          <div>
            <label className="block text-xs font-medium text-slate-400 mb-1">Schema Mapper API URL</label>
            <input
              className="bg-slate-700 border border-slate-600 rounded-lg px-3 py-1.5 text-sm text-white w-64
                         focus:outline-none focus:ring-1 focus:ring-indigo-500"
              value={apiUrl}
              onChange={e => { setApiUrl(e.target.value); setApiBase(e.target.value) }}
            />
          </div>
          <div>
            <label className="block text-xs font-medium text-slate-400 mb-1">Connector Service URL</label>
            <input
              className="bg-slate-700 border border-slate-600 rounded-lg px-3 py-1.5 text-sm text-white w-64
                         focus:outline-none focus:ring-1 focus:ring-indigo-500"
              value={connectorUrl}
              onChange={e => { setConnectorUrl(e.target.value); setConnectorBase(e.target.value) }}
            />
          </div>
          <div>
            <label className="block text-xs font-medium text-slate-400 mb-1">
              Table Editor URL
              <span className="ml-1 text-slate-500 font-normal">(local: :8005 · Azure: /table-editor)</span>
            </label>
            <input
              className="bg-slate-700 border border-slate-600 rounded-lg px-3 py-1.5 text-sm text-white w-64
                         focus:outline-none focus:ring-1 focus:ring-indigo-500"
              value={tableEditorUrl}
              onChange={e => { setTableEditorUrl(e.target.value); setTableEditorBase(e.target.value) }}
            />
          </div>
          <div>
            <label className="block text-xs font-medium text-slate-400 mb-1">Doc RAG API URL</label>
            <input
              className="bg-slate-700 border border-slate-600 rounded-lg px-3 py-1.5 text-sm text-white w-64
                         focus:outline-none focus:ring-1 focus:ring-indigo-500"
              value={docRagUrl}
              onChange={e => { setDocRagUrl(e.target.value); setDocRagBase(e.target.value) }}
            />
          </div>
          <div>
            <label className="block text-xs font-medium text-slate-400 mb-1">Work Order Service URL</label>
            <input
              className="bg-slate-700 border border-slate-600 rounded-lg px-3 py-1.5 text-sm text-white w-64
                         focus:outline-none focus:ring-1 focus:ring-indigo-500"
              value={woServiceUrl}
              onChange={e => setWoServiceUrl(e.target.value)}
            />
          </div>
          <div>
            <label className="block text-xs font-medium text-slate-400 mb-1">Organisation ID</label>
            <input
              className="bg-slate-700 border border-slate-600 rounded-lg px-3 py-1.5 text-sm text-white w-80
                         focus:outline-none focus:ring-1 focus:ring-indigo-500"
              value={orgId}
              onChange={e => setOrgId(e.target.value)}
            />
          </div>
          {activeId && (
            <button
              onClick={handleReset}
              className="px-3 py-1.5 rounded-lg text-sm bg-slate-600 text-white hover:bg-slate-500 transition-colors"
            >
              {section === 'migration' ? 'New migration' : 'New schema mapping'}
            </button>
          )}
        </div>
      )}

      {/* ── Main layout ── */}
      <div className="flex flex-1 overflow-hidden">
        {/* Left sidebar */}
        {hasSidebar && (
          <aside className="w-64 bg-white border-r border-slate-200 overflow-y-auto shrink-0">
            {section === 'migration' && migration && (
              <PipelineTracker migration={migration} />
            )}
            {section === 'schema' && schemaSession && (
              <SchemaPipelineTracker session={schemaSession} />
            )}
          </aside>
        )}

        {/* Main content */}
        <main className={`flex-1 overflow-hidden flex flex-col ${section !== 'tabcustomizer' ? 'overflow-y-auto p-6' : ''}`}>
          {pollError && (
            <div className="mb-4 rounded-lg bg-red-50 border border-red-200 px-4 py-3 text-sm text-red-700">
              Poll error: {pollError}
            </div>
          )}

          {/* Migration section */}
          {section === 'migration' && (
            !migrationId ? (
              <UploadPanel orgId={orgId} onStarted={setMigrationId} />
            ) : (
              <MigrationContent
                migration={migration}
                migrationId={migrationId}
                onRefresh={migrationRefresh}
                onReset={() => setMigrationId(null)}
              />
            )
          )}

          {/* Schema mapping section */}
          {section === 'schema' && (
            !schemaSessionId ? (
              <SchemaStartPanel orgId={orgId} onStarted={setSchemaSessionId} />
            ) : (
              <SchemaContent
                session={schemaSession}
                sessionId={schemaSessionId}
                onRefresh={schemaRefresh}
                onReset={() => setSchemaSessionId(null)}
              />
            )
          )}

          {/* Doc RAG section */}
          {section === 'docrag' && <DocRagPanel />}

          {/* Table Editor section */}
          {section === 'tabcustomizer' && <TableCustomizerPanel />}

          {/* Work Orders section */}
          {section === 'workorders' && <WorkOrderPanel woServiceUrl={woServiceUrl} />}
        </main>
      </div>
    </div>
  )
}
