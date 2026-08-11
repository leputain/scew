"""
Сервис уведомлений.

Сценарии:
- Получение событий для уведомления
- Отметка доставки
- Violations notify в чат нарушений
"""

import json
import os
import html
from dataclasses import dataclass
from datetime import date
from typing import Any

from core.screw_core.config.departments import OZKO_DEPARTMENT_ID
from core.screw_core.models.enums import EventType, NotifyChannel
from core.screw_core.repos.events_repo import EventsRepo
from core.screw_core.services.checks import ChecksService
from core.screw_core.services.telegram_delivery import send_system_message
from core.screw_core.utils.logging import get_logger

logger = get_logger(__name__)


# =============================================================================
# Конфигурация violations notify
# =============================================================================


def get_violations_ozko_chat_id() -> int | None:
    """Получить ID чата для нарушений ОЗКО."""
    value = os.getenv("VIOLATIONS_OZKO_CHAT_ID", "")
    if value.strip():
        try:
            return int(value.strip())
        except ValueError:
            logger.warning("VIOLATIONS_OZKO_CHAT_ID: неверный формат — %s", value)
    return None


def get_violations_notify_enabled() -> bool:
    """Включена ли отправка уведомлений о нарушениях."""
    return os.getenv("VIOLATIONS_NOTIFY_ENABLED", "0") == "1"


def get_birthday_notify_enabled() -> bool:
    """Включена ли отправка уведомлений о днях рождения."""
    return os.getenv("BIRTHDAY_NOTIFY_ENABLED", "0") == "1"


def get_birthday_notify_chat_id() -> int | None:
    """
    Получить ID чата для поздравлений.

    По умолчанию используется тот же leadership/violations chat.
    """
    value = os.getenv("BIRTHDAY_NOTIFY_CHAT_ID", "").strip()
    if not value:
        return get_violations_ozko_chat_id()
    try:
        return int(value)
    except ValueError:
        logger.warning("BIRTHDAY_NOTIFY_CHAT_ID: неверный формат — %s", value)
        return None


def get_birthday_notify_message_thread_id() -> int | None:
    """Получить ID Telegram forum topic для поздравлений."""
    value = os.getenv("BIRTHDAY_NOTIFY_MESSAGE_THREAD_ID", "").strip()
    if not value:
        return None
    try:
        return int(value)
    except ValueError:
        logger.warning("BIRTHDAY_NOTIFY_MESSAGE_THREAD_ID: неверный формат — %s", value)
        return None


def is_birthday_notify_message_thread_id_invalid() -> bool:
    """Проверить, задан ли topic id в неверном формате."""
    value = os.getenv("BIRTHDAY_NOTIFY_MESSAGE_THREAD_ID", "").strip()
    if not value:
        return False
    try:
        int(value)
        return False
    except ValueError:
        return True


def get_violations_notify_max_lines() -> int:
    """Максимум строк в сообщении о нарушениях."""
    value = os.getenv("VIOLATIONS_NOTIFY_MAX_LINES", "50")
    try:
        return int(value)
    except ValueError:
        return 50


# =============================================================================
# Типы нарушений для violations chat
# =============================================================================

# Типы событий, которые отправляются в violations chat
VIOLATION_EVENT_TYPES = {
    EventType.NO_CHECKIN,
}


@dataclass
class ViolationMessage:
    """Сообщение о нарушении для отправки в чат."""

    event_id: int
    event_type: str
    event_date: date | None
    user_id: int | None
    full_name: str | None
    text: str
    department_id: int | None = None
    department_code: str | None = None
    payload: dict[str, Any] | None = None


def _parse_payload(payload: Any) -> dict[str, Any]:
    """Безопасно распарсить payload (может быть str или dict)."""
    if isinstance(payload, str):
        try:
            return json.loads(payload)
        except (json.JSONDecodeError, TypeError):
            return {}
    return payload or {}


def _build_violations_summary_text(
    check_date: date,
    violations: list[dict[str, Any]],
) -> str:
    """
    Сформировать сводное сообщение о нарушениях за день.

    Формат:
    📋 Сводка нарушений за ДД.ММ.ГГГГ

    ⚠️ Не отметились в опросе:
    👤 Иванов Иван Иванович
    ...
    """
    date_str = check_date.strftime("%d.%m.%Y")

    dept_labels = {
        "OZKO": "ОЗКО",
        "OSB": "ОСБ",
    }
    no_checkin_by_dept: dict[str, list[str]] = {}

    for v in violations:
        event_type = v.get("event_type", "")
        full_name = v.get("full_name") or "Неизвестный"
        department_code = str(v.get("department_code") or "").strip()
        department_label = dept_labels.get(department_code, department_code or "Без отдела")

        if event_type == EventType.NO_CHECKIN.value:
            no_checkin_by_dept.setdefault(department_label, []).append(str(full_name))

    lines = [f"📋 <b>Сводка нарушений за {date_str}</b>"]

    total_no_checkin = sum(len(items) for items in no_checkin_by_dept.values())
    if total_no_checkin:
        lines.append("")
        lines.append(f"⚠️ <b>Не отметились в опросе ({total_no_checkin}):</b>")
        for department_label in sorted(no_checkin_by_dept):
            items = sorted(no_checkin_by_dept[department_label])
            lines.append("")
            safe_department = html.escape(department_label, quote=False)
            lines.append(f"<b>{safe_department} ({len(items)}):</b>")
            lines.extend(f"• {html.escape(item, quote=False)}" for item in items)

    if not total_no_checkin:
        lines.append("")
        lines.append("✅ Нарушений не обнаружено.")

    return "\n".join(lines)


