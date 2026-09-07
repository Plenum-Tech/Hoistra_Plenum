"""Application configuration — loaded from environment variables / .env file."""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ── App ───────────────────────────────────────────────────────
    app_name: str = "CAFM Connector Service"
    app_version: str = "0.1.0"
    debug: bool = False
    environment: Literal["development", "staging", "production"] = "development"

    # ── API server ────────────────────────────────────────────────
    host: str = "0.0.0.0"
    port: int = 8000
    api_prefix: str = "/api/v1"
    cors_origins: list[str] = ["*"]

    # ── JWT auth ──────────────────────────────────────────────────
    jwt_secret: str = "change-me-in-production"
    jwt_algorithm: str = "HS256"
    jwt_expire_minutes: int = 60

    # ── Primary database (PostgreSQL) ─────────────────────────────
    # Stores connector configs, import jobs, field maps, assets
    db_url: str = ""  # set DB_URL — no production default in source
    db_pool_size: int = 10
    db_max_overflow: int = 20

    # ── Redis / job queue ─────────────────────────────────────────
    redis_url: str = "redis://localhost:6379/0"  # set REDIS_URL
    job_concurrency: int = 5          # max parallel import jobs (per spec)
    job_timeout_seconds: int = 3600   # 1 hour hard cap per job

    # ── Secrets backend ───────────────────────────────────────────
    # "env"   → credentials stored as encrypted env vars (dev default)
    # "vault" → HashiCorp Vault (production)
    secrets_backend: Literal["env", "vault"] = "env"
    secrets_aes_key: str = Field(
        default="00000000000000000000000000000000",  # 32-byte hex; MUST override
        description="AES-256 key (hex) used to encrypt connector credentials at rest",
    )

    # Vault settings (ignored when secrets_backend == "env")
    vault_url: str = "http://localhost:8200"
    vault_token: str = ""
    vault_mount_path: str = "secret"
    vault_connector_path: str = "cafm/connectors"

    # ── QR code storage ───────────────────────────────────────────
    qr_storage_backend: Literal["local", "s3"] = "local"
    qr_local_dir: str = "/tmp/cafm_qr"
    qr_s3_bucket: str = ""
    qr_s3_prefix: str = "qr/"

    # ── Azure Blob Storage (file uploads) ─────────────────────────
    azure_storage_connection_string: str = (
        "DefaultEndpointsProtocol=https;AccountName=plenumstorage;"
        "AccountKey=CHANGE_ME;"  # set AZURE_STORAGE_CONNECTION_STRING; no live key in source
        "EndpointSuffix=core.windows.net"
    )
    azure_blob_container_name: str = "plenum-agentic-ai-attachments"
    azure_blob_account_name: str = "plenumstorage"
    # Sub-folder inside the container where imported source files are stored
    azure_blob_uploads_prefix: str = "cafm-imports/"

    # ── Import tuning ─────────────────────────────────────────────
    import_preview_rows: int = 50
    import_streaming_batch_size: int = 1000
    duplicate_hash_fields: list[str] = ["asset_id", "serial_no", "name"]


@lru_cache
def get_settings() -> Settings:
    return Settings()
