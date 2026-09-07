"""Migration Pipeline UI — Production API

Connects to svc-ai-schema-mapper (port 8003) using the production
/api/migration endpoints.  All state is DB-backed — no fragile session-state
merging.  The UI polls a single /status endpoint and renders the correct
widget (gate or results) based on what the DB says.

Usage:
    streamlit run streamlit_app.py

Requires:
    pip install streamlit httpx pandas
"""

import json
import time
from datetime import datetime
from typing import Any, Optional

import httpx
import pandas as pd
import streamlit as st

# ── Page config ────────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="CAFM Migration Pipeline",
    page_icon="🏗️",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ── Session state bootstrap ────────────────────────────────────────────────────
_SS_KEYS = {
    "migration_id": None,
    "status_data": None,
    "gate_pre_semantic_decisions": {},  # {table: {source_field: "approve"|"semantic"}}
    "gate1_flagged_decisions": {},   # {table: {source_field: {"action": ..., "target_field": ..., "rationale": ...}}}
    "gate1_unmapped_decisions": {},  # {table: {source_field: {"action": ..., ...}}}
    "gate2_decisions": {},           # {item_id: {"action": ..., ...}}
    "fiix_mapper_cache": None,
    "fiix_is_active": False,
    "last_poll_time": 0.0,
    "gate_submitted": False,
    # Tracks which gate type was just submitted — cleared when status advances past that gate.
    # Prevents re-rendering the gate form while the pipeline is still transitioning.
    "submitted_gate_type": None,
}
for _k, _v in _SS_KEYS.items():
    if _k not in st.session_state:
        st.session_state[_k] = _v

# ── Helpers ─────────────────────────────────────────────────────────────────────

def _api(path: str, base: str) -> str:
    return base.rstrip("/") + "/" + path.lstrip("/")


def _get(url: str, timeout: int = 15) -> Optional[dict]:
    try:
        r = httpx.get(url, timeout=timeout)
        r.raise_for_status()
        return r.json()
    except httpx.HTTPStatusError as e:
        st.error(f"API error {e.response.status_code}: {e.response.text[:300]}")
        return None
    except Exception as e:
        st.error(f"Request failed: {e}")
        return None


def _post(url: str, payload: dict, timeout: int = 30) -> Optional[dict]:
    try:
        r = httpx.post(url, json=payload, timeout=timeout)
        r.raise_for_status()
        return r.json()
    except httpx.HTTPStatusError as e:
        st.error(f"API error {e.response.status_code}: {e.response.text[:300]}")
        return None
    except Exception as e:
        st.error(f"Request failed: {e}")
        return None


def _post_form(url: str, data: dict, files: dict, timeout: int = 120) -> Optional[dict]:
    try:
        r = httpx.post(url, data=data, files=files, timeout=timeout)
        r.raise_for_status()
        return r.json()
    except httpx.HTTPStatusError as e:
        st.error(f"Upload error {e.response.status_code}: {e.response.text[:300]}")
        return None
    except Exception as e:
        st.error(f"Upload failed: {e}")
        return None


def _status_colour(status: str) -> str:
    return {
        "running": "🟡",
        "step_paused": "🔵",
        "awaiting_review": "🟠",
        "complete": "🟢",
        "failed": "🔴",
        "ddl_failed": "🔴",
        "cancelled": "⚫",
    }.get(status, "⚪")


def _node_badge(n: int, current_step: int, status: str) -> str:
    """Return coloured node badge text."""
    if status == "complete" and current_step >= n:
        return f"✅ {n}"
    if current_step > n:
        return f"✅ {n}"
    if current_step == n:
        return f"⏳ {n}"
    return f"⬜ {n}"


# ── Sidebar ────────────────────────────────────────────────────────────────────

def render_sidebar() -> tuple[str, str, str]:
    """Render sidebar, return (api_url, org_id, cmms_name)."""
    with st.sidebar:
        st.title("⚙️ Configuration")

        api_url = st.text_input(
            "API URL",
            value="http://localhost:8003",
            key="cfg_api_url",
        )
        org_id = st.text_input(
            "Organization ID (UUID)",
            value="00000000-0000-0000-0000-000000000001",
            key="cfg_org_id",
        )
        cmms_name = st.text_input(
            "Source CMMS",
            value="Custom",
            key="cfg_cmms_name",
            help="e.g. Maximo, Fiix, SAP, Archibus",
        )

        if st.button("Health Check", use_container_width=True):
            resp = _get(_api("/health", api_url))
            if resp:
                st.success(f"Healthy — {resp.get('status', 'ok')}")

        st.divider()
        st.subheader("Fiix Connector")

        col1, col2 = st.columns(2)
        with col1:
            if st.button("Test Connection", use_container_width=True):
                resp = _get(_api("/api/fiix/status", api_url))
                if resp:
                    if resp.get("connected"):
                        st.success("Connected")
                    else:
                        st.warning("Not connected")
        with col2:
            if st.button("Fetch Schema", use_container_width=True):
                with st.spinner("Fetching Fiix schema..."):
                    resp = _get(_api(f"/api/fiix/schema?org_id={org_id}", api_url))
                if resp:
                    st.session_state.fiix_mapper_cache = resp
                    st.session_state.fiix_is_active = True
                    st.success(f"Loaded {len(resp.get('tables', {}))} tables")

        if st.session_state.fiix_is_active:
            st.caption("Fiix mapper: active")
            if st.button("Clear Fiix", use_container_width=True):
                st.session_state.fiix_mapper_cache = None
                st.session_state.fiix_is_active = False
                st.rerun()

        st.divider()
        st.subheader("Navigation")

        if st.session_state.migration_id:
            st.caption(f"Active migration:\n`{st.session_state.migration_id[:8]}...`")
            if st.button("New Migration", use_container_width=True):
                for k in ["migration_id", "status_data", "gate_pre_semantic_decisions",
                          "gate1_flagged_decisions", "gate1_unmapped_decisions",
                          "gate2_decisions", "gate_submitted", "submitted_gate_type"]:
                    st.session_state[k] = _SS_KEYS[k]
                st.rerun()

        resume_id = st.text_input("Resume migration ID", placeholder="Paste migration UUID")
        if st.button("Resume", use_container_width=True) and resume_id.strip():
            st.session_state.migration_id = resume_id.strip()
            st.session_state.status_data = None
            st.rerun()

    return api_url, org_id, cmms_name


