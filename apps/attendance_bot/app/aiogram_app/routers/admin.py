"""Native aiogram admin and manager router."""

from __future__ import annotations

import calendar
from datetime import date, datetime, timezone

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from apps.attendance_bot.app.aiogram_app.keyboards import to_aiogram_markup
from apps.attendance_bot.app.scheduler import (
    get_heartbeat_status,
    get_scheduler_status,
    get_telegram_health_status,
)
from apps.attendance_bot.app.ui import admin_panel_views as panel_core
from apps.attendance_bot.app.ui import admin_views as admin_core
from apps.attendance_bot.app.ui.keyboards_neutral import (
    ALPHA_RANGES,
    DEPT_NAMES,
    MONTH_NAMES_RU,
    back_to_pending_keyboard,
    confirm_reject_keyboard,
    format_invites_text,
    invites_list_keyboard,
    manager_panel_navigation_keyboard,
    navigation_keyboard,
    pending_request_keyboard,
    user_picker_keyboard,
    work_mode_keyboard,
)
from apps.attendance_bot.app.utils.message_tracker import cleanup_user_messages, track_bot_message
from apps.attendance_bot.app.utils.permissions import get_permissions
from apps.attendance_bot.app.utils.private_chat_guard import MSG_PRIVATE_ONLY
from apps.attendance_bot.app.utils.revoked_access import send_revoked_notice_if_needed
from core.screw_core.models.enums import UserStatus, WorkMode
from core.screw_core.repos.registration_repo import RegistrationRepo
from core.screw_core.repos.users_repo import UsersRepo
from core.screw_core.services.health_status import (
    SchedulerHealth,
    SchedulerJobHealth,
    build_health_snapshot,
    format_health_status,
    is_health_ok,
)
from core.screw_core.services.permissions import OZKO_DEPARTMENT_ID
from core.screw_core.services.registration import RegistrationError, RegistrationService
from core.screw_core.utils.logging import get_logger

logger = get_logger(__name__)
router = Router(name="attendance_admin")


class AdminStates(StatesGroup):
    reject_reason = State()
    access_search = State()


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


async def _require_manager_message(message: Message) -> dict | None:
    if not message.from_user:
        return None
    is_mgr, user = await admin_core.is_manager(message.from_user.id)
    if not is_mgr or not user:
        await message.answer("❌ Эта команда доступна только менеджерам.")
        return None
    return user


async def _require_manager_callback(callback: CallbackQuery) -> dict | None:
    if not callback.from_user:
        return None
    is_mgr, user = await admin_core.is_manager(callback.from_user.id)
    if not is_mgr or not user:
        await callback.answer("❌ Нет прав доступа.", show_alert=True)
        return None
    return user


def _manager_can_use_department(manager: dict, department_id: int) -> bool:
    """Admin can use any department; manager is limited to their own department."""
    if manager.get("role") == "admin":
        return True
    return bool(manager.get("department_id") == department_id)


def _manager_can_manage_invite(manager: dict, invite: dict) -> bool:
    """Admin can manage any invite; manager can manage only own invite in own department."""
    if manager.get("role") == "admin":
        return True
    return bool(
        invite.get("created_by") == manager.get("id")
        and invite.get("department_id") == manager.get("department_id")
    )


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
        return await callback.message.edit_text(
            text,
            reply_markup=reply_markup,
            parse_mode=parse_mode,
        )
    except TelegramBadRequest as exc:
        if "message is not modified" in str(exc).lower():
            return callback.message
        raise


async def _replace_callback_message(
    callback: CallbackQuery,
    text: str,
    *,
    reply_markup=None,
    parse_mode: str | None = "HTML",
    screen: str | None = None,
) -> None:
    edited = await _edit_callback_message(
        callback,
        text,
        reply_markup=reply_markup,
        parse_mode=parse_mode,
    )
    if edited and callback.from_user:
        await track_bot_message(
            telegram_id=callback.from_user.id,
            chat_id=edited.chat.id,
            message_id=edited.message_id,
            screen=screen,
        )


async def _send_pending_requests(
    target: Message,
    user: dict | None,
    back_target: str,
    *,
    use_edit: bool = False,
) -> None:
    is_admin = user.get("role") == "admin" if user else False
    department_id = None if is_admin else (user.get("department_id") if user else None)
    requests = await RegistrationService.get_pending_requests(department_id)

    if not requests:
        text = "📭 Нет заявок на рассмотрении."
        markup = to_aiogram_markup(navigation_keyboard(back_target))
        if use_edit:
            await target.edit_text(text, reply_markup=markup)
        else:
            await target.answer(text, reply_markup=markup)
        return

    count = len(requests)
    lines = [f"📋 <b>Заявки на рассмотрении</b> ({count})\n"]
    for index, request in enumerate(requests, 1):
        lines.append(f"<b>#{index}.</b> Заявка #{request['id']}")
        lines.append(admin_core.format_request_info(request))
        if index < count:
            lines.append("─" * 20)

    text = "\n".join(lines).strip()
    if len(text) > 4000:
        text = text[:4000] + "\n\n... (и ещё заявки)"

    rows: list[list[InlineKeyboardButton]] = []
    if len(requests) > 1:
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"✅ Одобрить все ({len(requests)})",
                    callback_data="reg_batch_approve",
                )
            ]
        )
    for request in requests[:10]:
        request_id = request["id"]
        if len(requests) == 1:
            rows.append([InlineKeyboardButton(text=f"🔎 Открыть заявку #{request_id}", callback_data=f"reg_view:{request_id}")])
        else:
            rows.append(
                [
                    InlineKeyboardButton(text=f"🔎 #{request_id}", callback_data=f"reg_view:{request_id}"),
                    InlineKeyboardButton(text="✅", callback_data=f"reg_approve:{request_id}"),
                    InlineKeyboardButton(text="❌", callback_data=f"reg_reject:{request_id}"),
                ]
            )
    rows.append(
        [
            InlineKeyboardButton(text="⬅️ Назад", callback_data=f"nav:back:{back_target}"),
            InlineKeyboardButton(text="🏠 Главное меню", callback_data="nav:home"),
        ]
    )
    markup = InlineKeyboardMarkup(inline_keyboard=rows)

    if use_edit:
        await target.edit_text(text, reply_markup=markup, parse_mode="HTML")
    else:
        await target.answer(text, reply_markup=markup, parse_mode="HTML")


