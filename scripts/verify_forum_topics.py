"""Explicit integration check for an isolated synthetic PostgreSQL database.

Requires FORUM_TOPIC_VERIFY_ISOLATED=1 and FORUM_TOPIC_TEST_DSN. The DSN must
point to loopback (or the isolated Docker service `postgres`) and a database
named screw_forum_test_*. It never loads application DB settings or .env.
Synthetic writes are transactional and always rolled back. No Telegram calls.
"""
from __future__ import annotations

import asyncio
import os
import re
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from unittest.mock import patch
from urllib.parse import unquote, urlsplit

import asyncpg

from core.screw_core.repos.forum_topics_repo import ForumTopicsRepo

SYNTHETIC_CHAT_ID = -1001234567890


def isolated_test_dsn() -> str:
    if os.getenv("FORUM_TOPIC_VERIFY_ISOLATED") != "1":
        raise ValueError("explicit isolated verification flag required")
    dsn = os.getenv("FORUM_TOPIC_TEST_DSN", "")
    parsed = urlsplit(dsn)
    if (parsed.scheme not in {"postgres", "postgresql"}
            or parsed.hostname not in {"127.0.0.1", "localhost", "::1", "postgres"}
            or not re.fullmatch(r"screw_forum_test_[a-z0-9_]+", unquote(parsed.path.lstrip("/")))
            or parsed.query or parsed.fragment):
        raise ValueError("only an explicit isolated synthetic database is allowed")
    return dsn


async def verify(conn: asyncpg.Connection) -> None:
    """Check a provisioned fixture; retain neither rows nor transaction changes."""
    thread_id = 9223372036854775806
    tx = conn.transaction()
    await tx.start()
    try:
        if await conn.fetchval(
            "SELECT 1 FROM screw.forum_topics WHERE chat_id=$1 AND message_thread_id=$2",
            SYNTHETIC_CHAT_ID, thread_id,
        ):
            raise RuntimeError("synthetic fixture row already exists")

        @asynccontextmanager
        async def connection():
            yield conn

        seen = datetime.now(timezone.utc)
        payload = dict(chat_id=SYNTHETIC_CHAT_ID, message_thread_id=thread_id,
                       name="SYNTHETIC_INTEGRATION_ONLY", name_source="owner_marker",
                       message_id=100, observed_at=seen)
        with patch("core.screw_core.repos.forum_topics_repo.acquire_connection", connection):
            await ForumTopicsRepo.observe(**payload)
            payload.update(name="SYNTHETIC_RENAMED", name_source="service", message_id=102)
            await ForumTopicsRepo.observe(**payload)
            payload.update(name="SYNTHETIC_STALE", message_id=101, observed_at=seen - timedelta(seconds=1))
            await ForumTopicsRepo.observe(**payload)
            payload.update(name=None, name_source=None, message_id=103, observed_at=seen + timedelta(seconds=1))
            await ForumTopicsRepo.observe(**payload)
        row = await conn.fetchrow(
            "SELECT * FROM screw.forum_topics WHERE chat_id=$1 AND message_thread_id=$2",
            SYNTHETIC_CHAT_ID, thread_id,
        )
        if not (row["name"] == "SYNTHETIC_RENAMED" and row["name_source"] == "service"
                and row["name_message_id"] == 102 and row["first_seen_at"] == seen - timedelta(seconds=1)
                and row["last_seen_at"] == seen + timedelta(seconds=1)):
            raise AssertionError("metadata ordering or null-name preservation failed")
        for wrong_chat in (SYNTHETIC_CHAT_ID - 1, 99):
            try:
                async with conn.transaction():
                    await conn.execute(
                        "INSERT INTO screw.forum_topics (chat_id,message_thread_id,first_seen_at,last_seen_at) "
                        "VALUES ($1,1,now(),now())", wrong_chat,
                    )
            except asyncpg.CheckViolationError:
                pass
            else:
                raise AssertionError("exact-chat database guard missing")
    finally:
        await tx.rollback()
    if await conn.fetchval(
        "SELECT 1 FROM screw.forum_topics WHERE chat_id=$1 AND message_thread_id=$2",
        SYNTHETIC_CHAT_ID, thread_id,
    ):
        raise AssertionError("synthetic row was not rolled back")


async def main() -> int:
    # Validate before attempting any connection, without echoing a DSN/credential.
    try:
        dsn = isolated_test_dsn()
    except ValueError:
        print("FAIL: isolated test database configuration required")
        return 2
    conn = None
    try:
        conn = await asyncpg.connect(dsn, timeout=5, command_timeout=10)
        await verify(conn)
    except Exception as exc:
        print(f"FAIL: isolated forum verification ({type(exc).__name__})")
        return 1
    finally:
        if conn is not None:
            await conn.close()
    print("PASS: isolated PostgreSQL ordering, exact-chat guard and rollback")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
