"""
Конфигурация attendance_bot.

Загружает специфичные для бота настройки из переменных окружения.
"""

import os

from core.screw_core.config.telegram import (
    get_admin_telegram_ids,  # noqa: F401 - re-exported for bootstrap.py
    get_manager_ozko_telegram_ids,  # noqa: F401 - re-exported for bootstrap.py
    parse_telegram_ids as _parse_telegram_ids,
)


def get_bot_token() -> str:
    """
    Получить токен бота из переменной окружения.

    Returns:
        Токен Telegram-бота.

    Raises:
        ValueError: Если токен не задан.
    """
    token = os.getenv("ATTENDANCE_BOT_TOKEN")
    if not token:
        raise ValueError("Переменная окружения ATTENDANCE_BOT_TOKEN не задана")
    return token


def is_dev_self_registration_enabled() -> bool:
    """
    Проверить, включён ли режим самостоятельной регистрации для разработки.

    DEV-ONLY: Позволяет уже зарегистрированным пользователям (admin/manager)
    проходить регистрацию заново по токену. Используется для тестирования
    флоу регистрации с одного аккаунта.

    ВНИМАНИЕ: В продакшене должен быть выключен (DEV_ALLOW_SELF_REGISTRATION=0).

    Returns:
        True если режим включён (DEV_ALLOW_SELF_REGISTRATION=1).
    """
    value = os.getenv("DEV_ALLOW_SELF_REGISTRATION", "0")
    return value == "1"


def is_dev_bootstrap_admins_enabled() -> bool:
    """
    Проверить, включён ли автоматический bootstrap админов/менеджеров.

    DEV-ONLY: При включении бот автоматически создаёт/обновляет пользователей
    из списков ADMIN_TELEGRAM_IDS и MANAGER_*_TELEGRAM_IDS при старте.

    ВНИМАНИЕ: В продакшене должен быть выключен (DEV_BOOTSTRAP_ADMINS=0).

    Returns:
        True если режим включён (DEV_BOOTSTRAP_ADMINS=1).
    """
    value = os.getenv("DEV_BOOTSTRAP_ADMINS", "0")
    return value == "1"


def get_manager_osb_telegram_ids() -> list[int]:
    """
    Получить список Telegram ID менеджеров ОСБ.

    Returns:
        Список ID или пустой список.
    """
    value = os.getenv("MANAGER_OSB_TELEGRAM_IDS", "")
    return _parse_telegram_ids(value)


# =============================================================================
# Настройки опросов явки
# =============================================================================


def get_attendance_group_id() -> int | None:
    """
    Получить ID группы для опросов из переменной окружения.

    Returns:
        ID группы или None если не задан.
    """
    value = os.getenv("ATTENDANCE_GROUP_ID")
    if value:
        try:
            return int(value)
        except ValueError:
            pass
    return None


def get_welcome_ozko_contact() -> str:
    """Public-safe contact label for the OZKO welcome message."""
    return os.getenv("ATTENDANCE_WELCOME_OZKO_CONTACT", "").strip() or "уточните у администратора"


def get_welcome_osb_contact() -> str:
    """Public-safe contact label for the OSB welcome message."""
    return os.getenv("ATTENDANCE_WELCOME_OSB_CONTACT", "").strip() or "уточните у администратора"


def get_workplace_setup_url() -> str:
    """Optional workplace setup URL shown in the group welcome message."""
    return os.getenv("ATTENDANCE_WORKPLACE_SETUP_URL", "").strip()


def get_poll_summary_chat_id() -> int | None:
    """
    Получить ID отдельного чата для итогов опроса.

    Если переменная не задана, итоговые сообщения после закрытия не отправляются.
    """
    value = os.getenv("POLL_SUMMARY_CHAT_ID")
    if value:
        try:
            return int(value)
        except ValueError:
            pass
    return None


def get_department_lists_chat_id() -> int | None:
    """
    Получить ID чата для общей сводки списков ОЗКО/ОСБ.

    Если переменная не задана, сообщение со списками отделов после закрытия
    опроса не отправляется.
    """
    value = os.getenv("DEPARTMENT_LISTS_CHAT_ID")
    if value:
        try:
            return int(value)
        except ValueError:
            pass
    return None


_ALLOWED_GROUP_CHAT_ID_ENV_VARS = (
    "MAIN_GROUP_CHAT_ID",
    "ATTENDANCE_GROUP_ID",
    "VIOLATIONS_OZKO_CHAT_ID",
    "BIRTHDAY_NOTIFY_CHAT_ID",
    "FLUD_CHAT_ID",
    "POLL_SUMMARY_CHAT_ID",
    "DEPARTMENT_LISTS_CHAT_ID",
)


