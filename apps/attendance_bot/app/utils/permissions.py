"""
Вспомогательные функции для проверки прав доступа.

Тонкая обёртка над get_user_permissions, чтобы не повторять
аргументы конфигурации в каждом роутере.
"""

from apps.attendance_bot.app.config import (
    get_manager_osb_telegram_ids,
    is_dev_bootstrap_admins_enabled,
)
from core.screw_core.config.telegram import (
    get_admin_telegram_ids,
    get_manager_ozko_telegram_ids,
)
from core.screw_core.services.permissions import UserPermissions, get_user_permissions


async def get_permissions(telegram_id: int) -> UserPermissions:
    """Получить права пользователя по telegram_id."""
    return await get_user_permissions(
        telegram_id,
        admin_ids=get_admin_telegram_ids(),
        manager_ozko_ids=get_manager_ozko_telegram_ids(),
        manager_osb_ids=get_manager_osb_telegram_ids(),
        bootstrap_enabled=is_dev_bootstrap_admins_enabled(),
    )
