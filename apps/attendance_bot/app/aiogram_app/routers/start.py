"""Native aiogram /start and registration router."""

from __future__ import annotations

from datetime import date, timedelta

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from apps.attendance_bot.app.aiogram_app.keyboards import to_aiogram_markup
from apps.attendance_bot.app.config import is_dev_self_registration_enabled
from apps.attendance_bot.app.ui.registration_texts import (
    MSG_ALREADY_REGISTERED,
    MSG_ASK_FULL_NAME,
    MSG_NEED_INVITE,
    MSG_PENDING_REQUEST,
    MSG_REQUEST_CREATED,
    MSG_TOKEN_ALREADY_USED,
    MSG_TOKEN_EXPIRED,
    MSG_TOKEN_NOT_FOUND,
)
from apps.attendance_bot.app.ui import start_views
from apps.attendance_bot.app.ui.keyboards_neutral import (
    DEPT_NAMES,
    build_user_nav_footer,
    compress_days_to_ranges,
    format_date_range,
    format_month_ru,
    navigation_keyboard,
    registration_cancel_keyboard,
    root_menu_keyboard,
    user_month_navigation_keyboard,
    user_panel_keyboard,
    user_status_navigation_keyboard,
)
from apps.attendance_bot.app.utils.message_tracker import track_bot_message
from apps.attendance_bot.app.utils.permissions import get_permissions
from apps.attendance_bot.app.utils.private_chat_guard import MSG_PRIVATE_ONLY
from core.screw_core.models.enums import AttendanceChoice
from core.screw_core.repos.attendance_repo import AttendanceRepo
from core.screw_core.repos.day_status_repo import DayStatusRepo
from core.screw_core.repos.polls_repo import PollsRepo
from core.screw_core.repos.registration_repo import RegistrationRepo
from core.screw_core.repos.users_repo import UsersRepo
from core.screw_core.services.permissions import OZKO_DEPARTMENT_ID
from core.screw_core.services.registration import RegistrationError, RegistrationService
from core.screw_core.utils.logging import get_logger

logger = get_logger(__name__)
router = Router(name="attendance_start")


class RegistrationStates(StatesGroup):
    waiting_full_name = State()


async def _reject_if_group_without_token(message: Message, token: str | None) -> bool:
    if message.chat.type == "private" or token:
        return False
    logger.info(
        "start_blocked_in_group: user=%s chat=%s command=%s",
        message.from_user.id if message.from_user else None,
        message.chat.id,
        (message.text or "")[:50],
    )
    await message.answer(MSG_PRIVATE_ONLY)
    return True


async def _show_root_menu(message: Message, telegram_id: int) -> None:
    perms = await get_permissions(telegram_id)
    pending_count = await start_views.get_pending_count(perms)
    keyboard = root_menu_keyboard(
        show_manager=perms.is_manager,
        show_admin=perms.is_admin,
        pending_count=pending_count,
    )
    sent = await message.answer("🏠 Главное меню", reply_markup=to_aiogram_markup(keyboard))
    await track_bot_message(telegram_id, sent.chat.id, sent.message_id, screen="root_menu")


async def _is_user_ozko(telegram_id: int) -> bool:
    user = await UsersRepo.get_by_telegram_id(telegram_id)
    return bool(user and user.get("department_id") == OZKO_DEPARTMENT_ID)


async def _show_user_panel(message: Message, telegram_id: int) -> None:
    perms = await get_permissions(telegram_id)
    is_privileged = perms.is_manager or perms.is_admin
    is_ozko = perms.department_id == OZKO_DEPARTMENT_ID if perms.department_id else await _is_user_ozko(telegram_id)
    text = await start_views.build_dashboard_text(telegram_id)
    keyboard = user_panel_keyboard(is_privileged=is_privileged, is_ozko=is_ozko)
    sent = await message.answer(text, reply_markup=to_aiogram_markup(keyboard), parse_mode="HTML")
    await track_bot_message(telegram_id, sent.chat.id, sent.message_id, screen="user_panel")