# ── Start form ─────────────────────────────────────────────────────────────────

def render_start_form(api_url: str, org_id: str, cmms_name: str):
    st.header("Start Migration")
    st.markdown(
        "Upload a CSV or Excel file from your CMMS. The 9-node AI pipeline will "
        "detect encoding, map fields, validate hierarchies, and produce an "
        "IntermediateSchema for ingestion into `plenum_cafm`."
    )

    uploaded = st.file_uploader(
        "Source file (CSV / Excel)",
        type=["csv", "tsv", "xlsx", "xls", "xlsm"],
        help="Up to 500 MB",
    )

    col_a, col_b = st.columns(2)
    with col_a:
        run_cmms = st.text_input("CMMS name", value=cmms_name, key="start_cmms")
    with col_b:
        run_org = st.text_input("Organization ID", value=org_id, key="start_org")

    if uploaded and st.button("🚀 Start Migration", type="primary", use_container_width=True):
        with st.spinner("Uploading and starting migration..."):
            resp = _post_form(
                _api("/api/migration/start-with-upload", api_url),
                data={"cmms_name": run_cmms, "organization_id": run_org},
                files={"file": (uploaded.name, uploaded.getvalue(), "application/octet-stream")},
                timeout=120,
            )
        if resp:
            st.session_state.migration_id = str(resp["migration_id"])
            st.session_state.status_data = None
            st.session_state.gate_pre_semantic_decisions = {}
            st.session_state.gate1_flagged_decisions = {}
            st.session_state.gate1_unmapped_decisions = {}
            st.session_state.gate2_decisions = {}
            st.session_state.gate_submitted = False
            st.session_state.submitted_gate_type = None
            st.session_state.last_poll_time = 0.0
            st.success(f"Migration started: `{resp['migration_id']}`")
            time.sleep(0.5)
            st.rerun()


# ── Progress strip ─────────────────────────────────────────────────────────────

NODE_LABELS = {
    1: "Ingest",
    2: "Det.Map",
    3: "Sem.Map",
    4: "Gate 1",
    5: "Preprocess",
    6: "Hierarchy",
    7: "Gate 2",
    8: "Output",
    9: "Gate 3",
}


def render_progress_strip(status_data: dict):
    current_step = status_data.get("current_step", 0)
    status = status_data.get("status", "running")

    cols = st.columns(9)
    for i, (n, label) in enumerate(NODE_LABELS.items()):
        with cols[i]:
            badge = _node_badge(n, current_step, status)
            is_gate = n in (4, 7, 9)
            gate_icon = " 🔒" if is_gate else ""
            st.markdown(
                f"<div style='text-align:center;font-size:0.8em'>"
                f"<b>{badge}</b><br/>{label}{gate_icon}</div>",
                unsafe_allow_html=True,
            )


# ── Status banner ──────────────────────────────────────────────────────────────

def render_status_banner(status_data: dict):
    status = status_data.get("status", "running")
    pct = status_data.get("progress_pct", 0)
    step = status_data.get("current_step", 0)
    gate = status_data.get("pending_gate_type")
    t1 = status_data.get("t1_mapped_count", 0)
    t2_auto = status_data.get("t2_auto_count", 0)
    t2_human = status_data.get("t2_human_count", 0)
    unmapped = status_data.get("unmapped_count", 0)
    total = status_data.get("total_fields", 0)
    err = status_data.get("error_message")

    icon = _status_colour(status)

    if status == "complete":
        st.success(f"{icon} **Migration complete** ({pct:.0f}%)")
    elif status == "step_paused":
        payload = status_data.get("pending_gate_payload") or {}
        node_label = payload.get("label", gate or "step")
        st.info(f"{icon} **Node {payload.get('node', '?')} complete — {node_label}** · Click 'Next Node' to continue")
    elif status == "awaiting_review":
        gate_name = {
            "pre_semantic": "Pre-Semantic Gate — T1 Mapping Review",
            "field_mapping": "Gate 1 — Field Mapping Review",
            "hierarchy": "Gate 2 — Hierarchy Verification",
            "write": "Gate 3 — Final Confirmation",
            "final_confirmation": "Gate 3 — Final Confirmation",
        }.get(gate or "", f"Gate — {gate}")
        st.warning(f"{icon} **Awaiting review: {gate_name}**")
    elif status in ("failed", "ddl_failed"):
        st.error(f"{icon} **{status.upper()}**: {err or 'Unknown error'}")
    else:
        st.info(f"{icon} **Running** — Node {step} ({pct:.0f}%)")

    # Mapping stats row
    if total > 0:
        c1, c2, c3, c4, c5 = st.columns(5)
        c1.metric("Total fields", total)
        c2.metric("T1 mapped", t1, help="Deterministic (tier 1)")
        c3.metric("T2 auto", t2_auto, help="Semantic auto-accepted (≥0.85)")
        c4.metric("T2 human", t2_human, help="Semantic — human reviewed")
        c5.metric("Unmapped", unmapped)

    st.progress(min(pct / 100, 1.0))


# ── Pre-Semantic Gate: T1 mapping review ──────────────────────────────────────

