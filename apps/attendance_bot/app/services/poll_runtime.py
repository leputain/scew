"""Runtime-neutral helpers for attendance Telegram polls.

Telegram runtime details stay in the aiogram router and payload adapter.
"""

from __future__ import annotations

import asyncio
import html
import json
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Any

from apps.attendance_bot.app.config import (
    get_department_lists_chat_id,
    get_poll_summary_chat_id,
    get_poll_duration_seconds,
    get_poll_reminder_before_end_minutes,
    get_poll_timezone,
)
from apps.attendance_bot.app.utils.poll_payload import PollPayload, build_poll_payload
from core.screw_core.repos.attendance_repo import AttendanceRepo
from core.screw_core.models.enums import AttendanceChoice, UserRole
from core.screw_core.repos.polls_repo import PollOptionSnapshot, PollsRepo
from core.screw_core.repos.day_status_repo import DayStatusRepo
from core.screw_core.repos.users_repo import UsersRepo
from core.screw_core.services.permissions import OZKO_DEPARTMENT_ID, OSB_DEPARTMENT_ID
from core.screw_core.services.polls import PollError, PollResult, PollsService
from core.screw_core.services.telegram_report_delivery import send_tracked_report
from core.screw_core.utils.logging import get_logger
from core.screw_core.utils.timezone import get_tzinfo

logger = get_logger(__name__)

_POLL_REMINDER_DM_ENABLED = True

# Global scheduler reference, set from runtime lifecycle.
_scheduler = None


def set_poll_scheduler(scheduler) -> None:
    """Set scheduler instance for poll runtime helpers."""
    global _scheduler
    _scheduler = scheduler


def get_poll_scheduler():
    """Get current scheduler, or None if runtime has not started it."""
    return _scheduler


_DEFAULT_POLL_OPTIONS = ["МФК «Эволюция»", "ЦСКА", "Удалёнка"]
_DEFAULT_OPTION_TO_CHOICE: dict[int, AttendanceChoice] = {
    0: AttendanceChoice.EVOLUTION,
    1: AttendanceChoice.CSKA,
    2: AttendanceChoice.REMOTE,
}

# Backward-compatible module-level constants used by legacy handlers/tests.
POLL_OPTIONS = list(_DEFAULT_POLL_OPTIONS)
OPTION_TO_CHOICE = dict(_DEFAULT_OPTION_TO_CHOICE)
_POLL_PERSISTENT_TO_CHOICE: dict[str, dict[str, AttendanceChoice]] = {}


@dataclass(frozen=True)
class LoadedPollConfig:
    payload: PollPayload
    option_to_choice: dict[int, AttendanceChoice]
    persistent_to_choice: dict[str, AttendanceChoice]
    duration_seconds: int
    reminder_minutes: int | None


def _default_raw_poll_options() -> list[dict[str, str]]:
    return [
        {"key": choice.value, "label": label}
        for label, choice in zip(_DEFAULT_POLL_OPTIONS, _DEFAULT_OPTION_TO_CHOICE.values(), strict=True)
    ]


async def _load_poll_config() -> LoadedPollConfig:
    """Load poll config from DB with env/default fallback."""
    from core.screw_core.repos.poll_config_repo import PollConfigRepo

    try:
        config = await PollConfigRepo.get_config()
    except Exception:
        duration = get_poll_duration_seconds()
        return LoadedPollConfig(
            payload=build_poll_payload(_default_raw_poll_options(), {"poll_duration_seconds": duration}),
            option_to_choice=dict(_DEFAULT_OPTION_TO_CHOICE),
            persistent_to_choice={},
            duration_seconds=duration,
            reminder_minutes=get_poll_reminder_before_end_minutes(),
        )

    raw_options = config.get("poll_options", [])
    if not isinstance(raw_options, list):
        raw_options = []

    payload_options: list[dict[str, Any]] = []
    mapping: dict[int, AttendanceChoice] = {}
    persistent_mapping: dict[str, AttendanceChoice] = {}
    for opt in raw_options:
        if not isinstance(opt, dict):
            continue
        key = str(opt.get("key", "")).strip()
        label = str(opt.get("label", key)).strip()
        if not label:
            continue

        option_idx = len(payload_options)
        payload_options.append(opt | {"key": key, "label": label})
        try:
            choice = AttendanceChoice(key)
        except ValueError:
            continue

        mapping[option_idx] = choice
        persistent_id = str(opt.get("persistent_id") or "").strip()
        if persistent_id:
            persistent_mapping[persistent_id] = choice

    if not payload_options:
        payload_options = _default_raw_poll_options()
        mapping = dict(_DEFAULT_OPTION_TO_CHOICE)
        persistent_mapping = {}

    duration = config.get("poll_duration_seconds") or get_poll_duration_seconds()
    reminder = config.get("reminder_before_end_minutes")
    if reminder == 0:
        reminder = None

    return LoadedPollConfig(
        payload=build_poll_payload(payload_options, config),
        option_to_choice=mapping,
        persistent_to_choice=persistent_mapping,
        duration_seconds=int(duration),
        reminder_minutes=reminder,
    )


