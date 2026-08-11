"""
Репозиторий опросов.

Работа с таблицей poll_runs.
"""

from datetime import date, datetime
from typing import Any

from core.screw_core.db.tx import acquire_connection, parse_rowcount, transaction
from core.screw_core.models.enums import PollStatus


class PollsRepo:
    """Репозиторий для работы с опросами явки."""

    @staticmethod
    async def create_poll(
        poll_date: date,
        chat_id: int,
        message_id: int | None = None,
        telegram_poll_id: str | None = None,
        closes_at: datetime | None = None,
    ) -> int:
        """
        Создать или пересоздать опрос на дату (UPSERT).

        При пересоздании обновляет существующую запись:
        - Новый message_id и telegram_poll_id
        - Сброс reminder_sent_at
        - Статус 'open'

        Args:
            poll_date: Дата опроса.
            chat_id: ID чата для публикации.
            message_id: ID сообщения в Telegram (если уже известен).
            telegram_poll_id: ID опроса в Telegram (для native polls).
            closes_at: Время автоматического закрытия опроса.

        Returns:
            ID опроса.
        """
        async with acquire_connection() as conn:
            now = datetime.now()
            poll_id = await conn.fetchval(
                """
                INSERT INTO screw.poll_runs
                    (poll_date, chat_id, message_id, telegram_poll_id, published_at, closes_at, status)
                VALUES ($1, $2, $3, $4, $5, $6, 'open')
                ON CONFLICT (poll_date) DO UPDATE SET
                    chat_id = EXCLUDED.chat_id,
                    message_id = EXCLUDED.message_id,
                    telegram_poll_id = EXCLUDED.telegram_poll_id,
                    published_at = EXCLUDED.published_at,
                    closes_at = EXCLUDED.closes_at,
                    status = 'open',
                    reminder_sent_at = NULL,
                    closed_at = NULL
                RETURNING id
                """,
                poll_date,
                chat_id,
                message_id,
                telegram_poll_id,
                now if message_id else None,
                closes_at,
            )
            return poll_id

    @staticmethod
    async def create_poll_with_expected_users(
        poll_date: date,
        chat_id: int,
        message_id: int | None = None,
        telegram_poll_id: str | None = None,
        closes_at: datetime | None = None,
    ) -> tuple[int, int]:
        """Create/recreate a poll and replace its expected-user snapshot atomically."""
        async with transaction() as conn:
            existing_poll_id = await conn.fetchval(
                "SELECT id FROM screw.poll_runs WHERE poll_date = $1 FOR UPDATE",
                poll_date,
            )
            deleted_marks = 0
            if existing_poll_id is not None:
                result = await conn.execute(
                    "DELETE FROM screw.attendance_marks WHERE poll_run_id = $1",
                    existing_poll_id,
                )
                deleted_marks = parse_rowcount(result)

            captured_at = datetime.now()
            poll_id = await conn.fetchval(
                """
                INSERT INTO screw.poll_runs
                    (poll_date, chat_id, message_id, telegram_poll_id, published_at,
                     closes_at, status, expected_users_snapshot_at)
                VALUES ($1, $2, $3, $4, $5, $6, 'open', $7)
                ON CONFLICT (poll_date) DO UPDATE SET
                    chat_id = EXCLUDED.chat_id,
                    message_id = EXCLUDED.message_id,
                    telegram_poll_id = EXCLUDED.telegram_poll_id,
                    published_at = EXCLUDED.published_at,
                    closes_at = EXCLUDED.closes_at,
                    status = 'open',
                    reminder_sent_at = NULL,
                    closed_at = NULL,
                    expected_users_snapshot_at = EXCLUDED.expected_users_snapshot_at
                RETURNING id
                """,
                poll_date,
                chat_id,
                message_id,
                telegram_poll_id,
                captured_at if message_id else None,
                closes_at,
                captured_at,
            )

            await conn.execute(
                "DELETE FROM screw.poll_expected_users WHERE poll_run_id = $1",
                poll_id,
            )
            await conn.execute(
                """
                INSERT INTO screw.poll_expected_users
                    (poll_run_id, user_id, department_id, captured_at)
                SELECT $1, u.id, u.department_id, $3
                FROM screw.users u
                WHERE u.status = 'active'
                  AND u.department_id IS NOT NULL
                  AND u.role <> 'no_tg_worker'
                  AND (u.work_mode IS NULL OR u.work_mode <> 'DUTY')
                  AND NOT EXISTS (
                      SELECT 1
                      FROM screw.user_day_statuses uds
                      WHERE uds.user_id = u.id
                        AND uds.status_date = $2
                        AND uds.kind IN ('VACATION', 'SICK', 'LEAVE')
                  )
                ON CONFLICT (poll_run_id, user_id) DO NOTHING
                """,
                poll_id,
                poll_date,
                captured_at,
            )
            return int(poll_id), deleted_marks

    @staticmethod
    async def get_by_date(poll_date: date) -> dict[str, Any] | None:
        """
        Получить опрос по дате.

        Args:
            poll_date: Дата опроса.

        Returns:
            Данные опроса или None.
        """
        async with acquire_connection() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM screw.poll_runs WHERE poll_date = $1",
                poll_date,
            )
            return dict(row) if row else None

    @staticmethod
    async def get_by_id(poll_id: int) -> dict[str, Any] | None:
        """
        Получить опрос по ID.

        Args:
            poll_id: ID опроса.

        Returns:
            Данные опроса или None.
        """
        async with acquire_connection() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM screw.poll_runs WHERE id = $1",
                poll_id,
            )
            return dict(row) if row else None

    @staticmethod
    async def update_message_id(poll_id: int, message_id: int) -> None:
        """
        Обновить ID сообщения опроса.

        Args:
            poll_id: ID опроса.
            message_id: ID сообщения в Telegram.
        """
        async with acquire_connection() as conn:
            await conn.execute(
                """
                UPDATE screw.poll_runs
                SET message_id = $2, published_at = $3
                WHERE id = $1
                """,
                poll_id,
                message_id,
                datetime.now(),
            )

    @staticmethod
    async def mark_reminder_sent(poll_id: int, message_id: int | None = None) -> None:
        """
        Отметить отправку напоминания и сохранить ID сообщения.

        Args:
            poll_id: ID опроса.
            message_id: ID сообщения-напоминания в Telegram.
        """
        async with acquire_connection() as conn:
            await conn.execute(
                """
                UPDATE screw.poll_runs
                SET reminder_sent_at = $2, reminder_message_id = $3
                WHERE id = $1
                """,
                poll_id,
                datetime.now(),
                message_id,
            )

    @staticmethod
    async def get_reminder_message_id(poll_id: int) -> int | None:
        """
        Получить ID сообщения-напоминания.

        Args:
            poll_id: ID опроса.

        Returns:
            ID сообщения или None.
        """
        async with acquire_connection() as conn:
            return await conn.fetchval(
                "SELECT reminder_message_id FROM screw.poll_runs WHERE id = $1",
                poll_id,
            )

    @staticmethod
    async def clear_reminder_message_id(poll_id: int) -> None:
        """
        Сбросить ID сообщения-напоминания.

        Args:
            poll_id: ID опроса.
        """
        async with acquire_connection() as conn:
            await conn.execute(
                "UPDATE screw.poll_runs SET reminder_message_id = NULL WHERE id = $1",
                poll_id,
            )

    @staticmethod
    async def clear_attendance_marks(poll_run_id: int) -> int:
        """
        Удалить все отметки явки для опроса.

        Используется при пересоздании опроса для очистки старых голосов.

        Args:
            poll_run_id: ID опроса.

        Returns:
            Количество удалённых записей.
        """
        async with acquire_connection() as conn:
            result = await conn.execute(
                "DELETE FROM screw.attendance_marks WHERE poll_run_id = $1",
                poll_run_id,
            )
            return parse_rowcount(result)

    @staticmethod
    async def close_poll(poll_id: int) -> None:
        """
        Закрыть опрос.

        Args:
            poll_id: ID опроса.
        """
        async with transaction() as conn:
            poll_status = await conn.fetchval(
                "SELECT status FROM screw.poll_runs WHERE id = $1 FOR UPDATE",
                poll_id,
            )
            if poll_status != PollStatus.OPEN.value:
                return

            await conn.execute(
                """
                DELETE FROM screw.poll_expected_users pe
                USING screw.users u, screw.poll_runs pr
                WHERE pe.poll_run_id = $1
                  AND pe.user_id = u.id
                  AND pr.id = pe.poll_run_id
                  AND (
                      u.status <> 'active'
                      OR u.role = 'no_tg_worker'
                      OR u.work_mode = 'DUTY'
                      OR EXISTS (
                          SELECT 1
                          FROM screw.user_day_statuses uds
                          WHERE uds.user_id = u.id
                            AND uds.status_date = pr.poll_date
                            AND uds.kind IN ('VACATION', 'SICK', 'LEAVE')
                      )
                  )
                """,
                poll_id,
            )
            await conn.execute(
                """
                UPDATE screw.poll_runs
                SET status = $2, closed_at = $3
                WHERE id = $1 AND status = 'open'
                """,
                poll_id,
                PollStatus.CLOSED.value,
                datetime.now(),
            )

    @staticmethod
    async def get_open_poll() -> dict[str, Any] | None:
        """
        Получить текущий открытый опрос.

        Returns:
            Данные опроса или None.
        """
        async with acquire_connection() as conn:
            row = await conn.fetchrow(
                """
                SELECT * FROM screw.poll_runs
                WHERE status = 'open'
                ORDER BY poll_date DESC
                LIMIT 1
                """,
            )
            return dict(row) if row else None

    @staticmethod
    async def get_open_poll_for_date(poll_date: date) -> dict[str, Any] | None:
        """
        Получить открытый опрос на конкретную дату.

        Args:
            poll_date: Дата опроса.

        Returns:
            Данные опроса или None.
        """
        async with acquire_connection() as conn:
            row = await conn.fetchrow(
                """
                SELECT * FROM screw.poll_runs
                WHERE poll_date = $1 AND status = 'open'
                """,
                poll_date,
            )
            return dict(row) if row else None

    @staticmethod
    async def get_poll_by_message(chat_id: int, message_id: int) -> dict[str, Any] | None:
        """
        Получить опрос по chat_id и message_id.

        Args:
            chat_id: ID чата.
            message_id: ID сообщения.

        Returns:
            Данные опроса или None.
        """
        async with acquire_connection() as conn:
            row = await conn.fetchrow(
                """
                SELECT * FROM screw.poll_runs
                WHERE chat_id = $1 AND message_id = $2
                """,
                chat_id,
                message_id,
            )
            return dict(row) if row else None

    @staticmethod
    async def get_by_telegram_poll_id(telegram_poll_id: str) -> dict[str, Any] | None:
        """
        Получить опрос по Telegram poll ID.

        Args:
            telegram_poll_id: ID опроса в Telegram.

        Returns:
            Данные опроса или None.
        """
        async with acquire_connection() as conn:
            row = await conn.fetchrow(
                """
                SELECT * FROM screw.poll_runs
                WHERE telegram_poll_id = $1
                """,
                telegram_poll_id,
            )
            return dict(row) if row else None

    @staticmethod
    async def update_telegram_poll_id(poll_id: int, telegram_poll_id: str) -> None:
        """
        Обновить ID Telegram опроса.

        Args:
            poll_id: ID опроса в БД.
            telegram_poll_id: ID опроса в Telegram.
        """
        async with acquire_connection() as conn:
            await conn.execute(
                """
                UPDATE screw.poll_runs
                SET telegram_poll_id = $2
                WHERE id = $1
                """,
                poll_id,
                telegram_poll_id,
            )

    @staticmethod
    async def get_poll_count_for_month(year: int, month: int) -> int:
        """Подсчитать количество опросов за месяц (рабочих дней)."""
        from calendar import monthrange

        start = date(year, month, 1)
        _, last_day = monthrange(year, month)
        end = date(year, month, last_day)

        async with acquire_connection() as conn:
            count = await conn.fetchval(
                """
                SELECT COUNT(*) FROM screw.poll_runs
                WHERE poll_date BETWEEN $1 AND $2
                """,
                start,
                end,
            )
            return count or 0

    @staticmethod
    async def get_expired_open_polls(
        duration_seconds: int,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        """
        Получить список просроченных открытых опросов.

        Опрос считается просроченным если:
        - status = 'open'
        - closes_at <= now() (если closes_at задан)
        - ИЛИ published_at + duration_seconds <= now() (fallback для старых записей без closes_at)

        Args:
            duration_seconds: Длительность опроса в секундах (используется для fallback).
            limit: Максимальное количество опросов.

        Returns:
            Список данных опросов.
        """
        async with acquire_connection() as conn:
            rows = await conn.fetch(
                """
                SELECT * FROM screw.poll_runs
                WHERE status = 'open'
                  AND (
                      (closes_at IS NOT NULL AND closes_at <= NOW())
                      OR (closes_at IS NULL AND published_at IS NOT NULL
                          AND published_at + ($1 || ' seconds')::INTERVAL <= NOW())
                  )
                ORDER BY COALESCE(closes_at, published_at) ASC
                LIMIT $2
                """,
                str(duration_seconds),
                limit,
            )
            return [dict(row) for row in rows]

    @staticmethod
    async def get_non_respondents(
        poll_id: int,
        department_id: int | None = None,
    ) -> list[dict[str, Any]]:
        """
        Получить список активных пользователей, не ответивших на ТЕКУЩИЙ опрос.

        ВАЖНО: Проверяет отметки по poll_run_id, а не по mark_date.
        Это критично для корректной работы после пересоздания опроса —
        пользователи, голосовавшие в старом опросе, должны попасть в список
        неотметившихся для нового опроса.

        Args:
            poll_id: ID опроса (poll_run.id).
            department_id: Опциональный фильтр по отделу.

        Returns:
            Список пользователей (id, telegram_id, full_name, role).
        """
        async with acquire_connection() as conn:
            # Получаем дату опроса
            poll = await conn.fetchrow(
                """
                SELECT poll_date, status, expected_users_snapshot_at
                FROM screw.poll_runs
                WHERE id = $1
                """,
                poll_id,
            )
            if not poll:
                return []

            poll_date = poll["poll_date"]

            poll_status = poll.get("status", PollStatus.OPEN.value)
            snapshot_at = poll.get("expected_users_snapshot_at")
            if poll_status != PollStatus.OPEN.value and snapshot_at is not None:
                params: list[Any] = [poll_id]
                department_filter = ""
                if department_id is not None:
                    params.append(department_id)
                    department_filter = f" AND pe.department_id = ${len(params)}"
                rows = await conn.fetch(
                    f"""
                    SELECT u.id, u.telegram_id, u.full_name, pe.department_id, u.role
                    FROM screw.poll_expected_users pe
                    JOIN screw.users u ON u.id = pe.user_id
                    WHERE pe.poll_run_id = $1
                      {department_filter}
                      AND NOT EXISTS (
                          SELECT 1 FROM screw.attendance_marks am
                          WHERE am.user_id = pe.user_id AND am.poll_run_id = pe.poll_run_id
                      )
                    ORDER BY u.full_name
                    """,
                    *params,
                )
                return [dict(row) for row in rows]

            # Активные пользователи, у которых нет отметки в ЭТОМ КОНКРЕТНОМ опросе
            # (проверяем по poll_run_id, а не по mark_date!)
            # и нет статуса отсутствия (отпуск/больничный/отгул)
            # DAY_OFF (выходной из графика) НЕ исключает — если опрос создан, все должны отмечаться
            # DUTY (дежурные) исключаются — они не обязаны отмечаться
            # Админы и менеджеры НЕ исключаются — они тоже должны отмечаться
            params: list[Any] = [poll_id, poll_date]
            department_filter = ""
            if department_id is not None:
                params.append(department_id)
                department_filter = f" AND u.department_id = ${len(params)}"

            rows = await conn.fetch(
                f"""
                SELECT u.id, u.telegram_id, u.full_name, u.department_id, u.role
                FROM screw.users u
                WHERE u.status = 'active'
                  AND u.role <> 'no_tg_worker'
                  AND (u.work_mode IS NULL OR u.work_mode != 'DUTY')
                  {department_filter}
                  AND NOT EXISTS (
                      SELECT 1 FROM screw.attendance_marks am
                      WHERE am.user_id = u.id AND am.poll_run_id = $1
                  )
                  AND NOT EXISTS (
                      SELECT 1 FROM screw.user_day_statuses uds
                      WHERE uds.user_id = u.id AND uds.status_date = $2
                        AND uds.kind IN ('VACATION', 'SICK', 'LEAVE')
                  )
                ORDER BY u.full_name
                """,
                *params,
            )
            return [dict(row) for row in rows]

    @staticmethod
    async def get_latest_published_summary() -> dict[str, Any] | None:
        """Return the latest published poll and an aggregate response count."""
        async with acquire_connection() as conn:
            row = await conn.fetchrow(
                """
                SELECT pr.id,
                       pr.poll_date,
                       pr.published_at,
                       pr.closed_at,
                       pr.status,
                       COUNT(DISTINCT am.user_id)::int AS response_count
                FROM screw.poll_runs pr
                LEFT JOIN screw.attendance_marks am ON am.poll_run_id = pr.id
                WHERE pr.published_at IS NOT NULL
                GROUP BY pr.id
                ORDER BY pr.published_at DESC
                LIMIT 1
                """
            )
            return dict(row) if row else None
