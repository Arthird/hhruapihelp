from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from llama_index.core.llms import ChatMessage
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.config import Settings
from backend.app.db import repositories as repo
from backend.app.logging import get_logger

logger = get_logger(__name__)

HISTORY_LIMIT = 12
MAX_REPLY_CHARS = 3900


@dataclass
class Reply:
    text: str
    format: str = "markdown"
    attachments: list[dict[str, str]] = field(default_factory=list)


@dataclass
class UpdateResult:
    reply: Reply | None
    already_delivered: bool = False


class UpdateService:
    def __init__(
        self,
        *,
        settings: Settings,
        agent_factory: Any,
    ) -> None:
        self._settings = settings
        self._agent_factory = agent_factory

    async def process(
        self,
        session: AsyncSession,
        *,
        update_key: str,
        payload: dict[str, Any],
    ) -> UpdateResult:
        update_type = str(payload["update_type"])
        update_timestamp = int(payload["update_timestamp"])

        reserved = await repo.reserve_update(
            session,
            update_key=update_key,
            update_type=update_type,
            update_timestamp=update_timestamp,
        )
        if reserved is None:
            return await self._replay(session, update_key)

        chat_payload = payload["chat"]
        user_payload = payload["user"]
        user = await repo.upsert_user(
            session,
            max_user_id=int(user_payload["id"]),
            first_name=user_payload.get("first_name"),
            last_name=user_payload.get("last_name"),
            username=user_payload.get("username"),
            is_bot=bool(user_payload.get("is_bot", False)),
            locale=payload.get("user_locale"),
        )
        chat = await repo.upsert_chat(
            session,
            max_chat_id=int(chat_payload["id"]),
            chat_type=str(chat_payload["type"]),
            title=chat_payload.get("title"),
            added_by_user_id=user.id if update_type in {"bot_added", "bot_started"} else None,
        )

        if update_type in {"bot_removed", "dialog_removed"}:
            await repo.upsert_chat(
                session,
                max_chat_id=int(chat_payload["id"]),
                chat_type=str(chat_payload["type"]),
                title=chat_payload.get("title"),
                status="removed",
            )
            await repo.close_active_conversations(session, chat_id=chat.id, reason="removed")
            await repo.complete_update(
                session,
                reserved,
                conversation_id=None,
                inbound_message_id=None,
                outbound_message_id=None,
                reply_text=None,
                status="ignored",
            )
            return UpdateResult(reply=None)

        if update_type in {"dialog_cleared", "bot_stopped"}:
            await repo.close_active_conversations(
                session, chat_id=chat.id, user_id=None, reason="cleared"
            )
            text = "Контекст очищен. Задайте новый вопрос по API HH.ru."
            await repo.complete_update(
                session,
                reserved,
                conversation_id=None,
                inbound_message_id=None,
                outbound_message_id=None,
                reply_text=text,
                status="replied",
            )
            return UpdateResult(reply=Reply(text=text))

        if update_type == "bot_started":
            text = "Здравствуйте! Я Хеша, помогу с API HH.ru. Задайте вопрос."
            await repo.complete_update(
                session,
                reserved,
                conversation_id=None,
                inbound_message_id=None,
                outbound_message_id=None,
                reply_text=text,
                status="replied",
            )
            return UpdateResult(reply=Reply(text=text))

        if user.is_bot:
            await repo.complete_update(
                session,
                reserved,
                conversation_id=None,
                inbound_message_id=None,
                outbound_message_id=None,
                reply_text=None,
                status="ignored",
            )
            return UpdateResult(reply=None)

        if update_type == "message_callback":
            return await self._handle_callback(session, reserved, chat, user, payload)

        if update_type != "message_created":
            await repo.complete_update(
                session,
                reserved,
                conversation_id=None,
                inbound_message_id=None,
                outbound_message_id=None,
                reply_text=None,
                status="ignored",
            )
            return UpdateResult(reply=None)

        message_payload = payload.get("message") or {}
        question = str(message_payload.get("text") or "").strip()
        if not question:
            await repo.complete_update(
                session,
                reserved,
                conversation_id=None,
                inbound_message_id=None,
                outbound_message_id=None,
                reply_text=None,
                status="ignored",
            )
            return UpdateResult(reply=None)

        conversation = await repo.get_or_create_active_conversation(
            session, chat_id=chat.id, user_id=user.id
        )
        inbound = await repo.add_inbound_message(
            session,
            conversation=conversation,
            chat_db_id=chat.id,
            user_db_id=user.id,
            max_message_id=message_payload.get("id"),
            update_key=update_key,
            text=question,
        )

        if question.startswith("/"):
            return await self._handle_command(session, reserved, conversation, chat, question)

        history = await self._history(session, conversation.id)
        try:
            agent = await self._agent_factory()
            result = await agent.answer(question=question, history=history)
        except Exception as exc:
            logger.error(
                "agent_failed",
                update_key=update_key,
                conversation_id=conversation.id,
                error=type(exc).__name__,
            )
            text = (
                "Не удалось получить ответ из базы знаний. "
                "Попробуйте переформулировать вопрос позже."
            )
            await repo.add_llm_run(
                session,
                conversation_id=conversation.id,
                update_key=update_key,
                model_name=self._settings.model_name,
                prompt_version="unknown",
                knowledge_version_id=None,
                retrieved_chunks=[],
                latency_ms=0,
                status="error",
                error_code=type(exc).__name__,
            )
            outbound = await repo.add_outbound_message(
                session, conversation=conversation, chat_db_id=chat.id, text=text
            )
            await repo.complete_update(
                session,
                reserved,
                conversation_id=conversation.id,
                inbound_message_id=inbound.id,
                outbound_message_id=outbound.id,
                reply_text=text,
            )
            return UpdateResult(reply=Reply(text=text))

        text, attachments = self._format_for_max(result.text, result.sources)
        await repo.add_llm_run(
            session,
            conversation_id=conversation.id,
            update_key=update_key,
            model_name=self._settings.model_name,
            prompt_version=result.prompt_version,
            knowledge_version_id=agent.knowledge_version_id,
            retrieved_chunks=result.sources,
            latency_ms=result.latency_ms,
            status="success",
        )
        outbound = await repo.add_outbound_message(
            session, conversation=conversation, chat_db_id=chat.id, text=text
        )
        await repo.complete_update(
            session,
            reserved,
            conversation_id=conversation.id,
            inbound_message_id=inbound.id,
            outbound_message_id=outbound.id,
            reply_text=text,
            reply_attachments=attachments,
        )
        return UpdateResult(reply=Reply(text=text, attachments=attachments))

    async def _replay(self, session: AsyncSession, update_key: str) -> UpdateResult:
        row = await repo.get_update(session, update_key)
        if row is None or row.status in {"processing", "failed"}:
            return UpdateResult(reply=None)
        if row.status == "delivered":
            return UpdateResult(reply=None, already_delivered=True)
        if row.reply_text:
            return UpdateResult(
                reply=Reply(
                    text=row.reply_text,
                    format=row.reply_format or "markdown",
                    attachments=row.reply_attachments or [],
                )
            )
        return UpdateResult(reply=None)

    async def _handle_command(
        self,
        session: AsyncSession,
        reserved: repo.ProcessedUpdate,
        conversation: repo.Conversation,
        chat: repo.MaxChat,
        question: str,
    ) -> UpdateResult:
        command = question.split(maxsplit=1)[0].lower()
        if command in {"/start", "/help"}:
            text = (
                "Я помогаю с API HH.ru: методы, параметры, ошибки и примеры кода. "
                "Очистить контекст: /clear."
            )
        elif command == "/clear":
            await repo.close_active_conversations(
                session, chat_id=chat.id, user_id=conversation.user_id, reason="command"
            )
            text = "Контекст очищен. Задайте новый вопрос."
        else:
            text = "Неизвестная команда. Доступны /start, /help и /clear."
        outbound = await repo.add_outbound_message(
            session, conversation=conversation, chat_db_id=chat.id, text=text
        )
        await repo.complete_update(
            session,
            reserved,
            conversation_id=conversation.id,
            inbound_message_id=None,
            outbound_message_id=outbound.id,
            reply_text=text,
        )
        return UpdateResult(reply=Reply(text=text))

    async def _handle_callback(
        self,
        session: AsyncSession,
        reserved: repo.ProcessedUpdate,
        chat: repo.MaxChat,
        user: repo.MaxUser,
        payload: dict[str, Any],
    ) -> UpdateResult:
        callback = payload.get("callback") or {}
        action = str(callback.get("payload") or "")
        conversation = await repo.get_or_create_active_conversation(
            session, chat_id=chat.id, user_id=user.id
        )
        if action == "clear":
            await repo.close_active_conversations(
                session, chat_id=chat.id, user_id=user.id, reason="cleared"
            )
            text = "Контекст очищен."
        elif action.startswith("feedback:"):
            rating = 1 if action.endswith("positive") else -1
            await repo.add_feedback(
                session,
                conversation_id=conversation.id,
                message_id=None,
                user_id=user.id,
                rating=rating,
            )
            text = "Спасибо за оценку."
        else:
            text = "Действие не распознано."
        await repo.complete_update(
            session,
            reserved,
            conversation_id=conversation.id,
            inbound_message_id=None,
            outbound_message_id=None,
            reply_text=text,
        )
        return UpdateResult(reply=Reply(text=text))

    async def _history(self, session: AsyncSession, conversation_id: int) -> list[ChatMessage]:
        messages = await repo.list_conversation_messages(session, conversation_id, HISTORY_LIMIT)
        return [
            ChatMessage(
                role="user" if item.direction == "inbound" else "assistant",
                content=item.text,
            )
            for item in messages[:-1]
        ]

    @staticmethod
    def _ensure_source(text: str, sources: list[dict[str, object]]) -> str:
        if not sources or "Источник" in text:
            result = text
        else:
            sections: list[str] = []
            for source in sources[:2]:
                section = str(source.get("section") or "").strip()
                endpoint = str(source.get("endpoint") or "").strip()
                label = f"{section} ({endpoint})" if endpoint else section
                if label and label not in sections:
                    sections.append(label)
            suffix = "\n\nИсточник: " + "; ".join(sections) if sections else ""
            result = text + suffix
        if len(result) > MAX_REPLY_CHARS:
            result = result[: MAX_REPLY_CHARS - 1].rstrip() + "…"
        return result

    @classmethod
    def _format_for_max(
        cls, text: str, sources: list[dict[str, object]]
    ) -> tuple[str, list[dict[str, str]]]:
        attachments: list[dict[str, str]] = []
        normalized = _strip_markdown_lists(text)

        def replace_code(match: re.Match[str]) -> str:
            language = match.group(1).strip().lower()
            code = match.group(2).rstrip("\n")
            if "\n" not in code:
                return f"`{code.strip()}`"
            attachment_index = len(attachments) + 1
            extension = _extension_for(language)
            filename = f"example-{attachment_index}.{extension}"
            attachments.append(
                {
                    "type": "file",
                    "filename": filename,
                    "mime_type": _mime_for(extension),
                    "content": code,
                }
            )
            return f"Пример кода: {filename} (во вложении)."

        normalized = CODE_BLOCK_RE.sub(replace_code, normalized)
        normalized = re.sub(r"\n{3,}", "\n\n", normalized).strip()
        return cls._ensure_source(normalized, sources), attachments


