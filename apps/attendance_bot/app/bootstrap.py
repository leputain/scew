"""
DEV-режим: автоматический bootstrap админов и менеджеров.

При DEV_BOOTSTRAP_ADMINS=1 создаёт/обновляет пользователей из env-списков
при старте бота. Позволяет сохранять права после пересоздания БД.
"""

from apps.attendance_bot.app.config import (
    get_admin_telegram_ids,
    get_manager_osb_telegram_ids,
    get_manager_ozko_telegram_ids,
    is_dev_bootstrap_admins_enabled,
)
from core.screw_core.repos.users_repo import UsersRepo
from core.screw_core.utils.logging import get_logger

logger = get_logger(__name__)

# ID отделов (соответствуют screw.org_departments)
DEPARTMENT_OZKO_ID = 1
DEPARTMENT_OSB_ID = 2


async def bootstrap_admins_if_enabled() -> None:
    """
    Выполнить bootstrap админов/менеджеров если включён DEV-режим.

    Работает только при DEV_BOOTSTRAP_ADMINS=1.
    Идемпотентно: повторные вызовы не создают дубликатов.
    """
    if not is_dev_bootstrap_admins_enabled():
        logger.debug("DEV_BOOTSTRAP_ADMINS выключен, пропуск bootstrap")
        return

    logger.info("DEV_BOOTSTRAP_ADMINS включён, запуск bootstrap...")

    # Админы (без привязки к отделу)
    admin_ids = get_admin_telegram_ids()
    logger.info("bootstrap: parsed admin telegram IDs=%s", len(admin_ids))

    count = 0
    for telegram_id in admin_ids:
        await UsersRepo.upsert_admin_or_manager(
            telegram_id=telegram_id,
            role="admin",
            department_id=None,
        )
        logger.info("bootstrap: admin telegram_id=%s", telegram_id)
        count += 1

    # Менеджеры ОЗКО
    manager_ozko_ids = get_manager_ozko_telegram_ids()
    for telegram_id in manager_ozko_ids:
        await UsersRepo.upsert_admin_or_manager(
            telegram_id=telegram_id,
            role="manager",
            department_id=DEPARTMENT_OZKO_ID,
        )
        logger.info("bootstrap: manager OZKO telegram_id=%s", telegram_id)
        count += 1

    # Менеджеры ОСБ
    manager_osb_ids = get_manager_osb_telegram_ids()
    for telegram_id in manager_osb_ids:
        await UsersRepo.upsert_admin_or_manager(
            telegram_id=telegram_id,
            role="manager",
            department_id=DEPARTMENT_OSB_ID,
        )
        logger.info("bootstrap: manager OSB telegram_id=%s", telegram_id)
        count += 1

    logger.info("DEV bootstrap завершён: %s пользователей", count)