def _get_extra_field(obj, field_name: str):
    if hasattr(obj, field_name):
        return getattr(obj, field_name)
    api_kwargs = getattr(obj, "api_kwargs", None) or {}
    return api_kwargs.get(field_name)


def _remember_poll_option_mapping(
    telegram_poll_id: str,
    poll_options,
    option_to_choice: dict[int, AttendanceChoice],
    persistent_to_choice: dict[str, AttendanceChoice] | None = None,
) -> None:
    mapping = dict(persistent_to_choice or {})
    for idx, option in enumerate(poll_options or []):
        persistent_id = _get_extra_field(option, "persistent_id")
        if persistent_id and idx in option_to_choice:
            mapping[str(persistent_id)] = option_to_choice[idx]

    if mapping:
        _POLL_PERSISTENT_TO_CHOICE[telegram_poll_id] = mapping


def poll_option_snapshot(poll_options, config: LoadedPollConfig) -> list[PollOptionSnapshot]:
    return [
        {
            "index": index,
            "persistent_id": _get_extra_field(poll_options[index], "persistent_id"),
            "choice": choice.value,
        }
        for index, choice in config.option_to_choice.items()
        if index < len(poll_options)
    ]


async def register_published_poll(
    bot: Any, message: Any, *, poll_date: date, chat_id: int, closes_at: datetime, config: LoadedPollConfig,
) -> PollResult:
    try:
        result = await PollsService.create_daily_poll(
            poll_date=poll_date,
            chat_id=chat_id,
            message_id=message.message_id,
            telegram_poll_id=message.poll.id,
            closes_at=closes_at,
            option_snapshot=poll_option_snapshot(message.poll.options, config),
        )
    except Exception as exc:
        logger.error("poll publication could not be saved (%s)", type(exc).__name__)
        # The commit may have reached PostgreSQL even when the client did not receive its reply.
        try:
            persisted = await PollsService.get_poll_by_telegram_id(message.poll.id)
        except Exception:
            return PollResult(success=False, error=PollError.POLL_PERSIST_FAILED)
        if persisted:
            return PollResult(success=True, poll_id=persisted["id"])
        result = PollResult(success=False, error=PollError.POLL_PERSIST_FAILED)

    if not result.success:
        await _stop_telegram_poll_safe(bot, chat_id, message.message_id)
    return result


def poll_close_at(duration_seconds: int) -> datetime:
    # Telegram uses whole Unix seconds and requires at least five seconds of lead time.
    duration = min(max(duration_seconds, 6), 2628000)
    return datetime.now(timezone.utc).replace(microsecond=0) + timedelta(seconds=duration)


async def _resolve_choice_from_poll_answer(
    poll_answer, option_snapshot: str | list[PollOptionSnapshot] | None = None,
) -> tuple[AttendanceChoice | None, str]:
    persistent_ids = _get_extra_field(poll_answer, "option_persistent_ids") or []
    if option_snapshot is not None:
        options = json.loads(option_snapshot) if isinstance(option_snapshot, str) else option_snapshot
        if persistent_ids:
            for option in options:
                if option["persistent_id"] in persistent_ids:
                    return AttendanceChoice(option["choice"]), "poll_snapshot"
            if any(option["persistent_id"] for option in options):
                return None, "unknown_persistent_id"
        for option in options:
            if option["index"] in poll_answer.option_ids:
                return AttendanceChoice(option["choice"]), "poll_snapshot"
        return None, "unknown_option"

    cached_mapping = _POLL_PERSISTENT_TO_CHOICE.get(poll_answer.poll_id, {})
    for persistent_id in persistent_ids:
        choice = cached_mapping.get(str(persistent_id))
        if choice:
            return choice, f"persistent_id:{persistent_id}"

    loaded_config: LoadedPollConfig | None = None
    if persistent_ids:
        loaded_config = await _load_poll_config()
        for persistent_id in persistent_ids:
            choice = loaded_config.persistent_to_choice.get(str(persistent_id))
            if choice:
                return choice, f"config_persistent_id:{persistent_id}"

    option_ids = list(poll_answer.option_ids or [])
    if not option_ids:
        return None, "empty"

    option_id = option_ids[0]
    if loaded_config is None:
        loaded_config = await _load_poll_config()
    choice = loaded_config.option_to_choice.get(option_id) or OPTION_TO_CHOICE.get(option_id)
    return choice, f"option_id:{option_id}"


