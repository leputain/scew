from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock

import asyncpg
import pytest
from aiogram.types import Update

from apps.attendance_bot.app.aiogram_app import forum_topics as topics
from apps.attendance_bot.app.aiogram_app.middlewares import AiogramSecurityPolicyMiddleware
from scripts import verify_forum_topics as verifier

CHAT = verifier.SYNTHETIC_CHAT_ID
OWNER = 123456789


@pytest.fixture
def configured(monkeypatch):
    monkeypatch.setenv("FORUM_TOPIC_CAPTURE_ENABLED", "1")
    monkeypatch.setenv("FORUM_TOPIC_CAPTURE_CHAT_ID", str(CHAT))
    monkeypatch.setenv("FORUM_TOPIC_CAPTURE_OWNER_ID", str(OWNER))
    monkeypatch.setattr(topics, "get_allowed_group_chat_ids", lambda: {CHAT})


def update(**changes) -> Update:
    message = dict(
        message_id=22, date=datetime(2026, 11, 14, tzinfo=timezone.utc), message_thread_id=7,
        chat=dict(id=CHAT, type="supergroup", is_forum=True),
        from_user=dict(id=OWNER, is_bot=False, first_name="Synthetic"), text="ordinary body",
    )
    message.update(changes)
    return Update.model_validate(dict(update_id=1, message=message))


@pytest.mark.parametrize("setting,value", [
    ("FORUM_TOPIC_CAPTURE_ENABLED", "0"), ("FORUM_TOPIC_CAPTURE_ENABLED", "true"),
    ("FORUM_TOPIC_CAPTURE_CHAT_ID", ""), ("FORUM_TOPIC_CAPTURE_CHAT_ID", "12"),
    ("FORUM_TOPIC_CAPTURE_CHAT_ID", "-0"), ("FORUM_TOPIC_CAPTURE_CHAT_ID", "invalid"),
    ("FORUM_TOPIC_CAPTURE_CHAT_ID", "-9223372036854775809"),
    ("FORUM_TOPIC_CAPTURE_OWNER_ID", ""), ("FORUM_TOPIC_CAPTURE_OWNER_ID", "0"),
    ("FORUM_TOPIC_CAPTURE_OWNER_ID", "-2"), ("FORUM_TOPIC_CAPTURE_OWNER_ID", "9223372036854775808"),
])
def test_capture_fails_closed_for_incomplete_or_invalid_config(configured, monkeypatch, setting, value):
    monkeypatch.setenv(setting, value)
    assert topics.load_capture_config() is None
    assert topics.extract_topic(update()) is None


def test_capture_requires_main_group_allowlist(configured, monkeypatch):
    monkeypatch.setattr(topics, "get_allowed_group_chat_ids", lambda: {CHAT - 1})
    assert topics.extract_topic(update()) is None


def test_capture_is_off_without_opt_in(monkeypatch):
    monkeypatch.delenv("FORUM_TOPIC_CAPTURE_ENABLED", raising=False)
    assert topics.extract_topic(update()) is None


@pytest.mark.parametrize("changes", [
    {"chat": dict(id=CHAT - 1, type="supergroup", is_forum=True)},
    {"chat": dict(id=CHAT, type="group", is_forum=True)},
    {"chat": dict(id=CHAT, type="supergroup", is_forum=False)},
    {"message_thread_id": None}, {"message_thread_id": 0}, {"message_thread_id": -1},
])
def test_other_chats_and_non_topic_messages_are_ignored(configured, changes):
    assert topics.extract_topic(update(**changes)) is None
    assert topics.extract_topic(Update(update_id=2)) is None


@pytest.mark.parametrize("field,payload", [
    ("forum_topic_created", dict(name="Synthetic created", icon_color=1)),
    ("forum_topic_edited", dict(name="Synthetic renamed")),
])
def test_service_events_capture_topic_names(configured, field, payload):
    result = topics.extract_topic(update(**{field: payload}))
    assert result["name"] == payload["name"]
    assert result["name_source"] == "service"
    assert set(result) == {"chat_id", "message_thread_id", "name", "name_source", "message_id", "observed_at"}


def test_owner_marker_is_exact_and_no_message_body_or_author_is_retained(configured):
    result = topics.extract_topic(update(text="Тема: Synthetic name"))
    assert result["name"] == "Synthetic name"
    assert result["name_source"] == "owner_marker"
    plain = topics.extract_topic(update(text="ordinary private text"))
    assert plain["name"] is None and plain["name_source"] is None
    assert "ordinary private text" not in repr(plain)
    assert "from_user" not in plain


@pytest.mark.parametrize("text", ["prefix Тема: Synthetic", "Тема: ", "Тема: one\ntwo", "Тема: " + "a" * 129])
def test_invalid_markers_do_not_change_the_name(configured, text):
    assert topics.extract_topic(update(text=text))["name"] is None


@pytest.mark.parametrize("changes", [
    {"from_user": dict(id=OWNER + 1, is_bot=False, first_name="Other")},
    {"sender_chat": dict(id=CHAT, type="supergroup")},
    {"forward_origin": dict(type="hidden_user", sender_user_name="Synthetic", date=0)},
    {"from_user": None},
])
def test_impersonated_or_forwarded_owner_marker_does_not_name_topic(configured, changes):
    assert topics.extract_topic(update(text="Тема: Synthetic", **changes))["name"] is None


