# 🚀 GitHub Quick Deploy - CHEAT SHEET

## One-Command Deploy

```bash
bash deploy_to_github.sh
```

**That's it!** The script will guide you through the process.

---

## Manual Deploy (5 Commands)

```bash
cd rag_platform
git init
git add .
git commit -m "Initial commit: RAG Platform"
git remote add origin https://github.com/plenumbala/doc-rag.git
git branch -M main
git push -u origin main
```

---

## 🔑 Get GitHub Token

1. Visit: https://github.com/settings/tokens
2. Click: "Generate new token (classic)"
3. Select: `repo` (full control)
4. Copy token
5. Use as password when pushing

---

## ✅ After Deployment

Visit: https://github.com/plenumbala/doc-rag

Should see:
- ✅ All code files
- ✅ README.md
- ✅ Documentation
- ❌ NO .env file
- ❌ NO database files

---

## 🔄 Future Updates

```bash
git add .
git commit -m "Update: description"
git push
```

---

## 📁 What's Included

```
✅ Complete FastAPI backend
✅ Streamlit web UI
✅ Documentation (10+ guides)
✅ Sample data
✅ Docker setup
✅ Scripts and utilities
✅ Tests
✅ .gitignore (protects secrets)
✅ LICENSE (MIT)
✅ CONTRIBUTING.md
```

---

## 🛡️ Security Checklist

Before pushing:
- [ ] No .env file (only .env.example)
- [ ] No API keys in code
- [ ] .gitignore includes sensitive files
- [ ] Database files excluded

---

## 📞 Help

Problems? Check:
- GITHUB_DEPLOY.md (full guide)
- https://docs.github.com/en/get-started

---

**Ready to deploy!** 🎉
