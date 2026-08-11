"""
Сервис учёта явки.

Сценарии:
- Запись отметки из опроса
- Получение фактов явки
"""

from datetime import date
from typing import Any

from core.screw_core.models.enums import AttendanceChoice
from core.screw_core.repos.attendance_repo import AttendanceRepo


class AttendanceService:
    """Сервис для работы с отметками явки."""

    @staticmethod
    async def record_choice(
        user_id: int,
        mark_date: date,
        choice: AttendanceChoice,
        poll_run_id: int | None = None,
    ) -> int:
        """
        Записать выбор явки пользователя.

        Args:
            user_id: ID пользователя.
            mark_date: Дата.
            choice: Выбор (офис/удалёнка).
            poll_run_id: ID опроса.

        Returns:
            ID отметки.
        """
        mark_id = await AttendanceRepo.create_mark(
            user_id=user_id,
            mark_date=mark_date,
            choice=choice,
            poll_run_id=poll_run_id,
            source="poll" if poll_run_id else "manual",
        )
        return mark_id

    @staticmethod
    async def get_attendance_for_date(mark_date: date) -> list[dict[str, Any]]:
        """
        Получить все отметки за дату.

        Args:
            mark_date: Дата.

        Returns:
            Список отметок с данными пользователей.
        """
        return await AttendanceRepo.get_marks_for_date(mark_date)

    @staticmethod
    async def get_users_without_mark(mark_date: date) -> list[dict[str, Any]]:
        """
        Получить пользователей без отметки.

        Args:
            mark_date: Дата.

        Returns:
            Список пользователей.
        """
        return await AttendanceRepo.get_users_without_mark(mark_date)

    @staticmethod
    async def get_user_attendance(
        user_id: int,
        start_date: date,
        end_date: date,
    ) -> list[dict[str, Any]]:
        """
        Получить отметки пользователя за период.

        Args:
            user_id: ID пользователя.
            start_date: Начало периода.
            end_date: Конец периода.

        Returns:
            Список отметок.
        """
        return await AttendanceRepo.get_marks_for_user_range(user_id, start_date, end_date)
