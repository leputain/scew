"""Synthetic notifier tests: no real token, person, chat, polling or HTTP request."""

import asyncio
import io
import json
import os
import secrets
import subprocess
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import uuid4

import asyncpg
import pytest
from aiogram.exceptions import (
    TelegramBadRequest,
    TelegramForbiddenError,
    TelegramNetworkError,
    TelegramRetryAfter,
    TelegramServerError,
    TelegramUnauthorizedError,
)
from aiogram.methods import SendMessage

from apps.medoed_notifier import smoke, worker


def item(**overrides):
    return {"spin_id": str(uuid4()), "employee_id": 1, "additional_days": 2,
            "claim_token": uuid4(), "attempts": 1, **overrides}


class FakePool:
    def __init__(self, target=None):
        self.target = target
        self.updates = []
        self.operations = []

    async def fetchrow(self, sql, *parameters):
        assert "LEFT JOIN medoed.notification_recipients" in sql or "FROM medoed.ozko_employees" in sql
        assert len(parameters) == 2
        return self.target

    async def execute(self, sql, *parameters):
        assert sql in {worker.SETTLE_SQL, worker.GROUP_SETTLE_SQL}
        self.operations.append((sql, parameters))
        self.updates.append(parameters)
        return "UPDATE 1"


@pytest.mark.parametrize(("days", "expected"), [(0, "Дополнительных офисных дней нет"), (1, "1 офисный день"),
                                                   (2, "2 офисных дня"), (5, "5 офисных дней"),
                                                   (11, "11 офисных дней"), (21, "21 офисный день"),
                                                   (31, "31 офисный день")])
def test_numeric_snapshot_message(days, expected):
    notification_id = str(uuid4())
    text = worker.notification_text(notification_id, days)
    assert expected in text
    assert notification_id not in text
    assert "Результат сохранён в журнале Медоеда" in text
    assert "http" not in text


@pytest.mark.parametrize("days", [-1, 32, "2", True, None])
def test_rejects_invalid_metadata(days):
    with pytest.raises(ValueError):
        worker.notification_text(str(uuid4()), days)


def test_config_reads_only_explicit_secret_files_and_sanitizes_errors(tmp_path):
    database = tmp_path / "database.json"
    token = tmp_path / "token"
    group = tmp_path / "group-target.json"
    database.write_text(json.dumps({"host": "fixture-db", "port": 5432, "database": "fixture",
                                    "user": "fixture-role", "password": "fixture-password",
                                    "max_size": 100, "command_timeout": 0}), encoding="utf-8")
    token.write_text("123456:" + "x" * 35, encoding="utf-8")
    group.write_text(json.dumps({"chat_id": -100123456, "message_thread_id": None}), encoding="utf-8")
    env = {"MEDOED_NOTIFIER_DB_CONFIG_FILE": str(database), "SCREW_BOT_TOKEN_FILE": str(token),
           "MEDOED_NOTIFIER_GROUP_CONFIG_FILE": str(group)}
    config = worker.load_config(env)
    assert set(config.database) == {"host", "port", "database", "user", "password"}
    assert config.interval == 1
    assert config.group_target == worker.GroupTarget(-100123456, None)
    token.write_text("private-invalid-fixture-token", encoding="utf-8")
    with pytest.raises(ValueError) as failure:
        worker.load_config(env)
    assert "private-invalid" not in str(failure.value)
    assert str(tmp_path) not in str(failure.value)


@pytest.mark.parametrize("target", [{}, {"chat_id": 123}, {"chat_id": True}, {"chat_id": "-100123"},
    {"chat_id": -100123, "message_thread_id": 0}, {"chat_id": -100123, "message_thread_id": True},
    {"chat_id": -100123, "message_thread_id": -1}])
