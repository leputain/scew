"""
Общие настройки приложения.

Загружает конфигурацию из переменных окружения.
"""

import os


def get_env(key: str, default: str | None = None) -> str:
    """
    Получить значение переменной окружения.

    Args:
        key: Имя переменной окружения.
        default: Значение по умолчанию (если None — переменная обязательна).

    Returns:
        Значение переменной окружения.

    Raises:
        ValueError: Если переменная не задана и нет значения по умолчанию.
    """
    value = os.getenv(key, default)
    if value is None:
        raise ValueError(f"Переменная окружения {key} не задана")
    return value


class DatabaseSettings:
    """Настройки подключения к PostgreSQL."""

    def __init__(self) -> None:
        self.host = get_env("POSTGRES_HOST", "localhost")
        self.port = int(get_env("POSTGRES_PORT", "5432"))
        self.database = get_env("POSTGRES_DB", "screw")
        self.user = get_env("POSTGRES_USER", "screw")
        self.password = get_env("POSTGRES_PASSWORD")

    @property
    def dsn(self) -> str:
        """Строка подключения к БД в формате DSN."""
        return (
            f"postgresql://{self.user}:{self.password}"
            f"@{self.host}:{self.port}/{self.database}"
        )


# Синглтон настроек БД (создаётся при первом обращении)
_db_settings: DatabaseSettings | None = None


def get_db_settings() -> DatabaseSettings:
    """
    Получить настройки БД.

    Returns:
        Экземпляр DatabaseSettings с загруженной конфигурацией.
    """
    global _db_settings
    if _db_settings is None:
        _db_settings = DatabaseSettings()
    return _db_settings
