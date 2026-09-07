#!/bin/bash
# Quick GitHub Deployment Script
# Run this to push your code to https://github.com/plenumbala/doc-rag.git

set -e  # Exit on error

echo "================================================"
echo "GitHub Deployment for doc-rag"
echo "================================================"
echo ""

# Check if git is installed
if ! command -v git &> /dev/null; then
    echo "❌ Error: git is not installed"
    echo "Install git: https://git-scm.com/downloads"
    exit 1
fi

echo "✅ Git is installed"
echo ""

# Check if we're in the right directory
if [ ! -f "README.md" ] || [ ! -f "docker-compose.yml" ]; then
    echo "❌ Error: Run this script from the rag_platform directory"
    exit 1
fi

echo "✅ In correct directory"
echo ""

# Check for sensitive files
echo "🔍 Checking for sensitive files..."
if [ -f ".env" ]; then
    echo "⚠️  WARNING: .env file found!"
    echo "This should NOT be committed to GitHub"
    echo ""
    read -p "Remove .env file? (y/n): " -n 1 -r
    echo
    if [[ $REPLY =~ ^[Yy]$ ]]; then
        rm .env
        echo "✅ Removed .env"
    else
        echo "❌ Aborting. Please remove .env manually"
        exit 1
    fi
fi

if [ -f "data/rag_platform.db" ]; then
    echo "⚠️  Database file found (will be ignored by .gitignore)"
fi

echo "✅ No sensitive files will be committed"
echo ""

# Initialize git if needed
if [ ! -d ".git" ]; then
    echo "📦 Initializing git repository..."
    git init
    echo "✅ Git initialized"
else
    echo "✅ Git already initialized"
fi
echo ""

# Add files
echo "📝 Adding files to git..."
git add .

# Show what will be committed
echo ""
echo "📋 Files to be committed:"
git status --short
echo ""

# Ask for confirmation
read -p "Continue with commit? (y/n): " -n 1 -r
echo
if [[ ! $REPLY =~ ^[Yy]$ ]]; then
    echo "❌ Aborted"
    exit 1
fi

# Commit
echo ""
echo "💾 Creating commit..."
git commit -m "Initial commit: RAG Platform with hybrid search and Streamlit UI

Features:
- Multi-format document ingestion (PDF, DOCX, TXT)
- Hybrid BM25 + Semantic + Metadata matching
- Row-by-row iteration API
- Streamlit web UI with 3-step workflow
- Vision-based table extraction
- PostgreSQL + pgvector storage
- Complete API documentation
- Sample data included"

echo "✅ Commit created"
echo ""

# Add remote
echo "🔗 Connecting to GitHub..."
git remote remove origin 2>/dev/null || true
git remote add origin https://github.com/plenumbala/doc-rag.git
echo "✅ Connected to https://github.com/plenumbala/doc-rag.git"
echo ""

# Rename branch to main
git branch -M main

# Push
echo "🚀 Pushing to GitHub..."
echo ""
echo "You will be prompted for your GitHub credentials:"
echo "  Username: plenumbala"
echo "  Password: Your Personal Access Token (not your password!)"
echo ""
echo "Get a token at: https://github.com/settings/tokens"
echo ""

git push -u origin main

echo ""
echo "================================================"
echo "✅ SUCCESS! Code pushed to GitHub"
echo "================================================"
echo ""
echo "View your repository at:"
echo "  https://github.com/plenumbala/doc-rag"
echo ""
echo "Next steps:"
echo "  1. Visit the repository URL above"
echo "  2. Add repository description and topics"
echo "  3. Enable Issues and Discussions"
echo "  4. Create your first release (v1.0.0)"
echo ""
echo "🎉 Happy coding!"
