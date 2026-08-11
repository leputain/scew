"""Native aiogram reports router."""

from __future__ import annotations

from datetime import date, datetime

from aiogram import Router
from aiogram.filters import Command, CommandObject
from aiogram.types import Message

from apps.attendance_bot.app.config import (
    get_admin_telegram_ids,
    get_manager_ozko_telegram_ids,
    get_poll_timezone,
    get_report_mail_enabled,
    get_report_monthly_enabled,
    is_dev_bootstrap_admins_enabled,
    is_email_report_configured,
)
from apps.attendance_bot.app.utils.private_chat_guard import MSG_PRIVATE_ONLY
from core.screw_core.services.permissions import get_user_permissions
from core.screw_core.services.report_delivery_service import ReportDeliveryService
from core.screw_core.services.reports import ReportsService
from core.screw_core.utils.logging import get_logger
from core.screw_core.utils.timezone import get_tzinfo

logger = get_logger(__name__)
router = Router(name="attendance_reports")


async def _reject_if_group_chat(message: Message) -> bool:
    if message.chat.type == "private":
        return False
    logger.info(
        "report_command_blocked_in_group: user=%s chat=%s command=%s",
        message.from_user.id if message.from_user else None,
        message.chat.id,
        (message.text or "")[:50],
    )
    await message.answer(MSG_PRIVATE_ONLY)
    return True


async def is_admin_or_manager_ozko(telegram_id: int) -> bool:
    perms = await get_user_permissions(
        telegram_id,
        admin_ids=get_admin_telegram_ids(),
        manager_ozko_ids=get_manager_ozko_telegram_ids(),
        bootstrap_enabled=is_dev_bootstrap_admins_enabled(),
    )
    if perms.is_admin:
        return True
    return perms.is_manager and perms.department_id == 1


async def is_admin(telegram_id: int) -> bool:
    perms = await get_user_permissions(
        telegram_id,
        admin_ids=get_admin_telegram_ids(),
        manager_ozko_ids=get_manager_ozko_telegram_ids(),
        bootstrap_enabled=is_dev_bootstrap_admins_enabled(),
    )
    return perms.is_admin


def get_local_date() -> date:
    tz = get_tzinfo(get_poll_timezone())
    return datetime.now(tz).date()


@router.message(Command("report_today"))
async def cmd_report_today(message: Message) -> None:
    if not message.from_user:
        return
    if await _reject_if_group_chat(message):
        return

    telegram_id = message.from_user.id
    if not await is_admin_or_manager_ozko(telegram_id):
        logger.warning("aiogram report_today: отказано в доступе для telegram_id=%s", telegram_id)
        await message.answer("Доступ запрещён. Только admin или manager ОЗКО.")
        return

    logger.info("aiogram report_today: запрос от telegram_id=%s", telegram_id)
    report_date = get_local_date()

    try:
        report = await ReportsService.build_daily_report_ozko(report_date)
        await message.answer(report.text)
        logger.info(
            "aiogram report_today: отправлен отчёт telegram_id=%s violations=%d",
            telegram_id,
            report.total_violations,
        )
    except Exception as exc:
        logger.exception("aiogram report_today: ошибка формирования отчёта")
        await message.answer(f"Ошибка формирования отчёта: {str(exc)[:200]}")


