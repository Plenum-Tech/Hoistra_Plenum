---
name: security-committed-secrets
description: Live production credentials are committed to git — rotate before any deploy/infra work
metadata:
  type: project
---

**Live production secrets are committed to the repo (verified 2026-06-12).** Before any deployment, infra, or git-history work, flag these for rotation + history scrub:

- Azure Postgres password `Plenum_Tech1` — in `apps/backend/config.toml:12`, as a **source default** in `cafm-connector-service/src/cafm_connector/core/config.py:39`, and inlined across `docker-compose.single-url.local.yml` and the big `docker-compose.yml` (~9 services), plus several `.env.example` files.
- Azure Blob AccountKey — `config.toml:23` and source default `core/config.py:70`.
- Azure Redis access key (full `rediss://` DSN) — source default `core/config.py:44`.
- Two different real Anthropic keys (`config.toml:6`, `doc-rag-main/.env.example:32`), a real OpenAI key (`config.toml:7`, `doc-rag-main/.env.example:39`), a real LangSmith key (`config.toml:8`), Fiix app/access/secret keys (`config.toml:30-32` + `fiix_standalone_test.txt`).

Worst offenders: `apps/backend/config.toml` and `doc-rag-main/.env.example` contain working credentials (the root `apps/backend/.env.example` IS sanitized to `xxxx`). Secrets also leak as pydantic-settings **defaults in source**, so they apply even without any `.env`.

Also note: connector-service auth is effectively disabled — `get_current_user()` returns a hardcoded anonymous user; the real JWT code is commented out. No authorization on any endpoint incl. the Table Editor DDL. **How to apply:** treat this repo as having a compromised secret set; never assume env-injection is the only exposure.

[[architecture-caveats]] [[project-overview]]
