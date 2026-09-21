// Migration — CSV / Excel into plenum_cafm through svc-ai-schema-mapper's gated pipeline.
// `vals` is the view model from useHoistra() (logic/migration.js's mgVals). Every control
// here is a callback on a row the view model shaped; the screen decides nothing itself.
import React from 'react';

const BARE = { font: "inherit", background: "transparent", border: "none", padding: "0", margin: "0", cursor: "pointer", color: "inherit" };
const KICKER = { fontSize: "10px", letterSpacing: "0.12em", textTransform: "uppercase", color: "var(--color-neutral-500)" };
const MONO = { fontFamily: "ui-monospace,monospace", fontVariantNumeric: "tabular-nums" };
const CARD = { padding: "16px 18px", borderRadius: "10px", background: "var(--color-surface)", boxShadow: "var(--shadow-sm)" };
const TH = { textAlign: "left", fontSize: "10px", letterSpacing: "0.09em", textTransform: "uppercase", color: "var(--color-neutral-500)", padding: "6px 8px", borderBottom: "1px solid var(--color-divider)", whiteSpace: "nowrap" };
const TD = { padding: "7px 8px", borderBottom: "1px solid var(--color-divider)", fontSize: "12.5px", verticalAlign: "top" };
const SELECT = { fontSize: "12px", padding: "5px 8px", minHeight: "0", borderRadius: "6px", maxWidth: "100%" };

// A row of small exclusive buttons — Approve / Semantic, Keep / Reject, and so on.
function Seg({ items }) {
  return (
    <div style={{ display: "inline-flex", gap: "3px", flexWrap: "wrap" }}>
      {(items || []).map((o) => (
        <button key={o.value} type="button" onClick={o.pick} aria-pressed={o.on}
          style={{ ...BARE, fontSize: "11px", padding: "4px 9px", borderRadius: "6px", whiteSpace: "nowrap",
            border: "1px solid " + (o.on ? (o.tone === "var(--color-text)" ? "var(--color-accent)" : o.tone) : "var(--color-divider)"),
            background: o.on ? (o.tone === "var(--color-text)" ? "var(--color-accent-900)" : "color-mix(in srgb, " + o.tone + " 12%, transparent)") : "transparent",
            color: o.on ? (o.tone === "var(--color-text)" ? "var(--color-accent)" : o.tone) : "var(--color-neutral-400)" }}>
          {o.label}
        </button>
      ))}
    </div>
  );
}

function Pill({ label, tone, bg }) {
  return (
    <span style={{ display: "inline-flex", alignItems: "center", gap: "6px", fontSize: "11px", padding: "3px 9px", borderRadius: "999px", background: bg, color: tone, whiteSpace: "nowrap" }}>
      <span style={{ width: "6px", height: "6px", borderRadius: "50%", background: tone }}></span>
      {label}
    </span>
  );
}

function Changed({ on }) {
  return on ? <span title="Changed from the pipeline's proposal" style={{ ...MONO, fontSize: "9.5px", color: "var(--color-accent)", marginLeft: "6px" }}>{"edited"}</span> : null;
}

function Table({ head, children }) {
  return (
    <div style={{ overflowX: "auto" }}>
      <table style={{ width: "100%", borderCollapse: "collapse" }}>
        <thead><tr>{head.map((h, i) => <th key={i} style={TH}>{h}</th>)}</tr></thead>
        <tbody>{children}</tbody>
      </table>
    </div>
  );
}

// ── the gate bodies ─────────────────────────────────────────────────────────────────

