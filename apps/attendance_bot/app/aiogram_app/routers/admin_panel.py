"""Native aiogram admin panel shell and user management."""

from __future__ import annotations

import asyncio
import calendar as cal_module
import html
import json
import os
from datetime import date, datetime, timedelta, timezone

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from apps.attendance_bot.app.aiogram_app.keyboards import to_aiogram_markup
from apps.attendance_bot.app.config import get_attendance_group_id, get_flud_chat_id, get_flud_top_limit
from apps.attendance_bot.app.jobs.flud_digest import (
    build_current_monthly_flud_digest,
    build_current_weekly_flud_digest,
    build_monthly_flud_digest_for_month,
    current_month_period,
    previous_month_period,
    send_monthly_flud_digest,
    send_weekly_flud_digest,
)
from apps.attendance_bot.app.scheduler import (
    remove_poll_auto_close_job,
    remove_poll_reminder_job,
    reschedule_poll_auto_send,
    schedule_poll_auto_close,
    schedule_poll_reminder,
)
from apps.attendance_bot.app.services.group_preflight import check_attendance_group_access
from apps.attendance_bot.app.services import poll_runtime as poll_core
from apps.attendance_bot.app.ui import admin_panel_views as panel_core
from apps.attendance_bot.app.ui.keyboards_neutral import DEPT_NAMES
from apps.attendance_bot.app.utils.message_tracker import track_bot_message
from apps.attendance_bot.app.utils.permissions import get_permissions
from apps.attendance_bot.app.utils.poll_payload import (
    build_aiogram_poll_payload,
    build_poll_payload_diagnostics,
    send_aiogram_poll_with_fallback,
)
from apps.attendance_bot.app.utils.private_chat_guard import MSG_PRIVATE_ONLY
from apps.attendance_bot.app.utils.log_redaction import redact_text
from core.screw_core.models.enums import UserRole, UserStatus, WorkMode
from core.screw_core.repos.flud_repo import FludRepo
from core.screw_core.repos.poll_config_repo import PollConfigRepo
from core.screw_core.repos.registration_repo import RegistrationRepo
from core.screw_core.repos.users_repo import UsersRepo
from core.screw_core.services.notify import NotifyService
from core.screw_core.services.permissions import OZKO_DEPARTMENT_ID
from core.screw_core.services.polls import PollError, PollsService
from core.screw_core.services.registration import RegistrationService
from core.screw_core.services.report_delivery_service import ReportDeliveryService
from core.screw_core.utils.logging import get_logger

logger = get_logger(__name__)
router = Router(name="attendance_admin_panel")

DANGEROUS_ACTIONS = {
    "admin:poll:close",
    "admin:poll:create",
    "admin:report:daily:today",
    "admin:flud:publish_week",
    "admin:flud:publish_month",
    "admin:flud:publish_prev_month",
}


class AdminPanelStates(StatesGroup):
    user_search = State()
    invite_full_name = State()
    manager_telegram_id = State()
    poll_option_label = State()
    poll_option_media = State()
    poll_skip_reason = State()
    office_req_hybrid = State()
    office_req_hybrid_plus = State()
    birthday_input = State()


async def _reject_if_group_message(message: Message) -> bool:
    if message.chat.type == "private":
        return False
    await message.answer(MSG_PRIVATE_ONLY)
    return True


async def _reject_if_group_callback(callback: CallbackQuery) -> bool:
    message = callback.message
    chat_type = getattr(getattr(message, "chat", None), "type", None)
    if chat_type in (None, "private"):
        return False
    await callback.answer(MSG_PRIVATE_ONLY, show_alert=True)
    return True


async def _is_admin(telegram_id: int) -> bool:
    perms = await get_permissions(telegram_id)
    return perms.is_admin


async def _require_admin_message(message: Message) -> bool:
    if not message.from_user:
        return False
    if await _reject_if_group_message(message):
        return False
    if not panel_core.is_admin_panel_enabled():
        await message.answer("⚠️ Админ-панель временно отключена.")
        return False
    if not await _is_admin(message.from_user.id):
        await message.answer("⛔ Недостаточно прав.")
        logger.warning("aiogram admin_panel: denied telegram_id=%s", message.from_user.id)
        return False
    return True


async def _require_admin_callback(callback: CallbackQuery) -> bool:
    if not callback.from_user:
        return False
    if await _reject_if_group_callback(callback):
        return False
    if not panel_core.is_admin_panel_enabled():
        await callback.answer("⚠️ Админ-панель временно отключена.", show_alert=True)
        return False
    if not await _is_admin(callback.from_user.id):
        await callback.answer("⛔ Недостаточно прав.", show_alert=True)
        logger.warning("aiogram admin_panel: denied callback telegram_id=%s", callback.from_user.id)
        return False
    return True


async def _edit_callback_message(
    callback: CallbackQuery,
    text: str,
    *,
    reply_markup=None,
    parse_mode: str | None = None,
) -> Message | None:
    if not isinstance(callback.message, Message):
        await callback.answer("Экран уже недоступен. Откройте меню заново.", show_alert=True)
        return None

    try:
        return await callback.message.edit_text(text, reply_markup=reply_markup, parse_mode=parse_mode)
    except TelegramBadRequest as exc:
        if "message is not modified" in str(exc).lower():
            return callback.message
        raise


async def _show_admin_panel_message(message: Message) -> None:
    summary_text, pending_count = await panel_core._build_admin_summary()
    sent = await message.answer(
        summary_text,
        reply_markup=to_aiogram_markup(panel_core.build_admin_panel_keyboard(pending_count)),
        parse_mode="HTML",
    )
    if message.from_user:
        await track_bot_message(message.from_user.id, sent.chat.id, sent.message_id, screen="admin_panel")


async def _show_admin_panel_callback(callback: CallbackQuery, *, screen: str = "admin_panel") -> None:
    summary_text, pending_count = await panel_core._build_admin_summary()
    edited = await _edit_callback_message(
        callback,
        summary_text,
        reply_markup=to_aiogram_markup(panel_core.build_admin_panel_keyboard(pending_count)),
        parse_mode="HTML",
    )
    if edited and callback.from_user:
        await track_bot_message(callback.from_user.id, edited.chat.id, edited.message_id, screen=screen)


@router.message(Command("admin"))
async def cmd_admin(message: Message) -> None:
    if not message.from_user:
        return
    if await _reject_if_group_message(message):
        return
    if not await _require_admin_message(message):
        return
    await _show_admin_panel_message(message)
    logger.info("aiogram admin_panel: opened telegram_id=%s", message.from_user.id)


@router.callback_query(F.data == "panel:admin")
@router.callback_query(F.data == "nav:back:admin")
@router.callback_query(F.data == "admin:back")
async def callback_open_admin_panel(callback: CallbackQuery, state: FSMContext) -> None:
    if await _reject_if_group_callback(callback):
        return
    if not await _require_admin_callback(callback):
        return
    await state.clear()
    await _show_admin_panel_callback(callback, screen=callback.data or "admin_panel")
    await callback.answer()


@router.callback_query(F.data == "admin:menu:polls")
async def menu_polls(callback: CallbackQuery) -> None:
    if not await _require_admin_callback(callback):
        return
    await _edit_callback_message(
        callback,
        "📋 <b>Опросы</b>\n\nВыберите действие:",
        reply_markup=to_aiogram_markup(panel_core.build_polls_submenu_keyboard()),
        parse_mode="HTML",
    )
    await callback.answer()


@router.callback_query(F.data == "admin:menu:users")
async def menu_users(callback: CallbackQuery) -> None:
    if not await _require_admin_callback(callback):
        return
    await _edit_callback_message(
        callback,
        "👥 <b>Пользователи</b>\n\nВыберите действие:",
        reply_markup=to_aiogram_markup(panel_core.build_users_submenu_keyboard()),
        parse_mode="HTML",
    )
    await callback.answer()


@router.callback_query(F.data == "admin:menu:requests")
async def menu_requests(callback: CallbackQuery) -> None:
    if not await _require_admin_callback(callback):
        return
    await _edit_callback_message(
        callback,
        "📨 <b>Заявки</b>\n\nВыберите действие:",
        reply_markup=to_aiogram_markup(panel_core.build_requests_submenu_keyboard()),
        parse_mode="HTML",
    )
    await callback.answer()


@router.callback_query(F.data == "admin:menu:reports")
async def menu_reports(callback: CallbackQuery) -> None:
    if not await _require_admin_callback(callback):
        return
    await _edit_callback_message(
        callback,
        "📊 <b>Отчёты</b>\n\nВыберите действие:",
        reply_markup=to_aiogram_markup(panel_core.build_reports_submenu_keyboard()),
        parse_mode="HTML",
    )
    await callback.answer()


@router.callback_query(F.data == "admin:menu:flud")
async def menu_flud(callback: CallbackQuery) -> None:
    if not await _require_admin_callback(callback):
        return
    await _edit_callback_message(
        callback,
        "🧃 <b>Пульс флудилки</b>\n\nВыберите действие:",
        reply_markup=to_aiogram_markup(panel_core.build_flud_submenu_keyboard()),
        parse_mode="HTML",
    )
    await callback.answer()


@router.callback_query(F.data == "admin:menu:system")
async def menu_system(callback: CallbackQuery) -> None:
    if not await _require_admin_callback(callback):
        return
    await _edit_callback_message(
        callback,
        "⚙️ <b>Система</b>\n\nВыберите действие:",
        reply_markup=to_aiogram_markup(panel_core.build_system_submenu_keyboard()),
        parse_mode="HTML",
    )
    await callback.answer()


@router.callback_query(F.data == "admin:system:status")
async def system_status(callback: CallbackQuery) -> None:
    if not await _require_admin_callback(callback):
        return

    ews_configured = bool(os.getenv("EXCHANGE_EWS_URL"))
    report_mail_enabled = os.getenv("REPORT_MAIL_ENABLED", "0") == "1"
    report_monthly_enabled = os.getenv("REPORT_MONTHLY_ENABLED", "0") == "1"
    admin_panel_enabled = panel_core.is_admin_panel_enabled()
    text = (
        "📊 <b>Статус системы</b>\n\n"
        f"📧 EWS: {'✅ настроен' if ews_configured else '❌ не настроен'}\n"
        f"📨 Email-отчёты: {'✅ вкл' if report_mail_enabled else '❌ выкл'}\n"
        f"🗓 Месячные отчёты: {'✅ вкл' if report_monthly_enabled else '❌ выкл'}\n"
        f"🛠 Админ-панель: {'✅ вкл' if admin_panel_enabled else '❌ выкл'}"
    )
    await _edit_callback_message(
        callback,
        text,
        reply_markup=to_aiogram_markup(panel_core.build_navigation_keyboard("admin:system:status")),
        parse_mode="HTML",
    )
    if callback.from_user:
        await panel_core.record_admin_action(action="system_status", admin_id=callback.from_user.id)
    await callback.answer()


@router.callback_query(F.data == "admin:users")
async def users_dept_selector(callback: CallbackQuery) -> None:
    if not await _require_admin_callback(callback):
        return
    ozko_count = await UsersRepo.count_active_by_department(1)
    osb_count = await UsersRepo.count_active_by_department(2)
    rows = [
        [InlineKeyboardButton(text=f"ОЗКО ({ozko_count})", callback_data="admin:users:dept:1")],
        [InlineKeyboardButton(text=f"ОСБ ({osb_count})", callback_data="admin:users:dept:2")],
        [InlineKeyboardButton(text="🔍 Найти пользователя", callback_data="admin:user:search")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="admin:menu:users")],
    ]
    await _edit_callback_message(
        callback,
        "👥 <b>Пользователи</b>\n\nВыберите отдел:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
        parse_mode="HTML",
    )
    await callback.answer()