@router.message(Command("pending"))
async def cmd_pending(message: Message) -> None:
    if not message.from_user:
        return
    if await _reject_if_group_message(message):
        return

    user = await _require_manager_message(message)
    if not user:
        return

    back_target = "admin" if user.get("role") == "admin" else "manager"
    await _send_pending_requests(message, user, back_target=back_target)


@router.message(Command("manager"))
async def cmd_manager(message: Message) -> None:
    if not message.from_user:
        return
    if await _reject_if_group_message(message):
        return

    user = await _require_manager_message(message)
    if not user:
        return

    pending_count = await admin_core._get_pending_count(message.from_user.id)
    sent = await message.answer(
        "👔 <b>Панель руководителя</b>",
        reply_markup=to_aiogram_markup(manager_panel_navigation_keyboard("home", pending_count=pending_count)),
        parse_mode="HTML",
    )
    await track_bot_message(message.from_user.id, sent.chat.id, sent.message_id, screen="manager_panel")


@router.message(Command("health"))
async def cmd_health(message: Message) -> None:
    if not message.from_user:
        return
    if await _reject_if_group_message(message):
        return

    user = await _require_manager_message(message)
    if not user:
        return

    scheduler_status_raw = get_scheduler_status()
    scheduler_status = SchedulerHealth(
        enabled=bool(scheduler_status_raw.get("enabled")),
        job_ids=list(scheduler_status_raw.get("job_ids") or []),
        running=bool(scheduler_status_raw.get("running")),
        jobs=[
            SchedulerJobHealth(
                job_id=str(item.get("id") or "unknown"),
                next_run_at=item.get("next_run_at"),
            )
            for item in scheduler_status_raw.get("jobs") or []
            if isinstance(item, dict)
        ],
    )
    snapshot = await build_health_snapshot(
        scheduler_status,
        heartbeat_status=get_heartbeat_status(),
        telegram_status=get_telegram_health_status(),
    )
    await message.answer(format_health_status(snapshot), parse_mode="HTML")

    result = "ok" if is_health_ok(snapshot) else "error"
    logger.info("AIROGRAM_HEALTH_CHECK user_id=%s result=%s", message.from_user.id, result)


@router.callback_query(F.data == "panel:manager")
@router.callback_query(F.data == "nav:back:manager")
async def callback_manager_panel(callback: CallbackQuery) -> None:
    if await _reject_if_group_callback(callback):
        return
    if not await _require_manager_callback(callback):
        return

    pending_count = await admin_core._get_pending_count(callback.from_user.id)
    await _replace_callback_message(
        callback,
        "👔 <b>Панель руководителя</b>",
        reply_markup=to_aiogram_markup(manager_panel_navigation_keyboard("home", pending_count=pending_count)),
        screen=callback.data,
    )
    await callback.answer()


@router.callback_query(F.data == "mgr:pending")
async def callback_mgr_pending(callback: CallbackQuery) -> None:
    if await _reject_if_group_callback(callback):
        return
    user = await _require_manager_callback(callback)
    if not user or not isinstance(callback.message, Message):
        return

    await _send_pending_requests(callback.message, user, back_target="manager", use_edit=True)
    await callback.answer()


@router.callback_query(F.data.startswith("reg_view:"))
async def callback_view_request(callback: CallbackQuery) -> None:
    if await _reject_if_group_callback(callback):
        return
    manager = await _require_manager_callback(callback)
    if not manager:
        return

    try:
        request_id = int((callback.data or "").split(":")[1])
    except (ValueError, IndexError):
        await callback.answer("Ошибка данных.", show_alert=True)
        return

    request = await RegistrationRepo.get_request_by_id(request_id)
    if not request or request.get("status") != "pending":
        await _edit_callback_message(
            callback,
            "⚠️ Заявка уже обработана или не найдена.",
            reply_markup=to_aiogram_markup(back_to_pending_keyboard()),
        )
        await callback.answer()
        return

    department_id = request.get("department_id")
    if department_id and not _manager_can_use_department(manager, int(department_id)):
        await callback.answer("❌ Недостаточно прав для этого отдела.", show_alert=True)
        return

    await _edit_callback_message(
        callback,
        f"📄 <b>Заявка #{request_id}</b>\n\n{admin_core.format_request_info(request)}",
        reply_markup=to_aiogram_markup(pending_request_keyboard(request_id)),
        parse_mode="HTML",
    )
    await callback.answer()


@router.callback_query(F.data == "mgr:users")
async def callback_mgr_users(callback: CallbackQuery) -> None:
    if await _reject_if_group_callback(callback):
        return
    if not callback.from_user:
        return

    perms = await get_permissions(callback.from_user.id)
    if not perms.is_manager:
        await callback.answer("❌ Нет прав доступа.", show_alert=True)
        return
    if not perms.department_id:
        await _edit_callback_message(
            callback,
            "❌ Не указан отдел.",
            reply_markup=to_aiogram_markup(navigation_keyboard("manager")),
            parse_mode="HTML",
        )
        await callback.answer()
        return

    users = await UsersRepo.get_active_by_department(perms.department_id)
    dept_name = DEPT_NAMES.get(perms.department_id, "—")
    rows = [
        [
            InlineKeyboardButton(
                text=user.get("full_name") or "(без ФИО)",
                callback_data=f"admin:user:{user.get('id')}",
            )
        ]
        for user in users
    ]
    rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="nav:back:manager")])

    await _edit_callback_message(
        callback,
        f"👥 <b>{dept_name}</b> ({len(users)})\n\nВыберите сотрудника:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
        parse_mode="HTML",
    )
    await callback.answer()


@router.callback_query(F.data == "mgr:invite")
async def callback_mgr_invite(callback: CallbackQuery, bot: Bot) -> None:
    if await _reject_if_group_callback(callback):
        return
    user = await _require_manager_callback(callback)
    if not user:
        return

    department_id = user.get("department_id")
    if not department_id:
        await callback.answer("❌ Не указан отдел.", show_alert=True)
        return

    invite = await RegistrationRepo.get_active_invite_by_creator(
        created_by=user.get("id"),
        department_id=department_id,
        now=datetime.now(timezone.utc),
    )
    if not invite:
        token = await RegistrationService.create_invite(
            department_id=department_id,
            expected_full_name=None,
            created_by=user.get("id"),
        )
        invite = await RegistrationRepo.get_invite_by_token(token)

    bot_username = (await bot.get_me()).username
    text = panel_core.format_invite_screen(invite or {}, bot_username)
    _, is_active = panel_core.get_invite_status(invite or {})
    invite_id = invite.get("id") if invite else 0

    await _edit_callback_message(
        callback,
        text,
        reply_markup=to_aiogram_markup(panel_core.build_invite_keyboard(invite_id, is_active, "manager")),
        parse_mode="HTML",
    )
    await callback.answer()


