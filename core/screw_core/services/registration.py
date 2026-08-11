"""
Сервис регистрации пользователей.

Сценарии:
- Создание приглашения (с TTL 30 минут)
- Регистрация по deep-link
- Подтверждение/отклонение заявки менеджером
"""

import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import StrEnum
from typing import Any

from core.screw_core.models.enums import WorkMode
from core.screw_core.repos.registration_repo import RegistrationRepo
from core.screw_core.repos.users_repo import UsersRepo
from core.screw_core.services.permissions import OZKO_DEPARTMENT_ID


# Время жизни токена приглашения (30 минут)
INVITE_TTL_MINUTES = 30


class RegistrationError(StrEnum):
    """Ошибки регистрации."""

    TOKEN_NOT_FOUND = "token_not_found"
    TOKEN_EXPIRED = "token_expired"
    TOKEN_ALREADY_USED = "token_already_used"
    ALREADY_REGISTERED = "already_registered"
    PENDING_REQUEST_EXISTS = "pending_request_exists"
    REQUEST_NOT_FOUND = "request_not_found"
    NOT_INVITE_CREATOR = "not_invite_creator"


@dataclass
class RegistrationResult:
    """Результат операции регистрации."""

    success: bool
    error: RegistrationError | None = None
    request_id: int | None = None
    user_id: int | None = None
    data: dict[str, Any] | None = None