@router.callback_query(F.data.startswith("admin:users:dept:"))
async def users_dept_list(callback: CallbackQuery) -> None:
    if not await _require_admin_callback(callback):
        return
    parts = (callback.data or "").split(":")
    try:
        dept_id = int(parts[3])
    except (ValueError, IndexError):
        await callback.answer("Ошибка данных.", show_alert=True)
        return
    page = 0
    if len(parts) >= 6 and parts[4] == "p":
        try:
            page = int(parts[5])
        except ValueError:
            page = 0
    await _show_dept_users(callback, dept_id, page=page)


async def _show_dept_users(callback: CallbackQuery, dept_id: int, *, page: int = 0) -> None:
    users = await UsersRepo.get_active_by_department(dept_id)
    dept_name = DEPT_NAMES.get(dept_id, "—")
    per_page = 15
    total = len(users)
    total_pages = max(1, (total + per_page - 1) // per_page)
    page = max(0, min(page, total_pages - 1))
    page_users = users[page * per_page : (page + 1) * per_page]

    rows = [
        [
            InlineKeyboardButton(
                text=user.get("full_name") or "(без ФИО)",
                callback_data=f"admin:user:{user.get('id')}",
            )
        ]
        for user in page_users
    ]
    if total_pages > 1:
        pager: list[InlineKeyboardButton] = []
        if page > 0:
            pager.append(InlineKeyboardButton(text="◀️", callback_data=f"admin:users:dept:{dept_id}:p:{page - 1}"))
        pager.append(InlineKeyboardButton(text=f"{page + 1}/{total_pages}", callback_data="nav:ignore"))
        if page < total_pages - 1:
            pager.append(InlineKeyboardButton(text="▶️", callback_data=f"admin:users:dept:{dept_id}:p:{page + 1}"))
        rows.append(pager)
    rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="admin:users")])

    await _edit_callback_message(
        callback,
        f"👥 <b>{dept_name}</b> ({total})\n\nВыберите сотрудника:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
        parse_mode="HTML",
    )
    await callback.answer()


@router.callback_query(F.data.regexp(r"^admin:user:\d+$"))
async def user_card(callback: CallbackQuery) -> None:
    if not await _require_admin_callback(callback):
        return
    try:
        user_id = int((callback.data or "").split(":")[2])
    except (ValueError, IndexError):
        await callback.answer("Ошибка данных.", show_alert=True)
        return
    await _show_user_card(callback, user_id)
    await callback.answer()


async def _show_user_card(callback: CallbackQuery, target_user_id: int) -> None:
    target_user = await UsersRepo.get_by_id(target_user_id)
    if not target_user:
        await _edit_callback_message(
            callback,
            "❌ Пользователь не найден.",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[[InlineKeyboardButton(text="⬅️ Назад", callback_data="admin:users")]]
            ),
            parse_mode="HTML",
        )
        return

    full_name = target_user.get("full_name") or "(без ФИО)"
    dept_id = target_user.get("department_id")
    dept_name = DEPT_NAMES.get(dept_id, "—") if dept_id else "—"
    role = target_user.get("role", "user")
    work_mode = target_user.get("work_mode", "")
    work_mode_labels = {
        "HYBRID": "🏢 Гибрид",
        "HYBRID_PLUS": "🏢+ Гибрид+",
        "FULL_REMOTE": "🏠 Полная удалёнка",
        "DUTY": "⏰ Дежурство",
    }
    role_labels = {
        "admin": "👑 Админ",
        "manager": "👔 Менеджер",
        "employee": "👤 Сотрудник",
        "user": "👤 Пользователь",
        UserRole.NO_TG_WORKER.value: "👤 Работник без ТГ",
    }
    created_at = target_user.get("created_at")
    created_str = created_at.strftime("%d.%m.%Y") if created_at else "—"
    telegram_id = target_user.get("telegram_id") or 0
    status = str(target_user.get("status") or UserStatus.ACTIVE.value)
    is_terminated = status == UserStatus.TERMINATED.value
    birthday = target_user.get("birthday")
    if birthday:
        birthday_str = birthday.strftime("%d.%m.%Y") if birthday.year != 2000 else birthday.strftime("%d.%m")
    else:
        birthday_str = "не указан"

    text = (
        f"👤 <b>{full_name}</b>\n\n"
        f"📂 Отдел: {dept_name}\n"
        f"{role_labels.get(role, role)}\n"
        f"{work_mode_labels.get(work_mode, work_mode or '—')}\n"
        f"🎂 День рождения: {birthday_str}\n"
        f"📅 Зарегистрирован: {created_str}\n"
        f"🆔 Telegram: <code>{telegram_id or '—'}</code>\n"
        f"📊 Статус: {'❌ уволен' if is_terminated else '✅ активный'}"
    )

    rows: list[list[InlineKeyboardButton]] = []
    if telegram_id and not is_terminated:
        rows.append(
            [InlineKeyboardButton(text="🚫 Отозвать доступ", callback_data=f"access:confirm:revoke:{telegram_id}:admin")]
        )
        if dept_id == 1:
            rows.append([InlineKeyboardButton(text="🏢 Изменить режим работы", callback_data=f"admin:wm:{target_user_id}")])
            rows.append([InlineKeyboardButton(text="🎂 День рождения", callback_data=f"admin:bday:{target_user_id}")])
    rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data=f"admin:users:dept:{dept_id}" if dept_id else "admin:users")])

    await _edit_callback_message(
        callback,
        text,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
        parse_mode="HTML",
    )


@router.callback_query(F.data == "admin:user:search")
async def user_search_start(callback: CallbackQuery, state: FSMContext) -> None:
    if not await _require_admin_callback(callback):
        return
    await state.set_state(AdminPanelStates.user_search)
    await _edit_callback_message(
        callback,
        "🔍 Введите часть ФИО для поиска:",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="❌ Отмена", callback_data="admin:menu:users")],
            ]
        ),
        parse_mode="HTML",
    )
    await callback.answer()


@router.message(AdminPanelStates.user_search, F.text)
async def user_search_input(message: Message, state: FSMContext) -> None:
    if not message.from_user or not message.text:
        return
    if message.text.startswith("/"):
        await state.clear()
        return
    if not await _require_admin_message(message):
        await state.clear()
        return

    query = message.text.strip()
    if len(query) < 2:
        await message.answer("Введите минимум 2 символа для поиска.")
        return

    results = await UsersRepo.search_active_users(query, limit=10)
    rows: list[list[InlineKeyboardButton]] = []
    if results:
        for user in results:
            full_name = user.get("full_name") or "(без ФИО)"
            dept_id = user.get("department_id")
            dept_label = DEPT_NAMES.get(dept_id, "") if dept_id else ""
            button_text = f"{full_name} ({dept_label})" if dept_label else full_name
            rows.append([InlineKeyboardButton(text=button_text, callback_data=f"admin:user:{user.get('id')}")])
        text = f"🔍 Результаты по «{query}» ({len(results)}):"
    else:
        text = f"🔍 По запросу «{query}» ничего не найдено."

    rows.append([InlineKeyboardButton(text="🔍 Искать снова", callback_data="admin:user:search")])
    rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="admin:menu:users")])
    await message.answer(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=rows), parse_mode="HTML")
    await state.clear()


@router.callback_query(F.data.regexp(r"^admin:wm:\d+$"))
async def work_mode_select(callback: CallbackQuery) -> None:
    if not await _require_admin_callback(callback):
        return
    try:
        target_user_id = int((callback.data or "").split(":")[2])
    except (ValueError, IndexError):
        await callback.answer("Ошибка данных.", show_alert=True)
        return

    target_user = await UsersRepo.get_by_id(target_user_id)
    if not target_user:
        await callback.answer("Пользователь не найден", show_alert=True)
        return

    current_mode = target_user.get("work_mode", "")
    current_role = str(target_user.get("role") or UserRole.EMPLOYEE.value)
    full_name = target_user.get("full_name") or "(без ФИО)"
    mode_labels = {
        "HYBRID": "🏢 Гибрид",
        "HYBRID_PLUS": "🏢+ Гибрид+",
        "FULL_REMOTE": "🏠 Полная удалёнка",
        "DUTY": "⏰ Дежурство",
    }
    rows = [
        [
            InlineKeyboardButton(
                text=f"{'• ' if mode_key == current_mode else ''}{label}",
                callback_data=f"admin:wm:set:{target_user_id}:{mode_key}",
            )
        ]
        for mode_key, label in mode_labels.items()
    ]
    if current_role not in (UserRole.ADMIN.value, UserRole.MANAGER.value):
        is_no_tg = current_role == UserRole.NO_TG_WORKER.value
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"{'• ' if is_no_tg else ''}👤 Работник без ТГ",
                    callback_data=f"admin:wm:type:{target_user_id}:{UserRole.NO_TG_WORKER.value}",
                )
            ]
        )
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"{'• ' if not is_no_tg else ''}👤 Обычный сотрудник",
                    callback_data=f"admin:wm:type:{target_user_id}:{UserRole.EMPLOYEE.value}",
                )
            ]
        )
    rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data=f"admin:user:{target_user_id}")])
    await _edit_callback_message(
        callback,
        "🏢 <b>Режим работы</b>\n\n"
        f"👤 {full_name}\n"
        f"Текущий: {mode_labels.get(current_mode, current_mode or '—')}\n\n"
        "Выберите новый режим или тип учёта:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
        parse_mode="HTML",
    )
    await callback.answer()


@router.callback_query(F.data.startswith("admin:wm:set:"))
async def work_mode_set(callback: CallbackQuery) -> None:
    if not await _require_admin_callback(callback):
        return
    parts = (callback.data or "").split(":")
    try:
        target_user_id = int(parts[3])
        work_mode = WorkMode(parts[4])
    except (ValueError, IndexError):
        await callback.answer("❌ Неизвестный режим.", show_alert=True)
        return

    await UsersRepo.update_work_mode(target_user_id, work_mode)
    if callback.from_user:
        await panel_core.record_admin_action(
            action="change_work_mode",
            admin_id=callback.from_user.id,
            parameters={"user_id": target_user_id, "mode": work_mode.value},
        )
    logger.info(
        "aiogram admin_panel: work_mode changed user_id=%s to %s by %s",
        target_user_id,
        work_mode.value,
        callback.from_user.id if callback.from_user else None,
    )
    await callback.answer("✅ Режим обновлён", show_alert=True)
    await _show_user_card(callback, target_user_id)


@router.callback_query(F.data.startswith("admin:wm:type:"))
async def work_accounting_type_set(callback: CallbackQuery) -> None:
    if not await _require_admin_callback(callback):
        return
    parts = (callback.data or "").split(":")
    try:
        target_user_id = int(parts[3])
        role = UserRole(parts[4])
    except (ValueError, IndexError):
        await callback.answer("❌ Неизвестный тип учёта.", show_alert=True)
        return
    if role not in (UserRole.EMPLOYEE, UserRole.NO_TG_WORKER):
        await callback.answer("❌ Этот тип нельзя назначить здесь.", show_alert=True)
        return

    target_user = await UsersRepo.get_by_id(target_user_id)
    if not target_user:
        await callback.answer("Пользователь не найден", show_alert=True)
        return
    if target_user.get("role") in (UserRole.ADMIN.value, UserRole.MANAGER.value):
        await callback.answer("❌ Нельзя менять тип учёта для admin/manager.", show_alert=True)
        return

    await UsersRepo.update_role(target_user_id, role)
    if callback.from_user:
        await panel_core.record_admin_action(
            action="change_user_accounting_type",
            admin_id=callback.from_user.id,
            parameters={"user_id": target_user_id, "role": role.value},
        )
    logger.info(
        "aiogram admin_panel: accounting type changed user_id=%s to %s by %s",
        target_user_id,
        role.value,
        callback.from_user.id if callback.from_user else None,
    )
    await callback.answer("✅ Тип учёта обновлён", show_alert=True)
    await _show_user_card(callback, target_user_id)


async def _admin_user_id(telegram_id: int) -> int:
    user = await UsersRepo.get_by_telegram_id(telegram_id)
    return int(user["id"]) if user and user.get("id") else 0


