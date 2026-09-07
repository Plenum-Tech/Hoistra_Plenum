import { useState } from 'react'
import { submitSchemaGateHierarchy } from '../../../api'
import type { SchemaGate2Payload, SchemaFKItem } from '../../../types'
import {
  CheckCircle, XCircle, GitBranch, AlertTriangle, ChevronDown,
  ChevronUp, Key, ArrowRight, Link, RotateCcw, Layers, Network, Minus,
} from 'lucide-react'

interface Props {
  sessionId: string
  payload: SchemaGate2Payload
  onSubmitted: () => void
}

type FKDecision = 'approve' | 'reject'

export default function SchemaGateHierarchy({ sessionId, payload, onSubmitted }: Props) {
  const fks = payload.detected_foreign_keys ?? []
  const forest = payload.hierarchy_forest ?? []
  const junctions = payload.junction_tables ?? []
  const horizontals = payload.horizontal_relationships ?? []
  const isolated = payload.isolated_tables ?? []

  const [decisions, setDecisions] = useState<FKDecision[]>(
    fks.map(fk => (fk.confidence ?? 1) >= 0.7 ? 'approve' : 'reject')
  )
  const [reviewerNotes, setReviewerNotes] = useState('')
  const [forestExpanded, setForestExpanded] = useState(true)
  const [junctionExpanded, setJunctionExpanded] = useState(false)
  const [horizontalExpanded, setHorizontalExpanded] = useState(false)
  const [implicitExpanded, setImplicitExpanded] = useState(false)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  function setDecision(idx: number, val: FKDecision) {
    setDecisions(prev => prev.map((d, i) => i === idx ? val : d))
  }

  async function handleSubmit() {
    setLoading(true)
    setError(null)
    try {
      const approved: SchemaFKItem[] = []
      const rejected: SchemaFKItem[] = []
      fks.forEach((fk, idx) => {
        if (decisions[idx] === 'approve') approved.push({ ...fk, user_confirmed: true })
        else rejected.push(fk)
      })
      await submitSchemaGateHierarchy(sessionId, {
        approved_foreign_keys: approved,
        rejected_foreign_keys: rejected,
        reviewer_notes: reviewerNotes,
      })
      onSubmitted()
    } catch (err: any) {
      setError(err.message ?? 'Failed to submit hierarchy decisions')
      setLoading(false)
    }
  }

  const approvedCount = decisions.filter(d => d === 'approve').length
  const rejectedCount = decisions.filter(d => d === 'reject').length
  const lowConfCount = fks.filter(fk => (fk.confidence ?? 1) < 0.7).length
  const selfRefCount = fks.filter(fk => fk.source_table === fk.target_table).length
  const implicitKeys = Object.keys(payload.implicit_hierarchies ?? {})
  const summary = payload.summary
  const maxDepth = summary?.max_hierarchy_depth ?? summary?.hierarchy_depth ?? 0

  // Group FKs by source table
  const fksBySourceTable: Record<string, { fk: SchemaFKItem; idx: number }[]> = {}
  fks.forEach((fk, idx) => {
    const key = fk.source_table
    if (!fksBySourceTable[key]) fksBySourceTable[key] = []
    fksBySourceTable[key].push({ fk, idx })
  })

  return (
    <div className="max-w-4xl">
      {/* Header */}
      <div className="flex items-start gap-4 mb-6">
        <div className="w-10 h-10 rounded-xl bg-purple-100 flex items-center justify-center shrink-0">
          <GitBranch size={20} className="text-purple-600" />
        </div>
        <div>
          <h2 className="text-lg font-bold text-slate-900">Gate 2 — Hierarchy Verification</h2>
          <p className="text-sm text-slate-500 mt-0.5">
            Review the full schema graph: hierarchy trees, junction tables, peer relationships, and isolated tables.
            Approve correct FK links or reject incorrect ones.
          </p>
        </div>
      </div>

      {/* Stats grid */}
      <div className="grid grid-cols-4 gap-3 mb-5">
        <StatCard label="FK relationships"    value={fks.length}       color="text-indigo-600" bg="bg-indigo-50" />
        <StatCard label="Hierarchy trees"     value={forest.length}    color="text-purple-600" bg="bg-purple-50" />
        <StatCard label="Max depth"           value={maxDepth}         color="text-slate-600"  bg="bg-slate-50" />
        <StatCard label="Low confidence FKs"  value={lowConfCount}
          color={lowConfCount > 0 ? 'text-amber-600' : 'text-green-600'}
          bg={lowConfCount > 0 ? 'bg-amber-50' : 'bg-green-50'} />
      </div>
      <div className="grid grid-cols-4 gap-3 mb-5">
        <StatCard label="Junction tables"         value={junctions.length}    color="text-teal-600"   bg="bg-teal-50" />
        <StatCard label="Horizontal rels"          value={horizontals.length}  color="text-blue-600"   bg="bg-blue-50" />
        <StatCard label="Isolated tables"          value={isolated.length}     color="text-slate-500"  bg="bg-slate-50" />
        <StatCard label="Implicit hierarchies"     value={implicitKeys.length} color="text-violet-600" bg="bg-violet-50" />
      </div>

      {/* Decision summary */}
      <div className="grid grid-cols-2 gap-3 mb-5">
        <div className="rounded-lg bg-green-50 border border-green-200 px-4 py-3 text-center">
          <div className="text-xl font-bold font-mono text-green-700">{approvedCount}</div>
          <div className="text-xs text-green-600">FKs Approved</div>
        </div>
        <div className="rounded-lg bg-red-50 border border-red-200 px-4 py-3 text-center">
          <div className="text-xl font-bold font-mono text-red-700">{rejectedCount}</div>
          <div className="text-xs text-red-600">FKs Rejected</div>
        </div>
      </div>

      {/* ── HIERARCHY FOREST ──────────────────────────────────────────── */}
      {forest.length > 0 && (
        <div className="card mb-5 overflow-hidden">
          <button
            className="w-full flex items-center justify-between px-5 py-3.5 hover:bg-slate-50 transition-colors text-left"
            onClick={() => setForestExpanded(v => !v)}
          >
            <div className="flex items-center gap-2">
              <Layers size={16} className="text-purple-500" />
              <span className="text-sm font-semibold text-slate-700">Hierarchy forest</span>
              <span className="badge bg-purple-100 text-purple-700 text-xs">
                {forest.length} tree{forest.length !== 1 ? 's' : ''} · depth {maxDepth}
              </span>
            </div>
            {forestExpanded ? <ChevronUp size={16} className="text-slate-400" /> : <ChevronDown size={16} className="text-slate-400" />}
          </button>
          {forestExpanded && (
            <div className="border-t border-slate-100 px-5 py-4 overflow-auto max-h-[500px] space-y-6">
              {forest.map((root, treeIdx) => (
                <div key={treeIdx}>
                  {forest.length > 1 && (
                    <div className="text-xs text-slate-400 font-semibold mb-2 uppercase tracking-wider">
                      Tree {treeIdx + 1}
                    </div>
                  )}
                  <HierarchyTreeNode node={root} fks={fks} isRoot />
                </div>
              ))}
            </div>
          )}
        </div>
      )}

      {/* ── JUNCTION TABLES ───────────────────────────────────────────── */}
      {junctions.length > 0 && (
        <div className="card mb-5 overflow-hidden">
          <button
            className="w-full flex items-center justify-between px-5 py-3.5 hover:bg-slate-50 transition-colors text-left"
            onClick={() => setJunctionExpanded(v => !v)}
          >
            <div className="flex items-center gap-2">
              <Network size={16} className="text-teal-500" />
              <span className="text-sm font-semibold text-slate-700">Many-to-many junction tables</span>
              <span className="badge bg-teal-100 text-teal-700 text-xs">{junctions.length}</span>
            </div>
            {junctionExpanded ? <ChevronUp size={16} className="text-slate-400" /> : <ChevronDown size={16} className="text-slate-400" />}
          </button>
          {junctionExpanded && (
            <div className="border-t border-slate-100 divide-y divide-slate-100">
              {junctions.map((jt, i) => (
                <div key={i} className="px-5 py-3 flex items-center gap-3 flex-wrap">
                  <span className="badge bg-teal-100 text-teal-700 font-mono text-xs">{jt.table_name}</span>
                  <div className="flex items-center gap-1.5 text-xs text-slate-600">
                    <span className="font-mono font-semibold">{jt.left_table}</span>
                    <code className="text-slate-400">.{jt.left_fk_column}</code>
                    <ArrowRight size={10} className="text-slate-400" />
                    <span className="font-mono font-semibold">{jt.right_table}</span>
                    <code className="text-slate-400">.{jt.right_fk_column}</code>
                  </div>
                  {jt.confidence != null && <ConfidenceBadge value={jt.confidence} />}
                  {jt.reasoning && (
                    <p className="text-xs text-slate-400 w-full mt-0.5">{jt.reasoning}</p>
                  )}
                </div>
              ))}
            </div>
          )}
        </div>
      )}

      {/* ── HORIZONTAL RELATIONSHIPS ──────────────────────────────────── */}
      {horizontals.length > 0 && (
        <div className="card mb-5 overflow-hidden">
          <button
            className="w-full flex items-center justify-between px-5 py-3.5 hover:bg-slate-50 transition-colors text-left"
            onClick={() => setHorizontalExpanded(v => !v)}
          >
            <div className="flex items-center gap-2">
              <Minus size={16} className="text-blue-500" />
              <span className="text-sm font-semibold text-slate-700">Horizontal / peer relationships</span>
              <span className="badge bg-blue-100 text-blue-700 text-xs">{horizontals.length}</span>
            </div>
            {horizontalExpanded ? <ChevronUp size={16} className="text-slate-400" /> : <ChevronDown size={16} className="text-slate-400" />}
          </button>
          {horizontalExpanded && (
            <div className="border-t border-slate-100 divide-y divide-slate-100">
              {horizontals.map((hr, i) => (
                <div key={i} className="px-5 py-3 flex items-center gap-3 flex-wrap">
                  <span className={`badge text-xs uppercase font-mono ${
                    hr.relationship_type === 'SIBLING' ? 'bg-blue-100 text-blue-700' :
                    hr.relationship_type === 'MANY_TO_MANY' ? 'bg-teal-100 text-teal-700' :
                    'bg-slate-100 text-slate-600'
                  }`}>{hr.relationship_type ?? 'PEER'}</span>
                  <div className="flex items-center gap-1.5 text-xs text-slate-600">
                    <span className="font-mono font-semibold">{hr.source_table}</span>
                    <ArrowRight size={10} className="text-slate-400" />
                    <span className="font-mono font-semibold">{hr.target_table}</span>
                  </div>
                  {hr.via_table && (
                    <span className="text-xs text-slate-400">via <code className="font-mono">{hr.via_table}</code></span>
                  )}
                  {hr.shared_parent && (
                    <span className="text-xs text-slate-400">shared parent: <code className="font-mono">{hr.shared_parent}</code></span>
                  )}
                  {hr.confidence != null && <ConfidenceBadge value={hr.confidence} />}
                  {hr.reasoning && (
                    <p className="text-xs text-slate-400 w-full mt-0.5">{hr.reasoning}</p>
                  )}
                </div>
              ))}
            </div>
          )}
        </div>
      )}

      {/* ── ISOLATED TABLES ───────────────────────────────────────────── */}
      {isolated.length > 0 && (
        <div className="card mb-5 px-5 py-4">
          <div className="flex items-center gap-2 mb-3">
            <Minus size={14} className="text-slate-400" />
            <span className="text-sm font-semibold text-slate-600">
              Isolated tables
              <span className="badge bg-slate-100 text-slate-500 ml-2">{isolated.length}</span>
            </span>
          </div>
          <div className="flex flex-wrap gap-2">
            {isolated.map(t => (
              <span key={t} className="badge bg-slate-100 text-slate-600 font-mono text-xs">{t}</span>
            ))}
          </div>
        </div>
      )}

      {/* ── IMPLICIT HIERARCHIES ──────────────────────────────────────── */}
      {implicitKeys.length > 0 && (
        <div className="card mb-5 overflow-hidden">
          <button
            className="w-full flex items-center justify-between px-5 py-3.5 hover:bg-slate-50 transition-colors text-left"
            onClick={() => setImplicitExpanded(v => !v)}
          >
            <div className="flex items-center gap-2">
              <Link size={16} className="text-violet-500" />
              <span className="text-sm font-semibold text-slate-700">Implicit / code-based hierarchies</span>
              <span className="badge bg-violet-100 text-violet-700 text-xs">{implicitKeys.length}</span>
            </div>
            {implicitExpanded ? <ChevronUp size={16} className="text-slate-400" /> : <ChevronDown size={16} className="text-slate-400" />}
          </button>
          {implicitExpanded && (
            <div className="border-t border-slate-100 divide-y divide-slate-100">
              {implicitKeys.map(key => {
                const info = (payload.implicit_hierarchies ?? {})[key]
                return (
                  <div key={key} className="px-5 py-3">
                    <div className="flex items-center gap-2 mb-1">
                      <span className="badge bg-violet-100 text-violet-700 font-mono text-xs">{key}</span>
                    </div>
                    {info && typeof info === 'object' && (
                      <p className="text-xs text-slate-500 font-mono">
                        {JSON.stringify(info, null, 0).slice(0, 200)}
                      </p>
                    )}
                  </div>
                )
              })}
            </div>
          )}
        </div>
      )}

      {/* ── FK RELATIONSHIPS LIST ──────────────────────────────────────── */}
      <h3 className="text-sm font-semibold text-slate-700 mb-3 flex items-center gap-2">
        <GitBranch size={14} className="text-indigo-500" />
        Foreign key relationships — approve or reject each
        <span className="badge bg-slate-100 text-slate-600">{fks.length}</span>
      </h3>

      {fks.length === 0 ? (
        <div className="card p-6 text-center text-slate-500 text-sm mb-5">
          No foreign key relationships detected.
        </div>
      ) : (
        <div className="space-y-2 mb-5">
          {Object.entries(fksBySourceTable).map(([sourceTable, entries]) => (
            <SourceTableGroup
              key={sourceTable}
              sourceTable={sourceTable}
              entries={entries}
              decisions={decisions}
              onSetDecision={setDecision}
            />
          ))}
        </div>
      )}

      {/* Reviewer notes */}
      <div className="mb-5">
        <label className="block text-sm font-medium text-slate-700 mb-1.5">
          Reviewer notes <span className="text-slate-400 font-normal">(optional)</span>
        </label>
        <textarea
          className="input w-full resize-none"
          rows={2}
          placeholder="Any corrections or comments about the detected hierarchy…"
          value={reviewerNotes}
          onChange={e => setReviewerNotes(e.target.value)}
        />
      </div>

      {error && (
        <div className="mb-4 rounded-lg bg-red-50 border border-red-200 px-4 py-3 text-sm text-red-700">
          {error}
        </div>
      )}

      <button
        className="btn-primary px-8 py-3 text-base"
        onClick={handleSubmit}
        disabled={loading}
      >
        {loading ? (
          <>
            <span className="animate-spin inline-block w-4 h-4 border-2 border-white border-t-transparent rounded-full" />
            Submitting…
          </>
        ) : (
          <>
            <CheckCircle size={18} />
            Confirm hierarchy ({approvedCount} approved, {rejectedCount} rejected)
          </>
        )}
      </button>
    </div>
  )
}

