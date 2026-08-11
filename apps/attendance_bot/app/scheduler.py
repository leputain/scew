"""
APScheduler для attendance_bot.

Задачи:
- Ежедневный XLSX отчёт в 09:35 MSK (по рабочим дням)
- Месячный XLSX отчёт в последний день месяца в 10:00 MSK
- Автозакрытие опросов (persistent, восстанавливается при рестарте)

Запуск:
- Scheduler стартует вместе с ботом
- Graceful shutdown при остановке бота
"""

import asyncio
import os
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from core.screw_core.utils.timezone import get_tzinfo

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.date import DateTrigger
from apscheduler.triggers.interval import IntervalTrigger

from apps.attendance_bot.app.config import (
    get_flud_monthly_digest_hour,
    get_flud_monthly_digest_minute,
    get_flud_retention_days,
    get_flud_weekly_digest_day_of_week,
    get_flud_weekly_digest_hour,
    get_flud_weekly_digest_minute,
    get_flud_monthly_digest_enabled,
    get_flud_weekly_digest_enabled,
    is_flud_enabled,
)
from core.screw_core.repos.polls_repo import PollsRepo
from core.screw_core.services.report_delivery_service import ReportDeliveryService
from core.screw_core.utils.calendar import (
    get_previous_working_day,
    is_last_day_of_month,
    is_working_day,
)
from core.screw_core.utils.logging import get_logger

from core.screw_core.services.checks import ChecksService
from core.screw_core.services.notify import NotifyService
from core.screw_core.services.permissions import OZKO_DEPARTMENT_ID

logger = get_logger(__name__)

# Таймзона для всех задач
SCHEDULER_TIMEZONE = "Europe/Moscow"

# Глобальная ссылка на scheduler для статусов
_scheduler_instance: AsyncIOScheduler | None = None


# =============================================================================
# Конфигурация
# =============================================================================


def is_scheduler_enabled() -> bool:
    """Проверить, включён ли APScheduler."""
    return os.getenv("SCHEDULER_ENABLED", "1") == "1"


def is_daily_report_enabled() -> bool:
    """Проверить, включена ли задача daily report."""
    return os.getenv("REPORT_MAIL_ENABLED", "0") == "1"


def get_daily_report_hour() -> int:
    """Час запуска daily report (default: 9)."""
    try:
        return int(os.getenv("REPORT_MAIL_HOUR", "9"))
    except ValueError:
        return 9


def get_daily_report_minute() -> int:
    """Минута запуска daily report (default: 35)."""
    try:
        return int(os.getenv("REPORT_MAIL_MINUTE", "35"))
    except ValueError:
        return 35


def is_monthly_report_enabled() -> bool:
    """Проверить, включена ли задача monthly report."""
    return os.getenv("REPORT_MONTHLY_ENABLED", "0") == "1"


def get_monthly_report_hour() -> int:
    """Час запуска monthly report (default: 10)."""
    try:
        return int(os.getenv("REPORT_MONTHLY_SEND_HOUR", "10"))
    except ValueError:
        return 10


def get_monthly_report_minute() -> int:
    """Минута запуска monthly report (default: 0)."""
    try:
        return int(os.getenv("REPORT_MONTHLY_SEND_MINUTE", "0"))
    except ValueError:
        return 0


def is_ews_configured() -> bool:
    """Проверить, настроен ли EWS."""
    return bool(
        os.getenv("EXCHANGE_EWS_URL")
        and os.getenv("EXCHANGE_USERNAME")
        and os.getenv("EXCHANGE_PASSWORD")
        and os.getenv("REPORT_TO")
    )


# =============================================================================
# Utility functions
# =============================================================================


# =============================================================================
# Job functions
# =============================================================================


async def job_daily_report() -> None:
    """
    Задача: отправка ежедневного XLSX отчёта по email.

    Отправляет отчёт за предыдущий рабочий день.
    Идемпотентна: ReportDeliveryService использует dedup_key.
    Пропускает выходные (проверка внутри).
    """
    tz = get_tzinfo(SCHEDULER_TIMEZONE)
    today = datetime.now(tz).date()

    # Пропускаем выходные
    if not is_working_day(today):
        logger.debug("scheduler: daily_report пропущен — выходной день")
        return

    # Отчёт за предыдущий рабочий день
    report_date = get_previous_working_day(today)

    logger.info("scheduler: запуск daily_report за %s", report_date)

    try:
        result = await ReportDeliveryService.send_daily_report(report_date)

        if result.success:
            logger.info(
                "scheduler: daily_report успешно — date=%s, recipients=%d",
                report_date,
                result.recipients_count,
            )
        else:
            logger.error(
                "scheduler: daily_report ошибка — date=%s, error=%s",
                report_date,
                result.error_summary,
            )

    except Exception as e:
        logger.exception("scheduler: daily_report неожиданная ошибка — %s", e)