def _nav_markup(refresh_action: str | None = None) -> InlineKeyboardMarkup | None:
    return to_aiogram_markup(panel_core.build_navigation_keyboard(refresh_action))


@router.callback_query(F.data == "admin:flud:week")
async def flud_week_preview(callback: CallbackQuery) -> None:
    if not await _require_admin_callback(callback):
        return
    text, _, _ = await build_current_weekly_flud_digest()
    await _edit_callback_message(
        callback,
        text or "FLUD_CHAT_ID не настроен.",
        reply_markup=to_aiogram_markup(panel_core.build_flud_submenu_keyboard()),
        parse_mode="HTML",
    )
    await callback.answer()


@router.callback_query(F.data == "admin:flud:month")
async def flud_month_preview(callback: CallbackQuery) -> None:
    if not await _require_admin_callback(callback):
        return
    text, _, _, _, _ = await build_current_monthly_flud_digest()
    await _edit_callback_message(
        callback,
        text or "FLUD_CHAT_ID не настроен.",
        reply_markup=to_aiogram_markup(panel_core.build_flud_submenu_keyboard()),
        parse_mode="HTML",
    )
    await callback.answer()


@router.callback_query(F.data == "admin:flud:prev_month")
async def flud_previous_month_preview(callback: CallbackQuery) -> None:
    if not await _require_admin_callback(callback):
        return
    year, month, _, _ = previous_month_period()
    text, _, _, _, _ = await build_monthly_flud_digest_for_month(year, month)
    await _edit_callback_message(
        callback,
        text or "FLUD_CHAT_ID не настроен.",
        reply_markup=to_aiogram_markup(panel_core.build_flud_submenu_keyboard()),
        parse_mode="HTML",
    )
    await callback.answer()


@router.callback_query(F.data == "admin:flud:top")
async def flud_top_preview(callback: CallbackQuery) -> None:
    if not await _require_admin_callback(callback):
        return
    flud_chat_id = get_flud_chat_id()
    if not flud_chat_id:
        text = "FLUD_CHAT_ID не настроен."
    else:
        from core.screw_core.services.flud_stats import format_top_messages

        _, _, period_start, period_end = current_month_period()
        top = await FludRepo.get_top_reacted_messages(period_start, period_end, flud_chat_id, get_flud_top_limit())
        text = "🏆 <b>Топ сообщений флудилки</b>\n\n" + format_top_messages(top, get_flud_top_limit())
    await _edit_callback_message(
        callback,
        text,
        reply_markup=to_aiogram_markup(panel_core.build_flud_submenu_keyboard()),
        parse_mode="HTML",
    )
    await callback.answer()


def _parse_stats_callback(data: str) -> tuple[int, int, int]:
    parts = data.split(":")
    year_text, month_text = parts[3].split("-", maxsplit=1)
    return int(year_text), int(month_text), int(parts[4])


@router.callback_query(F.data == "admin:report:monthly:current")
@router.callback_query(F.data == "admin:report:monthly:current:confirm")
@router.callback_query(F.data.regexp(r"^admin:stats:m:[01]$"))
async def monthly_stats_months(callback: CallbackQuery) -> None:
    """Show the 12-month picker and redirect callbacks from the old direct action."""
    if not await _require_admin_callback(callback):
        return
    data = callback.data or ""
    page = int(data.rsplit(":", maxsplit=1)[-1]) if data.startswith("admin:stats:m:") else 0
    await _edit_callback_message(
        callback,
        "📊 <b>Статистика явки ОЗКО</b>\n\nВыберите месяц для предпросмотра.",
        reply_markup=to_aiogram_markup(panel_core.build_monthly_stats_months_keyboard(page)),
        parse_mode="HTML",
    )
    await callback.answer()


@router.callback_query(F.data.regexp(r"^admin:stats:v:\d{4}-\d{2}:[01]$"))
async def monthly_stats_preview(callback: CallbackQuery) -> None:
    if not await _require_admin_callback(callback):
        return
    year, month, page = _parse_stats_callback(callback.data or "")
    if not 1 <= month <= 12:
        await callback.answer("Некорректный месяц.", show_alert=True)
        return

    from apps.attendance_bot.app.jobs.monthly_stats import build_monthly_stats_report

    report = await build_monthly_stats_report(year, month)
    if report is None:
        text = f"📊 <b>Статистика явки ОЗКО</b>\n\nЗа {month:02d}/{year} данных нет."
    else:
        warning = ""
        if report.is_approximate:
            warning = (
                "⚠️ <i>Исторический расчёт приблизительный: для части опросов "
                "нет зафиксированного снимка участников.</i>\n\n"
            )
        text = warning + report.html

    await _edit_callback_message(
        callback,
        text,
        reply_markup=to_aiogram_markup(panel_core.build_monthly_stats_preview_keyboard(year, month, page)),
        parse_mode="HTML",
    )
    await callback.answer()


@router.callback_query(F.data.regexp(r"^admin:stats:p:\d{4}-\d{2}:[01]$"))
async def monthly_stats_publish(callback: CallbackQuery, bot: Bot) -> None:
    if not await _require_admin_callback(callback):
        return
    year, month, page = _parse_stats_callback(callback.data or "")
    if not 1 <= month <= 12:
        await callback.answer("Некорректный месяц.", show_alert=True)
        return
    if panel_core.is_confirm_enabled():
        await _edit_callback_message(
            callback,
            f"❓ <b>Опубликовать статистику явки ОЗКО за {month:02d}/{year}?</b>",
            reply_markup=to_aiogram_markup(
                panel_core.build_monthly_stats_publish_confirm_keyboard(year, month, page)
            ),
            parse_mode="HTML",
        )
        await callback.answer()
        return
    await _publish_monthly_stats(callback, bot, year, month, page)


@router.callback_query(F.data.regexp(r"^admin:stats:p:\d{4}-\d{2}:[01]:ok$"))
async def monthly_stats_publish_confirmed(callback: CallbackQuery, bot: Bot) -> None:
    if not await _require_admin_callback(callback):
        return
    year, month, page = _parse_stats_callback(callback.data or "")
    if not 1 <= month <= 12:
        await callback.answer("Некорректный месяц.", show_alert=True)
        return
    await _publish_monthly_stats(callback, bot, year, month, page)


async def _publish_monthly_stats(
    callback: CallbackQuery,
    bot: Bot,
    year: int,
    month: int,
    page: int,
) -> None:
    from apps.attendance_bot.app.jobs.monthly_stats import send_monthly_stats_to_violations_chat

    await callback.answer("Публикую статистику...")
    admin_id = callback.from_user.id if callback.from_user else 0
    try:
        success, status, chat_id = await send_monthly_stats_to_violations_chat(bot, year, month)
        if success:
            text = f"✅ Статистика явки ОЗКО за {month:02d}/{year} опубликована."
        else:
            text = f"❌ Статистика не опубликована: {html.escape(status)}"
        await _edit_callback_message(
            callback,
            text,
            reply_markup=to_aiogram_markup(panel_core.build_monthly_stats_months_keyboard(page)),
            parse_mode="HTML",
        )
        await panel_core.record_admin_action(
            action="monthly_stats_publish",
            admin_id=admin_id,
            parameters={"year": year, "month": month, "success": success, "chat_id": chat_id},
            error_summary=status if not success else None,
        )
    except Exception as exc:
        await _edit_callback_message(
            callback,
            "⚠️ Ошибка: сервис временно недоступен",
            reply_markup=to_aiogram_markup(panel_core.build_monthly_stats_preview_keyboard(year, month, page)),
        )
        await panel_core.record_admin_action(
            action="monthly_stats_publish",
            admin_id=admin_id,
            parameters={"year": year, "month": month},
            error_summary=str(exc)[:100],
        )
        raise


@router.callback_query(F.data == "admin:poll:close")
@router.callback_query(F.data == "admin:poll:create")
@router.callback_query(F.data == "admin:report:daily:today")
@router.callback_query(F.data == "admin:flud:publish_week")
@router.callback_query(F.data == "admin:flud:publish_month")
@router.callback_query(F.data == "admin:flud:publish_prev_month")
async def dangerous_action(callback: CallbackQuery, bot: Bot) -> None:
    if not await _require_admin_callback(callback):
        return
    action = callback.data or ""
    if panel_core.is_confirm_enabled() and action in DANGEROUS_ACTIONS:
        await _show_confirm(callback, action)
        return
    await _execute_action(callback, bot, action)


@router.callback_query(F.data == "admin:poll:close:confirm")
@router.callback_query(F.data == "admin:poll:create:confirm")
@router.callback_query(F.data == "admin:report:daily:today:confirm")
@router.callback_query(F.data == "admin:flud:publish_week:confirm")
@router.callback_query(F.data == "admin:flud:publish_month:confirm")
@router.callback_query(F.data == "admin:flud:publish_prev_month:confirm")
async def confirmed_action(callback: CallbackQuery, bot: Bot) -> None:
    if not await _require_admin_callback(callback):
        return
    action = (callback.data or "").removesuffix(":confirm")
    if action not in DANGEROUS_ACTIONS:
        await callback.answer("❓ Неизвестное действие.", show_alert=True)
        return
    await _execute_action(callback, bot, action)


async def _show_confirm(callback: CallbackQuery, action: str) -> None:
    label = panel_core.ACTION_LABELS.get(action, action)
    if action == "admin:poll:create":
        existing = await PollsService.get_current_poll()
        poll_date = poll_core.get_local_date()
        if existing and existing.get("poll_date") == poll_date:
            closes_at = existing.get("closes_at")
            closes_str = closes_at.strftime("%H:%M") if closes_at else "—"
            label = f"⚠️ Сегодня уже есть активный опрос до {closes_str}. Пересоздать?"

    await _edit_callback_message(
        callback,
        f"❓ <b>Подтвердить действие?</b>\n\n{label}",
        reply_markup=to_aiogram_markup(panel_core.build_confirm_keyboard(action)),
        parse_mode="HTML",
    )
    await callback.answer()


async def _execute_action(callback: CallbackQuery, bot: Bot, action: str) -> None:
    admin_id = callback.from_user.id if callback.from_user else 0
    if action == "admin:poll:close":
        await _handle_poll_close(callback, admin_id, bot)
    elif action == "admin:poll:create":
        await _handle_poll_create(callback, admin_id, bot=bot)
    elif action == "admin:report:daily:today":
        await _handle_report_daily(callback, admin_id)
    elif action == "admin:flud:publish_week":
        await _handle_flud_publish_week(callback, admin_id, bot)
    elif action == "admin:flud:publish_month":
        await _handle_flud_publish_month(callback, admin_id, bot)
    elif action == "admin:flud:publish_prev_month":
        await _handle_flud_publish_month(callback, admin_id, bot, previous=True)
    else:
        await callback.answer("❓ Неизвестное действие.", show_alert=True)


async def _handle_flud_publish_week(callback: CallbackQuery, admin_id: int, bot: Bot) -> None:
    await callback.answer("Публикую дайджест...")
    created_by = await _admin_user_id(admin_id) if admin_id else None
    success, status, _ = await send_weekly_flud_digest(bot, created_by=created_by or None)
    if success:
        await _edit_callback_message(
            callback,
            "✅ Дайджест опубликован в чат руководства.",
            reply_markup=to_aiogram_markup(panel_core.build_flud_submenu_keyboard()),
        )
        await panel_core.record_admin_action(action="flud_publish_week", admin_id=admin_id)
    else:
        await _edit_callback_message(
            callback,
            f"❌ Дайджест не опубликован: {html.escape(status)}",
            reply_markup=to_aiogram_markup(panel_core.build_flud_submenu_keyboard()),
            parse_mode="HTML",
        )
        await panel_core.record_admin_action(action="flud_publish_week", admin_id=admin_id, error_summary=status)


