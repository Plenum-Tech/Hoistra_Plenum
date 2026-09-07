"""
Streamlit UI for RAG Document-to-Asset Matching

Features:
- Upload documents (PDF, DOCX, TXT)
- View document categories and chunks
- See row-by-row matching results
- Display which metadata fields matched
- Show page citations
"""
import streamlit as st
import requests
import pandas as pd
from datetime import datetime
import json

# Configuration
API_URL = "http://localhost:8000"

# Page config
st.set_page_config(
    page_title="Document Asset Matcher",
    page_icon="📄",
    layout="wide",
)

# Custom CSS
st.markdown("""
<style>
    .match-card {
        padding: 1rem;
        border-radius: 0.5rem;
        border: 1px solid #ddd;
        margin: 0.5rem 0;
    }
    .high-confidence {
        background-color: #d4edda;
        border-color: #c3e6cb;
    }
    .medium-confidence {
        background-color: #fff3cd;
        border-color: #ffeaa7;
    }
    .low-confidence {
        background-color: #f8d7da;
        border-color: #f5c6cb;
    }
    .no-match {
        background-color: #e2e3e5;
        border-color: #d6d8db;
    }
    .matched-field {
        display: inline-block;
        background-color: #007bff;
        color: white;
        padding: 0.2rem 0.5rem;
        border-radius: 0.25rem;
        margin: 0.2rem;
        font-size: 0.85rem;
    }
</style>
""", unsafe_allow_html=True)

# Title
st.title("📄 Document Asset Matcher")
st.markdown("Upload a document and match it against your asset database")

# Sidebar
with st.sidebar:
    st.header("⚙️ Settings")
    
    confidence_threshold = st.slider(
        "Confidence Threshold",
        min_value=0.0,
        max_value=1.0,
        value=0.15,
        step=0.05,
        help="Minimum confidence score to show matches"
    )
    
    show_unmatched = st.checkbox(
        "Show Unmatched Assets",
        value=True,
        help="Display assets that were not found in the document"
    )
    
    show_chunk_text = st.checkbox(
        "Show Full Chunk Text",
        value=False,
        help="Display complete chunk text (can be long)"
    )
    
    st.markdown("---")
    st.markdown("### About")
    st.markdown("""
    This tool uses hybrid semantic + BM25 matching to find assets 
    mentioned in your documents.
    
    **Match Methods:**
    - 🎯 Exact Key
    - 📊 BM25 (keywords)
    - 🧠 Semantic (AI)
    - 📋 Metadata fields
    """)

# Main content
tab1, tab2, tab3 = st.tabs(["📤 Upload", "🔍 Match Results", "📊 Statistics"])

with tab1:
    st.header("Upload Document")
    
    uploaded_file = st.file_uploader(
        "Choose a file",
        type=["pdf", "docx", "txt"],
        help="Upload a document to match against your asset database"
    )
    
    if uploaded_file:
        st.success(f"File selected: {uploaded_file.name}")
        
        col1, col2 = st.columns([1, 3])
        
        with col1:
            if st.button("🚀 Upload & Process", type="primary", use_container_width=True):
                with st.spinner("Uploading and processing document..."):
                    try:
                        # Upload document
                        files = {"file": (uploaded_file.name, uploaded_file, uploaded_file.type)}
                        response = requests.post(f"{API_URL}/documents/upload", files=files)
                        
                        if response.status_code == 200:
                            result = response.json()
                            
                            # Store in session state
                            st.session_state.document_id = result["document_id"]
                            st.session_state.file_name = result["file_name"]
                            st.session_state.num_chunks = result["num_chunks"]
                            st.session_state.document_type = result.get("document_type", "unknown")
                            
                            st.success("✅ Document uploaded successfully!")
                            st.json(result)
                            
                        else:
                            st.error(f"Upload failed: {response.status_code}")
                            st.error(response.text)
                            
                    except Exception as e:
                        st.error(f"Error: {str(e)}")
        
        with col2:
            if "document_id" in st.session_state:
                st.info(f"**Document ID:** `{st.session_state.document_id}`")
                st.info(f"**Chunks:** {st.session_state.num_chunks}")
                st.info(f"**Type:** {st.session_state.document_type}")

