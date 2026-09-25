// MigrationRun — a CSV / Excel migration, answered in the conversation that started it.
//
// There is no Migration page any more. A run is one card in the Orchestrator transcript:
// the nodes that have finished stack above, collapsed, and the gate waiting on a person
// sits at the bottom where the composer's scroll lands. The gate bodies below came from
// screens/Migration.jsx unchanged — they read `vals` and decide nothing themselves, so
// moving them was a move, not a rewrite.
//
// The nine nodes, the body each gate is answered with, the polling cadence and the
// two-step arm/confirm on the write are all still logic/migration.js's; this file renders
// what mgVals() already returned to the page.
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
                  {r.isNew && (
                    <div style={{ display: "flex", gap: "6px", alignItems: "center", flexWrap: "wrap", marginTop: "6px" }}>
                      <input
                        className="input"
                        value={r.newName}
                        onChange={r.setNewName}
                        placeholder="column name"
                        aria-label={"New column name for " + r.field}
                        style={{ ...SELECT, width: "170px", fontFamily: "var(--font-mono)", fontSize: "12px" }}
                      />
                      <select className="input" value={r.newType} onChange={r.setNewType} style={{ ...SELECT, width: "140px" }}>
                        {r.newTypes.map((t) => <option key={t} value={t}>{t}</option>)}
                      </select>
                      <input
                        className="input"
                        value={r.newTable}
                        onChange={r.setNewTable}
                        placeholder={"table (" + r.routedTable + ")"}
                        aria-label={"Target table for " + r.field}
                        style={{ ...SELECT, width: "150px", fontFamily: "var(--font-mono)", fontSize: "12px" }}
                      />
                      <div style={{ fontSize: "10.5px", color: r.newNameSafe ? "var(--color-neutral-500)" : "var(--st-risk)" }}>
                        {!r.newNameSafe
                          ? "not a usable column name"
                          : (r.newTableIsNew ? "creates table " : "adds to ")
                            + r.newTarget + "." + r.newNameSafe}
                      </div>
                    </div>
                  )}
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