@router.callback_query(F.data == "mgr:invites_list")
async def callback_mgr_invites_list(callback: CallbackQuery) -> None:
    if await _reject_if_group_callback(callback):
        return
    user = await _require_manager_callback(callback)
    if not user:
        return

    department_id = user.get("department_id")
    if user.get("role") == "admin":
        invites = await RegistrationRepo.get_all_invites(limit=30)
        title = "📋 <b>Все приглашения</b>"
        group_by_dept = True
    elif department_id:
        invites = await RegistrationRepo.get_invites_by_department(department_id, limit=20)
        dept_name = DEPT_NAMES.get(department_id, f"Отдел {department_id}")
        title = f"📋 <b>Приглашения — {dept_name}</b>"
        group_by_dept = False
    else:
        await callback.answer("❌ Не указан отдел.", show_alert=True)
        return

    await _edit_callback_message(
        callback,
        format_invites_text(invites, title, group_by_dept=group_by_dept),
        reply_markup=to_aiogram_markup(invites_list_keyboard("manager", show_create=True)),
        parse_mode="HTML",
    )
    await callback.answer()


@router.callback_query(F.data.startswith("invite:refresh:"))
async def callback_invite_refresh(callback: CallbackQuery, bot: Bot) -> None:
    if await _reject_if_group_callback(callback):
        return
    manager = await _require_manager_callback(callback)
    if not manager:
        return

    try:
        invite_id = int((callback.data or "").split(":")[-1])
    except ValueError:
        await callback.answer("Ошибка данных.", show_alert=True)
        return

    invite = await RegistrationRepo.get_invite_by_id(invite_id)
    if not invite:
        await callback.answer("Приглашение не найдено.", show_alert=True)
        return
    if not _manager_can_manage_invite(manager, invite):
        await callback.answer("❌ Недостаточно прав для этого приглашения.", show_alert=True)
        return

    bot_username = (await bot.get_me()).username
    text = panel_core.format_invite_screen(invite, bot_username)
    _, is_active = panel_core.get_invite_status(invite)
    await callback.answer(panel_core.format_time_remaining(invite.get("expires_at")), show_alert=False)
    await _edit_callback_message(
        callback,
        text,
        reply_markup=to_aiogram_markup(panel_core.build_invite_keyboard(invite_id, is_active, "manager")),
        parse_mode="HTML",
    )


@router.callback_query(F.data.startswith("invite:delete:confirm:"))
async def callback_invite_delete_confirm(callback: CallbackQuery) -> None:
    if await _reject_if_group_callback(callback):
        return
    user = await _require_manager_callback(callback)
    if not user:
        return

    try:
        invite_id = int((callback.data or "").split(":")[-1])
    except ValueError:
        await callback.answer("Ошибка данных.", show_alert=True)
        return

    invite = await RegistrationRepo.get_invite_by_id(invite_id)
    if not invite:
        await callback.answer("Приглашение не найдено.", show_alert=True)
        return
    if not _manager_can_manage_invite(user, invite):
        await callback.answer("❌ Недостаточно прав для этого приглашения.", show_alert=True)
        return

    deleted = await RegistrationRepo.expire_invite(invite_id)
    await callback.answer("Приглашение удалено." if deleted else "Не удалось удалить.", show_alert=not deleted)
    if deleted:
        logger.info("aiogram invite: deleted invite_id=%s by user_id=%s", invite_id, user.get("id"))

    pending_count = await admin_core._get_pending_count(callback.from_user.id)
    await _replace_callback_message(
        callback,
        "👔 <b>Панель руководителя</b>",
        reply_markup=to_aiogram_markup(manager_panel_navigation_keyboard("home", pending_count=pending_count)),
        screen="invite:deleted",
    )


@router.callback_query(F.data.startswith("invite:delete:cancel:"))
async def callback_invite_delete_cancel(callback: CallbackQuery, bot: Bot) -> None:
    if await _reject_if_group_callback(callback):
        return
    manager = await _require_manager_callback(callback)
    if not manager:
        return

    try:
        invite_id = int((callback.data or "").split(":")[-1])
    except ValueError:
        await callback.answer("Ошибка данных.", show_alert=True)
        return

    invite = await RegistrationRepo.get_invite_by_id(invite_id)
    if not invite:
        await callback.answer("Приглашение не найдено.", show_alert=True)
        return
    if not _manager_can_manage_invite(manager, invite):
        await callback.answer("❌ Недостаточно прав для этого приглашения.", show_alert=True)
        return

    bot_username = (await bot.get_me()).username
    text = panel_core.format_invite_screen(invite, bot_username)
    _, is_active = panel_core.get_invite_status(invite)
    await _edit_callback_message(
        callback,
        text,
        reply_markup=to_aiogram_markup(panel_core.build_invite_keyboard(invite_id, is_active, "manager")),
        parse_mode="HTML",
    )
    await callback.answer()


@router.callback_query(F.data.startswith("invite:delete:"))
async def callback_invite_delete(callback: CallbackQuery) -> None:
    if await _reject_if_group_callback(callback):
        return
    manager = await _require_manager_callback(callback)
    if not manager:
        return

    try:
        invite_id = int((callback.data or "").split(":")[-1])
    except ValueError:
        await callback.answer("Ошибка данных.", show_alert=True)
        return

    invite = await RegistrationRepo.get_invite_by_id(invite_id)
    if not invite:
        await callback.answer("Приглашение не найдено.", show_alert=True)
        return
    if not _manager_can_manage_invite(manager, invite):
        await callback.answer("❌ Недостаточно прав для этого приглашения.", show_alert=True)
        return

    await _edit_callback_message(
        callback,
        "❗ <b>Удалить приглашение?</b>\n\n"
        "Действие необратимо.\n"
        "Ссылка станет недействительной.",
        reply_markup=to_aiogram_markup(panel_core.build_invite_delete_confirm_keyboard(invite_id)),
        parse_mode="HTML",
    )
    await callback.answer()


def _format_user_label(user: dict | None, telegram_id: int) -> str:
    full_name = user.get("full_name") if user else None
    username = user.get("username") if user else None
    label = full_name or "Без ФИО"
    if username:
        label += f" (@{username})"
    return f"{label}, id={telegram_id}"


