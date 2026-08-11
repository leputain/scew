"""Runtime-neutral admin panel view helpers.

Keeps callback_data and text construction separate from aiogram handlers.
"""

from __future__ import annotations

import os
from datetime import date, datetime, timezone
from typing import Any

from apps.attendance_bot.app.ui.keyboard_spec import KeyboardButtonSpec, KeyboardMarkupSpec, kb, km
from core.screw_core.models.enums import EventType, NotifyChannel
from core.screw_core.services.permissions import OZKO_DEPARTMENT_ID, OSB_DEPARTMENT_ID
from core.screw_core.repos.events_repo import EventsRepo
from core.screw_core.repos.registration_repo import RegistrationRepo
from core.screw_core.repos.users_repo import UsersRepo
from core.screw_core.services.polls import PollsService
from core.screw_core.utils.timezone import get_tzinfo


ACTION_LABELS = {
    "admin:poll:close": "Закрыть опрос",
    "admin:poll:create": "Создать/пересоздать опрос",
    "admin:report:daily:today": "Отправить дневной отчёт",
    "admin:flud:publish_week": "Опубликовать недельный дайджест флудилки",
    "admin:flud:publish_month": "Опубликовать месячный дайджест флудилки",
    "admin:flud:publish_prev_month": "Опубликовать дайджест флудилки за прошлый месяц",
}

DEPT_NAMES = {OZKO_DEPARTMENT_ID: "ОЗКО", OSB_DEPARTMENT_ID: "ОСБ"}


def is_admin_panel_enabled() -> bool:
    """Проверить, включена ли админ-панель (default=1)."""
    return os.getenv("ADMIN_PANEL_ENABLED", "1") == "1"


def is_confirm_enabled() -> bool:
    """Проверить, включено ли подтверждение действий (default=1)."""
    return os.getenv("PANEL_CONFIRM_ENABLED", "1") == "1"


def get_timezone() -> str:
    """Получить таймзону проекта."""
    return os.getenv("TZ", "Europe/Moscow")


def get_local_date() -> date:
    """Получить текущую локальную дату."""
    tz = get_tzinfo(get_timezone())
    return datetime.now(tz).date()


def get_local_datetime() -> datetime:
    """Получить текущее локальное время."""
    tz = get_tzinfo(get_timezone())
    return datetime.now(tz)


def format_expires_at_msk(expires_at: datetime | None) -> str:
    """Format expires_at in Moscow time."""
    if not expires_at:
        return "—"

    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)

    return expires_at.astimezone(get_tzinfo("Europe/Moscow")).strftime("%d.%m.%Y %H:%M")


def format_time_remaining(expires_at: datetime | None) -> str:
    """Форматировать оставшееся время до истечения."""
    if not expires_at:
        return "—"

    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)

    now = datetime.now(timezone.utc)
    if expires_at <= now:
        return "🔴 Ссылка истекла"

    delta = expires_at - now
    total_seconds = int(delta.total_seconds())

    if total_seconds < 60:
        return f"⏳ Осталось: {total_seconds} сек."

    minutes = total_seconds // 60
    if minutes < 60:
        return f"⏳ Осталось: {minutes} мин."

    hours = minutes // 60
    remaining_minutes = minutes % 60
    if hours < 24:
        if remaining_minutes:
            return f"⏳ Осталось: {hours} ч. {remaining_minutes} мин."
        return f"⏳ Осталось: {hours} ч."

    days = hours // 24
    remaining_hours = hours % 24
    if remaining_hours:
        return f"⏳ Осталось: {days} дн. {remaining_hours} ч."
    return f"⏳ Осталось: {days} дн."


def get_invite_status(invite: dict) -> tuple[str, bool]:
    """Определить статус приглашения."""
    if not invite:
        return "⚫ Не найдено", False

    if invite.get("used_by") is not None:
        return "⚫ Использована", False

    expires_at = invite.get("expires_at")
    if not expires_at:
        return "⚫ Не найдено", False

    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)

    now = datetime.now(timezone.utc)
    if expires_at <= now:
        return "🔴 Истекла", False

    return "🟢 Активна", True


