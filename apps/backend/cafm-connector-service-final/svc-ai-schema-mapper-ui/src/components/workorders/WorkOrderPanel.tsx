import { useState, useEffect, useCallback, useRef } from 'react'
import {
  Mail, Wrench, Zap, CheckCircle, Lock, AlertTriangle, Shield,
  FileText, MapPin, Cpu, Key, Package, Archive, Building2, User,
  Calendar, MapPinned, Route, RefreshCw, Wifi, WifiOff, Clock,
  ChevronDown, ChevronRight, Database, Inbox, Settings2, Activity,
  TrendingUp, BarChart3, ClipboardList, CheckSquare, X, Send,
  Search, BrainCircuit, CircleDot, UserCheck, ThumbsUp, ThumbsDown,
  GitBranch, MessageSquare, Bell, Bot, RotateCcw, Plus,
} from 'lucide-react'

// ── Types ────────────────────────────────────────────────────────────────────

interface DashboardStats {
  total_work_orders: number
  by_status: Record<string, number>
  by_priority: Record<string, number>
  by_source: Record<string, number>
}

interface WorkOrder {
  work_order_id: string
  status: string
  priority: string
  asset: string
  location: string
  issue_description: string
  source: string
  requester_name: string
  requester_email?: string
  vendor?: string
  scheduled_date?: string
  created_at: string
}

interface EmailStatus {
  connected: boolean
  display_name?: string
  email?: string
}

interface JourneyAnalytics {
  total_journeys?: number
  in_progress_journeys?: number
  completed_journeys?: number
  failed_journeys?: number
  avg_completion_percentage?: number
}

// ── Chat Types ────────────────────────────────────────────────────────────────

interface ChatMessage {
  role: 'user' | 'assistant'
  content: string
  work_order?: Record<string, unknown> | null
  ts: number
}

// ── Flow Step Types ────────────────────────────────────────────────────────────

type StepStatus = 'idle' | 'running' | 'complete' | 'warning' | 'error'

interface StepState {
  status: StepStatus
  message: string
  elapsed_ms?: number
  data?: Record<string, unknown>
}

const FLOW_STEPS = [
  { key: 'email_received',      label: 'Email Received',              Icon: Mail,          color: 'text-sky-500',     desc: 'Ingested from Outlook inbox' },
  { key: 'classification',      label: 'AI: Classify Email',          Icon: BrainCircuit,  color: 'text-violet-500',  desc: 'GPT-4o-mini: is this a maintenance request?' },
  { key: 'parsing',             label: 'AI: Extract Fields',          Icon: Search,        color: 'text-blue-500',    desc: 'Parse asset, location, priority, requester' },
  { key: 'db_lookup',           label: 'DB: Resolve References',      Icon: Database,      color: 'text-emerald-500', desc: 'Match extracted values to database records' },
  { key: 'ai_assessment',       label: 'AI: 13-Block Assessment',     Icon: Cpu,           color: 'text-indigo-500',  desc: 'Criticality · Safety · Compliance · Parts · Vendors · Schedule…' },
  { key: 'wo_create',           label: 'Create Work Order',           Icon: ClipboardList, color: 'text-teal-500',    desc: 'Persist to plenum_cafm.work_orders' },
  { key: 'journey_log',         label: 'Initialize Journey Log',      Icon: Route,         color: 'text-cyan-500',    desc: 'Status tracking and milestone initialization' },
  { key: 'notification',        label: 'Notify Requester',            Icon: Send,          color: 'text-pink-500',    desc: 'Confirmation email sent via Outlook Graph API' },
  { key: 'approval_request',    label: 'Approval Request Sent',       Icon: UserCheck,     color: 'text-amber-500',   desc: 'Routed to Facility Manager by asset category — awaiting reply' },
  { key: 'waiting_approval',    label: 'Waiting for Manager Reply',   Icon: Clock,         color: 'text-amber-400',   desc: 'Background poller checks inbox every 60s — stream stays live' },
  { key: 'technician_assigned', label: 'Technician Assigned',         Icon: Wrench,        color: 'text-blue-500',    desc: 'Best-matched technician selected from database' },
  { key: 'notifications_sent',  label: 'Outcome Notifications Sent',  Icon: Bell,          color: 'text-violet-500',  desc: 'Approval confirmation → requester · Assignment → technician' },
] as const

function StepStatusIcon({ status }: { status: StepStatus }) {
  if (status === 'running') {
    return <div className="w-6 h-6 rounded-full border-2 border-blue-400 border-t-transparent animate-spin shrink-0" />
  }
  if (status === 'complete') {
    return (
      <div className="w-6 h-6 rounded-full bg-emerald-100 flex items-center justify-center shrink-0">
        <CheckCircle size={14} className="text-emerald-600" />
      </div>
    )
  }
  if (status === 'warning') {
    return (
      <div className="w-6 h-6 rounded-full bg-amber-100 flex items-center justify-center shrink-0">
        <AlertTriangle size={13} className="text-amber-600" />
      </div>
    )
  }
  if (status === 'error') {
    return (
      <div className="w-6 h-6 rounded-full bg-red-100 flex items-center justify-center shrink-0">
        <X size={13} className="text-red-600" />
      </div>
    )
  }
  return (
    <div className="w-6 h-6 rounded-full border-2 border-slate-200 flex items-center justify-center shrink-0">
      <CircleDot size={12} className="text-slate-300" />
    </div>
  )
}

// ── Constants ─────────────────────────────────────────────────────────────────

const LIFECYCLE_STAGES = [
  {
    key: 'pending_approval', label: 'Pending Approval', step: 1,
    Icon: Clock,
    bg: 'bg-amber-500', lightBg: 'bg-amber-50', text: 'text-amber-700',
    border: 'border-amber-300', iconBg: 'bg-amber-100', iconText: 'text-amber-600',
    ring: 'ring-amber-400',
  },
  {
    key: 'preparing', label: 'Preparing', step: 2,
    Icon: Wrench,
    bg: 'bg-blue-500', lightBg: 'bg-blue-50', text: 'text-blue-700',
    border: 'border-blue-300', iconBg: 'bg-blue-100', iconText: 'text-blue-600',
    ring: 'ring-blue-400',
  },
  {
    key: 'prepared', label: 'Prepared', step: 3,
    Icon: CheckSquare,
    bg: 'bg-cyan-500', lightBg: 'bg-cyan-50', text: 'text-cyan-700',
    border: 'border-cyan-300', iconBg: 'bg-cyan-100', iconText: 'text-cyan-600',
    ring: 'ring-cyan-400',
  },
  {
    key: 'active', label: 'Active', step: 4,
    Icon: Zap,
    bg: 'bg-emerald-500', lightBg: 'bg-emerald-50', text: 'text-emerald-700',
    border: 'border-emerald-300', iconBg: 'bg-emerald-100', iconText: 'text-emerald-600',
    ring: 'ring-emerald-400',
  },
  {
    key: 'completed', label: 'Completed', step: 5,
    Icon: CheckCircle,
    bg: 'bg-teal-500', lightBg: 'bg-teal-50', text: 'text-teal-700',
    border: 'border-teal-300', iconBg: 'bg-teal-100', iconText: 'text-teal-600',
    ring: 'ring-teal-400',
  },
  {
    key: 'closed', label: 'Closed', step: 6,
    Icon: Lock,
    bg: 'bg-slate-500', lightBg: 'bg-slate-100', text: 'text-slate-600',
    border: 'border-slate-300', iconBg: 'bg-slate-200', iconText: 'text-slate-500',
    ring: 'ring-slate-400',
  },
]

const SOURCES = [
  { key: 'email',  label: 'Outlook Email', Icon: Mail,      color: 'text-sky-400',     bg: 'bg-sky-900/30',     dot: 'bg-sky-400'     },
  { key: 'manual', label: 'Manual Entry',  Icon: Settings2, color: 'text-violet-400',  bg: 'bg-violet-900/30',  dot: 'bg-violet-400'  },
  { key: 'ppm',    label: 'PPM Scheduler', Icon: Activity,  color: 'text-emerald-400', bg: 'bg-emerald-900/30', dot: 'bg-emerald-400' },
]

const AI_BLOCKS = [
  { key: 'criticality',        label: 'Criticality',    Icon: AlertTriangle, color: 'text-red-400'    },
  { key: 'safety',             label: 'Safety',         Icon: Shield,        color: 'text-orange-400' },
  { key: 'compliance',         label: 'Compliance',     Icon: FileText,      color: 'text-blue-400'   },
  { key: 'location',           label: 'Location',       Icon: MapPin,        color: 'text-green-400'  },
  { key: 'asset_intelligence', label: 'Asset Intel',    Icon: Cpu,           color: 'text-purple-400' },
  { key: 'site_clearance',     label: 'Site Clearance', Icon: Key,           color: 'text-yellow-400' },
  { key: 'parts_list',         label: 'Parts List',     Icon: Package,       color: 'text-indigo-400' },
  { key: 'inventory',          label: 'Inventory',      Icon: Archive,       color: 'text-teal-400'   },
  { key: 'vendors',            label: 'Vendors',        Icon: Building2,     color: 'text-cyan-400'   },
  { key: 'technician',         label: 'Technician',     Icon: User,          color: 'text-pink-400'   },
  { key: 'schedule',           label: 'Schedule',       Icon: Calendar,      color: 'text-lime-400'   },
  { key: 'workspace_pin',      label: 'Workspace',      Icon: MapPinned,     color: 'text-rose-400'   },
  { key: 'journey',            label: 'Journey Log',    Icon: Route,         color: 'text-violet-400' },
]

