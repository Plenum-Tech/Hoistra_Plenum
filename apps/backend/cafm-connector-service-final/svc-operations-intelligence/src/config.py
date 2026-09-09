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
    # Demo portfolio rows for plenum_cafm.sites (seeds/portfolio_buildings.sql). Off = the
    # Buildings table shows exactly what the sites table holds.
    auto_seed_portfolio_buildings: bool = Field(
        True,
        validation_alias=AliasChoices("AUTO_SEED_PORTFOLIO_BUILDINGS", "auto_seed_portfolio_buildings"),
    )
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

    # ── Authentication ──────────────────────────────────────────────────────────────
    #
    # Email is the account identifier: there is no separate username to lose or collide.
    #
    # Both secrets below are DELIBERATELY empty by default rather than carrying a
    # development fallback. A shipped default signing key is the same key in every
    # deployment that forgot to set it, and anyone holding it can mint a token for any
    # account. Empty means the service refuses to start in production and generates an
    # ephemeral per-process key in development — where restarting invalidates every
    # token, which is annoying exactly often enough to be noticed and set properly.
    auth_jwt_secret: str = Field(
        "",
        validation_alias=AliasChoices("AUTH_JWT_SECRET", "JWT_SECRET", "auth_jwt_secret"),
    )
    auth_jwt_algorithm: str = Field(
        "HS256",
        validation_alias=AliasChoices("AUTH_JWT_ALGORITHM", "auth_jwt_algorithm"),
    )
    # Keyed hash for one-time codes. Separate from the signing key so that disclosure of
    # one does not hand over the other, and so the signing key can be rotated (ending
    # sessions) without invalidating every code in flight, or the reverse.
    auth_otp_pepper: str = Field(
        "",
        validation_alias=AliasChoices("AUTH_OTP_PEPPER", "auth_otp_pepper"),
    )

    # Short, because an access token cannot be revoked — it is only ever outlived.
    auth_access_token_ttl_minutes: int = Field(
        30,
        validation_alias=AliasChoices("AUTH_ACCESS_TOKEN_TTL_MINUTES", "auth_access_token_ttl_minutes"),
    )
    # Long, because a refresh token IS a row and can be revoked the moment it needs to be.
    auth_refresh_token_ttl_days: int = Field(
        14,
        validation_alias=AliasChoices("AUTH_REFRESH_TOKEN_TTL_DAYS", "auth_refresh_token_ttl_days"),
    )

    auth_otp_length: int = Field(
        6,
        validation_alias=AliasChoices("AUTH_OTP_LENGTH", "auth_otp_length"),
    )
    # Long enough to fetch an email, short enough that a code read over someone's shoulder
    # is worthless by the time it is typed somewhere else.
    auth_otp_ttl_minutes: int = Field(
        10,
        validation_alias=AliasChoices("AUTH_OTP_TTL_MINUTES", "auth_otp_ttl_minutes"),
    )
    # Six digits is a million combinations, which is a great many at one guess per request
    # and none at all without a cap.
    auth_otp_max_attempts: int = Field(
        5,
        validation_alias=AliasChoices("AUTH_OTP_MAX_ATTEMPTS", "auth_otp_max_attempts"),
    )
    # Resending is also an attack: on the mailbox owner, whose inbox fills, and on the
    # code space, since every send is a fresh million-to-one draw.
    auth_otp_resend_cooldown_seconds: int = Field(
        60,
        validation_alias=AliasChoices("AUTH_OTP_RESEND_COOLDOWN_SECONDS", "auth_otp_resend_cooldown_seconds"),
    )
    auth_otp_max_per_hour: int = Field(
        5,
        validation_alias=AliasChoices("AUTH_OTP_MAX_PER_HOUR", "auth_otp_max_per_hour"),
    )

    # NIST SP 800-63B: length is what matters; composition rules push people towards
    # Passw0rd! and no further.
    auth_password_min_length: int = Field(
        12,
        validation_alias=AliasChoices("AUTH_PASSWORD_MIN_LENGTH", "auth_password_min_length"),
    )
    auth_login_max_failures: int = Field(
        8,
        validation_alias=AliasChoices("AUTH_LOGIN_MAX_FAILURES", "auth_login_max_failures"),
    )
    auth_lockout_minutes: int = Field(
        15,
        validation_alias=AliasChoices("AUTH_LOCKOUT_MINUTES", "auth_lockout_minutes"),
    )

    # Which organisation a self-registered account joins. Left empty, registration uses
    # the only organisation on the platform, and refuses to guess when there is more than
    # one — putting a new account in the wrong tenant is not a mistake that announces
    # itself.
    auth_default_organization_id: str = Field(
        "",
        validation_alias=AliasChoices("AUTH_DEFAULT_ORGANIZATION_ID", "auth_default_organization_id"),
    )
    # Open sign-up. Off means an account can only be created by an existing operator.
    auth_allow_self_registration: bool = Field(
        True,
        validation_alias=AliasChoices("AUTH_ALLOW_SELF_REGISTRATION", "auth_allow_self_registration"),
    )

    class Config:
        env_file = ".env"
        extra = "ignore"


settings = Settings()
