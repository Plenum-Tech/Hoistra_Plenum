"""Streamlit UI for the 8-node Schema Mapping Pipeline.

Connects to the svc-ai-schema-mapper production API.
Polls job status, renders per-node result tabs, and surfaces HITL gate review UIs
for Gate 1 (Node 4 — field mapping) and Gate 2 (Node 6 — hierarchy verification).

Usage:
    streamlit run streamlit_schema_mapper.py
"""

import json
import time
from datetime import datetime
from typing import Any, Optional

import httpx
import pandas as pd
import streamlit as st

st.set_page_config(
    page_title="Schema Mapper — 8-Node Pipeline",
    page_icon="🗺️",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ── Session state ──────────────────────────────────────────────────────────────

def _init_state() -> None:
    defaults = {
        "sm_id": None,                  # current schema_mapping_id
        "sm_status_data": None,         # latest GET /status response
        "sm_accumulated_logs": [],       # appended on each poll
        "fiix_schema": None,            # fetched Fiix schema payload
        "fiix_is_active": False,
        "uploaded_schema_text": None,   # manually pasted / uploaded schema
        "last_poll_ts": 0.0,
    }
    for k, v in defaults.items():
        if k not in st.session_state:
            st.session_state[k] = v

_init_state()


# ── Helpers ────────────────────────────────────────────────────────────────────

def _api(path: str, *, base: str) -> str:
    return f"{base.rstrip('/')}{path}"


def _poll_status(api_url: str, sm_id: str) -> Optional[dict]:
    """Call GET /api/schema-mapping/{id}/status and return JSON."""
    try:
        r = httpx.get(_api(f"/api/schema-mapping/{sm_id}/status", base=api_url), timeout=15)
        if r.status_code == 200:
            return r.json()
        st.error(f"Status poll failed: HTTP {r.status_code} — {r.text[:200]}")
    except Exception as e:
        st.error(f"Status poll error: {e}")
    return None


def _display_event_logger(logs: list[str], title: str) -> None:
    """Render execution logs in a collapsible expander with filter + search."""
    if not logs:
        st.info("No execution logs available.")
        return

    with st.expander(f"📜 Execution Logs — {title} ({len(logs)} entries)", expanded=False):
        col_lv, col_sr = st.columns([1, 3])
        with col_lv:
            level_filter = st.selectbox(
                "Level", ["All", "INFO", "WARNING", "ERROR", "DEBUG"],
                key=f"log_lvl_{title.replace(' ', '_')}"
            )
        with col_sr:
            search = st.text_input(
                "Search", placeholder="filter by keyword…",
                key=f"log_srch_{title.replace(' ', '_')}"
            )

        filtered = logs
        if level_filter != "All":
            filtered = [l for l in filtered if level_filter in l]
        if search:
            filtered = [l for l in filtered if search.lower() in l.lower()]

        if filtered:
            st.code("\n".join(filtered), language="", line_numbers=True)
            st.caption(f"Showing {len(filtered)} of {len(logs)} entries")
        else:
            st.info("No entries match the current filter.")


def _node_badge(node: int, current: Optional[int]) -> str:
    if current is None:
        return "⬜"
    if node < current:
        return "✅"
    if node == current:
        return "🔄"
    return "⬜"


NODE_LABELS = {
    0: "Fetch Canonical Schema",
    1: "Ingest",
    2: "Deterministic Map",
    3: "Semantic Map",
    4: "⏸ Gate 1: Field Mapping",
    5: "Hierarchy Detection",
    6: "⏸ Gate 2: Hierarchy",
    7: "Generate Output",
    8: "Write to DB",
}


def _progress_strip(current_node: Optional[int]) -> None:
    """Render a 9-node pipeline progress strip."""
    cols = st.columns(9)
    for i, (node_num, label) in enumerate(NODE_LABELS.items()):
        badge = _node_badge(node_num, current_node)
        short = label.replace("⏸ ", "").split(":")[0].split(" ")[:2]
        with cols[i]:
            st.markdown(
                f"<div style='text-align:center;font-size:1.3em'>{badge}</div>"
                f"<div style='text-align:center;font-size:0.65em'>{' '.join(short)}</div>",
                unsafe_allow_html=True,
            )


# ── Sidebar ────────────────────────────────────────────────────────────────────

with st.sidebar:
    st.title("⚙️ Config")

    api_url = st.text_input(
        "API Base URL",
        value="http://localhost:8003",
        help="Where svc-ai-schema-mapper is running.",
    )
    organization_id = st.text_input(
        "Organization ID (UUID)",
        value="00000000-0000-0000-0000-000000000001",
    )
    cmms_name = st.selectbox(
        "Source CMMS",
        ["Fiix", "Maximo", "SAP PM", "Archibus", "Infor EAM", "eMaint", "Hippo CMMS", "Custom"],
        index=0,
    )

    st.divider()

    # Health check
    if st.button("💊 Check Service", use_container_width=True):
        try:
            r = httpx.get(_api("/health", base=api_url), timeout=5)
            if r.status_code == 200:
                d = r.json()
                st.success(f"✅ Online v{d.get('version', '?')}")
            else:
                st.error(f"HTTP {r.status_code}")
        except Exception as e:
            st.error(f"Unreachable: {e}")

    st.divider()

    # Fiix connector
    st.subheader("🔗 Fiix CMMS Connector")
    col_t, col_f = st.columns(2)

    with col_t:
        if st.button("🔐 Test Fiix", use_container_width=True):
            try:
                with st.spinner("Testing…"):
                    r = httpx.get(_api("/api/platforms/fiix/test-connection", base=api_url), timeout=30)
                    if r.status_code == 200:
                        st.success(f"✅ {r.json().get('message', 'Connected')}")
                    else:
                        st.error(r.json().get("detail", r.text))
            except httpx.ConnectError:
                st.error(f"Cannot connect to {api_url}")
            except Exception as e:
                st.error(str(e))

    with col_f:
        if st.button("📥 Fetch Schema", use_container_width=True):
            try:
                with st.spinner("Fetching Fiix schema (may take a minute)…"):
                    r = httpx.get(
                        _api("/api/platforms/fiix/fetch-schema", base=api_url),
                        timeout=300,
                    )
                    if r.status_code == 200:
                        data = r.json()
                        st.session_state.fiix_schema = data
                        st.session_state.fiix_is_active = True
                        field_count = len(data.get("mapper", {}).get("canonical_fields", {}))
                        st.success(f"✅ {field_count} fields loaded")
                        st.rerun()
                    else:
                        st.error(r.json().get("detail", r.text))
            except httpx.TimeoutException:
                st.error("Timed out — Fiix schema extraction takes time for large instances.")
            except httpx.ConnectError:
                st.error(f"Cannot connect to {api_url}")

    if st.session_state.fiix_is_active:
        st.info("✓ Fiix schema loaded")
        if st.button("🔄 Clear Fiix Schema", use_container_width=True):
            st.session_state.fiix_schema = None
            st.session_state.fiix_is_active = False
            st.rerun()

    st.divider()

    # Reset current job
    if st.session_state.sm_id:
        st.caption(f"**Active job:** `{str(st.session_state.sm_id)[:8]}…`")
        if st.button("🗑️ Clear Current Job", use_container_width=True):
            st.session_state.sm_id = None
            st.session_state.sm_status_data = None
            st.session_state.sm_accumulated_logs = []
            st.rerun()


# ── Main area ──────────────────────────────────────────────────────────────────

st.title("🗺️ AI Schema Mapper — 8-Node Pipeline")
st.caption(
    "Start the full schema-mapping LangGraph pipeline. "
    "Results appear tab-by-tab as each node completes. "
    "HITL gates pause for your review before continuing."
)
st.divider()

# ── Schema source section ──────────────────────────────────────────────────────

st.subheader("1️⃣ Schema Source")

schema_source = st.radio(
    "Choose how to provide the source schema:",
    ["Fetch from Fiix (live)", "Upload schema file", "Paste JSON"],
    horizontal=True,
)

schema_content: Optional[str] = None
schema_format: str = "json"
schema_source_label: str = "manual"

if schema_source == "Fetch from Fiix (live)":
    if st.session_state.fiix_is_active and st.session_state.fiix_schema:
        schema_data = st.session_state.fiix_schema
        mapper = schema_data.get("mapper", schema_data)
        field_count = len(mapper.get("canonical_fields", {}))
        st.success(f"✅ Fiix schema ready — {field_count} canonical fields")
        with st.expander("Preview schema", expanded=False):
            st.json(schema_data)
        schema_content = json.dumps(schema_data)
        schema_format = "json"
        schema_source_label = "fiix"
    else:
        st.info("Use **Fetch Schema** in the sidebar to load the Fiix schema first.")

elif schema_source == "Upload schema file":
    uploaded = st.file_uploader(
        "Upload schema (JSON, CSV, XLSX)",
        type=["json", "csv", "xlsx", "xls"],
        help="Upload the CMMS source schema or data export file",
    )
    if uploaded:
        if uploaded.name.endswith(".json"):
            schema_content = uploaded.read().decode("utf-8", errors="replace")
            schema_format = "json"
        else:
            # For CSV/XLSX: send raw bytes as base64-encoded string
            import base64
            schema_content = base64.b64encode(uploaded.read()).decode("utf-8")
            schema_format = "csv" if uploaded.name.endswith(".csv") else "xlsx"
        schema_source_label = "upload"
        st.success(f"✅ Loaded: **{uploaded.name}** ({uploaded.size / 1024:.1f} KB)")

elif schema_source == "Paste JSON":
    pasted = st.text_area(
        "Paste schema JSON here",
        height=200,
        placeholder='{"tables": [...], "fields": [...]}',
        key="schema_paste",
    )
    if pasted.strip():
        try:
            json.loads(pasted)  # validate
            schema_content = pasted
            schema_format = "json"
            schema_source_label = "manual"
            st.success("✅ Valid JSON")
        except json.JSONDecodeError as e:
            st.error(f"Invalid JSON: {e}")

st.divider()

# ── Start pipeline ─────────────────────────────────────────────────────────────

st.subheader("2️⃣ Start Pipeline")

col_start, col_info = st.columns([1, 3])

with col_start:
    start_disabled = schema_content is None and not st.session_state.sm_id
    start_btn = st.button(
        "▶️ Start Schema Mapping",
        type="primary",
        use_container_width=True,
        disabled=start_disabled,
        help="Provide a schema source above first" if start_disabled else "",
    )

with col_info:
    if st.session_state.sm_id:
        st.info(
            f"Job in progress: `{st.session_state.sm_id}` — "
            "scroll down to see status and results."
        )
    elif schema_content:
        st.success("Schema loaded — ready to start.")
    else:
        st.warning("Provide a schema source to enable the Start button.")

if start_btn and schema_content:
    payload = {
        "external_cmms_name": cmms_name,
        "organization_id": organization_id,
        "schema_content": schema_content,
        "schema_format": schema_format,
        "schema_source": schema_source_label,
        "connector_type": schema_source_label,
    }
    try:
        with st.spinner("Starting schema mapping pipeline…"):
            resp = httpx.post(
                _api("/api/schema-mapping", base=api_url),
                json=payload,
                timeout=60,
            )
        if resp.status_code in (200, 201):
            data = resp.json()
            sm_id = data.get("schema_mapping_id") or data.get("id")
            if sm_id:
                st.session_state.sm_id = sm_id
                st.session_state.sm_status_data = None
                st.session_state.sm_accumulated_logs = []
                st.success(f"✅ Pipeline started — Job ID: `{sm_id[:8]}…`")
                st.rerun()
            else:
                st.error(f"No schema_mapping_id in response: {data}")
        else:
            st.error(f"Start failed: HTTP {resp.status_code} — {resp.json().get('detail', resp.text)}")
    except httpx.ConnectError:
        st.error(f"Cannot connect to API at {api_url}")
    except Exception as e:
        st.error(f"Error: {e}")

# ── Job status section (only shown when a job is active) ───────────────────────

sm_id = st.session_state.sm_id
if not sm_id:
    st.stop()

st.divider()
st.subheader("3️⃣ Pipeline Status")

# Poll button + auto-poll
col_poll, col_auto = st.columns([1, 4])
with col_poll:
    poll_btn = st.button("🔄 Refresh Status", use_container_width=True)
with col_auto:
    auto_poll = st.checkbox(
        "Auto-refresh every 5 s",
        value=False,
        help="Automatically refreshes the page every 5 seconds while pipeline is running",
    )

# Fetch status
should_poll = poll_btn or (
    auto_poll
    and (time.monotonic() - st.session_state.last_poll_ts) > 5.0
)

if should_poll or st.session_state.sm_status_data is None:
    with st.spinner("Polling status…"):
        status_data = _poll_status(api_url, sm_id)
    if status_data:
        st.session_state.sm_status_data = status_data
        st.session_state.last_poll_ts = time.monotonic()
        # Accumulate logs
        new_logs = status_data.get("execution_logs", [])
        if new_logs:
            existing = set(st.session_state.sm_accumulated_logs)
            for log in new_logs:
                if log not in existing:
                    st.session_state.sm_accumulated_logs.append(log)
                    existing.add(log)
    if auto_poll:
        time.sleep(0.1)
        st.rerun()

status_data = st.session_state.sm_status_data
if not status_data:
    st.info("Press **Refresh Status** to check pipeline progress.")
    st.stop()

# ── Status overview ────────────────────────────────────────────────────────────

status = status_data.get("status", "unknown")
current_node = status_data.get("current_node")
progress_pct = status_data.get("progress_pct", 0.0) or 0.0
pending_gate = status_data.get("pending_gate_type") or status_data.get("gate_type")
error_msg = status_data.get("error_message")

# Status badge
STATUS_COLORS = {
    "running": "🔄",
    "awaiting_review": "⏸️",
    "complete": "✅",
    "failed": "❌",
    "cancelled": "🚫",
}
badge = STATUS_COLORS.get(status, "❓")

col_st1, col_st2, col_st3, col_st4 = st.columns(4)
col_st1.metric("Status", f"{badge} {status.replace('_', ' ').title()}")
col_st2.metric("Current Node", f"Node {current_node}" if current_node is not None else "—")
col_st3.metric("Progress", f"{progress_pct:.0f}%")
col_st4.metric("Gate", pending_gate or "—")

# Progress bar
if progress_pct > 0:
    st.progress(min(progress_pct / 100.0, 1.0))

# Pipeline strip
_progress_strip(current_node)

if error_msg:
    st.error(f"**Pipeline Error:** {error_msg}")

st.divider()

# ── HITL GATE 1 — Field Mapping Review (Node 4) ────────────────────────────────

if status == "awaiting_review" and pending_gate in ("field_mapping", "node4", None):
    gate_payload = (
        status_data.get("pending_gate_payload")
        or status_data.get("human_review_payload")
        or {}
    )

    # If gate_payload is None but we're awaiting_review and current_node is 4,
    # treat it as field mapping gate
    is_field_mapping_gate = (
        pending_gate == "field_mapping"
        or (current_node == 4 and pending_gate is None)
        or (status == "awaiting_review" and gate_payload.get("low_confidence_tier1") is not None)
        or (status == "awaiting_review" and gate_payload.get("unmapped_fields") is not None)
    )

    if is_field_mapping_gate:
        st.subheader("⏸️ GATE 1 — Field Mapping Review (Node 4)")
        st.info(
            "The pipeline is paused. Review low-confidence mappings and unmapped fields. "
            "Submit your decisions to continue."
        )

        total_flagged = gate_payload.get("total_flagged", 0)
        low_conf_t1 = gate_payload.get("low_confidence_tier1", {})
        low_conf_t2 = gate_payload.get("low_confidence_tier2", {})
        unmapped = gate_payload.get("unmapped_fields", {})
        canonical_tables = gate_payload.get("existing_canonical_tables", [])

        col_g1, col_g2, col_g3 = st.columns(3)
        col_g1.metric("Low-Conf Tier 1", sum(len(v) for v in low_conf_t1.values()))
        col_g2.metric("Low-Conf Tier 2", sum(len(v) for v in low_conf_t2.values()))
        col_g3.metric("Unmapped Fields", sum(len(v) for v in unmapped.values()))

        st.divider()

        # Collect all decisions
        decisions: list[dict] = []

        # ── Low-Confidence Tier 1 & Tier 2 mappings ───────────────────────────

        all_low_conf: dict[str, list] = {}
        for tbl, fields in low_conf_t1.items():
            all_low_conf.setdefault(tbl, []).extend(fields)
        for tbl, fields in low_conf_t2.items():
            all_low_conf.setdefault(tbl, []).extend(fields)

        if all_low_conf:
            st.subheader("🟡 Low-Confidence Mappings — Accept, Reject, or Override")

            for tbl, fields in all_low_conf.items():
                with st.expander(f"📋 Table: **{tbl}** ({len(fields)} fields)", expanded=True):
                    for f in fields:
                        src = f.get("source_field", "")
                        suggested = f.get("suggested_target", "")
                        conf = f.get("confidence", 0.0)
                        tier = f.get("tier", "")
                        key_pfx = f"lc_{tbl}_{src}".replace(".", "_").replace(" ", "_")

                        with st.container(border=True):
                            c1, c2, c3, c4 = st.columns([2, 2, 1, 2])
                            c1.markdown(f"**Source:** `{src}`")
                            c2.markdown(f"**Suggested:** `{suggested}`")
                            c3.markdown(f"**Conf:** {conf:.2f}")
                            c4.caption(f"Tier: {tier}")

                            action = st.radio(
                                "Decision",
                                ["accept", "reject", "override"],
                                horizontal=True,
                                key=f"{key_pfx}_action",
                                label_visibility="collapsed",
                            )

                            override_target = ""
                            if action == "override":
                                override_target = st.text_input(
                                    "Override target field",
                                    placeholder="e.g. asset_code",
                                    key=f"{key_pfx}_override",
                                )

                        decision_entry: dict = {
                            "action": action,
                            "source_field": src,
                            "source_table": tbl,
                        }
                        if action == "override" and override_target:
                            decision_entry["target_field"] = override_target
                        decisions.append(decision_entry)

        # ── Unmapped fields ────────────────────────────────────────────────────

        if unmapped:
            st.subheader("🔴 Unmapped Fields — Custom Column, Raw Metadata, or Skip")
            st.caption(
                "**custom** — add a new column to a Plenum CAFM table  |  "
                "**raw_metadata** — store in JSONB column  |  "
                "**skip** — discard"
            )

            for tbl, fields in unmapped.items():
                with st.expander(f"📋 Table: **{tbl}** ({len(fields)} fields)", expanded=True):
                    for f in fields:
                        src = f.get("source_field", "")
                        hint_type = f.get("data_type_hint", "")
                        nullable = f.get("nullable", True)
                        key_pfx = f"um_{tbl}_{src}".replace(".", "_").replace(" ", "_")

                        with st.container(border=True):
                            c1, c2 = st.columns([3, 1])
                            c1.markdown(f"**Field:** `{src}`")
                            c2.caption(f"Type hint: {hint_type or '—'}")

                            action = st.radio(
                                "Decision",
                                ["raw_metadata", "skip", "custom"],
                                horizontal=True,
                                key=f"{key_pfx}_action",
                                label_visibility="collapsed",
                            )

                            custom_entry: dict = {
                                "action": action,
                                "source_field": src,
                                "source_table": tbl,
                            }

                            if action == "custom":
                                cc1, cc2, cc3, cc4 = st.columns([2, 2, 2, 1])
                                target_table = cc1.selectbox(
                                    "Target table",
                                    canonical_tables or ["assets", "work_orders", "parts"],
                                    key=f"{key_pfx}_tgt_tbl",
                                )
                                col_name = cc2.text_input(
                                    "Column name",
                                    placeholder="e.g. vendor_ref",
                                    key=f"{key_pfx}_col",
                                )
                                data_type = cc3.text_input(
                                    "SQL data type",
                                    value="VARCHAR(255)",
                                    key=f"{key_pfx}_dtype",
                                )
                                is_new = cc4.checkbox(
                                    "New table?",
                                    key=f"{key_pfx}_new_tbl",
                                )
                                custom_entry.update({
                                    "target_table": target_table,
                                    "custom_column_name": col_name,
                                    "data_type": data_type,
                                    "nullable": nullable,
                                    "is_new_table": is_new,
                                })

                        decisions.append(custom_entry)

        st.divider()

        # Submit Gate 1 decisions
        if st.button(
            "✅ Submit Field Mapping Decisions",
            type="primary",
            use_container_width=True,
            key="gate1_submit",
        ):
            # Validate custom decisions have required fields
            errors = []
            for d in decisions:
                if d.get("action") == "custom":
                    if not d.get("target_table"):
                        errors.append(f"`{d['source_field']}`: target_table required for custom")
                    if not d.get("custom_column_name"):
                        errors.append(f"`{d['source_field']}`: column name required for custom")
                    if not d.get("data_type"):
                        errors.append(f"`{d['source_field']}`: data_type required for custom")

            if errors:
                for err in errors:
                    st.error(err)
            else:
                try:
                    with st.spinner("Submitting decisions and resuming pipeline…"):
                        resp = httpx.post(
                            _api(f"/api/schema-mapping/{sm_id}/gate/field-mapping", base=api_url),
                            json={"decisions": decisions},
                            timeout=60,
                        )
                    if resp.status_code in (200, 201, 202):
                        st.success("✅ Decisions submitted — pipeline resuming…")
                        st.session_state.sm_status_data = None
                        time.sleep(1)
                        st.rerun()
                    else:
                        st.error(
                            f"Gate submission failed: HTTP {resp.status_code} — "
                            f"{resp.json().get('detail', resp.text)}"
                        )
                except Exception as e:
                    st.error(f"Error submitting gate: {e}")

        st.divider()

# ── HITL GATE 2 — Hierarchy Review (Node 6) ────────────────────────────────────

elif status == "awaiting_review" and pending_gate in ("hierarchy", "node6"):
    gate_payload = (
        status_data.get("pending_gate_payload")
        or status_data.get("hierarchy_review_payload")
        or {}
    )

    st.subheader("⏸️ GATE 2 — Hierarchy Verification (Node 6)")
    st.info(
        "Review detected FK relationships and hierarchical structures. "
        "Approve, reject, or correct each relationship."
    )

    hierarchies = (
        gate_payload.get("confirmed_hierarchies")
        or gate_payload.get("hierarchy_relationships")
        or gate_payload.get("relationships")
        or []
    )
    cycles = gate_payload.get("cycles", [])
    orphans = gate_payload.get("orphans", [])

    col_h1, col_h2, col_h3 = st.columns(3)
    col_h1.metric("Relationships", len(hierarchies))
    col_h2.metric("Cycles Detected", len(cycles))
    col_h3.metric("Orphan Records", len(orphans))

    st.divider()

    # Warn about cycles
    if cycles:
        st.error(
            f"**{len(cycles)} cycle(s) detected** in CONTAINMENT relationships. "
            "These must be resolved before the pipeline can continue."
        )
        for cyc in cycles:
            st.warning(f"Cycle: {cyc}")

    # Hierarchy approval form
    hierarchy_decisions: list[dict] = []

    if hierarchies:
        st.subheader("🌳 Detected Relationships")
        for i, rel in enumerate(hierarchies):
            key_pfx = f"hier_{i}"
            with st.container(border=True):
                c1, c2, c3, c4 = st.columns([2, 2, 2, 1])
                rel_type = rel.get("relationship_type", rel.get("type", "CONTAINMENT"))
                parent = rel.get("parent_table", rel.get("parent", "?"))
                child = rel.get("child_table", rel.get("child", "?"))
                fk_col = rel.get("fk_column", rel.get("fk", ""))
                conf = rel.get("confidence", rel.get("relationship_confidence", 0.0))

                c1.markdown(f"**{parent}** → **{child}**")
                c2.caption(f"FK: `{fk_col}`")
                c3.caption(f"Type: {rel_type} | Conf: {conf:.2f}")

                approved = c4.checkbox("✓ Approve", value=True, key=f"{key_pfx}_approve")

                override_type = ""
                if approved:
                    override_type = st.selectbox(
                        "Relationship type (override optional)",
                        ["", "CONTAINMENT", "REFERENCE", "DEPENDENCY", "ASSOCIATION"],
                        key=f"{key_pfx}_type",
                        label_visibility="collapsed",
                    )

                hierarchy_decisions.append({
                    "approved": approved,
                    "parent_table": parent,
                    "child_table": child,
                    "fk_column": fk_col,
                    "relationship_type": override_type or rel_type,
                })
    else:
        st.info("No hierarchy relationships detected — you can approve without changes.")

    st.divider()

    if st.button(
        "✅ Approve Hierarchies & Continue",
        type="primary",
        use_container_width=True,
        key="gate2_submit",
    ):
        try:
            with st.spinner("Submitting hierarchy decisions…"):
                resp = httpx.post(
                    _api(f"/api/schema-mapping/{sm_id}/gate/hierarchy", base=api_url),
                    json={"decisions": hierarchy_decisions},
                    timeout=60,
                )
            if resp.status_code in (200, 201, 202):
                st.success("✅ Hierarchy decisions submitted — pipeline resuming…")
                st.session_state.sm_status_data = None
                time.sleep(1)
                st.rerun()
            else:
                st.error(
                    f"Gate submission failed: HTTP {resp.status_code} — "
                    f"{resp.json().get('detail', resp.text)}"
                )
        except Exception as e:
            st.error(f"Error submitting gate: {e}")

    st.divider()

# ── Completion banner ──────────────────────────────────────────────────────────

elif status == "complete":
    st.success("🎉 **Pipeline complete!** All 8 nodes finished successfully.")

elif status == "failed":
    st.error(f"Pipeline failed: {error_msg or 'Unknown error'}")
    if st.button("🔄 Retry Pipeline", key="retry_btn"):
        st.session_state.sm_id = None
        st.session_state.sm_status_data = None
        st.rerun()

# ── Result Tabs ────────────────────────────────────────────────────────────────

st.divider()
st.subheader("4️⃣ Node Results")

# Determine which tabs to show based on what's in status_data
has_ingest = (
    status_data.get("external_tables") is not None
    or status_data.get("source_fields") is not None
    or status_data.get("schema_content") is not None
    or (current_node is not None and current_node >= 1)
)
has_deterministic = (
    status_data.get("tier1_mappings") is not None
    or status_data.get("unmapped_after_t1") is not None
    or (current_node is not None and current_node >= 2)
)
has_semantic = (
    status_data.get("tier2_auto_mapped") is not None
    or status_data.get("tier2_unmappable") is not None
    or (current_node is not None and current_node >= 3)
)
has_gate1 = (
    status_data.get("extra_fields_config") is not None
    or status_data.get("tier1_mappings") is not None
    or (current_node is not None and current_node >= 4)
)
has_hierarchy = (
    status_data.get("confirmed_hierarchies") is not None
    or (current_node is not None and current_node >= 5)
)
has_output = (
    status_data.get("json_blob_url") is not None
    or status_data.get("output_generated") is not None
    or (current_node is not None and current_node >= 7)
)

# Build tabs dynamically
tab_labels = ["📊 Overview"]
if has_ingest:
    tab_labels.append("📥 Node 1: Ingest")
if has_deterministic:
    tab_labels.append("🔗 Node 2: Deterministic")
if has_semantic:
    tab_labels.append("✨ Node 3: Semantic")
if has_gate1:
    tab_labels.append("👤 Node 4: Gate 1 Results")
if has_hierarchy:
    tab_labels.append("🌳 Node 5: Hierarchy")
if has_output:
    tab_labels.append("📤 Node 7-8: Output")
tab_labels.append("🔧 Raw Response")

tabs = st.tabs(tab_labels)
tab_iter = iter(tabs)

# ── TAB: Overview ─────────────────────────────────────────────────────────────
with next(tab_iter):
    st.subheader("Pipeline Overview")

    # Overall confidence
    overall_conf = status_data.get("overall_mapping_confidence", 0.0)
    t1_count = len(status_data.get("tier1_mappings") or [])
    t2_auto = len(status_data.get("tier2_auto_mapped") or [])
    t2_flagged = len(status_data.get("tier2_flagged") or [])
    t2_unmappable = len(status_data.get("tier2_unmappable") or [])

    col1, col2, col3, col4 = st.columns(4)
    col1.metric("Overall Confidence", f"{overall_conf:.1%}" if overall_conf else "—")
    col2.metric("Tier 1 Mapped", t1_count or "—")
    col3.metric("Tier 2 Auto", t2_auto or "—")
    col4.metric("Unmappable", t2_unmappable or "—")

    st.divider()

    # Notes from nodes
    notes = status_data.get("notes", [])
    if notes:
        st.subheader("📝 Pipeline Notes")
        for note in notes:
            st.info(note)

    # Event log
    all_logs = st.session_state.sm_accumulated_logs or status_data.get("execution_logs", [])
    _display_event_logger(all_logs, "Full Pipeline")

# ── TAB: Node 1 Ingest ────────────────────────────────────────────────────────
if has_ingest:
    with next(tab_iter):
        st.subheader("📥 Node 1: Ingest & Configure")

        external_tables = status_data.get("external_tables", {})
        source_fields = status_data.get("source_fields", [])
        schema_content_stored = status_data.get("schema_content", "")

        if external_tables:
            st.subheader("📋 Source Tables")
            for tbl_name, tbl_info in external_tables.items():
                fields = tbl_info.get("fields", []) if isinstance(tbl_info, dict) else []
                with st.expander(f"**{tbl_name}** ({len(fields)} fields)", expanded=False):
                    if fields:
                        st.dataframe(
                            pd.DataFrame(fields) if isinstance(fields[0], dict)
                            else pd.DataFrame({"field": fields}),
                            use_container_width=True,
                            hide_index=True,
                        )
        elif source_fields:
            st.subheader("📋 Source Fields")
            if isinstance(source_fields, list):
                if source_fields and isinstance(source_fields[0], dict):
                    st.dataframe(pd.DataFrame(source_fields), use_container_width=True, hide_index=True)
                else:
                    st.write(", ".join([f"`{f}`" for f in source_fields]))
        else:
            st.info("Ingest node ran successfully. Source schema details not exposed in status.")

        canonical_tables = status_data.get("canonical_tables", {})
        if canonical_tables:
            st.divider()
            st.subheader("🎯 Canonical Tables Loaded")
            st.write(f"{len(canonical_tables)} canonical tables available for mapping.")
            with st.expander("View canonical tables", expanded=False):
                st.write(sorted(canonical_tables.keys()))

# ── TAB: Node 2 Deterministic ─────────────────────────────────────────────────
if has_deterministic:
    with next(tab_iter):
        st.subheader("🔗 Node 2: Deterministic Mapping (Tier 1)")

        tier1 = status_data.get("tier1_mappings", []) or []
        unmapped_t1 = status_data.get("unmapped_after_t1", []) or []

        col1, col2, col3 = st.columns(3)
        col1.metric("✅ Tier 1 Mapped", len(tier1))
        col2.metric("⏳ Unresolved → Node 3", len(unmapped_t1))
        col3.metric("Total Fields", len(tier1) + len(unmapped_t1))

        st.divider()

        if tier1:
            st.subheader(f"✅ Tier 1 Auto-Mapped ({len(tier1)} fields)")
            t1_rows = []
            for m in tier1:
                if isinstance(m, dict):
                    t1_rows.append({
                        "Source Field": m.get("source_field", ""),
                        "Source Table": m.get("source_table", ""),
                        "Target Field": m.get("target_field", ""),
                        "Confidence": f"{m.get('confidence', 0):.2f}",
                        "Tier": m.get("tier", "T1"),
                        "Strategy": m.get("match_strategy", "—"),
                    })
            if t1_rows:
                st.dataframe(pd.DataFrame(t1_rows), use_container_width=True, hide_index=True)

        if unmapped_t1:
            st.divider()
            st.subheader(f"⏳ Unresolved Fields ({len(unmapped_t1)}) → Node 3")
            un_rows = []
            for f in unmapped_t1:
                if isinstance(f, dict):
                    un_rows.append({
                        "Field": f.get("field_name", f.get("source_field", "")),
                        "Table": f.get("source_table", ""),
                        "Type": f.get("data_type", ""),
                    })
                else:
                    un_rows.append({"Field": str(f)})
            st.dataframe(pd.DataFrame(un_rows), use_container_width=True, hide_index=True)

# ── TAB: Node 3 Semantic ──────────────────────────────────────────────────────
if has_semantic:
    with next(tab_iter):
        st.subheader("✨ Node 3: Semantic Mapping (Tier 2)")

        t2_auto = status_data.get("tier2_auto_mapped", []) or []
        t2_flagged = status_data.get("tier2_flagged", []) or []
        t2_unmappable = status_data.get("tier2_unmappable", []) or []
        overall_conf = status_data.get("overall_mapping_confidence", 0.0)

        col1, col2, col3, col4 = st.columns(4)
        col1.metric("🟢 Auto-Accept (≥ 0.85)", len(t2_auto))
        col2.metric("🟡 Flagged (0.65–0.84)", len(t2_flagged))
        col3.metric("🔴 Unmappable (< 0.65)", len(t2_unmappable))
        col4.metric("Avg Confidence", f"{overall_conf:.2f}" if overall_conf else "—")

        st.divider()

        if t2_auto:
            st.subheader(f"🟢 Auto-Accepted ({len(t2_auto)})")
            rows = []
            for m in t2_auto:
                if isinstance(m, dict):
                    rows.append({
                        "Source Field": m.get("source_field", ""),
                        "Source Table": m.get("source_table", ""),
                        "Target Field": m.get("target_field", ""),
                        "Confidence": f"{m.get('confidence', 0):.3f}",
                    })
            if rows:
                st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)

        if t2_flagged:
            st.divider()
            st.subheader(f"🟡 Flagged for Gate 1 Review ({len(t2_flagged)})")
            rows = []
            for m in t2_flagged:
                if isinstance(m, dict):
                    rows.append({
                        "Source Field": m.get("source_field", ""),
                        "Source Table": m.get("source_table", ""),
                        "Suggested Target": m.get("target_field", ""),
                        "Confidence": f"{m.get('confidence', 0):.3f}",
                    })
            if rows:
                st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)

        if t2_unmappable:
            st.divider()
            st.subheader(f"🔴 Unmappable ({len(t2_unmappable)})")
            rows = []
            for f in t2_unmappable:
                if isinstance(f, dict):
                    rows.append({
                        "Field": f.get("field_name", f.get("source_field", "")),
                        "Table": f.get("source_table", ""),
                    })
                else:
                    rows.append({"Field": str(f)})
            st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)