def format_invite_status(invite: dict) -> tuple[str, str]:
    """Return invite status icon and compact description."""
    now = datetime.now(timezone.utc)
    used_by = invite.get("used_by")
    expires_at = invite.get("expires_at")

    if used_by is not None:
        used_name = invite.get("used_by_name") or "—"
        return "⚫", f"использовано → {used_name}"

    if expires_at and expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)

    if expires_at and expires_at <= now:
        return "🔴", "истекло"

    if expires_at:
        delta = expires_at - now
        total_sec = int(delta.total_seconds())
        if total_sec <= 0:
            return "🔴", "истекло"
        if total_sec < 60:
            return "🟢", f"осталось {total_sec} сек."
        minutes = total_sec // 60
        if minutes < 60:
            return "🟢", f"осталось {minutes} мин."
        hours = minutes // 60
        mins = minutes % 60
        return "🟢", f"осталось {hours}ч {mins}мин."

    return "🟢", "активное"


def format_invite_screen(invite: dict, bot_username: str | None) -> str:
    """Форматировать экран приглашения для руководителя."""
    department_name = DEPT_NAMES.get(invite.get("department_id"), "—")
    expires_str = format_expires_at_msk(invite.get("expires_at"))
    status_text, is_active = get_invite_status(invite)
    token = invite.get("token") or ""
    deep_link = f"https://t.me/{bot_username}?start={token}" if is_active and bot_username else ""

    header = (
        f"🔗 <b>Приглашение в отдел {department_name}</b>\n\n"
        f"⏳ Действует до: {expires_str}\n"
        f"{status_text}"
    )

    if deep_link:
        forward_text = (
            "👋 Привет!\n\n"
            f"Это ссылка для регистрации в боте отдела {department_name}.\n\n"
            "⚠️ Регистрация работает только в личных сообщениях с ботом.\n"
            "После перехода по ссылке нужно нажать Start.\n\n"
            f"🔗 {deep_link}"
        )
    else:
        forward_text = (
            "Ссылка уже недействительна. Создайте новую ссылку приглашения."
        )

    return (
        f"{header}\n\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        "📨 <b>Простые инструкции:</b>\n\n"
        f"{forward_text}"
    )


def format_invite_message(invite: dict, status: str, bot_username: str | None) -> str:
    """Format invite info for admin UI with copy-ready forwarding text."""
    return format_invite_screen(invite, bot_username)


def format_invites_text(invites: list[dict], title: str, *, group_by_dept: bool = False) -> str:
    """Format invites list for admin UI."""
    if not invites:
        return f"{title}\n\n📭 Нет приглашений."

    def _shorten(name: str | None) -> str:
        if not name:
            return "—"
        parts = name.split()
        if len(parts) >= 3:
            return f"{parts[0]} {parts[1][0]}.{parts[2][0]}."
        if len(parts) >= 2:
            return f"{parts[0]} {parts[1][0]}."
        return name

    lines = [title, ""]

    if group_by_dept:
        by_dept: dict[int, list[dict]] = {}
        for invite in invites:
            department_id = invite.get("department_id", 0)
            by_dept.setdefault(department_id, []).append(invite)

        for department_id in sorted(by_dept.keys()):
            dept_name = DEPT_NAMES.get(department_id, f"Отдел {department_id}")
            lines.append(f"— <b>{dept_name}</b> —")
            for invite in by_dept[department_id]:
                icon, desc = format_invite_status(invite)
                creator = _shorten(invite.get("creator_name"))
                created = invite.get("created_at")
                date_str = created.strftime("%d.%m %H:%M") if created else ""
                lines.append(f"{icon} {desc} | {creator} | {date_str}")
            lines.append("")
    else:
        for invite in invites:
            icon, desc = format_invite_status(invite)
            creator = _shorten(invite.get("creator_name"))
            created = invite.get("created_at")
            date_str = created.strftime("%d.%m %H:%M") if created else ""
            lines.append(f"{icon} {desc} | {creator} | {date_str}")

    active = sum(1 for invite in invites if format_invite_status(invite)[0] == "🟢")
    used = sum(1 for invite in invites if invite.get("used_by") is not None)
    expired = len(invites) - active - used
    lines.append(f"\nВсего: {len(invites)} (🟢 {active} | ⚫ {used} | 🔴 {expired})")

    return "\n".join(lines)


