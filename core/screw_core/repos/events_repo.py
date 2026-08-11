"""
Репозиторий событий.

Работа с таблицей events (нарушения, дедупликация, retry уведомлений).
"""

from datetime import date, datetime
from typing import Any

from core.screw_core.config.departments import OZKO_DEPARTMENT_ID
from core.screw_core.db.tx import acquire_connection, parse_rowcount
from core.screw_core.models.enums import EventType, NotifyChannel


class EventsRepo:
    """Репозиторий для работы с событиями и нарушениями."""

    @staticmethod
    async def create_event(
        event_type: EventType,
        dedup_key: str,
        user_id: int | None = None,
        event_date: date | None = None,
        payload: dict[str, Any] | None = None,
        notify_channel: NotifyChannel = NotifyChannel.VIOLATIONS_CHAT,
        notify_error: str | None = None,
    ) -> int | None:
        """
        Создать событие (с дедупликацией).

        Args:
            event_type: Тип события.
            dedup_key: Ключ дедупликации.
            user_id: ID пользователя.
            event_date: Дата события.
            payload: Дополнительные данные (JSON).
            notify_channel: Канал уведомления.
            notify_error: Текст ошибки уведомления (если есть).

        Returns:
            ID события или None, если событие уже существует.
        """
        async with acquire_connection() as conn:
            # Используем ON CONFLICT DO NOTHING для дедупликации
            import json
            payload_json = json.dumps(payload) if payload else None

            event_id = await conn.fetchval(
                """
                INSERT INTO screw.events
                    (event_type, dedup_key, user_id, event_date, payload, notify_channel, notify_error)
                VALUES ($1, $2, $3, $4, $5::jsonb, $6, $7)
                ON CONFLICT (dedup_key) DO NOTHING
                RETURNING id
                """,
                event_type.value,
                dedup_key,
                user_id,
                event_date,
                payload_json,
                notify_channel.value,
                notify_error,
            )
            return event_id

    @staticmethod
    async def reset_notification(dedup_key: str) -> int | None:
        """
        Сбросить is_notified для существующего события (для повторной отправки).

        Args:
            dedup_key: Ключ дедупликации.

        Returns:
            ID события или None если не найдено.
        """
        async with acquire_connection() as conn:
            return await conn.fetchval(
                """
                UPDATE screw.events
                SET is_notified = FALSE, notify_attempts = 0
                WHERE dedup_key = $1
                RETURNING id
                """,
                dedup_key,
            )

    @staticmethod
    async def get_by_id(event_id: int) -> dict[str, Any] | None:
        """
        Получить событие по ID.

        Args:
            event_id: ID события.

        Returns:
            Данные события или None.
        """
        async with acquire_connection() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM screw.events WHERE id = $1",
                event_id,
            )
            return dict(row) if row else None

    @staticmethod
    async def get_by_dedup_key(dedup_key: str) -> dict[str, Any] | None:
        """
        Получить событие по ключу дедупликации.

        Args:
            dedup_key: Ключ дедупликации.

        Returns:
            Данные события или None.
        """
        async with acquire_connection() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM screw.events WHERE dedup_key = $1",
                dedup_key,
            )
            return dict(row) if row else None

    @staticmethod
    async def get_pending_notifications(limit: int = 100) -> list[dict[str, Any]]:
        """
        Получить события, ожидающие уведомления.

        Args:
            limit: Максимальное количество.

        Returns:
            Список событий.
        """
        async with acquire_connection() as conn:
            rows = await conn.fetch(
                """
                SELECT e.*, u.telegram_id, u.full_name, u.department_id, d.code AS department_code
                FROM screw.events e
                LEFT JOIN screw.users u ON e.user_id = u.id
                LEFT JOIN screw.org_departments d ON d.id = u.department_id
                WHERE e.is_notified = FALSE
                  AND e.notify_channel != 'none'
                  AND e.notify_attempts < 3
                ORDER BY e.created_at
                LIMIT $1
                """,
                limit,
            )
            return [dict(row) for row in rows]

    @staticmethod
    async def mark_notified(event_id: int) -> None:
        """
        Отметить событие как доставленное.

        Args:
            event_id: ID события.
        """
        async with acquire_connection() as conn:
            await conn.execute(
                """
                UPDATE screw.events
                SET is_notified = TRUE, notified_at = $2, notify_attempts = notify_attempts + 1
                WHERE id = $1
                """,
                event_id,
                datetime.now(),
            )

    @staticmethod
    async def mark_notify_error(event_id: int, error: str) -> None:
        """
        Отметить ошибку доставки уведомления.

        Args:
            event_id: ID события.
            error: Текст ошибки.
        """
        async with acquire_connection() as conn:
            await conn.execute(
                """
                UPDATE screw.events
                SET notify_attempts = notify_attempts + 1, notify_error = $2
                WHERE id = $1
                """,
                event_id,
                error,
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
        async with acquire_connection() as conn:
            rows = await conn.fetch(
                """
                SELECT e.*, u.telegram_id, u.full_name
                FROM screw.events e
                LEFT JOIN screw.users u ON e.user_id = u.id
                WHERE e.event_date = $1
                ORDER BY e.created_at
                """,
                event_date,
            )
            return [dict(row) for row in rows]

    @staticmethod
    async def get_events_by_type(
        event_type: EventType,
        start_date: date | None = None,
        end_date: date | None = None,
    ) -> list[dict[str, Any]]:
        """
        Получить события по типу за период.

        Args:
            event_type: Тип события.
            start_date: Начало периода.
            end_date: Конец периода.

        Returns:
            Список событий.
        """
        async with acquire_connection() as conn:
            if start_date and end_date:
                rows = await conn.fetch(
                    """
                    SELECT * FROM screw.events
                    WHERE event_type = $1
                      AND event_date BETWEEN $2 AND $3
                    ORDER BY created_at
                    """,
                    event_type.value,
                    start_date,
                    end_date,
                )
            else:
                rows = await conn.fetch(
                    """
                    SELECT * FROM screw.events
                    WHERE event_type = $1
                    ORDER BY created_at
                    """,
                    event_type.value,
                )
            return [dict(row) for row in rows]

    @staticmethod
    async def get_latest_event_by_types(event_types: list[EventType]) -> dict[str, Any] | None:
        """
        Получить последнее событие среди указанных типов.

        Args:
            event_types: Список типов событий.

        Returns:
            Данные события или None.
        """
        if not event_types:
            return None

        async with acquire_connection() as conn:
            event_type_values = [et.value for et in event_types]
            row = await conn.fetchrow(
                """
                SELECT *
                FROM screw.events
                WHERE event_type = ANY($1)
                ORDER BY created_at DESC
                LIMIT 1
                """,
                event_type_values,
            )
            return dict(row) if row else None

    @staticmethod
    async def get_diff_events_for_ozko(
        event_date: date,
        event_types: list[EventType],
    ) -> list[dict[str, Any]]:
        """
        Получить события нарушений для ОЗКО за дату.

        Возвращает события указанных типов для пользователей отдела ОЗКО (department_id=1).
        Включает full_name пользователя для формирования отчёта.

        Args:
            event_date: Дата событий.
            event_types: Список типов событий для фильтрации.

        Returns:
            Список событий с full_name пользователя.
        """
        if not event_types:
            return []

        async with acquire_connection() as conn:
            event_type_values = [et.value for et in event_types]
            rows = await conn.fetch(
                """
                SELECT e.id, e.event_type, e.dedup_key, e.user_id, e.event_date,
                       e.payload, e.created_at, u.full_name, u.telegram_id
                FROM screw.events e
                JOIN screw.users u ON e.user_id = u.id
                WHERE e.event_date = $1
                  AND e.event_type = ANY($2)
                  AND u.department_id = $3
                  AND u.status = 'active'
                ORDER BY e.event_type, u.full_name
                """,
                event_date,
                event_type_values,
                OZKO_DEPARTMENT_ID,
            )
            return [dict(row) for row in rows]

    @staticmethod
    async def count_violations_by_type_for_date(
        event_date: date,
        event_types: list[EventType],
        department_id: int | None = None,
    ) -> dict[str, int]:
        """
        Подсчитать количество нарушений по типу за дату.

        Args:
            event_date: Дата событий.
            event_types: Список типов нарушений для подсчёта.
            department_id: Опциональный фильтр по отделу.

        Returns:
            Словарь event_type -> count.
        """
        if not event_types:
            return {}

        async with acquire_connection() as conn:
            event_type_values = [et.value for et in event_types]
            if department_id is not None:
                rows = await conn.fetch(
                    """
                    SELECT e.event_type, COUNT(*) as count
                    FROM screw.events e
                    JOIN screw.users u ON e.user_id = u.id
                    WHERE e.event_date = $1
                      AND e.event_type = ANY($2)
                      AND u.department_id = $3
                      AND u.status = 'active'
                    GROUP BY e.event_type
                    """,
                    event_date,
                    event_type_values,
                    department_id,
                )
            else:
                rows = await conn.fetch(
                    """
                    SELECT e.event_type, COUNT(*) as count
                    FROM screw.events e
                    JOIN screw.users u ON e.user_id = u.id
                    WHERE e.event_date = $1
                      AND e.event_type = ANY($2)
                      AND u.status = 'active'
                    GROUP BY e.event_type
                    """,
                    event_date,
                    event_type_values,
                )
            return {row["event_type"]: row["count"] for row in rows}

    @staticmethod
    async def get_top_violators_for_date(
        event_date: date,
        event_types: list[EventType],
        department_id: int | None = None,
        limit: int = 10,
    ) -> list[dict[str, Any]]:
        """
        Получить топ нарушителей за дату.

        Группирует по пользователю, сортирует по количеству нарушений.

        Args:
            event_date: Дата событий.
            event_types: Список типов нарушений для подсчёта.
            department_id: Опциональный фильтр по отделу.
            limit: Максимальное количество записей.

        Returns:
            Список словарей с user_id, full_name, violation_count.
        """
        if not event_types:
            return []

        async with acquire_connection() as conn:
            event_type_values = [et.value for et in event_types]
            if department_id is not None:
                rows = await conn.fetch(
                    """
                    SELECT e.user_id, u.full_name, COUNT(*) as violation_count
                    FROM screw.events e
                    JOIN screw.users u ON e.user_id = u.id
                    WHERE e.event_date = $1
                      AND e.event_type = ANY($2)
                      AND u.department_id = $3
                      AND u.status = 'active'
                    GROUP BY e.user_id, u.full_name
                    ORDER BY violation_count DESC, u.full_name
                    LIMIT $4
                    """,
                    event_date,
                    event_type_values,
                    department_id,
                    limit,
                )
            else:
                rows = await conn.fetch(
                    """
                    SELECT e.user_id, u.full_name, COUNT(*) as violation_count
                    FROM screw.events e
                    JOIN screw.users u ON e.user_id = u.id
                    WHERE e.event_date = $1
                      AND e.event_type = ANY($2)
                      AND u.status = 'active'
                    GROUP BY e.user_id, u.full_name
                    ORDER BY violation_count DESC, u.full_name
                    LIMIT $3
                    """,
                    event_date,
                    event_type_values,
                    limit,
                )
            return [dict(row) for row in rows]

    @staticmethod
    async def count_violations_by_type_for_period(
        start_date: date,
        end_date: date,
        event_types: list[EventType],
        department_id: int | None = None,
    ) -> dict[str, int]:
        """
        Подсчитать количество нарушений по типу за период.

        Args:
            start_date: Начало периода.
            end_date: Конец периода.
            event_types: Список типов нарушений для подсчёта.
            department_id: Опциональный фильтр по отделу.

        Returns:
            Словарь event_type -> count.
        """
        if not event_types:
            return {}

        async with acquire_connection() as conn:
            event_type_values = [et.value for et in event_types]
            if department_id is not None:
                rows = await conn.fetch(
                    """
                    SELECT e.event_type, COUNT(*) as count
                    FROM screw.events e
                    JOIN screw.users u ON e.user_id = u.id
                    WHERE e.event_date BETWEEN $1 AND $2
                      AND e.event_type = ANY($3)
                      AND u.department_id = $4
                      AND u.status = 'active'
                    GROUP BY e.event_type
                    """,
                    start_date,
                    end_date,
                    event_type_values,
                    department_id,
                )
            else:
                rows = await conn.fetch(
                    """
                    SELECT e.event_type, COUNT(*) as count
                    FROM screw.events e
                    JOIN screw.users u ON e.user_id = u.id
                    WHERE e.event_date BETWEEN $1 AND $2
                      AND e.event_type = ANY($3)
                      AND u.status = 'active'
                    GROUP BY e.event_type
                    """,
                    start_date,
                    end_date,
                    event_type_values,
                )
            return {row["event_type"]: row["count"] for row in rows}

    @staticmethod
    async def get_bot_messages_for_user(
        telegram_id: int,
        limit: int = 30,
    ) -> list[dict[str, Any]]:
        """
        Получить последние сообщения бота для пользователя.

        Используется для очистки сообщений при отзыве доступа.

        Args:
            telegram_id: Telegram ID пользователя.
            limit: Максимальное количество сообщений.

        Returns:
            Список событий с payload содержащим chat_id и message_id.
        """
        async with acquire_connection() as conn:
            rows = await conn.fetch(
                """
                SELECT id, payload, created_at
                FROM screw.events
                WHERE dedup_key LIKE $1
                  AND payload->>'kind' = 'BOT_SENT_MESSAGE'
                ORDER BY created_at DESC
                LIMIT $2
                """,
                f"botmsg:{telegram_id}:%",
                limit,
            )
            return [dict(row) for row in rows]

    @staticmethod
    async def delete_bot_message_events(
        telegram_id: int,
        message_ids: list[int],
    ) -> int:
        """
        Удалить события отслеживания сообщений после очистки.

        Args:
            telegram_id: Telegram ID пользователя.
            message_ids: Список ID сообщений.

        Returns:
            Количество удалённых записей.
        """
        if not message_ids:
            return 0

        async with acquire_connection() as conn:
            result = await conn.execute(
                """
                DELETE FROM screw.events
                WHERE dedup_key = ANY($1::text[])
                """,
                [f"botmsg:{telegram_id}:{mid}" for mid in message_ids],
            )
            return parse_rowcount(result)

    @staticmethod
    async def delete_old_diagnostic_events(
        event_types: list[str],
        before_date: date,
    ) -> int:
        """
        Удалить старые диагностические события.

        Args:
            event_types: Список типов событий для удаления.
            before_date: Удалить события старше этой даты.

        Returns:
            Количество удалённых записей.
        """
        async with acquire_connection() as conn:
            result = await conn.execute(
                """
                DELETE FROM screw.events
                WHERE event_type::text = ANY($1::text[])
                  AND event_date < $2
                """,
                event_types,
                before_date,
            )
            return parse_rowcount(result)

    @staticmethod
    async def get_recent_notify_error_summary(hours: int = 24) -> list[dict[str, Any]]:
        """Aggregate recent delivery errors without returning payloads or identifiers."""
        safe_hours = max(1, min(int(hours), 24 * 30))
        async with acquire_connection() as conn:
            rows = await conn.fetch(
                """
                SELECT event_type::text AS event_type,
                       COUNT(*)::int AS error_count,
                       MAX(created_at) AS last_error_at
                FROM screw.events
                WHERE notify_error IS NOT NULL
                  AND created_at >= NOW() - ($1 * INTERVAL '1 hour')
                GROUP BY event_type
                ORDER BY error_count DESC, event_type
                """,
                safe_hours,
            )
            return [dict(row) for row in rows]