ABSENCE_EMOJI: dict[str, str] = {
    "VACATION": "🏖",
    "SICK": "🤒",
    "LEAVE": "📴",
    "DAY_OFF": "📴",
}

_ABSENCE_LABELS = {
    "VACATION": "Отпуск",
    "SICK": "Больничный",
    "LEAVE": "Отгул",
}

_DEPT_NAMES = {
    OZKO_DEPARTMENT_ID: "ОЗКО",
    OSB_DEPARTMENT_ID: "ОСБ",
}

_CHOICE_LABELS = {
    AttendanceChoice.EVOLUTION.value: "МФК «Эволюция»",
    AttendanceChoice.CSKA.value: "ЦСКА",
    AttendanceChoice.REMOTE.value: "Удалёнка",
}


async def is_manager_or_admin(telegram_id: int) -> tuple[bool, dict | None]:
    """Check whether user is a manager/admin."""
    user = await UsersRepo.get_by_telegram_id(telegram_id)
    if not user:
        return False, None
    if user.get("role") not in ("manager", "admin"):
        return False, None
    return True, user


def get_local_date() -> date:
    """Get current date in poll timezone."""
    tz = get_tzinfo(get_poll_timezone())
    return datetime.now(tz).date()


def get_local_datetime() -> datetime:
    """Get current datetime in poll timezone."""
    tz = get_tzinfo(get_poll_timezone())
    return datetime.now(tz)


def format_time(dt: datetime) -> str:
    """Format datetime in the configured poll timezone as HH:MM."""
    if dt.tzinfo is not None:
        dt = dt.astimezone(get_tzinfo(get_poll_timezone()))
    return dt.strftime("%H:%M")


async def build_absence_message(poll_date: date) -> str | None:
    """Build group message with today's absent users."""
    statuses = await DayStatusRepo.get_statuses_for_date(poll_date)

    if not statuses:
        return None

    by_kind: dict[str, list[tuple[str, date | None]]] = {}
    for status in statuses:
        kind = status.get("kind")
        if kind not in _ABSENCE_LABELS:
            continue
        full_name = status.get("full_name", "Unknown")
        end_date = status.get("end_date")
        by_kind.setdefault(kind, []).append((full_name, end_date))

    if not by_kind:
        return None

    lines = ["📌 Сегодня отсутствуют:", ""]
    for kind, label in _ABSENCE_LABELS.items():
        if kind not in by_kind:
            continue
        emoji = ABSENCE_EMOJI.get(kind, "")
        lines.append(f"{emoji} {label}:")
        for full_name, end_date in sorted(by_kind[kind], key=lambda item: item[0]):
            if end_date:
                lines.append(f"  • {full_name} — до {end_date.strftime('%d.%m.%Y')} включительно")
            else:
                lines.append(f"  • {full_name}")
        lines.append("")

    return "\n".join(lines).rstrip()


async def send_absence_message(bot: Any, chat_id: int, poll_date: date) -> bool:
    """Send absence message to group if there are absent users."""
    text = await build_absence_message(poll_date)
    if not text:
        logger.info("absence_message: нет отсутствующих на %s", poll_date)
        return False

    try:
        await bot.send_message(chat_id=chat_id, text=text)
        logger.info("absence_message: отправлено для poll_date=%s, chat_id=%s", poll_date, chat_id)
        return True
    except Exception as exc:
        logger.warning("absence_message: ошибка отправки для poll_date=%s: %s", poll_date, exc)
        return False


def _escape_html(value: Any) -> str:
    return html.escape(str(value or ""), quote=False)


def _is_no_tg_worker(user: dict[str, Any]) -> bool:
    return user.get("role") == UserRole.NO_TG_WORKER.value


def _format_non_respondent_for_group(user: dict[str, Any]) -> str | None:
    """Format user for public group messages; no-TG workers are hidden."""
    if _is_no_tg_worker(user):
        return None
    name = _escape_html(user.get("full_name") or "Пользователь")
    telegram_id = user.get("telegram_id")
    if telegram_id:
        return f'<a href="tg://user?id={telegram_id}">{name}</a>'
    return name