async def _handle_flud_publish_month(
    callback: CallbackQuery,
    admin_id: int,
    bot: Bot,
    *,
    previous: bool = False,
) -> None:
    await callback.answer("Публикую дайджест...")
    created_by = await _admin_user_id(admin_id) if admin_id else None
    success, status, _ = await send_monthly_flud_digest(
        bot,
        created_by=created_by or None,
        previous=previous,
    )
    if success:
        action_name = "flud_publish_prev_month" if previous else "flud_publish_month"
        await _edit_callback_message(
            callback,
            "✅ Дайджест опубликован в чат руководства.",
            reply_markup=to_aiogram_markup(panel_core.build_flud_submenu_keyboard()),
        )
        await panel_core.record_admin_action(action=action_name, admin_id=admin_id)
    else:
        action_name = "flud_publish_prev_month" if previous else "flud_publish_month"
        await _edit_callback_message(
            callback,
            f"❌ Дайджест не опубликован: {html.escape(status)}",
            reply_markup=to_aiogram_markup(panel_core.build_flud_submenu_keyboard()),
            parse_mode="HTML",
        )
        await panel_core.record_admin_action(action=action_name, admin_id=admin_id, error_summary=status)


async def _handle_poll_close(callback: CallbackQuery, admin_id: int, bot: Bot) -> None:
    await callback.answer("Закрываю опрос...")
    result = await PollsService.close_current_poll()
    if not result.success:
        error_msg = "Нет открытых опросов" if result.error == PollError.POLL_NOT_FOUND else str(result.error)
        await _edit_callback_message(callback, f"❌ {error_msg}", reply_markup=_nav_markup())
        await panel_core.record_admin_action(action="poll_close", admin_id=admin_id, error_summary=error_msg)
        return

    poll_id = result.poll_id
    poll_data = result.data
    scheduler = poll_core.get_poll_scheduler()
    if poll_id and scheduler:
        remove_poll_auto_close_job(scheduler, poll_id)
        remove_poll_reminder_job(scheduler, poll_id)

    if poll_data:
        chat_id = poll_data.get("chat_id")
        msg_id = poll_data.get("message_id")

        async def _cleanup_poll_artifacts() -> None:
            if chat_id and msg_id:
                await poll_core._stop_telegram_poll_safe(bot, chat_id, msg_id)
                await poll_core._unpin_poll_message(bot, chat_id, msg_id)
            if chat_id and poll_id:
                await poll_core._delete_or_edit_reminder(bot, chat_id, poll_id)

        try:
            await asyncio.wait_for(_cleanup_poll_artifacts(), timeout=30)
        except asyncio.TimeoutError:
            logger.warning("aiogram admin_panel poll_close: cleanup timeout poll_id=%s", poll_id)
        except Exception as exc:
            logger.warning("aiogram admin_panel poll_close: cleanup error poll_id=%s: %s", poll_id, exc)

    poll_date = poll_data.get("poll_date") if poll_data else None
    await _edit_callback_message(callback, f"✅ Опрос #{poll_id} закрыт.", reply_markup=_nav_markup())
    logger.info("aiogram admin_panel: poll_close poll_id=%s admin_id=%s", poll_id, admin_id)
    await panel_core.record_admin_action(
        action="poll_close",
        admin_id=admin_id,
        parameters={"poll_id": poll_id, "poll_date": str(poll_date) if poll_date else None},
    )

    check_date = poll_date if isinstance(poll_date, date) else poll_core.get_local_date()
    await asyncio.sleep(10)
    try:
        await asyncio.wait_for(
            NotifyService.run_post_poll_checks(
                bot,
                check_date,
                department_id=OZKO_DEPARTMENT_ID,
                ignore_day_off=True,
                poll_id=poll_id,
            ),
            timeout=120,
        )
    except asyncio.TimeoutError:
        logger.error("aiogram admin_panel poll_close: post checks timeout poll_id=%s", poll_id)
    except Exception as exc:
        logger.exception("aiogram admin_panel poll_close: post checks failed poll_id=%s: %s", poll_id, exc)
    await poll_core.send_poll_close_summary(
        bot,
        poll_data,
        department_id=OZKO_DEPARTMENT_ID,
    )
    await poll_core.send_department_lists_summary(bot, poll_data)


@router.callback_query(F.data == "admin:poll:create:skipday_ok")
async def poll_create_skip_day_confirmed(callback: CallbackQuery, bot: Bot) -> None:
    if not await _require_admin_callback(callback):
        return
    await _handle_poll_create(callback, callback.from_user.id if callback.from_user else 0, skip_day_check=False, bot=bot)


async def _handle_poll_create(
    callback: CallbackQuery,
    admin_id: int,
    *,
    skip_day_check: bool = True,
    bot: Bot,
) -> None:
    await callback.answer("Создаю опрос...")
    group_id = get_attendance_group_id()
    if not group_id:
        await _edit_callback_message(callback, "❌ ATTENDANCE_GROUP_ID не настроен.", reply_markup=_nav_markup())
        return
    group_check = await check_attendance_group_access(bot)
    if not group_check.accessible:
        await _edit_callback_message(
            callback,
            "❌ Бот сейчас не видит группу для опросов или не имеет нужных прав.\n"
            "Добавьте бота в группу и выдайте права на сообщения, опросы и закрепление.",
            reply_markup=_nav_markup(),
        )
        return

    poll_date = poll_core.get_local_date()
    if skip_day_check:
        skip_info = await PollConfigRepo.get_skip_date_info(poll_date)
        if skip_info:
            reason = skip_info.get("reason") or "без указания причины"
            keyboard = InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(text="✅ Создать всё равно", callback_data="admin:poll:create:skipday_ok"),
                        InlineKeyboardButton(text="❌ Отмена", callback_data="admin:menu:polls"),
                    ]
                ]
            )
            await _edit_callback_message(
                callback,
                f"⚠️ <b>Сегодня ({poll_date.strftime('%d.%m')}) — пропуск:</b>\n"
                f"«{reason}»\n\nСоздать опрос?",
                reply_markup=keyboard,
                parse_mode="HTML",
            )
            return

    existing = await PollsService.get_current_poll()
    if existing and existing.get("poll_date") == poll_date:
        old_poll_id = existing.get("id")
        old_chat_id = existing.get("chat_id")
        old_message_id = existing.get("message_id")
        await PollsService.close_poll(old_poll_id)
        if old_chat_id and old_message_id:
            await poll_core._stop_telegram_poll_safe(bot, old_chat_id, old_message_id)
        scheduler = poll_core.get_poll_scheduler()
        if old_poll_id and scheduler:
            remove_poll_auto_close_job(scheduler, old_poll_id)
            remove_poll_reminder_job(scheduler, old_poll_id)

    poll_config = await poll_core._load_poll_config()
    poll_options, plain_options, api_kwargs = build_aiogram_poll_payload(poll_config.payload)
    duration = poll_config.duration_seconds
    reminder_minutes = poll_config.reminder_minutes
    poll_question = f"Явка за {poll_date.strftime('%d.%m.%Y')}"
    try:
        poll_message = await send_aiogram_poll_with_fallback(
            bot,
            logger,
            chat_id=group_id,
            question=poll_question,
            options=poll_options,
            plain_options=plain_options,
            api_kwargs=api_kwargs,
            is_anonymous=False,
            allows_multiple_answers=False,
        )
    except Exception as exc:
        logger.error("aiogram admin_panel: poll_create failed: %s", exc)
        await _edit_callback_message(callback, f"❌ Ошибка: {exc}", reply_markup=_nav_markup())
        await panel_core.record_admin_action(action="poll_create", admin_id=admin_id, error_summary=str(exc))
        return

    telegram_poll_id = poll_message.poll.id if poll_message.poll else None
    if not telegram_poll_id:
        await _edit_callback_message(callback, "❌ Не получен poll_id от Telegram.", reply_markup=_nav_markup())
        return

    poll_core._remember_poll_option_mapping(
        telegram_poll_id,
        poll_message.poll.options if poll_message.poll else [],
        poll_config.option_to_choice,
        poll_config.persistent_to_choice,
    )
    closes_at = poll_core.get_local_datetime() + timedelta(seconds=duration)
    result = await PollsService.create_daily_poll(
        poll_date=poll_date,
        chat_id=group_id,
        message_id=poll_message.message_id,
        telegram_poll_id=telegram_poll_id,
        closes_at=closes_at,
    )
    if not result.success:
        try:
            await bot.stop_poll(chat_id=group_id, message_id=poll_message.message_id)
        except Exception as exc:
            logger.debug("aiogram admin_panel poll_create cleanup stop_poll failed: %s", exc)
        await _edit_callback_message(callback, f"❌ Ошибка: {result.error}", reply_markup=_nav_markup())
        await panel_core.record_admin_action(action="poll_create", admin_id=admin_id, error_summary=str(result.error))
        return

    poll_id = result.poll_id
    await poll_core._pin_poll_message(
        bot=bot,
        chat_id=group_id,
        message_id=poll_message.message_id,
        poll_date=poll_date,
        notify_user_id=admin_id,
    )
    await poll_core.send_absence_message(bot, group_id, poll_date)

    scheduler = poll_core.get_poll_scheduler()
    if scheduler:
        schedule_poll_auto_close(
            scheduler=scheduler,
            poll_id=poll_id,
            closes_at=closes_at,
            chat_id=group_id,
            message_id=poll_message.message_id,
            telegram_poll_id=telegram_poll_id,
        )
    else:
        logger.warning("aiogram admin_panel: scheduler not found, auto-close not scheduled")

    reminder_info = ""
    if reminder_minutes and reminder_minutes * 60 < duration and scheduler:
        remind_at = closes_at - timedelta(seconds=reminder_minutes * 60)
        schedule_poll_reminder(
            scheduler=scheduler,
            poll_id=poll_id,
            remind_at=remind_at,
            poll_date=poll_date,
            telegram_poll_id=telegram_poll_id,
        )
        reminder_info = f"\nReminder за {reminder_minutes} мин."

    await _edit_callback_message(
        callback,
        f"✅ Опрос #{poll_id} создан.\n\n"
        f"Дата: {poll_date.strftime('%d.%m.%Y')}\n"
        f"Закроется через {duration // 60} мин.{reminder_info}",
        reply_markup=_nav_markup(),
    )
    await panel_core.record_admin_action(
        action="poll_create",
        admin_id=admin_id,
        parameters={"poll_id": poll_id, "poll_date": str(poll_date)},
    )


async def _handle_report_daily(callback: CallbackQuery, admin_id: int) -> None:
    await callback.answer("Отправляю дневной отчёт...")
    today = panel_core.get_local_date()
    try:
        result = await ReportDeliveryService.send_daily_report(report_date=today, manual_retry=True)
        if result.success:
            text = f"✅ Дневной отчёт за {today.strftime('%d.%m.%Y')} отправлен.\n📧 Получателей: {result.recipients_count}"
        else:
            text = f"❌ Ошибка отправки: {result.error_summary or 'сервис временно недоступен'}"
        await _edit_callback_message(callback, text, reply_markup=_nav_markup())
        await panel_core.record_admin_action(
            action="report_daily",
            admin_id=admin_id,
            parameters={"date": str(today), "success": result.success},
            error_summary=result.error_summary if not result.success else None,
        )
    except Exception as exc:
        await _edit_callback_message(callback, "⚠️ Ошибка: сервис временно недоступен", reply_markup=_nav_markup())
        await panel_core.record_admin_action(
            action="report_daily",
            admin_id=admin_id,
            parameters={"date": str(today)},
            error_summary=str(exc)[:100],
        )
        raise


