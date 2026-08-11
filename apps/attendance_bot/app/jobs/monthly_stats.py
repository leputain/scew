"""Monthly OZKO attendance statistics for the leadership chat."""

from __future__ import annotations

import html
import os
from calendar import monthrange
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any

from core.screw_core.models.enums import EventType, NotifyChannel
from core.screw_core.repos.events_repo import EventsRepo
from core.screw_core.repos.monthly_stats_repo import MonthlyStatsRepo
from core.screw_core.services.notify import get_violations_ozko_chat_id
from core.screw_core.services.permissions import OZKO_DEPARTMENT_ID
from core.screw_core.services.telegram_delivery import send_system_message
from core.screw_core.utils.logging import get_logger

logger = get_logger(__name__)

MAX_REPORT_LENGTH = 3800
DEPARTMENT_CODE = "ОЗКО"
_MONTH_NAMES_GENITIVE = {
    1: "января",
    2: "февраля",
    3: "марта",
    4: "апреля",
    5: "мая",
    6: "июня",
    7: "июля",
    8: "августа",
    9: "сентября",
    10: "октября",
    11: "ноября",
    12: "декабря",
}


@dataclass(frozen=True, slots=True)
class MonthlyStatsReport:
    html: str
    year: int
    month: int
    department_id: int
    department_code: str
    poll_count: int
    expected_count: int
    response_count: int
    participation_pct: int
    perfect_count: int
    is_approximate: bool


def is_monthly_stats_enabled() -> bool:
    return os.getenv("MONTHLY_STATS_ENABLED", "0") == "1"


