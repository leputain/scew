"""
Guard для проверки приватного чата.

Меню и панели бота доступны только в личных сообщениях.
В групповых чатах разрешена только работа с опросами.
"""

from __future__ import annotations

from typing import Any

from core.screw_core.utils.logging import get_logger

logger = get_logger(__name__)

# Сообщение для групповых чатов
MSG_PRIVATE_ONLY = "Меню доступно только в личных сообщениях. Напишите мне в личку."


def _event_chat_type(event: Any) -> str | None:
    chat = getattr(event, "chat", None)
    if chat is not None:
        return getattr(chat, "type", None)
    message = getattr(event, "message", None)
    if message is not None:
        chat = getattr(message, "chat", None)
        if chat is not None:
            return getattr(chat, "type", None)
    return None


def is_private_chat(event: Any) -> bool:
    """
    Проверить, что событие из приватного чата.

    Args:
        event: Message или CallbackQuery.

    Returns:
        True если приватный чат, False если группа/супергруппа/канал.
    """
    return _event_chat_type(event) == "private"


async def reject_if_group_chat(event: Any) -> bool:
    """
    Отклонить запрос если он из группового чата.

    Отправляет сообщение пользователю и логирует попытку.

    Args:
        event: Message или CallbackQuery.

    Returns:
        True если запрос отклонён (группа), False если разрешён (приватный).
    """
    if is_private_chat(event):
        return False

    # Определяем telegram_id для логирования
    telegram_id = event.from_user.id if event.from_user else "unknown"

    if hasattr(event, "reply_text") or (hasattr(event, "answer") and not hasattr(event, "data")):
        chat_id = event.chat.id
        logger.info(
            "menu_blocked_in_group: user=%s, chat=%s, command=%s",
            telegram_id,
            chat_id,
            event.text[:50] if event.text else "—",
        )
        if hasattr(event, "reply_text"):
            await event.reply_text(MSG_PRIVATE_ONLY)
        else:
            await event.answer(MSG_PRIVATE_ONLY)
    elif hasattr(event, "data"):
        chat_id = event.message.chat.id if event.message else "unknown"
        callback_data = event.data or "—"
        logger.info(
            "menu_blocked_in_group: user=%s, chat=%s, callback=%s",
            telegram_id,
            chat_id,
            callback_data,
        )
        await event.answer(MSG_PRIVATE_ONLY, show_alert=True)

    return True
