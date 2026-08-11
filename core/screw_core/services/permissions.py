"""
Role/permission helpers shared by bots.
"""

from dataclasses import dataclass

from core.screw_core.config.departments import OZKO_DEPARTMENT_ID, OSB_DEPARTMENT_ID  # noqa: F401
from core.screw_core.repos.users_repo import UsersRepo


@dataclass(frozen=True)
class UserPermissions:
    is_admin: bool
    is_manager: bool
    department_id: int | None


async def get_user_permissions(
    telegram_id: int,
    *,
    admin_ids: list[int] | None = None,
    manager_ozko_ids: list[int] | None = None,
    manager_osb_ids: list[int] | None = None,
    bootstrap_enabled: bool = False,
) -> UserPermissions:
    """
    Load user permissions from DB; optionally bootstrap from env lists in DEV mode.
    """
    user = await UsersRepo.get_by_telegram_id(telegram_id)

    if not user and bootstrap_enabled:
        role = None
        department_id = None

        if admin_ids and telegram_id in admin_ids:
            role = "admin"
        elif manager_ozko_ids and telegram_id in manager_ozko_ids:
            role = "manager"
            department_id = OZKO_DEPARTMENT_ID
        elif manager_osb_ids and telegram_id in manager_osb_ids:
            role = "manager"
            department_id = OSB_DEPARTMENT_ID

        if role:
            await UsersRepo.upsert_admin_or_manager(
                telegram_id=telegram_id,
                role=role,
                department_id=department_id,
            )
            user = await UsersRepo.get_by_telegram_id(telegram_id)

    if not user:
        return UserPermissions(is_admin=False, is_manager=False, department_id=None)
    if user.get("status") != "active":
        return UserPermissions(is_admin=False, is_manager=False, department_id=None)

    role = user.get("role")
    is_admin = role == "admin"
    is_manager = role in ("manager", "admin")
    return UserPermissions(
        is_admin=is_admin,
        is_manager=is_manager,
        department_id=user.get("department_id"),
    )
