"""Runtime-neutral admin/manager view helpers."""

from __future__ import annotations

import os

from apps.attendance_bot.app.config import (
    get_ozko_bot_chat_invite_link,
    get_ozko_dept_chat_invite_link,
)
from apps.attendance_bot.app.ui.keyboard_spec import KeyboardButtonSpec, KeyboardMarkupSpec, kb, km
from apps.attendance_bot.app.utils.permissions import get_permissions
from core.screw_core.models.enums import EventType, NotifyChannel
from core.screw_core.repos.events_repo import EventsRepo
from core.screw_core.repos.registration_repo import RegistrationRepo
from core.screw_core.repos.users_repo import UsersRepo
from core.screw_core.services.permissions import OZKO_DEPARTMENT_ID, OSB_DEPARTMENT_ID
from core.screw_core.utils.logging import get_logger

logger = get_logger(__name__)

DEPT_NAMES = {OZKO_DEPARTMENT_ID: "ОЗКО", OSB_DEPARTMENT_ID: "ОСБ"}

KIND_EMOJI = {
    "VACATION": "✈️",
    "SICK": "🏥",
    "LEAVE": "🕓",
    "DAY_OFF": "📅",
}


async def is_manager(telegram_id: int) -> tuple[bool, dict | None]:
    """Проверить, является ли пользователь менеджером."""
    perms = await get_permissions(telegram_id)
    if not perms.is_manager:
        return False, None

    user = await UsersRepo.get_by_telegram_id(telegram_id)
    if not user:
        return False, None

    return True, user


async def _get_pending_count(telegram_id: int) -> int:
    """Получить количество ожидающих заявок для менеджера."""
    _, mgr_user = await is_manager(telegram_id)
    if not mgr_user:
        return 0
    dept = mgr_user.get("department_id") if mgr_user.get("role") != "admin" else None
    pending = await RegistrationRepo.get_pending_requests(dept)
    return len(pending)


def get_group_invite_link() -> str | None:
    """Получить ссылку приглашения в рабочую группу."""
    return os.getenv("ATTENDANCE_GROUP_INVITE_LINK")


def _build_approve_user_message(department_id: int | None = None) -> str:
    """Построить единое сообщение об одобрении заявки для пользователя."""
    is_ozko = department_id == OZKO_DEPARTMENT_ID

    lines = ["🎉 Ваша заявка на регистрацию одобрена!"]

    invite_link = get_group_invite_link()
    if invite_link:
        lines.append("")
        lines.append(f"📌 Общий чат отделов ОСБ ОЗКО Рабочие вопросы:\n{invite_link}")

    if is_ozko:
        bot_chat_link = get_ozko_bot_chat_invite_link()
        dept_chat_link = get_ozko_dept_chat_invite_link()

        if bot_chat_link or dept_chat_link:
            lines.append("")
            lines.append("📌 Внутренний чат ОЗКО:")
            if bot_chat_link:
                lines.append(f"• Чат бота: {bot_chat_link}")
            if dept_chat_link:
                lines.append(f"• Чат отдела: {dept_chat_link}")

    lines.append("")
    lines.append("Бот будет присылать опросы посещаемости и уведомления.")
    lines.append("Нажмите кнопку ниже, чтобы открыть меню и начать пользоваться ботом.")
    lines.append("ℹ️ Помощь доступна в меню бота.")

    return "\n".join(lines)


def _bot_start_link() -> str | None:
    """Вернуть deep-link на бота, чтобы не редактировать approval-сообщение."""
    link = get_ozko_bot_chat_invite_link()
    if not link:
        return None
    if "?" in link:
        return link
    return f"{link}?start=menu"


def build_approve_notice_keyboard() -> KeyboardMarkupSpec:
    """Кнопка входа в бот после одобрения без замены сообщения со ссылками."""
    bot_link = _bot_start_link()
    if bot_link:
        return km([[kb(text="🚀 Начать пользоваться ботом", url=bot_link)]])
    return km([[kb(text="🚀 Начать пользоваться ботом", callback_data="panel:user")]])


