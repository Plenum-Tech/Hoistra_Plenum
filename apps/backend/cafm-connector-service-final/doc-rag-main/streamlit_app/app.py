"""Streamlit UI for testing the RAG platform.

Provides three tabs:
  1. Ingest — upload PDF/DOCX/TXT files and see the ingestion result.
  2. Explore — browse ingested documents and their chunks.
  3. Query — run RAG queries in normal or debug mode.

Run:
    streamlit run streamlit_app/app.py
"""
from __future__ import annotations

import os
from pathlib import Path

import requests
import streamlit as st

API_BASE = os.getenv("RAG_API_BASE", "http://localhost:8000")

st.set_page_config(
    page_title="RAG Platform - Test UI",
    page_icon="📄",
    layout="wide",
)

# ---------- Sidebar ----------
with st.sidebar:
    st.title("📄 RAG Platform")
    st.caption("Test UI for document ingestion and retrieval.")

    api_url = st.text_input("API Base URL", value=API_BASE)
    st.session_state["api_base"] = api_url

    try:
        r = requests.get(f"{api_url}/", timeout=3)
        if r.ok:
            info = r.json()
            st.success("API connected")
            st.json(info, expanded=False)
        else:
            st.error(f"API returned {r.status_code}")
    except Exception as e:
        st.error(f"API unreachable: {e}")

    st.markdown("---")
    st.markdown(
        "**Tips**\n\n"
        "- Leave `OPENAI_API_KEY` empty in `.env` to run in mock mode (free).\n"
        "- Default `USE_SQLITE_DEV=true` needs no Postgres.\n"
        "- Upload a few PDFs/DOCX, then query them in the **Query** tab."
    )


tab_ingest, tab_explore, tab_query = st.tabs(["📤 Ingest", "📚 Explore", "🔎 Query"])


# ====================================================================
# 1. Ingest
# ====================================================================
with tab_ingest:
    st.header("Upload a document")
    st.caption("Supports PDF, DOCX, and TXT. The file is extracted, classified, "
               "chunked, embedded, and indexed in one step.")

    uploaded = st.file_uploader(
        "Choose a file",
        type=["pdf", "docx", "txt"],
        accept_multiple_files=False,
    )

    if uploaded is not None:
        st.write(f"**Selected:** {uploaded.name}  •  {uploaded.size:,} bytes")
        if st.button("🚀 Ingest document", type="primary"):
            with st.spinner("Ingesting... (extraction → classification → chunking → embedding)"):
                try:
                    files = {"file": (uploaded.name, uploaded.getvalue(), uploaded.type)}
                    r = requests.post(
                        f"{api_url}/documents/upload",
                        files=files,
                        timeout=300,
                    )
                    if r.ok:
                        result = r.json()
                        st.success(f"✅ Ingested in {result['processing_time_ms']} ms")

                        c1, c2, c3, c4 = st.columns(4)
                        c1.metric("Document ID", result["document_id"][:8] + "…")
                        c2.metric("Pages", result["num_pages"])
                        c3.metric("Chunks", result["num_chunks"])
                        c4.metric("Doc type", result.get("document_type") or "unknown")

                        with st.expander("Full response JSON"):
                            st.json(result)
                    else:
                        st.error(f"Ingestion failed: {r.status_code}")
                        st.code(r.text)
                except Exception as e:
                    st.exception(e)


