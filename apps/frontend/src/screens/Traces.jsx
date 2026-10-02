// Traces — Administration › Hoist Traces (logic/tracesPage.js). The runs the orchestrator
// answered with cost, latency and status; each run as a span tree with a waterfall, and the
// selected span's input and output. Thumbs rate a run for the training export.
import React from 'react';

const CARD = { borderRadius: "10px", background: "var(--color-surface)", boxShadow: "var(--shadow-sm)" };
const KICK = { fontSize: "10.5px", letterSpacing: "0.09em", textTransform: "uppercase", color: "var(--color-neutral-500)" };
const MONO = { fontFamily: "ui-monospace,monospace" };
const QUIET = { fontSize: "12px", padding: "7px 12px", borderRadius: "7px", border: "1px solid var(--color-divider)", background: "transparent", color: "var(--color-neutral-400)", cursor: "pointer" };
const CELL = { padding: "9px 12px", borderBottom: "1px solid var(--color-divider)", fontSize: "12.5px", verticalAlign: "top" };
const chip = (on) => ({ fontSize: "12px", padding: "6px 12px", borderRadius: "999px", cursor: "pointer", whiteSpace: "nowrap",
  border: "1px solid " + (on ? "var(--color-accent)" : "var(--color-divider)"), background: on ? "var(--color-accent-900)" : "transparent",
  color: on ? "var(--color-accent)" : "var(--color-neutral-400)" });
const KIND = {
  turn: ["ph-chat-circle-text", "var(--color-text)"], agent: ["ph-tree-structure", "var(--color-neutral-300)"], router: ["ph-signpost", "var(--st-warn)"],
  llm: ["ph-brain", "var(--color-accent)"], tool: ["ph-wrench", "var(--st-ok)"], stage: ["ph-steps", "var(--color-neutral-400)"],
  db: ["ph-database", "var(--color-neutral-300)"]
};

// The rows a query answered, as a table: the columns of the first row, up to the kept rows.
function Rows({ rows, count, truncated }) {
  if (!rows || !rows.length) return <div style={{ fontSize: "12px", color: "var(--color-neutral-500)", marginTop: "6px" }}>{count === 0 ? "No rows." : "Rows not kept."}</div>;
  const cols = Object.keys(rows[0]);
  const cell = (v) => (v === null || v === undefined ? "" : typeof v === "object" ? JSON.stringify(v) : String(v));
  return (
    <div style={{ marginTop: "6px", overflowX: "auto", maxHeight: "360px", overflowY: "auto", borderRadius: "8px", border: "1px solid var(--color-divider)" }}>
      <table style={{ borderCollapse: "collapse", minWidth: "100%" }}>
        <thead><tr>{cols.map((c) => <th key={c} style={{ ...CELL, ...KICK, ...MONO, padding: "7px 10px", position: "sticky", top: "0", background: "var(--color-surface)", textAlign: "left" }}>{c}</th>)}</tr></thead>
        <tbody>{rows.map((r, i) => <tr key={i}>{cols.map((c) => <td key={c} style={{ ...CELL, ...MONO, fontSize: "11.5px", padding: "5px 10px", whiteSpace: "nowrap", maxWidth: "320px", overflow: "hidden", textOverflow: "ellipsis" }} title={cell(r[c])}>{cell(r[c])}</td>)}</tr>)}</tbody>
      </table>
      {truncated ? <div style={{ fontSize: "11px", color: "var(--color-neutral-500)", padding: "6px 10px" }}>{"+ " + truncated + " more rows not kept in the trace"}</div> : null}
    </div>
  );
}

function Crumbs({ vals, run }) {
  return (
    <div style={{ display: "flex", alignItems: "center", gap: "8px", padding: "24px 0 0", fontSize: "12px", color: "var(--color-neutral-500)" }}>
      <button type="button" onClick={vals.goHome} style={{ ...QUIET, border: "none", padding: "0", display: "flex", alignItems: "center", gap: "6px" }}>
        <i className="ph ph-arrow-left" style={{ fontSize: "12px" }}></i>{"Home"}
      </button>
      <span>{"/"}</span><span>{"Administration"}</span><span>{"/"}</span>
      {run ? (<><button type="button" onClick={vals.tpBack} style={{ ...QUIET, border: "none", padding: "0" }}>{"Hoist Traces"}</button><span>{"/"}</span><span style={{ color: "var(--color-text)" }}>{run}</span></>)
        : <span style={{ color: "var(--color-text)" }}>{"Hoist Traces"}</span>}
    </div>
  );
}