def _build_access_confirm_keyboard(action: str, telegram_id: int, back_target: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✅ Подтвердить",
                    callback_data=f"access:confirm:{action}:{telegram_id}:{back_target}",
                ),
                InlineKeyboardButton(
                    text="❌ Отмена",
                    callback_data=f"access:cancel:{back_target}",
                ),
            ]
        ]
    )


@router.callback_query(F.data.startswith("access:revoke"))
async def access_revoke(callback: CallbackQuery) -> None:
    if await _reject_if_group_callback(callback):
        return
    mgr_user = await _require_manager_callback(callback)
    if not mgr_user:
        return

    parts = (callback.data or "").split(":")
    back_target = parts[2] if len(parts) > 2 else "manager"
    if len(parts) >= 4 and parts[2] == "dept":
        try:
            dept_id = int(parts[3])
        except ValueError:
            await callback.answer("Ошибка данных.", show_alert=True)
            return
        if not _manager_can_use_department(mgr_user, dept_id):
            await callback.answer("❌ Недостаточно прав для этого отдела.", show_alert=True)
            return
        await _show_access_dept_users(callback, "revoke", dept_id, parts[4] if len(parts) > 4 else "admin")
        return

    target_dept = mgr_user.get("department_id") if mgr_user.get("role") != "admin" else None
    if target_dept:
        await _show_access_dept_users(callback, "revoke", target_dept, back_target)
        return

    await _show_access_dept_selector(callback, "revoke", back_target)


@router.callback_query(F.data.startswith("access:restore"))
async def access_restore(callback: CallbackQuery) -> None:
    if await _reject_if_group_callback(callback):
        return
    mgr_user = await _require_manager_callback(callback)
    if not mgr_user:
        return

    parts = (callback.data or "").split(":")
    back_target = parts[2] if len(parts) > 2 else "manager"
    if len(parts) >= 4 and parts[2] == "dept":
        try:
            dept_id = int(parts[3])
        except ValueError:
            await callback.answer("Ошибка данных.", show_alert=True)
            return
        if not _manager_can_use_department(mgr_user, dept_id):
            await callback.answer("❌ Недостаточно прав для этого отдела.", show_alert=True)
            return
        await _show_access_dept_users(callback, "restore", dept_id, parts[4] if len(parts) > 4 else "admin")
        return

    target_dept = mgr_user.get("department_id") if mgr_user.get("role") != "admin" else None
    if target_dept:
        await _show_access_dept_users(callback, "restore", target_dept, back_target)
        return

    await _show_access_dept_selector(callback, "restore", back_target)


async def _show_access_dept_selector(callback: CallbackQuery, action: str, back_target: str) -> None:
    title = "🚫 <b>Отозвать доступ</b>" if action == "revoke" else "✅ <b>Восстановить доступ</b>"
    if action == "restore":
        ozko_users = await UsersRepo.get_terminated_by_department(1)
        osb_users = await UsersRepo.get_terminated_by_department(2)
    else:
        ozko_users = await UsersRepo.get_active_by_department(1)
        osb_users = await UsersRepo.get_active_by_department(2)

    rows = [
        [InlineKeyboardButton(text=f"ОЗКО ({len(ozko_users)})", callback_data=f"access:{action}:dept:1:{back_target}")],
        [InlineKeyboardButton(text=f"ОСБ ({len(osb_users)})", callback_data=f"access:{action}:dept:2:{back_target}")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data=f"nav:back:{back_target}")],
    ]
    await _edit_callback_message(
        callback,
        f"{title}\n\nВыберите отдел:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
        parse_mode="HTML",
    )
    await callback.answer()


async def _show_access_dept_users(
    callback: CallbackQuery,
    action: str,
    dept_id: int,
    back_target: str,
    page: int = 0,
) -> None:
    manager = await _require_manager_callback(callback)
    if not manager:
        return
    if not _manager_can_use_department(manager, dept_id):
        await callback.answer("❌ Недостаточно прав для этого отдела.", show_alert=True)
        return

    if action == "restore":
        users = await UsersRepo.get_terminated_by_department(dept_id)
    else:
        users = await UsersRepo.get_active_by_department(dept_id)
        users = [item for item in users if item.get("telegram_id") != callback.from_user.id]

    dept_name = DEPT_NAMES.get(dept_id, f"Отдел {dept_id}")
    title = "🚫 <b>Отозвать доступ</b>" if action == "revoke" else "✅ <b>Восстановить доступ</b>"

    per_page = 15
    total = len(users)
    total_pages = max(1, (total + per_page - 1) // per_page)
    page = max(0, min(page, total_pages - 1))
    page_users = users[page * per_page : (page + 1) * per_page]

    if not users:
        empty_msg = "Нет уволенных сотрудников." if action == "restore" else "Нет сотрудников."
        await _edit_callback_message(
            callback,
            f"{title}\n\n{empty_msg}",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [InlineKeyboardButton(text="⬅️ Назад", callback_data=f"access:{action}:{back_target}")]
                ]
            ),
            parse_mode="HTML",
        )
        await callback.answer()
        return

    rows: list[list[InlineKeyboardButton]] = []
    for user in page_users:
        full_name = user.get("full_name") or "—"
        telegram_id = user.get("telegram_id", 0)
        rows.append([InlineKeyboardButton(text=full_name, callback_data=f"access:pick:{action}:{telegram_id}:{back_target}")])

    if total_pages > 1:
        pagination_row: list[InlineKeyboardButton] = []
        if page > 0:
            pagination_row.append(
                InlineKeyboardButton(text="◀️", callback_data=f"access:pg:{action}:{dept_id}:{page - 1}:{back_target}")
            )
        pagination_row.append(InlineKeyboardButton(text=f"{page + 1}/{total_pages}", callback_data="nav:ignore"))
        if page < total_pages - 1:
            pagination_row.append(
                InlineKeyboardButton(text="▶️", callback_data=f"access:pg:{action}:{dept_id}:{page + 1}:{back_target}")
            )
        rows.append(pagination_row)

    _, mgr_user = await admin_core.is_manager(callback.from_user.id)
    if mgr_user and mgr_user.get("role") == "admin":
        rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data=f"access:{action}:{back_target}")])
    else:
        rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data=f"nav:back:{back_target}")])

    await _edit_callback_message(
        callback,
        f"{title}\n\n<b>{dept_name}</b> ({total})\nВыберите сотрудника:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
        parse_mode="HTML",
    )
    await callback.answer()


