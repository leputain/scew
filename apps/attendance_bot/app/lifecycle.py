"""Shared attendance_bot runtime lifecycle.

Starts and stops the DB pool, background jobs and APScheduler for aiogram.
"""

from __future__ import annotations

import asyncio
import os
from typing import Any

from apps.attendance_bot.app.bootstrap import bootstrap_admins_if_enabled
from apps.attendance_bot.app.jobs.poll_reaper import start_poll_reaper
from apps.attendance_bot.app.jobs.report_mail_scheduler import (
    start_monthly_report_scheduler,
    start_report_mail_scheduler,
)
from apps.attendance_bot.app.jobs.report_sender import start_report_sender
from apps.attendance_bot.app.scheduler import (
    create_scheduler,
    reschedule_poll_auto_send,
    restore_poll_auto_close_jobs,
    restore_poll_reminder_jobs,
    set_application_instance,
    set_bot_instance,
    shutdown_scheduler,
    start_scheduler,
)
from apps.attendance_bot.app.services.group_preflight import check_attendance_group_access
from core.screw_core.db.pool import check_connection, close_pool, create_pool
from core.screw_core.utils.logging import get_logger

logger = get_logger(__name__)

GIT_SHA = os.getenv("GIT_SHA", "unknown")

_background_tasks: list[asyncio.Task] = []
_scheduler_instance = None


def is_debug_navigation_enabled() -> bool:
    """Проверить, включена ли диагностика навигации (DEBUG_NAVIGATION=1)."""
    return os.getenv("DEBUG_NAVIGATION", "0") == "1"


async def attendance_startup(bot: Any, *, application: Any | None = None) -> None:
    """Start DB pool, background jobs, scheduler and persistent poll jobs."""
    global _background_tasks, _scheduler_instance

    if _background_tasks:
        logger.warning("attendance lifecycle: startup called with %d existing tasks", len(_background_tasks))

    if is_debug_navigation_enabled():
        logger.warning("RUNNING_BUILD git_sha=%s DEBUG_NAVIGATION=1", GIT_SHA)

    logger.info("Запуск attendance_bot...")
    await create_pool()
    await check_connection()

    await bootstrap_admins_if_enabled()
    await check_attendance_group_access(bot)

    reaper_task = start_poll_reaper(bot)
    _background_tasks.append(reaper_task)

    report_sender_task = start_report_sender(bot)
    if report_sender_task:
        _background_tasks.append(report_sender_task)

    report_mail_task = start_report_mail_scheduler()
    if report_mail_task:
        _background_tasks.append(report_mail_task)

    monthly_report_task = start_monthly_report_scheduler()
    if monthly_report_task:
        _background_tasks.append(monthly_report_task)

    scheduler = create_scheduler()
    _scheduler_instance = scheduler

    set_bot_instance(bot)
    set_application_instance(application)

    from apps.attendance_bot.app.services.poll_runtime import set_poll_scheduler

    set_poll_scheduler(scheduler)

    start_scheduler(scheduler)
    await restore_poll_auto_close_jobs(scheduler, bot)
    await restore_poll_reminder_jobs(scheduler, bot)
    await reschedule_poll_auto_send(scheduler)

    logger.info("attendance_bot готов к работе")


async def attendance_shutdown() -> None:
    """Stop scheduler, background jobs and DB pool."""
    global _background_tasks, _scheduler_instance

    logger.info("Остановка attendance_bot...")

    shutdown_scheduler(_scheduler_instance)
    _scheduler_instance = None

    for task in _background_tasks:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
    _background_tasks.clear()

    set_application_instance(None)
    await close_pool()
    logger.info("attendance_bot остановлен")
