from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any, ClassVar

import pytest

import backend.app.rag.agent as agent_module
from backend.app.config import Settings
from backend.app.rag.agent import HHDocsAgent, _RouteDecision
from backend.app.rag.store import RetrievedChunk


class FakeEmbedding:
    async def aget_query_embedding(self, query: str) -> list[float]:
        return [0.1] * 256


class FakeFunctionAgent:
    calls: ClassVar[list[dict[str, Any]]] = []
    tool_queries: ClassVar[list[str]] = []

    def __init__(self, **kwargs: Any) -> None:
        type(self).calls.append(kwargs)
        self.tools = kwargs["tools"]

    def run(self, *, user_msg: str, chat_history: list[Any]) -> Any:
        async def execute() -> SimpleNamespace:
            await self.tools[0].acall("исходный запрос")
            return SimpleNamespace(response=SimpleNamespace(content="Ответ по документации"))

        return execute()


class FakeLLM:
    def __init__(self, route: _RouteDecision | Exception) -> None:
        self.route = route
        self.route_calls: list[dict[str, Any]] = []
        self.chat_calls: list[list[Any]] = []

    async def astructured_predict(
        self,
        output_cls: type[Any],
        prompt: Any,
        **kwargs: Any,
    ) -> Any:
        if output_cls is _RouteDecision:
            self.route_calls.append(kwargs)
            if isinstance(self.route, Exception):
                raise self.route
            return self.route
        if output_cls.__name__ == "_EndpointChoice":
            return output_cls(index=0)
        raise AssertionError(f"Unexpected structured output: {output_cls}")

    async def achat(self, messages: list[Any]) -> Any:
        self.chat_calls.append(messages)
        return SimpleNamespace(message=SimpleNamespace(content="Ответ по истории"))


def make_settings(tmp_path: Path) -> Settings:
    prompt = tmp_path / "initial.md"
    prompt.write_text("Отвечай по документации.", encoding="utf-8")
    return Settings.model_validate(
        {
            "INTERNAL_API_TOKEN": "x" * 20,
            "DATABASE_URL": "postgresql+asyncpg://db",
            "MODEL_NAME": "test-model",
            "YANDEX_CLOUD_FOLDER": "folder",
            "YANDEX_CLOUD_API_KEY": "key",
            "PROMPT_PATH": str(prompt),
        }
    )


def make_agent(tmp_path: Path, route: _RouteDecision | Exception) -> tuple[HHDocsAgent, FakeLLM]:
    llm = FakeLLM(route)
    agent = HHDocsAgent(
        settings=make_settings(tmp_path),
        llm=llm,
        embedding=FakeEmbedding(),
        vector_store=object(),
        knowledge_version_id=1,
    )
    return agent, llm


@pytest.fixture
def fake_retrieve(monkeypatch: pytest.MonkeyPatch) -> None:
    async def retrieve(*args: Any, **kwargs: Any) -> list[RetrievedChunk]:
        return [
            RetrievedChunk(
                text="GET /vacancies",
                score=0.9,
                metadata={"section": "get-vacancies", "endpoint": "GET /vacancies"},
            )
        ]

    monkeypatch.setattr(agent_module, "retrieve", retrieve)
    monkeypatch.setattr(agent_module, "FunctionAgent", FakeFunctionAgent)
    FakeFunctionAgent.calls.clear()


@pytest.mark.asyncio
async def test_agent_returns_sources_collected_inside_tool(
    tmp_path: Path, fake_retrieve: None
) -> None:
    agent, _ = make_agent(
        tmp_path,
        _RouteDecision(intent="api_question", search_query="список вакансий"),
    )

    result = await agent.answer(question="Как получить вакансии?", history=[])

    assert result.text == "Ответ по документации"
    assert result.sources[0]["section"] == "get-vacancies"
    assert result.sources[0]["endpoint"] == "GET /vacancies"
    assert FakeFunctionAgent.calls[-1]["initial_tool_choice"] == "search_hh_docs"
    assert result.intent == "api_question"
    assert result.search_query == "список вакансий"