# ── TAB: Node 4 Gate 1 Results ────────────────────────────────────────────────
if has_gate1:
    with next(tab_iter):
        st.subheader("👤 Node 4: Gate 1 Field Mapping — Results")

        # Show what happened after the gate
        tier1_after = status_data.get("tier1_mappings", []) or []
        extra_fields = status_data.get("extra_fields_config", []) or []
        rejected_count = status_data.get("user_rejected_count", 0) or 0

        if current_node is not None and current_node > 4:
            # Gate completed
            col1, col2, col3 = st.columns(3)
            col1.metric("Approved Mappings", len(tier1_after))
            col2.metric("Extra Fields (DDL)", len(extra_fields))
            col3.metric("Rejected", rejected_count)

            st.divider()

            if extra_fields:
                st.subheader("🗄️ Extra Fields Config (for DDL)")
                ef_rows = []
                for ef in extra_fields:
                    if isinstance(ef, dict):
                        ef_rows.append({
                            "Source Field": ef.get("source_field", ""),
                            "Source Table": ef.get("source_table", ""),
                            "Strategy": ef.get("storage_strategy", ""),
                            "Target Table": ef.get("target_table", "—"),
                            "Column": ef.get("custom_column_name", "—"),
                            "Type": ef.get("data_type", "—"),
                        })
                if ef_rows:
                    st.dataframe(pd.DataFrame(ef_rows), use_container_width=True, hide_index=True)
        elif status == "awaiting_review":
            st.info("⏸️ Gate 1 is pending — see the gate review UI above.")
        else:
            st.info("Gate 1 not yet reached.")