with tab2:
    st.header("Row Matching Results")
    
    if "document_id" not in st.session_state:
        st.warning("⚠️ Please upload a document first")
    else:
        col1, col2 = st.columns([1, 3])
        
        with col1:
            if st.button("🔍 Run Matching", type="primary", use_container_width=True):
                with st.spinner("Matching document to database rows..."):
                    try:
                        # Call row iteration API
                        response = requests.post(
                            f"{API_URL}/rows/{st.session_state.document_id}/iterate-rows/summary",
                            json={
                                "confidence_threshold": confidence_threshold,
                                "show_unmatched": show_unmatched
                            }
                        )
                        
                        if response.status_code == 200:
                            st.session_state.match_results = response.json()
                            st.success("✅ Matching complete!")
                        else:
                            st.error(f"Matching failed: {response.status_code}")
                            st.error(response.text)
                            
                    except Exception as e:
                        st.error(f"Error: {str(e)}")
        
        # Display results
        if "match_results" in st.session_state:
            results = st.session_state.match_results
            
            # Summary metrics
            col1, col2, col3, col4 = st.columns(4)
            
            with col1:
                st.metric("Total Assets Checked", results["total_rows_checked"])
            
            with col2:
                st.metric("Assets Matched", results["rows_with_matches"])
            
            with col3:
                st.metric("Assets Not Found", results["rows_without_matches"])
            
            with col4:
                match_rate = (results["rows_with_matches"] / results["total_rows_checked"] * 100) if results["total_rows_checked"] > 0 else 0
                st.metric("Match Rate", f"{match_rate:.1f}%")
            
            st.markdown("---")
            
            # Filter and sort options
            col1, col2, col3 = st.columns(3)
            
            with col1:
                sort_by = st.selectbox(
                    "Sort by",
                    ["Confidence (High to Low)", "Confidence (Low to High)", "Asset Code", "Match Count"]
                )
            
            with col2:
                filter_status = st.selectbox(
                    "Filter",
                    ["All", "Matched Only", "Unmatched Only"]
                )
            
            with col3:
                search = st.text_input("🔍 Search asset code or name")
            
            # Apply filters
            iterations = results["iterations"]
            
            if filter_status == "Matched Only":
                iterations = [i for i in iterations if i["has_match"]]
            elif filter_status == "Unmatched Only":
                iterations = [i for i in iterations if not i["has_match"]]
            
            if search:
                iterations = [
                    i for i in iterations 
                    if search.lower() in i["row_pk"].lower() 
                    or search.lower() in str(i["row_data"].get("asset_name", "")).lower()
                ]
            
            # Sort
            if sort_by == "Confidence (High to Low)":
                iterations = sorted(iterations, key=lambda x: x["best_confidence"], reverse=True)
            elif sort_by == "Confidence (Low to High)":
                iterations = sorted(iterations, key=lambda x: x["best_confidence"])
            elif sort_by == "Asset Code":
                iterations = sorted(iterations, key=lambda x: x["row_pk"])
            elif sort_by == "Match Count":
                iterations = sorted(iterations, key=lambda x: x["total_chunks_matched"], reverse=True)
            
            st.markdown(f"### 📋 Showing {len(iterations)} assets")
            
            # Display each row
            for idx, row in enumerate(iterations):
                # Determine card style based on confidence
                if not row["has_match"]:
                    card_class = "no-match"
                    emoji = "❌"
                elif row["best_confidence"] >= 0.5:
                    card_class = "high-confidence"
                    emoji = "✅"
                elif row["best_confidence"] >= 0.3:
                    card_class = "medium-confidence"
                    emoji = "⚠️"
                else:
                    card_class = "low-confidence"
                    emoji = "🔍"
                
                with st.expander(
                    f"{emoji} **{row['row_pk']}** - {row['row_data'].get('asset_name', 'N/A')} "
                    f"{'(Confidence: ' + str(row['best_confidence']) + ')' if row['has_match'] else '(No Match)'}",
                    expanded=(idx < 3 and row['has_match'])  # Auto-expand first 3 matches
                ):
                    
                    # Asset details
                    col1, col2 = st.columns([2, 1])
                    
                    with col1:
                        st.markdown("#### 📊 Asset Details")
                        
                        # Show all row data as a table
                        row_df = pd.DataFrame([row["row_data"]]).T
                        row_df.columns = ["Value"]
                        row_df.index.name = "Field"
                        st.dataframe(row_df, use_container_width=True)
                    
                    with col2:
                        st.markdown("#### 📈 Match Summary")
                        st.info(row["match_summary"])
                        
                        if row["has_match"]:
                            st.metric("Best Confidence", f"{row['best_confidence']:.3f}")
                            st.metric("Chunks Matched", row["total_chunks_matched"])
                    
                    # Matched chunks details
                    if row["has_match"] and row["matched_chunks"]:
                        st.markdown("#### 📄 Document Citations")
                        
                        for chunk_idx, chunk in enumerate(row["matched_chunks"]):
                            st.markdown(f"**Citation {chunk_idx + 1}** - Page {chunk['page_number']}, Chunk {chunk['chunk_index']}")
                            
                            # Matched fields
                            if chunk["matched_fields"]:
                                st.markdown("**Matched Fields:**")
                                fields_html = " ".join([
                                    f'<span class="matched-field">{field}</span>' 
                                    for field in chunk["matched_fields"]
                                ])
                                st.markdown(fields_html, unsafe_allow_html=True)
                            
                            # Chunk text
                            if show_chunk_text:
                                st.markdown("**Full Text:**")
                                st.text_area(
                                    "Chunk Text",
                                    chunk.get("chunk_text", chunk.get("chunk_text_preview", "")),
                                    height=150,
                                    key=f"chunk_{row['row_pk']}_{chunk_idx}",
                                    label_visibility="collapsed"
                                )
                            else:
                                st.markdown("**Preview:**")
                                st.info(chunk.get("chunk_text_preview", ""))
                            
                            st.markdown("---")