// ── Source-table grouped FK card ──────────────────────────────────────────────
function SourceTableGroup({
  sourceTable, entries, decisions, onSetDecision,
}: {
  sourceTable: string
  entries: { fk: SchemaFKItem; idx: number }[]
  decisions: FKDecision[]
  onSetDecision: (idx: number, val: FKDecision) => void
}) {
  const [open, setOpen] = useState(true)
  const approvedCount = entries.filter(e => decisions[e.idx] === 'approve').length

  return (
    <div className="card overflow-hidden">
      <button
        className="w-full flex items-center justify-between px-5 py-3 hover:bg-slate-50 transition-colors text-left"
        onClick={() => setOpen(v => !v)}
      >
        <div className="flex items-center gap-3">
          <span className="font-semibold text-slate-800 text-sm font-mono">{sourceTable}</span>
          <span className="badge bg-slate-100 text-slate-600">{entries.length} FK{entries.length > 1 ? 's' : ''}</span>
          {approvedCount === entries.length && (
            <span className="badge bg-green-100 text-green-700">All approved</span>
          )}
        </div>
        {open ? <ChevronUp size={15} className="text-slate-400" /> : <ChevronDown size={15} className="text-slate-400" />}
      </button>

      {open && (
        <div className="border-t border-slate-100 divide-y divide-slate-100">
          {entries.map(({ fk, idx }) => {
            const dec = decisions[idx] ?? 'approve'
            const conf = fk.confidence ?? 1
            const isSelfRef = fk.source_table === fk.target_table
            const isLowConf = conf < 0.7

            return (
              <div
                key={idx}
                className={`px-5 py-3.5 transition-colors ${
                  dec === 'approve' ? 'bg-green-50/30' : 'bg-red-50/30'
                } ${isSelfRef ? 'border-l-2 border-violet-400' : ''} ${isLowConf ? 'border-l-2 border-amber-400' : ''}`}
              >
                <div className="flex items-start gap-4">
                  <div className="flex-1 min-w-0">
                    {/* Relationship line */}
                    <div className="flex items-center gap-2 flex-wrap mb-1.5">
                      <RelTypeBadge type={fk.relationship_type} isSelfRef={isSelfRef} />
                      <code className="text-xs font-mono font-semibold text-slate-700 bg-slate-100 px-1.5 py-0.5 rounded">
                        {fk.source_column}
                      </code>
                      <ArrowRight size={12} className="text-slate-400 shrink-0" />
                      <span className="font-mono text-xs font-semibold text-indigo-700">
                        {fk.target_table}
                      </span>
                      {fk.target_column && (
                        <>
                          <span className="text-slate-300 text-xs">.</span>
                          <code className="text-xs font-mono text-indigo-600 bg-indigo-50 px-1.5 py-0.5 rounded">
                            {fk.target_column}
                          </code>
                        </>
                      )}
                      <ConfidenceBadge value={conf} />
                    </div>

                    {/* Reasoning */}
                    {fk.reasoning && (
                      <p className="text-xs text-slate-400 mb-1.5">{fk.reasoning}</p>
                    )}

                    {/* Warnings */}
                    {isSelfRef && (
                      <div className="flex items-center gap-1.5 text-xs text-violet-700 bg-violet-50 px-3 py-1.5 rounded-lg mb-1 w-fit">
                        <RotateCcw size={11} />
                        Self-referential — parent/child within same table
                      </div>
                    )}
                    {isLowConf && !isSelfRef && (
                      <div className="flex items-center gap-1.5 text-xs text-amber-700 bg-amber-50 px-3 py-1.5 rounded-lg mb-1 w-fit">
                        <AlertTriangle size={11} />
                        Low confidence — review carefully
                      </div>
                    )}
                    {dec === 'reject' && (
                      <p className="text-xs text-red-500">FK will be excluded from schema output</p>
                    )}
                  </div>

                  <div className="flex gap-1.5 shrink-0">
                    <ActionBtn
                      label="Approve"
                      icon={<CheckCircle size={11} />}
                      active={dec === 'approve'}
                      activeColor="bg-green-600"
                      onClick={() => onSetDecision(idx, 'approve')}
                    />
                    <ActionBtn
                      label="Reject"
                      icon={<XCircle size={11} />}
                      active={dec === 'reject'}
                      activeColor="bg-red-600"
                      onClick={() => onSetDecision(idx, 'reject')}
                    />
                  </div>
                </div>
              </div>
            )
          })}
        </div>
      )}
    </div>
  )
}