# ── TAB: Node 5 Hierarchy ─────────────────────────────────────────────────────
if has_hierarchy:
    with next(tab_iter):
        st.subheader("🌳 Node 5: Hierarchy Detection")

        hierarchies = status_data.get("confirmed_hierarchies", []) or []
        fk_candidates = status_data.get("fk_candidates", []) or []
        cycles = status_data.get("hierarchy_cycles", []) or []
        el_passed = status_data.get("el_m6_passed")

        col1, col2, col3 = st.columns(3)
        col1.metric("FK Relationships", len(hierarchies) or len(fk_candidates))
        col2.metric("Cycles", len(cycles))
        col3.metric(
            "EL-M.6",
            "✅ PASSED" if el_passed else ("❌ FAILED" if el_passed is False else "—"),
        )

        st.divider()

        if hierarchies:
            st.subheader("🔗 Detected Hierarchy Relationships")
            h_rows = []
            for h in hierarchies:
                if isinstance(h, dict):
                    h_rows.append({
                        "Parent Table": h.get("parent_table", ""),
                        "Child Table": h.get("child_table", ""),
                        "FK Column": h.get("fk_column", ""),
                        "Type": h.get("relationship_type", ""),
                        "Confidence": f"{h.get('confidence', 0):.2f}",
                    })
            if h_rows:
                st.dataframe(pd.DataFrame(h_rows), use_container_width=True, hide_index=True)

        if cycles:
            st.error(f"⚠️ {len(cycles)} cycle(s) in CONTAINMENT — must be resolved at Gate 2")
            for cyc in cycles:
                st.warning(str(cyc))

