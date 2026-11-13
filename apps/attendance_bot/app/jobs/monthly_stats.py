"""Monthly OZKO attendance statistics for the leadership chat."""

from __future__ import annotations

import html
import os
from calendar import monthrange
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any

from core.screw_core.models.enums import EventType, NotifyChannel
from core.screw_core.repos.events_repo import EventsRepo
from core.screw_core.repos.monthly_stats_repo import MonthlyStatsRepo
from core.screw_core.services.notify import get_violations_ozko_chat_id
from core.screw_core.services.permissions import OZKO_DEPARTMENT_ID
from core.screw_core.services.telegram_delivery import send_system_message
from core.screw_core.utils.logging import get_logger

logger = get_logger(__name__)

# Telegram text limits count UTF-16 units; reserve space for the preview warning.
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


def _message_units(text: str) -> int:
    """Conservatively budget escaped HTML in Telegram's UTF-16 units."""
    return len(text.encode("utf-16-le")) // 2


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
        if _message_units(prefix) + 1 + _message_units(block) > max_length:
            break
        rendered = candidate

    if not rendered:
        return prefix + f"\n<i>Список не помещается в сообщение: {_format_count(len(names))} человек.</i>"

    omitted = len(names) - len(rendered)
    suffix = f"\n… и ещё {omitted}" if omitted else ""
    return prefix + "\n" + opening + "\n".join(rendered) + suffix + closing


@dataclass(frozen=True, slots=True)
class FirstPlaceRank:
    user_id: int
    name: str
    count: int
    best_seconds: Decimal
    rank: int


def _positive_response_seconds(raw: Any) -> Decimal | None:
    if raw is None:
        return None
    try:
        seconds = Decimal(str(raw))
    except InvalidOperation:
        return None
    return seconds if seconds.is_finite() and seconds > 0 else None


def _rank_first_places(
    responses: list[dict[str, Any]], names_by_user: dict[int, str]
) -> list[FirstPlaceRank]:
    """Competition ranking: wins DESC, best WINNING time ASC; credit co-winners."""
    by_poll: dict[int, list[tuple[int, Decimal]]] = defaultdict(list)
    for row in responses:
        seconds = _positive_response_seconds(row.get("response_seconds"))
        if seconds is not None:
            by_poll[int(row["poll_run_id"])].append((int(row["user_id"]), seconds))

    counts: Counter[int] = Counter()
    winning_best: dict[int, Decimal] = {}
    for answers in by_poll.values():
        winning_time = min(seconds for _, seconds in answers)
        for user_id in {uid for uid, seconds in answers if seconds == winning_time}:
            counts[user_id] += 1
            winning_best[user_id] = min(winning_best.get(user_id, winning_time), winning_time)

    ordered = sorted(counts, key=lambda uid: (
        -counts[uid], winning_best[uid], names_by_user.get(uid, "—"), uid,
    ))
    result: list[FirstPlaceRank] = []
    previous = None
    rank = 0
    for position, user_id in enumerate(ordered, 1):
        score = (counts[user_id], winning_best[user_id])
        if score != previous:
            rank = position
        previous = score
        result.append(FirstPlaceRank(
            user_id, _shorten_name(names_by_user.get(user_id, "—")),
            counts[user_id], winning_best[user_id], rank,
        ))
    return result


def _format_winning_seconds(seconds: Decimal) -> str:
    # Preserve exact DB precision: visibly different ranks must not look tied.
    rendered = format(seconds, "f")
    if "." in rendered:
        rendered = rendered.rstrip("0").rstrip(".")
    return f"{rendered} сек."


def _append_bounded_section(
    lines: list[str], heading: str, rows: list[str], *, max_length: int, omitted_label: str
) -> None:
    """Keep whole escaped rows and explicitly disclose omitted leaderboard entries."""
    if not rows:
        return
    lines.extend(["", heading])
    prefix = "\n".join(lines)
    rendered: list[str] = []
    for row in rows:
        candidate = rendered + [row]
        remaining = len(rows) - len(candidate)
        suffix = f"\n<i>Список сокращён; не показано {omitted_label}: {_format_count(remaining)}.</i>" if remaining else ""
        if _message_units(prefix) + 1 + _message_units("\n".join(candidate)) + _message_units(suffix) > max_length:
            break
        rendered = candidate
    lines.extend(rendered)
    remaining = len(rows) - len(rendered)
    if remaining:
        lines.append(f"<i>Список сокращён; не показано {omitted_label}: {_format_count(remaining)}.</i>")


def _build_report_html(
    *,
    year: int,
    month: int,
    poll_count: int,
    expected_count: int,
    response_count: int,
    participation_pct: int,
    fastest: list[tuple[str, float | Decimal]],
    first_top: list[FirstPlaceRank],
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

    footer = f"✅ <b>Ответили на все ожидаемые опросы — {_format_count(len(perfect_names))}</b>"
    perfect_fallback = (
        f"\n<i>Список не помещается в сообщение: {_format_count(len(perfect_names))} человек.</i>"
        if perfect_names else ""
    )
    sections_budget = MAX_REPORT_LENGTH - _message_units(footer) - 2 - _message_units(perfect_fallback)
    leaders = [entry for entry in first_top if entry.rank <= 3]
    first_heading = "🎯 <b>Чаще всех отвечали первыми</b>"
    first_fallback = f"<i>Список сокращён; не показано призёров: {_format_count(len(leaders))}.</i>"
    first_reserve = _message_units(first_heading) + _message_units(first_fallback) + 3 if leaders else 0
    medals = ["🥇", "🥈", "🥉"]
    _append_bounded_section(
        lines,
        "⚡ <b>Лучшее время месяца</b>",
        [
            f"{medals[index]} {_html(name)} — {_format_seconds(seconds)}"
            for index, (name, seconds) in enumerate(fastest[:3])
        ],
        max_length=sections_budget - first_reserve,
        omitted_label="участников",
    )
    _append_bounded_section(
        lines,
        first_heading,
        [
            f"{medals[entry.rank - 1]} {_html(entry.name)} — {entry.count} раз"
            f" · лучшее время победы: {_format_winning_seconds(entry.best_seconds)}"
            for entry in leaders
        ],
        max_length=sections_budget,
        omitted_label="призёров",
    )
    lines.extend(["", footer])
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

    best_by_user: dict[int, Decimal] = {}
    for row in valid_responses:
        seconds = _positive_response_seconds(row.get("response_seconds"))
        if seconds is None:
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

    first_top = _rank_first_places(valid_responses, names_by_user)

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