// ── Visual hierarchy tree ─────────────────────────────────────────────────────
function HierarchyTreeNode({
  node, fks, isRoot = false, depth = 0,
}: {
  node: any
  fks: SchemaFKItem[]
  isRoot?: boolean
  depth?: number
}) {
  const [collapsed, setCollapsed] = useState(false)
  if (!node || !node.table_name) return null

  const children: any[] = node.children ?? []
  const hasChildren = children.length > 0

  // Find the FK column that connects this node to its parent
  // (the column in this node's table that references the parent)
  const parentFKs = fks.filter(
    fk => fk.source_table === node.table_name
  )

  return (
    <div className={depth > 0 ? 'ml-6 mt-2' : ''}>
      <div className="flex items-start gap-0">
        {/* Vertical + horizontal connector lines */}
        {depth > 0 && (
          <div className="flex flex-col items-center mr-2 shrink-0 mt-1">
            <div className="w-4 h-px bg-slate-300" />
          </div>
        )}

        <div className="flex-1 min-w-0">
          {/* Table node box */}
          <div
            className={`inline-flex items-start gap-2 rounded-lg border px-3 py-2 cursor-pointer select-none transition-colors
              ${isRoot
                ? 'bg-indigo-50 border-indigo-300 hover:bg-indigo-100'
                : depth === 1
                ? 'bg-purple-50 border-purple-200 hover:bg-purple-100'
                : 'bg-slate-50 border-slate-200 hover:bg-slate-100'
              }`}
            onClick={() => hasChildren && setCollapsed(v => !v)}
          >
            <div>
              <div className="flex items-center gap-1.5">
                {isRoot && <span className="text-indigo-400 text-xs">◉</span>}
                {!isRoot && <span className="text-slate-400 text-xs">◆</span>}
                <span className={`font-mono text-xs font-bold ${isRoot ? 'text-indigo-700' : 'text-slate-700'}`}>
                  {node.table_name}
                </span>
                {hasChildren && (
                  <span className="text-slate-400">
                    {collapsed ? <ChevronDown size={11} /> : <ChevronUp size={11} />}
                  </span>
                )}
              </div>
              {node.primary_key && (
                <div className="flex items-center gap-1 mt-0.5">
                  <Key size={9} className="text-amber-500 shrink-0" />
                  <span className="text-xs font-mono text-amber-600">{node.primary_key}</span>
                </div>
              )}
              {hasChildren && (
                <div className="text-xs text-slate-400 mt-0.5">
                  {node.children_count ?? children.length} child table{(node.children_count ?? children.length) !== 1 ? 's' : ''}
                </div>
              )}
            </div>
          </div>

          {/* Children */}
          {hasChildren && !collapsed && (
            <div className="mt-1 ml-2 border-l-2 border-slate-200 pl-2">
              {children.map((child: any, i: number) => {
                // Find FK connecting this child to its parent (node.table_name)
                const connectingFK = fks.find(
                  fk => fk.source_table === child.table_name && fk.target_table === node.table_name
                )
                return (
                  <div key={i} className="mt-2">
                    {/* FK edge annotation */}
                    {connectingFK && (
                      <div className="flex items-center gap-1.5 mb-1 ml-5">
                        <div className="w-3 h-px bg-slate-300" />
                        <div className="flex items-center gap-1 bg-white border border-slate-200 rounded px-1.5 py-0.5">
                          <code className="text-xs font-mono text-slate-500">
                            {connectingFK.source_column}
                          </code>
                          <ArrowRight size={9} className="text-slate-400" />
                          <code className="text-xs font-mono text-indigo-600">
                            {connectingFK.target_column || connectingFK.target_table}
                          </code>
                          {connectingFK.relationship_type && (
                            <RelTypeBadge type={connectingFK.relationship_type} isSelfRef={false} tiny />
                          )}
                        </div>
                      </div>
                    )}
                    <HierarchyTreeNode node={child} fks={fks} depth={depth + 1} />
                  </div>
                )
              })}
            </div>
          )}
        </div>
      </div>
    </div>
  )
}

