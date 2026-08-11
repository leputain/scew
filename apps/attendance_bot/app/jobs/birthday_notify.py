"""
Уведомление о днях рождения.

Отправляет поздравление в birthday chat/topic.
ежедневно в 08:00 MSK для пользователей ОЗКО, у которых сегодня ДР.
"""

from __future__ import annotations

import html
from datetime import datetime

from core.screw_core.models.enums import EventType, NotifyChannel
from core.screw_core.repos.events_repo import EventsRepo
from core.screw_core.repos.users_repo import UsersRepo
from core.screw_core.services.notify import (
    get_birthday_notify_chat_id,
    get_birthday_notify_enabled,
    get_birthday_notify_message_thread_id,
    is_birthday_notify_message_thread_id_invalid,
)
from core.screw_core.services.permissions import OZKO_DEPARTMENT_ID
from core.screw_core.services.telegram_delivery import send_system_message
from core.screw_core.utils.logging import get_logger
from core.screw_core.utils.timezone import get_tzinfo

logger = get_logger(__name__)


def _birthday_notify_dedup_key(today) -> str:
    return f"birthday_notify:{today.isoformat()}:OZKO"


async def job_birthday_notify(bot) -> None:
    """Отправить поздравление с ДР в birthday chat/topic."""
    if not get_birthday_notify_enabled():
        logger.debug("birthday_notify: disabled")
        return

    chat_id = get_birthday_notify_chat_id()
    if not chat_id:
        logger.debug("birthday_notify: chat id not set")
        return

    if is_birthday_notify_message_thread_id_invalid():
        logger.warning("birthday_notify: invalid message_thread_id, skip send")
        return
    message_thread_id = get_birthday_notify_message_thread_id()

    tz = get_tzinfo("Europe/Moscow")
    today = datetime.now(tz).date()

    users = await UsersRepo.get_users_with_birthday_today(
        month=today.month,
        day=today.day,
        department_id=OZKO_DEPARTMENT_ID,
    )

    if not users:
        logger.debug("birthday_notify: no birthdays today (%s)", today)
        return

    dedup_key = _birthday_notify_dedup_key(today)
    existing = await EventsRepo.get_by_dedup_key(dedup_key)
    if existing:
        logger.info("birthday_notify: already sent for %s", today)
        return

    lines = ["🎂 <b>Сегодня день рождения!</b>\n"]
    for user in users:
        full_name = user.get("full_name") or "Сотрудник"
        lines.append(f"🎉 <b>{html.escape(str(full_name), quote=False)}</b>")

    lines.append("\nПоздравляем! Желаем отличного дня, здоровья и хорошего настроения! 🥳")
    text = "\n".join(lines)

    try:
        await send_system_message(
            bot,
            chat_id=chat_id,
            html_text=text,
            message_thread_id=message_thread_id,
            reaction_emoji="🎂",
        )
        await EventsRepo.create_event(
            event_type=EventType.BIRTHDAY_NOTIFY_SENT,
            dedup_key=dedup_key,
            event_date=today,
            payload={
                "count": len(users),
                "department_id": OZKO_DEPARTMENT_ID,
                "runtime": "aiogram",
                "thread_configured": message_thread_id is not None,
            },
            notify_channel=NotifyChannel.NONE,
        )
        logger.info("birthday_notify: sent for %d user(s)", len(users))
    except Exception as e:
        logger.warning("birthday_notify: send error: %s", str(e)[:100])
