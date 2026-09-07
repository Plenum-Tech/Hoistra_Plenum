"""
demo/ingestion_ui.py

Streamlit UI — CAFM Document Ingestion Tester

Two-phase flow:
  1. Upload + click "Preview" → dry_run=True → extracted entities shown, NO DB write
  2. Review the preview → click "Approve & Write to DB" → dry_run=False → committed

Run:
    streamlit run demo/ingestion_ui.py

Requirements:
    pip install streamlit httpx pandas
"""

from __future__ import annotations

import json
import os

import httpx
import pandas as pd
import streamlit as st

# ── Config ────────────────────────────────────────────────────────────────────

INGESTION_URL = os.environ.get("INGESTION_URL", "http://localhost:8001")

ACCEPT_COLOUR    = "#28a745"   # green
REVIEW_COLOUR    = "#ffc107"   # amber
REEXTRACT_COLOUR = "#dc3545"   # red

ENTITY_LABELS = {
    "assets":       "Assets",
    "work_orders":  "Work Orders",
    "findings":     "Findings",
    "readings":     "Readings",
    "technicians":  "Technicians",
    "vendors":      "Vendors",
    "certificates": "Certificates",
    "spare_parts":  "Spare Parts",
}

ENDPOINT_MAP = {
    "pdf":  "/ingest/pdf",
    "docx": "/ingest/word",
    "doc":  "/ingest/word",
    "csv":  "/ingest/csv",
    "tsv":  "/ingest/csv",
    "xlsx": "/ingest/excel",
    "xls":  "/ingest/excel",
    "xlsm": "/ingest/excel",
}

# ── Page setup ────────────────────────────────────────────────────────────────

st.set_page_config(
    page_title="CAFM Ingestion Tester",
    page_icon="📄",
    layout="wide",
)

st.title("📄 CAFM Document Ingestion Tester")
st.caption(
    f"Uploads documents to **svc-ingestion** (`{INGESTION_URL}`) "
    "and shows every extracted CAFM entity."
)

# ── Sidebar ───────────────────────────────────────────────────────────────────

with st.sidebar:
    st.header("⚙️ Settings")
    ingestion_url = st.text_input("Ingestion Service URL", value=INGESTION_URL)
    force_multipass = st.checkbox(
        "Force 3-pass voting (PDF only)",
        value=False,
        help="Use for compliance certificates and legal documents",
    )
    st.divider()
    st.markdown("**Endpoints used:**")
    st.code(
        "POST /ingest/pdf\n"
        "POST /ingest/word\n"
        "POST /ingest/csv\n"
        "POST /ingest/excel"
    )
    st.divider()
    if st.button("🔍 Check service health"):
        try:
            r = httpx.get(f"{ingestion_url}/health", timeout=5)
            if r.status_code == 200:
                st.success(f"✓ Service OK — {r.json().get('service','')}")
            else:
                st.error(f"HTTP {r.status_code}")
        except Exception as e:
            st.error(f"Unreachable: {e}")

# ── Session state ─────────────────────────────────────────────────────────────
# Stores the dry_run preview result between the two button clicks.

if "preview_result" not in st.session_state:
    st.session_state.preview_result = None
if "preview_filename" not in st.session_state:
    st.session_state.preview_filename = None
if "preview_bytes" not in st.session_state:
    st.session_state.preview_bytes = None
if "preview_ext" not in st.session_state:
    st.session_state.preview_ext = None

# ── File uploader ─────────────────────────────────────────────────────────────

uploaded = st.file_uploader(
    "Drop your document here",
    type=["pdf", "docx", "doc", "csv", "tsv", "xlsx", "xls", "xlsm"],
    help="PDF, Word, CSV, TSV, or Excel — the correct agent is chosen automatically",
)

if not uploaded:
    st.info("Upload a file above to start extraction.")
    # Clear stale preview when file is removed
    st.session_state.preview_result = None
    st.stop()