async def _show_poll_settings_screen(callback: CallbackQuery) -> None:
    config = await PollConfigRepo.get_config()
    duration = config.get("poll_duration_seconds", 900)
    reminder = config.get("reminder_before_end_minutes")
    options = config.get("poll_options", [])
    auto_enabled = config.get("auto_send_enabled", False)
    auto_time = config.get("auto_send_time")

    duration_text = f"{duration // 60} мин." if duration < 3600 else f"{duration // 3600} ч."
    reminder_text = f"за {reminder} мин. до закрытия" if reminder else "выключено"
    if auto_enabled and auto_time:
        auto_text = f"🟢 {auto_time.strftime('%H:%M')} MSK"
    elif auto_time:
        auto_text = f"🔴 выкл. (время: {auto_time.strftime('%H:%M')})"
    else:
        auto_text = "🔴 выключена"
    options_text = "\n".join(f"  {idx + 1}. {opt.get('label', opt.get('key', '?'))}" for idx, opt in enumerate(options))

    rows = [
        [
            InlineKeyboardButton(text="⏱ Длительность", callback_data="admin:poll:cfg:duration"),
            InlineKeyboardButton(text="🔔 Напоминание", callback_data="admin:poll:cfg:reminder"),
        ],
        [InlineKeyboardButton(text="✏️ Варианты ответов", callback_data="admin:poll:cfg:options")],
        [InlineKeyboardButton(text="🧩 Rich UX диагностика", callback_data="admin:poll:cfg:rich")],
        [InlineKeyboardButton(text="🕐 Авто-отправка", callback_data="admin:poll:cfg:auto")],
        [InlineKeyboardButton(text="🏢 Нормы офисных дней", callback_data="admin:poll:cfg:office_req")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="admin:menu:polls")],
    ]
    await _edit_callback_message(
        callback,
        "⚙️ <b>Настройки опросов</b>\n\n"
        f"⏱ Длительность: {duration_text}\n"
        f"🔔 Напоминание: {reminder_text}\n"
        f"🕐 Авто-отправка: {auto_text}\n\n"
        f"📋 Варианты ответов:\n{options_text}",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
        parse_mode="HTML",
    )


@router.callback_query(F.data == "admin:poll:settings")
async def poll_settings(callback: CallbackQuery) -> None:
    if not await _require_admin_callback(callback):
        return
    await _show_poll_settings_screen(callback)
    await callback.answer()


def _yes_no(value: bool) -> str:
    return "🟢 да" if value else "⚪ нет"


def _media_dict(value: object) -> dict[str, object] | None:
    if isinstance(value, dict) and value:
        return value
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) and parsed else None


def _media_file_id(media: dict[str, object] | None) -> str | None:
    if not media:
        return None
    value = media.get("media")
    return str(value).strip() if value else None


async def _validate_poll_media_file(bot: Bot, label: str, media: dict[str, object] | None) -> str:
    media_type = str((media or {}).get("type") or "unknown")
    file_id = _media_file_id(media)
    if not media:
        return f"▫️ {html.escape(label)}: media не настроено"
    if media_type in {"location", "venue"}:
        return f"🟡 {html.escape(label)}: type={html.escape(media_type)}, file_id не требуется"
    if not file_id:
        return f"🔴 {html.escape(label)}: media есть, но file_id пустой"

    try:
        await bot.get_file(file_id)
    except Exception as exc:
        reason = html.escape(redact_text(exc))
        return f"🔴 {html.escape(label)}: file_id не проходит get_file — <code>{reason}</code>"
    return f"🟢 {html.escape(label)}: file_id валиден"


async def _show_poll_rich_diagnostics_screen(callback: CallbackQuery) -> None:
    config = await PollConfigRepo.get_config()
    options = config.get("poll_options", [])
    if not isinstance(options, list):
        options = []
    diagnostics = build_poll_payload_diagnostics(options, config)

    fallback = diagnostics.get("last_fallback")
    if fallback:
        fallback_text = (
            f"🟡 был fallback: {html.escape(fallback.get('at', ''))}, "
            f"{html.escape(fallback.get('runtime', ''))}\n"
            f"Причина: <code>{html.escape(fallback.get('reason', ''))}</code>"
        )
    else:
        fallback_text = "🟢 fallback не фиксировался с момента старта процесса"
    open_period = diagnostics.get("open_period")
    if open_period:
        open_period_text = str(open_period)
    elif diagnostics.get("duration_seconds"):
        open_period_text = f"нет ({diagnostics['duration_seconds']} сек. вне диапазона 5-2628000)"
    else:
        open_period_text = "нет"

    text = (
        "🧩 <b>Rich UX диагностика poll</b>\n\n"
        f"Описание: {_yes_no(bool(diagnostics['has_description']))}\n"
        f"Media описания: {_yes_no(bool(diagnostics['has_poll_media']))}\n"
        f"Media вариантов: <b>{diagnostics['option_media_count']}</b>\n"
        f"Persistent ID вариантов: <b>{diagnostics['persistent_id_count']}</b>\n"
        f"Telegram open_period: <b>{open_period_text}</b>\n\n"
        "<b>Безопасные флаги</b>\n"
        f"allows_revoting: <b>{diagnostics['allows_revoting']}</b>\n"
        f"allow_adding_options: <b>{diagnostics['allow_adding_options']}</b>\n"
        f"shuffle_options: <b>{diagnostics['shuffle_options']}</b>\n\n"
        f"{fallback_text}"
    )
    await _edit_callback_message(
        callback,
        text,
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="🧪 Проверить media", callback_data="admin:poll:cfg:rich:media")],
                [InlineKeyboardButton(text="⬅️ Назад", callback_data="admin:poll:settings")],
            ]
        ),
        parse_mode="HTML",
    )


@router.callback_query(F.data == "admin:poll:cfg:rich")
async def poll_cfg_rich_diagnostics(callback: CallbackQuery) -> None:
    if not await _require_admin_callback(callback):
        return
    await _show_poll_rich_diagnostics_screen(callback)
    await callback.answer()


@router.callback_query(F.data == "admin:poll:cfg:rich:media")
async def poll_cfg_rich_media_check(callback: CallbackQuery, bot: Bot) -> None:
    if not await _require_admin_callback(callback):
        return

    config = await PollConfigRepo.get_config()
    options = config.get("poll_options", [])
    if not isinstance(options, list):
        options = []

    lines = ["🧪 <b>Проверка media опроса</b>\n"]
    top_level_media = _media_dict(config.get("poll_media")) or _media_dict(config.get("media")) or _media_dict(
        os.getenv("POLL_MEDIA_JSON")
    )
    lines.append(await _validate_poll_media_file(bot, "Описание опроса", top_level_media))

    if options:
        for idx, option in enumerate(options, 1):
            if not isinstance(option, dict):
                lines.append(f"🔴 Вариант #{idx}: некорректный JSON-объект")
                continue
            label = str(option.get("label") or option.get("key") or f"#{idx}")
            media = _media_dict(option.get("media"))
            lines.append(await _validate_poll_media_file(bot, f"Вариант #{idx}: {label}", media))
    else:
        lines.append("🔴 Варианты опроса не настроены")

    await _edit_callback_message(
        callback,
        "\n".join(lines),
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="🔄 Повторить проверку", callback_data="admin:poll:cfg:rich:media")],
                [InlineKeyboardButton(text="🧩 Rich диагностика", callback_data="admin:poll:cfg:rich")],
                [InlineKeyboardButton(text="⬅️ Назад", callback_data="admin:poll:settings")],
            ]
        ),
        parse_mode="HTML",
    )
    await callback.answer()


@router.callback_query(F.data == "admin:poll:cfg:duration")
async def poll_cfg_duration(callback: CallbackQuery) -> None:
    if not await _require_admin_callback(callback):
        return
    config = await PollConfigRepo.get_config()
    current = config.get("poll_duration_seconds", 900)
    presets = [
        (300, "5 мин."),
        (600, "10 мин."),
        (900, "15 мин."),
        (1800, "30 мин."),
        (3600, "1 час"),
        (7200, "2 часа"),
        (14400, "4 часа"),
    ]
    rows: list[list[InlineKeyboardButton]] = []
    row: list[InlineKeyboardButton] = []
    for seconds, label in presets:
        row.append(
            InlineKeyboardButton(
                text=f"{'• ' if seconds == current else ''}{label}",
                callback_data=f"admin:poll:cfg:dur:{seconds}",
            )
        )
        if len(row) == 4:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="admin:poll:settings")])
    current_text = f"{current // 60} мин." if current < 3600 else f"{current // 3600} ч."
    await _edit_callback_message(
        callback,
        f"⏱ <b>Длительность опроса</b>\n\nТекущее: {current_text}",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
        parse_mode="HTML",
    )
    await callback.answer()


@router.callback_query(F.data.startswith("admin:poll:cfg:dur:"))
async def poll_cfg_duration_set(callback: CallbackQuery) -> None:
    if not await _require_admin_callback(callback):
        return
    try:
        seconds = int((callback.data or "").split(":")[-1])
    except ValueError:
        await callback.answer("Ошибка данных.", show_alert=True)
        return
    await PollConfigRepo.update_duration(seconds, await _admin_user_id(callback.from_user.id))
    await callback.answer("✅ Длительность обновлена", show_alert=True)
    await _show_poll_settings_screen(callback)


@router.callback_query(F.data == "admin:poll:cfg:reminder")
async def poll_cfg_reminder(callback: CallbackQuery) -> None:
    if not await _require_admin_callback(callback):
        return
    config = await PollConfigRepo.get_config()
    current = config.get("reminder_before_end_minutes")
    presets = [(0, "Выкл."), (1, "1 мин."), (3, "3 мин."), (5, "5 мин."), (10, "10 мин.")]
    row = [
        InlineKeyboardButton(
            text=f"{'• ' if (minutes == 0 and not current) or minutes == current else ''}{label}",
            callback_data=f"admin:poll:cfg:rem:{minutes}",
        )
        for minutes, label in presets
    ]
    current_text = f"за {current} мин." if current else "выключено"
    await _edit_callback_message(
        callback,
        f"🔔 <b>Напоминание перед закрытием</b>\n\nТекущее: {current_text}",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[row, [InlineKeyboardButton(text="⬅️ Назад", callback_data="admin:poll:settings")]]
        ),
        parse_mode="HTML",
    )
    await callback.answer()


@router.callback_query(F.data.startswith("admin:poll:cfg:rem:"))
async def poll_cfg_reminder_set(callback: CallbackQuery) -> None:
    if not await _require_admin_callback(callback):
        return
    try:
        minutes = int((callback.data or "").split(":")[-1])
    except ValueError:
        await callback.answer("Ошибка данных.", show_alert=True)
        return
    await PollConfigRepo.update_reminder(minutes if minutes > 0 else None, await _admin_user_id(callback.from_user.id))
    await callback.answer("✅ Напоминание обновлено", show_alert=True)
    await _show_poll_settings_screen(callback)


async def _show_office_requirements_screen(callback: CallbackQuery) -> None:
    reqs = await PollConfigRepo.get_office_requirements()
    hybrid = reqs.get("HYBRID", 8)
    hybrid_plus = reqs.get("HYBRID_PLUS", 4)
    rows = [
        [InlineKeyboardButton(text=f"✏️ HYBRID ({hybrid})", callback_data="admin:poll:cfg:office_req:hybrid")],
        [
            InlineKeyboardButton(
                text=f"✏️ HYBRID_PLUS ({hybrid_plus})",
                callback_data="admin:poll:cfg:office_req:hybrid_plus",
            )
        ],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="admin:poll:settings")],
    ]
    await _edit_callback_message(
        callback,
        "🏢 <b>Нормы офисных дней в месяц</b>\n\n"
        f"HYBRID: <b>{hybrid}</b> дней\n"
        f"HYBRID_PLUS: <b>{hybrid_plus}</b> дней\n\n"
        "Эти значения используются для расчёта месячной статистики посещаемости.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
        parse_mode="HTML",
    )


@router.callback_query(F.data == "admin:poll:cfg:office_req")
async def poll_cfg_office_req(callback: CallbackQuery) -> None:
    if not await _require_admin_callback(callback):
        return
    await _show_office_requirements_screen(callback)
    await callback.answer()


@router.callback_query(F.data == "admin:poll:cfg:office_req:hybrid")
async def poll_cfg_office_req_hybrid(callback: CallbackQuery, state: FSMContext) -> None:
    if not await _require_admin_callback(callback):
        return
    await state.set_state(AdminPanelStates.office_req_hybrid)
    await _edit_callback_message(
        callback,
        "✏️ <b>Норма офисных дней для HYBRID</b>\n\nВведите количество дней (от 0 до 31):",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="❌ Отмена", callback_data="admin:poll:cfg:office_req")]]
        ),
        parse_mode="HTML",
    )
    await callback.answer()