with tab3:
    st.header("📊 Match Statistics")
    
    if "match_results" not in st.session_state:
        st.warning("⚠️ Run matching first to see statistics")
    else:
        results = st.session_state.match_results
        
        # Overview
        st.subheader("Overview")
        
        col1, col2 = st.columns(2)
        
        with col1:
            # Match distribution pie chart
            st.markdown("#### Match Distribution")
            
            match_data = pd.DataFrame({
                "Status": ["Matched", "Not Matched"],
                "Count": [results["rows_with_matches"], results["rows_without_matches"]]
            })
            
            st.bar_chart(match_data.set_index("Status"))
        
        with col2:
            # Confidence distribution
            st.markdown("#### Confidence Distribution")
            
            matched_rows = [r for r in results["iterations"] if r["has_match"]]
            
            if matched_rows:
                confidence_buckets = {
                    "High (≥0.5)": len([r for r in matched_rows if r["best_confidence"] >= 0.5]),
                    "Medium (0.3-0.5)": len([r for r in matched_rows if 0.3 <= r["best_confidence"] < 0.5]),
                    "Low (<0.3)": len([r for r in matched_rows if r["best_confidence"] < 0.3]),
                }
                
                conf_df = pd.DataFrame({
                    "Confidence Level": list(confidence_buckets.keys()),
                    "Count": list(confidence_buckets.values())
                })
                
                st.bar_chart(conf_df.set_index("Confidence Level"))
            else:
                st.info("No matched rows to analyze")
        
        st.markdown("---")
        
        # Top matches
        st.subheader("🏆 Top 10 Matches")
        
        matched_rows = [r for r in results["iterations"] if r["has_match"]]
        top_matches = sorted(matched_rows, key=lambda x: x["best_confidence"], reverse=True)[:10]
        
        if top_matches:
            top_df = pd.DataFrame([
                {
                    "Rank": idx + 1,
                    "Asset Code": r["row_pk"],
                    "Asset Name": r["row_data"].get("asset_name", "N/A"),
                    "Confidence": f"{r['best_confidence']:.3f}",
                    "Chunks": r["total_chunks_matched"],
                    "Summary": r["match_summary"]
                }
                for idx, r in enumerate(top_matches)
            ])
            
            st.dataframe(top_df, use_container_width=True, hide_index=True)
        else:
            st.info("No matches found")
        
        st.markdown("---")
        
        # Metadata field analysis
        st.subheader("🔍 Metadata Field Analysis")
        st.markdown("Which metadata fields are most commonly matched?")
        
        if matched_rows:
            # Count field occurrences
            field_counts = {}
            
            for row in matched_rows:
                for chunk in row["matched_chunks"]:
                    for field in chunk.get("matched_fields", []):
                        field_name = field.split("=")[0]
                        field_counts[field_name] = field_counts.get(field_name, 0) + 1
            
            if field_counts:
                field_df = pd.DataFrame({
                    "Field": list(field_counts.keys()),
                    "Occurrences": list(field_counts.values())
                }).sort_values("Occurrences", ascending=False)
                
                st.bar_chart(field_df.set_index("Field"))
                
                # Show table
                st.dataframe(field_df, use_container_width=True, hide_index=True)
            else:
                st.info("No metadata field matches found")
        else:
            st.info("No matched rows to analyze")
        
        st.markdown("---")
        
        # Export results
        st.subheader("💾 Export Results")
        
        col1, col2 = st.columns(2)
        
        with col1:
            # Export as JSON
            if st.button("📄 Export as JSON", use_container_width=True):
                json_str = json.dumps(results, indent=2)
                st.download_button(
                    label="Download JSON",
                    data=json_str,
                    file_name=f"match_results_{st.session_state.document_id}.json",
                    mime="application/json"
                )
        
        with col2:
            # Export as CSV
            if st.button("📊 Export as CSV", use_container_width=True):
                # Create flat CSV
                csv_rows = []
                for row in results["iterations"]:
                    csv_rows.append({
                        "Asset Code": row["row_pk"],
                        "Asset Name": row["row_data"].get("asset_name", ""),
                        "Has Match": row["has_match"],
                        "Confidence": row["best_confidence"],
                        "Chunks Matched": row["total_chunks_matched"],
                        "Summary": row["match_summary"],
                    })
                
                csv_df = pd.DataFrame(csv_rows)
                csv_str = csv_df.to_csv(index=False)
                
                st.download_button(
                    label="Download CSV",
                    data=csv_str,
                    file_name=f"match_results_{st.session_state.document_id}.csv",
                    mime="text/csv"
                )

# Footer
st.markdown("---")
st.markdown(
    """
    <div style='text-align: center; color: #666; padding: 1rem;'>
    Powered by RAG Platform | Hybrid Semantic + BM25 Matching
    </div>
    """,
    unsafe_allow_html=True
)
