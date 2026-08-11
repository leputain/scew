"""
Runtime-neutral клавиатуры для attendance_bot.

Inline и Reply клавиатуры для взаимодействия с пользователями.
"""

from datetime import date as date_type
from typing import NamedTuple

from apps.attendance_bot.app.ui.keyboard_spec import (
    KeyboardButtonSpec as InlineKeyboardButton,
    KeyboardMarkupSpec as InlineKeyboardMarkup,
)

from core.screw_core.models.enums import AttendanceChoice, WorkMode


# =============================================================================
# Константы UI — единый источник истины для названий
# =============================================================================

# Emoji для "Мой кабинет" (единая точка изменения)
USER_PANEL_EMOJI = "🗂"
USER_PANEL_TITLE = f"{USER_PANEL_EMOJI} Мой кабинет"

# Название раздела "Мои статусы" → "Мой календарь"
MY_STATUSES_TITLE = "📆 Мой календарь"

# Названия месяцев на русском (индекс 0 не используется)
MONTH_NAMES_RU = [
    "", "Январь", "Февраль", "Март", "Апрель", "Май", "Июнь",
    "Июль", "Август", "Сентябрь", "Октябрь", "Ноябрь", "Декабрь"
]

# Пагинация
STATUSES_PER_PAGE = 10


def format_month_ru(year: int, month: int) -> str:
    """Форматировать месяц по-русски: 'Январь 2026'."""
    return f"{MONTH_NAMES_RU[month]} {year}"


# =============================================================================
# Helper: навигационный footer для пользовательских экранов
# =============================================================================


def build_user_nav_footer(
    *,
    show_back: bool = True,
    show_home: bool = True,
    back_target: str = "user",
    is_privileged: bool = False,
) -> list[list[InlineKeyboardButton]]:
    """
    Построить footer навигации для пользовательских экранов.

    Args:
        show_back: Показать кнопку "Назад".
        show_home: Показать кнопку "Главное меню".
        back_target: Цель для кнопки "Назад".
        is_privileged: Не используется, оставлен для обратной совместимости.

    Returns:
        Список рядов с кнопками.
    """
    row = []
    if show_back:
        row.append(InlineKeyboardButton(text="⬅️ Назад", callback_data=f"nav:back:{back_target}"))
    if show_home:
        row.append(InlineKeyboardButton(text="🏠 Главное меню", callback_data="nav:home"))

    return [row] if row else []


# =============================================================================
# Helper: сжатие дней в диапазоны
# =============================================================================


class DateRange(NamedTuple):
    """Диапазон дат с одинаковым статусом."""

    start: date_type
    end: date_type
    kind: str


def compress_days_to_ranges(days_with_status: list[tuple[date_type, str]]) -> list[DateRange]:
    """
    Сжать список дней в диапазоны подряд идущих дней с одинаковым статусом.

    Args:
        days_with_status: Список кортежей (дата, kind), отсортированный по дате.

    Returns:
        Список диапазонов DateRange.
    """
    if not days_with_status:
        return []

    # Сортируем на всякий случай
    sorted_days = sorted(days_with_status, key=lambda x: x[0])

    ranges: list[DateRange] = []
    current_start = sorted_days[0][0]
    current_end = sorted_days[0][0]
    current_kind = sorted_days[0][1]

    from datetime import timedelta

    for day, kind in sorted_days[1:]:
        # Если тот же статус и следующий день — расширяем диапазон
        if kind == current_kind and day == current_end + timedelta(days=1):
            current_end = day
        else:
            # Сохраняем текущий диапазон и начинаем новый
            ranges.append(DateRange(start=current_start, end=current_end, kind=current_kind))
            current_start = day
            current_end = day
            current_kind = kind

    # Добавляем последний диапазон
    ranges.append(DateRange(start=current_start, end=current_end, kind=current_kind))

    return ranges


def format_date_range(range_item: DateRange) -> str:
    """
    Форматировать диапазон дат для отображения.

    Одиночный день: "05"
    Диапазон: "02–09"
    """
    if range_item.start == range_item.end:
        return range_item.start.strftime("%d")
    return f"{range_item.start.strftime('%d')}–{range_item.end.strftime('%d')}"