async def job_monthly_report_check() -> None:
    """
    Задача: проверка и отправка месячного XLSX отчёта.

    Запускается каждый день в 10:00. Если сегодня последний день месяца —
    отправляет отчёт. Иначе — пропускает.

    Идемпотентна: ReportDeliveryService использует dedup_key.
    """
    tz = get_tzinfo(SCHEDULER_TIMEZONE)
    today = datetime.now(tz).date()

    # Проверяем, последний ли день месяца
    if not is_last_day_of_month(today):
        logger.debug("scheduler: monthly_report пропущен — не последний день месяца")
        return

    year = today.year
    month = today.month

    logger.info("scheduler: запуск monthly_report за %d-%02d (последний день месяца)", year, month)

    try:
        result = await ReportDeliveryService.send_monthly_report(year, month)

        if result.success:
            logger.info(
                "scheduler: monthly_report успешно — year=%d, month=%d, recipients=%d",
                year,
                month,
                result.recipients_count,
            )
        else:
            logger.error(
                "scheduler: monthly_report ошибка — year=%d, month=%d, error=%s",
                year,
                month,
                result.error_summary,
            )

    except Exception as e:
        logger.exception("scheduler: monthly_report неожиданная ошибка — %s", e)


# =============================================================================
# Poll auto-close jobs
# =============================================================================

# Глобальная ссылка на бота (устанавливается при старте)
_bot_instance: Any | None = None


def set_bot_instance(bot: Any) -> None:
    """Установить экземпляр бота для использования в job'ах."""
    global _bot_instance
    _bot_instance = bot


_application_instance = None


def set_application_instance(app) -> None:
    """Установить экземпляр Application для проверки живости Updater в heartbeat."""
    global _application_instance
    _application_instance = app


HEARTBEAT_FILE = Path("/tmp/bot_heartbeat_attendance")
HEARTBEAT_PENDING_UPDATES_THRESHOLD = int(os.getenv("HEARTBEAT_PENDING_UPDATES_THRESHOLD", "100"))
HEARTBEAT_MAX_FAILURES = int(os.getenv("HEARTBEAT_MAX_FAILURES", "3"))
_heartbeat_failures = 0
_heartbeat_pending_failures = 0
_telegram_health_state: dict[str, object] = {
    "checked_at": None,
    "ok": None,
    "pending_updates": None,
    "error": None,
}


def _reset_heartbeat_failures() -> None:
    global _heartbeat_failures, _heartbeat_pending_failures
    _heartbeat_failures = 0
    _heartbeat_pending_failures = 0


def _fail_heartbeat(reason: str, max_failures: int | None = None) -> None:
    """Record heartbeat failure and terminate so Docker restart policy can recover."""
    global _heartbeat_failures

    _heartbeat_failures += 1
    limit = max_failures or HEARTBEAT_MAX_FAILURES
    logger.error("heartbeat: %s (failure %s/%s)", reason, _heartbeat_failures, limit)

    if _heartbeat_failures >= limit:
        try:
            HEARTBEAT_FILE.unlink(missing_ok=True)
        except Exception:
            pass
        logger.critical("heartbeat: terminating process to restart attendance_bot")
        os._exit(1)


async def _job_heartbeat() -> None:
    """Write process liveness without coupling it to Telegram API availability."""
    if _application_instance is not None:
        updater = getattr(_application_instance, "updater", None)
        running = bool(updater and getattr(updater, "running", False))
        if not running:
            logger.error("heartbeat: updater NOT running — контейнер должен перезапуститься")
            _fail_heartbeat("updater NOT running", max_failures=1)
        return

    try:
        _reset_heartbeat_failures()
        HEARTBEAT_FILE.write_text(str(int(time.time())))
    except Exception as e:
        logger.warning("heartbeat: write failed: %r", e)


async def _job_telegram_health() -> None:
    """Check Telegram reachability for diagnostics without restarting the bot."""
    global _heartbeat_pending_failures, _telegram_health_state
    if _bot_instance is None:
        return
    checked_at = datetime.now(timezone.utc)
    try:
        await asyncio.wait_for(_bot_instance.get_me(), timeout=8)
        webhook_info = await asyncio.wait_for(_bot_instance.get_webhook_info(), timeout=8)
    except Exception as exc:
        _telegram_health_state = {
            "checked_at": checked_at,
            "ok": False,
            "pending_updates": None,
            "error": type(exc).__name__,
        }
        logger.warning("telegram_health: API unavailable: %r", exc)
        return

    pending_updates = getattr(webhook_info, "pending_update_count", 0) or 0
    if pending_updates > HEARTBEAT_PENDING_UPDATES_THRESHOLD:
        _heartbeat_pending_failures += 1
        _telegram_health_state = {
            "checked_at": checked_at,
            "ok": False,
            "pending_updates": pending_updates,
            "error": "pending_updates_high",
        }
        logger.warning(
            "telegram_health: pending updates high (%s > %s, observation %s)",
            pending_updates,
            HEARTBEAT_PENDING_UPDATES_THRESHOLD,
            _heartbeat_pending_failures,
        )
        return
    _heartbeat_pending_failures = 0
    _telegram_health_state = {
        "checked_at": checked_at,
        "ok": True,
        "pending_updates": pending_updates,
        "error": None,
    }


def get_poll_auto_close_job_id(poll_id: int) -> str:
    """Сформировать ID job'а для auto-close опроса."""
    return f"poll_auto_close:{poll_id}"


