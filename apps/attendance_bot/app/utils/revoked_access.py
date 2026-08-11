from __future__ import annotations

from datetime import date, datetime


from core.screw_core.models.enums import EventType, NotifyChannel
from core.screw_core.repos.events_repo import EventsRepo
from core.screw_core.repos.users_repo import UsersRepo
from core.screw_core.utils.logging import get_logger
from core.screw_core.utils.timezone import get_tzinfo

logger = get_logger(__name__)


async def send_revoked_notice_if_needed(bot, telegram_id: int) -> bool:
    """
    Send one-time revoked notice to user using events dedup.

    Returns:
        True if notice was sent now.
    """
    dedup_key = f"revoked_notice_sent:{telegram_id}"
    event_id = await EventsRepo.create_event(
        event_type=EventType.ADMIN_ACTION,
        dedup_key=dedup_key,
        user_id=None,
        event_date=date.today(),
        payload={"kind": "REVOKED_NOTICE_SENT", "telegram_id": telegram_id},
        notify_channel=NotifyChannel.NONE,
    )
    if not event_id:
        return False

    try:
        await bot.send_message(
            telegram_id,
            "🚫 Доступ к боту отозван. Обратитесь к администратору.",
        )
        return True
    except Exception as e:
        logger.warning("revoked_notice: failed to send to %s: %s", telegram_id, e)
        return False


async def notify_admins_about_revoked_access(bot, payload: dict) -> None:
    """Notify admins that a terminated user tried to use the bot."""
    if not bot:
        return

    admin_ids = await UsersRepo.list_admin_telegram_ids()
    if not admin_ids:
        return

    display_name = payload.get("full_name") or "Без ФИО"
    username = payload.get("username")
    who = f"{display_name}"
    if username:
        who += f" (@{username})"
    who += f", id={payload.get('telegram_id')}"

    text = (payload.get("text") or "").strip()
    if not text:
        text = "(без текста)"
    if len(text) > 300:
        text = text[:300] + "…"

    ts = payload.get("ts")
    ts_display = "—"
    if ts:
        try:
            dt = datetime.fromisoformat(ts)
            ts_display = dt.astimezone(get_tzinfo("Europe/Moscow")).strftime("%d.%m.%Y %H:%M")
        except ValueError:
            ts_display = ts

    alert = (
        "⚠️ Уволенный пользователь пишет боту\n"
        f"{who}\n"
        f"Текст: {text}\n"
        f"Время: {ts_display}"
    )

    for admin_id in admin_ids:
        try:
            await bot.send_message(chat_id=admin_id, text=alert)
        except Exception as exc:
            logger.warning("revoked_guard: notify admin %s failed: %s", admin_id, exc)