def render_gate_pre_semantic(payload: dict, migration_id: str, api_url: str):
    """Review T1_exact / T1_variation / T1_regex / T1_llm mappings before Node 3."""
    st.subheader("Pre-Semantic Gate — T1 Mapping Review")
    st.markdown(
        "The deterministic mapper (Node 2) produced the following Tier 1 field mappings. "
        "**Approve** to keep a mapping as-is, or **semantic** to send it back to the "
        "semantic mapper (Node 3) for a second opinion. T1_alias mappings auto-passed "
        "and are not shown here."
    )

    total_reviewable = payload.get("total_reviewable", 0)
    review_items_by_table: dict = payload.get("review_items_by_table", {})

    if total_reviewable == 0 or not review_items_by_table:
        st.info("No T1 mappings require review. Click Submit to proceed.")
        if st.button("Submit Pre-Semantic Gate", type="primary", key="psg_empty_submit"):
            _submit_gate_pre_semantic(migration_id, api_url, {})
        return

    st.metric("Fields to review", total_reviewable)

    dec = st.session_state.gate_pre_semantic_decisions

    tier_colours = {
        "T1_exact": "🟢",
        "T1_variation": "🔵",
        "T1_regex": "🟡",
        "T1_llm": "🟠",
    }

    for table_name, items in review_items_by_table.items():
        dec.setdefault(table_name, {})
        with st.expander(f"Table: **{table_name}** ({len(items)} fields)", expanded=True):
            for item in items:
                sf = item.get("source_field", "")
                tf = item.get("target_field", "")
                conf = item.get("confidence", 0.0)
                tier = item.get("tier", "")
                rationale = item.get("rationale", "")
                samples = item.get("sample_values", [])

                # Default to approve
                dec[table_name].setdefault(sf, "approve")

                tier_icon = tier_colours.get(tier, "⚪")
                st.markdown(
                    f"{tier_icon} **`{sf}`** → `{tf}` &nbsp;&nbsp; "
                    f"conf: `{conf:.2f}` &nbsp; tier: `{tier}`",
                    unsafe_allow_html=True,
                )
                if rationale:
                    st.caption(f"Rationale: {rationale}")
                if samples:
                    st.caption(f"Samples: {', '.join(str(s) for s in samples[:5])}")

                cur = dec[table_name][sf]
                cur_idx = 0 if cur == "approve" else 1
                chosen = st.radio(
                    f"Decision for `{sf}`",
                    ["approve", "semantic"],
                    index=cur_idx,
                    key=f"psg_{table_name}_{sf}",
                    horizontal=True,
                    captions=["Keep T1 mapping", "Re-evaluate in Node 3"],
                    label_visibility="collapsed",
                )
                dec[table_name][sf] = chosen
                st.divider()

    # Summary counts
    total_approve = sum(
        1 for tbl in dec.values() for v in tbl.values() if v == "approve"
    )
    total_semantic = sum(
        1 for tbl in dec.values() for v in tbl.values() if v == "semantic"
    )
    st.info(
        f"Decisions: **{total_approve} approved** (keep T1) · "
        f"**{total_semantic} sent to semantic** (re-evaluate in Node 3)"
    )

    st.divider()
    if st.button(
        "Submit Pre-Semantic Gate Decisions",
        type="primary",
        use_container_width=True,
        key="psg_submit",
    ):
        _submit_gate_pre_semantic(migration_id, api_url, dec)


def _submit_gate_pre_semantic(migration_id: str, api_url: str, dec: dict):
    """Build payload and POST to /gate/pre-semantic."""
    # Convert {table: {sf: "approve"|"semantic"}} → {table: [{source_field, decision}]}
    decisions_payload: dict[str, list] = {}
    for table_name, fields in dec.items():
        for sf, decision in fields.items():
            decisions_payload.setdefault(table_name, []).append({
                "source_field": sf,
                "decision": decision,
            })

    payload = {"decisions": decisions_payload}
    with st.spinner("Submitting Pre-Semantic Gate decisions..."):
        resp = _post(
            _api(f"/api/migration/{migration_id}/gate/pre-semantic", api_url),
            payload,
        )
    if resp:
        st.session_state.gate_pre_semantic_decisions = {}
        st.session_state.gate_submitted = True
        st.session_state.submitted_gate_type = "pre_semantic"
        st.session_state.status_data = None
        st.session_state.last_poll_time = 0.0
        st.success("Pre-Semantic Gate decisions submitted. Pipeline resuming...")
        time.sleep(0.5)
        st.rerun()


# ── Gate 1: Field mapping ──────────────────────────────────────────────────────

