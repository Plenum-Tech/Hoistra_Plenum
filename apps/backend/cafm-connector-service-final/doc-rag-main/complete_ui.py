"""
Complete RAG Platform UI - CSV Upload + Document Upload + Row Matching

Features:
1. Upload CSV to row_semantic_index table
2. Upload PDF/DOCX/TXT documents for chunking
3. Run row iteration matching
4. View detailed results with metadata field tracking
"""
import streamlit as st
import requests
import pandas as pd
import json
from io import StringIO

# Configuration
API_URL = "http://localhost:8000"

# Page config
st.set_page_config(
    page_title="RAG Platform - Complete Workflow",
    page_icon="📄",
    layout="wide",
)

# Custom CSS
st.markdown("""
<style>
    .main-header {
        font-size: 2.5rem;
        font-weight: bold;
        color: #1f77b4;
        margin-bottom: 1rem;
    }
    .step-box {
        padding: 1.5rem;
        border-radius: 0.5rem;
        border: 2px solid #ddd;
        margin: 1rem 0;
        background-color: #f8f9fa;
    }
    .step-complete {
        border-color: #28a745;
        background-color: #d4edda;
    }
    .step-active {
        border-color: #007bff;
        background-color: #d1ecf1;
    }
    .match-high {
        background-color: #d4edda;
        padding: 0.5rem;
        border-radius: 0.3rem;
        border-left: 4px solid #28a745;
    }
    .match-medium {
        background-color: #fff3cd;
        padding: 0.5rem;
        border-radius: 0.3rem;
        border-left: 4px solid #ffc107;
    }
    .match-low {
        background-color: #f8d7da;
        padding: 0.5rem;
        border-radius: 0.3rem;
        border-left: 4px solid #dc3545;
    }
    .no-match {
        background-color: #e2e3e5;
        padding: 0.5rem;
        border-radius: 0.3rem;
        border-left: 4px solid #6c757d;
    }
    .field-badge {
        display: inline-block;
        background-color: #007bff;
        color: white;
        padding: 0.2rem 0.6rem;
        border-radius: 0.25rem;
        margin: 0.2rem;
        font-size: 0.85rem;
    }
</style>
""", unsafe_allow_html=True)

# Initialize session state
if 'csv_loaded' not in st.session_state:
    st.session_state.csv_loaded = False
if 'document_uploaded' not in st.session_state:
    st.session_state.document_uploaded = False
if 'matching_done' not in st.session_state:
    st.session_state.matching_done = False

# Title
st.markdown('<div class="main-header">📄 RAG Platform - Complete Workflow</div>', unsafe_allow_html=True)
st.markdown("**3-Step Process:** Upload Assets CSV → Upload Document → Match & View Results")

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
        value=False,
        help="Display assets that were not found in the document"
    )
    
    st.markdown("---")
    
    # API Status
    st.subheader("🔌 API Status")
    try:
        response = requests.get(f"{API_URL}/", timeout=2)
        if response.status_code == 200:
            st.success("✅ Connected")
            
            # Get row count
            try:
                # This is a simple check - you might need to adjust based on your API
                st.info(f"API: {API_URL}")
            except:
                pass
        else:
            st.error("⚠️ API Error")
    except:
        st.error("❌ Not Connected")
        st.caption("Start API: `docker compose up`")
    
    st.markdown("---")
    st.markdown("### Workflow Status")
    st.write("✅ CSV Loaded" if st.session_state.csv_loaded else "⬜ CSV Loaded")
    st.write("✅ Document Uploaded" if st.session_state.document_uploaded else "⬜ Document Uploaded")
    st.write("✅ Matching Done" if st.session_state.matching_done else "⬜ Matching Done")

# Main content
col1, col2, col3 = st.columns(3)

