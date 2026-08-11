"""Aiogram ingress logging and fail-closed access policy."""

from __future__ import annotations

import asyncio
from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware
from aiogram.types import TelegramObject, Update

from apps.attendance_bot.app.config import get_allowed_group_chat_ids
from apps.attendance_bot.app.utils.revoked_access import (
    notify_admins_about_revoked_access,
    send_revoked_notice_if_needed,
)
from core.screw_core.models.enums import EventType, NotifyChannel, UserStatus
from core.screw_core.repos.events_repo import EventsRepo
from core.screw_core.repos.users_repo import UsersRepo
from core.screw_core.utils.logging import get_logger
from core.screw_core.utils.timezone import get_tzinfo

logger = get_logger(__name__)

REGISTRATION_FULL_NAME_STATE = "RegistrationStates:waiting_full_name"


def is_private_invite_start(chat_type: str | None, text: str | None) -> bool:
    """Allow an unknown user only for a private /start with an invite token."""
    if chat_type != "private" or not text:
        return False
    parts = text.strip().split(maxsplit=1)
    return len(parts) == 2 and parts[0] == "/start" and bool(parts[1].strip())


async def is_registration_fsm_state(data: dict[str, Any]) -> bool:
    state = data.get("state")
    get_state = getattr(state, "get_state", None)
    if not callable(get_state):
        return False
    try:
        return await get_state() == REGISTRATION_FULL_NAME_STATE
    except Exception as exc:
        logger.warning("aiogram security_policy: FSM state read failed: %s", exc)
        return False


def _update_actor_and_chat(event: Update) -> tuple[Any | None, int | None, str | None, str | None]:
    if event.message:
        return event.message.from_user, event.message.chat.id, event.message.chat.type, event.message.text
    if event.callback_query:
        chat_id = None
        chat_type = None
        if event.callback_query.message:
            chat_id = event.callback_query.message.chat.id
            chat_type = event.callback_query.message.chat.type
        return event.callback_query.from_user, chat_id, chat_type, None
    if event.poll_answer:
        return event.poll_answer.user, None, None, None
    if event.message_reaction:
        return (
            event.message_reaction.user,
            event.message_reaction.chat.id,
            event.message_reaction.chat.type,
            None,
        )
    if event.message_reaction_count:
        return None, event.message_reaction_count.chat.id, event.message_reaction_count.chat.type, None
    if event.chat_member:
        return None, event.chat_member.chat.id, event.chat_member.chat.type, None
    if event.my_chat_member:
        return None, event.my_chat_member.chat.id, event.my_chat_member.chat.type, None
    return None, None, None, None


class AiogramIngressMiddleware(BaseMiddleware):
    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        if isinstance(event, Update):
            chat = None
            if event.message:
                chat = event.message.chat
            elif event.callback_query and event.callback_query.message:
                chat = event.callback_query.message.chat
            elif event.message_reaction:
                chat = event.message_reaction.chat
            elif event.message_reaction_count:
                chat = event.message_reaction_count.chat
            elif event.chat_member:
                chat = event.chat_member.chat
            elif event.my_chat_member:
                chat = event.my_chat_member.chat

            if chat:
                logger.info(
                    "AIOGRAM_INGRESS update_id=%s type=%s chat_id=%s chat_type=%s chat_title=%r",
                    event.update_id,
                    event.event_type,
                    chat.id,
                    chat.type,
                    getattr(chat, "title", None),
                )
            else:
                logger.info("AIOGRAM_INGRESS update_id=%s type=%s", event.update_id, event.event_type)
        return await handler(event, data)


class AiogramSecurityPolicyMiddleware(BaseMiddleware):
    """Apply group allowlist and one fail-closed user lookup before routers."""

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        if not isinstance(event, Update):
            return await handler(event, data)

        actor, chat_id, chat_type, text = _update_actor_and_chat(event)
        if chat_type and chat_type != "private" and chat_id not in get_allowed_group_chat_ids():
            logger.warning(
                "aiogram security_policy: blocked non-allowlisted chat update_id=%s chat_id=%s chat_type=%s",
                event.update_id,
                chat_id,
                chat_type,
            )
            return None

        if actor is None:
            return await handler(event, data)

        registration_exception = is_private_invite_start(
            chat_type,
            text,
        ) or await is_registration_fsm_state(data)
        try:
            user = await asyncio.wait_for(UsersRepo.get_by_telegram_id(actor.id), timeout=3)
        except asyncio.TimeoutError:
            logger.warning("aiogram security_policy: DB timeout for telegram_id=%s", actor.id)
            return None
        except Exception as exc:
            logger.warning("aiogram security_policy: DB error for telegram_id=%s: %s", actor.id, exc)
            return None

        if not user:
            if registration_exception:
                return await handler(event, data)
            logger.warning(
                "aiogram security_policy: blocked unknown telegram_id=%s chat_id=%s chat_type=%s update_id=%s",
                actor.id,
                chat_id,
                chat_type,
                event.update_id,
            )
            return None

        status = str(user.get("status") or "")
        if status == UserStatus.ACTIVE.value:
            data["auth_user"] = user
            return await handler(event, data)
        if status != UserStatus.TERMINATED.value:
            logger.warning(
                "aiogram security_policy: blocked invalid status telegram_id=%s status=%s",
                actor.id,
                status,
            )
            return None

        await self._block_revoked(event, data, user, actor, chat_id, chat_type)
        return None

    @staticmethod
    async def _block_revoked(
        event: Update,
        data: dict[str, Any],
        user: dict[str, Any],
        actor: Any,
        chat_id: int | None,
        chat_type: str | None,
    ) -> None:
        bot = data.get("bot")
        callback_id = None
        callback_data = None
        if event.callback_query:
            kind = "REVOKED_CALLBACK"
            callback_id = event.callback_query.id
            callback_data = event.callback_query.data
            try:
                await event.callback_query.answer(
                    "🚫 Доступ к боту отозван. Обратитесь к администратору.",
                    show_alert=True,
                )
            except Exception as exc:
                logger.debug("aiogram security_policy: revoked callback answer failed: %s", exc)
        elif event.message:
            kind = "REVOKED_MESSAGE"
            if bot:
                try:
                    await send_revoked_notice_if_needed(bot, actor.id)
                except Exception as exc:
                    logger.warning("aiogram security_policy: revoked notice failed: %s", exc)
        else:
            kind = "REVOKED_UPDATE"

        payload = {
            "kind": kind,
            "runtime": "aiogram",
            "telegram_id": actor.id,
            "username": actor.username,
            "full_name": user.get("full_name"),
            "chat_type": str(chat_type) if chat_type else None,
            "chat_id": chat_id,
            "callback_data": callback_data,
            "ts": event.message.date.astimezone(get_tzinfo("Europe/Moscow")).isoformat()
            if event.message
            else None,
        }
        try:
            await EventsRepo.create_event(
                event_type=EventType.ADMIN_ACTION,
                dedup_key=f"aiogram_revoked:{actor.id}:{callback_id or event.update_id}",
                user_id=user.get("id"),
                payload=payload,
                notify_channel=NotifyChannel.NONE,
            )
            if bot:
                await notify_admins_about_revoked_access(bot, payload)
        except Exception as exc:
            logger.warning("aiogram security_policy: revoked audit failed: %s", exc)

        logger.info("aiogram security_policy: blocked revoked telegram_id=%s kind=%s", actor.id, kind)
