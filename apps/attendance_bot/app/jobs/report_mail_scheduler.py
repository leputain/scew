"""
Фоновая задача отправки email-отчётов.

Отправляет ежедневный и месячный XLSX-отчёт руководству по email через EWS.
- Ежедневный: запускается в 09:35 MSK по рабочим дням.
- Месячный: запускается в последний день месяца в 10:00 MSK.

По умолчанию выключено (REPORT_MAIL_ENABLED=0).

Настройки ежедневного отчёта:
- REPORT_MAIL_ENABLED: 0/1 (по умолчанию 0)
- REPORT_MAIL_HOUR: час отправки (0-23, по умолчанию 9)
- REPORT_MAIL_MINUTE: минута отправки (0-59, по умолчанию 35)

Настройки месячного отчёта:
- REPORT_MONTHLY_ENABLED: 0/1 (по умолчанию 0)
- REPORT_MONTHLY_SEND_HOUR: час отправки (0-23, по умолчанию 10)
- REPORT_MONTHLY_SEND_MINUTE: минута отправки (0-59, по умолчанию 0)
"""

import asyncio
import calendar
from datetime import date, datetime, timedelta, tzinfo

from core.screw_core.utils.calendar import is_working_day, get_previous_working_day
from core.screw_core.utils.timezone import get_tzinfo

from apps.attendance_bot.app.config import (
    get_report_daily_timezone,
    get_report_mail_hour,
    get_report_mail_minute,
    get_report_monthly_hour,
    get_report_monthly_minute,
)
from core.screw_core.services.report_delivery_service import ReportDeliveryService
from core.screw_core.utils.logging import get_logger

logger = get_logger(__name__)


def _get_timezone() -> tzinfo:
    """Получить таймзону."""
    return get_tzinfo(get_report_daily_timezone())



def _get_next_run_time() -> datetime:
    """
    Вычислить время следующего запуска.

    Returns:
        datetime следующего запуска.
    """
    tz = _get_timezone()
    now = datetime.now(tz)

    target_hour = get_report_mail_hour()
    target_minute = get_report_mail_minute()

    # Целевое время на сегодня
    target_today = now.replace(hour=target_hour, minute=target_minute, second=0, microsecond=0)

    if now >= target_today:
        # Уже прошло — следующий запуск завтра
        target = target_today + timedelta(days=1)
    else:
        target = target_today

    # Пропускаем выходные
    while not is_working_day(target.date()):
        target += timedelta(days=1)

    return target


def _seconds_until(target: datetime) -> float:
    """Секунды до целевого времени."""
    tz = _get_timezone()
    now = datetime.now(tz)
    delta = target - now
    return max(delta.total_seconds(), 0)


async def _send_daily_report_email() -> None:
    """
    Отправить ежедневный отчёт по email.

    Отправляет отчёт за предыдущий рабочий день.
    """
    tz = _get_timezone()
    today = datetime.now(tz).date()

    # Отчёт за предыдущий рабочий день
    report_date = get_previous_working_day(today)

    logger.info(
        "report_mail_scheduler: отправка отчёта за %s",
        report_date,
    )

    result = await ReportDeliveryService.send_daily_report(report_date)

    if result.success:
        logger.info(
            "report_mail_scheduler: успешно — date=%s, recipients=%d",
            report_date,
            result.recipients_count,
        )
    else:
        logger.error(
            "report_mail_scheduler: ошибка — date=%s, error=%s",
            report_date,
            result.error_summary,
        )


async def report_mail_scheduler_loop() -> None:
    """
    Бесконечный цикл отправки email-отчётов.

    Запускается в заданное время по рабочим дням.
    """
    hour = get_report_mail_hour()
    minute = get_report_mail_minute()
    tz_name = get_report_daily_timezone()

    logger.info(
        "report_mail_scheduler запущен — отправка в %02d:%02d (%s) по рабочим дням",
        hour,
        minute,
        tz_name,
    )

    while True:
        next_run = _get_next_run_time()
        sleep_seconds = _seconds_until(next_run)

        logger.debug(
            "report_mail_scheduler: следующий запуск в %s (через %.0f сек)",
            next_run.strftime("%Y-%m-%d %H:%M:%S"),
            sleep_seconds,
        )

        await asyncio.sleep(sleep_seconds)

        # Отправляем отчёт
        try:
            await _send_daily_report_email()
        except Exception as e:
            # Ошибка не должна остановить scheduler
            logger.exception("report_mail_scheduler: критическая ошибка — %s", e)

        # Ждём минуту, чтобы гарантированно перейти на следующий день
        await asyncio.sleep(60)