async def _reject_if_group_callback(callback: CallbackQuery) -> bool:
    message = callback.message
    chat_type = getattr(getattr(message, "chat", None), "type", None)
    if chat_type in (None, "private"):
        return False
    await callback.answer(MSG_PRIVATE_ONLY, show_alert=True)
    return True


async def _edit_callback_message(
    callback: CallbackQuery,
    text: str,
    *,
    reply_markup=None,
    parse_mode: str | None = None,
    screen: str | None = None,
) -> None:
    if not isinstance(callback.message, Message):
        await callback.answer("Экран уже недоступен. Откройте меню заново.", show_alert=True)
        return
    try:
        edited = await callback.message.edit_text(text, reply_markup=reply_markup, parse_mode=parse_mode)
    except TelegramBadRequest as exc:
        if "message is not modified" in str(exc).lower():
            return
        raise
    if callback.from_user:
        await track_bot_message(
            callback.from_user.id,
            edited.chat.id,
            edited.message_id,
            screen=screen,
        )


async def _edit_root_menu(callback: CallbackQuery) -> None:
    if not callback.from_user:
        return
    perms = await get_permissions(callback.from_user.id)
    pending_count = await start_views.get_pending_count(perms)
    keyboard = root_menu_keyboard(
        show_manager=perms.is_manager,
        show_admin=perms.is_admin,
        pending_count=pending_count,
    )
    await _edit_callback_message(
        callback,
        "🏠 Главное меню",
        reply_markup=to_aiogram_markup(keyboard),
        screen="nav:home",
    )


async def _edit_user_panel(callback: CallbackQuery) -> None:
    if not callback.from_user:
        return
    telegram_id = callback.from_user.id
    perms = await get_permissions(telegram_id)
    is_privileged = perms.is_manager or perms.is_admin
    is_ozko = perms.department_id == OZKO_DEPARTMENT_ID if perms.department_id else await _is_user_ozko(telegram_id)
    text = await start_views.build_dashboard_text(telegram_id)
    await _edit_callback_message(
        callback,
        text,
        reply_markup=to_aiogram_markup(user_panel_keyboard(is_privileged=is_privileged, is_ozko=is_ozko)),
        parse_mode="HTML",
        screen="panel:user",
    )


class _PTBRowsMarkup:
    def __init__(self, rows) -> None:
        self.inline_keyboard = rows


def _aiogram_rows_from_ptb(rows) -> list[list[InlineKeyboardButton]]:
    markup = to_aiogram_markup(_PTBRowsMarkup(rows))
    return markup.inline_keyboard if markup else []


@router.message(CommandStart())
async def cmd_start(message: Message, command: CommandObject, state: FSMContext) -> None:
    if not message.from_user:
        return

    await state.clear()
    telegram_id = message.from_user.id
    token = command.args.strip() if command.args else None

    if await _reject_if_group_without_token(message, token):
        return

    dev_mode = is_dev_self_registration_enabled()
    logger.info(
        "aiogram start: user=%s token=%s dev_mode=%s",
        telegram_id,
        "есть" if token else "нет",
        dev_mode,
    )

    status = await RegistrationService.check_user_status(telegram_id)
    if status["is_registered"] and not (dev_mode and token):
        perms = await get_permissions(telegram_id)
        if perms.is_manager or perms.is_admin:
            await _show_root_menu(message, telegram_id)
        else:
            await _show_user_panel(message, telegram_id)
        return

    if status["has_pending"] and not token:
        await message.answer(MSG_PENDING_REQUEST)
        return

    if not token:
        await message.answer(MSG_NEED_INVITE)
        return

    if message.chat.type != "private":
        await message.answer("Пожалуйста, откройте бота в личных сообщениях для регистрации.")
        return

    validation = await RegistrationService.validate_token(token)
    if not validation.success:
        error_messages = {
            RegistrationError.TOKEN_NOT_FOUND: MSG_TOKEN_NOT_FOUND,
            RegistrationError.TOKEN_EXPIRED: MSG_TOKEN_EXPIRED,
            RegistrationError.TOKEN_ALREADY_USED: MSG_TOKEN_ALREADY_USED,
        }
        await message.answer(error_messages.get(validation.error, MSG_TOKEN_NOT_FOUND))
        return

    await state.update_data(registration_token=token)
    await state.set_state(RegistrationStates.waiting_full_name)
    await message.answer(MSG_ASK_FULL_NAME, reply_markup=to_aiogram_markup(registration_cancel_keyboard()))