CODE_BLOCK_RE = re.compile(r"```([a-zA-Z0-9_+-]*)\n([\s\S]*?)```")
LIST_ITEM_RE = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s+(.*)$")


def _strip_markdown_lists(text: str) -> str:
    lines: list[str] = []
    in_code = False
    for raw_line in text.splitlines():
        if raw_line.strip().startswith("```"):
            in_code = not in_code
            lines.append(raw_line)
            continue
        if in_code:
            lines.append(raw_line)
            continue
        item = LIST_ITEM_RE.match(raw_line)
        if item:
            lines.append(item.group(1).strip())
        else:
            lines.append(raw_line)
    return "\n".join(lines)


def _extension_for(language: str) -> str:
    return {
        "python": "py",
        "py": "py",
        "javascript": "js",
        "js": "js",
        "typescript": "ts",
        "ts": "ts",
        "json": "json",
        "bash": "sh",
        "shell": "sh",
        "sh": "sh",
        "http": "http",
        "sql": "sql",
        "java": "java",
        "go": "go",
        "ruby": "rb",
        "php": "php",
    }.get(language, "txt")


def _mime_for(extension: str) -> str:
    return {
        "py": "text/x-python",
        "js": "text/javascript",
        "ts": "text/typescript",
        "json": "application/json",
        "sh": "text/x-shellscript",
        "http": "text/plain",
        "sql": "application/sql",
        "java": "text/x-java-source",
        "go": "text/x-go",
        "rb": "text/x-ruby",
        "php": "text/x-php",
        "txt": "text/plain",
    }.get(extension, "text/plain")