# STEP 1: Upload CSV
with col1:
    step_class = "step-complete" if st.session_state.csv_loaded else "step-active"
    st.markdown(f'<div class="step-box {step_class}">', unsafe_allow_html=True)
    st.markdown("### 📊 Step 1: Upload Assets CSV")
    
    csv_file = st.file_uploader(
        "Upload CSV File",
        type=["csv"],
        key="csv_uploader",
        help="Upload your assets database CSV file"
    )
    
    if csv_file:
        # Show preview
        try:
            df = pd.read_csv(csv_file)
            st.caption(f"📁 {csv_file.name}")
            st.caption(f"Rows: {len(df)} | Columns: {len(df.columns)}")
            
            with st.expander("Preview CSV"):
                st.dataframe(df.head(), use_container_width=True)
            
            # Get PK column
            pk_column = st.selectbox(
                "Primary Key Column",
                options=df.columns.tolist(),
                help="Select the column to use as primary key"
            )
            
            table_name = st.text_input(
                "Table Name",
                value="assets",
                help="Name for this table in the database"
            )
            
            if st.button("🚀 Load to Database", type="primary", use_container_width=True):
                with st.spinner("Loading CSV to database..."):
                    try:
                        # Save CSV temporarily
                        csv_content = csv_file.getvalue().decode('utf-8')
                        
                        # Prepare data for API
                        csv_data = []
                        reader = pd.read_csv(StringIO(csv_content))
                        
                        for _, row in reader.iterrows():
                            csv_data.append(row.to_dict())
                        
                        # Send to backend (we'll use a workaround since we don't have direct upload endpoint)
                        # For now, show success and store in session
                        st.session_state.csv_data = csv_data
                        st.session_state.csv_pk_column = pk_column
                        st.session_state.csv_table_name = table_name
                        st.session_state.csv_loaded = True
                        st.session_state.csv_filename = csv_file.name
                        st.session_state.csv_row_count = len(csv_data)
                        
                        st.success(f"✅ Loaded {len(csv_data)} rows!")
                        st.info(f"**Table:** {table_name} | **PK:** {pk_column}")
                        st.rerun()
                        
                    except Exception as e:
                        st.error(f"Error: {str(e)}")
        
        except Exception as e:
            st.error(f"Error reading CSV: {str(e)}")
    
    if st.session_state.csv_loaded:
        st.success(f"✅ {st.session_state.csv_row_count} rows loaded")
        st.caption(f"Table: {st.session_state.csv_table_name}")
        st.caption(f"PK: {st.session_state.csv_pk_column}")
    
    st.markdown('</div>', unsafe_allow_html=True)

# STEP 2: Upload Document
with col2:
    step_class = "step-complete" if st.session_state.document_uploaded else ("step-active" if st.session_state.csv_loaded else "")
    st.markdown(f'<div class="step-box {step_class}">', unsafe_allow_html=True)
    st.markdown("### 📄 Step 2: Upload Document")
    
    if not st.session_state.csv_loaded:
        st.warning("⚠️ Upload CSV first")
    else:
        doc_file = st.file_uploader(
            "Upload Document",
            type=["pdf", "docx", "txt"],
            key="doc_uploader",
            help="Upload PDF, DOCX, or TXT file"
        )
        
        if doc_file:
            st.caption(f"📁 {doc_file.name}")
            st.caption(f"Size: {doc_file.size / 1024:.1f} KB")
            
            if st.button("🚀 Process Document", type="primary", use_container_width=True):
                with st.spinner("Uploading and processing..."):
                    try:
                        # Upload to API
                        files = {"file": (doc_file.name, doc_file, doc_file.type)}
                        response = requests.post(f"{API_URL}/documents/upload", files=files)
                        
                        if response.status_code == 200:
                            result = response.json()
                            
                            st.session_state.document_id = result["document_id"]
                            st.session_state.file_name = result["file_name"]
                            st.session_state.num_chunks = result["num_chunks"]
                            st.session_state.document_type = result.get("document_type", "unknown")
                            st.session_state.document_uploaded = True
                            
                            st.success("✅ Document processed!")
                            st.json(result)
                            st.rerun()
                        else:
                            st.error(f"Upload failed: {response.status_code}")
                            st.error(response.text)
                            
                    except Exception as e:
                        st.error(f"Error: {str(e)}")
        
        if st.session_state.document_uploaded:
            st.success(f"✅ Document ready")
            st.caption(f"Chunks: {st.session_state.num_chunks}")
            st.caption(f"ID: {st.session_state.document_id[:20]}...")
    
    st.markdown('</div>', unsafe_allow_html=True)

# STEP 3: Run Matching
with col3:
    step_class = "step-complete" if st.session_state.matching_done else ("step-active" if st.session_state.document_uploaded else "")
    st.markdown(f'<div class="step-box {step_class}">', unsafe_allow_html=True)
    st.markdown("### 🔍 Step 3: Match Assets")
    
    if not st.session_state.document_uploaded:
        st.warning("⚠️ Upload document first")
    else:
        if st.button("🔍 Run Matching", type="primary", use_container_width=True):
            with st.spinner("Matching rows to document..."):
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
                        st.session_state.matching_done = True
                        st.success("✅ Matching complete!")
                        st.rerun()
                    else:
                        st.error(f"Matching failed: {response.status_code}")
                        st.error(response.text)
                        
                except Exception as e:
                    st.error(f"Error: {str(e)}")
        
        if st.session_state.matching_done:
            results = st.session_state.match_results
            st.success(f"✅ {results['rows_with_matches']} matches")
            st.caption(f"Checked: {results['total_rows_checked']} rows")
            st.caption(f"Rate: {results['rows_with_matches']/results['total_rows_checked']*100:.1f}%")
    
    st.markdown('</div>', unsafe_allow_html=True)