// ── Helpers ───────────────────────────────────────────────────────────────────
function StatCard({ label, value, color, bg }: { label: string; value: number; color: string; bg: string }) {
  return (
    <div className={`card p-3 ${bg}`}>
      <div className={`text-xl font-bold font-mono ${color}`}>{value}</div>
      <div className="text-xs text-slate-500 mt-0.5">{label}</div>
    </div>
  )
}

function RelTypeBadge({ type, isSelfRef, tiny }: { type?: string; isSelfRef: boolean; tiny?: boolean }) {
  if (isSelfRef) {
    return (
      <span className={`badge ${tiny ? 'text-[9px] px-1 py-0' : 'text-xs'} bg-violet-100 text-violet-700 uppercase font-mono`}>
        SELF-REF
      </span>
    )
  }
  if (!type) return null
  const map: Record<string, string> = {
    CONTAINMENT: 'bg-indigo-100 text-indigo-700',
    REFERENCE:   'bg-slate-100 text-slate-600',
    OWNERSHIP:   'bg-purple-100 text-purple-700',
    PART_OF:     'bg-blue-100 text-blue-700',
  }
  return (
    <span className={`badge ${tiny ? 'text-[9px] px-1 py-0' : 'text-xs'} uppercase font-mono ${map[type] ?? 'bg-slate-100 text-slate-600'}`}>
      {type}
    </span>
  )
}

function ConfidenceBadge({ value }: { value: number }) {
  const pct = Math.round(value * 100)
  const color = pct >= 90 ? 'bg-green-100 text-green-700' : pct >= 70 ? 'bg-amber-100 text-amber-700' : 'bg-red-100 text-red-700'
  return <span className={`badge text-xs font-mono ${color}`}>{pct}%</span>
}

function ActionBtn({ label, icon, active, activeColor, onClick }: {
  label: string; icon: React.ReactNode; active: boolean; activeColor: string; onClick: () => void
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={`flex items-center gap-1 text-xs font-medium px-2.5 py-1.5 rounded-lg transition-colors ${
        active ? `${activeColor} text-white` : 'bg-slate-100 text-slate-600 hover:bg-slate-200'
      }`}
    >
      {icon}{label}
    </button>
  )
}