@router.callback_query(F.data == "admin:poll:cfg:office_req:hybrid_plus")
async def poll_cfg_office_req_hybrid_plus(callback: CallbackQuery, state: FSMContext) -> None:
    if not await _require_admin_callback(callback):
        return
    await state.set_state(AdminPanelStates.office_req_hybrid_plus)
    await _edit_callback_message(
        callback,
        "✏️ <b>Норма офисных дней для HYBRID_PLUS</b>\n\nВведите количество дней (от 0 до 31):",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="❌ Отмена", callback_data="admin:poll:cfg:office_req")]]
        ),
        parse_mode="HTML",
    )
    await callback.answer()


async def _process_office_requirement(message: Message, state: FSMContext, *, target: str) -> None:
    if not message.from_user or not message.text:
        return
    if not await _require_admin_message(message):
        await state.clear()
        return
    try:
        value = int(message.text.strip())
    except ValueError:
        await message.answer("❌ Введите целое число")
        return
    if value < 0 or value > 31:
        await message.answer("❌ Значение должно быть от 0 до 31")
        return
    reqs = await PollConfigRepo.get_office_requirements()
    hybrid = value if target == "HYBRID" else reqs.get("HYBRID", 8)
    hybrid_plus = value if target == "HYBRID_PLUS" else reqs.get("HYBRID_PLUS", 4)
    await PollConfigRepo.update_office_requirements(hybrid, hybrid_plus, await _admin_user_id(message.from_user.id))
    await state.clear()
    await message.answer(f"✅ Норма для {target} обновлена: {value} дней")


@router.message(AdminPanelStates.office_req_hybrid, F.text)
async def office_req_hybrid_input(message: Message, state: FSMContext) -> None:
    await _process_office_requirement(message, state, target="HYBRID")


@router.message(AdminPanelStates.office_req_hybrid_plus, F.text)
async def office_req_hybrid_plus_input(message: Message, state: FSMContext) -> None:
    await _process_office_requirement(message, state, target="HYBRID_PLUS")


async def _show_auto_send_screen(callback: CallbackQuery) -> None:
    config = await PollConfigRepo.get_config()
    enabled = config.get("auto_send_enabled", False)
    auto_time = config.get("auto_send_time")
    status_emoji = "🟢" if enabled else "🔴"
    status_text = "Включена" if enabled else "Выключена"
    time_text = f"{auto_time.strftime('%H:%M')} MSK" if auto_time else "не задано"
    toggle_text = "🔴 Выключить" if enabled else "🟢 Включить"
    rows = [
        [InlineKeyboardButton(text=toggle_text, callback_data="admin:poll:cfg:at:tog")],
        [InlineKeyboardButton(text="🕐 Задать время", callback_data="admin:poll:cfg:at:h")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="admin:poll:settings")],
    ]
    await _edit_callback_message(
        callback,
        "🕐 <b>Авто-отправка опроса</b>\n\n"
        f"Статус: {status_emoji} {status_text}\n"
        f"Время: {time_text}\n\n"
        "Опрос создаётся автоматически по будням (пн-пт), кроме пропусков.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
        parse_mode="HTML",
    )


@router.callback_query(F.data == "admin:poll:cfg:auto")
async def poll_cfg_auto(callback: CallbackQuery) -> None:
    if not await _require_admin_callback(callback):
        return
    await _show_auto_send_screen(callback)
    await callback.answer()


@router.callback_query(F.data == "admin:poll:cfg:at:tog")
async def poll_cfg_auto_toggle(callback: CallbackQuery) -> None:
    if not await _require_admin_callback(callback):
        return
    config = await PollConfigRepo.get_config()
    new_state = not config.get("auto_send_enabled", False)
    await PollConfigRepo.update_auto_send_enabled(new_state, await _admin_user_id(callback.from_user.id))
    await reschedule_poll_auto_send()
    await callback.answer(f"Авто-отправка {'включена' if new_state else 'выключена'}", show_alert=True)
    await _show_auto_send_screen(callback)


@router.callback_query(F.data.startswith("admin:poll:cfg:at:h"))
async def poll_cfg_auto_hour(callback: CallbackQuery) -> None:
    if not await _require_admin_callback(callback):
        return
    parts = (callback.data or "").split(":")
    if len(parts) == 5:
        rows: list[list[InlineKeyboardButton]] = []
        row: list[InlineKeyboardButton] = []
        for hour in range(7, 21):
            row.append(InlineKeyboardButton(text=f"{hour:02d}:00", callback_data=f"admin:poll:cfg:at:h:{hour:02d}"))
            if len(row) == 4:
                rows.append(row)
                row = []
        if row:
            rows.append(row)
        rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="admin:poll:cfg:auto")])
        await _edit_callback_message(
            callback,
            "🕐 <b>Выберите час отправки:</b>",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
            parse_mode="HTML",
        )
        await callback.answer()
        return
    if len(parts) == 6:
        hour = parts[-1]
        rows = []
        row = []
        for minute in range(0, 60, 5):
            row.append(
                InlineKeyboardButton(
                    text=f"{hour}:{minute:02d}",
                    callback_data=f"admin:poll:cfg:at:m:{hour}:{minute:02d}",
                )
            )
            if len(row) == 4:
                rows.append(row)
                row = []
        if row:
            rows.append(row)
        rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="admin:poll:cfg:at:h")])
        await _edit_callback_message(
            callback,
            f"🕐 <b>Час: {hour}. Выберите минуты:</b>",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
            parse_mode="HTML",
        )
        await callback.answer()


@router.callback_query(F.data.startswith("admin:poll:cfg:at:m:"))
async def poll_cfg_auto_time_set(callback: CallbackQuery) -> None:
    if not await _require_admin_callback(callback):
        return
    parts = (callback.data or "").split(":")
    try:
        hour = int(parts[-2])
        minute = int(parts[-1])
    except ValueError:
        await callback.answer("Ошибка данных.", show_alert=True)
        return
    await PollConfigRepo.update_auto_send_time(hour, minute, await _admin_user_id(callback.from_user.id))
    await reschedule_poll_auto_send()
    await callback.answer(f"✅ Время: {hour:02d}:{minute:02d}", show_alert=True)
    await _show_auto_send_screen(callback)


async def _show_poll_options_screen(callback: CallbackQuery) -> None:
    config = await PollConfigRepo.get_config()
    options = config.get("poll_options", [])
    rows = []
    for idx, option in enumerate(options):
        label = option.get("label", option.get("key", "?"))
        media_marker = "🖼" if isinstance(option.get("media"), dict) and option.get("media") else "▫️"
        rows.append(
            [
                InlineKeyboardButton(text=f"{idx + 1}. {media_marker} {label}", callback_data="nav:ignore"),
                InlineKeyboardButton(text="✏️", callback_data=f"admin:poll:cfg:opt:{idx}"),
                InlineKeyboardButton(text="🖼", callback_data=f"admin:poll:cfg:optmedia:{idx}"),
            ]
        )
    rows.append([InlineKeyboardButton(text="🔄 Сбросить к стандартным", callback_data="admin:poll:cfg:opt:reset")])
    rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="admin:poll:settings")])
    await _edit_callback_message(
        callback,
        "✏️ <b>Варианты ответов в опросе</b>\n\nНажмите ✏️ чтобы изменить текст:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
        parse_mode="HTML",
    )


@router.callback_query(F.data == "admin:poll:cfg:options")
async def poll_cfg_options(callback: CallbackQuery) -> None:
    if not await _require_admin_callback(callback):
        return
    await _show_poll_options_screen(callback)
    await callback.answer()


@router.callback_query(F.data == "admin:poll:cfg:opt:reset")
async def poll_cfg_options_reset(callback: CallbackQuery) -> None:
    if not await _require_admin_callback(callback):
        return
    default_options = [
        {"key": "EVOLUTION", "label": "МФК «Эволюция»"},
        {"key": "CSKA", "label": "ЦСКА"},
        {"key": "REMOTE", "label": "Удалёнка"},
    ]
    await PollConfigRepo.update_options(default_options, await _admin_user_id(callback.from_user.id))
    await callback.answer("✅ Варианты сброшены к стандартным", show_alert=True)
    await _show_poll_options_screen(callback)


@router.callback_query(F.data.startswith("admin:poll:cfg:opt:"))
async def poll_cfg_option_edit(callback: CallbackQuery, state: FSMContext) -> None:
    if not await _require_admin_callback(callback):
        return
    try:
        index = int((callback.data or "").split(":")[-1])
    except ValueError:
        await callback.answer("Ошибка данных.", show_alert=True)
        return
    config = await PollConfigRepo.get_config()
    options = config.get("poll_options", [])
    current_label = options[index].get("label", "?") if index < len(options) else "?"
    await state.update_data(poll_option_index=index)
    await state.set_state(AdminPanelStates.poll_option_label)
    await _edit_callback_message(
        callback,
        f"✏️ <b>Редактирование варианта #{index + 1}</b>\n\n"
        f"Текущий текст: <b>{current_label}</b>\n\nВведите новый текст:",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="❌ Отмена", callback_data="admin:poll:cfg:options")]]
        ),
        parse_mode="HTML",
    )
    await callback.answer()


@router.callback_query(F.data.startswith("admin:poll:cfg:optmedia:"))
async def poll_cfg_option_media_edit(callback: CallbackQuery, state: FSMContext) -> None:
    if not await _require_admin_callback(callback):
        return
    try:
        index = int((callback.data or "").split(":")[-1])
    except ValueError:
        await callback.answer("Ошибка данных.", show_alert=True)
        return
    config = await PollConfigRepo.get_config()
    options = config.get("poll_options", [])
    if index >= len(options):
        await callback.answer("Вариант не найден.", show_alert=True)
        return
    current = options[index].get("media") if isinstance(options[index], dict) else None
    current_text = "есть" if isinstance(current, dict) and current.get("media") else "нет"
    await state.update_data(poll_option_media_index=index)
    await state.set_state(AdminPanelStates.poll_option_media)
    await _edit_callback_message(
        callback,
        f"🖼 <b>Media варианта #{index + 1}</b>\n\n"
        f"Текущее media: <b>{current_text}</b>\n\n"
        "Отправьте картинку или Telegram <code>file_id</code> картинки.\n"
        "Чтобы очистить media, отправьте: <code>-</code>",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="❌ Отмена", callback_data="admin:poll:cfg:options")]]
        ),
        parse_mode="HTML",
    )
    await callback.answer()


@router.message(AdminPanelStates.poll_option_label, F.text)
async def poll_option_label_input(message: Message, state: FSMContext) -> None:
    if not message.from_user or not message.text:
        return
    if not await _require_admin_message(message):
        await state.clear()
        return
    new_label = message.text.strip()
    if not new_label or len(new_label) > 100:
        await message.answer("Введите текст от 1 до 100 символов.")
        return
    data = await state.get_data()
    index = int(data.get("poll_option_index", 0))
    await state.clear()
    config = await PollConfigRepo.get_config()
    options = config.get("poll_options", [])
    if index < len(options):
        options[index]["label"] = new_label
        await PollConfigRepo.update_options(options, await _admin_user_id(message.from_user.id))
        await message.answer(
            f"✅ Вариант #{index + 1} обновлён: <b>{new_label}</b>",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [InlineKeyboardButton(text="✏️ Варианты ответов", callback_data="admin:poll:cfg:options")],
                    [InlineKeyboardButton(text="⬅️ К настройкам", callback_data="admin:poll:settings")],
                ]
            ),
            parse_mode="HTML",
        )
    else:
        await message.answer(
            "❌ Вариант не найден.",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[[InlineKeyboardButton(text="⬅️ К настройкам", callback_data="admin:poll:settings")]]
            ),
        )


