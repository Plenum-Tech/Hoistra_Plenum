"""
UDR — Universal Database Reader
FastAPI application entry point.
"""
import os
import time
import uuid as _uuid_mod
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request, status
from fastapi.exceptions import RequestValidationError, ResponseValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.openapi.utils import get_openapi
from fastapi.responses import JSONResponse
from sqlalchemy.exc import SQLAlchemyError

from .core.logging import configure_logging, get_logger
from .api.routes import agent, tables, runs, spaces
from .api.schemas.database import ErrorDetail, ErrorResponse
from .db import ensure_udr_tables

log = get_logger(__name__)


# ── Lifespan ──────────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):  # type: ignore[type-arg]
    configure_logging()
    log.info("service.startup", service="svc-udr", version="1.0.0")
    try:
        await ensure_udr_tables()
        log.info("service.startup.udr_tables_ready")
    except Exception as exc:  # don't block startup if DB is briefly unavailable
        log.error("service.startup.udr_tables_failed", detail=str(exc)[:300])
    yield
    log.info("service.shutdown", service="svc-udr")


# ── App ───────────────────────────────────────────────────────────────────────

app = FastAPI(
    title="UDR — Universal Database Reader",
    version="1.0.0",
    description=(
        "AI agent sub-service for the Plenum CAFM platform. "
        "Provides full CRUD access to all plenum_cafm schema tables "
        "via both a natural-language agent interface and structured REST endpoints."
    ),
    contact={"name": "Plenum Tech", "email": "dev@plenum-tech.com"},
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:3001",
        "http://127.0.0.1:3001",
        "http://localhost:3000",
        "http://127.0.0.1:3000",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── Request timing middleware ─────────────────────────────────────────────────

@app.middleware("http")
async def log_requests(request: Request, call_next):  # type: ignore[type-arg]
    request_id = str(_uuid_mod.uuid4())[:8]
    start = time.monotonic()
    log.info("request.start", request_id=request_id, method=request.method, path=request.url.path)
    response = await call_next(request)
    elapsed_ms = round((time.monotonic() - start) * 1000)
    log.info(
        "request.complete",
        request_id=request_id,
        method=request.method,
        path=request.url.path,
        status_code=response.status_code,
        elapsed_ms=elapsed_ms,
    )
    return response


# ── Centralised exception handlers ────────────────────────────────────────────

def _error_response(
    status_code: int, code: str, message: str, field: str | None = None
) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content=ErrorResponse(
            errors=[ErrorDetail(code=code, message=message, field=field)]
        ).model_dump(),
    )


@app.exception_handler(RequestValidationError)
async def validation_error_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    errors = []
    for err in exc.errors():
        field = ".".join(str(loc) for loc in err["loc"] if loc != "body")
        errors.append(ErrorDetail(code="validation_error", message=err["msg"], field=field or None))
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        content=ErrorResponse(errors=errors).model_dump(),
    )


@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException) -> JSONResponse:
    detail = exc.detail
    if isinstance(detail, dict):
        errors = [ErrorDetail(
            code=detail.get("code", "error"),
            message=detail.get("message", str(detail)),
            field=detail.get("field"),
        )]
    else:
        errors = [ErrorDetail(code="error", message=str(detail))]
    if exc.status_code >= 500:
        log.error("request.http_error", path=request.url.path, status_code=exc.status_code)
    return JSONResponse(status_code=exc.status_code, content=ErrorResponse(errors=errors).model_dump())


@app.exception_handler(SQLAlchemyError)
async def db_error_handler(request: Request, exc: SQLAlchemyError) -> JSONResponse:
    log.error("request.db_error", path=request.url.path, exc_type=type(exc).__name__)
    return _error_response(
        status.HTTP_500_INTERNAL_SERVER_ERROR, "database_error",
        "A database error occurred. Please try again.",
    )


@app.exception_handler(ResponseValidationError)
async def response_validation_handler(request: Request, exc: ResponseValidationError) -> JSONResponse:
    log.error("response.validation_error", path=request.url.path, errors=exc.errors()[:4])
    return _error_response(
        status.HTTP_500_INTERNAL_SERVER_ERROR, "response_validation",
        "Response did not match the documented schema.",
    )


@app.exception_handler(Exception)
async def unhandled_error_handler(request: Request, exc: Exception) -> JSONResponse:
    if os.getenv("DEBUG", "false").lower() == "true":
        log.error("request.unhandled_error", path=request.url.path, exc_type=type(exc).__name__, exc_info=True)
    else:
        log.error("request.unhandled_error", path=request.url.path, exc_type=type(exc).__name__, detail=str(exc)[:400])
    return _error_response(
        status.HTTP_500_INTERNAL_SERVER_ERROR, "internal_error",
        f"An unexpected error occurred: {type(exc).__name__}",
    )


# ── Routes ────────────────────────────────────────────────────────────────────

app.include_router(agent.router,  prefix="/api/agent",  tags=["Agent"])
app.include_router(tables.router, prefix="/api/tables", tags=["Tables", "CRUD"])
app.include_router(runs.router,   prefix="/api/udr",    tags=["UDR Runs"])
app.include_router(spaces.router, prefix="/api/spaces", tags=["Saved Spaces"])


@app.get("/health", tags=["Health"], summary="Service health check")
async def health() -> dict:
    return {"status": "ok", "service": "svc-udr"}


# ── Custom OpenAPI schema ─────────────────────────────────────────────────────

def custom_openapi() -> dict:  # type: ignore[return]
    if app.openapi_schema:
        return app.openapi_schema
    schema = get_openapi(
        title=app.title,
        version=app.version,
        description=app.description,
        routes=app.routes,
        servers=[dict(s) for s in app.servers],
        tags=[
            {
                "name": "Agent",
                "description": (
                    "Natural-language interface to the UDR agent. "
                    "The agent introspects the schema and executes the required DB operations autonomously."
                ),
            },
            {
                "name": "Tables",
                "description": "Schema introspection — list tables, describe columns, primary keys, and foreign keys.",
            },
            {
                "name": "CRUD",
                "description": (
                    "Structured read/write endpoints for all plenum_cafm tables. "
                    "Use these when the orchestrator knows exactly which table and record to target."
                ),
            },
            {"name": "Health", "description": "Liveness probe."},
        ],
    )
    app.openapi_schema = schema
    return schema


app.openapi = custom_openapi  # type: ignore[method-assign]
