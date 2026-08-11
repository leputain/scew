"""Native aiogram poll router."""

from __future__ import annotations

import asyncio
from datetime import date, datetime, timedelta

from aiogram import Bot, Router
from aiogram.filters import Command
from aiogram.types import Message, PollAnswer

from apps.attendance_bot.app.config import get_attendance_group_id, get_poll_timezone
from apps.attendance_bot.app.scheduler import (
    remove_poll_auto_close_job,
    remove_poll_reminder_job,
    schedule_poll_auto_close,
    schedule_poll_reminder,
)
from apps.attendance_bot.app.services.group_preflight import check_attendance_group_access
from apps.attendance_bot.app.services import poll_runtime as poll_core
from apps.attendance_bot.app.utils.poll_payload import (
    build_aiogram_poll_payload,
    send_aiogram_poll_with_fallback,
)
from apps.attendance_bot.app.utils.private_chat_guard import MSG_PRIVATE_ONLY
from core.screw_core.models.enums import EventType, NotifyChannel
from core.screw_core.repos.poll_config_repo import PollConfigRepo
from core.screw_core.repos.users_repo import UsersRepo
from core.screw_core.services.attendance import AttendanceService
from core.screw_core.services.events import EventsService
from core.screw_core.services.notify import NotifyService
from core.screw_core.services.permissions import OZKO_DEPARTMENT_ID
from core.screw_core.services.polls import PollError, PollsService
from core.screw_core.utils.logging import get_logger
from core.screw_core.utils.timezone import get_tzinfo

logger = get_logger(__name__)
router = Router(name="attendance_poll")


async def _reject_if_group_chat(message: Message) -> bool:
    if message.chat.type == "private":
        return False
    logger.info(
        "poll_command_blocked_in_group: user=%s chat=%s command=%s",
        message.from_user.id if message.from_user else None,
        message.chat.id,
        (message.text or "")[:50],
    )
    await message.answer(MSG_PRIVATE_ONLY)
    return True


@router.message(Command("poll_open"))
async def cmd_poll_open(message: Message, bot: Bot) -> None:
    if not message.from_user:
        return
    if await _reject_if_group_chat(message):
        return

    is_mgr, _ = await poll_core.is_manager_or_admin(message.from_user.id)
    if not is_mgr:
        await message.answer("Эта команда доступна только менеджерам.")
        return

    group_id = get_attendance_group_id()
    if not group_id:
        await message.answer("ATTENDANCE_GROUP_ID не настроен.")
        return
    group_check = await check_attendance_group_access(bot)
    if not group_check.accessible:
        await message.answer(
            "Бот сейчас не видит группу для опросов или не имеет нужных прав.\n"
            "Добавьте бота в группу и выдайте права на сообщения, опросы и закрепление."
        )
        return

    poll_date = poll_core.get_local_date()

    try:
        if await PollConfigRepo.is_skip_date(poll_date):
            skip_info = await PollConfigRepo.get_skip_date_info(poll_date)
            reason = skip_info.get("reason", "") if skip_info else ""
            reason_text = f" ({reason})" if reason else ""
            await message.answer(
                f"📅 Сегодня ({poll_date.strftime('%d.%m')}) — пропуск{reason_text}.\n"
                "Опрос не создан. Создайте через админ-панель с подтверждением.",
            )
            logger.info("aiogram poll_open: skip date %s, reason=%s", poll_date, reason)
            return
    except Exception as exc:
        logger.warning("aiogram poll_open: ошибка проверки skip_date: %s", exc)

    existing = await PollsService.get_current_poll()
    if existing and existing.get("poll_date") == poll_date:
        await message.answer(
            f"Опрос на {poll_date.strftime('%d.%m.%Y')} уже существует.\n"
            "Используйте /poll_close для закрытия текущего опроса."
        )
        return

    poll_config = await poll_core._load_poll_config()
    poll_options, plain_options, api_kwargs = build_aiogram_poll_payload(poll_config.payload)
    duration = poll_config.duration_seconds
    reminder_minutes = poll_config.reminder_minutes

    poll_question = f"Явка за {poll_date.strftime('%d.%m.%Y')}"
    try:
        poll_message = await send_aiogram_poll_with_fallback(
            bot,
            logger,
            chat_id=group_id,
            question=poll_question,
            options=poll_options,
            plain_options=plain_options,
            api_kwargs=api_kwargs,
            is_anonymous=False,
            allows_multiple_answers=False,
        )
    except Exception as exc:
        logger.error("aiogram poll_open: не удалось опубликовать native poll: %s", exc)
        await message.answer(f"Не удалось опубликовать опрос в группе: {exc}")
        return

    telegram_poll_id = poll_message.poll.id if poll_message.poll else None
    if not telegram_poll_id:
        logger.error("aiogram poll_open: не удалось получить telegram_poll_id")
        await message.answer("Внутренняя ошибка: не получен poll_id от Telegram.")
        return

    poll_core._remember_poll_option_mapping(
        telegram_poll_id,
        poll_message.poll.options if poll_message.poll else [],
        poll_config.option_to_choice,
        poll_config.persistent_to_choice,
    )

    closes_at = poll_core.get_local_datetime() + timedelta(seconds=duration)
    result = await PollsService.create_daily_poll(
        poll_date=poll_date,
        chat_id=group_id,
        message_id=poll_message.message_id,
        telegram_poll_id=telegram_poll_id,
        closes_at=closes_at,
    )

    if not result.success:
        logger.error("aiogram poll_open: ошибка создания опроса в БД: %s", result.error)
        try:
            await bot.stop_poll(chat_id=group_id, message_id=poll_message.message_id)
        except Exception as exc:
            logger.debug("aiogram poll_open stop_poll cleanup failed: %s", exc)
        await message.answer(f"Ошибка создания опроса: {result.error}")
        return

    poll_id = result.poll_id
    logger.info(
        "aiogram poll_create: poll_id=%s, poll_date=%s, tg_poll_id=%s, message_id=%s, closes_at=%s",
        poll_id,
        poll_date,
        telegram_poll_id[:16],
        poll_message.message_id,
        closes_at,
    )

    await poll_core._pin_poll_message(
        bot=bot,
        chat_id=group_id,
        message_id=poll_message.message_id,
        poll_date=poll_date,
        notify_user_id=message.from_user.id,
    )
    await poll_core.send_absence_message(bot, group_id, poll_date)

    scheduler = poll_core.get_poll_scheduler()
    if scheduler:
        schedule_poll_auto_close(
            scheduler=scheduler,
            poll_id=poll_id,
            closes_at=closes_at,
            chat_id=group_id,
            message_id=poll_message.message_id,
            telegram_poll_id=telegram_poll_id,
        )
    else:
        logger.warning("aiogram poll_open: scheduler не настроен, автозакрытие не запланировано")

    reminder_info = ""
    if reminder_minutes and reminder_minutes * 60 < duration and scheduler:
        remind_at = closes_at - timedelta(seconds=reminder_minutes * 60)
        schedule_poll_reminder(
            scheduler=scheduler,
            poll_id=poll_id,
            remind_at=remind_at,
            poll_date=poll_date,
            telegram_poll_id=telegram_poll_id,
        )
        reminder_info = f"\nReminder за {reminder_minutes} мин до закрытия."

    await message.answer(
        f"Опрос #{poll_id} опубликован.\n\n"
        f"Дата: {poll_date.strftime('%d.%m.%Y')}\n"
        f"Закроется в {poll_core.format_time(closes_at)} "
        f"(через {duration // 60} мин).{reminder_info}"
    )