def test_group_secret_rejects_missing_private_or_invalid_targets_before_startup(tmp_path, target):
    database = tmp_path / "database.json"
    token = tmp_path / "token"
    group = tmp_path / "group.json"
    database.write_text(json.dumps({"host": "fixture", "port": 5432, "database": "fixture",
                                    "user": "fixture", "password": "fixture"}), encoding="utf-8")
    token.write_text("123456:" + "x" * 35, encoding="utf-8")
    group.write_text(json.dumps(target), encoding="utf-8")
    env = {"MEDOED_NOTIFIER_DB_CONFIG_FILE": str(database), "SCREW_BOT_TOKEN_FILE": str(token),
           "MEDOED_NOTIFIER_GROUP_CONFIG_FILE": str(group)}
    with pytest.raises(ValueError, match="Invalid notifier configuration"):
        worker.load_config(env)
    del env["MEDOED_NOTIFIER_GROUP_CONFIG_FILE"]
    with pytest.raises(ValueError, match="Invalid notifier configuration"):
        worker.load_config(env)


def test_group_message_uses_escaped_snapshot_numeric_days_and_configured_topic_only(monkeypatch):
    async def scenario():
        pool = FakePool({"eligible": True, "deleted_at": None})
        calls = []

        async def send(bot, **kwargs):
            calls.append(kwargs)
            return SimpleNamespace(message=SimpleNamespace(message_id=112233))

        monkeypatch.setattr(worker, "send_system_message", send)
        notifier = worker.Notifier(pool, object(), group_target=worker.GroupTarget(-100123456, 77))
        queued = item(employee_name="Синтетический <b> & Co")
        await notifier.deliver(queued, "group")
        assert calls[0]["chat_id"] == -100123456
        assert calls[0]["message_thread_id"] == 77
        assert "Синтетический &lt;b&gt; &amp; Co" in calls[0]["html_text"]
        assert "2 офисных дня" in calls[0]["html_text"]
        assert queued["spin_id"] not in calls[0]["html_text"]
        assert "123456" not in calls[0]["html_text"]
        assert pool.operations[0][0] == worker.GROUP_SETTLE_SQL
        assert pool.updates[0][2] == "sent"
    asyncio.run(scenario())


@pytest.mark.parametrize("target", [None, {"eligible": True, "deleted_at": "deleted"},
                                      {"eligible": False, "deleted_at": None}])
def test_group_cancelled_for_deleted_or_ineligible_employee_without_private_recipient(monkeypatch, target):
    async def scenario():
        async def forbidden_send(*args, **kwargs):
            pytest.fail("Cancelled group messages must not call Telegram")

        monkeypatch.setattr(worker, "send_system_message", forbidden_send)
        pool = FakePool(target)
        notifier = worker.Notifier(pool, object(), group_target=worker.GroupTarget(-100123456))
        await notifier.deliver(item(employee_name="Synthetic"), "group")
        assert pool.updates[0][2] == "cancelled"
        assert pool.operations[0][0] == worker.GROUP_SETTLE_SQL
    asyncio.run(scenario())


def test_group_dependency_failure_does_not_block_dm_and_channels_run_concurrently(tmp_path):
    async def scenario():
        stop = asyncio.Event()
        group_started = asyncio.Event()
        calls = []

        class ConcurrentNotifier(worker.Notifier):
            async def preflight(self):
                pass

            async def process_channel(self, channel):
                calls.append(channel)
                if channel == "group":
                    group_started.set()
                    return False
                await asyncio.wait_for(group_started.wait(), timeout=1)
                stop.set()
                return True

        notifier = ConcurrentNotifier(None, None, group_target=worker.GroupTarget(-100123456),
                                      heartbeat=tmp_path / "heartbeat")
        await notifier.run(stop)
        assert set(calls) == {"dm", "group"}
    asyncio.run(scenario())


