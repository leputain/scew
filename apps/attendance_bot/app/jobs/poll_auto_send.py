"""
Автоматическая отправка ежедневного опроса по расписанию.

Вызывается APScheduler CronTrigger в заданное время (пн-пт).
Переиспользует логику создания опроса из poll.py.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from apps.attendance_bot.app.config import get_attendance_group_id
from apps.attendance_bot.app.services.group_preflight import check_attendance_group_access
from apps.attendance_bot.app.services.poll_runtime import (
    _load_poll_config,
    _pin_poll_message,
    _remember_poll_option_mapping,
    get_local_date,
    get_local_datetime,
    get_poll_scheduler,
    send_absence_message,
)
from apps.attendance_bot.app.scheduler import schedule_poll_auto_close, schedule_poll_reminder
from apps.attendance_bot.app.utils.poll_payload import (
    PollPayload,
    build_aiogram_poll_payload,
    send_aiogram_poll_with_fallback,
)
from core.screw_core.repos.poll_config_repo import PollConfigRepo
from core.screw_core.services.polls import PollsService
from core.screw_core.utils.logging import get_logger

logger = get_logger(__name__)


async def _send_poll(
    bot: Any,
    poll_payload: PollPayload,
    *,
    chat_id: int,
    question: str,
):
    poll_options, plain_options, api_kwargs = build_aiogram_poll_payload(poll_payload)
    return await send_aiogram_poll_with_fallback(
        bot,
        logger,
        chat_id=chat_id,
        question=question,
        options=poll_options,
        plain_options=plain_options,
        api_kwargs=api_kwargs,
        is_anonymous=False,
        allows_multiple_answers=False,
    )


async def job_poll_auto_send(bot: Any) -> None:
    """Автоматическое создание ежедневного опроса."""
    logger.info("AUTO_SEND_POLL: job started")

    # 1. Проверяем enabled в БД (double-check)
    config = await PollConfigRepo.get_config()
    if not config.get("auto_send_enabled", False):
        logger.info("AUTO_SEND_POLL: disabled in config, skipping")
        return

    # 2. Проверяем skip_dates (праздники)
    poll_date = get_local_date()
    is_skip = await PollConfigRepo.is_skip_date(poll_date)
    if is_skip:
        skip_info = await PollConfigRepo.get_skip_date_info(poll_date)
        reason = skip_info.get("reason", "") if skip_info else ""
        logger.info("AUTO_SEND_POLL: skip date %s (%s), skipping", poll_date, reason)
        return

    # 3. Проверяем нет ли уже опроса на сегодня
    existing = await PollsService.get_current_poll()
    if existing and existing.get("poll_date") == poll_date:
        logger.info("AUTO_SEND_POLL: poll already exists for %s, skipping", poll_date)
        return

    # 4. Проверяем group_id
    group_id = get_attendance_group_id()
    if not group_id:
        logger.error("AUTO_SEND_POLL: ATTENDANCE_GROUP_ID not configured")
        return

    group_check = await check_attendance_group_access(bot)
    if not group_check.accessible:
        logger.warning("AUTO_SEND_POLL: attendance group unavailable, skipping")
        return

    # 5. Загружаем конфиг и создаём опрос
    poll_config = await _load_poll_config()
    poll_payload = poll_config.payload
    duration = poll_config.duration_seconds
    reminder_minutes = poll_config.reminder_minutes

    poll_question = f"Явка за {poll_date.strftime('%d.%m.%Y')}"
    try:
        poll_message = await _send_poll(
            bot,
            poll_payload,
            chat_id=group_id,
            question=poll_question,
        )
    except Exception as e:
        logger.error("AUTO_SEND_POLL: failed to send poll: %s", e)
        return

    telegram_poll_id = poll_message.poll.id if poll_message.poll else None
    if not telegram_poll_id:
        logger.error("AUTO_SEND_POLL: no telegram_poll_id received")
        return
    _remember_poll_option_mapping(
        telegram_poll_id,
        poll_message.poll.options if poll_message.poll else [],
        poll_config.option_to_choice,
        poll_config.persistent_to_choice,
    )

    closes_at = get_local_datetime() + timedelta(seconds=duration)

    result = await PollsService.create_daily_poll(
        poll_date=poll_date,
        chat_id=group_id,
        message_id=poll_message.message_id,
        telegram_poll_id=telegram_poll_id,
        closes_at=closes_at,
    )

    if not result.success:
        logger.error("AUTO_SEND_POLL: DB error: %s", result.error)
        try:
            await bot.stop_poll(chat_id=group_id, message_id=poll_message.message_id)
        except Exception as e:
            logger.debug("stop_poll cleanup failed: %s", e)
        return

    poll_id = result.poll_id
    logger.info(
        "AUTO_SEND_POLL: created poll_id=%s, poll_date=%s, closes_at=%s",
        poll_id,
        poll_date,
        closes_at,
    )

    # 6. Закрепляем сообщение
    await _pin_poll_message(
        bot=bot,
        chat_id=group_id,
        message_id=poll_message.message_id,
        poll_date=poll_date,
    )

    # 7. Отправляем сообщение об отсутствующих
    await send_absence_message(bot, group_id, poll_date)

    # 8. Планируем авто-закрытие
    scheduler = get_poll_scheduler()
    if scheduler:
        schedule_poll_auto_close(
            scheduler=scheduler,
            poll_id=poll_id,
            closes_at=closes_at,
            chat_id=group_id,
            message_id=poll_message.message_id,
            telegram_poll_id=telegram_poll_id,
        )

    # 9. Reminder если настроен
    if reminder_minutes and reminder_minutes * 60 < duration and scheduler:
        remind_at = closes_at - timedelta(seconds=reminder_minutes * 60)
        schedule_poll_reminder(
            scheduler=scheduler,
            poll_id=poll_id,
            remind_at=remind_at,
            poll_date=poll_date,
            telegram_poll_id=telegram_poll_id,
        )

    logger.info(
        "AUTO_SEND_POLL: done — poll_id=%s, duration=%dm, reminder=%s",
        poll_id,
        duration // 60,
        f"{reminder_minutes}m" if reminder_minutes else "off",
    )
