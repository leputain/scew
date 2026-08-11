"""Safe Telegram system-message delivery helpers."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Literal

from core.screw_core.utils.logging import get_logger

logger = get_logger(__name__)

TelegramDeliveryMode = Literal["rich_first", "html"]


@dataclass(slots=True)
class TelegramSendResult:
    """Result of a system message delivery attempt."""

    message: Any
    method: str
    rich_used: bool
    fallback_used: bool
    reaction_set: bool = False


def _env_bool(name: str, default: str = "0") -> bool:
    return os.getenv(name, default).strip().lower() in {"1", "true", "yes", "on"}


def is_rich_messages_enabled() -> bool:
    """Return whether rich system messages should be attempted."""
    return _env_bool("TELEGRAM_RICH_MESSAGES_ENABLED", "1")


def is_system_reactions_enabled() -> bool:
    """Return whether bot should react to its own system messages."""
    return _env_bool("TELEGRAM_SYSTEM_REACTIONS_ENABLED", "0")


def _is_aiogram_like_bot(bot: Any) -> bool:
    module = getattr(getattr(bot, "__class__", None), "__module__", "")
    return module.startswith("aiogram.") or hasattr(bot, "send_rich_message")


def _message_id(message: Any) -> int | None:
    if isinstance(message, dict):
        value = message.get("message_id")
    else:
        value = getattr(message, "message_id", None)
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _input_rich_message(html_text: str) -> Any | None:
    try:
        from aiogram.types import InputRichMessage

        return InputRichMessage(html=html_text)
    except Exception as exc:
        logger.debug("telegram_delivery: InputRichMessage unavailable: %s", exc)
        return None


def _disabled_link_preview_options(bot: Any) -> Any | None:
    if not _is_aiogram_like_bot(bot):
        return None
    try:
        from aiogram.types import LinkPreviewOptions

        return LinkPreviewOptions(is_disabled=True)
    except Exception as exc:
        logger.debug("telegram_delivery: LinkPreviewOptions unavailable: %s", exc)
        return None


async def _try_set_reaction(
    bot: Any,
    *,
    chat_id: int,
    message: Any,
    reaction_emoji: str | None,
) -> bool:
    if not reaction_emoji or not is_system_reactions_enabled() or not hasattr(bot, "set_message_reaction"):
        return False

    message_id = _message_id(message)
    if message_id is None:
        return False

    try:
        from aiogram.types import ReactionTypeEmoji

        reaction = [ReactionTypeEmoji(emoji=reaction_emoji)]
    except Exception:
        reaction = [{"type": "emoji", "emoji": reaction_emoji}]

    try:
        await bot.set_message_reaction(chat_id=chat_id, message_id=message_id, reaction=reaction)
        return True
    except Exception as exc:
        logger.debug("telegram_delivery: set_message_reaction failed: %s", str(exc)[:120])
        return False


async def send_system_message(
    bot: Any,
    *,
    chat_id: int,
    html_text: str,
    rich_html_text: str | None = None,
    message_thread_id: int | None = None,
    reply_parameters: Any | None = None,
    reply_markup: Any | None = None,
    disable_notification: bool | None = None,
    protect_content: bool | None = None,
    disable_link_preview: bool = True,
    reaction_emoji: str | None = None,
    delivery_mode: TelegramDeliveryMode = "rich_first",
) -> TelegramSendResult:
    """
    Send a system message via rich Telegram API with HTML fallback.

    The helper intentionally logs no text, token or chat id. Delivery failures of
    the fallback path are propagated to preserve existing retry/error behavior.
    """
    if delivery_mode not in {"rich_first", "html"}:
        raise ValueError(f"Unsupported Telegram delivery mode: {delivery_mode}")

    rich_payload = rich_html_text or html_text
    rich_attempted = delivery_mode == "rich_first" and is_rich_messages_enabled()

    if rich_attempted and hasattr(bot, "send_rich_message"):
        rich_message = _input_rich_message(rich_payload)
        if rich_message is not None:
            try:
                message = await bot.send_rich_message(
                    chat_id=chat_id,
                    rich_message=rich_message,
                    message_thread_id=message_thread_id,
                    reply_parameters=reply_parameters,
                    reply_markup=reply_markup,
                    disable_notification=disable_notification,
                    protect_content=protect_content,
                )
                reaction_set = await _try_set_reaction(
                    bot,
                    chat_id=chat_id,
                    message=message,
                    reaction_emoji=reaction_emoji,
                )
                logger.info("telegram_delivery: sent system message method=rich")
                return TelegramSendResult(
                    message=message,
                    method="rich",
                    rich_used=True,
                    fallback_used=False,
                    reaction_set=reaction_set,
                )
            except Exception as exc:
                logger.warning(
                    "telegram_delivery: rich send failed, retrying as HTML: %s",
                    str(exc)[:120],
                )

    kwargs: dict[str, Any] = {
        "chat_id": chat_id,
        "text": html_text,
        "parse_mode": "HTML",
    }
    if message_thread_id is not None:
        kwargs["message_thread_id"] = message_thread_id
    if reply_parameters is not None:
        kwargs["reply_parameters"] = reply_parameters
    if reply_markup is not None:
        kwargs["reply_markup"] = reply_markup
    if disable_notification is not None:
        kwargs["disable_notification"] = disable_notification
    if protect_content is not None:
        kwargs["protect_content"] = protect_content
    if disable_link_preview:
        link_preview_options = _disabled_link_preview_options(bot)
        if link_preview_options is not None:
            kwargs["link_preview_options"] = link_preview_options
        else:
            kwargs["disable_web_page_preview"] = True

    message = await bot.send_message(**kwargs)
    reaction_set = await _try_set_reaction(
        bot,
        chat_id=chat_id,
        message=message,
        reaction_emoji=reaction_emoji,
    )
    logger.info("telegram_delivery: sent system message method=html")
    return TelegramSendResult(
        message=message,
        method="html",
        rich_used=False,
        fallback_used=hasattr(bot, "send_rich_message") and rich_attempted,
        reaction_set=reaction_set,
    )