def render_gate1(payload: dict, migration_id: str, api_url: str):
    st.subheader("Gate 1 — Field Mapping Review")

    total_flagged = payload.get("total_flagged", 0)
    total_unmappable = payload.get("total_unmappable", 0)
    confidence_alert = payload.get("confidence_alert")
    review_items_by_table: dict = payload.get("review_items_by_table", {})
    unmappable_by_table: dict = payload.get("unmappable_items_by_table", {})
    canonical_tables: list = payload.get("existing_canonical_tables", [])

    if confidence_alert:
        st.warning(confidence_alert.get("message", "Low confidence — review all mappings"))

    col_a, col_b = st.columns(2)
    col_a.metric("Fields needing review", total_flagged)
    col_b.metric("Unmapped fields", total_unmappable)

    if total_flagged == 0 and total_unmappable == 0:
        st.info("No fields require review. Click Submit to proceed.")
        if st.button("Submit Gate 1", type="primary", key="g1_empty_submit"):
            _submit_gate1(migration_id, api_url, {}, {})
        return

    # ── Flagged fields (per table) ──────────────────────────────────────────
    if review_items_by_table:
        st.markdown("#### Flagged Mappings (accept / reject / override)")
        st.caption("Fields with confidence 0.65–0.84 that need your approval.")

        flagged_dec = st.session_state.gate1_flagged_decisions
        for table_name, items in review_items_by_table.items():
            flagged_dec.setdefault(table_name, {})
            with st.expander(f"Table: **{table_name}** ({len(items)} fields)", expanded=True):
                for item in items:
                    sf = item["source_field"]
                    suggested = item.get("suggested_target", "")
                    conf = item.get("confidence", 0)
                    rationale = item.get("rationale", "")
                    samples = item.get("sample_values", [])
                    suggestions = item.get("suggestions", [])

                    flagged_dec[table_name].setdefault(sf, {
                        "action": "accept",
                        "target_field": suggested,
                        "rationale": "",
                    })

                    st.markdown(f"**`{sf}`** → `{suggested}` (confidence: {conf:.2f})")
                    if rationale:
                        st.caption(f"Rationale: {rationale}")
                    if samples:
                        st.caption(f"Samples: {', '.join(str(s) for s in samples[:5])}")

                    action_opts = ["accept", "reject", "override"]
                    current_action = flagged_dec[table_name][sf].get("action", "accept")
                    action_idx = action_opts.index(current_action) if current_action in action_opts else 0

                    col1, col2 = st.columns([1, 3])
                    with col1:
                        chosen = st.radio(
                            "Action",
                            action_opts,
                            index=action_idx,
                            key=f"g1_act_{table_name}_{sf}",
                            horizontal=True,
                            label_visibility="collapsed",
                        )
                        flagged_dec[table_name][sf]["action"] = chosen

                    if chosen == "override":
                        override_opts = [suggested] + [s for s in suggestions if s != suggested]
                        with col2:
                            tgt = st.selectbox(
                                "Target field",
                                options=override_opts + ["(other — type below)"],
                                key=f"g1_tgt_{table_name}_{sf}",
                                label_visibility="collapsed",
                            )
                            if tgt == "(other — type below)":
                                tgt = st.text_input(
                                    "Custom target",
                                    value=suggested,
                                    key=f"g1_custom_{table_name}_{sf}",
                                )
                        flagged_dec[table_name][sf]["target_field"] = tgt
                        note = st.text_input(
                            "Rationale (optional)",
                            key=f"g1_note_{table_name}_{sf}",
                        )
                        flagged_dec[table_name][sf]["rationale"] = note

                    st.divider()

        # Count summary
        total_acc = sum(
            1 for tbl in flagged_dec.values()
            for d in tbl.values() if d.get("action") == "accept"
        )
        total_rej = sum(
            1 for tbl in flagged_dec.values()
            for d in tbl.values() if d.get("action") == "reject"
        )
        total_ovr = sum(
            1 for tbl in flagged_dec.values()
            for d in tbl.values() if d.get("action") == "override"
        )
        st.info(f"Flagged decisions: {total_acc} accepted · {total_rej} rejected · {total_ovr} overridden")

    # ── Unmapped fields (per table) ──────────────────────────────────────────
    if unmappable_by_table:
        st.markdown("#### Unmapped Fields (custom DDL / raw_metadata / skip)")
        st.caption("Fields with confidence < 0.65 that couldn't be mapped automatically.")

        unmap_dec = st.session_state.gate1_unmapped_decisions
        for table_name, items in unmappable_by_table.items():
            unmap_dec.setdefault(table_name, {})
            with st.expander(f"Table: **{table_name}** ({len(items)} unmapped)", expanded=True):
                for item in items:
                    sf = item["source_field"]
                    unmap_dec[table_name].setdefault(sf, {"action": "raw_metadata"})

                    st.markdown(f"**`{sf}`**")
                    act_opts = ["raw_metadata", "skip", "custom"]
                    cur_act = unmap_dec[table_name][sf].get("action", "raw_metadata")
                    cur_idx = act_opts.index(cur_act) if cur_act in act_opts else 0

                    chosen = st.radio(
                        f"Storage for `{sf}`",
                        act_opts,
                        index=cur_idx,
                        key=f"g1_unmap_act_{table_name}_{sf}",
                        horizontal=True,
                        captions=["JSONB column", "Discard", "New DB column"],
                    )
                    unmap_dec[table_name][sf]["action"] = chosen

                    if chosen == "custom":
                        c1, c2, c3 = st.columns(3)
                        with c1:
                            tgt_tbl = st.selectbox(
                                "Target table",
                                options=canonical_tables or ["assets", "work_orders", "parts"],
                                key=f"g1_tbl_{table_name}_{sf}",
                            )
                        with c2:
                            col_name = st.text_input(
                                "Column name",
                                value=sf.lower().replace(" ", "_"),
                                key=f"g1_col_{table_name}_{sf}",
                            )
                        with c3:
                            dtype = st.selectbox(
                                "Data type",
                                ["TEXT", "VARCHAR(100)", "VARCHAR(255)", "INTEGER",
                                 "BIGINT", "NUMERIC(10,2)", "BOOLEAN", "DATE",
                                 "TIMESTAMPTZ", "JSONB"],
                                key=f"g1_dtype_{table_name}_{sf}",
                            )
                        nullable = st.checkbox("Nullable", value=True, key=f"g1_null_{table_name}_{sf}")

                        unmap_dec[table_name][sf].update({
                            "target_table": tgt_tbl,
                            "custom_column_name": col_name,
                            "data_type": dtype,
                            "nullable": nullable,
                        })

                    st.divider()

        total_raw = sum(
            1 for tbl in unmap_dec.values()
            for d in tbl.values() if d.get("action") == "raw_metadata"
        )
        total_skip = sum(
            1 for tbl in unmap_dec.values()
            for d in tbl.values() if d.get("action") == "skip"
        )
        total_custom = sum(
            1 for tbl in unmap_dec.values()
            for d in tbl.values() if d.get("action") == "custom"
        )
        st.info(f"Unmapped decisions: {total_raw} raw_metadata · {total_skip} skipped · {total_custom} custom DDL")

    st.divider()
    if st.button("Submit Gate 1 Decisions", type="primary", use_container_width=True, key="g1_submit"):
        _submit_gate1(
            migration_id,
            api_url,
            st.session_state.gate1_flagged_decisions,
            st.session_state.gate1_unmapped_decisions,
        )


def _submit_gate1(migration_id: str, api_url: str, flagged: dict, unmapped: dict):
    # Build decisions payload
    decisions_flagged: dict[str, list] = {}
    for table_name, fields in flagged.items():
        for sf, d in fields.items():
            decisions_flagged.setdefault(table_name, []).append({
                "action": d.get("action", "accept"),
                "source_field": sf,
                "target_field": d.get("target_field", sf),
                "rationale": d.get("rationale", ""),
            })

    decisions_unmapped: dict[str, list] = {}
    for table_name, fields in unmapped.items():
        for sf, d in fields.items():
            entry: dict[str, Any] = {"action": d.get("action", "raw_metadata"), "source_field": sf}
            if d.get("action") == "custom":
                entry.update({
                    "target_table": d.get("target_table", ""),
                    "custom_column_name": d.get("custom_column_name", sf),
                    "data_type": d.get("data_type", "TEXT"),
                    "nullable": d.get("nullable", True),
                })
            decisions_unmapped.setdefault(table_name, []).append(entry)

    payload = {"decisions": {"flagged": decisions_flagged, "unmapped": decisions_unmapped}}

    with st.spinner("Submitting Gate 1 decisions..."):
        resp = _post(_api(f"/api/migration/{migration_id}/gate/field-mapping", api_url), payload)

    if resp:
        st.session_state.gate1_flagged_decisions = {}
        st.session_state.gate1_unmapped_decisions = {}
        st.session_state.gate_submitted = True
        st.session_state.submitted_gate_type = "field_mapping"
        st.session_state.status_data = None
        st.session_state.last_poll_time = 0.0
        st.success("Gate 1 decisions submitted. Pipeline resuming...")
        time.sleep(0.5)
        st.rerun()


