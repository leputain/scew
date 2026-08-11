"""
Перечисления (enum) для проекта SCREW.

Значения должны совпадать с enum-типами в PostgreSQL.
"""

from enum import StrEnum


class Department(StrEnum):
    """Отделы организации."""

    OZKO = "OZKO"  # Отдел защиты конечного оборудования
    OSB = "OSB"  # Отдел сетевой безопасности


class UserStatus(StrEnum):
    """Статус пользователя в системе."""

    ACTIVE = "active"  # Активный сотрудник
    TERMINATED = "terminated"  # Уволен


class UserRole(StrEnum):
    """Роль/тип пользователя в системе."""

    EMPLOYEE = "employee"  # Обычный сотрудник с Telegram
    NO_TG_WORKER = "no_tg_worker"  # Работник без Telegram: учитывается в сводках, не тегается
    MANAGER = "manager"  # Руководитель
    ADMIN = "admin"  # Администратор


class WorkMode(StrEnum):
    """Режим работы сотрудника."""

    HYBRID = "HYBRID"  # Гибридный (офис + удалёнка)
    HYBRID_PLUS = "HYBRID_PLUS"  # Гибридный расширенный
    FULL_REMOTE = "FULL_REMOTE"  # Полностью удалённый
    DUTY = "DUTY"  # Дежурный режим


class AttendanceChoice(StrEnum):
    """Выбор явки в ежедневном опросе."""

    EVOLUTION = "EVOLUTION"  # Офис Эволюция
    CSKA = "CSKA"  # Офис ЦСКА
    REMOTE = "REMOTE"  # Удалённая работа


class DayStatusKind(StrEnum):
    """Вид статуса дня (отсутствие)."""

    VACATION = "VACATION"  # Отпуск
    SICK = "SICK"  # Больничный
    LEAVE = "LEAVE"  # Отгул
    DAY_OFF = "DAY_OFF"  # Выходной


class EventType(StrEnum):
    """Тип события/нарушения."""

    NO_CHECKIN = "NO_CHECKIN"  # Не отметился в опросе
    CONFLICT_STATUS = "CONFLICT_STATUS"  # Конфликт статусов (например, отпуск и явка)
    REPORT_DAILY_SENT = "REPORT_DAILY_SENT"  # Ежедневный отчёт успешно отправлен
    REPORT_DAILY_FAILED = "REPORT_DAILY_FAILED"  # Ошибка отправки ежедневного отчёта
    REPORT_MONTHLY_SENT = "REPORT_MONTHLY_SENT"  # Месячный отчёт успешно отправлен
    REPORT_MONTHLY_FAILED = "REPORT_MONTHLY_FAILED"  # Ошибка отправки месячного отчёта
    MONTHLY_STATS_SENT = "MONTHLY_STATS_SENT"  # Месячная Telegram-статистика отправлена
    OZKO_POST_APPROVE_NOTICE = "OZKO_POST_APPROVE_NOTICE"  # Уведомление ОЗКО после одобрения
    BIRTHDAY_NOTIFY_SENT = "BIRTHDAY_NOTIFY_SENT"  # Поздравление с ДР отправлено
    ADMIN_ACTION = "ADMIN_ACTION"  # Действие через админ-панель
    MANAGER_ACTION = "MANAGER_ACTION"  # Действие через панель менеджера


class NotifyChannel(StrEnum):
    """Канал уведомления о событии."""

    VIOLATIONS_CHAT = "violations_chat"  # Чат нарушений
    USER_DM = "user_dm"  # Личное сообщение пользователю
    NONE = "none"  # Без уведомления


class PollStatus(StrEnum):
    """Статус опроса явки."""

    OPEN = "open"  # Опрос открыт
    CLOSED = "closed"  # Опрос закрыт
    DELETED = "deleted"  # Опрос удалён
