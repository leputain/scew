"""
Фоновая задача отправки ежедневных отчётов.

Отправляет ежедневный отчёт менеджерам ОЗКО в заданное время.
По умолчанию выключено (REPORT_DAILY_ENABLED=0).

Настройки:
- REPORT_DAILY_ENABLED: 0/1 (по умолчанию 0 — выключено)
- REPORT_DAILY_HOUR: час отправки (0-23, по умолчанию 18)
- REPORT_DAILY_MINUTE: минута отправки (0-59, по умолчанию 0)
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from typing import Any

from core.screw_core.utils.timezone import get_tzinfo

from apps.attendance_bot.app.config import (
    get_manager_ozko_telegram_ids,
    get_report_daily_enabled,
    get_report_daily_hour,
    get_report_daily_minute,
    get_report_daily_timezone,
)
from core.screw_core.services.reports import ReportsService
from core.screw_core.utils.logging import get_logger

logger = get_logger(__name__)


def _get_next_run_time() -> datetime:
    """
    Вычислить время следующего запуска отправки отчёта.

    Returns:
        datetime следующего запуска.
    """
    tz = get_tzinfo(get_report_daily_timezone())
    now = datetime.now(tz)

    target_hour = get_report_daily_hour()
    target_minute = get_report_daily_minute()

    # Целевое время на сегодня
    target_today = now.replace(hour=target_hour, minute=target_minute, second=0, microsecond=0)

    if now >= target_today:
        # Уже прошло — следующий запуск завтра
        return target_today + timedelta(days=1)
    else:
        return target_today


def _seconds_until(target: datetime) -> float:
    """Секунды до целевого времени."""
    tz = get_tzinfo(get_report_daily_timezone())
    now = datetime.now(tz)
    delta = target - now
    return max(delta.total_seconds(), 0)


async def _send_daily_report(bot: Any) -> None:
    """
    Сформировать и отправить ежедневный отчёт менеджерам ОЗКО.

    Args:
        bot: Экземпляр бота для отправки сообщений.
    """
    tz = get_tzinfo(get_report_daily_timezone())
    report_date = datetime.now(tz).date()

    # Получаем список менеджеров ОЗКО
    manager_ids = get_manager_ozko_telegram_ids()
    if not manager_ids:
        logger.warning("report_sender: MANAGER_OZKO_TELEGRAM_IDS пуст — отчёт не отправлен")
        return

    try:
        report = await ReportsService.build_daily_report_ozko(report_date)
    except Exception as e:
        logger.error("report_sender: ошибка формирования отчёта — %s", e)
        return

    # Отправляем каждому менеджеру
    sent_count = 0
    for manager_telegram_id in manager_ids:
        try:
            await bot.send_message(
                chat_id=manager_telegram_id,
                text=report.text,
            )
            sent_count += 1
        except Exception as e:
            logger.warning(
                "report_sender: не удалось отправить менеджеру telegram_id=%s: %s",
                manager_telegram_id,
                e,
            )

    logger.info(
        "report_sender: отчёт за %s отправлен %d/%d менеджерам (violations=%d)",
        report_date,
        sent_count,
        len(manager_ids),
        report.total_violations,
    )


async def report_sender_loop(bot: Any) -> None:
    """
    Бесконечный цикл ежедневной отправки отчётов.

    Args:
        bot: Экземпляр бота.
    """
    hour = get_report_daily_hour()
    minute = get_report_daily_minute()
    tz_name = get_report_daily_timezone()

    logger.info(
        "report_sender запущен — отправка в %02d:%02d (%s)",
        hour,
        minute,
        tz_name,
    )

    while True:
        next_run = _get_next_run_time()
        sleep_seconds = _seconds_until(next_run)

        logger.debug(
            "report_sender: следующий запуск в %s (через %.0f сек)",
            next_run.strftime("%Y-%m-%d %H:%M:%S"),
            sleep_seconds,
        )

        await asyncio.sleep(sleep_seconds)

        # Отправляем отчёт
        try:
            await _send_daily_report(bot)
        except Exception as e:
            logger.exception("report_sender: критическая ошибка — %s", e)

        # Ждём минуту, чтобы гарантированно перейти на следующий день
        await asyncio.sleep(60)


def start_report_sender(bot: Any) -> asyncio.Task | None:
    """
    Запустить фоновую задачу отправки отчётов.

    Запускается только если REPORT_DAILY_ENABLED=1.

    Args:
        bot: Экземпляр бота.

    Returns:
        asyncio.Task или None если отключено.
    """
    if not get_report_daily_enabled():
        logger.info("report_sender выключен (REPORT_DAILY_ENABLED=0)")
        return None

    return asyncio.create_task(report_sender_loop(bot))
