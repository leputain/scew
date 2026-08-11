"""Aiogram polling runtime for attendance_bot."""

from __future__ import annotations

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.exceptions import TelegramNetworkError, TelegramUnauthorizedError
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import ErrorEvent

from apps.attendance_bot.app.aiogram_app.middlewares import (
    AiogramIngressMiddleware,
    AiogramSecurityPolicyMiddleware,
)
from apps.attendance_bot.app.aiogram_app.routers.admin import router as admin_router
from apps.attendance_bot.app.aiogram_app.routers.admin_panel import router as admin_panel_router
from apps.attendance_bot.app.aiogram_app.routers.chat_member import router as chat_member_router
from apps.attendance_bot.app.aiogram_app.routers.fallback import router as fallback_router
from apps.attendance_bot.app.aiogram_app.routers.flud import router as flud_router
from apps.attendance_bot.app.aiogram_app.routers.poll import router as poll_router
from apps.attendance_bot.app.aiogram_app.routers.report import router as report_router
from apps.attendance_bot.app.aiogram_app.routers.start import router as start_router
from apps.attendance_bot.app.aiogram_app.routers.user_status import router as user_status_router
from apps.attendance_bot.app.config import get_bot_token, get_proxy_url
from apps.attendance_bot.app.lifecycle import attendance_shutdown, attendance_startup
from apps.attendance_bot.app.utils.log_redaction import redact_text
from core.screw_core.utils.logging import get_logger, setup_logging

logger = get_logger(__name__)

ALLOWED_UPDATES = [
    "message",
    "callback_query",
    "chat_member",
    "my_chat_member",
    "poll_answer",
    "message_reaction",
    "message_reaction_count",
]
AIOGRAM_ROUTERS = (
    start_router,
    poll_router,
    report_router,
    admin_router,
    admin_panel_router,
    flud_router,
    chat_member_router,
    user_status_router,
)
AIOGRAM_FALLBACK_ROUTER = fallback_router

async def _aiogram_error_handler(event: ErrorEvent) -> bool:
    error_text = redact_text(event.exception)
    if isinstance(event.exception, TelegramNetworkError):
        logger.warning("aiogram network error: %s", error_text)
        return True
    if isinstance(event.exception, TelegramUnauthorizedError):
        logger.error("aiogram unauthorized: проверьте ATTENDANCE_BOT_TOKEN")
        return True
    logger.exception("aiogram unhandled error: %s", error_text)
    return True


def create_aiogram_dispatcher() -> Dispatcher:
    dp = Dispatcher(storage=MemoryStorage())

    dp.update.outer_middleware(AiogramIngressMiddleware())
    dp.update.middleware(AiogramSecurityPolicyMiddleware())
    for router in AIOGRAM_ROUTERS:
        dp.include_router(router)
    dp.include_router(AIOGRAM_FALLBACK_ROUTER)
    dp.errors.register(_aiogram_error_handler)
    return dp


def create_aiogram_bot() -> Bot:
    proxy_url = get_proxy_url()
    session = AiohttpSession(proxy=proxy_url or None, limit=100)
    return Bot(
        token=get_bot_token(),
        session=session,
        default=DefaultBotProperties(),
    )


async def run_aiogram_runtime() -> None:
    setup_logging()
    dp = create_aiogram_dispatcher()
    bot = create_aiogram_bot()

    logger.info("aiogram runtime: startup")
    lifecycle_started = False
    try:
        lifecycle_started = True
        await attendance_startup(bot, application=None)
        await bot.delete_webhook(drop_pending_updates=False)
        await dp.start_polling(
            bot,
            allowed_updates=ALLOWED_UPDATES,
            polling_timeout=20,
            tasks_concurrency_limit=32,
        )
    finally:
        logger.info("aiogram runtime: shutdown")
        if lifecycle_started:
            await attendance_shutdown()
        await bot.session.close()
