"""
Сервис отчётов.

Сценарии:
- Ежедневный отчёт
- Месячный отчёт
- Экспорт в XLSX
"""

import calendar
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from core.screw_core.config.departments import OZKO_DEPARTMENT_ID
from core.screw_core.models.enums import EventType
from core.screw_core.repos.attendance_repo import AttendanceRepo
from core.screw_core.repos.events_repo import EventsRepo
from core.screw_core.repos.users_repo import UsersRepo
from core.screw_core.services.policy import OFFICE_ATTENDANCE_CHOICES
from core.screw_core.utils.logging import get_logger

logger = get_logger(__name__)


# =============================================================================
# Типы событий для отчётов
# =============================================================================

DAILY_REPORT_VIOLATION_TYPES = [
    EventType.NO_CHECKIN,
]


@dataclass
class DailyReportResult:
    """Результат формирования ежедневного отчёта."""

    report_date: date
    text: str

    # Фактические отметки
    actual_office: int = 0  # EVOLUTION + CSKA
    actual_remote: int = 0
    total_marks: int = 0

    # Участие в опросе
    total_active_users: int = 0
    poll_participation_percent: float = 0.0

    # Нарушения по типам
    violations_by_type: dict[str, int] = field(default_factory=dict)
    total_violations: int = 0

    # Топ нарушителей
    top_violators: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class MonthlyReportResult:
    """Результат формирования месячного отчёта (сводка месяца)."""

    year: int
    month: int

    # Рабочие дни (Mon-Fri без праздников)
    working_days_count: int = 0

    # Фактические офисные дни (из attendance_marks)
    office_days_total: int = 0
    office_days_evolution: int = 0
    office_days_cska: int = 0

    # Удалённые дни (из attendance_marks)
    remote_days: int = 0

    # Статусы (из day_statuses)
    vacation_days: int = 0
    sick_days: int = 0
    leave_days: int = 0

    # Не отметились
    no_checkin_count: int = 0

    # Общее количество активных пользователей
    total_active_users: int = 0


# =============================================================================
# Вспомог��тельные функции
# =============================================================================


def _get_violation_label(event_type: str) -> str:
    """Получить читаемое название типа нарушения."""
    labels = {
        EventType.NO_CHECKIN.value: "Не отметились в опросе",
    }
    return labels.get(event_type, event_type)


def _count_working_days_in_month(year: int, month: int) -> int:
    """Подсчитать количество рабочих дней в месяце (Mon-Fri)."""
    _, days_in_month = calendar.monthrange(year, month)
    count = 0
    for day in range(1, days_in_month + 1):
        d = date(year, month, day)
        if d.weekday() < 5:
            count += 1
    return count


def _get_month_date_range(year: int, month: int) -> tuple[date, date]:
    """Получить диапазон дат для месяца."""
    _, days_in_month = calendar.monthrange(year, month)
    start_date = date(year, month, 1)
    end_date = date(year, month, days_in_month)
    return start_date, end_date


# =============================================================================
# Сервис
# =============================================================================


