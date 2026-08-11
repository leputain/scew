#!/usr/bin/env python3
"""Import Telegram Desktop JSON export into flud pulse metadata tables.

The importer stores only the same metadata as live collection. Full message
text is never persisted; optional preview is controlled by env or CLI flag.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from apps.attendance_bot.app.config import (
    get_flud_store_text_preview,
    get_flud_text_preview_max_chars,
)
from core.screw_core.db.pool import close_pool, create_pool
from core.screw_core.repos.flud_repo import FludRepo
from core.screw_core.repos.users_repo import UsersRepo
from core.screw_core.services.flud_stats import build_flud_message_payload


def _extract_text(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        chunks = []
        for item in value:
            if isinstance(item, str):
                chunks.append(item)
            elif isinstance(item, dict):
                chunks.append(str(item.get("text") or ""))
        return "".join(chunks)
    return ""


def _parse_telegram_user_id(value: Any) -> int | None:
    if value is None:
        return None
    text = str(value)
    match = re.search(r"(\d+)$", text)
    return int(match.group(1)) if match else None


def _parse_sent_at(item: dict[str, Any]) -> datetime:
    unixtime = item.get("date_unixtime")
    if unixtime:
        try:
            return datetime.fromtimestamp(int(unixtime), tz=timezone.utc)
        except (TypeError, ValueError):
            pass
    value = item.get("date")
    if value:
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
            return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
        except ValueError:
            pass
    return datetime.now(timezone.utc)


def _classify_export_message(item: dict[str, Any], text: str) -> tuple[str, dict[str, bool]]:
    media_type = str(item.get("media_type") or "").lower()
    mime_type = str(item.get("mime_type") or "").lower()
    file_name = str(item.get("file") or "").lower()
    photo = bool(item.get("photo")) or media_type == "photo"
    video = media_type == "video" or mime_type.startswith("video/")
    animation = media_type in {"animation", "gif"} or file_name.endswith(".gif")
    sticker = media_type == "sticker" or bool(item.get("sticker_emoji"))
    voice = media_type in {"voice_message", "voice"} or mime_type.startswith("audio/ogg")
    document = bool(item.get("file")) and not (photo or video or animation or sticker or voice)
    flags = {
        "has_photo": photo,
        "has_video": video,
        "has_animation": animation,
        "has_sticker": sticker,
        "has_voice": voice,
        "has_document": document,
    }
    if photo:
        return "photo", flags
    if video:
        return "video", flags
    if animation:
        return "animation", flags
    if sticker:
        return "sticker", flags
    if voice:
        return "voice", flags
    if document:
        return "document", flags
    if text:
        return "text", flags
    return "other", flags


def _reaction_counts(item: dict[str, Any]) -> dict[str, int]:
    result: dict[str, int] = {}
    for reaction in item.get("reactions") or []:
        if not isinstance(reaction, dict):
            continue
        emoji = reaction.get("emoji") or reaction.get("reaction") or reaction.get("type")
        count = reaction.get("count")
        try:
            parsed_count = int(count)
        except (TypeError, ValueError):
            parsed_count = 0
        if emoji and parsed_count > 0:
            result[f"emoji:{emoji}"] = parsed_count
    return result


async def _import(args: argparse.Namespace) -> int:
    raw = json.loads(Path(args.file).read_text(encoding="utf-8"))
    messages = raw.get("messages") if isinstance(raw, dict) else raw
    if not isinstance(messages, list):
        raise ValueError("Telegram export JSON must contain a messages list")

    store_preview = bool(args.store_preview) if args.store_preview is not None else get_flud_store_text_preview()
    preview_max_chars = args.preview_max_chars or get_flud_text_preview_max_chars()
    rows: list[tuple[dict[str, Any], dict[str, int]]] = []
    if args.apply:
        await create_pool(max_retries=1)

    try:
        for item in messages:
            if not isinstance(item, dict) or item.get("type") not in (None, "message"):
                continue
            message_id = item.get("id")
            if not isinstance(message_id, int):
                continue
            text = _extract_text(item.get("text"))
            telegram_user_id = _parse_telegram_user_id(item.get("from_id") or item.get("actor_id"))
            user_id = None
            if args.apply and telegram_user_id:
                user = await UsersRepo.get_by_telegram_id(telegram_user_id)
                user_id = int(user["id"]) if user and user.get("id") else None
            message_type, flags = _classify_export_message(item, text)
            payload = build_flud_message_payload(
                chat_id=args.chat_id,
                message_id=message_id,
                sent_at=_parse_sent_at(item),
                telegram_user_id=telegram_user_id,
                user_id=user_id,
                text=text,
                message_type=message_type,
                store_text_preview=store_preview,
                preview_max_chars=preview_max_chars,
                is_reply=item.get("reply_to_message_id") is not None,
                reply_to_message_id=item.get("reply_to_message_id"),
                **flags,
            )
            rows.append((payload, _reaction_counts(item)))

        if args.dry_run:
            print(f"dry-run: parsed_messages={len(rows)} store_preview={int(store_preview)}")
            return 0
        for payload, reactions in rows:
            await FludRepo.upsert_message(**payload)
            if reactions:
                await FludRepo.upsert_reactions(args.chat_id, payload["message_id"], reactions)
    finally:
        if args.apply:
            await close_pool()
    print(f"imported_messages={len(rows)} store_preview={int(store_preview)}")
    return 0


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Import Telegram Desktop export JSON into flud pulse tables.")
    parser.add_argument("--file", required=True, help="Path to Telegram Desktop result.json")
    parser.add_argument("--chat-id", required=True, type=int, help="Flud Telegram chat id")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true", help="Parse and report count without DB writes")
    mode.add_argument("--apply", action="store_true", help="Write parsed metadata to DB")
    parser.add_argument("--store-preview", action=argparse.BooleanOptionalAction, default=None)
    parser.add_argument("--preview-max-chars", type=int, default=None)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv or sys.argv[1:])
    return asyncio.run(_import(args))


if __name__ == "__main__":
    raise SystemExit(main())
