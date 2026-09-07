# Document RAG Platform

**Advanced RAG system with vision-based table extraction, hybrid semantic search, and row-level database grounding.**

[![Python 3.12+](https://img.shields.io/badge/python-3.12+-blue.svg)](https://www.python.org/downloads/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.109-green.svg)](https://fastapi.tiangolo.com/)
[![Streamlit](https://img.shields.io/badge/Streamlit-1.31-red.svg)](https://streamlit.io/)

## 🌟 Features

- 📄 **Multi-format Document Ingestion** - PDF, DOCX, TXT with vision-based table extraction
- 🔍 **Hybrid Search** - BM25 + Semantic (pgvector) + Metadata field matching
- 🗄️ **Database Row Grounding** - Link document chunks to external database rows
- 📊 **Asset Matching** - Automatic document-to-asset mapping with confidence scores
- 🎯 **Row-by-Row Iteration** - See which assets are mentioned in your documents
- 🖼️ **Vision Table Extraction** - GPT-4 Vision extracts tables from images
- 🌐 **Web UI** - Beautiful Streamlit interface for the complete workflow

## 🚀 Quick Start

```bash
# 1. Clone repository
git clone https://github.com/plenumbala/doc-rag.git
cd doc-rag

# 2. Set up environment
echo "OPENAI_API_KEY=sk-your-key-here" > .env

# 3. Start services
docker compose up -d

# 4. Launch UI
pip install -r streamlit_requirements.txt
streamlit run complete_ui.py
```

**Access at:** http://localhost:8501

## 📖 Documentation

- [QUICK_START.md](QUICK_START.md) - Get started in 3 steps
- [API_TESTING_GUIDE.md](API_TESTING_GUIDE.md) - Complete API reference
- [COMPLETE_UI_GUIDE.md](COMPLETE_UI_GUIDE.md) - Streamlit UI documentation
- [HYBRID_MATCHING_GUIDE.md](HYBRID_MATCHING_GUIDE.md) - Understanding the algorithm

## 🎯 Use Cases

- Asset coverage reports from facility contracts
- Compliance verification with page citations
- Equipment specification extraction
- Maintenance documentation linking

## 🤝 Contributing

Contributions welcome! Open an issue or submit a PR.

## 📝 License

MIT License - see [LICENSE](LICENSE)

---

⭐ Star this repo if you find it useful!
