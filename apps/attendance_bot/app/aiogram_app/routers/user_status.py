"""Native aiogram user day status router."""

from __future__ import annotations

from datetime import date, timedelta

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message

from apps.attendance_bot.app.aiogram_app.keyboards import to_aiogram_markup
from apps.attendance_bot.app.ui.keyboards_neutral import (
    MY_STATUSES_TITLE,
    USER_PANEL_TITLE,
    calendar_keyboard,
    my_statuses_keyboard,
    status_cancel_keyboard,
    status_clear_all_confirm_keyboard,
    status_confirm_keyboard,
    status_delete_confirm_keyboard,
    status_skip_comment_keyboard,
    status_type_keyboard,
    user_back_keyboard,
    user_panel_keyboard,
)
from apps.attendance_bot.app.utils.permissions import get_permissions
from apps.attendance_bot.app.utils.private_chat_guard import MSG_PRIVATE_ONLY
from core.screw_core.models.enums import DayStatusKind
from core.screw_core.repos.day_status_repo import DayStatusRepo
from core.screw_core.repos.users_repo import UsersRepo
from core.screw_core.services.day_status import DayStatusService, is_blocking_absence_range_overlap
from core.screw_core.utils.logging import get_logger

logger = get_logger(__name__)
router = Router(name="attendance_user_status")


class StatusCreationStates(StatesGroup):
    choosing_type = State()
    choosing_start_date = State()
    choosing_end_date = State()
    entering_comment = State()
    confirming = State()


STATUS_TYPE_LABELS = {
    "vacation": ("✈️ Отпуск", DayStatusKind.VACATION),
    "sick": ("🏥 Больничный", DayStatusKind.SICK),
    "leave": ("🕓 Отгул", DayStatusKind.LEAVE),
}


async def _reject_if_group_chat(callback: CallbackQuery) -> bool:
    if callback.message and callback.message.chat.type == "private":
        return False
    logger.info(
        "user_status_blocked_in_group: user=%s chat=%s data=%s",
        callback.from_user.id if callback.from_user else None,
        callback.message.chat.id if callback.message else None,
        callback.data,
    )
    await callback.answer(MSG_PRIVATE_ONLY, show_alert=True)
    return True


async def _edit(callback: CallbackQuery, text: str, reply_markup=None, parse_mode: str | None = "HTML") -> None:
    if not callback.message:
        return
    await callback.message.edit_text(
        text,
        reply_markup=to_aiogram_markup(reply_markup),
        parse_mode=parse_mode,
    )


async def _edit_reply_markup(callback: CallbackQuery, reply_markup=None) -> None:
    if not callback.message:
        return
    await callback.message.edit_reply_markup(reply_markup=to_aiogram_markup(reply_markup))


async def _get_status_dates_for_month(telegram_id: int, year: int, month: int) -> set[str]:
    from calendar import monthrange

    user = await UsersRepo.get_by_telegram_id(telegram_id)
    if not user:
        return set()
    start = date(year, month, 1)
    _, last_day = monthrange(year, month)
    end = date(year, month, last_day)
    statuses = await DayStatusRepo.get_user_statuses_range(user["id"], start, end)
    return {s["status_date"].isoformat() for s in statuses if s.get("status_date")}


def _status_kind_label(kind: str) -> str:
    kind_labels = {
        DayStatusKind.VACATION.value: "✈️ Отпуск",
        DayStatusKind.SICK.value: "🏥 Больничный",
        DayStatusKind.LEAVE.value: "🕓 Отгул",
        DayStatusKind.DAY_OFF.value: "📅 Выходной",
    }
    return kind_labels.get(kind, kind)


def _get_end_of_year() -> date:
    return date(date.today().year, 12, 31)


async def _get_user_statuses(user_id: int) -> list[dict]:
    today = date.today()
    end_date = _get_end_of_year()
    statuses_dict = await DayStatusRepo.get_statuses_for_date_range(
        start_date=today,
        end_date=end_date,
        user_ids=[user_id],
    )

    statuses = []
    for (uid, status_date), kind in statuses_dict.items():
        if uid == user_id and kind in (
            DayStatusKind.VACATION.value,
            DayStatusKind.SICK.value,
            DayStatusKind.LEAVE.value,
        ):
            statuses.append({"date": status_date, "kind": kind})
    statuses.sort(key=lambda s: s["date"])
    return statuses