function PkGate({ vals }) {
  return (
    <Table head={["Source table", "Detected key", "Kind", "Confidence", "Key to use"]}>
      {(vals.pkRows || []).map((r) => (
        <tr key={r.table}>
          <td style={{ ...TD, fontWeight: "500" }}>{r.table}</td>
          <td style={{ ...TD, ...MONO, fontSize: "12px" }}>{r.detected}</td>
          <td style={TD}>{r.kind}</td>
          <td style={{ ...TD, ...MONO }}>{r.confidence}</td>
          <td style={TD}>
            <select className="input" value={r.chosen} onChange={r.pick} style={SELECT}>
              {r.options.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
            </select>
            <Changed on={r.changed} />
          </td>
        </tr>
      ))}
    </Table>
  );
}

function UniqueTablesGate({ vals }) {
  return (
    <>
      <Table head={["Source table", "Destination", "Matched by", "Confidence", "Confirmed key"]}>
        {(vals.utRows || []).map((r) => (
          <tr key={r.source}>
            <td style={{ ...TD, fontWeight: "500" }}>{r.source}</td>
            <td style={{ ...TD, ...MONO, fontSize: "12px" }}>{r.destination}</td>
            <td style={TD}>{r.method}</td>
            <td style={{ ...TD, ...MONO }}>{r.confidence}</td>
            <td style={{ ...TD, ...MONO, fontSize: "12px" }}>{r.pk}</td>
          </tr>
        ))}
      </Table>
      <div style={{ fontSize: "11px", color: "var(--color-neutral-500)", marginTop: "8px" }}>
        {vals.utNote}{(vals.utDuplicates || []).length ? " — duplicates: " + vals.utDuplicates.join("; ") : ""}
      </div>
    </>
  );
}

function PreSemanticGate({ vals }) {
  if (vals.psRouting) {
    return (
      <Table head={["Source table", "Suggested destination", "Destination to use"]}>
        {(vals.psTables || []).map((t) => (
          <tr key={t.table}>
            <td style={{ ...TD, fontWeight: "500" }}>{t.table}</td>
            <td style={{ ...TD, ...MONO, fontSize: "12px" }}>{t.suggested}</td>
            <td style={TD}>
              <div style={{ display: "flex", gap: "6px", alignItems: "center", flexWrap: "wrap" }}>
                <select className="input" value={t.chosen} onChange={t.pick} style={{ ...SELECT, width: "220px" }}>
                  {t.options.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
                </select>
                <input className="input" placeholder="or type a new table name" onBlur={t.setNew} style={{ ...SELECT, width: "200px" }} />
                <Changed on={t.changed} />
              </div>
            </td>
          </tr>
        ))}
      </Table>
    );
  }
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: "16px" }}>
      {(vals.psTables || []).map((t) => (
        <div key={t.table}>
          <div style={{ display: "flex", alignItems: "baseline", gap: "10px", marginBottom: "4px" }}>
            <span style={{ fontSize: "13px", fontWeight: "500" }}>{t.table}</span>
            <span style={{ ...MONO, fontSize: "11px", color: "var(--color-neutral-500)" }}>{"→ " + t.target}</span>
          </div>
          <Table head={["Source column", "Matched column", "Confidence", "How", "Sample values", "Decision"]}>
            {t.rows.map((r) => (
              <tr key={r.field}>
                <td style={{ ...TD, ...MONO, fontSize: "12px" }}>{r.field}{r.pk ? <span className="tag tag-outline" style={{ marginLeft: "6px", fontSize: "9px", padding: "1px 6px" }}>{"PK"}</span> : null}</td>
                <td style={{ ...TD, ...MONO, fontSize: "12px" }}>{r.target}</td>
                <td style={{ ...TD, ...MONO }}>{r.confidence}</td>
                <td style={{ ...TD, color: "var(--color-neutral-400)" }}>{r.tier}</td>
                <td style={{ ...TD, color: "var(--color-neutral-400)", maxWidth: "220px", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{r.samples}</td>
                <td style={TD}><Seg items={r.seg} /></td>
              </tr>
            ))}
          </Table>
        </div>
      ))}
      {vals.psSemanticCount ? <div style={{ fontSize: "11px", color: "var(--color-neutral-500)" }}>{vals.psSemanticCount + " column" + (vals.psSemanticCount === 1 ? "" : "s") + " will go to semantic matching."}</div> : null}
    </div>
  );
}

function ClassificationGate({ vals }) {
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: "16px" }}>
      <div>
        <div style={{ ...KICKER, marginBottom: "6px" }}>{"Foreign keys and shared attributes"}</div>
        {(vals.clRows || []).length === 0 ? <div style={{ fontSize: "12px", color: "var(--color-neutral-500)" }}>{"None found between these tables."}</div> : null}
        <div style={{ display: "flex", flexDirection: "column", gap: "8px" }}>
          {(vals.clRows || []).map((r) => (
            <div key={r.id} style={{ display: "flex", flexWrap: "wrap", gap: "8px 12px", alignItems: "center", padding: "10px 12px", borderRadius: "8px", border: "1px solid var(--color-divider)", background: r.chosen === "exclude" ? "var(--st-risk-bg)" : "var(--color-bg)" }}>
              <span style={{ ...MONO, fontSize: "11px", color: "var(--color-neutral-500)", flex: "0 0 32px" }}>{r.id}</span>
              <div style={{ flex: "1 1 260px", minWidth: "0" }}>
                <div style={{ ...MONO, fontSize: "12px", overflowWrap: "anywhere" }}>{r.members}</div>
                <div style={{ fontSize: "11px", color: "var(--color-neutral-500)", marginTop: "2px", lineHeight: "1.4" }}>
                  {[r.detected === "fk" ? "detected as a foreign key" : "detected as a shared attribute", r.ri, r.pk ? "key " + r.pk : "", r.lookup].filter(Boolean).join(" · ")}
                </div>
              </div>
              <div style={{ flex: "0 0 auto" }}><Seg items={r.seg} /><Changed on={r.changed} /></div>
            </div>
          ))}
        </div>
      </div>
      <div>
        <div style={{ ...KICKER, marginBottom: "6px" }}>{"Primary-key groups (fixed)"}</div>
        <div style={{ display: "flex", flexWrap: "wrap", gap: "6px" }}>
          {(vals.clPk || []).map((r) => (
            <span key={r.id} style={{ ...MONO, fontSize: "11px", padding: "4px 8px", borderRadius: "6px", background: "var(--color-neutral-900)" }}>{r.id + " · " + r.members}</span>
          ))}
        </div>
      </div>
      {vals.clExcluded ? <div style={{ fontSize: "11px", color: "var(--st-risk)" }}>{vals.clExcluded + " group" + (vals.clExcluded === 1 ? "" : "s") + " will be dropped."}</div> : null}
    </div>
  );
}

