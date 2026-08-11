"""
Сервис статусов дней.

Сценарии:
- Установка отпуска/больничного/отгула
- Получение статусов
"""

from datetime import date
from typing import Any

from core.screw_core.models.enums import DayStatusKind
from core.screw_core.repos.day_status_repo import DayStatusRepo


def is_blocking_absence_range_overlap(kind: str | DayStatusKind) -> bool:
    """
    Проверить, должен ли существующий статус блокировать отпуск/больничный.

    DAY_OFF — системная отметка графика, её можно заменить реальным отсутствием.
    VACATION/SICK/LEAVE — пользовательские статусы, их защищаем от случайной перезаписи.
    """
    kind_value = kind.value if isinstance(kind, DayStatusKind) else str(kind)
    return kind_value in {
        DayStatusKind.VACATION.value,
        DayStatusKind.SICK.value,
        DayStatusKind.LEAVE.value,
    }


class DayStatusService:
    """Сервис для работы со статусами дней."""

    @staticmethod
    async def set_vacation(
        user_id: int,
        start_date: date,
        end_date: date,
        created_by: int | None = None,
    ) -> int:
        """
        Установить отпуск.

        Args:
            user_id: ID пользователя.
            start_date: Начало отпуска.
            end_date: Конец отпуска.
            created_by: Кто установил.

        Returns:
            Количество созданных записей.
        """
        return await DayStatusRepo.set_status_range(
            user_id=user_id,
            start_date=start_date,
            end_date=end_date,
            kind=DayStatusKind.VACATION,
            created_by=created_by,
        )

    @staticmethod
    async def set_sick_leave(
        user_id: int,
        start_date: date,
        end_date: date,
        created_by: int | None = None,
    ) -> int:
        """
        Установить больничный.

        Args:
            user_id: ID пользователя.
            start_date: Начало больничного.
            end_date: Конец больничного.
            created_by: Кто установил.

        Returns:
            Количество созданных записей.
        """
        return await DayStatusRepo.set_status_range(
            user_id=user_id,
            start_date=start_date,
            end_date=end_date,
            kind=DayStatusKind.SICK,
            created_by=created_by,
        )

    @staticmethod
    async def set_leave(
        user_id: int,
        leave_date: date,
        created_by: int | None = None,
        comment: str | None = None,
    ) -> int:
        """
        Установить отгул на один день.

        Args:
            user_id: ID пользователя.
            leave_date: Дата отгула.
            created_by: Кто установил.
            comment: Комментарий.

        Returns:
            ID записи.
        """
        return await DayStatusRepo.set_status(
            user_id=user_id,
            status_date=leave_date,
            kind=DayStatusKind.LEAVE,
            comment=comment,
            created_by=created_by,
        )

    @staticmethod
    async def get_statuses_for_date(status_date: date) -> list[dict[str, Any]]:
        """
        Получить все статусы на дату.

        Args:
            status_date: Дата.

        Returns:
            Список статусов.
        """
        return await DayStatusRepo.get_statuses_for_date(status_date)

    @staticmethod
    async def get_user_status(user_id: int, status_date: date) -> dict[str, Any] | None:
        """
        Получить статус пользователя на дату.

        Args:
            user_id: ID пользователя.
            status_date: Дата.

        Returns:
            Данные статуса или None.
        """
        return await DayStatusRepo.get_status(user_id, status_date)

    @staticmethod
    async def cancel_status(user_id: int, status_date: date) -> bool:
        """
        Отменить статус дня.

        Args:
            user_id: ID пользователя.
            status_date: Дата.

        Returns:
            True если удалено.
        """
        return await DayStatusRepo.delete_status(user_id, status_date)

    @staticmethod
    async def cancel_all_statuses(
        user_id: int,
        start_date: date,
        end_date: date,
    ) -> int:
        """
        Отменить все статусы пользователя за период.

        Удаляет только VACATION, SICK, LEAVE (не DAY_OFF).

        Args:
            user_id: ID пользователя.
            start_date: Начало периода.
            end_date: Конец периода.

        Returns:
            Количество удалённых записей.
        """
        kinds = [
            DayStatusKind.VACATION.value,
            DayStatusKind.SICK.value,
            DayStatusKind.LEAVE.value,
        ]
        return await DayStatusRepo.delete_statuses_range(
            user_id=user_id,
            start_date=start_date,
            end_date=end_date,
            kinds=kinds,
        )
