"""
Напоминание о начале отпуска/больничного.

Вечером перед началом — DM пользователю:
"Напоминание: завтра начинается ваш ✈️ Отпуск (15.02 — 22.02).
Не забудь списать трудозатраты и оставить СЭД коллеге!"
"""

from __future__ import annotations

import asyncio
import os
from datetime import datetime, timedelta
from typing import Any

from core.screw_core.repos.day_status_repo import DayStatusRepo
from core.screw_core.utils.logging import get_logger
from core.screw_core.utils.timezone import get_tzinfo

logger = get_logger(__name__)

KIND_EMOJI = {
    "VACATION": "✈️",
    "SICK": "🏥",
    "LEAVE": "🕓",
    "DAY_OFF": "📅",
}

KIND_LABELS = {
    "VACATION": "Отпуск",
    "SICK": "Больничный",
    "LEAVE": "Отгул",
    "DAY_OFF": "Отгул",
}

KIND_MESSAGES = {
    "VACATION": "Не забудь списать трудозатраты и оставить СЭД коллеге!\nХорошего отдыха! 🌴",
    "SICK": "Выздоравливайте! 🙏",
}


def is_vacation_reminder_enabled() -> bool:
    return os.getenv("VACATION_REMINDER_ENABLED", "1") == "1"


async def send_vacation_reminders(bot: Any) -> None:
    """Отправить DM-напоминания пользователям, у которых завтра начинается отпуск/больничный."""
    if not is_vacation_reminder_enabled():
        logger.info("vacation_reminder: disabled")
        return

    tz = get_tzinfo("Europe/Moscow")
    tomorrow = (datetime.now(tz) + timedelta(days=1)).date()

    # Получаем все статусы на завтра
    statuses = await DayStatusRepo.get_statuses_for_date(tomorrow)
    if not statuses:
        logger.debug("vacation_reminder: no statuses for %s", tomorrow)
        return

    sent_count = 0
    for status in statuses:
        user_id = status.get("user_id")
        kind = status.get("kind", "")
        telegram_id = status.get("telegram_id")

        if not telegram_id or not user_id:
            continue

        # Напоминаем только для отпуска/больничного
        if kind not in ("VACATION", "SICK"):
            continue

        # Проверяем, что это НАЧАЛО периода (вчера нет такого же статуса)
        yesterday = tomorrow - timedelta(days=1)
        yesterday_status = await DayStatusRepo.get_status(user_id, yesterday)
        if yesterday_status and yesterday_status.get("kind") == kind:
            continue  # Середина периода, не начало

        # Определяем конец периода
        end_date = status.get("end_date", tomorrow)
        emoji = KIND_EMOJI.get(kind, "📌")
        label = KIND_LABELS.get(kind, kind)
        extra = KIND_MESSAGES.get(kind, "")

        if end_date and end_date != tomorrow:
            period = f"{tomorrow.strftime('%d.%m')} — {end_date.strftime('%d.%m')}"
        else:
            period = tomorrow.strftime("%d.%m")

        text = f"📌 Напоминание: завтра начинается ваш {emoji} {label} ({period})."
        if extra:
            text += f"\n{extra}"

        try:
            await bot.send_message(telegram_id, text)
            sent_count += 1
        except Exception as e:
            logger.warning("vacation_reminder: error sending to %s: %s", telegram_id, e)
        # Rate-limit: защита от исчерпания HTTPX connection pool при массовой рассылке
        await asyncio.sleep(0.05)

    logger.info("vacation_reminder: sent=%d reminders for %s", sent_count, tomorrow)
