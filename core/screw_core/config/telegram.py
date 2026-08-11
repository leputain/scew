"""
Общие утилиты для парсинга Telegram ID и ролей из переменных окружения.

Переиспользуется в config.py обоих ботов.
"""

import os


def parse_telegram_ids(value: str) -> list[int]:
    """
    Распарсить строку с ID через запятую.

    Args:
        value: Строка вида "123,456,789".

    Returns:
        Список int ID.
    """
    if not value.strip():
        return []
    result = []
    for part in value.split(","):
        part = part.strip()
        if part:
            try:
                result.append(int(part))
            except ValueError:
                pass
    return result


def get_admin_telegram_ids() -> list[int]:
    """Получить список Telegram ID админов из ADMIN_TELEGRAM_IDS."""
    return parse_telegram_ids(os.getenv("ADMIN_TELEGRAM_IDS", ""))


def get_manager_ozko_telegram_ids() -> list[int]:
    """Получить список Telegram ID менеджеров ОЗКО из MANAGER_OZKO_TELEGRAM_IDS."""
    return parse_telegram_ids(os.getenv("MANAGER_OZKO_TELEGRAM_IDS", ""))
