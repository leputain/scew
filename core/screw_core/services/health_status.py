"""Operational health snapshot for the private admin screen."""

from __future__ import annotations

import html
import os
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any

from core.screw_core.db.tx import acquire_connection
from core.screw_core.models.enums import EventType
from core.screw_core.repos.events_repo import EventsRepo
from core.screw_core.repos.polls_repo import PollsRepo
from core.screw_core.utils.timezone import get_tzinfo


@dataclass
class DbHealth:
    ok: bool
    error: str | None = None


@dataclass
class SchedulerJobHealth:
    job_id: str
    next_run_at: datetime | None = None


@dataclass
class SchedulerHealth:
    enabled: bool
    job_ids: list[str]
    running: bool = False
    jobs: list[SchedulerJobHealth] = field(default_factory=list)


@dataclass
class HeartbeatHealth:
    fresh: bool
    age_seconds: int | None = None


@dataclass
class TelegramHealth:
    ok: bool | None
    checked_at: datetime | None = None
    pending_updates: int | None = None
    error: str | None = None


@dataclass
class PollHealth:
    poll_date: date | None = None
    status: str | None = None
    published_at: datetime | None = None
    response_count: int = 0


@dataclass
class ErrorAggregate:
    event_type: str
    count: int
    last_error_at: datetime | None = None


@dataclass
class ReportsHealth:
    daily_status: str
    daily_date: date | None
    monthly_status: str
    monthly_period: str | None


@dataclass
class HealthSnapshot:
    db: DbHealth
    scheduler: SchedulerHealth
    reports: ReportsHealth
    heartbeat: HeartbeatHealth
    telegram: TelegramHealth
    poll: PollHealth
    recent_errors: list[ErrorAggregate]


async def build_health_snapshot(
    scheduler_status: SchedulerHealth,
    *,
    heartbeat_status: dict[str, object] | None = None,
    telegram_status: dict[str, object] | None = None,
) -> HealthSnapshot:
    """Build a best-effort snapshot; diagnostics must not break the admin router."""
    db = await check_db_health()

    try:
        reports = await get_reports_health()
    except Exception:
        reports = ReportsHealth("UNKNOWN", None, "UNKNOWN", None)

    try:
        poll = _parse_poll(await PollsRepo.get_latest_published_summary())
    except Exception:
        poll = PollHealth()

    try:
        recent_errors = [
            ErrorAggregate(
                event_type=str(item.get("event_type") or "UNKNOWN"),
                count=int(item.get("error_count") or 0),
                last_error_at=item.get("last_error_at"),
            )
            for item in await EventsRepo.get_recent_notify_error_summary(hours=24)
        ]
    except Exception:
        recent_errors = []

    heartbeat_status = heartbeat_status or {}
    heartbeat = HeartbeatHealth(
        fresh=bool(heartbeat_status.get("fresh")),
        age_seconds=_optional_int(heartbeat_status.get("age_seconds")),
    )

    telegram_status = telegram_status or {}
    telegram = TelegramHealth(
        ok=_optional_bool(telegram_status.get("ok")),
        checked_at=_optional_datetime(telegram_status.get("checked_at")),
        pending_updates=_optional_int(telegram_status.get("pending_updates")),
        error=str(telegram_status["error"]) if telegram_status.get("error") else None,
    )

    return HealthSnapshot(db, scheduler_status, reports, heartbeat, telegram, poll, recent_errors)


async def check_db_health() -> DbHealth:
    try:
        async with acquire_connection() as conn:
            value = await conn.fetchval("SELECT 1")
            return DbHealth(ok=value == 1, error=None if value == 1 else "unexpected result")
    except Exception as exc:
        return DbHealth(ok=False, error=type(exc).__name__)


async def get_reports_health() -> ReportsHealth:
    daily_event = await EventsRepo.get_latest_event_by_types(
        [EventType.REPORT_DAILY_SENT, EventType.REPORT_DAILY_FAILED],
    )
    monthly_event = await EventsRepo.get_latest_event_by_types(
        [EventType.REPORT_MONTHLY_SENT, EventType.REPORT_MONTHLY_FAILED],
    )
    daily_status, daily_date = _parse_report_event(daily_event)
    monthly_status, monthly_period = _parse_monthly_event(monthly_event)
    return ReportsHealth(daily_status, daily_date, monthly_status, monthly_period)