function ColumnMappingGate({ vals }) {
  return (
    <>
      <Table head={["Source column", "Destination table", "Outcome", "Confidence", "Sample values", "Destination column"]}>
        {(vals.cmRows || []).map((r) => (
          <tr key={r.key} style={{ background: r.suggested && !r.changed ? "var(--color-accent-900)" : "transparent" }}>
            <td style={{ ...TD, ...MONO, fontSize: "12px" }}>{r.source}{r.pk ? <span className="tag tag-outline" style={{ marginLeft: "6px", fontSize: "9px", padding: "1px 6px" }}>{"PK"}</span> : null}</td>
            <td style={{ ...TD, ...MONO, fontSize: "12px" }}>{r.dest}</td>
            <td style={{ ...TD, color: r.suggested ? "var(--color-accent)" : "var(--color-neutral-400)" }}>{r.outcome}</td>
            <td style={{ ...TD, ...MONO }}>{r.confidence}</td>
            <td style={{ ...TD, color: "var(--color-neutral-400)", maxWidth: "200px", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{r.samples}</td>
            <td style={TD}>
              <select className="input" value={r.chosen} onChange={r.pick} style={{ ...SELECT, width: "210px" }}>
                {r.options.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
              </select>
              <Changed on={r.changed} />
            </td>
          </tr>
        ))}
      </Table>
      <div style={{ fontSize: "11px", color: "var(--color-neutral-500)", marginTop: "8px" }}>
        {"Highlighted rows are suggestions the matcher wants confirmed. " + (vals.cmNew || 0) + " column" + (vals.cmNew === 1 ? "" : "s") + " will be created new" + (vals.cmChanged ? " · " + vals.cmChanged + " re-targeted by you" : "") + "."}
      </div>
    </>
  );
}

function FieldMappingGate({ vals }) {
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: "16px" }}>
      <div style={{ fontSize: "11px", color: "var(--color-neutral-500)" }}>{vals.fmCounts}</div>
      {vals.fmAlert ? <div style={{ fontSize: "12px", padding: "8px 11px", borderRadius: "8px", background: "var(--st-warn-bg)", color: "var(--st-warn)" }}>{vals.fmAlert}</div> : null}
      {(vals.fmFlagged || []).map((t) => (
        <div key={"f" + t.table}>
          <div style={{ ...KICKER, marginBottom: "6px" }}>{"Flagged · " + t.table}</div>
          <Table head={["Source field", "Suggested target", "Confidence", "Why", "Decision"]}>
            {t.rows.map((r) => (
              <tr key={r.field}>
                <td style={{ ...TD, ...MONO, fontSize: "12px" }}>{r.field}<div style={{ fontFamily: "var(--font-body)", fontSize: "10.5px", color: "var(--color-neutral-500)" }}>{r.samples}</div></td>
                <td style={{ ...TD, ...MONO, fontSize: "12px" }}>{r.suggested}</td>
                <td style={{ ...TD, ...MONO }}>{r.confidence}</td>
                <td style={{ ...TD, color: "var(--color-neutral-400)", maxWidth: "260px" }}>{r.rationale}</td>
                <td style={TD}>
                  <div style={{ display: "flex", gap: "6px", alignItems: "center", flexWrap: "wrap" }}>
                    <Seg items={r.seg} />
                    <select className="input" value={r.mode === "override" ? r.target : ""} onChange={r.pickTarget} style={{ ...SELECT, width: "170px" }}>
                      {r.options.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
                    </select>
                  </div>
                </td>
              </tr>
            ))}
          </Table>
        </div>
      ))}
      {(vals.fmUnmapped || []).map((t) => (
        <div key={"u" + t.table}>
          <div style={{ ...KICKER, marginBottom: "6px" }}>{"Nothing matched · " + t.table}</div>
          <Table head={["Source field", "Would become", "Type", "Decision"]}>
            {t.rows.map((r) => (
              <tr key={r.field}>
                <td style={{ ...TD, ...MONO, fontSize: "12px" }}>{r.field}</td>
                <td style={{ ...TD, ...MONO, fontSize: "12px" }}>{r.chosen === "custom" ? r.target + "." + r.column : r.chosen === "raw_metadata" ? "raw_metadata JSON" : "— not migrated"}</td>
                <td style={{ ...TD, color: "var(--color-neutral-400)" }}>{r.chosen === "custom" ? r.type : ""}</td>
                <td style={TD}><Seg items={r.seg} /></td>
              </tr>
            ))}
          </Table>
        </div>
      ))}
    </div>
  );
}

function HierarchyGate({ vals }) {
  return (
    <div style={{ display: "grid", gridTemplateColumns: "minmax(0,1fr) 220px", gap: "18px" }}>
      <div style={{ minWidth: "0" }}>
        <Table head={["From (child)", "To (parent)", "Type", "Confidence", "Decision"]}>
          {(vals.hiRows || []).map((r) => (
            <tr key={r.key}>
              <td style={{ ...TD, ...MONO, fontSize: "12px" }}>{r.from}</td>
              <td style={{ ...TD, ...MONO, fontSize: "12px" }}>{r.to}</td>
              <td style={{ ...TD, color: "var(--color-neutral-400)" }}>{r.type}</td>
              <td style={{ ...TD, ...MONO }}>{r.confidence}<div style={{ fontSize: "10.5px", color: "var(--color-neutral-500)" }}>{r.match}</div></td>
              <td style={TD}>{r.fixed ? <span style={{ fontSize: "11px", color: "var(--color-neutral-500)" }}>{"platform default"}</span> : <Seg items={r.seg} />}</td>
            </tr>
          ))}
        </Table>
        {(vals.hiImplicit || []).length ? (
          <div style={{ marginTop: "14px" }}>
            <div style={{ ...KICKER, marginBottom: "6px" }}>{"Code-shaped columns (information only)"}</div>
            {(vals.hiImplicit || []).map((r) => (
              <div key={r.column} style={{ fontSize: "12px", color: "var(--color-neutral-400)", padding: "3px 0" }}>
                <span style={MONO}>{r.column}</span>{" · " + r.levels + " levels on “" + r.separator + "” · " + r.examples}
              </div>
            ))}
          </div>
        ) : null}
        <div style={{ fontSize: "11px", color: "var(--color-neutral-500)", marginTop: "10px" }}>{vals.hiNote}{vals.hiRejected ? " · " + vals.hiRejected + " rejected by you" : ""}</div>
      </div>
      <div>
        <div style={{ ...KICKER, marginBottom: "6px" }}>{"Tree"}</div>
        <pre style={{ ...MONO, fontSize: "11.5px", lineHeight: "1.5", margin: "0", padding: "10px 12px", borderRadius: "8px", background: "var(--color-neutral-900)", whiteSpace: "pre", overflowX: "auto" }}>{vals.hiTree || "—"}</pre>
      </div>
    </div>
  );
}

function WriteGate({ vals }) {
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: "14px" }}>
      <div style={{ display: "flex", flexWrap: "wrap", gap: "8px" }}>
        {(vals.wrCounts || []).map((c) => (
          <div key={c.table} style={{ padding: "8px 12px", borderRadius: "8px", background: "var(--color-neutral-900)", minWidth: "110px" }}>
            <div style={{ ...MONO, fontSize: "18px", lineHeight: "1.1" }}>{c.n}</div>
            <div style={{ fontSize: "10.5px", color: "var(--color-neutral-500)", marginTop: "2px" }}>{c.table}</div>
          </div>
        ))}
      </div>
      <div style={{ fontSize: "12.5px", color: "var(--color-neutral-400)" }}>
        {(vals.wrTotal || 0) + " rows from " + (vals.wrFile || "the upload") + " · overall confidence " + vals.wrConfidence + ". Nothing has been written yet."}
      </div>
      <div style={{ display: "flex", gap: "8px", alignItems: "center", flexWrap: "wrap" }}>
        {!vals.wrArmed ? (
          <button type="button" className="hv13" onClick={vals.mgArm} style={{ ...BARE, fontSize: "12px", padding: "8px 14px", borderRadius: "8px", border: "1px solid var(--color-accent)", color: "var(--color-accent)" }}>
            {"Write to plenum_cafm…"}
          </button>
        ) : (
          <>
            <button type="button" className="hv7" onClick={vals.mgConfirmWrite} style={{ ...BARE, fontSize: "12px", padding: "8px 14px", borderRadius: "8px", background: "var(--color-accent)", color: "var(--accent-ink)" }}>
              {vals.mgWriteLabel}
            </button>
            <button type="button" className="hv11" onClick={vals.mgDisarm} style={{ ...BARE, fontSize: "12px", color: "var(--color-neutral-500)" }}>{"Not yet"}</button>
          </>
        )}
        <span style={{ flex: "1" }}></span>
        <button type="button" className="hv13" onClick={vals.mgRejectWrite} style={{ ...BARE, fontSize: "12px", padding: "8px 14px", borderRadius: "8px", border: "1px solid var(--color-divider)", color: "var(--st-risk)" }}>
          {"Reject — write nothing"}
        </button>
      </div>
      {vals.wrArmed ? <div style={{ fontSize: "11px", color: "var(--st-warn)" }}>{"This inserts rows into the live database. Rows already in it are not checked for duplicates."}</div> : null}
    </div>
  );
}

