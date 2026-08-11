"""Aiogram-only entrypoint for attendance_bot."""

import asyncio

from apps.attendance_bot.app.aiogram_app.main import run_aiogram_runtime
from core.screw_core.utils.logging import get_logger, setup_logging

logger = get_logger(__name__)


def main() -> None:
    """Start the only supported Telegram runtime."""
    setup_logging()
    logger.info("Запуск attendance_bot runtime=aiogram")
    asyncio.run(run_aiogram_runtime())


if __name__ == "__main__":
    main()