class NotifyService:
    """Сервис для работы с уведомлениями о событиях."""

    @staticmethod
    async def get_pending_notifications(limit: int = 100) -> list[dict[str, Any]]:
        """Получить события, ожидающие уведомления."""
        return await EventsRepo.get_pending_notifications(limit)

    @staticmethod
    async def mark_notified(event_id: int) -> None:
        """Отметить событие как доставленное."""
        await EventsRepo.mark_notified(event_id)

    @staticmethod
    async def mark_error(event_id: int, error: str) -> None:
        """Отметить ошибку доставки."""
        await EventsRepo.mark_notify_error(event_id, error)

    @staticmethod
    async def process_pending_notifications() -> int:
        """Обработать ожидающие уведомления."""
        pending = await NotifyService.get_pending_notifications()
        return len(pending)

    @staticmethod
    async def get_pending_violations_for_chat(
        limit: int | None = None,
    ) -> list[ViolationMessage]:
        """
        Получить события нарушений для отправки в violations chat.

        Возвращает события с notify_channel=violations_chat,
        которые ещё не были уведомлены (is_notified=FALSE).
        """
        if limit is None:
            limit = get_violations_notify_max_lines()

        pending = await EventsRepo.get_pending_notifications(limit)

        violation_type_values = [et.value for et in VIOLATION_EVENT_TYPES]
        violations = []
        for event in pending:
            if event.get("notify_channel") != NotifyChannel.VIOLATIONS_CHAT.value:
                continue
            event_type = event.get("event_type", "")
            if event_type not in violation_type_values:
                continue
            violations.append(ViolationMessage(
                event_id=event["id"],
                event_type=event_type,
                event_date=event.get("event_date"),
                user_id=event.get("user_id"),
                full_name=event.get("full_name"),
                department_id=event.get("department_id"),
                department_code=event.get("department_code"),
                text="",
                payload=event.get("payload"),
            ))

        return violations

    @staticmethod
    async def build_violations_summary_for_date(
        check_date: date,
        department_id: int | None = None,
    ) -> tuple[str | None, list[int]]:
        """
        Собрать сводное сообщение о нарушениях за конкретную дату.

        Возвращает одно сводное сообщение и список event_id для отметки.
        """
        all_violations = await NotifyService.get_pending_violations_for_chat(limit=10_000)

        # Фильтруем только за нужную дату и, если задано, за нужный отдел.
        day_violations = [
            v
            for v in all_violations
            if v.event_date == check_date
            and (department_id is None or v.department_id == department_id)
        ]

        if not day_violations:
            return None, []

        # Собираем данные для форматирования
        events_data = []
        event_ids = []
        for v in day_violations:
            events_data.append({
                "event_type": v.event_type,
                "full_name": v.full_name,
                "department_id": v.department_id,
                "department_code": v.department_code,
                "payload": v.payload,
            })
            event_ids.append(v.event_id)

        text = _build_violations_summary_text(check_date, events_data)
        return text, event_ids

    @staticmethod
    def is_violations_notify_configured() -> bool:
        """Проверить, настроена ли отправка нарушений."""
        if not get_violations_notify_enabled():
            return False
        if not get_violations_ozko_chat_id():
            return False
        return True

    @staticmethod
    async def mark_violation_sent(event_id: int) -> None:
        """Отметить нарушение как отправленное."""
        await EventsRepo.mark_notified(event_id)

    @staticmethod
    async def mark_violation_error(event_id: int, error: str) -> None:
        """Отметить ошибку отправки нарушения."""
        error_truncated = error[:200] if error else "unknown"
        await EventsRepo.mark_notify_error(event_id, error_truncated)

    @staticmethod
    async def run_post_poll_checks(
        bot,
        check_date: date,
        department_id: int | None = None,
        ignore_day_off: bool = False,
        poll_id: int | None = None,
    ) -> None:
        """
        Запустить daily checks и отправить сводку нарушений в violations chat.

        Args:
            bot: Экземпляр Bot.
            check_date: Дата проверки.
            department_id: ID подразделения; None = все активные сотрудники.
            ignore_day_off: Если True — не пропускать DAY_OFF.
            poll_id: ID опроса, который инициировал проверку.
        """
        try:
            result = await ChecksService.run_daily_checks(
                check_date=check_date,
                poll_id=poll_id,
                department_id=department_id,
                ignore_day_off=ignore_day_off,
                bot=bot,
            )
            logger.info(
                "post-poll checks: users=%d, no_checkin=%d",
                result.total_users,
                result.no_checkin_count,
            )
        except Exception as e:
            logger.exception("post-poll checks: ошибка — %s", e)
            return

        if not NotifyService.is_violations_notify_configured():
            return

        chat_id = get_violations_ozko_chat_id()
        if not chat_id:
            return

        text, event_ids = await NotifyService.build_violations_summary_for_date(
            check_date,
            department_id=department_id or OZKO_DEPARTMENT_ID,
        )
        if not text:
            return

        try:
            await send_system_message(
                bot,
                chat_id=chat_id,
                html_text=text,
                reaction_emoji="✅",
                delivery_mode="html",
            )
            for eid in event_ids:
                await NotifyService.mark_violation_sent(eid)
            logger.info("post-poll violations: сводка отправлена (%d нарушений)", len(event_ids))
        except Exception as e:
            for eid in event_ids:
                await NotifyService.mark_violation_error(eid, str(e)[:200])
            logger.warning("post-poll violations: ошибка отправки: %s", str(e)[:100])

        # Отправка результатов опроса по email (если настроено)
        try:
            from core.screw_core.services.poll_results_mailer import (
                is_poll_results_email_configured,
                send_poll_results_email,
            )
            if is_poll_results_email_configured():
                await send_poll_results_email(check_date)
        except Exception as e:
            logger.warning("post-poll email: ошибка отправки — %s", str(e)[:100])
