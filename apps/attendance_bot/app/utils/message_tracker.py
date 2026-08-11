"""
Отслеживание сообщений бота для последующей очистки.

Сохраняет message_id в таблицу events для возможности удаления
при отзыве доступа пользователя.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from core.screw_core.models.enums import EventType, NotifyChannel
from core.screw_core.repos.events_repo import EventsRepo
from core.screw_core.utils.logging import get_logger

logger = get_logger(__name__)


def _is_bad_request_error(exc: Exception) -> bool:
    exc_type = exc.__class__
    return exc_type.__name__ in {"BadRequest", "TelegramBadRequest"} and (
        exc_type.__module__.startswith("telegram")
        or exc_type.__module__.startswith("aiogram")
    )


async def track_bot_message(
    telegram_id: int,
    chat_id: int,
    message_id: int,
    screen: str | None = None,
) -> None:
    """
    Записать ID сообщения бота для последующей очистки.

    Args:
        telegram_id: Telegram ID пользователя.
        chat_id: ID чата.
        message_id: ID сообщения.
        screen: Название экрана/панели (опционально).
    """
    dedup_key = f"botmsg:{telegram_id}:{message_id}"
    payload = {
        "kind": "BOT_SENT_MESSAGE",
        "telegram_id": telegram_id,
        "chat_id": chat_id,
        "message_id": message_id,
        "screen": screen,
        "ts": datetime.now(timezone.utc).isoformat(),
    }

    await EventsRepo.create_event(
        event_type=EventType.ADMIN_ACTION,
        dedup_key=dedup_key,
        user_id=None,
        payload=payload,
        notify_channel=NotifyChannel.NONE,
    )


async def track_sent_message(
    message: Any,
    telegram_id: int,
    screen: str | None = None,
) -> None:
    """
    Записать отправленное сообщение (удобная обёртка).

    Args:
        message: Отправленное сообщение.
        telegram_id: Telegram ID пользователя.
        screen: Название экрана/панели.
    """
    if not message:
        return

    await track_bot_message(
        telegram_id=telegram_id,
        chat_id=message.chat.id,
        message_id=message.message_id,
        screen=screen,
    )


async def cleanup_user_messages(bot: Any, telegram_id: int) -> dict:
    """
    Очистить недавние сообщения бота для пользователя.

    Удаляет inline-клавиатуры и сами сообщения.
    Используется при отзыве доступа.

    Args:
        bot: Экземпляр бота.
        telegram_id: Telegram ID пользователя.

    Returns:
        Статистика: edited, deleted, failed, total.
    """
    stats = {"edited": 0, "deleted": 0, "failed": 0, "total": 0}

    # Получаем последние tracked сообщения
    messages = await EventsRepo.get_bot_messages_for_user(telegram_id, limit=30)
    stats["total"] = len(messages)

    if not messages:
        return stats

    deleted_ids = []

    for msg_event in messages:
        raw_payload = msg_event.get("payload", {})

        # Handle payload that may be string (JSON) or dict
        if isinstance(raw_payload, str):
            try:
                payload = json.loads(raw_payload)
            except (json.JSONDecodeError, TypeError):
                logger.debug(
                    "cleanup: skipping event with unparseable payload: %s",
                    raw_payload[:100] if raw_payload else "(empty)",
                )
                continue
        elif isinstance(raw_payload, dict):
            payload = raw_payload
        else:
            logger.debug(
                "cleanup: skipping event with unexpected payload type: %s",
                type(raw_payload).__name__,
            )
            continue

        chat_id = payload.get("chat_id")
        message_id = payload.get("message_id")

        if not chat_id or not message_id:
            continue

        # Сначала пробуем убрать клавиатуру (edit)
        try:
            await bot.edit_message_reply_markup(
                chat_id=chat_id,
                message_id=message_id,
                reply_markup=None,
            )
            stats["edited"] += 1
        except Exception as e:
            if not _is_bad_request_error(e):
                raise
            # Сообщение не найдено или нельзя изменить
            if "message is not modified" not in str(e):
                logger.debug(
                    "cleanup: edit failed chat=%s msg=%s: %s",
                    chat_id, message_id, e,
                )

        # Затем пробуем удалить сообщение
        try:
            await bot.delete_message(chat_id=chat_id, message_id=message_id)
            stats["deleted"] += 1
            deleted_ids.append(message_id)
        except Exception as e:
            if not _is_bad_request_error(e):
                raise
            logger.debug(
                "cleanup: delete failed chat=%s msg=%s: %s",
                chat_id, message_id, e,
            )
            stats["failed"] += 1

    # Удаляем записи об успешно удалённых сообщениях
    if deleted_ids:
        await EventsRepo.delete_bot_message_events(telegram_id, deleted_ids)

    logger.info(
        "cleanup_user_messages: telegram_id=%s edited=%d deleted=%d failed=%d total=%d",
        telegram_id,
        stats["edited"],
        stats["deleted"],
        stats["failed"],
        stats["total"],
    )

    return stats
