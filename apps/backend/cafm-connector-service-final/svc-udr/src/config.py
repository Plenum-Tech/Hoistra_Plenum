from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # Database — reads DB_URL (.env local) or DATABASE_URL (container env)
    db_url: str = Field(..., validation_alias=AliasChoices("DB_URL", "DATABASE_URL", "db_url"))

    # Target PostgreSQL schema
    db_schema: str = "plenum_cafm"

    # Anthropic — claude-haiku-4-5 for fast DB routing decisions
    anthropic_api_key: str = ""
    anthropic_model: str = "claude-haiku-4-5"

    # Safety limits
    max_query_rows: int = 500       # hard cap on result set size
    max_agent_iterations: int = 10  # max tool-use rounds per query

    class Config:
        env_file = ".env"
        extra = "ignore"


settings = Settings()
