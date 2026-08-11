"""
Политика посещений — единый источник истины.

Модуль содержит все правила расчёта:
- Нормы по work_mode (HYBRID, HYBRID_PLUS, FULL_REMOTE, DUTY)
- Скидки за отпуск
- Проверка exempt-статусов (отпуск/больничный/отгул/выходной)
- Определение, обязан ли пользователь отмечаться
"""

import os

from core.screw_core.models.enums import (
    AttendanceChoice,
    DayStatusKind,
    WorkMode,
)


# =============================================================================
# Конфигурация через ENV
# =============================================================================


def get_vacation_discount_enabled() -> bool:
    """
    Включена ли скидка на норму офисных дней за отпуск.

    По умолчанию: True (1).
    """
    return os.getenv("POLICY_VACATION_DISCOUNT_ENABLED", "1") == "1"


def get_vacation_discount_table() -> dict[tuple[int, int], int]:
    """
    Получить таблицу скидок за отпускные дни.

    Формат env: "5-7:-2,10-14:-4"
    Означает: 5-7 дней отпуска = -2 от нормы, 10-14 дней = -4.
    """
    default = "5-7:-2,10-14:-4"
    raw = os.getenv("POLICY_VACATION_DISCOUNT_TABLE", default)

    result: dict[tuple[int, int], int] = {}
    for part in raw.split(","):
        part = part.strip()
        if not part or ":" not in part:
            continue
        try:
            range_part, discount_part = part.split(":")
            discount = int(discount_part)

            if "-" in range_part:
                min_str, max_str = range_part.split("-")
                min_days = int(min_str)
                max_days = int(max_str)
            else:
                min_days = max_days = int(range_part)

            result[(min_days, max_days)] = discount
        except (ValueError, IndexError):
            continue

    return result


# =============================================================================
# Константы — статусы и режимы
# =============================================================================


# Фактические отметки, которые означают "офис"
OFFICE_ATTENDANCE_CHOICES: set[AttendanceChoice] = {
    AttendanceChoice.EVOLUTION,
    AttendanceChoice.CSKA,
}

# Статусы дня, которые исключают пользователя из проверок (exempt)
EXEMPT_DAY_STATUSES: set[DayStatusKind] = {
    DayStatusKind.VACATION,
    DayStatusKind.SICK,
    DayStatusKind.LEAVE,
    DayStatusKind.DAY_OFF,
}

# Базовые нормы офисных дней в месяц по work_mode
BASE_OFFICE_REQUIREMENT: dict[WorkMode, int] = {
    WorkMode.HYBRID: 8,        # Гибридный — 8 дней в офисе
    WorkMode.HYBRID_PLUS: 4,   # Гибридный+ — 4 дня в офисе
    WorkMode.FULL_REMOTE: 0,   # Полностью удалённый — 0 дней, но обязан отмечаться
    WorkMode.DUTY: 0,          # Дежурный — 0, не обязан отмечаться
}


# =============================================================================
# Функции проверки
# =============================================================================


def is_office_attendance(attendance_choice: str | None) -> bool:
    """
    Проверить, является ли отметка офисной.

    Args:
        attendance_choice: Значение AttendanceChoice или None.

    Returns:
        True если отметка офисная.
    """
    if not attendance_choice:
        return False
    return attendance_choice in {c.value for c in OFFICE_ATTENDANCE_CHOICES}


def is_day_exempt_by_status(day_status: str | None) -> bool:
    """
    Проверить, исключён ли день из проверок по статусу дня.

    Статусы: VACATION, SICK, LEAVE, DAY_OFF.

    Args:
        day_status: Значение DayStatusKind или None.

    Returns:
        True если день исключён.
    """
    if not day_status:
        return False
    return day_status in {s.value for s in EXEMPT_DAY_STATUSES}


def is_day_exempt(day_status: str | None) -> bool:
    """
    Проверить, исключён ли день из проверок.

    Args:
        day_status: Значение DayStatusKind или None.

    Returns:
        True если день исключён.
    """
    return is_day_exempt_by_status(day_status)


def is_work_mode_duty(work_mode: WorkMode | str | None) -> bool:
    """
    Проверить, является ли режим работы DUTY.

    DUTY не участвует в проверках и не обязан отмечаться.
    """
    if work_mode is None:
        return False
    if isinstance(work_mode, WorkMode):
        return work_mode == WorkMode.DUTY
    return work_mode == WorkMode.DUTY.value


def is_work_mode_full_remote(work_mode: WorkMode | str | None) -> bool:
    """
    Проверить, является ли режим работы FULL_REMOTE.

    FULL_REMOTE обязан отмечаться всегда, но норма офисных дней = 0.
    """
    if work_mode is None:
        return False
    if isinstance(work_mode, WorkMode):
        return work_mode == WorkMode.FULL_REMOTE
    return work_mode == WorkMode.FULL_REMOTE.value


def must_checkin(work_mode: WorkMode | str | None) -> bool:
    """
    Проверить, обязан ли пользователь отмечаться.

    DUTY — не обязан. Все остальные — обязаны.
    """
    return not is_work_mode_duty(work_mode)


# =============================================================================
# Расчёт нормы и скидок
# =============================================================================


async def get_base_required(work_mode: WorkMode | str | None) -> int:
    """
    Получить базовую норму офисных дней для режима работы из БД.
    """
    from core.screw_core.repos.poll_config_repo import PollConfigRepo

    if work_mode is None:
        return 0

    if isinstance(work_mode, str):
        try:
            work_mode = WorkMode(work_mode)
        except ValueError:
            return 0

    # FULL_REMOTE и DUTY всегда 0
    if work_mode in (WorkMode.FULL_REMOTE, WorkMode.DUTY):
        return 0

    # Получаем из БД для HYBRID и HYBRID_PLUS
    reqs = await PollConfigRepo.get_office_requirements()
    return reqs.get(work_mode.value, 0)


def calculate_vacation_discount(vacation_days: int) -> int:
    """
    Рассчитать скидку за отпуск.

    Возвращает отрицательное число (уменьшение нормы).
    """
    if not get_vacation_discount_enabled():
        return 0

    if vacation_days <= 0:
        return 0

    discount_table = get_vacation_discount_table()

    for (min_days, max_days), discount in discount_table.items():
        if min_days <= vacation_days <= max_days:
            return discount

    # Если больше максимального диапазона, берём максимальную скидку
    if discount_table:
        max_range = max(discount_table.keys(), key=lambda x: x[1])
        if vacation_days > max_range[1]:
            return discount_table[max_range]

    return 0


async def calculate_required_office_days(
    work_mode: WorkMode | str | None,
    vacation_days: int = 0,
) -> int:
    """
    Рассчитать требуемое количество офисных дней.

    Формула: base_required + vacation_discount.
    """
    base = await get_base_required(work_mode)
    discount = calculate_vacation_discount(vacation_days)

    required = base + discount  # discount отрицательный
    return max(0, required)