async def _render_my_statuses(callback: CallbackQuery, user_id: int, page: int = 0, is_privileged: bool = False) -> None:
    today = date.today()
    end_date = _get_end_of_year()
    statuses = await _get_user_statuses(user_id)

    if statuses:
        text = (
            f"<b>{MY_STATUSES_TITLE}</b>\n\n"
            f"До конца года ({today.strftime('%d.%m')} — {end_date.strftime('%d.%m')}):\n\n"
            "Нажмите на статус для просмотра или отмены."
        )
    else:
        text = (
            f"<b>{MY_STATUSES_TITLE}</b>\n\n"
            "У вас нет запланированных отсутствий.\n\n"
            "Нажмите «➕ Сообщить об отсутствии» чтобы\n"
            "оформить отпуск, больничный или отгул."
        )

    await _edit(
        callback,
        text,
        my_statuses_keyboard(statuses, page=page, is_privileged=is_privileged),
    )


@router.callback_query(F.data == "status:create")
async def callback_status_create(callback: CallbackQuery, state: FSMContext) -> None:
    if not callback.from_user:
        return
    if await _reject_if_group_chat(callback):
        return

    telegram_id = callback.from_user.id
    user = await UsersRepo.get_by_telegram_id(telegram_id)
    if not user:
        await callback.answer("Вы не зарегистрированы в системе.", show_alert=True)
        return

    await state.clear()
    await state.update_data(user_id=user["id"], telegram_id=telegram_id)
    await state.set_state(StatusCreationStates.choosing_type)
    await _edit(
        callback,
        "➕ <b>Создание запроса</b>\n\n"
        "Выберите тип:",
        status_type_keyboard(),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("status:quick:"))
async def callback_status_quick(callback: CallbackQuery, state: FSMContext) -> None:
    if not callback.from_user:
        return
    if await _reject_if_group_chat(callback):
        return

    telegram_id = callback.from_user.id
    user = await UsersRepo.get_by_telegram_id(telegram_id)
    if not user:
        await callback.answer("Вы не зарегистрированы.", show_alert=True)
        return

    action = callback.data.split(":")[-1] if callback.data else ""
    user_id = user["id"]
    today = date.today()

    try:
        if action == "leave_tomorrow":
            tomorrow = today + timedelta(days=1)
            existing = await DayStatusRepo.get_statuses_for_date_range(
                start_date=tomorrow,
                end_date=tomorrow,
                user_ids=[user_id],
            )
            if existing:
                for (uid, _status_date), kind in existing.items():
                    if uid == user_id:
                        kind_label = STATUS_TYPE_LABELS.get(kind.lower(), (kind, None))[0] if kind.lower() in STATUS_TYPE_LABELS else kind
                        await _edit(
                            callback,
                            f"⛔ На <b>{tomorrow.strftime('%d.%m.%Y')}</b> уже есть статус: {kind_label}\n\n"
                            "Сначала отмените существующий статус в разделе «Мой календарь».",
                            user_back_keyboard(),
                        )
                        await callback.answer()
                        return
            await DayStatusService.set_leave(user_id=user_id, leave_date=tomorrow, created_by=user_id)
            text = f"✅ 🕓 Отгул на <b>{tomorrow.strftime('%d.%m.%Y')}</b> создан."
        else:
            await callback.answer("Неизвестное действие", show_alert=True)
            return

        await _edit(callback, text, user_back_keyboard())
        await state.clear()
        await callback.answer("✅ Статус создан")
    except Exception as exc:
        logger.error("aiogram quick_status failed: %s", exc)
        await _edit(callback, "❌ Ошибка при создании статуса. Попробуйте позже.", user_back_keyboard())
        await callback.answer()


@router.callback_query(StatusCreationStates.choosing_type, F.data.startswith("status:type:"))
async def callback_status_type(callback: CallbackQuery, state: FSMContext) -> None:
    if not callback.data:
        return
    status_type = callback.data.split(":")[-1]
    if status_type not in STATUS_TYPE_LABELS:
        await callback.answer("Неизвестный тип статуса.", show_alert=True)
        return

    label, kind = STATUS_TYPE_LABELS[status_type]
    await state.update_data(status_type=status_type, kind=kind.value, label=label)

    today = date.today()
    telegram_id = callback.from_user.id if callback.from_user else 0
    status_dates = await _get_status_dates_for_month(telegram_id, today.year, today.month)

    if status_type == "leave":
        await _edit(
            callback,
            f"{label}\n\n"
            "📅 Выберите дату отгула:",
            calendar_keyboard(today.year, today.month, status_dates=status_dates),
        )
    else:
        await _edit(
            callback,
            f"{label}\n\n"
            "📅 Выберите <b>дату начала</b>:\n\n"
            "Выходные дни тоже можно выбирать.",
            calendar_keyboard(today.year, today.month, status_dates=status_dates),
        )
    await state.set_state(StatusCreationStates.choosing_start_date)
    await callback.answer()


