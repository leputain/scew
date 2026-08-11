"""
Персональные достижения (ачивки) — только ОЗКО.

Проверяются после закрытия опроса. При достижении рубежа —
DM пользователю.

Ачивки привязаны к серии офисных дней подряд и особым условиям.
"""

import os
from datetime import date, timedelta

from core.screw_core.models.enums import AttendanceChoice
from core.screw_core.repos.attendance_repo import AttendanceRepo
from core.screw_core.utils.logging import get_logger

logger = get_logger(__name__)

# Ачивки по серии офисных дней
STREAK_ACHIEVEMENTS = [
    (3, "🏢", "Офисный старт", "3 дня подряд в офисе!"),
    (5, "🏢", "Офисная неделя", "5 дней подряд в офисе!"),
    (10, "🔥", "Офисный марафонец", "10 дней подряд в офисе!"),
    (15, "💎", "Железная воля", "15 дней подряд в офисе!"),
    (30, "👑", "Легенда офиса", "30 дней подряд в офисе!"),
]

# Особые ачивки
FLASH_THRESHOLD_SECONDS = 10  # "Молния" — ответ менее чем за 10 сек


def is_achievements_enabled() -> bool:
    return os.getenv("ACHIEVEMENTS_ENABLED", "1") == "1"


async def _get_office_streak(user_id: int, check_date: date) -> int:
    """Подсчитать текущую серию офисных дней подряд (назад от check_date)."""
    streak = 0
    current = check_date
    while True:
        mark = await AttendanceRepo.get_mark(user_id, current)
        if not mark:
            break
        choice = mark.get("choice", "")
        if choice in (AttendanceChoice.EVOLUTION.value, AttendanceChoice.CSKA.value):
            streak += 1
            current -= timedelta(days=1)
            # Пропускаем выходные
            while current.weekday() >= 5:
                current -= timedelta(days=1)
        else:
            break
    return streak


async def check_streak_achievements(
    user_id: int,
    telegram_id: int,
    check_date: date,
) -> list[dict[str, str]]:
    """
    Проверить ачивки по серии офисных дней.

    Returns:
        Список ачивок для отправки (emoji, title, description).
    """
    streak = await _get_office_streak(user_id, check_date)
    if streak == 0:
        return []

    earned = []
    for threshold, emoji, title, description in STREAK_ACHIEVEMENTS:
        if streak == threshold:
            earned.append({
                "emoji": emoji,
                "title": title,
                "description": description,
            })

    return earned


async def check_flash_achievement(response_seconds: float) -> dict[str, str] | None:
    """Проверить ачивку 'Молния' — ответ менее чем за 10 секунд."""
    if response_seconds < FLASH_THRESHOLD_SECONDS:
        return {
            "emoji": "⚡",
            "title": "Молния",
            "description": f"ответил на опрос менее чем за {FLASH_THRESHOLD_SECONDS} секунд!",
        }
    return None


def format_achievement_message(achievement: dict[str, str]) -> str:
    """Форматировать сообщение об ачивке."""
    return (
        f"🎖 <b>Новое достижение!</b>\n\n"
        f"{achievement['emoji']} <b>«{achievement['title']}»</b> — {achievement['description']}"
    )


async def check_perfect_month_achievement(
    user_id: int,
    year: int,
    month: int,
) -> dict[str, str] | None:
    """Проверить ачивку 'Безупречный месяц' — 100% отметок за месяц."""
    from core.screw_core.repos.polls_repo import PollsRepo

    poll_count = await PollsRepo.get_poll_count_for_month(year, month)
    if poll_count == 0:
        return None

    response_count = await AttendanceRepo.get_user_response_count_for_month(user_id, year, month)
    if response_count >= poll_count:
        return {
            "emoji": "✨",
            "title": "Безупречный месяц",
            "description": "100% отметок за месяц — ни одного пропуска!",
        }
    return None