@router.message(AdminPanelStates.poll_option_media)
async def poll_option_media_input(message: Message, state: FSMContext) -> None:
    if not message.from_user:
        return
    if not await _require_admin_message(message):
        await state.clear()
        return

    if message.photo:
        file_id = message.photo[-1].file_id
    elif message.text:
        file_id = message.text.strip()
    else:
        await message.answer("Отправьте картинку, Telegram file_id или '-' для очистки.")
        return

    if file_id != "-" and (len(file_id) < 20 or len(file_id) > 512):
        await message.answer("Введите корректный Telegram file_id, отправьте картинку или '-' для очистки.")
        return
    data = await state.get_data()
    index = int(data.get("poll_option_media_index", 0))
    await state.clear()
    config = await PollConfigRepo.get_config()
    options = config.get("poll_options", [])
    if index >= len(options):
        await message.answer("❌ Вариант не найден.")
        return

    if file_id == "-":
        options[index].pop("media", None)
        action_text = "очищено"
    else:
        options[index]["media"] = {"type": "photo", "media": file_id}
        action_text = "обновлено"
    await PollConfigRepo.update_options(options, await _admin_user_id(message.from_user.id))
    await message.answer(
        f"✅ Media варианта #{index + 1} {action_text}.",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="✏️ Варианты ответов", callback_data="admin:poll:cfg:options")],
                [InlineKeyboardButton(text="⬅️ К настройкам", callback_data="admin:poll:settings")],
            ]
        ),
    )


async def _show_skip_dates_screen(callback: CallbackQuery) -> None:
    today = panel_core.get_local_date()
    skip_dates = await PollConfigRepo.get_upcoming_skip_dates(today, limit=20)
    if skip_dates:
        lines = ["📅 <b>Пропуск дат (праздники)</b>\n"]
        for item in skip_dates:
            skip_date = item["skip_date"]
            reason = item.get("reason") or ""
            creator = item.get("creator_name") or ""
            line = f"• <b>{skip_date.strftime('%d.%m.%Y')}</b>"
            if reason:
                line += f" — {reason}"
            if creator:
                line += f" ({creator})"
            lines.append(line)
        text = "\n".join(lines)
    else:
        text = "📅 <b>Пропуск дат (праздники)</b>\n\nНет запланированных пропусков."

    rows = []
    for item in skip_dates:
        skip_date = item["skip_date"]
        reason = item.get("reason") or ""
        label = f"🗑 {skip_date.strftime('%d.%m')} {reason[:20]}" if reason else f"🗑 {skip_date.strftime('%d.%m.%Y')}"
        rows.append([InlineKeyboardButton(text=label, callback_data=f"admin:poll:skip:del:{skip_date.isoformat()}")])
    rows.append([InlineKeyboardButton(text="➕ Добавить", callback_data="admin:poll:skip:add")])
    rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="admin:menu:polls")])
    await _edit_callback_message(
        callback,
        text,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
        parse_mode="HTML",
    )


@router.callback_query(F.data == "admin:poll:skip_dates")
async def poll_skip_dates(callback: CallbackQuery) -> None:
    if not await _require_admin_callback(callback):
        return
    await _show_skip_dates_screen(callback)
    await callback.answer()


def _build_skip_calendar_keyboard(year: int, month: int) -> InlineKeyboardMarkup:
    month_names = [
        "",
        "Январь",
        "Февраль",
        "Март",
        "Апрель",
        "Май",
        "Июнь",
        "Июль",
        "Август",
        "Сентябрь",
        "Октябрь",
        "Ноябрь",
        "Декабрь",
    ]
    rows = [
        [
            InlineKeyboardButton(text="◀️", callback_data=f"admin:skipcal:prev:{year}:{month}"),
            InlineKeyboardButton(text=f"{month_names[month]} {year}", callback_data="nav:ignore"),
            InlineKeyboardButton(text="▶️", callback_data=f"admin:skipcal:next:{year}:{month}"),
        ],
        [InlineKeyboardButton(text=day, callback_data="nav:ignore") for day in ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]],
    ]
    today = panel_core.get_local_date()
    calendar = cal_module.Calendar(firstweekday=0)
    for week in calendar.monthdayscalendar(year, month):
        row = []
        for day_num in week:
            if day_num == 0:
                row.append(InlineKeyboardButton(text=" ", callback_data="nav:ignore"))
                continue
            day_date = date(year, month, day_num)
            if day_date == today:
                text = f"•{day_num}•"
            elif day_date.weekday() >= 5:
                text = f"~{day_num}~"
            else:
                text = str(day_num)
            row.append(InlineKeyboardButton(text=text, callback_data=f"admin:skipcal:day:{day_date.isoformat()}"))
        rows.append(row)
    rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="admin:poll:skip_dates")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


@router.callback_query(F.data == "admin:poll:skip:add")
async def poll_skip_add(callback: CallbackQuery) -> None:
    if not await _require_admin_callback(callback):
        return
    today = panel_core.get_local_date()
    await _edit_callback_message(
        callback,
        "📅 <b>Добавить пропуск даты</b>\n\nВыберите дату:",
        reply_markup=_build_skip_calendar_keyboard(today.year, today.month),
        parse_mode="HTML",
    )
    await callback.answer()


@router.callback_query(F.data.startswith("admin:skipcal:prev:"))
@router.callback_query(F.data.startswith("admin:skipcal:next:"))
async def poll_skip_calendar_nav(callback: CallbackQuery) -> None:
    if not await _require_admin_callback(callback):
        return
    parts = (callback.data or "").split(":")
    year = int(parts[3])
    month = int(parts[4])
    if "prev" in (callback.data or ""):
        month -= 1
        if month < 1:
            month = 12
            year -= 1
    else:
        month += 1
        if month > 12:
            month = 1
            year += 1
    await _edit_callback_message(
        callback,
        "📅 <b>Добавить пропуск даты</b>\n\nВыберите дату:",
        reply_markup=_build_skip_calendar_keyboard(year, month),
        parse_mode="HTML",
    )
    await callback.answer()


@router.callback_query(F.data.startswith("admin:skipcal:day:"))
async def poll_skip_day(callback: CallbackQuery, state: FSMContext) -> None:
    if not await _require_admin_callback(callback):
        return
    date_str = (callback.data or "").split(":")[-1]
    skip_date = date.fromisoformat(date_str)
    await state.update_data(skip_date=date_str)
    await state.set_state(AdminPanelStates.poll_skip_reason)
    await _edit_callback_message(
        callback,
        f"📅 <b>Пропуск: {skip_date.strftime('%d.%m.%Y')}</b>\n\nВведите причину (или нажмите «Без причины»):",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="Без причины", callback_data=f"admin:poll:skip:noreason:{date_str}")],
                [InlineKeyboardButton(text="❌ Отмена", callback_data="admin:poll:skip_dates")],
            ]
        ),
        parse_mode="HTML",
    )
    await callback.answer()


@router.callback_query(F.data.startswith("admin:poll:skip:noreason:"))
async def poll_skip_no_reason(callback: CallbackQuery, state: FSMContext) -> None:
    if not await _require_admin_callback(callback):
        return
    await state.clear()
    skip_date = date.fromisoformat((callback.data or "").split(":")[-1])
    result = await PollConfigRepo.add_skip_date(skip_date, None, await _admin_user_id(callback.from_user.id))
    await callback.answer(
        f"✅ {skip_date.strftime('%d.%m.%Y')} добавлен" if result else "⚠️ Эта дата уже добавлена",
        show_alert=True,
    )
    await _show_skip_dates_screen(callback)


@router.message(AdminPanelStates.poll_skip_reason, F.text)
async def poll_skip_reason_input(message: Message, state: FSMContext) -> None:
    if not message.from_user or not message.text:
        return
    if not await _require_admin_message(message):
        await state.clear()
        return
    reason = message.text.strip()
    if len(reason) > 200:
        await message.answer("Причина не может быть длиннее 200 символов.")
        return
    data = await state.get_data()
    date_str = data.get("skip_date")
    await state.clear()
    if not date_str:
        await message.answer("❌ Дата не найдена. Попробуйте снова.")
        return
    skip_date = date.fromisoformat(str(date_str))
    result = await PollConfigRepo.add_skip_date(skip_date, reason, await _admin_user_id(message.from_user.id))
    if result:
        await message.answer(f"✅ Пропуск добавлен: <b>{skip_date.strftime('%d.%m.%Y')}</b> — {reason}", parse_mode="HTML")
    else:
        await message.answer("⚠️ Эта дата уже добавлена.")


@router.callback_query(F.data.startswith("admin:poll:skip:del:"))
async def poll_skip_delete(callback: CallbackQuery) -> None:
    if not await _require_admin_callback(callback):
        return
    skip_date = date.fromisoformat((callback.data or "").split(":")[-1])
    removed = await PollConfigRepo.remove_skip_date(skip_date)
    await callback.answer(
        f"✅ {skip_date.strftime('%d.%m.%Y')} удалён" if removed else "❌ Дата не найдена",
        show_alert=True,
    )
    await _show_skip_dates_screen(callback)


@router.callback_query(F.data == "admin:invites:all")
async def admin_invites_all(callback: CallbackQuery) -> None:
    if not await _require_admin_callback(callback):
        return
    invites = await RegistrationRepo.get_all_invites(limit=30)
    text = panel_core.format_invites_text(invites, "📋 <b>Все приглашения</b>", group_by_dept=True)
    await _edit_callback_message(
        callback,
        text,
        reply_markup=to_aiogram_markup(panel_core.invites_list_keyboard(back_target="admin", show_create=True)),
        parse_mode="HTML",
    )
    await callback.answer()


@router.callback_query(F.data == "admin:invite")
async def admin_invite_start(callback: CallbackQuery, state: FSMContext) -> None:
    if not await _require_admin_callback(callback):
        return
    await state.clear()
    await _edit_callback_message(
        callback,
        "Выберите отдел для приглашения:",
        reply_markup=to_aiogram_markup(panel_core.build_invite_department_keyboard()),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("admin:invite:dept:"))
async def admin_invite_dept(callback: CallbackQuery, state: FSMContext) -> None:
    if not await _require_admin_callback(callback):
        return
    try:
        department_id = int((callback.data or "").split(":")[3])
    except (ValueError, IndexError):
        await callback.answer("Ошибка данных.", show_alert=True)
        return
    await state.update_data(invite_department_id=department_id)
    await _edit_callback_message(
        callback,
        "Добавить ожидаемое ФИО?",
        reply_markup=to_aiogram_markup(panel_core.build_invite_name_keyboard()),
    )
    await callback.answer()


@router.callback_query(F.data == "admin:invite:enter_name")
async def admin_invite_enter_name(callback: CallbackQuery, state: FSMContext) -> None:
    if not await _require_admin_callback(callback):
        return
    data = await state.get_data()
    if not data.get("invite_department_id"):
        await callback.answer("Не выбран отдел.", show_alert=True)
        return
    await state.set_state(AdminPanelStates.invite_full_name)
    await _edit_callback_message(callback, "Введите ожидаемое ФИО:", reply_markup=_nav_markup())
    await callback.answer()


@router.callback_query(F.data == "admin:invite:skip")
async def admin_invite_skip(callback: CallbackQuery, state: FSMContext, bot: Bot) -> None:
    if not await _require_admin_callback(callback):
        return
    data = await state.get_data()
    department_id = data.get("invite_department_id")
    if not department_id:
        await callback.answer("Не выбран отдел.", show_alert=True)
        return
    await state.clear()
    await _create_invite(callback, bot, callback.from_user.id, int(department_id), expected_full_name=None)