@router.callback_query(
    StatusCreationStates.choosing_start_date,
    F.data.startswith("cal:prev:") | F.data.startswith("cal:next:"),
)
@router.callback_query(
    StatusCreationStates.choosing_end_date,
    F.data.startswith("cal:prev:") | F.data.startswith("cal:next:"),
)
async def callback_cal_nav(callback: CallbackQuery, state: FSMContext) -> None:
    if not callback.data:
        return
    parts = callback.data.split(":")
    year, month = int(parts[2]), int(parts[3])
    direction = parts[1]

    if direction == "prev":
        if month == 1:
            year -= 1
            month = 12
        else:
            month -= 1
    else:
        if month == 12:
            year += 1
            month = 1
        else:
            month += 1

    data = await state.get_data()
    selected = data.get("start_date")
    telegram_id = callback.from_user.id if callback.from_user else 0
    status_dates = await _get_status_dates_for_month(telegram_id, year, month)

    await _edit_reply_markup(
        callback,
        calendar_keyboard(year, month, selected, status_dates=status_dates),
    )
    await callback.answer()


@router.callback_query(
    StatusCreationStates.choosing_start_date,
    F.data == "cal:ignore",
)
@router.callback_query(
    StatusCreationStates.choosing_end_date,
    F.data == "cal:ignore",
)
async def callback_cal_ignore(callback: CallbackQuery) -> None:
    await callback.answer()


@router.callback_query(StatusCreationStates.choosing_start_date, F.data.startswith("cal:day:"))
async def callback_cal_day_start(callback: CallbackQuery, state: FSMContext) -> None:
    if not callback.data:
        return
    date_str = callback.data.split(":")[-1]
    try:
        selected_date = date.fromisoformat(date_str)
    except ValueError:
        await callback.answer("Некорректная дата.", show_alert=True)
        return

    data = await state.get_data()
    status_type = data.get("status_type")
    label = data.get("label", "")
    await state.update_data(start_date=date_str)

    if status_type == "leave":
        user_id = data.get("user_id")
        if user_id:
            existing = await DayStatusRepo.get_statuses_for_date_range(
                start_date=selected_date,
                end_date=selected_date,
                user_ids=[user_id],
            )
            if existing:
                for (uid, _status_date), kind in existing.items():
                    if uid == user_id:
                        kind_label = STATUS_TYPE_LABELS.get(kind.lower(), (kind, None))[0] if kind.lower() in STATUS_TYPE_LABELS else kind
                        await _edit(
                            callback,
                            f"⛔ На <b>{selected_date.strftime('%d.%m.%Y')}</b> уже есть статус: {kind_label}\n\n"
                            "Сначала отмените существующий статус в разделе «Мой календарь», затем создайте новый.",
                            status_cancel_keyboard(),
                        )
                        await state.clear()
                        await callback.answer()
                        return

        await _edit(
            callback,
            f"{label}\n\n"
            f"📅 Дата: <b>{selected_date.strftime('%d.%m.%Y')}</b>\n\n"
            "💬 Введите комментарий (или нажмите Пропустить):",
            status_skip_comment_keyboard(),
        )
        await state.set_state(StatusCreationStates.entering_comment)
        await callback.answer()
        return

    await _edit(
        callback,
        f"{label}\n\n"
        f"📅 Начало: <b>{selected_date.strftime('%d.%m.%Y')}</b>\n\n"
        "📅 Выберите <b>дату окончания</b>:\n\n"
        "Выходные дни тоже можно выбирать.",
        calendar_keyboard(selected_date.year, selected_date.month, date_str),
    )
    await state.set_state(StatusCreationStates.choosing_end_date)
    await callback.answer()