@router.message(Command("ping"))
async def cmd_ping(message: Message) -> None:
    logger.info("aiogram ping: user=%s", message.from_user.id if message.from_user else None)
    await message.answer("pong")


@router.message(Command("help"))
async def cmd_help(message: Message) -> None:
    if not message.from_user:
        return
    await _show_root_menu(message, message.from_user.id)


@router.callback_query(F.data == "nav:home")
@router.callback_query(F.data == "nav:back:home")
async def nav_home(callback: CallbackQuery) -> None:
    if await _reject_if_group_callback(callback):
        return
    await _edit_root_menu(callback)
    await callback.answer()


@router.callback_query(F.data == "panel:user")
@router.callback_query(F.data == "nav:back:user")
async def nav_user_panel(callback: CallbackQuery) -> None:
    if await _reject_if_group_callback(callback):
        return
    await _edit_user_panel(callback)
    await callback.answer()


@router.callback_query(F.data == "nav:ignore")
async def nav_ignore(callback: CallbackQuery) -> None:
    await callback.answer()


@router.callback_query(F.data.startswith("nav:help:"))
async def nav_help(callback: CallbackQuery) -> None:
    if await _reject_if_group_callback(callback):
        return
    target = (callback.data or "").split(":")[-1]
    if target == "user":
        text = start_views.build_help_text_user()
    elif target == "manager":
        text = start_views.build_help_text_manager()
    elif target == "admin":
        text = start_views.build_help_text_admin()
    else:
        await callback.answer("Раздел помощи недоступен.", show_alert=True)
        return
    await _edit_callback_message(
        callback,
        text,
        reply_markup=to_aiogram_markup(navigation_keyboard(target)),
        parse_mode="HTML",
        screen=f"nav:help:{target}",
    )
    await callback.answer()


@router.callback_query(F.data == "user:today")
async def user_today(callback: CallbackQuery) -> None:
    if await _reject_if_group_callback(callback):
        return
    if not callback.from_user:
        return

    telegram_id = callback.from_user.id
    today = date.today()
    user = await UsersRepo.get_by_telegram_id(telegram_id)
    if not user:
        await _edit_callback_message(
            callback,
            "⚠️ Вы не зарегистрированы в системе.",
            reply_markup=to_aiogram_markup(navigation_keyboard("user")),
            parse_mode="HTML",
        )
        await callback.answer()
        return

    perms = await get_permissions(telegram_id)
    is_privileged = perms.is_manager or perms.is_admin
    user_id = user["id"]
    full_name = user.get("full_name") or "—"
    status = await DayStatusRepo.get_status(user_id, today)
    attendance_mark = await AttendanceRepo.get_mark(user_id, today)
    weekday = start_views.WEEKDAY_NAMES_SHORT[today.weekday()]
    today_str = today.strftime("%d.%m.%Y")
    work_mode = user.get("work_mode", "")
    work_mode_label = start_views.WORK_MODE_LABELS.get(work_mode, "")

    text = f"📅 <b>Сегодня</b> ({weekday}, {today_str})\n\n👤 {full_name}\n"
    if work_mode_label:
        text += f"{work_mode_label}\n"
    if status:
        kind = status.get("kind", "")
        status_label = start_views.STATUS_LABELS.get(kind, kind)
        text += f"📋 Статус: {status_label}"
        if status.get("comment"):
            text += f"\n💬 {status.get('comment')}"
    else:
        text += "📋 Статус: <i>обычный рабочий день</i>"

    if attendance_mark:
        choice = attendance_mark.get("choice", "")
        text += f"\n✅ Опрос: {start_views.ATTENDANCE_CHOICE_LABELS.get(choice, choice)}"
    else:
        text += "\n⏳ Опрос: <i>не отмечено</i>"

    if is_privileged:
        try:
            dept_id = user.get("department_id")
            if perms.is_admin:
                dept_users = []
                for department_id in [1, 2]:
                    dept_users.extend(await UsersRepo.get_active_by_department(department_id))
            elif dept_id:
                dept_users = await UsersRepo.get_active_by_department(dept_id)
            else:
                dept_users = []
            if dept_users:
                dept_user_ids = {item["id"] for item in dept_users if item.get("id")}
                marks = await AttendanceRepo.get_marks_for_date(today)
                dept_marks = [mark for mark in marks if mark.get("user_id") in dept_user_ids]
                evolution_count = sum(1 for mark in dept_marks if mark.get("choice") == "EVOLUTION")
                cska_count = sum(1 for mark in dept_marks if mark.get("choice") == "CSKA")
                remote_count = sum(1 for mark in dept_marks if mark.get("choice") == "REMOTE")
                office_count = evolution_count + cska_count
                marked_ids = {mark.get("user_id") for mark in dept_marks}
                statuses_map = await DayStatusRepo.get_statuses_for_date_range(
                    today,
                    today,
                    user_ids=list(dept_user_ids),
                )
                absent_ids = {uid for (uid, _), kind in statuses_map.items() if kind != "DAY_OFF"}
                unmarked = len(dept_user_ids) - len(marked_ids) - len(absent_ids - marked_ids)
                text += (
                    "\n\n━━━━━━━━━━━━━━━━\n"
                    "📊 <b>Отдел сегодня:</b>\n"
                    f"  🏢 Офис: {office_count}  |  🏠 Удалёнка: {remote_count}  |  "
                    f"⏳ Нет ответа: {max(unmarked, 0)}"
                )
                if absent_ids:
                    text += f"\n  ✈️ Отсутствуют: {len(absent_ids)}"
        except Exception as exc:
            logger.debug("aiogram user_today dept stats failed: %s", exc)

    await _edit_callback_message(
        callback,
        text,
        reply_markup=to_aiogram_markup(user_status_navigation_keyboard(is_privileged=is_privileged)),
        parse_mode="HTML",
        screen="user:today",
    )
    await callback.answer()


