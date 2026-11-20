from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request, status
from sqlalchemy import text

from backend.app.api.dependencies import require_internal_token
from backend.app.api.schemas import (
    ClearOut,
    DeliveredOut,
    HealthOut,
    UpdateIn,
    UpdateOut,
)
from backend.app.config import (
    ensure_database_url,
    get_settings,
    validate_runtime_settings,
)
from backend.app.db import repositories as repo
from backend.app.db.session import create_engine, create_session_factory
from backend.app.llm.client import build_llm
from backend.app.logging import configure_logging, get_logger
from backend.app.orchestrator.service import UpdateService
from backend.app.rag.agent import HHDocsAgent
from backend.app.rag.embeddings import build_embedding
from backend.app.rag.store import create_vector_store

logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    configure_logging(settings.log_level)
    validate_runtime_settings(settings, require_llm=False)
    ensure_database_url(settings)

    engine = create_engine(settings)
    session_factory = create_session_factory(engine)
    app.state.settings = settings
    app.state.engine = engine
    app.state.session_factory = session_factory
    app.state.agent_cache = None

    async def build_agent() -> HHDocsAgent:
        if app.state.agent_cache is not None:
            return app.state.agent_cache
        validate_runtime_settings(settings, require_llm=True)
        async with session_factory() as session:
            version = await repo.get_active_knowledge_version(session)
            if version is None:
                raise RuntimeError(
                    "No active knowledge version. Run scripts/sync_knowledge.py first."
                )
            agent = HHDocsAgent(
                settings=settings,
                llm=build_llm(settings),
                embedding=build_embedding(settings),
                vector_store=create_vector_store(settings, version.table_name),
                knowledge_version_id=version.id,
            )
            app.state.agent_cache = agent
            return agent

    app.state.agent_factory = build_agent
    app.state.service = UpdateService(settings=settings, agent_factory=build_agent)
    logger.info("backend_started")
    try:
        yield
    finally:
        await engine.dispose()
        logger.info("backend_stopped")


app = FastAPI(title="HH API Help Bot", version="0.1.0", lifespan=lifespan)


@app.get("/health", response_model=HealthOut)
async def health(request: Request) -> HealthOut:
    database_ok = False
    version_id = None
    try:
        async with request.app.state.session_factory() as session:
            await session.execute(text("SELECT 1"))
            version = await repo.get_active_knowledge_version(session)
            version_id = version.id if version is not None else None
            database_ok = True
    except Exception:
        logger.exception("health_database_failed")
    return HealthOut(
        status="ok" if database_ok else "degraded",
        database=database_ok,
        knowledge_version_id=version_id,
    )


@app.post(
    "/internal/v1/updates",
    response_model=UpdateOut,
    dependencies=[Depends(require_internal_token)],
)
async def process_update(
    payload: UpdateIn,
    request: Request,
    idempotency_key: str = Header(alias="Idempotency-Key"),
) -> UpdateOut:
    if not idempotency_key.strip():
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Empty key")
    service: UpdateService = request.app.state.service
    async with request.app.state.session_factory() as session:
        try:
            result = await service.process(
                session,
                update_key=idempotency_key,
                payload=payload.model_dump(),
            )
            await session.commit()
        except Exception:
            await session.rollback()
            logger.exception("update_processing_failed", update_key=idempotency_key)
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Update processing failed",
            ) from None
    if result.reply is None:
        return UpdateOut(reply=None, already_delivered=result.already_delivered)
    return UpdateOut(
        reply={
            "text": result.reply.text,
            "format": result.reply.format,
            "attachments": result.reply.attachments,
        }
    )


@app.post(
    "/internal/v1/updates/{update_key}/delivered",
    response_model=DeliveredOut,
    dependencies=[Depends(require_internal_token)],
)
async def mark_delivered(update_key: str, request: Request) -> DeliveredOut:
    async with request.app.state.session_factory() as session:
        delivered = await repo.mark_update_delivered(session, update_key)
        await session.commit()
    return DeliveredOut(delivered=delivered)


@app.post(
    "/internal/v1/actions/clear",
    response_model=ClearOut,
    dependencies=[Depends(require_internal_token)],
)
async def clear_context(
    request: Request,
    chat_id: int = Query(gt=0),
    user_id: int = Query(gt=0),
) -> ClearOut:
    async with request.app.state.session_factory() as session:
        chat = await _find_chat(session, chat_id)
        if chat is None:
            return ClearOut(closed=0)
        closed = await repo.close_active_conversations(
            session, chat_id=chat.id, user_id=None, reason="cleared"
        )
        await session.commit()
    return ClearOut(closed=closed)


async def _find_chat(session: Any, max_chat_id: int) -> Any:
    from sqlalchemy import select

    from backend.app.db.models import MaxChat

    return await session.scalar(select(MaxChat).where(MaxChat.max_chat_id == max_chat_id))