def invites_list_keyboard(back_target: str = "manager", show_create: bool = True) -> KeyboardMarkupSpec:
    """Build invites list keyboard."""
    rows: list[list[KeyboardButtonSpec]] = []
    if show_create:
        rows.append([kb(text="🔗 Создать новое", callback_data="mgr:invite")])
    rows.append(
        [
            kb(text="⬅️ Назад", callback_data=f"nav:back:{back_target}"),
            kb(text="🏠 Главное меню", callback_data="nav:home"),
        ]
    )
    return km(rows)


async def record_admin_action(
    action: str,
    admin_id: int,
    parameters: dict[str, Any] | None = None,
    error_summary: str | None = None,
) -> int | None:
    """Записать событие ADMIN_ACTION."""
    today = get_local_date()
    dedup_key = f"ADMIN_ACTION:{action}:{today}:{admin_id}"

    payload: dict[str, Any] = {
        "action": action,
        "admin_id": admin_id,
        "timestamp": get_local_datetime().isoformat(),
    }
    if parameters:
        payload["parameters"] = parameters
    if error_summary:
        payload["error_summary"] = error_summary

    return await EventsRepo.create_event(
        event_type=EventType.ADMIN_ACTION,
        dedup_key=dedup_key,
        user_id=None,
        event_date=today,
        payload=payload,
        notify_channel=NotifyChannel.NONE,
    )


async def _build_admin_summary() -> tuple[str, int]:
    """Собрать сводку для главного экрана админ-панели."""
    ozko_users = await UsersRepo.get_active_by_department(1)
    osb_users = await UsersRepo.get_active_by_department(2)
    total = len(ozko_users) + len(osb_users)

    pending = await RegistrationRepo.get_pending_requests()
    pending_count = len(pending)

    current_poll = await PollsService.get_current_poll()
    poll_status = "открыт" if current_poll else "нет"

    text = (
        "🛠 <b>Админ-панель</b>\n\n"
        f"👥 Пользователей: {total} (ОЗКО: {len(ozko_users)}, ОСБ: {len(osb_users)})\n"
        f"📨 Заявок: {pending_count}\n"
        f"📋 Опрос: {poll_status}"
    )
    return text, pending_count


def build_admin_panel_keyboard(pending_count: int = 0) -> KeyboardMarkupSpec:
    """Построить главное меню админ-панели."""
    requests_label = "📨 Заявки"
    if pending_count > 0:
        requests_label += f" ({pending_count})"

    keyboard = [
        [kb(text="📋 Опросы", callback_data="admin:menu:polls")],
        [kb(text="👥 Пользователи", callback_data="admin:menu:users")],
        [kb(text=requests_label, callback_data="admin:menu:requests")],
        [kb(text="📊 Отчёты", callback_data="admin:menu:reports")],
        [kb(text="⚙️ Система", callback_data="admin:menu:system")],
        [kb(text="ℹ️ Помощь", callback_data="nav:help:admin")],
        [kb(text="⬅️ Назад", callback_data="nav:back:home")],
    ]
    return km(keyboard)


def build_polls_submenu_keyboard() -> KeyboardMarkupSpec:
    """Подменю: Опросы."""
    keyboard = [
        [kb(text="📋 Создать / пересоздать опрос", callback_data="admin:poll:create")],
        [kb(text="▶️ Закрыть опрос", callback_data="admin:poll:close")],
        [kb(text="⚙️ Настройки опросов", callback_data="admin:poll:settings")],
        [kb(text="📅 Пропуск дат (праздники)", callback_data="admin:poll:skip_dates")],
        [kb(text="⬅️ Назад", callback_data="admin:back")],
    ]
    return km(keyboard)


def build_users_submenu_keyboard() -> KeyboardMarkupSpec:
    """Подменю: Пользователи."""
    keyboard = [
        [kb(text="👥 Список пользователей", callback_data="admin:users")],
        [kb(text="🎂 Дни рождения", callback_data="admin:bday:bulk")],
        [kb(text="🚫 Отозвать доступ", callback_data="access:revoke:admin")],
        [kb(text="✅ Восстановить доступ", callback_data="access:restore:admin")],
        [kb(text="👔 Назначить руководителя", callback_data="admin:mgr:assign")],
        [kb(text="🧹 Снять руководителя", callback_data="admin:mgr:remove")],
        [kb(text="⬅️ Назад", callback_data="admin:back")],
    ]
    return km(keyboard)


