"""
Сервис опросов явки.

Сценарии:
- Создание/публикация опроса
- Напоминание
- Закрытие опроса
"""

from dataclasses import dataclass
from datetime import date, datetime
from enum import StrEnum
from typing import Any

from core.screw_core.repos.polls_repo import PollsRepo


class PollError(StrEnum):
    """Ошибки при работе с опросами."""

    POLL_NOT_FOUND = "poll_not_found"
    POLL_ALREADY_CLOSED = "poll_already_closed"


@dataclass
class PollResult:
    """Результат операции с опросом."""

    success: bool
    error: PollError | None = None
    poll_id: int | None = None
    data: dict[str, Any] | None = None


class PollsService:
    """Сервис для работы с опросами явки."""

    @staticmethod
    async def create_daily_poll(
        poll_date: date,
        chat_id: int,
        message_id: int | None = None,
        telegram_poll_id: str | None = None,
        closes_at: datetime | None = None,
    ) -> PollResult:
        """
        Создать или пересоздать ежедневный опрос (UPSERT).

        При пересоздании существующий опрос обновляется:
        - Новый message_id и telegram_poll_id
        - Сброс reminder_sent_at и closed_at
        - Статус 'open'
        - ОЧИСТКА attendance_marks для старого poll_run

        Args:
            poll_date: Дата опроса.
            chat_id: ID чата для публикации.
            message_id: ID сообщения в Telegram (если уже опубликовано).
            telegram_poll_id: ID опроса в Telegram (для native polls).
            closes_at: Время автоматического закрытия опроса.

        Returns:
            Результат с ID опроса и флагом is_recreate.
        """
        # Проверяем, существует ли опрос на эту дату (для определения recreate)
        existing = await PollsRepo.get_by_date(poll_date)
        is_recreate = existing is not None

        poll_id, deleted_marks = await PollsRepo.create_poll_with_expected_users(
            poll_date, chat_id, message_id, telegram_poll_id, closes_at
        )

        return PollResult(
            success=True,
            poll_id=poll_id,
            data={
                "is_recreate": is_recreate,
                "deleted_marks": deleted_marks,
            },
        )

    @staticmethod
    async def publish_poll(poll_id: int, message_id: int) -> None:
        """
        Отметить опрос как опубликованный (обновить message_id).

        Args:
            poll_id: ID опроса.
            message_id: ID сообщения в Telegram.
        """
        await PollsRepo.update_message_id(poll_id, message_id)

    @staticmethod
    async def send_reminder(poll_id: int) -> None:
        """
        Отправить напоминание об опросе.

        Args:
            poll_id: ID опроса.
        """
        # TODO: получить список пользователей без отметки
        # TODO: подготовить текст напоминания
        await PollsRepo.mark_reminder_sent(poll_id)

    @staticmethod
    async def close_poll(poll_id: int) -> PollResult:
        """
        Закрыть опрос.

        Args:
            poll_id: ID опроса.

        Returns:
            Результат операции.
        """
        poll = await PollsRepo.get_by_id(poll_id)
        if not poll:
            return PollResult(success=False, error=PollError.POLL_NOT_FOUND)

        if poll["status"] != "open":
            return PollResult(success=False, error=PollError.POLL_ALREADY_CLOSED)

        await PollsRepo.close_poll(poll_id)
        return PollResult(success=True, poll_id=poll_id, data=poll)

    @staticmethod
    async def close_current_poll() -> PollResult:
        """
        Закрыть текущий открытый опрос.

        Returns:
            Результат операции.
        """
        poll = await PollsRepo.get_open_poll()
        if not poll:
            return PollResult(success=False, error=PollError.POLL_NOT_FOUND)

        await PollsRepo.close_poll(poll["id"])
        return PollResult(success=True, poll_id=poll["id"], data=poll)

    @staticmethod
    async def get_current_poll() -> dict | None:
        """
        Получить текущий открытый опрос.

        Returns:
            Данные опроса или None.
        """
        return await PollsRepo.get_open_poll()

    @staticmethod
    async def get_poll_by_message(chat_id: int, message_id: int) -> dict | None:
        """
        Получить опрос по chat_id и message_id.

        Args:
            chat_id: ID чата.
            message_id: ID сообщения.

        Returns:
            Данные опроса или None.
        """
        return await PollsRepo.get_poll_by_message(chat_id, message_id)

    @staticmethod
    async def get_poll(poll_id: int) -> dict | None:
        """
        Получить опрос по ID.

        Args:
            poll_id: ID опроса.

        Returns:
            Данные опроса или None.
        """
        return await PollsRepo.get_by_id(poll_id)

    @staticmethod
    async def get_poll_by_telegram_id(telegram_poll_id: str) -> dict | None:
        """
        Получить опрос по Telegram poll ID.

        Args:
            telegram_poll_id: ID опроса в Telegram.

        Returns:
            Данные опроса или None.
        """
        return await PollsRepo.get_by_telegram_poll_id(telegram_poll_id)

    @staticmethod
    async def update_telegram_poll_id(poll_id: int, telegram_poll_id: str) -> None:
        """
        Обновить ID Telegram опроса.

        Args:
            poll_id: ID опроса в БД.
            telegram_poll_id: ID опроса в Telegram.
        """
        await PollsRepo.update_telegram_poll_id(poll_id, telegram_poll_id)

    @staticmethod
    async def get_non_respondents(
        poll_id: int,
        department_id: int | None = None,
    ) -> list[dict[str, Any]]:
        """
        Получить список пользователей, не ответивших на опрос.

        Args:
            poll_id: ID опроса.
            department_id: Опциональный фильтр по отделу.

        Returns:
            Список пользователей (id, telegram_id, full_name).
        """
        return await PollsRepo.get_non_respondents(poll_id, department_id=department_id)

    @staticmethod
    async def mark_reminder_sent(poll_id: int, message_id: int | None = None) -> None:
        """
        Отметить отправку напоминания и сохранить ID сообщения.

        Args:
            poll_id: ID опроса.
            message_id: ID сообщения-напоминания в Telegram.
        """
        await PollsRepo.mark_reminder_sent(poll_id, message_id)

    @staticmethod
    async def get_reminder_message_id(poll_id: int) -> int | None:
        """
        Получить ID сообщения-напоминания.

        Args:
            poll_id: ID опроса.

        Returns:
            ID сообщения или None.
        """
        return await PollsRepo.get_reminder_message_id(poll_id)

    @staticmethod
    async def clear_reminder_message_id(poll_id: int) -> None:
        """
        Сбросить ID сообщения-напоминания.

        Args:
            poll_id: ID опроса.
        """
        await PollsRepo.clear_reminder_message_id(poll_id)