function Pre({ text, label }) {
  return (
    <div style={{ marginTop: "12px" }}>
      <div style={KICK}>{label}</div>
      <pre style={{ ...MONO, fontSize: "11.5px", lineHeight: "1.5", margin: "6px 0 0", padding: "12px", borderRadius: "8px", background: "var(--color-bg)", color: "var(--color-text)",
        whiteSpace: "pre-wrap", wordBreak: "break-word", maxHeight: "420px", overflow: "auto" }}>{text || "—"}</pre>
    </div>
  );
}

function Run({ vals }) {
  const d = vals.tpDetail;
  if (vals.tpTurnLoading) return <div style={{ ...CARD, marginTop: "16px", padding: "18px", fontSize: "12.5px", color: "var(--color-neutral-500)" }}>{"Reading the run…"}</div>;
  if (vals.tpTurnError) return <div style={{ ...CARD, marginTop: "16px", padding: "18px", fontSize: "12.5px", color: "var(--st-risk)" }}>{"Could not read the run: " + vals.tpTurnError}</div>;
  if (!d) return null;
  const sp = d.span;
  return (
    <>
      <div style={{ ...CARD, marginTop: "16px", padding: "16px 18px" }}>
        <div style={{ display: "flex", justifyContent: "space-between", gap: "16px", flexWrap: "wrap", alignItems: "flex-start" }}>
          <div style={{ minWidth: "0", flex: "1 1 520px" }}>
            <div style={KICK}>{d.when + " · " + d.who}</div>
            <div style={{ fontSize: "15px", marginTop: "6px", lineHeight: "1.5" }}>{d.question}</div>
            {d.error ? <div style={{ fontSize: "12.5px", color: "var(--st-risk)", marginTop: "6px" }}>{"Failed: " + d.error}</div> : null}
          </div>
          <div style={{ display: "flex", gap: "18px", flexWrap: "wrap", fontSize: "12px", color: "var(--color-neutral-400)" }}>
            {[["Cost", d.cost + (d.costIncomplete ? " (partial)" : "")], ["Latency", d.latency], ["Model calls", String(d.llm)], ["Tool calls", String(d.tools)], ["Tokens", d.tokens], ["Models", d.models]].map(([k, v]) => (
              <div key={k}><div style={KICK}>{k}</div><div style={{ color: "var(--color-text)", marginTop: "3px", ...(k === "Models" ? {} : MONO) }}>{v}</div></div>
            ))}
          </div>
        </div>
        <div style={{ display: "flex", gap: "10px", alignItems: "center", marginTop: "14px", flexWrap: "wrap" }}>
          <span style={{ fontSize: "12px", color: "var(--color-neutral-500)" }}>{"Was this answer right?"}</span>
          <button type="button" aria-label="Rate up" onClick={d.rateUp} style={{ ...QUIET, borderColor: d.rating === "up" ? "var(--st-ok)" : "var(--color-divider)", color: d.rating === "up" ? "var(--st-ok)" : "var(--color-neutral-400)" }}><i className="ph ph-thumbs-up"></i></button>
          <button type="button" aria-label="Rate down" onClick={d.rateDown} style={{ ...QUIET, borderColor: d.rating === "down" ? "var(--st-risk)" : "var(--color-divider)", color: d.rating === "down" ? "var(--st-risk)" : "var(--color-neutral-400)" }}><i className="ph ph-thumbs-down"></i></button>
          <input aria-label="Comment on this answer" className="input" placeholder="What was right or wrong (kept with the export)…" value={d.comment} onChange={d.setComment} onBlur={d.saveComment}
            style={{ flex: "1 1 320px", fontSize: "12.5px", padding: "7px 10px", borderRadius: "7px", border: "1px solid var(--color-divider)", background: "var(--color-surface)", color: "var(--color-text)" }} />
          {d.commentDirty ? <button type="button" onClick={d.saveComment} style={QUIET}>{"Save"}</button> : null}
        </div>
      </div>

      <div style={{ display: "grid", gridTemplateColumns: "minmax(360px, 5fr) minmax(360px, 7fr)", gap: "14px", marginTop: "14px", alignItems: "start" }}>
        <div style={{ ...CARD, padding: "12px 0" }}>
          <div style={{ ...KICK, padding: "0 16px 8px" }}>{"Trace · waterfall"}</div>
          {d.tree.map((nd) => (
            <div key={nd.id} role="button" tabIndex={0} onClick={nd.pick} onKeyDown={(e) => { if (e.key === "Enter") nd.pick(); }}
              style={{ display: "grid", gridTemplateColumns: "minmax(0, 1fr) 120px", gap: "8px", alignItems: "center", padding: "6px 16px 6px " + (16 + nd.depth * 18) + "px", cursor: "pointer",
                background: nd.on ? "var(--color-accent-900)" : "transparent", borderLeft: nd.on ? "2px solid var(--color-accent)" : "2px solid transparent" }}>
              <div style={{ display: "flex", alignItems: "center", gap: "8px", minWidth: "0" }}>
                <i className={"ph " + (KIND[nd.kind] || KIND.stage)[0]} style={{ fontSize: "13px", color: nd.ok ? (KIND[nd.kind] || KIND.stage)[1] : "var(--st-risk)", flexShrink: "0" }}></i>
                <span style={{ fontSize: "12.5px", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", color: nd.ok ? "var(--color-text)" : "var(--st-risk)" }}>{nd.name}</span>
                {nd.model ? <span style={{ ...MONO, fontSize: "10.5px", padding: "1px 6px", borderRadius: "5px", border: "1px solid var(--color-divider)", color: "var(--color-neutral-400)", whiteSpace: "nowrap" }}>{nd.model}</span> : null}
                <span style={{ ...MONO, fontSize: "11px", color: "var(--color-neutral-500)", whiteSpace: "nowrap" }}>{nd.latency}</span>
                {nd.cost ? <span style={{ ...MONO, fontSize: "11px", color: "var(--color-neutral-500)", whiteSpace: "nowrap" }}>{nd.cost}</span> : null}
              </div>
              <div style={{ position: "relative", height: "8px", background: "var(--color-bg)", borderRadius: "3px" }}>
                <div style={{ position: "absolute", left: nd.start + "%", width: nd.width + "%", top: "0", bottom: "0", borderRadius: "3px", background: nd.ok ? (KIND[nd.kind] || KIND.stage)[1] : "var(--st-risk)", opacity: nd.kind === "turn" || nd.kind === "agent" ? "0.35" : "0.9" }}></div>
              </div>
            </div>
          ))}
        </div>
        <div style={{ ...CARD, padding: "16px 18px" }}>
          {sp ? (
            <>
              <div style={{ display: "flex", alignItems: "center", gap: "10px", flexWrap: "wrap" }}>
                <i className={"ph " + (KIND[sp.kindLabel === "Model" ? "llm" : sp.kindLabel === "Query" ? "db" : sp.kindLabel.toLowerCase()] || KIND.stage)[0]} style={{ fontSize: "20px", color: "var(--color-accent)" }}></i>
                <h3 style={{ margin: "0", fontSize: "19px" }}>{sp.name}</h3>
                {sp.model ? <span style={{ ...MONO, fontSize: "11.5px", padding: "2px 8px", borderRadius: "6px", border: "1px solid var(--color-divider)", color: "var(--color-neutral-300)" }}>{sp.model}</span> : null}
                <span style={{ fontSize: "11.5px", color: "var(--color-neutral-500)" }}>{sp.kindLabel + (sp.agent ? " · " + sp.agent : "")}</span>
              </div>
              <div style={{ display: "flex", gap: "18px", flexWrap: "wrap", marginTop: "12px", fontSize: "12px" }}>
                {[["Latency", sp.latency], ["Cost", sp.cost], ["Tokens", sp.tokens], ["Started", sp.started]].map(([k, v]) => (
                  <div key={k}><div style={KICK}>{k}</div><div style={{ ...MONO, color: "var(--color-text)", marginTop: "3px" }}>{v || "—"}</div></div>
                ))}
              </div>
              {sp.error ? <div style={{ fontSize: "12.5px", color: "var(--st-risk)", marginTop: "10px" }}>{"Error: " + sp.error}</div> : null}
              {sp.sql ? (
                <>
                  <Pre label="Query" text={sp.sql} />
                  {sp.sqlParams ? <Pre label="Parameters" text={sp.sqlParams} /> : null}
                  <div style={{ marginTop: "12px" }}>
                    <div style={KICK}>{"Result rows" + (sp.rowCount !== null ? " · " + sp.rowCount : "")}</div>
                    <Rows rows={sp.rows} count={sp.rowCount} truncated={sp.rowsTruncated} />
                  </div>
                </>
              ) : (
                <>
                  <Pre label="Input" text={sp.input} />
                  <Pre label="Output" text={sp.output} />
                </>
              )}
            </>
          ) : <div style={{ fontSize: "12.5px", color: "var(--color-neutral-500)" }}>{"Pick a span on the left."}</div>}
          <Pre label="Final answer" text={d.answer} />
        </div>
      </div>
    </>
  );
}

export default function Traces({ vals }) {
  const inRun = vals.tpTurnLoading || vals.tpTurnError || vals.tpDetail;
  return (
    <div style={{ maxWidth: "1280px", margin: "0 auto", padding: "0 24px 60px" }}>
      <Crumbs vals={vals} run={inRun ? (vals.tpDetail ? vals.tpDetail.when : "Run") : null} />
      {inRun ? <Run vals={vals} /> : (
        <>
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-end", gap: "16px", marginTop: "14px", flexWrap: "wrap" }}>
            <div>
              <h2 style={{ margin: "0", fontSize: "24px" }}>{"Hoist Traces"}</h2>
              <p style={{ margin: "6px 0 0", fontSize: "12.5px", color: "var(--color-neutral-500)", maxWidth: "760px", lineHeight: "1.5" }}>
                {"Every turn the chat answered: what it cost, how long it took, which models and tools ran, and whether it failed. Open a run to see the trace — each model and tool call with its input and output — and rate the answer; rated runs are the labelled export."}
              </p>
            </div>
            <div style={{ display: "flex", gap: "8px" }}>
              <button type="button" onClick={vals.tpReload} style={QUIET}>{"Refresh"}</button>
              {vals.tpCanManage ? <button type="button" onClick={vals.tpExportRated} disabled={vals.tpExporting} style={QUIET}>{vals.tpExporting ? "Exporting…" : "Export rated (JSONL)"}</button> : null}
              {vals.tpCanManage ? <button type="button" onClick={vals.tpExport} disabled={vals.tpExporting} style={QUIET}>{"Export all"}</button> : null}
            </div>
          </div>
          {vals.tpUnavailable ? <div style={{ ...CARD, marginTop: "16px", padding: "14px 16px", fontSize: "12.5px", color: "var(--st-warn)" }}>{"The trace store is not available on this deployment right now."}</div> : null}
          {vals.tpError ? <div style={{ ...CARD, marginTop: "16px", padding: "14px 16px", fontSize: "12.5px", color: "var(--st-risk)" }}>{"Could not read the traces: " + vals.tpError}</div> : null}

          <div style={{ display: "flex", gap: "8px", alignItems: "center", marginTop: "16px", flexWrap: "wrap" }}>
            {vals.tpRanges.map((r) => <button key={r.key} type="button" onClick={r.pick} style={chip(r.on)}>{r.label}</button>)}
            <span style={{ width: "1px", height: "22px", background: "var(--color-divider)", margin: "0 4px" }}></span>
            {vals.tpStatuses.map((r) => <button key={r.label} type="button" onClick={r.pick} style={chip(r.on)}>{r.label}</button>)}
            <form onSubmit={(e) => { e.preventDefault(); vals.tpSearch(); }} style={{ marginLeft: "auto", display: "flex", gap: "6px" }}>
              <input aria-label="Search questions and answers" className="input" placeholder="Search questions and answers…" value={vals.tpQuery} onChange={vals.tpSetQuery}
                style={{ fontSize: "12.5px", padding: "7px 10px", borderRadius: "7px", border: "1px solid var(--color-divider)", background: "var(--color-surface)", color: "var(--color-text)", minWidth: "280px" }} />
              <button type="submit" style={QUIET}>{"Search"}</button>
            </form>
          </div>

          <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit,minmax(180px,1fr))", gap: "12px", marginTop: "16px" }}>
            {vals.tpTiles.map((t) => (
              <div key={t.label} style={{ ...CARD, padding: "14px 16px" }}>
                <div style={{ fontSize: "24px", fontWeight: "600", color: t.tone, lineHeight: "1", ...MONO }}>{t.value}</div>
                <div style={{ ...KICK, marginTop: "8px" }}>{t.label}</div>
                <div style={{ fontSize: "11.5px", color: "var(--color-neutral-500)", marginTop: "3px" }}>{t.hint}</div>
              </div>
            ))}
          </div>

          <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit,minmax(340px,1fr))", gap: "14px", marginTop: "14px" }}>
            <div style={{ ...CARD, padding: "14px 16px" }}>
              <div style={KICK}>{"Cost and turns by day"}</div>
              {vals.tpDays.length ? (
                <div style={{ display: "flex", alignItems: "flex-end", gap: "6px", height: "120px", marginTop: "12px" }}>
                  {vals.tpDays.map((d) => (
                    <div key={d.day} title={d.day + " · " + d.turns + " turns · " + d.cost + " · p95 " + d.p95 + (d.failed ? " · " + d.failed + " failed" : "")} style={{ flex: "1 1 0", display: "flex", flexDirection: "column", justifyContent: "flex-end", alignItems: "stretch", gap: "2px", height: "100%" }}>
                      <div style={{ height: d.costPct + "%", background: "var(--color-accent)", borderRadius: "3px 3px 0 0", minHeight: "2px", opacity: "0.85" }}></div>
                      <div style={{ height: Math.max(2, d.turnsPct * 0.4) + "px", background: d.failed ? "var(--st-risk)" : "var(--color-neutral-300)", borderRadius: "2px" }}></div>
                    </div>
                  ))}
                </div>
              ) : <div style={{ fontSize: "12px", color: "var(--color-neutral-500)", marginTop: "10px" }}>{"No turns in this range."}</div>}
              <div style={{ fontSize: "11px", color: "var(--color-neutral-500)", marginTop: "8px" }}>{"Tall bars: cost · short bars: turns (red when any failed) · hover for the day"}</div>
            </div>
            <div style={{ ...CARD, padding: "14px 16px" }}>
              <div style={KICK}>{"By model"}</div>
              <table style={{ width: "100%", borderCollapse: "collapse", marginTop: "8px" }}>
                <tbody>
                  {vals.tpModels.map((m) => (
                    <tr key={m.model}><td style={{ ...CELL, ...MONO, padding: "7px 0" }}>{m.model}</td><td style={{ ...CELL, padding: "7px 0", textAlign: "right", ...MONO }}>{m.calls + " calls"}</td>
                      <td style={{ ...CELL, padding: "7px 0", textAlign: "right", ...MONO }}>{m.cost}</td><td style={{ ...CELL, padding: "7px 0", textAlign: "right", ...MONO, color: "var(--color-neutral-500)" }}>{"p50 " + m.p50}</td></tr>
                  ))}
                  {!vals.tpModels.length ? <tr><td style={{ ...CELL, padding: "7px 0", color: "var(--color-neutral-500)" }}>{"No model calls yet."}</td></tr> : null}
                </tbody>
              </table>
            </div>
            <div style={{ ...CARD, padding: "14px 16px" }}>
              <div style={KICK}>{"By tool"}</div>
              <table style={{ width: "100%", borderCollapse: "collapse", marginTop: "8px" }}>
                <tbody>
                  {vals.tpTools.slice(0, 10).map((x) => (
                    <tr key={x.name}><td style={{ ...CELL, ...MONO, padding: "7px 0" }}>{x.name}</td><td style={{ ...CELL, padding: "7px 0", textAlign: "right", ...MONO }}>{x.calls}</td>
                      <td style={{ ...CELL, padding: "7px 0", textAlign: "right", ...MONO, color: "var(--color-neutral-500)" }}>{"p50 " + x.p50 + " · p95 " + x.p95}</td>
                      <td style={{ ...CELL, padding: "7px 0", textAlign: "right", color: x.failed ? "var(--st-risk)" : "var(--color-neutral-500)" }}>{x.failed ? x.failed + " failed" : ""}</td></tr>
                  ))}
                  {!vals.tpTools.length ? <tr><td style={{ ...CELL, padding: "7px 0", color: "var(--color-neutral-500)" }}>{"No tool calls yet."}</td></tr> : null}
                </tbody>
              </table>
            </div>
          </div>

          <div style={{ ...CARD, marginTop: "14px", overflowX: "auto" }}>
            <div style={{ ...KICK, padding: "14px 16px 6px" }}>{"Runs"}</div>
            {vals.tpLoading ? <div style={{ padding: "18px", fontSize: "12.5px", color: "var(--color-neutral-500)" }}>{"Reading the runs…"}</div>
              : vals.tpEmpty ? <div style={{ padding: "22px 18px", fontSize: "12.5px", color: "var(--color-neutral-500)" }}>{"No runs in this range. Every chat turn from now on lands here."}</div>
                : (
                  <table style={{ width: "100%", borderCollapse: "collapse" }}>
                    <thead><tr style={{ textAlign: "left", ...KICK }}><th style={CELL}>{"When"}</th><th style={CELL}>{"Question"}</th><th style={CELL}>{"Who"}</th><th style={CELL}>{"Calls"}</th><th style={CELL}>{"Cost"}</th><th style={CELL}>{"Latency"}</th><th style={CELL}>{"Status"}</th></tr></thead>
                    <tbody>
                      {vals.tpRuns.map((r) => (
                        <tr key={r.id} onClick={r.open} style={{ cursor: "pointer" }}>
                          <td style={{ ...CELL, whiteSpace: "nowrap", color: "var(--color-neutral-400)" }}>{r.when}</td>
                          <td style={{ ...CELL, maxWidth: "520px" }}>
                            <div style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{r.question}</div>
                            <div style={{ fontSize: "11px", color: "var(--color-neutral-500)", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", marginTop: "2px" }}>{r.error ? r.error : r.answer}</div>
                          </td>
                          <td style={{ ...CELL, whiteSpace: "nowrap", color: "var(--color-neutral-400)" }}>{r.who}</td>
                          <td style={{ ...CELL, whiteSpace: "nowrap", ...MONO }}>{r.llm + " llm · " + r.tools + " tool"}</td>
                          <td style={{ ...CELL, whiteSpace: "nowrap", ...MONO }}>{r.cost + (r.costIncomplete ? "*" : "")}</td>
                          <td style={{ ...CELL, whiteSpace: "nowrap", ...MONO }}>{r.latency}</td>
                          <td style={{ ...CELL, whiteSpace: "nowrap" }}>
                            <span style={{ fontSize: "10.5px", padding: "2px 8px", borderRadius: "999px", background: r.ok ? "var(--st-ok-bg, transparent)" : "var(--st-risk-bg, transparent)", color: r.ok ? "var(--st-ok)" : "var(--st-risk)", border: "1px solid currentColor" }}>{r.ok ? "answered" : "failed"}</span>
                            {r.rating ? <i className={"ph ph-thumbs-" + r.rating} style={{ marginLeft: "8px", color: r.rating === "up" ? "var(--st-ok)" : "var(--st-risk)" }}></i> : null}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                )}
          </div>
        </>
      )}
    </div>
  );
}
