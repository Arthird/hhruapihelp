from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import delete, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.db.models import (
    Conversation,
    Feedback,
    KnowledgeVersion,
    LLMRun,
    MaxChat,
    MaxUser,
    Message,
    ProcessedUpdate,
)


def utcnow() -> datetime:
    return datetime.now(UTC)


async def upsert_user(
    session: AsyncSession,
    *,
    max_user_id: int,
    first_name: str | None,
    last_name: str | None,
    username: str | None,
    is_bot: bool,
    locale: str | None = None,
) -> MaxUser:
    user = await session.scalar(select(MaxUser).where(MaxUser.max_user_id == max_user_id))
    now = utcnow()
    if user is None:
        user = MaxUser(
            max_user_id=max_user_id,
            first_name=first_name,
            last_name=last_name,
            username=username,
            is_bot=is_bot,
            locale=locale,
            first_seen_at=now,
            last_seen_at=now,
        )
        session.add(user)
        await session.flush()
        return user
    user.first_name = first_name or user.first_name
    user.last_name = last_name or user.last_name
    user.username = username or user.username
    user.is_bot = is_bot
    user.locale = locale or user.locale
    user.last_seen_at = now
    await session.flush()
    return user


async def upsert_chat(
    session: AsyncSession,
    *,
    max_chat_id: int,
    chat_type: str,
    title: str | None,
    added_by_user_id: int | None = None,
    status: str = "active",
) -> MaxChat:
    chat = await session.scalar(select(MaxChat).where(MaxChat.max_chat_id == max_chat_id))
    now = utcnow()
    if chat is None:
        chat = MaxChat(
            max_chat_id=max_chat_id,
            chat_type=chat_type,
            title=title,
            status=status,
            added_by_user_id=added_by_user_id,
            first_seen_at=now,
            last_seen_at=now,
        )
        session.add(chat)
        await session.flush()
        return chat
    chat.chat_type = chat_type
    chat.title = title if title is not None else chat.title
    chat.status = status
    chat.last_seen_at = now
    if added_by_user_id is not None:
        chat.added_by_user_id = added_by_user_id
    if status == "removed" and chat.removed_at is None:
        chat.removed_at = now
    await session.flush()
    return chat


async def get_or_create_active_conversation(
    session: AsyncSession,
    *,
    chat_id: int,
    user_id: int,
) -> Conversation:
    conversation = await session.scalar(
        select(Conversation).where(
            Conversation.chat_id == chat_id,
            Conversation.user_id == user_id,
            Conversation.status == "active",
        )
    )
    if conversation is not None:
        conversation.last_activity_at = utcnow()
        return conversation
    conversation = Conversation(
        chat_id=chat_id,
        user_id=user_id,
        status="active",
        started_at=utcnow(),
        last_activity_at=utcnow(),
    )
    session.add(conversation)
    await session.flush()
    return conversation


async def close_active_conversations(
    session: AsyncSession,
    *,
    chat_id: int,
    user_id: int | None = None,
    reason: str,
) -> int:
    query = select(Conversation).where(
        Conversation.chat_id == chat_id, Conversation.status == "active"
    )
    if user_id is not None:
        query = query.where(Conversation.user_id == user_id)
    conversations = list((await session.scalars(query)).all())
    now = utcnow()
    for conversation in conversations:
        conversation.status = "closed" if reason == "cleared" else "removed"
        conversation.close_reason = reason
        conversation.closed_at = now
    await session.flush()
    return len(conversations)


async def add_inbound_message(
    session: AsyncSession,
    *,
    conversation: Conversation,
    chat_db_id: int,
    user_db_id: int,
    max_message_id: str | None,
    update_key: str,
    text: str,
) -> Message:
    message = Message(
        conversation_id=conversation.id,
        chat_id=chat_db_id,
        user_id=user_db_id,
        direction="inbound",
        max_message_id=max_message_id,
        update_key=update_key,
        text=text,
        text_length=len(text),
        status="received",
    )
    session.add(message)
    await session.flush()
    return message


async def add_outbound_message(
    session: AsyncSession,
    *,
    conversation: Conversation,
    chat_db_id: int,
    text: str,
    status: str = "pending",
) -> Message:
    message = Message(
        conversation_id=conversation.id,
        chat_id=chat_db_id,
        user_id=None,
        direction="outbound",
        max_message_id=None,
        update_key=None,
        text=text,
        text_length=len(text),
        status=status,
    )
    session.add(message)
    await session.flush()
    return message


