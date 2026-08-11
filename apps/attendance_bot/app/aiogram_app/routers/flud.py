"""Aiogram router for privacy-preserving flud chat pulse."""

from __future__ import annotations

import html

from aiogram import F, Router
from aiogram.filters import Command, CommandObject
from aiogram.types import Message, MessageReactionCountUpdated, MessageReactionUpdated

from apps.attendance_bot.app.config import (
    get_flud_chat_id,
    get_flud_message_thread_id,
    get_flud_reactions_enabled,
    get_flud_store_text_preview,
    get_flud_text_preview_max_chars,
    get_flud_top_limit,
    is_flud_message_thread_id_invalid,
    is_flud_enabled,
)
from apps.attendance_bot.app.jobs.flud_digest import (
    build_current_weekly_flud_digest,
    build_monthly_flud_digest_for_month,
    current_month_period,
    parse_flud_month_arg,
    send_monthly_flud_digest,
    send_weekly_flud_digest,
)
from apps.attendance_bot.app.utils.permissions import get_permissions
from core.screw_core.repos.flud_repo import FludRepo
from core.screw_core.repos.users_repo import UsersRepo
from core.screw_core.services.flud_stats import (
    build_flud_message_payload,
    classify_message,
    format_top_messages,
    reaction_counts_from_list,
    reaction_delta,
)
from core.screw_core.utils.logging import get_logger

logger = get_logger(__name__)
router = Router(name="attendance_flud")


def _is_flud_chat(chat_id: int | None) -> bool:
    flud_chat_id = get_flud_chat_id()
    return bool(is_flud_enabled() and flud_chat_id and chat_id == flud_chat_id)


def _is_flud_message(message: Message) -> bool:
    if not _is_flud_chat(message.chat.id):
        return False
    if is_flud_message_thread_id_invalid():
        logger.warning("flud: invalid FLUD_MESSAGE_THREAD_ID, skip collection")
        return False
    thread_id = get_flud_message_thread_id()
    if thread_id is None:
        return True
    return getattr(message, "message_thread_id", None) == thread_id


async def _require_admin_private(message: Message) -> bool:
    if message.chat.type != "private":
        await message.answer("Эти команды доступны только в личном чате с ботом.")
        return False
    if not message.from_user:
        return False
    perms = await get_permissions(message.from_user.id)
    if not perms.is_admin:
        await message.answer("⛔ Недостаточно прав.")
        return False
    return True


@router.message(Command("flud_week"))
async def cmd_flud_week(message: Message) -> None:
    if not await _require_admin_private(message):
        return
    text, _, _ = await build_current_weekly_flud_digest()
    await message.answer(text or "FLUD_CHAT_ID не настроен.", parse_mode="HTML")


@router.message(Command("flud_month"))
async def cmd_flud_month(message: Message, command: CommandObject) -> None:
    if not await _require_admin_private(message):
        return
    month_arg = parse_flud_month_arg(command.args)
    if month_arg is None:
        await message.answer("Формат: /flud_month 2026-06, /flud_month 06.2026 или /flud_month июнь")
        return
    year, month = month_arg
    text, _, _, _, _ = await build_monthly_flud_digest_for_month(year, month)
    await message.answer(text or "FLUD_CHAT_ID не настроен.", parse_mode="HTML")


@router.message(Command("flud_top"))
async def cmd_flud_top(message: Message) -> None:
    if not await _require_admin_private(message):
        return
    flud_chat_id = get_flud_chat_id()
    if not flud_chat_id:
        await message.answer("FLUD_CHAT_ID не настроен.")
        return
    _, _, period_start, period_end = current_month_period()
    top = await FludRepo.get_top_reacted_messages(period_start, period_end, flud_chat_id, get_flud_top_limit())
    await message.answer(
        "🏆 <b>Топ сообщений флудилки</b>\n\n" + format_top_messages(top, get_flud_top_limit()),
        parse_mode="HTML",
    )


@router.message(Command("flud_publish_week"))
async def cmd_flud_publish_week(message: Message) -> None:
    if not await _require_admin_private(message):
        return
    created_by = None
    if message.from_user:
        user = await UsersRepo.get_by_telegram_id(message.from_user.id)
        created_by = int(user["id"]) if user and user.get("id") else None
    success, status, _ = await send_weekly_flud_digest(message.bot, created_by=created_by)
    await message.answer("✅ Дайджест опубликован в чат руководства." if success else f"❌ Не опубликовано: {html.escape(status)}")


@router.message(Command("flud_publish_month"))
async def cmd_flud_publish_month(message: Message, command: CommandObject) -> None:
    if not await _require_admin_private(message):
        return
    month_arg = parse_flud_month_arg(command.args)
    if month_arg is None:
        await message.answer("Формат: /flud_publish_month 2026-06, /flud_publish_month 06.2026 или /flud_publish_month июнь")
        return
    year, month = month_arg
    created_by = None
    if message.from_user:
        user = await UsersRepo.get_by_telegram_id(message.from_user.id)
        created_by = int(user["id"]) if user and user.get("id") else None
    success, status, _ = await send_monthly_flud_digest(
        message.bot,
        created_by=created_by,
        year=year,
        month=month,
    )
    await message.answer(
        f"✅ Дайджест за {month:02d}.{year} опубликован в чат руководства."
        if success
        else f"❌ Не опубликовано: {html.escape(status)}"
    )


@router.message(F.chat.type.in_({"group", "supergroup"}))
async def collect_flud_message(message: Message) -> None:
    if not _is_flud_message(message):
        return
    if message.from_user and message.from_user.is_bot:
        return

    telegram_user_id = message.from_user.id if message.from_user else None
    user_id = None
    if telegram_user_id:
        user = await UsersRepo.get_by_telegram_id(telegram_user_id)
        user_id = int(user["id"]) if user and user.get("id") else None

    message_type = classify_message(message)
    payload = build_flud_message_payload(
        chat_id=message.chat.id,
        message_id=message.message_id,
        sent_at=message.date,
        telegram_user_id=telegram_user_id,
        user_id=user_id,
        text=message.text,
        caption=message.caption,
        message_type=message_type,
        store_text_preview=get_flud_store_text_preview(),
        preview_max_chars=get_flud_text_preview_max_chars(),
        has_photo=bool(message.photo),
        has_video=bool(message.video),
        has_animation=bool(message.animation),
        has_sticker=bool(message.sticker),
        has_voice=bool(message.voice),
        has_document=bool(message.document),
        is_reply=message.reply_to_message is not None,
        reply_to_message_id=message.reply_to_message.message_id if message.reply_to_message else None,
    )
    await FludRepo.upsert_message(**payload)


@router.message_reaction()
async def collect_flud_reaction(event: MessageReactionUpdated) -> None:
    if not get_flud_reactions_enabled() or not _is_flud_chat(event.chat.id):
        return
    if not await FludRepo.message_exists(event.chat.id, event.message_id):
        return
    delta = reaction_delta(event.old_reaction, event.new_reaction)
    await FludRepo.apply_reaction_delta(event.chat.id, event.message_id, delta)


@router.message_reaction_count()
async def collect_flud_reaction_count(event: MessageReactionCountUpdated) -> None:
    if not get_flud_reactions_enabled() or not _is_flud_chat(event.chat.id):
        return
    if not await FludRepo.message_exists(event.chat.id, event.message_id):
        return
    counts = reaction_counts_from_list(event.reactions)
    await FludRepo.upsert_reactions(event.chat.id, event.message_id, counts)