def _format_seconds(seconds: float | Decimal) -> str:
    """Format response time without losing sub-second precision."""
    value = float(seconds)
    if value < 60:
        rendered = f"{value:.3f}".rstrip("0").rstrip(".")
        return f"{rendered} сек."
    minutes = int(value // 60)
    secs = int(value % 60)
    return f"{minutes} мин. {secs} сек."


def _shorten_name(full_name: str) -> str:
    parts = str(full_name or "").split()
    if len(parts) >= 3:
        return f"{parts[0]} {parts[1][0]}.{parts[2][0]}."
    if len(parts) >= 2:
        return f"{parts[0]} {parts[1][0]}."
    return full_name or "—"


def _html(value: object) -> str:
    return html.escape(str(value or ""), quote=False)


def _format_count(value: int) -> str:
    return f"{value:,}".replace(",", " ")


def _render_perfect_block(prefix: str, names: list[str], *, max_length: int) -> str:
    if not names:
        return prefix

    opening = "<blockquote expandable>"
    closing = "</blockquote>"
    rendered: list[str] = []
    for index, name in enumerate(names):
        candidate = rendered + [_html(name)]
        omitted = len(names) - len(candidate)
        suffix = f"\n… и ещё {omitted}" if omitted else ""
        block = opening + "\n".join(candidate) + suffix + closing
        if len(prefix) + 1 + len(block) > max_length:
            break
        rendered = candidate

    if not rendered:
        return prefix + f"\n<i>Список не помещается в сообщение: {_format_count(len(names))} человек.</i>"

    omitted = len(names) - len(rendered)
    suffix = f"\n… и ещё {omitted}" if omitted else ""
    return prefix + "\n" + opening + "\n".join(rendered) + suffix + closing


def _build_report_html(
    *,
    year: int,
    month: int,
    poll_count: int,
    expected_count: int,
    response_count: int,
    participation_pct: int,
    fastest: list[tuple[str, float]],
    first_top: list[tuple[str, int]],
    perfect_names: list[str],
) -> str:
    month_name = _MONTH_NAMES_GENITIVE.get(month, str(month))
    lines = [
        f"🏆 <b>Итоги {month_name} {year}</b>",
        f"<i>{DEPARTMENT_CODE} · {poll_count} опросов</i>",
        "",
        "📊 <b>Главное</b>",
        f"Участие в опросах: <b>{participation_pct}%</b>",
        f"Ответов: <b>{_format_count(response_count)} из {_format_count(expected_count)}</b>",
        f"Без пропусков: <b>{_format_count(len(perfect_names))} человек</b>",
    ]

    if fastest:
        lines.extend(["", "⚡ <b>Лучшее время месяца</b>"])
        medals = ["🥇", "🥈", "🥉"]
        lines.extend(
            f"{medals[index]} {_html(name)} — {_format_seconds(seconds)}"
            for index, (name, seconds) in enumerate(fastest[:3])
        )

    if first_top:
        lines.extend(["", "🎯 <b>Чаще всех отвечали первыми</b>"])
        medals = ["🥇", "🥈", "🥉"]
        lines.extend(
            f"{medals[index]} {_html(name)} — {count} раз"
            for index, (name, count) in enumerate(first_top[:3])
        )

    lines.extend(
        [
            "",
            f"✅ <b>Ответили на все ожидаемые опросы — {_format_count(len(perfect_names))}</b>",
        ]
    )
    prefix = "\n".join(lines)
    return _render_perfect_block(prefix, perfect_names, max_length=MAX_REPORT_LENGTH)


async def build_monthly_stats_report(
    year: int,
    month: int,
    *,
    department_id: int = OZKO_DEPARTMENT_ID,
) -> MonthlyStatsReport | None:
    source = await MonthlyStatsRepo.load_period(year, month, department_id)
    polls = list(source.get("polls") or [])
    if not polls:
        return None

    expected_rows = list(source.get("expected") or [])
    expected_pairs = {
        (int(row["poll_run_id"]), int(row["user_id"]))
        for row in expected_rows
    }
    names_by_user = {
        int(row["user_id"]): str(row.get("full_name") or "—")
        for row in expected_rows
    }

    valid_responses: list[dict[str, Any]] = []
    seen_response_pairs: set[tuple[int, int]] = set()
    for row in source.get("responses") or []:
        pair = (int(row["poll_run_id"]), int(row["user_id"]))
        if pair not in expected_pairs or pair in seen_response_pairs:
            continue
        seen_response_pairs.add(pair)
        names_by_user.setdefault(int(row["user_id"]), str(row.get("full_name") or "—"))
        valid_responses.append(row)

    expected_by_user: dict[int, set[int]] = defaultdict(set)
    for poll_id, user_id in expected_pairs:
        expected_by_user[user_id].add(poll_id)

    responded_by_user: dict[int, set[int]] = defaultdict(set)
    for row in valid_responses:
        responded_by_user[int(row["user_id"])].add(int(row["poll_run_id"]))

    perfect_user_ids = [
        user_id
        for user_id, expected_poll_ids in expected_by_user.items()
        if expected_poll_ids and responded_by_user.get(user_id, set()) == expected_poll_ids
    ]
    perfect_names = sorted(_shorten_name(names_by_user.get(user_id, "—")) for user_id in perfect_user_ids)

    best_by_user: dict[int, float] = {}
    for row in valid_responses:
        raw_seconds = row.get("response_seconds")
        if raw_seconds is None:
            continue
        seconds = float(raw_seconds)
        if seconds <= 0:
            continue
        user_id = int(row["user_id"])
        best_by_user[user_id] = min(best_by_user.get(user_id, seconds), seconds)
    fastest = [
        (_shorten_name(names_by_user.get(user_id, "—")), seconds)
        for user_id, seconds in sorted(
            best_by_user.items(),
            key=lambda item: (item[1], _shorten_name(names_by_user.get(item[0], "—"))),
        )[:3]
    ]

    first_counts: Counter[int] = Counter()
    responses_by_poll: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for row in valid_responses:
        raw_seconds = row.get("response_seconds")
        if raw_seconds is not None and float(raw_seconds) > 0:
            responses_by_poll[int(row["poll_run_id"])].append(row)
    for responses in responses_by_poll.values():
        first = min(
            responses,
            key=lambda row: (float(row["response_seconds"]), int(row["user_id"])),
        )
        first_counts[int(first["user_id"])] += 1
    first_top = [
        (_shorten_name(names_by_user.get(user_id, "—")), count)
        for user_id, count in sorted(
            first_counts.items(),
            key=lambda item: (-item[1], _shorten_name(names_by_user.get(item[0], "—"))),
        )[:3]
    ]

    expected_count = len(expected_pairs)
    response_count = len(seen_response_pairs)
    participation_pct = round(response_count / expected_count * 100) if expected_count else 0
    report_html = _build_report_html(
        year=year,
        month=month,
        poll_count=len(polls),
        expected_count=expected_count,
        response_count=response_count,
        participation_pct=participation_pct,
        fastest=fastest,
        first_top=first_top,
        perfect_names=perfect_names,
    )
    return MonthlyStatsReport(
        html=report_html,
        year=year,
        month=month,
        department_id=department_id,
        department_code=DEPARTMENT_CODE,
        poll_count=len(polls),
        expected_count=expected_count,
        response_count=response_count,
        participation_pct=participation_pct,
        perfect_count=len(perfect_names),
        is_approximate=bool(source.get("approximate_poll_ids")),
    )


async def build_monthly_stats_text(year: int, month: int) -> str | None:
    """Backward-compatible text-only monthly stats builder."""
    report = await build_monthly_stats_report(year, month)
    return report.html if report else None


async def send_monthly_stats_to_violations_chat(
    bot: Any,
    year: int,
    month: int,
    *,
    automatic: bool = False,
) -> tuple[bool, str, int | None]:
    chat_id = get_violations_ozko_chat_id()
    if not chat_id:
        return False, "VIOLATIONS_OZKO_CHAT_ID не настроен", None

    dedup_key = f"monthly_stats:{year:04d}-{month:02d}:OZKO:auto"
    if automatic and await EventsRepo.get_by_dedup_key(dedup_key):
        return True, f"уже опубликовано за {year}-{month:02d}", chat_id

    report = await build_monthly_stats_report(year, month)
    if not report:
        return False, f"нет данных за {year}-{month:02d}", chat_id

    delivery = await send_system_message(
        bot,
        chat_id=chat_id,
        html_text=report.html,
        reaction_emoji="🏆",
        delivery_mode="html",
    )

    if automatic:
        try:
            await EventsRepo.create_event(
                event_type=EventType.MONTHLY_STATS_SENT,
                dedup_key=dedup_key,
                event_date=date(year, month, monthrange(year, month)[1]),
                payload={
                    "department": DEPARTMENT_CODE,
                    "poll_count": report.poll_count,
                    "response_count": report.response_count,
                    "expected_count": report.expected_count,
                    "approximate": report.is_approximate,
                    "delivery_method": delivery.method,
                },
                notify_channel=NotifyChannel.NONE,
            )
        except Exception as exc:
            logger.warning("monthly_stats: failed to persist automatic dedup marker: %s", exc)

    return True, f"опубликовано за {year}-{month:02d}", chat_id