def _parse_chat_id(value: str | None) -> int | None:
    if not value:
        return None
    try:
        return int(value.strip())
    except (TypeError, ValueError):
        return None


def get_allowed_group_chat_ids() -> set[int]:
    """
    Получить allowlist групповых чатов, где бот имеет право отвечать.

    Источники:
    - известные chat/group env-переменные проекта;
    - ATTENDANCE_ALLOWED_GROUP_IDS как явный comma-separated override/addition.
    """
    result: set[int] = set()
    for env_name in _ALLOWED_GROUP_CHAT_ID_ENV_VARS:
        chat_id = _parse_chat_id(os.getenv(env_name))
        if chat_id is not None:
            result.add(chat_id)

    extra = os.getenv("ATTENDANCE_ALLOWED_GROUP_IDS", "")
    for item in extra.replace(";", ",").split(","):
        chat_id = _parse_chat_id(item)
        if chat_id is not None:
            result.add(chat_id)
    return result


def _env_bool(name: str, default: str = "0") -> bool:
    return os.getenv(name, default).strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int, *, min_value: int | None = None, max_value: int | None = None) -> int:
    value = os.getenv(name, str(default))
    try:
        parsed = int(str(value).strip())
    except (TypeError, ValueError):
        return default
    if min_value is not None and parsed < min_value:
        return default
    if max_value is not None and parsed > max_value:
        return default
    return parsed


def is_flud_enabled() -> bool:
    """Включён ли сбор статистики флудилки."""
    return _env_bool("FLUD_ENABLED", "0")


def get_flud_chat_id() -> int | None:
    """Получить ID флудилки."""
    return _parse_chat_id(os.getenv("FLUD_CHAT_ID"))


def get_flud_message_thread_id() -> int | None:
    """Получить ID Telegram forum topic для флудилки."""
    return _parse_chat_id(os.getenv("FLUD_MESSAGE_THREAD_ID"))


def is_flud_message_thread_id_invalid() -> bool:
    """Проверить, задан ли topic id в неверном формате."""
    value = os.getenv("FLUD_MESSAGE_THREAD_ID", "").strip()
    if not value:
        return False
    return _parse_chat_id(value) is None


def get_flud_digest_chat_id() -> int | None:
    """Получить ID чата для публикации дайджестов флудилки."""
    return _parse_chat_id(os.getenv("FLUD_DIGEST_CHAT_ID")) or _parse_chat_id(os.getenv("VIOLATIONS_OZKO_CHAT_ID"))


def get_flud_store_text_preview() -> bool:
    """Хранить ли короткий preview текста сообщений."""
    return _env_bool("FLUD_STORE_TEXT_PREVIEW", "0")


def get_flud_text_preview_max_chars() -> int:
    """Максимальная длина preview текста."""
    return _env_int("FLUD_TEXT_PREVIEW_MAX_CHARS", 120, min_value=0, max_value=120)


def get_flud_retention_days() -> int:
    """Retention period для данных флудилки."""
    return _env_int("FLUD_RETENTION_DAYS", 90, min_value=0)


def get_flud_weekly_digest_enabled() -> bool:
    """Включён ли недельный дайджест флудилки."""
    return _env_bool("FLUD_WEEKLY_DIGEST_ENABLED", "0")


def get_flud_weekly_digest_day_of_week() -> int:
    """День недели weekly digest: 0=mon ... 6=sun."""
    return _env_int("FLUD_WEEKLY_DIGEST_DAY_OF_WEEK", 5, min_value=0, max_value=6)


def get_flud_weekly_digest_hour() -> int:
    """Час отправки weekly digest."""
    return _env_int("FLUD_WEEKLY_DIGEST_HOUR", 17, min_value=0, max_value=23)


def get_flud_weekly_digest_minute() -> int:
    """Минута отправки weekly digest."""
    return _env_int("FLUD_WEEKLY_DIGEST_MINUTE", 0, min_value=0, max_value=59)


def get_flud_monthly_digest_enabled() -> bool:
    """Включён ли месячный дайджест флудилки."""
    return _env_bool("FLUD_MONTHLY_DIGEST_ENABLED", "0")


def get_flud_monthly_digest_hour() -> int:
    """Час отправки monthly digest."""
    return _env_int("FLUD_MONTHLY_DIGEST_HOUR", 17, min_value=0, max_value=23)


def get_flud_monthly_digest_minute() -> int:
    """Минута отправки monthly digest."""
    return _env_int("FLUD_MONTHLY_DIGEST_MINUTE", 30, min_value=0, max_value=59)


def get_flud_reactions_enabled() -> bool:
    """Включён ли сбор реакций флудилки."""
    return _env_bool("FLUD_REACTIONS_ENABLED", "1")