def build_requests_submenu_keyboard() -> KeyboardMarkupSpec:
    """Подменю: Заявки."""
    keyboard = [
        [kb(text="📨 Ожидающие заявки", callback_data="mgr:pending")],
        [kb(text="🔗 Создать ссылку приглашения", callback_data="admin:invite")],
        [kb(text="📋 Все приглашения", callback_data="admin:invites:all")],
        [kb(text="⬅️ Назад", callback_data="admin:back")],
    ]
    return km(keyboard)


def build_reports_submenu_keyboard() -> KeyboardMarkupSpec:
    """Подменю: Отчёты."""
    keyboard = [
        [kb(text="📄 Дневной отчёт", callback_data="admin:report:daily:today")],
        [kb(text="📊 Статистика явки", callback_data="admin:stats:m:0")],
        [kb(text="🧃 Флудилка", callback_data="admin:menu:flud")],
        [kb(text="⬅️ Назад", callback_data="admin:back")],
    ]
    return km(keyboard)


_MONTH_NAMES_NOMINATIVE = {
    1: "Январь",
    2: "Февраль",
    3: "Март",
    4: "Апрель",
    5: "Май",
    6: "Июнь",
    7: "Июль",
    8: "Август",
    9: "Сентябрь",
    10: "Октябрь",
    11: "Ноябрь",
    12: "Декабрь",
}


def get_monthly_stats_months(*, today: date | None = None) -> list[tuple[int, int]]:
    """Return the current and previous 11 months, newest first."""
    current = today or get_local_date()
    months: list[tuple[int, int]] = []
    year, month = current.year, current.month
    for _ in range(12):
        months.append((year, month))
        month -= 1
        if month == 0:
            year -= 1
            month = 12
    return months


def build_monthly_stats_months_keyboard(
    page: int = 0,
    *,
    today: date | None = None,
) -> KeyboardMarkupSpec:
    """Build two pages with six monthly attendance report choices each."""
    page = max(0, min(page, 1))
    visible = get_monthly_stats_months(today=today)[page * 6 : (page + 1) * 6]
    buttons = [
        kb(
            text=f"{_MONTH_NAMES_NOMINATIVE[month]} {year}",
            callback_data=f"admin:stats:v:{year:04d}-{month:02d}:{page}",
        )
        for year, month in visible
    ]
    rows = [buttons[index : index + 2] for index in range(0, len(buttons), 2)]
    rows.append(
        [
            kb(text="◀️", callback_data=f"admin:stats:m:{max(page - 1, 0)}"),
            kb(text=f"{page + 1}/2", callback_data="nav:ignore"),
            kb(text="▶️", callback_data=f"admin:stats:m:{min(page + 1, 1)}"),
        ]
    )
    rows.append([kb(text="⬅️ Назад", callback_data="admin:menu:reports")])
    return km(rows)


def build_monthly_stats_preview_keyboard(year: int, month: int, page: int) -> KeyboardMarkupSpec:
    """Build actions for a monthly attendance preview."""
    period = f"{year:04d}-{month:02d}"
    return km(
        [
            [kb(text="📤 Опубликовать", callback_data=f"admin:stats:p:{period}:{page}")],
            [kb(text="⬅️ К месяцам", callback_data=f"admin:stats:m:{page}")],
        ]
    )


def build_monthly_stats_publish_confirm_keyboard(year: int, month: int, page: int) -> KeyboardMarkupSpec:
    """Build explicit confirmation for a manual monthly publication."""
    period = f"{year:04d}-{month:02d}"
    return km(
        [
            [
                kb(text="✅ Подтвердить", callback_data=f"admin:stats:p:{period}:{page}:ok"),
                kb(text="❌ Отмена", callback_data=f"admin:stats:v:{period}:{page}"),
            ]
        ]
    )


def build_flud_submenu_keyboard() -> KeyboardMarkupSpec:
    """Подменю: Пульс флудилки."""
    keyboard = [
        [kb(text="🗓 Недельный дайджест", callback_data="admin:flud:week")],
        [kb(text="📆 Месячный дайджест", callback_data="admin:flud:month")],
        [kb(text="📆 Прошлый месяц", callback_data="admin:flud:prev_month")],
        [kb(text="🏆 Топ месяца", callback_data="admin:flud:top")],
        [kb(text="📤 Опубликовать неделю", callback_data="admin:flud:publish_week")],
        [kb(text="📤 Опубликовать месяц", callback_data="admin:flud:publish_month")],
        [kb(text="📤 Опубликовать прошлый месяц", callback_data="admin:flud:publish_prev_month")],
        [kb(text="⬅️ Назад", callback_data="admin:menu:reports")],
    ]
    return km(keyboard)