function GateBody({ vals }) {
  switch (vals.mgGate) {
    case "pk_approval": return <PkGate vals={vals} />;
    case "unique_table_approval": return <UniqueTablesGate vals={vals} />;
    case "pre_semantic": return <PreSemanticGate vals={vals} />;
    case "classification_approval": return <ClassificationGate vals={vals} />;
    case "column_mapping_approval": return <ColumnMappingGate vals={vals} />;
    case "field_mapping": return <FieldMappingGate vals={vals} />;
    case "hierarchy": return <HierarchyGate vals={vals} />;
    case "write": case "final_confirmation": return <WriteGate vals={vals} />;
    default: return <div style={{ fontSize: "12px", color: "var(--color-neutral-500)" }}>{"This gate has no screen yet — approve to send the pipeline's own proposal."}</div>;
  }
}

// ── the page ────────────────────────────────────────────────────────────────────────

function UploadPanel({ vals }) {
  return (
    <div style={{ display: "grid", gridTemplateColumns: "minmax(0,1.4fr) minmax(260px,1fr)", gap: "20px", alignItems: "start", marginTop: "22px" }}>
      <div style={CARD}>
        <div style={KICKER}>{"New migration"}</div>
        <label onDragOver={(e) => e.preventDefault()} onDrop={vals.mgDropFiles}
          style={{ display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "center", gap: "8px", marginTop: "12px", padding: "28px 16px", borderRadius: "10px", border: "1.5px dashed var(--color-rule-strong)", background: "var(--color-bg)", cursor: "pointer", textAlign: "center" }}>
          <i className="ph ph-file-arrow-up" style={{ fontSize: "26px", color: "var(--color-accent)" }}></i>
          <span style={{ fontSize: "13px" }}>{"Drop a CMMS export here, or click to choose"}</span>
          <span style={{ fontSize: "11px", color: "var(--color-neutral-500)" }}>{vals.mgFilesNote}</span>
          <input type="file" multiple accept=".csv,.tsv,.xlsx,.xlsm,.xls" onChange={vals.mgPickFiles} style={{ display: "none" }} />
        </label>
        {!vals.mgFilesEmpty ? (
          <div style={{ display: "flex", flexWrap: "wrap", gap: "6px", marginTop: "10px" }}>
            {vals.mgFiles.map((f) => (
              <span key={f.key} style={{ display: "inline-flex", alignItems: "center", gap: "6px", padding: "5px 9px", borderRadius: "7px", border: "1px solid var(--color-divider)", fontSize: "11.5px", maxWidth: "100%" }}>
                <i className={`ph ${f.icon}`} style={{ fontSize: "13px", color: "var(--color-accent)" }}></i>
                <span style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{f.name}</span>
                <span style={{ ...MONO, fontSize: "9.5px", color: "var(--color-neutral-500)" }}>{f.size}</span>
                <button type="button" className="hv21" onClick={f.drop} title={"Remove " + f.name} style={{ ...BARE, display: "flex", opacity: "0.6" }}><i className="ph ph-x" style={{ fontSize: "10px" }}></i></button>
              </span>
            ))}
          </div>
        ) : null}
        <div style={{ display: "flex", gap: "10px", alignItems: "flex-end", flexWrap: "wrap", marginTop: "14px" }}>
          <label style={{ display: "flex", flexDirection: "column", gap: "4px", flex: "1 1 200px" }}>
            <span style={{ fontSize: "11px", color: "var(--color-neutral-500)" }}>{"Source system"}</span>
            <input className="input" value={vals.mgCmms} onChange={vals.mgSetCmms} placeholder="Custom" style={{ fontSize: "13px" }} />
          </label>
          <button type="button" className="hv7" onClick={vals.mgCanStart ? vals.mgStart : undefined} disabled={!vals.mgCanStart}
            style={{ ...BARE, fontSize: "13px", padding: "10px 16px", borderRadius: "8px", background: "var(--color-accent)", color: "var(--accent-ink)", opacity: vals.mgCanStart ? "1" : "0.5", cursor: vals.mgCanStart ? "pointer" : "default" }}>
            {vals.mgStartLabel}
          </button>
        </div>
        {vals.mgError ? <div style={{ marginTop: "10px", fontSize: "12px", padding: "8px 11px", borderRadius: "8px", background: "var(--st-risk-bg)", color: "var(--st-risk)" }}>{vals.mgError}</div> : null}
        <div style={{ fontSize: "10.5px", color: "var(--color-neutral-500)", lineHeight: "1.5", marginTop: "12px" }}>
          {"The file is migrated into " + vals.mgOrg + ". Every gate is a screen on this page; nothing is written to the database until the last one is confirmed."}
        </div>
      </div>
      <div style={{ display: "flex", flexDirection: "column", gap: "16px" }}>
        <div style={CARD}>
          <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
            <span style={KICKER}>{"Recent runs"}</span>
            <i className="ph ph-arrow-clockwise hv6" onClick={vals.mgRecentReload} title="Reload" style={{ fontSize: "13px", color: "var(--color-neutral-500)", cursor: "pointer" }}></i>
          </div>
          <div style={{ display: "flex", flexDirection: "column", marginTop: "8px" }}>
            {vals.mgRecent.map((m) => (
              <button key={m.id} type="button" className="hv2" onClick={m.open} style={{ ...BARE, display: "grid", gridTemplateColumns: "64px minmax(0,1fr) auto", gap: "10px", alignItems: "center", padding: "7px 8px", borderRadius: "7px", textAlign: "left", background: m.active ? "var(--color-accent-900)" : "transparent" }}>
                <span style={{ ...MONO, fontSize: "11px", color: "var(--color-neutral-500)" }}>{m.short}</span>
                <span style={{ minWidth: "0" }}>
                  <span style={{ display: "block", fontSize: "12px", color: m.tone }}>{m.status}</span>
                  <span style={{ display: "block", fontSize: "10.5px", color: "var(--color-neutral-500)" }}>{m.cmms + " · " + m.mapped + " mapped"}</span>
                </span>
                <span style={{ fontSize: "10.5px", color: "var(--color-neutral-500)", whiteSpace: "nowrap" }}>{m.when}</span>
              </button>
            ))}
            {vals.mgRecentEmpty ? <span style={{ fontSize: "11.5px", color: "var(--color-neutral-500)", padding: "6px 8px" }}>{"No runs yet for this company."}</span> : null}
            {vals.mgRecentNote ? <span style={{ fontSize: "11px", color: "var(--color-neutral-500)", padding: "6px 8px" }}>{vals.mgRecentNote}</span> : null}
          </div>
        </div>
        <div style={CARD}>
          <div style={KICKER}>{"What runs, in order"}</div>
          <ol style={{ margin: "10px 0 0", padding: "0", listStyle: "none", display: "flex", flexDirection: "column", gap: "8px" }}>
            {vals.mgNodes.map((n) => (
              <li key={n.id} style={{ display: "grid", gridTemplateColumns: "20px minmax(0,1fr)", gap: "10px" }}>
                <span style={{ ...MONO, fontSize: "11px", color: "var(--color-neutral-500)", paddingTop: "1px" }}>{n.id}</span>
                <span>
                  <span style={{ display: "block", fontSize: "12.5px" }}>{n.name}</span>
                  <span style={{ display: "block", fontSize: "11px", color: "var(--color-neutral-500)", lineHeight: "1.4" }}>{n.blurb}</span>
                </span>
              </li>
            ))}
          </ol>
        </div>
      </div>
    </div>
  );
}

