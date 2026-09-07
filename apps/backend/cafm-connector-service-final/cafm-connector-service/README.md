# CAFM Connector Service — US-01

Data Import & Connector Service for the CAFM Platform.
Supports all 12 source types from the spec.

---

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                     FastAPI (port 8000)                     │
│                                                             │
│  POST /api/v1/connectors/test      ← test before saving     │
│  POST /api/v1/connectors           ← save + encrypt creds   │
│  GET  /api/v1/connectors           ← list connectors        │
│  POST /api/v1/imports/preview      ← 50-row preview         │
│  POST /api/v1/imports/field-map    ← save column mapping    │
│  POST /api/v1/imports/run          ← enqueue job (202)      │
│  GET  /api/v1/imports/{id}/status  ← poll progress          │
│  GET  /api/v1/imports/{id}/log     ← error rows             │
│  WS   /ws/imports/progress         ← real-time push         │
└─────────────┬───────────────────────────────────────────────┘
              │ enqueue
              ▼
┌─────────────────────────┐        ┌──────────────────────────┐
│   Redis (ARQ queue)     │───────▶│  ARQ Worker              │
│   + pub/sub channel     │        │  max_jobs = 5            │
└─────────────────────────┘        │                          │
              ▲                    │  1. Connect to source    │
              │ broadcast          │  2. Stream rows          │
              │                    │  3. Apply field maps     │
┌─────────────┴───────────┐        │  4. Dedup check          │
│   WebSocket relay       │        │  5. Write to Postgres    │
└─────────────────────────┘        │  6. Generate QR codes    │
                                   │  7. Rollback on error    │
                                   └──────────────────────────┘

Secrets: AES-256 (dev) or HashiCorp Vault KV v2 (prod)
DB:      PostgreSQL (connectors, import_jobs, field_maps, assets)
```

---

## Quick Start

```bash
# 1. Copy env file
cp .env.example .env
# Edit JWT_SECRET and SECRETS_AES_KEY at minimum

# 2. Start the full stack
cd docker
docker compose up

# API docs: http://localhost:8000/docs
# Vault UI: http://localhost:8200 (token: cafm-dev-token)
```

## Run locally (without Docker)

```bash
pip install -e ".[all,dev]"

# Start Postgres + Redis
docker compose up postgres redis -d

# Run DB migrations
alembic upgrade head

# Start API
uvicorn cafm_connector.api.app:app --reload

# Start worker (separate terminal)
arq cafm_connector.jobs.worker.WorkerSettings
```

## Running tests

```bash
pytest tests/ -v
```

---

## Connector Reference

| Source     | Auth                          | Key params                          |
|------------|-------------------------------|-------------------------------------|
| PostgreSQL | username + password           | host, port, database, schema        |
| MySQL      | username + password           | host, port, database                |
| MSSQL      | username + password           | host, port, database, driver        |
| MongoDB    | username + password           | host, port, database                |
| CSV        | none                          | file_path, delimiter                |
| Excel      | none                          | file_path, sheet_name               |
| JSON       | none                          | file_path or url, root_key          |
| XML        | none                          | file_path, record_tag               |
| Parquet    | none                          | file_path                           |
| REST       | bearer / api_key / basic      | base_url, endpoint, data_key        |
| SOAP       | basic (WS-Security)           | wsdl_url, operation                 |
| OData      | bearer / basic                | base_url, version (v2/v4)           |

---

## Switching to Vault (production)

1. Set `SECRETS_BACKEND=vault` in `.env`
2. Set `VAULT_URL`, `VAULT_TOKEN`, `VAULT_MOUNT_PATH`
3. Ensure Vault KV v2 is enabled at the mount path
4. No code changes required — the abstraction handles it

---

## Project structure

```
src/cafm_connector/
  api/
    app.py                  ← FastAPI factory + lifespan
    dependencies.py         ← DB session, auth, service DI
    routes/
      connectors.py         ← All 8 US-01 endpoints
    schemas/
      connectors.py         ← Pydantic request/response models
    websocket.py            ← Real-time progress relay
  connectors/
    base.py                 ← Abstract Connector + SchemaInspector
    registry.py             ← Singleton plugin registry
    plugins/
      postgresql/           ← SQLAlchemy + psycopg2
      mysql/                ← SQLAlchemy + PyMySQL
      mssql/                ← SQLAlchemy + pyodbc
      mongodb/              ← pymongo + $changeStream
      csv_source/           ← pandas streaming
      excel/                ← openpyxl/pandas
      json_source/          ← httpx + file
      xml_source/           ← lxml
      parquet/              ← PyArrow
      rest/                 ← httpx + pagination
      soap/                 ← zeep WSDL
      odata/                ← httpx + OData protocol
  core/
    config.py               ← Pydantic settings
    types.py                ← Enums + RawRow type
    exceptions.py           ← Exception hierarchy
    logging.py              ← structlog setup
  jobs/
    worker.py               ← ARQ worker + job logic
  models/
    db.py                   ← SQLAlchemy ORM models
  secrets/
    backend.py              ← AES-256 / Vault abstraction
  services/
    connector_service.py    ← Business logic layer
  schema/
    models.py               ← Unified schema models
tests/
  unit/
    connectors/
      test_registry.py
    test_secrets.py
  integration/
docker/
  docker-compose.yml
  Dockerfile
```
