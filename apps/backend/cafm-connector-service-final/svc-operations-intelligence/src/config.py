from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    db_url: str = Field("", validation_alias=AliasChoices("DB_URL", "DATABASE_URL", "db_url"))
    redis_url: str = Field("redis://localhost:6379/0", validation_alias=AliasChoices("REDIS_URL"))

    environment: str = "development"
    debug: bool = False
    service_name: str = "svc-operations-intelligence"
    service_port: int = 8009

    email_delivery_mode: str = Field(
        "platform",
        validation_alias=AliasChoices("EMAIL_DELIVERY_MODE", "email_delivery_mode"),
    )
    smtp_host: str = Field("", validation_alias=AliasChoices("SMTP_HOST", "smtp_host"))
    smtp_port: int = Field(587, validation_alias=AliasChoices("SMTP_PORT", "smtp_port"))
    smtp_user: str = Field("", validation_alias=AliasChoices("SMTP_USER", "smtp_user"))
    smtp_password: str = Field(
        "",
        validation_alias=AliasChoices("SMTP_PASSWORD", "smtp_password"),
    )
    smtp_from: str = Field(
        "",
        validation_alias=AliasChoices("SMTP_FROM", "smtp_from"),
    )
    smtp_use_tls: bool = Field(
        True,
        validation_alias=AliasChoices("SMTP_USE_TLS", "smtp_use_tls"),
    )
    smtp_use_ssl: bool = Field(
        False,
        validation_alias=AliasChoices("SMTP_USE_SSL", "smtp_use_ssl"),
    )
    email_dry_run: bool = Field(
        True,
        validation_alias=AliasChoices("EMAIL_DRY_RUN", "email_dry_run"),
    )

    # Microsoft Graph (preferred for M365 — app-only Mail.Send)
    azure_tenant_id: str = Field(
        "",
        validation_alias=AliasChoices("AZURE_TENANT_ID", "azure_tenant_id"),
    )
    azure_client_id: str = Field(
        "",
        validation_alias=AliasChoices("AZURE_CLIENT_ID", "azure_client_id"),
    )
    azure_client_secret: str = Field(
        "",
        validation_alias=AliasChoices("AZURE_CLIENT_SECRET", "azure_client_secret"),
    )
    outlook_user_mail: str = Field(
        "",
        validation_alias=AliasChoices("OUTLOOK_USER_MAIL", "outlook_user_mail", "SMTP_FROM"),
    )

    default_pm_email: str = "pm@example.com"
    senior_pm_email: str = "senior.pm@example.com"

    # Daily compliance/accreditation scan runs at this local hour. 2 = 02:00 local (PRD A1).
    compliance_scan_hour_local: int = 2
    default_country_code: str = "UK"
    auto_seed_uk_pack: bool = True
    auto_seed_uae_pack: bool = True
    auto_seed_us_pack: bool = True
    auto_migrate_on_startup: bool = True

    public_base_url: str = Field(
        "http://localhost:8009",
        validation_alias=AliasChoices("PUBLIC_BASE_URL", "public_base_url"),
    )
    frontend_public_url: str = Field(
        "http://localhost:3000",
        validation_alias=AliasChoices("FRONTEND_PUBLIC_URL", "frontend_public_url"),
    )
    approval_token_ttl_hours: int = 72
    # When true, scan auto-sends ladder emails via Graph/SMTP (or dry-run) after drafting.
    auto_send_ladder_emails: bool = Field(
        True,
        validation_alias=AliasChoices(
            "AUTO_SEND_LADDER_EMAILS",
            "auto_send_ladder_emails",
        ),
    )
    anthropic_api_key: str = Field(
        "",
        validation_alias=AliasChoices("ANTHROPIC_API_KEY", "anthropic_api_key"),
    )
    verification_dump_dir: str = Field(
        "/app/data/verification_dumps",
        validation_alias=AliasChoices("VERIFICATION_DUMP_DIR", "verification_dump_dir"),
    )
    # CCC register bot — Playwright live search + auto-verify on name match
    register_bot_enabled: bool = Field(
        True,
        validation_alias=AliasChoices("REGISTER_BOT_ENABLED", "register_bot_enabled"),
    )
    register_bot_auto_verify: bool = Field(
        True,
        validation_alias=AliasChoices(
            "REGISTER_BOT_AUTO_VERIFY",
            "register_bot_auto_verify",
        ),
    )
    register_bot_min_confidence: float = Field(
        0.85,
        validation_alias=AliasChoices(
            "REGISTER_BOT_MIN_CONFIDENCE",
            "register_bot_min_confidence",
        ),
    )

    # CCC §8.4 — public / government API verification.
    # EPC/DEC/TM44 need NO key — they verify via the public GOV.UK "Find an energy
    # certificate" reference-number lookup (see channels/public_apis.py:_govuk_energy).
    fca_api_key: str = Field(
        "",
        validation_alias=AliasChoices("FCA_API_KEY", "FCA_REGISTER_API_KEY", "fca_api_key"),
    )
    fca_api_email: str = Field(
        "",
        validation_alias=AliasChoices("FCA_API_EMAIL", "fca_api_email"),
    )
    companies_house_api_key: str = Field(
        "",
        validation_alias=AliasChoices(
            "COMPANIES_HOUSE_API_KEY",
            "companies_house_api_key",
        ),
    )

    # Feature C — Energy
    doc_rag_base_url: str = Field(
        "http://localhost:8004",
        validation_alias=AliasChoices("DOC_RAG_BASE_URL", "doc_rag_base_url"),
    )
    dcc_api_base_url: str = Field(
        "",
        validation_alias=AliasChoices("DCC_API_BASE_URL", "dcc_api_base_url"),
    )
    dcc_api_key: str = Field("", validation_alias=AliasChoices("DCC_API_KEY", "dcc_api_key"))
    # SAM.gov Entity Management API — official US federal register of entities doing
    # business with the government. Free key from https://sam.gov (Account Details ->
    # Request Public API Key). Used to verify a US vendor entity is registered and not
    # excluded/debarred. Without it, US entity checks report needs_config.
    sam_gov_api_key: str = Field(
        "",
        validation_alias=AliasChoices("SAM_GOV_API_KEY", "SAM_API_KEY", "sam_gov_api_key"),
    )
    dcc_api_timeout_seconds: float = 30.0
    dcc_simulate_fill: bool = Field(
        True,
        validation_alias=AliasChoices("DCC_SIMULATE_FILL", "dcc_simulate_fill"),
    )
    azure_storage_connection_string: str = Field(
        "",
        validation_alias=AliasChoices(
            "AZURE_STORAGE_CONNECTION_STRING",
            "azure_storage_connection_string",
        ),
    )
    azure_blob_container_name: str = Field(
        "plenum-agentic-ai-attachments",
        validation_alias=AliasChoices(
            "AZURE_BLOB_CONTAINER_NAME",
            "azure_blob_container_name",
        ),
    )

    class Config:
        env_file = ".env"
        extra = "ignore"


settings = Settings()
