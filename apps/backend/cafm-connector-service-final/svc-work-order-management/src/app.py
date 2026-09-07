"""
AIMMS Work Order Management Service
FastAPI application entry point with centralised error handling and OpenAPI docs.
"""
import os
import time
import uuid as _uuid_mod

from fastapi import FastAPI, HTTPException, Request, status
from fastapi.exceptions import RequestValidationError, ResponseValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.openapi.utils import get_openapi
from contextlib import asynccontextmanager
from sqlalchemy.exc import SQLAlchemyError

from .core.logging import configure_logging, get_logger
from .db import init_db
from .api.routes import work_orders, approvals, approval_admin, email_processor, ppm_scheduler, journeys, assets, dashboard, chat
from .api.schemas.work_order import ErrorDetail, ErrorResponse

log = get_logger(__name__)


# ── Lifespan ──────────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    configure_logging()
    log.info("service.startup", service="svc-work-order-management", version="1.0.0")
    await init_db()

    from .config import settings
    from .api.routes.email_processor import _approval_poller
    import asyncio as _asyncio

    poller_task = None
    if settings.approval_email_poll_enabled:
        interval = settings.approval_email_poll_interval_seconds
        poller_task = _asyncio.create_task(_approval_poller(interval=interval))
        log.info("approval_poller.task_created", interval_seconds=interval)
    else:
        log.info(
            "approval_poller.disabled",
            hint="Set APPROVAL_EMAIL_POLL_ENABLED=true when using email approvals, or POST /api/email/poll",
        )

    yield

    if poller_task is not None:
        poller_task.cancel()
        try:
            await poller_task
        except _asyncio.CancelledError:
            pass
    log.info("service.shutdown", service="svc-work-order-management")


# ── App ───────────────────────────────────────────────────────────────────────

app = FastAPI(
    title="AIMMS Work Order Management Service",
    version="1.0.0",
    description=(
        "AI-powered work order creation, management, and execution service. "
        "Handles multi-source intake (email, PPM, manual, tenant, internal, remediation), "
        "runs a 15-step intelligent assessment, and integrates bidirectionally with CMMS systems."
    ),
    contact={"name": "Plenum Tech", "email": "dev@plenum-tech.com"},
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc",
)

# Allow local UI origins to call API routes (including streaming and POST endpoints).
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
async def log_requests(request: Request, call_next):
    request_id = str(_uuid_mod.uuid4())[:8]
    start = time.monotonic()
    log.info(
        "request.start",
        request_id=request_id,
        method=request.method,
        path=request.url.path,
    )
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


# ── BE1-13: Centralised error handlers ───────────────────────────────────────

def _error_response(status_code: int, code: str, message: str, field: str | None = None) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content=ErrorResponse(
            errors=[ErrorDetail(code=code, message=message, field=field)]
        ).model_dump(),
    )


@app.exception_handler(RequestValidationError)
async def validation_error_handler(request: Request, exc: RequestValidationError):
    """Pydantic field-level validation errors → 422 with per-field breakdown."""
    errors = []
    for err in exc.errors():
        field = ".".join(str(loc) for loc in err["loc"] if loc != "body")
        errors.append(ErrorDetail(
            code="validation_error",
            message=err["msg"],
            field=field or None,
        ))
    log.warning(
        "request.validation_error",
        path=request.url.path,
        error_count=len(errors),
        fields=[e.field for e in errors if e.field],
    )
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        content=ErrorResponse(errors=errors).model_dump(),
    )


@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    """Wraps all HTTPExceptions in the ErrorResponse envelope."""
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
        log.error("request.http_error", path=request.url.path, status_code=exc.status_code, detail=exc.detail)
    elif exc.status_code >= 400:
        log.warning("request.http_error", path=request.url.path, status_code=exc.status_code, detail=exc.detail)

    return JSONResponse(
        status_code=exc.status_code,
        content=ErrorResponse(errors=errors).model_dump(),
    )


@app.exception_handler(SQLAlchemyError)
async def db_error_handler(request: Request, exc: SQLAlchemyError):
    """Catches any unhandled SQLAlchemy error and returns a safe 500."""
    orig = getattr(exc, "orig", None) or getattr(exc, "__cause__", None)
    log.error(
        "request.db_error",
        path=request.url.path,
        method=request.method,
        exc_type=type(exc).__name__,
        message=str(exc),
        pg_detail=str(orig) if orig else None,
        exc_info=exc,
    )
    return _error_response(
        status.HTTP_500_INTERNAL_SERVER_ERROR,
        "database_error",
        "A database error occurred. Please try again.",
    )


