from functools import lru_cache
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_prefix="SNOWFLAKE_", env_ignore_empty=True, extra="ignore"
    )

    account: str = "sfsehol-anthropic_snowflake_hackathon_fevwgp"
    user: str
    role: str | None = None
    warehouse: str | None = None
    database: str | None = None
    # "schema" shadows a BaseModel attribute, so map the env var explicitly
    schema_: str | None = Field(default=None, validation_alias="SNOWFLAKE_SCHEMA")
    auth: Literal["password", "keypair", "externalbrowser", "pat"] = "pat"
    password: str | None = Field(default=None, repr=False)
    private_key_path: str | None = None
    private_key_passphrase: str | None = Field(default=None, repr=False)
    token: str | None = Field(default=None, repr=False)
    app_schema: str = "SUPPLY_RECOVERY_V1"
    app_role: str = "PROJECT_RECOVERY_APP_V1"
    evaluator_role: str = "PROJECT_RECOVERY_EVALUATOR_V1"
    evaluator_user: str | None = None
    evaluator_token: str | None = Field(default=None, repr=False)
    search_service: str = "EVIDENCE_SEARCH"
    semantic_view: str = "RECOVERY_ANALYTICS"
    snowsight_url: str | None = None
    cortex_model: str = "claude-sonnet-4-6"
    rest_auth: Literal["pat", "session"] = "pat"
    rest_base_url: str | None = None
    statement_timeout_seconds: int = Field(default=45, ge=1, le=120)
    network_timeout_seconds: int = Field(default=30, ge=1, le=120)
    max_query_rows: int = Field(default=500, ge=1, le=2000)
    max_completion_tokens: int = Field(default=3000, ge=128, le=8192)
    company: str = "synthetic-company"
    scope: str = "shortage-recovery-v1"


class AppSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    cors_origins: str = "http://localhost:5173"
    project_store: str | None = None
    project_live_reviewer_id: str | None = None
    project_live_reviewer_token: str | None = Field(default=None, repr=False)


@lru_cache
def get_settings() -> Settings:
    return Settings()
