"""Runtime-neutral view helpers for /start and user dashboard."""

from __future__ import annotations

from datetime import date, timedelta

from apps.attendance_bot.app.ui.keyboards_neutral import USER_PANEL_TITLE
from core.screw_core.models.enums import AttendanceChoice, DayStatusKind
from core.screw_core.repos.attendance_repo import AttendanceRepo
from core.screw_core.repos.day_status_repo import DayStatusRepo
from core.screw_core.repos.registration_repo import RegistrationRepo
from core.screw_core.repos.users_repo import UsersRepo
from core.screw_core.utils.logging import get_logger

logger = get_logger(__name__)


STATUS_LABELS: dict[str, str] = {
    DayStatusKind.VACATION.value: "✈️ Отпуск",
    DayStatusKind.SICK.value: "🏥 Больничный",
    DayStatusKind.LEAVE.value: "🕓 Отгул",
    DayStatusKind.DAY_OFF.value: "📅 Выходной",
}

ATTENDANCE_CHOICE_LABELS: dict[str, str] = {
    AttendanceChoice.EVOLUTION.value: "🏢 Офис (Эволюция)",
    AttendanceChoice.CSKA.value: "🏢 Офис (ЦСКА)",
    AttendanceChoice.REMOTE.value: "🏠 Удалёнка",
}

WEEKDAY_NAMES_SHORT = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]

WORK_MODE_LABELS: dict[str, str] = {
    "HYBRID": "🏢 Гибрид",
    "HYBRID_PLUS": "🏢+ Гибрид+",
    "FULL_REMOTE": "🏠 Полная удалёнка",
    "DUTY": "⏰ Дежурство",
}


def build_help_text_user() -> str:
    """Помощь для Мой кабинет (пользователь)."""
    return (
        f"🆘 <b>Помощь — {USER_PANEL_TITLE}</b>\n\n"
        "<b>Что можно:</b>\n"
        "• 📅 Сегодня — посмотреть свой статус на сегодня\n"
        "• 🗓 Месяц — статусы за месяц (переключение ◀️/▶️)\n"
        "• 📆 Мой календарь — просмотр и отмена отпусков/больничных/отгулов\n"
        "• ➕ Сообщить об отсутствии — создать отпуск/больничный/отгул\n\n"
        "<b>Как работает:</b>\n"
        "• Бот отправляет ежедневный опрос явки в рабочую группу\n"
        "• Выберите, где вы сегодня работаете (офис или удалёнка)\n"
        "• Если вы в отпуске/на больничном — опрос можно пропустить\n\n"
        "<b>Типовые сценарии:</b>\n"
        "• Уходите в отпуск → «Сообщить об отсутствии» → Отпуск → даты\n"
        "• Заболели → «Сообщить об отсутствии» → Больничный → даты\n"
        "• Нужен отгул → «Сообщить об отсутствии» → Отгул → дата\n\n"
        "<b>Если что-то не работает:</b>\n"
        "Обратитесь к руководителю вашего отдела."
    )


def build_help_text_manager() -> str:
    """Помощь для панели руководителя."""
    return (
        "🆘 <b>Помощь — Руководитель</b>\n\n"
        "<b>Что можно:</b>\n"
        "• 📝 Заявки — просмотр и обработка заявок на регистрацию\n"
        "• 👥 Список пользователей — сотрудники вашего отдела\n"
        "• 🔗 Ссылка приглашение — создать приглашение для нового сотрудника\n"
        "• 🚫 Отозвать доступ — деактивировать сотрудника\n"
        "• ✅ Восстановить доступ — вернуть доступ сотруднику\n\n"
        "<b>Как работает регистрация:</b>\n"
        "1. Создайте ссылку-приглашение (действует 30 минут)\n"
        "2. Отправьте ссылку новому сотруднику\n"
        "3. Сотрудник переходит по ссылке в личку бота\n"
        "4. Сотрудник вводит ФИО\n"
        "5. Вы получаете заявку и одобряете её\n\n"
        "<b>Правила одобрения:</b>\n"
        "• Вы можете одобрить только заявки по своим приглашениям\n"
        "• ОЗКО: выбирается режим работы (гибрид/удалёнка/дежурство)\n"
        "• ОСБ: режим работы назначается автоматически\n\n"
        "<b>Если что-то не работает:</b>\n"
        "Обратитесь к администратору системы."
    )