# ── Gate 2: Hierarchy ──────────────────────────────────────────────────────────

def render_gate2(payload: dict, migration_id: str, api_url: str):
    st.subheader("Gate 2 — Hierarchy Verification")

    review_items: list = payload.get("review_items", [])
    tree_visual: str = payload.get("hierarchy_tree", "")
    total_h = payload.get("total_hierarchies", 0)
    total_c = payload.get("total_cycles", 0)
    total_o = payload.get("total_orphans", 0)

    col1, col2, col3 = st.columns(3)
    col1.metric("FK relationships", total_h)
    col2.metric("Cycles detected", total_c, delta=f"{'⚠️ must resolve' if total_c else ''}")
    col3.metric("Orphaned records", total_o)

    if tree_visual:
        with st.expander("Hierarchy tree", expanded=False):
            st.code(tree_visual)

    g2_dec = st.session_state.gate2_decisions

    # ── Cycles (must resolve first) ────────────────────────────────────────
    cycle_items = [i for i in review_items if i.get("type") == "cycle_alert"]
    if cycle_items:
        st.markdown("#### Cycles (must be resolved)")
        st.caption("Select which relationships to remove to break each cycle.")
        for item in cycle_items:
            item_id = item["id"]
            g2_dec.setdefault(item_id, {"type": "cycle_resolution", "id": item_id, "action": "remove_last"})
            st.error(item.get("message", f"Cycle: {item_id}"))
            st.caption(item.get("instruction", ""))
            cycle_path = item.get("cycle", [])
            if cycle_path:
                opts = [f"{cycle_path[i]} → {cycle_path[(i+1) % len(cycle_path)]}" for i in range(len(cycle_path))]
                chosen_rel = st.multiselect(
                    "Relationships to remove",
                    options=opts,
                    default=opts[-1:] if opts else [],
                    key=f"g2_cycle_{item_id}",
                )
                g2_dec[item_id]["relationships_to_remove"] = chosen_rel
            st.divider()

    # ── FK relationships ────────────────────────────────────────────────────
    hier_items = [i for i in review_items if i.get("type") == "hierarchy"]
    if hier_items:
        st.markdown("#### FK Relationships")
        st.caption("Confirm, reject, or modify each detected foreign-key relationship.")
        for item in hier_items:
            item_id = item["id"]
            src_tbl = item.get("source_table", "")
            src_col = item.get("source_column", "")
            tgt_tbl = item.get("target_table", "")
            tgt_col = item.get("target_column", "")
            rel_type = item.get("relationship_type", "")
            conf = item.get("confidence", 0)
            reasoning = item.get("reasoning", "")
            match_rate = item.get("data_match_rate", 0)

            g2_dec.setdefault(item_id, {
                "type": "hierarchy_confirmation",
                "id": item_id,
                "source_table": src_tbl,
                "target_table": tgt_tbl,
                "action": "confirm",
            })

            with st.expander(
                f"{src_tbl}.{src_col} → {tgt_tbl}.{tgt_col} "
                f"[{rel_type}] (conf: {conf:.2f})",
                expanded=True,
            ):
                if reasoning:
                    st.caption(f"Reasoning: {reasoning}")
                if match_rate:
                    st.caption(f"Data match rate: {match_rate:.1%}")

                cur_action = g2_dec[item_id].get("action", "confirm")
                act_opts = ["confirm", "reject", "modify"]
                act_idx = act_opts.index(cur_action) if cur_action in act_opts else 0
                chosen = st.radio(
                    "Action",
                    act_opts,
                    index=act_idx,
                    key=f"g2_act_{item_id}",
                    horizontal=True,
                )
                g2_dec[item_id].update({
                    "type": "hierarchy_confirmation",
                    "id": item_id,
                    "source_table": src_tbl,
                    "target_table": tgt_tbl,
                    "action": chosen,
                })

                if chosen == "modify":
                    new_src_col = st.text_input(
                        "Source column", value=src_col, key=f"g2_sc_{item_id}"
                    )
                    new_tgt_col = st.text_input(
                        "Target column", value=tgt_col, key=f"g2_tc_{item_id}"
                    )
                    g2_dec[item_id].update({
                        "source_column": new_src_col,
                        "target_column": new_tgt_col,
                    })

    # ── Orphaned records ────────────────────────────────────────────────────
    orphan_items = [i for i in review_items if i.get("type") == "orphaned_records"]
    if orphan_items:
        st.markdown("#### Orphaned Records")
        for item in orphan_items:
            st.warning(item.get("message", f"Orphans in {item.get('table', '')}"))
            sample = item.get("sample_rows", [])
            if sample:
                with st.expander("Sample rows"):
                    st.dataframe(pd.DataFrame(sample), use_container_width=True)

    # ── Implicit hierarchies ─────────────────────────────────────────────────
    implicit_items = [i for i in review_items if i.get("type") == "implicit_hierarchy"]
    if implicit_items:
        st.markdown("#### Implicit Hierarchies (code-based)")
        for item in implicit_items:
            st.info(item.get("message", ""))

    # ── Counts ───────────────────────────────────────────────────────────────
    confirmed = sum(1 for d in g2_dec.values() if d.get("action") == "confirm")
    rejected = sum(1 for d in g2_dec.values() if d.get("action") == "reject")
    modified = sum(1 for d in g2_dec.values() if d.get("action") == "modify")
    st.info(f"Hierarchy decisions: {confirmed} confirmed · {rejected} rejected · {modified} modified")

    st.divider()
    if st.button("Submit Gate 2 Decisions", type="primary", use_container_width=True, key="g2_submit"):
        _submit_gate2(migration_id, api_url, list(g2_dec.values()))


def _submit_gate2(migration_id: str, api_url: str, decisions: list):
    payload = {"decisions": decisions}
    with st.spinner("Submitting Gate 2 decisions..."):
        resp = _post(_api(f"/api/migration/{migration_id}/gate/hierarchy", api_url), payload)
    if resp:
        st.session_state.gate2_decisions = {}
        st.session_state.gate_submitted = True
        st.session_state.submitted_gate_type = "hierarchy"
        st.session_state.status_data = None
        st.session_state.last_poll_time = 0.0
        st.success("Gate 2 decisions submitted. Pipeline resuming...")
        time.sleep(0.5)
        st.rerun()