async def job_poll_auto_close(
    poll_id: int,
    chat_id: int,
    message_id: int,
    telegram_poll_id: str,
) -> None:
    """
    Задача: автоматическое закрытие опроса.

    Идемпотентна: проверяет статус и telegram_poll_id перед закрытием.
    Не падает если опрос уже закрыт.

    Args:
        poll_id: ID опроса в БД.
        chat_id: ID чата с опросом.
        message_id: ID сообщения с опросом.
        telegram_poll_id: ID опроса в Telegram (для compare-and-set).
    """
    logger.info(
        "AUTO_CLOSE_POLL poll_id=%s, chat_id=%s, message_id=%s, tg_poll_id=%s",
        poll_id,
        chat_id,
        message_id,
        telegram_poll_id[:16] if telegram_poll_id else None,
    )

    # Проверяем текущее состояние опроса (compare-and-set)
    poll = await PollsRepo.get_by_id(poll_id)
    if not poll:
        logger.info("AUTO_CLOSE_POLL poll_id=%s — опрос не найден, пропускаем", poll_id)
        return

    current_status = poll.get("status")
    current_tg_poll_id = poll.get("telegram_poll_id")

    # Идемпотентность: уже закрыт
    if current_status != "open":
        logger.info(
            "AUTO_CLOSE_POLL poll_id=%s — уже закрыт (status=%s), пропускаем",
            poll_id,
            current_status,
        )
        return

    # Compare-and-set: проверяем telegram_poll_id (защита от пересоздания)
    if current_tg_poll_id != telegram_poll_id:
        logger.info(
            "AUTO_CLOSE_POLL poll_id=%s — telegram_poll_id изменился (%s -> %s), пропускаем",
            poll_id,
            telegram_poll_id[:16] if telegram_poll_id else None,
            current_tg_poll_id[:16] if current_tg_poll_id else None,
        )
        return

    # Закрываем опрос в БД
    await PollsRepo.close_poll(poll_id)
    logger.info("AUTO_CLOSE_POLL poll_id=%s — закрыт в БД", poll_id)

    # Останавливаем Telegram poll (best-effort)
    if _bot_instance:
        try:
            await _bot_instance.stop_poll(chat_id=chat_id, message_id=message_id)
            logger.info(
                "AUTO_CLOSE_POLL poll_id=%s — Telegram poll остановлен",
                poll_id,
            )
        except Exception as e:
            error_str = str(e).lower()
            if (
                "poll has already been closed" in error_str
                or "poll_already_closed" in error_str
                or "poll can't be stopped" in error_str
                or "poll cant be stopped" in error_str
            ):
                logger.info(
                    "AUTO_CLOSE_POLL poll_id=%s — Telegram poll уже закрыт",
                    poll_id,
                )
            else:
                logger.warning(
                    "AUTO_CLOSE_POLL poll_id=%s — ошибка остановки Telegram poll: %s",
                    poll_id,
                    e,
                )

        # Открепляем сообщение (best-effort)
        if chat_id < 0:  # Только для групп
            try:
                await _bot_instance.unpin_chat_message(chat_id=chat_id, message_id=message_id)
                logger.debug("AUTO_CLOSE_POLL poll_id=%s — сообщение откреплено", poll_id)
            except Exception as e:
                logger.debug("AUTO_CLOSE_POLL poll_id=%s — не удалось открепить: %s", poll_id, e)

        # Удаляем/редактируем reminder (best-effort)
        reminder_message_id = await PollsRepo.get_reminder_message_id(poll_id)
        if reminder_message_id:
            try:
                await _bot_instance.delete_message(chat_id=chat_id, message_id=reminder_message_id)
                logger.debug("AUTO_CLOSE_POLL poll_id=%s — reminder удалён", poll_id)
            except Exception as e:
                logger.debug("AUTO_CLOSE_POLL poll_id=%s — не удалось удалить reminder: %s", poll_id, e)
                try:
                    await _bot_instance.edit_message_text(
                        chat_id=chat_id,
                        message_id=reminder_message_id,
                        text="✅ Опрос закрыт.",
                    )
                except Exception as e2:
                    logger.debug("AUTO_CLOSE_POLL poll_id=%s — не удалось отредактировать reminder: %s", poll_id, e2)
            await PollsRepo.clear_reminder_message_id(poll_id)
    else:
        logger.warning("AUTO_CLOSE_POLL poll_id=%s — бот не инициализирован", poll_id)

    # Ждём доставки последних голосов от Telegram, затем проверки
    if _bot_instance:
        await asyncio.sleep(10)
        tz = get_tzinfo(SCHEDULER_TIMEZONE)
        fallback_date = datetime.now(tz).date()
        check_date = poll.get("poll_date") if isinstance(poll.get("poll_date"), date) else fallback_date
        try:
            await asyncio.wait_for(
                NotifyService.run_post_poll_checks(
                    _bot_instance,
                    check_date,
                    department_id=OZKO_DEPARTMENT_ID,
                    poll_id=poll_id,
                ),
                timeout=120,
            )
        except Exception as e:
            logger.error("AUTO_CLOSE_POLL poll_id=%s — ошибка отправки нарушений: %s", poll_id, e)
        try:
            from apps.attendance_bot.app.services.poll_runtime import (
                send_department_lists_summary,
                send_poll_close_summary,
            )

            await send_poll_close_summary(
                _bot_instance,
                poll,
                department_id=OZKO_DEPARTMENT_ID,
            )
            await send_department_lists_summary(_bot_instance, poll)
        except Exception as e:
            logger.error("AUTO_CLOSE_POLL poll_id=%s — ошибка отправки итогов: %s", poll_id, e)