def get_flud_digest_message_links_enabled() -> bool:
    """Показывать ли ссылки на исходные сообщения в дайджестах флудилки."""
    return _env_bool("FLUD_DIGEST_MESSAGE_LINKS_ENABLED", "0")


def get_flud_top_limit() -> int:
    """Лимит топ-сообщений в дайджестах."""
    return _env_int("FLUD_TOP_LIMIT", 5, min_value=1, max_value=20)


def get_poll_duration_seconds() -> int:
    """
    Получить длительность опроса в секундах.

    По умолчанию: 900 секунд (15 минут).

    Returns:
        Длительность в секундах.
    """
    value = os.getenv("POLL_DURATION_SECONDS", "900")
    try:
        return int(value)
    except ValueError:
        return 900


def get_poll_timezone() -> str:
    """
    Получить таймзону для опросов.

    По умолчанию: Europe/Moscow.

    Returns:
        Название таймзоны.
    """
    return os.getenv("TZ", "Europe/Moscow")


def get_poll_reaper_interval_seconds() -> int:
    """
    Получить интервал проверки просроченных опросов (reaper).

    По умолчанию: 30 секунд.

    Returns:
        Интервал в секундах.
    """
    value = os.getenv("POLL_REAPER_INTERVAL_SECONDS", "30")
    try:
        return int(value)
    except ValueError:
        return 30


def get_poll_reminder_before_end_minutes() -> int | None:
    """
    Получить время напоминания перед закрытием опроса (в минутах).

    Если задано, бот отправит reminder неответившим за N минут до закрытия.
    Если не задано или 0 — reminder отключён.

    Returns:
        Количество минут или None если отключено.
    """
    value = os.getenv("POLL_REMINDER_BEFORE_END_MINUTES", "")
    if not value.strip():
        return None
    try:
        minutes = int(value)
        if minutes > 0:
            return minutes
        return None
    except ValueError:
        return None


# =============================================================================
# Настройки ежедневных отчётов
# =============================================================================


def get_report_daily_enabled() -> bool:
    """
    Проверить, включена ли ежедневная отправка отчётов.

    По умолчанию: выключено (0).

    Returns:
        True если включено (REPORT_DAILY_ENABLED=1).
    """
    return os.getenv("REPORT_DAILY_ENABLED", "0") == "1"


def get_report_daily_hour() -> int:
    """
    Получить час отправки ежедневного отчёта.

    По умолчанию: 18 (18:00).

    Returns:
        Час (0-23).
    """
    value = os.getenv("REPORT_DAILY_HOUR", "18")
    try:
        hour = int(value)
        if 0 <= hour <= 23:
            return hour
        return 18
    except ValueError:
        return 18


def get_report_daily_minute() -> int:
    """
    Получить минуту отправки ежедневного отчёта.

    По умолчанию: 0.

    Returns:
        Минута (0-59).
    """
    value = os.getenv("REPORT_DAILY_MINUTE", "0")
    try:
        minute = int(value)
        if 0 <= minute <= 59:
            return minute
        return 0
    except ValueError:
        return 0


def get_report_daily_timezone() -> str:
    """
    Получить таймзону для отправки ежедневных отчётов.

    По умолчанию: Europe/Moscow (из TZ).

    Returns:
        Название таймзоны.
    """
    return os.getenv("TZ", "Europe/Moscow")


# =============================================================================
# Настройки email-отчётов (EWS)
# =============================================================================


def get_exchange_ews_url() -> str | None:
    """
    Получить URL EWS сервера Exchange.

    Returns:
        URL или None если не задан.
    """
    return os.getenv("EXCHANGE_EWS_URL") or None


def get_exchange_username() -> str | None:
    """
    Получить имя пользователя Exchange.

    Returns:
        Username или None если не задан.
    """
    return os.getenv("EXCHANGE_USERNAME") or None


def get_exchange_password() -> str | None:
    """
    Получить пароль Exchange.

    Returns:
        Password или None если не задан.
    """
    return os.getenv("EXCHANGE_PASSWORD") or None


def get_exchange_auth_type() -> str:
    """
    Получить тип аутентификации Exchange.

    По умолчанию: NTLM.

    Returns:
        Тип аутентификации.
    """
    return os.getenv("EXCHANGE_AUTH", "NTLM")


def get_report_email_from() -> str | None:
    """
    Получить адрес отправителя отчётов.

    Опционально. Если не задан, используется адрес учётной записи Exchange.

    Returns:
        Email адрес или None.
    """
    return os.getenv("REPORT_FROM") or None