@pytest.mark.asyncio
@pytest.mark.parametrize("fail", ["timeout", "error"])
async def test_capture_errors_never_consume_updates_or_log_payload(configured, monkeypatch, fail, caplog):
    async def observe(**kwargs):
        if fail == "timeout":
            await asyncio.sleep(1)
        raise RuntimeError("sensitive message or SQL arguments")

    monkeypatch.setattr(topics.ForumTopicsRepo, "observe", observe)
    monkeypatch.setattr(topics, "CAPTURE_TIMEOUT", 0.005)
    handler = AsyncMock(return_value="handled")
    event, data = update(), {}
    assert await topics.ForumTopicCaptureMiddleware()(handler, event, data) == "handled"
    handler.assert_awaited_once_with(event, data)
    assert "sensitive message" not in caplog.text
    assert "ordinary body" not in caplog.text


@pytest.mark.asyncio
async def test_capture_does_not_bypass_unknown_user_policy(configured, monkeypatch):
    from apps.attendance_bot.app.aiogram_app import middlewares

    monkeypatch.setattr(middlewares, "get_allowed_group_chat_ids", lambda: {CHAT})
    monkeypatch.setattr(middlewares.UsersRepo, "get_by_telegram_id", AsyncMock(return_value=None))
    observe = AsyncMock()
    monkeypatch.setattr(topics.ForumTopicsRepo, "observe", observe)
    business_handler = AsyncMock()

    async def policy(event, data):
        return await AiogramSecurityPolicyMiddleware()(business_handler, event, data)

    await topics.ForumTopicCaptureMiddleware()(policy, update(), {})
    observe.assert_awaited_once()
    business_handler.assert_not_awaited()


@pytest.mark.asyncio
async def test_disabled_capture_does_not_touch_db(configured, monkeypatch):
    monkeypatch.setenv("FORUM_TOPIC_CAPTURE_ENABLED", "0")
    observe = AsyncMock()
    monkeypatch.setattr(topics.ForumTopicsRepo, "observe", observe)
    handler = AsyncMock(return_value="ok")
    assert await topics.ForumTopicCaptureMiddleware()(handler, update(), {}) == "ok"
    observe.assert_not_awaited()


@pytest.mark.parametrize("dsn", [
    "postgresql://localhost/screw", "postgresql://remote.example/screw_forum_test_fixture",
    "postgresql://localhost/screw_forum_test_fixture?host=remote.example",
    "postgresql://localhost/screw_forum_test_fixture#fragment", "",
])
def test_verifier_rejects_non_isolated_targets(monkeypatch, dsn):
    monkeypatch.setenv("FORUM_TOPIC_VERIFY_ISOLATED", "1")
    monkeypatch.setenv("FORUM_TOPIC_TEST_DSN", dsn)
    with pytest.raises(ValueError):
        verifier.isolated_test_dsn()


@pytest.mark.asyncio
async def test_verifier_does_not_connect_without_explicit_guard(monkeypatch):
    monkeypatch.delenv("FORUM_TOPIC_VERIFY_ISOLATED", raising=False)
    connect = AsyncMock()
    monkeypatch.setattr(verifier.asyncpg, "connect", connect)
    assert await verifier.main() == 2
    connect.assert_not_awaited()


@pytest.mark.asyncio
async def test_postgres_forum_migration_and_upsert_in_isolated_database():
    """Opt-in real PG check; an empty, explicitly named test database is required."""
    import os

    if os.getenv("FORUM_TOPIC_VERIFY_ISOLATED") != "1":
        pytest.skip("set isolated verifier flag and test DSN for real PostgreSQL checks")
    dsn = verifier.isolated_test_dsn()
    conn = await asyncpg.connect(dsn, timeout=5, command_timeout=15)
    owns_schema = False
    migration = Path("infra/postgres/migrations/006_forum_topics.sql").read_text(encoding="utf-8")

    async def guard():
        return await conn.fetchval(
            "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
            "WHERE conrelid='screw.forum_topics'::regclass AND conname='forum_topics_chat_id_check'",
        )

    async def insert(chat_id):
        await conn.execute(
            "INSERT INTO screw.forum_topics (chat_id,message_thread_id,first_seen_at,last_seen_at) "
            "VALUES ($1,7,now(),now())", chat_id,
        )

    try:
        if await conn.fetchval("SELECT to_regnamespace('screw')") is not None:
            raise RuntimeError("integration fixture must have no existing screw schema")
        await conn.execute(Path("infra/postgres/init/001_schema.sql").read_text(encoding="utf-8"))
        owns_schema = True
        assert await guard() == "CHECK (false)"
        for _ in range(2):
            await conn.execute(migration)
            assert await guard() == "CHECK (false)"
            with pytest.raises(asyncpg.CheckViolationError):
                await insert(CHAT)

        await conn.execute("SELECT set_config('screw.forum_topic_chat_id', $1, false)", str(CHAT))
        await conn.execute(migration)
        await conn.execute(migration)
        await conn.execute("RESET screw.forum_topic_chat_id")
        exact_guard = await guard()
        await verifier.verify(conn)
        await insert(CHAT)
        await conn.execute(migration)
        assert await guard() == exact_guard

        # Re-scope populated data must fail atomically, retaining the old guard.
        await conn.execute("SELECT set_config('screw.forum_topic_chat_id', $1, false)", str(CHAT - 1))
        with pytest.raises(asyncpg.CheckViolationError):
            await conn.execute(migration)
        await conn.execute("ROLLBACK")
        await conn.execute("RESET screw.forum_topic_chat_id")
        assert await guard() == exact_guard
        assert await conn.fetchval("SELECT count(*) FROM screw.forum_topics") == 1
        with pytest.raises(asyncpg.CheckViolationError):
            await insert(CHAT - 1)

        # Standalone migration (without fresh init) is also deny-all by default.
        await conn.execute("DROP TABLE screw.forum_topics")
        await conn.execute(migration)
        await conn.execute(migration)
        assert await guard() == "CHECK (false)"
    finally:
        await conn.execute("ROLLBACK")
        if owns_schema:
            await conn.execute("DROP SCHEMA screw CASCADE")
        await conn.close()
