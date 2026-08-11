"""
Репозиторий feature flags.

Работа с таблицей feature_flags.
"""

from datetime import datetime
from typing import Any

from core.screw_core.db.tx import acquire_connection


class FeatureFlagsRepo:
    """Репозиторий для работы с флагами функций."""

    @staticmethod
    async def get_flag(flag_name: str) -> dict[str, Any] | None:
        """
        Получить флаг по имени.

        Args:
            flag_name: Имя флага.

        Returns:
            Данные флага или None.
        """
        async with acquire_connection() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM screw.feature_flags WHERE flag_name = $1",
                flag_name,
            )
            return dict(row) if row else None

    @staticmethod
    async def is_enabled(flag_name: str) -> bool:
        """
        Проверить, включён ли флаг.

        Args:
            flag_name: Имя флага.

        Returns:
            True если включён, False если выключен или не существует.
        """
        async with acquire_connection() as conn:
            enabled = await conn.fetchval(
                "SELECT is_enabled FROM screw.feature_flags WHERE flag_name = $1",
                flag_name,
            )
            return bool(enabled)

    @staticmethod
    async def set_flag(flag_name: str, is_enabled: bool, description: str | None = None) -> int:
        """
        Создать или обновить флаг.

        Args:
            flag_name: Имя флага.
            is_enabled: Включён ли флаг.
            description: Описание флага.

        Returns:
            ID флага.
        """
        async with acquire_connection() as conn:
            flag_id = await conn.fetchval(
                """
                INSERT INTO screw.feature_flags (flag_name, is_enabled, description)
                VALUES ($1, $2, $3)
                ON CONFLICT (flag_name) DO UPDATE
                SET is_enabled = $2, description = COALESCE($3, feature_flags.description),
                    updated_at = $4
                RETURNING id
                """,
                flag_name,
                is_enabled,
                description,
                datetime.now(),
            )
            return flag_id

    @staticmethod
    async def enable_flag(flag_name: str) -> None:
        """
        Включить флаг.

        Args:
            flag_name: Имя флага.
        """
        await FeatureFlagsRepo.set_flag(flag_name, True)

    @staticmethod
    async def disable_flag(flag_name: str) -> None:
        """
        Выключить флаг.

        Args:
            flag_name: Имя флага.
        """
        await FeatureFlagsRepo.set_flag(flag_name, False)

    @staticmethod
    async def get_all_flags() -> list[dict[str, Any]]:
        """
        Получить все флаги.

        Returns:
            Список флагов.
        """
        async with acquire_connection() as conn:
            rows = await conn.fetch(
                "SELECT * FROM screw.feature_flags ORDER BY flag_name",
            )
            return [dict(row) for row in rows]
