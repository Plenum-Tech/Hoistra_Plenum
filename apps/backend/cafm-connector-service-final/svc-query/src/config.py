"""
svc-query/src/config.py

Pydantic Settings for the CAFM Query Service.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ── App ───────────────────────────────────────────────────────────────
    app_name: str = "CAFM Query Service"
    app_version: str = "0.1.0"
    debug: bool = False
    environment: Literal["development", "staging", "production"] = "development"

    # ── Server ────────────────────────────────────────────────────────────
    host: str = "0.0.0.0"
    port: int = 8002
    cors_origins: list[str] = ["*"]

    # ── Database ──────────────────────────────────────────────────────────
    db_url: str = (
        "postgresql+asyncpg://cafm:cafm@localhost:5432/cafm_connectors"
    )
    db_pool_size: int = 5
    db_max_overflow: int = 10

    # ── Redis ─────────────────────────────────────────────────────────────
    redis_url: str = "redis://localhost:6379/0"

    # ── OTel ──────────────────────────────────────────────────────────────
    otel_exporter_otlp_endpoint: str = "http://localhost:4317"

    # ── Claude API ────────────────────────────────────────────────────────
    anthropic_api_key: str = ""
    claude_default_model: str = "claude-sonnet-4-6"
    claude_intent_model: str = "claude-haiku-4-5"


@lru_cache
def get_settings() -> Settings:
    return Settings()
