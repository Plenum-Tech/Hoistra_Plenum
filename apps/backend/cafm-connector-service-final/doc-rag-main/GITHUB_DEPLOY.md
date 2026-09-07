# 🚀 Deploy to GitHub: doc-rag

Complete guide to push your RAG Platform to https://github.com/plenumbala/doc-rag.git

---

## ✅ Pre-Flight Checklist

Before pushing to GitHub, ensure you have:

- [ ] GitHub account credentials
- [ ] Git installed (`git --version`)
- [ ] Repository created at https://github.com/plenumbala/doc-rag.git
- [ ] Clean working directory (no sensitive data)

---

## 📦 Step 1: Extract Your Code

```bash
# Extract the zip file
unzip rag_platform.zip
cd rag_platform
```

---

## 🔐 Step 2: Verify No Sensitive Data

**CRITICAL:** Check these files DON'T contain secrets:

```bash
# Check .env file doesn't exist (should use .env.example)
ls -la .env  # Should NOT exist

# Verify .env.example has placeholder
cat .env.example  # Should have "sk-your-key-here"

# Check .gitignore includes .env
cat .gitignore | grep "^.env$"  # Should show ".env"
```

✅ **Good:** `.env.example` with placeholders  
❌ **Bad:** `.env` with real API keys

---

## 🎯 Step 3: Initialize Git Repository

```bash
# Navigate to project directory
cd rag_platform

# Initialize git
git init

# Add all files
git add .

# Check what will be committed (should NOT include .env, *.db, __pycache__)
git status

# Commit
git commit -m "Initial commit: RAG Platform with hybrid search and Streamlit UI"
```

---

## 🔗 Step 4: Connect to GitHub

```bash
# Add remote repository
git remote add origin https://github.com/plenumbala/doc-rag.git

# Verify remote
git remote -v
# Should show:
# origin  https://github.com/plenumbala/doc-rag.git (fetch)
# origin  https://github.com/plenumbala/doc-rag.git (push)
```

---

## ⬆️ Step 5: Push to GitHub

```bash
# Push to main branch
git branch -M main
git push -u origin main
```

**You'll be prompted for:**
- **Username:** plenumbala
- **Password:** Your GitHub Personal Access Token (not password!)

### 🔑 Need a Personal Access Token?

