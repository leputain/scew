"""
EWS Mailer — отправка email через Exchange Web Services.

Использует on-prem Exchange с NTLM-аутентификацией.
Не использует SMTP и autodiscover.

ВАЖНО: НЕ логировать credentials, URL с паролями, body, attachment bytes.
"""

import os
from dataclasses import dataclass

from core.screw_core.utils.logging import get_logger

logger = get_logger(__name__)


@dataclass
class Attachment:
    """Вложение для email."""

    filename: str
    content: bytes
    mime_type: str


class EwsMailerError(Exception):
    """Базовая ошибка EwsMailer."""

    pass


class EwsConfigError(EwsMailerError):
    """Ошибка конфигурации EWS."""

    pass


class EwsSendError(EwsMailerError):
    """Ошибка отправки email."""

    pass


def _get_ews_url() -> str | None:
    """Получить EWS URL из env."""
    return os.getenv("EXCHANGE_EWS_URL") or None


def _get_username() -> str | None:
    """Получить username из env."""
    return os.getenv("EXCHANGE_USERNAME") or None


def _get_password() -> str | None:
    """Получить password из env."""
    return os.getenv("EXCHANGE_PASSWORD") or None


def _get_auth_type() -> str:
    """Получить тип аутентификации из env."""
    return os.getenv("EXCHANGE_AUTH", "NTLM")


class EwsMailer:
    """
    Mailer для отправки email через Exchange EWS.

    Использует библиотеку exchangelib с NTLM-аутентификацией.
    """

    def __init__(
        self,
        ews_url: str | None = None,
        username: str | None = None,
        password: str | None = None,
        auth_type: str | None = None,
    ):
        """
        Инициализировать mailer.

        Args:
            ews_url: URL EWS сервера. Если не задан, берётся из env.
            username: Username. Если не задан, берётся из env.
            password: Password. Если не задан, берётся из env.
            auth_type: Тип аутентификации (NTLM). Если не задан, берётся из env.

        Raises:
            EwsConfigError: Если не заданы обязательные параметры.
        """
        self.ews_url = ews_url or _get_ews_url()
        self.username = username or _get_username()
        self.password = password or _get_password()
        self.auth_type = auth_type or _get_auth_type()

        # Не логируем credentials!
        if not self.ews_url:
            raise EwsConfigError("EXCHANGE_EWS_URL не задан")
        if not self.username:
            raise EwsConfigError("EXCHANGE_USERNAME не задан")
        if not self.password:
            raise EwsConfigError("EXCHANGE_PASSWORD не задан")

        self._account = None

    def _get_account(self):
        """
        Получить или создать Account для отправки.

        Returns:
            exchangelib.Account

        Raises:
            EwsMailerError: При ошибке подключения.
        """
        if self._account is not None:
            return self._account

        try:
            from exchangelib import (
                DELEGATE,
                Account,
                Configuration,
                Credentials,
            )

            # Отключаем проверку SSL для on-prem (если требуется)
            # from exchangelib.protocol import BaseProtocol, NoVerifyHTTPAdapter
            # BaseProtocol.HTTP_ADAPTER_CLS = NoVerifyHTTPAdapter

            credentials = Credentials(
                username=self.username,
                password=self.password,
            )

            # Explicit EWS URL, no autodiscover
            config = Configuration(
                server=self._extract_server_from_url(),
                credentials=credentials,
                service_endpoint=self.ews_url,
                auth_type=self.auth_type,
            )

            # Извлекаем email из username (если username = email)
            # или используем username как primary_smtp_address
            primary_email = self.username
            if "\\" in self.username:
                # DOMAIN\user format — нужен отдельный email
                # В этом случае нужно задавать REPORT_FROM
                primary_email = os.getenv("REPORT_FROM") or self.username

            self._account = Account(
                primary_smtp_address=primary_email,
                config=config,
                autodiscover=False,
                access_type=DELEGATE,
            )

            logger.debug("EWS account создан для отправки")
            return self._account

        except ImportError:
            raise EwsMailerError("exchangelib не установлен. Установите: pip install exchangelib")
        except Exception as e:
            # Логируем тип ошибки (без credentials)
            logger.error("EWS: ошибка создания account — %s: %s", type(e).__name__, e)
            raise EwsMailerError(f"Ошибка подключения к Exchange: {type(e).__name__}")

    def _extract_server_from_url(self) -> str:
        """Извлечь hostname из EWS URL."""
        from urllib.parse import urlparse

        parsed = urlparse(self.ews_url)
        return parsed.netloc

    def send(
        self,
        to: list[str],
        subject: str,
        body_text: str,
        cc: list[str] | None = None,
        attachments: list[Attachment] | None = None,
    ) -> None:
        """
        Отправить email.

        Args:
            to: Список получателей (TO).
            subject: Тема письма.
            body_text: Текст письма (plain text).
            cc: Список получателей копии (CC).
            attachments: Список вложений.

        Raises:
            EwsSendError: При ошибке отправки.
        """
        if not to:
            raise EwsSendError("Список получателей пуст")

        try:
            from exchangelib import FileAttachment, Message

            account = self._get_account()

            message = Message(
                account=account,
                subject=subject,
                body=body_text,
                to_recipients=to,
                cc_recipients=cc or [],
            )

            # Добавляем вложения
            if attachments:
                for att in attachments:
                    file_attachment = FileAttachment(
                        name=att.filename,
                        content=att.content,
                        content_type=att.mime_type,
                    )
                    message.attach(file_attachment)

            message.send()

            # Логируем только безопасную информацию
            logger.info(
                "EWS: email отправлен — recipients=%d, attachments=%d",
                len(to) + len(cc or []),
                len(attachments or []),
            )

        except EwsMailerError:
            raise
        except Exception as e:
            # Не логируем детали — могут содержать sensitive data
            logger.error("EWS: ошибка отправки email — %s", type(e).__name__)
            raise EwsSendError(f"Ошибка отправки: {type(e).__name__}")

    def close(self) -> None:
        """Закрыть соединение (если открыто)."""
        if self._account is not None:
            try:
                self._account.protocol.close()
            except Exception:
                pass
            self._account = None


def is_ews_configured() -> bool:
    """
    Проверить, настроен ли EWS.

    Returns:
        True если все обязательные параметры заданы.
    """
    return bool(
        _get_ews_url()
        and _get_username()
        and _get_password()
    )
