"""Private attendance calendar and opt-in native Telegram widget."""

import json
import os
from datetime import date, timedelta
from html import escape
from pathlib import Path

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramAPIError, TelegramBadRequest
from aiogram.types import CallbackQuery, Message, WidgetQuery, WidgetQueryTypeData

from apps.attendance_bot.app.aiogram_app.keyboards import to_aiogram_markup
from apps.attendance_bot.app.services.poll_runtime import get_local_date
from apps.attendance_bot.app.ui.keyboard_spec import kb, km
from apps.attendance_bot.app.ui.keyboards_neutral import REPORT_ABSENCE_BUTTON, USER_CALENDAR_TITLE, user_calendar_tabs
from apps.attendance_bot.app.ui.start_views import ATTENDANCE_CHOICE_LABELS, STATUS_LABELS, WEEKDAY_NAMES_SHORT
from apps.attendance_bot.app.utils.message_tracker import track_sent_message
from core.screw_core.repos.attendance_repo import AttendanceRepo
from core.screw_core.repos.day_status_repo import DayStatusRepo
from core.screw_core.utils.logging import get_logger

router = Router(name="attendance_week_widget")
logger = get_logger(__name__)
_WIDGET_HTML = Path(__file__).with_suffix(".html").read_text(encoding="utf-8")


def week_widget_enabled(telegram_id: int) -> bool:
    ids = os.getenv("ATTENDANCE_WEEK_WIDGET_PILOT_IDS", "")
    return str(telegram_id) in {item.strip() for item in ids.split(",")}


def monday(day: date) -> date:
    return day - timedelta(days=day.weekday())


async def _week_data(user_id: int, start: date) -> str:
    today = get_local_date()
    stop = start + timedelta(days=6)
    marks = await AttendanceRepo.get_marks_for_user_range(user_id, start, stop)
    attendance = {mark["mark_date"]: mark["choice"] for mark in marks}
    statuses = await DayStatusRepo.get_statuses_for_date_range(start, stop, user_ids=[user_id])
    days = []
    for offset in range(7):
        day = start + timedelta(days=offset)
        status = statuses.get((user_id, day))
        choice = attendance.get(day)
        if status and (status != "DAY_OFF" or not choice):
            label = STATUS_LABELS.get(status, status)
        elif choice:
            label = ATTENDANCE_CHOICE_LABELS.get(choice, choice)
        elif day > today:
            label = "Предстоит"
        elif day.weekday() >= 5:
            label = "Выходной"
        else:
            label = "Нет отметки"
        days.append([f"{day:%d.%m}", label])
    return json.dumps({
        "title": f"{start:%d.%m} – {stop:%d.%m.%Y}",
        "days": days,
        "previous": (start - timedelta(days=7)).isoformat(),
        "current": monday(today).isoformat(),
        "next": (start + timedelta(days=7)).isoformat(),
        "refresh": start.isoformat(),
    }, ensure_ascii=False)


def _parse_week(value: str, today: date) -> date | None:
    try:
        start = date.fromisoformat(value)
    except ValueError:
        return None
    if start.weekday() != 0 or abs((today - start).days) > 366:
        return None
    return start


async def _edit_week_card(message: Message, start: date, today: date, data: str, *, native_enabled: bool) -> None:
    week = json.loads(data)
    lines = [f"<b>{USER_CALENDAR_TITLE} · Неделя</b>", f"<i>{escape(week['title'])}</i>", ""]
    for offset, (label, status) in enumerate(week["days"]):
        day_label = f"{WEEKDAY_NAMES_SHORT[offset]}, {escape(label)}"
        if start + timedelta(days=offset) == today:
            day_label += " · сегодня"
        lines.append(f"<b>{day_label}</b>  ·  {escape(status)}")

    navigation = []
    if _parse_week(week["previous"], today):
        navigation.append(kb("←", f"user:week:{week['previous']}"))
    navigation.append(kb("Текущая", f"user:week:{week['current']}"))
    if _parse_week(week["next"], today):
        navigation.append(kb("→", f"user:week:{week['next']}"))
    rows = [user_calendar_tabs("week"), navigation, [REPORT_ABSENCE_BUTTON]]
    if native_enabled and start == monday(today):
        rows.append([kb("HTML-виджет", "user:week_widget")])
    rows.append([kb("← В кабинет", "panel:user")])

    try:
        await message.edit_text("\n".join(lines), parse_mode="HTML", reply_markup=to_aiogram_markup(km(rows)))
    except TelegramBadRequest as exc:
        if "message is not modified" not in str(exc).lower():
            raise


@router.callback_query(F.data == "user:week_widget")
@router.callback_query(F.data == "user:calendar")
@router.callback_query(F.data.startswith("user:week:"))
async def open_week_widget(callback: CallbackQuery, bot: Bot, auth_user: dict) -> None:
    message = callback.message
    telegram_id = callback.from_user.id
    if not isinstance(message, Message) or message.chat.type != "private" or message.chat.id != telegram_id:
        await callback.answer()
        return

    today = get_local_date()
    action = callback.data or ""
    start = _parse_week(action.removeprefix("user:week:"), today) if action.startswith("user:week:") else monday(today)
    if start is None:
        await callback.answer("Выберите неделю в пределах года.", show_alert=True)
        return

    await callback.answer()
    current = await _week_data(auth_user["id"], start)
    native_enabled = os.getenv("ATTENDANCE_NATIVE_WEEK_WIDGET_ENABLED") == "1" and week_widget_enabled(telegram_id)
    if action == "user:week_widget" and native_enabled:
        try:
            sent = await bot.send_widget(
                chat_id=telegram_id,
                html=_WIDGET_HTML,
                chat_data=current,
                caption="Моя неделя",
                allow_queries=True,
            )
        except TelegramAPIError as exc:
            logger.info("week widget unavailable, opening the in-chat calendar (%s)", type(exc).__name__)
        else:
            await track_sent_message(sent, telegram_id, screen="user:week_widget")
            return
    await _edit_week_card(message, start, today, current, native_enabled=native_enabled)


@router.widget_query()
async def update_week_widget(query: WidgetQuery, bot: Bot, auth_user: dict) -> None:
    if (
        not query.chat or query.chat.type != "private" or query.chat.id != query.from_user.id
        or not query.message_id or query.widget_id or not isinstance(query.type, WidgetQueryTypeData)
        or not week_widget_enabled(query.from_user.id)
    ):
        await bot.answer_widget_query(query.id, data="Карточка недоступна.")
        return

    action, _, value = query.data.partition(":")
    today = get_local_date()
    start = _parse_week(value, today)
    if action != "screw-week" or start is None:
        await bot.answer_widget_query(query.id, data="Выберите неделю в пределах года.")
        return

    data = await _week_data(auth_user["id"], start)
    await bot.edit_message_widget_data(chat_id=query.chat.id, message_id=query.message_id, chat_data=data)
    await bot.answer_widget_query(query.id)
