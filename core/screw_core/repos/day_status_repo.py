"""
Репозиторий статусов дней.

Работа с таблицей user_day_statuses (отпуск, больничный, отгул).
"""

from datetime import date
from typing import Any

from core.screw_core.db.tx import acquire_connection, parse_rowcount
from core.screw_core.models.enums import DayStatusKind


class DayStatusRepo:
    """Репозиторий для работы со статусами дней."""

    @staticmethod
    async def set_status(
        user_id: int,
        status_date: date,
        kind: DayStatusKind,
        comment: str | None = None,
        created_by: int | None = None,
    ) -> int:
        """
        Установить статус дня для пользователя.

        Args:
            user_id: ID пользователя.
            status_date: Дата.
            kind: Вид статуса (отпуск/больничный/отгул).
            comment: Комментарий.
            created_by: Кто установил статус.

        Returns:
            ID записи.
        """
        async with acquire_connection() as conn:
            status_id = await conn.fetchval(
                """
                INSERT INTO screw.user_day_statuses
                    (user_id, status_date, kind, comment, created_by)
                VALUES ($1, $2, $3, $4, $5)
                ON CONFLICT (user_id, status_date) DO UPDATE
                SET kind = $3, comment = $4, updated_at = NOW()
                RETURNING id
                """,
                user_id,
                status_date,
                kind.value,
                comment,
                created_by,
            )
            return status_id

    @staticmethod
    async def set_status_range(
        user_id: int,
        start_date: date,
        end_date: date,
        kind: DayStatusKind,
        comment: str | None = None,
        created_by: int | None = None,
    ) -> int:
        """
        Установить статус для диапазона дат.

        Args:
            user_id: ID пользователя.
            start_date: Начало периода.
            end_date: Конец периода.
            kind: Вид статуса.
            comment: Комментарий.
            created_by: Кто установил.

        Returns:
            Количество созданных/обновлённых записей.
        """
        count = 0
        current = start_date
        while current <= end_date:
            await DayStatusRepo.set_status(user_id, current, kind, comment, created_by)
            count += 1
            # Переход к следующему дню
            from datetime import timedelta
            current = current + timedelta(days=1)
        return count

    @staticmethod
    async def get_status(user_id: int, status_date: date) -> dict[str, Any] | None:
        """
        Получить статус пользователя на дату.

        Args:
            user_id: ID пользователя.
            status_date: Дата.

        Returns:
            Данные статуса или None.
        """
        async with acquire_connection() as conn:
            row = await conn.fetchrow(
                """
                SELECT * FROM screw.user_day_statuses
                WHERE user_id = $1 AND status_date = $2
                """,
                user_id,
                status_date,
            )
            return dict(row) if row else None

    @staticmethod
    async def get_statuses_for_date(status_date: date) -> list[dict[str, Any]]:
        """
        Получить все статусы на дату с end_date (последний день подряд).

        Args:
            status_date: Дата.

        Returns:
            Список статусов с полем end_date — последний день статуса.
        """
        async with acquire_connection() as conn:
            # Используем window function чтобы найти конец периода
            # Группируем подряд идущие дни с одинаковым статусом
            rows = await conn.fetch(
                """
                WITH current_statuses AS (
                    SELECT uds.*, u.telegram_id, u.full_name
                    FROM screw.user_day_statuses uds
                    JOIN screw.users u ON uds.user_id = u.id
                    WHERE uds.status_date = $1
                ),
                -- Для каждого пользователя находим end_date: последний подряд идущий день
                -- с тем же kind начиная от status_date
                end_dates AS (
                    SELECT
                        cs.user_id,
                        cs.kind,
                        (
                            SELECT MAX(uds2.status_date)
                            FROM screw.user_day_statuses uds2
                            WHERE uds2.user_id = cs.user_id
                              AND uds2.kind = cs.kind
                              AND uds2.status_date >= $1
                              -- Проверяем что все дни между $1 и uds2.status_date заполнены
                              AND NOT EXISTS (
                                  SELECT 1
                                  FROM generate_series($1::date, uds2.status_date, '1 day'::interval) AS d(dt)
                                  WHERE NOT EXISTS (
                                      SELECT 1 FROM screw.user_day_statuses uds3
                                      WHERE uds3.user_id = cs.user_id
                                        AND uds3.kind = cs.kind
                                        AND uds3.status_date = d.dt::date
                                  )
                              )
                        ) AS end_date
                    FROM current_statuses cs
                )
                SELECT cs.*, ed.end_date
                FROM current_statuses cs
                JOIN end_dates ed ON cs.user_id = ed.user_id AND cs.kind = ed.kind
                ORDER BY cs.full_name
                """,
                status_date,
            )
            return [dict(row) for row in rows]

    @staticmethod
    async def delete_status(user_id: int, status_date: date) -> bool:
        """
        Удалить статус дня.

        Args:
            user_id: ID пользователя.
            status_date: Дата.

        Returns:
            True если удалено, False если не найдено.
        """
        async with acquire_connection() as conn:
            result = await conn.execute(
                """
                DELETE FROM screw.user_day_statuses
                WHERE user_id = $1 AND status_date = $2
                """,
                user_id,
                status_date,
            )
            return parse_rowcount(result) == 1

    @staticmethod
    async def delete_statuses_range(
        user_id: int,
        start_date: date,
        end_date: date,
        kinds: list[str] | None = None,
    ) -> int:
        """
        Удалить статусы пользователя за диапазон дат.

        Args:
            user_id: ID пользователя.
            start_date: Начало диапазона.
            end_date: Конец диапазона.
            kinds: Опциональный список типов для фильтрации.

        Returns:
            Количество удалённых записей.
        """
        async with acquire_connection() as conn:
            if kinds:
                result = await conn.execute(
                    """
                    DELETE FROM screw.user_day_statuses
                    WHERE user_id = $1
                      AND status_date BETWEEN $2 AND $3
                      AND kind = ANY($4)
                    """,
                    user_id,
                    start_date,
                    end_date,
                    kinds,
                )
            else:
                result = await conn.execute(
                    """
                    DELETE FROM screw.user_day_statuses
                    WHERE user_id = $1
                      AND status_date BETWEEN $2 AND $3
                    """,
                    user_id,
                    start_date,
                    end_date,
                )
            return parse_rowcount(result)

    @staticmethod
    async def get_user_statuses_range(
        user_id: int,
        start_date: date,
        end_date: date,
    ) -> list[dict[str, Any]]:
        """Получить статусы пользователя за диапазон дат."""
        async with acquire_connection() as conn:
            rows = await conn.fetch(
                """
                SELECT * FROM screw.user_day_statuses
                WHERE user_id = $1 AND status_date BETWEEN $2 AND $3
                ORDER BY status_date
                """,
                user_id,
                start_date,
                end_date,
            )
            return [dict(row) for row in rows]

    @staticmethod
    async def count_statuses_by_kind_for_period(
        start_date: date,
        end_date: date,
        department_id: int | None = None,
    ) -> dict[str, int]:
        """
        Подсчитать количество дней по типу статуса за период.

        Args:
            start_date: Начало периода.
            end_date: Конец периода.
            department_id: Опциональный фильтр по отделу.

        Returns:
            Словарь kind -> количество дней.
        """
        async with acquire_connection() as conn:
            if department_id is not None:
                rows = await conn.fetch(
                    """
                    SELECT uds.kind, COUNT(*) AS cnt
                    FROM screw.user_day_statuses uds
                    JOIN screw.users u ON uds.user_id = u.id
                    WHERE uds.status_date BETWEEN $1 AND $2
                      AND u.department_id = $3
                      AND u.status = 'active'
                    GROUP BY uds.kind
                    """,
                    start_date,
                    end_date,
                    department_id,
                )
            else:
                rows = await conn.fetch(
                    """
                    SELECT kind, COUNT(*) AS cnt
                    FROM screw.user_day_statuses
                    WHERE status_date BETWEEN $1 AND $2
                    GROUP BY kind
                    """,
                    start_date,
                    end_date,
                )
            return {row["kind"]: row["cnt"] for row in rows}

    @staticmethod
    async def get_statuses_for_date_range(
        start_date: date,
        end_date: date,
        user_ids: list[int] | None = None,
    ) -> dict[tuple[int, date], str]:
        """
        Получить статусы дней за диапазон дат.

        Возвращает словарь (user_id, status_date) -> kind.
        Эффективно для batch-проверки изменений.

        Args:
            start_date: Начало диапазона.
            end_date: Конец диапазона.
            user_ids: Опциональный фильтр по user_id.

        Returns:
            Словарь (user_id, date) -> kind.
        """
        async with acquire_connection() as conn:
            if user_ids:
                rows = await conn.fetch(
                    """
                    SELECT user_id, status_date, kind
                    FROM screw.user_day_statuses
                    WHERE status_date BETWEEN $1 AND $2
                      AND user_id = ANY($3)
                    """,
                    start_date,
                    end_date,
                    user_ids,
                )
            else:
                rows = await conn.fetch(
                    """
                    SELECT user_id, status_date, kind
                    FROM screw.user_day_statuses
                    WHERE status_date BETWEEN $1 AND $2
                    """,
                    start_date,
                    end_date,
                )
            return {(row["user_id"], row["status_date"]): row["kind"] for row in rows}
