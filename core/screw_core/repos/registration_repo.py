"""
Репозиторий регистрации.

Работа с приглашениями и заявками на регистрацию.

Схема БД (screw.registration_invites):
- token varchar(64) UNIQUE — plaintext токен
- created_by bigint FK users(id) — кто создал приглашение
- used_by bigint FK users(id) — ID созданного пользователя после approve
- used_at timestamptz — когда приглашение было использовано (после approve)
- expires_at timestamptz — срок действия токена
"""

from datetime import datetime
from typing import Any

from core.screw_core.db.tx import acquire_connection, parse_rowcount
from core.screw_core.models.enums import WorkMode


class RegistrationRepo:
    """Репозиторий для приглашений и заявок на регистрацию."""

    # =========================================================================
    # Приглашения (invites)
    # =========================================================================

    @staticmethod
    async def create_invite(
        token: str,
        department_id: int,
        expected_full_name: str | None = None,
        work_mode: WorkMode = WorkMode.HYBRID,
        created_by: int | None = None,
        expires_at: datetime | None = None,
    ) -> int:
        """
        Создать приглашение на регистрацию.

        Args:
            token: Уникальный токен приглашения (plaintext, до 64 символов).
            department_id: ID отдела.
            expected_full_name: Ожидаемое ФИО сотрудника.
            work_mode: Режим работы.
            created_by: ID пользователя, создавшего приглашение.
            expires_at: Дата истечения.

        Returns:
            ID приглашения.
        """
        async with acquire_connection() as conn:
            invite_id = await conn.fetchval(
                """
                INSERT INTO screw.registration_invites
                    (token, department_id, expected_full_name, work_mode, created_by, expires_at)
                VALUES ($1, $2, $3, $4, $5, $6)
                RETURNING id
                """,
                token,
                department_id,
                expected_full_name,
                work_mode.value,
                created_by,
                expires_at,
            )
            return invite_id

    @staticmethod
    async def get_invite_by_token(token: str) -> dict[str, Any] | None:
        """
        Получить приглашение по токену.

        Args:
            token: Токен приглашения (plaintext).

        Returns:
            Данные приглашения или None.
        """
        async with acquire_connection() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM screw.registration_invites WHERE token = $1",
                token,
            )
            return dict(row) if row else None

    @staticmethod
    async def mark_invite_used(invite_id: int, used_by_user_id: int) -> None:
        """
        Отметить приглашение как использованное.

        Вызывается после approve заявки, когда пользователь создан в БД.
        used_by — ID созданного пользователя (screw.users.id).

        Args:
            invite_id: ID приглашения.
            used_by_user_id: ID созданного пользователя в таблице users.
        """
        async with acquire_connection() as conn:
            await conn.execute(
                """
                UPDATE screw.registration_invites
                SET used_by = $2, used_at = $3
                WHERE id = $1
                """,
                invite_id,
                used_by_user_id,
                datetime.now(),
            )

    # =========================================================================
    # Заявки (requests)
    # =========================================================================

    @staticmethod
    async def create_request(
        telegram_id: int,
        invite_id: int,
        username: str | None = None,
        first_name: str | None = None,
        last_name: str | None = None,
    ) -> int:
        """
        Создать заявку на регистрацию.

        Args:
            telegram_id: Telegram ID пользователя.
            invite_id: ID использованного приглашения.
            username: Юзернейм.
            first_name: Имя.
            last_name: Фамилия.

        Returns:
            ID заявки.
        """
        async with acquire_connection() as conn:
            request_id = await conn.fetchval(
                """
                INSERT INTO screw.registration_requests
                    (telegram_id, invite_id, username, first_name, last_name)
                VALUES ($1, $2, $3, $4, $5)
                RETURNING id
                """,
                telegram_id,
                invite_id,
                username,
                first_name,
                last_name,
            )
            return request_id

    @staticmethod
    async def get_pending_requests(department_id: int | None = None) -> list[dict[str, Any]]:
        """
        Получить заявки, ожидающие подтверждения.

        Args:
            department_id: Фильтр по отделу (опционально).

        Returns:
            Список заявок.
        """
        async with acquire_connection() as conn:
            if department_id:
                rows = await conn.fetch(
                    """
                    SELECT r.*, i.department_id, i.expected_full_name
                    FROM screw.registration_requests r
                    JOIN screw.registration_invites i ON r.invite_id = i.id
                    WHERE r.status = 'pending' AND i.department_id = $1
                    ORDER BY r.created_at
                    """,
                    department_id,
                )
            else:
                rows = await conn.fetch(
                    """
                    SELECT r.*, i.department_id, i.expected_full_name
                    FROM screw.registration_requests r
                    JOIN screw.registration_invites i ON r.invite_id = i.id
                    WHERE r.status = 'pending'
                    ORDER BY r.created_at
                    """,
                )
            return [dict(row) for row in rows]

    @staticmethod
    async def approve_request(request_id: int, reviewed_by: int) -> None:
        """
        Подтвердить заявку на регистрацию.

        Args:
            request_id: ID заявки.
            reviewed_by: ID менеджера.
        """
        async with acquire_connection() as conn:
            await conn.execute(
                """
                UPDATE screw.registration_requests
                SET status = 'approved', reviewed_by = $2, reviewed_at = $3
                WHERE id = $1
                """,
                request_id,
                reviewed_by,
                datetime.now(),
            )

    @staticmethod
    async def reject_request(request_id: int, reviewed_by: int, reason: str | None = None) -> None:
        """
        Отклонить заявку на регистрацию.

        Args:
            request_id: ID заявки.
            reviewed_by: ID менеджера.
            reason: Причина отклонения.
        """
        async with acquire_connection() as conn:
            await conn.execute(
                """
                UPDATE screw.registration_requests
                SET status = 'rejected', reviewed_by = $2, reviewed_at = $3, reject_reason = $4
                WHERE id = $1
                """,
                request_id,
                reviewed_by,
                datetime.now(),
                reason,
            )

    @staticmethod
    async def get_request_by_id(request_id: int) -> dict[str, Any] | None:
        """
        Получить заявку по ID с данными приглашения.

        Args:
            request_id: ID заявки.

        Returns:
            Данные заявки с информацией из приглашения или None.
        """
        async with acquire_connection() as conn:
            row = await conn.fetchrow(
                """
                SELECT r.*, i.department_id, i.expected_full_name, i.work_mode, i.created_by
                FROM screw.registration_requests r
                JOIN screw.registration_invites i ON r.invite_id = i.id
                WHERE r.id = $1
                """,
                request_id,
            )
            return dict(row) if row else None

    @staticmethod
    async def cancel_pending_requests(telegram_id: int) -> int:
        """
        Отменить все pending-заявки пользователя.

        Используется при повторной регистрации по новому токену —
        старые необработанные заявки отменяются автоматически.

        Args:
            telegram_id: Telegram ID пользователя.

        Returns:
            Количество отменённых заявок.
        """
        async with acquire_connection() as conn:
            result = await conn.execute(
                """
                UPDATE screw.registration_requests
                SET status = 'cancelled', reviewed_at = NOW()
                WHERE telegram_id = $1 AND status = 'pending'
                """,
                telegram_id,
            )
            return parse_rowcount(result)

    @staticmethod
    async def get_pending_request_by_telegram_id(telegram_id: int) -> dict[str, Any] | None:
        """
        Получить ожидающую заявку пользователя.

        Args:
            telegram_id: Telegram ID пользователя.

        Returns:
            Данные заявки или None.
        """
        async with acquire_connection() as conn:
            row = await conn.fetchrow(
                """
                SELECT r.*, i.department_id, i.expected_full_name
                FROM screw.registration_requests r
                JOIN screw.registration_invites i ON r.invite_id = i.id
                WHERE r.telegram_id = $1 AND r.status = 'pending'
                ORDER BY r.created_at DESC
                LIMIT 1
                """,
                telegram_id,
            )
            return dict(row) if row else None

    @staticmethod
    async def get_invite_by_id(invite_id: int) -> dict[str, Any] | None:
        """
        Получить приглашение по ID.

        Args:
            invite_id: ID приглашения.

        Returns:
            Данные приглашения или None.
        """
        async with acquire_connection() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM screw.registration_invites WHERE id = $1",
                invite_id,
            )
            return dict(row) if row else None

    @staticmethod
    async def get_active_invite_by_creator(
        created_by: int,
        department_id: int,
        now: datetime,
    ) -> dict[str, Any] | None:
        """
        Получить активное приглашение создателя по отделу.

        Args:
            created_by: ID создателя приглашения (users.id).
            department_id: ID отдела.
            now: Текущее время.

        Returns:
            Активное приглашение или None.
        """
        async with acquire_connection() as conn:
            row = await conn.fetchrow(
                """
                SELECT *
                FROM screw.registration_invites
                WHERE created_by = $1
                  AND department_id = $2
                  AND used_by IS NULL
                  AND expires_at > $3
                ORDER BY created_at DESC
                LIMIT 1
                """,
                created_by,
                department_id,
                now,
            )
            return dict(row) if row else None

    @staticmethod
    async def get_invites_by_department(
        department_id: int,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        """
        Получить приглашения по отделу (все статусы).

        Args:
            department_id: ID отдела.
            limit: Максимальное количество.

        Returns:
            Список приглашений с именами создателя и использовавшего.
        """
        async with acquire_connection() as conn:
            rows = await conn.fetch(
                """
                SELECT i.*, u.full_name as creator_name, u2.full_name as used_by_name
                FROM screw.registration_invites i
                LEFT JOIN screw.users u ON u.id = i.created_by
                LEFT JOIN screw.users u2 ON u2.id = i.used_by
                WHERE i.department_id = $1
                ORDER BY i.created_at DESC
                LIMIT $2
                """,
                department_id,
                limit,
            )
            return [dict(row) for row in rows]

    @staticmethod
    async def get_all_invites(limit: int = 50) -> list[dict[str, Any]]:
        """
        Получить все приглашения по всем отделам.

        Args:
            limit: Максимальное количество.

        Returns:
            Список приглашений с именами создателя и использовавшего.
        """
        async with acquire_connection() as conn:
            rows = await conn.fetch(
                """
                SELECT i.*, u.full_name as creator_name, u2.full_name as used_by_name
                FROM screw.registration_invites i
                LEFT JOIN screw.users u ON u.id = i.created_by
                LEFT JOIN screw.users u2 ON u2.id = i.used_by
                ORDER BY i.created_at DESC
                LIMIT $1
                """,
                limit,
            )
            return [dict(row) for row in rows]

    @staticmethod
    async def expire_invite(invite_id: int) -> bool:
        """
        Досрочно истечь приглашение (сделать недействительным).

        Args:
            invite_id: ID приглашения.

        Returns:
            True если обновлено, False если не найдено.
        """
        from datetime import datetime, timezone

        async with acquire_connection() as conn:
            result = await conn.execute(
                """
                UPDATE screw.registration_invites
                SET expires_at = $2
                WHERE id = $1 AND used_by IS NULL
                """,
                invite_id,
                datetime.now(timezone.utc),
            )
            return parse_rowcount(result) == 1
