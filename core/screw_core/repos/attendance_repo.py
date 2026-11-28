"""
Репозиторий отметок явки.

Работа с таблицей attendance_marks.
"""

from datetime import date
from typing import Any

from core.screw_core.db.tx import acquire_connection, transaction
from core.screw_core.models.enums import AttendanceChoice


class AttendanceRepo:
    """Репозиторий для работы с отметками явки."""

    @staticmethod
    async def create_mark(
        user_id: int,
        mark_date: date,
        choice: AttendanceChoice,
        poll_run_id: int | None = None,
        source: str = "poll",
    ) -> int:
        """
        Создать отметку явки.

        Args:
            user_id: ID пользователя.
            mark_date: Дата отметки.
            choice: Выбор (офис/удалёнка).
            poll_run_id: ID опроса (если из опроса).
            source: Источник отметки.

        Returns:
            ID отметки.
        """
        async with acquire_connection() as conn:
            mark_id = await conn.fetchval(
                """
                INSERT INTO screw.attendance_marks
                    (user_id, mark_date, choice, poll_run_id, source)
                VALUES ($1, $2, $3, $4, $5)
                ON CONFLICT (user_id, mark_date) DO UPDATE
                SET choice = $3, poll_run_id = $4, source = $5, updated_at = NOW()
                RETURNING id
                """,
                user_id,
                mark_date,
                choice.value,
                poll_run_id,
                source,
            )
            return mark_id

    @staticmethod
    async def apply_poll_answer(
        telegram_poll_id: str,
        user_id: int,
        choice: AttendanceChoice | None,
        update_id: int,
    ) -> None:
        """Apply a vote or retraction to the currently matching poll generation."""
        async with transaction() as conn:
            poll = await conn.fetchrow(
                """
                SELECT id, poll_date
                FROM screw.poll_runs
                WHERE telegram_poll_id = $1
                  AND (status = 'open' OR (
                      summary_pending AND closed_at > NOW() - INTERVAL '60 seconds'
                  ))
                FOR UPDATE
                """,
                telegram_poll_id,
            )
            if not poll:
                return
            poll_id, poll_date = poll["id"], poll["poll_date"]
            accepted = await conn.fetchval(
                """
                INSERT INTO screw.poll_vote_updates (poll_run_id, user_id, update_id)
                VALUES ($1, $2, $3)
                ON CONFLICT (poll_run_id, user_id) DO UPDATE
                SET update_id = EXCLUDED.update_id
                WHERE poll_vote_updates.update_id < EXCLUDED.update_id
                RETURNING update_id
                """,
                poll_id, user_id, update_id,
            )
            if accepted is None:
                return
            if choice is None:
                await conn.execute(
                    """
                    DELETE FROM screw.attendance_marks
                    WHERE poll_run_id = $1 AND user_id = $2 AND source = 'poll'
                    """,
                    poll_id, user_id,
                )
                return
            await conn.execute(
                """
                INSERT INTO screw.attendance_marks
                    (user_id, mark_date, choice, poll_run_id, source)
                SELECT $1, $2, $3, $4, 'poll'
                WHERE NOT EXISTS (
                    SELECT 1 FROM screw.user_day_statuses
                    WHERE user_id = $1 AND status_date = $2 AND kind IN ('VACATION', 'SICK', 'LEAVE')
                )
                ON CONFLICT (user_id, mark_date) DO UPDATE
                SET choice = EXCLUDED.choice, poll_run_id = EXCLUDED.poll_run_id, updated_at = NOW()
                WHERE attendance_marks.source = 'poll'
                """,
                user_id, poll_date, choice.value, poll_id,
            )

    @staticmethod
    async def get_mark(user_id: int, mark_date: date) -> dict[str, Any] | None:
        """
        Получить отметку пользователя за дату.

        Args:
            user_id: ID пользователя.
            mark_date: Дата.

        Returns:
            Данные отметки или None.
        """
        async with acquire_connection() as conn:
            row = await conn.fetchrow(
                """
                SELECT * FROM screw.attendance_marks
                WHERE user_id = $1 AND mark_date = $2
                """,
                user_id,
                mark_date,
            )
            return dict(row) if row else None

    @staticmethod
    async def get_marks_for_date(
        mark_date: date,
        department_id: int | None = None,
    ) -> list[dict[str, Any]]:
        """
        Получить все отметки за дату.

        Args:
            mark_date: Дата.
            department_id: Опциональный фильтр по отделу.

        Returns:
            Список отметок.
        """
        async with acquire_connection() as conn:
            if department_id is not None:
                rows = await conn.fetch(
                    """
                    SELECT am.*, u.telegram_id, u.full_name
                    FROM screw.attendance_marks am
                    JOIN screw.users u ON am.user_id = u.id
                    WHERE am.mark_date = $1
                      AND u.department_id = $2
                    ORDER BY u.full_name
                    """,
                    mark_date,
                    department_id,
                )
            else:
                rows = await conn.fetch(
                    """
                    SELECT am.*, u.telegram_id, u.full_name
                    FROM screw.attendance_marks am
                    JOIN screw.users u ON am.user_id = u.id
                    WHERE am.mark_date = $1
                    ORDER BY u.full_name
                    """,
                    mark_date,
                )
            return [dict(row) for row in rows]

    @staticmethod
    async def get_marks_for_user_range(
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
        async with acquire_connection() as conn:
            rows = await conn.fetch(
                """
                SELECT * FROM screw.attendance_marks
                WHERE user_id = $1 AND mark_date BETWEEN $2 AND $3
                ORDER BY mark_date
                """,
                user_id,
                start_date,
                end_date,
            )
            return [dict(row) for row in rows]

    @staticmethod
    async def count_marks_by_choice_for_date(
        mark_date: date,
        department_id: int | None = None,
    ) -> dict[str, int]:
        """
        Подсчитать количество отметок по типу за дату.

        Группирует по choice и возвращает счётчики.
        Учитывает только активных пользователей.

        Args:
            mark_date: Дата.
            department_id: Опциональный фильтр по отделу.

        Returns:
            Словарь choice -> count.
        """
        async with acquire_connection() as conn:
            if department_id is not None:
                rows = await conn.fetch(
                    """
                    SELECT am.choice, COUNT(*) as count
                    FROM screw.attendance_marks am
                    JOIN screw.users u ON am.user_id = u.id
                    WHERE am.mark_date = $1
                      AND u.department_id = $2
                      AND u.status = 'active'
                    GROUP BY am.choice
                    """,
                    mark_date,
                    department_id,
                )
            else:
                rows = await conn.fetch(
                    """
                    SELECT am.choice, COUNT(*) as count
                    FROM screw.attendance_marks am
                    JOIN screw.users u ON am.user_id = u.id
                    WHERE am.mark_date = $1
                      AND u.status = 'active'
                    GROUP BY am.choice
                    """,
                    mark_date,
                )
            return {row["choice"]: row["count"] for row in rows}

    @staticmethod
    async def count_total_marks_for_date(
        mark_date: date,
        department_id: int | None = None,
    ) -> int:
        """
        Подсчитать общее количество отметок за дату.

        Учитывает только активных пользователей.

        Args:
            mark_date: Дата.
            department_id: Опциональный фильтр по отделу.

        Returns:
            Количество отметок.
        """
        async with acquire_connection() as conn:
            if department_id is not None:
                count = await conn.fetchval(
                    """
                    SELECT COUNT(*)
                    FROM screw.attendance_marks am
                    JOIN screw.users u ON am.user_id = u.id
                    WHERE am.mark_date = $1
                      AND u.department_id = $2
                      AND u.status = 'active'
                    """,
                    mark_date,
                    department_id,
                )
            else:
                count = await conn.fetchval(
                    """
                    SELECT COUNT(*)
                    FROM screw.attendance_marks am
                    JOIN screw.users u ON am.user_id = u.id
                    WHERE am.mark_date = $1
                      AND u.status = 'active'
                    """,
                    mark_date,
                )
            return count or 0

    @staticmethod
    async def count_marks_by_choice_for_period(
        start_date: date,
        end_date: date,
        department_id: int | None = None,
    ) -> dict[str, int]:
        """
        Подсчитать количество отметок по типу за период.

        Группирует по choice и возвращает счётчики.
        Учитывает только активных пользователей.

        Args:
            start_date: Начало периода.
            end_date: Конец периода.
            department_id: Опциональный фильтр по отделу.

        Returns:
            Словарь choice -> count.
        """
        async with acquire_connection() as conn:
            if department_id is not None:
                rows = await conn.fetch(
                    """
                    SELECT am.choice, COUNT(*) as count
                    FROM screw.attendance_marks am
                    JOIN screw.users u ON am.user_id = u.id
                    WHERE am.mark_date BETWEEN $1 AND $2
                      AND u.department_id = $3
                      AND u.status = 'active'
                    GROUP BY am.choice
                    """,
                    start_date,
                    end_date,
                    department_id,
                )
            else:
                rows = await conn.fetch(
                    """
                    SELECT am.choice, COUNT(*) as count
                    FROM screw.attendance_marks am
                    JOIN screw.users u ON am.user_id = u.id
                    WHERE am.mark_date BETWEEN $1 AND $2
                      AND u.status = 'active'
                    GROUP BY am.choice
                    """,
                    start_date,
                    end_date,
                )
            return {row["choice"]: row["count"] for row in rows}

    @staticmethod
    async def get_user_monthly_choice_counts(
        user_id: int,
        year: int,
        month: int,
    ) -> dict[str, int]:
        """
        Подсчитать отметки пользователя по типу за месяц.

        Returns:
            Словарь choice -> count.
        """
        from calendar import monthrange

        start = date(year, month, 1)
        _, last_day = monthrange(year, month)
        end = date(year, month, last_day)

        async with acquire_connection() as conn:
            rows = await conn.fetch(
                """
                SELECT choice, COUNT(*) as count
                FROM screw.attendance_marks
                WHERE user_id = $1 AND mark_date BETWEEN $2 AND $3
                GROUP BY choice
                """,
                user_id,
                start,
                end,
            )
            return {row["choice"]: row["count"] for row in rows}

    @staticmethod
    async def get_user_response_count_for_month(
        user_id: int,
        year: int,
        month: int,
    ) -> int:
        """Подсчитать количество ответов пользователя на опросы за месяц."""
        from calendar import monthrange

        start = date(year, month, 1)
        _, last_day = monthrange(year, month)
        end = date(year, month, last_day)

        async with acquire_connection() as conn:
            count = await conn.fetchval(
                """
                SELECT COUNT(*) FROM screw.attendance_marks
                WHERE user_id = $1 AND mark_date BETWEEN $2 AND $3
                """,
                user_id,
                start,
                end,
            )
            return count or 0

    @staticmethod
    async def get_response_times_for_month(
        year: int,
        month: int,
        department_id: int | None = None,
    ) -> list[dict[str, Any]]:
        """
        Получить времена ответов на опросы за месяц.

        Returns:
            Список записей с user_id, full_name, response_seconds.
        """
        from calendar import monthrange

        start = date(year, month, 1)
        _, last_day = monthrange(year, month)
        end = date(year, month, last_day)

        async with acquire_connection() as conn:
            query = """
                SELECT
                    u.id as user_id,
                    u.full_name,
                    am.created_at as responded_at,
                    pr.published_at,
                    EXTRACT(EPOCH FROM (am.created_at - pr.published_at)) as response_seconds
                FROM screw.attendance_marks am
                JOIN screw.users u ON am.user_id = u.id
                JOIN screw.poll_runs pr ON am.poll_run_id = pr.id
                WHERE am.mark_date BETWEEN $1 AND $2
                  AND u.status = 'active'
                  AND pr.published_at IS NOT NULL
                  AND am.created_at IS NOT NULL
            """
            params: list = [start, end]
            if department_id is not None:
                query += " AND u.department_id = $3"
                params.append(department_id)
            query += " ORDER BY response_seconds ASC"

            rows = await conn.fetch(query, *params)
            return [dict(row) for row in rows]

    @staticmethod
    async def get_users_without_mark(mark_date: date) -> list[dict[str, Any]]:
        """
        Получить активных пользователей без отметки за дату.

        Args:
            mark_date: Дата.

        Returns:
            Список пользователей.
        """
        async with acquire_connection() as conn:
            rows = await conn.fetch(
                """
                SELECT u.*
                FROM screw.users u
                WHERE u.status = 'active'
                  AND NOT EXISTS (
                      SELECT 1 FROM screw.attendance_marks am
                      WHERE am.user_id = u.id AND am.mark_date = $1
                  )
                ORDER BY u.full_name
                """,
                mark_date,
            )
            return [dict(row) for row in rows]