def pending_request_keyboard(request_id: int) -> InlineKeyboardMarkup:
    """
    Клавиатура для заявки на рассмотрении.

    Args:
        request_id: ID заявки.

    Returns:
        Inline клавиатура с кнопками Approve/Reject.
    """
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✅ Подтвердить",
                    callback_data=f"reg_approve:{request_id}",
                ),
                InlineKeyboardButton(
                    text="❌ Отклонить",
                    callback_data=f"reg_reject:{request_id}",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="⬅️ Назад к списку",
                    callback_data="reg_list",
                ),
            ],
        ]
    )


def work_mode_keyboard(request_id: int) -> InlineKeyboardMarkup:
    """
    Клавиатура выбора режима работы при подтверждении.

    Args:
        request_id: ID заявки.

    Returns:
        Inline клавиатура с вариантами work_mode.
    """
    modes = [
        (WorkMode.HYBRID, "🏢 Гибрид"),
        (WorkMode.HYBRID_PLUS, "🏢+ Гибрид+"),
        (WorkMode.FULL_REMOTE, "🏠 Полная удалёнка"),
        (WorkMode.DUTY, "⏰ Дежурство"),
    ]

    buttons = [
        [
            InlineKeyboardButton(
                text=label,
                callback_data=f"reg_mode:{request_id}:{mode.value}",
            )
        ]
        for mode, label in modes
    ]

    # Добавляем кнопку отмены
    buttons.append([
        InlineKeyboardButton(
            text="↩️ Назад",
            callback_data=f"reg_back:{request_id}",
        )
    ])

    return InlineKeyboardMarkup(inline_keyboard=buttons)


def confirm_reject_keyboard(request_id: int) -> InlineKeyboardMarkup:
    """
    Клавиатура подтверждения отклонения.

    Args:
        request_id: ID заявки.

    Returns:
        Inline клавиатура.
    """
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="❌ Отклонить без причины",
                    callback_data=f"reg_reject_confirm:{request_id}",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="↩️ Назад",
                    callback_data=f"reg_back:{request_id}",
                ),
            ],
        ]
    )


def back_to_pending_keyboard() -> InlineKeyboardMarkup:
    """
    Клавиатура возврата к списку заявок.

    Returns:
        Inline клавиатура.
    """
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="📋 К списку заявок",
                    callback_data="reg_list",
                ),
            ],
        ]
    )


def manager_panel_keyboard() -> InlineKeyboardMarkup:
    """
    Клавиатура панели менеджера.
    """
    return manager_panel_navigation_keyboard(back_target="home")


def manager_panel_navigation_keyboard(
    back_target: str,
    pending_count: int = 0,
) -> InlineKeyboardMarkup:
    """
    Клавиатура панели менеджера с навигацией.

    Args:
        back_target: Цель для кнопки "Назад".
        pending_count: Количество ожидающих заявок (для бейджа).
    """
    requests_label = "📝 Заявки"
    if pending_count > 0:
        requests_label += f" ({pending_count})"

    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=requests_label,
                    callback_data="mgr:pending",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="👥 Список пользователей",
                    callback_data="mgr:users",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="🔗 Создать приглашение",
                    callback_data="mgr:invite",
                ),
                InlineKeyboardButton(
                    text="📋 Приглашения",
                    callback_data="mgr:invites_list",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="📅 Отпуска отдела",
                    callback_data="mgr:dept_vacations",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="🚫 Отозвать доступ",
                    callback_data="access:revoke:manager",
                ),
                InlineKeyboardButton(
                    text="✅ Восстановить доступ",
                    callback_data="access:restore:manager",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="ℹ️ Помощь",
                    callback_data="nav:help:manager",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="⬅️ Назад",
                    callback_data=f"nav:back:{back_target}",
                ),
                InlineKeyboardButton(
                    text="🏠 Главное меню",
                    callback_data="nav:home",
                ),
            ],
        ]
    )