function Run({ vals }) {
  return (
    <>
      <div style={{ ...CARD, marginTop: "22px", display: "flex", flexDirection: "column", gap: "12px" }}>
        <div style={{ display: "flex", alignItems: "center", gap: "12px", flexWrap: "wrap" }}>
          <Pill {...vals.mgPill} />
          <span style={{ fontSize: "13px", fontWeight: "500" }}>{vals.mgFile || "Migration " + vals.mgIdShort}</span>
          <span style={{ ...MONO, fontSize: "11px", color: "var(--color-neutral-500)" }} title={vals.mgId}>{vals.mgIdShort}</span>
          <span style={{ fontSize: "11px", color: "var(--color-neutral-500)" }}>{[vals.mgCmmsName, vals.mgStarted ? "started " + vals.mgStarted : "", vals.mgStepLabel].filter(Boolean).join(" · ")}</span>
          <span style={{ flex: "1" }}></span>
          <label style={{ display: "inline-flex", alignItems: "center", gap: "6px", fontSize: "11.5px", color: "var(--color-neutral-400)", cursor: "pointer" }}>
            <input type="checkbox" checked={vals.mgAuto} onChange={vals.mgToggleAuto} />
            {"Continue past step pauses automatically"}
          </label>
          <button type="button" className="hv13" onClick={vals.mgRefresh} style={{ ...BARE, fontSize: "11.5px", padding: "5px 10px", borderRadius: "7px", border: "1px solid var(--color-divider)", color: "var(--color-neutral-400)" }}>
            <i className="ph ph-arrow-clockwise" style={{ fontSize: "12px", marginRight: "5px" }}></i>{"Refresh"}
          </button>
        </div>
        <div style={{ height: "4px", borderRadius: "2px", background: "var(--color-neutral-900)", overflow: "hidden" }}>
          <div style={{ height: "100%", width: vals.mgProgress + "%", background: vals.mgKind === "failed" ? "var(--st-risk)" : "var(--color-accent)", transition: "width 0.4s ease" }}></div>
        </div>
        {vals.mgCoverage.length ? (
          <div style={{ display: "flex", gap: "18px", flexWrap: "wrap" }}>
            {vals.mgCoverage.map((c) => (
              <span key={c.label} style={{ fontSize: "11px", color: "var(--color-neutral-500)" }}>
                <span style={{ ...MONO, fontSize: "14px", color: "var(--color-text)", marginRight: "5px" }}>{c.value}</span>{c.label}
              </span>
            ))}
          </div>
        ) : null}
      </div>

      {vals.mgError ? <div style={{ marginTop: "12px", fontSize: "12px", padding: "9px 12px", borderRadius: "8px", background: "var(--st-risk-bg)", color: "var(--st-risk)" }}>{vals.mgError}</div> : null}

      <div style={{ display: "grid", gridTemplateColumns: "280px minmax(0,1fr)", gap: "20px", alignItems: "start", marginTop: "16px" }}>
        {/* ── tracker ── */}
        <div style={CARD}>
          <div style={KICKER}>{"Pipeline"}</div>
          <ol style={{ margin: "10px 0 0", padding: "0", listStyle: "none", display: "flex", flexDirection: "column", gap: "2px" }}>
            {vals.mgNodes.map((n) => (
              <li key={n.id} style={{ borderRadius: "7px", background: n.current ? "var(--color-accent-900)" : "transparent", padding: "6px 8px" }}>
                <div style={{ display: "grid", gridTemplateColumns: "18px minmax(0,1fr) auto", gap: "8px", alignItems: "start" }}>
                  <i className={`ph ${n.icon}`} style={{ fontSize: "14px", color: n.tone, marginTop: "2px", animation: n.spin ? "spin 1.1s linear infinite" : "none" }}></i>
                  <div style={{ minWidth: "0" }}>
                    <div style={{ fontSize: "12.5px", color: n.status === "pending" ? "var(--color-neutral-500)" : "var(--color-text)" }}>{n.id + ". " + n.name}</div>
                    {n.outcome ? <div style={{ fontSize: "11px", color: "var(--color-neutral-500)", lineHeight: "1.4", marginTop: "1px" }}>{n.outcome}</div> : null}
                    {n.hasLogs ? (
                      <button type="button" className="hv11" onClick={n.toggle} style={{ ...BARE, fontSize: "10.5px", color: "var(--color-accent)", marginTop: "3px" }}>
                        {n.logsOpen ? "Hide log" : "Show log (" + n.logs.length + ")"}
                      </button>
                    ) : null}
                    {n.logsOpen ? (
                      <pre style={{ ...MONO, fontSize: "10.5px", lineHeight: "1.45", margin: "6px 0 0", padding: "8px", borderRadius: "6px", background: "var(--color-neutral-900)", whiteSpace: "pre-wrap", maxHeight: "260px", overflow: "auto" }}>{n.logs.join("\n")}</pre>
                    ) : null}
                  </div>
                  <span style={{ ...MONO, fontSize: "10px", color: "var(--color-neutral-500)", whiteSpace: "nowrap" }}>{n.ms}</span>
                </div>
              </li>
            ))}
          </ol>
        </div>

        {/* ── the open gate / step / result ── */}
        <div style={{ display: "flex", flexDirection: "column", gap: "16px", minWidth: "0" }}>
          <div style={CARD}>
            <div style={{ display: "flex", alignItems: "baseline", gap: "10px", flexWrap: "wrap" }}>
              <h3 style={{ fontSize: "17px", margin: "0", lineHeight: "1.2" }}>{vals.mgGateTitle}</h3>
              {vals.mgGateCount ? <span style={{ fontSize: "11px", color: "var(--color-neutral-500)" }}>{vals.mgGateCount}</span> : null}
              <span style={{ flex: "1" }}></span>
              {vals.mgDecided ? (
                <button type="button" className="hv11" onClick={vals.mgResetDecisions} style={{ ...BARE, fontSize: "11px", color: "var(--color-neutral-500)" }}>{"Reset " + vals.mgDecided + " change" + (vals.mgDecided === 1 ? "" : "s")}</button>
              ) : null}
            </div>
            <p style={{ fontSize: "12.5px", color: vals.mgKind === "failed" ? "var(--st-risk)" : "var(--color-neutral-400)", margin: "6px 0 0", lineHeight: "1.5", maxWidth: "80ch" }}>{vals.mgGateBlurb}</p>

            {vals.mgLoading ? <div style={{ fontSize: "12px", color: "var(--color-neutral-500)", marginTop: "14px" }}>{"Reading the migration…"}</div> : null}

            {vals.mgKind === "gate" ? <div style={{ marginTop: "14px" }}><GateBody vals={vals} /></div> : null}

            {vals.mgKind === "step" && vals.mgStepFacts.length ? (
              <div style={{ display: "flex", flexWrap: "wrap", gap: "8px", marginTop: "14px" }}>
                {vals.mgStepFacts.map((f) => (
                  <span key={f.label} style={{ fontSize: "11px", padding: "5px 9px", borderRadius: "6px", background: "var(--color-neutral-900)" }}>
                    <span style={{ color: "var(--color-neutral-500)" }}>{f.label + " "}</span><span style={MONO}>{f.value}</span>
                  </span>
                ))}
              </div>
            ) : null}

            {vals.mgPrimary ? (
              <div style={{ display: "flex", alignItems: "center", gap: "10px", marginTop: "16px", paddingTop: "14px", borderTop: "1px solid var(--color-divider)" }}>
                <button type="button" className="hv7" onClick={vals.mgPrimary.run} style={{ ...BARE, fontSize: "13px", padding: "9px 16px", borderRadius: "8px", background: "var(--color-accent)", color: "var(--accent-ink)", opacity: vals.mgBusy ? "0.7" : "1" }}>
                  {vals.mgPrimary.label}
                </button>
                <span style={{ fontSize: "11px", color: "var(--color-neutral-500)" }}>{vals.mgKind === "gate" ? "Rows you did not change are sent as the pipeline proposed them." : ""}</span>
              </div>
            ) : null}
          </div>

          {vals.mgOutputs.length ? (
            <div style={CARD}>
              <div style={KICKER}>{"Artefacts"}</div>
              <div style={{ display: "flex", flexWrap: "wrap", gap: "8px", marginTop: "10px" }}>
                {vals.mgOutputs.map((o) => (
                  <a key={o.label} href={o.href} target="_blank" rel="noreferrer" className="hv13" style={{ fontSize: "12px", padding: "7px 12px", borderRadius: "8px", border: "1px solid var(--color-divider)", color: "var(--color-text)", display: "inline-flex", alignItems: "center", gap: "6px" }}>
                    <i className="ph ph-download-simple" style={{ fontSize: "13px", color: "var(--color-accent)" }}></i>{o.label}
                  </a>
                ))}
              </div>
            </div>
          ) : null}
        </div>
      </div>
    </>
  );
}

