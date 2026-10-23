from functools import lru_cache
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="SNOWFLAKE_", env_ignore_empty=True, extra="ignore")

    account: str
    user: str
    role: str | None = None
    warehouse: str | None = None
    database: str | None = None
    # "schema" shadows a BaseModel attribute, so map the env var explicitly
    schema_: str | None = Field(default=None, validation_alias="SNOWFLAKE_SCHEMA")
    auth: Literal["password", "keypair", "externalbrowser", "pat"] = "pat"
    password: str | None = None
    private_key_path: str | None = None
    private_key_passphrase: str | None = None
    token: str | None = None


class AppSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    cors_origins: str = "http://localhost:5173"


@lru_cache
def get_settings() -> Settings:
    return Settings()