def format_health_status(snapshot: HealthSnapshot) -> str:
    """Format a compact HTML diagnostic without user or Telegram identifiers."""
    heartbeat_age = "нет данных" if snapshot.heartbeat.age_seconds is None else f"{snapshot.heartbeat.age_seconds} сек."
    heartbeat_state = "актуален" if snapshot.heartbeat.fresh else "просрочен"
    telegram_state = _format_telegram(snapshot.telegram)
    scheduler_state = "работает" if snapshot.scheduler.running else ("не запущен" if snapshot.scheduler.enabled else "выключен")
    poll_state = _format_poll(snapshot.poll)
    reports = _format_reports(snapshot.reports)

    lines = [
        "🩺 <b>Состояние attendance_bot</b>",
        "",
        f"<b>Runtime</b>: {_format_runtime_identity()}",
        f"<b>БД</b>: {'доступна' if snapshot.db.ok else 'ошибка ' + html.escape(snapshot.db.error or 'unknown')}",
        f"<b>Heartbeat</b>: {heartbeat_state}, {heartbeat_age}",
        f"<b>Telegram API</b>: {telegram_state}",
        f"<b>Scheduler</b>: {scheduler_state}, задач: {len(snapshot.scheduler.job_ids)}",
        "",
        f"<b>Последний опрос</b>: {poll_state}",
        f"<b>Отчёты</b>: {reports[0]}; {reports[1]}",
        f"<b>Ошибки доставки за 24 ч.</b>: {_format_errors(snapshot.recent_errors)}",
    ]

    if snapshot.scheduler.jobs:
        job_lines = [
            f"{html.escape(job.job_id)} — {_format_job_time(job.next_run_at)}"
            for job in snapshot.scheduler.jobs
        ]
        lines.extend(["", "<b>Задачи scheduler</b>", f"<blockquote expandable>{chr(10).join(job_lines)}</blockquote>"])

    return "\n".join(lines)


def is_health_ok(snapshot: HealthSnapshot) -> bool:
    """Telegram availability is diagnostic and does not affect process liveness."""
    return snapshot.db.ok and snapshot.heartbeat.fresh


def _parse_poll(value: dict[str, Any] | None) -> PollHealth:
    if not value:
        return PollHealth()
    return PollHealth(
        poll_date=value.get("poll_date"),
        status=str(value.get("status") or "UNKNOWN"),
        published_at=value.get("published_at"),
        response_count=int(value.get("response_count") or 0),
    )


def _format_poll(poll: PollHealth) -> str:
    if poll.poll_date is None:
        return "нет данных"
    status = html.escape(poll.status or "UNKNOWN")
    return f"{_format_date(poll.poll_date)}, {status}, ответов: {poll.response_count}"


def _format_telegram(status: TelegramHealth) -> str:
    if status.ok is None:
        return "ещё не проверялся"
    checked = _format_datetime(status.checked_at) if status.checked_at else "время неизвестно"
    pending = f", pending updates: {status.pending_updates}" if status.pending_updates is not None else ""
    if status.ok:
        return f"доступен ({checked}{pending})"
    error = html.escape(status.error or "unknown")
    return f"недоступен ({checked}, {error}{pending})"


def _format_errors(errors: list[ErrorAggregate]) -> str:
    if not errors:
        return "0"
    return ", ".join(f"{html.escape(item.event_type)}: {item.count}" for item in errors[:6])


def _format_reports(reports: ReportsHealth) -> tuple[str, str]:
    daily_date = _format_date(reports.daily_date) if reports.daily_date else "UNKNOWN"
    return (
        f"daily {html.escape(reports.daily_status)} {daily_date}",
        f"monthly {html.escape(reports.monthly_status)} {html.escape(reports.monthly_period or 'UNKNOWN')}",
    )


def _format_job_time(value: datetime | None) -> str:
    return _format_datetime(value) if value else "приостановлена"


def _format_runtime_identity() -> str:
    version = html.escape(os.getenv("SCREW_APP_VERSION", "dev"))
    revision = os.getenv("SCREW_VCS_REF", "unversioned").strip()
    if revision and revision != "unversioned":
        return f"aiogram · {version} · {html.escape(revision[:12])}"
    return f"aiogram · {version}"


def _format_datetime(value: datetime) -> str:
    if value.tzinfo is not None:
        value = value.astimezone(get_tzinfo("Europe/Moscow"))
    return value.strftime("%d.%m.%Y %H:%M")


def _format_date(value: date) -> str:
    return value.strftime("%d.%m.%Y")


def _optional_int(value: object) -> int | None:
    return int(value) if isinstance(value, int) else None


def _optional_bool(value: object) -> bool | None:
    return value if isinstance(value, bool) else None


def _optional_datetime(value: object) -> datetime | None:
    return value if isinstance(value, datetime) else None


def _parse_report_event(event: dict | None) -> tuple[str, date | None]:
    if not event:
        return "UNKNOWN", None
    event_type = event.get("event_type")
    status = "FAILED" if event_type == EventType.REPORT_DAILY_FAILED.value else "SENT"
    return status, event.get("event_date")


def _parse_monthly_event(event: dict | None) -> tuple[str, str | None]:
    if not event:
        return "UNKNOWN", None
    event_type = event.get("event_type")
    status = "FAILED" if event_type == EventType.REPORT_MONTHLY_FAILED.value else "SENT"
    payload = event.get("payload") or {}
    year = payload.get("year")
    month = payload.get("month")
    if year and month:
        return status, f"{int(year):04d}-{int(month):02d}"
    event_date = event.get("event_date")
    if isinstance(event_date, date):
        return status, f"{event_date.year:04d}-{event_date.month:02d}"
    return status, None