# ── Gate 3: Final confirmation ─────────────────────────────────────────────────

def render_gate3(payload: dict, migration_id: str, api_url: str):
    st.subheader("Gate 3 — Final Confirmation")
    st.markdown(
        "Review the migration summary below. Click **CONFIRM** to hand off to "
        "`svc-ingestion`, or **REJECT** to return for corrections."
    )

    summary = payload.get("summary", {})
    entity_counts: dict = summary.get("entity_counts", {})
    total_entities = summary.get("total_entities", 0)
    confidence = summary.get("overall_confidence", 0)
    src_file = summary.get("source_filename", "unknown")

    st.markdown(f"**Source file:** `{src_file}`")
    st.markdown(f"**Overall confidence:** {confidence:.1%}")

    if entity_counts:
        st.markdown("**Entities to be ingested:**")
        cols = st.columns(min(len(entity_counts), 4))
        for i, (etype, count) in enumerate(sorted(entity_counts.items())):
            cols[i % len(cols)].metric(etype, f"{count:,}")
        st.metric("Total entities", f"{total_entities:,}")

    st.divider()
    col1, col2 = st.columns(2)
    with col1:
        if st.button("CONFIRM — Send to svc-ingestion", type="primary", use_container_width=True):
            _submit_gate3(migration_id, api_url, "confirm")
    with col2:
        if st.button("REJECT — Return for corrections", type="secondary", use_container_width=True):
            _submit_gate3(migration_id, api_url, "reject")


def _submit_gate3(migration_id: str, api_url: str, action: str):
    payload = {"decisions": {"action": action}}
    with st.spinner(f"Submitting Gate 3 ({action})..."):
        resp = _post(_api(f"/api/migration/{migration_id}/gate/final", api_url), payload)
    if resp:
        st.session_state.gate_submitted = True
        st.session_state.submitted_gate_type = "write"
        st.session_state.status_data = None
        st.session_state.last_poll_time = 0.0
        if action == "confirm":
            st.success("Migration confirmed! Handing off to svc-ingestion...")
        else:
            st.warning("Migration rejected. You can resume later.")
        time.sleep(0.5)
        st.rerun()


def _submit_advance(migration_id: str, api_url: str, step_key: str):
    """POST to /advance to resume pipeline past an interrupt_after step pause."""
    with st.spinner("Advancing to next node..."):
        resp = _post(_api(f"/api/migration/{migration_id}/advance", api_url), {})
    if resp:
        st.session_state.submitted_gate_type = step_key
        st.session_state.status_data = None
        st.session_state.last_poll_time = 0.0
        time.sleep(0.5)
        st.rerun()
    else:
        st.error("Failed to advance pipeline. Check API logs.")


def render_step_card(step_key: str, summary: dict, migration_id: str, api_url: str):
    """Render the step-pause review card with node output and 'Next Node' button."""
    node_num = summary.get("node", "?")
    label = summary.get("label", step_key)

    st.subheader(f"Node {node_num} Complete — {label}")
    st.caption("Review the output below, then click **Next Node** to continue the pipeline.")

    # Node-specific summary display
    if step_key == "step_1_ingest":
        c1, c2, c3 = st.columns(3)
        c1.metric("Rows ingested", summary.get("rows", 0))
        c2.metric("Columns detected", summary.get("columns", 0))
        c3.metric("Format", summary.get("format", "—"))
        tables = summary.get("tables", [])
        if tables:
            st.write(f"**Tables:** {', '.join(str(t) for t in tables)}")

    elif step_key == "step_2_deterministic_mapping":
        c1, c2 = st.columns(2)
        c1.metric("T1 mapped fields", summary.get("t1_mapped", 0))
        c2.metric("Unresolved (→ T2)", summary.get("unresolved", 0))

    elif step_key == "step_3_semantic_mapping":
        c1, c2, c3 = st.columns(3)
        c1.metric("T2 auto-accepted", summary.get("t2_auto", 0))
        c2.metric("Flagged for review", summary.get("flagged", 0))
        c3.metric("Unmappable", summary.get("unmappable", 0))
        if summary.get("flagged", 0) > 0:
            st.info("Flagged fields will go to Gate 1 (Human Review) after this step.")

    elif step_key == "step_5_preprocess":
        c1, c2 = st.columns(2)
        c1.metric("Rows after dedup", summary.get("rows_cleaned", 0))
        c2.metric("Data quality warnings", summary.get("warnings", 0))
        tables = summary.get("tables", [])
        if tables:
            st.write(f"**Tables:** {', '.join(str(t) for t in tables)}")

    elif step_key == "step_6_hierarchy":
        c1, c2, c3 = st.columns(3)
        c1.metric("FK relationships", summary.get("hierarchies", 0))
        c2.metric("Cycles detected", summary.get("cycles", 0))
        c3.metric("Orphaned records", summary.get("orphans", 0))

    elif step_key == "step_8_output_generation":
        c1, c2 = st.columns(2)
        c1.metric("Tables exported", summary.get("tables", 0))
        c2.metric("Artifacts uploaded", summary.get("artifacts_uploaded", 0))
        formats = summary.get("formats", [])
        if formats:
            st.write(f"**Output formats:** {', '.join(formats)}")
        json_url = summary.get("json_url")
        csv_url = summary.get("csv_url")
        sql_url = summary.get("sql_url")
        report_url = summary.get("report_url")
        if any([json_url, csv_url, sql_url, report_url]):
            st.markdown("**Download links:**")
            if json_url:
                st.markdown(f"- [JSON]({json_url})")
            if csv_url:
                st.markdown(f"- [CSV]({csv_url})")
            if sql_url:
                st.markdown(f"- [SQL]({sql_url})")
            if report_url:
                st.markdown(f"- [PDF Report]({report_url})")
    else:
        # Generic fallback — show raw summary
        st.json(summary)

    st.divider()
    if st.button("Next Node →", type="primary", use_container_width=True, key=f"advance_{step_key}"):
        _submit_advance(migration_id, api_url, step_key)


# ── Results tabs ───────────────────────────────────────────────────────────────

