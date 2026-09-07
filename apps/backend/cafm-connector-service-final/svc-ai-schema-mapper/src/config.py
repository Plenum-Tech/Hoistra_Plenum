"""svc-AI-Schema-Mapper configuration.

Settings are loaded from .env file or environment variables.
All secrets are environment-injected, never hardcoded.
"""

from functools import lru_cache
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings for svc-AI-Schema-Mapper."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ── App ────────────────────────────────────────────────────────
    app_name: str = "CAFM AI Schema Mapper"
    app_version: str = "0.1.0"
    debug: bool = False
    environment: Literal["development", "staging", "production"] = "development"

    # ── Server ─────────────────────────────────────────────────────
    host: str = "0.0.0.0"
    port: int = 8003
    cors_origins: list[str] = ["*"]

    # ── Database (shared PostgreSQL instance) ──────────────────────
    db_url: str = ""
    db_pool_size: int = 10
    db_max_overflow: int = 20

    # ── Redis (shared instance) ────────────────────────────────────
    redis_url: str = ""

    # ── OpenTelemetry ──────────────────────────────────────────────
    otel_exporter_otlp_endpoint: str = "http://localhost:4317"

    # ── Claude API ─────────────────────────────────────────────────
    anthropic_api_key: str = ""
    claude_default_model: str = "claude-haiku-4-5"

    # ── OpenAI (for embeddings in semantic mapping) ────────────────
    openai_api_key: str = ""
    embedding_provider: Literal["openai", "voyage"] = "openai"
    embedding_model: str = "text-embedding-3-small"

    # ── LangSmith (primary observability for this service) ─────────
    langsmith_api_key: str = ""
    langsmith_project: str = "cafm-ai-schema-mapper"
    langsmith_endpoint: str = "https://api.smith.langchain.com"
    langsmith_tracing: bool = True

    # ── Azure Blob ─────────────────────────────────────────────────
    azure_storage_connection_string: str = ""
    azure_blob_container_name: str = "plenum-agentic-ai-attachments"

    # ── svc-ingestion (handoff target) ────────────────────────────
    svc_ingestion_url: str = "http://svc-ingestion:8001"

    # ── Fiix CMMS Connector (optional platform schema mapping) ─────
    fiix_enabled: bool = False
    fiix_subdomain: str = ""
    fiix_app_key: str = ""
    fiix_access_key: str = ""
    fiix_secret_key: str = ""
    fiix_timeout: int = 3600
    # After a Fiix schema-mapping run completes (Node 8), automatically pull the
    # Fiix records (rows) into the freshly created schema. Without this the new
    # schema has the canonical structure but zero Fiix data. Set FIIX_AUTO_INGEST=false
    # to require a manual POST /api/fiix-ingestion instead.
    fiix_auto_ingest: bool = True

    # ── Migration limits ───────────────────────────────────────────
    max_file_size_mb: int = 500
    max_rows_per_table: int = 5_000_000
    max_unresolved_fields_before_error: int = 20


@lru_cache
def get_settings() -> Settings:
    """Get cached settings instance."""
    return Settings()
