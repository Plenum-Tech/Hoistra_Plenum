// The migration card in Hoistra's Orchestrator transcript: one pipeline step on screen at a
// time, a rail of the ten steps across the top, finished steps reopenable, and the same
// answers going to the service as CAFM Web sends.
//
// Everything inside a step is CAFM Web's own code (src/cafm/features/...): the gates, the
// analysis cards, the step outputs and the submissions. This file only decides which step is
// on screen and draws the frame around it. The panel (migration-content.tsx) reports what the
// run is waiting for through the WizardBridge; hoistra-wizard-steps.js turns that into the rail.
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { QueryClient, QueryClientProvider, useQueryClient } from "@tanstack/react-query";
import { AlertTriangle, ArrowLeft, Check, CheckCircle, ChevronDown, FileSpreadsheet, RotateCcw, X } from "lucide-react";
import { MIGRATION_POLL_INTERVAL_MS, useMigrationStatus, type MigrationStatusResponse } from "@/features/ai/chat-api";
import { createUdrRun, listUdrRuns, type UdrRunVersion } from "@/features/ai/udr-runs-api";
import MigrationContent, { CenterPreSemanticHistory } from "@/features/ai/pipeline/migration/migration-content";
import { MigrationStepSnapshot } from "@/features/ai/pipeline/migration/step-pause";
import { ColumnIntelligencePanels, TableResolutionPanels } from "@/features/ai/pipeline/migration/migration-metadata-view";
import { SkeletonPanel } from "@/components/ui";
import { cn } from "@/utils/cn";
import { fmtDuration, progressLine, settleActive, wizardModel } from "./hoistra-wizard-steps.js";
import type { WizardActions, WizardActive, WizardBridge, WizardContentState } from "./hoistra-wizard-bridge";
// Cancel goes through Hoistra's own client: DELETE /api/migration/{id} (signed in, own company).
import { schemaMapperApi } from "../api/schemaMapper.js";
import "./cafm.css";

type Props = {
  migrationId: string;
  fileName?: string;
  sessionId?: string;
  onDismiss?: () => void;
  /** Tells Hoistra's own migration poll to stand down while this card drives the run. */
  onMounted?: (mounted: boolean) => void;
};

type WizardStep = {
  n: number;
  id: string;
  short: string;
  title: string;
  state: "done" | "active" | "pending" | "error";
  current: boolean;
  reviewable: boolean;
  durationMs: number | null;
};

type WizardModel = {
  activeNode: number;
  steps: WizardStep[];
  status: { tone: "accent" | "ok" | "risk" | "neutral"; label: string };
  context: string;
  subTitle: string | null;
  subIndex: [number, number] | null;
};

type View = { kind: "current" } | { kind: "step"; n: number };

const LOADING: WizardActive = { kind: "loading" };

const TERMINAL = new Set(["complete", "failed", "ddl_failed", "error", "cancelled", "canceled"]);

export default function HoistraMigrationWizard(props: Props) {
  const [client] = useState(
    () =>
      new QueryClient({
        defaultOptions: {
          queries: { retry: 1, refetchOnWindowFocus: false, refetchOnReconnect: false, refetchOnMount: false, staleTime: 5 * 60 * 1000 },
          mutations: { retry: 0 },
        },
      }),
  );
  return (
    <QueryClientProvider client={client}>
      <Wizard {...props} />
    </QueryClientProvider>
  );
}

/** Steps whose output the card can reopen: kept snapshots, plus the analysis reports that stand in for steps 2 and 3. */
function reviewableNodes(content: WizardContentState | null): number[] {
  if (!content) return [];
  const out = new Set<number>();
  for (const s of content.snapshots) if (s.nodeId !== 2) out.add(s.nodeId);
  if (content.udrTable) out.add(2);
  if (content.preSemantic.length || content.udrColumn) out.add(3);
  return [...out];
}

function useNow(ticking: boolean) {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (!ticking) return;
    const t = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(t);
  }, [ticking]);
  return now;
}

