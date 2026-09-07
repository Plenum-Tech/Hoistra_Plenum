---
name: project-overview
description: What the FINAL-PLENUM-CAFM project is — services, ports, stack, entrypoints
metadata:
  type: project
---

FINAL-PLENUM-CAFM is a monorepo for an AI-driven CAFM/CMMS platform (UAE facilities ops). Two roots: `apps/backend/cafm-connector-service-final/` (9 Python FastAPI services) and `apps/frontend/` (Next.js 16 + React 19, name `cafm-web`). Canonical DB schema is `plenum_cafm` on Azure Postgres (~62 tables).

Service → port (verified): connector 8000, svc-ingestion 8001, svc-query 8002, svc-ai-schema-mapper 8003, doc-rag 8004 (container 8000 in big compose), table-editor 8005 (connector sub-app), **svc-udr 8006** (docs that say 8000 are wrong), svc-work-order-management 8007 (root-path `/work-order`), **svc-deepagents 8008** (the single-door orchestrator hub), frontend 3000, nginx gateway 80 (prod single-app) / 8080 (local gateway).

Stack: FastAPI + SQLAlchemy 2 async (asyncpg) + Pydantic v2 + Alembic (per-service) + Redis/ARQ. AI: LangGraph (schema-mapper graphs + deepagents react agent), Anthropic Claude (Haiku eval, Sonnet extraction, Opus legal) AND OpenAI (deepagents gpt-4o-mini temp 0, WO engine gpt-4o-mini, doc-rag embeddings/vision). Frontend: Zustand + TanStack Query + Tailwind 4 + AG Grid + native WebSocket.

Production ships as ONE combined image (`Dockerfile.single-app`): node build → python runtime → supervisord runs all services + nginx on port 80. CI (`azure-pipelines.yml`) is build-and-push-to-ACR only (no deploy/test stage). The big `apps/backend/.../docker-compose.yml` is a divergent per-service dev model that does NOT match prod.

The frontend's center of gravity is the single-door AI shell at `/ai` ([[architecture-caveats]] for what's real vs stubbed).