def root_menu_keyboard(
    show_manager: bool,
    show_admin: bool,
    pending_count: int = 0,
) -> InlineKeyboardMarkup:
    """
    Главное меню.

    UX: "Менеджер" → "Руководитель", "Пользователь" → "Мой кабинет"
    """
    rows = [
        [InlineKeyboardButton(text=USER_PANEL_TITLE, callback_data="panel:user")],
    ]

    if show_manager:
        label = "👔 Руководитель"
        if pending_count > 0:
            label += f" ({pending_count} заявк{'а' if pending_count == 1 else 'и' if 2 <= pending_count <= 4 else ''})"
        rows.append([InlineKeyboardButton(text=label, callback_data="panel:manager")])

    if show_admin:
        rows.append([InlineKeyboardButton(text="🦁 Админ", callback_data="panel:admin")])

    return InlineKeyboardMarkup(inline_keyboard=rows)


def navigation_keyboard(back_target: str) -> InlineKeyboardMarkup:
    """
    Универсальная навигация.
    """
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="⬅️ Назад", callback_data=f"nav:back:{back_target}"),
                InlineKeyboardButton(text="🏠 Главное меню", callback_data="nav:home"),
            ],
        ]
    )


def user_panel_keyboard(
    *,
    is_privileged: bool = False,
    is_ozko: bool = False,
) -> InlineKeyboardMarkup:
    """
    Клавиатура панели пользователя ("Мой кабинет").

    Args:
        is_privileged: True если пользователь MANAGER или ADMIN.
        is_ozko: True если пользователь из отдела ОЗКО.
    """
    rows = [
        [
            InlineKeyboardButton(text="📅 Сегодня", callback_data="user:today"),
            InlineKeyboardButton(text="🗓 Месяц", callback_data="user:month"),
        ],
        [
            InlineKeyboardButton(text=MY_STATUSES_TITLE, callback_data="user:my_statuses"),
        ],
        [
            InlineKeyboardButton(text="➕ Сообщить об отсутствии", callback_data="status:create"),
        ],
    ]

    # "Кто в офисе" и "Моя статистика" — только для ОЗКО
    if is_ozko:
        rows.append([
            InlineKeyboardButton(text="🏢 Кто в офисе", callback_data="user:who_in_office"),
            InlineKeyboardButton(text="📈 Моя статистика", callback_data="user:my_stats"),
        ])

    rows.append([InlineKeyboardButton(text="ℹ️ Помощь", callback_data="nav:help:user")])

    # Правило D: навигация только для MANAGER/ADMIN
    rows.extend(build_user_nav_footer(back_target="home", is_privileged=is_privileged))

    return InlineKeyboardMarkup(inline_keyboard=rows)


def user_status_navigation_keyboard(*, is_privileged: bool = False) -> InlineKeyboardMarkup:
    """
    Клавиатура навигации для экрана "Сегодня".

    Args:
        is_privileged: True если пользователь MANAGER или ADMIN.
    """
    rows = [
        [
            InlineKeyboardButton(text="🕓 Отгул завтра", callback_data="status:quick:leave_tomorrow"),
        ],
        [
            InlineKeyboardButton(text="🔄 Обновить", callback_data="user:today"),
            InlineKeyboardButton(text="🗓 Месяц", callback_data="user:month"),
        ],
    ]

    rows.extend(build_user_nav_footer(back_target="user", is_privileged=is_privileged))

    return InlineKeyboardMarkup(inline_keyboard=rows)


def user_month_navigation_keyboard(
    year: int,
    month: int,
    *,
    is_privileged: bool = False,
) -> InlineKeyboardMarkup:
    """
    Клавиатура навигации для экрана месячного просмотра.

    Включает переключение месяцев и кнопку "Сегодня".

    Args:
        year: Текущий год просмотра.
        month: Текущий месяц просмотра (1-12).
        is_privileged: True если пользователь MANAGER или ADMIN.
    """
    rows = [
        # Переключение месяцев
        [
            InlineKeyboardButton(text="◀️ Пред.", callback_data=f"user:month:nav:{year}:{month}:prev"),
            InlineKeyboardButton(text="Текущий месяц", callback_data="user:month:current"),
            InlineKeyboardButton(text="След. ▶️", callback_data=f"user:month:nav:{year}:{month}:next"),
        ],
        # Переход к Сегодня и обновление
        [
            InlineKeyboardButton(text="📅 Сегодня", callback_data="user:today"),
            InlineKeyboardButton(text="🔄 Обновить", callback_data="user:month:current"),
        ],
    ]

    rows.extend(build_user_nav_footer(back_target="user", is_privileged=is_privileged))

    return InlineKeyboardMarkup(inline_keyboard=rows)