def test_next_group_item_progresses_while_dm_request_is_still_running(tmp_path):
    async def scenario():
        stop = asyncio.Event()
        release_dm = asyncio.Event()
        group_cycles = []

        class SlowDmNotifier(worker.Notifier):
            async def preflight(self):
                pass

            async def process_channel(self, channel):
                if channel == "dm":
                    await release_dm.wait()
                    return True
                group_cycles.append("group")
                if len(group_cycles) == 2:
                    stop.set()
                    release_dm.set()
                return True

        notifier = SlowDmNotifier(None, None, group_target=worker.GroupTarget(-100123456),
                                  interval=0.001, heartbeat=tmp_path / "heartbeat")
        await asyncio.wait_for(notifier.run(stop), timeout=1)
        assert len(group_cycles) == 2
    asyncio.run(scenario())


@pytest.mark.parametrize(("kind", "state", "code"), [
    ("success", "sent", None), ("429", "pending", "rate_limited"),
    ("403", "failed", "forbidden"), ("400", "failed", "bad_request"),
    ("401", "failed", "unauthorized"), ("network", "uncertain", "transport_uncertain"),
    ("server", "uncertain", "transport_uncertain"), ("timeout", "uncertain", "transport_uncertain"),
    ("os", "uncertain", "transport_uncertain"), ("unknown", "uncertain", "unexpected_delivery_error"),
    ("missing_id", "uncertain", "missing_message_id"),
])
def test_exactly_one_send_attempt_and_explicit_error_policy(monkeypatch, kind, state, code):
    async def scenario():
        pool = FakePool({"telegram_id": 123456, "deleted_at": None})
        notifier = worker.Notifier(pool, object())
        queued = item()
        calls = []
        request = SendMessage(chat_id=123456, text="Synthetic")
        errors = {"429": TelegramRetryAfter(request, "Fixture", retry_after=9),
                  "403": TelegramForbiddenError(request, "Fixture"),
                  "400": TelegramBadRequest(request, "Fixture"),
                  "401": TelegramUnauthorizedError(request, "Fixture"),
                  "network": TelegramNetworkError(request, "Fixture"),
                  "server": TelegramServerError(request, "Fixture"),
                  "timeout": TimeoutError("Fixture"), "os": OSError("Fixture"),
                  "unknown": RuntimeError("synthetic-private-value-must-not-be-logged")}

        async def send(bot, **kwargs):
            calls.append(kwargs)
            if kind in errors:
                raise errors[kind]
            return SimpleNamespace(message=SimpleNamespace(message_id=None if kind == "missing_id" else 654321))

        monkeypatch.setattr(worker, "send_system_message", send)
        before = datetime.now(timezone.utc)
        await notifier.deliver(queued)
        assert len(calls) == 1
        assert calls[0]["delivery_mode"] == "html"
        assert calls[0]["chat_id"] == 123456
        assert calls[0]["reaction_emoji"] is None
        result = pool.updates[0]
        assert result[:4] == (queued["spin_id"], queued["claim_token"], state, code)
        assert notifier.paused == (kind == "401")
        if kind == "429":
            assert result[4] >= before + timedelta(seconds=10)
            assert result[4] < datetime.now(timezone.utc) + timedelta(seconds=12)
        if kind == "success":
            assert result[5] == 654321
    asyncio.run(scenario())


@pytest.mark.parametrize(("target", "code"), [(None, "deleted_spin"),
    ({"telegram_id": 123456, "deleted_at": "2026-10-18"}, "deleted_spin"),
    ({"telegram_id": None, "deleted_at": None}, "recipient_unavailable"),
    ({"telegram_id": -123, "deleted_at": None}, "recipient_unavailable")])
def test_cancelled_notifications_never_send(monkeypatch, target, code):
    async def forbidden_send(*args, **kwargs):
        pytest.fail("Cancelled notifications must not call Telegram")

    monkeypatch.setattr(worker, "send_system_message", forbidden_send)
    pool = FakePool(target)
    asyncio.run(worker.Notifier(pool, object()).deliver(item()))
    assert pool.updates[0][2:4] == ("cancelled", code)