# ====================================================================
# 2. Explore
# ====================================================================
with tab_explore:
    st.header("Ingested documents")

    if st.button("🔄 Refresh list"):
        st.rerun()

    try:
        r = requests.get(f"{api_url}/documents", timeout=10)
        if r.ok:
            docs = r.json()
            if not docs:
                st.info("No documents ingested yet. Upload one in the **Ingest** tab.")
            else:
                st.write(f"**{len(docs)}** documents indexed:")
                for d in docs:
                    with st.expander(
                        f"📄 {d['file_name']}  •  {d.get('document_type') or 'unknown'}  "
                        f"•  {d.get('num_chunks', 0)} chunks"
                    ):
                        c1, c2 = st.columns([3, 1])
                        c1.json(d, expanded=False)
                        if c2.button("Delete", key=f"del_{d['id']}"):
                            dr = requests.delete(f"{api_url}/documents/{d['id']}")
                            if dr.ok:
                                st.success("Deleted")
                                st.rerun()
                            else:
                                st.error(dr.text)

                        if st.button("Show chunks", key=f"chk_{d['id']}"):
                            cr = requests.get(
                                f"{api_url}/documents/{d['id']}/chunks?limit=100"
                            )
                            if cr.ok:
                                chunks = cr.json()
                                st.write(f"Showing first {len(chunks)} chunks:")
                                for ch in chunks:
                                    with st.container(border=True):
                                        st.caption(
                                            f"#{ch['chunk_index']}  •  "
                                            f"{ch['block_type']}  •  "
                                            f"page {ch.get('page_start')}  •  "
                                            f"section: {ch.get('section_label') or '—'}"
                                        )
                                        # Render embedded image if this chunk is an image
                                        meta = ch.get("meta") or {}
                                        img_url = meta.get("image_url")
                                        if ch["block_type"] == "image" and img_url:
                                            try:
                                                st.image(
                                                    f"{api_url}{img_url}",
                                                    width=360,
                                                    caption=(
                                                        f"{meta.get('width')}x"
                                                        f"{meta.get('height')} "
                                                        f"{meta.get('format')} "
                                                        f"[{meta.get('image_source')}]"
                                                    ),
                                                )
                                            except Exception:
                                                st.caption(f"🖼 {img_url}")
                                        st.write(ch["text_content"][:500] + (
                                            "…" if len(ch["text_content"]) > 500 else ""
                                        ))
        else:
            st.error(f"Failed to list documents: {r.status_code}")
    except Exception as e:
        st.exception(e)


# ====================================================================
# 3. Query
# ====================================================================
with tab_query:
    st.header("Ask a question")

    query = st.text_area(
        "Your question",
        placeholder="e.g. What is the preventive maintenance frequency for AHU-17?",
        height=80,
    )

    c1, c2, c3 = st.columns([1, 1, 2])
    top_k = c1.number_input("top_k", min_value=1, max_value=30, value=8)
    debug_mode = c2.checkbox("Debug mode", value=True,
                             help="Show retrieved chunks and stage scores")

    if c3.button("🔎 Run query", type="primary", disabled=not query.strip()):
        endpoint = "/rag/debug" if debug_mode else "/rag/query"
        with st.spinner("Retrieving and generating answer..."):
            try:
                r = requests.post(
                    f"{api_url}{endpoint}",
                    json={"query": query, "top_k": int(top_k)},
                    timeout=120,
                )
                if r.ok:
                    resp = r.json()

                    # Headline metrics
                    c1, c2, c3, c4 = st.columns(4)
                    c1.metric("Query type", resp.get("query_type", "—"))
                    c2.metric("Confidence", f"{resp.get('confidence', 0):.2f}")
                    c3.metric("Latency", f"{resp.get('latency_ms', 0)} ms")
                    c4.metric("Model", resp.get("model_name", "—"))

                    st.subheader("Answer")
                    st.write(resp.get("answer", ""))

                    # Citations
                    citations = resp.get("citations", [])
                    if citations:
                        st.subheader(f"Citations ({len(citations)})")
                        for i, cit in enumerate(citations, start=1):
                            with st.container(border=True):
                                st.caption(
                                    f"[{i}] 📄 **{cit['file_name']}**  "
                                    f"• page {cit.get('page_start', '?')}  "
                                    f"• section: {cit.get('section') or '—'}"
                                )
                                st.write(f"> {cit.get('quote', '')}")

                    # Matched rows
                    matched = resp.get("matched_rows") or []
                    if matched:
                        st.subheader(f"Row-level matches ({len(matched)})")
                        st.dataframe(matched)

                    # Debug info
                    if debug_mode:
                        st.subheader("Retrieval debug")
                        stages = resp.get("stages", {})
                        st.json(stages, expanded=False)

                        chunks = resp.get("retrieved_chunks", [])
                        if chunks:
                            st.write(f"**{len(chunks)} retrieved chunks:**")
                            for c in chunks:
                                with st.container(border=True):
                                    st.caption(
                                        f"{c['file_name']}  •  {c['block_type']}  •  "
                                        f"page {c.get('page_start')}  •  "
                                        f"**score={c['score']:.3f}**  "
                                        f"(v={c.get('vector_score', 0):.2f} / "
                                        f"b={c.get('bm25_score', 0):.2f})"
                                    )
                                    st.write(c["text_content"][:400] + (
                                        "…" if len(c["text_content"]) > 400 else ""
                                    ))

                    with st.expander("Full JSON response"):
                        st.json(resp)
                else:
                    st.error(f"Query failed: {r.status_code}")
                    st.code(r.text)
            except Exception as e:
                st.exception(e)
