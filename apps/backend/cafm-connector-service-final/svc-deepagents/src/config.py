from pathlib import Path

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings

# Walk up from this file's location to find the nearest .env
# Works regardless of which directory uvicorn is launched from.
def _find_env_file() -> str | None:
    here = Path(__file__).resolve().parent
    for _ in range(6):
        candidate = here / ".env"
        if candidate.exists():
            return str(candidate)
        here = here.parent
    return None

_ENV_FILE = _find_env_file()


class Settings(BaseSettings):
    # Database
    db_url: str = Field(..., validation_alias=AliasChoices("DB_URL", "DATABASE_URL", "db_url"))

    # OpenAI — main orchestrator
    openai_api_key: str = Field("", validation_alias=AliasChoices("OPENAI_API_KEY", "openai_api_key"))
    openai_model: str = "gpt-4o-mini"
    openai_api_base: str = Field(
        "",
        validation_alias=AliasChoices("OPENAI_API_BASE", "OPENAI_BASE_URL", "OPENAI_API_BASE_URL"),
    )
    openai_ssl_verify: bool = Field(
        True,
        validation_alias=AliasChoices("OPENAI_SSL_VERIFY"),
    )
    # Cloud provider routing for orchestrator LLM:
    # - auto: use Azure when configured, else Tencent when configured, else OpenAI.
    # - azure: force Azure OpenAI path.
    # - tencent: force Tencent OpenAI-compatible path.
    # - openai: force public/custom OpenAI path.
    cloud_provider: str = Field(
        "auto",
        validation_alias=AliasChoices("CLOUD_PROVIDER"),
    )
    # Azure OpenAI (preferred in enterprise) — when endpoint is set, public api.openai.com is not used
    azure_openai_endpoint: str = Field(
        "",
        validation_alias=AliasChoices("AZURE_OPENAI_ENDPOINT", "AZURE_OPENAI_API_BASE"),
    )
    azure_openai_api_key: str = Field(
        "",
        validation_alias=AliasChoices("AZURE_OPENAI_API_KEY"),
    )
    azure_openai_deployment: str = Field(
        "",
        validation_alias=AliasChoices(
            "AZURE_OPENAI_DEPLOYMENT",
            "AZURE_OPENAI_DEPLOYMENT_NAME",
        ),
    )
    azure_openai_api_version: str = Field(
        "2024-08-01-preview",
        validation_alias=AliasChoices("AZURE_OPENAI_API_VERSION", "OPENAI_API_VERSION"),
    )
    # Tencent Cloud (OpenAI-compatible endpoint)
    tencent_openai_base_url: str = Field(
        "",
        validation_alias=AliasChoices(
            "TENCENT_OPENAI_BASE_URL",
            "TENCENT_API_BASE_URL",
        ),
    )
    tencent_openai_api_key: str = Field(
        "",
        validation_alias=AliasChoices(
            "TENCENT_OPENAI_API_KEY",
            "TENCENT_API_KEY",
        ),
    )
    tencent_openai_model: str = Field(
        "hunyuan-lite",
        validation_alias=AliasChoices("TENCENT_OPENAI_MODEL"),
    )

    # Anthropic — available for future subagent use
    anthropic_api_key: str = Field("", validation_alias=AliasChoices("ANTHROPIC_API_KEY", "anthropic_api_key"))


    # Model for the compliance portfolio summary. That call must apply compound row filters
    # ("forged AND still compliant") over the whole table, which the cheap routing model gets
    # wrong, so it runs on Claude independently of OPENAI_MODEL.
    compliance_summary_model: str = Field(
        "claude-opus-5",
        validation_alias=AliasChoices(
            "COMPLIANCE_SUMMARY_MODEL", "compliance_summary_model"
        ),
    )
    # Effort for the two heavy Claude calls. The analyst ran at "high" and was the single
    # largest block of a 75 s turn; "medium" is the measured default. Raise per deployment
    # if the reviewer starts sending answers back more often.
    compliance_analyst_effort: str = Field(
        "medium",
        validation_alias=AliasChoices("COMPLIANCE_ANALYST_EFFORT", "compliance_analyst_effort"),
    )
    compliance_review_effort: str = Field(
        "medium",
        validation_alias=AliasChoices("COMPLIANCE_REVIEW_EFFORT", "compliance_review_effort"),
    )
    # When true, the compliance flow also logs the FULL payloads at each stage: every DB row,
    # the complete system prompt + data JSON, and the full LLM answer. Off by default - those
    # lines are large and contain certificate detail.
    compliance_debug_payloads: bool = Field(
        False,
        validation_alias=AliasChoices(
            "COMPLIANCE_DEBUG_PAYLOADS", "compliance_debug_payloads"
        ),
    )
    # Activity log: every input/output message of the orchestrator and the compliance
    # stages, appended to plenum_cafm.agent_activity_log for troubleshooting. Payloads are
    # bounded (ACTIVITY_LOG_PAYLOAD_CHARS). See agents/activity_log.py.
    activity_log_enabled: bool = Field(
        True, validation_alias=AliasChoices("ACTIVITY_LOG_ENABLED", "activity_log_enabled")
    )
    activity_log_payload_chars: int = Field(
        64000,
        validation_alias=AliasChoices("ACTIVITY_LOG_PAYLOAD_CHARS", "activity_log_payload_chars"),
    )
    # Downstream service URLs
    # NOTE: UDR (user/data lookup) uses direct DB access — no HTTP svc-udr needed
    wo_engine_base_url: str = "http://localhost:8001"       # svc-ingestion (legacy alias kept)
    wo_management_base_url: str = "http://localhost:8007"   # svc-work-order-management
    operations_intelligence_base_url: str = Field(
        "http://localhost:8009",
        validation_alias=AliasChoices(
            "OPERATIONS_INTELLIGENCE_BASE_URL",
            "operations_intelligence_base_url",
        ),
    )
    udr_base_url: str = "http://localhost:8006"
    doc_rag_base_url: str = "http://localhost:8004"
    migration_base_url: str = "http://localhost:8003"
    deep_agents_upload_dir: str = "/tmp/deepagents_uploads"
    # Cumulative cap for a with-files migration upload, enforced (streamed) in the
    # run-stateful-with-files endpoint. Default aligns with the nginx gateway's client_max_body_size
    # (200m) so the front door, the gateway, and the client agree on one ceiling. Override via env.
    deep_agents_max_upload_mb: int = Field(
        200,
        validation_alias=AliasChoices("DEEP_AGENTS_MAX_UPLOAD_MB"),
    )
    ingest_batch_inline_threshold: int = Field(
        # Batches of THIS many files or fewer run inline (single-door flow) so per-file
        # compliance results render in the center chat; larger batches go async-bulk (only
        # a "Bulk ingest started" placeholder). The CCC §3.1 compliance batch max is 5, so
        # default 5 keeps typical certificate uploads showing their results inline.
        5,
        validation_alias=AliasChoices("INGEST_BATCH_INLINE_THRESHOLD"),
    )
    ingest_batch_concurrency: int = Field(
        4,
        validation_alias=AliasChoices("INGEST_BATCH_CONCURRENCY"),
    )
    doc_match_confidence_threshold: float = Field(
        0.25,
        validation_alias=AliasChoices("DOC_MATCH_CONFIDENCE_THRESHOLD"),
    )
    doc_match_max_rows_in_report: int = Field(
        20,
        validation_alias=AliasChoices("DOC_MATCH_MAX_ROWS_IN_REPORT"),
    )
    # Max approximate tokens of chat history fed to the model on each ReAct step.
    # Trims the oldest messages (system prompt + recent turns kept) so a long
    # multi-turn session never overflows the model context window (gpt-4o-mini = 128k).
    # Budget leaves headroom for the (large) tool schemas + completion.
    orchestrator_history_max_tokens: int = Field(
        48000,
        validation_alias=AliasChoices("ORCHESTRATOR_HISTORY_MAX_TOKENS"),
    )
    # Phase 6 — optional object-storage connectors (stubs until drivers wired)
    azure_storage_connection_string: str = Field(
        "",
        validation_alias=AliasChoices("AZURE_STORAGE_CONNECTION_STRING"),
    )
    azure_storage_account: str = Field(
        "",
        validation_alias=AliasChoices("AZURE_STORAGE_ACCOUNT"),
    )
    tencent_cos_secret_id: str = Field(
        "",
        validation_alias=AliasChoices("TENCENT_COS_SECRET_ID"),
    )
    tencent_cos_secret_key: str = Field(
        "",
        validation_alias=AliasChoices("TENCENT_COS_SECRET_KEY"),
    )

    # Service config
    port: int = 8008
    debug: bool = False
    # HITL: enables interrupt() gates in migration tools + Postgres checkpointer
    hitl_enabled: bool = True

    # LangSmith tracing (zero-instrumentation — set env vars and LangChain auto-traces)
    langsmith_api_key: str = Field("", validation_alias=AliasChoices("LANGSMITH_API_KEY", "langsmith_api_key"))
    langsmith_project: str = Field("cafm-deepagents", validation_alias=AliasChoices("LANGSMITH_PROJECT", "langsmith_project"))
    langsmith_tracing: bool = Field(False, validation_alias=AliasChoices("LANGSMITH_TRACING", "langsmith_tracing"))

    class Config:
        env_file = _ENV_FILE  # absolute path — works from any working directory
        extra = "ignore"


settings = Settings()