def build_help_text_admin() -> str:
    """Помощь для админ-панели."""
    return (
        "🆘 <b>Помощь — Админ</b>\n\n"
        "<b>Опросы:</b>\n"
        "• 📋 Создать опрос — создать/пересоздать ежедневный опрос\n"
        "• ▶️ Закрыть опрос — принудительно закрыть активный опрос\n"
        "• 🔄 Перепроверить — повторный запуск проверок за сегодня\n\n"
        "<b>Пользователи:</b>\n"
        "• 👥 Список пользователей — все сотрудники по отделам\n"
        "• 🚫 Отозвать доступ — деактивировать любого сотрудника\n"
        "• ✅ Восстановить доступ — вернуть доступ сотруднику\n"
        "• 👔 Назначить/снять руководителя — управление ролями\n\n"
        "<b>Заявки:</b>\n"
        "• 📨 Ожидающие заявки — все заявки на регистрацию\n"
        "• 🔗 Ссылка приглашение — создать приглашение (любой отдел)\n\n"
        "<b>Отчёты:</b>\n"
        "• 📄 Дневной отчёт — отправить дневной отчёт по email\n"
        "• 📆 Месячный отчёт — отправить месячный отчёт по email\n\n"
        "<b>Система:</b>\n"
        "• ⚠️ Статус системы — состояние сервисов\n\n"
        "<b>Если что-то не работает:</b>\n"
        "Проверьте логи: docker logs screw-attendance-bot"
    )


def day_suffix(n: int) -> str:
    """Сокращённое склонение слова день."""
    if 11 <= n % 100 <= 19:
        return "дн."
    r = n % 10
    if r == 1:
        return "дн."
    if 2 <= r <= 4:
        return "дн."
    return "дн."


async def get_pending_count(perms) -> int:
    """Получить количество pending-заявок для менеджера."""
    if not perms.is_manager and not perms.is_admin:
        return 0
    try:
        pending = await RegistrationRepo.get_pending_requests(
            department_id=perms.department_id if not perms.is_admin else None,
        )
        return len(pending)
    except Exception as exc:
        logger.debug("get_pending_count failed: %s", exc)
        return 0


async def build_dashboard_text(telegram_id: int) -> str:
    """Построить текст мини-дашборда для пользователя."""
    today = date.today()
    weekday = WEEKDAY_NAMES_SHORT[today.weekday()]

    text = f"{USER_PANEL_TITLE}\n\n"

    user = await UsersRepo.get_by_telegram_id(telegram_id)
    if not user:
        return text

    user_id = user["id"]

    status = await DayStatusRepo.get_status(user_id, today)
    if status:
        kind = status.get("kind", "")
        status_label = STATUS_LABELS.get(kind, kind)
        text += f"📅 Сегодня ({weekday}): {status_label}\n"
    else:
        text += f"📅 Сегодня ({weekday}): обычный рабочий день\n"

    mark = await AttendanceRepo.get_mark(user_id, today)
    if mark:
        choice = mark.get("choice", "")
        choice_label = ATTENDANCE_CHOICE_LABELS.get(choice, choice)
        text += f"✅ Опрос: {choice_label}\n"
    else:
        text += "⏳ Опрос: не отмечено\n"

    try:
        upcoming = await DayStatusRepo.get_statuses_for_date_range(
            start_date=today + timedelta(days=1),
            end_date=today + timedelta(days=90),
            user_ids=[user_id],
        )
        if upcoming:
            first_date = min(dt for (uid, dt), _kind in upcoming.items() if uid == user_id)
            first_kind = upcoming[(user_id, first_date)]
            kind_label = STATUS_LABELS.get(first_kind, first_kind)
            text += f"\n📌 Ближайшее: {kind_label} — {first_date.strftime('%d.%m')}"
    except Exception as exc:
        logger.debug("dashboard upcoming status failed: %s", exc)

    return text