function Wizard({ migrationId, fileName, sessionId, onDismiss, onMounted }: Props) {
  const queryClient = useQueryClient();

  useEffect(() => {
    onMounted?.(true);
    return () => onMounted?.(false);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // ── The run, polled the way CAFM Web's panel polls it ────────────────────────────────
  const [forcePollUntil, setForcePollUntil] = useState(0);
  const { data: migration, isLoading, refetch } = useMigrationStatus(migrationId, {
    enabled: !!migrationId,
    refetchInterval: MIGRATION_POLL_INTERVAL_MS,
    forceUntil: forcePollUntil,
    // A step can take longer than the five-minute force window (output generation and the write
    // on a 280k-row workbook run well past it). CAFM's poll otherwise stops on a "running" run
    // whose next gate node is still pending, and the card would say "running" for good.
    keepPollingWhileRunning: true,
  });
  useEffect(() => {
    if (!migrationId) return;
    const st = String(migration?.status ?? "").toLowerCase();
    if (TERMINAL.has(st) || st === "step_paused" || st === "awaiting_review") {
      setForcePollUntil(0);
      return;
    }
    if (st === "running") setForcePollUntil(Date.now() + 5 * 60_000);
  }, [migrationId, migration?.status]);
  const handleRefresh = useCallback(() => {
    setForcePollUntil(Date.now() + 60_000);
    void refetch();
    void queryClient.invalidateQueries({ queryKey: ["migration", "status", migrationId] });
  }, [migrationId, queryClient, refetch]);

  // ── Saved versions (CAFM Web's "Save version" / version picker) ──────────────────────
  const [versions, setVersions] = useState<UdrRunVersion[]>([]);
  const [savingVersion, setSavingVersion] = useState(false);
  const [pickedVersionId, setPickedVersionId] = useState("");
  const reloadVersions = useCallback(async () => {
    if (!sessionId) return;
    try {
      setVersions(await listUdrRuns(sessionId, 10));
    } catch {
      /* keep the list we have */
    }
  }, [sessionId]);
  useEffect(() => {
    void reloadVersions();
  }, [reloadVersions]);
  const handleSaveVersion = async () => {
    if (!sessionId || !migrationId) return;
    setSavingVersion(true);
    try {
      const nextNo = (versions[0]?.version_no ?? 0) + 1;
      const created = await createUdrRun({ sessionId, customName: `Migration version ${nextNo}`, migrationIds: [migrationId] });
      // Keep this run's step outputs under the version, so reopening it later shows them
      // even after "Restart from step 1" has rewritten the live run's store.
      if (created?.id && typeof window !== "undefined") {
        try {
          for (const key of ["plenum-migration-snapshot-by-node", "plenum-migration-pre-semantic-history"]) {
            const v = localStorage.getItem(`${key}:${migrationId}`);
            if (v) localStorage.setItem(`${key}:version:${created.id}`, v);
          }
        } catch {
          /* private mode / quota */
        }
      }
      await reloadVersions();
    } catch {
      /* the list stays as it was */
    } finally {
      setSavingVersion(false);
    }
  };

  // ── What the panel decided to show ───────────────────────────────────────────────────
  const [content, setContent] = useState<WizardContentState | null>(null);
  const [cancelError, setCancelError] = useState<string | null>(null);
  const actionsRef = useRef<WizardActions | null>(null);
  const bridge = useMemo<WizardBridge>(
    () => ({ onState: setContent, register: (a) => { actionsRef.current = a; } }),
    [],
  );
  // The step on the rail never slips backwards while the service is working (settleActive):
  // held per run in a ref and settled synchronously — settleActive is idempotent, so a
  // repeated render lands on the same answer.
  const settledRef = useRef<{ key: string; active: WizardActive }>({ key: "", active: LOADING });
  const runKey = `${migrationId}::${pickedVersionId}`;
  const rawActive: WizardActive = content?.active ?? LOADING;
  const active: WizardActive =
    settledRef.current.key === runKey ? (settleActive(settledRef.current.active, rawActive) as WizardActive) : rawActive;
  settledRef.current = { key: runKey, active };
  const reviewable = useMemo(() => reviewableNodes(content), [content]);
  const model = useMemo(
    () => wizardModel(active, reviewable, migration?.nodes ?? []) as WizardModel,
    [active, reviewable, migration?.nodes],
  );

  // ── Which step is on screen ──────────────────────────────────────────────────────────
  const [view, setView] = useState<View>({ kind: "current" });
  // The run moved on while a finished step was open: a step that is now current, or the
  // whole run restarted, returns the card to the live step; otherwise the notice below asks.
  useEffect(() => {
    setView((v) => (v.kind === "step" && v.n >= model.activeNode ? { kind: "current" } : v));
  }, [model.activeNode]);
  useEffect(() => {
    setView({ kind: "current" });
  }, [migrationId, pickedVersionId]);
  // A different run: nothing of the previous one — its reported steps, a saved version picked
  // for it, a cancel notice — may show over the new one while it loads.
  useEffect(() => {
    setContent(null);
    setPickedVersionId("");
    setCancelError(null);
  }, [migrationId]);

  const viewingStep = view.kind === "step" ? model.steps[view.n - 1] : null;
  const needsPerson = active.kind === "gate" || (active.kind === "pause" && !active.auto) || active.kind === "failed";

  // Cancel: offered while the run can still be stopped — running, paused at a step, or waiting
  // at a gate. The step already running finishes; nothing after it starts, nothing more is written.
  const canCancel = !pickedVersionId && (active.kind === "gate" || active.kind === "pause" || active.kind === "running");
  const handleCancel = async () => {
    setCancelError(null);
    try {
      await schemaMapperApi.cancel(migrationId);
      handleRefresh();
    } catch (e) {
      setCancelError("The migration was not cancelled: " + (e instanceof Error ? e.message : String(e)));
    }
  };

  // One authored moment: the step area rises in when the step on screen changes, and the card
  // starts that step from the top.
  const areaRef = useRef<HTMLDivElement | null>(null);
  const cardRef = useRef<HTMLElement | null>(null);
  const enterKey = `${view.kind === "step" ? view.n : "current"}:${active.kind}:${active.node ?? ""}:${active.sub ?? ""}`;
  useEffect(() => {
    const el = areaRef.current;
    if (!el) return;
    el.classList.remove("hz-step-enter");
    void el.offsetWidth;
    el.classList.add("hz-step-enter");
    cardRef.current?.scrollTo({ top: 0 });
  }, [enterKey]);

  const running = !!migration && !TERMINAL.has(String(migration.status ?? "").toLowerCase());
  const now = useNow(running);
  const elapsed = (() => {
    const start = migration?.started_at ? Date.parse(migration.started_at) : NaN;
    if (!Number.isFinite(start)) return null;
    const end = migration?.completed_at ? Date.parse(migration.completed_at) : now;
    return fmtDuration(Math.max(0, (Number.isFinite(end) ? end : now) - start));
  })();

  const name = fileName || migration?.source_filename || migration?.cmms_name || "Migration";
  const shortId = migrationId.slice(0, 8);

  return (
    <div className="cafm-scope mt-[18px]">
      <section
        ref={cardRef}
        className="@container relative max-h-[80vh] overflow-y-auto rounded-2xl border border-slate-200 bg-white shadow-sm"
        aria-label={`Migration ${shortId}`}
      >
        <header className="sticky top-0 z-20 border-b border-slate-200 bg-white px-4 pt-3 pb-2.5">
          <div className="flex items-center gap-2">
            <FileSpreadsheet size={14} className="shrink-0 text-emerald-600" aria-hidden="true" />
            <span className="min-w-0 truncate text-[13px] font-semibold text-slate-900" title={name}>
              {name}
            </span>
            <span className="shrink-0 font-mono text-[10.5px] text-slate-400" title={migrationId}>
              {shortId}
            </span>
            <StatusPill tone={model.status.tone} label={model.status.label} live={model.status.label === "Running" || model.status.label === "Starting"} />
            {elapsed ? (
              <span className="hidden shrink-0 text-[11px] tabular-nums text-slate-500 @lg:inline" title="Time since the run started">
                {elapsed}
              </span>
            ) : null}
            <div className="ml-auto shrink-0">
              <MoreMenu
                sessionId={sessionId}
                versions={versions}
                pickedVersionId={pickedVersionId}
                onPickVersion={setPickedVersionId}
                onSaveVersion={() => void handleSaveVersion()}
                savingVersion={savingVersion}
                onRestart={() => actionsRef.current?.restartFromNode1()}
                canRestart={!!content && content.active.kind !== "loading" && !pickedVersionId}
                onCancel={() => void handleCancel()}
                canCancel={canCancel}
                onClose={onDismiss}
              />
            </div>
          </div>

          <StepRail
            steps={model.steps}
            viewing={view.kind === "step" ? view.n : null}
            onPick={(n) => setView(model.steps[n - 1]?.current ? { kind: "current" } : { kind: "step", n })}
          />

          <p className="mt-2 text-[12px] leading-snug text-slate-600" aria-live="polite">
            {pickedVersionId ? (
              <span className="mr-1.5 rounded bg-slate-100 px-1.5 py-0.5 text-[10.5px] font-medium text-slate-600">
                Saved version
              </span>
            ) : null}
            {model.context}
          </p>
          {(() => {
            const line = running ? progressLine(migration?.engine_progress, now) : null;
            return line ? (
              <p className="mt-0.5 truncate text-[11.5px] tabular-nums text-slate-500" title={line} data-testid="engine-progress">
                {line}
              </p>
            ) : null;
          })()}
          {cancelError ? (
            <p role="alert" className="mt-1.5 rounded-md border border-red-200 bg-red-50 px-2.5 py-1.5 text-[11.5px] text-red-700">
              {cancelError}
            </p>
          ) : null}
        </header>

        <div ref={areaRef} className="px-4 py-4">
          {view.kind === "step" && viewingStep ? (
            <>
              {needsPerson || active.kind === "complete" ? (
                <div
                  role="status"
                  className={cn(
                    "mb-4 flex flex-wrap items-center gap-2 rounded-lg border px-3 py-2 text-[12px]",
                    active.kind === "failed"
                      ? "border-red-200 bg-red-50 text-red-700"
                      : active.kind === "complete"
                        ? "border-emerald-200 bg-emerald-50 text-emerald-800"
                        : "border-indigo-200 bg-indigo-50 text-indigo-800",
                  )}
                >
                  {active.kind === "complete" ? <CheckCircle size={14} aria-hidden="true" /> : <AlertTriangle size={14} aria-hidden="true" />}
                  <span className="font-medium">
                    {active.kind === "complete"
                      ? "The migration has finished."
                      : active.kind === "failed"
                        ? `Step ${model.activeNode} failed.`
                        : `Step ${model.activeNode} needs you.`}
                  </span>
                  <button
                    type="button"
                    onClick={() => setView({ kind: "current" })}
                    className="ml-auto rounded-md bg-indigo-600 px-2.5 py-1 text-[11.5px] font-medium text-white hover:bg-indigo-700 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-indigo-500"
                  >
                    {active.kind === "complete" ? "See the results" : `Go to step ${model.activeNode}`}
                  </button>
                </div>
              ) : null}
              <DoneStepPage
                step={viewingStep}
                content={content}
                migration={migration ?? null}
                routingOpen={active.kind === "gate" && active.node === 3 && active.sub === "routing"}
                onBack={() => setView({ kind: "current" })}
              />
            </>
          ) : null}

          <div hidden={view.kind !== "current"} style={view.kind !== "current" ? { display: "none" } : undefined}>
            {isLoading && !migration ? (
              <div className="space-y-3 p-2">
                <SkeletonPanel rows={3} />
                <SkeletonPanel rows={5} />
              </div>
            ) : migrationId ? (
              <MigrationContent
                key={`${migrationId}::${pickedVersionId}`}
                migration={migration}
                migrationId={migrationId}
                viewingVersionId={pickedVersionId || undefined}
                onRefresh={handleRefresh}
                onReset={onDismiss ?? handleRefresh}
                showCompletedHistory={false}
                embeddedRail={false}
                drivePipelineSteps
                wizard={bridge}
              />
            ) : (
              <p className="px-2 text-xs text-slate-500">No migration to show.</p>
            )}
            {content?.rerunMsg ? (
              <p className="mt-3 rounded-md border border-indigo-100 bg-indigo-50 px-3 py-2 text-[11px] text-indigo-800">{content.rerunMsg}</p>
            ) : null}
          </div>
        </div>
        <RunDetails engine={migration?.engine ?? null} steps={model.steps} />
      </section>
    </div>
  );
}

function StatusPill({ tone, label, live }: { tone: WizardModel["status"]["tone"]; label: string; live: boolean }) {
  const cls =
    tone === "ok"
      ? "bg-emerald-50 text-emerald-700"
      : tone === "risk"
        ? "bg-red-50 text-red-700"
        : tone === "accent"
          ? "bg-indigo-50 text-indigo-700"
          : "bg-slate-100 text-slate-600";
  const dot =
    tone === "ok" ? "bg-emerald-500" : tone === "risk" ? "bg-red-500" : tone === "accent" ? "bg-indigo-500" : "bg-slate-400";
  return (
    <span className={cn("inline-flex shrink-0 items-center gap-1.5 rounded-full px-2 py-0.5 text-[11px] font-medium", cls)}>
      <span className={cn("h-1.5 w-1.5 rounded-full", dot, live && "hz-pulse")} aria-hidden="true" />
      {label}
    </span>
  );
}

function StepRail({ steps, viewing, onPick }: { steps: WizardStep[]; viewing: number | null; onPick: (n: number) => void }) {
  return (
    <ol className="mt-2.5 flex items-start" aria-label="Migration steps">
      {steps.map((s, i) => {
        const clickable = s.reviewable || s.current;
        const isViewing = viewing === s.n;
        const lineDone = s.state === "done" && (steps[i + 1]?.state === "done" || steps[i + 1]?.state === "active" || steps[i + 1]?.state === "error");
        const circle =
          s.state === "done"
            ? "bg-emerald-600 text-white"
            : s.state === "error"
              ? "bg-red-600 text-white"
              : s.state === "active"
                ? "bg-indigo-600 text-white ring-4 ring-indigo-100"
                : "border border-slate-300 bg-white text-slate-500";
        return (
          <li
            key={s.n}
            className={cn(
              "relative min-w-0 flex-1 before:absolute before:left-1/2 before:top-[11px] before:h-px before:w-full last:before:hidden",
              lineDone ? "before:bg-emerald-500" : "before:bg-slate-200",
            )}
          >
            <button
              type="button"
              disabled={!clickable}
              onClick={() => onPick(s.n)}
              aria-current={s.current ? "step" : undefined}
              aria-label={`Step ${s.n}: ${s.title}${s.state === "done" ? " (complete)" : s.state === "error" ? " (failed)" : s.current ? " (current)" : ""}`}
              title={`${s.title}${s.durationMs != null ? ` · ${fmtDuration(s.durationMs)}` : ""}${s.reviewable ? " · click to review" : ""}`}
              className={cn(
                "relative z-10 flex w-full flex-col items-center gap-1 rounded-md py-0.5 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-indigo-500",
                clickable ? "cursor-pointer" : "cursor-default",
              )}
            >
              <span
                className={cn(
                  "flex h-[22px] w-[22px] items-center justify-center rounded-full text-[11px] font-semibold tabular-nums transition-colors",
                  circle,
                  isViewing && "ring-4 ring-emerald-200",
                  s.state === "active" && "hz-pulse",
                )}
              >
                {s.state === "done" ? <Check size={12} strokeWidth={3} aria-hidden="true" /> : s.state === "error" ? <X size={12} strokeWidth={3} aria-hidden="true" /> : s.n}
              </span>
              <span
                className={cn(
                  "hidden max-w-full truncate text-[11px] leading-tight @2xl:block",
                  s.state === "active" ? "font-semibold text-slate-900" : s.state === "done" ? "text-slate-700" : s.state === "error" ? "font-semibold text-red-700" : "text-slate-400",
                )}
              >
                {s.short}
              </span>
            </button>
          </li>
        );
      })}
    </ol>
  );
}

/** Run details: which engine did the row work, and how long each finished step took. */
function RunDetails({ engine, steps }: { engine: string | null; steps: WizardStep[] }) {
  const timed = steps.filter((s) => s.durationMs != null);
  return (
    <details className="border-t border-slate-200 px-4 py-2.5 text-[12px] text-slate-600">
      <summary className="cursor-pointer select-none text-[11.5px] font-medium text-slate-500 hover:text-slate-700">
        Run details
      </summary>
      <dl className="mt-2 grid grid-cols-[auto_1fr] gap-x-4 gap-y-1">
        <dt className="text-slate-500">Engine</dt>
        <dd className="text-slate-800">{engine === "go" ? "hoist-engine (Go)" : "Python"}</dd>
        {timed.map((s) => (
          <div key={s.n} className="contents">
            <dt className="text-slate-500">
              {s.n}. {s.title}
            </dt>
            <dd className="tabular-nums text-slate-800">{fmtDuration(s.durationMs)}</dd>
          </div>
        ))}
      </dl>
    </details>
  );
}

function MoreMenu({
  sessionId,
  versions,
  pickedVersionId,
  onPickVersion,
  onSaveVersion,
  savingVersion,
  onRestart,
  canRestart,
  onCancel,
  canCancel,
  onClose,
}: {
  sessionId?: string;
  versions: UdrRunVersion[];
  pickedVersionId: string;
  onPickVersion: (id: string) => void;
  onSaveVersion: () => void;
  savingVersion: boolean;
  onRestart: () => void;
  canRestart: boolean;
  onCancel: () => void;
  canCancel: boolean;
  onClose?: () => void;
}) {
  const ref = useRef<HTMLDetailsElement | null>(null);
  // The two actions that end or discard work ask once, inline, before they act.
  const [confirming, setConfirming] = useState<null | "restart" | "cancel">(null);
  const confirmRestart = confirming === "restart";
  const confirmCancel = confirming === "cancel";
  const setConfirmRestart = (on: boolean) => setConfirming(on ? "restart" : null);
  useEffect(() => {
    const onDoc = (e: MouseEvent) => {
      const el = ref.current;
      if (el?.open && e.target instanceof Node && !el.contains(e.target)) {
        el.open = false;
        setConfirming(null);
      }
    };
    const onKey = (e: KeyboardEvent) => {
      const el = ref.current;
      if (e.key === "Escape" && el?.open) {
        el.open = false;
        setConfirming(null);
      }
    };
    document.addEventListener("mousedown", onDoc);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDoc);
      document.removeEventListener("keydown", onKey);
    };
  }, []);
  const close = () => {
    if (ref.current) ref.current.open = false;
    setConfirming(null);
  };
  const item = "flex w-full items-center gap-2 rounded-md px-2.5 py-1.5 text-left text-[12px] text-slate-700 hover:bg-slate-100 disabled:cursor-default disabled:opacity-50 focus-visible:outline-2 focus-visible:outline-offset-[-2px] focus-visible:outline-indigo-500";
  return (
    <details ref={ref} className="relative">
      <summary
        className="flex cursor-pointer list-none items-center gap-1 rounded-md border border-slate-200 bg-white px-2 py-1 text-[11.5px] font-medium text-slate-700 hover:bg-slate-50 [&::-webkit-details-marker]:hidden focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-indigo-500"
        aria-label="More actions"
      >
        More <ChevronDown size={12} aria-hidden="true" />
      </summary>
      <div className="absolute right-0 z-30 mt-1 w-60 rounded-lg border border-slate-200 bg-white p-1 shadow-md">
        {sessionId ? (
          <>
            <label className="block px-2.5 pt-1.5 pb-1 text-[10.5px] font-medium text-slate-500">
              Showing
              <select
                value={pickedVersionId}
                onChange={(e) => { onPickVersion(e.target.value); close(); }}
                className="mt-1 block w-full rounded border border-slate-200 bg-white px-1.5 py-1 text-[12px] font-normal text-slate-800"
              >
                <option value="">Current run</option>
                {versions.map((v) => (
                  <option key={v.id} value={v.id}>
                    v{v.version_no}
                    {v.custom_name ? ` · ${v.custom_name}` : ""}
                  </option>
                ))}
              </select>
            </label>
            <button type="button" className={item} disabled={savingVersion} onClick={() => { onSaveVersion(); close(); }}>
              {savingVersion ? "Saving…" : "Save this run as a version"}
            </button>
            <div className="my-1 h-px bg-slate-100" />
          </>
        ) : null}
        {confirmRestart ? (
          <div className="rounded-md bg-amber-50 px-2.5 py-2 text-[11.5px] text-amber-800">
            <p className="font-medium">Discard every step and start again from step 1?</p>
            <div className="mt-1.5 flex gap-1.5">
              <button
                type="button"
                onClick={() => { onRestart(); close(); }}
                className="rounded-md bg-amber-600 px-2 py-1 text-[11.5px] font-medium text-white hover:bg-amber-700"
              >
                Restart
              </button>
              <button type="button" onClick={() => setConfirmRestart(false)} className="rounded-md border border-amber-200 bg-white px-2 py-1 text-[11.5px] font-medium text-amber-800 hover:bg-amber-100">
                Keep going
              </button>
            </div>
          </div>
        ) : (
          <button type="button" className={item} disabled={!canRestart} onClick={() => setConfirmRestart(true)}>
            <RotateCcw size={13} aria-hidden="true" /> Restart from step 1
          </button>
        )}
        {confirmCancel ? (
          <div className="rounded-md bg-red-50 px-2.5 py-2 text-[11.5px] text-red-800">
            <p className="font-medium">Stop this migration?</p>
            <p className="mt-0.5 text-red-700/90">The step already running finishes; nothing after it starts, and nothing more is written.</p>
            <div className="mt-1.5 flex gap-1.5">
              <button
                type="button"
                onClick={() => { onCancel(); close(); }}
                className="rounded-md bg-red-600 px-2 py-1 text-[11.5px] font-medium text-white hover:bg-red-700"
              >
                Cancel migration
              </button>
              <button type="button" onClick={() => setConfirming(null)} className="rounded-md border border-red-200 bg-white px-2 py-1 text-[11.5px] font-medium text-red-800 hover:bg-red-100">
                Keep going
              </button>
            </div>
          </div>
        ) : (
          <button type="button" className={item} disabled={!canCancel} onClick={() => setConfirming("cancel")}>
            <X size={13} aria-hidden="true" /> Cancel this migration
          </button>
        )}
        {onClose ? (
          <button type="button" className={item} onClick={() => { close(); onClose(); }}>
            <X size={13} aria-hidden="true" /> Close this card
          </button>
        ) : null}
      </div>
    </details>
  );
}