async def _render_month_view(callback: CallbackQuery, user: dict, year: int, month: int, is_privileged: bool) -> None:
    user_id = user["id"]
    full_name = user.get("full_name") or "—"
    start_of_month = date(year, month, 1)
    if month == 12:
        end_of_month = date(year + 1, 1, 1) - timedelta(days=1)
    else:
        end_of_month = date(year, month + 1, 1) - timedelta(days=1)

    statuses = await DayStatusRepo.get_statuses_for_date_range(
        start_date=start_of_month,
        end_date=end_of_month,
        user_ids=[user_id],
    )
    month_text = format_month_ru(year, month)
    text = f"🗓 <b>Месяц</b> ({month_text})\n\n👤 {full_name}\n\n"
    if statuses:
        days_with_status = [
            (status_date, kind)
            for (uid, status_date), kind in statuses.items()
            if uid == user_id
        ]
        ranges = compress_days_to_ranges(days_with_status)
        by_kind: dict[str, list[str]] = {}
        day_counts: dict[str, int] = {}
        for item in ranges:
            by_kind.setdefault(item.kind, []).append(format_date_range(item))
            day_counts[item.kind] = day_counts.get(item.kind, 0) + (item.end - item.start).days + 1
        for kind in sorted(by_kind):
            status_label = start_views.STATUS_LABELS.get(kind, kind)
            count = day_counts.get(kind, 0)
            text += f"{status_label}: {', '.join(by_kind[kind])} ({count} {start_views.day_suffix(count)})\n"
        total_absent = sum(day_counts.values())
        if total_absent > 0:
            text += f"\n━━━\n📊 Итого отсутствий: {total_absent} {start_views.day_suffix(total_absent)}"
    else:
        text += "<i>Нет особых статусов в этом месяце.</i>"

    await _edit_callback_message(
        callback,
        text,
        reply_markup=to_aiogram_markup(user_month_navigation_keyboard(year, month, is_privileged=is_privileged)),
        parse_mode="HTML",
        screen="user:month",
    )