@router.callback_query(F.data.startswith("access:pick:"))
async def access_pick_user(callback: CallbackQuery) -> None:
    mgr_user = await _require_manager_callback(callback)
    if not mgr_user:
        return

    parts = (callback.data or "").split(":")
    if len(parts) != 5:
        await callback.answer("Ошибка", show_alert=True)
        return

    action = parts[2]
    try:
        telegram_id = int(parts[3])
    except ValueError:
        await callback.answer("Ошибка", show_alert=True)
        return
    back_target = parts[4]

    target_user = await UsersRepo.get_by_telegram_id(telegram_id)
    if not target_user:
        await callback.answer("Пользователь не найден", show_alert=True)
        return
    if mgr_user.get("role") != "admin":
        if not mgr_user.get("department_id") or mgr_user.get("department_id") != target_user.get("department_id"):
            await callback.answer("❌ Недостаточно прав для этого отдела", show_alert=True)
            return

    label = _format_user_label(target_user, telegram_id)
    if action == "revoke":
        text = f"⚠️ <b>Вы уверены?</b>\n\n{label} потеряет доступ к боту."
    else:
        text = f"✅ Восстановить доступ для {label}?"

    await _edit_callback_message(
        callback,
        text,
        reply_markup=_build_access_confirm_keyboard(action, telegram_id, back_target),
        parse_mode="HTML",
    )
    await callback.answer()


@router.callback_query(F.data.startswith("access:picker_page:"))
async def access_picker_page(callback: CallbackQuery) -> None:
    parts = (callback.data or "").split(":")
    if len(parts) != 5:
        return

    action = parts[2]
    try:
        page = int(parts[3])
    except ValueError:
        return
    back_target = parts[4]

    mgr_user = await _require_manager_callback(callback)
    if not mgr_user:
        return
    target_dept = mgr_user.get("department_id") if mgr_user.get("role") != "admin" else None
    users = await _fetch_users_for_action(action, target_dept, callback.from_user.id)
    await _edit_callback_message(
        callback,
        "Выберите сотрудника:",
        reply_markup=to_aiogram_markup(user_picker_keyboard(users, action, back_target, page=page)),
        parse_mode="HTML",
    )
    await callback.answer()


@router.callback_query(F.data.startswith("access:pg:"))
async def access_dept_page(callback: CallbackQuery) -> None:
    parts = (callback.data or "").split(":")
    if len(parts) < 6:
        return
    action = parts[2]
    try:
        dept_id = int(parts[3])
        page = int(parts[4])
    except ValueError:
        return
    manager = await _require_manager_callback(callback)
    if not manager:
        return
    if not _manager_can_use_department(manager, dept_id):
        await callback.answer("❌ Недостаточно прав для этого отдела.", show_alert=True)
        return
    await _show_access_dept_users(callback, action, dept_id, parts[5], page=page)


async def _fetch_users_for_action(
    action: str,
    target_dept: int | None,
    exclude_telegram_id: int | None = None,
) -> list[dict]:
    if action == "restore":
        users = await UsersRepo.get_terminated_by_department(target_dept)
    elif target_dept:
        users = await UsersRepo.get_active_by_department(target_dept)
    else:
        users = []
        for dept_id in [1, 2]:
            users.extend(await UsersRepo.get_active_by_department(dept_id))

    if exclude_telegram_id and action != "restore":
        users = [item for item in users if item.get("telegram_id") != exclude_telegram_id]
    return users


@router.callback_query(F.data.startswith("access:alpha:"))
async def access_alpha_filter(callback: CallbackQuery) -> None:
    parts = (callback.data or "").split(":")
    if len(parts) != 5:
        return

    action = parts[2]
    range_label = parts[3]
    back_target = parts[4]
    mgr_user = await _require_manager_callback(callback)
    if not mgr_user:
        return

    target_dept = mgr_user.get("department_id") if mgr_user.get("role") != "admin" else None
    is_admin = mgr_user.get("role") == "admin"
    users = await _fetch_users_for_action(action, target_dept, callback.from_user.id)

    allowed_chars = ""
    for label, chars in ALPHA_RANGES:
        if label == range_label:
            allowed_chars = chars
            break

    if allowed_chars:
        filtered = [
            user
            for user in users
            if user.get("full_name") and user["full_name"][0].upper() in allowed_chars
        ]
    else:
        filtered = users

    title = "🚫 <b>Отозвать доступ</b>" if action == "revoke" else "✅ <b>Восстановить доступ</b>"
    count_info = f" [{range_label}: {len(filtered)} из {len(users)}]"
    await _edit_callback_message(
        callback,
        f"{title}{count_info}\n\nВыберите сотрудника:",
        reply_markup=to_aiogram_markup(
            user_picker_keyboard(
                filtered,
                action,
                back_target,
                grouped=is_admin and not allowed_chars,
                alpha_filter=range_label,
            )
        ),
        parse_mode="HTML",
    )
    await callback.answer()


@router.callback_query(F.data.startswith("access:search:"))
async def access_search_start(callback: CallbackQuery, state: FSMContext) -> None:
    parts = (callback.data or "").split(":")
    if len(parts) != 4:
        await callback.answer("Ошибка данных.", show_alert=True)
        return
    if not await _require_manager_callback(callback):
        return

    await state.update_data(access_search_action=parts[2], access_search_back_target=parts[3])
    await state.set_state(AdminStates.access_search)
    await _edit_callback_message(
        callback,
        "🔍 Введите часть фамилии для поиска:",
        reply_markup=to_aiogram_markup(navigation_keyboard(parts[3])),
        parse_mode="HTML",
    )
    await callback.answer()


