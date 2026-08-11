#!/usr/bin/env python3
"""Import sanitized flud pulse JSONL payloads into metadata tables."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any

from core.screw_core.db.pool import close_pool, create_pool
from core.screw_core.repos.flud_repo import FludRepo

REQUIRED_PAYLOAD_KEYS = {
    "chat_id",
    "message_id",
    "sent_at",
    "message_type",
}


def _parse_sent_at(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value
    if not value:
        raise ValueError("payload.sent_at is required")
    return datetime.fromisoformat(str(value).replace("Z", "+00:00"))


def _load_rows(path: Path) -> list[tuple[dict[str, Any], dict[str, int]]]:
    rows: list[tuple[dict[str, Any], dict[str, int]]] = []
    with path.open("r", encoding="utf-8") as fh:
        for line_no, line in enumerate(fh, start=1):
            if not line.strip():
                continue
            item = json.loads(line)
            if not isinstance(item, dict):
                raise ValueError(f"line {line_no}: expected JSON object")
            payload = item.get("payload")
            reactions = item.get("reactions") or {}
            if not isinstance(payload, dict):
                raise ValueError(f"line {line_no}: payload must be object")
            if not REQUIRED_PAYLOAD_KEYS.issubset(payload):
                missing = ", ".join(sorted(REQUIRED_PAYLOAD_KEYS - set(payload)))
                raise ValueError(f"line {line_no}: missing payload keys: {missing}")
            if not isinstance(reactions, dict):
                raise ValueError(f"line {line_no}: reactions must be object")
            payload["sent_at"] = _parse_sent_at(payload["sent_at"])
            safe_reactions: dict[str, int] = {}
            for key, value in reactions.items():
                try:
                    count = int(value)
                except (TypeError, ValueError):
                    count = 0
                if key and count > 0:
                    safe_reactions[str(key)] = count
            rows.append((payload, safe_reactions))
    return rows


async def _run(args: argparse.Namespace) -> int:
    rows = _load_rows(Path(args.file))
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
            f"messages={len(rows)} "
            f"author_hashes={len(author_hashes)} "
            f"reactions={reaction_total} "
            f"replies={replies} "
            f"types={dict(sorted(type_counts.items()))}"
        )
        return 0

    await create_pool(max_retries=1)
    try:
        for payload, reactions in rows:
            await FludRepo.upsert_message(**payload)
            if reactions:
                await FludRepo.upsert_reactions(int(payload["chat_id"]), int(payload["message_id"]), reactions)
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
    parser = argparse.ArgumentParser(description="Import sanitized flud pulse JSONL into metadata tables.")
    parser.add_argument("--file", required=True, help="Path to sanitized JSONL")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true", help="Parse and report count without DB writes")
    mode.add_argument("--apply", action="store_true", help="Write metadata to DB")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv or sys.argv[1:])
    return asyncio.run(_run(args))


if __name__ == "__main__":
    raise SystemExit(main())