def registration_cancel_keyboard() -> InlineKeyboardMarkup:
    """
    Клавиатура отмены регистрации (только кнопка Отмена).
    """
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="❌ Отмена", callback_data="reg:cancel"),
            ],
        ]
    )


# =============================================================================
# Клавиатуры для опросов явки (DEPRECATED — используем native Telegram polls)
# =============================================================================


def poll_keyboard(poll_id: int) -> InlineKeyboardMarkup:
    """
    Клавиатура опроса явки (DEPRECATED).

    DEPRECATED: С Task 04.1 используются native Telegram polls (send_poll).
    Эта функция оставлена для обратной совместимости, но не используется.

    Args:
        poll_id: ID опроса для callback_data.

    Returns:
        Inline клавиатура с кнопками выбора места работы.
    """
    choices = [
        (AttendanceChoice.EVOLUTION, "EVOLUTION"),
        (AttendanceChoice.CSKA, "CSKA"),
        (AttendanceChoice.REMOTE, "REMOTE"),
    ]

    buttons = [
        [
            InlineKeyboardButton(
                text=label,
                callback_data=f"poll:{poll_id}:{choice.value}",
            )
        ]
        for choice, label in choices
    ]

    return InlineKeyboardMarkup(inline_keyboard=buttons)


# =============================================================================
# Клавиатуры для создания статусов (отпуск/больничный/отгул)
# =============================================================================


def user_back_keyboard() -> InlineKeyboardMarkup:
    """
    Клавиатура с одной кнопкой "Назад" для возврата в Мой кабинет.

    Используется для USER после успешных операций,
    когда не нужно показывать "Главное меню".
    """
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="⬅️ Назад", callback_data="nav:back:user")],
        ]
    )


def status_type_keyboard() -> InlineKeyboardMarkup:
    """
    Клавиатура выбора типа статуса с быстрыми действиями.
    """
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="✈️ Отпуск", callback_data="status:type:vacation"),
            ],
            [
                InlineKeyboardButton(text="🏥 Больничный", callback_data="status:type:sick"),
            ],
            [
                InlineKeyboardButton(text="🕓 Отгул", callback_data="status:type:leave"),
                InlineKeyboardButton(text="🕓 Отгул завтра", callback_data="status:quick:leave_tomorrow"),
            ],
            [
                InlineKeyboardButton(text="⬅️ Назад", callback_data="status:cancel"),
            ],
        ]
    )


ALPHA_RANGES = [
    ("А-Д", "АБВГД"),
    ("Е-К", "ЕЖЗИЁЙК"),
    ("Л-П", "ЛМНОП"),
    ("Р-Я", "РСТУФХЦЧШЩЪЫЬЭЮЯ"),
]


