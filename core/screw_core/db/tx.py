"""
Хелперы для работы с транзакциями и соединениями БД.

Предоставляет контекстные менеджеры для транзакций.
"""

from contextlib import asynccontextmanager
from typing import AsyncIterator

import asyncpg

from core.screw_core.db.pool import get_pool


@asynccontextmanager
async def acquire_connection() -> AsyncIterator[asyncpg.Connection]:
    """
    Получить соединение из пула.

    Yields:
        Соединение asyncpg.

    Example:
        async with acquire_connection() as conn:
            await conn.fetch("SELECT * FROM screw.users")
    """
    pool = await get_pool()
    async with pool.acquire(timeout=10) as conn:
        yield conn


def parse_rowcount(result: str) -> int:
    """
    Разобрать строку результата asyncpg execute() вида 'DELETE N' или 'UPDATE N'.

    asyncpg.Connection.execute() возвращает строку команды, а не число.
    Эта функция надёжно извлекает количество затронутых строк.

    Args:
        result: Строка вида 'DELETE 5', 'UPDATE 1', 'INSERT 0 3' и т.д.

    Returns:
        Количество строк (последнее число в строке), или 0 при ошибке.
    """
    try:
        return int(result.split()[-1])
    except (AttributeError, IndexError, ValueError):
        return 0


@asynccontextmanager
async def transaction() -> AsyncIterator[asyncpg.Connection]:
    """
    Выполнить операции в транзакции.

    При исключении транзакция откатывается автоматически.

    Yields:
        Соединение asyncpg с открытой транзакцией.

    Example:
        async with transaction() as conn:
            await conn.execute("INSERT INTO screw.users ...")
            await conn.execute("INSERT INTO screw.events ...")
    """
    pool = await get_pool()
    async with pool.acquire(timeout=10) as conn:
        async with conn.transaction():
            yield conn
