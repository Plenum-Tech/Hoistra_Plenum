"use client";
import { useState } from "react";
import { CheckCircle, KeyRound } from "lucide-react";
import {
  useMigrationGateClassificationApproval,
  type MigrationClassificationGatePayload,
  type UdrGroupClassification,
} from "../../../chat-api";

interface Props {
  migrationId: string;
  payload: MigrationClassificationGatePayload;
  onSubmitted: () => void;
}

/** Per-group decision the user can make at the B20.1 gate. */
type Choice = "fk" | "shared" | "exclude";

/**
 * B20.1 gate — HITL approval of the PK / FK / Shared-Attribute assignment. The pipeline is
 * interrupted here and does NOT proceed until the user approves. For every FK / shared group the
 * user can keep the detected classification, RE-CLASSIFY it (FK → Shared Attribute drops the
 * enforcement and synthesises a lookup table instead; Shared → FK skips the lookup table), or
 * EXCLUDE it entirely. Primary-Key groups are shown read-only for observability — the PK itself
 * was signed off at the earlier PK gate. On submit, excluded group_ids go up as `rejected_groups`
 * and changed verdicts as `verdict_overrides`.
 */
export default function GateClassificationApproval({ migrationId, payload, onSubmitted }: Props) {
  const cls = payload.classification ?? [];
  const fkRows = cls.filter((c) => /foreign/i.test(c.verdict));
  const sharedRows = cls.filter((c) => /shared/i.test(c.verdict));
  // PRIMARY KEYS = EVERY distinct PK anchor across ALL groups, not just the standalone PK-verdict
  // groups. A PK that an FK group references (sites.site_id, technicians.tech_id, vendors.vendor_id
  // as the target of G2 / G4 / G5) is still a primary key, so it must be listed here too — matching
  // the B20.1 · PK card's count (6), which was showing 3 before.
  const _pkSeen = new Set<string>();
  const pkRows = cls
    .map((c) => ({
      group_id: c.group_id,
      pk:
        c.canonical_has_pk ??
        c.has_pk ??
        (/primary/i.test(c.verdict) ? c.canonical_name ?? null : null),
    }))
    .filter((r): r is { group_id: string; pk: string } => {
      if (!r.pk || _pkSeen.has(r.pk)) return false;
      _pkSeen.add(r.pk);
      return true;
    });
  const lookupByGroup = new Map(
    (payload.shared_attribute_tables ?? []).map((t) => [t.from_group, t]),
  );

  const defaultChoice = (c: UdrGroupClassification): Choice =>
    /foreign/i.test(c.verdict) ? "fk" : "shared";

  const [choices, setChoices] = useState<Record<string, Choice>>(() =>
    Object.fromEntries([...fkRows, ...sharedRows].map((c) => [c.group_id, defaultChoice(c)])),
  );
  const [error, setError] = useState<string | null>(null);
  const { mutate, isPending } = useMigrationGateClassificationApproval({
    onSuccess: () => onSubmitted(),
    onError: (e) => setError(e instanceof Error ? e.message : "Failed to submit classification"),
  });

  const setChoice = (gid: string, choice: Choice) =>
    setChoices((prev) => ({ ...prev, [gid]: choice }));

  const reviewable = [...fkRows, ...sharedRows];
  const excludedCount = reviewable.filter((c) => choices[c.group_id] === "exclude").length;
  const changedCount = reviewable.filter(
    (c) => choices[c.group_id] !== "exclude" && choices[c.group_id] !== defaultChoice(c),
  ).length;

  const handleApprove = () => {
    setError(null);
    const rejected = reviewable
      .map((c) => c.group_id)
      .filter((g) => choices[g] === "exclude");
    const overrides: Record<string, "fk" | "shared"> = {};
    for (const c of reviewable) {
      const choice = choices[c.group_id];
      if (choice !== "exclude" && choice !== defaultChoice(c)) overrides[c.group_id] = choice;
    }
    mutate({
      migrationId,
      body: { rejected_groups: rejected, verdict_overrides: overrides },
    });
  };

  /** Segmented FK / Shared / Exclude control. FK is disabled for a shared group with no PK
   *  member — there is nothing to enforce the FK against. */
  const ChoicePicker = ({ row }: { row: UdrGroupClassification }) => {
    const gid = row.group_id;
    const current = choices[gid];
    const fkDisabled = !/foreign/i.test(row.verdict) && !row.has_pk && !row.canonical_has_pk;
    const btn = (value: Choice, label: string, disabled = false, title?: string) => (
      <button
        type="button"
        disabled={disabled}
        title={title}
        onClick={() => setChoice(gid, value)}
        className={`rounded-md px-2.5 py-1 text-[11px] font-medium transition-colors ${
          current === value
            ? value === "exclude"
              ? "bg-red-600 text-white"
              : "bg-indigo-600 text-white"
            : disabled
              ? "cursor-not-allowed bg-slate-50 text-slate-300"
              : "bg-slate-100 text-slate-600 hover:bg-slate-200"
        }`}
      >
        {label}
      </button>
    );
    return (
      <div className="flex shrink-0 items-center gap-1">
        {btn(
          "fk",
          "Foreign Key",
          fkDisabled,
          fkDisabled ? "No primary-key member to enforce this FK against" : undefined,
        )}
        {btn("shared", "Shared Attribute")}
        {btn("exclude", "Exclude")}
      </div>
    );
  };

  // ── Emitted DDL, under the choices as they stand right now ────────────────────────────
  // The B22.1 "New tables from shared attributes" card is hidden while this gate is open, so
  // until you approve there was nowhere to see what a Shared Attribute actually produces. Worse,
  // the payload behind that card is built BEFORE the answer, so a group re-classified here could
  // not appear in it at all. This panel is built from the live choices instead.
  const q = (n: string) => (/^[a-z_][a-z0-9_]*$/.test(n) ? n : `"${String(n).replace(/"/g, '""')}"`);

  // Every source table in the run — the backend appends "_ref" when a lookup would collide with
  // one, so the preview has to apply the same rule or it names a table that never appears.
  const sourceTables = new Set<string>();
  cls.forEach((c) => (c.members ?? []).forEach((m) => {
    const t = String(m).split(".")[0];
    if (t) sourceTables.add(t);
  }));

  const previewDdl = (canonical: string, members: string[]) => {
    const pk = canonical;
    const table = sourceTables.has(pk) ? `${pk}_ref` : pk;
    const pairs = members.map((m) => String(m).split(".")).filter((x) => x.length === 2);
    const lines = [
      `CREATE TABLE IF NOT EXISTS plenum_cafm.${q(table)} (`,
      `    ${q(pk)} TEXT PRIMARY KEY`,
      `);`,
      `COMMENT ON TABLE plenum_cafm.${q(table)} IS 'Shared attribute normalised into a lookup ` +
        `table. Source columns, each now a foreign key into this table: ` +
        `${pairs.map(([t, c]) => `${t}.${c}`).join(", ")}.';`,
    ];
    pairs.forEach(([t, c]) => lines.push(
      `ALTER TABLE plenum_cafm.${q(t)} ADD CONSTRAINT ${q(`fk_${t}_${c}`)} ` +
      `FOREIGN KEY (${q(c)}) REFERENCES plenum_cafm.${q(table)} (${q(pk)});`));
    return lines.join("\n");
  };

  const ddlEntries = [...fkRows, ...sharedRows]
    .filter((c) => choices[c.group_id] === "shared")
    .map((c) => {
      const lk = lookupByGroup.get(c.group_id);
      const changed = choices[c.group_id] !== defaultChoice(c);
      if (lk && !changed) {
        return {
          gid: c.group_id, table: lk.table_name, preview: false,
          meta: `${lk.distinct_count} values`,
          ddl: [lk.create_ddl, ...(lk.fk_ddl ?? [])].filter(Boolean).join("\n"),
        };
      }
      if (changed) {
        const name = c.canonical_name ?? "";
        if (!name) return null;
        return {
          gid: c.group_id, table: sourceTables.has(name) ? `${name}_ref` : name, preview: true,
          meta: "row count known on submit",
          ddl: previewDdl(name, (c.members ?? []).map(String)),
        };
      }
      // An unchanged shared group with no lookup table is one the service declined to normalise
      // (a yes/no column is not a vocabulary). Promising DDL for it here would be a lie.
      return { gid: c.group_id, table: c.canonical_name ?? "", preview: false,
               meta: "no lookup table — the values are not a vocabulary", ddl: "" };
    })
    .filter(Boolean) as Array<{ gid: string; table: string; preview: boolean; meta: string; ddl: string }>;

  const RowShell = ({
    row,
    children,
  }: {
    row: UdrGroupClassification;
    children: React.ReactNode;
  }) => {
    const choice = choices[row.group_id];
    const changed = choice !== "exclude" && choice !== defaultChoice(row);
    return (
      <div
        className={`flex items-start justify-between gap-3 rounded-lg border p-2.5 transition-colors ${
          choice === "exclude"
            ? "border-red-200 bg-red-50"
            : changed
              ? "border-indigo-300 bg-indigo-50/40"
              : "border-slate-200 hover:border-indigo-200"
        }`}
      >
        <div className="min-w-0 flex-1">
          {children}
          {choice === "exclude" ? (
            <div className="mt-1 text-[10px] font-medium text-red-600">
              Excluded — nothing from this group will be applied.
            </div>
          ) : changed ? (
            <div className="mt-1 text-[10px] font-medium text-indigo-700">
              {choice === "shared"
                ? "Re-classified: FK will NOT be enforced — a lookup table is created instead."
                : "Re-classified: treated as a Foreign Key — its lookup table will NOT be created."}
            </div>
          ) : null}
        </div>
        <ChoicePicker row={row} />
      </div>
    );
  };

  return (
    <div className="max-w-6xl">
      <div className="mb-4">
        <div className="flex flex-wrap items-center gap-2">
          <span className="font-mono text-[11px] text-slate-400">B20.1</span>
          <span className="text-lg font-semibold text-slate-800">
            Primary-key, foreign-key &amp; shared-attribute assignment
          </span>
          <span className="inline-flex items-center rounded bg-indigo-50 px-1.5 py-0.5 text-[10px] font-medium text-indigo-700">
            Action
          </span>
          <span className="inline-flex items-center gap-1 rounded-full bg-amber-100 px-2 py-0.5 text-[10px] font-semibold text-amber-800">
            Human approval required
          </span>
          <span className="ml-auto text-[11px] text-slate-400">
            {pkRows.length} PK · {fkRows.length} FK · {sharedRows.length} Shared
          </span>
        </div>
        <div className="mt-1 text-sm text-slate-500">
          Observability + human-in-the-loop for the whole B20 classification in one card. The
          pipeline is paused until you approve. For each group keep the detected classification,
          switch it between Foreign Key and Shared Attribute, or exclude it. A FK demoted to Shared
          Attribute is not enforced and gets a lookup table instead; a Shared Attribute promoted to
          FK skips its lookup table.
        </div>
      </div>

      {pkRows.length ? (
        <div className="mb-4">
          <div className="mb-1.5 text-[11px] font-semibold uppercase tracking-wider text-slate-500">
            Primary keys ({pkRows.length}) — approved at the primary-key gate
          </div>
          <div className="grid gap-1.5 sm:grid-cols-2">
            {pkRows.map((r) => (
              <div
                key={r.pk}
                className="flex items-center gap-2 rounded-lg border border-emerald-100 bg-emerald-50/50 px-2.5 py-1.5"
              >
                <KeyRound size={12} className="shrink-0 text-emerald-600" />
                <span className="truncate font-mono text-[10.5px] text-emerald-800">
                  {r.pk}
                </span>
                <span className="ml-auto shrink-0 rounded-full bg-emerald-100 px-2 py-0.5 text-[10px] font-medium text-emerald-700">
                  PK
                </span>
              </div>
            ))}
          </div>
        </div>
      ) : null}

      {fkRows.length ? (
        <div className="mb-4">
          <div className="mb-1.5 text-[11px] font-semibold uppercase tracking-wider text-slate-500">
            Foreign keys ({fkRows.length})
          </div>
          <div className="space-y-2">
            {fkRows.map((c) => {
              const pk = c.canonical_has_pk ?? c.has_pk;
              const fks = (c.members ?? []).filter((m) => m !== pk && m !== c.has_pk);
              return (
                <RowShell key={c.group_id} row={c}>
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="font-mono text-[11px] font-semibold text-slate-700">{c.group_id}</span>
                    <span className="inline-flex items-center rounded-full bg-amber-100 px-2 py-0.5 text-[10px] font-medium text-amber-700">FK</span>
                    {typeof c.ri === "number" ? (
                      <span
                        className="text-[10px] text-slate-500"
                        title="Referential integrity — share of FK values that resolve to the referenced primary key"
                      >
                        RI {Math.round(c.ri * 100)}%
                      </span>
                    ) : null}
                  </div>
                  <div className="mt-1 flex flex-wrap items-center gap-1.5 font-mono text-[10.5px]">
                    <span className="text-amber-700">{(fks.length ? fks : [c.canonical_name ?? ""]).join(" · ")}</span>
                    <span className="text-slate-300">→</span>
                    <span className="text-emerald-700">{pk}</span>
                  </div>
                  {c.owned_by_destination ? (
                    <div className="mt-1 text-[10px] text-slate-500">
                      No table in this file owns this column, but{" "}
                      <b className="font-mono">{String(c.owned_by_destination).split(".")[0]}</b>{" "}
                      already exists in the destination and does — so these columns reference it.
                      Switching to Shared Attribute would create a second table of the same values.
                    </div>
                  ) : null}
                </RowShell>
              );
            })}
          </div>
        </div>
      ) : null}

      {sharedRows.length ? (
        <div className="mb-4">
          <div className="mb-1.5 text-[11px] font-semibold uppercase tracking-wider text-slate-500">
            Shared attributes ({sharedRows.length})
          </div>
          <div className="space-y-2">
            {sharedRows.map((c) => {
              const lk = lookupByGroup.get(c.group_id);
              return (
                <RowShell key={c.group_id} row={c}>
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="font-mono text-[11px] font-semibold text-slate-700">{c.group_id}</span>
                    <span className="inline-flex items-center rounded-full bg-slate-100 px-2 py-0.5 text-[10px] font-medium text-slate-600">Shared</span>
                    <span className="font-mono text-[10.5px] text-indigo-700">{c.canonical_name}</span>
                  </div>
                  {lk && choices[c.group_id] === "shared" ? (
                    <div className="mt-1 font-mono text-[10px] text-emerald-700">
                      → creates lookup table <b>{lk.table_name}</b> (PK: {lk.pk_column}, {lk.distinct_count} values)
                    </div>
                  ) : (
                    <div className="mt-1 font-mono text-[10px] text-slate-500">{(c.members ?? []).join(" · ")}</div>
                  )}
                </RowShell>
              );
            })}
          </div>
        </div>
      ) : null}

      {ddlEntries.length ? (
        <div className="mb-4">
          <div className="mb-1.5 text-[11px] font-semibold uppercase tracking-wider text-slate-500">
            Emitted DDL — {ddlEntries.filter((e) => e.ddl).length} lookup table
            {ddlEntries.filter((e) => e.ddl).length === 1 ? "" : "s"} under your current choices
          </div>
          <div className="mb-2 text-[11px] text-slate-500">
            Every table that will be created, and the foreign key added to each table that shares the
            column. This follows the buttons above, so a group you re-classify appears here straight
            away &mdash; marked <b>preview</b>, because its rows are counted only once you submit.
          </div>
          <div className="space-y-2">
            {ddlEntries.map((e) => (
              <div key={e.gid}>
                <div className="flex flex-wrap items-center gap-2 px-0.5 pb-1">
                  <span className="font-mono text-[10px] text-slate-400">{e.gid}</span>
                  <span className="font-mono text-[10.5px] font-semibold text-emerald-700">{e.table}</span>
                  {e.preview ? (
                    <span className="inline-flex items-center rounded-sm bg-indigo-100 px-1 text-[8.5px] font-semibold uppercase text-indigo-800">
                      preview
                    </span>
                  ) : null}
                  <span className="text-[10px] text-slate-400">{e.meta}</span>
                </div>
                {e.ddl ? (
                  <pre className="overflow-x-auto rounded bg-slate-900 p-2.5 text-[10px] leading-relaxed text-slate-100">
                    <code>{e.ddl}</code>
                  </pre>
                ) : null}
              </div>
            ))}
          </div>
        </div>
      ) : null}

      {error ? (
        <div className="mb-4 rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">{error}</div>
      ) : null}

      <button
        type="button"
        onClick={handleApprove}
        disabled={isPending}
        className="inline-flex items-center gap-2 rounded-lg bg-indigo-600 px-8 py-3 text-base font-medium text-white transition-colors hover:bg-indigo-700 disabled:opacity-50"
      >
        {isPending ? (
          <>
            <span className="h-4 w-4 animate-spin rounded-full border-2 border-white border-t-transparent" />
            Submitting…
          </>
        ) : (
          <>
            <CheckCircle size={18} />
            {excludedCount || changedCount
              ? `Apply decisions (${changedCount ? `${changedCount} re-classified` : ""}${changedCount && excludedCount ? ", " : ""}${excludedCount ? `${excludedCount} excluded` : ""}) & continue`
              : "Approve all & continue"}
          </>
        )}
      </button>
    </div>
  );
}
