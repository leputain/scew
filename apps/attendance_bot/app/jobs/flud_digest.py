"""Scheduled publication of flud chat pulse digests."""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta
from typing import Any

from apps.attendance_bot.app.config import (
    get_flud_chat_id,
    get_flud_digest_chat_id,
    get_flud_digest_message_links_enabled,
    get_flud_message_thread_id,
    get_flud_retention_days,
    get_flud_top_limit,
)
from core.screw_core.repos.flud_repo import FludRepo
from core.screw_core.services.flud_stats import (
    build_monthly_digest_text,
    build_weekly_digest_text,
)
from core.screw_core.utils.logging import get_logger
from core.screw_core.utils.timezone import get_tzinfo

logger = get_logger(__name__)

_MONTH_ALIASES = {
    "январь": 1,
    "января": 1,
    "февраль": 2,
    "февраля": 2,
    "март": 3,
    "марта": 3,
    "апрель": 4,
    "апреля": 4,
    "май": 5,
    "мая": 5,
    "июнь": 6,
    "июня": 6,
    "июль": 7,
    "июля": 7,
    "август": 8,
    "августа": 8,
    "сентябрь": 9,
    "сентября": 9,
    "октябрь": 10,
    "октября": 10,
    "ноябрь": 11,
    "ноября": 11,
    "декабрь": 12,
    "декабря": 12,
}


def _today_msk() -> date:
    return datetime.now(get_tzinfo("Europe/Moscow")).date()


def current_week_period(today: date | None = None) -> tuple[date, date]:
    end = today or _today_msk()
    return end - timedelta(days=6), end


def current_month_period(today: date | None = None) -> tuple[int, int, date, date]:
    value = today or _today_msk()
    return month_period(value.year, value.month)


def month_period(year: int, month: int) -> tuple[int, int, date, date]:
    start = date(year, month, 1)
    if month == 12:
        next_month = date(year + 1, 1, 1)
    else:
        next_month = date(year, month + 1, 1)
    return year, month, start, next_month - timedelta(days=1)


def previous_month_period(today: date | None = None) -> tuple[int, int, date, date]:
    value = today or _today_msk()
    first_this_month = date(value.year, value.month, 1)
    end = first_this_month - timedelta(days=1)
    return current_month_period(end)


def parse_flud_month_arg(value: str | None, *, today: date | None = None) -> tuple[int, int] | None:
    """Parse admin month arguments like 2026-06, 06.2026, 06 or июнь."""
    current = today or _today_msk()
    raw = (value or "").strip().lower()
    if not raw:
        return current.year, current.month

    month_by_name = _MONTH_ALIASES.get(raw)
    if month_by_name is not None:
        return current.year, month_by_name

    match = re.fullmatch(r"(\d{4})[-./](\d{1,2})", raw)
    if match:
        year = int(match.group(1))
        month = int(match.group(2))
        return (year, month) if 1 <= month <= 12 else None

    match = re.fullmatch(r"(\d{1,2})[-./](\d{4})", raw)
    if match:
        month = int(match.group(1))
        year = int(match.group(2))
        return (year, month) if 1 <= month <= 12 else None

    match = re.fullmatch(r"(\d{1,2})", raw)
    if match:
        month = int(match.group(1))
        return (current.year, month) if 1 <= month <= 12 else None

    return None


async def _send_digest_html_message(bot: Any, *, chat_id: int, text: str) -> Any:
    kwargs: dict[str, Any] = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML",
    }
    try:
        from aiogram.types import LinkPreviewOptions

        kwargs["link_preview_options"] = LinkPreviewOptions(is_disabled=True)
    except Exception:
        kwargs["disable_web_page_preview"] = True
    return await bot.send_message(**kwargs)