def poll_message_url(poll: dict[str, Any]) -> str | None:
    chat_id = str(poll.get("chat_id"))
    message_id = poll.get("message_id")
    if chat_id.startswith("-100") and chat_id[4:].isdigit() and message_id:
        return f"https://t.me/c/{chat_id[4:]}/{int(message_id)}"
    return None


def _bounded_names(names: list[str], budget: int) -> list[str]:
    displayed = []
    for name in sorted(names):
        units = len(name.encode("utf-16-le")) // 2 + 1
        if units > budget:
            break
        budget -= units
        displayed.append(_escape_html(name))

    omitted = len(names) - len(displayed)
    if omitted:
        displayed.append(f"И ещё {omitted}")
    return displayed


def _group_report_absences(poll_date: date, absence_statuses: list[dict[str, Any]]) -> dict[str, list[str]]:
    absent: dict[str, list[str]] = {}
    for status in absence_statuses:
        kind = status.get("kind")
        if kind not in _ABSENCE_LABELS:
            continue
        name = str(status.get("full_name") or "Без имени")
        end_date = status.get("end_date")
        if end_date and end_date > poll_date:
            name += f" — до {end_date:%d.%m.%Y} включительно"
        absent.setdefault(kind, []).append(name)
    return absent


def _render_daily_summary(
    poll_date: date, by_choice: dict[str, list[str]], unanswered: list[str], poll_url: str | None,
    *, absence_statuses: list[dict[str, Any]] | None = None,
) -> tuple[str, str]:
    responded = sum(len(names) for names in by_choice.values())
    missed = len(unanswered)
    expected = responded + missed
    date_label = poll_date.strftime("%d.%m.%Y")
    if expected:
        totals = f"<b>{responded} из {expected}</b> отметились · <b>{responded / expected:.0%}</b> участия"
    else:
        totals = "Сегодня ответов не ожидалось"

    rows = []
    plain_rows = []
    sections = []
    for key, names in by_choice.items():
        label = _escape_html("Удалённо" if key == AttendanceChoice.REMOTE else _CHOICE_LABELS[key])
        count = len(names)
        share = f"{count / responded:.0%}" if responded else "—"
        rows.append(f'<tr><td>{label}</td><td align="right">{count}</td><td align="right">{share}</td></tr>')
        plain_rows.append(f"{label} · <b>{count}</b>")
        if names:
            sections.append((label, names))

    rich = [
        "<h2>Явка ОЗКО</h2>",
        f"<p><i>{date_label} · Итоги опроса</i></p>",
        f"<p>{totals}</p>",
    ]
    plain = ["<b>Явка ОЗКО</b>", f"<i>{date_label} · Итоги опроса</i>", "", totals]
    if missed:
        missed_text = f"Не ответили: <b>{missed}</b>"
        rich.append(f"<p>{missed_text}</p>")
        plain.append(missed_text)
    rich.append(
        '<table bordered striped compact><tr><th>Вариант</th><th align="right">Чел.</th>'
        '<th align="right">Доля</th></tr>' + "".join(rows) + "</table>"
    )
    plain.extend(["", *plain_rows])
    if unanswered:
        sections.append(("Не ответили", unanswered))

    absent = _group_report_absences(poll_date, absence_statuses or [])

    if sections or absent:
        rich.extend(["<hr/>", "<h4>Списки участников</h4>"])
        plain.extend(["", "<b>Списки участников</b>"])

    # Allocate a separate budget to each section so a large office cannot hide the others.
    for label, names in sections:
        plain_names = _bounded_names(names, 650)
        rich_names = _bounded_names(names, 5000)
        items = "".join(f"<li>{name}</li>" for name in rich_names)
        rich.append(f"<details><summary><b>{label}</b> · {len(names)}</summary><ul>{items}</ul></details>")
        plain.append(f"<b>{label} · {len(names)}</b>")
        plain.append("<blockquote expandable>" + "\n".join(plain_names) + "</blockquote>")

    if absent:
        absent_total = sum(len(names) for names in absent.values())
        rich.append(f"<details><summary><b>Отсутствуют</b> · {absent_total}</summary>")
        plain.append(f"<b>Отсутствуют · {absent_total}</b>")
        plain_groups = []
        for kind, label in _ABSENCE_LABELS.items():
            names = absent.get(kind)
            if not names:
                continue
            title = f"{label} · {len(names)}"
            items = "".join(f"<li>{name}</li>" for name in _bounded_names(names, 5000 // len(absent)))
            rich.append(f"<h5>{title}</h5><ul>{items}</ul>")
            plain_groups.append(f"<b>{title}</b>\n" + "\n".join(_bounded_names(names, 650 // len(absent))))
        rich.append("</details>")
        plain.append("<blockquote expandable>" + "\n\n".join(plain_groups) + "</blockquote>")

    if poll_url:
        url = html.escape(poll_url, quote=True)
        rich.append(
            f'<tg-button-row align="left"><tg-button type="url" style="primary" url="{url}">'
            'Открыть опрос</tg-button></tg-button-row>'
        )
        plain.append(f'<a href="{url}">Открыть опрос</a>')
    return "\n".join(plain), "\n".join(rich)


async def _load_department_attendance(
    poll: dict[str, Any], poll_date: date, department_id: int,
) -> tuple[dict[str, list[str]], list[str]]:
    poll_id = poll.get("id")
    if poll.get("expected_users_snapshot_at") and poll_id:
        rows = await PollsRepo.get_summary_attendance(poll_id, poll.get("telegram_poll_id"), department_id)
    else:
        marks = await AttendanceRepo.get_marks_for_date(poll_date, department_id=department_id)
        marked_users = {mark["user_id"] for mark in marks if mark.get("user_id") is not None}
        non_respondents = await PollsService.get_non_respondents(poll_id, department_id=department_id) if poll_id else []
        rows = [*marks, *(
            row for row in non_respondents if row.get("id") not in marked_users and not _is_no_tg_worker(row)
        )]

    by_choice: dict[str, list[str]] = {choice.value: [] for choice in AttendanceChoice}
    unanswered = []
    for row in rows:
        name = str(row.get("full_name") or "Без имени")
        choice = row.get("choice")
        if choice in by_choice:
            by_choice[choice].append(name)
        elif not choice:
            unanswered.append(name)
    return by_choice, unanswered


async def send_poll_close_summary(
    bot: Any,
    poll: dict[str, Any] | None,
    *,
    delay_seconds: float = 0,
    department_id: int = OZKO_DEPARTMENT_ID,
) -> bool:
    summary_chat_id = get_poll_summary_chat_id()
    if not summary_chat_id or not poll:
        return False
    if delay_seconds > 0:
        await asyncio.sleep(delay_seconds)

    poll_date = poll.get("poll_date") or get_local_date()
    by_choice, unanswered = await _load_department_attendance(poll, poll_date, department_id)
    absence_statuses = await DayStatusRepo.get_statuses_for_date(poll_date, department_id=department_id)
    html_text, rich_html_text = _render_daily_summary(
        poll_date, by_choice, unanswered, poll_message_url(poll), absence_statuses=absence_statuses,
    )
    state = await send_tracked_report(
        bot,
        f'poll_summary:{poll.get("telegram_poll_id") or poll.get("id") or poll_date}',
        chat_id=summary_chat_id,
        html_text=html_text,
        rich_html_text=rich_html_text,
    )
    return state == "sent"


@dataclass(frozen=True)
class DepartmentAttendanceReport:
    by_choice: dict[str, list[str]]
    unanswered: list[str]
    absent: dict[str, list[str]]

    @property
    def responded(self) -> int:
        return sum(len(names) for names in self.by_choice.values())

    @property
    def expected(self) -> int:
        return self.responded + len(self.unanswered)

    @property
    def absent_count(self) -> int:
        return sum(len(names) for names in self.absent.values())


def _render_department_lists_summary(
    poll_date: date, ozko: DepartmentAttendanceReport, osb: DepartmentAttendanceReport,
) -> tuple[str, str]:
    departments = (("ОЗКО", ozko), ("ОСБ", osb))
    count_rows = [
        ("Ответили", ozko.responded, osb.responded),
        ("Не ответили", len(ozko.unanswered), len(osb.unanswered)),
    ]
    for choice in AttendanceChoice:
        label = "Удалённо" if choice == AttendanceChoice.REMOTE else _CHOICE_LABELS[choice.value]
        count_rows.append((label, len(ozko.by_choice[choice.value]), len(osb.by_choice[choice.value])))
    count_rows.append(("Отсутствуют", ozko.absent_count, osb.absent_count))

    total = ozko.responded + osb.responded
    expected = ozko.expected + osb.expected
    participation = (
        f"<b>{total} из {expected}</b> отметились · <b>{total / expected:.0%}</b> участия"
        if expected else "Сегодня ответов не ожидалось"
    )
    rich = [
        "<h2>Явка по отделам</h2>",
        f"<p><i>{poll_date:%d.%m.%Y} · ОЗКО и ОСБ</i></p>",
        f"<p>{participation}</p>",
        '<table bordered striped compact><tr><th>Статус</th><th>ОЗКО</th><th>ОСБ</th></tr>',
    ]
    plain = ["<b>Явка по отделам</b>", f"<i>{poll_date:%d.%m.%Y} · ОЗКО и ОСБ</i>", participation, ""]
    for index, (label, first, second) in enumerate(count_rows):
        label = _escape_html(label)
        cells = (label, str(first), str(second))
        if index == 0:
            cells = tuple(f"<b>{value}</b>" for value in cells)
        left, middle, right = cells
        rich.append(f'<tr><td>{left}</td><td align="right">{middle}</td><td align="right">{right}</td></tr>')
        plain.append(f"{label} · ОЗКО <b>{first}</b> / ОСБ <b>{second}</b>")
    rich.extend(["</table>", "<hr/>"])

    for label, department in departments:
        rich.append(f"<details><summary><b>{label}</b> · {department.responded} из {department.expected} отметились</summary>")
        plain.append(f"\n<b>{label}</b>")
        sections = [
            ("Удалённо" if choice == AttendanceChoice.REMOTE else _CHOICE_LABELS[choice.value],
             department.by_choice[choice.value])
            for choice in AttendanceChoice if department.by_choice[choice.value]
        ]
        if department.unanswered:
            sections.append(("Не ответили", department.unanswered))
        absence_start = len(sections)
        sections.extend(
            (title, department.absent[kind]) for kind, title in _ABSENCE_LABELS.items()
            if kind in department.absent
        )
        plain_details = []
        plain_budget = 1400 // max(1, len(sections))
        rich_budget = 8000 // max(1, len(sections))
        for index, (section_label, names) in enumerate(sections):
            if index == absence_start:
                rich.append(f"<h4>Отсутствуют · {department.absent_count}</h4>")
                plain_details.append(f"<b>Отсутствуют · {department.absent_count}</b>")
            title = f"{_escape_html(section_label)} · {len(names)}"
            items = "".join(f"<li>{name}</li>" for name in _bounded_names(names, rich_budget))
            rich.append(f"<h5>{title}</h5><ul>{items}</ul>")
            plain_details.extend([f"<b>{title}</b>", *_bounded_names(names, plain_budget)])
        if plain_details:
            plain.append("<blockquote expandable>" + "\n".join(plain_details) + "</blockquote>")
        else:
            rich.append("<p>Нет отметок для списка.</p>")
        rich.append("</details>")

    return "\n".join(plain), "\n".join(rich)


async def send_department_lists_summary(
    bot: Any,
    poll: dict[str, Any] | None,
    *,
    delay_seconds: float = 0,
) -> bool:
    """Send the combined attendance report to DEPARTMENT_LISTS_CHAT_ID."""
    chat_id = get_department_lists_chat_id()
    if not chat_id or not poll:
        return False

    if delay_seconds > 0:
        await asyncio.sleep(delay_seconds)

    poll_date = poll.get("poll_date") or get_local_date()
    reports = []
    for department_id in (OZKO_DEPARTMENT_ID, OSB_DEPARTMENT_ID):
        by_choice, unanswered = await _load_department_attendance(poll, poll_date, department_id)
        statuses = await DayStatusRepo.get_statuses_for_date(poll_date, department_id=department_id)
        reports.append(DepartmentAttendanceReport(by_choice, unanswered, _group_report_absences(poll_date, statuses)))

    plain, rich = _render_department_lists_summary(poll_date, *reports)
    state = await send_tracked_report(
        bot,
        f'department_lists:{poll.get("telegram_poll_id") or poll.get("id") or poll_date}',
        chat_id=chat_id,
        html_text=plain,
        rich_html_text=rich,
    )
    return state == "sent"



def _is_poll_already_closed_error(exc: Exception) -> bool:
    """Check whether Telegram reports that the poll is already closed."""
    error_str = str(exc).lower()
    return (
        "poll has already been closed" in error_str
        or "poll_already_closed" in error_str
        or "poll can't be stopped" in error_str
        or "poll cant be stopped" in error_str
    )


def _is_not_enough_rights_error(exc: Exception) -> bool:
    """Check whether Telegram reports missing group permissions."""
    error_str = str(exc).lower()
    return (
        "not enough rights" in error_str
        or "chat_admin_required" in error_str
        or "can't pin" in error_str
    )


async def _pin_poll_message(
    bot: Any,
    chat_id: int,
    message_id: int,
    poll_date: date,
    notify_user_id: int | None = None,
) -> bool:
    """Pin poll message in a group on a best-effort basis."""
    if chat_id >= 0:
        logger.debug("pin_poll: chat_id=%s не группа, пропускаем", chat_id)
        return False

    try:
        await bot.pin_chat_message(
            chat_id=chat_id,
            message_id=message_id,
            disable_notification=False,
        )
        logger.info(
            "poll_pinned: chat_id=%s, message_id=%s, poll_date=%s",
            chat_id,
            message_id,
            poll_date,
        )
        return True
    except Exception as exc:
        if _is_not_enough_rights_error(exc):
            logger.warning("poll_pin_failed: not enough rights, chat_id=%s", chat_id)
            if notify_user_id:
                try:
                    await bot.send_message(
                        chat_id=notify_user_id,
                        text="⚠️ Не могу закрепить опрос: у бота нет прав на закрепление сообщений в группе.",
                    )
                except Exception as notify_exc:
                    logger.debug("poll_pin_notify failed: %s", notify_exc)
        else:
            logger.warning("poll_pin_failed: %s", exc)
        return False


async def _unpin_poll_message(bot: Any, chat_id: int, message_id: int) -> bool:
    """Unpin poll message in a group on a best-effort basis."""
    if chat_id >= 0:
        return False

    try:
        await bot.unpin_chat_message(chat_id=chat_id, message_id=message_id)
        logger.info("poll_unpinned: chat_id=%s, message_id=%s", chat_id, message_id)
        return True
    except Exception as exc:
        logger.debug("poll_unpin_failed: %s", exc)
        return False


async def _stop_telegram_poll_safe(bot: Any, chat_id: int, message_id: int) -> None:
    """Stop Telegram poll without breaking the close flow on Telegram errors."""
    try:
        await bot.stop_poll(chat_id=chat_id, message_id=message_id)
        logger.info("native poll остановлен: chat_id=%s, message_id=%s", chat_id, message_id)
    except Exception as exc:
        if _is_poll_already_closed_error(exc):
            logger.info("native poll уже закрыт: chat_id=%s, message_id=%s", chat_id, message_id)
        else:
            logger.warning("не удалось остановить native poll: %s", exc)


async def _delete_or_edit_reminder(bot: Any, chat_id: int, poll_id: int) -> None:
    """Delete or neutralize poll reminder message after poll close."""
    reminder_message_id = await PollsService.get_reminder_message_id(poll_id)
    if not reminder_message_id:
        logger.debug("close_poll: reminder_message_id отсутствует для poll_id=%s", poll_id)
        return

    try:
        await bot.delete_message(chat_id=chat_id, message_id=reminder_message_id)
        logger.info(
            "close_poll: сообщение-напоминание удалено, chat_id=%s, message_id=%s",
            chat_id,
            reminder_message_id,
        )
    except Exception as delete_error:
        error_text = str(delete_error).lower()
        if "message to delete not found" in error_text:
            logger.info(
                "close_poll: сообщение-напоминание уже удалено, chat_id=%s, message_id=%s",
                chat_id,
                reminder_message_id,
            )
        else:
            logger.info(
                "close_poll: не удалось удалить напоминание (%s), пробуем редактирование",
                delete_error,
            )
            try:
                await bot.edit_message_text(
                    chat_id=chat_id,
                    message_id=reminder_message_id,
                    text="✅ Опрос закрыт.",
                )
                logger.info(
                    "close_poll: сообщение-напоминание отредактировано, chat_id=%s, message_id=%s",
                    chat_id,
                    reminder_message_id,
                )
            except Exception as edit_error:
                logger.info("close_poll: не удалось отредактировать напоминание: %s", edit_error)

    await PollsService.clear_reminder_message_id(poll_id)


async def send_poll_reminder(
    bot: Any,
    poll_id: int,
    poll_date: date,
    telegram_poll_id: str,
) -> None:
    """Send attendance poll reminder to the group and OZKO DMs."""
    logger.info(
        "reminder: запуск для poll_id=%s, tg_poll_id=%s",
        poll_id,
        telegram_poll_id[:16] if telegram_poll_id else None,
    )

    poll = await PollsService.get_poll(poll_id)
    if not poll:
        logger.debug("reminder: опрос #%s не найден, пропускаем", poll_id)
        return

    current_tg_poll_id = poll.get("telegram_poll_id")
    current_status = poll.get("status")
    if current_tg_poll_id != telegram_poll_id:
        logger.info(
            "reminder: опрос #%s пересоздан (tg_poll_id изменился: %s -> %s), пропускаем",
            poll_id,
            telegram_poll_id[:16] if telegram_poll_id else None,
            current_tg_poll_id[:16] if current_tg_poll_id else None,
        )
        return

    if current_status != "open":
        logger.debug("reminder: опрос #%s уже закрыт (status=%s), пропускаем", poll_id, current_status)
        return

    if poll.get("reminder_sent_at"):
        logger.debug("reminder: опрос #%s уже имеет reminder, пропускаем", poll_id)
        return

    chat_id = poll.get("chat_id")
    if not chat_id:
        logger.warning("reminder: опрос #%s не имеет chat_id", poll_id)
        return

    non_respondents = await PollsService.get_non_respondents(poll_id)
    logger.info(
        "reminder: poll_id=%s, poll_date=%s, tg_poll_id=%s, non_respondents_count=%d",
        poll_id,
        poll_date,
        telegram_poll_id[:16] if telegram_poll_id else None,
        len(non_respondents),
    )

    public_non_respondents = [nr for nr in non_respondents if not _is_no_tg_worker(nr)]

    if not public_non_respondents:
        logger.info(
            "reminder: публичный список неотметившихся пуст для poll_id=%s, "
            "напоминание не отправлено (все отметились, исключены статусами или скрыты из общего чата)",
            poll_id,
        )
        await PollsService.mark_reminder_sent(poll_id)
        return

    first_ids = [nr.get("telegram_id") for nr in public_non_respondents[:5]]
    logger.debug("reminder: first_telegram_ids=%s", first_ids)

    dept_groups: dict[int | None, list[str]] = {}
    for nr in public_non_respondents:
        dept = nr.get("department_id")
        formatted = _format_non_respondent_for_group(nr)
        if formatted:
            dept_groups.setdefault(dept, []).append(formatted)

    lines = ["⏰ Напоминание: отметьтесь в опросе явки, пожалуйста."]
    for dept_id in (OZKO_DEPARTMENT_ID, OSB_DEPARTMENT_ID):
        members = dept_groups.pop(dept_id, [])
        if members:
            lines.append(f"\n{_DEPT_NAMES.get(dept_id, f'Отдел {dept_id}')}: {', '.join(members)}")
    for members in dept_groups.values():
        if members:
            lines.append(f"\n{', '.join(members)}")

    reminder_message_id: int | None = None
    try:
        sent_message = await bot.send_message(
            chat_id=chat_id,
            text="\n".join(lines),
            parse_mode="HTML",
            disable_web_page_preview=True,
        )
        reminder_message_id = sent_message.message_id
        logger.info(
            "reminder: отправлен для poll_id=%s, tg_poll_id=%s, неотметившихся: %d, message_id=%s",
            poll_id,
            telegram_poll_id[:16] if telegram_poll_id else None,
            len(public_non_respondents),
            reminder_message_id,
        )
    except Exception as exc:
        logger.warning("reminder: ошибка отправки для опроса #%s: %s", poll_id, exc)

    await PollsService.mark_reminder_sent(poll_id, reminder_message_id)

    if _POLL_REMINDER_DM_ENABLED and non_respondents:
        await _send_poll_reminder_dm_ozko(bot, non_respondents, poll_id)


async def _send_poll_reminder_dm_ozko(
    bot: Any,
    non_respondents: list[dict],
    poll_id: int,
) -> None:
    """Send private reminders to OZKO users who have not voted."""
    try:
        ozko_users = await UsersRepo.get_active_by_department(OZKO_DEPARTMENT_ID)
    except Exception as exc:
        logger.warning("reminder_dm: error fetching OZKO users: %s", exc)
        return

    ozko_tg_ids = {user["telegram_id"] for user in ozko_users if user.get("telegram_id")}
    if not ozko_tg_ids:
        return

    ozko_non_respondents = [
        nr for nr in non_respondents
        if nr.get("telegram_id") in ozko_tg_ids
        and not _is_no_tg_worker(nr)
    ]

    if not ozko_non_respondents:
        logger.debug("reminder_dm: no OZKO non-respondents for poll_id=%s", poll_id)
        return

    dm_text = "⏰ Не забудь отметиться в опросе явки!\nОпрос скоро закроется. \nПара дней офиса не помешают 😉"
    sent = 0
    for nr in ozko_non_respondents:
        tg_id = nr.get("telegram_id")
        if not tg_id:
            continue
        try:
            await bot.send_message(tg_id, dm_text)
            sent += 1
        except Exception as exc:
            logger.debug("reminder_dm: error sending to %s: %s", tg_id, exc)
        await asyncio.sleep(0.05)

    logger.info("reminder_dm: sent %d/%d DMs to OZKO non-respondents", sent, len(ozko_non_respondents))