def render_results(status_data: dict, migration_id: str, api_url: str):
    """Show tabbed results driven by DB status — no session-state merging."""
    current_step = status_data.get("current_step", 0)
    status = status_data.get("status", "running")
    err = status_data.get("error_message")

    tab_labels = ["Overview", "Raw Status"]
    if current_step >= 1 or status == "complete":
        tab_labels.insert(1, "Node 1 — Ingest")
    if current_step >= 2 or status == "complete":
        tab_labels.insert(-1, "Node 2 — Det.Map")
    if current_step >= 3 or status == "complete":
        tab_labels.insert(-1, "Node 3 — Sem.Map")
    if current_step >= 4 or status == "complete":
        tab_labels.insert(-1, "Gate 1 Results")
    if current_step >= 5 or status == "complete":
        tab_labels.insert(-1, "Node 5 — Preprocess")
    if current_step >= 6 or status == "complete":
        tab_labels.insert(-1, "Node 6 — Hierarchy")
    if current_step >= 7 or status == "complete":
        tab_labels.insert(-1, "Gate 2 Results")
    if current_step >= 8 or status == "complete":
        tab_labels.insert(-1, "Node 8 — Output")
    if current_step >= 9 or status == "complete":
        tab_labels.insert(-1, "Write / Gate 3")

    tabs = st.tabs(tab_labels)
    tab_map = {label: tab for label, tab in zip(tab_labels, tabs)}

    # Overview tab
    with tab_map["Overview"]:
        st.markdown(f"**Migration ID:** `{migration_id}`")
        st.markdown(f"**Status:** {_status_colour(status)} `{status}`")
        st.markdown(f"**Progress:** {status_data.get('progress_pct', 0):.0f}%")
        if err:
            st.error(f"Error: {err}")

        # Mapping stats
        t1 = status_data.get("t1_mapped_count", 0)
        t2_auto = status_data.get("t2_auto_count", 0)
        t2_human = status_data.get("t2_human_count", 0)
        unm = status_data.get("unmapped_count", 0)
        total = status_data.get("total_fields", 0)
        if total > 0:
            st.markdown("**Field mapping summary:**")
            df_stats = pd.DataFrame([
                {"Tier": "T1 Deterministic", "Count": t1, "Pct": f"{t1/total:.1%}" if total else "—"},
                {"Tier": "T2 Auto (semantic ≥0.85)", "Count": t2_auto, "Pct": f"{t2_auto/total:.1%}" if total else "—"},
                {"Tier": "T2 Human reviewed", "Count": t2_human, "Pct": f"{t2_human/total:.1%}" if total else "—"},
                {"Tier": "Unmapped", "Count": unm, "Pct": f"{unm/total:.1%}" if total else "—"},
            ])
            st.dataframe(df_stats, use_container_width=True, hide_index=True)

        # Output downloads
        _render_output_links(status_data, api_url, migration_id)

    # Mappings tab (Gate 1 Results)
    if "Gate 1 Results" in tab_map:
        with tab_map["Gate 1 Results"]:
            st.markdown("Field mappings stored in DB.")
            resp = _get(_api(f"/api/migration/{migration_id}/mappings", api_url))
            if resp:
                mappings = resp if isinstance(resp, list) else resp.get("mappings", [])
                if mappings:
                    df_map = pd.DataFrame(mappings)
                    st.dataframe(df_map, use_container_width=True)
                else:
                    st.info("No mappings data available yet.")
            else:
                st.info("Mappings not yet available.")

    # Hierarchy tab (Gate 2 Results)
    if "Gate 2 Results" in tab_map:
        with tab_map["Gate 2 Results"]:
            resp = _get(_api(f"/api/migration/{migration_id}/hierarchy", api_url))
            if resp:
                hierarchies = resp if isinstance(resp, list) else resp.get("hierarchies", [])
                if hierarchies:
                    df_hier = pd.DataFrame(hierarchies)
                    st.dataframe(df_hier, use_container_width=True)
                else:
                    st.info("No hierarchy data available yet.")

    # Output tab
    if "Node 8 — Output" in tab_map:
        with tab_map["Node 8 — Output"]:
            _render_output_links(status_data, api_url, migration_id)

    # Write / Gate 3 tab
    if "Write / Gate 3" in tab_map:
        with tab_map["Write / Gate 3"]:
            if status == "complete":
                st.success("Migration complete — data handed off to svc-ingestion.")
            else:
                st.info("Write node pending or in progress.")
            write_payload = status_data.get("pending_gate_payload")
            if write_payload and isinstance(write_payload, dict):
                summary = write_payload.get("summary", {})
                if summary:
                    st.json(summary)

    # Raw Status tab (always last)
    with tab_map["Raw Status"]:
        st.json(status_data)


def _render_output_links(status_data: dict, api_url: str, migration_id: str):
    json_url = status_data.get("output_json_url")
    csv_url = status_data.get("output_csv_url")
    sql_url = status_data.get("output_sql_url")
    report_url = status_data.get("migration_report_url")

    links = [
        ("JSON output", json_url, "json"),
        ("CSV export", csv_url, "csv"),
        ("SQL statements", sql_url, "sql"),
        ("Migration report", report_url, "report"),
    ]
    available = [(label, fmt) for label, url, fmt in links if url]
    if not available:
        st.caption("Output files not yet generated.")
        return

    st.markdown("**Download outputs:**")
    for label, fmt in available:
        resp = _get(_api(f"/api/migration/{migration_id}/download/{fmt}", api_url))
        if resp and resp.get("url"):
            st.markdown(f"[{label}]({resp['url']})")
        else:
            st.caption(f"{label}: URL pending")


# ── Main polling loop ──────────────────────────────────────────────────────────

_MIN_POLL_INTERVAL = 2.0  # seconds — minimum time between API status calls


def poll_status(migration_id: str, api_url: str, force: bool = False) -> dict:
    """Poll /status and update session state. Returns latest status_data.

    Rate-limited to _MIN_POLL_INTERVAL seconds between real API calls.
    Pass force=True to skip rate limiting (e.g. immediately after gate submit).
    """
    now = time.time()
    elapsed = now - st.session_state.last_poll_time
    cached = st.session_state.status_data

    # Use cached data if polled recently and not forcing a refresh
    if not force and cached and elapsed < _MIN_POLL_INTERVAL:
        return cached

    resp = _get(_api(f"/api/migration/{migration_id}/status", api_url))
    if resp:
        st.session_state.status_data = resp
        st.session_state.last_poll_time = now
        return resp
    # Fall back to last known status if poll fails
    return cached or {}


