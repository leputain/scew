"""Store report delivery state before performing a non-idempotent Telegram send."""

from aiogram.exceptions import TelegramAPIError, TelegramNetworkError, TelegramServerError

from core.screw_core.db.tx import acquire_connection
from core.screw_core.services.telegram_delivery import send_system_message
from core.screw_core.utils.logging import get_logger

logger = get_logger(__name__)


async def send_tracked_report(bot, delivery_key: str, **kwargs) -> str:
    async with acquire_connection() as conn:
        claimed = await conn.fetchval(
            """
            INSERT INTO screw.telegram_report_deliveries (delivery_key, state)
            VALUES ($1, 'sending')
            ON CONFLICT (delivery_key) DO UPDATE SET state = 'sending', updated_at = NOW()
            WHERE telegram_report_deliveries.state = 'failed'
            RETURNING state
            """,
            delivery_key,
        )
        if not claimed:
            return await conn.fetchval(
                """
                SELECT CASE WHEN state = 'sending' THEN 'uncertain' ELSE state END
                FROM screw.telegram_report_deliveries WHERE delivery_key = $1
                """,
                delivery_key,
            )

    try:
        await send_system_message(bot, strict_rich_fallback=True, **kwargs)
        state = "sent"
    except Exception as exc:
        definitive = isinstance(exc, TelegramAPIError) and not isinstance(
            exc, (TelegramNetworkError, TelegramServerError),
        )
        state = "failed" if definitive else "uncertain"
        logger.warning("telegram report state=%s error=%s", state, type(exc).__name__)

    async with acquire_connection() as conn:
        await conn.execute(
            """
            UPDATE screw.telegram_report_deliveries SET state = $2, updated_at = NOW()
            WHERE delivery_key = $1
            """,
            delivery_key,
            state,
        )
    return state


async def count_unresolved_report_deliveries() -> int:
    async with acquire_connection() as conn:
        return await conn.fetchval(
            """
            SELECT COUNT(*) FROM screw.telegram_report_deliveries
            WHERE state = 'uncertain' OR (state = 'sending' AND updated_at < NOW() - INTERVAL '5 minutes')
            """
        )