@router.callback_query(F.data == "user:month")
@router.callback_query(F.data == "user:month:current")
async def user_month(callback: CallbackQuery) -> None:
    if await _reject_if_group_callback(callback):
        return
    if not callback.from_user:
        return

    today = date.today()
    telegram_id = callback.from_user.id
    user = await UsersRepo.get_by_telegram_id(telegram_id)
    if not user:
        await _edit_callback_message(
            callback,
            "⚠️ Вы не зарегистрированы в системе.",
            reply_markup=to_aiogram_markup(navigation_keyboard("user")),
            parse_mode="HTML",
        )
        await callback.answer()
        return
    perms = await get_permissions(telegram_id)
    await _render_month_view(callback, user, today.year, today.month, perms.is_manager or perms.is_admin)
    await callback.answer("✅ Текущий месяц" if callback.data == "user:month:current" else None)


@router.callback_query(F.data.startswith("user:month:nav:"))
async def user_month_nav(callback: CallbackQuery) -> None:
    if await _reject_if_group_callback(callback):
        return
    if not callback.from_user or not callback.data:
        return
    parts = callback.data.split(":")
    if len(parts) != 6:
        await callback.answer("Ошибка навигации", show_alert=True)
        return
    try:
        year = int(parts[3])
        month = int(parts[4])
    except ValueError:
        await callback.answer("Ошибка навигации", show_alert=True)
        return
    direction = parts[5]
    if direction == "prev":
        if month == 1:
            year -= 1
            month = 12
        else:
            month -= 1
    elif direction == "next":
        if month == 12:
            year += 1
            month = 1
        else:
            month += 1

    user = await UsersRepo.get_by_telegram_id(callback.from_user.id)
    if not user:
        await callback.answer("Вы не зарегистрированы", show_alert=True)
        return
    perms = await get_permissions(callback.from_user.id)
    await _render_month_view(callback, user, year, month, perms.is_manager or perms.is_admin)
    await callback.answer()


@router.callback_query(F.data == "user:who_in_office")
async def who_in_office(callback: CallbackQuery) -> None:
    if await _reject_if_group_callback(callback):
        return
    if not callback.from_user:
        return
    telegram_id = callback.from_user.id
    if not await _is_user_ozko(telegram_id):
        await callback.answer("Доступно только для ОЗКО", show_alert=True)
        return

    today = date.today()
    marks = await AttendanceRepo.get_marks_for_date(today)
    ozko_users = await UsersRepo.get_active_by_department(OZKO_DEPARTMENT_ID)
    ozko_user_ids = {user["id"] for user in ozko_users}
    ozko_names = {user["id"]: user.get("full_name", "—") for user in ozko_users}
    evolution: list[str] = []
    cska: list[str] = []
    remote: list[str] = []
    for mark in marks:
        user_id = mark.get("user_id")
        if user_id not in ozko_user_ids:
            continue
        name = ozko_names.get(user_id, "—")
        parts = name.split()
        if len(parts) >= 3:
            short_name = f"{parts[0]} {parts[1][0]}.{parts[2][0]}."
        elif len(parts) >= 2:
            short_name = f"{parts[0]} {parts[1][0]}."
        else:
            short_name = name
        choice = mark.get("choice", "")
        if choice == AttendanceChoice.EVOLUTION.value:
            evolution.append(short_name)
        elif choice == AttendanceChoice.CSKA.value:
            cska.append(short_name)
        elif choice == AttendanceChoice.REMOTE.value:
            remote.append(short_name)

    weekday = start_views.WEEKDAY_NAMES_SHORT[today.weekday()]
    total_office = len(evolution) + len(cska)
    text = f"🏢 <b>Сегодня в офисе</b> ({weekday}, {today.strftime('%d.%m')})\n\n"
    if evolution:
        text += f"🏢 Офис Эволюция ({len(evolution)}):\n  {', '.join(evolution)}\n\n"
    if cska:
        text += f"🏢 Офис ЦСКА ({len(cska)}):\n  {', '.join(cska)}\n\n"
    if remote:
        text += f"🏠 Удалёнка ({len(remote)}):\n  {', '.join(remote)}\n\n"
    if not evolution and not cska and not remote:
        text += "<i>Пока никто не отметился.</i>\n"
    else:
        text += f"━━━\n📊 В офисе: {total_office} | Удалёнка: {len(remote)}"

    perms = await get_permissions(telegram_id)
    await _edit_callback_message(
        callback,
        text,
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=_aiogram_rows_from_ptb(
                build_user_nav_footer(
                    back_target="user",
                    is_privileged=perms.is_manager or perms.is_admin,
                )
            )
        ),
        parse_mode="HTML",
        screen="user:who_in_office",
    )
    await callback.answer()