function DoneStepPage({
  step,
  content,
  migration,
  routingOpen,
  onBack,
}: {
  step: WizardStep;
  content: WizardContentState | null;
  migration: MigrationStatusResponse | null;
  /** The table-routing gate is open: the analysis shows only what was decided before it. */
  routingOpen: boolean;
  onBack: () => void;
}) {
  const snap = content?.snapshots.find((s) => s.nodeId === step.n) ?? null;
  const dur = fmtDuration(step.durationMs);
  const nodes = migration?.nodes ?? [];
  let body: React.ReactNode = null;
  if (step.n === 2) {
    body = content?.udrTable ? (
      <TableResolutionPanels report={content.udrTable} phase={routingOpen ? "pk" : undefined} routingConfirmed={!routingOpen} />
    ) : null;
  } else if (step.n === 3) {
    const any = !!snap || !!content?.preSemantic.length || !!content?.udrColumn;
    body = any ? (
      <div className="space-y-4">
        {snap ? <MigrationStepSnapshot stepKey={snap.stepKey} payload={snap.payload} allNodes={nodes} /> : null}
        {content?.preSemantic.length ? <CenterPreSemanticHistory snapshots={content.preSemantic} /> : null}
        {content?.udrColumn ? <ColumnIntelligencePanels report={content.udrColumn} /> : null}
      </div>
    ) : null;
  } else if (snap) {
    body = (
      <MigrationStepSnapshot
        stepKey={snap.stepKey}
        payload={snap.payload}
        allNodes={nodes}
        fieldMappingDraft={migration?.field_mapping_draft}
      />
    );
  }
  return (
    <div>
      <div className="mb-4 flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="text-[11px] text-slate-500">
            Step {step.n} of 10 · finished{dur ? ` in ${dur}` : ""}
          </p>
          <h2 className="text-[15px] font-semibold leading-snug text-slate-900">{step.title}</h2>
        </div>
        <button
          type="button"
          onClick={onBack}
          className="inline-flex shrink-0 items-center gap-1.5 rounded-md border border-slate-200 bg-white px-2.5 py-1.5 text-[12px] font-medium text-slate-700 hover:bg-slate-50 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-indigo-500"
        >
          <ArrowLeft size={13} aria-hidden="true" /> Back to the current step
        </button>
      </div>
      {body ?? (
        <div className="rounded-xl border border-slate-200 bg-slate-50 px-4 py-6 text-center">
          <p className="text-[13px] font-medium text-slate-700">This step finished{dur ? ` in ${dur}` : ""}.</p>
          <p className="mt-1 text-[12px] text-slate-500">Its output wasn't kept in this browser, so there is nothing more to show here.</p>
        </div>
      )}
    </div>
  );
}
