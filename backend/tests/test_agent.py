from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any, ClassVar

import pytest

import backend.app.rag.agent as agent_module
from backend.app.config import Settings
from backend.app.rag.agent import HHDocsAgent
from backend.app.rag.store import RetrievedChunk


class FakeEmbedding:
    async def aget_query_embedding(self, query: str) -> list[float]:
        return [0.1] * 256


class FakeFunctionAgent:
    calls: ClassVar[list[dict[str, Any]]] = []

    def __init__(self, **kwargs: Any) -> None:
        type(self).calls.append(kwargs)
        self.tools = kwargs["tools"]

    def run(self, *, user_msg: str, chat_history: list[Any]) -> Any:
        async def execute() -> SimpleNamespace:
            await self.tools[0].acall("получить вакансии")
            return SimpleNamespace(response=SimpleNamespace(content="Ответ по документации"))

        return execute()


@pytest.mark.asyncio
async def test_agent_returns_sources_collected_inside_tool(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    prompt = tmp_path / "initial.md"
    prompt.write_text("Отвечай по документации.", encoding="utf-8")
    settings = Settings.model_validate(
        {
            "INTERNAL_API_TOKEN": "x" * 20,
            "DATABASE_URL": "postgresql+asyncpg://db",
            "MODEL_NAME": "test-model",
            "YANDEX_CLOUD_FOLDER": "folder",
            "YANDEX_CLOUD_API_KEY": "key",
            "PROMPT_PATH": str(prompt),
        }
    )

    async def fake_retrieve(*args: Any, **kwargs: Any) -> list[RetrievedChunk]:
        chunk = RetrievedChunk(
            text="GET /vacancies",
            score=0.9,
            metadata={"section": "get-vacancies", "endpoint": "GET /vacancies"},
        )
        return [
            chunk,
            RetrievedChunk(
                text="GET /areas",
                score=0.8,
                metadata={"section": "get-areas", "endpoint": "GET /areas"},
            ),
        ]

    monkeypatch.setattr(agent_module, "retrieve", fake_retrieve)
    monkeypatch.setattr(agent_module, "FunctionAgent", FakeFunctionAgent)
    llm = SimpleNamespace(astructured_predict=fake_endpoint_choice)
    hh_agent = HHDocsAgent(
        settings=settings,
        llm=llm,
        embedding=FakeEmbedding(),
        vector_store=object(),
        knowledge_version_id=1,
    )

    result = await hh_agent.answer(question="Как получить вакансии?", history=[])

    assert result.text == "Ответ по документации"
    assert result.sources[0]["section"] == "get-vacancies"
    assert result.sources[0]["endpoint"] == "GET /vacancies"
    assert FakeFunctionAgent.calls[-1]["initial_tool_choice"] == "search_hh_docs"


async def fake_endpoint_choice(*args: Any, **kwargs: Any) -> Any:
    from backend.app.rag.agent import _EndpointChoice

    return _EndpointChoice(index=0)