// ── the run, in the transcript ───────────────────────────────────────────────────────
//
// The same shape the Migration page had in production: a head that says which run this is
// and how far along, the nine-node pipeline down the left, and the gate waiting on a person
// on the right. The page is gone; the layout is not, because it is the layout that tells
// you where you are in a nine-step process — a stack of finished steps alone never does.
//
// The conversation column is ~930px (.chat-grid is minmax(0,1fr) 320px inside max-width
// 1280px), so the tracker column is narrower here than the page's 280px and the gate's own
// tables scroll horizontally inside themselves when a mapping has many columns.
export default function MigrationRun({ vals }) {
  return (
    <div style={{ marginTop: "18px", border: "1px solid var(--color-divider)", borderRadius: "12px", background: "var(--color-surface)", overflow: "hidden", animation: "fadeUp 0.25s ease both" }}>

      {/* ── which run this is, and how far along ── */}
      <div style={{ padding: "12px 16px", display: "flex", flexDirection: "column", gap: "11px", borderBottom: "1px solid var(--color-divider)" }}>
        <div style={{ display: "flex", alignItems: "center", gap: "10px", flexWrap: "wrap" }}>
          {/* The page had an <h2>Migration</h2> above this row to say what it was. In a
              transcript the card has to say so itself — it arrives among answers, notes
              and forms, and a filename alone does not tell you which of them this is. */}
          <span style={{ display: "inline-flex", alignItems: "center", gap: "6px", fontSize: "12.5px" }}>
            <i className="ph ph-file-arrow-up" style={{ fontSize: "14px", color: "var(--color-accent)" }}></i>
            {"Migration"}
          </span>
          <Pill {...vals.mgPill} />
          {/* Only when a document has told us the file name. The fallback used to be
              "Migration <short id>", which printed the id immediately before the id chip
              below — the same eight characters twice, on the one card that has no file
              name to show because nothing has been read yet. */}
          {vals.mgFile ? (
            <span style={{ fontSize: "12.5px", fontWeight: "500", minWidth: "0", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", maxWidth: "34ch" }} title={vals.mgFile}>
              {vals.mgFile}
            </span>
          ) : null}
          <span style={{ ...MONO, fontSize: "10.5px", color: "var(--color-neutral-500)" }} title={vals.mgId}>{vals.mgIdShort}</span>
          <span style={{ fontSize: "10.5px", color: "var(--color-neutral-500)" }}>
            {[vals.mgCmmsName, vals.mgStarted ? "started " + vals.mgStarted : "", vals.mgStepLabel].filter(Boolean).join(" · ")}
          </span>
          <span style={{ flex: "1" }}></span>
          <label style={{ display: "inline-flex", alignItems: "center", gap: "6px", fontSize: "11px", color: "var(--color-neutral-400)", cursor: "pointer", whiteSpace: "nowrap" }}>
            <input type="checkbox" checked={vals.mgAuto} onChange={vals.mgToggleAuto} />
            {"Continue past step pauses automatically"}
          </label>
          <button type="button" className="hv13" onClick={vals.mgRefresh} style={{ ...BARE, fontSize: "11px", padding: "4px 10px", borderRadius: "7px", border: "1px solid var(--color-divider)", color: "var(--color-neutral-400)", whiteSpace: "nowrap" }}>
            <i className="ph ph-arrow-clockwise" style={{ fontSize: "11px", marginRight: "5px" }}></i>{"Refresh"}
          </button>
          <button type="button" className="hv11" onClick={vals.mgNew} title="Put this run aside — it keeps running and stays in the list" style={{ ...BARE, fontSize: "11px", color: "var(--color-neutral-500)", whiteSpace: "nowrap" }}>
            {"Close"}
          </button>
        </div>

        {/* Scaled, not resized. The page animated this bar's `width`, which lays out the
            document on every frame of the transition; scaleX is composited instead and
            looks identical — the fill carries no radius of its own (the 2px is on the
            track, which clips it) and no text, so there is nothing for the scale to
            distort. */}
        <div style={{ height: "4px", borderRadius: "2px", background: "var(--color-neutral-900)", overflow: "hidden" }}>
          <div style={{ height: "100%", width: "100%", transformOrigin: "left", transform: "scaleX(" + Math.max(0, Math.min(100, Number(vals.mgProgress) || 0)) / 100 + ")", background: vals.mgKind === "failed" ? "var(--st-risk)" : "var(--color-accent)", transition: "transform 0.4s ease" }}></div>
        </div>

        {vals.mgCoverage.length ? (
          <div style={{ display: "flex", gap: "16px", flexWrap: "wrap" }}>
            {vals.mgCoverage.map((c) => (
              <span key={c.label} style={{ fontSize: "10.5px", color: "var(--color-neutral-500)" }}>
                <span style={{ ...MONO, fontSize: "13px", color: "var(--color-text)", marginRight: "5px" }}>{c.value}</span>{c.label}
              </span>
            ))}
          </div>
        ) : null}
      </div>

      {vals.mgError ? (
        <div style={{ margin: "12px 16px 0", fontSize: "11.5px", padding: "9px 12px", borderRadius: "8px", background: "var(--st-risk-bg)", color: "var(--st-risk)" }}>{vals.mgError}</div>
      ) : null}

      <div className="mg-run-grid" style={{ display: "grid", gridTemplateColumns: "minmax(190px, 240px) minmax(0,1fr)", gap: "16px", alignItems: "start", padding: "14px 16px 16px" }}>

        {/* ── the nine nodes, whether or not they have run ── */}
        <div>
          <div style={KICKER}>{"Pipeline"}</div>
          <ol style={{ margin: "9px 0 0", padding: "0", listStyle: "none", display: "flex", flexDirection: "column", gap: "2px" }}>
            {vals.mgNodes.map((n) => (
              <li key={n.id} style={{ borderRadius: "7px", background: n.current ? "var(--color-accent-900)" : "transparent", padding: "6px 8px" }}>
                <div style={{ display: "grid", gridTemplateColumns: "16px minmax(0,1fr) auto", gap: "7px", alignItems: "start" }}>
                  <i className={`ph ${n.icon}`} style={{ fontSize: "13px", color: n.tone, marginTop: "2px", animation: n.spin ? "spin 1.1s linear infinite" : "none" }}></i>
                  <div style={{ minWidth: "0" }}>
                    <div style={{ fontSize: "11.5px", color: n.status === "pending" ? "var(--color-neutral-500)" : "var(--color-text)" }}>{n.id + ". " + n.name}</div>
                    {n.outcome ? <div style={{ fontSize: "10.5px", color: "var(--color-neutral-500)", lineHeight: "1.4", marginTop: "1px" }}>{n.outcome}</div> : null}
                    {n.hasLogs ? (
                      <button type="button" className="hv11" onClick={n.toggle} style={{ ...BARE, fontSize: "10px", color: "var(--color-accent)", marginTop: "3px" }}>
                        {n.logsOpen ? "Hide log" : "Show log (" + n.logs.length + ")"}
                      </button>
                    ) : null}
                    {n.logsOpen ? (
                      <pre style={{ ...MONO, fontSize: "10px", lineHeight: "1.45", margin: "6px 0 0", padding: "8px", borderRadius: "6px", background: "var(--color-neutral-900)", whiteSpace: "pre-wrap", maxHeight: "240px", overflow: "auto" }}>{n.logs.join("\n")}</pre>
                    ) : null}
                  </div>
                  <span style={{ ...MONO, fontSize: "9.5px", color: "var(--color-neutral-500)", whiteSpace: "nowrap" }}>{n.ms}</span>
                </div>
              </li>
            ))}
          </ol>
        </div>

        {/* ── the gate waiting on a person ── */}
        <div style={{ minWidth: "0", display: "flex", flexDirection: "column", gap: "14px" }}>
          <div>
            <div style={{ display: "flex", alignItems: "baseline", gap: "10px", flexWrap: "wrap" }}>
              <span style={{ fontSize: "15px" }}>{vals.mgGateTitle}</span>
              {vals.mgGateCount ? <span style={{ fontSize: "10.5px", color: "var(--color-neutral-500)" }}>{vals.mgGateCount}</span> : null}
              <span style={{ flex: "1" }}></span>
              {vals.mgDecided ? (
                <button type="button" className="hv11" onClick={vals.mgResetDecisions} style={{ ...BARE, fontSize: "10.5px", color: "var(--color-neutral-500)" }}>
                  {"Reset " + vals.mgDecided + " change" + (vals.mgDecided === 1 ? "" : "s")}
                </button>
              ) : null}
            </div>
            <p style={{ fontSize: "11.5px", color: vals.mgKind === "failed" ? "var(--st-risk)" : "var(--color-neutral-400)", margin: "5px 0 0", lineHeight: "1.5", maxWidth: "80ch" }}>{vals.mgGateBlurb}</p>

            {vals.mgLoading ? <div style={{ fontSize: "11.5px", color: "var(--color-neutral-500)", marginTop: "12px" }}>{"Reading the migration…"}</div> : null}

            {/* A run that claims to be working but has not moved in minutes. "Working…" on
                its own is indistinguishable from abandoned, which cost five hours once. */}
            {vals.mgStallNote ? (
              <div style={{ display: "grid", gridTemplateColumns: "14px minmax(0,1fr)", gap: "8px", alignItems: "start", marginTop: "12px", padding: "9px 11px", borderRadius: "8px", background: "var(--st-warn-bg)", color: "var(--st-warn)", fontSize: "11.5px", lineHeight: "1.5" }}>
                <i className="ph ph-warning-circle" style={{ fontSize: "13px", marginTop: "2px" }}></i>
                <span>{vals.mgStallNote}</span>
              </div>
            ) : null}

            {vals.mgKind === "gate" ? <div style={{ marginTop: "12px" }}><GateBody vals={vals} /></div> : null}

            {vals.mgKind === "step" && vals.mgStepFacts.length ? (
              <div style={{ display: "flex", flexWrap: "wrap", gap: "7px", marginTop: "12px" }}>
                {vals.mgStepFacts.map((f) => (
                  <span key={f.label} style={{ fontSize: "10.5px", padding: "5px 9px", borderRadius: "6px", background: "var(--color-neutral-900)" }}>
                    <span style={{ color: "var(--color-neutral-500)" }}>{f.label + " "}</span><span style={MONO}>{f.value}</span>
                  </span>
                ))}
              </div>
            ) : null}

            {vals.mgPrimary ? (
              <div style={{ display: "flex", alignItems: "center", gap: "10px", flexWrap: "wrap", marginTop: "14px", paddingTop: "12px", borderTop: "1px solid var(--color-divider)" }}>
                <button type="button" className="hv7" onClick={vals.mgPrimary.run} style={{ ...BARE, fontSize: "12.5px", padding: "9px 16px", borderRadius: "8px", background: "var(--color-accent)", color: "var(--accent-ink)", opacity: vals.mgBusy ? "0.7" : "1" }}>
                  {vals.mgPrimary.label}
                </button>
                <span style={{ fontSize: "10.5px", color: "var(--color-neutral-500)" }}>{vals.mgKind === "gate" ? "Rows you did not change are sent as the pipeline proposed them." : ""}</span>
              </div>
            ) : null}
          </div>

          {vals.mgOutputs.length ? (
            <div>
              <div style={KICKER}>{"Artefacts"}</div>
              <div style={{ display: "flex", flexWrap: "wrap", gap: "7px", marginTop: "9px" }}>
                {vals.mgOutputs.map((o) => (
                  <a key={o.label} href={o.href} target="_blank" rel="noreferrer" className="hv13" style={{ fontSize: "11px", padding: "6px 11px", borderRadius: "7px", border: "1px solid var(--color-divider)", color: "var(--color-text)", display: "inline-flex", alignItems: "center", gap: "6px", textDecoration: "none" }}>
                    <i className="ph ph-download-simple" style={{ fontSize: "12px", color: "var(--color-accent)" }}></i>{o.label}
                  </a>
                ))}
              </div>
            </div>
          ) : null}
        </div>
      </div>
    </div>
  );
}