def user_picker_keyboard(
    users: list[dict],
    action: str,
    back_target: str,
    page: int = 0,
    per_page: int = 8,
    *,
    grouped: bool = False,
    alpha_filter: str | None = None,
    search_query: str | None = None,
) -> InlineKeyboardMarkup:
    """
    Клавиатура выбора пользователя (вместо ввода Telegram ID).

    Args:
        users: Список пользователей с полями full_name, telegram_id, department_id.
        action: 'revoke' или 'restore'.
        back_target: Цель для кнопки "Назад".
        page: Текущая страница.
        per_page: Кол-во пользователей на странице.
        grouped: Группировать по отделам (заголовки-разделители).
        alpha_filter: Активный алфавитный фильтр (напр. "А-Д").
        search_query: Активный поисковый запрос (для индикации).
    """
    buttons: list[list[InlineKeyboardButton]] = []

    # Алфавитные табы
    alpha_row = []
    for label, _chars in ALPHA_RANGES:
        if alpha_filter == label:
            btn_text = f"•{label}•"
        else:
            btn_text = label
        alpha_row.append(
            InlineKeyboardButton(
                text=btn_text,
                callback_data=f"access:alpha:{action}:{label}:{back_target}",
            )
        )
    buttons.append(alpha_row)

    # Кнопка поиска
    search_label = f"🔍 «{search_query}»" if search_query else "🔍 Поиск"
    buttons.append([
        InlineKeyboardButton(
            text=search_label,
            callback_data=f"access:search:{action}:{back_target}",
        ),
    ])

    if grouped:
        # Группировка по отделам
        by_dept: dict[int, list[dict]] = {}
        for u in users:
            did = u.get("department_id", 0)
            by_dept.setdefault(did, []).append(u)

        for did in sorted(by_dept.keys()):
            dept_name = DEPT_NAMES.get(did, f"Отдел {did}")
            count = len(by_dept[did])
            buttons.append([
                InlineKeyboardButton(
                    text=f"— {dept_name} ({count}) —",
                    callback_data="nav:ignore",
                ),
            ])
            for u in by_dept[did]:
                full_name = u.get("full_name") or "—"
                tid = u.get("telegram_id", 0)
                buttons.append([
                    InlineKeyboardButton(
                        text=full_name,
                        callback_data=f"access:pick:{action}:{tid}:{back_target}",
                    ),
                ])
    else:
        # Плоский список с пагинацией
        total = len(users)
        total_pages = max(1, (total + per_page - 1) // per_page)
        page = max(0, min(page, total_pages - 1))

        start_idx = page * per_page
        end_idx = min(start_idx + per_page, total)
        page_users = users[start_idx:end_idx]

        for u in page_users:
            full_name = u.get("full_name") or "—"
            tid = u.get("telegram_id", 0)
            buttons.append([
                InlineKeyboardButton(
                    text=full_name,
                    callback_data=f"access:pick:{action}:{tid}:{back_target}",
                ),
            ])

        if total_pages > 1:
            pagination_row = []
            if page > 0:
                pagination_row.append(
                    InlineKeyboardButton(text="◀️", callback_data=f"access:picker_page:{action}:{page - 1}:{back_target}")
                )
            pagination_row.append(
                InlineKeyboardButton(text=f"Стр {page + 1}/{total_pages}", callback_data="nav:ignore")
            )
            if page < total_pages - 1:
                pagination_row.append(
                    InlineKeyboardButton(text="▶️", callback_data=f"access:picker_page:{action}:{page + 1}:{back_target}")
                )
            buttons.append(pagination_row)

    buttons.append([
        InlineKeyboardButton(text="⬅️ Назад", callback_data=f"nav:back:{back_target}"),
    ])

    return InlineKeyboardMarkup(inline_keyboard=buttons)


DEPT_NAMES = {1: "ОЗКО", 2: "ОСБ"}


def format_invite_status(invite: dict) -> tuple[str, str]:
    """
    Определить статус приглашения и иконку.

    Returns:
        (icon, description) — напр. ("🟢", "осталось 15 мин.")
    """
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc)
    used_by = invite.get("used_by")
    expires_at = invite.get("expires_at")

    if used_by is not None:
        used_name = invite.get("used_by_name") or "—"
        return "⚫", f"использовано → {used_name}"

    if expires_at and expires_at.tzinfo is None:
        from datetime import timezone as tz
        expires_at = expires_at.replace(tzinfo=tz.utc)

    if expires_at and expires_at <= now:
        return "🔴", "истекло"

    # Активное — показываем оставшееся время
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


def format_invites_text(
    invites: list[dict],
    title: str,
    *,
    group_by_dept: bool = False,
) -> str:
    """
    Форматировать список приглашений в текст.

    Args:
        invites: Список приглашений из RegistrationRepo.
        title: Заголовок экрана.
        group_by_dept: Группировать по отделам (для админа).
    """
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
        for inv in invites:
            did = inv.get("department_id", 0)
            by_dept.setdefault(did, []).append(inv)

        for did in sorted(by_dept.keys()):
            dept_name = DEPT_NAMES.get(did, f"Отдел {did}")
            lines.append(f"— <b>{dept_name}</b> —")
            for inv in by_dept[did]:
                icon, desc = format_invite_status(inv)
                creator = _shorten(inv.get("creator_name"))
                created = inv.get("created_at")
                date_str = created.strftime("%d.%m %H:%M") if created else ""
                lines.append(f"{icon} {desc} | {creator} | {date_str}")
            lines.append("")
    else:
        for inv in invites:
            icon, desc = format_invite_status(inv)
            creator = _shorten(inv.get("creator_name"))
            created = inv.get("created_at")
            date_str = created.strftime("%d.%m %H:%M") if created else ""
            lines.append(f"{icon} {desc} | {creator} | {date_str}")

    # Статистика
    active = sum(1 for i in invites if format_invite_status(i)[0] == "🟢")
    used = sum(1 for i in invites if i.get("used_by") is not None)
    expired = len(invites) - active - used
    lines.append(f"\nВсего: {len(invites)} (🟢 {active} | ⚫ {used} | 🔴 {expired})")

    return "\n".join(lines)


