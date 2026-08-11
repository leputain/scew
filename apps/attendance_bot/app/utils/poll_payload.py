"""Helpers for Telegram poll payloads.

Keep Bot API 10.0-specific extras isolated here so the business flow can
fall back to plain text polls if a client/library/server rejects rich media.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime
from typing import Any


@dataclass(frozen=True)
class RichPollOption:
    """Runtime-neutral rich poll option."""

    text: str
    media: dict[str, Any] | None = None

    @property
    def api_kwargs(self) -> dict[str, Any]:
        if not self.media:
            return {}
        return {"media": self.media}


@dataclass(frozen=True)
class PollPayload:
    """Prepared poll options and Bot API extras."""

    options: list[str | RichPollOption]
    plain_options: list[str]
    api_kwargs: dict[str, Any]
    is_rich: bool


_LAST_RICH_FALLBACK: dict[str, str] | None = None
_OPEN_PERIOD_MIN_SECONDS = 5
_OPEN_PERIOD_MAX_SECONDS = 2_628_000


def _json_dict(value: Any) -> dict[str, Any] | None:
    if isinstance(value, dict) and value:
        return value
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) and parsed else None


def _poll_media_from_config(config: dict[str, Any]) -> dict[str, Any] | None:
    for key in ("poll_media", "media"):
        media = _json_dict(config.get(key))
        if media:
            return media
    for env_key in ("POLL_MEDIA_JSON", "POLL_MEDIA"):
        media = _json_dict(os.getenv(env_key))
        if media:
            return media
    return None


def _poll_description_from_config(config: dict[str, Any]) -> str:
    return str(config.get("poll_description") or os.getenv("POLL_DESCRIPTION", "")).strip()


def _duration_seconds_from_config(config: dict[str, Any]) -> int | None:
    value = config.get("open_period") or config.get("poll_duration_seconds") or os.getenv("POLL_DURATION_SECONDS")
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _open_period_from_config(config: dict[str, Any]) -> int | None:
    seconds = _duration_seconds_from_config(config)
    if seconds is None:
        return None
    if _OPEN_PERIOD_MIN_SECONDS <= seconds <= _OPEN_PERIOD_MAX_SECONDS:
        return seconds
    return None


def _record_rich_fallback(runtime: str, reason: Any) -> None:
    from apps.attendance_bot.app.utils.log_redaction import redact_text

    global _LAST_RICH_FALLBACK
    _LAST_RICH_FALLBACK = {
        "runtime": runtime,
        "at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "reason": redact_text(reason)[:240],
    }


def _is_members_only_scope_error(exc: Any) -> bool:
    text = str(exc).lower()
    return "members_only" in text or "poll voters can be restricted only in channel chats" in text


def get_last_rich_fallback() -> dict[str, str] | None:
    return dict(_LAST_RICH_FALLBACK) if _LAST_RICH_FALLBACK else None


def reset_last_rich_fallback() -> None:
    global _LAST_RICH_FALLBACK
    _LAST_RICH_FALLBACK = None


def build_poll_payload_diagnostics(raw_options: list[dict[str, Any]], config: dict[str, Any] | None = None) -> dict[str, Any]:
    config = config or {}
    options = [opt for opt in raw_options if isinstance(opt, dict)]
    payload = build_poll_payload(options, config)
    return {
        "duration_seconds": _duration_seconds_from_config(config),
        "has_description": bool(payload.api_kwargs.get("description")),
        "has_poll_media": bool(payload.api_kwargs.get("media")),
        "option_media_count": sum(1 for opt in options if _json_dict(opt.get("media"))),
        "persistent_id_count": sum(1 for opt in options if str(opt.get("persistent_id") or "").strip()),
        "open_period": payload.api_kwargs.get("open_period"),
        "allows_revoting": payload.api_kwargs.get("allows_revoting"),
        "allow_adding_options": payload.api_kwargs.get("allow_adding_options"),
        "shuffle_options": payload.api_kwargs.get("shuffle_options"),
        "last_fallback": get_last_rich_fallback(),
    }


def build_poll_payload(raw_options: list[dict[str, Any]], config: dict[str, Any] | None = None) -> PollPayload:
    """Build a runtime-neutral poll payload with Bot API 10.0 extras.

    Supported optional fields:
    - poll option: {"label": "...", "media": {...}}
    - config/env: poll_description / POLL_DESCRIPTION
    - config/env: poll_media/media/POLL_MEDIA_JSON for top-level poll media
    - config/env: open_period or poll_duration_seconds/POLL_DURATION_SECONDS
    """
    config = config or {}
    options: list[str | RichPollOption] = []
    plain_options: list[str] = []
    is_rich = False

    for opt in raw_options:
        key = str(opt.get("key") or "").strip()
        label = str(opt.get("label") or key).strip()
        if not label:
            continue

        plain_options.append(label)
        media = _json_dict(opt.get("media"))
        if media:
            options.append(RichPollOption(text=label, media=media))
            is_rich = True
        else:
            options.append(label)

    api_kwargs: dict[str, Any] = {
        "allows_revoting": True,
        "shuffle_options": False,
        "allow_adding_options": False,
    }

    description = _poll_description_from_config(config)
    if description:
        api_kwargs["description"] = description

    poll_media = _poll_media_from_config(config)
    if poll_media:
        api_kwargs["media"] = poll_media
        is_rich = True

    open_period = _open_period_from_config(config)
    if open_period:
        api_kwargs["open_period"] = open_period

    return PollPayload(
        options=options,
        plain_options=plain_options,
        api_kwargs=api_kwargs,
        is_rich=is_rich or bool(description),
    )


def build_aiogram_poll_payload(payload: PollPayload) -> tuple[list[Any], list[str], dict[str, Any]]:
    """Convert the prepared payload into aiogram-native send_poll arguments."""
    from aiogram.types import InputPollOption

    aiogram_options: list[Any] = []
    for option in payload.options:
        if isinstance(option, str):
            aiogram_options.append(option)
            continue

        media = (getattr(option, "api_kwargs", None) or {}).get("media")
        if media:
            aiogram_options.append(InputPollOption(text=option.text, media=media))
        else:
            aiogram_options.append(option.text)

    return aiogram_options, list(payload.plain_options), dict(payload.api_kwargs)


async def send_aiogram_poll_with_fallback(bot, logger, **kwargs):
    """Send aiogram poll with Bot API 10 extras, retrying as a plain poll on rejection."""
    from aiogram.exceptions import TelegramBadRequest

    options = kwargs.pop("options")
    plain_options = kwargs.pop("plain_options", None) or [
        option.text if hasattr(option, "text") else str(option) for option in options
    ]
    api_kwargs = kwargs.pop("api_kwargs", None) or {}

    try:
        return await bot.send_poll(options=options, **api_kwargs, **kwargs)
    except (TelegramBadRequest, TypeError) as exc:
        if api_kwargs.get("members_only") is True and _is_members_only_scope_error(exc):
            retry_kwargs = dict(api_kwargs)
            retry_kwargs.pop("members_only", None)
            logger.warning(
                "aiogram poll members_only rejected for this chat, retrying rich poll without members_only: %s",
                exc,
            )
            try:
                return await bot.send_poll(options=options, **retry_kwargs, **kwargs)
            except (TelegramBadRequest, TypeError) as retry_exc:
                exc = retry_exc
                api_kwargs = retry_kwargs
        if not api_kwargs and options == plain_options:
            raise
        _record_rich_fallback("aiogram", exc)
        logger.warning("aiogram poll rich payload rejected, retrying plain poll: %s", exc)
        return await bot.send_poll(options=plain_options, **kwargs)
