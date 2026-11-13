"""Opt-in discovery of forum metadata on the existing update stream."""
from __future__ import annotations

import asyncio
import os
import re
from dataclasses import dataclass

from aiogram import BaseMiddleware
from aiogram.types import Update

from apps.attendance_bot.app.config import get_allowed_group_chat_ids
from core.screw_core.repos.forum_topics_repo import ForumTopicsRepo
from core.screw_core.utils.logging import get_logger

CAPTURE_TIMEOUT = 0.5
logger = get_logger(__name__)
_MARKER = re.compile(r"Тема: ([^\r\n]{1,128})")
_BIGINT_MAX = 2**63 - 1


@dataclass(frozen=True, slots=True)
class ForumTopicCaptureConfig:
    chat_id: int
    owner_id: int


def load_capture_config() -> ForumTopicCaptureConfig | None:
    """Missing, invalid or non-allowlisted configuration disables all capture."""
    if os.getenv("FORUM_TOPIC_CAPTURE_ENABLED", "0") != "1":
        return None
    chat = os.getenv("FORUM_TOPIC_CAPTURE_CHAT_ID", "").strip()
    owner = os.getenv("FORUM_TOPIC_CAPTURE_OWNER_ID", "").strip()
    if not re.fullmatch(r"-[0-9]{1,19}", chat) or not re.fullmatch(r"[0-9]{1,19}", owner):
        return None
    chat_id, owner_id = int(chat), int(owner)
    if not (-_BIGINT_MAX <= chat_id < 0 and 0 < owner_id <= _BIGINT_MAX):
        return None
    if chat_id not in get_allowed_group_chat_ids():
        return None
    return ForumTopicCaptureConfig(chat_id, owner_id)


def extract_topic(event: Update, config: ForumTopicCaptureConfig | None = None) -> dict | None:
    """Never infer a General ID, inspect reply bodies, or retain arbitrary text."""
    config = config or load_capture_config()
    if config is None:
        return None
    message = event.message if isinstance(event, Update) else None
    if (message is None or message.chat.id != config.chat_id
            or message.chat.type != "supergroup" or message.chat.is_forum is not True
            or message.message_thread_id is None or message.message_thread_id <= 0):
        return None
    name = source = None
    service = message.forum_topic_created or message.forum_topic_edited
    if service and service.name:
        name, source = service.name, "service"
    elif (message.from_user and message.from_user.id == config.owner_id
          and not message.sender_chat and not message.forward_origin and message.text):
        marker = _MARKER.fullmatch(message.text)
        if marker and marker[1].strip():
            name, source = marker[1].strip(), "owner_marker"
    return dict(chat_id=message.chat.id, message_thread_id=message.message_thread_id,
                name=name, name_source=source, message_id=message.message_id,
                observed_at=message.date)


class ForumTopicCaptureMiddleware(BaseMiddleware):
    """Observe metadata without authorizing, consuming or changing updates.

    The configured chat must also be in the main group allowlist. Observation
    precedes user-policy checks, as service events need no registered employee;
    the existing security middleware still decides access to ordinary handlers.
    Failures log only the exception type, never DB arguments or message text.
    """

    async def __call__(self, handler, event, data):
        try:
            topic = extract_topic(event)
            if topic:
                await asyncio.wait_for(ForumTopicsRepo.observe(**topic), CAPTURE_TIMEOUT)
        except Exception as exc:
            logger.warning("forum topic capture skipped: %s", type(exc).__name__)
        return await handler(event, data)
