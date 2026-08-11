"""
Общие хелперы для APScheduler (start/shutdown).

Переиспользуется в scheduler.py обоих ботов.
"""

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from core.screw_core.utils.logging import get_logger

logger = get_logger(__name__)


def start_scheduler(scheduler: AsyncIOScheduler | None) -> None:
    """
    Запустить scheduler.

    Args:
        scheduler: Экземпляр AsyncIOScheduler или None.
    """
    if scheduler is None:
        return

    if not scheduler.running:
        scheduler.start()
        logger.info("scheduler: запущен")


def shutdown_scheduler(scheduler: AsyncIOScheduler | None) -> None:
    """
    Остановить scheduler (graceful).

    Args:
        scheduler: Экземпляр AsyncIOScheduler или None.
    """
    if scheduler is None:
        return

    if scheduler.running:
        scheduler.shutdown(wait=True)
        logger.info("scheduler: остановлен")