def build_system_submenu_keyboard() -> KeyboardMarkupSpec:
    """Подменю: Система."""
    keyboard = [
        [kb(text="⚠️ Статус системы", callback_data="admin:system:status")],
        [kb(text="⬅️ Назад", callback_data="admin:back")],
    ]
    return km(keyboard)


def build_invite_department_keyboard() -> KeyboardMarkupSpec:
    """Inline keyboard for department selection."""
    buttons = [
        [kb(text=label, callback_data=f"admin:invite:dept:{department_id}")]
        for department_id, label in DEPT_NAMES.items()
    ]
    buttons.append(
        [
            kb(text="⬅️ Назад", callback_data="nav:back:admin"),
            kb(text="🏠 Главное меню", callback_data="nav:home"),
        ]
    )
    return km(buttons)


def build_invite_name_keyboard() -> KeyboardMarkupSpec:
    """Inline keyboard for optional expected full name."""
    return KeyboardMarkupSpec(
        inline_keyboard=[
            [
                kb(text="Пропустить", callback_data="admin:invite:skip"),
                kb(text="Ввести ФИО", callback_data="admin:invite:enter_name"),
            ],
            [
                kb(text="⬅️ Назад", callback_data="nav:back:admin"),
                kb(text="🏠 Главное меню", callback_data="nav:home"),
            ],
        ]
    )


def build_manager_department_keyboard(action: str) -> KeyboardMarkupSpec:
    """Inline keyboard for department selection in manager assignment."""
    buttons = [
        [kb(text=label, callback_data=f"admin:mgr:{action}:dept:{department_id}")]
        for department_id, label in DEPT_NAMES.items()
    ]
    buttons.append(
        [
            kb(text="⬅️ Назад", callback_data="nav:back:admin"),
            kb(text="🏠 Главное меню", callback_data="nav:home"),
        ]
    )
    return km(buttons)


def build_confirm_keyboard(action: str) -> KeyboardMarkupSpec:
    """Построить клавиатуру подтверждения действия."""
    return KeyboardMarkupSpec(
        inline_keyboard=[
            [
                kb(text="✅ Подтвердить", callback_data=f"{action}:confirm"),
                kb(text="❌ Отмена", callback_data="admin:back"),
            ],
        ]
    )


def build_navigation_keyboard(refresh_action: str | None = None) -> KeyboardMarkupSpec:
    """Построить клавиатуру навигации (Назад/Обновить)."""
    buttons: list[KeyboardButtonSpec] = []
    if refresh_action:
        buttons.append(kb(text="🔄 Обновить", callback_data=refresh_action))

    buttons.extend(
        [
            kb(text="⬅️ Назад", callback_data="nav:back:admin"),
            kb(text="🏠 Главное меню", callback_data="nav:home"),
        ]
    )

    return km([buttons])


def build_invite_keyboard(
    invite_id: int,
    is_active: bool,
    back_target: str = "manager",
) -> KeyboardMarkupSpec:
    """Построить клавиатуру экрана приглашения."""
    rows = [
        [kb(text="🔄 Проверить статус", callback_data=f"invite:refresh:{invite_id}")],
    ]

    if is_active:
        rows.append([kb(text="🗑️ Удалить ссылку", callback_data=f"invite:delete:{invite_id}")])

    rows.append(
        [
            kb(text="⬅️ Назад", callback_data=f"nav:back:{back_target}"),
            kb(text="🏠 Главное меню", callback_data="nav:home"),
        ]
    )

    return km(rows)


def build_invite_delete_confirm_keyboard(invite_id: int) -> KeyboardMarkupSpec:
    """Клавиатура подтверждения удаления приглашения."""
    return KeyboardMarkupSpec(
        inline_keyboard=[
            [
                kb(text="✅ Да, удалить", callback_data=f"invite:delete:confirm:{invite_id}"),
                kb(text="↩️ Отмена", callback_data=f"invite:delete:cancel:{invite_id}"),
            ],
        ]
    )
