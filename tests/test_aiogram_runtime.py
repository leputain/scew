from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

from core.screw_core.services.health_status import (
    DbHealth,
    ErrorAggregate,
    HealthSnapshot,
    HeartbeatHealth,
    PollHealth,
    ReportsHealth,
    SchedulerHealth,
    SchedulerJobHealth,
    TelegramHealth,
    format_health_status,
    is_health_ok,
)


def test_runtime_sources_and_lock_are_ptb_free() -> None:
    source_roots = [Path("apps/attendance_bot/app"), Path("core/screw_core")]
    forbidden = ("from telegram", "import telegram", "telegram.ext", "PTBCompat")
    matches = []

    for root in source_roots:
        for path in root.rglob("*.py"):
            source = path.read_text(encoding="utf-8")
            for marker in forbidden:
                if marker in source:
                    matches.append(f"{path}:{marker}")

    lock = Path("requirements.lock.txt").read_text(encoding="utf-8").lower()
    direct = Path("requirements.txt").read_text(encoding="utf-8").lower()

    assert matches == []
    assert "python-telegram-bot" not in lock
    assert "python-telegram-bot" not in direct
    assert "aiogram==3.30.0" in lock
    assert "aiogram==3.30.0" in direct


def test_health_status_contains_operational_data_without_identifiers(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SCREW_APP_VERSION", "1.2")
    monkeypatch.setenv("SCREW_VCS_REF", "abcdef0123456789")
    now = datetime(2026, 8, 11, 10, 30, tzinfo=timezone.utc)
    snapshot = HealthSnapshot(
        db=DbHealth(ok=True),
        scheduler=SchedulerHealth(
            enabled=True,
            running=True,
            job_ids=["heartbeat", "poll_auto_send"],
            jobs=[SchedulerJobHealth("heartbeat", now)],
        ),
        reports=ReportsHealth("SENT", date(2026, 8, 10), "SENT", "2026-07"),
        heartbeat=HeartbeatHealth(fresh=True, age_seconds=12),
        telegram=TelegramHealth(ok=False, checked_at=now, error="TelegramNetworkError"),
        poll=PollHealth(date(2026, 8, 11), "closed", now, 42),
        recent_errors=[ErrorAggregate("REPORT_DAILY_FAILED", 2, now)],
    )

    text = format_health_status(snapshot)

    assert "Runtime</b>: aiogram · 1.2 · abcdef012345" in text
    assert "Heartbeat</b>: актуален, 12 сек." in text
    assert "Последний опрос</b>: 11.08.2026, closed, ответов: 42" in text
    assert "REPORT_DAILY_FAILED: 2" in text
    assert "<blockquote expandable>" in text
    assert "telegram_id" not in text
    assert "chat_id" not in text
    assert is_health_ok(snapshot) is True


def test_release_metadata_and_smoke_script_are_wired() -> None:
    version = Path("VERSION").read_text(encoding="utf-8").strip()
    dockerfile = Path("Dockerfile").read_text(encoding="utf-8")
    compose = Path("docker-compose.yml").read_text(encoding="utf-8")
    deploy = Path("scripts/deploy_manual.sh").read_text(encoding="utf-8")
    smoke = Path("scripts/post_deploy_smoke.sh").read_text(encoding="utf-8")

    assert version == "1.2"
    assert "org.opencontainers.image.version" in dockerfile
    assert "org.opencontainers.image.revision" in dockerfile
    assert "APP_VERSION: ${APP_VERSION:-1.2}" in compose
    assert "bash scripts/post_deploy_smoke.sh" in deploy
    assert "find_spec(\"telegram\") is None" in smoke
    assert "get_webhook_info" in smoke
    assert "cat .env" not in smoke


@pytest.mark.asyncio
async def test_telegram_diagnostic_state_is_sanitized(monkeypatch: pytest.MonkeyPatch) -> None:
    from apps.attendance_bot.app import scheduler

    class FailingBot:
        async def get_me(self):
            raise RuntimeError("token=secret chat_id=123")

    monkeypatch.setattr(scheduler, "_bot_instance", FailingBot())

    await scheduler._job_telegram_health()
    state = scheduler.get_telegram_health_status()

    assert state["ok"] is False
    assert state["error"] == "RuntimeError"
    assert "secret" not in str(state)
    assert isinstance(state["checked_at"], datetime)


@pytest.mark.asyncio
async def test_latest_poll_summary_aggregates_responses(monkeypatch: pytest.MonkeyPatch) -> None:
    from core.screw_core.repos import polls_repo

    captured: dict[str, str] = {}

    class Connection:
        async def fetchrow(self, query: str):
            captured["query"] = query
            return {
                "id": 7,
                "poll_date": date(2026, 8, 11),
                "published_at": datetime(2026, 8, 11, tzinfo=timezone.utc),
                "closed_at": None,
                "status": "open",
                "response_count": 3,
            }

    @asynccontextmanager
    async def fake_connection():
        yield Connection()

    monkeypatch.setattr(polls_repo, "acquire_connection", fake_connection)

    result = await polls_repo.PollsRepo.get_latest_published_summary()

    assert result and result["response_count"] == 3
    assert "COUNT(DISTINCT am.user_id)" in captured["query"]
    assert "ORDER BY pr.published_at DESC" in captured["query"]


@pytest.mark.asyncio
async def test_recent_error_query_returns_aggregates_only(monkeypatch: pytest.MonkeyPatch) -> None:
    from core.screw_core.repos import events_repo

    captured: dict[str, object] = {}

    class Connection:
        async def fetch(self, query: str, hours: int):
            captured["query"] = query
            captured["hours"] = hours
            return [{"event_type": "REPORT_DAILY_FAILED", "error_count": 2, "last_error_at": None}]

    @asynccontextmanager
    async def fake_connection():
        yield Connection()

    monkeypatch.setattr(events_repo, "acquire_connection", fake_connection)

    result = await events_repo.EventsRepo.get_recent_notify_error_summary(hours=24)

    assert result[0]["error_count"] == 2
    assert captured["hours"] == 24
    query = str(captured["query"])
    assert "GROUP BY event_type" in query
    assert "payload" not in query
    assert "user_id" not in query
