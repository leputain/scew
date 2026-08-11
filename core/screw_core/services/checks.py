"""
Сервис проверок явки.

Сценарии:
- Запуск проверок за день
- Создание событий NO_CHECKIN для не отметившихся

Правила:
- DUTY не участвует в проверках
- Exempt дни (VACATION/SICK/LEAVE/DAY_OFF) пропускаются
- NO_CHECKIN: если обязан отмечаться и нет отметки
"""

from dataclasses import dataclass
from datetime import date
from typing import Any

from core.screw_core.models.enums import (
    DayStatusKind,
    EventType,
    NotifyChannel,
)
from core.screw_core.repos.attendance_repo import AttendanceRepo
from core.screw_core.repos.day_status_repo import DayStatusRepo
from core.screw_core.repos.events_repo import EventsRepo
from core.screw_core.repos.users_repo import UsersRepo
from core.screw_core.services.policy import (
    is_day_exempt_by_status,
    is_work_mode_duty,
    must_checkin,
)
from core.screw_core.utils.logging import get_logger

logger = get_logger(__name__)


@dataclass
class CheckResult:
    """Результат проверки за день."""

    total_users: int = 0
    skipped_with_status: int = 0
    no_checkin_count: int = 0
    events_created: list[dict[str, Any]] | None = None

    def __post_init__(self) -> None:
        if self.events_created is None:
            self.events_created = []


class ChecksService:
    """Сервис проверок явки."""

    @staticmethod
    async def run_daily_checks(
        check_date: date,
        poll_id: int | None = None,
        department_id: int | None = None,
        ignore_day_off: bool = False,
        bot=None,
    ) -> CheckResult:
        """
        Запустить проверки за день.

        Идемпотентно: повторный запуск не создаёт дубли (dedup_key в events).

        Args:
            check_date: Дата проверки.
            poll_id: ID закрытого опроса (для payload).
            department_id: Фильтр по отделу (None = все).
            ignore_day_off: Если True — не пропускать DAY_OFF (ручное закрытие опроса).
            bot: Не используется, оставлен для совместимости API.

        Returns:
            Результат проверки с количеством событий.
        """
        result = CheckResult()

        # 1. Получить активных пользователей
        if department_id is not None:
            active_users = await UsersRepo.get_active_by_department(department_id)
        else:
            active_users = await UsersRepo.get_all_active()
        result.total_users = len(active_users)

        if not active_users:
            logger.info("checks: нет активных пользователей для проверки")
            return result

        # 2. Получить все статусы дней на дату
        day_statuses = await DayStatusRepo.get_statuses_for_date(check_date)
        day_status_by_user: dict[int, str] = {s["user_id"]: s.get("kind") for s in day_statuses}

        # 3. Получить все отметки на дату
        marks = await AttendanceRepo.get_marks_for_date(check_date)
        marks_by_user: dict[int, dict] = {m["user_id"]: m for m in marks}

        # 4. Проверить каждого пользователя
        for user in active_users:
            user_id = user["id"]
            work_mode = user.get("work_mode")

            # a) DUTY не проверяется
            if is_work_mode_duty(work_mode):
                result.skipped_with_status += 1
                continue

            # b) Проверяем exempt
            day_status = day_status_by_user.get(user_id)

            if ignore_day_off:
                # При ручном запуске: пропускаем только VACATION/SICK/LEAVE, но НЕ DAY_OFF
                if day_status in (
                    DayStatusKind.VACATION.value,
                    DayStatusKind.SICK.value,
                    DayStatusKind.LEAVE.value,
                ):
                    result.skipped_with_status += 1
                    continue
            elif is_day_exempt_by_status(day_status):
                result.skipped_with_status += 1
                continue

            # c) NO_CHECKIN: обязан отмечаться, но нет отметки
            actual = marks_by_user.get(user_id)
            if must_checkin(work_mode) and actual is None:
                dedup_key = f"viol:{check_date}:NO_CHECKIN:{user_id}"
                event_id = await EventsRepo.create_event(
                    event_type=EventType.NO_CHECKIN,
                    dedup_key=dedup_key,
                    user_id=user_id,
                    event_date=check_date,
                    payload={
                        "work_mode": work_mode,
                        "poll_id": poll_id,
                        "full_name": user.get("full_name"),
                    },
                    notify_channel=NotifyChannel.VIOLATIONS_CHAT,
                )

                if event_id:
                    result.no_checkin_count += 1
                    result.events_created.append({
                        "id": event_id,
                        "type": EventType.NO_CHECKIN.value,
                        "user_id": user_id,
                    })
                    logger.info(
                        "checks: NO_CHECKIN user_id=%s (work_mode=%s, нет отметки)",
                        user_id, work_mode,
                    )
                else:
                    result.no_checkin_count += 1

        logger.info(
            "checks: проверка за %s завершена — users=%s, skipped=%s, no_checkin=%s",
            check_date, result.total_users, result.skipped_with_status, result.no_checkin_count,
        )

        return result