def schedule_poll_auto_close(
    scheduler: AsyncIOScheduler,
    poll_id: int,
    closes_at: datetime,
    chat_id: int,
    message_id: int,
    telegram_poll_id: str,
) -> bool:
    """
    Запланировать автозакрытие опроса.

    Args:
        scheduler: Экземпляр APScheduler.
        poll_id: ID опроса.
        closes_at: Время закрытия (с таймзоной).
        chat_id: ID чата.
        message_id: ID сообщения.
        telegram_poll_id: Telegram poll ID.

    Returns:
        True если job запланирован.
    """
    if scheduler is None:
        logger.warning("SCHEDULE_POLL_AUTOCLOSE poll_id=%s — scheduler не активен", poll_id)
        return False

    job_id = get_poll_auto_close_job_id(poll_id)
    tz = get_tzinfo(SCHEDULER_TIMEZONE)

    # Убедимся, что closes_at имеет таймзону
    if closes_at.tzinfo is None:
        closes_at = closes_at.replace(tzinfo=tz)

    try:
        scheduler.add_job(
            job_poll_auto_close,
            DateTrigger(run_date=closes_at),
            id=job_id,
            name=f"Poll auto-close #{poll_id}",
            replace_existing=True,
            kwargs={
                "poll_id": poll_id,
                "chat_id": chat_id,
                "message_id": message_id,
                "telegram_poll_id": telegram_poll_id,
            },
        )
        logger.info(
            "SCHEDULE_POLL_AUTOCLOSE poll_id=%s, closes_at=%s, job_id=%s",
            poll_id,
            closes_at.strftime("%Y-%m-%d %H:%M:%S %Z"),
            job_id,
        )
        return True
    except Exception as e:
        logger.error("SCHEDULE_POLL_AUTOCLOSE poll_id=%s — ошибка: %s", poll_id, e)
        return False


def remove_poll_auto_close_job(scheduler: AsyncIOScheduler | None, poll_id: int) -> bool:
    """
    Удалить job автозакрытия опроса.

    Args:
        scheduler: Экземпляр APScheduler.
        poll_id: ID опроса.

    Returns:
        True если job удалён.
    """
    if scheduler is None:
        return False

    job_id = get_poll_auto_close_job_id(poll_id)

    try:
        scheduler.remove_job(job_id)
        logger.info("REMOVE_POLL_AUTOCLOSE poll_id=%s, job_id=%s", poll_id, job_id)
        return True
    except Exception:
        # Job не найден — это нормально
        logger.debug("REMOVE_POLL_AUTOCLOSE poll_id=%s — job не найден", poll_id)
        return False


async def restore_poll_auto_close_jobs(scheduler: AsyncIOScheduler | None, bot: Any) -> int:
    """
    Восстановить job'ы автозакрытия для открытых опросов при старте бота.

    Если текущее время > closes_at — закрывает опрос немедленно.
    Иначе — планирует job на closes_at.

    Args:
        scheduler: Экземпляр APScheduler.
        bot: Экземпляр бота.

    Returns:
        Количество восстановленных/обработанных опросов.
    """
    if scheduler is None:
        logger.info("RESTORE_POLL_AUTOCLOSE — scheduler не активен, пропускаем")
        return 0

    # Сохраняем бота для использования в job'ах
    set_bot_instance(bot)

    # Получаем открытый опрос
    poll = await PollsRepo.get_open_poll()

    if not poll:
        logger.debug("RESTORE_POLL_AUTOCLOSE — нет открытых опросов")
        return 0

    poll_id = poll.get("id")
    closes_at = poll.get("closes_at")
    chat_id = poll.get("chat_id")
    message_id = poll.get("message_id")
    telegram_poll_id = poll.get("telegram_poll_id")

    if not all([poll_id, closes_at, chat_id, message_id, telegram_poll_id]):
        logger.warning(
            "RESTORE_POLL_AUTOCLOSE poll_id=%s — неполные данные, пропускаем",
            poll_id,
        )
        return 0

    tz = get_tzinfo(SCHEDULER_TIMEZONE)
    now = datetime.now(tz)

    # Убедимся, что closes_at имеет таймзону
    if closes_at.tzinfo is None:
        closes_at = closes_at.replace(tzinfo=tz)

    if now > closes_at:
        # Время уже прошло — закрываем немедленно
        logger.info(
            "RESTORE_POLL_AUTOCLOSE poll_id=%s — время истекло (closes_at=%s, now=%s), закрываем",
            poll_id,
            closes_at.strftime("%H:%M:%S"),
            now.strftime("%H:%M:%S"),
        )
        await job_poll_auto_close(poll_id, chat_id, message_id, telegram_poll_id)
    else:
        # Планируем на closes_at
        schedule_poll_auto_close(
            scheduler=scheduler,
            poll_id=poll_id,
            closes_at=closes_at,
            chat_id=chat_id,
            message_id=message_id,
            telegram_poll_id=telegram_poll_id,
        )
        logger.info(
            "RESTORE_POLL_AUTOCLOSE poll_id=%s — восстановлен на %s",
            poll_id,
            closes_at.strftime("%H:%M:%S"),
        )

    return 1


