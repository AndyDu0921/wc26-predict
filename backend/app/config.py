from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Annotated, Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

ROOT_DIR = Path(__file__).resolve().parents[2]
BACKEND_DIR = ROOT_DIR / "backend"
KNOWN_UNSAFE_ADMIN_TOKENS = {
    "",
    "admin",
    "change-me",
    "changeme",
    "change_me_to_random_32_chars",
    "secret",
}


def is_secure_admin_token(value: str) -> bool:
    token = str(value or "").strip()
    return len(token) >= 32 and token.lower() not in KNOWN_UNSAFE_ADMIN_TOKENS


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(
            str(BACKEND_DIR / ".env"),
            str(ROOT_DIR / ".env"),
            str(BACKEND_DIR / ".env.local"),
            str(ROOT_DIR / ".env.local"),
        ),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = "World Cup Predictor"
    environment: Literal["development", "test", "production"] = "development"
    api_prefix: str = "/api"
    timezone: str = "UTC"
    app_base_url: str = Field(default="http://127.0.0.1:8000", alias="APP_BASE_URL")

    postgres_url: str = Field(
        default="postgresql+asyncpg://worldcup:CHANGE_ME@localhost:5432/worldcup",
        alias="POSTGRES_URL",
    )
    redis_url: str = Field(default="redis://localhost:6379/0", alias="REDIS_URL")

    football_data_api_key: str | None = Field(default=None, alias="FOOTBALL_DATA_API_KEY")
    football_data_base_url: str = "https://api.football-data.org/v4"
    football_data_calls_per_minute: int = 10

    event_registry_api_key: str | None = Field(default=None, alias="EVENT_REGISTRY_API_KEY")
    event_registry_base_url: str = "https://eventregistry.org/api/v1"
    gdelt_base_url: str = "https://api.gdeltproject.org/api/v2/doc/doc"

    llm_provider: Literal["qwen", "deepseek", "zhipu"] = Field(default="deepseek", alias="LLM_PROVIDER")
    llm_base_url: str | None = Field(default=None, alias="LLM_BASE_URL")
    llm_api_key: str | None = Field(default=None, alias="LLM_API_KEY")
    llm_model: str = Field(default="deepseek-v4-pro", alias="LLM_MODEL")
    llm_timeout_seconds: int = 30
    llm_max_retries: int = 2
    odds_api_key: str | None = Field(default=None, alias="ODDS_API_KEY")
    api_football_key: str | None = Field(default=None, alias="API_FOOTBALL_KEY")
    apifootball_com_key: str | None = Field(default=None, alias="APIFOOTBALL_COM_KEY")
    sentry_dsn: str | None = Field(default=None, alias="SENTRY_DSN")

    object_storage_bucket: str | None = Field(default=None, alias="OBJECT_STORAGE_BUCKET")
    model_artifact_dir: Path = Field(default=BACKEND_DIR / "model_artifacts", alias="MODEL_ARTIFACT_DIR")

    admin_token: str = Field(default="CHANGE_ME_TO_RANDOM_32_CHARS", alias="ADMIN_TOKEN")

    @field_validator("admin_token", mode="after")
    @classmethod
    def _warn_default_admin_token(cls, value: str) -> str:
        if not is_secure_admin_token(value):
            import logging
            logging.getLogger("app.config").warning(
                "\n╔══════════════════════════════════════════════════════════════╗\n"
                "║  SECURITY WARNING: ADMIN_TOKEN is weak or a known default.   ║\n"
                "║  Generate a secure token and set it in your .env file:       ║\n"
                "║    python -c \"import secrets; print(secrets.token_urlsafe(32))\"  ║\n"
                "╚══════════════════════════════════════════════════════════════╝"
            )
        return value
    cors_origins: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: ["http://localhost:5173", "http://127.0.0.1:5173"],
        alias="CORS_ORIGINS",
    )

    embedding_mode: Literal["local", "api"] = Field(default="local", alias="EMBEDDING_MODE")
    default_competition_codes: list[str] = Field(
        default_factory=lambda: ["WC", "QCAF", "QAFC", "QCBL", "QCON", "QOFC", "QUFA", "EC", "PL", "PD", "BL1", "SA", "FL1", "CL"],
    )

    @field_validator("cors_origins", mode="before")
    @classmethod
    def parse_cors_origins(cls, value: object) -> object:
        if isinstance(value, str):
            stripped = value.strip()
            if stripped.startswith("["):
                import json

                return json.loads(stripped)
            return [item.strip() for item in stripped.split(",") if item.strip()]
        return value

    @property
    def sync_database_url(self) -> str:
        if self.postgres_url.startswith("postgresql+asyncpg://"):
            return self.postgres_url.replace("postgresql+asyncpg://", "postgresql+psycopg://", 1)
        if self.postgres_url.startswith("sqlite+aiosqlite://"):
            return self.postgres_url.replace("sqlite+aiosqlite://", "sqlite://", 1)
        return self.postgres_url


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    settings = Settings()
    settings.model_artifact_dir.mkdir(parents=True, exist_ok=True)
    return settings
