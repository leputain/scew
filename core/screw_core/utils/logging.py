"""
Настройка логирования для проекта SCREW.

Предоставляет единый формат логов для всех модулей.
"""

import logging
import re
import sys


_TELEGRAM_BOT_TOKEN_RE = re.compile(r"bot\d+:[A-Za-z0-9_-]+|\b\d+:[A-Za-z0-9_-]{20,}\b")


class RedactingFormatter(logging.Formatter):
    """Formatter that removes secrets from rendered log records."""

    def format(self, record: logging.LogRecord) -> str:
        message = super().format(record)
        return _TELEGRAM_BOT_TOKEN_RE.sub("bot<redacted>", message)


def setup_logging(level: int = logging.INFO) -> None:
    """
    Настроить логирование для приложения.

    Args:
        level: Уровень логирования (по умолчанию INFO).
    """
    formatter = RedactingFormatter(
        fmt="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(formatter)

    root_logger = logging.getLogger()
    root_logger.setLevel(level)

    # Удаляем существующие хендлеры, чтобы не дублировать логи
    for h in root_logger.handlers[:]:
        root_logger.removeHandler(h)

    root_logger.addHandler(handler)
    logging.getLogger("httpx").setLevel(logging.WARNING)


def get_logger(name: str) -> logging.Logger:
    """
    Получить логгер для модуля.

    Args:
        name: Имя модуля (обычно __name__).

    Returns:
        Настроенный логгер.
    """
    return logging.getLogger(name)