@app.exception_handler(ResponseValidationError)
async def response_validation_handler(request: Request, exc: ResponseValidationError):
    """Response model vs ORM/data mismatch — log compactly (avoid huge ASGI-locals tracebacks)."""
    errors = exc.errors()
    log.error(
        "response.validation_error",
        path=request.url.path,
        error_count=len(errors),
        first_errors=errors[:8],
    )
    return _error_response(
        status.HTTP_500_INTERNAL_SERVER_ERROR,
        "response_validation",
        "Response did not match the documented schema.",
    )


@app.exception_handler(Exception)
async def unhandled_error_handler(request: Request, exc: Exception):
    # Full traceback repeats ASGI scope/locals per middleware frame → very large logs
    # in Azure; enable with DEBUG=true when you need a traceback in stdout.
    if os.getenv("DEBUG", "false").lower() == "true":
        log.error("request.unhandled_error", path=request.url.path, exc_type=type(exc).__name__, exc_info=True)
    else:
        log.error(
            "request.unhandled_error",
            path=request.url.path,
            exc_type=type(exc).__name__,
            detail=str(exc)[:500],
        )
    return _error_response(
        status.HTTP_500_INTERNAL_SERVER_ERROR,
        "internal_error",
        f"An unexpected error occurred: {type(exc).__name__}",
    )


# ── Routes ────────────────────────────────────────────────────────────────────

app.include_router(work_orders.router,     prefix="/api/work-orders",          tags=["Work Orders"])
app.include_router(approvals.router,       prefix="/api/work-orders/approvals", tags=["Approvals"])
# Backward-compatible alias used by older clients/tests.
app.include_router(approvals.router,       prefix="/api/approvals",             tags=["Approvals"])
app.include_router(approval_admin.router,  prefix="/api/admin",                 tags=["Approval Admin"])
app.include_router(email_processor.router, prefix="/api/email",                 tags=["Email Intake"])
app.include_router(ppm_scheduler.router,   prefix="/api/ppm",                   tags=["PPM Scheduler"])
app.include_router(journeys.router,        prefix="/api/journeys",              tags=["Journeys"])
app.include_router(assets.router,          prefix="/api",                       tags=["Assets", "Locations"])
app.include_router(dashboard.router,       prefix="/api/dashboard",             tags=["Dashboard"])
app.include_router(chat.router,            prefix="/api/chat",                  tags=["Chat Interface"])


@app.get("/health", tags=["Health"], summary="Service health check")
async def health():
    """Returns 200 OK when the service is running."""
    return {"status": "ok", "service": "svc-work-order-management"}


# ── BE1-14: Custom OpenAPI schema with tag descriptions ───────────────────────

def custom_openapi():
    if app.openapi_schema:
        return app.openapi_schema

    # Include app.servers so /openapi.json can inject scope root_path (from uvicorn
    # --root-path) into the schema; get_openapi omits servers otherwise and Swagger
    # resolves paths at the host root instead of behind /work-order.
    schema = get_openapi(
        title=app.title,
        version=app.version,
        description=app.description,
        routes=app.routes,
        servers=[dict(s) for s in app.servers],
        tags=[
            {
                "name": "Work Orders",
                "description": (
                    "Core CRUD operations on work orders. "
                    "Supports creation, retrieval, status transitions, approval, and closure."
                ),
            },
            {
                "name": "Approvals",
                "description": "Approval workflow endpoints — respond to pending approval requests.",
            },
            {
                "name": "Email Intake",
                "description": (
                    "Email-based work order creation. "
                    "Accepts raw Outlook email dicts, extracts fields via AI, and persists a work order."
                ),
            },
            {
                "name": "PPM Scheduler",
                "description": "Planned Preventive Maintenance scheduler — query due schedules and trigger runs.",
            },
            {
                "name": "Journeys",
                "description": "Journey log tracking — milestones, analytics, per-WO timeline.",
            },
            {
                "name": "Assets",
                "description": "Asset reference lookup — search and retrieve CMMS asset records.",
            },
            {
                "name": "Locations",
                "description": "Location reference list — used to populate form dropdowns.",
            },
            {
                "name": "Dashboard",
                "description": "Aggregate statistics for the dashboard view.",
            },
            {
                "name": "Health",
                "description": "Liveness probe.",
            },
            {
                "name": "Chat Interface",
                "description": (
                    "GPT-powered conversational work order interface. "
                    "Handles chat messages, email ingestion, and PPM triggers through a single "
                    "LLM agent that intelligently routes through assessment, scheduling, "
                    "vendor scoring, and CMMS submission tools."
                ),
            },
        ],
    )
    app.openapi_schema = schema
    return schema


app.openapi = custom_openapi
