"""Privacy-preserving flud chat statistics and digest formatting."""

from __future__ import annotations

import html
import re
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from typing import Any

from core.screw_core.repos.flud_repo import FludRepo

MESSAGE_TYPE_LABELS = {
    "text": "текст",
    "photo": "фото",
    "video": "видео",
    "animation": "GIF",
    "sticker": "стикер",
    "voice": "голосовое",
    "document": "документ",
    "other": "сообщение",
}

MONTH_NAMES = {
    1: "январь",
    2: "февраль",
    3: "март",
    4: "апрель",
    5: "май",
    6: "июнь",
    7: "июль",
    8: "август",
    9: "сентябрь",
    10: "октябрь",
    11: "ноябрь",
    12: "декабрь",
}


def sanitize_preview(text: str | None, max_chars: int = 120) -> str | None:
    """Normalize and truncate optional message preview."""
    if not text or max_chars <= 0:
        return None
    normalized = re.sub(r"\s+", " ", text).strip()
    if not normalized:
        return None
    if len(normalized) <= max_chars:
        return normalized
    return normalized[: max(0, max_chars - 1)].rstrip() + "…"


def classify_message(payload: Any) -> str:
    """Classify an aiogram-like message object into a compact message type."""
    if getattr(payload, "photo", None):
        return "photo"
    if getattr(payload, "video", None):
        return "video"
    if getattr(payload, "animation", None):
        return "animation"
    if getattr(payload, "sticker", None):
        return "sticker"
    if getattr(payload, "voice", None):
        return "voice"
    if getattr(payload, "document", None):
        return "document"
    if getattr(payload, "text", None) or getattr(payload, "caption", None):
        return "text"
    return "other"


def build_flud_message_payload(
    *,
    chat_id: int,
    message_id: int,
    sent_at: datetime | None,
    telegram_user_id: int | None,
    user_id: int | None,
    text: str | None = None,
    caption: str | None = None,
    message_type: str = "other",
    store_text_preview: bool = False,
    preview_max_chars: int = 120,
    has_photo: bool = False,
    has_video: bool = False,
    has_animation: bool = False,
    has_sticker: bool = False,
    has_voice: bool = False,
    has_document: bool = False,
    is_reply: bool = False,
    reply_to_message_id: int | None = None,
    source_author_hash: str | None = None,
) -> dict[str, Any]:
    """Build DB payload without storing full message text."""
    raw_text = text or caption or ""
    words = re.findall(r"\S+", raw_text)
    has_text = bool(raw_text)
    return {
        "chat_id": chat_id,
        "message_id": message_id,
        "telegram_user_id": telegram_user_id,
        "user_id": user_id,
        "sent_at": sent_at or datetime.now(timezone.utc),
        "message_type": message_type,
        "text_len": len(raw_text),
        "word_count": len(words),
        "text_preview": sanitize_preview(raw_text, preview_max_chars) if store_text_preview else None,
        "has_text": has_text,
        "has_photo": has_photo,
        "has_video": has_video,
        "has_animation": has_animation,
        "has_sticker": has_sticker,
        "has_voice": has_voice,
        "has_document": has_document,
        "is_reply": is_reply,
        "reply_to_message_id": reply_to_message_id,
        "source_author_hash": source_author_hash,
    }


def _fmt_date(value: date) -> str:
    return value.strftime("%d.%m.%Y")


def _reaction_display(value: str | None) -> str:
    if not value:
        return "🔥"
    if value.startswith("emoji:"):
        return value.split(":", 1)[1]
    if value.startswith("custom_emoji:"):
        return "💠"
    if value == "paid":
        return "⭐"
    return "🔥"


def _message_label(row: dict[str, Any]) -> str:
    preview = row.get("text_preview")
    if preview:
        return html.escape(str(preview))
    return MESSAGE_TYPE_LABELS.get(str(row.get("message_type") or "other"), "сообщение")


def _chat_slug(chat_id: int) -> str:
    raw = str(chat_id)
    if raw.startswith("-100"):
        return raw[4:]
    if raw.startswith("-"):
        return raw[1:]
    return raw


def telegram_message_link(chat_id: int, message_id: int, message_thread_id: int | None = None) -> str:
    """Build a Telegram private supergroup message link."""
    slug = _chat_slug(chat_id)
    if message_thread_id is not None:
        return f"https://t.me/c/{slug}/{message_thread_id}/{message_id}"
    return f"https://t.me/c/{slug}/{message_id}"


def _open_message_anchor(row: dict[str, Any], message_thread_id: int | None) -> str | None:
    chat_id = row.get("chat_id")
    message_id = row.get("message_id")
    if chat_id is None or message_id is None:
        return None
    link = telegram_message_link(int(chat_id), int(message_id), message_thread_id)
    return f'<a href="{html.escape(link, quote=True)}">открыть сообщение</a>'