@router.callback_query(StatusCreationStates.choosing_end_date, F.data.startswith("cal:day:"))
async def callback_cal_day_end(callback: CallbackQuery, state: FSMContext) -> None:
    if not callback.data:
        return
    date_str = callback.data.split(":")[-1]
    try:
        end_date = date.fromisoformat(date_str)
    except ValueError:
        await callback.answer("Некорректная дата.", show_alert=True)
        return

    data = await state.get_data()
    start_date_str = data.get("start_date")
    start_date = date.fromisoformat(start_date_str) if start_date_str else date.today()
    label = data.get("label", "")

    if end_date < start_date:
        await callback.answer("Дата окончания не может быть раньше даты начала.", show_alert=True)
        return

    await state.update_data(end_date=date_str)
    days_count = (end_date - start_date).days + 1

    user_id = data.get("user_id")
    if user_id:
        existing = await DayStatusRepo.get_statuses_for_date_range(
            start_date=start_date,
            end_date=end_date,
            user_ids=[user_id],
        )
        if existing:
            overlap_dates = []
            for (uid, status_date), kind in existing.items():
                if uid == user_id and is_blocking_absence_range_overlap(kind):
                    kind_label = STATUS_TYPE_LABELS.get(kind.lower(), (kind, None))[0] if kind.lower() in STATUS_TYPE_LABELS else kind
                    overlap_dates.append(f"{status_date.strftime('%d.%m')} ({kind_label})")
            if overlap_dates:
                overlap_text = "\n".join(f"  • {item}" for item in overlap_dates[:10])
                if len(overlap_dates) > 10:
                    overlap_text += f"\n  … и ещё {len(overlap_dates) - 10}"
                await _edit(
                    callback,
                    f"⛔ На выбранные даты уже есть статусы:\n{overlap_text}\n\n"
                    "Сначала отмените существующие статусы в разделе «Мой календарь», затем создайте новый.",
                    status_cancel_keyboard(),
                )
                await state.clear()
                await callback.answer()
                return

    await _edit(
        callback,
        f"{label}\n\n"
        f"📅 Начало: <b>{start_date.strftime('%d.%m.%Y')}</b>\n"
        f"📅 Окончание: <b>{end_date.strftime('%d.%m.%Y')}</b>\n"
        f"📊 Всего дней: <b>{days_count}</b>\n\n"
        "Подтвердите создание:",
        status_confirm_keyboard(),
    )
    await state.set_state(StatusCreationStates.confirming)
    await callback.answer()