@router.callback_query(F.data == "user:my_stats")
@router.callback_query(F.data.startswith("user:my_stats:"))
async def user_my_stats(callback: CallbackQuery) -> None:
    if await _reject_if_group_callback(callback):
        return
    if not callback.from_user:
        return
    telegram_id = callback.from_user.id
    if not await _is_user_ozko(telegram_id):
        await callback.answer("Доступно только для ОЗКО", show_alert=True)
        return

    data = callback.data or ""
    parts = data.split(":")
    today = date.today()
    if len(parts) >= 3 and "-" in parts[2]:
        try:
            year_text, month_text = parts[2].split("-")
            year, month = int(year_text), int(month_text)
        except (ValueError, IndexError):
            year, month = today.year, today.month
    else:
        year, month = today.year, today.month

    user = await UsersRepo.get_by_telegram_id(telegram_id)
    user_id = user["id"] if user else 0
    choice_counts = await AttendanceRepo.get_user_monthly_choice_counts(user_id, year, month)
    response_count = await AttendanceRepo.get_user_response_count_for_month(user_id, year, month)
    poll_count = await PollsRepo.get_poll_count_for_month(year, month)
    evolution_count = choice_counts.get("EVOLUTION", 0)
    cska_count = choice_counts.get("CSKA", 0)
    remote_count = choice_counts.get("REMOTE", 0)
    not_marked = max(0, poll_count - response_count) if poll_count > 0 else 0

    current_month = format_month_ru(year, month)
    text = f"📈 <b>Моя статистика — {current_month}</b>\n\n"
    text += f"🏢 Офис (Эволюция): {evolution_count} {start_views.day_suffix(evolution_count)}\n"
    text += f"🏢 Офис (ЦСКА): {cska_count} {start_views.day_suffix(cska_count)}\n"
    text += f"🏠 Удалёнка: {remote_count} {start_views.day_suffix(remote_count)}\n"
    if not_marked > 0:
        text += f"❌ Не отметился: {not_marked} {start_views.day_suffix(not_marked)}\n"
    text += "━━━\n"
    text += f"📊 Рабочих дней: {poll_count}\n"
    if poll_count > 0:
        text += f"✅ Опросов отмечено: {response_count} из {poll_count}\n"

    prev_year, prev_month = (year - 1, 12) if month == 1 else (year, month - 1)
    next_year, next_month = (year + 1, 1) if month == 12 else (year, month + 1)
    nav_row = [
        InlineKeyboardButton(text="◀️", callback_data=f"user:my_stats:{prev_year}-{prev_month:02d}"),
        InlineKeyboardButton(text="📅 Текущий", callback_data="user:my_stats"),
        InlineKeyboardButton(text="▶️", callback_data=f"user:my_stats:{next_year}-{next_month:02d}"),
    ]
    perms = await get_permissions(telegram_id)
    await _edit_callback_message(
        callback,
        text,
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                nav_row,
                *_aiogram_rows_from_ptb(
                    build_user_nav_footer(
                        back_target="user",
                        is_privileged=perms.is_manager or perms.is_admin,
                    )
                ),
            ]
        ),
        parse_mode="HTML",
        screen="user:my_stats",
    )
    await callback.answer()


