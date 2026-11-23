"""Opt-in read-only attendance calendar inside the private bot chat."""

import json
import os
from datetime import date, timedelta
from html import escape
from pathlib import Path

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramAPIError, TelegramBadRequest
from aiogram.types import CallbackQuery, WidgetQuery, WidgetQueryTypeData

from apps.attendance_bot.app.services.poll_runtime import get_local_date
from apps.attendance_bot.app.ui.start_views import ATTENDANCE_CHOICE_LABELS, STATUS_LABELS
from core.screw_core.repos.attendance_repo import AttendanceRepo
from core.screw_core.repos.day_status_repo import DayStatusRepo
from core.screw_core.services.telegram_delivery import send_system_message
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


async def _send_week_disclosures(bot: Bot, chat_id: int, user_id: int, start: date, current: str) -> None:
    rich = ["<h2>Моя неделя</h2><p>Нажмите на период, чтобы раскрыть или свернуть неделю.</p>"]
    plain = ["<b>Моя неделя</b>"]
    payloads = [current]
    for offset in (1, 2):
        payloads.append(await _week_data(user_id, start - timedelta(weeks=offset)))

    for index, payload in enumerate(payloads):
        week = json.loads(payload)
        title = escape(week["title"])
        lines = [f"<b>{escape(day)}</b> · {escape(label)}" for day, label in week["days"]]
        items = "".join(f"<li>{line}</li>" for line in lines)
        opened = "" if index else " open"
        rich.append(f"<details{opened}><summary>{title}</summary><ul>{items}</ul></details>")
        body = "\n".join(lines)
        if index:
            body = f"<blockquote expandable>{body}</blockquote>"
        plain.append(f"<b>{title}</b>\n{body}")

    await send_system_message(
        bot,
        chat_id=chat_id,
        html_text="\n\n".join(plain),
        rich_html_text="".join(rich),
        protect_content=True,
        strict_rich_fallback=True,
    )


@router.callback_query(F.data == "user:week_widget")
async def open_week_widget(callback: CallbackQuery, bot: Bot, auth_user: dict) -> None:
    message = callback.message
    telegram_id = callback.from_user.id
    if not message or message.chat.type != "private" or message.chat.id != telegram_id:
        await callback.answer()
        return
    if not week_widget_enabled(telegram_id):
        await callback.answer("Виджет пока недоступен.", show_alert=True)
        return
    await callback.answer()
    user_id = auth_user["id"]
    start = monday(get_local_date())
    current = await _week_data(user_id, start)
    try:
        await bot.send_widget(
            chat_id=telegram_id,
            html=_WIDGET_HTML,
            chat_data=current,
            caption="Моя неделя · Подробнее в разделе «Мой календарь»",
            allow_queries=True,
        )
    except TelegramBadRequest:
        logger.info("week widget rejected, opening expandable calendar")
        await _send_week_disclosures(bot, telegram_id, user_id, start, current)
    except TelegramAPIError:
        logger.warning("week widget could not be sent")
        await bot.send_message(telegram_id, "Карточка недоступна. Откройте «Мой календарь» в меню.")


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
    try:
        start = date.fromisoformat(value)
    except ValueError:
        start = None
    if action != "screw-week" or start is None or start.weekday() != 0 or abs((today - start).days) > 366:
        await bot.answer_widget_query(query.id, data="Выберите неделю в пределах года.")
        return

    data = await _week_data(auth_user["id"], start)
    await bot.edit_message_widget_data(chat_id=query.chat.id, message_id=query.message_id, chat_data=data)
    await bot.answer_widget_query(query.id)
