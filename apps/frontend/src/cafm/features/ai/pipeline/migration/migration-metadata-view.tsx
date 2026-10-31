"use client";

/**
 * Document summary (#10) + full table/column metadata (#9) views.
 *
 * Reads the `document_inventory` and `tables_metadata` blocks the migration gate
 * payloads now carry (built by svc-ai-schema-mapper/src/graph/migration_metadata.py).
 * Both are read-only and degrade to nothing when the data is absent.
 */
import { Children, useEffect, useRef, useState } from "react";
import { ChevronDown, ChevronRight, Database, FileText } from "lucide-react";

import type { ColumnScoreBreakdown, MigrationPkConfirmation, UdrColumnIntelligence, UdrDuplicateTableGroup, UdrTableResolution } from "../../chat-api";
import { plainAliasSource, plainMethodLabel, plainPkList } from "./migration-mapping-utils";

export type DocInventoryTable = { table_name: string; row_count: number; column_count: number };
export type DocInventoryFile = {
  file_name: string;
  file_type: string;
  sheet_count: number;
  table_count: number;
  tables: DocInventoryTable[];
};
export type DocumentInventory = { files: DocInventoryFile[] };

export type TableColumnMeta = {
  column_name: string;
  datatype: string;
  nullable: boolean;
  unique: boolean;
  sample_values: string[];
};
export type TableMeta = {
  table_name: string;
  row_count: number;
  source_file: string;
  primary_keys: string[];
  /** 7.3 AC5 — the PK is a multi-column composite. */
  composite_primary_key?: boolean;
  /** 7.3 AC6 — no natural PK found; a surrogate key was generated. */
  surrogate_key?: boolean;
  foreign_keys: string[];
  column_count: number;
  columns: TableColumnMeta[];
};

/** B7.1 — per-table card grid (name · PK · col count · 3 sample values) shown
 *  above "Step 1 — Confirm table routing" so the user can see what the unique-
 *  table identification step produced before routing each sheet. */
export function UniqueTablesPanelB7({ tables }: { tables: TableMeta[] }) {
  if (!tables.length) return null;
  return (
    <div className="mb-4 rounded-xl border border-slate-200 bg-white shadow-sm overflow-hidden">
      <div className="flex items-center gap-2 px-5 py-2.5 border-b border-slate-100">
        <span className="font-mono text-[10px] text-slate-400">B7.1</span>
        <span className="text-sm font-semibold text-slate-700">Unique table identification</span>
        <span className="ml-1 inline-flex items-center px-1.5 py-0.5 rounded text-[10px] font-medium bg-purple-50 text-purple-700">
          Analysis
        </span>
        <span className="ml-auto text-[11px] text-slate-400">
          {tables.length} table{tables.length === 1 ? "" : "s"} · name + metadata similarity
        </span>
      </div>
      <div className="px-4 py-3 grid grid-cols-2 md:grid-cols-3 lg:grid-cols-5 gap-3">
        {tables.map((t) => (
          <div key={t.table_name} className="rounded-lg border border-slate-200 p-3 min-w-0">
            <div className="font-mono text-[12.5px] font-medium text-slate-800 truncate">
              {t.table_name}
            </div>
            {/* PK/FK are intentionally NOT shown here — unique-table identification runs
                BEFORE primary-key detection + confirmation. PK appears at the B8.1 /
                Primary-key confirmation step; FK at hierarchy detection. */}
            <div className="text-[11px] text-slate-500 mt-0.5">
              <span className="font-medium text-slate-700">cols</span> {t.column_count}
            </div>
            <ul className="mt-1.5 space-y-0.5">
              {t.columns.slice(0, 3).map((c) => (
                <li key={c.column_name} className="text-[10.5px] text-slate-500">
                  <span className="font-mono text-slate-700">{c.column_name}</span>{" "}
                  <span className="text-slate-400 truncate">
                    {c.sample_values.slice(0, 3).join(", ")}
                  </span>
                </li>
              ))}
            </ul>
          </div>
        ))}
      </div>
    </div>
  );
}

/** B8.1 — primary-key detection per table: Kind · PK · Uniqueness · Null-rate ·
 *  Tie-break. Kind is "natural" when at least one column passes the null+unique
 *  gate (no composite/surrogate fallback was needed in that run). */