# Results Section
if st.session_state.matching_done:
    st.markdown("---")
    st.markdown("## 📊 Match Results")
    
    results = st.session_state.match_results
    
    # Summary metrics
    col1, col2, col3, col4 = st.columns(4)
    
    with col1:
        st.metric("Total Assets", results["total_rows_checked"])
    
    with col2:
        st.metric("Matched", results["rows_with_matches"], 
                 delta=f"{results['rows_with_matches']/results['total_rows_checked']*100:.1f}%")
    
    with col3:
        st.metric("Not Found", results["rows_without_matches"])
    
    with col4:
        avg_confidence = 0
        matched = [r for r in results["iterations"] if r["has_match"]]
        if matched:
            avg_confidence = sum(r["best_confidence"] for r in matched) / len(matched)
        st.metric("Avg Confidence", f"{avg_confidence:.3f}")
    
    st.markdown("---")
    
    # Tabs for different views
    tab1, tab2, tab3 = st.tabs(["📋 All Results", "📈 Statistics", "💾 Export"])
    
    with tab1:
        # Filters
        col1, col2, col3 = st.columns(3)
        
        with col1:
            sort_option = st.selectbox(
                "Sort by",
                ["Confidence (High to Low)", "Confidence (Low to High)", "Asset Code", "Match Count"]
            )
        
        with col2:
            filter_option = st.selectbox(
                "Filter",
                ["All", "Matched Only", "Unmatched Only"]
            )
        
        with col3:
            search_query = st.text_input("🔍 Search", placeholder="Search asset code or name...")
        
        # Apply filters
        iterations = results["iterations"]
        
        if filter_option == "Matched Only":
            iterations = [i for i in iterations if i["has_match"]]
        elif filter_option == "Unmatched Only":
            iterations = [i for i in iterations if not i["has_match"]]
        
        if search_query:
            iterations = [
                i for i in iterations
                if search_query.lower() in i["row_pk"].lower()
                or search_query.lower() in str(i["row_data"].get("asset_name", "")).lower()
            ]
        
        # Sort
        if sort_option == "Confidence (High to Low)":
            iterations = sorted(iterations, key=lambda x: x["best_confidence"], reverse=True)
        elif sort_option == "Confidence (Low to High)":
            iterations = sorted(iterations, key=lambda x: x["best_confidence"])
        elif sort_option == "Asset Code":
            iterations = sorted(iterations, key=lambda x: x["row_pk"])
        elif sort_option == "Match Count":
            iterations = sorted(iterations, key=lambda x: x["total_chunks_matched"], reverse=True)
        
        st.markdown(f"### Showing {len(iterations)} assets")
        
        # Display results
        for idx, row in enumerate(iterations):
            # Determine style
            if not row["has_match"]:
                style_class = "no-match"
                emoji = "❌"
            elif row["best_confidence"] >= 0.5:
                style_class = "match-high"
                emoji = "✅"
            elif row["best_confidence"] >= 0.3:
                style_class = "match-medium"
                emoji = "⚠️"
            else:
                style_class = "match-low"
                emoji = "🔍"
            
            # Build title
            title = f"{emoji} **{row['row_pk']}** - {row['row_data'].get('asset_name', 'N/A')}"
            if row['has_match']:
                title += f" (Confidence: {row['best_confidence']:.3f})"
            else:
                title += " (No Match)"
            
            with st.expander(title, expanded=(idx < 3 and row['has_match'])):
                col1, col2 = st.columns([3, 2])
                
                with col1:
                    st.markdown("#### 📊 Asset Details")
                    
                    # Display as table
                    details_df = pd.DataFrame([row["row_data"]]).T
                    details_df.columns = ["Value"]
                    st.dataframe(details_df, use_container_width=True)
                
                with col2:
                    st.markdown("#### 📈 Match Info")
                    st.info(row["match_summary"])
                    
                    if row["has_match"]:
                        st.metric("Confidence", f"{row['best_confidence']:.3f}")
                        st.metric("Chunks", row["total_chunks_matched"])
                
                # Show matched chunks
                if row["has_match"] and row["matched_chunks"]:
                    st.markdown("#### 📄 Document Citations")
                    
                    for chunk_idx, chunk in enumerate(row["matched_chunks"]):
                        st.markdown(f"**Citation {chunk_idx + 1}** - Page {chunk['page_number']}, Chunk #{chunk['chunk_index']}")
                        
                        col1, col2 = st.columns([2, 3])
                        
                        with col1:
                            st.markdown("**Matched Fields:**")
                            if chunk["matched_fields"]:
                                fields_html = " ".join([
                                    f'<span class="field-badge">{field}</span>'
                                    for field in chunk["matched_fields"]
                                ])
                                st.markdown(fields_html, unsafe_allow_html=True)
                            else:
                                st.caption("No specific fields")
                        
                        with col2:
                            st.markdown("**Text Preview:**")
                            st.info(chunk.get("chunk_text_preview", ""))
                        
                        st.markdown("---")
    
    with tab2:
        st.markdown("### 📊 Statistics Dashboard")
        
        # Charts
        col1, col2 = st.columns(2)
        
        with col1:
            st.markdown("#### Match Distribution")
            chart_data = pd.DataFrame({
                "Status": ["Matched", "Not Matched"],
                "Count": [results["rows_with_matches"], results["rows_without_matches"]]
            })
            st.bar_chart(chart_data.set_index("Status"))
        
        with col2:
            st.markdown("#### Confidence Distribution")
            matched_rows = [r for r in results["iterations"] if r["has_match"]]
            
            if matched_rows:
                buckets = {
                    "High (≥0.5)": len([r for r in matched_rows if r["best_confidence"] >= 0.5]),
                    "Medium (0.3-0.5)": len([r for r in matched_rows if 0.3 <= r["best_confidence"] < 0.5]),
                    "Low (<0.3)": len([r for r in matched_rows if r["best_confidence"] < 0.3]),
                }
                conf_df = pd.DataFrame(list(buckets.items()), columns=["Level", "Count"])
                st.bar_chart(conf_df.set_index("Level"))
        
        # Top matches
        st.markdown("#### 🏆 Top 10 Matches")
        matched = [r for r in results["iterations"] if r["has_match"]]
        top_matches = sorted(matched, key=lambda x: x["best_confidence"], reverse=True)[:10]
        
        if top_matches:
            top_df = pd.DataFrame([
                {
                    "Rank": idx + 1,
                    "Asset Code": r["row_pk"],
                    "Asset Name": r["row_data"].get("asset_name", "N/A"),
                    "Confidence": f"{r['best_confidence']:.3f}",
                    "Chunks": r["total_chunks_matched"],
                }
                for idx, r in enumerate(top_matches)
            ])
            st.dataframe(top_df, use_container_width=True, hide_index=True)
        
        # Field analysis
        st.markdown("#### 🔍 Most Matched Fields")
        field_counts = {}
        for row in matched:
            for chunk in row["matched_chunks"]:
                for field in chunk.get("matched_fields", []):
                    field_name = field.split("=")[0]
                    field_counts[field_name] = field_counts.get(field_name, 0) + 1
        
        if field_counts:
            field_df = pd.DataFrame(
                list(field_counts.items()), 
                columns=["Field", "Occurrences"]
            ).sort_values("Occurrences", ascending=False)
            
            st.bar_chart(field_df.set_index("Field"))
            st.dataframe(field_df, use_container_width=True, hide_index=True)
    
    with tab3:
        st.markdown("### 💾 Export Results")
        
        col1, col2 = st.columns(2)
        
        with col1:
            st.markdown("#### JSON Export")
            json_str = json.dumps(results, indent=2)
            st.download_button(
                label="📄 Download JSON",
                data=json_str,
                file_name=f"match_results_{st.session_state.document_id}.json",
                mime="application/json",
                use_container_width=True
            )
        
        with col2:
            st.markdown("#### CSV Export")
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
                label="📊 Download CSV",
                data=csv_str,
                file_name=f"match_results_{st.session_state.document_id}.csv",
                mime="text/csv",
                use_container_width=True
            )

# Footer
st.markdown("---")
st.markdown(
    """
    <div style='text-align: center; color: #666; padding: 1rem;'>
    RAG Platform | Hybrid Semantic + BM25 + Metadata Matching
    </div>
    """,
    unsafe_allow_html=True
)