async def _publish_digest(
    bot: Any,
    *,
    digest_type: str,
    period_start: date,
    period_end: date,
    text: str,
    created_by: int | None = None,
) -> tuple[bool, str, int | None]:
    flud_chat_id = get_flud_chat_id()
    if not flud_chat_id:
        logger.warning("flud_digest: FLUD_CHAT_ID не настроен")
        return False, "FLUD_CHAT_ID не настроен", None

    target_chat_id = get_flud_digest_chat_id()
    if not target_chat_id:
        logger.warning("flud_digest: digest chat не настроен")
        return False, "digest chat не настроен", None

    run_id = await FludRepo.create_digest_run(
        digest_type=digest_type,
        period_start=period_start,
        period_end=period_end,
        chat_id=flud_chat_id,
        target_chat_id=target_chat_id,
        created_by=created_by,
    )
    try:
        message = await _send_digest_html_message(
            bot,
            chat_id=target_chat_id,
            text=text,
        )
        message_id = getattr(message, "message_id", None)
        await FludRepo.mark_digest_published(run_id, target_chat_id, message_id)
        logger.info("flud_digest: %s published run_id=%s", digest_type, run_id)
        return True, "published", target_chat_id
    except Exception as exc:
        error = str(exc)[:200]
        await FludRepo.mark_digest_failed(run_id, error)
        logger.warning("flud_digest: %s failed: %s", digest_type, error)
        return False, error, target_chat_id


async def build_current_weekly_flud_digest() -> tuple[str | None, date, date]:
    flud_chat_id = get_flud_chat_id()
    period_start, period_end = current_week_period()
    if not flud_chat_id:
        return None, period_start, period_end
    text = await build_weekly_digest_text(
        period_start,
        period_end,
        flud_chat_id,
        top_limit=get_flud_top_limit(),
        include_message_links=get_flud_digest_message_links_enabled(),
        message_thread_id=get_flud_message_thread_id(),
    )
    return text, period_start, period_end


async def build_current_monthly_flud_digest() -> tuple[str | None, int, int, date, date]:
    flud_chat_id = get_flud_chat_id()
    year, month, period_start, period_end = current_month_period()
    if not flud_chat_id:
        return None, year, month, period_start, period_end
    text = await build_monthly_digest_text(
        year,
        month,
        flud_chat_id,
        top_limit=get_flud_top_limit(),
        include_message_links=get_flud_digest_message_links_enabled(),
        message_thread_id=get_flud_message_thread_id(),
    )
    return text, year, month, period_start, period_end


async def build_monthly_flud_digest_for_month(year: int, month: int) -> tuple[str | None, int, int, date, date]:
    flud_chat_id = get_flud_chat_id()
    _, _, period_start, period_end = month_period(year, month)
    if not flud_chat_id:
        return None, year, month, period_start, period_end
    text = await build_monthly_digest_text(
        year,
        month,
        flud_chat_id,
        top_limit=get_flud_top_limit(),
        include_message_links=get_flud_digest_message_links_enabled(),
        message_thread_id=get_flud_message_thread_id(),
    )
    return text, year, month, period_start, period_end


async def send_weekly_flud_digest(bot: Any, *, created_by: int | None = None) -> tuple[bool, str, int | None]:
    text, period_start, period_end = await build_current_weekly_flud_digest()
    if text is None:
        logger.warning("flud_digest: weekly skipped, FLUD_CHAT_ID не настроен")
        return False, "FLUD_CHAT_ID не настроен", None
    return await _publish_digest(
        bot,
        digest_type="weekly",
        period_start=period_start,
        period_end=period_end,
        text=text,
        created_by=created_by,
    )


async def send_monthly_flud_digest(
    bot: Any,
    *,
    created_by: int | None = None,
    previous: bool = False,
    year: int | None = None,
    month: int | None = None,
) -> tuple[bool, str, int | None]:
    flud_chat_id = get_flud_chat_id()
    if not flud_chat_id:
        logger.warning("flud_digest: monthly skipped, FLUD_CHAT_ID не настроен")
        return False, "FLUD_CHAT_ID не настроен", None

    if year is not None and month is not None:
        _, _, period_start, period_end = month_period(year, month)
    elif previous:
        year, month, period_start, period_end = previous_month_period()
    else:
        year, month, period_start, period_end = current_month_period()
    text = await build_monthly_digest_text(
        year,
        month,
        flud_chat_id,
        top_limit=get_flud_top_limit(),
        include_message_links=get_flud_digest_message_links_enabled(),
        message_thread_id=get_flud_message_thread_id(),
    )
    return await _publish_digest(
        bot,
        digest_type="monthly",
        period_start=period_start,
        period_end=period_end,
        text=text,
        created_by=created_by,
    )


async def cleanup_flud_data() -> int:
    retention_days = get_flud_retention_days()
    if retention_days <= 0:
        logger.debug("flud_cleanup: disabled")
        return 0
    deleted = await FludRepo.cleanup_old_flud_data(retention_days)
    logger.info("flud_cleanup: удалено %d строк старше %d дней", deleted, retention_days)
    return deleted
