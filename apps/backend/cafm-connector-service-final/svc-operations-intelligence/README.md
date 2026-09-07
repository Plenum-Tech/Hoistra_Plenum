# Phase 2 — Operations Intelligence (Compliance + Contract + Energy)
#
# Service: svc-operations-intelligence (port 8009)
# Features: A1–A5 Compliance · B1–B3 Contract Performance · C Energy Intelligence
#
# UDR hybrid ownership: see UDR_PHASE2.md
# Locked product decisions: see DECISIONS.md
#
# Quick start (from this directory):
#   pip install -r requirements.txt
#   set DB_URL=postgresql+asyncpg://...
#   uvicorn src.app:app --host 0.0.0.0 --port 8009 --reload
#
# Nightly scan worker:
#   arq src.worker.WorkerSettings
#
# Key endpoints:
#   POST /api/compliance/scan
#   POST /api/compliance/certificates
#   GET  /api/compliance/certificates
#   GET  /api/compliance/country-pack
#   POST /api/compliance/country-pack/seed-uk
#   POST /api/compliance/verify-now
#   GET  /api/approvals
#   GET  /api/compliance/approvals
#   POST /api/compliance/approvals/{id}/decide
#   POST /api/compliance/adversary
#   GET  /api/compliance/saved-space/summary
#
# UK pack: 54 certificate types (27 Building + 27 Vendor) from
# UK_Compliance_Certification_Pack_v1.1.docx — rebuild:
#   python scripts/rebuild_uk_pack_from_docx.py
# Audit: ops_audit_log is append-only (app + DB trigger phase2_ops_audit_append_only.sql)