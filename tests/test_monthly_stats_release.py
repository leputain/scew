from __future__ import annotations

import asyncio
from datetime import date, datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest


@pytest.mark.asyncio
async def test_monthly_report_uses_expected_ozko_users_and_escapes_names(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from apps.attendance_bot.app.jobs import monthly_stats

    async def fake_load_period(_year: int, _month: int, department_id: int):
        assert department_id == 1
        return {
            "polls": [{"id": 10}, {"id": 11}],
            "expected": [
                {"poll_run_id": 10, "user_id": 1, "full_name": "<Иванов & Иван>"},
                {"poll_run_id": 11, "user_id": 1, "full_name": "<Иванов & Иван>"},
                {"poll_run_id": 10, "user_id": 2, "full_name": "Петров Петр Петрович"},
            ],
            "responses": [
                {"poll_run_id": 10, "user_id": 1, "full_name": "ignored", "response_seconds": 0.987},
                {"poll_run_id": 11, "user_id": 1, "full_name": "ignored", "response_seconds": 2.0},
                {"poll_run_id": 10, "user_id": 2, "full_name": "ignored", "response_seconds": 1.2},
                {"poll_run_id": 10, "user_id": 99, "full_name": "ОСБ Быстрее Всех", "response_seconds": 0.1},
            ],
            "approximate_poll_ids": [10],
        }

    monkeypatch.setattr(monthly_stats.MonthlyStatsRepo, "load_period", fake_load_period)
    report = await monthly_stats.build_monthly_stats_report(2026, 7)

    assert report is not None
    assert report.department_code == "ОЗКО"
    assert report.expected_count == 3
    assert report.response_count == 3
    assert report.participation_pct == 100
    assert report.perfect_count == 2
    assert report.is_approximate is True
    assert "\n\n📊" in report.html
    assert "<blockquote expandable>" in report.html
    assert "Ответили на все ожидаемые опросы" in report.html
    assert "100% явка" not in report.html
    assert "ОСБ Быстрее" not in report.html
    assert "0.987 сек." in report.html
    assert "&lt;Иванов &amp;" in report.html


@pytest.mark.asyncio
async def test_monthly_report_returns_none_without_polls(monkeypatch: pytest.MonkeyPatch) -> None:
    from apps.attendance_bot.app.jobs import monthly_stats

    async def fake_load_period(*_args):
        return {"polls": [], "expected": [], "responses": [], "approximate_poll_ids": []}

    monkeypatch.setattr(monthly_stats.MonthlyStatsRepo, "load_period", fake_load_period)
    assert await monthly_stats.build_monthly_stats_report(2026, 7) is None


def test_monthly_report_truncates_expandable_list_to_telegram_limit() -> None:
    from apps.attendance_bot.app.jobs.monthly_stats import MAX_REPORT_LENGTH, _build_report_html

    names = [f"Оченьдлиннаяфамилия{i} Имя Отчество" for i in range(500)]
    rendered = _build_report_html(
        year=2026,
        month=7,
        poll_count=23,
        expected_count=1426,
        response_count=923,
        participation_pct=65,
        fastest=[],
        first_top=[],
        perfect_names=names,
    )

    assert len(rendered) <= MAX_REPORT_LENGTH
    assert "… и ещё" in rendered
    assert rendered.endswith("</blockquote>")


@pytest.mark.asyncio
async def test_html_delivery_mode_never_attempts_rich_message(monkeypatch: pytest.MonkeyPatch) -> None:
    from core.screw_core.services.telegram_delivery import send_system_message

    monkeypatch.setenv("TELEGRAM_RICH_MESSAGES_ENABLED", "1")

    class FakeBot:
        def __init__(self) -> None:
            self.rich_calls = 0
            self.html_calls: list[dict] = []

        async def send_rich_message(self, **_kwargs):
            self.rich_calls += 1
            raise AssertionError("rich delivery must not be used")

        async def send_message(self, **kwargs):
            self.html_calls.append(kwargs)
            return SimpleNamespace(message_id=1)

    bot = FakeBot()
    result = await send_system_message(
        bot,
        chat_id=-1001,
        html_text="<b>Заголовок</b>\n\nТекст",
        delivery_mode="html",
    )

    assert result.method == "html"
    assert bot.rich_calls == 0
    assert bot.html_calls[0]["text"] == "<b>Заголовок</b>\n\nТекст"


def test_monthly_stats_menu_has_twelve_months_on_two_pages() -> None:
    from apps.attendance_bot.app.ui.admin_panel_views import (
        build_monthly_stats_months_keyboard,
        get_monthly_stats_months,
    )

    months = get_monthly_stats_months(today=date(2026, 8, 11))
    assert months == [
        (2026, 8),
        (2026, 7),
        (2026, 6),
        (2026, 5),
        (2026, 4),
        (2026, 3),
        (2026, 2),
        (2026, 1),
        (2025, 12),
        (2025, 11),
        (2025, 10),
        (2025, 9),
    ]
    first = build_monthly_stats_months_keyboard(0, today=date(2026, 8, 11))
    second = build_monthly_stats_months_keyboard(1, today=date(2026, 8, 11))
    first_callbacks = [button.callback_data for row in first.inline_keyboard for button in row]
    second_callbacks = [button.callback_data for row in second.inline_keyboard for button in row]
    assert "admin:stats:v:2026-08:0" in first_callbacks
    assert "admin:stats:v:2026-03:0" in first_callbacks
    assert "admin:stats:v:2026-02:1" in second_callbacks
    assert "admin:stats:v:2025-09:1" in second_callbacks


@pytest.mark.asyncio
async def test_automatic_monthly_stats_dedup_skips_existing_event(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from apps.attendance_bot.app.jobs import monthly_stats

    async def fake_get(_key: str):
        return {"id": 1}

    monkeypatch.setattr(monthly_stats, "get_violations_ozko_chat_id", lambda: -1001)
    monkeypatch.setattr(monthly_stats.EventsRepo, "get_by_dedup_key", fake_get)

    class FakeBot:
        async def send_message(self, **_kwargs):
            raise AssertionError("duplicate automatic report must not be sent")

    success, status, chat_id = await monthly_stats.send_monthly_stats_to_violations_chat(
        FakeBot(),
        2026,
        7,
        automatic=True,
    )
    assert success is True
    assert "уже опубликовано" in status
    assert chat_id == -1001


@pytest.mark.asyncio
async def test_poll_snapshot_replaced_atomically_on_recreate(monkeypatch: pytest.MonkeyPatch) -> None:
    from core.screw_core.repos import polls_repo

    queries: list[tuple[str, tuple[object, ...]]] = []

    class FakeConnection:
        async def fetchval(self, query: str, *args):
            queries.append((query, args))
            if "SELECT id" in query:
                return 41
            return 41

        async def execute(self, query: str, *args):
            queries.append((query, args))
            return "DELETE 2" if "attendance_marks" in query else "OK"

    class FakeTransaction:
        async def __aenter__(self):
            return FakeConnection()

        async def __aexit__(self, *_args):
            return False

    monkeypatch.setattr(polls_repo, "transaction", lambda: FakeTransaction())
    poll_id, deleted = await polls_repo.PollsRepo.create_poll_with_expected_users(
        date(2026, 8, 11),
        -1001,
    )

    assert poll_id == 41
    assert deleted == 2
    upsert_args = next(args for query, args in queries if "INSERT INTO screw.poll_runs" in query)
    assert upsert_args[4] is None
    assert isinstance(upsert_args[6], datetime)
    assert any("DELETE FROM screw.poll_expected_users" in query for query, _ in queries)
    assert any("INSERT INTO screw.poll_expected_users" in query for query, _ in queries)


@pytest.mark.asyncio
async def test_closed_poll_snapshot_is_not_mutated_again(monkeypatch: pytest.MonkeyPatch) -> None:
    from core.screw_core.repos import polls_repo

    class FakeConnection:
        async def fetchval(self, _query: str, *_args):
            return "closed"

        async def execute(self, *_args, **_kwargs):
            raise AssertionError("closed poll must not be updated")

    class FakeTransaction:
        async def __aenter__(self):
            return FakeConnection()

        async def __aexit__(self, *_args):
            return False

    monkeypatch.setattr(polls_repo, "transaction", lambda: FakeTransaction())
    await polls_repo.PollsRepo.close_poll(41)


@pytest.mark.asyncio
async def test_security_policy_blocks_terminated_and_db_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    from aiogram.types import Update

    from apps.attendance_bot.app.aiogram_app.middlewares import AiogramSecurityPolicyMiddleware
    from core.screw_core.repos import users_repo

    event = Update.model_validate(
        {
            "update_id": 50,
            "message": {
                "message_id": 1,
                "date": int(datetime.now(timezone.utc).timestamp()),
                "chat": {"id": 42, "type": "private"},
                "from": {"id": 42, "is_bot": False, "first_name": "User"},
                "text": "/admin",
            },
        }
    )
    handler_calls = 0

    async def handler(_event, _data):
        nonlocal handler_calls
        handler_calls += 1

    async def terminated(_telegram_id: int):
        return {"id": 7, "full_name": "Terminated", "status": "terminated"}

    async def fake_event(*_args, **_kwargs):
        return 1

    monkeypatch.setattr(users_repo.UsersRepo, "get_by_telegram_id", terminated)
    monkeypatch.setattr(
        "apps.attendance_bot.app.aiogram_app.middlewares.EventsRepo.create_event",
        fake_event,
    )
    assert await AiogramSecurityPolicyMiddleware()(handler, event, {}) is None

    async def timeout(_telegram_id: int):
        raise asyncio.TimeoutError

    monkeypatch.setattr(users_repo.UsersRepo, "get_by_telegram_id", timeout)
    assert await AiogramSecurityPolicyMiddleware()(handler, event, {}) is None
    assert handler_calls == 0


@pytest.mark.asyncio
async def test_reaction_update_uses_group_allowlist_before_user_lookup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from aiogram.types import Update

    from apps.attendance_bot.app.aiogram_app.middlewares import AiogramSecurityPolicyMiddleware
    from core.screw_core.repos import users_repo

    monkeypatch.setenv("FLUD_CHAT_ID", "-1001")

    async def unexpected_lookup(_telegram_id: int):
        raise AssertionError("blocked reaction chat must not query the user")

    monkeypatch.setattr(users_repo.UsersRepo, "get_by_telegram_id", unexpected_lookup)
    event = Update.model_validate(
        {
            "update_id": 51,
            "message_reaction": {
                "chat": {"id": -1002, "type": "supergroup", "title": "Blocked"},
                "message_id": 10,
                "date": int(datetime.now(timezone.utc).timestamp()),
                "old_reaction": [],
                "new_reaction": [{"type": "emoji", "emoji": "👍"}],
                "user": {"id": 42, "is_bot": False, "first_name": "User"},
            },
        }
    )

    async def handler(_event, _data):
        raise AssertionError("blocked reaction chat must not reach handler")

    assert await AiogramSecurityPolicyMiddleware()(handler, event, {}) is None


@pytest.mark.asyncio
async def test_liveness_heartbeat_does_not_call_telegram_api(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from apps.attendance_bot.app import scheduler

    class ExplodingBot:
        async def get_me(self):
            raise AssertionError("liveness must not call Telegram")

    heartbeat = tmp_path / "heartbeat"
    monkeypatch.setattr(scheduler, "HEARTBEAT_FILE", heartbeat)
    monkeypatch.setattr(scheduler, "_application_instance", None)
    monkeypatch.setattr(scheduler, "_bot_instance", ExplodingBot())

    await scheduler._job_heartbeat()
    assert heartbeat.exists()


def test_poll_cant_be_stopped_is_expected_best_effort_result() -> None:
    from apps.attendance_bot.app.services.poll_runtime import _is_poll_already_closed_error

    assert _is_poll_already_closed_error(RuntimeError("Bad Request: poll can't be stopped")) is True


def test_poll_expected_users_migration_contract() -> None:
    migration = Path("infra/postgres/migrations/005_poll_expected_users.sql").read_text(encoding="utf-8")
    assert "CREATE TABLE IF NOT EXISTS screw.poll_expected_users" in migration
    assert "PRIMARY KEY (poll_run_id, user_id)" in migration
    assert "expected_users_snapshot_at" in migration
    assert "MONTHLY_STATS_SENT" in migration