ext = uploaded.name.rsplit(".", 1)[-1].lower()
endpoint = ENDPOINT_MAP.get(ext)
if not endpoint:
    st.error(f"Unsupported file type: .{ext}")
    st.stop()

# If a different file was uploaded, clear the old preview
if st.session_state.preview_filename != uploaded.name:
    st.session_state.preview_result = None
    st.session_state.preview_filename = uploaded.name
    st.session_state.preview_bytes = uploaded.getvalue()
    st.session_state.preview_ext = ext

st.markdown(f"**File:** `{uploaded.name}` · **Agent:** `{endpoint}`")

# ── Helper: call the API ───────────────────────────────────────────────────────

def _call_api(file_bytes: bytes, filename: str, file_ext: str, dry_run: bool) -> dict:
    """POST to the ingestion endpoint. Returns parsed JSON or raises."""
    mime_map = {
        "pdf":  "application/pdf",
        "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "doc":  "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "csv":  "text/csv",
        "tsv":  "text/csv",
        "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "xls":  "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "xlsm": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    }
    mime = mime_map.get(file_ext, "application/octet-stream")
    ep = ENDPOINT_MAP[file_ext]
    files = {"file": (filename, file_bytes, mime)}
    data: dict = {"dry_run": str(dry_run).lower()}
    if file_ext == "pdf":
        data["force_multipass"] = str(force_multipass).lower()

    resp = httpx.post(
        f"{ingestion_url}{ep}",
        files=files,
        data=data,
        timeout=120,
    )
    if resp.status_code != 200:
        raise RuntimeError(f"HTTP {resp.status_code}: {resp.text[:500]}")
    return resp.json()


# ── Render results ────────────────────────────────────────────────────────────