def test_cancelled_http_attempt_is_uncertain_and_cancellation_propagates(monkeypatch):
    async def scenario():
        async def send(*args, **kwargs):
            raise asyncio.CancelledError

        monkeypatch.setattr(worker, "send_system_message", send)
        pool = FakePool({"telegram_id": 123456, "deleted_at": None})
        with pytest.raises(asyncio.CancelledError):
            await worker.Notifier(pool, object()).deliver(item())
        assert pool.updates[0][2:4] == ("uncertain", "delivery_interrupted")
    asyncio.run(scenario())


def test_smoke_has_explicit_disclaimer_and_strict_id_input():
    assert smoke.read_payload('{"employeeId":"9007199254740993","additionalDays":2}') == (9007199254740993, 2)
    for raw in ['{"employeeId":1,"additionalDays":2}', '{"employeeId":"01","additionalDays":2}',
                '{"employeeId":"9223372036854775808","additionalDays":2}',
                '{"employeeId":"1","additionalDays":true}',
                '{"employeeId":"1","additionalDays":2,"chatId":123}']:
        with pytest.raises(ValueError):
            smoke.read_payload(raw)
    text = smoke.test_message(2)
    assert "Тестовое уведомление" in text
    assert "Не является назначением офисных дней" in text
    assert "2 офисных дня" in text
    assert "история розыгрышей не изменены" in text


@pytest.mark.parametrize("timeout", [False, True])
def test_smoke_one_readonly_recipient_getme_and_one_send_without_any_database_writes(monkeypatch, timeout):
    async def scenario():
        calls = []

        class ReadOnlyConnection:
            async def fetchrow(self, sql, employee_id):
                assert sql == "SELECT telegram_id FROM medoed.notification_recipients WHERE user_id = $1"
                assert employee_id == 1
                calls.append("recipient")
                return {"telegram_id": 123456}

        class SendOnlyBot:
            async def get_me(self, **kwargs):
                calls.append("getMe")
                return SimpleNamespace(is_bot=True)

        async def send(bot, **kwargs):
            calls.append("sendMessage")
            assert kwargs["chat_id"] == 123456
            assert kwargs["delivery_mode"] == "html"
            assert "Не является назначением офисных дней" in kwargs["html_text"]
            if timeout:
                raise TimeoutError("Synthetic timeout after possible commit")
            return SimpleNamespace(message=SimpleNamespace(message_id=765432))

        monkeypatch.setattr(smoke, "send_system_message", send)
        if timeout:
            with pytest.raises(TimeoutError):
                await smoke.run_smoke(ReadOnlyConnection(), SendOnlyBot(), 1, 2)
        else:
            assert await smoke.run_smoke(ReadOnlyConnection(), SendOnlyBot(), 1, 2) == 765432
        assert calls == ["recipient", "getMe", "sendMessage"]
    asyncio.run(scenario())


def test_smoke_rejects_unexpected_container_identity_before_secret_or_stdin_access(monkeypatch, capsys):
    monkeypatch.setattr(smoke.os, "geteuid", lambda: 10002, raising=False)
    assert asyncio.run(smoke.main()) == 2
    assert json.loads(capsys.readouterr().out) == {"status": "failed", "code": "operator_container_identity_required"}


@pytest.mark.parametrize("uid", [0, 10001])
def test_smoke_accepts_worker_uid_for_its_own_restricted_secret_files(monkeypatch, capsys, uid):
    monkeypatch.setattr(smoke.os, "geteuid", lambda: uid, raising=False)
    monkeypatch.setattr(smoke.sys, "stdin", io.StringIO('{"employeeId":"1","additionalDays":2}'))
    monkeypatch.setattr(smoke.logging, "disable", lambda _: None)
    reached = []

    def synthetic_missing_config():
        reached.append("configuration")
        raise ValueError("Synthetic missing configuration")

    monkeypatch.setattr(smoke, "load_config", synthetic_missing_config)
    assert asyncio.run(smoke.main()) == 1
    assert reached == ["configuration"]
    assert json.loads(capsys.readouterr().out) == {
        "status": "failed", "code": "configuration_or_recipient_unavailable"}