export function PrimaryKeyDetectionPanelB8({ tables }: { tables: TableMeta[] }) {
  if (!tables.length) return null;
  return (
    <div className="mb-4 rounded-xl border border-slate-200 bg-white shadow-sm overflow-hidden">
      <div className="flex items-center gap-2 px-5 py-2.5 border-b border-slate-100">
        <span className="font-mono text-[10px] text-slate-400">B8.1</span>
        <span className="text-sm font-semibold text-slate-700">Primary-key detection</span>
        <span className="ml-1 inline-flex items-center px-1.5 py-0.5 rounded text-[10px] font-medium bg-purple-50 text-purple-700">
          Analysis
        </span>
        <span className="ml-auto text-[11px] text-slate-400">
          null + unique tests · per table, no cross-table comparison
        </span>
      </div>
      <div className="overflow-x-auto">
        <table className="w-full text-[11.5px]">
          <thead className="bg-slate-50 text-slate-500">
            <tr>
              <th className="px-4 py-2 text-left font-medium">Table</th>
              <th className="px-4 py-2 text-left font-medium">Kind</th>
              <th className="px-4 py-2 text-left font-medium">PK</th>
              <th className="px-4 py-2 text-left font-medium">Uniqueness</th>
              <th className="px-4 py-2 text-left font-medium">Null-rate</th>
              <th className="px-4 py-2 text-left font-medium">Tie-break</th>
            </tr>
          </thead>
          <tbody>
            {tables.map((t) => {
              const pk = t.primary_keys[0];
              const pkCol = t.columns.find((c) => c.column_name === pk);
              const hasPk = !!pkCol;
              const candidates = t.columns.filter((c) => c.unique && !c.nullable);
              const tieBreak = !hasPk
                ? "surrogate key"
                : candidates.length > 1
                  ? pk && /(^|_)id$/i.test(pk)
                    ? "name rank *_id"
                    : pk && /(code|key|no|number)$/i.test(pk)
                      ? "name rank code/key"
                      : "first qualified"
                  : "only candidate";
              return (
                <tr key={t.table_name} className="border-t border-slate-100">
                  <td className="px-4 py-2 font-mono text-slate-700">{t.table_name}</td>
                  <td className="px-4 py-2">
                    <span className="inline-flex items-center px-1.5 py-0.5 rounded text-[10px] font-medium bg-blue-50 text-blue-700 font-mono">
                      {hasPk ? "natural" : "surrogate"}
                    </span>
                  </td>
                  <td className="px-4 py-2 font-mono text-slate-700">{pk || "surrogate key"}</td>
                  <td className="px-4 py-2 font-mono text-slate-500">{hasPk ? "1.00" : "—"}</td>
                  <td className="px-4 py-2 font-mono text-slate-500">{hasPk ? "0.00" : "—"}</td>
                  <td className="px-4 py-2 text-slate-500">{tieBreak}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}

/** B9.1 / B10.1 / B11.1 — final per-source-table routing decision (Method ·
 *  Destination · Confidence). Method is inferred from confidence: 1.0 = exact
 *  name match (deterministic), <1.0 with target = semantic search (LLM/RAG),
 *  no target = unmatched (next stage). Reads payload values the backend
 *  already produces in pre_semantic_review_node.py. */
export type TableRouting = {
  source: string;
  target: string | null;
  confidence: number;
};
export function TableMappingDecisionsPanel({ rows }: { rows: TableRouting[] }) {
  if (!rows.length) return null;
  return (
    <div className="mb-4 rounded-xl border border-slate-200 bg-white shadow-sm overflow-hidden">
      <div className="flex items-center gap-2 px-5 py-2.5 border-b border-slate-100">
        <span className="font-mono text-[10px] text-slate-400">B9.1 · B10.1 · B11.1</span>
        <span className="text-sm font-semibold text-slate-700">Table mapping — final decisions</span>
        <span className="ml-1 inline-flex items-center px-1.5 py-0.5 rounded text-[10px] font-medium bg-blue-50 text-blue-700">
          Action
        </span>
        <span className="ml-auto text-[11px] text-slate-400">
          deterministic → alias match → AI match
        </span>
      </div>
      <div className="overflow-x-auto">
        <table className="w-full text-[11.5px]">
          <thead className="bg-slate-50 text-slate-500">
            <tr>
              <th className="px-4 py-2 text-left font-medium">Source</th>
              <th className="px-4 py-2 text-left font-medium">Destination</th>
              <th className="px-4 py-2 text-left font-medium">Method</th>
              <th className="px-4 py-2 text-left font-medium">Confidence</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => {
              const hasTarget = !!r.target;
              const conf = r.confidence;
              const method = !hasTarget
                ? { label: "unmatched", tone: "bg-slate-100 text-slate-600" }
                : conf >= 0.999
                  ? { label: "exact name", tone: "bg-emerald-100 text-emerald-700" }
                  : conf >= 0.95
                    ? { label: "Alias match", tone: "bg-indigo-100 text-indigo-700" }
                    : { label: "AI match", tone: "bg-amber-100 text-amber-700" };
              return (
                <tr key={r.source} className="border-t border-slate-100">
                  <td className="px-4 py-2 font-mono text-slate-700">{r.source}</td>
                  <td className="px-4 py-2 font-mono text-slate-700">{r.target ?? "—"}</td>
                  <td className="px-4 py-2">
                    <span className={`inline-flex items-center px-2 py-0.5 rounded-full text-[10px] font-medium font-mono ${method.tone}`}>
                      {method.label}
                    </span>
                  </td>
                  <td className="px-4 py-2 font-mono text-slate-500">
                    {hasTarget ? `${Math.round(conf * 100)}%` : "—"}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}

// ── B7.1 → B12.1 — the full table-resolution step panels, rendered from the backend
// `table_resolution` report so each step shows complete dynamic detail (matching the
// Migration-Analysis sample): unique table identification → PK detection → deterministic →
// RAG/alias → semantic table mapping → final decisions. ──────────────────────────────────

function _two(x: number | null | undefined): string {
  return typeof x === "number" ? x.toFixed(2) : "—";
}
function _pctText(x: number | null | undefined): string {
  return typeof x === "number" ? `${Math.round(x * 100)}%` : "—";
}
function _confTone(x: number | null | undefined): string {
  if (typeof x !== "number") return "text-slate-500";
  return x >= 0.9 ? "text-emerald-700" : x >= 0.7 ? "text-amber-700" : "text-red-700";
}
function _methodTone(method: string): string {
  const m = method.toLowerCase();
  if (/(exact|levenshtein)/.test(m)) return "bg-emerald-100 text-emerald-700";
  if (/(rag|alias)/.test(m)) return "bg-indigo-100 text-indigo-700";
  if (/(semantic|suggest)/.test(m)) return "bg-amber-100 text-amber-700";
  return "bg-slate-100 text-slate-500";
}

// Sub-step numbers within Step 2 (Table & Column Analysis) so the cards read as an ordered
// 2.1, 2.2, … list — matching the right-rail activity-log stages 1:1 (the parent step is 2).
const ANALYSIS_SUBSTEP: Record<string, string> = {
  "B6.1": "2.0",
  "B7.1": "2.1",
  // Column merge sits between unique-table id and PK detection — the PK is picked from the
  // merged columns, so it has to be visible first.
  "B7.2": "2.1b",
  "B8.1": "2.2",
  "B9.1": "2.3",
  "B10.1": "2.4",
  "B11.1": "2.5",
  // Confirmed routing summary — the conclusion of the table-mapping section, right after B11.1.
  "B12.1": "2.5b",
  "B13.1": "2.6",
  "B17.1 · B18.1": "2.7",
  "B17.1": "2.7",
  "B19.1": "2.8",
  "B20.1": "2.9",
  "B14.1 · B15.1": "2.11",
};

function SubStepBadge({ code }: { code: string }) {
  const n = ANALYSIS_SUBSTEP[code];
  if (!n) return null;
  return (
    <span className="flex h-4 items-center justify-center rounded-full bg-indigo-600 px-1.5 text-[9px] font-bold text-white shrink-0">
      {n}
    </span>
  );
}

function StepCard({
  code,
  title,
  tone,
  meta,
  stage,
  children,
}: {
  code: string;
  title: string;
  tone: "CoT" | "CoA";
  meta?: string;
  /** Cross-panel stage link key (matches the Activity-Log `data-stage`) — scrolling this
   *  card into view reveals its matching log entry on the right. See pipeline-stage-link.ts. */
  stage?: string;
  children: React.ReactNode;
}) {
  return (
    <div data-pipeline-stage={stage} className="mb-4 rounded-xl border border-slate-200 bg-white shadow-sm overflow-hidden">
      <div className="flex items-center gap-2 px-5 py-2.5 border-b border-slate-100">
        <SubStepBadge code={code} />
        <span className="font-mono text-[10px] text-slate-400">{code}</span>
        <span className="text-sm font-semibold text-slate-700">{title}</span>
        <span
          className={`ml-1 inline-flex items-center px-1.5 py-0.5 rounded text-[10px] font-medium ${
            tone === "CoT" ? "bg-purple-50 text-purple-700" : "bg-blue-50 text-blue-700"
          }`}
        >
          {tone === "CoT" ? "Analysis" : "Action"}
        </span>
        {meta ? <span className="ml-auto text-[11px] text-slate-400">{meta}</span> : null}
      </div>
      {children}
    </div>
  );
}

function StepTable({ head, rows }: { head: string[]; rows: React.ReactNode[][] }) {
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-[11.5px]">
        <thead className="bg-slate-50 text-slate-500">
          <tr>
            {head.map((h) => (
              <th key={h} className="px-4 py-2 text-left font-medium">
                {h}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((r, i) => (
            <tr key={i} className="border-t border-slate-100">
              {r.map((cell, ci) => (
                <td key={ci} className="px-4 py-2 align-top">
                  {cell}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/**
 * Reveals its children ONE STEP AT A TIME, top → bottom (B7.1, then B8.1, …).
 *
 * The table-resolution report arrives in a single shot — the backend only builds
 * it when the pipeline pauses at the pre-semantic gate (pre_semantic_review_node),
 * so the whole B7.1→B12.1 block otherwise pops in at once, well AFTER the right-
 * rail Activity Log has already streamed those same Layer-1 substeps one by one.
 * Cascading the reveal here makes the left panel "load in sequence" to match the
 * right-rail feed instead of appearing as a single late block.
 *
 * Purely presentational: it never hides data permanently — every child is shown
 * within (childCount × stepMs), and `visible` only ever grows, so a re-render
 * (e.g. the 2s status poll) never re-hides an already-revealed step.
 */
/** Gap between analysis cards appearing. Slower than the component default so the reveal reads as
 *  a legible "one card at a time" sequence instead of the whole block arriving at once. */
const ANALYSIS_REVEAL_MS = 700;

function SequentialReveal({
  children,
  stepMs = 450,
  manualApprove = false,
  onComplete,
}: {
  children: React.ReactNode;
  stepMs?: number;
  /** HITL mode — each step reveals only after the user clicks "Approve & continue", instead of
   *  auto-advancing on a timer. Lets the user approve each analysis stage before the next loads. */
  manualApprove?: boolean;
  /** Fired once every step has been revealed/approved (used to unlock the routing step). */
  onComplete?: () => void;
}) {
  const items = Children.toArray(children).filter(Boolean);
  const [visible, setVisible] = useState(items.length ? 1 : 0);

  // Only start the cascade once these cards are actually ON SCREEN. `visible` only ever grows, and
  // the analysis block is latched the moment its payload lands — so the reveal used to run (and
  // finish) while the user was still watching the spinner or reading further up the page. Arriving
  // afterwards, every card was already shown, which reads as "loaded all at once". Gating the
  // timer on visibility guarantees the sequence happens while the user is looking at it.
  const hostRef = useRef<HTMLDivElement | null>(null);
  const [started, setStarted] = useState(false);
  useEffect(() => {
    if (started) return;
    const el = hostRef.current;
    if (!el) return;
    if (typeof IntersectionObserver === "undefined") {
      setStarted(true); // SSR / unsupported — fall back to the old always-on behaviour
      return;
    }
    const io = new IntersectionObserver(
      (entries) => {
        if (entries.some((e) => e.isIntersecting)) {
          setStarted(true);
          io.disconnect();
        }
      },
      { rootMargin: "0px 0px -10% 0px" },
    );
    io.observe(el);
    return () => io.disconnect();
  }, [started]);

  useEffect(() => {
    if (manualApprove || !started) return; // HITL: only the Approve button advances
    if (visible >= items.length) return;
    const t = setTimeout(() => setVisible((v) => Math.min(v + 1, items.length)), stepMs);
    return () => clearTimeout(t);
  }, [visible, items.length, stepMs, manualApprove, started]);
  const done = items.length > 0 && visible >= items.length;
  // onComplete is idempotent (sets an approval flag) — fire once when completion flips true.
  const onCompleteRef = useRef(onComplete);
  onCompleteRef.current = onComplete;
  useEffect(() => {
    if (done) onCompleteRef.current?.();
  }, [done]);
  const remaining = items.length - visible;
  return (
    <div ref={hostRef}>
      {items.slice(0, visible).map((child, i) => (
        <div key={i} className="animate-in fade-in slide-in-from-bottom-2 duration-300">
          {child}
        </div>
      ))}
      {manualApprove && !done ? (
        <div className="mb-4 rounded-xl border border-amber-300 bg-amber-50 px-5 py-4">
          <div className="text-sm font-semibold text-amber-900">Approve this step to continue</div>
          <div className="mt-0.5 text-xs text-amber-700">
            Review the step above, then approve to load the next.
            {remaining > 1 ? ` ${remaining - 1} more after this.` : ""}
          </div>
          <button
            type="button"
            onClick={() => setVisible((v) => Math.min(v + 1, items.length))}
            className="mt-3 inline-flex items-center gap-2 rounded-lg bg-amber-600 px-5 py-2 text-sm font-medium text-white transition-colors hover:bg-amber-700"
          >
            ✓ Approve &amp; continue
          </button>
        </div>
      ) : null}
    </div>
  );
}

/**
 * Editable B8.1 — the HITL primary-key confirmation surface, rendered IN the
 * Primary-key detection step. The user can select / confirm the PK, remove a
 * column when the table has a composite (multi-column) PK via the × on each chip,
 * add a column, or switch to a surrogate key. Changed rows and non-unique picks
 * are colour-marked (amber), qualifying columns green.
 */
export function EditablePkDetection({
  pkConfirmation,
  value,
  onChange,
  duplicateGroups,
}: {
  pkConfirmation: Record<string, MigrationPkConfirmation>;
  value: Record<string, string[]>;
  onChange: (table: string, cols: string[]) => void;
  /** Duplicate table groups (e.g. work_order + workorders share wo_id) — collapsed to ONE row
   *  so the same-entity tables show the same PK info; edits apply to every member. */
  duplicateGroups?: UdrDuplicateTableGroup[];
}) {
  const allTables = Object.keys(pkConfirmation);
  if (!allTables.length) return null;

  // Collapse duplicate-group members into a single row (representative = first member present),
  // so work_order + workorders appear once. Editing the row's PK applies to every member.
  const groupByTable = new Map<string, UdrDuplicateTableGroup>();
  for (const g of duplicateGroups ?? []) for (const t of g.tables) groupByTable.set(t, g);
  const seen = new Set<string>();
  const rows: Array<{ key: string; label: string; members: string[] }> = [];
  for (const t of allTables) {
    const g = groupByTable.get(t);
    if (g) {
      const gkey = g.tables.join("|");
      if (seen.has(gkey)) continue;
      seen.add(gkey);
      const rep = g.tables.find((x) => pkConfirmation[x]) ?? t;
      rows.push({ key: rep, label: g.label || rep, members: g.tables });
    } else {
      rows.push({ key: t, label: t, members: [t] });
    }
  }
  return (
    <StepCard
      code="B8.1"
      title="Primary-key detection"
      tone="CoT"
      stage="unique_tables"
      meta="select · confirm · remove — approved before hierarchy / FK detection"
    >
      <div className="px-5 pt-2 text-[11px] text-amber-700">
        Confirm each table&rsquo;s primary key. Add or remove columns for a composite key (× on a
        chip), or choose a surrogate key. Changes are highlighted below.
      </div>
      <div className="overflow-x-auto">
        <table className="w-full text-[11.5px]">
          <thead className="bg-slate-50 text-slate-500">
            <tr>
              <th className="px-4 py-2 text-left font-medium">Table</th>
              <th className="px-4 py-2 text-left font-medium">Kind</th>
              <th className="px-4 py-2 text-left font-medium">Primary key</th>
              <th className="px-4 py-2 text-left font-medium">Uniqueness</th>
              <th className="px-4 py-2 text-left font-medium">Null-rate</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => {
              const tbl = row.key;
              const isGroup = row.members.length > 1;
              // Editing a duplicate group applies the same PK to every member table.
              const change = (cols: string[]) => {
                for (const m of row.members) onChange(m, cols);
              };
              const info = pkConfirmation[tbl];
              const cols = info?.columns ?? [];
              const selected = value[tbl] ?? [];
              const detected = (info?.detected_pk ?? []).filter((c) => c && c !== "_udr_id");
              const changed =
                selected.length !== detected.length || selected.some((c, i) => c !== detected[i]);
              const kind =
                selected.length === 0 ? "surrogate" : selected.length > 1 ? "composite" : "natural";
              const statOf = (c: string) => cols.find((x) => x.column === c);
              // Conservative aggregate across the selected PK columns: min uniqueness, max null.
              const uniq =
                selected.length === 0 ? 1 : Math.min(...selected.map((c) => statOf(c)?.uniqueness ?? 0));
              const nullr =
                selected.length === 0 ? 0 : Math.max(...selected.map((c) => statOf(c)?.null_rate ?? 0));
              const invalid = selected.length > 0 && (uniq < 1 || nullr > 0);
              const available = cols.map((c) => c.column).filter((c) => !selected.includes(c));
              return (
                <tr
                  key={row.members.join("|")}
                  className={`border-t border-slate-100 align-top ${changed ? "bg-amber-50/60" : isGroup ? "bg-amber-50/30" : ""}`}
                >
                  <td className="px-4 py-2">
                    <div className="flex items-center gap-1.5">
                      <span className="font-mono text-slate-700">{row.label}</span>
                      {isGroup ? (
                        <span
                          className="shrink-0 rounded-full bg-amber-500 px-1.5 py-0.5 text-[9px] font-bold text-white"
                          title={`duplicate of: ${row.members.join(", ")}`}
                        >
                          ×{row.members.length}
                        </span>
                      ) : null}
                    </div>
                    {isGroup ? (
                      <div className="mt-0.5 text-[9.5px] text-amber-700 truncate" title={row.members.join(", ")}>
                        {row.members.join(" · ")}
                      </div>
                    ) : null}
                  </td>
                  <td className="px-4 py-2">
                    <span
                      className={`inline-flex items-center px-1.5 py-0.5 rounded text-[10px] font-medium font-mono ${
                        kind === "surrogate"
                          ? "bg-blue-50 text-blue-700"
                          : kind === "composite"
                            ? "bg-purple-50 text-purple-700"
                            : "bg-emerald-50 text-emerald-700"
                      }`}
                    >
                      {kind}
                    </span>
                  </td>
                  <td className="px-4 py-2">
                    <div className="flex flex-wrap items-center gap-1.5">
                      {selected.length === 0 ? (
                        <span className="inline-flex items-center rounded-md bg-blue-100 px-1.5 py-0.5 text-[10.5px] font-mono text-blue-700">
                          surrogate key
                        </span>
                      ) : (
                        selected.map((c) => {
                          const q = statOf(c)?.qualifies;
                          return (
                            <span
                              key={c}
                              className={`inline-flex items-center gap-1 rounded-md px-1.5 py-0.5 text-[10.5px] font-mono border ${
                                q
                                  ? "bg-emerald-100 text-emerald-800 border-emerald-300"
                                  : "bg-amber-100 text-amber-800 border-amber-300"
                              }`}
                              title={q ? "unique · non-null" : "not unique / has nulls"}
                            >
                              {c}
                              <button
                                type="button"
                                onClick={() => change(selected.filter((x) => x !== c))}
                                className="ml-0.5 font-bold hover:text-red-600"
                                aria-label={`Remove ${c} from primary key`}
                              >
                                ×
                              </button>
                            </span>
                          );
                        })
                      )}
                      {available.length ? (
                        <select
                          value=""
                          onChange={(e) => {
                            const c = e.target.value;
                            if (c) change([...selected, c]);
                          }}
                          className="rounded-md border border-slate-300 bg-white px-1.5 py-0.5 text-[10.5px] font-mono text-slate-600 focus:border-indigo-400 focus:outline-none"
                        >
                          <option value="">+ add column…</option>
                          {available.map((c) => (
                            <option key={c} value={c}>
                              {c}
                              {statOf(c)?.qualifies ? " ✓" : ""}
                            </option>
                          ))}
                        </select>
                      ) : null}
                      {selected.length ? (
                        <button
                          type="button"
                          onClick={() => change([])}
                          className="rounded-md border border-slate-300 px-1.5 py-0.5 text-[10.5px] text-slate-500 hover:bg-slate-50"
                        >
                          use surrogate
                        </button>
                      ) : null}
                      {changed ? (
                        <span className="rounded-full bg-amber-100 px-1.5 py-0.5 text-[9.5px] font-medium text-amber-700">
                          changed
                        </span>
                      ) : null}
                    </div>
                    {invalid ? (
                      <div className="mt-1 text-[10.5px] text-amber-700">
                        Not unique / has nulls — won&rsquo;t enforce row identity.
                      </div>
                    ) : null}
                  </td>
                  <td className="px-4 py-2 font-mono text-slate-500">{uniq.toFixed(2)}</td>
                  <td className="px-4 py-2 font-mono text-slate-500">{nullr.toFixed(2)}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </StepCard>
  );
}

/** Collapse a list of ``{source}`` rows so all members of a duplicate group appear ONCE
 *  (representative row), keeping the table-mapping flow (B9.1/B10.1/B11.1/B12.1) consistent
 *  with the collapsed B7.1/B8.1 — work_order + workorders → a single "work_order ×2" row. */
function collapseBySource<T extends { source?: string }>(
  list: T[],
  groupByTable: Map<string, UdrDuplicateTableGroup>,
): Array<{ item: T; label: string; group?: UdrDuplicateTableGroup }> {
  const seen = new Set<string>();
  const out: Array<{ item: T; label: string; group?: UdrDuplicateTableGroup }> = [];
  for (const item of list) {
    const src = item.source ?? "";
    const g = src ? groupByTable.get(src) : undefined;
    if (g) {
      const key = g.tables.join("|");
      if (seen.has(key)) continue;
      seen.add(key);
      out.push({ item, label: g.label || src, group: g });
    } else {
      out.push({ item, label: src });
    }
  }
  return out;
}

/** Source-table cell that shows a duplicate group's representative label + ×count badge. */
function DupSourceCell({ label, group }: { label: string; group?: UdrDuplicateTableGroup }) {
  return (
    <span className="inline-flex items-center gap-1.5">
      <span className="font-mono text-slate-700">{label}</span>
      {group ? (
        <span
          className="shrink-0 rounded-full bg-amber-500 px-1.5 py-0.5 text-[9px] font-bold text-white"
          title={`duplicate of: ${group.tables.join(", ")}`}
        >
          ×{group.count}
        </span>
      ) : null}
    </span>
  );
}

/** All five resolution steps (B7.1 → B11.1) + B12.1 final decisions, from the report. */
export type TableTopMatch = { table: string; pct: number; mapped?: number; total?: number };
export type TableTopMatches = Record<string, { exact: TableTopMatch[]; semantic: TableTopMatch[] }>;

/** B11.1 companion — per-source-table top-3 candidate destinations, split into the exact-name
 *  and semantic methods. Duplicate groups collapse to one entry. Read-only (informational). */
function TableTopMatchesSection({
  topMatches,
  dupGroups,
  show = "both",
}: {
  topMatches: TableTopMatches;
  dupGroups: UdrDuplicateTableGroup[];
  /** Which method rows to render — "semantic" only in the B11.1 semantic step, "exact" only in
   *  a deterministic step, or "both". */
  show?: "exact" | "semantic" | "both";
}) {
  const showExact = show !== "semantic";
  const showSemantic = show !== "exact";
  const groupByTable = new Map<string, UdrDuplicateTableGroup>();
  for (const g of dupGroups) for (const t of g.tables) groupByTable.set(t, g);
  const seen = new Set<string>();
  const rows: Array<{ key: string; label: string; group?: UdrDuplicateTableGroup }> = [];
  for (const t of Object.keys(topMatches)) {
    const g = groupByTable.get(t);
    if (g) {
      const gk = g.tables.join("|");
      if (seen.has(gk)) continue;
      seen.add(gk);
      rows.push({ key: t, label: g.label || t, group: g });
    } else {
      rows.push({ key: t, label: t });
    }
  }
  if (!rows.length) return null;
  const chip = (m: TableTopMatch) => {
    const p = Math.round(m.pct * 100);
    return (
      <span
        key={m.table}
        title={m.mapped != null ? `${m.mapped}/${m.total} columns fit` : undefined}
        className="inline-flex items-center gap-1 rounded-full border border-slate-200 bg-white px-2 py-0.5 text-[10px] font-mono text-slate-600"
      >
        {m.table}
        <span className={p >= 70 ? "text-green-600" : p >= 40 ? "text-amber-600" : "text-slate-400"}>{p}%</span>
      </span>
    );
  };
  return (
    <div className="border-t border-slate-100">
      <div className="px-5 py-2 text-[10px] font-semibold uppercase tracking-wider text-slate-500">
        {show === "semantic"
          ? "Top 3 semantic candidate tables (per source)"
          : show === "exact"
            ? "Top 3 deterministic candidate tables (per source)"
            : "Top 3 candidate tables (per source)"}
      </div>
      <div className="space-y-2.5 px-5 pb-3">
        {rows.map((row) => {
          const tm = topMatches[row.key];
          return (
            <div key={row.key}>
              <div className="mb-1 flex items-center gap-1.5">
                <span className="font-mono text-[11px] font-medium text-slate-700">{row.label}</span>
                {row.group ? (
                  <span className="rounded-full bg-amber-500 px-1.5 py-0.5 text-[9px] font-bold text-white">
                    ×{row.group.count}
                  </span>
                ) : null}
              </div>
              <div className="space-y-1">
                {showExact ? (
                  <div className="flex items-start gap-2 flex-wrap">
                    <span className="mt-0.5 w-48 shrink-0 text-[10px] text-slate-500">Deterministic exact matching</span>
                    <div className="flex flex-wrap gap-1.5">
                      {tm.exact.length ? tm.exact.map(chip) : <span className="text-[10px] text-slate-300">—</span>}
                    </div>
                  </div>
                ) : null}
                {showSemantic ? (
                  <div className="flex items-start gap-2 flex-wrap">
                    <span className="mt-0.5 w-48 shrink-0 text-[10px] text-slate-500">Semantic matching</span>
                    <div className="flex flex-wrap gap-1.5">
                      {tm.semantic.length ? tm.semantic.map(chip) : <span className="text-[10px] text-slate-300">—</span>}
                    </div>
                  </div>
                ) : null}
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}

export function TableResolutionPanels({
  report,
  pkConfirmation,
  pkOverrides,
  onPkChange,
  tableTopMatches,
  phase,
  routingConfirmed = true,
}: {
  report?: UdrTableResolution | null;
  /** B12.1 "Table routing — confirmed" is the RESULT of the routing gate, so it must not render
   *  while that gate is still asking the user to confirm. The gate passes false for Step 1 and
   *  true afterwards; read-only / history views leave it true. */
  routingConfirmed?: boolean;
  /** When these three are supplied (at the PK-approval gate), the B8.1 step renders the
   *  EDITABLE primary-key confirmation instead of the read-only detection table. */
  pkConfirmation?: Record<string, MigrationPkConfirmation>;
  pkOverrides?: Record<string, string[]>;
  onPkChange?: (table: string, cols: string[]) => void;
  /** Per-source-table top-3 candidate destinations (exact-name + semantic) — rendered in the
   *  B11.1 "Semantic table mapping" step. Supplied by the gate; absent in read-only views. */
  tableTopMatches?: TableTopMatches;
  /** Which analysis group to render:
   *   "pk"          — Group A (both): B6.1 duplicate · B7.1 unique tables · B8.1 primary key
   *   "pk_only"     — Group A step 1: B8.1 primary-key IDENTIFICATION (editable) only
   *   "unique_only" — Group A step 2: B6.1 duplicate · B7.1 unique tables (with confirmed PK) only
   *   "mapping"     — Group B: B9.1 exact · B10.1 RAG · B11.1 semantic
   *   undefined     — everything (read-only history / archived views). */
  phase?: "pk" | "pk_only" | "unique_only" | "mapping";
}) {
  if (!report) return null;
  const cards = report.metadata_cards ?? [];
  const pk = report.pk_detection ?? [];
  const det = report.deterministic ?? [];
  const rag = report.rag_alias ?? [];
  const sem = report.semantic ?? [];
  const final = report.final_decisions ?? [];
  const pair = report.pairwise?.highest_pair;
  const counts = report.counts ?? {};
  const dupGroups = report.duplicate_tables?.groups ?? [];
  const mergedColumns = report.merged_columns ?? [];
  if (!cards.length && !final.length) return null;

  // Group A is now TWO sequential gates: PK identification (B8.1, "pk_only") then unique-table
  // identification (B6.1/B7.1, "unique_only"). "pk" keeps both together (read-only history).
  const showUnique = phase === "pk" || phase === "unique_only" || phase === undefined; // B6.1 + B7.1
  const showPkDetection = phase === "pk" || phase === "pk_only" || phase === undefined; // B8.1
  const showMapping = phase === "mapping" || phase === undefined;
  // B7.1's unique-table cards show each table's confirmed PK at the SECOND gate (unique_only),
  // where the PK is already decided; hidden at the combined/history views to avoid pre-empting B8.1.
  const showPkInUniqueCards = phase === "unique_only";

  // B7.1 collapse — a duplicate group's members (e.g. work_order_1 + work_order_2) are shown as
  // ONE card labelled with the group name + ×count, instead of one card per source table.
  const groupByTable = new Map<string, (typeof dupGroups)[number]>();
  for (const g of dupGroups) for (const t of g.tables) groupByTable.set(t, g);
  const b7Seen = new Set<string>();
  const b7Cards: Array<{ card: (typeof cards)[number]; group?: (typeof dupGroups)[number] }> = [];
  for (const c of cards) {
    const g = c.table ? groupByTable.get(c.table) : undefined;
    if (g) {
      const key = g.tables.join("|");
      if (b7Seen.has(key)) continue; // one card per duplicate group
      b7Seen.add(key);
      b7Cards.push({ card: c, group: g });
    } else {
      b7Cards.push({ card: c });
    }
  }

  return (
    <SequentialReveal stepMs={ANALYSIS_REVEAL_MS}>
      {/* B8.1 — Primary-key detection FIRST. Editable HITL confirmation at the PK IDENTIFICATION
          gate (Group A step 1); read-only detection table in the combined / history views. Rendered
          ABOVE the duplicate/unique-table cards so PRIMARY KEY always appears before unique-table
          identification, matching the step order (confirm PK → then unique tables). */}
      {showPkDetection && pkConfirmation && onPkChange && Object.keys(pkConfirmation).length ? (
        <EditablePkDetection
          pkConfirmation={pkConfirmation}
          value={pkOverrides ?? {}}
          onChange={onPkChange}
          duplicateGroups={report.duplicate_tables?.groups}
        />
      ) : pk.length ? (
        // Read-only B8.1 — the CONFIRMED primary-key step stays visible as history in every later
        // view, including the unique-table gate (step 2), so the PK card doesn't vanish after it is
        // approved. (The unique-table cards also show the PK inline; this keeps the step card too.)
        <StepCard code="B8.1" title="Primary-key detection" tone="CoT" stage="unique_tables" meta="null + unique tests · per table">
          <StepTable
            head={["Table", "Kind", "PK", "Uniqueness", "Null-rate", "Tie-break"]}
            rows={(() => {
              const dupByTable = new Map<string, UdrDuplicateTableGroup>();
              for (const g of report.duplicate_tables?.groups ?? []) for (const t of g.tables) dupByTable.set(t, g);
              const seenDup = new Set<string>();
              const kept: Array<{ p: (typeof pk)[number]; label: string }> = [];
              for (const p of pk) {
                const g = dupByTable.get(p.table);
                if (g) {
                  const gkey = g.tables.join("|");
                  if (seenDup.has(gkey)) continue;
                  seenDup.add(gkey);
                  kept.push({ p, label: g.label || p.table });
                } else {
                  kept.push({ p, label: p.table });
                }
              }
              return kept.map(({ p, label }) => [
                <span className="font-mono text-slate-700">{label}</span>,
                <span className="inline-flex items-center px-1.5 py-0.5 rounded text-[10px] font-medium bg-blue-50 text-blue-700 font-mono">{p.kind}</span>,
                <span className="font-mono text-slate-700">{plainPkList(p.primary_key)}</span>,
                <span className="font-mono text-slate-500">{_two(p.uniqueness)}</span>,
                <span className="font-mono text-slate-500">{_two(p.null_rate)}</span>,
                <span className="text-slate-500">{p.tie_break}</span>,
              ]);
            })()}
          />
        </StepCard>
      ) : null}

      {/* B6.1 — Duplicate table detection (runs BEFORE unique-table identification): tables
          that share similar columns are flagged as duplicates, with the group count (e.g. 2). */}
      {showUnique && cards.length ? (
        <StepCard
          code="B6.1"
          title="Duplicate table detection"
          tone="CoT"
          stage="unique_tables"
          meta={
            dupGroups.length
              ? `${dupGroups.length} duplicate group(s) · by shared columns`
              : "no duplicates · by shared columns"
          }
        >
          {dupGroups.length ? (
            <div className="px-4 py-3 space-y-2">
              {dupGroups.map((g, i) => (
                <div key={i} className="rounded-lg border border-amber-200 bg-amber-50/50 p-3">
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="rounded-full bg-amber-500 px-2 py-0.5 text-[11px] font-bold text-white">
                      duplicate ×{g.count}
                    </span>
                    {g.tables.map((t) => (
                      <span
                        key={t}
                        className="rounded-md border border-amber-300 bg-white px-1.5 py-0.5 font-mono text-[11px] text-amber-800"
                      >
                        {t}
                      </span>
                    ))}
                    <span className="ml-auto text-[11px] text-slate-500 tabular-nums">
                      {Math.round((g.similarity ?? 0) * 100)}% column overlap
                    </span>
                  </div>
                  {g.shared_columns.length ? (
                    <div className="mt-2 flex flex-wrap gap-1">
                      <span className="text-[10.5px] text-slate-500">shared:</span>
                      {g.shared_columns.slice(0, 12).map((c) => (
                        <span
                          key={c}
                          className="rounded bg-slate-100 px-1.5 py-0.5 font-mono text-[10px] text-slate-600"
                        >
                          {c}
                        </span>
                      ))}
                      {g.shared_columns.length > 12 ? (
                        <span className="text-[10px] text-slate-400">
                          +{g.shared_columns.length - 12} more
                        </span>
                      ) : null}
                    </div>
                  ) : null}
                </div>
              ))}
            </div>
          ) : (
            <div className="px-5 py-3 text-[11px] text-slate-500">
              No duplicate tables — all {report.duplicate_tables?.checked ?? cards.length} tables
              have distinct columns.
            </div>
          )}
        </StepCard>
      ) : null}

      {/* B7.1 — Unique table identification (cards + pairwise verdict) */}
      {showUnique && cards.length ? (
        <StepCard
          code="B7.1"
          title="Unique table identification"
          tone="CoT"
          stage="unique_tables"
          meta={`${b7Cards.length} unique · ${counts.tables ?? cards.length} source table(s)${dupGroups.length ? ` · ${dupGroups.length} duplicate group(s)` : ""}`}
        >
          <div className="px-4 py-3 grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3">
            {b7Cards.map(({ card: c, group }) => (
              <div
                key={group ? group.tables.join("|") : c.table}
                className={`rounded-lg border p-3 min-w-0 ${group ? "border-amber-300 bg-amber-50/40" : "border-slate-200"}`}
              >
                <div className="flex items-center gap-1.5">
                  <span className="font-mono text-[12.5px] font-medium text-slate-800 truncate">
                    {group ? (group.label || c.table) : c.table}
                  </span>
                  {group ? (
                    <span
                      className="shrink-0 rounded-full bg-amber-500 px-1.5 py-0.5 text-[10px] font-bold text-white"
                      title={`${group.count} duplicate source tables merged`}
                    >
                      ×{group.count}
                    </span>
                  ) : null}
                </div>
                {/* For a duplicate group, show which source tables were merged. */}
                {group ? (
                  <div className="mt-0.5 text-[10px] text-amber-700 truncate" title={group.tables.join(", ")}>
                    {group.tables.join(" · ")}
                  </div>
                ) : null}
                {/* PK shown here at the SECOND gate (unique-table identification), where the
                    primary key has already been confirmed at the first gate. Hidden in the
                    combined/history view so it doesn't pre-empt the B8.1 confirmation step. */}
                {showPkInUniqueCards && (c.primary_key?.length ?? 0) > 0 ? (
                  <div className="mt-1 flex flex-wrap items-center gap-1">
                    <span className="rounded bg-emerald-100 px-1.5 py-0.5 text-[9.5px] font-bold text-emerald-700 border border-emerald-300">
                      PK
                    </span>
                    <span className="font-mono text-[10.5px] text-emerald-800">
                      {c.primary_key.join(" · ")}
                    </span>
                  </div>
                ) : null}
                <div className="text-[11px] text-slate-500 mt-0.5">
                  <span className="font-medium text-slate-700">cols</span> {c.column_count}
                </div>
                <ul className="mt-1.5 space-y-0.5 max-h-44 overflow-y-auto pr-0.5">
                  {(c.samples ?? []).map((s) => (
                    <li key={s.column} className="text-[10.5px] text-slate-500 truncate">
                      <span className="font-mono text-slate-700">{s.column}</span>{" "}
                      <span className="text-slate-400">{(s.values ?? []).slice(0, 3).join(", ")}</span>
                    </li>
                  ))}
                </ul>
              </div>
            ))}
          </div>
          {dupGroups.length ? (
            <div className="px-5 pb-2 -mt-1">
              <p className="rounded-md bg-amber-50 px-3 py-2 text-[11px] text-amber-800">
                {dupGroups.map((g) => `${g.label || g.tables[0]} (×${g.count})`).join(", ")} merged from
                similar columns — shown as {b7Cards.length} unique table
                {b7Cards.length === 1 ? "" : "s"} instead of {cards.length}.
              </p>
            </div>
          ) : null}
          {report.pairwise?.verdict ? (
            <div className="px-5 pb-3 -mt-1">
              <p className="rounded-md bg-slate-50 px-3 py-2 text-[11px] text-slate-600">
                {pair ? (
                  <>
                    Highest pair: <span className="font-mono text-slate-700">{pair.table_a}</span> ↔{" "}
                    <span className="font-mono text-slate-700">{pair.table_b}</span> — meta {_two(pair.metadata_similarity)} · name {_two(pair.name_similarity)}.{" "}
                  </>
                ) : null}
                {report.pairwise.verdict}
              </p>
            </div>
          ) : null}
        </StepCard>
      ) : null}

      {/* B7.2 — identical columns merged to one name in Node 1. Shown with the UNIQUE-TABLE gate
          (step 2), NOT the primary-key gate: step 1 is purely the PK confirmation, and unique-table
          / column-structure cards follow only after the PK is approved. */}
      {showUnique && mergedColumns.length ? (
        <StepCard
          code="B7.2"
          title="Duplicate column merge"
          tone="CoT"
          stage="unique_tables"
          meta={`${mergedColumns.length} merge${mergedColumns.length === 1 ? "" : "s"} · ${mergedColumns.reduce((n, m) => n + (m.dropped?.length ?? 0), 0)} column(s) removed`}
        >
          <div className="px-4 py-3 space-y-2">
            {mergedColumns.map((m) => (
              <div
                key={`${m.table}.${m.kept}`}
                className="rounded-lg border border-emerald-200 bg-emerald-50/40 px-3 py-2"
              >
                <div className="flex flex-wrap items-center gap-2">
                  <span className="font-mono text-[11px] text-slate-500">{m.table}</span>
                  <span className="font-mono text-[12px] text-slate-400">
                    {(m.members ?? []).join(" = ")}
                  </span>
                  <span className="text-slate-300">→</span>
                  <span className="font-mono text-[12.5px] font-semibold text-emerald-800">
                    {m.kept}
                  </span>
                  <span className="ml-auto shrink-0 rounded-full bg-emerald-100 px-2 py-0.5 text-[10px] font-medium text-emerald-700">
                    {m.match_pct ?? 100}% identical
                  </span>
                </div>
                <div className="mt-1 text-[10.5px] text-emerald-800/80">
                  Values matched row-for-row across {m.row_count ?? 0} row
                  {m.row_count === 1 ? "" : "s"} — kept{" "}
                  <span className="font-mono">{m.kept}</span> (most specific name), removed{" "}
                  <span className="font-mono">{(m.dropped ?? []).join(", ")}</span>. Primary-key
                  detection below runs on the merged column.
                </div>
              </div>
            ))}
          </div>
        </StepCard>
      ) : null}

      {/* B9.1 — Deterministic exact matching (Group B). */}
      {showMapping && det.length ? (
        <StepCard code="B9.1" title="Deterministic exact matching" tone="CoA" stage="deterministic" meta="exact + Levenshtein ≤ 2 · auto-resolve ≥ 0.95">
          <StepTable
            head={["Source", "Method", "Destination", "Conf.", "Notes"]}
            rows={collapseBySource(det, groupByTable).map(({ item: d, label, group }) => [
              <DupSourceCell label={label} group={group} />,
              <span className={`inline-flex items-center px-2 py-0.5 rounded-full text-[10px] font-medium font-mono ${_methodTone(d.method)}`}>{plainMethodLabel(d.method)}</span>,
              d.destination ? <span className="font-mono text-slate-700">{d.destination}</span> : <span className="text-slate-400">—</span>,
              <span className={`font-mono ${_confTone(d.confidence)}`}>{_two(d.confidence)}</span>,
              <span className="text-slate-500">{d.note}</span>,
            ])}
          />
        </StepCard>
      ) : null}

      {/* B10.1 — Deterministic RAG based matching. Always shown as a distinct step (even when
          empty) so the deterministic exact (B9.1) and RAG (B10.1) stages read as two steps. */}
      {showMapping && (det.length || rag.length) ? (
        <StepCard code="B10.1" title="Known-term matching" tone="CoA" stage="deterministic" meta="facilities-management vocabulary + terms learned from earlier imports · auto-resolve ≥ 0.95">
          {rag.length ? (
            <>
              <StepTable
                head={["Source", "Alias hit", "Destination", "Conf.", "Source of alias"]}
                rows={collapseBySource(rag, groupByTable).map(({ item: d, label, group }) => [
                  <DupSourceCell label={label} group={group} />,
                  d.alias_hit ? (
                    <span className="inline-flex items-center px-2 py-0.5 rounded-full text-[10px] font-medium bg-emerald-100 text-emerald-700 font-mono">{d.alias_hit}</span>
                  ) : (
                    <span className="inline-flex items-center px-2 py-0.5 rounded-full text-[10px] font-medium bg-slate-100 text-slate-400 font-mono">none</span>
                  ),
                  d.destination ? <span className="font-mono text-slate-700">{d.destination}</span> : <span className="text-slate-400">—</span>,
                  <span className={`font-mono ${_confTone(d.confidence)}`}>{_two(d.confidence)}</span>,
                  <span className="text-slate-500">{plainAliasSource(d.alias_source)}</span>,
                ])}
              />
              <p className="px-5 py-2 text-[10.5px] text-slate-400">
                Tables resolved by an exact/Levenshtein name match in B9.1 are skipped here.
              </p>
            </>
          ) : (
            <p className="px-5 py-3 text-[11px] text-slate-400">
              No alias matches — all tables resolved by exact / Levenshtein name match in B9.1.
            </p>
          )}
        </StepCard>
      ) : null}

      {/* B11.1 — Semantic table mapping + top-3 candidates (Group B) */}
      {showMapping && (sem.length || final.length || (tableTopMatches && Object.keys(tableTopMatches).length)) ? (
        <StepCard code="B11.1" title="Semantic table mapping" tone="CoA" stage="semantic" meta="AI matcher · name + metadata signal · ≥ 70% suggested">
          {sem.length ? (
            <StepTable
              head={["Source", "Signal (columns)", "Destination", "Conf.", "Method"]}
              rows={collapseBySource(sem, groupByTable).map(({ item: d, label, group }) => [
                <DupSourceCell label={label} group={group} />,
                <span className="text-slate-500 truncate">{d.signal}</span>,
                d.destination ? <span className="font-mono text-slate-700">{d.destination}</span> : <span className="text-slate-400">—</span>,
                <span className={`font-mono ${_confTone(d.confidence)}`}>{_pctText(d.confidence)}</span>,
                <span className={`inline-flex items-center px-2 py-0.5 rounded-full text-[10px] font-medium ${d.band === "suggested" ? "bg-amber-100 text-amber-700" : "bg-slate-100 text-slate-500"}`}>
                  {d.band === "suggested" ? "suggested — confirm" : "review required"}
                </span>,
              ])}
            />
          ) : (
            <p className="px-5 py-2 text-[11px] text-slate-400">All tables resolved deterministically or by alias — no semantic step needed.</p>
          )}
          {/* Top-3 candidate destination tables per source (exact-name + semantic), requested
              in the B11.1 step. Rendered when the gate supplies the candidate scores. */}
          {tableTopMatches && Object.keys(tableTopMatches).length ? (
            <TableTopMatchesSection topMatches={tableTopMatches} dupGroups={dupGroups} show="semantic" />
          ) : null}
        </StepCard>
      ) : null}

      {/* B12.1 — Table routing CONFIRMED (final decision per sheet). Placed as the LAST table
          card — right after the per-method breakdown (B9.1 exact / B10.1 RAG / B11.1 semantic) and
          directly above the Column Intelligence Pipeline — so it reads as the CONCLUSION of table
          routing and is visible next to the semantic-mapping card the user is looking at (rather
          than buried at the top above everything). Duplicate sheets co-routed to the same table
          (work_order + workorders → work_orders) collapse to one row with a ×count. */}
      {showMapping && routingConfirmed && final.length ? (
        <StepCard code="B12.1" title="Table routing — confirmed" tone="CoA" stage="deterministic" meta="final destination per sheet · exact → alias → semantic">
          <StepTable
            head={["Source sheet", "Confirmed destination", "Method", "Confidence"]}
            rows={collapseBySource(final, groupByTable).map(({ item: f, label, group }) => [
              <DupSourceCell label={label} group={group} />,
              f.destination ? (
                <span className="font-mono text-slate-800 font-semibold">{f.destination}</span>
              ) : (
                <span className="text-slate-400">— unmatched —</span>
              ),
              <span className={`inline-flex items-center px-2 py-0.5 rounded-full text-[10px] font-medium font-mono ${_methodTone(f.method ?? "")}`}>
                {f.method ? plainMethodLabel(f.method) : "—"}
              </span>,
              <span className={`font-mono ${_confTone(f.confidence)}`}>{_pctText(f.confidence)}</span>,
            ])}
          />
        </StepCard>
      ) : null}
    </SequentialReveal>
  );
}

// ── B13.1 → B21.1 — the Column Intelligence Pipeline (collapsible step panels) ───────────

function _clsChip(cls: string): string {
  const c = cls.toLowerCase();
  if (c === "pk") return "bg-emerald-100 text-emerald-700";
  if (c === "fk") return "bg-blue-100 text-blue-700";
  return "bg-slate-100 text-slate-600"; // Shared
}

function CollapsibleStepCard({
  code,
  title,
  tone,
  meta,
  stage,
  defaultOpen = false,
  children,
}: {
  code: string;
  title: string;
  tone: "CoT" | "CoA";
  meta?: string;
  /** Cross-panel stage link key (matches the Activity-Log `data-stage`). See pipeline-stage-link.ts. */
  stage?: string;
  defaultOpen?: boolean;
  children: React.ReactNode;
}) {
  const [open, setOpen] = useState(defaultOpen);
  return (
    <div data-pipeline-stage={stage} className="mb-3 rounded-xl border border-slate-200 bg-white shadow-sm overflow-hidden">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="flex w-full items-center gap-2 px-5 py-2.5 text-left hover:bg-slate-50/60"
      >
        {open ? <ChevronDown size={13} className="shrink-0 text-slate-400" /> : <ChevronRight size={13} className="shrink-0 text-slate-400" />}
        <SubStepBadge code={code} />
        <span className="font-mono text-[10px] text-slate-400">{code}</span>
        <span className="text-sm font-semibold text-slate-700">{title}</span>
        <span
          className={`ml-1 inline-flex items-center px-1.5 py-0.5 rounded text-[10px] font-medium ${
            tone === "CoT" ? "bg-purple-50 text-purple-700" : "bg-blue-50 text-blue-700"
          }`}
        >
          {tone === "CoT" ? "Analysis" : "Action"}
        </span>
        {meta ? <span className="ml-auto text-[11px] text-slate-400">{meta}</span> : null}
      </button>
      {open ? <div className="border-t border-slate-100">{children}</div> : null}
    </div>
  );
}

/** B13.1 → B21.1 — full Column Intelligence Pipeline, rendered between B11 (table mapping)
 *  and "Step 1 — Confirm table routing". Collapsed by default; expand for full detail. */
export function ColumnIntelligencePanels({
  report,
  canonicalOverrides,
  // Canonical-name editing (the "Unified column names" card + confirm step) was removed; these
  // props are still accepted so existing callers compile, but are no longer used.
  onCanonicalChange: _onCanonicalChange,
  effectiveCanonical,
  canonicalsConfirmed = true,
  onConfirmCanonicals: _onConfirmCanonicals,
  confirmReMatchPending: _confirmReMatchPending = false,
  confirmInProgress: _confirmInProgress = false,
  confirmChangeCount: _confirmChangeCount = null,
  classificationGate,
  columnMappingGate,
  section,
}: {
  report?: UdrColumnIntelligence | null;
  /** Hoistra's step-by-step card shows the pipeline one stop at a time: "analysis" = B13.1–B19.1
   *  (how columns were grouped), "classification" = the B20.1 keys / shared-attribute cards and
   *  their gate, "mapping" = B22.1 · B14.1 (with its gate) · B21.1. Unset renders everything. */
  section?: "analysis" | "classification" | "mapping";
  /** 2.11 — the LIVE backend column-mapping-approval gate (B14.1). When the pipeline is interrupted
   *  at that gate this is rendered in the B14.1 · B15.1 slot (with Keep / Re-target / New column
   *  controls) INSTEAD of the read-only metadata card, so a single B14.1 card shows — no duplicate. */
  columnMappingGate?: React.ReactNode;
  /** 2.9 — the LIVE backend classification-approval gate. When the pipeline is
   *  interrupted at the B20.1 gate this is rendered in the 2.9 slot, directly
   *  under the PK / FK / Shared cards it refers to, INSTEAD of as a separate
   *  step-3 card. When absent the local (non-blocking) confirm is used. */
  classificationGate?: React.ReactNode;
  /** Pending canonical overrides the user has typed (group_id → new name). The
   *  override displays as the canonical chip's value until the gate is advanced
   *  and the backend re-runs build_column_intelligence with the pinned names. */
  canonicalOverrides?: Record<string, string>;
  /** Called when the user edits a canonical chip in the Unified column names
   *  panel. Empty string clears the override for that group. */
  onCanonicalChange?: (groupId: string, newName: string) => void;
  /** Pre-built map of lowercase 'table.col' → effective canonical column name
   *  (payload's column_canonical overlaid with the user's pending pins). When
   *  provided, EVERY downstream panel (B14.1, B17.1·B18.1, B19.1, B20.1, B21.1)
   *  reads its canonical from this map so the pin reflects everywhere even
   *  before the gate is advanced. */
  effectiveCanonical?: Record<string, string>;
  /** When false, B14.1 + B21.1 are HIDDEN behind a 'Confirm canonical names'
   *  step. The user clicks the button rendered under the Unified column names
   *  panel; on confirm B14/B21 reveal with the (possibly re-matched) data.
   *  Defaults to true so callers that don't need this gating behave unchanged. */
  canonicalsConfirmed?: boolean;
  /** Click handler for the confirm/re-match button. When unset, no button is shown
   *  (B14/B21 just gate on canonicalsConfirmed). */
  onConfirmCanonicals?: () => void;
  /** True when there are pending canonical pins that haven't been re-matched yet.
   *  The button label switches to 'Re-match & continue' to make the work explicit. */
  confirmReMatchPending?: boolean;
  /** True while the re-match request is in flight — disables the button + shows a
   *  spinner-style label so the user can see the work is happening. */
  confirmInProgress?: boolean;
  /** Diff summary from the last successful re-match: number of B14 / B21 rows
   *  whose canonical or matched-column changed. Surfaced beneath the panels so
   *  the user can SEE whether the re-match actually changed anything. */
  confirmChangeCount?: number | null;
}) {
  if (!report) return null;
  // Helper: canonical column name for a source (table, column) — returns null
  // if the override is the same as the source name (no actual rename).
  const _canonOf = (key: string): string | null => {
    const lc = key.toLowerCase();
    const canon = effectiveCanonical?.[lc];
    if (!canon) return null;
    const [, src] = key.split(".");
    return canon === src ? null : canon;
  };
  // Helper: canonical-overridden prefixed_key (e.g. 'vendors.id' → 'vendors.asset_id').
  const _canonKey = (prefixedKey: string): string => {
    const canon = _canonOf(prefixedKey);
    if (!canon) return prefixedKey;
    const [t] = prefixedKey.split(".");
    return `${t}.${canon}`;
  };
  // Helper: effective canonical NAME for a whole group — the user's pending pin beats the
  // payload's value, so the B19 header / B20 column stay in sync with the editable pill and
  // the member chips (which already reflect the pin via _canonKey).
  const _groupCanon = (groupId: string, fallback?: string): string | undefined =>
    canonicalOverrides?.[groupId] ?? fallback;
  // The canonical name the user EXPLICITLY pinned for a source column (live, client-side) —
  // a direct column pin or a pin on the column's group. Returns null when the column was not
  // pinned. B21 shows the SOURCE column name by default and only swaps in a name the user
  // deliberately pinned; it never auto-renames a customer's columns to the unified canonical.
  const _pinnedCanonOf = (src: string): string | null => {
    if (!canonicalOverrides) return null;
    const lc = src.toLowerCase();
    for (const [k, v] of Object.entries(canonicalOverrides)) {
      if (v && k.toLowerCase() === lc) return v;
    }
    for (const g of report.groups ?? []) {
      const hit = (g.member_keys ?? []).some(
        (mk) => `${mk.table}.${mk.column}`.toLowerCase() === lc,
      );
      if (hit && g.group_id && canonicalOverrides[g.group_id]) return canonicalOverrides[g.group_id];
    }
    return null;
  };
  // Mirror of the backend entity_prefix_conflict: a qualified-PK alias that crosses entities —
  // e.g. pinning the asset group to 'asset_id' should NOT rename vendors.id (the vendor PK).
  // Used so a group pin's live display skips a cross-entity member, matching the backend.
  const _ENTITY_PREFIXES = new Set([
    "asset", "vendor", "site", "work", "workorder", "wo", "resource", "customer", "employee",
    "engineer", "technician", "supplier", "product", "contact", "location", "building",
    "department", "user", "team", "company", "client", "tenant", "lease", "contract",
    "invoice", "task",
  ]);
  const _normTok = (s: string) => (s || "").toLowerCase().replace(/[^a-z0-9]+/g, "_").replace(/^_+|_+$/g, "");
  const _singularTok = (t: string) => {
    const s = (t || "").replace(/(?!^)([A-Z])/g, "_$1").toLowerCase();
    return s.endsWith("s") && !s.endsWith("ss") ? s.slice(0, -1) : s;
  };
  const _entityConflict = (srcName: string, destName: string, destTable?: string | null): boolean => {
    if (!destTable) return false;
    const st = new Set(_normTok(srcName).split("_").filter(Boolean));
    const dt = new Set(_normTok(destName).split("_").filter(Boolean));
    if (!st.size || !dt.size) return false;
    const stArr = [...st], dtArr = [...dt];
    const subset = stArr.every((x) => dt.has(x)) || dtArr.every((x) => st.has(x));
    if (!subset) return false;
    const [larger, smaller] = st.size >= dt.size ? [st, dt] : [dt, st];
    const extra = [...larger].filter((x) => !smaller.has(x));
    const destSing = _singularTok(destTable);
    return extra.some((tok) => tok && tok !== destSing && _ENTITY_PREFIXES.has(tok));
  };
  const show = (s: "analysis" | "classification" | "mapping") => !section || section === s;
  const pre = report.prefixing ?? [];
  const md = report.metadata ?? [];
  const fk = report.fk_candidates ?? [];
  const groups = report.groups ?? [];
  const cls = report.classification ?? [];
  const dest = report.dest_mapping ?? [];
  const s = report.summary ?? {};
  // 2.9 inline sign-off: the FK / Shared classification (B20.1) must be confirmed before the
  // downstream steps load. Once canonicals are confirmed the user is already past this, so treat
  // that as implicitly confirmed too (survives a re-render/remount).
  const [classificationConfirmed, setClassificationConfirmed] = useState(false);
  if (!pre.length && !md.length && !groups.length) return null;
  // Nothing after 2.9 loads until the classification is approved. While the LIVE backend gate is
  // open the pipeline is interrupted at grouping_review_node, so the UI must not render 2.10+
  // either — otherwise the user sees canonical names and destination mappings derived from a
  // classification they have not yet signed off (and which their FK/Shared/Exclude choices will
  // change). `canonicalsConfirmed` does NOT override this: the gate is the authority.
  const showAfterClassification = classificationGate
    ? false
    : !cls.length || classificationConfirmed || !!canonicalsConfirmed;

  // B17.1 / B18.1 — format grouping (server-built; FE renders once per group). Columns that share
  // a cell-value format, grouped within each table and across all tables, with duplicate tables
  // already collapsed (work_order ×2) by the backend.
  // B17.1's per-column matches are built below (colMatchRows) directly from the raw pairwise
  // format gate, so the old anchored top_format_matches pre-processing is no longer needed.

  // AC1/AC2 — cross-table pairwise gate. Each column pair is scored on FORMAT then VALUE; the value
  // result rides INLINE on the format pair. We show ONLY the pairs that actually GROUP TOGETHER
  // (format ≥80% AND value match) — the "separate"/"dissimilar" pairs are noise here. Value % is
  // read inline (the standalone value_pattern list is capped independently and can't be joined).
  // The raw format_gate is NOT duplicate-collapsed (unlike the B19.1 groups), so map each duplicate
  // table to its representative (workorders → work_order), then dedupe — removing both the repeated
  // matches AND the intra-duplicate self-pairs (work_order.wo_id ↔ workorders.wo_id).
  const _tableRep = new Map<string, string>();
  for (const g of report.duplicate_groups ?? []) {
    for (const t of g.tables) _tableRep.set(t, g.representative);
  }
  const _repKey = (key: string): string => {
    const dot = key.indexOf(".");
    if (dot < 0) return key;
    const t = key.slice(0, dot);
    return `${_tableRep.get(t) ?? t}.${key.slice(dot + 1)}`;
  };
  const _seenGate = new Set<string>();
  const gateRows = (report.format_gate ?? [])
    .filter((p) => p.value_decision === "group together")
    .map((p) => ({
      a: _repKey(p.col_a), b: _repKey(p.col_b), fa: p.format_a, fb: p.format_b,
      fmtPct: typeof p.score_pct === "number" ? p.score_pct : Math.round((p.score ?? 0) * 100),
      valPct: p.value_score_pct,
      threshold: p.threshold_pct ?? 80,
    }))
    .filter((r) => {
      if (r.a === r.b) return false; // self-pair created by collapsing a duplicate table
      const key = [r.a, r.b].sort().join("|");
      if (_seenGate.has(key)) return false;
      _seenGate.add(key);
      return true;
    })
    .sort((x, y) => (y.valPct ?? 0) - (x.valPct ?? 0));

  // B17.1 FORMAT gate — EVERY scored pair (pass + fail), duplicate-collapsed & unordered-deduped,
  // strongest first. This is the pairwise view the reference shows: Column A · Column B · Format A ·
  // Format B · Score (pass/fail). Table + PK metadata are ignored by the scorer (criterion note).
  const _seenFmt = new Set<string>();
  const fmtGatePairs = (report.format_gate ?? [])
    .map((p) => ({
      a: _repKey(p.col_a), b: _repKey(p.col_b), fa: p.format_a, fb: p.format_b,
      pct: typeof p.score_pct === "number" ? p.score_pct : Math.round((p.score ?? 0) * 100),
      pass: !!p.pass,
      threshold: p.threshold_pct ?? 80,
      valPct: p.value_score_pct,
      decision: p.value_decision,
    }))
    .filter((r) => {
      if (r.a === r.b) return false;
      const key = [r.a, r.b].sort().join("|");
      if (_seenFmt.has(key)) return false;
      _seenFmt.add(key);
      return true;
    })
    .sort((x, y) => (y.pct - x.pct) || (x.pass === y.pass ? 0 : x.pass ? -1 : 1));
  // B18.1 VALUE-PATTERN gate — only the pairs that SURVIVED the format gate are re-checked on the
  // value pattern (format of the values, not literal values). Decision = group together / separate.
  const valueGatePairs = fmtGatePairs.filter((r) => r.pass);
  const _fmtPassCount = valueGatePairs.length;
  const _valueGroupCount = valueGatePairs.filter((r) => r.decision === "group together").length;

  // Per-column view for B17.1 — each SOURCE column becomes a collapsible row; expanding it lists
  // every column in OTHER tables it matches by cell-value FORMAT (with the value-pattern % and the
  // group/separate decision). Built from the raw pairwise gate so a pair contributes to BOTH of its
  // endpoints (asset_id shows work_orders.asset_id AND sites.site_id). Cross-table only — the
  // criterion is "every other column in all OTHER tables".
  type ColMatch = { target: string; targetFormat: string; fmtPct?: number; valPct?: number; group: boolean; isPk: boolean };
  // Per-column format lookup (from B14.1 metadata) + per-pair format/value % lookup (from the gate),
  // so each group match can still show its format · value scores when the pair survived the cap.
  const _fmtByCol = new Map<string, string>();
  for (const m of md) _fmtByCol.set(_repKey(m.prefixed_key), m.format);
  const _pairScore = new Map<string, { fmtPct: number; valPct?: number }>();
  for (const p of report.format_gate ?? []) {
    const a = _repKey(p.col_a), b = _repKey(p.col_b);
    if (a === b) continue;
    _pairScore.set([a, b].sort().join("|"), {
      fmtPct: typeof p.score_pct === "number" ? p.score_pct : Math.round((p.score ?? 0) * 100),
      valPct: p.value_score_pct,
    });
  }
  // Build the per-column matches from the AUTHORITATIVE grouping (report.groups), NOT the capped
  // pairwise gate: a column's matches are the OTHER members of its group. This guarantees the
  // PRIMARY KEY of the group is always shown — a foreign-key column (work_order.site_id) lists the
  // PK it references (sites.site_id), not an arbitrary non-PK peer (assets.site_id) — and nothing is
  // lost to the 200-pair cap. Each match is tagged isPk so the PK sorts first with a badge.
  const _colMatchMap = new Map<string, { format: string; matches: ColMatch[] }>();
  for (const g of groups) {
    const mc = g.member_classes ?? [];
    if (mc.length < 2) continue;
    const members = mc.map((m) => ({
      key: _repKey(m.prefixed_key),
      isPk: String(m.classification).toUpperCase() === "PK",
    }));
    for (const src of members) {
      for (const tgt of members) {
        if (src.key === tgt.key) continue;
        if (src.key.split(".")[0] === tgt.key.split(".")[0]) continue; // other tables only
        if (!_colMatchMap.has(src.key)) {
          _colMatchMap.set(src.key, { format: _fmtByCol.get(src.key) ?? "", matches: [] });
        }
        const entry = _colMatchMap.get(src.key)!;
        if (entry.matches.some((m) => m.target === tgt.key)) continue;
        const sc = _pairScore.get([src.key, tgt.key].sort().join("|"));
        entry.matches.push({
          target: tgt.key,
          targetFormat: _fmtByCol.get(tgt.key) ?? "",
          fmtPct: sc?.fmtPct,
          valPct: sc?.valPct,
          group: true,
          isPk: tgt.isPk,
        });
      }
    }
  }
  const colMatchRows = [..._colMatchMap.entries()]
    .map(([source, v]) => ({
      source,
      format: v.format,
      // PRIMARY KEY match first, then by format/value strength.
      matches: v.matches.sort(
        (x, y) => (Number(y.isPk) - Number(x.isPk)) || ((y.fmtPct ?? 0) - (x.fmtPct ?? 0)) || ((y.valPct ?? 0) - (x.valPct ?? 0)),
      ),
      groupCount: v.matches.length,
    }))
    .filter((r) => r.matches.length > 0)
    .sort((x, y) => (y.groupCount - x.groupCount) || x.source.localeCompare(y.source));
  // Group the per-column rows by their SOURCE TABLE so B17.1 reads table → column → matches:
  // an outer collapsible per table, each column collapsible inside it.
  const colMatchByTable = (() => {
    const m = new Map<string, typeof colMatchRows>();
    for (const r of colMatchRows) {
      const t = r.source.split(".")[0] ?? "—";
      if (!m.has(t)) m.set(t, []);
      m.get(t)!.push(r);
    }
    return [...m.entries()]
      .map(([table, rows]) => ({
        table,
        rows,
        groupCount: rows.reduce((n, r) => n + r.groupCount, 0),
      }))
      .sort((x, y) => (y.groupCount - x.groupCount) || x.table.localeCompare(y.table));
  })();

  // FK relationships collapsed by duplicate table (workorders.site_id → work_order.site_id, deduped)
  // so the FK card shows one section per real table, not work_order AND workorders separately.
  const _dupLabelByRep = new Map<string, string>();
  for (const g of report.duplicate_groups ?? []) _dupLabelByRep.set(g.representative, g.label);
  const _fkSeen = new Set<string>();
  const fkCollapsed = fk
    .map((f) => ({ ...f, src_table: _tableRep.get(f.src_table ?? "") ?? f.src_table }))
    .filter((f) => {
      const key = `${f.src_table}.${f.src_column}->${f.dst_table}.${f.dst_column}`;
      if (_fkSeen.has(key)) return false;
      _fkSeen.add(key);
      return true;
    });

  return (
    <div className="mb-4">
      {section ? null : (
      <div className="mb-2 flex items-center gap-2">
        <span className="text-[11px] font-semibold uppercase tracking-wider text-slate-500">Column Intelligence Pipeline</span>
        <span className="text-[11px] text-slate-400">how every column was analysed, grouped, classified &amp; mapped — against the confirmed destination table</span>
      </div>
      )}
      {/* Reveal the column cards ONE BY ONE (same progressive reveal as the table panels) instead
          of dropping the whole pipeline in at once. Paced slower than the default so the sequence
          is actually legible as "card by card" rather than reading as a single block. */}
      <SequentialReveal stepMs={ANALYSIS_REVEAL_MS}>

      {/* B13.1 — Table prefixing (source UDR override: canonical name in place of the original) */}
      {show("analysis") && pre.length ? (
        <CollapsibleStepCard code="B13.1" title="Table prefixing" tone="CoA" stage="table_prefixing" meta="prefixed with the confirmed destination table · prevents name collisions" defaultOpen>
          <StepTable
            head={["Confirmed table", "Column (override)", "Prefixed key"]}
            rows={pre.slice(0, 60).map((p) => {
              const displayCol = p.canonical_name ?? p.column;
              const renamedCol = p.canonical_name && p.canonical_name !== p.column;
              // Routing is confirmed → prefix with the destination table (fall back to prefixed_key
              // only when a table wasn't routed). Source table is no longer shown.
              const confirmedTable = p.dest_table ?? p.source_table;
              const prefixed = p.dest_prefixed_key ?? p.prefixed_key;
              // Duplicate sheets routed to one destination collapse to a SINGLE row — show the
              // ×count so it's clear the second sheet was folded in, not dropped.
              const coRouted = p.source_tables ?? [];
              return [
                <div className="leading-tight">
                  <span className="font-mono text-slate-700">{confirmedTable}</span>
                  {coRouted.length > 1 ? (
                    <span
                      className="ml-1.5 rounded-full bg-amber-500 px-1.5 py-0.5 text-[9px] font-bold text-white"
                      title={`Merged from ${coRouted.join(", ")}`}
                    >
                      ×{coRouted.length}
                    </span>
                  ) : null}
                </div>,
                <div className="leading-tight">
                  <span className="font-mono text-indigo-700">{displayCol}</span>
                  {renamedCol ? (
                    <div className="font-mono text-[10px] text-slate-400">renamed from {p.source_column ?? p.column}</div>
                  ) : null}
                  {p.canonical_suppressed ? (
                    <div className="font-mono text-[10px] text-amber-600">override suppressed · would collide with existing column in this table</div>
                  ) : null}
                </div>,
                // Prefixed with the CONFIRMED destination table (e.g. work_orders.wo_id).
                <span className="font-mono text-indigo-700">{prefixed}</span>,
              ];
            })}
          />
        </CollapsibleStepCard>
      ) : null}

      {/* B14.1·B15.1 metadata moved BELOW B20 + unification (it carries the canonical name). */}

      {/* B17.1 — Step 1 · FORMAT gate. Every column scored against every column in OTHER tables on
          coarse cell-value format only. Pairs at ≥ threshold proceed to the value-pattern gate. */}
      {show("analysis") && colMatchByTable.length ? (
        <CollapsibleStepCard
          code="B17.1"
          title="Step 1 — Column similarity within source · FORMAT gate"
          tone="CoT"
          stage="column_within_source"
          meta={`${s.format_pairs_scored ?? fmtGatePairs.length} pairs scored`}
          defaultOpen
        >
          <div className="px-5 py-2 text-[11px] leading-relaxed text-slate-500">
            Every column is scored against every column in <i>other</i> tables on coarse cell-value
            format only (id_code · categorical · date · free_text · integer · decimal · empty). Pairs
            at <b>≥ {(fmtGatePairs[0]?.threshold ?? s.format_threshold_pct ?? 80)}%</b> proceed to the
            value-pattern gate. <b>Table name and primary-key metadata are deliberately ignored</b> at
            this stage.
          </div>

          {colMatchByTable.length ? (
            <>
              <div className="px-5 pt-2 pb-1 text-[10.5px] text-slate-500">
                Grouped <b>by table</b> — expand a table, then a column inside it, to see the columns
                in <b>other tables</b> it <b>groups with</b> (passed BOTH the format gate and the
                value-pattern gate). Matched on <b>format, not actual values</b>; format-only pairs are
                hidden.
              </div>
              {colMatchByTable.map((t) => (
                <details key={t.table} className="group/table border-t border-slate-200" open>
                  <summary className="flex cursor-pointer list-none items-center gap-2 bg-slate-50 px-4 py-2 text-[12px] hover:bg-slate-100 select-none [&::-webkit-details-marker]:hidden">
                    <span className="text-slate-400 transition-transform group-open/table:rotate-90">▸</span>
                    <span className="font-mono font-semibold text-slate-800">{t.table}</span>
                    <span className="ml-auto flex items-center gap-1.5">
                      {t.groupCount ? (
                        <span className="rounded-full bg-emerald-100 px-2 py-0.5 text-[10px] font-medium text-emerald-800">
                          {t.groupCount} grouped
                        </span>
                      ) : null}
                      <span className="rounded-full bg-slate-200 px-2 py-0.5 text-[10px] text-slate-600">
                        {t.rows.length} column{t.rows.length === 1 ? "" : "s"}
                      </span>
                    </span>
                  </summary>
                  {t.rows.map((r) => (
                    <details key={r.source} className="group/col border-t border-slate-100" open>
                      <summary className="flex cursor-pointer list-none items-center gap-2 pl-9 pr-5 py-2 text-[11.5px] hover:bg-slate-50 select-none [&::-webkit-details-marker]:hidden">
                        <span className="text-slate-400 transition-transform group-open/col:rotate-90">▸</span>
                        <span className="font-mono font-semibold text-indigo-700">{r.source.split(".")[1] ?? r.source}</span>
                        <span className="inline-flex items-center rounded-full bg-slate-100 px-2 py-0.5 text-[10px] text-slate-600">{r.format}</span>
                        <span className="ml-auto">
                          <span className="rounded-full bg-emerald-100 px-2 py-0.5 text-[10px] font-medium text-emerald-800">
                            {r.matches.length} grouped
                          </span>
                        </span>
                      </summary>
                      <div className="bg-slate-50/60 pl-9 pr-5 py-1.5">
                        <table className="w-full text-[10.5px]">
                          <tbody>
                            {r.matches.map((m, i) => (
                              <tr key={i} className="border-b border-slate-100 last:border-0">
                                <td className="py-1.5 pr-3">
                                  <span className="font-mono text-slate-700">{m.target}</span>
                                  {m.isPk ? (
                                    <span className="ml-1.5 inline-flex items-center rounded bg-emerald-100 px-1.5 py-0.5 text-[8.5px] font-bold text-emerald-800 border border-emerald-300" title="Primary key of this group — the column its peers reference">
                                      PK
                                    </span>
                                  ) : null}
                                </td>
                                <td className="py-1.5 pr-3">
                                  <span className="inline-flex items-center rounded-full bg-slate-100 px-1.5 py-0.5 text-[9.5px] text-slate-500">{m.targetFormat}</span>
                                </td>
                                <td className="py-1.5 pr-3 font-mono text-slate-400 tabular-nums">
                                  {typeof m.fmtPct === "number"
                                    ? `format ${m.fmtPct}%${typeof m.valPct === "number" ? ` · value ${m.valPct}%` : ""}`
                                    : "grouped"}
                                </td>
                                <td className="py-1.5 text-right">
                                  <span className="inline-flex items-center rounded-full bg-emerald-100 px-2 py-0.5 text-[9.5px] font-medium text-emerald-800">
                                    group together
                                  </span>
                                </td>
                              </tr>
                            ))}
                          </tbody>
                        </table>
                      </div>
                    </details>
                  ))}
                </details>
              ))}
              <div className="px-5 py-2 text-[10.5px] text-slate-400">
                {colMatchByTable.length} table(s) · {colMatchRows.length} column(s) with grouped matches ·{" "}
                {s.format_pairs_scored ?? fmtGatePairs.length} pairs scored · {_fmtPassCount} passed format · {_valueGroupCount} grouped.
              </div>
            </>
          ) : null}
        </CollapsibleStepCard>
      ) : null}

      {/* B18.1 (VALUE-PATTERN gate) card removed — B17.1 above already shows only the group-together
          matches (pairs that passed BOTH the format AND value-pattern gates), so the separate
          value-pattern pairs card was redundant. */}

      {/* B19.1 — Similar-column grouping (+ unified names + per-member PK/FK class) */}
      {show("analysis") && groups.length ? (
        <CollapsibleStepCard code="B19.1" title="Group similar columns (name-agnostic) + unified name" tone="CoA" stage="column_grouping" meta={`${groups.length} multi-member groups`}>
          {gateRows.length ? (
            <>
              <div className="px-5 pt-3 pb-1 text-[10.5px] text-slate-500">
                <b>Grouped pairs</b> — cross-table column pairs that passed both gates
                (<b>format ≥{gateRows[0]?.threshold ?? 80}%</b> then <b>value match</b>) and were grouped
                together. Each row shows its format and value match %.
              </div>
              <StepTable
                head={["Column pair (cross-table)", "Formats", "Format match", "Value match"]}
                rows={gateRows.slice(0, 80).map((r) => [
                  <span className="font-mono text-[10.5px] text-indigo-700">{r.a} <span className="text-slate-300">↔</span> {r.b}</span>,
                  <span className="text-[10px] text-slate-500">{r.fa} <span className="text-slate-300">/</span> {r.fb}</span>,
                  <span className="text-[10px] text-slate-600">{r.fmtPct}%</span>,
                  <span className="inline-flex items-center rounded-full bg-emerald-100 px-2 py-0.5 text-[10px] font-medium text-emerald-800">{typeof r.valPct === "number" ? `${r.valPct}%` : "—"}</span>,
                ])}
              />
            </>
          ) : null}
          <div className="px-4 py-3 space-y-2">
            {groups.map((g) => {
              const classByKey = new Map<string, string>(
                (g.member_classes ?? []).map((mc) => [mc.prefixed_key, mc.classification]),
              );
              const joinByKey = new Map<string, "name" | "value">(
                (g.member_classes ?? []).flatMap((mc) =>
                  mc.joined_by ? [[mc.prefixed_key, mc.joined_by] as [string, "name" | "value"]] : [],
                ),
              );
              const matchByKey = new Map<string, number>(
                (g.member_classes ?? []).flatMap((mc) =>
                  typeof mc.match_pct === "number" ? [[mc.prefixed_key, mc.match_pct] as [string, number]] : [],
                ),
              );
              return (
                <div key={g.group_id} className="rounded-lg border border-slate-200 p-2.5">
                  <div className="flex items-center gap-2 flex-wrap">
                    <span className="font-mono text-[11px] font-semibold text-slate-700">{g.group_id}</span>
                    {g.format ? <span className="text-[10px] text-slate-400">{g.format}</span> : null}
                    <span className="ml-auto inline-flex items-center gap-1 rounded-full bg-indigo-50 px-2 py-0.5 text-[10px] font-medium text-indigo-700">
                      canonical: <span className="font-mono">{_groupCanon(g.group_id, g.canonical_name)}</span>
                    </span>
                  </div>
                  <div className="mt-1.5 flex flex-wrap gap-1.5">
                    {g.members.map((m, i) => {
                      // Show the REAL source column name (m), never the canonical-overridden name —
                      // a member joined "by value" (e.g. technicians.base_site_id) must NOT be
                      // relabelled to the group canonical (site_id), which would print a column that
                      // doesn't exist on that table. The unified name is shown once in the header.
                      const cls = classByKey.get(m);
                      const join = joinByKey.get(m);
                      const matchPct = matchByKey.get(m);
                      const chipTone =
                        cls === "PK" ? "bg-emerald-100 text-emerald-800 border border-emerald-300" :
                        cls === "FK" ? "bg-amber-100 text-amber-800 border border-amber-300" :
                                       "bg-slate-100 text-slate-600 border border-transparent";
                      const joinTone =
                        join === "name"  ? "bg-indigo-50 text-indigo-700 border border-indigo-200" :
                        join === "value" ? "bg-violet-50 text-violet-700 border border-violet-200" : "";
                      return (
                        <span key={i} className={`inline-flex items-center gap-1 rounded px-1.5 py-0.5 font-mono text-[10.5px] ${chipTone}`} title={cls === "PK" ? "primary key (100%)" : (typeof matchPct === "number" ? `referential integrity ${matchPct}%${join ? ` · joined by ${join}` : ""}` : (join ? `joined by ${join}` : undefined))}>
                          {cls ? <span className="text-[9px] font-semibold uppercase tracking-wider">{cls}</span> : null}
                          {m}
                          {typeof matchPct === "number" ? (
                            <span className="ml-1 inline-flex items-center rounded-sm bg-emerald-50 px-1 text-[8.5px] font-semibold text-emerald-700">{matchPct}%</span>
                          ) : null}
                          {join ? (
                            <span className={`ml-1 inline-flex items-center rounded-sm px-1 text-[8.5px] font-medium uppercase tracking-wider ${joinTone}`}>
                              by {join}
                            </span>
                          ) : null}
                        </span>
                      );
                    })}
                  </div>
                </div>
              );
            })}
          </div>
        </CollapsibleStepCard>
      ) : null}

      {/* B20.1 — split into THREE cards: Primary keys · Foreign keys → PK · Shared attributes.
          Hidden while the classification GATE is open (classificationGate present): the gate below
          shows the SAME PK / FK / Shared assignment interactively, so the read-only cards would be
          duplicate. They reappear as the read-only result once the gate is approved. */}
      {show("classification") && cls.length && !classificationGate ? (() => {
        // Primary keys = EVERY distinct PK anchor across all groups (a PK inside an FK-relationship
        // group — e.g. sites.site_id — is still a primary key), so this lists all 6, matching B8.1.
        const _pkSeen = new Set<string>();
        const pkRows = cls.filter((r) => {
          const pk = r.canonical_has_pk ?? r.has_pk;
          if (!pk || _pkSeen.has(pk)) return false;
          _pkSeen.add(pk);
          return true;
        });
        const sharedRows = cls.filter((r) => /shared/i.test(r.verdict));
        return (
          <>
            {pkRows.length ? (
              <CollapsibleStepCard code="B20.1 · PK" title="Primary keys" tone="CoA" stage="column_classification" meta={`${pkRows.length} primary key${pkRows.length === 1 ? "" : "s"}`} defaultOpen>
                <StepTable
                  head={["Group", "Primary key", "Canonical name"]}
                  rows={pkRows.map((r) => [
                    <span className="font-mono text-slate-700">{r.group_id}</span>,
                    <span className="inline-flex items-center gap-1 font-mono text-[10.5px] text-emerald-700">
                      <span className="text-[8.5px] font-semibold uppercase tracking-wider">PK</span>{r.canonical_has_pk ?? r.has_pk ?? "—"}
                    </span>,
                    <span className="font-mono text-slate-600">{_groupCanon(r.group_id, r.canonical_name)}</span>,
                  ])}
                />
              </CollapsibleStepCard>
            ) : null}

            {fkCollapsed.length ? (
              <CollapsibleStepCard code="B20.1 · FK" title="Foreign-key relationships" tone="CoA" stage="column_classification" meta={`${fkCollapsed.filter((f) => f.confirmed).length} confirmed · ${fkCollapsed.filter((f) => !f.confirmed).length} shared`} defaultOpen>
                <div className="px-5 py-2 text-[11px] text-slate-500">
                  Every source column identified as a foreign key, the primary key it references, and its
                  <b> referential integrity</b> (share of the FK&apos;s values found in that PK). Duplicate
                  tables are collapsed (e.g. <span className="font-mono">work_order ×2</span>).
                </div>
                {(() => {
                  const byTable = new Map<string, typeof fkCollapsed>();
                  for (const f of [...fkCollapsed].sort((a, b) => Number(b.confirmed) - Number(a.confirmed) || (b.ri ?? 0) - (a.ri ?? 0))) {
                    const tbl = f.src_table ?? "—";
                    if (!byTable.has(tbl)) byTable.set(tbl, []);
                    byTable.get(tbl)!.push(f);
                  }
                  const renderRow = (f: (typeof fkCollapsed)[number]) => [
                    <span className="font-mono text-[10.5px] text-amber-700">{f.src_table}.{f.src_column}</span>,
                    <span className="inline-flex items-center gap-1 font-mono text-[10.5px] text-slate-700">
                      <span className="text-amber-600">→</span>
                      <span className="text-[8.5px] font-semibold uppercase tracking-wider text-emerald-700">PK</span>
                      {f.dst_table}.{f.dst_column}
                    </span>,
                    typeof f.ri === "number"
                      ? <span className={`inline-flex items-center rounded-full px-2 py-0.5 text-[10px] font-medium ${f.ri >= 0.95 ? "bg-emerald-100 text-emerald-800" : "bg-amber-100 text-amber-800"}`}>{Math.round(f.ri * 100)}%</span>
                      : <span className="text-slate-400">—</span>,
                    <span className={`inline-flex items-center rounded-full px-1.5 py-0.5 text-[10px] font-medium ${f.confirmed ? "bg-emerald-100 text-emerald-700" : "bg-slate-100 text-slate-600"}`}>{f.confirmed ? "FK confirmed" : "shared attribute"}</span>,
                  ];
                  return [...byTable.entries()].map(([tbl, rows]) => (
                    <details key={tbl} className="group border-t border-slate-100" open>
                      <summary className="flex cursor-pointer list-none items-center gap-2 px-5 py-2 text-[11.5px] hover:bg-slate-50 select-none [&::-webkit-details-marker]:hidden">
                        <span className="text-slate-400 transition-transform group-open:rotate-90">▸</span>
                        <span className="font-mono font-semibold text-slate-700">{_dupLabelByRep.get(tbl) ?? tbl}</span>
                        <span className="rounded-full bg-slate-100 px-2 py-0.5 text-[10px] text-slate-500">{rows.length} FK{rows.length === 1 ? "" : "s"}</span>
                      </summary>
                      <StepTable
                        head={["Foreign key (source)", "References (primary key)", "Referential integrity", "Status"]}
                        rows={rows.map(renderRow)}
                      />
                    </details>
                  ));
                })()}
              </CollapsibleStepCard>
            ) : null}

            {sharedRows.length ? (
              <CollapsibleStepCard code="B20.1 · Shared" title="Shared attributes" tone="CoA" stage="column_classification" meta={`${sharedRows.length} shared`} defaultOpen>
                <div className="px-5 py-2 text-[11px] text-slate-500">
                  Attributes shared across tables that are not any table&apos;s key (each becomes a lookup
                  table in B22.1).
                </div>
                <StepTable
                  head={["Group", "Canonical", "Columns"]}
                  rows={sharedRows.map((r) => [
                    <span className="font-mono text-slate-700">{r.group_id}</span>,
                    <span className="font-mono text-slate-600">{_groupCanon(r.group_id, r.canonical_name)}</span>,
                    <span className="font-mono text-[10.5px] text-indigo-700">{(r.members ?? []).join(" · ")}</span>,
                  ])}
                />
              </CollapsibleStepCard>
            ) : null}
          </>
        );
      })() : null}

      {/* 2.9 approval. When the backend has interrupted at the B20.1 gate the REAL gate renders
          here — inline, right under the PK / FK / Shared cards it refers to — so there is no
          separate confirmation card hanging off step 3. */}
      {show("classification") && classificationGate ? (
        <div className="mb-4 rounded-xl border border-indigo-200 bg-indigo-50/30 px-5 py-4">
          {classificationGate}
        </div>
      ) : null}

      {/* Local (non-blocking) confirm — only when no live gate is pending. */}
      {show("classification") && !classificationGate && cls.length && !showAfterClassification ? (
        <div className="mb-4 rounded-xl border border-indigo-200 bg-indigo-50/50 px-5 py-4">
          <div className="flex flex-wrap items-center gap-3">
            <div className="flex-1 min-w-0">
              <p className="text-[13px] font-semibold text-indigo-900">
                Confirm the PK / FK / Shared classification (2.9)
              </p>
              <p className="mt-0.5 text-[11.5px] leading-relaxed text-indigo-800/80">
                Review the grouping (2.8) and the FK / Shared verdicts above. Confirm to load the
                foreign-key relationships, new lookup tables and destination mapping.
              </p>
            </div>
            <button
              type="button"
              onClick={() => setClassificationConfirmed(true)}
              className="inline-flex items-center gap-1.5 rounded-md bg-indigo-600 px-3 py-1.5 text-[12px] font-semibold text-white hover:bg-indigo-700"
            >
              Confirm classification &amp; continue ↓
            </button>
          </div>
        </div>
      ) : null}

      {show("mapping") && showAfterClassification ? (
      <>
      {/* FK relationships now render in the B20.1 · FK card above (by source table), so the
          duplicate standalone card was removed. */}

      {/* B22.1 — Shared-attribute lookup tables (appended): shared attribute → new PK table */}
      {(report.shared_attribute_tables ?? []).length ? (
        <CollapsibleStepCard
          code="B22.1"
          title="New tables from shared attributes (attribute → primary key)"
          tone="CoA"
          stage="shared_attribute_tables"
          meta={`${(report.shared_attribute_tables ?? []).length} lookup table(s) appended`}
          defaultOpen
        >
          <div className="px-5 py-2 text-[11px] text-slate-500">
            Shared attributes that are not any table&apos;s key are normalised into new <b>lookup tables</b> —
            the attribute becomes the <b>primary key</b> and its distinct values become the rows; every source
            column is <b>rewritten as a foreign key</b> to it. The emitted DDL is shown per table.
          </div>
          <StepTable
            head={["New table", "Primary key", "FK rewrites (source → lookup PK)", "Distinct values"]}
            rows={(report.shared_attribute_tables ?? []).map((t) => [
              <span className="inline-flex items-center gap-1 font-mono text-[10.5px] text-emerald-700">
                <span className="inline-flex items-center rounded-sm bg-emerald-100 px-1 text-[8.5px] font-semibold uppercase text-emerald-800">new</span>
                {t.table_name}
              </span>,
              <span className="inline-flex items-center gap-1 font-mono text-[10.5px] text-slate-700">
                <span className="text-[8.5px] font-semibold uppercase tracking-wider text-emerald-700">PK</span>{t.pk_column}
              </span>,
              <div className="flex flex-col gap-0.5">
                {(t.fk_rewrites ?? []).map((rw, i) => (
                  <span key={i} className="font-mono text-[10px] text-slate-600">{rw.source} <span className="text-amber-600">→ FK →</span> {rw.references}</span>
                ))}
              </div>,
              <div className="leading-tight">
                <span className="text-[10px] text-slate-600">{t.sample_values.join(" · ")}</span>
                <span className="text-[10px] text-slate-400"> ({t.distinct_count} total)</span>
              </div>,
            ])}
          />
          <div className="px-5 py-3 space-y-2 border-t border-slate-100">
            <div className="text-[10.5px] font-medium text-slate-500">Emitted DDL</div>
            {(report.shared_attribute_tables ?? []).map((t, i) => (
              <pre key={i} className="overflow-x-auto rounded bg-slate-900 p-2.5 text-[10px] leading-relaxed text-slate-100">
                <code>{[t.create_ddl, ...(t.fk_ddl ?? [])].filter(Boolean).join("\n")}</code>
              </pre>
            ))}
          </div>
        </CollapsibleStepCard>
      ) : null}

      {/* Unified column names card removed per request — canonical naming is applied silently by
          the backend; B14.1 / B21.1 below show the results directly (no separate confirm step). */}

      {/* B14.1 · B15.1 — Column metadata + the MATCHED destination column (from B21.1).
          When the backend has interrupted at the B14.1 column-mapping gate the REAL gate renders
          HERE — inline, in this exact 2.11 slot, with the Keep / Re-target / New column controls —
          INSTEAD of the read-only metadata card, so there is no separate duplicate "Confirm column
          mapping" card. The read-only metadata card returns as history once the gate is answered. */}
      {columnMappingGate ? (
        <div className="mb-4 rounded-xl border border-indigo-200 bg-indigo-50/30 px-5 py-4">
          {columnMappingGate}
        </div>
      ) : md.length ? (
        <CollapsibleStepCard code="B14.1 · B15.1" title="Column metadata — 4 dimensions + matched destination column" tone="CoA" stage="column_metadata" meta="dest · class · format · 5 samples · matched column">
          <div className="px-5 py-1.5 text-[10.5px] text-slate-500">
            The last column is the <span className="font-mono text-emerald-700">matched destination column</span> —
            the closest destination-table column the deterministic + semantic mapper resolved to by
            <b> format and values</b> (e.g. <span className="font-mono">serial → serial_number</span>), not an echo of
            the source header. A column with no confident match is shown as <span className="text-slate-500">new column</span>.
          </div>
          {/* Grouped by SOURCE TABLE, each table collapsible — so the user tracks one table's
              column mapping at a time (sites, then assets, …) instead of one long flat list. */}
          {(() => {
            const byTable = new Map<string, typeof md>();
            for (const m of md) {
              const tbl = m.source_table ?? m.prefixed_key.split(".")[0] ?? "—";
              if (!byTable.has(tbl)) byTable.set(tbl, []);
              byTable.get(tbl)!.push(m);
            }
            // The last column shows the MATCHED DESTINATION column (from the deterministic +
            // semantic mapper's closest match by format & values, i.e. B21.1's dest_mapping),
            // NOT the source-derived canonical name. So assets.serial shows 'serial_number', the
            // column the data actually lands in — not an echo of the source header.
            const destByKey = new Map<string, (typeof dest)[number]>();
            for (const d of dest) destByKey.set(d.source, d);
            const renderRow = (m: (typeof md)[number]) => {
              const liveKey = _canonKey(m.prefixed_key);
              const displayKey = liveKey !== m.prefixed_key ? liveKey : (m.canonical_prefixed_key ?? m.prefixed_key);
              const renamed = displayKey !== m.prefixed_key;
              const destRow = destByKey.get(m.prefixed_key);
              const matchedCol = destRow?.matched_column ?? null;
              const isNewCol = !matchedCol && /new/i.test(destRow?.outcome ?? "");
              return [
                <div className="leading-tight">
                  <div className="font-mono text-indigo-700">{displayKey}</div>
                  {renamed ? (
                    <div className="font-mono text-[10px] text-slate-400">renamed from {m.prefixed_key}</div>
                  ) : null}
                </div>,
                m.dest_table ? <span className="font-mono text-slate-700">{m.dest_table}</span> : <span className="text-slate-400">—</span>,
                <span className={`inline-flex items-center px-1.5 py-0.5 rounded-full text-[10px] font-medium ${_clsChip(m.classification)}`}>{m.classification}</span>,
                <span className="text-slate-500">{m.format}</span>,
                <span className="font-mono text-slate-400">{(m.samples ?? []).slice(0, 5).join(", ")}</span>,
                matchedCol ? (
                  <div className="leading-tight">
                    <span className="font-mono text-emerald-700">{matchedCol}</span>
                    {destRow?.outcome ? (
                      <div className="text-[9.5px] text-slate-400">{destRow.outcome}</div>
                    ) : null}
                  </div>
                ) : isNewCol ? (
                  <span className="text-[10.5px] text-slate-500">new column</span>
                ) : (
                  <span className="text-slate-400">—</span>
                ),
              ];
            };
            return [...byTable.entries()].map(([tbl, rows]) => (
              <details key={tbl} className="group border-t border-slate-100">
                <summary className="flex cursor-pointer list-none items-center gap-2 px-5 py-2 text-[11.5px] hover:bg-slate-50 select-none [&::-webkit-details-marker]:hidden">
                  <span className="text-slate-400 transition-transform group-open:rotate-90">▸</span>
                  <span className="font-mono font-semibold text-slate-700">{tbl}</span>
                  <span className="rounded-full bg-slate-100 px-2 py-0.5 text-[10px] text-slate-500">{rows.length} columns</span>
                </summary>
                <StepTable
                  head={["Canonical key (renamed from)", "Destination table", "Class", "Format", "Sample values (5)", "Matched destination column"]}
                  rows={rows.map(renderRow)}
                />
              </details>
            ));
          })()}
          {fk.length ? (
            <div className="border-t border-slate-100 px-5 py-3">
              <p className="mb-2 text-[11px] font-semibold text-slate-600">
                Identical columns across different tables → FK relationships
              </p>
              <div className="space-y-2">
                {fk.map((f, i) => (
                  <div key={i} className="rounded-lg border border-slate-200 p-2.5">
                    <div className="flex items-center justify-between gap-2 mb-1">
                      <span className="text-[11px] font-semibold text-slate-700">FK candidate</span>
                      <span className={`inline-flex items-center px-1.5 py-0.5 rounded-full text-[10px] font-medium ${f.confirmed ? "bg-emerald-100 text-emerald-700" : "bg-amber-100 text-amber-700"}`}>
                        {f.confirmed ? "FK confirmed" : "Shared Attribute"}
                        {typeof f.ri === "number" ? ` · RI ${Math.round(f.ri * 100)}%` : ""}
                      </span>
                    </div>
                    <div className="flex items-center gap-2 text-[11.5px]">
                      <span className="font-mono text-slate-700">{f.src_table}.{f.src_column}</span>
                      <ArrowRightChar />
                      <span className="font-mono text-slate-700">{f.dst_table}.{f.dst_column}</span>
                    </div>
                    {f.reason ? <p className="mt-0.5 text-[10px] text-slate-500">{f.reason}</p> : null}
                  </div>
                ))}
              </div>
            </div>
          ) : null}
        </CollapsibleStepCard>
      ) : null}

      {/* B21.1 "Destination column mapping" card removed — its data (matched destination column
          per source) is duplicate: it is already shown in B14.1 above (the "Matched destination
          column" column) and in the Step-2 Column-matching gate. */}
      {false ? (() => {
        const _isAuto = (o: string) => /auto|matched/i.test(o);
        const _isSuggest = (o: string) => /suggest/i.test(o);
        const _isNew = (o: string) => /new/i.test(o);
        const countAuto = dest.filter((r) => _isAuto(r.outcome)).length;
        const countSuggest = dest.filter((r) => _isSuggest(r.outcome)).length;
        const countNew = dest.filter((r) => _isNew(r.outcome)).length;
        const distinctDestTables = new Set(
          dest.map((r) => r.dest_table).filter((t): t is string => !!t)
        );
        // Outcome chips: count by exact outcome label so the user sees the raw
        // mapper verdict distribution (e.g. "auto_resolve: 18, suggest: 6, new_column: 5").
        const outcomeCounts: Record<string, number> = {};
        for (const r of dest) {
          const key = (r.outcome || "—").toLowerCase();
          outcomeCounts[key] = (outcomeCounts[key] ?? 0) + 1;
        }
        const OUTCOME_TONES: Record<string, string> = {
          auto_resolve: "bg-emerald-100 text-emerald-700",
          auto: "bg-emerald-100 text-emerald-700",
          matched: "bg-emerald-100 text-emerald-700",
          suggest: "bg-amber-100 text-amber-700",
          suggested: "bg-amber-100 text-amber-700",
          new_column: "bg-slate-100 text-slate-600",
          "new column": "bg-slate-100 text-slate-600",
        };
        return (
        <CollapsibleStepCard code="B21.1" title="Destination column mapping" tone="CoA" stage="column_to_destination" meta={`${s.columns_mapped ?? 0} of ${s.columns_analyzed ?? dest.length} mapped · canonical source key`}>
          {/* Step-2-style summary cards */}
          <div className="px-5 pt-3">
            <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 mb-3">
              {[
                { label: "Auto-matched", value: countAuto, color: "text-green-600" },
                { label: "Suggested", value: countSuggest, color: "text-amber-600" },
                { label: "New column", value: countNew, color: "text-slate-700" },
                { label: "Tables", value: distinctDestTables.size, color: "text-indigo-700" },
              ].map(({ label, value, color }) => (
                <div key={label} className="rounded-xl border border-slate-200 bg-white shadow-sm p-3 text-center">
                  <div className={`text-xl font-bold font-mono ${color}`}>{value}</div>
                  <div className="text-[10.5px] text-slate-500 mt-0.5">{label}</div>
                </div>
              ))}
            </div>
            {/* Outcome chips */}
            {Object.keys(outcomeCounts).length > 0 ? (
              <div className="flex flex-wrap gap-1.5 mb-3">
                {Object.entries(outcomeCounts)
                  .sort((a, b) => b[1] - a[1])
                  .map(([outcome, count]) => (
                    <span
                      key={outcome}
                      className={`inline-flex items-center gap-1 px-2.5 py-1 rounded-full text-[10.5px] font-medium ${OUTCOME_TONES[outcome] ?? "bg-slate-100 text-slate-600"}`}
                    >
                      {outcome}: {count}
                    </span>
                  ))}
              </div>
            ) : null}
          </div>
          <div className="px-5 pt-1 text-[10.5px] text-slate-500">
            Auto-resolve requires <b>name AND value AND vocabulary</b> to agree (not name alone). When the
            destination column has data, the source values are checked against it — a name match with
            disjoint values (e.g. asset codes vs vendor ids) becomes a <b>new column</b>, not a merge.
          </div>
          {/* Grouped by SOURCE TABLE, each collapsible — track one table's destination mapping at a
              time (same pattern as B14.1) instead of one long flat list. */}
          {(() => {
            const byTable = new Map<string, typeof dest>();
            for (const r of dest) {
              const tbl = r.source.split(".")[0] ?? "—";
              if (!byTable.has(tbl)) byTable.set(tbl, []);
              byTable.get(tbl)!.push(r);
            }
            const renderRow = (r: (typeof dest)[number]) => {
              // Use the SAME effectiveCanonical map every other panel reads from
              // (B14.1, B19.1, B20.1) so B21 stays consistent — when B14 says the
              // canonical is 'site_id', B21 reads 'sites.site_id (was sites.id)'.
              // effectiveCanonical is the backend's column_canonical overlaid with
              // any pending user pins, so this also covers live overrides without
              // a separate code path. Cross-entity pins are still guarded: when a
              // pin would clash with the destination table (asset_id ↛ vendors.id),
              // the entity-conflict check keeps the source name in place.
              const liveKey = _canonKey(r.source);
              const pin = _pinnedCanonOf(r.source);
              const pinClashes =
                !!pin && !!_entityConflict(pin, r.matched_column ?? r.source.split(".")[1] ?? "", r.dest_table);
              const displaySrc = pinClashes
                ? r.source
                : (liveKey !== r.source ? liveKey : (r.canonical_source ?? r.source));
              const renamed = displaySrc !== r.source;
              const pct = (x?: number | null) => (typeof x === "number" ? `${Math.round(x * 100)}%` : "–");
              const sc = r.scores;
              // Unified canonical chip — ALWAYS visible, drawn from
              // r.canonical_name (the group/FM canonical the column intelligence
              // assigned). For ungrouped columns falls back to the column name
              // so every row has a canonical label visible, even when it
              // equals the source.
              const sourceCol = r.source.split(".")[1] ?? r.source;
              const canonicalLabel = (r.canonical_name && r.canonical_name.trim()) || sourceCol;
              const canonicalEqualsSource = canonicalLabel.toLowerCase() === sourceCol.toLowerCase();
              return [
                <div className="leading-tight space-y-0.5">
                  <span className="font-mono text-indigo-700">{displaySrc}</span>
                  <div className="flex items-center gap-1 mt-0.5">
                    <span
                      className={`inline-flex items-center font-mono text-[9.5px] px-1.5 py-0.5 rounded-full border ${
                        canonicalEqualsSource
                          ? "bg-slate-50 text-slate-500 border-slate-200"
                          : "bg-indigo-50 text-indigo-700 border-indigo-200"
                      }`}
                      title="Canonical name from column intelligence (B19)"
                    >
                      canonical: {canonicalLabel}
                    </span>
                  </div>
                  {renamed ? (
                    <div className="font-mono text-[10px] text-slate-400">was {r.source}</div>
                  ) : null}
                </div>,
                r.dest_table ? <span className="font-mono text-slate-600">{r.dest_table}</span> : <span className="text-slate-400">—</span>,
                r.matched_column ? <span className="font-mono text-slate-700">{r.matched_column}</span> : <span className="text-slate-400">—</span>,
                sc ? (
                  <span className="font-mono text-[10px] text-slate-500" title="name · value · vocabulary · context → final">
                    {pct(sc.name)}·
                    <span className={typeof sc.value === "number" && sc.value < 0.5 ? "font-semibold text-red-600" : undefined}>{pct(sc.value)}</span>·
                    {pct(sc.ontology)}·{pct(sc.context)}
                    <span className="text-slate-300"> → </span>
                    <span className="font-semibold text-slate-700">{pct(sc.final)}</span>
                  </span>
                ) : typeof r.confidence === "number" ? (
                  <span className={`font-mono ${_confTone(r.confidence)}`}>{Math.round(r.confidence * 100)}% conf</span>
                ) : <span className="text-slate-400">—</span>,
                <span className={`inline-flex items-center px-1.5 py-0.5 rounded-full text-[10px] font-medium ${/auto|matched/i.test(r.outcome) ? "bg-emerald-100 text-emerald-700" : /suggest/i.test(r.outcome) ? "bg-amber-100 text-amber-700" : "bg-slate-100 text-slate-500"}`}>{r.outcome}</span>,
              ];
            };
            return [...byTable.entries()].map(([tbl, rows]) => (
              <details key={tbl} className="group border-t border-slate-100">
                <summary className="flex cursor-pointer list-none items-center gap-2 px-5 py-2 text-[11.5px] hover:bg-slate-50 select-none [&::-webkit-details-marker]:hidden">
                  <span className="text-slate-400 transition-transform group-open:rotate-90">▸</span>
                  <span className="font-mono font-semibold text-slate-700">{tbl}</span>
                  <span className="rounded-full bg-slate-100 px-2 py-0.5 text-[10px] text-slate-500">{rows.length} columns</span>
                </summary>
                <StepTable
                  head={["Source (canonical)", "Dest table", "Matched column", "Scores  name·value·onto·ctx → final", "Outcome"]}
                  rows={rows.map(renderRow)}
                />
              </details>
            ));
          })()}
        </CollapsibleStepCard>
        );
      })() : null}

      {/* Summary card */}
      <div className="mb-4 rounded-xl border border-indigo-200 bg-indigo-50/40 p-4">
        <div className="text-[11px] font-semibold uppercase tracking-wider text-indigo-700 mb-2">Column Analysis Summary</div>
        <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-6 gap-2 text-center">
          {[
            ["Analyzed", s.columns_analyzed],
            ["PK groups", s.pk_groups],
            ["FK groups", s.fk_groups],
            ["Shared", s.shared_groups],
            ["Mapped", s.columns_mapped],
            ["Confidence", typeof s.confidence === "number" ? `${Math.round(s.confidence * 100)}%` : "—"],
          ].map(([label, value]) => (
            <div key={String(label)} className="rounded-lg border border-indigo-100 bg-white px-2 py-2">
              <div className="font-mono text-base font-bold text-indigo-700">{value ?? 0}</div>
              <div className="text-[10px] text-slate-500">{label as string}</div>
            </div>
          ))}
        </div>
      </div>
      </>
      ) : null}
      </SequentialReveal>
    </div>
  );
}

function ArrowRightChar() {
  return <ChevronRight size={12} className="shrink-0 text-slate-400" />;
}

function num(n: number | null | undefined): string {
  return typeof n === "number" ? n.toLocaleString() : "—";
}

/** #6 — per-component column-match score breakdown (semantic / keyword /
 *  datatype / numeric pattern / ontology / final). Surfaces the numeric-pattern
 *  similarity that previously wasn't displayed. */
export function ScoreBreakdownChips({ breakdown }: { breakdown?: ColumnScoreBreakdown | null }) {
  if (!breakdown) return null;
  const items: Array<[string, number]> = [
    ["Semantic", breakdown.semantic_score],
    ["Keyword", breakdown.keyword_score],
    ["Datatype", breakdown.datatype_score],
    ["Numeric pattern", breakdown.numeric_pattern_score],
    ["Vocabulary", breakdown.ontology_score],
  ];
  const tone = (v: number) =>
    v >= 0.85 ? "bg-emerald-500" : v >= 0.6 ? "bg-amber-500" : "bg-red-400";
  const pct = (v: number) => `${Math.round((v ?? 0) * 100)}%`;
  return (
    <div className="mt-1.5 mb-1 rounded-lg border border-slate-200 bg-slate-50/70 px-2.5 py-2">
      <div className="mb-1 flex items-center justify-between">
        <span className="text-[9px] font-semibold uppercase tracking-wide text-slate-500">Match breakdown</span>
        <span className="text-[10px] font-medium text-slate-700">
          Final {pct(breakdown.final_score)}
        </span>
      </div>
      <div className="grid grid-cols-2 gap-x-3 gap-y-1 sm:grid-cols-3">
        {items.map(([label, v]) => (
          <div key={label} className="flex items-center gap-1.5" title={`${label}: ${pct(v)}`}>
            <span className="w-[5.5rem] shrink-0 truncate text-[9.5px] text-slate-500">{label}</span>
            <span className="h-1 flex-1 overflow-hidden rounded-full bg-slate-200">
              <span className={`block h-full rounded-full ${tone(v)}`} style={{ width: pct(v) }} />
            </span>
            <span className="w-7 shrink-0 text-right font-mono text-[9px] tabular-nums text-slate-500">{pct(v)}</span>
          </div>
        ))}
      </div>
    </div>
  );
}

/** #10 — uploaded documents + their extracted tables (shown before mapping). */
export function DocumentSummary({ inventory }: { inventory?: DocumentInventory | null }) {
  if (!inventory?.files?.length) return null;
  return (
    <div className="mb-4 rounded-xl border border-slate-200 bg-white p-4 shadow-sm">
      <div className="mb-2 flex items-center gap-2 text-sm font-semibold text-slate-700">
        <FileText size={14} className="text-indigo-600" />
        Uploaded documents &amp; extracted tables
      </div>
      <div className="space-y-2.5">
        {inventory.files.map((f) => (
          <div key={f.file_name}>
            <div className="flex flex-wrap items-center gap-2 text-[12px] font-medium text-slate-800">
              <span className="font-mono">{f.file_name}</span>
              <span className="rounded bg-slate-100 px-1.5 py-0.5 text-[10px] uppercase text-slate-500">
                {f.file_type}
              </span>
              {f.sheet_count > 1 ? (
                <span className="text-[10px] text-slate-400">{f.sheet_count} sheets</span>
              ) : null}
              <span className="text-[10px] text-slate-400">
                {f.table_count} table{f.table_count === 1 ? "" : "s"}
              </span>
            </div>
            <ul className="ml-2 mt-1 space-y-0.5 border-l border-slate-200 pl-3">
              {f.tables.map((t, i) => (
                <li key={t.table_name} className="flex flex-wrap items-center gap-2 text-[11px] text-slate-600">
                  <span className="text-slate-300">{i === f.tables.length - 1 ? "└──" : "├──"}</span>
                  <span className="font-mono text-slate-700">{t.table_name}</span>
                  <span className="text-[10px] text-slate-400">
                    {num(t.row_count)} rows · {num(t.column_count)} cols
                  </span>
                </li>
              ))}
            </ul>
          </div>
        ))}
      </div>
    </div>
  );
}

/** #9 — full table + column metadata (untruncated), expand a table for columns. */
export function TablesMetadataPanel({ tables }: { tables?: TableMeta[] | null }) {
  const [open, setOpen] = useState<string | null>(null);
  if (!tables?.length) return null;
  return (
    <div className="mb-6 overflow-hidden rounded-xl border border-slate-200 bg-white shadow-sm">
      <div className="flex items-center gap-2 border-b border-slate-100 px-5 py-3 text-sm font-semibold text-slate-700">
        <Database size={14} className="text-slate-500" />
        Table &amp; column metadata
        <span className="text-[10px] font-normal text-slate-400">({tables.length} tables)</span>
      </div>
      <div className="divide-y divide-slate-100">
        {tables.map((t) => {
          const expanded = open === t.table_name;
          return (
            <div key={t.table_name}>
              <button
                type="button"
                onClick={() => setOpen(expanded ? null : t.table_name)}
                className="flex w-full flex-wrap items-center gap-x-3 gap-y-1 px-5 py-2.5 text-left hover:bg-slate-50"
              >
                {expanded ? (
                  <ChevronDown size={13} className="shrink-0 text-slate-400" />
                ) : (
                  <ChevronRight size={13} className="shrink-0 text-slate-400" />
                )}
                <span className="font-mono text-[12px] font-medium text-slate-800">{t.table_name}</span>
                <span className="text-[10px] text-slate-400">
                  {num(t.row_count)} rows · {num(t.column_count)} cols
                </span>
                {t.primary_keys.length ? (
                  <span className="rounded bg-emerald-50 px-1.5 py-0.5 text-[10px] text-emerald-700">
                    PK: {t.primary_keys.join(", ")}
                    {t.composite_primary_key ? " (composite)" : ""}
                  </span>
                ) : null}
                {t.surrogate_key ? (
                  <span
                    className="rounded bg-violet-50 px-1.5 py-0.5 text-[10px] text-violet-700"
                    title="No natural primary key was found — a surrogate key is generated for this table"
                  >
                    surrogate key
                  </span>
                ) : null}
                {t.foreign_keys.length ? (
                  <span className="rounded bg-blue-50 px-1.5 py-0.5 text-[10px] text-blue-700">
                    FK: {t.foreign_keys.join(", ")}
                  </span>
                ) : null}
                <span className="ml-auto font-mono text-[10px] text-slate-400">{t.source_file}</span>
              </button>
              {expanded ? (
                <div className="overflow-x-auto px-5 pb-3">
                  <table className="w-full border-collapse text-[11px]">
                    <thead>
                      <tr className="text-slate-400">
                        <th className="py-1 pr-3 text-left font-medium">Column</th>
                        <th className="py-1 pr-3 text-left font-medium">Type</th>
                        <th className="py-1 pr-3 text-left font-medium">Nullable</th>
                        <th className="py-1 pr-3 text-left font-medium">Unique</th>
                        <th className="py-1 text-left font-medium">Sample values</th>
                      </tr>
                    </thead>
                    <tbody>
                      {t.columns.map((c) => (
                        <tr key={c.column_name} className="border-t border-slate-100">
                          <td className="py-1 pr-3 font-mono text-slate-700">
                            {c.column_name}
                            {t.primary_keys.includes(c.column_name) ? (
                              <span className="ml-1 text-emerald-600">★</span>
                            ) : null}
                          </td>
                          <td className="py-1 pr-3 text-slate-500">{c.datatype}</td>
                          <td className="py-1 pr-3 text-slate-500">{c.nullable ? "yes" : "no"}</td>
                          <td className="py-1 pr-3 text-slate-500">{c.unique ? "yes" : "no"}</td>
                          <td className="max-w-[18rem] truncate py-1 font-mono text-slate-400">
                            {c.sample_values.join(", ")}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              ) : null}
            </div>
          );
        })}
      </div>
    </div>
  );
}