# ── Main ───────────────────────────────────────────────────────────────────────

def main():
    api_url, org_id, cmms_name = render_sidebar()

    st.title("🏗️ CAFM Migration Pipeline")
    st.caption("9-node AI pipeline: field mapping → hierarchy resolution → svc-ingestion handoff")

    migration_id = st.session_state.migration_id

    if not migration_id:
        render_start_form(api_url, org_id, cmms_name)
        return

    # ── Active migration ────────────────────────────────────────────────────
    # Rate-limited poll: skip spinner when using fresh cached data to avoid
    # flickering the spinner on every widget interaction while on a gate screen.
    submitted_gate = st.session_state.submitted_gate_type
    now = time.time()
    elapsed_since_poll = now - st.session_state.last_poll_time
    needs_real_poll = (
        not st.session_state.status_data
        or elapsed_since_poll >= _MIN_POLL_INTERVAL
        or submitted_gate is not None  # force fresh poll after a submission
    )

    if needs_real_poll:
        with st.spinner("Fetching status..."):
            status_data = poll_status(migration_id, api_url, force=submitted_gate is not None)
    else:
        status_data = st.session_state.status_data or {}

    if not status_data:
        st.error(f"Could not load status for migration `{migration_id}`")
        return

    st.divider()
    render_progress_strip(status_data)
    st.divider()
    render_status_banner(status_data)
    st.divider()

    status = status_data.get("status", "running")
    pending_gate = status_data.get("pending_gate_type")
    pending_payload = status_data.get("pending_gate_payload") or {}

    # ── Step-pause UI (interrupt_after non-gate nodes) ───────────────────────
    if status == "step_paused":
        submitted_gate = st.session_state.get("submitted_gate_type")
        # If we just submitted an advance for THIS step, show spinner while transitioning
        if submitted_gate is not None and pending_gate == submitted_gate:
            st.info("⏳ Advancing — pipeline is resuming, please wait...")
            time.sleep(1.5)
            st.session_state.status_data = None
            st.rerun()
            return
        # Clear stale submission tracking
        st.session_state.submitted_gate_type = None
        render_step_card(pending_gate or "", pending_payload, migration_id, api_url)
        render_results(status_data, migration_id, api_url)
        return

    # ── Gate UIs ────────────────────────────────────────────────────────────
    if status == "awaiting_review":
        # If we just submitted a gate decision, check whether the pipeline has
        # actually advanced yet.  The API may still return awaiting_review for
        # the same gate while the background worker processes the submission.
        # In that case, show a "resuming" spinner instead of re-rendering the
        # gate form — which would confuse the user and could re-submit data.
        if submitted_gate is not None:
            # Determine whether the gate has changed since submission
            gate_still_pending = (pending_gate == submitted_gate) or (
                submitted_gate == "write" and pending_gate in ("write", "final_confirmation")
            )
            if gate_still_pending:
                st.info("⏳ Decision submitted — pipeline is resuming, please wait...")
                time.sleep(1.5)
                st.session_state.status_data = None  # force fresh poll next render
                st.rerun()
                return

        # Gate has changed (or no recent submission) — clear the submitted flag
        # and render the new gate form.
        st.session_state.submitted_gate_type = None
        st.session_state.gate_submitted = False

        if pending_gate == "pre_semantic":
            render_gate_pre_semantic(pending_payload, migration_id, api_url)
        elif pending_gate == "field_mapping":
            render_gate1(pending_payload, migration_id, api_url)
        elif pending_gate == "hierarchy":
            render_gate2(pending_payload, migration_id, api_url)
        elif pending_gate in ("write", "final_confirmation"):
            render_gate3(pending_payload, migration_id, api_url)
        else:
            st.warning(f"Unknown gate type: `{pending_gate}`. Raw payload:")
            st.json(pending_payload)

    elif status == "running":
        # Clear any lingering submission state when the pipeline is running.
        if submitted_gate is not None:
            st.session_state.submitted_gate_type = None
            st.session_state.gate_submitted = False

        st.info("Pipeline is running. Auto-refreshing...")
        render_results(status_data, migration_id, api_url)

        # Sleep only the remaining time until the next poll interval is due,
        # so we never block longer than necessary.
        elapsed = time.time() - st.session_state.last_poll_time
        sleep_remaining = max(0.3, _MIN_POLL_INTERVAL - elapsed)
        time.sleep(sleep_remaining)
        st.rerun()

    elif status == "complete":
        # Clear submission state on completion.
        st.session_state.submitted_gate_type = None
        st.session_state.gate_submitted = False
        st.balloons()
        render_results(status_data, migration_id, api_url)

    elif status in ("failed", "ddl_failed"):
        render_results(status_data, migration_id, api_url)

        if status == "ddl_failed":
            st.subheader("DDL Error Recovery")
            st.markdown(
                "The DDL execution failed. Correct the field definitions below and retry."
            )
            err_msg = status_data.get("error_message", "")
            if err_msg:
                st.error(err_msg)

            retry_config = st.text_area(
                "Corrected extra_fields_config (JSON array)",
                value="[]",
                height=200,
            )
            if st.button("Retry DDL", type="primary"):
                try:
                    config_json = json.loads(retry_config)
                    resp = _post(
                        _api(f"/api/migration/{migration_id}/retry-ddl", api_url),
                        {"extra_fields_config": config_json},
                    )
                    if resp:
                        st.success("DDL retry submitted.")
                        st.session_state.status_data = None
                        time.sleep(1)
                        st.rerun()
                except json.JSONDecodeError as e:
                    st.error(f"Invalid JSON: {e}")

    else:
        # Cancelled or unknown
        render_results(status_data, migration_id, api_url)

    # ── Manual refresh button ────────────────────────────────────────────────
    if status not in ("running",):  # running already auto-reruns
        st.divider()
        if st.button("Refresh Status", use_container_width=True):
            st.session_state.status_data = None
            st.session_state.last_poll_time = 0.0
            st.rerun()


if __name__ == "__main__":
    main()