@router.message(Command("poll_close"))
async def cmd_poll_close(message: Message, bot: Bot) -> None:
    if not message.from_user:
        return
    if await _reject_if_group_chat(message):
        return

    is_mgr, _ = await poll_core.is_manager_or_admin(message.from_user.id)
    if not is_mgr:
        await message.answer("Эта команда доступна только менеджерам.")
        return

    result = await PollsService.close_current_poll()
    if not result.success:
        if result.error == PollError.POLL_NOT_FOUND:
            await message.answer("Нет открытых опросов для закрытия.")
        else:
            await message.answer(f"Ошибка закрытия опроса: {result.error}")
        return

    poll_id = result.poll_id
    poll_data = result.data
    scheduler = poll_core.get_poll_scheduler()

    if poll_id and scheduler:
        remove_poll_auto_close_job(scheduler, poll_id)
        remove_poll_reminder_job(scheduler, poll_id)

    if poll_data:
        chat_id = poll_data.get("chat_id")
        msg_id = poll_data.get("message_id")

        async def _cleanup_poll_artifacts() -> None:
            if chat_id and msg_id:
                await poll_core._stop_telegram_poll_safe(bot, chat_id, msg_id)
                await poll_core._unpin_poll_message(bot, chat_id, msg_id)
            if chat_id and poll_id:
                await poll_core._delete_or_edit_reminder(bot, chat_id, poll_id)

        try:
            await asyncio.wait_for(_cleanup_poll_artifacts(), timeout=30)
        except asyncio.TimeoutError:
            logger.warning("aiogram poll_close: cleanup timeout poll_id=%s", poll_id)
        except Exception as exc:
            logger.warning("aiogram poll_close: cleanup error poll_id=%s: %s", poll_id, exc)

    await message.answer(f"Опрос #{poll_id} закрыт.")
    logger.info("aiogram poll_close: poll_id=%s by telegram_id=%s", poll_id, message.from_user.id)

    tz = get_tzinfo(get_poll_timezone())
    fallback_date = datetime.now(tz).date()
    poll_date = poll_data.get("poll_date") if poll_data else None
    check_date = poll_date if isinstance(poll_date, date) else fallback_date
    await asyncio.sleep(10)
    try:
        await asyncio.wait_for(
            NotifyService.run_post_poll_checks(
                bot,
                check_date,
                department_id=OZKO_DEPARTMENT_ID,
                ignore_day_off=True,
                poll_id=poll_id,
            ),
            timeout=120,
        )
    except asyncio.TimeoutError:
        logger.error("aiogram poll_close: post-poll checks timeout poll_id=%s", poll_id)
    except Exception as exc:
        logger.exception("aiogram poll_close: post-poll checks failed poll_id=%s: %s", poll_id, exc)
    await poll_core.send_poll_close_summary(
        bot,
        poll_data,
        department_id=OZKO_DEPARTMENT_ID,
    )
    await poll_core.send_department_lists_summary(bot, poll_data)


