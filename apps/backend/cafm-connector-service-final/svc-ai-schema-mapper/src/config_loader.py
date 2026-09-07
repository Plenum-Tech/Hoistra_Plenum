"""Load configuration from config.toml and .env files."""

import os
import tomllib
from pathlib import Path
from typing import Any, Dict, Optional
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """Load settings from .env (secrets) + config.toml (app config)."""

    # API Keys (from .env)
    anthropic_api_key: str
    openai_api_key: str
    langsmith_api_key: Optional[str] = None

    # Database (from .env)
    db_url: str
    db_pool_size: int = 10
    db_max_overflow: int = 5

    # Redis (from .env)
    redis_url: Optional[str] = None
    job_concurrency: int = 5

    # Azure (from .env)
    azure_storage_connection_string: Optional[str] = None
    azure_blob_container_name: str = "plenum-agentic-ai-attachments"

    # Fiix (from .env)
    fiix_enabled: bool = False
    fiix_subdomain: Optional[str] = None
    fiix_app_key: Optional[str] = None
    fiix_access_key: Optional[str] = None
    fiix_secret_key: Optional[str] = None
    fiix_timeout: int = 3600

    # Service URLs (from .env)
    svc_ingestion_url: str = "http://svc-ingestion:8001"
    svc_query_url: str = "http://svc-query:8002"

    # App Settings (from config.toml or .env)
    app_name: str = "CAFM AI Platform"
    app_version: str = "1.0.0"
    environment: str = "development"
    debug: bool = True
    log_level: str = "INFO"

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        case_sensitive = False


def load_config_toml(config_path: str = "config.toml") -> Dict[str, Any]:
    """
    Load configuration from TOML file.

    Args:
        config_path: Path to config.toml file

    Returns:
        Dictionary with full config structure
    """
    config_file = Path(config_path)

    if not config_file.exists():
        print(f"⚠️  Config file not found: {config_path}")
        return {}

    with open(config_file, "rb") as f:
        config = tomllib.load(f)

    return config


def get_settings() -> Settings:
    """
    Get application settings.
    Combines .env (secrets) + config.toml (structured config).

    Returns:
        Settings object with all configuration
    """
    settings = Settings()
    return settings


def get_config() -> Dict[str, Any]:
    """
    Get full TOML configuration.

    Returns:
        Dictionary with app config (doesn't include secrets)
    """
    return load_config_toml("config.toml")


# ── Usage Examples ──────────────────────────────────────────────────────

if __name__ == "__main__":
    # Load settings (from .env)
    settings = get_settings()
    print(f"App: {settings.app_name} v{settings.app_version}")
    print(f"Environment: {settings.environment}")
    print(f"Debug: {settings.debug}")

    # Load config (from config.toml)
    config = get_config()
    print(f"\nServer: {config['server']}")
    print(f"Database pool size: {config['database']['pool_size']}")
    print(f"Claude model: {config['claude']['default_model']}")
    print(f"Fiix enabled: {config['fiix']['enabled']}")
