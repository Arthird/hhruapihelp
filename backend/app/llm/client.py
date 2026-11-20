from __future__ import annotations

from llama_index.llms.openai_like import OpenAILike

from backend.app.config import Settings


def build_llm(settings: Settings) -> OpenAILike:
    api_key = settings.yandex_cloud_api_key.get_secret_value()
    if not api_key:
        raise ValueError("YANDEX_CLOUD_API_KEY is required")
    if not settings.yandex_cloud_folder:
        raise ValueError("YANDEX_CLOUD_FOLDER is required")
    if not settings.model_name:
        raise ValueError("MODEL_NAME is required")
    if not settings.model_name.startswith("gpt://"):
        raise ValueError(
            "MODEL_NAME must be a Yandex model URI, for example "
            f"gpt://{settings.yandex_cloud_folder}/yandexgpt/latest"
        )
    return OpenAILike(
        model=settings.model_name,
        api_base=settings.yandex_cloud_base_url,
        api_key=api_key,
        default_headers={
            "x-folder-id": settings.yandex_cloud_folder,
        },
        is_chat_model=True,
        is_function_calling_model=True,
        timeout=settings.backend_timeout_seconds,
        max_retries=2,
        temperature=0.1,
        context_window=32000,
    )