1. Go to https://github.com/settings/tokens
2. Click "Generate new token (classic)"
3. Select scopes: `repo` (full control)
4. Copy the token (you won't see it again!)
5. Use it as your password when pushing

---

## ✅ Step 6: Verify Upload

Visit: https://github.com/plenumbala/doc-rag

You should see:
- ✅ README.md with project description
- ✅ All source code files
- ✅ Documentation files
- ✅ .gitignore file
- ❌ NO .env file (only .env.example)
- ❌ NO database files (.db)
- ❌ NO __pycache__ directories

---

## 📝 Step 7: Add GitHub Repository Description

1. Go to https://github.com/plenumbala/doc-rag
2. Click the ⚙️ icon next to "About"
3. Add description:
   ```
   Advanced RAG system with vision-based table extraction, hybrid semantic search, and row-level database grounding
   ```
4. Add topics (tags):
   ```
   rag, fastapi, streamlit, openai, pgvector, document-processing, semantic-search
   ```

---

## 🎨 Optional: Add GitHub Actions (CI/CD)

Create `.github/workflows/test.yml`:

```yaml
name: Tests

on: [push, pull_request]

jobs:
  test:
    runs-on: ubuntu-latest
    
    services:
      postgres:
        image: pgvector/pgvector:pg16
        env:
          POSTGRES_PASSWORD: test_password
        options: >-
          --health-cmd pg_isready
          --health-interval 10s
          --health-timeout 5s
          --health-retries 5
    
    steps:
    - uses: actions/checkout@v3
    
    - name: Set up Python
      uses: actions/setup-python@v4
      with:
        python-version: '3.12'
    
    - name: Install dependencies
      run: |
        pip install -r requirements.txt
        pip install pytest
    
    - name: Run tests
      env:
        OPENAI_API_KEY: ${{ secrets.OPENAI_API_KEY }}
      run: pytest app/tests/ -v
```

---

## 📚 Update README on GitHub

Your README.md is already included! It contains:

- 🌟 Features overview
- 🚀 Quick start guide
- 📖 Documentation links
- 🏗️ Architecture diagram
- 🎯 Use cases
- 🔧 API endpoints
- 📁 Project structure

---

## 🔄 Future Updates

When you make changes:

```bash
# Stage changes
git add .

# Commit with descriptive message
git commit -m "Add: feature description"

# Push to GitHub
git push origin main
```

---

## 🌿 Working with Branches

For new features:

```bash
# Create feature branch
git checkout -b feature/new-feature

# Make changes, commit
git add .
git commit -m "Add new feature"

# Push branch
git push origin feature/new-feature

# Create Pull Request on GitHub
# Merge after review
```

---

## 🛡️ Security Best Practices

### ✅ DO:
- Use `.env.example` with placeholders
- Add `.env` to `.gitignore`
- Use environment variables for secrets
- Include LICENSE file
- Add CONTRIBUTING.md

### ❌ DON'T:
- Commit `.env` files with real API keys
- Commit database files (*.db, *.sqlite)
- Commit API keys in code comments
- Commit test data with sensitive info

---

## 📊 Repository Structure on GitHub

```
doc-rag/
├── .github/
│   └── workflows/          # CI/CD (optional)
├── app/                    # FastAPI backend
├── scripts/                # Utility scripts
├── sample_data/            # Sample datasets
├── complete_ui.py          # Streamlit UI
├── docker-compose.yml      # Docker setup
├── requirements.txt        # Python deps
├── .gitignore             # Git ignore rules
├── .env.example           # Environment template
├── LICENSE                # MIT License
├── README.md              # Main documentation
├── CONTRIBUTING.md        # Contributor guide
└── *.md                   # Additional docs
```

---

## 🚨 Emergency: Remove Committed Secrets

If you accidentally committed secrets:

```bash
# Remove .env from git history
git rm --cached .env

# Add to .gitignore
echo ".env" >> .gitignore

# Commit the change
git commit -m "Remove .env from tracking"

# Force push (DANGER: rewrites history)
git push origin main --force

# IMMEDIATELY rotate the exposed API key!
```

---

## ✅ Quick Commands Reference

```bash
# Clone repository
git clone https://github.com/plenumbala/doc-rag.git

# Check status
git status

# Add files
git add .

# Commit
git commit -m "Your message"

# Push
git push origin main

# Pull latest
git pull origin main

# View remote
git remote -v

# View commit history
git log --oneline
```

---

## 🎉 Success Checklist

After pushing, verify:

- [ ] Repository visible at https://github.com/plenumbala/doc-rag
- [ ] README.md displays properly
- [ ] No .env file in repository
- [ ] .gitignore working correctly
- [ ] All documentation files present
- [ ] LICENSE file included
- [ ] Sample data available
- [ ] Docker files present

---

## 📞 Support

If you encounter issues:

1. **Git errors:** Check your token permissions
2. **Push rejected:** Pull latest changes first (`git pull`)
3. **Merge conflicts:** Resolve conflicts manually
4. **Large files:** Ensure no huge files (>100MB)

---

## 🎯 Next Steps

After successful push:

1. ⭐ **Star your own repo** (optional vanity metric!)
2. 📝 **Add topics/tags** to help discovery
3. 📋 **Enable Issues** for bug tracking
4. 🔄 **Enable Discussions** for community Q&A
5. 🏷️ **Create first release** (v1.0.0)
6. 📢 **Share the repo** with your team

---

## 🚀 Complete Command Sequence

Copy and paste this entire sequence:

```bash
# Navigate to your extracted code
cd rag_platform

# Initialize git
git init

# Add all files
git add .

# First commit
git commit -m "Initial commit: RAG Platform with hybrid search and Streamlit UI

Features:
- Multi-format document ingestion (PDF, DOCX, TXT)
- Hybrid BM25 + Semantic + Metadata matching
- Row-by-row iteration API
- Streamlit web UI with 3-step workflow
- Vision-based table extraction
- PostgreSQL + pgvector storage"

# Connect to GitHub
git remote add origin https://github.com/plenumbala/doc-rag.git

# Push to GitHub
git branch -M main
git push -u origin main
```

**Enter your GitHub credentials when prompted!**

---

## ✅ Verification URLs

After pushing, visit these URLs:

- **Repository:** https://github.com/plenumbala/doc-rag
- **Code:** https://github.com/plenumbala/doc-rag/tree/main
- **README:** https://github.com/plenumbala/doc-rag#readme
- **Releases:** https://github.com/plenumbala/doc-rag/releases
- **Issues:** https://github.com/plenumbala/doc-rag/issues

---

**You're ready to push! 🚀**

Good luck with your GitHub repository!
