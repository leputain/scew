"""
Утилиты для работы с рабочими днями и календарём.

Переиспользуется в scheduler, report_mail_scheduler и других модулях.
"""

import calendar
from datetime import date, timedelta


def is_working_day(d: date) -> bool:
    """Проверить, является ли дата рабочим днём (пн-пт)."""
    return d.weekday() < 5


def is_last_day_of_month(d: date) -> bool:
    """Проверить, является ли дата последним днём месяца."""
    _, last_day = calendar.monthrange(d.year, d.month)
    return d.day == last_day


def get_previous_working_day(d: date) -> date:
    """Получить предыдущий рабочий день."""
    result = d - timedelta(days=1)
    while not is_working_day(result):
        result -= timedelta(days=1)
    return result