# =============================================================================
# Poll reminder jobs
# =============================================================================


def get_poll_reminder_job_id(poll_id: int) -> str:
    """Сформировать ID job'а для reminder опроса."""
    return f"poll_reminder:{poll_id}"


async def job_poll_reminder(
    poll_id: int,
    poll_date_str: str,
    telegram_poll_id: str,
) -> None:
    """
    Задача: отправка напоминания об опросе явки.

    Вызывается APScheduler'ом по времени (DateTrigger).
    Использует _bot_instance для отправки сообщения.

    Args:
        poll_id: ID опроса в БД.
        poll_date_str: Дата опроса в формате ISO (YYYY-MM-DD).
        telegram_poll_id: ID опроса в Telegram (для проверки актуальности).
    """
    if _bot_instance is None:
        logger.warning("POLL_REMINDER poll_id=%s — бот не инициализирован", poll_id)
        return

    from apps.attendance_bot.app.services.poll_runtime import send_poll_reminder

    poll_date = date.fromisoformat(poll_date_str)
    await send_poll_reminder(_bot_instance, poll_id, poll_date, telegram_poll_id)


def schedule_poll_reminder(
    scheduler: AsyncIOScheduler,
    poll_id: int,
    remind_at: datetime,
    poll_date: date,
    telegram_poll_id: str,
) -> bool:
    """
    Запланировать отправку напоминания об опросе.

    Args:
        scheduler: Экземпляр APScheduler.
        poll_id: ID опроса.
        remind_at: Время отправки напоминания (с таймзоной).
        poll_date: Дата опроса.
        telegram_poll_id: Telegram poll ID (для compare-and-set).

    Returns:
        True если job запланирован.
    """
    if scheduler is None:
        logger.warning("SCHEDULE_POLL_REMINDER poll_id=%s — scheduler не активен", poll_id)
        return False

    job_id = get_poll_reminder_job_id(poll_id)
    tz = get_tzinfo(SCHEDULER_TIMEZONE)

    if remind_at.tzinfo is None:
        remind_at = remind_at.replace(tzinfo=tz)

    try:
        scheduler.add_job(
            job_poll_reminder,
            DateTrigger(run_date=remind_at),
            id=job_id,
            name=f"Poll reminder #{poll_id}",
            replace_existing=True,
            kwargs={
                "poll_id": poll_id,
                "poll_date_str": poll_date.isoformat(),
                "telegram_poll_id": telegram_poll_id,
            },
        )
        logger.info(
            "SCHEDULE_POLL_REMINDER poll_id=%s, remind_at=%s, job_id=%s",
            poll_id,
            remind_at.strftime("%Y-%m-%d %H:%M:%S %Z"),
            job_id,
        )
        return True
    except Exception as e:
        logger.error("SCHEDULE_POLL_REMINDER poll_id=%s — ошибка: %s", poll_id, e)
        return False


def remove_poll_reminder_job(scheduler: AsyncIOScheduler | None, poll_id: int) -> bool:
    """
    Удалить job напоминания опроса.

    Args:
        scheduler: Экземпляр APScheduler.
        poll_id: ID опроса.

    Returns:
        True если job удалён.
    """
    if scheduler is None:
        return False

    job_id = get_poll_reminder_job_id(poll_id)

    try:
        scheduler.remove_job(job_id)
        logger.info("REMOVE_POLL_REMINDER poll_id=%s, job_id=%s", poll_id, job_id)
        return True
    except Exception:
        logger.debug("REMOVE_POLL_REMINDER poll_id=%s — job не найден", poll_id)
        return False


async def restore_poll_reminder_jobs(scheduler: AsyncIOScheduler | None, bot: Any) -> int:
    """
    Восстановить job напоминания для открытого опроса при старте бота.

    Если reminder ещё не отправлен и время ещё не пришло — планирует job.
    Если время уже прошло — отправляет немедленно.

    Args:
        scheduler: Экземпляр APScheduler.
        bot: Экземпляр бота.

    Returns:
        1 если job восстановлен/выполнен, 0 иначе.
    """
    if scheduler is None:
        logger.info("RESTORE_POLL_REMINDER — scheduler не активен, пропускаем")
        return 0

    poll = await PollsRepo.get_open_poll()
    if not poll:
        return 0

    # Если reminder уже отправлен — не нужно восстанавливать
    if poll.get("reminder_sent_at"):
        logger.debug("RESTORE_POLL_REMINDER — reminder уже отправлен, пропускаем")
        return 0

    poll_id = poll.get("id")
    closes_at = poll.get("closes_at")
    telegram_poll_id = poll.get("telegram_poll_id")
    poll_date = poll.get("poll_date")

    if not all([poll_id, closes_at, telegram_poll_id, poll_date]):
        logger.debug("RESTORE_POLL_REMINDER — неполные данные опроса, пропускаем")
        return 0

    # Получаем reminder_before_end_minutes из конфига
    from core.screw_core.repos.poll_config_repo import PollConfigRepo

    config = await PollConfigRepo.get_config()
    reminder_minutes = config.get("reminder_before_end_minutes")
    if not reminder_minutes:
        logger.debug("RESTORE_POLL_REMINDER — reminder не настроен")
        return 0

    tz = get_tzinfo(SCHEDULER_TIMEZONE)
    now = datetime.now(tz)

    if closes_at.tzinfo is None:
        closes_at = closes_at.replace(tzinfo=tz)

    remind_at = closes_at - timedelta(seconds=reminder_minutes * 60)

    if remind_at <= now:
        # Время напоминания уже прошло — отправляем немедленно
        logger.info(
            "RESTORE_POLL_REMINDER poll_id=%s — время прошло (remind_at=%s), отправляем немедленно",
            poll_id,
            remind_at.strftime("%H:%M:%S"),
        )
        await job_poll_reminder(poll_id, poll_date.isoformat(), telegram_poll_id)
    else:
        schedule_poll_reminder(
            scheduler=scheduler,
            poll_id=poll_id,
            remind_at=remind_at,
            poll_date=poll_date,
            telegram_poll_id=telegram_poll_id,
        )
        logger.info(
            "RESTORE_POLL_REMINDER poll_id=%s — восстановлен на %s",
            poll_id,
            remind_at.strftime("%H:%M:%S"),
        )

    return 1


