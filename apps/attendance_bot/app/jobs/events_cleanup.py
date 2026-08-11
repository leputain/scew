"""
Периодическая очистка старых диагностических событий.

Удаляет события старше 14 дней:
- CONFLICT_STATUS (конфликты статусов)
- Legacy типы из старых записей (MARKER_OUT_OF_RANGE, UNMAPPED_USER)

Запуск: еженедельно по воскресеньям в 03:00 MSK
"""

from datetime import datetime, timedelta

from core.screw_core.repos.events_repo import EventsRepo
from core.screw_core.utils.logging import get_logger
from core.screw_core.utils.timezone import get_tzinfo

logger = get_logger(__name__)

# Типы событий для очистки (включая legacy из старых записей БД)
CLEANUP_EVENT_TYPES = ["MARKER_OUT_OF_RANGE", "UNMAPPED_USER", "CONFLICT_STATUS"]

# Возраст событий для удаления (дни)
CLEANUP_AGE_DAYS = 14


async def job_cleanup_diagnostic_events() -> None:
    """Очистить старые диагностические события."""
    tz = get_tzinfo("Europe/Moscow")
    cutoff_date = (datetime.now(tz) - timedelta(days=CLEANUP_AGE_DAYS)).date()

    try:
        deleted_count = await EventsRepo.delete_old_diagnostic_events(
            event_types=CLEANUP_EVENT_TYPES,
            before_date=cutoff_date,
        )
        logger.info(
            "events_cleanup: удалено %d диагностических событий старше %s",
            deleted_count,
            cutoff_date,
        )
    except Exception as e:
        logger.exception("events_cleanup: ошибка очистки — %s", e)
