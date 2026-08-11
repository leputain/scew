"""
Репозиторий пользователей.

CRUD-операции и запросы к таблице users.
"""

from datetime import date, datetime
from typing import Any

from core.screw_core.config.departments import OZKO_DEPARTMENT_ID, OSB_DEPARTMENT_ID
from core.screw_core.db.tx import acquire_connection, parse_rowcount
from core.screw_core.utils.text import normalize_name
from core.screw_core.models.enums import UserRole, UserStatus, WorkMode


class UsersRepo:
    """Репозиторий для работы с пользователями."""

    @staticmethod
    async def get_by_id(user_id: int) -> dict[str, Any] | None:
        """
        Получить пользователя по ID.

        Args:
            user_id: ID пользователя в БД.

        Returns:
            Словарь с данными пользователя или None.
        """
        async with acquire_connection() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM screw.users WHERE id = $1",
                user_id,
            )
            return dict(row) if row else None

    @staticmethod
    async def get_by_telegram_id(telegram_id: int) -> dict[str, Any] | None:
        """
        Получить пользователя по Telegram ID.

        Args:
            telegram_id: ID пользователя в Telegram.

        Returns:
            Словарь с данными пользователя или None.
        """
        async with acquire_connection() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM screw.users WHERE telegram_id = $1",
                telegram_id,
            )
            return dict(row) if row else None

    @staticmethod
    async def set_status_by_telegram_id(telegram_id: int, status: UserStatus | str) -> None:
        """
        Обновить status пользователя по Telegram ID.

        Args:
            telegram_id: Telegram ID пользователя.
            status: Новое значение status.
        """
        status_value = status.value if isinstance(status, UserStatus) else str(status)
        async with acquire_connection() as conn:
            await conn.execute(
                """
                UPDATE screw.users
                SET status = $2, updated_at = $3
                WHERE telegram_id = $1
                """,
                telegram_id,
                status_value,
                datetime.now(),
            )

    @staticmethod
    async def list_admin_telegram_ids() -> list[int]:
        """
        Получить список Telegram ID админов.

        Returns:
            Список Telegram ID.
        """
        async with acquire_connection() as conn:
            rows = await conn.fetch(
                """
                SELECT telegram_id
                FROM screw.users
                WHERE role = 'admin' AND status = 'active'
                ORDER BY telegram_id
                """,
            )
            return [row["telegram_id"] for row in rows if row["telegram_id"]]

    @staticmethod
    async def create(
        telegram_id: int,
        username: str | None = None,
        first_name: str | None = None,
        last_name: str | None = None,
        full_name: str | None = None,
        role: str = "employee",
        department_id: int | None = None,
        work_mode: WorkMode = WorkMode.HYBRID,
    ) -> int:
        """
        Создать нового пользователя.

        Args:
            telegram_id: ID пользователя в Telegram.
            username: Юзернейм в Telegram.
            first_name: Имя.
            last_name: Фамилия.
            full_name: Полное ФИО.
            role: Роль (employee, manager, admin).
            department_id: ID отдела.
            work_mode: Режим работы.

        Returns:
            ID созданного пользователя.
        """
        async with acquire_connection() as conn:
            user_id = await conn.fetchval(
                """
                INSERT INTO screw.users
                    (telegram_id, username, first_name, last_name, full_name,
                     role, department_id, work_mode)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
                RETURNING id
                """,
                telegram_id,
                username,
                first_name,
                last_name,
                full_name,
                role,
                department_id,
                work_mode.value,
            )
            return user_id

    @staticmethod
    async def update_status(user_id: int, status: UserStatus) -> None:
        """
        Обновить статус пользователя.

        Args:
            user_id: ID пользователя.
            status: Новый статус.
        """
        async with acquire_connection() as conn:
            await conn.execute(
                """
                UPDATE screw.users
                SET status = $2, updated_at = $3
                WHERE id = $1
                """,
                user_id,
                status.value,
                datetime.now(),
            )

    @staticmethod
    async def count_active_by_department(department_id: int) -> int:
        """
        Подсчитать количество активных пользователей отдела.

        Args:
            department_id: ID отдела.

        Returns:
            Количество активных пользователей.
        """
        async with acquire_connection() as conn:
            count = await conn.fetchval(
                """
                SELECT COUNT(*) FROM screw.users
                WHERE department_id = $1 AND status = 'active'
                """,
                department_id,
            )
            return count or 0

    @staticmethod
    async def get_active_by_department(department_id: int) -> list[dict[str, Any]]:
        """
        Получить активных пользователей отдела.

        Args:
            department_id: ID отдела.

        Returns:
            Список пользователей.
        """
        async with acquire_connection() as conn:
            rows = await conn.fetch(
                """
                SELECT * FROM screw.users
                WHERE department_id = $1 AND status = 'active'
                ORDER BY full_name
                """,
                department_id,
            )
            return [dict(row) for row in rows]

    @staticmethod
    async def get_all_active() -> list[dict[str, Any]]:
        """
        Получить всех активных пользователей.

        Returns:
            Список пользователей.
        """
        async with acquire_connection() as conn:
            rows = await conn.fetch(
                """
                SELECT * FROM screw.users
                WHERE status = 'active'
                ORDER BY full_name
                """,
            )
            return [dict(row) for row in rows]

    @staticmethod
    async def get_users_with_departments() -> list[dict[str, Any]]:
        """
        Получить пользователей с данными отдела.

        Returns:
            Список пользователей с department_id и department_code.
        """
        async with acquire_connection() as conn:
            rows = await conn.fetch(
                """
                SELECT u.id, u.telegram_id, u.full_name, u.created_at,
                       u.department_id, d.code AS department_code
                FROM screw.users u
                LEFT JOIN screw.org_departments d ON d.id = u.department_id
                WHERE u.department_id = ANY($1)
                  AND u.status = 'active'
                ORDER BY u.department_id, u.full_name NULLS LAST, u.created_at
                """,
                [OZKO_DEPARTMENT_ID, OSB_DEPARTMENT_ID],
            )
            return [dict(row) for row in rows]

    @staticmethod
    async def update_work_mode(user_id: int, work_mode: WorkMode) -> None:
        """
        Обновить режим работы пользователя.

        Args:
            user_id: ID пользователя.
            work_mode: Новый режим работы.
        """
        async with acquire_connection() as conn:
            await conn.execute(
                """
                UPDATE screw.users
                SET work_mode = $2, updated_at = $3
                WHERE id = $1
                """,
                user_id,
                work_mode.value,
                datetime.now(),
            )

    @staticmethod
    async def update_role(user_id: int, role: UserRole | str) -> None:
        """
        Обновить роль/тип пользователя по внутреннему ID.

        Args:
            user_id: ID пользователя.
            role: Новая роль/тип пользователя.
        """
        role_value = role.value if isinstance(role, UserRole) else str(role)
        async with acquire_connection() as conn:
            await conn.execute(
                """
                UPDATE screw.users
                SET role = $2, updated_at = $3
                WHERE id = $1
                """,
                user_id,
                role_value,
                datetime.now(),
            )

    @staticmethod
    async def get_managers_by_department(department_id: int) -> list[dict[str, Any]]:
        """
        Получить менеджеров отдела.

        Args:
            department_id: ID отдела.

        Returns:
            Список менеджеров.
        """
        async with acquire_connection() as conn:
            rows = await conn.fetch(
                """
                SELECT * FROM screw.users
                WHERE department_id = $1
                AND status = 'active'
                AND role IN ('manager', 'admin')
                ORDER BY full_name
                """,
                department_id,
            )
            return [dict(row) for row in rows]

    @staticmethod
    async def get_users_by_full_names(
        full_names: list[str],
    ) -> dict[str, dict[str, Any]]:
        """
        Получить пользователей по списку ФИО.

        Поиск case-insensitive (LOWER).
        Возвращает только активных пользователей.

        Args:
            full_names: Список ФИО для поиска.

        Returns:
            dict: {full_name_lower: user_dict}
        """
        if not full_names:
            return {}

        async with acquire_connection() as conn:
            # Нормализуем имена для поиска (lowercase + ё→е)
            names_norm = [normalize_name(name) for name in full_names]

            rows = await conn.fetch(
                """
                SELECT * FROM screw.users
                WHERE REPLACE(LOWER(full_name), 'ё', 'е') = ANY($1)
                AND status = 'active'
                """,
                names_norm,
            )

            # Индексируем по normalize_name(full_name)
            result: dict[str, dict[str, Any]] = {}
            for row in rows:
                user = dict(row)
                if user.get("full_name"):
                    key = normalize_name(user["full_name"])
                    result[key] = user

            return result

    @staticmethod
    async def upsert_admin_or_manager(
        telegram_id: int,
        role: str,
        department_id: int | None = None,
    ) -> int:
        """
        Создать или обновить admin/manager пользователя.

        DEV-ONLY: Используется для автоматического bootstrap админов/менеджеров.

        При INSERT: создаёт пользователя с указанной ролью и статусом active.
        При UPDATE (CONFLICT): обновляет role, department_id, status=active.

        Args:
            telegram_id: Telegram ID пользователя.
            role: Роль (admin или manager).
            department_id: ID отдела (для менеджеров).

        Returns:
            ID пользователя.
        """
        async with acquire_connection() as conn:
            user_id = await conn.fetchval(
                """
                INSERT INTO screw.users
                    (telegram_id, role, department_id, status)
                VALUES ($1, $2, $3, 'active')
                ON CONFLICT (telegram_id) DO UPDATE
                SET role = CASE
                    WHEN screw.users.role = 'admin' THEN 'admin'
                    WHEN EXCLUDED.role = 'admin' THEN 'admin'
                    ELSE EXCLUDED.role
                END,
                department_id = CASE
                    WHEN screw.users.role = 'admin' THEN screw.users.department_id
                    ELSE EXCLUDED.department_id
                END,
                status = 'active',
                updated_at = NOW()
                RETURNING id
                """,
                telegram_id,
                role,
                department_id,
            )
            return user_id

    @staticmethod
    async def set_role_by_telegram_id(
        telegram_id: int,
        role: str,
        department_id: int | None = None,
    ) -> bool:
        """
        Установить роль пользователя по Telegram ID.

        Args:
            telegram_id: Telegram ID пользователя.
            role: Новая роль (employee, manager, admin, no_tg_worker).
            department_id: ID отдела (обновляется, если передан).

        Returns:
            True если пользователь найден и обновлён.
        """
        async with acquire_connection() as conn:
            if department_id is not None:
                result = await conn.execute(
                    """
                    UPDATE screw.users
                    SET role = $2, department_id = $3, updated_at = $4
                    WHERE telegram_id = $1
                    """,
                    telegram_id,
                    role,
                    department_id,
                    datetime.now(),
                )
            else:
                result = await conn.execute(
                    """
                    UPDATE screw.users
                    SET role = $2, updated_at = $3
                    WHERE telegram_id = $1
                    """,
                    telegram_id,
                    role,
                    datetime.now(),
                )
            return parse_rowcount(result) > 0

    @staticmethod
    async def get_terminated_by_department(
        department_id: int | None = None,
    ) -> list[dict[str, Any]]:
        """
        Получить уволенных (terminated) пользователей.

        Args:
            department_id: Фильтр по отделу (None = все отделы).

        Returns:
            Список уволенных пользователей.
        """
        async with acquire_connection() as conn:
            if department_id is not None:
                rows = await conn.fetch(
                    """
                    SELECT * FROM screw.users
                    WHERE status = 'terminated' AND department_id = $1
                    ORDER BY full_name
                    """,
                    department_id,
                )
            else:
                rows = await conn.fetch(
                    """
                    SELECT * FROM screw.users
                    WHERE status = 'terminated'
                    ORDER BY full_name
                    """,
                )
            return [dict(row) for row in rows]

    @staticmethod
    async def search_active_users(
        query: str,
        department_id: int | None = None,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        """
        Поиск активных пользователей по фрагменту ФИО.

        Args:
            query: Строка для поиска (ILIKE).
            department_id: Фильтр по отделу (None = все отделы).
            limit: Максимальное количество результатов.

        Returns:
            Список найденных пользователей.
        """
        async with acquire_connection() as conn:
            escaped = query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            pattern = f"%{escaped}%"
            if department_id is not None:
                rows = await conn.fetch(
                    """
                    SELECT * FROM screw.users
                    WHERE status = 'active'
                      AND full_name ILIKE $1
                      AND department_id = $2
                    ORDER BY full_name
                    LIMIT $3
                    """,
                    pattern,
                    department_id,
                    limit,
                )
            else:
                rows = await conn.fetch(
                    """
                    SELECT * FROM screw.users
                    WHERE status = 'active'
                      AND full_name ILIKE $1
                    ORDER BY full_name
                    LIMIT $2
                    """,
                    pattern,
                    limit,
                )
            return [dict(row) for row in rows]

    # =========================================================================
    # Birthday
    # =========================================================================

    @staticmethod
    async def update_birthday(user_id: int, birthday: date | None) -> None:
        """Обновить дату рождения пользователя."""
        async with acquire_connection() as conn:
            await conn.execute(
                """
                UPDATE screw.users
                SET birthday = $2, updated_at = $3
                WHERE id = $1
                """,
                user_id,
                birthday,
                datetime.now(),
            )

    @staticmethod
    async def get_users_with_birthday_today(
        month: int,
        day: int,
        department_id: int | None = None,
    ) -> list[dict[str, Any]]:
        """Получить активных пользователей, у которых сегодня день рождения."""
        async with acquire_connection() as conn:
            if department_id is not None:
                rows = await conn.fetch(
                    """
                    SELECT * FROM screw.users
                    WHERE status = 'active'
                      AND birthday IS NOT NULL
                      AND EXTRACT(MONTH FROM birthday) = $1
                      AND EXTRACT(DAY FROM birthday) = $2
                      AND department_id = $3
                    ORDER BY full_name
                    """,
                    month,
                    day,
                    department_id,
                )
            else:
                rows = await conn.fetch(
                    """
                    SELECT * FROM screw.users
                    WHERE status = 'active'
                      AND birthday IS NOT NULL
                      AND EXTRACT(MONTH FROM birthday) = $1
                      AND EXTRACT(DAY FROM birthday) = $2
                    ORDER BY full_name
                    """,
                    month,
                    day,
                )
            return [dict(row) for row in rows]

    @staticmethod
    async def get_users_without_birthday(
        department_id: int,
    ) -> list[dict[str, Any]]:
        """Получить активных пользователей отдела без даты рождения."""
        async with acquire_connection() as conn:
            rows = await conn.fetch(
                """
                SELECT * FROM screw.users
                WHERE status = 'active'
                  AND department_id = $1
                  AND birthday IS NULL
                ORDER BY full_name
                """,
                department_id,
            )
            return [dict(row) for row in rows]
