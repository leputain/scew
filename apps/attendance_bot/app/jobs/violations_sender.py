"""
Отправка нарушений в violations chat.

Вызывается из scheduler (cron job daily_violations_check).
Отправляет сводку NO_CHECKIN нарушений в VIOLATIONS_OZKO_CHAT_ID.
"""

from datetime import date

from core.screw_core.config.departments import OZKO_DEPARTMENT_ID
from core.screw_core.services.notify import NotifyService, get_violations_ozko_chat_id
from core.screw_core.services.telegram_delivery import send_system_message
from core.screw_core.utils.logging import get_logger

logger = get_logger(__name__)


async def send_violations_to_chat(bot, check_date: date | None = None) -> None:
    """
    Отправить сводку нарушений за день в violations chat.

    Args:
        bot: Экземпляр бота для отправки сообщений.
        check_date: Дата для сводки (None = сегодня).
    """
    from datetime import datetime
    from core.screw_core.utils.timezone import get_tzinfo

    if not NotifyService.is_violations_notify_configured():
        logger.debug("violations_sender: отключено или не настроено")
        return

    chat_id = get_violations_ozko_chat_id()
    if not chat_id:
        logger.warning("violations_sender: VIOLATIONS_OZKO_CHAT_ID не задан")
        return

    if check_date is None:
        tz = get_tzinfo("Europe/Moscow")
        check_date = datetime.now(tz).date()

    text, event_ids = await NotifyService.build_violations_summary_for_date(
        check_date,
        department_id=OZKO_DEPARTMENT_ID,
    )
    if not text:
        logger.debug("violations_sender: нет нарушений за %s", check_date)
        return

    try:
        await send_system_message(
            bot,
            chat_id=chat_id,
            html_text=text,
            reaction_emoji="✅",
            delivery_mode="html",
        )
        for eid in event_ids:
            await NotifyService.mark_violation_sent(eid)
        logger.info("violations_sender: сводка отправлена (%d нарушений)", len(event_ids))
    except Exception as e:
        for eid in event_ids:
            await NotifyService.mark_violation_error(eid, str(e)[:200])
        logger.warning("violations_sender: ошибка отправки: %s", str(e)[:100])