def _append_message_link(label: str, row: dict[str, Any], *, include_links: bool, message_thread_id: int | None) -> str:
    if not include_links:
        return label
    anchor = _open_message_anchor(row, message_thread_id)
    return f"{label} · {anchor}" if anchor else label


def _text_nomination_label(row: dict[str, Any]) -> str:
    preview = row.get("text_preview")
    if preview:
        return html.escape(str(preview))
    message_type = str(row.get("message_type") or "text")
    if message_type == "text":
        return "текст без preview"
    label = MESSAGE_TYPE_LABELS.get(message_type, "сообщение")
    return f"caption без preview ({label})"


def format_top_messages(
    rows: list[dict[str, Any]],
    limit: int = 5,
    *,
    include_message_links: bool = False,
    message_thread_id: int | None = None,
) -> str:
    if not rows:
        return "Пока нет сообщений с реакциями."
    lines = []
    for idx, row in enumerate(rows[:limit], start=1):
        author = html.escape(str(row.get("author_name") or "участник"))
        reaction_count = int(row.get("reaction_count") or 0)
        reaction = _reaction_display(row.get("top_reaction_type"))
        label = _append_message_link(
            _message_label(row),
            row,
            include_links=include_message_links,
            message_thread_id=message_thread_id,
        )
        lines.append(f"{idx}. {reaction} {reaction_count} реакции — {author}, {label}")
    return "\n".join(lines)


def format_message_type_stats(type_counts: dict[str, int]) -> str:
    photo = type_counts.get("photo", 0)
    gif_video = type_counts.get("animation", 0) + type_counts.get("video", 0)
    stickers = type_counts.get("sticker", 0)
    voice = type_counts.get("voice", 0)
    return (
        "🖼 <b>Медиа:</b>\n"
        f"• Фото: {photo}\n"
        f"• GIF/видео: {gif_video}\n"
        f"• Стикеры: {stickers}\n"
        f"• Голосовые: {voice}"
    )


def format_activity_peak(daily_activity: list[dict[str, Any]], hourly_activity: list[dict[str, Any]]) -> tuple[str, str]:
    if daily_activity:
        top_day = daily_activity[0]
        active_day = f"{_fmt_date(top_day['day'])} ({top_day['count']})"
    else:
        active_day = "—"
    if hourly_activity:
        top_hour = hourly_activity[0]
        peak_hour = f"{int(top_hour['hour']):02d}:00 ({top_hour['count']})"
    else:
        peak_hour = "—"
    return active_day, peak_hour


async def _build_digest_text(
    *,
    title: str,
    period_start: date,
    period_end: date,
    chat_id: int,
    top_limit: int = 5,
    monthly: bool = False,
    include_message_links: bool = False,
    message_thread_id: int | None = None,
) -> str:
    summary = await FludRepo.get_summary(period_start, period_end, chat_id)
    message_count = int(summary.get("message_count") or 0)
    if message_count <= 0:
        return f"{title}\n\nЗа период {_fmt_date(period_start)} — {_fmt_date(period_end)} данных нет."

    type_counts = await FludRepo.get_message_type_counts(period_start, period_end, chat_id)
    daily_activity = await FludRepo.get_daily_activity(period_start, period_end, chat_id)
    hourly_activity = await FludRepo.get_hourly_activity(period_start, period_end, chat_id)
    top_messages = await FludRepo.get_top_reacted_messages(period_start, period_end, chat_id, top_limit)
    active_day, peak_hour = format_activity_peak(daily_activity, hourly_activity)

    lines = [
        title,
        "",
        f"💬 Сообщений: {message_count}",
        f"👥 Активных участников: {int(summary.get('active_users_count') or 0)}",
        f"🔥 Реакций: {int(summary.get('reaction_count') or 0)}",
        f"↩️ Ответов: {int(summary.get('reply_count') or 0)}",
        "",
        format_message_type_stats(type_counts),
        "",
        f"📅 Самый активный день: {active_day}",
        f"🕛 Пик активности: {peak_hour}",
        "",
    ]

    if monthly:
        media_top = await FludRepo.get_top_media_messages(period_start, period_end, chat_id, 1)
        text_top = await FludRepo.get_top_text_messages(period_start, period_end, chat_id, 1)
        sticker_top = [row for row in top_messages if row.get("message_type") == "sticker"][:1]
        meme = media_top[0] if media_top else (top_messages[0] if top_messages else None)
        lines.append("🏆 <b>Мем месяца:</b>")
        if meme:
            meme_label = _append_message_link(
                _message_label(meme),
                meme,
                include_links=include_message_links,
                message_thread_id=message_thread_id,
            )
            lines.append(
                f"{_reaction_display(meme.get('top_reaction_type'))} "
                f"{int(meme.get('reaction_count') or 0)} реакции — "
                f"{html.escape(str(meme.get('author_name') or 'участник'))}\n"
                f"{meme_label}"
            )
        else:
            lines.append("Пока без явного победителя.")
        phrase_label = (
            _append_message_link(
                _text_nomination_label(text_top[0]),
                text_top[0],
                include_links=include_message_links,
                message_thread_id=message_thread_id,
            )
            if text_top
            else "нет данных"
        )
        picture_label = (
            _append_message_link(
                _message_label(media_top[0]),
                media_top[0],
                include_links=include_message_links,
                message_thread_id=message_thread_id,
            )
            if media_top
            else "нет данных"
        )
        sticker_label = (
            _append_message_link(
                _message_label(sticker_top[0]),
                sticker_top[0],
                include_links=include_message_links,
                message_thread_id=message_thread_id,
            )
            if sticker_top
            else "нет данных"
        )
        lines.extend([
            "",
            "🎭 <b>Номинации:</b>",
            f"• Фраза месяца — {phrase_label}",
            f"• Картинка месяца — {picture_label}",
            f"• Стикер месяца — {sticker_label}",
            "",
            "Флудилка жива. Значит, отдел ещё не полностью превратился в календарь совещаний.",
        ])
    else:
        lines.extend([
            "🏆 <b>Топ сообщений:</b>",
            "",
            format_top_messages(
                top_messages,
                top_limit,
                include_message_links=include_message_links,
                message_thread_id=message_thread_id,
            ),
            "",
            "Флудилка выполнила свою социальную функцию: люди выжили, мемы размножились.",
        ])

    return "\n".join(lines)