@router.message(AdminStates.access_search, F.text)
async def access_search_input(message: Message, state: FSMContext) -> None:
    if not message.from_user or not message.text:
        return
    if message.text.startswith("/"):
        await state.clear()
        return

    is_mgr, mgr_user = await admin_core.is_manager(message.from_user.id)
    if not is_mgr or not mgr_user:
        await state.clear()
        return

    data = await state.get_data()
    action = data.get("access_search_action", "revoke")
    back_target = data.get("access_search_back_target", "manager")
    query = message.text.strip()
    if not query:
        await message.answer("Введите хотя бы одну букву.")
        await state.clear()
        return

    target_dept = mgr_user.get("department_id") if mgr_user.get("role") != "admin" else None
    is_admin = mgr_user.get("role") == "admin"
    if action == "restore":
        all_users = await UsersRepo.get_terminated_by_department(target_dept)
        q_lower = query.lower()
        users = [user for user in all_users if user.get("full_name") and q_lower in user["full_name"].lower()]
    else:
        users = await UsersRepo.search_active_users(query, department_id=target_dept)
        users = [user for user in users if user.get("telegram_id") != message.from_user.id]

    title = "🚫 <b>Отозвать доступ</b>" if action == "revoke" else "✅ <b>Восстановить доступ</b>"
    if not users:
        await message.answer(
            f"{title}\n\n🔍 По запросу «{query}» ничего не найдено.",
            reply_markup=to_aiogram_markup(navigation_keyboard(back_target)),
            parse_mode="HTML",
        )
    else:
        await message.answer(
            f"{title}\n\n🔍 Результаты по «{query}» ({len(users)}):",
            reply_markup=to_aiogram_markup(
                user_picker_keyboard(
                    users,
                    action,
                    back_target,
                    grouped=is_admin and len(users) > 10,
                    search_query=query,
                )
            ),
            parse_mode="HTML",
        )
    await state.clear()


@router.callback_query(F.data.startswith("access:cancel:"))
async def access_cancel(callback: CallbackQuery, state: FSMContext) -> None:
    back_target = (callback.data or "").split(":")[2] if callback.data else "manager"
    await state.clear()
    if back_target == "admin":
        pending = await RegistrationRepo.get_pending_requests()
        await _edit_callback_message(
            callback,
            "🛠 <b>Админ-панель</b>",
            reply_markup=to_aiogram_markup(panel_core.build_admin_panel_keyboard(pending_count=len(pending))),
            parse_mode="HTML",
        )
    else:
        pending_count = await admin_core._get_pending_count(callback.from_user.id)
        await _edit_callback_message(
            callback,
            "👔 <b>Панель руководителя</b>",
            reply_markup=to_aiogram_markup(manager_panel_navigation_keyboard("home", pending_count=pending_count)),
            parse_mode="HTML",
        )
    await callback.answer("Действие отменено")


@router.callback_query(F.data.startswith("access:confirm:"))
async def access_confirm(callback: CallbackQuery, bot: Bot) -> None:
    parts = (callback.data or "").split(":")
    if len(parts) < 5:
        await callback.answer("❌ Некорректная команда", show_alert=True)
        return

    action = parts[2]
    try:
        telegram_id = int(parts[3])
    except ValueError:
        await callback.answer("❌ Некорректный Telegram ID", show_alert=True)
        return
    back_target = parts[4]

    actor = await UsersRepo.get_by_telegram_id(callback.from_user.id)
    if not actor or actor.get("role") not in ("admin", "manager"):
        await callback.answer("❌ Недостаточно прав", show_alert=True)
        return

    target_user = await UsersRepo.get_by_telegram_id(telegram_id)
    if not target_user:
        await callback.answer("Пользователь не найден", show_alert=True)
        return

    if actor.get("role") != "admin":
        if not actor.get("department_id") or actor.get("department_id") != target_user.get("department_id"):
            await callback.answer("❌ Недостаточно прав для этого отдела", show_alert=True)
            return

    if action == "revoke":
        if telegram_id == callback.from_user.id:
            await callback.answer("Нельзя отозвать доступ самому себе.", show_alert=True)
            return
        await UsersRepo.set_status_by_telegram_id(telegram_id, UserStatus.TERMINATED)
        await cleanup_user_messages(bot, telegram_id)
        await send_revoked_notice_if_needed(bot, telegram_id)
        result_text = f"🚫 Доступ отозван для {telegram_id}."
    else:
        await UsersRepo.set_status_by_telegram_id(telegram_id, UserStatus.ACTIVE)
        result_text = f"✅ Доступ восстановлен для {telegram_id}."

    await _edit_callback_message(
        callback,
        result_text,
        reply_markup=to_aiogram_markup(navigation_keyboard(back_target)),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("reg_approve:"))
async def callback_approve(callback: CallbackQuery, bot: Bot) -> None:
    manager = await _require_manager_callback(callback)
    if not manager:
        return

    try:
        request_id = int((callback.data or "").split(":")[1])
    except (ValueError, IndexError):
        await callback.answer("Ошибка данных.", show_alert=True)
        return

    request_data = await RegistrationRepo.get_request_by_id(request_id)
    if not request_data or request_data.get("status") != "pending":
        await callback.answer("❌ Заявка не найдена", show_alert=True)
        return

    department_id = request_data.get("department_id")
    if department_id != OZKO_DEPARTMENT_ID:
        work_mode_value = request_data.get("work_mode") or WorkMode.HYBRID.value
        try:
            work_mode = WorkMode(work_mode_value)
        except ValueError:
            work_mode = WorkMode.HYBRID

        await _approve_request(callback, bot, request_id, manager, work_mode, department_id)
        return

    await _edit_callback_message(
        callback,
        f"Выберите режим работы для сотрудника (заявка #{request_id}):",
        reply_markup=to_aiogram_markup(work_mode_keyboard(request_id)),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("reg_mode:"))
async def callback_work_mode(callback: CallbackQuery, bot: Bot) -> None:
    manager = await _require_manager_callback(callback)
    if not manager:
        return

    parts = (callback.data or "").split(":")
    try:
        request_id = int(parts[1])
        work_mode = WorkMode(parts[2])
    except (ValueError, IndexError):
        await callback.answer("❌ Неверный режим работы", show_alert=True)
        return

    await _approve_request(callback, bot, request_id, manager, work_mode, OZKO_DEPARTMENT_ID)