class ReportsService:
    """Сервис формирования отчётов."""

    @staticmethod
    async def generate_daily_report(report_date: date) -> dict[str, Any]:
        """Сформировать ежедневный отчёт (заглушка)."""
        return {
            "date": report_date.isoformat(),
            "type": "daily",
            "data": [],
        }

    @staticmethod
    async def generate_monthly_report(year: int, month: int) -> dict[str, Any]:
        """Сформировать месячный отчёт (заглушка)."""
        return {
            "year": year,
            "month": month,
            "type": "monthly",
            "data": [],
        }

    @staticmethod
    async def build_daily_report_ozko(report_date: date) -> DailyReportResult:
        """
        Сформировать ежедневный отчёт для ОЗКО за дату.

        Включает:
        - Фактические отметки (офис/удалёнка)
        - Участие в опросе (процент)
        - Нарушения (NO_CHECKIN)
        - Топ-10 нарушителей
        """
        department_id = OZKO_DEPARTMENT_ID

        # 1. Количество активных пользователей ОЗКО
        total_active_users = await UsersRepo.count_active_by_department(department_id)

        # 2. Фактические отметки
        marks_counts = await AttendanceRepo.count_marks_by_choice_for_date(
            mark_date=report_date,
            department_id=department_id,
        )
        actual_office = sum(
            marks_counts.get(choice.value, 0)
            for choice in OFFICE_ATTENDANCE_CHOICES
        )
        actual_remote = marks_counts.get("REMOTE", 0)
        total_marks = await AttendanceRepo.count_total_marks_for_date(
            mark_date=report_date,
            department_id=department_id,
        )

        # 3. Участие в опросе
        poll_participation_percent = 0.0
        if total_active_users > 0:
            poll_participation_percent = round(total_marks / total_active_users * 100, 1)

        # 4. Нарушения по типам
        violations_by_type = await EventsRepo.count_violations_by_type_for_date(
            event_date=report_date,
            event_types=DAILY_REPORT_VIOLATION_TYPES,
            department_id=department_id,
        )
        total_violations = sum(violations_by_type.values())

        # 5. Топ-10 нарушителей
        top_violators = await EventsRepo.get_top_violators_for_date(
            event_date=report_date,
            event_types=DAILY_REPORT_VIOLATION_TYPES,
            department_id=department_id,
            limit=10,
        )

        # Формируем текст отчёта
        date_str = report_date.strftime("%d.%m.%Y")
        lines = [
            f"📊 Ежедневный отчёт за {date_str} (ОЗКО)",
            "",
            "✅ Фактические отметки:",
            f"  • Офис: {actual_office}",
            f"  • Удалёнка: {actual_remote}",
            f"  • Всего: {total_marks}",
            "",
            f"📈 Участие в опросе: {poll_participation_percent}% ({total_marks}/{total_active_users})",
        ]

        if total_violations > 0:
            lines.append("")
            lines.append(f"⚠️ Нарушения ({total_violations}):")
            for event_type, count in violations_by_type.items():
                label = _get_violation_label(event_type)
                lines.append(f"  • {label}: {count}")

        if top_violators:
            lines.append("")
            lines.append("🔝 Топ нарушителей:")
            for i, violator in enumerate(top_violators, 1):
                full_name = violator.get("full_name", "—")
                count = violator.get("violation_count", 0)
                lines.append(f"  {i}. {full_name} — {count}")

        text = "\n".join(lines)

        return DailyReportResult(
            report_date=report_date,
            text=text,
            actual_office=actual_office,
            actual_remote=actual_remote,
            total_marks=total_marks,
            total_active_users=total_active_users,
            poll_participation_percent=poll_participation_percent,
            violations_by_type=violations_by_type,
            total_violations=total_violations,
            top_violators=top_violators,
        )

    @staticmethod
    async def build_monthly_report_ozko(year: int, month: int) -> MonthlyReportResult:
        """
        Сформировать месячный отчёт (сводка) для ОЗКО.

        Включает:
        - Количество рабочих дней в месяце
        - Фактические офисные и удалённые дни (из attendance_marks)
        - Отпуска/больничные/отгулы
        - Не отметившиеся (NO_CHECKIN events)
        """
        department_id = OZKO_DEPARTMENT_ID

        start_date, end_date = _get_month_date_range(year, month)
        working_days_count = _count_working_days_in_month(year, month)
        total_active_users = await UsersRepo.count_active_by_department(department_id)

        # Фактические отметки за месяц (из attendance_marks)
        marks_counts = await AttendanceRepo.count_marks_by_choice_for_period(
            start_date=start_date,
            end_date=end_date,
            department_id=department_id,
        )
        office_days_evolution = marks_counts.get("EVOLUTION", 0)
        office_days_cska = marks_counts.get("CSKA", 0)
        office_days_total = office_days_evolution + office_days_cska
        remote_days = marks_counts.get("REMOTE", 0)

        # Статусы за месяц (из day_statuses)
        from core.screw_core.repos.day_status_repo import DayStatusRepo
        statuses_counts = await DayStatusRepo.count_statuses_by_kind_for_period(
            start_date=start_date,
            end_date=end_date,
            department_id=department_id,
        )
        vacation_days = statuses_counts.get("VACATION", 0)
        sick_days = statuses_counts.get("SICK", 0)
        leave_days = statuses_counts.get("LEAVE", 0)

        # NO_CHECKIN события за месяц
        violations_counts = await EventsRepo.count_violations_by_type_for_period(
            start_date=start_date,
            end_date=end_date,
            event_types=[EventType.NO_CHECKIN],
            department_id=department_id,
        )
        no_checkin_count = violations_counts.get(EventType.NO_CHECKIN.value, 0)

        return MonthlyReportResult(
            year=year,
            month=month,
            working_days_count=working_days_count,
            office_days_total=office_days_total,
            office_days_evolution=office_days_evolution,
            office_days_cska=office_days_cska,
            remote_days=remote_days,
            vacation_days=vacation_days,
            sick_days=sick_days,
            leave_days=leave_days,
            no_checkin_count=no_checkin_count,
            total_active_users=total_active_users,
        )