def _render_results(result: dict, is_preview: bool) -> None:
    """Draw the extraction results section."""
    st.divider()

    if is_preview:
        st.subheader("👁️ Extraction Preview  *(not yet written to DB)*")
    else:
        st.subheader("✅ Committed to Database")

    entity_counts: dict = result.get("entity_counts", {})
    confidence:    dict = result.get("confidence", {})
    audit:         dict = result.get("audit", {})
    route:         str  = result.get("route", "unknown")
    eval_score: float | None = confidence.get("eval_score")

    total_entities = sum(entity_counts.values())

    m1, m2, m3, m4, m5 = st.columns(5)
    with m1:
        st.metric("Entities Extracted", total_entities)
    with m2:
        overall = confidence.get("overall", "—")
        st.metric("Overall Confidence", overall.upper() if overall else "—")
    with m3:
        st.metric("EL-2.3 eval_score", f"{eval_score:.2f}" if eval_score is not None else "N/A")
    with m4:
        route_labels = {
            "accept":       "✅ ACCEPT",
            "review_queue": "⚠️ REVIEW QUEUE",
            "re_extract":   "❌ RE-EXTRACT",
        }
        st.metric("Route Decision", route_labels.get(route, route.upper()))
    with m5:
        cost = audit.get("cost_usd", 0)
        ms   = audit.get("processing_ms", 0)
        st.metric("Cost / Time", f"${cost:.4f} / {ms:,}ms")

    # Route banner
    if route == "accept":
        st.success(
            f"✅ **ACCEPTED** — eval_score {eval_score:.2f} ≥ 0.85"
            + (" — preview only, data not yet written" if is_preview else " — data written to DB")
        )
    elif route == "review_queue":
        st.warning(
            f"⚠️ **REVIEW QUEUE** — eval_score {eval_score:.2f} is 0.60–0.84. "
            "A human reviewer should verify before writing to DB."
        )
    elif route == "re_extract":
        st.error(
            f"❌ **RE-EXTRACT** — eval_score {eval_score:.2f} < 0.60. "
            "Extraction quality too low."
        )

    # Rules violations
    violations = confidence.get("rules_violations", [])
    if violations:
        st.warning("**Contradiction rules fired:**")
        for v in violations:
            st.markdown(f"  - {v}")

    # Entity tables
    st.divider()
    st.subheader("🗂️ Extracted Entities")
    entities: dict = result.get("entities", {})
    has_any = False

    for key, label in ENTITY_LABELS.items():
        rows = entities.get(key, [])
        if not rows:
            continue
        has_any = True
        with st.expander(f"**{label}** ({len(rows)} records)", expanded=True):
            df = pd.DataFrame(rows)
            conf_cols = [c for c in df.columns if "confidence" in c.lower()]
            if conf_cols:
                def _colour_conf(val):
                    try:
                        v = float(val)
                        if v >= 0.85:
                            return f"background-color: {ACCEPT_COLOUR}33; color: #155724"
                        if v >= 0.60:
                            return f"background-color: {REVIEW_COLOUR}33; color: #856404"
                        return f"background-color: {REEXTRACT_COLOUR}33; color: #721c24"
                    except (TypeError, ValueError):
                        return ""
                styled = df.style.applymap(_colour_conf, subset=conf_cols)
                st.dataframe(styled, use_container_width=True)
            else:
                st.dataframe(df, use_container_width=True)

    if not has_any:
        st.info(
            "No entities were extracted. This may mean:\n"
            "- The document type doesn't match the expected format\n"
            "- The document contains no CAFM-relevant data\n"
            "- Check the raw response below for details"
        )

    # Per-field confidence
    per_field = confidence.get("per_field", {})
    if per_field:
        st.divider()
        st.subheader("🎯 Per-Field Confidence")
        pf_df = pd.DataFrame(
            [{"Field": k, "Confidence": v} for k, v in per_field.items()]
        ).sort_values("Field")
        def _badge(val):
            v = str(val).lower()
            if v == "high":
                return f"background-color: {ACCEPT_COLOUR}33"
            if v == "medium":
                return f"background-color: {REVIEW_COLOUR}33"
            return f"background-color: {REEXTRACT_COLOUR}33"
        st.dataframe(
            pf_df.style.applymap(_badge, subset=["Confidence"]),
            use_container_width=True,
            hide_index=True,
        )

    # Audit / cost
    st.divider()
    st.subheader("💰 Audit & Cost")
    a1, a2, a3, a4 = st.columns(4)
    a1.metric("Tokens In",  f"{audit.get('tokens_in', 0):,}")
    a2.metric("Tokens Out", f"{audit.get('tokens_out', 0):,}")
    a3.metric("Cost (USD)", f"${audit.get('cost_usd', 0):.6f}")
    a4.metric("Cost (AED)", f"AED {audit.get('cost_aed', 0):.4f}")
    st.caption(
        f"Model: `{result.get('model_used', '—')}` · "
        f"Method: `{result.get('extraction_method', '—')}` · "
        f"Ingestion ID: `{result.get('ingestion_id', '—')}`"
    )

    # ── Unmatched columns / schema extension (CSV only) ──────────────────────
    unmatched = result.get("unmatched_columns", [])
    target_table = result.get("target_table", "")
    if unmatched and target_table and target_table not in ("unknown", ""):
        st.divider()
        st.subheader("🔧 Unmatched Columns — Extend DB Schema?")
        st.caption(
            f"These columns from your file didn't map to any canonical CAFM field "
            f"and are currently stored in `raw_metadata`. "
            f"You can add them as real columns in `plenum_cafm.{target_table}`."
        )

        COLUMN_TYPES = ["text", "integer", "decimal", "boolean", "date", "timestamp"]

        col_defs: list[dict] = []
        for col in unmatched:
            c1, c2, c3 = st.columns([3, 2, 1])
            with c1:
                st.text(col)
            with c2:
                col_type = st.selectbox(
                    "Type",
                    COLUMN_TYPES,
                    key=f"type_{col}",
                    label_visibility="collapsed",
                )
            with c3:
                include = st.checkbox("Add", key=f"add_{col}", value=False)
            if include:
                col_defs.append({"column_name": col.lower().replace(" ", "_"), "column_type": col_type})

        if col_defs:
            if st.button("Apply Schema Changes", type="primary"):
                payload = {"table_name": target_table, "columns": col_defs}
                try:
                    r = httpx.post(
                        f"{ingestion_url}/schema/extend",
                        json=payload,
                        timeout=30,
                    )
                    resp_json = r.json()
                    if r.status_code == 200:
                        added = resp_json.get("added", [])
                        errors = resp_json.get("errors", [])
                        if added:
                            st.success(
                                f"Added columns to `{target_table}`: "
                                + ", ".join(f"`{c}`" for c in added)
                            )
                        if errors:
                            for err in errors:
                                st.error(err)
                    else:
                        st.error(f"HTTP {r.status_code}: {resp_json}")
                except Exception as ex:
                    st.error(f"Failed to extend schema: {ex}")
        else:
            st.info("Check the boxes next to any columns you want to add to the database.")

    # Raw JSON
    with st.expander("🔍 Raw API Response (JSON)", expanded=False):
        st.json(result)