# =============================================================================
# Monthly stats job
# =============================================================================


async def _job_monthly_stats() -> None:
    """Публикация ежемесячной статистики в leadership/violations chat 1 числа."""
    from apps.attendance_bot.app.jobs.monthly_stats import send_monthly_stats_to_violations_chat

    tz = get_tzinfo(SCHEDULER_TIMEZONE)
    today = datetime.now(tz).date()

    # Статистика за предыдущий месяц
    if today.month == 1:
        year, month = today.year - 1, 12
    else:
        year, month = today.year, today.month - 1

    logger.info("monthly_stats: запуск за %d-%02d", year, month)

    try:
        if _bot_instance:
            success, status, chat_id = await send_monthly_stats_to_violations_chat(
                _bot_instance,
                year,
                month,
                automatic=True,
            )
            if success:
                logger.info("monthly_stats: %s chat_id=%s", status, chat_id)
            else:
                logger.info("monthly_stats: %s", status)
        else:
            logger.warning("monthly_stats: бот не инициализирован")
    except Exception as e:
        logger.exception("monthly_stats: ошибка — %s", e)


# =============================================================================
# Scheduler setup
# =============================================================================


def create_scheduler() -> AsyncIOScheduler | None:
    """
    Создать и настроить APScheduler.

    Returns:
        AsyncIOScheduler или None если выключен.
    """
    global _scheduler_instance
    if not is_scheduler_enabled():
        logger.info("scheduler: выключен (SCHEDULER_ENABLED=0)")
        _scheduler_instance = None
        return None

    tz = get_tzinfo(SCHEDULER_TIMEZONE)
    scheduler = AsyncIOScheduler(timezone=tz)
    _scheduler_instance = scheduler

    jobs_registered = []

    # Job 1: Daily report (каждый день в 09:35, внутри проверка на рабочий день)
    if is_daily_report_enabled():
        if not is_ews_configured():
            logger.warning(
                "scheduler: daily_report включён, но EWS не настроен — "
                "проверьте EXCHANGE_EWS_URL, EXCHANGE_USERNAME, EXCHANGE_PASSWORD, REPORT_TO"
            )
        else:
            hour = get_daily_report_hour()
            minute = get_daily_report_minute()

            scheduler.add_job(
                job_daily_report,
                CronTrigger(hour=hour, minute=minute, timezone=tz),
                id="daily_report",
                name="Daily XLSX report",
                replace_existing=True,
            )
            jobs_registered.append(f"daily_report ({hour:02d}:{minute:02d} workdays)")
    else:
        logger.info("scheduler: daily_report выключен (REPORT_MAIL_ENABLED=0)")

    # Job 2: Monthly report (каждый день в 10:00, внутри проверка на последний день месяца)
    if is_monthly_report_enabled():
        if not is_ews_configured():
            logger.warning(
                "scheduler: monthly_report включён, но EWS не настроен — "
                "проверьте EXCHANGE_EWS_URL, EXCHANGE_USERNAME, EXCHANGE_PASSWORD, REPORT_TO"
            )
        else:
            hour = get_monthly_report_hour()
            minute = get_monthly_report_minute()

            # Запускаем каждый день, но проверяем внутри job_monthly_report_check
            scheduler.add_job(
                job_monthly_report_check,
                CronTrigger(hour=hour, minute=minute, timezone=tz),
                id="monthly_report",
                name="Monthly XLSX report (last day check)",
                replace_existing=True,
            )
            jobs_registered.append(f"monthly_report ({hour:02d}:{minute:02d} last day)")
    else:
        logger.info("scheduler: monthly_report выключен (REPORT_MONTHLY_ENABLED=0)")

    # Job 3: Monthly stats (1 числа каждого месяца в 10:30)
    from apps.attendance_bot.app.jobs.monthly_stats import is_monthly_stats_enabled
    if is_monthly_stats_enabled():
        scheduler.add_job(
            _job_monthly_stats,
            CronTrigger(day=1, hour=10, minute=30, timezone=tz),
            id="monthly_stats",
            name="Monthly stats to group chat",
            replace_existing=True,
        )
        jobs_registered.append("monthly_stats (1-го в 10:30)")

    # Job 4: Vacation reminder (ежедневно в 18:00 — напоминание о начале отпуска/больничного)
    from apps.attendance_bot.app.jobs.vacation_reminder import (
        is_vacation_reminder_enabled,
        send_vacation_reminders,
    )
    if is_vacation_reminder_enabled():
        async def _job_vacation_reminder():
            if _bot_instance is None:
                return
            await send_vacation_reminders(_bot_instance)

        scheduler.add_job(
            _job_vacation_reminder,
            CronTrigger(hour=18, minute=0, timezone=tz),
            id="vacation_reminder",
            name="Vacation reminder DM (daily 18:00)",
            replace_existing=True,
        )
        jobs_registered.append("vacation_reminder (18:00)")

    # Job 5: Legacy fixed-time violations check.
    # Основной путь отправки нарушений теперь привязан к закрытию конкретного poll_run.
    violations_check_enabled = os.getenv("VIOLATIONS_FIXED_CHECK_ENABLED", "0") == "1"
    if violations_check_enabled:
        violations_hour = int(os.getenv("VIOLATIONS_FIXED_CHECK_HOUR", "9"))
        violations_minute = int(os.getenv("VIOLATIONS_FIXED_CHECK_MINUTE", "35"))

        async def _job_daily_violations_check():
            if _bot_instance is None:
                logger.warning("daily_violations_check: bot not initialized")
                return
            from apps.attendance_bot.app.jobs.violations_sender import send_violations_to_chat

            tz_local = get_tzinfo(SCHEDULER_TIMEZONE)
            today = datetime.now(tz_local).date()

            # Проверяем, был ли опрос за сегодня — если нет, нарушения не проверяем
            poll = await PollsRepo.get_by_date(today)
            if not poll:
                logger.info("daily_violations_check: нет опроса за %s, пропускаем", today)
                return

            try:
                result = await ChecksService.run_daily_checks(
                    check_date=today,
                    poll_id=poll.get("id") if poll else None,
                    department_id=OZKO_DEPARTMENT_ID,
                    bot=_bot_instance,
                )
                logger.info(
                    "daily_violations_check: users=%d, no_checkin=%d",
                    result.total_users,
                    result.no_checkin_count,
                )
            except Exception as e:
                logger.exception("daily_violations_check: ошибка checks — %s", e)
                return

            try:
                await send_violations_to_chat(_bot_instance, today)
            except Exception as e:
                logger.exception("daily_violations_check: ошибка отправки — %s", e)

        scheduler.add_job(
            _job_daily_violations_check,
            CronTrigger(hour=violations_hour, minute=violations_minute, timezone=tz),
            id="legacy_daily_violations_check",
            name=f"Legacy daily violations check ({violations_hour:02d}:{violations_minute:02d} MSK)",
            replace_existing=True,
        )
        jobs_registered.append(f"legacy_daily_violations_check ({violations_hour:02d}:{violations_minute:02d})")
    else:
        logger.info("scheduler: fixed daily violations check выключен; violations отправляются при закрытии опроса")

    # Job 6: Birthday notification (ежедневно в 08:00)
    from apps.attendance_bot.app.jobs.birthday_notify import job_birthday_notify

    async def _job_birthday_notify():
        if _bot_instance is None:
            return
        await job_birthday_notify(_bot_instance)

    scheduler.add_job(
        _job_birthday_notify,
        CronTrigger(hour=8, minute=0, timezone=tz),
        id="birthday_notify",
        name="Birthday notification (daily 08:00 MSK)",
        replace_existing=True,
    )
    jobs_registered.append("birthday_notify (08:00)")

    # Job 7: Flud pulse digests and retention cleanup (opt-in).
    if is_flud_enabled():
        from apps.attendance_bot.app.jobs.flud_digest import (
            cleanup_flud_data,
            send_monthly_flud_digest,
            send_weekly_flud_digest,
        )

        if get_flud_weekly_digest_enabled():
            async def _job_weekly_flud_digest():
                if _bot_instance is None:
                    logger.warning("flud_weekly_digest: bot not initialized")
                    return
                await send_weekly_flud_digest(_bot_instance)

            weekly_day = get_flud_weekly_digest_day_of_week()
            weekly_hour = get_flud_weekly_digest_hour()
            weekly_minute = get_flud_weekly_digest_minute()
            scheduler.add_job(
                _job_weekly_flud_digest,
                CronTrigger(day_of_week=weekly_day, hour=weekly_hour, minute=weekly_minute, timezone=tz),
                id="flud_weekly_digest",
                name="Flud weekly digest",
                replace_existing=True,
            )
            jobs_registered.append(f"flud_weekly_digest (dow={weekly_day} {weekly_hour:02d}:{weekly_minute:02d})")

        if get_flud_monthly_digest_enabled():
            async def _job_monthly_flud_digest():
                if _bot_instance is None:
                    logger.warning("flud_monthly_digest: bot not initialized")
                    return
                await send_monthly_flud_digest(_bot_instance, previous=True)

            monthly_hour = get_flud_monthly_digest_hour()
            monthly_minute = get_flud_monthly_digest_minute()
            scheduler.add_job(
                _job_monthly_flud_digest,
                CronTrigger(day=1, hour=monthly_hour, minute=monthly_minute, timezone=tz),
                id="flud_monthly_digest",
                name="Flud monthly digest",
                replace_existing=True,
            )
            jobs_registered.append(f"flud_monthly_digest (1-го {monthly_hour:02d}:{monthly_minute:02d})")

        if get_flud_retention_days() > 0:
            scheduler.add_job(
                cleanup_flud_data,
                CronTrigger(hour=3, minute=20, timezone=tz),
                id="flud_cleanup",
                name="Flud data cleanup (daily 03:20 MSK)",
                replace_existing=True,
            )
            jobs_registered.append("flud_cleanup (03:20)")
    else:
        logger.info("scheduler: flud pulse выключен (FLUD_ENABLED=0)")

    # Job 8: Events cleanup (воскресенье в 03:00)
    from apps.attendance_bot.app.jobs.events_cleanup import job_cleanup_diagnostic_events

    scheduler.add_job(
        job_cleanup_diagnostic_events,
        CronTrigger(day_of_week="sun", hour=3, minute=0, timezone=tz),
        id="events_cleanup",
        name="Diagnostic events cleanup (Sun 03:00 MSK)",
        replace_existing=True,
    )
    jobs_registered.append("events_cleanup (Sun 03:00)")

    # Job 9: Heartbeat (каждые 30 сек) — пишет timestamp в /tmp для Docker healthcheck.
    # Если event loop зависнет, heartbeat тоже встанет, Docker перезапустит контейнер.
    scheduler.add_job(
        _job_heartbeat,
        IntervalTrigger(seconds=30),
        id="heartbeat",
        name="Heartbeat (30s)",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )
    jobs_registered.append("heartbeat (30s)")

    scheduler.add_job(
        _job_telegram_health,
        IntervalTrigger(minutes=5),
        id="telegram_health",
        name="Telegram API health (5m)",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )
    jobs_registered.append("telegram_health (5m)")

    if jobs_registered:
        logger.info(
            "scheduler: создан с задачами [%s] (timezone=%s)",
            ", ".join(jobs_registered),
            SCHEDULER_TIMEZONE,
        )
    else:
        logger.warning("scheduler: создан, но нет активных задач")

    return scheduler


