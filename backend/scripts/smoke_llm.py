from __future__ import annotations

import asyncio

from llama_index.core.agent import FunctionAgent
from llama_index.core.tools import FunctionTool

from backend.app.config import get_settings, load_env, validate_runtime_settings
from backend.app.llm.client import build_llm
from backend.app.rag.embeddings import build_embedding

SMOKE_TOOL_VALUE = "smoke-ok"


def build_smoke_tool():
    def get_smoke_value() -> str:
        """Returns the mandatory smoke-test value."""

        return SMOKE_TOOL_VALUE

    return FunctionTool.from_defaults(
        fn=get_smoke_value,
        name="get_smoke_value",
        description="Returns the mandatory smoke-test value. Call this tool before answering.",
    )


async def run_smoke() -> None:
    load_env()
    settings = get_settings()
    validate_runtime_settings(settings, require_llm=True)
    llm = build_llm(settings)
    embedding = build_embedding(settings)

    vector = await embedding.aget_query_embedding("smoke test")
    expected_dim = settings.embedding_dim
    if len(vector) != expected_dim:
        raise RuntimeError(
            f"Embedding dimension mismatch: expected {expected_dim}, got {len(vector)}"
        )

    agent = FunctionAgent(
        system_prompt=(
            "Call get_smoke_value and answer with exactly the value returned by the tool. "
            "Do not invent a value."
        ),
        tools=[build_smoke_tool()],
        llm=llm,
        streaming=False,
    )
    result = await agent.run(user_msg="Call get_smoke_value and report its result.")
    response = getattr(result, "response", result)
    content = getattr(response, "content", None) or str(response)
    if SMOKE_TOOL_VALUE not in content:
        raise RuntimeError("Tool calling smoke test failed: model did not use the tool result")
    print(f"tool_calling=ok embedding_dim={len(vector)} model={settings.model_name}")


if __name__ == "__main__":
    asyncio.run(run_smoke())
