"""
Пул соединений с PostgreSQL на базе asyncpg.

Предоставляет функции для создания и управления пулом соединений.
Включает retry с exponential backoff при подключении к БД.
"""

import asyncio

import asyncpg

from core.screw_core.config.settings import get_db_settings
from core.screw_core.utils.logging import get_logger

logger = get_logger(__name__)

# Глобальный пул соединений
_pool: asyncpg.Pool | None = None


async def create_pool(max_retries: int = 10, base_delay: float = 2.0) -> asyncpg.Pool:
    """
    Создать пул соединений с БД.

    При неудаче — retry с exponential backoff (до max_retries попыток).
    Задержка растёт экспоненциально: 2, 4, 8, 16, 32, 60, 60... секунд.

    Args:
        max_retries: Максимальное число попыток подключения.
        base_delay: Начальная задержка в секундах.

    Returns:
        Пул соединений asyncpg.

    Raises:
        Exception: Если все попытки подключения исчерпаны.
    """
    global _pool
    if _pool is not None:
        return _pool

    settings = get_db_settings()
    logger.info("Подключение к БД: %s:%s/%s", settings.host, settings.port, settings.database)

    for attempt in range(1, max_retries + 1):
        try:
            _pool = await asyncpg.create_pool(
                host=settings.host,
                port=settings.port,
                database=settings.database,
                user=settings.user,
                password=settings.password,
                min_size=1,
                max_size=10,
            )
            logger.info("Пул соединений с БД создан (попытка %d)", attempt)
            return _pool
        except (OSError, asyncpg.PostgresError, asyncpg.InterfaceError, ConnectionRefusedError) as e:
            delay = min(base_delay * (2 ** (attempt - 1)), 60.0)
            if attempt == max_retries:
                logger.error(
                    "БД недоступна после %d попыток: %s — завершаем",
                    max_retries, e,
                )
                raise
            logger.warning(
                "БД недоступна (попытка %d/%d): %s — повтор через %.0f сек",
                attempt, max_retries, e, delay,
            )
            await asyncio.sleep(delay)

    # Недостижимо, но для типизации
    raise RuntimeError("Не удалось подключиться к БД")


async def get_pool() -> asyncpg.Pool:
    """
    Получить существующий пул соединений.

    Returns:
        Пул соединений asyncpg.

    Raises:
        RuntimeError: Если пул не был создан.
    """
    if _pool is None:
        raise RuntimeError("Пул соединений не инициализирован. Вызовите create_pool() при старте.")
    return _pool


async def close_pool() -> None:
    """Закрыть пул соединений."""
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None
        logger.info("Пул соединений с БД закрыт")


async def check_connection() -> bool:
    """
    Проверить соединение с БД.

    Returns:
        True, если соединение успешно.

    Raises:
        Exception: При ошибке подключения.
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        result = await conn.fetchval("SELECT 1")
        logger.info("Проверка соединения с БД: OK")
        return result == 1