@router.message(Command("poll_status"))
async def cmd_poll_status(message: Message) -> None:
    if not message.from_user:
        return
    if await _reject_if_group_chat(message):
        return

    is_mgr, _ = await poll_core.is_manager_or_admin(message.from_user.id)
    if not is_mgr:
        await message.answer("Эта команда доступна только менеджерам.")
        return

    poll = await PollsService.get_current_poll()
    if not poll:
        await message.answer("Нет открытых опросов.")
        return

    poll_date = poll.get("poll_date", poll_core.get_local_date())
    poll_id = poll.get("id")
    published_at = poll.get("published_at")
    closes_at = poll.get("closes_at")
    telegram_poll_id = poll.get("telegram_poll_id")

    text = (
        f"Текущий опрос #{poll_id}\n\n"
        f"Дата: {poll_date.strftime('%d.%m.%Y')}\n"
        "Статус: открыт\n"
        "Тип: native Telegram poll\n"
    )

    if published_at:
        text += f"Опубликован: {published_at.strftime('%H:%M')}\n"
    if closes_at:
        text += f"Закроется в: {closes_at.strftime('%H:%M')}\n"
    if telegram_poll_id:
        text += f"Telegram Poll ID: {telegram_poll_id[:16]}..."

    await message.answer(text)


@router.poll_answer()
async def handle_poll_answer(poll_answer: PollAnswer) -> None:
    telegram_poll_id = poll_answer.poll_id
    telegram_id = poll_answer.user.id if poll_answer.user else None

    if not telegram_id:
        logger.warning("aiogram poll_answer без user: poll_id=%s", telegram_poll_id)
        return

    poll = await PollsService.get_poll_by_telegram_id(telegram_poll_id)
    if not poll:
        logger.debug("aiogram poll_answer для неизвестного poll_id=%s", telegram_poll_id)
        return

    poll_id = poll.get("id")
    poll_date = poll.get("poll_date", poll_core.get_local_date())

    if poll.get("status") != "open":
        closed_at = poll.get("closed_at")
        if closed_at:
            from datetime import timezone

            closed_at_utc = (
                closed_at.astimezone(timezone.utc)
                if closed_at.tzinfo is not None
                else closed_at.replace(tzinfo=timezone.utc)
            )
            age = (datetime.now(timezone.utc) - closed_at_utc).total_seconds()
            if age <= 60:
                logger.info(
                    "aiogram poll_answer: grace period poll_id=%s closed %ds ago",
                    poll_id,
                    int(age),
                )
            else:
                logger.debug("aiogram poll_answer для закрытого poll_id=%s (%ds)", poll_id, int(age))
                return
        else:
            logger.debug("aiogram poll_answer для закрытого poll_id=%s", poll_id)
            return

    persistent_ids = poll_core._get_extra_field(poll_answer, "option_persistent_ids") or []
    if not poll_answer.option_ids and not persistent_ids:
        logger.debug("aiogram poll_answer: telegram_id=%s отозвал голос poll_id=%s", telegram_id, poll_id)
        return

    choice, choice_source = await poll_core._resolve_choice_from_poll_answer(poll_answer)
    if not choice:
        logger.warning(
            "aiogram poll_answer: неизвестный выбор poll_id=%s option_ids=%s persistent_ids=%s",
            poll_id,
            poll_answer.option_ids,
            persistent_ids,
        )
        return

    user = await UsersRepo.get_by_telegram_id(telegram_id)
    if not user:
        dedup_key = f"poll:{poll_date}:CONFLICT_STATUS:{telegram_id}"
        await EventsService.create_event(
            event_type=EventType.CONFLICT_STATUS,
            dedup_key=dedup_key,
            event_date=poll_date,
            payload={
                "telegram_id": telegram_id,
                "poll_id": poll_id,
                "username": poll_answer.user.username if poll_answer.user else None,
            },
            notify_channel=NotifyChannel.NONE,
        )
        logger.warning("aiogram poll_answer: незарегистрированный telegram_id=%s poll_id=%s", telegram_id, poll_id)
        return

    if user.get("status") != "active":
        logger.debug("aiogram poll_answer от неактивного telegram_id=%s", telegram_id)
        return

    user_id = user.get("id")
    await AttendanceService.record_choice(
        user_id=user_id,
        mark_date=poll_date,
        choice=choice,
        poll_run_id=poll_id,
    )

    logger.info(
        "aiogram poll_answer: user_id=%s telegram_id=%s choice=%s poll_id=%s source=%s",
        user_id,
        telegram_id,
        choice.value,
        poll_id,
        choice_source,
    )