class RegistrationService:
    """Сервис регистрации пользователей."""

    @staticmethod
    async def create_invite(
        department_id: int,
        expected_full_name: str | None = None,
        work_mode: WorkMode = WorkMode.HYBRID,
        created_by: int | None = None,
        ttl_minutes: int = INVITE_TTL_MINUTES,
    ) -> str:
        """
        Создать приглашение на регистрацию.

        Args:
            department_id: ID отдела.
            expected_full_name: Ожидаемое ФИО.
            work_mode: Режим работы.
            created_by: Кто создаёт (user_id).
            ttl_minutes: Время жизни токена в минутах.

        Returns:
            Токен приглашения (plaintext, 32 hex символа).
        """
        # 16 байт -> 32 hex символа, умещается в varchar(64)
        token = secrets.token_hex(16)
        expires_at = datetime.now(timezone.utc) + timedelta(minutes=ttl_minutes)

        await RegistrationRepo.create_invite(
            token=token,
            department_id=department_id,
            expected_full_name=expected_full_name,
            work_mode=work_mode,
            created_by=created_by,
            expires_at=expires_at,
        )

        return token

    @staticmethod
    async def validate_token(token: str) -> RegistrationResult:
        """
        Проверить валидность токена.

        Args:
            token: Токен приглашения.

        Returns:
            Результат с данными приглашения или ошибкой.
        """
        invite = await RegistrationRepo.get_invite_by_token(token)

        if not invite:
            return RegistrationResult(success=False, error=RegistrationError.TOKEN_NOT_FOUND)

        if invite.get("used_by"):
            return RegistrationResult(success=False, error=RegistrationError.TOKEN_ALREADY_USED)

        expires_at = invite.get("expires_at")
        if expires_at:
            # Приводим к aware datetime для сравнения
            now = datetime.now(timezone.utc)
            if expires_at.tzinfo is None:
                expires_at = expires_at.replace(tzinfo=timezone.utc)
            if now > expires_at:
                return RegistrationResult(success=False, error=RegistrationError.TOKEN_EXPIRED)

        return RegistrationResult(success=True, data=invite)

    @staticmethod
    async def register_by_token(
        token: str,
        telegram_id: int,
        username: str | None = None,
        first_name: str | None = None,
        last_name: str | None = None,
    ) -> RegistrationResult:
        """
        Зарегистрироваться по токену приглашения.

        Создаёт заявку на регистрацию, которую должен подтвердить менеджер.

        Args:
            token: Токен приглашения.
            telegram_id: Telegram ID пользователя.
            username: Юзернейм.
            first_name: Имя.
            last_name: Фамилия.

        Returns:
            Результат с ID заявки или ошибкой.
        """
        # Проверяем, есть ли уже пользователь
        existing_user = await UsersRepo.get_by_telegram_id(telegram_id)
        if existing_user:
            return RegistrationResult(
                success=False,
                error=RegistrationError.ALREADY_REGISTERED,
                user_id=existing_user["id"],
            )

        # Проверяем токен ДО проверки pending —
        # при валидном новом токене старые pending отменяются
        validation = await RegistrationService.validate_token(token)
        if not validation.success:
            return validation

        invite = validation.data

        # Если есть старая pending заявка — автоотмена в пользу нового токена
        pending = await RegistrationRepo.get_pending_request_by_telegram_id(telegram_id)
        if pending:
            cancelled = await RegistrationRepo.cancel_pending_requests(telegram_id)
            if cancelled:
                from core.screw_core.utils.logging import get_logger
                logger = get_logger(__name__)
                logger.info(
                    "auto-cancelled %d pending request(s) for telegram_id=%s (new token)",
                    cancelled,
                    telegram_id,
                )

        # Создаём заявку
        request_id = await RegistrationRepo.create_request(
            telegram_id=telegram_id,
            invite_id=invite["id"],
            username=username,
            first_name=first_name,
            last_name=last_name,
        )

        return RegistrationResult(success=True, request_id=request_id)

    @staticmethod
    async def get_pending_requests(department_id: int | None = None) -> list[dict[str, Any]]:
        """
        Получить заявки, ожидающие подтверждения.

        Args:
            department_id: Фильтр по отделу (опционально).

        Returns:
            Список заявок.
        """
        return await RegistrationRepo.get_pending_requests(department_id)

    @staticmethod
    async def approve_request(
        request_id: int,
        reviewed_by: int,
        work_mode: WorkMode,
        full_name: str | None = None,
    ) -> RegistrationResult:
        """
        Подтвердить заявку на регистрацию.

        Создаёт пользователя в системе.

        Args:
            request_id: ID заявки.
            reviewed_by: ID менеджера.
            work_mode: Режим работы (выбирается менеджером).
            full_name: ФИО (если не было указано).

        Returns:
            Результат с ID созданного пользователя.
        """
        # Получаем данные заявки и приглашения
        request_data = await RegistrationRepo.get_request_by_id(request_id)
        if not request_data:
            return RegistrationResult(success=False, error=RegistrationError.REQUEST_NOT_FOUND)

        if request_data["status"] != "pending":
            return RegistrationResult(success=False, error=RegistrationError.REQUEST_NOT_FOUND)

        invite_creator_id = request_data.get("created_by")
        reviewer = await UsersRepo.get_by_id(reviewed_by)
        reviewer_is_admin = bool(reviewer and reviewer.get("role") == "admin")
        department_id = request_data.get("department_id")

        # Правила авторизации:
        # - OZKO: admin может одобрять любые заявки
        # - OSB: admin/manager может одобрять только свои приглашения (created_by == reviewed_by)
        if invite_creator_id != reviewed_by:
            # Reviewer не создавал приглашение
            if department_id == OZKO_DEPARTMENT_ID and reviewer_is_admin:
                # Admin может одобрять OZKO заявки от любого создателя
                pass
            else:
                # OSB заявки — только создатель может одобрить
                return RegistrationResult(success=False, error=RegistrationError.NOT_INVITE_CREATOR)

        # Формируем full_name
        user_full_name = full_name or request_data.get("expected_full_name")
        if not user_full_name:
            parts = [request_data.get("first_name"), request_data.get("last_name")]
            user_full_name = " ".join(p for p in parts if p) or "Неизвестно"

        # Создаём пользователя
        user_id = await UsersRepo.create(
            telegram_id=request_data["telegram_id"],
            username=request_data.get("username"),
            first_name=request_data.get("first_name"),
            last_name=request_data.get("last_name"),
            full_name=user_full_name,
            role="employee",
            department_id=request_data["department_id"],
            work_mode=work_mode,
        )

        # Отмечаем приглашение как использованное
        await RegistrationRepo.mark_invite_used(request_data["invite_id"], user_id)

        # Обновляем статус заявки
        await RegistrationRepo.approve_request(request_id, reviewed_by)

        return RegistrationResult(
            success=True,
            user_id=user_id,
            data={"telegram_id": request_data["telegram_id"], "full_name": user_full_name},
        )

    @staticmethod
    async def reject_request(
        request_id: int,
        reviewed_by: int,
        reason: str | None = None,
    ) -> RegistrationResult:
        """
        Отклонить заявку на регистрацию.

        Args:
            request_id: ID заявки.
            reviewed_by: ID менеджера.
            reason: Причина отклонения.

        Returns:
            Результат операции.
        """
        request_data = await RegistrationRepo.get_request_by_id(request_id)
        if not request_data:
            return RegistrationResult(success=False, error=RegistrationError.REQUEST_NOT_FOUND)

        if request_data["status"] != "pending":
            return RegistrationResult(success=False, error=RegistrationError.REQUEST_NOT_FOUND)

        await RegistrationRepo.reject_request(request_id, reviewed_by, reason)

        return RegistrationResult(
            success=True,
            data={"telegram_id": request_data["telegram_id"]},
        )

    @staticmethod
    async def check_user_status(telegram_id: int) -> dict[str, Any]:
        """
        Проверить статус пользователя.

        Args:
            telegram_id: Telegram ID пользователя.

        Returns:
            Словарь с информацией о статусе:
            - is_registered: bool - зарегистрирован ли пользователь
            - has_pending: bool - есть ли ожидающая заявка
            - user: dict | None - данные пользователя
            - pending_request: dict | None - данные заявки
        """
        user = await UsersRepo.get_by_telegram_id(telegram_id)
        if user:
            return {
                "is_registered": True,
                "has_pending": False,
                "user": user,
                "pending_request": None,
            }

        pending = await RegistrationRepo.get_pending_request_by_telegram_id(telegram_id)
        return {
            "is_registered": False,
            "has_pending": pending is not None,
            "user": None,
            "pending_request": pending,
        }
