from __future__ import annotations

import pytest

from backend.app.config import Settings, validate_runtime_settings


def test_database_url_is_built_from_parts() -> None:
    settings = Settings.model_validate(
        {
            "INTERNAL_API_TOKEN": "x" * 20,
            "POSTGRES_USER": "user",
            "POSTGRES_PASSWORD": "secret",
            "POSTGRES_HOST": "localhost",
            "POSTGRES_PORT": 5432,
            "POSTGRES_DB": "db",
            "DATABASE_URL": "",
        }
    )
    assert settings.sqlalchemy_url == "postgresql+asyncpg://user:secret@localhost:5432/db"
    assert settings.sync_database_url == "postgresql+psycopg://user:secret@localhost:5432/db"


def test_missing_llm_settings_are_reported_without_values() -> None:
    settings = Settings.model_validate(
        {"INTERNAL_API_TOKEN": "x" * 20, "DATABASE_URL": "postgresql+asyncpg://db"}
    )
    with pytest.raises(ValueError) as exc:
        validate_runtime_settings(settings, require_llm=True)
    message = str(exc.value)
    assert "YANDEX_CLOUD_API_KEY" in message
    assert "MODEL_NAME" in message
    assert "secret" not in message


def test_short_internal_token_is_rejected() -> None:
    with pytest.raises(ValueError):
        Settings.model_validate({"INTERNAL_API_TOKEN": "short"})