export default function Migration({ vals }) {
  return (
    <div style={{ flex: "1", display: "flex", justifyContent: "flex-start", padding: "0 40px 80px" }}>
      <div style={{ width: "100%", maxWidth: "1400px", animation: "fadeUp 0.28s ease both" }}>
        <div style={{ display: "flex", alignItems: "center", gap: "12px", padding: "24px 0 0" }}>
          <div className="hv6" onClick={vals.goHome} style={{ display: "flex", alignItems: "center", gap: "6px", fontSize: "12px", color: "var(--color-neutral-400)", cursor: "pointer" }}>
            <i className="ph ph-arrow-left" style={{ fontSize: "12px" }}></i>
            <span>{"Home"}</span>
          </div>
          <span style={{ fontSize: "12px", color: "var(--color-neutral-500)" }}>{"/"}</span>
          <span style={{ fontSize: "12px", color: "var(--color-neutral-500)" }}>{"Migration"}</span>
        </div>
        <div style={{ display: "flex", alignItems: "flex-end", justifyContent: "space-between", flexWrap: "wrap", gap: "16px 24px", marginTop: "18px" }}>
          <div style={{ minWidth: "0", flex: "1 1 340px" }}>
            <div style={{ fontSize: "10.5px", letterSpacing: "0.13em", textTransform: "uppercase", color: "var(--color-accent)" }}>{"Data migration · CSV and Excel"}</div>
            <div style={{ display: "flex", alignItems: "center", gap: "10px", flexWrap: "wrap", marginTop: "7px" }}>
              <h2 style={{ fontSize: "28px", margin: "0", lineHeight: "1.15" }}>{"Migration"}</h2>
              <span style={{ fontFamily: "ui-monospace,monospace", fontSize: "10px", letterSpacing: "0.09em", textTransform: "uppercase", padding: "3px 8px", borderRadius: "5px", background: "var(--color-accent)", color: "var(--accent-ink)" }}>{"Admin only"}</span>
            </div>
            <p style={{ fontSize: "13px", color: "var(--color-neutral-400)", margin: "8px 0 0", maxWidth: "88ch", lineHeight: "1.55" }}>
              {"A CMMS export becomes plenum_cafm rows in nine steps: tables and keys, column groups, destination columns, semantic matches, hierarchy, then the write. Each decision is a gate on this page, and the database is only touched at the last one."}
            </p>
          </div>
          {vals.mgHasRun ? (
            <div style={{ display: "flex", alignItems: "center", flexWrap: "wrap", gap: "10px" }}>
              <button type="button" className="hv4" onClick={vals.mgNew} style={{ ...BARE, fontSize: "12px", padding: "7px 14px", borderRadius: "8px", border: "1px solid var(--color-divider)", color: "var(--color-neutral-400)", whiteSpace: "nowrap" }}>
                <i className="ph ph-plus" style={{ fontSize: "12px", marginRight: "5px" }}></i>{"New migration"}
              </button>
            </div>
          ) : null}
        </div>
        {vals.mgHasRun ? <Run vals={vals} /> : <UploadPanel vals={vals} />}
      </div>
    </div>
  );
}
