from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest
import pytest_asyncio
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.config import Settings
from backend.app.db.models import KnowledgeVersion, LLMRun, Message
from backend.app.orchestrator.service import UpdateService


@dataclass
class FakeAgent:
    text: str = "Краткий ответ"
    sources: list[dict[str, object]] | None = None
    knowledge_version_id: int | None = 1

    async def answer(self, *, question: str, history: list[Any]) -> Any:
        from backend.app.rag.agent import AgentResult

        return AgentResult(
            text=self.text,
            sources=self.sources or [{"section": "get-vacancies", "endpoint": "GET /vacancies"}],
            prompt_version="test",
            latency_ms=5,
        )


class CountingFactory:
    def __init__(self, agent: FakeAgent) -> None:
        self.agent = agent
        self.calls = 0

    async def __call__(self) -> FakeAgent:
        self.calls += 1
        return self.agent


@pytest.fixture
def settings() -> Settings:
    return Settings.model_validate(
        {
            "INTERNAL_API_TOKEN": "x" * 20,
            "DATABASE_URL": "postgresql+asyncpg://db",
            "MODEL_NAME": "test-model",
            "YANDEX_CLOUD_FOLDER": "folder",
            "YANDEX_CLOUD_API_KEY": "key",
        }
    )


def make_payload(
    update_key: str = "message_created:1:mid-1",
) -> tuple[str, dict[str, Any]]:
    return (
        update_key,
        {
            "update_type": "message_created",
            "update_timestamp": 1,
            "chat": {"id": 10, "type": "dialog", "title": None},
            "user": {"id": 20, "first_name": "Иван", "is_bot": False},
            "message": {
                "id": "mid-1",
                "text": "Как получить вакансии?",
                "timestamp": 1,
            },
        },
    )


@pytest_asyncio.fixture
async def service(
    db_session: AsyncSession, settings: Settings
) -> tuple[UpdateService, CountingFactory]:
    version = KnowledgeVersion(
        source_path="hh_api.md",
        source_sha256="a" * 64,
        embedding_model="text-search-doc",
        embedding_dim=256,
        chunk_count=10,
        table_name="hh_docs_v1",
        status="active",
    )
    db_session.add(version)
    await db_session.flush()
    factory = CountingFactory(FakeAgent())
    return UpdateService(settings=settings, agent_factory=factory), factory


@pytest.mark.asyncio
async def test_duplicate_update_does_not_call_agent_twice(
    db_session: AsyncSession,
    service: tuple[UpdateService, CountingFactory],
) -> None:
    update_service, factory = service
    key, payload = make_payload()
    first = await update_service.process(db_session, update_key=key, payload=payload)
    await db_session.commit()
    second = await update_service.process(db_session, update_key=key, payload=payload)
    assert first.reply is not None
    assert second.reply is not None
    assert second.reply.text == first.reply.text
    assert factory.calls == 1
    count = await db_session.scalar(select(func.count()).select_from(Message))
    assert count == 2
    runs = await db_session.scalar(select(func.count()).select_from(LLMRun))
    assert runs == 1


@pytest.mark.asyncio
async def test_duplicate_update_replays_attachments(
    db_session: AsyncSession,
    service: tuple[UpdateService, CountingFactory],
) -> None:
    update_service, factory = service
    factory.agent.text = "Пример:\n```python\nprint('hello')\nprint('world')\n```"
    key, payload = make_payload("message_created:5:mid-5")

    first = await update_service.process(db_session, update_key=key, payload=payload)
    await db_session.commit()
    second = await update_service.process(db_session, update_key=key, payload=payload)

    assert first.reply is not None
    assert first.reply.attachments
    assert second.reply is not None
    assert second.reply.attachments == first.reply.attachments
    assert second.reply.text == first.reply.text
    assert factory.calls == 1


@pytest.mark.asyncio
async def test_delivered_duplicate_returns_no_reply(
    db_session: AsyncSession,
    service: tuple[UpdateService, CountingFactory],
) -> None:
    update_service, factory = service
    key, payload = make_payload("message_created:2:mid-2")
    await update_service.process(db_session, update_key=key, payload=payload)
    await db_session.commit()
    from backend.app.db.repositories import mark_update_delivered

    assert await mark_update_delivered(db_session, key) is True
    await db_session.commit()
    result = await update_service.process(db_session, update_key=key, payload=payload)
    assert result.reply is None
    assert result.already_delivered is True
    assert factory.calls == 1


@pytest.mark.asyncio
async def test_clear_creates_new_conversation_for_next_message(
    db_session: AsyncSession,
    service: tuple[UpdateService, CountingFactory],
) -> None:
    update_service, _ = service
    key, payload = make_payload("message_created:3:mid-3")
    await update_service.process(db_session, update_key=key, payload=payload)
    clear_payload = {
        **payload,
        "update_type": "dialog_cleared",
        "update_timestamp": 2,
        "message": None,
    }
    await update_service.process(db_session, update_key="dialog_cleared:2", payload=clear_payload)
    next_key, next_payload = make_payload("message_created:4:mid-4")
    result = await update_service.process(db_session, update_key=next_key, payload=next_payload)
    assert result.reply is not None
    from backend.app.db.models import Conversation

    active = await db_session.scalar(
        select(func.count()).select_from(Conversation).where(Conversation.status == "active")
    )
    assert active == 1


def test_format_for_max_removes_lists_and_moves_code_to_attachment() -> None:
    text = 'Параметры:\n- text — поиск\n1. page — страница\n```json\n{\n  "page": 0\n}\n```\n'
    formatted, attachments = UpdateService._format_for_max(
        text, [{"section": "get-vacancies", "endpoint": "GET /vacancies"}]
    )
    assert "text — поиск" in formatted
    assert "page — страница" in formatted
    assert "- text" not in formatted
    assert "```" not in formatted
    assert "example-1.json" in formatted
    assert attachments[0]["filename"] == "example-1.json"
    assert attachments[0]["content"] == '{\n  "page": 0\n}'
    assert "Источник" in formatted