async def reserve_update(
    session: AsyncSession,
    *,
    update_key: str,
    update_type: str,
    update_timestamp: int,
) -> ProcessedUpdate | None:
    statement = (
        insert(ProcessedUpdate)
        .values(
            update_key=update_key,
            update_type=update_type,
            update_timestamp=update_timestamp,
            status="processing",
        )
        .on_conflict_do_nothing(index_elements=[ProcessedUpdate.update_key])
        .returning(ProcessedUpdate)
    )
    reserved = await session.scalar(statement)
    return reserved


async def complete_update(
    session: AsyncSession,
    update_row: ProcessedUpdate,
    *,
    conversation_id: int | None,
    inbound_message_id: int | None,
    outbound_message_id: int | None,
    reply_text: str | None,
    reply_format: str = "markdown",
    reply_attachments: list[dict[str, str]] | None = None,
    status: str = "replied",
) -> None:
    update_row.conversation_id = conversation_id
    update_row.inbound_message_id = inbound_message_id
    update_row.outbound_message_id = outbound_message_id
    update_row.reply_text = reply_text
    update_row.reply_format = reply_format
    update_row.reply_attachments = reply_attachments or []
    update_row.status = status
    update_row.completed_at = utcnow()
    await session.flush()


async def mark_update_failed(
    session: AsyncSession, update_row: ProcessedUpdate, error_code: str
) -> None:
    update_row.status = "failed"
    update_row.error_code = error_code
    update_row.completed_at = utcnow()
    await session.flush()


async def get_update(session: AsyncSession, update_key: str) -> ProcessedUpdate | None:
    return await session.scalar(
        select(ProcessedUpdate).where(ProcessedUpdate.update_key == update_key)
    )


async def mark_update_delivered(session: AsyncSession, update_key: str) -> bool:
    update_row = await session.scalar(
        select(ProcessedUpdate).where(ProcessedUpdate.update_key == update_key)
    )
    if update_row is None:
        return False
    if update_row.status not in {"replied", "delivered"}:
        return False
    update_row.status = "delivered"
    update_row.completed_at = utcnow()
    if update_row.outbound_message_id is not None:
        await session.execute(
            update(Message)
            .where(Message.id == update_row.outbound_message_id)
            .values(status="delivered", sent_at=utcnow(), delivered_at=utcnow())
        )
    await session.flush()
    return True


async def list_conversation_messages(
    session: AsyncSession, conversation_id: int, limit: int
) -> list[Message]:
    rows = await session.scalars(
        select(Message)
        .where(Message.conversation_id == conversation_id)
        .order_by(Message.created_at.desc(), Message.id.desc())
        .limit(limit)
    )
    return list(reversed(rows.all()))


async def add_llm_run(
    session: AsyncSession,
    *,
    conversation_id: int,
    update_key: str,
    model_name: str,
    prompt_version: str,
    knowledge_version_id: int | None,
    retrieved_chunks: list[dict[str, object]],
    latency_ms: int | None,
    status: str,
    error_code: str | None = None,
    request_tokens: int | None = None,
    response_tokens: int | None = None,
) -> LLMRun:
    run = LLMRun(
        conversation_id=conversation_id,
        update_key=update_key,
        model_name=model_name,
        prompt_version=prompt_version,
        knowledge_version_id=knowledge_version_id,
        retrieved_chunks=retrieved_chunks,
        request_tokens=request_tokens,
        response_tokens=response_tokens,
        latency_ms=latency_ms,
        status=status,
        error_code=error_code,
    )
    session.add(run)
    await session.flush()
    return run


async def add_feedback(
    session: AsyncSession,
    *,
    conversation_id: int,
    message_id: int | None,
    user_id: int,
    rating: int | None,
    comment: str | None = None,
) -> Feedback:
    feedback = Feedback(
        conversation_id=conversation_id,
        message_id=message_id,
        user_id=user_id,
        rating=rating,
        comment=comment,
    )
    session.add(feedback)
    await session.flush()
    return feedback


async def get_active_knowledge_version(
    session: AsyncSession,
) -> KnowledgeVersion | None:
    return await session.scalar(select(KnowledgeVersion).where(KnowledgeVersion.status == "active"))


async def find_reusable_knowledge_version(
    session: AsyncSession, *, checksum: str, embedding_model: str
) -> KnowledgeVersion | None:
    return await session.scalar(
        select(KnowledgeVersion)
        .where(
            KnowledgeVersion.source_sha256 == checksum,
            KnowledgeVersion.embedding_model == embedding_model,
            KnowledgeVersion.status.in_(["active", "building"]),
        )
        .order_by(KnowledgeVersion.id.desc())
    )


async def delete_knowledge_version(session: AsyncSession, version: KnowledgeVersion) -> None:
    await session.execute(delete(KnowledgeVersion).where(KnowledgeVersion.id == version.id))
    await session.flush()
