#!/usr/bin/env python3
"""Import Telegram Desktop HTML export into flud pulse metadata tables."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import re
import sys
from collections import Counter
from collections.abc import Iterable
from datetime import date, datetime
from pathlib import Path
from typing import Any

from lxml import html

from core.screw_core.db.pool import close_pool, create_pool
from core.screw_core.repos.flud_repo import FludRepo
from core.screw_core.services.flud_stats import build_flud_message_payload


def _clean(value: str | None) -> str:
    return re.sub(r"\s+", " ", (value or "").strip())


def _parse_dt(title: str) -> datetime:
    return datetime.strptime(title.strip(), "%d.%m.%Y %H:%M:%S UTC%z")


def _class_expr(name: str) -> str:
    return f'contains(concat(" ", normalize-space(@class), " "), " {name} ")'


def _author_hash(author_name: str | None) -> str | None:
    normalized = _clean(author_name).casefold()
    if not normalized:
        return None
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _message_type_and_flags(node: Any) -> tuple[str, dict[str, bool]]:
    photo = bool(node.xpath(".//*[" + _class_expr("photo") + " or " + _class_expr("media_photo") + "]"))
    video = bool(node.xpath(".//*[" + _class_expr("media_video") + " or " + _class_expr("video_file") + "]"))
    animation = bool(node.xpath(".//*[" + _class_expr("animated") + "]"))
    sticker = bool(node.xpath(".//*[" + _class_expr("sticker") + "]"))
    voice = bool(node.xpath(".//*[" + _class_expr("media_audio_file") + "]"))
    document = bool(node.xpath(".//*[" + _class_expr("media_file") + "]"))
    text = _clean(" ".join(t.text_content() for t in node.xpath(".//div[" + _class_expr("text") + "]")))
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


def _reaction_counts(node: Any) -> dict[str, int]:
    result: dict[str, int] = {}
    for reaction in node.xpath(".//span[" + _class_expr("reaction") + "]"):
        emoji_nodes = reaction.xpath(".//span[" + _class_expr("emoji") + "]")
        count_nodes = reaction.xpath(".//span[" + _class_expr("count") + "]")
        emoji = _clean(emoji_nodes[0].text_content()) if emoji_nodes else "🔥"
        try:
            count = int(_clean(count_nodes[0].text_content())) if count_nodes else 1
        except ValueError:
            count = 1
        if emoji and count > 0:
            result[f"emoji:{emoji}"] = result.get(f"emoji:{emoji}", 0) + count
    return result


def _iter_html_files(path: Path) -> Iterable[Path]:
    if path.is_file():
        yield path
        return
    yield from sorted(path.glob("messages*.html"))


def _parse_messages(args: argparse.Namespace) -> list[tuple[dict[str, Any], dict[str, int]]]:
    root = Path(args.path)
    start = date.fromisoformat(args.start_date)
    end = date.fromisoformat(args.end_date)
    rows: list[tuple[dict[str, Any], dict[str, int]]] = []
    last_author: str | None = None
    body_expr = "./div[" + _class_expr("body") + "]"
    for html_path in _iter_html_files(root):
        tree = html.parse(str(html_path)).getroot()
        for msg in tree.xpath("//div[" + _class_expr("message") + " and " + _class_expr("default") + "]"):
            msg_id_match = re.search(r"(\d+)", msg.get("id") or "")
            if not msg_id_match:
                continue
            message_id = int(msg_id_match.group(1))
            titles = msg.xpath(".//div[" + _class_expr("date") + " and " + _class_expr("details") + "]/@title")
            if not titles:
                continue
            sent_at = _parse_dt(titles[0])
            if not (start <= sent_at.date() <= end):
                continue
            names = msg.xpath(".//div[" + _class_expr("from_name") + "]")
            if names:
                last_author = _clean(names[0].text_content())
            author = last_author or "участник"
            node = (msg.xpath(body_expr) or [msg])[0]
            text = _clean(" ".join(t.text_content() for t in node.xpath(".//div[" + _class_expr("text") + "]")))
            reply_targets = []
            for href in node.xpath(".//div[" + _class_expr("reply_to") + "]//a/@href"):
                match = re.search(r"(\d+)", href)
                if match:
                    reply_targets.append(int(match.group(1)))
            is_reply = any(target != args.topic_root_message_id for target in reply_targets)
            message_type, flags = _message_type_and_flags(node)
            payload = build_flud_message_payload(
                chat_id=args.chat_id,
                message_id=message_id,
                sent_at=sent_at,
                telegram_user_id=None,
                user_id=None,
                text=text,
                message_type=message_type,
                store_text_preview=False,
                is_reply=is_reply,
                reply_to_message_id=next((target for target in reply_targets if target != args.topic_root_message_id), None),
                source_author_hash=_author_hash(author),
                **flags,
            )
            rows.append((payload, _reaction_counts(node)))
    return rows


async def _run(args: argparse.Namespace) -> int:
    rows = _parse_messages(args)
    if args.output_jsonl:
        output_path = Path(args.output_jsonl)
        with output_path.open("w", encoding="utf-8") as fh:
            for payload, reactions in rows:
                fh.write(
                    json.dumps(
                        {
                            "payload": payload,
                            "reactions": reactions,
                        },
                        ensure_ascii=False,
                        default=str,
                    )
                    + "\n"
                )

    type_counts = Counter(payload["message_type"] for payload, _ in rows)
    reaction_total = sum(sum(reactions.values()) for _, reactions in rows)
    replies = sum(1 for payload, _ in rows if payload.get("is_reply"))
    author_hashes = {
        payload.get("source_author_hash")
        for payload, _ in rows
        if payload.get("source_author_hash")
    }
    if args.dry_run:
        print(
            "dry-run: "
            f"parsed_messages={len(rows)} "
            f"author_hashes={len(author_hashes)} "
            f"reactions={reaction_total} "
            f"replies={replies} "
            f"types={dict(sorted(type_counts.items()))}"
        )
        if args.output_jsonl:
            print(f"dry-run: wrote_jsonl={args.output_jsonl}")
        return 0

    await create_pool(max_retries=1)
    try:
        for payload, reactions in rows:
            await FludRepo.upsert_message(**payload)
            if reactions:
                await FludRepo.upsert_reactions(args.chat_id, payload["message_id"], reactions)
    finally:
        await close_pool()
    print(
        "imported: "
        f"messages={len(rows)} "
        f"author_hashes={len(author_hashes)} "
        f"reactions={reaction_total} "
        f"replies={replies} "
        f"types={dict(sorted(type_counts.items()))}"
    )
    return 0


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Import Telegram Desktop HTML export into flud pulse tables.")
    parser.add_argument("--path", required=True, help="Export directory or messages.html path")
    parser.add_argument("--chat-id", required=True, type=int)
    parser.add_argument("--start-date", required=True, help="YYYY-MM-DD")
    parser.add_argument("--end-date", required=True, help="YYYY-MM-DD")
    parser.add_argument(
        "--topic-root-message-id",
        type=int,
        help="Optional root message ID of the exported forum topic",
    )
    parser.add_argument("--output-jsonl", help="Write sanitized payload/reaction JSONL for server-side import")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true")
    mode.add_argument("--apply", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv or sys.argv[1:])
    return asyncio.run(_run(args))


if __name__ == "__main__":
    raise SystemExit(main())