@router.callback_query(StatusCreationStates.entering_comment, F.data == "status:skip_comment")
async def callback_skip_comment(callback: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    label = data.get("label", "")
    start_date_str = data.get("start_date")
    start_date = date.fromisoformat(start_date_str) if start_date_str else date.today()
    await state.update_data(comment=None)

    await _edit(
        callback,
        f"{label}\n\n"
        f"📅 Дата: <b>{start_date.strftime('%d.%m.%Y')}</b>\n\n"
        "Подтвердите создание:",
        status_confirm_keyboard(),
    )
    await state.set_state(StatusCreationStates.confirming)
    await callback.answer()


@router.message(StatusCreationStates.entering_comment, F.text)
async def message_comment(message: Message, state: FSMContext) -> None:
    if not message.text:
        return
    if message.text.startswith("/"):
        await state.clear()
        await message.answer("Создание статуса отменено.")
        return

    comment = message.text.strip()[:200]
    await state.update_data(comment=comment)
    data = await state.get_data()
    label = data.get("label", "")
    start_date_str = data.get("start_date")
    start_date = date.fromisoformat(start_date_str) if start_date_str else date.today()

    await message.answer(
        f"{label}\n\n"
        f"📅 Дата: <b>{start_date.strftime('%d.%m.%Y')}</b>\n"
        f"💬 Комментарий: <i>{comment}</i>\n\n"
        "Подтвердите создание:",
        reply_markup=to_aiogram_markup(status_confirm_keyboard()),
        parse_mode="HTML",
    )
    await state.set_state(StatusCreationStates.confirming)


@router.callback_query(StatusCreationStates.confirming, F.data == "status:confirm")
async def callback_status_confirm(callback: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    user_id = data.get("user_id")
    status_type = data.get("status_type")
    label = data.get("label", "")
    start_date_str = data.get("start_date")
    end_date_str = data.get("end_date")
    comment = data.get("comment")

    if not user_id or not start_date_str:
        await callback.answer("Ошибка данных. Попробуйте заново.", show_alert=True)
        await state.clear()
        return

    start_date = date.fromisoformat(start_date_str)
    end_date = date.fromisoformat(end_date_str) if end_date_str else start_date

    try:
        if status_type == "vacation":
            count = await DayStatusService.set_vacation(user_id, start_date, end_date, created_by=user_id)
        elif status_type == "sick":
            count = await DayStatusService.set_sick_leave(user_id, start_date, end_date, created_by=user_id)
        elif status_type == "leave":
            await DayStatusService.set_leave(user_id, start_date, created_by=user_id, comment=comment)
            count = 1
        else:
            await callback.answer("Неизвестный тип статуса.", show_alert=True)
            await state.clear()
            return

        logger.info(
            "aiogram user_status: created %s user_id=%s dates=%s..%s count=%d",
            status_type,
            user_id,
            start_date,
            end_date,
            count,
        )

        if count == 1:
            result_text = f"✅ {label} на <b>{start_date.strftime('%d.%m.%Y')}</b> создан."
        else:
            result_text = (
                f"✅ {label} создан.\n\n"
                f"📅 С <b>{start_date.strftime('%d.%m.%Y')}</b> "
                f"по <b>{end_date.strftime('%d.%m.%Y')}</b>\n"
                f"📊 Всего дней: <b>{count}</b>"
            )

        await _edit(callback, result_text, user_back_keyboard())
    except Exception as exc:
        logger.error("aiogram user_status: failed to create status: %s", exc)
        await _edit(callback, "❌ Ошибка при создании статуса. Попробуйте позже.", user_back_keyboard())

    await state.clear()
    await callback.answer("✅ Статус создан")


@router.callback_query(F.data == "status:cancel")
async def callback_status_cancel(callback: CallbackQuery, state: FSMContext) -> None:
    if not callback.from_user:
        return
    await state.clear()
    perms = await get_permissions(callback.from_user.id)
    await _edit(
        callback,
        USER_PANEL_TITLE,
        user_panel_keyboard(is_privileged=perms.is_manager or perms.is_admin),
    )
    await callback.answer("Создание отменено.")


@router.callback_query(F.data == "user:my_statuses")
async def callback_my_statuses(callback: CallbackQuery) -> None:
    if not callback.from_user:
        return
    if await _reject_if_group_chat(callback):
        return

    user = await UsersRepo.get_by_telegram_id(callback.from_user.id)
    if not user:
        await callback.answer("Вы не зарегистрированы в системе.", show_alert=True)
        return

    perms = await get_permissions(callback.from_user.id)
    await _render_my_statuses(callback, user["id"], page=0, is_privileged=perms.is_manager or perms.is_admin)
    await callback.answer()


@router.callback_query(F.data.startswith("user:my_statuses:page:"))
async def callback_my_statuses_page(callback: CallbackQuery) -> None:
    if not callback.from_user or not callback.data:
        return
    if await _reject_if_group_chat(callback):
        return

    try:
        page = int(callback.data.split(":")[3])
    except (ValueError, IndexError):
        await callback.answer("Ошибка навигации", show_alert=True)
        return

    user = await UsersRepo.get_by_telegram_id(callback.from_user.id)
    if not user:
        await callback.answer("Вы не зарегистрированы в системе.", show_alert=True)
        return

    perms = await get_permissions(callback.from_user.id)
    await _render_my_statuses(callback, user["id"], page=page, is_privileged=perms.is_manager or perms.is_admin)
    await callback.answer()


@router.callback_query(F.data.startswith("status:view:"))
async def callback_status_view(callback: CallbackQuery) -> None:
    if not callback.from_user or not callback.data:
        return

    date_str = callback.data.split(":")[-1]
    try:
        status_date = date.fromisoformat(date_str)
    except ValueError:
        await callback.answer("Некорректная дата.", show_alert=True)
        return

    user = await UsersRepo.get_by_telegram_id(callback.from_user.id)
    if not user:
        await callback.answer("Вы не зарегистрированы в системе.", show_alert=True)
        return

    status = await DayStatusRepo.get_status(user["id"], status_date)
    if not status:
        await callback.answer("Статус не найден.", show_alert=True)
        return

    label = _status_kind_label(status.get("kind", ""))
    comment = status.get("comment", "")
    text = f"<b>{label}</b>\n\n" f"📅 Дата: <b>{status_date.strftime('%d.%m.%Y')}</b>\n"
    if comment:
        text += f"💬 Комментарий: <i>{comment}</i>\n"
    text += "\n🗑 Удалить этот статус?"

    await _edit(callback, text, status_delete_confirm_keyboard(date_str))
    await callback.answer()


@router.callback_query(F.data.startswith("status:delete_confirm:"))
async def callback_status_delete_confirm(callback: CallbackQuery) -> None:
    if not callback.from_user or not callback.data:
        return

    date_str = callback.data.split(":")[-1]
    try:
        status_date = date.fromisoformat(date_str)
    except ValueError:
        await callback.answer("Некорректная дата.", show_alert=True)
        return

    user = await UsersRepo.get_by_telegram_id(callback.from_user.id)
    if not user:
        await callback.answer("Вы не зарегистрированы в системе.", show_alert=True)
        return

    try:
        deleted = await DayStatusService.cancel_status(user["id"], status_date)
        if deleted:
            logger.info("aiogram user_status: deleted status user_id=%s date=%s", user["id"], status_date)
            await callback.answer(f"✅ Статус на {status_date.strftime('%d.%m.%Y')} отменён.")
            perms = await get_permissions(callback.from_user.id)
            await _render_my_statuses(callback, user["id"], page=0, is_privileged=perms.is_manager or perms.is_admin)
            return

        await _edit(callback, "⚠️ Статус не найден или уже удалён.", user_back_keyboard())
        await callback.answer()
    except Exception as exc:
        logger.error("aiogram user_status: failed to delete status: %s", exc)
        await _edit(callback, "❌ Ошибка при отмене статуса. Попробуйте позже.", user_back_keyboard())
        await callback.answer()


@router.callback_query(F.data.startswith("status:delete:"))
async def callback_status_delete(callback: CallbackQuery) -> None:
    if not callback.from_user or not callback.data:
        return

    date_str = callback.data.split(":")[-1]
    try:
        status_date = date.fromisoformat(date_str)
    except ValueError:
        await callback.answer("Некорректная дата.", show_alert=True)
        return

    user = await UsersRepo.get_by_telegram_id(callback.from_user.id)
    if not user:
        await callback.answer("Вы не зарегистрированы в системе.", show_alert=True)
        return

    status = await DayStatusRepo.get_status(user["id"], status_date)
    if not status:
        await callback.answer("Статус не найден.", show_alert=True)
        return

    await _edit(
        callback,
        f"🗑 Удалить <b>{_status_kind_label(status.get('kind', ''))}</b> "
        f"на <b>{status_date.strftime('%d.%m.%Y')}</b>?",
        status_delete_confirm_keyboard(date_str),
    )
    await callback.answer()


@router.callback_query(F.data == "status:clear_all")
async def callback_status_clear_all(callback: CallbackQuery) -> None:
    if not callback.from_user:
        return
    if await _reject_if_group_chat(callback):
        return

    user = await UsersRepo.get_by_telegram_id(callback.from_user.id)
    if not user:
        await callback.answer("Вы не зарегистрированы в системе.", show_alert=True)
        return

    statuses = await _get_user_statuses(user["id"])
    if not statuses:
        await callback.answer("Нет статусов для отмены.", show_alert=True)
        return

    today = date.today()
    end_date = _get_end_of_year()
    await _edit(
        callback,
        "⚠️ <b>Отменить все статусы?</b>\n\n"
        f"Будет отменено: <b>{len(statuses)}</b> статус(ов)\n"
        f"Период: {today.strftime('%d.%m')} — {end_date.strftime('%d.%m')}\n\n"
        "Это действие нельзя отменить.",
        status_clear_all_confirm_keyboard(),
    )
    await callback.answer()


@router.callback_query(F.data == "status:clear_all:confirm")
async def callback_status_clear_all_confirm(callback: CallbackQuery) -> None:
    if not callback.from_user:
        return
    if await _reject_if_group_chat(callback):
        return

    user = await UsersRepo.get_by_telegram_id(callback.from_user.id)
    if not user:
        await callback.answer("Вы не зарегистрированы в системе.", show_alert=True)
        return

    today = date.today()
    end_date = _get_end_of_year()
    try:
        deleted_count = await DayStatusService.cancel_all_statuses(
            user_id=user["id"],
            start_date=today,
            end_date=end_date,
        )
        logger.info("aiogram user_status: mass cancel user_id=%s deleted=%d", user["id"], deleted_count)
        await callback.answer(f"✅ Отменено статусов: {deleted_count}" if deleted_count else "Нет статусов для отмены.")
    except Exception as exc:
        logger.error("aiogram user_status: failed to mass cancel: %s", exc)
        await _edit(callback, "❌ Ошибка при отмене статусов. Попробуйте позже.", user_back_keyboard())
        await callback.answer()
        return

    perms = await get_permissions(callback.from_user.id)
    await _render_my_statuses(callback, user["id"], page=0, is_privileged=perms.is_manager or perms.is_admin)
