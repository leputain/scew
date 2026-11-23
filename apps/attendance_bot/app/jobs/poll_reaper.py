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
from typing import Any

from apps.attendance_bot.app.config import (
    get_poll_duration_seconds,
    get_poll_reaper_interval_seconds,
)
from core.screw_core.models.enums import EventType, NotifyChannel
from core.screw_core.repos.polls_repo import PollsRepo
from core.screw_core.services.events import EventsService
from core.screw_core.services.notify import NotifyService
from core.screw_core.services.permissions import OZKO_DEPARTMENT_ID
from core.screw_core.utils.logging import get_logger

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

        if not await PollsRepo.close_poll(
            poll_id, telegram_poll_id=poll.get("telegram_poll_id"), publish_summary=True,
        ):
            continue

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
            await close_expired_polls(bot)
            from apps.attendance_bot.app.services.poll_runtime import (
                _delete_or_edit_reminder, _unpin_poll_message,
                send_department_lists_summary, send_poll_close_summary,
            )

            for poll in await PollsRepo.get_pending_summaries(EXPIRED_POLLS_LIMIT):
                poll_id = poll["id"]
                telegram_poll_id = poll.get("telegram_poll_id")
                current = await PollsRepo.get_by_id(poll_id)
                if not current or not current["summary_pending"] or current["telegram_poll_id"] != telegram_poll_id:
                    continue
                try:
                    if poll.get("message_id"):
                        await _unpin_poll_message(bot, poll["chat_id"], poll["message_id"])
                    await _delete_or_edit_reminder(bot, poll["chat_id"], poll_id)
                    await send_poll_close_summary(bot, poll)
                    await send_department_lists_summary(bot, poll)
                    await asyncio.wait_for(
                        NotifyService.run_post_poll_checks(
                            bot,
                            poll["poll_date"],
                            department_id=OZKO_DEPARTMENT_ID,
                            ignore_day_off=poll["summary_ignore_day_off"],
                            poll_id=poll_id,
                        ),
                        timeout=120,
                    )
                except TimeoutError:
                    logger.warning("reaper: превышено время проверки опроса %s", poll_id)
                except Exception as exc:
                    logger.warning("reaper: poll summary error poll_id=%s type=%s", poll_id, type(exc).__name__)
                    continue
                await PollsRepo.finish_summary(poll_id, telegram_poll_id)
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