def get_scheduler_status() -> dict[str, object]:
    """Return scheduler state and next run times without exposing job arguments."""
    enabled = is_scheduler_enabled()
    job_ids: list[str] = []
    jobs: list[dict[str, object]] = []
    running = False
    if _scheduler_instance is not None:
        running = bool(_scheduler_instance.running)
        for job in _scheduler_instance.get_jobs():
            job_ids.append(job.id)
            jobs.append({"id": job.id, "next_run_at": job.next_run_time})
    return {"enabled": enabled, "running": running, "job_ids": job_ids, "jobs": jobs}


def get_heartbeat_status() -> dict[str, object]:
    """Return heartbeat freshness for the admin diagnostic screen."""
    try:
        age_seconds = max(0, int(time.time() - HEARTBEAT_FILE.stat().st_mtime))
    except OSError:
        return {"fresh": False, "age_seconds": None}
    return {"fresh": age_seconds < 120, "age_seconds": age_seconds}


def get_telegram_health_status() -> dict[str, object]:
    """Return a copy of the last Telegram API diagnostic result."""
    return dict(_telegram_health_state)


POLL_AUTO_SEND_JOB_ID = "poll_auto_send"


async def reschedule_poll_auto_send(
    scheduler: AsyncIOScheduler | None = None,
) -> None:
    """
    (Пере)планировать job авто-отправки опроса по конфигу из БД.

    Вызывается при старте бота и при изменении настроек из UI.
    """
    if scheduler is None:
        scheduler = _scheduler_instance
    if scheduler is None:
        logger.info("reschedule_poll_auto_send: scheduler not active")
        return

    from core.screw_core.repos.poll_config_repo import PollConfigRepo

    config = await PollConfigRepo.get_config()
    enabled = config.get("auto_send_enabled", False)
    auto_time = config.get("auto_send_time")  # datetime.time or None

    # Удаляем старый job если есть
    try:
        scheduler.remove_job(POLL_AUTO_SEND_JOB_ID)
    except Exception:
        pass  # Job не существует — ожидаемо при первом запуске

    if not enabled or not auto_time:
        logger.info(
            "reschedule_poll_auto_send: not scheduling (enabled=%s, time=%s)",
            enabled,
            auto_time,
        )
        return

    tz = get_tzinfo(SCHEDULER_TIMEZONE)

    async def _job_poll_auto_send():
        if _bot_instance is None:
            logger.warning("poll_auto_send: bot not initialized")
            return
        from apps.attendance_bot.app.jobs.poll_auto_send import job_poll_auto_send

        await job_poll_auto_send(_bot_instance)

    scheduler.add_job(
        _job_poll_auto_send,
        CronTrigger(
            hour=auto_time.hour,
            minute=auto_time.minute,
            day_of_week="mon-fri",
            timezone=tz,
        ),
        id=POLL_AUTO_SEND_JOB_ID,
        name=f"Poll auto-send ({auto_time.strftime('%H:%M')} MSK, mon-fri)",
        replace_existing=True,
    )
    logger.info(
        "reschedule_poll_auto_send: scheduled at %s MSK (mon-fri)",
        auto_time.strftime("%H:%M"),
    )


from core.screw_core.utils.scheduler_helpers import start_scheduler, shutdown_scheduler  # noqa: E402, F401