@router.message(AdminPanelStates.invite_full_name, F.text)
async def admin_invite_full_name_input(message: Message, state: FSMContext, bot: Bot) -> None:
    if not message.from_user or not message.text:
        return
    if not await _require_admin_message(message):
        await state.clear()
        return
    full_name = message.text.strip()
    if not full_name:
        await message.answer("Введите ожидаемое ФИО или нажмите «Без ФИО».")
        return
    data = await state.get_data()
    department_id = data.get("invite_department_id")
    if not department_id:
        await state.clear()
        await message.answer("Не выбран отдел. Откройте админ-панель заново.")
        return
    await state.clear()
    text = await _build_invite_text(bot, message.from_user.id, int(department_id), full_name)
    sent = await message.answer(text, reply_markup=_nav_markup(), parse_mode="HTML")
    await track_bot_message(message.from_user.id, sent.chat.id, sent.message_id, screen="invite")


async def _create_invite(
    callback: CallbackQuery,
    bot: Bot,
    admin_telegram_id: int,
    department_id: int,
    *,
    expected_full_name: str | None,
) -> None:
    text = await _build_invite_text(bot, admin_telegram_id, department_id, expected_full_name)
    await _edit_callback_message(callback, text, reply_markup=_nav_markup(), parse_mode="HTML")
    await callback.answer()


async def _build_invite_text(
    bot: Bot,
    admin_telegram_id: int,
    department_id: int,
    expected_full_name: str | None,
) -> str:
    admin_user = await UsersRepo.get_by_telegram_id(admin_telegram_id)
    created_by = admin_user.get("id") if admin_user else None
    invite = None
    if created_by:
        invite = await RegistrationRepo.get_active_invite_by_creator(
            created_by=created_by,
            department_id=department_id,
            now=datetime.now(timezone.utc),
        )
    if not invite:
        token = await RegistrationService.create_invite(
            department_id=department_id,
            expected_full_name=expected_full_name,
            created_by=created_by,
        )
        invite = await RegistrationRepo.get_invite_by_token(token)
    bot_username = (await bot.get_me()).username
    return "🔗 Ссылка приглашение:\n\n" + panel_core.format_invite_message(invite or {}, "активна", bot_username)


@router.callback_query(F.data == "admin:mgr:assign")
@router.callback_query(F.data == "admin:mgr:remove")
async def manager_role_start(callback: CallbackQuery) -> None:
    if not await _require_admin_callback(callback):
        return
    action = "assign" if callback.data == "admin:mgr:assign" else "remove"
    title = "👔 <b>Назначить руководителя</b>" if action == "assign" else "🧹 <b>Снять руководителя</b>"
    text = f"{title}\n\nВыберите отдел" + (" для нового руководителя:" if action == "assign" else ":")
    await _edit_callback_message(
        callback,
        text,
        reply_markup=to_aiogram_markup(panel_core.build_manager_department_keyboard(action)),
        parse_mode="HTML",
    )
    await callback.answer()


@router.callback_query(F.data.startswith("admin:mgr:assign:dept:"))
@router.callback_query(F.data.startswith("admin:mgr:remove:dept:"))
async def manager_role_dept(callback: CallbackQuery, state: FSMContext) -> None:
    if not await _require_admin_callback(callback):
        return
    parts = (callback.data or "").split(":")
    action = parts[2]
    try:
        department_id = int(parts[-1])
    except ValueError:
        await callback.answer("Ошибка данных.", show_alert=True)
        return
    await state.update_data(manager_action=action, manager_department_id=department_id)
    await state.set_state(AdminPanelStates.manager_telegram_id)
    dept_name = DEPT_NAMES.get(department_id, "—")
    title = "👔 <b>Назначить руководителя" if action == "assign" else "🧹 <b>Снять руководителя"
    await _edit_callback_message(
        callback,
        f"{title} ({dept_name})</b>\n\nВведите Telegram ID пользователя:",
        reply_markup=_nav_markup(),
        parse_mode="HTML",
    )
    await callback.answer()


@router.message(AdminPanelStates.manager_telegram_id, F.text)
async def manager_role_input(message: Message, state: FSMContext) -> None:
    if not message.from_user or not message.text:
        return
    admin_telegram_id = message.from_user.id
    if not await _require_admin_message(message):
        await state.clear()
        return
    try:
        target_telegram_id = int(message.text.strip())
    except ValueError:
        await message.answer("❌ Некорректный Telegram ID. Введите число.", reply_markup=_nav_markup())
        return
    data = await state.get_data()
    action = data.get("manager_action")
    department_id = data.get("manager_department_id")
    await state.clear()
    if not action or not department_id:
        await message.answer("Ошибка. Откройте админ-панель заново.")
        return

    target_user = await UsersRepo.get_by_telegram_id(target_telegram_id)
    if not target_user:
        await message.answer(
            f"❌ Пользователь с Telegram ID {target_telegram_id} не найден в системе.",
            reply_markup=_nav_markup(),
        )
        return
    user_name = target_user.get("full_name") or f"ID {target_telegram_id}"
    dept_name = DEPT_NAMES.get(int(department_id), "—")
    if action == "assign":
        success = await UsersRepo.set_role_by_telegram_id(target_telegram_id, role="manager", department_id=int(department_id))
        if success:
            text = (
                "✅ <b>Руководитель назначен</b>\n\n"
                f"Пользователь: {user_name}\n"
                f"Telegram ID: {target_telegram_id}\n"
                f"Отдел: {dept_name}"
            )
            await panel_core.record_admin_action(
                action="admin:mgr:assign",
                admin_id=admin_telegram_id,
                parameters={"target_telegram_id": target_telegram_id, "department_id": department_id, "role": "manager"},
            )
        else:
            text = "❌ Не удалось назначить руководителя."
    else:
        current_role = target_user.get("role")
        if current_role != "manager":
            await message.answer(
                f"❌ Пользователь {user_name} не является руководителем (роль: {current_role}).",
                reply_markup=_nav_markup(),
            )
            return
        success = await UsersRepo.set_role_by_telegram_id(target_telegram_id, role="employee", department_id=None)
        if success:
            text = (
                "✅ <b>Руководитель снят</b>\n\n"
                f"Пользователь: {user_name}\n"
                f"Telegram ID: {target_telegram_id}\n"
                "Новая роль: employee"
            )
            await panel_core.record_admin_action(
                action="admin:mgr:remove",
                admin_id=admin_telegram_id,
                parameters={"target_telegram_id": target_telegram_id, "previous_role": "manager", "new_role": "employee"},
            )
        else:
            text = "❌ Не удалось снять руководителя."
    await message.answer(text, reply_markup=_nav_markup(), parse_mode="HTML")


@router.callback_query(F.data.startswith("admin:bday:bulk"))
async def birthday_bulk(callback: CallbackQuery) -> None:
    if not await _require_admin_callback(callback):
        return
    parts = (callback.data or "").split(":")
    page = 0
    if len(parts) >= 4:
        try:
            page = int(parts[3])
        except ValueError:
            page = 0
    users = []
    for dept_id in [1, 2]:
        users.extend(await UsersRepo.get_active_by_department(dept_id))
    per_page = 15
    total_pages = max(1, (len(users) + per_page - 1) // per_page)
    page = max(0, min(page, total_pages - 1))
    page_users = users[page * per_page : (page + 1) * per_page]
    rows = [
        [InlineKeyboardButton(text=user.get("full_name") or "(без ФИО)", callback_data=f"admin:bday:{user.get('id')}")]
        for user in page_users
    ]
    nav_row = []
    if page > 0:
        nav_row.append(InlineKeyboardButton(text="◀", callback_data=f"admin:bday:bulk:{page - 1}"))
    if page < total_pages - 1:
        nav_row.append(InlineKeyboardButton(text="▶", callback_data=f"admin:bday:bulk:{page + 1}"))
    if nav_row:
        rows.append(nav_row)
    rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="admin:menu:users")])
    await _edit_callback_message(
        callback,
        f"🎂 <b>Дни рождения</b> ({len(users)})\n\nВыберите сотрудника:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
        parse_mode="HTML",
    )
    await callback.answer()


@router.callback_query(F.data.startswith("admin:bday:clear:"))
async def birthday_clear(callback: CallbackQuery) -> None:
    if not await _require_admin_callback(callback):
        return
    try:
        user_id = int((callback.data or "").split(":")[3])
    except (ValueError, IndexError):
        await callback.answer("Ошибка данных.", show_alert=True)
        return
    await UsersRepo.update_birthday(user_id, None)
    await callback.answer("✅ День рождения очищен", show_alert=True)
    await _show_birthday_card(callback, user_id)


@router.callback_query(F.data.startswith("admin:bday:input:"))
async def birthday_input_start(callback: CallbackQuery, state: FSMContext) -> None:
    if not await _require_admin_callback(callback):
        return
    try:
        user_id = int((callback.data or "").split(":")[3])
    except (ValueError, IndexError):
        await callback.answer("Ошибка данных.", show_alert=True)
        return
    await state.update_data(birthday_target_user_id=user_id)
    await state.set_state(AdminPanelStates.birthday_input)
    await _edit_callback_message(
        callback,
        "📝 Введите дату рождения в формате <b>ДД.ММ</b> или <b>ДД.ММ.ГГГГ</b>:",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="⬅️ Отмена", callback_data=f"admin:bday:{user_id}")]]
        ),
        parse_mode="HTML",
    )
    await callback.answer()


@router.callback_query(F.data.regexp(r"^admin:bday:\d+$"))
async def birthday_card(callback: CallbackQuery) -> None:
    if not await _require_admin_callback(callback):
        return
    try:
        user_id = int((callback.data or "").split(":")[2])
    except (ValueError, IndexError):
        await callback.answer("Ошибка данных.", show_alert=True)
        return
    await _show_birthday_card(callback, user_id)
    await callback.answer()


async def _show_birthday_card(callback: CallbackQuery, user_id: int) -> None:
    target_user = await UsersRepo.get_by_id(user_id)
    if not target_user:
        await callback.answer("Пользователь не найден", show_alert=True)
        return
    birthday = target_user.get("birthday")
    current_text = (
        birthday.strftime("%d.%m.%Y") if birthday and birthday.year != 2000 else birthday.strftime("%d.%m") if birthday else "не указан"
    )
    full_name = target_user.get("full_name") or ""
    rows = [[InlineKeyboardButton(text="📝 Ввести дату", callback_data=f"admin:bday:input:{user_id}")]]
    if birthday:
        rows.append([InlineKeyboardButton(text="❌ Очистить", callback_data=f"admin:bday:clear:{user_id}")])
    rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data=f"admin:user:{user_id}")])
    await _edit_callback_message(
        callback,
        "🎂 <b>День рождения</b>\n\n"
        f"👤 {full_name}\n"
        f"Текущее значение: {current_text}\n\n"
        "Введите дату в формате ДД.ММ или ДД.ММ.ГГГГ",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
        parse_mode="HTML",
    )


@router.message(AdminPanelStates.birthday_input, F.text)
async def birthday_input(message: Message, state: FSMContext) -> None:
    if not message.from_user or not message.text:
        return
    if not await _require_admin_message(message):
        await state.clear()
        return
    data = await state.get_data()
    user_id = data.get("birthday_target_user_id")
    if not user_id:
        await state.clear()
        await message.answer("Ошибка: не найден пользователь. Откройте панель заново.")
        return
    text = message.text.strip()
    try:
        parts = text.split(".")
        if len(parts) == 2:
            birthday_value = date(2000, int(parts[1]), int(parts[0]))
        elif len(parts) == 3:
            birthday_value = date(int(parts[2]), int(parts[1]), int(parts[0]))
        else:
            await message.answer("❌ Неверный формат. Используйте ДД.ММ или ДД.ММ.ГГГГ")
            return
    except (ValueError, OverflowError):
        await message.answer("❌ Неверная дата. Используйте ДД.ММ или ДД.ММ.ГГГГ")
        return
    await UsersRepo.update_birthday(int(user_id), birthday_value)
    await state.clear()
    target_user = await UsersRepo.get_by_id(int(user_id))
    name = target_user.get("full_name", "") if target_user else ""
    display = birthday_value.strftime("%d.%m.%Y") if birthday_value.year != 2000 else birthday_value.strftime("%d.%m")
    await message.answer(f"✅ День рождения {name} установлен: {display}")