# ── TAB: Node 7-8 Output ──────────────────────────────────────────────────────
if has_output:
    with next(tab_iter):
        st.subheader("📤 Nodes 7–8: Output Generation & Write")

        json_url = status_data.get("json_blob_url")
        csv_url = status_data.get("csv_blob_url")
        sql_url = status_data.get("sql_blob_url")
        handoff = status_data.get("handoff_complete", False)
        final_config = status_data.get("final_mapper_config", {})

        col1, col2, col3 = st.columns(3)
        col1.metric("JSON Output", "✅" if json_url else "—")
        col2.metric("CSV Output", "✅" if csv_url else "—")
        col3.metric("Handoff Complete", "✅" if handoff else "Pending")

        if json_url:
            st.markdown(f"📎 [Download JSON output]({json_url})")
        if csv_url:
            st.markdown(f"📎 [Download CSV output]({csv_url})")
        if sql_url:
            st.markdown(f"📎 [Download SQL output]({sql_url})")

        if final_config:
            st.divider()
            st.subheader("🗺️ Final Mapper Config")
            with st.expander("View full config", expanded=False):
                st.json(final_config)

# ── TAB: Raw Response ─────────────────────────────────────────────────────────
with next(tab_iter):
    st.subheader("🔧 Raw Status Response")
    st.json(status_data)
