"""svc-operations-intelligence — Phase 2 A/B/C engines entrypoint."""
from __future__ import annotations

import time
import uuid as _uuid

from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from .api.routes import (
    admin_router,
    approvals_router,
    auth_router,
    compliance_router,
    contract_performance_router,
    energy_router,
    superadmin_router,
)
from .config import settings
from .engines.auth import keys as auth_keys
from .core.exceptions import OpsIntelligenceError
from .core.logging import configure_logging, get_logger
from .db import AsyncSessionLocal, apply_sql_seed, init_db

log = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    configure_logging()
    log.info("service.startup", service=settings.service_name, version="1.2.0")
    await init_db()

    # After the migrations, because on a fresh database plenum_cafm.users does not exist
    # until create_all has run. Before the first request, because a key type is a property
    # of the deployment: knowable now, unchanging while this process lives, and fatal to
    # get wrong. A service that cannot read it cannot serve auth, so it does not start.
    async with AsyncSessionLocal() as session:
        await auth_keys.resolve(session)
    if settings.auto_seed_portfolio_buildings:
        try:
            await apply_sql_seed("portfolio_buildings.sql")
        except Exception as exc:  # noqa: BLE001 — a demo seed must never block startup
            log.warning("portfolio_buildings.seed_failed", error=str(exc))
    from .engines.compliance.country_pack import (
        seed_uae_pack,
        seed_uk_pack,
        seed_us_pack,
    )

    seeders = (
        ("UK", settings.auto_seed_uk_pack, seed_uk_pack),
        ("UAE", settings.auto_seed_uae_pack, seed_uae_pack),
        ("US", settings.auto_seed_us_pack, seed_us_pack),
    )
    for country, enabled, seed_fn in seeders:
        if not enabled:
            continue
        try:
            async with AsyncSessionLocal() as session:
                result = await seed_fn(session)
                log.info("country_pack.startup_seed", country=country, **result)
        except Exception as exc:  # noqa: BLE001 — one bad pack must not block startup
            log.warning(
                "country_pack.startup_seed_failed", country=country, error=str(exc)
            )
    yield
    log.info("service.shutdown", service=settings.service_name)


app = FastAPI(
    title="Plenum Operations Intelligence — Compliance + Contract + Energy",
    version="1.2.0",
    description=(
        "Phase 2 Feature A: Compliance. Feature B: Contract Performance. "
        "Feature C: Energy Intelligence (meters, EUI/TM46, anomalies, reports). "
        "No work-order auto-create from Energy anomalies/recommendations."
    ),
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:3000",
        "http://127.0.0.1:3000",
        "http://localhost:3001",
        "http://127.0.0.1:3001",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def log_requests(request: Request, call_next):
    request_id = str(_uuid.uuid4())[:8]
    start = time.monotonic()
    response = await call_next(request)
    log.info(
        "request.complete",
        request_id=request_id,
        method=request.method,
        path=request.url.path,
        status_code=response.status_code,
        elapsed_ms=round((time.monotonic() - start) * 1000),
    )
    return response


@app.exception_handler(OpsIntelligenceError)
async def ops_error_handler(_request: Request, exc: OpsIntelligenceError):
    return JSONResponse(
        status_code=400,
        content={"ok": False, "code": exc.code, "error": exc.message},
    )


@app.get("/health")
async def health():
    return {
        "status": "ok",
        "service": settings.service_name,
        "features": [
            "compliance_A1_A5",
            "contract_performance_B1_B3",
            "energy_intelligence_C",
        ],
    }


@app.get("/metrics")
async def metrics():
    return {"service": settings.service_name, "uptime_probe": True}


app.include_router(approvals_router)
app.include_router(auth_router)
app.include_router(superadmin_router)
app.include_router(admin_router)
app.include_router(compliance_router)
app.include_router(contract_performance_router)
app.include_router(energy_router)