# ══════════════════════════════════════════════════════════════════════════════
# PHASE 1 — PREVIEW BUTTON
# ══════════════════════════════════════════════════════════════════════════════

col_preview, col_approve, col_discard = st.columns([2, 2, 1])

with col_preview:
    run_preview = st.button(
        "👁️ Preview Extraction",
        type="primary",
        use_container_width=True,
        help="Extract entities without writing to the database",
    )

# Trigger preview
if run_preview:
    file_bytes = uploaded.getvalue()
    with st.spinner(f"Extracting `{uploaded.name}` (dry run — no DB write)…"):
        try:
            result = _call_api(file_bytes, uploaded.name, ext, dry_run=True)
            st.session_state.preview_result = result
            st.session_state.preview_bytes = file_bytes
            st.session_state.preview_ext = ext
        except httpx.ConnectError:
            st.error(
                f"Cannot reach svc-ingestion at `{ingestion_url}`. "
                "Is `docker compose up svc-ingestion` running?"
            )
            st.stop()
        except httpx.TimeoutException:
            st.error("Request timed out (> 120s).")
            st.stop()
        except RuntimeError as e:
            st.error(str(e))
            st.stop()

# ── Show preview if we have one ───────────────────────────────────────────────

if st.session_state.preview_result is not None:
    _render_results(st.session_state.preview_result, is_preview=True)

    # ══════════════════════════════════════════════════════════════════════════
    # PHASE 2 — APPROVE / DISCARD BUTTONS
    # ══════════════════════════════════════════════════════════════════════════

    st.divider()
    st.markdown("### Commit to Database?")
    st.caption(
        "The preview above shows what will be written. "
        "Click **Approve** to commit, or **Discard** to cancel."
    )

    col_a, col_d, _ = st.columns([2, 2, 4])

    with col_a:
        approve = st.button(
            "✅ Approve & Write to DB",
            type="primary",
            use_container_width=True,
        )
    with col_d:
        discard = st.button(
            "🗑️ Discard",
            use_container_width=True,
        )

    if discard:
        st.session_state.preview_result = None
        st.info("Discarded. Upload a new file to start again.")
        st.rerun()

    if approve:
        file_bytes = st.session_state.preview_bytes
        file_ext   = st.session_state.preview_ext
        filename   = st.session_state.preview_filename

        with st.spinner(f"Writing `{filename}` to database…"):
            try:
                committed = _call_api(file_bytes, filename, file_ext, dry_run=False)
            except httpx.ConnectError:
                st.error("Lost connection to svc-ingestion during commit.")
                st.stop()
            except httpx.TimeoutException:
                st.error("Commit timed out.")
                st.stop()
            except RuntimeError as e:
                st.error(str(e))
                st.stop()

        # Clear preview state
        st.session_state.preview_result = None

        st.success(
            f"✅ **Committed!** "
            f"{sum(committed.get('entity_counts', {}).values())} entities "
            f"written to the database."
        )
        _render_results(committed, is_preview=False)