async def _send_approve_notice(
    bot,
    user_id: int,
    telegram_id: int | None,
    department_id: int | None = None,
    reply_markup=None,
) -> None:
    """Отправить единое DM пользователю после одобрения регистрации."""
    if not telegram_id:
        return

    dedup_key = f"{EventType.OZKO_POST_APPROVE_NOTICE}:{user_id}"
    existing = await EventsRepo.get_by_dedup_key(dedup_key)
    if existing:
        logger.debug("approve_notice: event уже существует — user_id=%s", user_id)
        return

    message_text = _build_approve_user_message(department_id)
    notify_error: str | None = None
    try:
        await bot.send_message(telegram_id, message_text, reply_markup=reply_markup)
        logger.info("approve_notice: DM отправлен — user_id=%s, telegram_id=%s", user_id, telegram_id)
    except Exception as exc:
        notify_error = str(exc)[:100]
        logger.warning("approve_notice: ошибка отправки DM — user_id=%s, error=%s", user_id, exc)

    await EventsRepo.create_event(
        event_type=EventType.OZKO_POST_APPROVE_NOTICE,
        dedup_key=dedup_key,
        user_id=user_id,
        notify_channel=NotifyChannel.USER_DM,
        notify_error=notify_error,
    )


def format_request_info(request: dict) -> str:
    """Форматировать информацию о заявке."""
    name_parts = []
    if request.get("first_name"):
        name_parts.append(request["first_name"])
    if request.get("last_name"):
        name_parts.append(request["last_name"])
    tg_name = " ".join(name_parts) if name_parts else "—"

    username = f"@{request['username']}" if request.get("username") else "—"
    expected = request.get("expected_full_name") or "не указано"
    dept_id = request.get("department_id")
    dept_name = DEPT_NAMES.get(dept_id, "—") if dept_id else "—"
    tg_id = request.get("telegram_id", "—")

    created_at = request["created_at"].strftime("%d.%m.%Y %H:%M") if request.get("created_at") else "—"

    lines = [
        f"👤 <b>{tg_name}</b>",
        f"🔗 {username}",
        f"🆔 <code>{tg_id}</code>",
        f"🏢 {dept_name}",
    ]
    if expected and expected != "не указано":
        lines.append(f"📝 Ожидание: {expected}")
    lines.append(f"🕐 {created_at}")

    return "\n".join(lines)


def _dept_vacations_keyboard(
    year: int,
    month: int,
    back_target: str = "manager",
    *,
    dept_filter: int = 0,
    show_dept_toggle: bool = False,
) -> KeyboardMarkupSpec:
    """Клавиатура навигации по месяцам для отпусков отдела."""
    if month == 1:
        prev_year, prev_month = year - 1, 12
    else:
        prev_year, prev_month = year, month - 1
    if month == 12:
        next_year, next_month = year + 1, 1
    else:
        next_year, next_month = year, month + 1

    suffix = f":{dept_filter}" if dept_filter else ""
    rows: list[list[KeyboardButtonSpec]] = []

    if show_dept_toggle:
        dept_row: list[KeyboardButtonSpec] = []
        label_all = "• Все" if dept_filter == 0 else "Все"
        dept_row.append(
            kb(
                text=label_all,
                callback_data=f"mgr:dept_vacations:{year}:{month}:0",
            )
        )
        for did, dname in DEPT_NAMES.items():
            label = f"• {dname}" if dept_filter == did else dname
            dept_row.append(
                kb(
                    text=label,
                    callback_data=f"mgr:dept_vacations:{year}:{month}:{did}",
                )
            )
        rows.append(dept_row)

    rows.append(
        [
            kb(text="◀️", callback_data=f"mgr:dept_vacations:{prev_year}:{prev_month}{suffix}"),
            kb(text="Текущий", callback_data=f"mgr:dept_vacations{suffix}"),
            kb(text="▶️", callback_data=f"mgr:dept_vacations:{next_year}:{next_month}{suffix}"),
        ]
    )
    rows.append([kb(text="⬅️ Назад", callback_data=f"nav:back:{back_target}")])

    return km(rows)
