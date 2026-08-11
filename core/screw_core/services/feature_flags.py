"""
Сервис feature flags.

Сценарии:
- Проверка флагов
- Управление флагами
"""

from typing import Any

from core.screw_core.repos.feature_flags_repo import FeatureFlagsRepo


class FeatureFlagsService:
    """Сервис для работы с флагами функций."""

    @staticmethod
    async def is_enabled(flag_name: str) -> bool:
        """
        Проверить, включён ли флаг.

        Args:
            flag_name: Имя флага.

        Returns:
            True если включён.
        """
        return await FeatureFlagsRepo.is_enabled(flag_name)

    @staticmethod
    async def enable(flag_name: str, description: str | None = None) -> None:
        """
        Включить флаг.

        Args:
            flag_name: Имя флага.
            description: Описание.
        """
        await FeatureFlagsRepo.set_flag(flag_name, True, description)

    @staticmethod
    async def disable(flag_name: str) -> None:
        """
        Выключить флаг.

        Args:
            flag_name: Имя флага.
        """
        await FeatureFlagsRepo.set_flag(flag_name, False)

    @staticmethod
    async def get_all() -> list[dict[str, Any]]:
        """
        Получить все флаги.

        Returns:
            Список флагов.
        """
        return await FeatureFlagsRepo.get_all_flags()

    @staticmethod
    async def set_flag(flag_name: str, is_enabled: bool, description: str | None = None) -> None:
        """
        Установить значение флага.

        Args:
            flag_name: Имя флага.
            is_enabled: Включён ли.
            description: Описание.
        """
        await FeatureFlagsRepo.set_flag(flag_name, is_enabled, description)
