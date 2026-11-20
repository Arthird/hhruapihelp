from __future__ import annotations

import asyncio
import time
from collections.abc import Sequence

from grpc import StatusCode
from llama_index.core.base.embeddings.base import BaseEmbedding
from llama_index.core.bridge.pydantic import Field, PrivateAttr
from llama_index.embeddings.openai import OpenAIEmbedding

from backend.app.config import Settings

_EMBEDDING_REQUESTS_PER_SECOND = 8.0
_EMBEDDING_BATCH_SIZE = 8
_embedding_lock = asyncio.Lock()
_last_embedding_request = 0.0
_RATE_LIMIT_RETRIES = 5


async def _respect_embedding_rate_limit() -> None:
    global _last_embedding_request
    async with _embedding_lock:
        now = time.monotonic()
        delay = (1.0 / _EMBEDDING_REQUESTS_PER_SECOND) - (now - _last_embedding_request)
        if delay > 0:
            await asyncio.sleep(delay)
        _last_embedding_request = time.monotonic()


class YandexAIStudioEmbedding(BaseEmbedding):
    """LlamaIndex embedding adapter for Yandex AI Studio text-search models."""

    model_name: str = Field(default="text-search-doc")
    dimensions: int = Field(default=256, gt=0)
    _sdk: object = PrivateAttr()

    def __init__(self, *, api_key: str, folder_id: str, model_name: str, dimensions: int) -> None:
        super().__init__(
            model_name=model_name,
            dimensions=dimensions,
            embed_batch_size=_EMBEDDING_BATCH_SIZE,
        )
        from yandex_ai_studio_sdk import AIStudio

        self._sdk = AIStudio(auth=api_key, folder_id=folder_id)

    @classmethod
    def class_name(cls) -> str:
        return "YandexAIStudioEmbedding"

    @property
    def embed_dim(self) -> int:
        return self.dimensions

    def _embed(self, text: str) -> list[float]:
        model = self._sdk.models.text_embeddings(self.model_name)
        result = model.run(text)
        return list(result.embedding)

    def _embed_many(self, texts: Sequence[str], *, query: bool) -> list[list[float]]:
        model_name = self.model_name.replace("doc", "query") if query else self.model_name
        model = self._sdk.models.text_embeddings(model_name)
        embeddings: list[list[float]] = []
        for text in texts:
            for attempt in range(_RATE_LIMIT_RETRIES):
                try:
                    embeddings.append(list(model.run(text).embedding))
                    break
                except Exception as exc:
                    if not _is_rate_limit_error(exc) or attempt == _RATE_LIMIT_RETRIES - 1:
                        raise
                    time.sleep(1.5 * (attempt + 1))
        return embeddings

    def _get_text_embedding(self, text: str) -> list[float]:
        return self._embed(text)

    def _get_query_embedding(self, query: str) -> list[float]:
        query_model = self.model_name.replace("doc", "query")
        model = self._sdk.models.text_embeddings(query_model)
        result = model.run(query)
        return list(result.embedding)

    async def _aget_text_embedding(self, text: str) -> list[float]:
        await _respect_embedding_rate_limit()
        return await asyncio.to_thread(self._get_text_embedding, text)

    async def _aget_query_embedding(self, query: str) -> list[float]:
        await _respect_embedding_rate_limit()
        return await asyncio.to_thread(self._get_query_embedding, query)

    def _get_text_embeddings(self, texts: Sequence[str]) -> list[list[float]]:
        return self._embed_many(texts, query=False)

    async def _aget_text_embeddings(self, texts: Sequence[str]) -> list[list[float]]:
        for _ in texts:
            await _respect_embedding_rate_limit()
        return await asyncio.to_thread(self._get_text_embeddings, texts)


def _is_rate_limit_error(error: Exception) -> bool:
    return getattr(error, "code", lambda: None)() == StatusCode.RESOURCE_EXHAUSTED


def build_embedding(settings: Settings) -> BaseEmbedding:
    api_key = settings.yandex_cloud_api_key.get_secret_value()
    if not api_key:
        raise ValueError("YANDEX_CLOUD_API_KEY is required for embeddings")
    if settings.embedding_model_name.startswith(("text-search-", "emb://")):
        return YandexAIStudioEmbedding(
            api_key=api_key,
            folder_id=settings.yandex_cloud_folder,
            model_name=settings.embedding_model_name,
            dimensions=settings.embedding_dim,
        )
    return OpenAIEmbedding(
        model=settings.embedding_model_name,
        api_key=api_key,
        api_base=settings.embedding_api_base,
        dimensions=settings.embedding_dim,
        timeout=settings.backend_timeout_seconds,
    )