const PRIORITY_BADGE: Record<string, string> = {
  critical: 'bg-red-100 text-red-700',
  urgent:   'bg-orange-100 text-orange-700',
  high:     'bg-amber-100 text-amber-700',
  medium:   'bg-blue-100 text-blue-700',
  low:      'bg-slate-100 text-slate-600',
}

const PRIORITY_BAR: Record<string, string> = {
  critical: 'bg-red-500',
  urgent:   'bg-orange-500',
  high:     'bg-amber-500',
  medium:   'bg-blue-500',
  low:      'bg-slate-400',
}

const STATUS_BADGE: Record<string, string> = {
  pending_approval: 'bg-amber-100 text-amber-700',
  preparing:        'bg-blue-100 text-blue-700',
  prepared:         'bg-cyan-100 text-cyan-700',
  active:           'bg-emerald-100 text-emerald-700',
  completed:        'bg-teal-100 text-teal-700',
  closed:           'bg-slate-100 text-slate-600',
  cancelled:        'bg-red-100 text-red-700',
}

// ── Sub-components ────────────────────────────────────────────────────────────

function FlowConnector({ delay = 0 }: { delay?: number }) {
  return (
    <div className="flex items-center gap-1 flex-1 min-w-0">
      <div className="relative flex-1 h-0.5 overflow-hidden rounded-full">
        <div className="absolute inset-0 bg-slate-700" />
        <div className="wo-flow-shimmer absolute top-0 left-0 h-full w-1/2" style={{ animationDelay: `${delay}s` }} />
      </div>
      <ChevronRight size={10} className="text-slate-600 shrink-0" />
    </div>
  )
}

function StageCard({
  stage, count, isActive, onClick,
}: {
  stage: typeof LIFECYCLE_STAGES[0]
  count: number
  isActive: boolean
  onClick: () => void
}) {
  return (
    <button
      onClick={onClick}
      className={`flex-1 flex flex-col items-center gap-2 p-3 rounded-xl border-2 transition-all duration-200 ${
        isActive
          ? `${stage.lightBg} ${stage.border} shadow-md`
          : 'bg-slate-800/80 border-slate-700/60 hover:border-slate-500'
      }`}
    >
      <div className={`w-9 h-9 rounded-xl flex items-center justify-center ${isActive ? stage.bg : 'bg-slate-700'}`}>
        <stage.Icon size={15} className={isActive ? 'text-white' : 'text-slate-400'} />
      </div>
      <span className={`text-2xl font-bold tabular-nums leading-none ${isActive ? stage.text : 'text-white'}`}>
        {count}
      </span>
      <span className={`text-xs text-center leading-tight font-medium ${isActive ? stage.text : 'text-slate-400'}`}>
        {stage.label}
      </span>
      <span className={`text-xs w-5 h-5 rounded-full flex items-center justify-center font-semibold
        ${isActive ? `${stage.bg} text-white` : 'bg-slate-700 text-slate-500'}`}>
        {stage.step}
      </span>
    </button>
  )
}

// ── Chat sub-components ───────────────────────────────────────────────────────

function WOCreatedCard({ wo }: { wo: Record<string, unknown> }) {
  const pBadge = PRIORITY_BADGE[(wo.priority as string) ?? ''] ?? 'bg-slate-100 text-slate-600'
  return (
    <div className="mt-2 rounded-xl border border-emerald-200 bg-emerald-50 p-3.5 text-xs space-y-1.5">
      <div className="flex items-center gap-2 text-emerald-700 font-semibold text-sm">
        <CheckCircle size={14} className="text-emerald-600 shrink-0" />
        Work Order Created
      </div>
      {wo.work_order_id != null && (
        <p className="font-mono text-slate-600">{String(wo.work_order_id)}</p>
      )}
      <div className="flex flex-wrap gap-2">
        {wo.priority != null && (
          <span className={`badge capitalize ${pBadge}`}>{String(wo.priority)}</span>
        )}
        {wo.status != null && (
          <span className="badge bg-blue-100 text-blue-700">{String(wo.status).replace(/_/g, ' ')}</span>
        )}
        {wo.source != null && (
          <span className="badge bg-slate-100 text-slate-600 capitalize">{String(wo.source)}</span>
        )}
      </div>
      {wo.issue_description != null && (
        <p className="text-slate-700 leading-snug">{String(wo.issue_description)}</p>
      )}
      <div className="flex flex-wrap gap-3 text-slate-500 mt-1">
        {wo.asset    != null && <span><span className="font-medium text-slate-600">Asset:</span> {String(wo.asset)}</span>}
        {wo.location != null && <span><span className="font-medium text-slate-600">Location:</span> {String(wo.location)}</span>}
        {wo.vendor   != null && <span><span className="font-medium text-slate-600">Vendor:</span> {String(wo.vendor)}</span>}
        {wo.scheduled_date != null && (
          <span><span className="font-medium text-slate-600">Scheduled:</span> {String(wo.scheduled_date)}</span>
        )}
      </div>
    </div>
  )
}

function TypingIndicator() {
  return (
    <div className="flex items-end gap-2.5 max-w-[82%]">
      <div className="w-7 h-7 rounded-full bg-indigo-100 flex items-center justify-center shrink-0">
        <Bot size={13} className="text-indigo-600" />
      </div>
      <div className="rounded-2xl rounded-bl-sm bg-white border border-slate-200 px-4 py-3 shadow-sm">
        <div className="flex items-center gap-1">
          {[0, 0.2, 0.4].map((d, i) => (
            <span
              key={i}
              className="w-1.5 h-1.5 rounded-full bg-indigo-400 animate-bounce"
              style={{ animationDelay: `${d}s`, animationDuration: '1s' }}
            />
          ))}
        </div>
      </div>
    </div>
  )
}

function ChatBubble({ msg }: { msg: ChatMessage }) {
  const isUser = msg.role === 'user'
  const time   = new Date(msg.ts).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })

  if (isUser) {
    return (
      <div className="flex justify-end">
        <div className="max-w-[78%]">
          <div className="rounded-2xl rounded-br-sm bg-indigo-600 text-white px-4 py-2.5 text-sm leading-relaxed shadow-sm">
            {msg.content}
          </div>
          <p className="text-right text-xs text-slate-400 mt-1 mr-1">{time}</p>
        </div>
      </div>
    )
  }

  return (
    <div className="flex items-end gap-2.5 max-w-[82%]">
      <div className="w-7 h-7 rounded-full bg-indigo-100 flex items-center justify-center shrink-0">
        <Bot size={13} className="text-indigo-600" />
      </div>
      <div className="min-w-0">
        <div className="rounded-2xl rounded-bl-sm bg-white border border-slate-200 px-4 py-2.5 text-sm text-slate-800 leading-relaxed shadow-sm whitespace-pre-wrap">
          {msg.content}
        </div>
        {msg.work_order && <WOCreatedCard wo={msg.work_order} />}
        <p className="text-xs text-slate-400 mt-1 ml-1">{time}</p>
      </div>
    </div>
  )
}

