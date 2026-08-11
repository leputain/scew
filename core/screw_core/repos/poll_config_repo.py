"""
Репозиторий конфигурации опросов.

Работа с таблицами poll_config (singleton) и poll_skip_dates.
"""

import json
from datetime import date
from typing import Any

from core.screw_core.db.tx import acquire_connection, parse_rowcount


class PollConfigRepo:
    """CRUD для конфигурации опросов и пропуска дат."""

    # =========================================================================
    # poll_config (singleton)
    # =========================================================================

    @staticmethod
    async def get_config() -> dict[str, Any]:
        """Получить конфигурацию опросов (всегда одна строка).

        Returns:
            Словарь с полями: poll_duration_seconds, reminder_before_end_minutes,
            poll_options (list[dict]), updated_at, updated_by.
        """
        async with acquire_connection() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM screw.poll_config WHERE id = 1",
            )
            if row:
                result = dict(row)
                # JSONB → Python list
                opts = result.get("poll_options")
                if isinstance(opts, str):
                    result["poll_options"] = json.loads(opts)
                return result
            # Fallback: defaults
            return {
                "poll_duration_seconds": 900,
                "reminder_before_end_minutes": None,
                "poll_options": [
                    {"key": "EVOLUTION", "label": "МФК «Эволюция»"},
                    {"key": "CSKA", "label": "ЦСКА"},
                    {"key": "REMOTE", "label": "Удалёнка"},
                ],
                "auto_send_enabled": False,
                "auto_send_time": None,
            }

    @staticmethod
    async def update_duration(seconds: int, updated_by: int) -> None:
        """Обновить длительность опроса."""
        async with acquire_connection() as conn:
            await conn.execute(
                """
                UPDATE screw.poll_config
                SET poll_duration_seconds = $1, updated_at = NOW(), updated_by = $2
                WHERE id = 1
                """,
                seconds,
                updated_by,
            )

    @staticmethod
    async def update_reminder(minutes: int | None, updated_by: int) -> None:
        """Обновить напоминание (None = выключить)."""
        async with acquire_connection() as conn:
            await conn.execute(
                """
                UPDATE screw.poll_config
                SET reminder_before_end_minutes = $1, updated_at = NOW(), updated_by = $2
                WHERE id = 1
                """,
                minutes,
                updated_by,
            )

    @staticmethod
    async def update_options(options: list[dict], updated_by: int) -> None:
        """Обновить варианты ответов (JSONB)."""
        async with acquire_connection() as conn:
            await conn.execute(
                """
                UPDATE screw.poll_config
                SET poll_options = $1::jsonb, updated_at = NOW(), updated_by = $2
                WHERE id = 1
                """,
                json.dumps(options, ensure_ascii=False),
                updated_by,
            )

    @staticmethod
    async def update_auto_send_time(hour: int, minute: int, updated_by: int) -> None:
        """Обновить время авто-отправки опроса."""
        async with acquire_connection() as conn:
            await conn.execute(
                """
                UPDATE screw.poll_config
                SET auto_send_time = make_time($1, $2, 0),
                    updated_at = NOW(), updated_by = $3
                WHERE id = 1
                """,
                hour,
                minute,
                updated_by,
            )

    @staticmethod
    async def update_auto_send_enabled(enabled: bool, updated_by: int) -> None:
        """Включить/выключить авто-отправку опроса."""
        async with acquire_connection() as conn:
            await conn.execute(
                """
                UPDATE screw.poll_config
                SET auto_send_enabled = $1,
                    updated_at = NOW(), updated_by = $2
                WHERE id = 1
                """,
                enabled,
                updated_by,
            )

    @staticmethod
    async def get_office_requirements() -> dict[str, int]:
        """Получить нормы офисных дней по режимам работы.

        Returns:
            Словарь {"HYBRID": int, "HYBRID_PLUS": int}.
        """
        async with acquire_connection() as conn:
            row = await conn.fetchrow(
                """
                SELECT office_requirement_hybrid, office_requirement_hybrid_plus
                FROM screw.poll_config
                WHERE id = 1
                """,
            )
            if row:
                return {
                    "HYBRID": row["office_requirement_hybrid"] or 8,
                    "HYBRID_PLUS": row["office_requirement_hybrid_plus"] or 4,
                }
            # Fallback: defaults
            return {"HYBRID": 8, "HYBRID_PLUS": 4}

    @staticmethod
    async def update_office_requirements(hybrid: int, hybrid_plus: int, updated_by: int) -> None:
        """Обновить нормы офисных дней.

        Args:
            hybrid: Норма для режима HYBRID.
            hybrid_plus: Норма для режима HYBRID_PLUS.
            updated_by: ID пользователя, обновившего настройки.
        """
        async with acquire_connection() as conn:
            await conn.execute(
                """
                UPDATE screw.poll_config
                SET office_requirement_hybrid = $1,
                    office_requirement_hybrid_plus = $2,
                    updated_at = NOW(),
                    updated_by = $3
                WHERE id = 1
                """,
                hybrid,
                hybrid_plus,
                updated_by,
            )

    # =========================================================================
    # poll_skip_dates
    # =========================================================================

    @staticmethod
    async def get_skip_dates(start: date, end: date) -> list[dict[str, Any]]:
        """Получить пропуски дат за период."""
        async with acquire_connection() as conn:
            rows = await conn.fetch(
                """
                SELECT sd.*, u.full_name as creator_name
                FROM screw.poll_skip_dates sd
                LEFT JOIN screw.users u ON u.id = sd.created_by
                WHERE sd.skip_date BETWEEN $1 AND $2
                ORDER BY sd.skip_date
                """,
                start,
                end,
            )
            return [dict(row) for row in rows]

    @staticmethod
    async def get_upcoming_skip_dates(from_date: date, limit: int = 20) -> list[dict[str, Any]]:
        """Получить ближайшие пропуски начиная с даты."""
        async with acquire_connection() as conn:
            rows = await conn.fetch(
                """
                SELECT sd.*, u.full_name as creator_name
                FROM screw.poll_skip_dates sd
                LEFT JOIN screw.users u ON u.id = sd.created_by
                WHERE sd.skip_date >= $1
                ORDER BY sd.skip_date
                LIMIT $2
                """,
                from_date,
                limit,
            )
            return [dict(row) for row in rows]

    @staticmethod
    async def add_skip_date(
        skip_date: date,
        reason: str | None,
        created_by: int,
    ) -> int | None:
        """Добавить пропуск даты (ON CONFLICT DO NOTHING).

        Returns:
            ID новой записи или None если дата уже существует.
        """
        async with acquire_connection() as conn:
            row = await conn.fetchval(
                """
                INSERT INTO screw.poll_skip_dates (skip_date, reason, created_by)
                VALUES ($1, $2, $3)
                ON CONFLICT (skip_date) DO NOTHING
                RETURNING id
                """,
                skip_date,
                reason,
                created_by,
            )
            return row

    @staticmethod
    async def remove_skip_date(skip_date: date) -> bool:
        """Удалить пропуск даты.

        Returns:
            True если запись удалена.
        """
        async with acquire_connection() as conn:
            result = await conn.execute(
                "DELETE FROM screw.poll_skip_dates WHERE skip_date = $1",
                skip_date,
            )
            return parse_rowcount(result) == 1

    @staticmethod
    async def is_skip_date(check_date: date) -> bool:
        """Проверить, является ли дата пропуском."""
        async with acquire_connection() as conn:
            row = await conn.fetchval(
                "SELECT 1 FROM screw.poll_skip_dates WHERE skip_date = $1",
                check_date,
            )
            return row is not None

    @staticmethod
    async def get_skip_date_info(check_date: date) -> dict[str, Any] | None:
        """Получить информацию о пропуске даты."""
        async with acquire_connection() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM screw.poll_skip_dates WHERE skip_date = $1",
                check_date,
            )
            return dict(row) if row else None