@router.message(RegistrationStates.waiting_full_name, F.text)
async def process_full_name(message: Message, state: FSMContext, bot: Bot) -> None:
    if not message.from_user or not message.text:
        return
    if message.text.startswith("/"):
        await state.clear()
        await message.answer(MSG_NEED_INVITE)
        return

    full_name = message.text.strip()
    if len(full_name) < 3:
        await message.answer("❌ ФИО слишком короткое. Введите полное ФИО.")
        return
    if len(full_name) > 200:
        await message.answer("❌ ФИО слишком длинное. Введите корректное ФИО.")
        return

    data = await state.get_data()
    token = data.get("registration_token")
    if not token:
        await state.clear()
        await message.answer(MSG_NEED_INVITE)
        return

    result = await RegistrationService.register_by_token(
        token=token,
        telegram_id=message.from_user.id,
        username=message.from_user.username,
        first_name=full_name,
        last_name=None,
    )
    await state.clear()

    if not result.success:
        error_messages = {
            RegistrationError.TOKEN_NOT_FOUND: MSG_TOKEN_NOT_FOUND,
            RegistrationError.TOKEN_EXPIRED: MSG_TOKEN_EXPIRED,
            RegistrationError.TOKEN_ALREADY_USED: MSG_TOKEN_ALREADY_USED,
            RegistrationError.ALREADY_REGISTERED: MSG_ALREADY_REGISTERED,
            RegistrationError.PENDING_REQUEST_EXISTS: MSG_PENDING_REQUEST,
        }
        await message.answer(error_messages.get(result.error, "❌ Ошибка регистрации."))
        return

    logger.info(
        "aiogram registration: request_id=%s telegram_id=%s",
        result.request_id,
        message.from_user.id,
    )
    await message.answer(MSG_REQUEST_CREATED)
    await _notify_invite_creator(bot, token, message, result.request_id)


@router.callback_query(RegistrationStates.waiting_full_name, F.data == "reg:cancel")
async def reg_cancel(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    if callback.message:
        await callback.message.edit_text(
            "❌ Регистрация отменена.\n\n"
            "Для повторной регистрации используйте ссылку-приглашение от менеджера.",
            parse_mode="HTML",
        )
    await callback.answer("Регистрация отменена.")


async def _notify_invite_creator(bot: Bot, token: str, message: Message, request_id: int | None) -> None:
    try:
        invite = await RegistrationRepo.get_invite_by_token(token)
        created_by = invite.get("created_by") if invite else None
        if not created_by:
            logger.warning("aiogram notify_invite_creator: invite creator missing")
            return

        creator_user = await UsersRepo.get_by_id(created_by)
        creator_telegram_id = creator_user.get("telegram_id") if creator_user else None
        if not creator_telegram_id:
            logger.warning("aiogram notify_invite_creator: creator telegram_id missing, user_id=%s", created_by)
            return

        username = message.from_user.username if message.from_user else None
        tg_name_parts = []
        if message.from_user and message.from_user.first_name:
            tg_name_parts.append(message.from_user.first_name)
        if message.from_user and message.from_user.last_name:
            tg_name_parts.append(message.from_user.last_name)
        tg_name = " ".join(tg_name_parts) if tg_name_parts else "—"

        department_name = DEPT_NAMES.get(invite.get("department_id"), "—") if invite else "—"
        expected_name = invite.get("expected_full_name") if invite else None

        lines = [
            f"🆕 <b>Новая заявка #{request_id}</b>",
            "",
            f"👤 Telegram: {tg_name}",
        ]
        if username:
            lines.append(f"🔗 Username: @{username}")
        lines.append(f"🏢 Отдел: {department_name}")
        if expected_name:
            lines.append(f"📝 Ожидаемое ФИО: {expected_name}")
        lines.append(f"🆔 ID: {message.from_user.id if message.from_user else '—'}")
        lines.append("")
        lines.append("Откройте карточку заявки кнопкой ниже.")

        rows: list[list[InlineKeyboardButton]] = []
        if request_id:
            rows.append([InlineKeyboardButton(text=f"🔎 Открыть заявку #{request_id}", callback_data=f"reg_view:{request_id}")])
        rows.append([InlineKeyboardButton(text="📋 Все заявки", callback_data="reg_list")])
        rows.append([InlineKeyboardButton(text="🔗 Приглашения", callback_data="mgr:invites_list")])

        await bot.send_message(
            creator_telegram_id,
            "\n".join(lines),
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
        )
    except Exception as exc:
        logger.warning("aiogram notify_invite_creator failed: %s", exc)