function ChatPanel({ base, fallbackBase }: { base: string; fallbackBase: string | null }) {
  const [messages,   setMessages]   = useState<ChatMessage[]>([])
  const [sessionId,  setSessionId]  = useState<string | null>(null)
  const [input,      setInput]      = useState('')
  const [loading,    setLoading]    = useState(false)
  const [error,      setError]      = useState<string | null>(null)
  const bottomRef = useRef<HTMLDivElement>(null)
  const inputRef  = useRef<HTMLTextAreaElement>(null)

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages, loading])

  async function sendMessage() {
    const text = input.trim()
    if (!text || loading) return

    const userMsg: ChatMessage = { role: 'user', content: text, ts: Date.now() }
    setMessages(prev => [...prev, userMsg])
    setInput('')
    setLoading(true)
    setError(null)

    try {
      const body = JSON.stringify({ message: text, session_id: sessionId })
      const headers = { 'Content-Type': 'application/json' }
      let res: Response
      try {
        res = await fetch(`${base}/api/chat/`, { method: 'POST', headers, body })
      } catch (primaryErr) {
        if (!fallbackBase) throw primaryErr
        res = await fetch(`${fallbackBase}/api/chat/`, { method: 'POST', headers, body })
      }
      const data = await res.json()
      if (!res.ok) {
        const detail = data?.errors?.[0]?.message ?? data?.detail ?? `HTTP ${res.status}`
        throw new Error(String(detail))
      }
      if (data.session_id) setSessionId(data.session_id)
      const assistantMsg: ChatMessage = {
        role: 'assistant',
        content: data.reply ?? '',
        work_order: data.work_order ?? null,
        ts: Date.now(),
      }
      setMessages(prev => [...prev, assistantMsg])
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
      setMessages(prev => prev.slice(0, -1))
      setInput(text)
    } finally {
      setLoading(false)
      setTimeout(() => inputRef.current?.focus(), 50)
    }
  }

  function handleKeyDown(e: React.KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      sendMessage()
    }
  }

  function startNew() {
    setMessages([])
    setSessionId(null)
    setError(null)
    setInput('')
    setTimeout(() => inputRef.current?.focus(), 50)
  }

  const SUGGESTIONS = [
    'The AC unit in Meeting Room 3 is making a loud noise',
    'Water leak reported in Basement Level 2 near the pump room',
    'Electrical fault — lights flickering in the lobby',
    'Schedule quarterly PM for all HVAC units on Floor 4',
  ]

  return (
    <div className="flex flex-col h-full min-h-0">
      {/* Header */}
      <div className="flex items-center justify-between px-5 py-3.5 border-b border-slate-200 bg-white shrink-0">
        <div className="flex items-center gap-3">
          <div className="w-8 h-8 rounded-xl bg-indigo-600 flex items-center justify-center">
            <Bot size={15} className="text-white" />
          </div>
          <div>
            <p className="text-sm font-semibold text-slate-900">WO Assistant</p>
            <p className="text-xs text-slate-400">
              {sessionId
                ? <span>Session <span className="font-mono">{sessionId.slice(0, 8)}…</span></span>
                : 'Start a conversation to create a work order'}
            </p>
          </div>
        </div>
        <div className="flex items-center gap-2">
          {sessionId && (
            <button
              onClick={startNew}
              className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium bg-slate-100 hover:bg-slate-200 text-slate-600 transition-colors"
            >
              <Plus size={12} />
              New conversation
            </button>
          )}
        </div>
      </div>

      {/* Messages area */}
      <div className="flex-1 overflow-y-auto px-5 py-5 space-y-4 bg-slate-50 min-h-0">
        {messages.length === 0 && (
          <div className="flex flex-col items-center justify-center h-full py-10 text-center">
            <div className="w-14 h-14 rounded-2xl bg-indigo-100 flex items-center justify-center mb-4">
              <Bot size={24} className="text-indigo-600" />
            </div>
            <h3 className="text-slate-800 font-semibold text-base mb-1">Work Order Assistant</h3>
            <p className="text-slate-500 text-sm max-w-xs leading-relaxed mb-6">
              Describe an issue, request a PPM, or ask anything about facilities maintenance.
              I'll gather the details and create a work order for you.
            </p>
            <div className="space-y-2 w-full max-w-sm">
              {SUGGESTIONS.map(s => (
                <button
                  key={s}
                  onClick={() => { setInput(s); setTimeout(() => inputRef.current?.focus(), 50) }}
                  className="w-full text-left px-4 py-2.5 rounded-xl border border-slate-200 bg-white hover:border-indigo-300 hover:bg-indigo-50 text-sm text-slate-700 transition-colors shadow-sm"
                >
                  {s}
                </button>
              ))}
            </div>
          </div>
        )}

        {messages.map((msg, i) => (
          <ChatBubble key={i} msg={msg} />
        ))}

        {loading && <TypingIndicator />}

        <div ref={bottomRef} />
      </div>

      {/* Error banner */}
      {error && (
        <div className="mx-5 mb-2 rounded-lg bg-red-50 border border-red-200 px-3 py-2 text-xs text-red-700 flex items-center justify-between shrink-0">
          <span>⚠ {error}</span>
          <button onClick={() => setError(null)} className="shrink-0 ml-2 text-red-400 hover:text-red-600"><X size={12} /></button>
        </div>
      )}

      {/* Input bar */}
      <div className="border-t border-slate-200 px-4 py-3 bg-white shrink-0">
        <div className="flex items-end gap-2">
          <textarea
            ref={inputRef}
            value={input}
            onChange={e => setInput(e.target.value)}
            onKeyDown={handleKeyDown}
            placeholder="Describe the issue or request… (Enter to send, Shift+Enter for new line)"
            rows={1}
            disabled={loading}
            className="flex-1 resize-none rounded-xl border border-slate-300 px-4 py-2.5 text-sm text-slate-900
                       placeholder-slate-400 focus:outline-none focus:ring-2 focus:ring-indigo-500 focus:border-transparent
                       disabled:opacity-50 min-h-[42px] max-h-32 overflow-y-auto leading-relaxed"
            style={{ fieldSizing: 'content' } as React.CSSProperties}
          />
          <button
            onClick={sendMessage}
            disabled={!input.trim() || loading}
            className="flex items-center justify-center w-10 h-10 rounded-xl bg-indigo-600 hover:bg-indigo-700
                       disabled:opacity-40 disabled:cursor-not-allowed text-white transition-colors shrink-0"
            title="Send message (Enter)"
          >
            <Send size={15} />
          </button>
        </div>
        <p className="text-xs text-slate-400 mt-1.5 ml-1">
          Powered by GPT-4o · Works for chat, email intake, and PPM triggers
        </p>
      </div>
    </div>
  )
}

// ── Main Component ─────────────────────────────────────────────────────────────

interface WorkOrderPanelProps {
  woServiceUrl: string
}

