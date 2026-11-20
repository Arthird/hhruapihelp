from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from dotenv import load_dotenv
from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]


def load_env() -> None:
    """Load repository-level .env without overriding real environment variables."""

    load_dotenv(REPOSITORY_ROOT / ".env", override=False)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=None,
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    max_api_base_url: str = "https://platform-api2.max.ru"
    max_bot_api_key: SecretStr | None = None
    max_root_ca_path: str | None = None

    backend_base_url: str = "http://127.0.0.1:8000"
    internal_api_token: SecretStr = Field(min_length=16)
    backend_timeout_seconds: float = Field(default=60.0, gt=0)

    postgres_db: str = "hh_api_help"
    postgres_user: str = "hh_api_help"
    postgres_password: SecretStr = SecretStr("")
    postgres_host: str = "db"
    postgres_port: int = 5432
    database_url: str = ""

    yandex_cloud_api_key: SecretStr = SecretStr("")
    yandex_cloud_base_url: str = "https://ai.api.cloud.yandex.net/v1"
    yandex_cloud_folder: str = ""
    model_name: str = ""
    embedding_model_name: str = "text-search-doc"
    embedding_dim: int = Field(default=256, gt=0)
    embedding_api_base: str = "https://ai.api.cloud.yandex.net/v1"

    knowledge_path: Path = Path("backend/data/hh_api.md")
    knowledge_source_path: Path = Path("doc_preparing/output/openapi.md")
    prompt_path: Path = Path("prompts/initial.md")
    rag_top_k: int = Field(default=5, ge=1, le=20)
    rag_chunk_size: int = Field(default=1200, ge=200)
    rag_chunk_overlap: int = Field(default=150, ge=0)
    pgvector_enabled: bool = True
    log_level: str = "INFO"

    @field_validator("database_url", mode="before")
    @classmethod
    def normalize_database_url(cls, value: object) -> str:
        if value is None:
            return ""
        return str(value)

    @property
    def sqlalchemy_url(self) -> str:
        if self.database_url:
            return self.database_url
        password = self.postgres_password.get_secret_value()
        return (
            f"postgresql+asyncpg://{self.postgres_user}:{password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )

    @property
    def sync_database_url(self) -> str:
        return self.sqlalchemy_url.replace("postgresql+asyncpg://", "postgresql+psycopg://", 1)

    @property
    def resolved_knowledge_path(self) -> Path:
        path = self.knowledge_path
        return path if path.is_absolute() else REPOSITORY_ROOT / path

    @property
    def resolved_knowledge_source_path(self) -> Path:
        path = self.knowledge_source_path
        return path if path.is_absolute() else REPOSITORY_ROOT / path

    @property
    def resolved_prompt_path(self) -> Path:
        path = self.prompt_path
        return path if path.is_absolute() else REPOSITORY_ROOT / path


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    load_env()
    return Settings()


def validate_runtime_settings(settings: Settings, *, require_llm: bool = False) -> None:
    required: dict[str, str] = {
        "INTERNAL_API_TOKEN": settings.internal_api_token.get_secret_value()
    }
    if require_llm:
        required.update(
            {
                "YANDEX_CLOUD_API_KEY": settings.yandex_cloud_api_key.get_secret_value(),
                "YANDEX_CLOUD_FOLDER": settings.yandex_cloud_folder,
                "MODEL_NAME": settings.model_name,
                "EMBEDDING_MODEL_NAME": settings.embedding_model_name,
            }
        )
    missing = [name for name, value in required.items() if not value]
    if missing:
        raise ValueError(f"Missing required environment variables: {', '.join(missing)}")


def ensure_database_url(settings: Settings) -> None:
    if not settings.database_url and not settings.postgres_password.get_secret_value():
        raise ValueError("DATABASE_URL or POSTGRES_PASSWORD must be set")


LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]
