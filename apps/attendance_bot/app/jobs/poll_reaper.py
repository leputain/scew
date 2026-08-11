"""
Фоновая задача автозакрытия просроченных опросов.

Poll reaper — устойчивое автозакрытие после рестартов бота:
- Периодически проверяет poll_runs со status='open' и closes_at <= now()
- Для старых записей без closes_at использует fallback: published_at + duration <= now()
- Закрывает их через bot.stop_poll()
- Помечает в БД как closed даже если stop_poll не сработал (сообщение удалено и т.п.)
"""

from __future__ import annotations

import asyncio
from datetime import date, datetime
from typing import Any

from apps.attendance_bot.app.config import (
    get_poll_duration_seconds,
    get_poll_reaper_interval_seconds,
    get_poll_timezone,
)
from core.screw_core.models.enums import EventType, NotifyChannel
from core.screw_core.repos.polls_repo import PollsRepo
from core.screw_core.services.events import EventsService
from core.screw_core.services.notify import NotifyService
from core.screw_core.services.permissions import OZKO_DEPARTMENT_ID
from core.screw_core.utils.logging import get_logger
from core.screw_core.utils.timezone import get_tzinfo

logger = get_logger(__name__)

# Лимит опросов за одну итерацию
EXPIRED_POLLS_LIMIT = 20


async def close_expired_polls(bot: Any) -> list[dict]:
    """
    Закрыть все просроченные открытые опросы.

    Args:
        bot: Экземпляр бота для вызова stop_poll.

    Returns:
        Список закрытых опросов.
    """
    duration_seconds = get_poll_duration_seconds()
    expired_polls = await PollsRepo.get_expired_open_polls(
        duration_seconds=duration_seconds,
        limit=EXPIRED_POLLS_LIMIT,
    )

    if not expired_polls:
        return []

    closed_polls: list[dict] = []

    for poll in expired_polls:
        poll_id = poll["id"]
        poll_date = poll["poll_date"]
        chat_id = poll.get("chat_id")
        message_id = poll.get("message_id")

        stop_poll_failed = False

        # Пытаемся остановить native poll в Telegram
        if chat_id and message_id:
            try:
                await bot.stop_poll(chat_id=chat_id, message_id=message_id)
                logger.info(
                    "reaper: native poll остановлен — poll_id=%s, chat_id=%s, message_id=%s",
                    poll_id,
                    chat_id,
                    message_id,
                )
            except Exception as e:
                # Poll мог быть удалён, уже остановлен или бот потерял доступ к чату
                logger.warning(
                    "reaper: не удалось остановить native poll poll_id=%s: %s",
                    poll_id,
                    e,
                )
                stop_poll_failed = True

        # Закрываем опрос в БД в любом случае
        await PollsRepo.close_poll(poll_id)
        logger.info("reaper: опрос poll_id=%s закрыт в БД", poll_id)
        closed_polls.append(poll)

        # Если stop_poll упал — записываем событие
        if stop_poll_failed:
            dedup_key = f"poll:close_failed:{poll_date}:{message_id}"
            await EventsService.create_event(
                event_type=EventType.CONFLICT_STATUS,
                dedup_key=dedup_key,
                event_date=poll_date,
                payload={
                    "poll_id": poll_id,
                    "chat_id": chat_id,
                    "message_id": message_id,
                    "reason": "stop_poll failed during reaper close",
                },
                notify_channel=NotifyChannel.NONE,
            )

    return closed_polls


async def poll_reaper_loop(bot: Any) -> None:
    """
    Бесконечный цикл проверки и закрытия просроченных опросов.

    Args:
        bot: Экземпляр бота.
    """
    interval = get_poll_reaper_interval_seconds()
    logger.info("poll reaper запущен, интервал=%s сек", interval)

    while True:
        try:
            closed_polls = await close_expired_polls(bot)
            if closed_polls:
                logger.info("reaper: закрыто %d опросов, ждём 10с для доставки голосов...", len(closed_polls))
                await asyncio.sleep(10)
                tz = get_tzinfo(get_poll_timezone())
                from apps.attendance_bot.app.services.poll_runtime import (
                    send_department_lists_summary,
                    send_poll_close_summary,
                )

                for poll in closed_polls:
                    poll_date = poll.get("poll_date")
                    check_date = poll_date if isinstance(poll_date, date) else datetime.now(tz).date()
                    await asyncio.wait_for(
                        NotifyService.run_post_poll_checks(
                            bot,
                            check_date,
                            department_id=OZKO_DEPARTMENT_ID,
                            poll_id=poll.get("id"),
                        ),
                        timeout=120,
                    )
                    await send_poll_close_summary(
                        bot,
                        poll,
                        department_id=OZKO_DEPARTMENT_ID,
                    )
                    await send_department_lists_summary(bot, poll)
        except Exception as e:
            logger.error("reaper: ошибка при закрытии опросов: %s", e)

        await asyncio.sleep(interval)


def start_poll_reaper(bot: Any) -> asyncio.Task:
    """
    Запустить фоновую задачу poll reaper.

    Args:
        bot: Экземпляр бота.

    Returns:
        asyncio.Task для возможной отмены.
    """
    return asyncio.create_task(poll_reaper_loop(bot))