@pytest.mark.asyncio
async def test_clarification_uses_standalone_search_query(
    tmp_path: Path, fake_retrieve: None
) -> None:
    agent, llm = make_agent(
        tmp_path,
        _RouteDecision(
            intent="clarification",
            search_query="GET /employers/{employer_id}/tests пример ошибки",
        ),
    )

    await agent.answer(
        question="Приведи пример ошибки",
        history=[],
    )

    assert llm.route_calls[-1]["question"] == "Приведи пример ошибки"
    assert agent._endpoint_catalog is not None
    assert FakeFunctionAgent.calls[-1]["initial_tool_choice"] == "search_hh_docs"


@pytest.mark.asyncio
async def test_history_question_skips_retrieval_and_uses_history(tmp_path: Path) -> None:
    agent, llm = make_agent(
        tmp_path,
        _RouteDecision(intent="history_question", search_query=""),
    )
    history = [agent_module.ChatMessage(role="user", content="Привет")]

    result = await agent.answer(question="Какое моё предыдущее сообщение?", history=history)

    assert result.text == "Ответ по истории"
    assert result.sources == []
    assert result.intent == "history_question"
    assert len(llm.chat_calls) == 1
    assert [message.role.value for message in llm.chat_calls[0]] == ["system", "user", "user"]


@pytest.mark.asyncio
async def test_history_question_does_not_call_retrieval(tmp_path: Path) -> None:
    agent, _ = make_agent(
        tmp_path,
        _RouteDecision(intent="history_question", search_query=""),
    )

    async def fail_retrieve(*args: Any, **kwargs: Any) -> list[RetrievedChunk]:
        raise AssertionError("RAG must not be called for history questions")

    agent_module.retrieve = fail_retrieve
    result = await agent.answer(question="О чём мы говорили?", history=[])

    assert result.text == "Ответ по истории"
    assert result.sources == []


@pytest.mark.asyncio
async def test_off_topic_skips_retrieval(tmp_path: Path) -> None:
    agent, llm = make_agent(
        tmp_path,
        _RouteDecision(intent="off_topic", search_query="погода"),
    )

    result = await agent.answer(question="Какая погода?", history=[])

    assert result.intent == "off_topic"
    assert result.sources == []
    assert len(llm.chat_calls) == 1


@pytest.mark.asyncio
async def test_classification_failure_falls_back_to_rag(
    tmp_path: Path, fake_retrieve: None
) -> None:
    agent, _ = make_agent(tmp_path, RuntimeError("classifier down"))

    result = await agent.answer(question="Как получить вакансии?", history=[])

    assert result.intent == "api_question"
    assert result.search_query == "Как получить вакансии?"
    assert result.sources
    assert FakeFunctionAgent.calls[-1]["initial_tool_choice"] == "search_hh_docs"


@pytest.mark.asyncio
async def test_search_tool_uses_rewritten_query_over_model_argument(
    tmp_path: Path, fake_retrieve: None
) -> None:
    agent, llm = make_agent(
        tmp_path,
        _RouteDecision(
            intent="clarification",
            search_query="GET /employers/{employer_id}/tests ошибки",
        ),
    )
    embedding_queries: list[str] = []

    async def capture_embedding(query: str) -> list[float]:
        embedding_queries.append(query)
        return [0.1] * 256

    agent._embedding = SimpleNamespace(aget_query_embedding=capture_embedding)

    await agent.answer(question="А какие там ошибки?", history=[])

    tool = FakeFunctionAgent.calls[-1]["tools"][0]
    await tool.acall("нерелевантный запрос модели")

    assert "GET /employers/{employer_id}/tests ошибки" in embedding_queries
    assert llm.route_calls


@pytest.mark.asyncio
async def test_search_query_strips_control_characters(tmp_path: Path) -> None:
    agent, _ = make_agent(
        tmp_path,
        _RouteDecision(
            intent="clarification",
            search_query="GET \x07/employers\x07{employer_id}\x07/tests",
        ),
    )

    decision = await agent._classify("А какие там ошибки?", [])

    assert decision.search_query == "GET /employers {employer_id} /tests"
