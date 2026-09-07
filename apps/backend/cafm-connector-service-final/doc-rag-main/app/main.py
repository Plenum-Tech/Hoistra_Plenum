"""FastAPI application entrypoint."""
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.core.config import settings
from app.core.logger import logger
from app.db.session import init_db
from app.routers import document_match, documents, feedback, rag, row_index, row_iteration


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Starting {} | env={}", settings.app_name, settings.app_env)
    try:
        init_db()
    except Exception as e:
        logger.exception("DB init failed: {}", e)
        raise

    # Surface critical operational warnings as an obvious banner so they
    # don't get lost in the noise of startup logs. These are the things
    # that silently degrade query quality if missed.
    from app.services.embedding_service import embedding_service
    from app.services.vision_service import vision_service

    issues = []
    if not vision_service.available:
        issues.append("vision (image table extraction)")
    if embedding_service.mock:
        issues.append("embeddings (semantic retrieval)")

    if issues:
        banner = "=" * 78
        logger.warning("\n" + banner)
        logger.warning("DEGRADED MODE — the following subsystems are NOT using OpenAI:")
        for item in issues:
            logger.warning("  • {}", item)
        logger.warning("Set OPENAI_API_KEY in your .env to enable them.")
        logger.warning("GET / for full mode/warnings JSON.")
        logger.warning(banner)
    else:
        logger.info("All subsystems operational with OpenAI.")

    yield
    logger.info("Shutting down {}", settings.app_name)


app = FastAPI(
    title="RAG Document Intelligence Platform",
    description=(
        "Production-style RAG backend: hybrid retrieval, row-level grounding, "
        "citations, audit, and feedback."
    ),
    version="0.1.0",
    lifespan=lifespan,
)

# CORS — wide open for dev; tighten in prod.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(documents.router)
app.include_router(document_match.router)
app.include_router(row_iteration.router)
app.include_router(row_index.router)
app.include_router(rag.router)
app.include_router(feedback.router)


# Serve extracted images at /images/<doc_id>/<filename>
_images_root = Path(settings.upload_dir) / "images"
_images_root.mkdir(parents=True, exist_ok=True)
app.mount("/images", StaticFiles(directory=str(_images_root)), name="images")


@app.get("/")
def root():
    """Returns app info AND the operational mode of every external dependency.

    Check this first when something is misbehaving — `vision_enabled: false`
    means image tables won't be extracted, regardless of how the rest of
    the pipeline is configured.
    """
    from app.services.embedding_service import embedding_service
    from app.services.ocr_service import ocr_service
    from app.services.vision_service import vision_service

    warnings = []
    if not vision_service.available:
        warnings.append(
            "VISION DISABLED: OPENAI_API_KEY is not set. Images and tables "
            "embedded inside images (exhibits, scans, figures) will fall back "
            "to tesseract OCR which CANNOT preserve table structure. Set "
            "OPENAI_API_KEY in your .env or compose environment to enable "
            "structured table extraction from images."
        )
    if embedding_service.mock:
        warnings.append(
            "EMBEDDINGS IN MOCK MODE: deterministic hash-based pseudo-embeddings "
            "are in use. Retrieval quality will be poor and ordering between "
            "similar chunks will be near-random. Set OPENAI_API_KEY for real "
            "semantic embeddings."
        )
    if not ocr_service.available:
        warnings.append(
            "OCR DISABLED: tesseract binary not found. Images will not have "
            "any text extracted (vision will still work if enabled)."
        )

    return {
        "app": settings.app_name,
        "env": settings.app_env,
        "modes": {
            "vision_enabled": vision_service.available,
            "vision_model": vision_service.model if vision_service.available else None,
            "embeddings_enabled": not embedding_service.mock,
            "embedding_model": (
                embedding_service.model if not embedding_service.mock else "mock-hash"
            ),
            "ocr_enabled": ocr_service.available,
            "sqlite_dev": settings.use_sqlite_dev,
        },
        "warnings": warnings,
        "docs": "/docs",
    }


@app.get("/health")
def health():
    """Liveness + readiness probe. Always returns 200 if the app process is up,
    but includes degraded subsystem flags so monitoring can alert on them."""
    from app.core.config import settings
    from app.services.embedding_service import embedding_service
    from app.services.vision_service import vision_service

    pgvector_ok = True
    if not settings.effective_use_sqlite_dev:
        try:
            import pgvector  # noqa: F401
        except ImportError:
            pgvector_ok = False

    return {
        "status": "ok",
        "vision": "ok" if vision_service.available else "degraded",
        "embeddings": "ok" if not embedding_service.mock else "degraded",
        "postgres": not settings.effective_use_sqlite_dev,
        "pgvector_package": pgvector_ok,
    }