def get_report_email_to() -> list[str]:
    """
    Получить список получателей отчётов (TO).

    Обязательно для отправки email.

    Returns:
        Список email адресов.
    """
    value = os.getenv("REPORT_TO", "")
    return _parse_email_list(value)


def get_report_email_cc() -> list[str]:
    """
    Получить список получателей копии отчётов (CC).

    Returns:
        Список email адресов.
    """
    value = os.getenv("REPORT_CC", "")
    return _parse_email_list(value)


def _parse_email_list(value: str) -> list[str]:
    """
    Распарсить строку email адресов через запятую.

    Args:
        value: Строка вида "a@b.com,c@d.com".

    Returns:
        Список email адресов.
    """
    if not value.strip():
        return []
    return [email.strip() for email in value.split(",") if email.strip()]


def is_email_report_configured() -> bool:
    """
    Проверить, полностью ли настроена отправка email-отчётов.

    Требуются: EWS URL, username, password, REPORT_TO.

    Returns:
        True если все обязательные параметры заданы.
    """
    return bool(
        get_exchange_ews_url()
        and get_exchange_username()
        and get_exchange_password()
        and get_report_email_to()
    )


def get_report_mail_enabled() -> bool:
    """
    Проверить, включена ли отправка отчётов по email.

    По умолчанию: выключено (0).

    Returns:
        True если включено (REPORT_MAIL_ENABLED=1).
    """
    return os.getenv("REPORT_MAIL_ENABLED", "0") == "1"


def get_report_mail_hour() -> int:
    """
    Получить час отправки email-отчёта.

    По умолчанию: 9 (09:35 MSK).

    Returns:
        Час (0-23).
    """
    value = os.getenv("REPORT_MAIL_HOUR", "9")
    try:
        hour = int(value)
        if 0 <= hour <= 23:
            return hour
        return 9
    except ValueError:
        return 9


def get_report_mail_minute() -> int:
    """
    Получить минуту отправки email-отчёта.

    По умолчанию: 35.

    Returns:
        Минута (0-59).
    """
    value = os.getenv("REPORT_MAIL_MINUTE", "35")
    try:
        minute = int(value)
        if 0 <= minute <= 59:
            return minute
        return 35
    except ValueError:
        return 35


# =============================================================================
# Настройки месячного email-отчёта
# =============================================================================


def get_report_monthly_enabled() -> bool:
    """
    Проверить, включена ли отправка месячных отчётов по email.

    По умолчанию: выключено (0).

    Returns:
        True если включено (REPORT_MONTHLY_ENABLED=1).
    """
    return os.getenv("REPORT_MONTHLY_ENABLED", "0") == "1"


def get_report_monthly_hour() -> int:
    """
    Получить час отправки месячного email-отчёта.

    По умолчанию: 10 (10:00 MSK).

    Returns:
        Час (0-23).
    """
    value = os.getenv("REPORT_MONTHLY_SEND_HOUR", "10")
    try:
        hour = int(value)
        if 0 <= hour <= 23:
            return hour
        return 10
    except ValueError:
        return 10


def get_report_monthly_minute() -> int:
    """
    Получить минуту отправки месячного email-отчёта.

    По умолчанию: 0.

    Returns:
        Минута (0-59).
    """
    value = os.getenv("REPORT_MONTHLY_SEND_MINUTE", "0")
    try:
        minute = int(value)
        if 0 <= minute <= 59:
            return minute
        return 0
    except ValueError:
        return 0


def get_report_monthly_subject_template() -> str:
    """
    Получить шаблон темы месячного email-отчёта.

    По умолчанию: "SCREW Monthly report — {year}-{month:02d}".

    Returns:
        Шаблон темы.
    """
    return os.getenv(
        "REPORT_MONTHLY_SUBJECT_TEMPLATE",
        "SCREW Monthly report — {year}-{month:02d}",
    )


# =============================================================================
# Настройки чатов OZKO (invite-ссылки для новых сотрудников)
# =============================================================================


def get_ozko_bot_chat_invite_link() -> str | None:
    """
    Получить invite-ссылку на операционный чат бота для сотрудников ОЗКО.

    Returns:
        Invite-ссылка на чат или None если не задана.
    """
    return os.getenv("OZKO_BOT_CHAT_INVITE_LINK") or None




def get_ozko_dept_chat_invite_link() -> str | None:
    """
    Получить invite-ссылку на чат отдела ОЗКО.

    Returns:
        Invite-ссылка на чат или None если не задана.
    """
    return os.getenv("OZKO_DEPT_CHAT_INVITE_LINK") or None


def get_proxy_url() -> str:
    """
    Получить URL прокси для Telegram API.

    Returns:
        URL прокси или пустая строка если не задан.
    """
    return os.getenv("TELEGRAM_PROXY_URL", "").strip()