async def _approve_request(
    callback: CallbackQuery,
    bot: Bot,
    request_id: int,
    manager: dict,
    work_mode: WorkMode,
    department_id: int | None,
) -> None:
    result = await RegistrationService.approve_request(
        request_id=request_id,
        reviewed_by=manager["id"],
        work_mode=work_mode,
    )
    if not result.success:
        if result.error == RegistrationError.NOT_INVITE_CREATOR:
            await callback.answer("⛔ Одобрить может только создатель приглашения или admin.", show_alert=True)
        else:
            await callback.answer("❌ Ошибка подтверждения заявки", show_alert=True)
        return

    user_telegram_id = result.data.get("telegram_id") if result.data else None
    user_full_name = result.data.get("full_name", "—") if result.data else "—"
    await _edit_callback_message(
        callback,
        f"✅ Заявка #{request_id} подтверждена!\n\n"
        f"👤 {user_full_name}\n"
        f"📊 Режим работы: {work_mode.value}",
        reply_markup=to_aiogram_markup(back_to_pending_keyboard()),
    )

    if result.user_id:
        await admin_core._send_approve_notice(
            bot=bot,
            user_id=result.user_id,
            telegram_id=user_telegram_id,
            department_id=department_id,
            reply_markup=to_aiogram_markup(admin_core.build_approve_notice_keyboard()),
        )

    logger.info(
        "aiogram registration approve: request_id=%s manager_id=%s user_id=%s",
        request_id,
        manager["id"],
        result.user_id,
    )
    await callback.answer("✅ Заявка подтверждена")


@router.callback_query(F.data.startswith("reg_reject:"))
async def callback_reject(callback: CallbackQuery, state: FSMContext) -> None:
    if not await _require_manager_callback(callback):
        return

    try:
        request_id = int((callback.data or "").split(":")[1])
    except (ValueError, IndexError):
        await callback.answer("Ошибка данных.", show_alert=True)
        return

    await state.update_data(reject_request_id=request_id)
    await state.set_state(AdminStates.reject_reason)
    await _edit_callback_message(
        callback,
        f"❌ Отклонение заявки #{request_id}\n\n"
        "Введите причину отклонения или нажмите кнопку для отклонения без причины:",
        reply_markup=to_aiogram_markup(confirm_reject_keyboard(request_id)),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("reg_reject_confirm:"))
async def callback_reject_confirm(callback: CallbackQuery, bot: Bot, state: FSMContext) -> None:
    manager = await _require_manager_callback(callback)
    if not manager:
        return

    try:
        request_id = int((callback.data or "").split(":")[1])
    except (ValueError, IndexError):
        await callback.answer("Ошибка данных.", show_alert=True)
        return

    await _reject_request(callback, bot, request_id, manager, reason=None)
    await state.clear()


@router.message(AdminStates.reject_reason, F.text)
async def process_reject_reason(message: Message, bot: Bot, state: FSMContext) -> None:
    if not message.from_user or not message.text:
        return
    if message.text.startswith("/"):
        await state.clear()
        return

    is_mgr, manager = await admin_core.is_manager(message.from_user.id)
    if not is_mgr or not manager:
        await state.clear()
        return

    data = await state.get_data()
    request_id = data.get("reject_request_id")
    if not request_id:
        await state.clear()
        return

    reason = message.text.strip()
    result = await RegistrationService.reject_request(
        request_id=int(request_id),
        reviewed_by=manager["id"],
        reason=reason,
    )
    if not result.success:
        await message.answer("❌ Ошибка отклонения заявки")
        await state.clear()
        return

    user_telegram_id = result.data.get("telegram_id") if result.data else None
    await message.answer(
        f"❌ Заявка #{request_id} отклонена.\n\nПричина: {reason}",
        reply_markup=to_aiogram_markup(back_to_pending_keyboard()),
    )
    if user_telegram_id:
        await cleanup_user_messages(bot, user_telegram_id)
        try:
            await bot.send_message(
                user_telegram_id,
                "❌ Заявка отклонена. Доступ не предоставлен.\n\n"
                f"Причина: {reason}\n\n"
                "Если это ошибка — обратитесь к менеджеру.",
            )
        except Exception as exc:
            logger.warning("aiogram reject: user notify failed telegram_id=%s error=%s", user_telegram_id, exc)

    logger.info("aiogram registration reject: request_id=%s manager_id=%s reason=yes", request_id, manager["id"])
    await state.clear()


async def _reject_request(
    callback: CallbackQuery,
    bot: Bot,
    request_id: int,
    manager: dict,
    *,
    reason: str | None,
) -> None:
    result = await RegistrationService.reject_request(
        request_id=request_id,
        reviewed_by=manager["id"],
        reason=reason,
    )
    if not result.success:
        await callback.answer("❌ Ошибка отклонения заявки", show_alert=True)
        return

    user_telegram_id = result.data.get("telegram_id") if result.data else None
    await _edit_callback_message(
        callback,
        f"❌ Заявка #{request_id} отклонена.",
        reply_markup=to_aiogram_markup(back_to_pending_keyboard()),
    )
    if user_telegram_id:
        await cleanup_user_messages(bot, user_telegram_id)
        try:
            await bot.send_message(
                user_telegram_id,
                "❌ Заявка отклонена. Доступ не предоставлен.\n\n"
                "Если это ошибка — обратитесь к менеджеру.",
            )
        except Exception as exc:
            logger.warning("aiogram reject: user notify failed telegram_id=%s error=%s", user_telegram_id, exc)

    logger.info("aiogram registration reject: request_id=%s manager_id=%s", request_id, manager["id"])
    await callback.answer("❌ Заявка отклонена")


@router.callback_query(F.data.startswith("reg_back:"))
async def callback_back(callback: CallbackQuery, state: FSMContext) -> None:
    try:
        request_id = int((callback.data or "").split(":")[1])
    except (ValueError, IndexError):
        await callback.answer("Ошибка данных.", show_alert=True)
        return

    request = await RegistrationRepo.get_request_by_id(request_id)
    await state.clear()
    if not request or request.get("status") != "pending":
        await _edit_callback_message(
            callback,
            "⚠️ Заявка уже обработана или не найдена.",
            reply_markup=to_aiogram_markup(back_to_pending_keyboard()),
        )
        await callback.answer()
        return

    await _edit_callback_message(
        callback,
        admin_core.format_request_info(request),
        reply_markup=to_aiogram_markup(pending_request_keyboard(request_id)),
        parse_mode="HTML",
    )
    await callback.answer()


@router.callback_query(F.data == "reg_list")
async def callback_list(callback: CallbackQuery) -> None:
    user = await _require_manager_callback(callback)
    if not user or not isinstance(callback.message, Message):
        return

    back_target = "admin" if user.get("role") == "admin" else "manager"
    await _send_pending_requests(callback.message, user, back_target=back_target, use_edit=True)
    await callback.answer()