@pytest.mark.skipif(os.environ.get("MEDOED_NOTIFIER_TEST_POSTGRES_DOCKER") != "1", reason="Opt-in isolated Docker fixture")
def test_actual_postgres_claim_permissions_no_resends_and_recipient_guards(monkeypatch):
    """Exercise real SKIP LOCKED/column grants/state transitions, never a real Telegram API."""
    suffix = secrets.token_hex(6)
    container = f"screw-medoed-notifier-test-{suffix}"
    password = secrets.token_hex(24)
    started = False

    def docker(*args):
        return subprocess.check_output(["docker", *args], text=True, encoding="utf-8", timeout=30).strip()

    async def scenario():
        bindings = json.loads(docker("inspect", "--format", "{{json .NetworkSettings.Ports}}", container))
        config = {"host": "127.0.0.1", "port": int(bindings["5432/tcp"][0]["HostPort"]),
                  "database": "postgres", "user": "postgres", "password": password}
        admin = None
        pool = None
        try:
            for _ in range(80):
                try:
                    admin = await asyncpg.connect(**config, timeout=1)
                    break
                except (OSError, asyncpg.PostgresError):
                    await asyncio.sleep(0.2)
            assert admin is not None
            await admin.execute(f"CREATE ROLE medoed_notifier LOGIN PASSWORD '{password}' CONNECTION LIMIT 3")
            await admin.execute("""
                CREATE SCHEMA screw;
                CREATE TABLE screw.users (id bigint PRIMARY KEY, telegram_id bigint, status text, department text,
                    work_mode text, role text);
                INSERT INTO screw.users VALUES (1, 123456, 'active', 'OZKO', 'HYBRID', 'employee'),
                    (2, 234567, 'active', 'OZKO', 'DUTY', 'employee'),
                    (3, 345678, 'active', 'OZKO', 'HYBRID', 'no_tg_worker'),
                    (4, 456789, 'terminated', 'OZKO', 'HYBRID', 'employee');
                CREATE SCHEMA medoed;
                CREATE TABLE medoed.spins(id text PRIMARY KEY, employee text, deleted_at text);
                CREATE TABLE medoed.notifications(spin_id text PRIMARY KEY REFERENCES medoed.spins(id),
                    employee_id bigint NOT NULL, additional_days integer NOT NULL,
                    state text NOT NULL DEFAULT 'pending', attempts integer NOT NULL DEFAULT 0,
                    available_at timestamptz NOT NULL DEFAULT (clock_timestamp()+interval '5 seconds'),
                    claimed_at timestamptz, claim_token uuid,
                    sent_at timestamptz, telegram_message_id bigint, last_error_code text,
                    created_at timestamptz NOT NULL DEFAULT now());
                CREATE TABLE medoed.group_notifications (LIKE medoed.notifications INCLUDING ALL);
                ALTER TABLE medoed.group_notifications ADD COLUMN employee_name text NOT NULL;
                CREATE VIEW medoed.notification_recipients WITH (security_barrier=true) AS
                    SELECT id AS user_id, telegram_id FROM screw.users WHERE status='active' AND department='OZKO'
                    AND work_mode <> 'DUTY' AND role <> 'no_tg_worker' AND telegram_id>0;
                CREATE VIEW medoed.ozko_employees WITH (security_barrier=true) AS
                    SELECT id FROM screw.users WHERE status='active' AND department='OZKO' AND work_mode<>'DUTY';
                REVOKE ALL ON SCHEMA medoed FROM PUBLIC;
                GRANT USAGE ON SCHEMA medoed TO medoed_notifier;
                GRANT SELECT ON medoed.notifications, medoed.group_notifications,
                    medoed.notification_recipients TO medoed_notifier;
                GRANT SELECT(id) ON medoed.ozko_employees TO medoed_notifier;
                GRANT SELECT(id, deleted_at) ON medoed.spins TO medoed_notifier;
                GRANT UPDATE(state, attempts, available_at, claimed_at, claim_token, sent_at,
                    telegram_message_id, last_error_code) ON medoed.notifications TO medoed_notifier;
                GRANT UPDATE(state, attempts, available_at, claimed_at, claim_token, sent_at,
                    telegram_message_id, last_error_code) ON medoed.group_notifications TO medoed_notifier;
            """)
            pool = await asyncpg.create_pool(**{**config, "user": "medoed_notifier"}, min_size=1, max_size=2,
                                             timeout=2, command_timeout=5)
            notifier = worker.Notifier(pool, object())
            await notifier.preflight()
            for sql in ["SELECT * FROM screw.users", "SELECT employee FROM medoed.spins",
                        "DELETE FROM medoed.notifications", "UPDATE medoed.notifications SET additional_days=31",
                        "INSERT INTO medoed.notifications(spin_id,employee_id,additional_days) VALUES('bad',1,1)",
                        "DELETE FROM medoed.group_notifications", "UPDATE medoed.group_notifications SET employee_name='bad'",
                        "UPDATE medoed.group_notifications SET employee_id=3",
                        "UPDATE medoed.group_notifications SET additional_days=31",
                        "INSERT INTO medoed.group_notifications(spin_id,employee_id,additional_days,employee_name) VALUES('bad',1,1,'bad')"]:
                with pytest.raises(asyncpg.InsufficientPrivilegeError):
                    await pool.execute(sql)

            async def queue(employee_id=1, deleted=False, *, group=False, ready=True):
                spin_id = str(uuid4())
                await admin.execute("INSERT INTO medoed.spins VALUES($1,'Synthetic',$2)", spin_id,
                                    "2026-10-18T00:00:00Z" if deleted else None)
                await admin.execute("INSERT INTO medoed.notifications(spin_id,employee_id,additional_days) VALUES($1,$2,2)",
                                    spin_id, employee_id)
                if ready:
                    await admin.execute("""UPDATE medoed.notifications SET available_at=now()-interval '1 second',
                        created_at=now()-interval '6 seconds' WHERE spin_id=$1""", spin_id)
                if group:
                    await admin.execute("""INSERT INTO medoed.group_notifications
                        (spin_id,employee_id,additional_days,employee_name) VALUES($1,$2,2,'Synthetic <Snapshot>')""",
                                        spin_id, employee_id)
                    if ready:
                        await admin.execute("""UPDATE medoed.group_notifications SET available_at=now()-interval '1 second',
                            created_at=now()-interval '6 seconds' WHERE spin_id=$1""", spin_id)
                return spin_id

            spin_id = await queue()
            claims = await asyncio.gather(*(notifier.claim() for _ in range(10)))
            claimed = [value for value in claims if value is not None]
            assert len(claimed) == 1
            assert claimed[0]["spin_id"] == spin_id
            await admin.execute("UPDATE medoed.notifications SET claimed_at=now()-interval '121 seconds' WHERE spin_id=$1",
                                spin_id)
            await notifier.expire_interrupted()
            assert await admin.fetchval("SELECT state FROM medoed.notifications WHERE spin_id=$1", spin_id) == "uncertain"
            assert await notifier.claim() is None

            sent = []

            async def send(bot, **kwargs):
                sent.append(kwargs)
                return SimpleNamespace(message=SimpleNamespace(message_id=111))

            monkeypatch.setattr(worker, "send_system_message", send)
            for employee_id, deleted in [(2, False), (3, False), (4, False), (1, True)]:
                guarded = await queue(employee_id, deleted)
                await notifier.deliver(await notifier.claim())
                assert await admin.fetchval("SELECT state FROM medoed.notifications WHERE spin_id=$1", guarded) == "cancelled"
            assert sent == []
            successful = await queue()
            current = await notifier.claim()
            wrong_owner = {**dict(current), "claim_token": uuid4()}
            await notifier.settle(wrong_owner, "sent", message_id=999)
            assert await admin.fetchval("SELECT state FROM medoed.notifications WHERE spin_id=$1", successful) == "processing"
            await notifier.deliver(current)
            record = await admin.fetchrow("SELECT state,telegram_message_id,attempts FROM medoed.notifications WHERE spin_id=$1", successful)
            assert dict(record) == {"state": "sent", "telegram_message_id": 111, "attempts": 1}
            assert len(sent) == 1
            assert await notifier.claim() is None

            both = worker.Notifier(pool, object(), group_target=worker.GroupTarget(-100123456))
            await both.preflight()
            fresh = await queue(group=True, ready=False)
            for channel in ("dm", "group"):
                assert await both.claim(channel) is None, 'A freshly inserted row must wait five seconds'
            for table in ("medoed.notifications", "medoed.group_notifications"):
                delay = await admin.fetchval(f"SELECT extract(epoch FROM available_at-created_at) FROM {table} WHERE spin_id=$1", fresh)
                assert float(delay) >= 5
                # Advance simulated wall time for the due timestamp only: the creation-age guard still rejects.
                await admin.execute(f"UPDATE {table} SET available_at=now()-interval '1 second' WHERE spin_id=$1", fresh)
            assert await both.claim("dm") is None
            assert await both.claim("group") is None
            for table in ("medoed.notifications", "medoed.group_notifications"):
                await admin.execute(f"UPDATE {table} SET created_at=now()-interval '6 seconds' WHERE spin_id=$1", fresh)
            dm_claim = await both.claim("dm")
            group_claim = await both.claim("group")
            assert dm_claim["spin_id"] == group_claim["spin_id"] == fresh
            await both.deliver(dm_claim, "dm")
            request = SendMessage(chat_id=-100123456, text="Synthetic")
            group_attempts = []

            async def uncertain_group(bot, **kwargs):
                group_attempts.append(kwargs)
                raise TelegramNetworkError(request, "Synthetic ambiguous group send")

            monkeypatch.setattr(worker, "send_system_message", uncertain_group)
            await both.deliver(group_claim, "group")
            assert await admin.fetchval("SELECT state FROM medoed.notifications WHERE spin_id=$1", fresh) == "sent"
            assert await admin.fetchval("SELECT state FROM medoed.group_notifications WHERE spin_id=$1", fresh) == "uncertain"
            for _ in range(3):
                assert await both.process_channel("dm")
                assert await both.process_channel("group")
            assert len(group_attempts) == 1
            assert len(sent) == 2, 'Uncertain group delivery must never resend the successful DM'

            second = await queue(group=True)
            dm_claim = await both.claim("dm")
            group_claim = await both.claim("group")

            async def dm_failed(bot, **kwargs):
                if kwargs["chat_id"] > 0:
                    raise TelegramForbiddenError(SendMessage(chat_id=kwargs["chat_id"], text="Synthetic"), "Synthetic")
                return SimpleNamespace(message=SimpleNamespace(message_id=222))

            monkeypatch.setattr(worker, "send_system_message", dm_failed)
            await both.deliver(dm_claim, "dm")
            await both.deliver(group_claim, "group")
            assert await admin.fetchval("SELECT state FROM medoed.notifications WHERE spin_id=$1", second) == "failed"
            assert await admin.fetchval("SELECT state FROM medoed.group_notifications WHERE spin_id=$1", second) == "sent"

            third = await queue(group=True)
            dm_claim = await both.claim("dm")
            group_claim = await both.claim("group")
            monkeypatch.setattr(worker, "send_system_message", send)
            await both.deliver(dm_claim, "dm")

            async def rate_limited_group(bot, **kwargs):
                raise TelegramRetryAfter(request, "Synthetic", retry_after=10)

            monkeypatch.setattr(worker, "send_system_message", rate_limited_group)
            await both.deliver(group_claim, "group")
            pending = await admin.fetchrow("""SELECT state,available_at>clock_timestamp() AS delayed
                FROM medoed.group_notifications WHERE spin_id=$1""", third)
            assert dict(pending) == {"state": "pending", "delayed": True}
            assert await both.claim("dm") is None
            assert await both.claim("group") is None
            await admin.execute("UPDATE medoed.group_notifications SET available_at=now()-interval '1 second' WHERE spin_id=$1", third)
            retried = await both.claim("group")
            assert retried["attempts"] == 2
            monkeypatch.setattr(worker, "send_system_message", send)
            await both.deliver(retried, "group")
            assert await admin.fetchval("SELECT attempts FROM medoed.notifications WHERE spin_id=$1", third) == 1

            permanent_failure = await queue(group=True)
            dm_claim = await both.claim("dm")
            group_claim = await both.claim("group")
            await both.deliver(dm_claim, "dm")

            async def forbidden_group(bot, **kwargs):
                raise TelegramForbiddenError(request, "Synthetic group permissions failure")

            monkeypatch.setattr(worker, "send_system_message", forbidden_group)
            await both.deliver(group_claim, "group")
            assert await admin.fetchval("SELECT state FROM medoed.group_notifications WHERE spin_id=$1", permanent_failure) == "failed"
            assert await both.claim("group") is None
            assert await both.claim("dm") is None
            assert await admin.fetchval("SELECT attempts FROM medoed.notifications WHERE spin_id=$1", permanent_failure) == 1
            monkeypatch.setattr(worker, "send_system_message", send)

            interrupted = await queue(group=True)
            await both.deliver(await both.claim("dm"), "dm")
            await both.claim("group")
            await admin.execute("""UPDATE medoed.group_notifications SET claimed_at=now()-interval '121 seconds'
                WHERE spin_id=$1""", interrupted)
            await both.expire_interrupted("group")
            assert await admin.fetchval("SELECT state FROM medoed.group_notifications WHERE spin_id=$1", interrupted) == "uncertain"
            assert await both.claim("group") is None
            assert await both.claim("dm") is None

            # A no-Telegram employee is valid for a group announcement and remains invalid for a DM.
            no_tg = await queue(employee_id=3, group=True)
            await both.deliver(await both.claim("dm"), "dm")
            await both.deliver(await both.claim("group"), "group")
            assert await admin.fetchval("SELECT state FROM medoed.notifications WHERE spin_id=$1", no_tg) == "cancelled"
            assert await admin.fetchval("SELECT state FROM medoed.group_notifications WHERE spin_id=$1", no_tg) == "sent"

            # Deleting a spin cancels each independent queue and never invokes Telegram.
            removed = await queue(group=True, deleted=True)
            before = len(sent)
            await both.deliver(await both.claim("dm"), "dm")
            await both.deliver(await both.claim("group"), "group")
            assert len(sent) == before
            for table in ("medoed.notifications", "medoed.group_notifications"):
                assert await admin.fetchval(f"SELECT state FROM {table} WHERE spin_id=$1", removed) == "cancelled"
        finally:
            if pool is not None:
                await pool.close()
            if admin is not None:
                await admin.close()

    try:
        docker("run", "--rm", "-d", "--name", container, "-e", f"POSTGRES_PASSWORD={password}",
               "-p", "127.0.0.1::5432", "postgres:16-alpine")
        started = True
        asyncio.run(scenario())
    finally:
        if started:
            docker("rm", "-f", container)
