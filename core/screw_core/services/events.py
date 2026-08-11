"""
Сервис событий.

Сценарии:
- Создание событий
- Получение событий
"""

from datetime import date
from typing import Any

from core.screw_core.models.enums import EventType, NotifyChannel
from core.screw_core.repos.events_repo import EventsRepo


class EventsService:
    """Сервис для работы с событиями и нарушениями."""

    @staticmethod
    async def create_event(
        event_type: EventType,
        dedup_key: str,
        user_id: int | None = None,
        event_date: date | None = None,
        payload: dict[str, Any] | None = None,
        notify_channel: NotifyChannel = NotifyChannel.VIOLATIONS_CHAT,
    ) -> int | None:
        """
        Создать событие (с дедупликацией).

        Args:
            event_type: Тип события.
            dedup_key: Ключ дедупликации.
            user_id: ID пользователя.
            event_date: Дата события.
            payload: Дополнительные данные.
            notify_channel: Канал уведомления.

        Returns:
            ID события или None если дубликат.
        """
        return await EventsRepo.create_event(
            event_type=event_type,
            dedup_key=dedup_key,
            user_id=user_id,
            event_date=event_date,
            payload=payload,
            notify_channel=notify_channel,
        )

    @staticmethod
    async def get_events_for_date(event_date: date) -> list[dict[str, Any]]:
        """
        Получить события за дату.

        Args:
            event_date: Дата.

        Returns:
            Список событий.
        """
        return await EventsRepo.get_events_for_date(event_date)

    @staticmethod
    async def get_events_by_type(
        event_type: EventType,
        start_date: date | None = None,
        end_date: date | None = None,
    ) -> list[dict[str, Any]]:
        """
        Получить события по типу.

        Args:
            event_type: Тип события.
            start_date: Начало периода.
            end_date: Конец периода.

        Returns:
            Список событий.
        """
        return await EventsRepo.get_events_by_type(event_type, start_date, end_date)

    @staticmethod
    async def get_event(event_id: int) -> dict[str, Any] | None:
        """
        Получить событие по ID.

        Args:
            event_id: ID события.

        Returns:
            Данные события или None.
        """
        return await EventsRepo.get_by_id(event_id)