@router.callback_query(F.data == "reg_batch_approve")
async def callback_batch_approve(callback: CallbackQuery) -> None:
    manager = await _require_manager_callback(callback)
    if not manager:
        return

    requests = await RegistrationService.get_pending_requests(manager.get("department_id"))
    if not requests:
        await callback.answer("📭 Нет заявок.", show_alert=True)
        return

    modes = [
        (WorkMode.HYBRID, "🏢 Гибрид"),
        (WorkMode.HYBRID_PLUS, "🏢+ Гибрид+"),
        (WorkMode.FULL_REMOTE, "🏠 Полная удалёнка"),
        (WorkMode.DUTY, "⏰ Дежурство"),
    ]
    rows = [[InlineKeyboardButton(text=label, callback_data=f"reg_batch_mode:{mode.value}")] for mode, label in modes]
    rows.append([InlineKeyboardButton(text="↩️ Назад", callback_data="reg_list")])

    await _edit_callback_message(
        callback,
        f"✅ <b>Одобрить все заявки ({len(requests)} шт.)</b>\n\n"
        "Выберите режим работы для всех:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
        parse_mode="HTML",
    )
    await callback.answer()


@router.callback_query(F.data.startswith("reg_batch_mode:"))
async def callback_batch_mode(callback: CallbackQuery, bot: Bot) -> None:
    manager = await _require_manager_callback(callback)
    if not manager:
        return

    try:
        work_mode = WorkMode((callback.data or "").split(":")[1])
    except (ValueError, IndexError):
        await callback.answer("❌ Неверный режим работы", show_alert=True)
        return

    requests = await RegistrationService.get_pending_requests(manager.get("department_id"))
    if not requests:
        await callback.answer("📭 Нет заявок.", show_alert=True)
        return

    approved = 0
    errors = 0
    for request in requests:
        try:
            result = await RegistrationService.approve_request(
                request_id=request["id"],
                reviewed_by=manager["id"],
                work_mode=work_mode,
            )
            if not result.success:
                errors += 1
                continue
            approved += 1
            user_telegram_id = result.data.get("telegram_id") if result.data else None
            if result.user_id:
                await admin_core._send_approve_notice(
                    bot=bot,
                    user_id=result.user_id,
                    telegram_id=user_telegram_id,
                    department_id=request.get("department_id"),
                    reply_markup=to_aiogram_markup(admin_core.build_approve_notice_keyboard()),
                )
        except Exception as exc:
            errors += 1
            logger.warning("aiogram batch_approve: request_id=%s error=%s", request.get("id"), exc)

    await _edit_callback_message(
        callback,
        "✅ Батч-одобрение завершено.\n\n"
        f"Одобрено: {approved}\n"
        f"Ошибки: {errors}\n"
        f"Режим работы: {work_mode.value}",
        reply_markup=to_aiogram_markup(back_to_pending_keyboard()),
    )
    logger.info(
        "aiogram batch_approve: approved=%d errors=%d mode=%s by=%s",
        approved,
        errors,
        work_mode.value,
        manager["id"],
    )
    await callback.answer(f"✅ Одобрено: {approved}")


@router.callback_query(F.data.startswith("mgr:dept_vacations"))
async def callback_dept_vacations(callback: CallbackQuery) -> None:
    if await _reject_if_group_callback(callback):
        return
    user = await _require_manager_callback(callback)
    if not user:
        return

    parts = (callback.data or "").split(":")
    today = date.today()
    if len(parts) >= 4:
        try:
            year = int(parts[2])
            month = int(parts[3])
        except (ValueError, IndexError):
            year, month = today.year, today.month
    else:
        year, month = today.year, today.month

    dept_filter = 0
    if len(parts) >= 5:
        try:
            dept_filter = int(parts[4])
        except (ValueError, IndexError):
            dept_filter = 0

    _, last_day = calendar.monthrange(year, month)
    start_date = date(year, month, 1)
    end_date = date(year, month, last_day)

    is_admin_user = user.get("role") == "admin"
    if is_admin_user:
        effective_dept = dept_filter if dept_filter > 0 else None
    else:
        effective_dept = user.get("department_id")

    dept_name = DEPT_NAMES.get(effective_dept, "—") if effective_dept else "всех отделов"

    from apps.attendance_bot.app.ui.keyboards_neutral import compress_days_to_ranges, format_date_range
    from core.screw_core.repos.day_status_repo import DayStatusRepo

    if effective_dept:
        dept_users = await UsersRepo.get_active_by_department(effective_dept)
    else:
        dept_users = []
        for dept_id in [1, 2]:
            dept_users.extend(await UsersRepo.get_active_by_department(dept_id))

    user_ids = [item["id"] for item in dept_users if item.get("id")]
    user_names = {item["id"]: item.get("full_name", "—") for item in dept_users}
    statuses_map = await DayStatusRepo.get_statuses_for_date_range(
        start_date,
        end_date,
        user_ids=user_ids if user_ids else None,
    )

    user_statuses: dict[int, list[tuple[date, str]]] = {}
    for (user_id, status_date), kind in statuses_map.items():
        if kind == "DAY_OFF":
            continue
        user_statuses.setdefault(user_id, []).append((status_date, kind))

    month_name = MONTH_NAMES_RU[month]
    if effective_dept:
        text = f"📅 <b>Отпуска отдела {dept_name} — {month_name} {year}</b>\n\n"
    else:
        text = f"📅 <b>Отпуска {dept_name} — {month_name} {year}</b>\n\n"

    if not user_statuses:
        text += "✅ Нет запланированных отсутствий.\n"
    else:
        for user_id, days in sorted(user_statuses.items(), key=lambda item: user_names.get(item[0], "")):
            name = user_names.get(user_id, f"ID:{user_id}")
            for item in compress_days_to_ranges(days):
                emoji = admin_core.KIND_EMOJI.get(item.kind, "📌")
                date_range = format_date_range(item)
                days_count = (item.end - item.start).days + 1
                text += f"{emoji} {name}: {date_range} ({days_count} дн.)\n"

        busy_user_ids = set(user_statuses.keys())
        free_count = len(user_ids) - len(busy_user_ids)
        text += f"\nСвободных: {free_count} из {len(user_ids)} сотрудников"

    if len(text) > 4000:
        text = text[:3900] + "\n\n<i>...сообщение обрезано</i>"

    back_target = "admin" if is_admin_user else "manager"
    keyboard = admin_core._dept_vacations_keyboard(
        year,
        month,
        back_target,
        dept_filter=dept_filter,
        show_dept_toggle=is_admin_user,
    )
    await _edit_callback_message(
        callback,
        text,
        reply_markup=to_aiogram_markup(keyboard),
        parse_mode="HTML",
    )
    await callback.answer()