def start_report_mail_scheduler() -> asyncio.Task | None:
    """
    Отключено — ежедневные email-отчёты теперь управляются APScheduler (scheduler.py).

    Returns:
        None всегда.
    """
    logger.info("report_mail_scheduler: отключён (используется APScheduler)")
    return None


# =============================================================================
# Месячный отчёт (monthly report)
# =============================================================================



def _get_next_last_day_of_month(d: date) -> date:
    """
    Получить следующий последний день месяца (включая текущий если это последний день).

    Args:
        d: Исходная дата.

    Returns:
        Дата последнего дня месяца.
    """
    _, last_day = calendar.monthrange(d.year, d.month)
    if d.day == last_day:
        return d
    # Переходим к следующему месяцу
    if d.month == 12:
        next_year = d.year + 1
        next_month = 1
    else:
        next_year = d.year
        next_month = d.month + 1
    _, next_last_day = calendar.monthrange(next_year, next_month)
    return date(next_year, next_month, next_last_day)


def _get_next_monthly_run_time() -> datetime:
    """
    Вычислить время следующего запуска месячного отчёта.

    Returns:
        datetime следующего запуска (последний день месяца в заданное время).
    """
    tz = _get_timezone()
    now = datetime.now(tz)

    target_hour = get_report_monthly_hour()
    target_minute = get_report_monthly_minute()

    # Целевой день — последний день текущего или следующего месяца
    _, last_day = calendar.monthrange(now.year, now.month)

    # Целевое время в последний день этого месяца
    target_today = now.replace(
        day=last_day,
        hour=target_hour,
        minute=target_minute,
        second=0,
        microsecond=0,
    )

    if now >= target_today:
        # Уже прошло — следующий запуск в последний день следующего месяца
        if now.month == 12:
            next_year = now.year + 1
            next_month = 1
        else:
            next_year = now.year
            next_month = now.month + 1
        _, next_last_day = calendar.monthrange(next_year, next_month)
        target = datetime(
            next_year,
            next_month,
            next_last_day,
            target_hour,
            target_minute,
            0,
            0,
            tzinfo=tz,
        )
    else:
        target = target_today

    return target


async def _send_monthly_report_email() -> None:
    """
    Отправить месячный отчёт по email.

    Отправляет отчёт за текущий месяц (вызывается в последний день месяца).
    """
    tz = _get_timezone()
    today = datetime.now(tz).date()

    # Отчёт за текущий месяц
    year = today.year
    month = today.month

    logger.info(
        "monthly_report_scheduler: отправка отчёта за %d-%02d",
        year,
        month,
    )

    result = await ReportDeliveryService.send_monthly_report(year, month)

    if result.success:
        logger.info(
            "monthly_report_scheduler: успешно — year=%d, month=%d, recipients=%d",
            year,
            month,
            result.recipients_count,
        )
    else:
        logger.error(
            "monthly_report_scheduler: ошибка — year=%d, month=%d, error=%s",
            year,
            month,
            result.error_summary,
        )


async def monthly_report_scheduler_loop() -> None:
    """
    Бесконечный цикл отправки месячных email-отчётов.

    Запускается в последний день месяца в заданное время.
    Если бот был выключен и пропустил отправку — не догоняет автоматически.
    """
    hour = get_report_monthly_hour()
    minute = get_report_monthly_minute()
    tz_name = get_report_daily_timezone()

    logger.info(
        "monthly_report_scheduler запущен — отправка в %02d:%02d (%s) в последний день месяца",
        hour,
        minute,
        tz_name,
    )

    while True:
        next_run = _get_next_monthly_run_time()
        sleep_seconds = _seconds_until(next_run)

        logger.debug(
            "monthly_report_scheduler: следующий запуск в %s (через %.0f сек)",
            next_run.strftime("%Y-%m-%d %H:%M:%S"),
            sleep_seconds,
        )

        await asyncio.sleep(sleep_seconds)

        # Отправляем отчёт
        try:
            await _send_monthly_report_email()
        except Exception as e:
            # Ошибка не должна остановить scheduler
            logger.exception("monthly_report_scheduler: критическая ошибка — %s", e)

        # Ждём минуту, чтобы гарантированно перейти на следующий месяц
        await asyncio.sleep(60)


def start_monthly_report_scheduler() -> asyncio.Task | None:
    """
    Отключено — месячные email-отчёты теперь управляются APScheduler (scheduler.py).

    Returns:
        None всегда.
    """
    logger.info("monthly_report_scheduler: отключён (используется APScheduler)")
    return None