async def build_weekly_digest_text(
    period_start: date,
    period_end: date,
    chat_id: int,
    top_limit: int = 5,
    *,
    include_message_links: bool = False,
    message_thread_id: int | None = None,
) -> str:
    return await _build_digest_text(
        title="🧃 <b>Пульс флудилки за неделю</b>",
        period_start=period_start,
        period_end=period_end,
        chat_id=chat_id,
        top_limit=top_limit,
        include_message_links=include_message_links,
        message_thread_id=message_thread_id,
    )


async def build_monthly_digest_text(
    year: int,
    month: int,
    chat_id: int,
    top_limit: int = 5,
    *,
    include_message_links: bool = False,
    message_thread_id: int | None = None,
) -> str:
    period_start = date(year, month, 1)
    next_month = date(year + (1 if month == 12 else 0), 1 if month == 12 else month + 1, 1)
    period_end = next_month - timedelta(days=1)
    month_name = MONTH_NAMES.get(month, f"{month:02d}.{year}")
    return await _build_digest_text(
        title=f"🧃 <b>Итоги флудилки за {month_name}</b>",
        period_start=period_start,
        period_end=period_end,
        chat_id=chat_id,
        top_limit=top_limit,
        monthly=True,
        include_message_links=include_message_links,
        message_thread_id=message_thread_id,
    )


async def build_manual_digest_text(
    period_start: date,
    period_end: date,
    chat_id: int,
    top_limit: int = 5,
    *,
    include_message_links: bool = False,
    message_thread_id: int | None = None,
) -> str:
    return await _build_digest_text(
        title=f"🧃 <b>Пульс флудилки: {_fmt_date(period_start)} — {_fmt_date(period_end)}</b>",
        period_start=period_start,
        period_end=period_end,
        chat_id=chat_id,
        top_limit=top_limit,
        include_message_links=include_message_links,
        message_thread_id=message_thread_id,
    )


def reaction_counts_from_list(reactions: list[Any]) -> dict[str, int]:
    """Convert aiogram reaction count objects to aggregate counts."""
    result: dict[str, int] = {}
    for item in reactions or []:
        reaction_type = reaction_type_to_key(getattr(item, "type", None) or item)
        count = int(getattr(item, "total_count", 0) or getattr(item, "count", 0) or 0)
        if reaction_type:
            result[reaction_type] = max(count, 0)
    return result


def reaction_delta(old_reactions: list[Any], new_reactions: list[Any]) -> dict[str, int]:
    """Compute aggregate reaction delta without storing actor identity."""
    old = Counter(reaction_type_to_key(item) for item in old_reactions or [])
    new = Counter(reaction_type_to_key(item) for item in new_reactions or [])
    delta: dict[str, int] = {}
    for key in set(old) | set(new):
        if key:
            value = new[key] - old[key]
            if value:
                delta[key] = value
    return delta


def reaction_type_to_key(reaction: Any) -> str | None:
    """Normalize Telegram reaction type into compact storage key."""
    if reaction is None:
        return None
    reaction_type = getattr(reaction, "type", None)
    if reaction_type == "emoji" or hasattr(reaction, "emoji"):
        emoji = getattr(reaction, "emoji", None)
        return f"emoji:{emoji}" if emoji else "emoji"
    if reaction_type == "custom_emoji" or hasattr(reaction, "custom_emoji_id"):
        custom_id = getattr(reaction, "custom_emoji_id", None)
        return f"custom_emoji:{custom_id}" if custom_id else "custom_emoji"
    if reaction_type == "paid":
        return "paid"
    if isinstance(reaction, str):
        return f"emoji:{reaction}"
    return str(reaction_type or reaction)