@router.message(Command("report_mail"))
async def cmd_report_mail(message: Message, command: CommandObject) -> None:
    if not message.from_user:
        return
    if await _reject_if_group_chat(message):
        return

    telegram_id = message.from_user.id
    if not await is_admin(telegram_id):
        logger.warning("aiogram report_mail: отказано в доступе для telegram_id=%s", telegram_id)
        await message.answer("Доступ запрещён. Только admin.")
        return

    if not get_report_mail_enabled():
        await message.answer("Email-отчёты выключены: REPORT_MAIL_ENABLED=0.")
        return

    if not is_email_report_configured():
        await message.answer(
            "Email-отчёты не настроены.\n"
            "Проверьте: EXCHANGE_EWS_URL, EXCHANGE_USERNAME, EXCHANGE_PASSWORD, REPORT_TO"
        )
        return

    if not command.args:
        await message.answer(
            "Укажите дату в формате YYYY-MM-DD.\n"
            "Пример: /report_mail 2025-01-15"
        )
        return

    date_str = command.args.split()[0].strip()
    try:
        report_date = date.fromisoformat(date_str)
    except ValueError:
        await message.answer(
            f"Неверный формат даты: {date_str}\n"
            "Используйте формат YYYY-MM-DD."
        )
        return

    logger.info("aiogram report_mail: запрос от telegram_id=%s date=%s", telegram_id, report_date)
    await message.answer(f"Отправка email-отчёта за {report_date}...")

    try:
        result = await ReportDeliveryService.retry_daily_report(report_date)
        if result.success:
            await message.answer(
                f"✅ Отчёт за {report_date} отправлен.\n"
                f"Получателей: {result.recipients_count}"
            )
            logger.info(
                "aiogram report_mail: успешно telegram_id=%s date=%s recipients=%d",
                telegram_id,
                report_date,
                result.recipients_count,
            )
        else:
            await message.answer(
                f"❌ Ошибка отправки отчёта за {report_date}.\n"
                f"Причина: {result.error_summary or 'неизвестно'}"
            )
            logger.error(
                "aiogram report_mail: ошибка telegram_id=%s date=%s error=%s",
                telegram_id,
                report_date,
                result.error_summary,
            )
    except Exception as exc:
        logger.exception("aiogram report_mail: критическая ошибка")
        await message.answer(f"Критическая ошибка: {str(exc)[:200]}")


@router.message(Command("report_mail_monthly"))
async def cmd_report_mail_monthly(message: Message, command: CommandObject) -> None:
    if not message.from_user:
        return
    if await _reject_if_group_chat(message):
        return

    telegram_id = message.from_user.id
    if not await is_admin(telegram_id):
        logger.warning("aiogram report_mail_monthly: отказано в доступе для telegram_id=%s", telegram_id)
        await message.answer("Доступ запрещён. Только admin.")
        return

    if not get_report_monthly_enabled():
        await message.answer("Месячные email-отчёты выключены: REPORT_MONTHLY_ENABLED=0.")
        return

    if not is_email_report_configured():
        await message.answer(
            "Email-отчёты не настроены.\n"
            "Проверьте: EXCHANGE_EWS_URL, EXCHANGE_USERNAME, EXCHANGE_PASSWORD, REPORT_TO"
        )
        return

    if not command.args:
        await message.answer(
            "Укажите месяц в формате YYYY-MM.\n"
            "Пример: /report_mail_monthly 2025-01"
        )
        return

    year_month_str = command.args.split()[0].strip()
    try:
        parts = year_month_str.split("-")
        if len(parts) != 2:
            raise ValueError("Неверный формат")
        year = int(parts[0])
        month = int(parts[1])
        if not (1 <= month <= 12):
            raise ValueError("Месяц должен быть от 1 до 12")
        if year < 2020 or year > 2100:
            raise ValueError("Год вне допустимого диапазона")
    except ValueError as exc:
        await message.answer(
            f"Неверный формат: {year_month_str}\n"
            "Используйте формат YYYY-MM (например, 2025-01).\n"
            f"Ошибка: {exc}"
        )
        return

    logger.info(
        "aiogram report_mail_monthly: запрос от telegram_id=%s year=%d month=%d",
        telegram_id,
        year,
        month,
    )
    await message.answer(f"Отправка месячного email-отчёта за {year}-{month:02d}...")

    try:
        result = await ReportDeliveryService.retry_monthly_report(year, month)
        if result.success:
            await message.answer(
                f"✅ Месячный отчёт за {year}-{month:02d} отправлен.\n"
                f"Получателей: {result.recipients_count}"
            )
            logger.info(
                "aiogram report_mail_monthly: успешно telegram_id=%s year=%d month=%d recipients=%d",
                telegram_id,
                year,
                month,
                result.recipients_count,
            )
        else:
            await message.answer(
                f"❌ Ошибка отправки месячного отчёта за {year}-{month:02d}.\n"
                f"Причина: {result.error_summary or 'неизвестно'}"
            )
            logger.error(
                "aiogram report_mail_monthly: ошибка telegram_id=%s year=%d month=%d error=%s",
                telegram_id,
                year,
                month,
                result.error_summary,
            )
    except Exception as exc:
        logger.exception("aiogram report_mail_monthly: критическая ошибка")
        await message.answer(f"Критическая ошибка: {str(exc)[:200]}")