export default function WorkOrderPanel({ woServiceUrl }: WorkOrderPanelProps) {
  const base = woServiceUrl.trim().replace(/\/+$/, '')
  const fallbackBase = base.includes('127.0.0.1')
    ? base.replace('127.0.0.1', 'localhost')
    : (base.includes('localhost') ? base.replace('localhost', '127.0.0.1') : null)

  const [activeTab,    setActiveTab]    = useState<'overview' | 'chat'>('overview')
  const [stats,        setStats]        = useState<DashboardStats | null>(null)
  const [workOrders,   setWorkOrders]   = useState<WorkOrder[]>([])
  const [emailStatus,  setEmailStatus]  = useState<EmailStatus | null>(null)
  const [journeyStats, setJourneyStats] = useState<JourneyAnalytics | null>(null)
  const [loading,      setLoading]      = useState(true)
  const [error,        setError]        = useState<string | null>(null)
  const [lastRefresh,  setLastRefresh]  = useState<Date | null>(null)
  const [selectedWO,   setSelectedWO]   = useState<string | null>(null)
  const [activeFilter, setActiveFilter] = useState<string | null>(null)
  const [aiExpanded,   setAiExpanded]   = useState(true)
  const [selectedAiBlock, setSelectedAiBlock] = useState<string | null>(null)

  const [sampling,       setSampling]       = useState(false)
  const [polling,        setPolling]        = useState(false)
  const [emailResultErr, setEmailResultErr] = useState<string | null>(null)

  const [flowVisible, setFlowVisible] = useState(false)
  const [flowSteps,   setFlowSteps]   = useState<Record<string, StepState>>({})
  const [flowResult,  setFlowResult]  = useState<Record<string, unknown> | null>(null)
  const [pollResult,  setPollResult]  = useState<Record<string, unknown> | null>(null)
  const readerRef = useRef<ReadableStreamDefaultReader<Uint8Array> | null>(null)

  async function processSample(streamPath: string = '/api/email/process/sample/stream') {
    setSampling(true)
    setFlowVisible(true)
    setFlowSteps({})
    setFlowResult(null)
    setPollResult(null)
    setEmailResultErr(null)

    try {
      const streamUrl = `${base}${streamPath}`
      let res: Response
      try {
        res = await fetch(streamUrl, { method: 'POST', headers: { Accept: 'text/event-stream' } })
      } catch (primaryErr) {
        if (!fallbackBase) throw primaryErr
        res = await fetch(`${fallbackBase}${streamPath}`, { method: 'POST', headers: { Accept: 'text/event-stream' } })
      }
      if (!res.ok || !res.body) throw new Error(`HTTP ${res.status}`)

      const reader = res.body.getReader()
      readerRef.current = reader
      const decoder = new TextDecoder()
      let buffer = ''

      while (true) {
        const { done, value } = await reader.read()
        if (done) break
        buffer += decoder.decode(value, { stream: true })
        const lines = buffer.split('\n')
        buffer = lines.pop() ?? ''
        for (const line of lines) {
          if (!line.startsWith('data: ')) continue
          const raw = line.slice(6).trim()
          if (raw === '[DONE]') { await fetchAll(); return }
          try {
            const evt = JSON.parse(raw) as {
              step: string; status: string; message?: string
              elapsed_ms?: number; data?: Record<string, unknown>
              result?: Record<string, unknown>
            }
            if (evt.step === 'done') {
              setFlowResult(evt.result ?? null)
            } else {
              setFlowSteps(prev => ({
                ...prev,
                [evt.step]: { status: evt.status as StepStatus, message: evt.message ?? '', elapsed_ms: evt.elapsed_ms, data: evt.data },
              }))
            }
          } catch { /* ignore parse errors */ }
        }
      }
      await fetchAll()
    } catch (err) {
      try {
        const processPath = streamPath.includes('missing-info')
          ? '/api/email/process/sample/missing-info'
          : '/api/email/process/sample'
        const primaryUrl = `${base}${processPath}`
        let fallbackRes: Response
        try {
          fallbackRes = await fetch(primaryUrl, { method: 'POST' })
        } catch (primaryErr) {
          if (!fallbackBase) throw primaryErr
          fallbackRes = await fetch(`${fallbackBase}${processPath}`, { method: 'POST' })
        }
        const fallbackData = await fallbackRes.json()
        if (!fallbackRes.ok) {
          throw new Error(fallbackData?.detail?.message ?? fallbackData?.detail ?? `HTTP ${fallbackRes.status}`)
        }
        const created = String(fallbackData?.status ?? '') === 'created'
        setFlowSteps(prev => ({
          ...prev,
          wo_create: created
            ? { status: 'complete', message: `Created ${String(fallbackData?.work_order_id ?? 'work order')}` }
            : { status: 'warning', message: `Flow finished with status: ${String(fallbackData?.status ?? 'unknown')}` },
          journey_log: created
            ? { status: 'complete', message: `Journey initialized (${String(fallbackData?.journey_log_id ?? 'n/a')})` }
            : { status: 'warning', message: 'Journey initialization skipped.' },
          notification: { status: 'warning', message: 'Stream unavailable; completed via non-stream fallback.' },
        }))
        setFlowResult(fallbackData as Record<string, unknown>)
        await fetchAll()
        return
      } catch { /* fall through to error */ }
      const raw = err instanceof Error ? err.message : String(err)
      const endpoint = `${base || '(empty-base-url)'}${streamPath}`
      const isNetworkError = raw.toLowerCase().includes('failed to fetch') || raw.toLowerCase().includes('networkerror')
      if (isNetworkError) {
        setEmailResultErr(`Cannot reach ${endpoint}. Check Work Order Service URL and ensure svc-work-order-management is reachable on 127.0.0.1:8007.`)
      } else {
        setEmailResultErr(`${raw} (${endpoint})`)
      }
    } finally {
      setSampling(false)
      readerRef.current = null
    }
  }

  async function pollInbox() {
    setPolling(true)
    setFlowVisible(false)
    setPollResult(null)
    setEmailResultErr(null)
    try {
      const res = await fetch(`${base}/api/email/poll`, { method: 'POST' })
      const data = await res.json()
      if (!res.ok) throw new Error(data?.detail?.message ?? data?.detail ?? `HTTP ${res.status}`)
      setPollResult(data)
      await fetchAll()
    } catch (err) {
      setEmailResultErr(String(err))
    } finally {
      setPolling(false)
    }
  }

  const fetchAll = useCallback(async () => {
    try {
      const [statsRes, wosRes, emailRes, journeyRes] = await Promise.allSettled([
        fetch(`${base}/api/dashboard/stats`).then(r => r.json()),
        fetch(`${base}/api/work-orders/?page=1&limit=20`).then(r => r.json()),
        fetch(`${base}/api/email/status`).then(r => r.json()),
        fetch(`${base}/api/journeys/analytics/summary`).then(r => r.json()),
      ])
      if (statsRes.status   === 'fulfilled') setStats(statsRes.value)
      if (wosRes.status     === 'fulfilled') {
        const d = wosRes.value
        setWorkOrders(Array.isArray(d) ? d : (d.items ?? d.work_orders ?? []))
      }
      if (emailRes.status   === 'fulfilled') setEmailStatus(emailRes.value)
      if (journeyRes.status === 'fulfilled') setJourneyStats(journeyRes.value)
      setError(null)
      setLastRefresh(new Date())
    } catch (e) {
      setError(String(e))
    } finally {
      setLoading(false)
    }
  }, [base])

  useEffect(() => {
    fetchAll()
    const id = setInterval(fetchAll, 15_000)
    return () => clearInterval(id)
  }, [fetchAll])

  if (loading) {
    return (
      <div className="flex items-center justify-center h-64">
        <div className="flex flex-col items-center gap-3">
          <div className="w-9 h-9 border-2 border-indigo-500 border-t-transparent rounded-full animate-spin" />
          <span className="text-sm text-slate-500">Connecting to Work Order Service…</span>
          <span className="text-xs text-slate-400 font-mono">{base}</span>
        </div>
      </div>
    )
  }

  const byStatus   = stats?.by_status   ?? {}
  const byPriority = stats?.by_priority ?? {}
  const bySource   = stats?.by_source   ?? {}
  const totalWOs   = stats?.total_work_orders ?? 0

  const filteredWOs = activeFilter
    ? workOrders.filter(wo => wo.status === activeFilter)
    : workOrders

  const latestAssessment = (flowResult?.full_assessment as Record<string, unknown> | undefined) ?? null
  const latestSummary    = (flowResult?.assessment_summary as Record<string, unknown> | undefined) ?? null

  function getAiBlockDetails(blockKey: string): unknown {
    if (latestAssessment) {
      const value = latestAssessment[blockKey]
      if (value !== undefined) return value
    }
    if (latestSummary) {
      const summaryMap: Record<string, unknown> = {
        criticality: { level: latestSummary.criticality_level, safety_score: latestSummary.safety_score, response_time_hours: latestSummary.response_time_hours },
        safety:      { critical_safety: latestSummary.critical_safety, ppe_required: latestSummary.ppe_required },
        compliance:  { compliance_required: latestSummary.compliance_required },
        technician:  { required_skills: latestSummary.required_skills, estimated_duration_hours: latestSummary.estimated_duration_hrs },
        schedule:    { suggested_timeframe: latestSummary.suggested_timeframe },
        vendors:     { vendor_type: latestSummary.vendor_type },
        parts_list:  { parts_needed: latestSummary.parts_needed },
        journey:     { sla_deadline_hours: latestSummary.sla_deadline_hours },
      }
      if (summaryMap[blockKey] != null) return summaryMap[blockKey]
    }
    if (flowSteps.ai_assessment) {
      return { stage_status: flowSteps.ai_assessment.status, message: flowSteps.ai_assessment.message, elapsed_ms: flowSteps.ai_assessment.elapsed_ms, data: flowSteps.ai_assessment.data ?? null }
    }
    return null
  }

  if (activeTab === 'chat') {
    return (
      <div className="flex flex-col h-full -m-6 overflow-hidden">
        {/* Tab bar (inside chat mode) */}
        <div className="flex items-center gap-1 px-4 py-2 bg-white border-b border-slate-200 shrink-0">
          <button
            onClick={() => setActiveTab('overview')}
            className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium text-slate-500 hover:text-slate-800 hover:bg-slate-100 transition-colors"
          >
            <ClipboardList size={13} />
            Overview
          </button>
          <button
            className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium bg-indigo-600 text-white"
          >
            <Bot size={13} />
            Chat Assistant
          </button>
        </div>
        <div className="flex-1 overflow-hidden">
          <ChatPanel base={base} fallbackBase={fallbackBase} />
        </div>
      </div>
    )
  }

  return (
    <div className="space-y-5 pb-10">

      {/* ── Tab bar ──────────────────────────────────────────────────── */}
      <div className="flex items-center gap-1 bg-slate-100 rounded-xl p-1 w-fit">
        <button
          onClick={() => setActiveTab('overview')}
          className="flex items-center gap-1.5 px-4 py-2 rounded-lg text-xs font-medium bg-white text-slate-800 shadow-sm"
        >
          <ClipboardList size={13} />
          Overview
        </button>
        <button
          onClick={() => setActiveTab('chat')}
          className="flex items-center gap-1.5 px-4 py-2 rounded-lg text-xs font-medium text-slate-500 hover:text-slate-800 hover:bg-white/60 transition-colors"
        >
          <Bot size={13} />
          Chat Assistant
          <span className="ml-1 px-1.5 py-0.5 rounded-full bg-indigo-100 text-indigo-600 text-xs font-semibold">New</span>
        </button>
      </div>

      {/* ── Status Bar ──────────────────────────────────────────────── */}
      <div className="flex items-center justify-between flex-wrap gap-3">
        <div className="flex items-center gap-5 flex-wrap">
          <div className={`flex items-center gap-1.5 text-sm font-medium ${emailStatus?.connected ? 'text-emerald-600' : 'text-slate-400'}`}>
            {emailStatus?.connected ? <Wifi size={14} /> : <WifiOff size={14} />}
            {emailStatus?.connected
              ? <span>Outlook: <strong>{emailStatus.display_name}</strong> ({emailStatus.email})</span>
              : <span>Outlook: Disconnected</span>}
          </div>
          <div className="flex items-center gap-1.5 text-sm text-slate-500">
            <Database size={13} />
            <span>{totalWOs} work orders total</span>
          </div>
          {lastRefresh && (
            <span className="text-xs text-slate-400">Last updated {lastRefresh.toLocaleTimeString()}</span>
          )}
        </div>
        <div className="flex items-center gap-2">
          <button
            onClick={() => processSample()}
            disabled={sampling || polling}
            title="Run the sample email through the full pipeline — no Outlook token needed"
            className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium bg-indigo-600 hover:bg-indigo-700 disabled:opacity-50 text-white transition-colors"
          >
            <Mail size={12} />
            {sampling ? 'Processing…' : 'Process Sample Email'}
          </button>
          <button
            onClick={() => processSample('/api/email/process/sample/missing-info/stream')}
            disabled={sampling || polling}
            title="Test the missing-info reply flow"
            className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium bg-amber-600 hover:bg-amber-700 disabled:opacity-50 text-white transition-colors"
          >
            <Mail size={12} />
            {sampling ? 'Processing…' : 'Test Missing-Info'}
          </button>
          <button
            onClick={pollInbox}
            disabled={sampling || polling || !emailStatus?.connected}
            title={emailStatus?.connected ? 'Poll Outlook inbox for unread emails' : 'Outlook not connected'}
            className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium bg-emerald-600 hover:bg-emerald-700 disabled:opacity-40 text-white transition-colors"
          >
            <Inbox size={12} />
            {polling ? 'Polling…' : 'Poll Inbox'}
          </button>
          <button
            onClick={fetchAll}
            className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium bg-slate-100 hover:bg-slate-200 text-slate-600 transition-colors"
          >
            <RefreshCw size={12} />
            Refresh
          </button>
        </div>
      </div>

      {/* ── Error banner ────────────────────────────────────────────── */}
      {emailResultErr && (
        <div className="rounded-lg bg-red-50 border border-red-200 px-4 py-3 text-sm text-red-700 flex items-start justify-between gap-3">
          <span>⚠ {emailResultErr}</span>
          <button onClick={() => setEmailResultErr(null)} className="shrink-0 text-red-400 hover:text-red-600"><X size={14} /></button>
        </div>
      )}

      {/* ── Poll result banner ───────────────────────────────────────── */}
      {pollResult && (
        <div className="rounded-xl bg-slate-50 border border-slate-200 px-4 py-3 flex items-start justify-between gap-3">
          <div className="space-y-2">
            <p className="text-sm font-semibold text-slate-800">
              Inbox poll complete
            </p>
            <div className="flex flex-wrap gap-3 text-xs">
              <span className="flex items-center gap-1 text-teal-700 font-medium">
                <ClipboardList size={11} /> {String(pollResult.created ?? 0)} created
              </span>
              <span className="flex items-center gap-1 text-emerald-700 font-medium">
                <ThumbsUp size={11} /> {String(pollResult.approved ?? 0)} approved
              </span>
              <span className="flex items-center gap-1 text-red-600 font-medium">
                <ThumbsDown size={11} /> {String(pollResult.rejected ?? 0)} rejected
              </span>
              <span className="flex items-center gap-1 text-amber-700 font-medium">
                <AlertTriangle size={11} /> {String(pollResult.missing_info ?? 0)} missing info
              </span>
              <span className="text-slate-500">
                fetched {String(pollResult.fetched ?? 0)} · skipped {String(pollResult.skipped ?? 0)} · errors {String(pollResult.errors ?? 0)}
              </span>
            </div>
            {Array.isArray(pollResult.work_orders) && pollResult.work_orders.length > 0 && (
              <p className="text-xs font-mono text-slate-500">{(pollResult.work_orders as string[]).join(', ')}</p>
            )}
          </div>
          <button onClick={() => setPollResult(null)} className="shrink-0 text-slate-400 hover:text-slate-600"><X size={14} /></button>
        </div>
      )}

      {/* ── Live SSE Flow Visualization ──────────────────────────────── */}
      {flowVisible && (
        <div className="rounded-2xl border border-slate-200 bg-white overflow-hidden shadow-sm">
          <div className="flex items-center justify-between px-5 py-3.5 border-b border-slate-100 bg-slate-50">
            <div className="flex items-center gap-2.5">
              <div className="w-7 h-7 rounded-lg bg-indigo-600 flex items-center justify-center">
                <Activity size={13} className="text-white" />
              </div>
              <div>
                <p className="text-sm font-semibold text-slate-800">Email Processing Pipeline</p>
                <p className="text-xs text-slate-400">Live step-by-step — 12 stages</p>
              </div>
            </div>
            <button
              onClick={() => { setFlowVisible(false); setFlowSteps({}); setFlowResult(null) }}
              className="text-slate-400 hover:text-slate-600 p-1"
            >
              <X size={14} />
            </button>
          </div>

          <div className="px-5 py-4 space-y-0">
            {FLOW_STEPS.map((step, idx) => {
              const s = flowSteps[step.key]
              const status: StepStatus = s?.status ?? 'idle'
              const isLast = idx === FLOW_STEPS.length - 1
              const isDivider = step.key === 'approval_request'

              return (
                <div key={step.key}>
                  {isDivider && (
                    <div className="flex items-center gap-2 my-3 ml-3">
                      <div className="w-3 h-px bg-amber-200" />
                      <span className="text-xs text-amber-600 font-medium">Approval Flow</span>
                      <div className="flex-1 h-px bg-amber-200" />
                    </div>
                  )}
                  <div className="flex gap-3">
                    <div className="flex flex-col items-center">
                      <StepStatusIcon status={status} />
                      {!isLast && (
                        <div className={`w-0.5 flex-1 mt-1 mb-1 rounded-full transition-colors duration-500 ${
                          status === 'complete' ? 'bg-emerald-300' :
                          status === 'running'  ? 'bg-blue-300 animate-pulse' :
                          'bg-slate-200'
                        }`} style={{ minHeight: '20px' }} />
                      )}
                    </div>
                    <div className={`flex-1 pb-4 ${isLast ? 'pb-2' : ''}`}>
                      <div className="flex items-center justify-between gap-2 min-h-[24px]">
                        <div className="flex items-center gap-2">
                          <step.Icon size={13} className={status === 'idle' ? 'text-slate-300' : step.color} />
                          <span className={`text-sm font-medium ${
                            status === 'idle'     ? 'text-slate-400' :
                            status === 'running'  ? 'text-slate-800' :
                            status === 'complete' ? 'text-slate-800' :
                            status === 'warning'  ? 'text-amber-700' :
                            'text-red-700'
                          }`}>{step.label}</span>
                          {status === 'running' && (
                            <span className="text-xs text-blue-500 animate-pulse font-medium">processing…</span>
                          )}
                        </div>
                        {s?.elapsed_ms != null && (
                          <span className="text-xs text-slate-400 tabular-nums shrink-0">
                            {s.elapsed_ms >= 1000 ? `${(s.elapsed_ms / 1000).toFixed(1)}s` : `${s.elapsed_ms}ms`}
                          </span>
                        )}
                      </div>
                      {status === 'idle' ? (
                        <p className="text-xs text-slate-400 mt-0.5">{step.desc}</p>
                      ) : s?.message ? (
                        <p className={`text-xs mt-0.5 leading-relaxed ${
                          status === 'warning' ? 'text-amber-600' : status === 'error' ? 'text-red-600' : 'text-slate-500'
                        }`}>{s.message}</p>
                      ) : null}
                      {s?.data && (status === 'complete' || status === 'running') && (() => {
                        const d = s.data
                        const chips: string[] = []
                        if (step.key === 'db_lookup') {
                          if (d.open_wos != null) chips.push(`${d.open_wos} open WOs on asset`)
                          if (d.asset_resolved === false) chips.push('asset unresolved')
                        }
                        if (step.key === 'ai_assessment') {
                          if (d.criticality) chips.push(`Criticality: ${d.criticality}`)
                          if (d.parts_needed != null) chips.push(`${d.parts_needed} parts`)
                          if (d.response_time_hours != null) chips.push(`${d.response_time_hours}h SLA`)
                        }
                        if (step.key === 'wo_create' && d.work_order_id) chips.push(String(d.work_order_id))
                        if (step.key === 'approval_request' && d.approver_email) chips.push(String(d.approver_email))
                        if (step.key === 'waiting_approval' && d.elapsed_seconds != null) chips.push(`${d.elapsed_seconds}s elapsed`)
                        if (step.key === 'technician_assigned' && d.technician_name) chips.push(String(d.technician_name))
                        if (!chips.length) return null
                        return (
                          <div className="flex flex-wrap gap-1 mt-1.5">
                            {chips.map(c => (
                              <span key={c} className="px-2 py-0.5 bg-slate-100 text-slate-600 rounded-full text-xs font-medium">{c}</span>
                            ))}
                          </div>
                        )
                      })()}
                    </div>
                  </div>
                </div>
              )
            })}
          </div>

          {flowResult && (() => {
            const approvalStep = flowSteps['waiting_approval']
            const techName = flowSteps['technician_assigned']?.data?.technician_name

            if (flowResult.status === 'created') {
              if (approvalStep?.status === 'complete') {
                return (
                  <div className="mx-5 mb-5 rounded-xl px-4 py-3.5 border bg-emerald-50 border-emerald-200">
                    <div className="space-y-1">
                      <p className="text-sm font-bold text-emerald-800 flex items-center gap-2">
                        <CheckCircle size={15} className="text-emerald-600" />
                        Approved! {techName ? `Technician "${techName}" assigned` : 'Technician assigned'} · Notifications sent
                      </p>
                      <p className="text-xs text-emerald-700 font-mono">{String(flowResult.work_order_id)}</p>
                      <p className="text-xs text-emerald-600">
                        Priority: <strong className="capitalize">{String(flowResult.priority)}</strong>
                        {' · '}Status: <strong>approved</strong>
                        {' · '}Journey: <strong className="font-mono">{String(flowResult.journey_log_id)}</strong>
                      </p>
                    </div>
                  </div>
                )
              }
              if (approvalStep?.status === 'error') {
                return (
                  <div className="mx-5 mb-5 rounded-xl px-4 py-3.5 border bg-red-50 border-red-200">
                    <div className="space-y-1">
                      <p className="text-sm font-bold text-red-800 flex items-center gap-2">
                        <AlertTriangle size={15} className="text-red-600" />
                        Rejected by Facility Manager
                      </p>
                      <p className="text-xs text-red-700 font-mono">{String(flowResult.work_order_id)}</p>
                      {approvalStep.message && (
                        <p className="text-xs text-red-600">{approvalStep.message}</p>
                      )}
                    </div>
                  </div>
                )
              }
              if (approvalStep?.status === 'warning') {
                return (
                  <div className="mx-5 mb-5 rounded-xl px-4 py-3.5 border bg-amber-50 border-amber-200">
                    <div className="space-y-1">
                      <p className="text-sm font-bold text-amber-800 flex items-center gap-2">
                        <AlertTriangle size={15} className="text-amber-600" />
                        Timed out — background poller will notify when manager replies
                      </p>
                      <p className="text-xs text-amber-700 font-mono">{String(flowResult.work_order_id)}</p>
                      <p className="text-xs text-amber-600">Status: <strong>pending_approval</strong></p>
                    </div>
                  </div>
                )
              }
              return (
                <div className="mx-5 mb-5 rounded-xl px-4 py-3.5 border bg-emerald-50 border-emerald-200">
                  <div className="space-y-1">
                    <p className="text-sm font-bold text-emerald-800 flex items-center gap-2">
                      <CheckCircle size={15} className="text-emerald-600" />
                      Work order created — approval request sent to Facility Manager
                    </p>
                    <p className="text-xs text-emerald-700 font-mono">{String(flowResult.work_order_id)}</p>
                    <p className="text-xs text-emerald-600">
                      Priority: <strong className="capitalize">{String(flowResult.priority)}</strong>
                      {' · '}Status: <strong>pending_approval</strong>
                      {' · '}Journey: <strong className="font-mono">{String(flowResult.journey_log_id)}</strong>
                    </p>
                  </div>
                </div>
              )
            }

            return (
              <div className="mx-5 mb-5 rounded-xl px-4 py-3.5 border bg-amber-50 border-amber-200">
                <div className="space-y-1">
                  <p className="text-sm font-bold text-amber-800 flex items-center gap-2">
                    <AlertTriangle size={15} className="text-amber-600" />
                    {flowResult.status === 'missing_info' ? 'Missing info — reply sent asking for clarification' : String(flowResult.status)}
                  </p>
                  {Array.isArray(flowResult.missing_fields) && (
                    <p className="text-xs text-amber-700">
                      Could not resolve: <strong>{(flowResult.missing_fields as string[]).join(', ')}</strong>
                    </p>
                  )}
                </div>
              </div>
            )
          })()}
        </div>
      )}

      {error && (
        <div className="rounded-lg bg-red-50 border border-red-200 px-4 py-3 text-sm text-red-700">
          ⚠ Service unreachable at <code className="font-mono">{base}</code> — {error}
        </div>
      )}

      {/* ── Pipeline Diagram ─────────────────────────────────────────── */}
      <div className="bg-slate-900 rounded-2xl p-6 space-y-7">

        <div className="flex items-center justify-between">
          <div className="flex items-center gap-3">
            <div className="w-8 h-8 bg-indigo-600 rounded-lg flex items-center justify-center">
              <Activity size={15} className="text-white" />
            </div>
            <div>
              <h2 className="text-white font-semibold text-sm">Intelligent Work Order Pipeline</h2>
              <p className="text-slate-500 text-xs">Email intake → AI assessment → Approval routing → Execution · auto-refreshes every 15s</p>
            </div>
          </div>
          <div className="flex items-center gap-2">
            <span className="w-2 h-2 rounded-full bg-emerald-400 animate-pulse" />
            <span className="text-emerald-400 text-xs font-medium">Live</span>
          </div>
        </div>

        {/* ── Phase 1: Intake & AI Assessment ── */}
        <div>
          <div className="text-slate-500 text-xs font-medium uppercase tracking-wider mb-3 flex items-center gap-2">
            <span className="w-4 h-px bg-slate-700" />
            Phase 1 — Email Intake &amp; AI Assessment
            <span className="flex-1 h-px bg-slate-700" />
          </div>

          <div className="flex items-center gap-3">
            <div className="flex flex-col gap-1.5 shrink-0">
              {SOURCES.map(s => (
                <div key={s.key} className={`flex items-center gap-2.5 px-3 py-2 rounded-lg ${s.bg} border border-slate-700/60`}>
                  <s.Icon size={13} className={s.color} />
                  <span className="text-slate-300 text-xs font-medium">{s.label}</span>
                  <span className={`ml-2 text-xs font-bold tabular-nums ${s.color}`}>{bySource[s.key] ?? 0}</span>
                </div>
              ))}
            </div>

            <FlowConnector delay={0} />

            <div className="shrink-0 relative">
              <div className="wo-node-glow bg-indigo-600 rounded-2xl px-5 py-4 text-center min-w-[148px] shadow-xl shadow-indigo-950">
                <div className="w-10 h-10 bg-white/15 rounded-xl flex items-center justify-center mx-auto mb-2">
                  <Cpu size={18} className="text-white" />
                </div>
                <div className="text-white font-bold text-sm">AI Engine</div>
                <div className="text-indigo-200 text-xs mt-0.5">13 assessment blocks</div>
                <div className="flex justify-center gap-1 mt-2 flex-wrap">
                  {[...Array(13)].map((_, i) => <span key={i} className="w-1.5 h-1.5 rounded-full bg-indigo-300/60" />)}
                </div>
              </div>
              <div className="wo-pulse-ring absolute inset-0 rounded-2xl ring-2 ring-indigo-400 pointer-events-none" />
            </div>

            <FlowConnector delay={0.8} />

            <div className="shrink-0">
              <div className="bg-slate-800 border border-teal-700/60 rounded-xl px-4 py-3.5 text-center min-w-[110px]">
                <div className="w-9 h-9 bg-slate-700 rounded-xl flex items-center justify-center mx-auto mb-2">
                  <ClipboardList size={15} className="text-teal-400" />
                </div>
                <div className="text-teal-300 font-semibold text-xs">WO Created</div>
                <div className="text-slate-400 text-xs mt-0.5">
                  <span className="text-white font-bold">{totalWOs}</span> total
                </div>
              </div>
            </div>

            <FlowConnector delay={1.2} />

            <div className="shrink-0">
              <div className="bg-slate-800 border border-pink-700/60 rounded-xl px-4 py-3.5 text-center min-w-[110px]">
                <div className="w-9 h-9 bg-slate-700 rounded-xl flex items-center justify-center mx-auto mb-2">
                  <Bell size={15} className="text-pink-400" />
                </div>
                <div className="text-pink-300 font-semibold text-xs">Notify Requester</div>
                <div className="text-slate-400 text-xs mt-0.5">Confirmation email</div>
              </div>
            </div>
          </div>
        </div>

        {/* ── Phase 2: Intelligent Approval Routing ── */}
        <div>
          <div className="text-slate-500 text-xs font-medium uppercase tracking-wider mb-4 flex items-center gap-2">
            <span className="w-4 h-px bg-slate-700" />
            Phase 2 — Intelligent Approval Routing
            <span className="flex-1 h-px bg-slate-700" />
          </div>

          <div className="bg-slate-800/40 border border-slate-700/60 rounded-xl p-4 space-y-4">

            {/* Row 1: WO → Router → Email Sent */}
            <div className="flex items-stretch gap-3">
              {/* WO node */}
              <div className="shrink-0 bg-slate-800 border border-amber-700/50 rounded-xl px-3 py-2.5 text-center min-w-[92px]">
                <ClipboardList size={14} className="text-amber-400 mx-auto mb-1" />
                <div className="text-amber-300 font-semibold text-xs">WO Created</div>
                <div className="text-slate-500 text-xs mt-0.5">pending_approval</div>
              </div>

              <FlowConnector delay={0.3} />

              {/* Approval Router */}
              <div className="shrink-0 bg-slate-800 border border-violet-700/50 rounded-xl px-3 py-2.5 min-w-[160px]">
                <div className="flex items-center gap-1.5 mb-2">
                  <Database size={11} className="text-violet-400" />
                  <span className="text-violet-300 font-semibold text-xs">Approval Router</span>
                </div>
                <div className="space-y-1 text-xs">
                  <div className="flex items-center gap-1.5 text-slate-400">
                    <span className="w-1.5 h-1.5 rounded-full bg-sky-400 shrink-0" />
                    HVAC · Electrical · Plumbing
                  </div>
                  <div className="flex items-center gap-1.5 text-slate-400">
                    <span className="w-1.5 h-1.5 rounded-full bg-emerald-400 shrink-0" />
                    Civil · IT · Fire · Other
                  </div>
                  <div className="text-violet-400/80 text-xs mt-1">↳ lookup plenum_cafm.users</div>
                </div>
              </div>

              <FlowConnector delay={0.7} />

              {/* Email sent */}
              <div className="shrink-0 bg-slate-800 border border-sky-700/50 rounded-xl px-3 py-2.5 text-center min-w-[120px]">
                <Mail size={14} className="text-sky-400 mx-auto mb-1" />
                <div className="text-sky-300 font-semibold text-xs">Approval Email</div>
                <div className="text-slate-500 text-xs mt-0.5">Outlook Graph API</div>
                <div className="mt-1.5 px-2 py-0.5 bg-sky-900/40 rounded text-xs text-sky-400">
                  Subject: [APPROVAL REQUIRED] {'{WO-ID}'}
                </div>
              </div>
            </div>

            {/* Down arrow */}
            <div className="flex justify-center">
              <div className="flex flex-col items-center gap-0.5">
                <div className="w-0.5 h-5 bg-slate-600 rounded-full" />
                <ChevronDown size={12} className="text-slate-500" />
              </div>
            </div>

            {/* Row 2: Manager's Inbox */}
            <div className="flex justify-center">
              <div className="bg-slate-800 border border-indigo-600/50 rounded-xl px-5 py-3 text-center w-72">
                <MessageSquare size={14} className="text-indigo-400 mx-auto mb-1.5" />
                <div className="text-indigo-300 font-semibold text-xs">Manager replies via email</div>
                <div className="flex justify-center gap-2 mt-2">
                  {['Approved', 'Go ahead', 'Rejected', 'Hold'].map(kw => (
                    <span key={kw} className="px-1.5 py-0.5 bg-slate-700 rounded text-xs text-slate-300 font-mono">{kw}</span>
                  ))}
                </div>
              </div>
            </div>

            {/* Down arrow */}
            <div className="flex justify-center">
              <div className="flex flex-col items-center gap-0.5">
                <div className="w-0.5 h-5 bg-slate-600 rounded-full" />
                <ChevronDown size={12} className="text-slate-500" />
              </div>
            </div>

            {/* Row 3: GPT Detection */}
            <div className="flex justify-center">
              <div className="bg-indigo-900/40 border border-indigo-600/50 rounded-xl px-5 py-3 text-center w-72">
                <BrainCircuit size={14} className="text-indigo-400 mx-auto mb-1.5" />
                <div className="text-indigo-300 font-semibold text-xs">GPT-4o-mini detects intent</div>
                <div className="flex justify-center gap-2 mt-2">
                  <span className="px-2 py-0.5 bg-emerald-900/50 border border-emerald-700/50 rounded text-xs text-emerald-400">approved</span>
                  <span className="px-2 py-0.5 bg-red-900/50 border border-red-700/50 rounded text-xs text-red-400">rejected</span>
                  <span className="px-2 py-0.5 bg-slate-700/80 rounded text-xs text-slate-400">unclear</span>
                </div>
              </div>
            </div>

            {/* Branch divider */}
            <div className="flex items-center gap-3">
              <div className="flex-1 h-px bg-slate-700" />
              <GitBranch size={13} className="text-slate-500 shrink-0" />
              <div className="flex-1 h-px bg-slate-700" />
            </div>

            {/* Row 4: Approved | Rejected */}
            <div className="grid grid-cols-2 gap-3">

              {/* APPROVED */}
              <div className="bg-emerald-900/25 border border-emerald-700/50 rounded-xl p-3.5">
                <div className="flex items-center gap-2 mb-3">
                  <div className="w-6 h-6 rounded-lg bg-emerald-700/60 flex items-center justify-center">
                    <ThumbsUp size={12} className="text-emerald-300" />
                  </div>
                  <span className="text-emerald-300 font-bold text-xs tracking-wide">APPROVED</span>
                </div>
                <div className="space-y-2">
                  <div className="flex items-start gap-2 text-xs text-slate-400">
                    <User size={10} className="text-blue-400 shrink-0 mt-0.5" />
                    <span>Find best skill-matched technician from <span className="text-blue-300">technicians</span> table</span>
                  </div>
                  <div className="flex items-start gap-2 text-xs text-slate-400">
                    <Calendar size={10} className="text-amber-400 shrink-0 mt-0.5" />
                    <span>Cross-check PPM schedule — flag overdue or due_soon</span>
                  </div>
                  <div className="flex items-start gap-2 text-xs text-slate-400">
                    <Send size={10} className="text-pink-400 shrink-0 mt-0.5" />
                    <span>Email requester — WO approved + technician name</span>
                  </div>
                  <div className="flex items-start gap-2 text-xs text-slate-400">
                    <Bell size={10} className="text-sky-400 shrink-0 mt-0.5" />
                    <span>Email technician — assignment details + PPM note</span>
                  </div>
                  <div className="mt-2.5 px-2.5 py-1.5 bg-emerald-800/40 rounded-lg text-xs text-emerald-400 text-center font-medium">
                    Status → preparing
                  </div>
                </div>
              </div>

              {/* REJECTED */}
              <div className="bg-red-900/20 border border-red-700/40 rounded-xl p-3.5">
                <div className="flex items-center gap-2 mb-3">
                  <div className="w-6 h-6 rounded-lg bg-red-700/60 flex items-center justify-center">
                    <ThumbsDown size={12} className="text-red-300" />
                  </div>
                  <span className="text-red-300 font-bold text-xs tracking-wide">REJECTED</span>
                </div>
                <div className="space-y-2">
                  <div className="flex items-start gap-2 text-xs text-slate-400">
                    <X size={10} className="text-red-400 shrink-0 mt-0.5" />
                    <span>Work order cancelled in database</span>
                  </div>
                  <div className="flex items-start gap-2 text-xs text-slate-400">
                    <Send size={10} className="text-orange-400 shrink-0 mt-0.5" />
                    <span>Email requester — rejection notice with approver's reason</span>
                  </div>
                  <div className="mt-2.5 px-2.5 py-1.5 bg-red-800/40 rounded-lg text-xs text-red-400 text-center font-medium">
                    Status → cancelled
                  </div>
                </div>
              </div>
            </div>
          </div>
        </div>

        {/* ── Phase 3: Lifecycle Execution ── */}
        <div>
          <div className="text-slate-500 text-xs font-medium uppercase tracking-wider mb-3 flex items-center gap-2">
            <span className="w-4 h-px bg-slate-700" />
            Phase 3 — Lifecycle &amp; Execution
            <span className="flex-1 h-px bg-slate-700" />
          </div>

          <div className="flex items-stretch gap-2">
            {LIFECYCLE_STAGES.map((stage, i) => (
              <>
                <StageCard
                  key={stage.key}
                  stage={stage}
                  count={byStatus[stage.key] ?? 0}
                  isActive={activeFilter === stage.key}
                  onClick={() => setActiveFilter(activeFilter === stage.key ? null : stage.key)}
                />
                {i < LIFECYCLE_STAGES.length - 1 && (
                  <div key={`arrow-${i}`} className="flex items-center shrink-0 self-center">
                    <div className="relative w-6 h-0.5 overflow-hidden rounded-full">
                      <div className="absolute inset-0 bg-slate-700" />
                      <div className="wo-flow-shimmer absolute top-0 left-0 h-full w-full" style={{ animationDelay: `${i * 0.3}s` }} />
                    </div>
                    <ChevronRight size={10} className="text-slate-600 -ml-1 shrink-0" />
                  </div>
                )}
              </>
            ))}
          </div>

          <div className="flex items-center justify-between mt-3 mx-1">
            <div className="flex items-center gap-2">
              <button
                onClick={() => setActiveFilter('cancelled')}
                className={`flex items-center gap-1.5 px-2.5 py-1 rounded-lg border text-xs font-medium transition-colors ${
                  activeFilter === 'cancelled'
                    ? 'bg-red-50 border-red-300 text-red-700'
                    : 'bg-slate-800 border-slate-700 text-slate-500 hover:border-red-700/60 hover:text-red-400'
                }`}
              >
                <X size={10} />
                Cancelled / Rejected — {byStatus['cancelled'] ?? 0}
              </button>
            </div>
            <p className="text-slate-600 text-xs">Click a stage to filter work orders below</p>
          </div>
        </div>
      </div>

      {/* ── Stats Row ────────────────────────────────────────────────── */}
      <div className="grid grid-cols-4 gap-4">
        <div className="card p-4">
          <div className="flex items-center justify-between mb-3">
            <span className="text-xs font-medium text-slate-500">Total Work Orders</span>
            <ClipboardList size={14} className="text-slate-400" />
          </div>
          <div className="text-3xl font-bold text-slate-900 tabular-nums">{totalWOs}</div>
          <div className="text-xs text-slate-400 mt-1">All time</div>
        </div>

        <div className="card p-4">
          <div className="flex items-center justify-between mb-3">
            <span className="text-xs font-medium text-slate-500">Pending Approval</span>
            <Clock size={14} className="text-amber-500" />
          </div>
          <div className="text-3xl font-bold text-amber-600 tabular-nums">{byStatus['pending_approval'] ?? 0}</div>
          <div className="text-xs text-slate-400 mt-1">Awaiting manager reply</div>
        </div>

        <div className="card p-4">
          <div className="flex items-center justify-between mb-3">
            <span className="text-xs font-medium text-slate-500">Active Now</span>
            <Zap size={14} className="text-emerald-500" />
          </div>
          <div className="text-3xl font-bold text-emerald-600 tabular-nums">{byStatus['active'] ?? 0}</div>
          <div className="text-xs text-slate-400 mt-1">In execution</div>
        </div>

        <div className="card p-4">
          <div className="flex items-center justify-between mb-3">
            <span className="text-xs font-medium text-slate-500">Cancelled / Rejected</span>
            <ThumbsDown size={14} className="text-red-400" />
          </div>
          <div className="text-3xl font-bold text-red-500 tabular-nums">{byStatus['cancelled'] ?? 0}</div>
          <div className="text-xs text-slate-400 mt-1">
            {journeyStats?.in_progress_journeys ?? 0} journeys in progress
          </div>
        </div>
      </div>

      {/* ── AI Intelligence Engine — 13 Blocks ──────────────────────── */}
      <div className="card overflow-hidden">
        <button
          className="w-full flex items-center justify-between px-5 py-4 hover:bg-slate-50 transition-colors"
          onClick={() => setAiExpanded(v => !v)}
        >
          <div className="flex items-center gap-3">
            <div className="w-9 h-9 bg-indigo-100 rounded-xl flex items-center justify-center">
              <Cpu size={16} className="text-indigo-600" />
            </div>
            <div className="text-left">
              <div className="font-semibold text-slate-900 text-sm">AI Intelligence Engine — 13 Assessment Blocks</div>
              <div className="text-xs text-slate-500 mt-0.5">
                Runs on every WO via GPT-4o-mini · produces skills, criticality, parts, PPM data used in approval routing
              </div>
            </div>
          </div>
          {aiExpanded ? <ChevronDown size={16} className="text-slate-400" /> : <ChevronRight size={16} className="text-slate-400" />}
        </button>

        {aiExpanded && (
          <div className="px-5 pb-5 border-t border-slate-100 pt-4">
            <div className="grid grid-cols-7 gap-2">
              {AI_BLOCKS.map((block, i) => (
                <button
                  key={block.key}
                  type="button"
                  onClick={() => setSelectedAiBlock(prev => (prev === block.key ? null : block.key))}
                  className={`flex flex-col items-center gap-1.5 p-2.5 rounded-xl border transition-all duration-150 group
                    ${selectedAiBlock === block.key
                      ? 'bg-indigo-50 border-indigo-300 shadow-sm'
                      : 'bg-slate-50 border-slate-100 hover:border-indigo-200 hover:bg-indigo-50'}`}
                >
                  <div className="w-9 h-9 rounded-xl bg-white border border-slate-200 flex items-center justify-center
                                  group-hover:border-indigo-300 group-hover:bg-indigo-50 transition-colors">
                    <block.Icon size={15} className={block.color} />
                  </div>
                  <span className="text-xs text-center text-slate-600 leading-tight font-medium group-hover:text-indigo-700">
                    {block.label}
                  </span>
                  <span className="text-xs text-slate-400 w-5 h-5 rounded-full bg-slate-200 flex items-center justify-center
                                   group-hover:bg-indigo-200 group-hover:text-indigo-600 transition-colors font-semibold">
                    {i + 1}
                  </span>
                </button>
              ))}
            </div>
            {selectedAiBlock && (
              <div className="mt-4 rounded-xl border border-indigo-100 bg-indigo-50/40 p-3">
                <div className="flex items-center justify-between mb-2">
                  <p className="text-xs font-semibold text-indigo-800">
                    {AI_BLOCKS.find(b => b.key === selectedAiBlock)?.label} — latest output
                  </p>
                  <button type="button" onClick={() => setSelectedAiBlock(null)} className="text-xs text-indigo-600 hover:text-indigo-800">close</button>
                </div>
                {getAiBlockDetails(selectedAiBlock) != null ? (
                  <pre className="text-[11px] leading-relaxed text-slate-700 whitespace-pre-wrap break-words max-h-56 overflow-auto bg-white border border-indigo-100 rounded-lg p-2">
                    {JSON.stringify(getAiBlockDetails(selectedAiBlock), null, 2)}
                  </pre>
                ) : (
                  <p className="text-xs text-slate-600">
                    No captured data yet. Run <strong>Process Sample Email</strong> first, then click this block.
                  </p>
                )}
              </div>
            )}
          </div>
        )}
      </div>

      {/* ── Breakdowns ───────────────────────────────────────────────── */}
      <div className="grid grid-cols-2 gap-4">

        <div className="card p-5">
          <div className="flex items-center gap-2 mb-4">
            <BarChart3 size={14} className="text-slate-500" />
            <h3 className="font-semibold text-slate-900 text-sm">Intake by Source</h3>
          </div>
          <div className="space-y-3">
            {SOURCES.map(s => {
              const count = bySource[s.key] ?? 0
              const pct = totalWOs > 0 ? Math.round((count / totalWOs) * 100) : 0
              return (
                <div key={s.key}>
                  <div className="flex items-center justify-between mb-1.5">
                    <div className="flex items-center gap-1.5">
                      <span className={`w-2 h-2 rounded-full ${s.dot}`} />
                      <span className="text-xs text-slate-600 font-medium">{s.label}</span>
                    </div>
                    <div className="flex items-center gap-2">
                      <span className="text-xs font-bold text-slate-800 tabular-nums">{count}</span>
                      <span className="text-xs text-slate-400 tabular-nums w-7 text-right">{pct}%</span>
                    </div>
                  </div>
                  <div className="h-2 bg-slate-100 rounded-full overflow-hidden">
                    <div className="h-full rounded-full bg-indigo-400 transition-all duration-700" style={{ width: `${pct}%` }} />
                  </div>
                </div>
              )
            })}
          </div>
        </div>

        <div className="card p-5">
          <div className="flex items-center gap-2 mb-4">
            <AlertTriangle size={14} className="text-slate-500" />
            <h3 className="font-semibold text-slate-900 text-sm">Work Orders by Priority</h3>
          </div>
          <div className="space-y-3">
            {['critical', 'urgent', 'high', 'medium', 'low'].map(pk => {
              const count = byPriority[pk] ?? 0
              const pct = totalWOs > 0 ? Math.round((count / totalWOs) * 100) : 0
              return (
                <div key={pk}>
                  <div className="flex items-center justify-between mb-1.5">
                    <div className="flex items-center gap-1.5">
                      <span className={`w-2 h-2 rounded-full ${PRIORITY_BAR[pk]}`} />
                      <span className="text-xs text-slate-600 font-medium capitalize">{pk}</span>
                    </div>
                    <div className="flex items-center gap-2">
                      <span className="text-xs font-bold text-slate-800 tabular-nums">{count}</span>
                      <span className="text-xs text-slate-400 tabular-nums w-7 text-right">{pct}%</span>
                    </div>
                  </div>
                  <div className="h-2 bg-slate-100 rounded-full overflow-hidden">
                    <div className={`h-full rounded-full transition-all duration-700 ${PRIORITY_BAR[pk]}`} style={{ width: `${pct}%` }} />
                  </div>
                </div>
              )
            })}
          </div>
        </div>
      </div>

      {/* ── Work Orders Table ─────────────────────────────────────────── */}
      <div className="card overflow-hidden">
        <div className="px-5 py-4 border-b border-slate-100 flex items-center justify-between">
          <div className="flex items-center gap-3">
            <ClipboardList size={14} className="text-slate-500" />
            <h3 className="font-semibold text-slate-900 text-sm">
              {activeFilter
                ? `${LIFECYCLE_STAGES.find(s => s.key === activeFilter)?.label ?? activeFilter.replace(/_/g, ' ')} — Work Orders`
                : 'Recent Work Orders'}
            </h3>
            {activeFilter && (
              <button onClick={() => setActiveFilter(null)} className="text-xs text-indigo-600 hover:text-indigo-800 underline underline-offset-2">
                clear filter
              </button>
            )}
          </div>
          <span className="text-xs text-slate-400 tabular-nums">{filteredWOs.length} records</span>
        </div>

        <div className="divide-y divide-slate-100">
          {filteredWOs.length === 0 ? (
            <div className="py-14 text-center">
              <ClipboardList size={32} className="text-slate-200 mx-auto mb-3" />
              <p className="text-slate-400 text-sm">No work orders found</p>
            </div>
          ) : (
            filteredWOs.map(wo => {
              const isExpanded = selectedWO === wo.work_order_id
              const sBadge = STATUS_BADGE[wo.status] ?? 'bg-slate-100 text-slate-600'
              const pBadge = PRIORITY_BADGE[wo.priority] ?? 'bg-slate-100 text-slate-600'
              return (
                <div key={wo.work_order_id}>
                  <button
                    className="w-full flex items-center gap-4 px-5 py-3.5 hover:bg-slate-50 transition-colors text-left"
                    onClick={() => setSelectedWO(isExpanded ? null : wo.work_order_id)}
                  >
                    <span className={`w-2 h-2 rounded-full shrink-0 ${PRIORITY_BAR[wo.priority] ?? 'bg-slate-400'}`} />
                    <div className="flex-1 min-w-0">
                      <div className="flex items-center gap-2 mb-0.5 flex-wrap">
                        <span className="font-mono text-xs text-slate-400">{wo.work_order_id.slice(-14)}</span>
                        <span className={`badge ${sBadge}`}>{wo.status.replace(/_/g, ' ')}</span>
                        <span className={`badge ${pBadge} capitalize`}>{wo.priority}</span>
                        <span className="badge bg-slate-100 text-slate-500 capitalize">{wo.source}</span>
                      </div>
                      <p className="text-sm font-medium text-slate-900 truncate">{wo.issue_description}</p>
                      <p className="text-xs text-slate-500 mt-0.5">
                        {wo.asset || '—'} &middot; {wo.location || '—'} &middot; {wo.requester_name}
                      </p>
                    </div>
                    <div className="shrink-0">
                      {isExpanded ? <ChevronDown size={14} className="text-slate-400" /> : <ChevronRight size={14} className="text-slate-400" />}
                    </div>
                  </button>

                  {isExpanded && (
                    <div className="px-5 pb-5 pt-3 bg-slate-50/70 border-t border-slate-100">
                      <div className="grid grid-cols-3 gap-x-8 gap-y-3 text-xs">
                        {[
                          ['Work Order ID', <span className="font-mono">{wo.work_order_id}</span>],
                          ['Status',        <span className={`badge ${sBadge}`}>{wo.status.replace(/_/g, ' ')}</span>],
                          ['Priority',      <span className={`badge ${pBadge} capitalize`}>{wo.priority}</span>],
                          ['Asset',         wo.asset || '—'],
                          ['Location',      wo.location || '—'],
                          ['Source',        <span className="capitalize">{wo.source}</span>],
                          ['Requester',     wo.requester_name],
                          ['Email',         wo.requester_email || '—'],
                          ['Vendor',        wo.vendor || 'Not assigned'],
                          ['Scheduled',     wo.scheduled_date || 'Not scheduled'],
                          ['Created',       wo.created_at ? new Date(wo.created_at).toLocaleString() : '—'],
                        ].map(([label, value], i) => (
                          <div key={i}>
                            <span className="text-slate-500 block mb-0.5">{label as string}</span>
                            <span className="text-slate-800 font-medium">{value as React.ReactNode}</span>
                          </div>
                        ))}
                        <div className="col-span-3">
                          <span className="text-slate-500 block mb-0.5">Issue Description</span>
                          <span className="text-slate-800">{wo.issue_description}</span>
                        </div>
                      </div>
                    </div>
                  )}
                </div>
              )
            })
          )}
        </div>
      </div>
    </div>
  )
}
