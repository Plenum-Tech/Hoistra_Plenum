from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # Database — reads DB_URL (.env local) or DATABASE_URL (container env)
    db_url: str = Field("", validation_alias=AliasChoices("DB_URL", "DATABASE_URL", "db_url"))

    # Default org ID — must match an existing integer organization_id in plenum_cafm.organizations
    default_organization_id: str = "1"

    # OpenAI
    openai_api_key: str = ""
    # Swap to any model name: gpt-4o-mini, gpt-4.1-mini, gpt-4.1, etc.
    openai_model: str = "gpt-4o-mini"

    # Outlook / Microsoft Graph API — client credentials (app-only, non-expiring)
    # Register an app in Azure Entra ID and grant Mail.Read + Mail.Send + Mail.ReadWrite
    # Application permissions, then admin-consent them.  Token is fetched automatically.
    azure_tenant_id: str = ""
    azure_client_id: str = ""
    azure_client_secret: str = ""
    outlook_user_email: str = Field(
        "",
        validation_alias=AliasChoices(
            "OUTLOOK_USER_EMAIL",
            "OUTLOOK_USER_MAIL",
            "outlook_user_email",
        ),
    )
    # Background inbox poll for WO approval replies (Graph + OpenAI per unread mail).
    # Keep false in dev unless actively testing email approvals; use POST /api/email/poll manually.
    approval_email_poll_enabled: bool = Field(
        False,
        validation_alias=AliasChoices(
            "APPROVAL_EMAIL_POLL_ENABLED",
            "approval_email_poll_enabled",
        ),
    )
    # Default 3600 = once per hour when APPROVAL_EMAIL_POLL_ENABLED=true
    approval_email_poll_interval_seconds: int = Field(
        3600,
        ge=60,
        validation_alias=AliasChoices(
            "APPROVAL_EMAIL_POLL_INTERVAL_SECONDS",
            "approval_email_poll_interval_seconds",
        ),
    )

    # AIMMS internal
    aimms_api_url: str = ""
    aimms_api_key: str = ""
    aimms_url: str = ""

    # CMMS (Maximo / SAP PM)
    cmms_api_url: str = ""
    cmms_api_key: str = ""
    cmms_integration_enabled: bool = False

    # BMS
    bms_api_url: str = ""

    class Config:
        env_file = ".env"
        extra = "ignore"


settings = Settings()
