"""Telegram group access preflight for attendance poll operations."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from apps.attendance_bot.app.config import get_attendance_group_id
from apps.attendance_bot.app.utils.log_redaction import redact_text
from core.screw_core.utils.logging import get_logger

logger = get_logger(__name__)

_REQUIRED_PERMISSION_FLAGS = (
    "can_send_messages",
    "can_send_polls",
    "can_pin_messages",
)
_BLOCKED_STATUSES = {"left", "kicked"}


@dataclass(frozen=True)
class GroupPreflightResult:
    configured: bool
    accessible: bool
    chat_id: int | None = None
    chat_type: str | None = None
    bot_status: str | None = None
    missing_permissions: tuple[str, ...] = ()
    error: str | None = None


def _plain_value(value: Any) -> str:
    return str(getattr(value, "value", value))


def _member_status(member: Any) -> str:
    return _plain_value(getattr(member, "status", "unknown"))


def _chat_type(chat: Any) -> str:
    return _plain_value(getattr(chat, "type", "unknown"))


def _missing_permissions(member: Any) -> tuple[str, ...]:
    """Return only explicitly denied permissions.

    Telegram runtimes expose different ChatMember subclasses. A missing
    attribute usually means "not applicable" rather than "false"; explicit
    False is the actionable signal.
    """
    return tuple(
        permission
        for permission in _REQUIRED_PERMISSION_FLAGS
        if getattr(member, permission, None) is False
    )


async def check_attendance_group_access(bot: Any) -> GroupPreflightResult:
    """Check that the configured attendance group is reachable by the bot.

    The check is diagnostic and never blocks startup: private/admin flows should
    stay available even when group publishing is misconfigured.
    """
    group_id = get_attendance_group_id()
    if not group_id:
        logger.warning(
            "attendance group preflight: ATTENDANCE_GROUP_ID не настроен; "
            "групповые опросы отключены"
        )
        return GroupPreflightResult(configured=False, accessible=False)

    try:
        chat = await bot.get_chat(group_id)
        me = await bot.get_me()
        member = await bot.get_chat_member(group_id, me.id)
    except Exception as exc:
        error = redact_text(exc)
        logger.warning(
            "attendance group preflight: бот не видит ATTENDANCE_GROUP_ID=%s: %s",
            group_id,
            error,
        )
        return GroupPreflightResult(
            configured=True,
            accessible=False,
            chat_id=group_id,
            error=error,
        )

    status = _member_status(member)
    chat_type = _chat_type(chat)
    missing = _missing_permissions(member)
    accessible = status not in _BLOCKED_STATUSES and not missing

    if status in _BLOCKED_STATUSES:
        logger.warning(
            "attendance group preflight: бот не состоит в группе chat_id=%s "
            "(status=%s); /poll_open и auto-send не готовы",
            group_id,
            status,
        )
    elif missing:
        logger.warning(
            "attendance group preflight: группе chat_id=%s не хватает прав %s; "
            "проверьте send messages/send polls/pin messages",
            group_id,
            ",".join(missing),
        )
    else:
        logger.info(
            "attendance group preflight: group ok chat_id=%s chat_type=%s bot_status=%s",
            group_id,
            chat_type,
            status,
        )

    return GroupPreflightResult(
        configured=True,
        accessible=accessible,
        chat_id=group_id,
        chat_type=chat_type,
        bot_status=status,
        missing_permissions=missing,
    )