def invites_list_keyboard(back_target: str, show_create: bool = True) -> InlineKeyboardMarkup:
    """Клавиатура для экрана приглашений."""
    rows = []
    if show_create:
        rows.append([InlineKeyboardButton(
            text="🔗 Создать новое",
            callback_data="mgr:invite",
        )])
    rows.append([
        InlineKeyboardButton(text="⬅️ Назад", callback_data=f"nav:back:{back_target}"),
        InlineKeyboardButton(text="🏠 Главное меню", callback_data="nav:home"),
    ])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def status_overlap_confirm_keyboard() -> InlineKeyboardMarkup:
    """Клавиатура подтверждения при пересечении дат."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="✅ Да, заменить", callback_data="status:confirm"),
            ],
            [
                InlineKeyboardButton(text="❌ Отмена", callback_data="status:cancel"),
            ],
        ]
    )


def status_cancel_keyboard() -> InlineKeyboardMarkup:
    """
    Клавиатура отмены создания статуса.
    """
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="⬅️ Назад", callback_data="status:cancel"),
            ],
        ]
    )


def status_confirm_keyboard() -> InlineKeyboardMarkup:
    """
    Клавиатура подтверждения создания статуса.
    """
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="✅ Подтвердить", callback_data="status:confirm"),
            ],
            [
                InlineKeyboardButton(text="⬅️ Назад", callback_data="status:cancel"),
            ],
        ]
    )


def status_skip_comment_keyboard() -> InlineKeyboardMarkup:
    """
    Клавиатура пропуска комментария.
    """
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="⏭️ Пропустить", callback_data="status:skip_comment"),
            ],
            [
                InlineKeyboardButton(text="⬅️ Назад", callback_data="status:cancel"),
            ],
        ]
    )


def calendar_keyboard(
    year: int,
    month: int,
    selected_date: str | None = None,
    status_dates: set[str] | None = None,
) -> InlineKeyboardMarkup:
    """
    Клавиатура-календарь для выбора даты.

    Args:
        year: Год.
        month: Месяц (1-12).
        selected_date: Выбранная дата в формате YYYY-MM-DD (для выделения).
        status_dates: Набор дат (YYYY-MM-DD) с существующими статусами.

    Returns:
        Inline клавиатура с календарём.
    """
    import calendar
    from datetime import date as date_type

    # Названия месяцев на русском
    month_names = [
        "", "Январь", "Февраль", "Март", "Апрель", "Май", "Июнь",
        "Июль", "Август", "Сентябрь", "Октябрь", "Ноябрь", "Декабрь"
    ]

    _status_dates = status_dates or set()

    buttons = []

    # Заголовок с месяцем и годом + навигация
    buttons.append([
        InlineKeyboardButton(text="◀️", callback_data=f"cal:prev:{year}:{month}"),
        InlineKeyboardButton(text=f"{month_names[month]} {year}", callback_data="cal:ignore"),
        InlineKeyboardButton(text="▶️", callback_data=f"cal:next:{year}:{month}"),
    ])

    # Дни недели
    buttons.append([
        InlineKeyboardButton(text=day, callback_data="cal:ignore")
        for day in ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]
    ])

    # Календарная сетка
    cal = calendar.Calendar(firstweekday=0)
    today = date_type.today()

    for week in cal.monthdayscalendar(year, month):
        row = []
        for day in week:
            if day == 0:
                row.append(InlineKeyboardButton(text=" ", callback_data="cal:ignore"))
            else:
                day_date = date_type(year, month, day)
                day_str = day_date.isoformat()
                is_weekend = day_date.weekday() >= 5
                has_status = day_str in _status_dates

                # Определяем текст кнопки
                if day_str == selected_date:
                    text = f"[{day}]"
                elif day_date == today:
                    text = f"📍{day}"
                elif has_status:
                    text = f"🔵{day}"
                elif is_weekend:
                    text = str(day)
                else:
                    text = str(day)

                row.append(InlineKeyboardButton(
                    text=text,
                    callback_data=f"cal:day:{day_str}",
                ))
        buttons.append(row)

    # Кнопка "Назад"
    buttons.append([
        InlineKeyboardButton(text="⬅️ Назад", callback_data="status:cancel"),
    ])

    return InlineKeyboardMarkup(inline_keyboard=buttons)


def my_statuses_keyboard(
    statuses: list[dict],
    *,
    page: int = 0,
    is_privileged: bool = False,
) -> InlineKeyboardMarkup:
    """
    Клавиатура списка статусов пользователя с пагинацией и массовой отменой.

    Args:
        statuses: Список статусов с полями date, kind, comment (все статусы).
        page: Номер страницы (0-based).
        is_privileged: True если пользователь MANAGER или ADMIN.

    Returns:
        Inline клавиатура.
    """
    buttons: list[list[InlineKeyboardButton]] = []

    # Маппинг типов на эмодзи
    kind_emoji = {
        "VACATION": "✈️",
        "SICK": "🏥",
        "LEAVE": "🕓",
        "DAY_OFF": "📅",
    }

    # Пагинация
    total = len(statuses)
    total_pages = max(1, (total + STATUSES_PER_PAGE - 1) // STATUSES_PER_PAGE)
    page = max(0, min(page, total_pages - 1))

    start_idx = page * STATUSES_PER_PAGE
    end_idx = min(start_idx + STATUSES_PER_PAGE, total)
    page_statuses = statuses[start_idx:end_idx]

    for s in page_statuses:
        status_date = s.get("date") or s.get("status_date")
        kind = s.get("kind", "")
        emoji = kind_emoji.get(kind, "📌")

        if hasattr(status_date, "strftime"):
            date_str = status_date.strftime("%d.%m")
            date_iso = status_date.isoformat()
        else:
            date_str = str(status_date)
            date_iso = str(status_date)

        buttons.append([
            InlineKeyboardButton(
                text=f"{emoji} {date_str}",
                callback_data=f"status:view:{date_iso}",
            ),
            InlineKeyboardButton(
                text="🗑️ Отменить",
                callback_data=f"status:delete:{date_iso}",
            ),
        ])

    if not buttons:
        buttons.append([
            InlineKeyboardButton(text="(нет статусов)", callback_data="nav:back:user"),
        ])
    else:
        # Пагинация (если больше одной страницы)
        if total_pages > 1:
            pagination_row = []
            if page > 0:
                pagination_row.append(
                    InlineKeyboardButton(text="◀️", callback_data=f"user:my_statuses:page:{page - 1}")
                )
            pagination_row.append(
                InlineKeyboardButton(text=f"Стр {page + 1}/{total_pages}", callback_data="nav:ignore")
            )
            if page < total_pages - 1:
                pagination_row.append(
                    InlineKeyboardButton(text="▶️", callback_data=f"user:my_statuses:page:{page + 1}")
                )
            buttons.append(pagination_row)

        # Кнопка массовой отмены
        buttons.append([
            InlineKeyboardButton(text="🗑️ Отменить все", callback_data="status:clear_all"),
        ])

    # Правило D: навигация только для MANAGER/ADMIN
    buttons.extend(build_user_nav_footer(back_target="user", is_privileged=is_privileged))

    return InlineKeyboardMarkup(inline_keyboard=buttons)


def status_clear_all_confirm_keyboard() -> InlineKeyboardMarkup:
    """
    Клавиатура подтверждения массовой отмены статусов.
    """
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✅ Да, отменить все",
                    callback_data="status:clear_all:confirm",
                ),
                InlineKeyboardButton(
                    text="↩️ Нет",
                    callback_data="user:my_statuses",
                ),
            ],
        ]
    )


def status_delete_confirm_keyboard(date_iso: str) -> InlineKeyboardMarkup:
    """
    Клавиатура подтверждения удаления статуса.

    Args:
        date_iso: Дата в формате YYYY-MM-DD.
    """
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🗑 Да, удалить",
                    callback_data=f"status:delete_confirm:{date_iso}",
                ),
                InlineKeyboardButton(
                    text="❌ Нет",
                    callback_data="user:my_statuses",
                ),
            ],
        ]
    )
