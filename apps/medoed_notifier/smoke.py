"""Operator-only, one-shot, explicitly labelled Telegram test; no business data writes.

Run from server root through Docker with private stdin JSON, keeping the worker UID:
``sudo docker exec -i CONTAINER python3 -m apps.medoed_notifier.smoke``.
Docker/host access is the authorization boundary; an in-container UID check is not.
The process reads the normal notifier secret files. It prints only sanitized JSON,
never retries a send, and must not be rerun after an uncertain response without
checking Telegram with the recipient first.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
from typing import Any

import asyncpg
from aiogram import Bot
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.exceptions import (
    TelegramBadRequest,
    TelegramForbiddenError,
    TelegramNetworkError,
    TelegramRetryAfter,
    TelegramServerError,
    TelegramUnauthorizedError,
)

from apps.medoed_notifier.worker import load_config, office_days
from core.screw_core.services.telegram_delivery import send_system_message


def read_payload(raw: str) -> tuple[int, int]:
    """Accept a stable server employee ID only; no names, arbitrary chat IDs or message text."""
    try:
        data = json.loads(raw)
        if not isinstance(data, dict) or set(data) != {"employeeId", "additionalDays"}:
            raise ValueError
        employee_id = data["employeeId"]
        if (not isinstance(employee_id, str) or not employee_id.isascii() or not employee_id.isdecimal()
                or employee_id.startswith("0") or len(employee_id) > 19):
            raise ValueError
        identifier = int(employee_id)
        days = data["additionalDays"]
        if identifier > 9_223_372_036_854_775_807 or type(days) is not int or not 0 <= days <= 31:
            raise ValueError
        return identifier, days
    except (ValueError, KeyError, TypeError):
        raise ValueError("Invalid smoke request") from None


def test_message(days: int) -> str:
    if type(days) is not int or not 0 <= days <= 31:
        raise ValueError("Invalid day count")
    example = office_days(days) if days else "без дополнительных офисных дней"
    return ("🧪 <b>Тестовое уведомление.</b>\n"
            "<b>Не является назначением офисных дней.</b>\n\n"
            f"Пример результата Медоеда: <b>{example}</b>.\n\n"
            "Это проверка личных уведомлений через SCREW. "
            "Твой рабочий график и история розыгрышей не изменены.")


async def run_smoke(connection: Any, bot: Any, employee_id: int, days: int) -> int:
    """Perform a read-only eligible-recipient lookup and one ordinary message request."""
    recipient = await connection.fetchrow(
        "SELECT telegram_id FROM medoed.notification_recipients WHERE user_id = $1", employee_id)
    if recipient is None or type(recipient["telegram_id"]) is not int or recipient["telegram_id"] <= 0:
        raise ValueError("Recipient unavailable")
    # Read-only identity check. Never print username, ID, profile or token.
    identity = await bot.get_me(request_timeout=20)
    if not identity.is_bot:
        raise ValueError("Invalid bot identity")
    result = await send_system_message(bot, chat_id=recipient["telegram_id"], html_text=test_message(days),
                                      delivery_mode="html", reaction_emoji=None)
    message_id = getattr(result.message, "message_id", None)
    if type(message_id) is not int or message_id <= 0:
        raise RuntimeError("Uncertain delivery")
    return message_id


async def main() -> int:
    # This CLI is operational, not an app endpoint. No recipient input in argv or environment.
    # The service runs without capabilities. UID 0 cannot bypass another UID's
    # mode-0400 secret permissions; keep UID 10001 for the normal Docker exec.
    if not hasattr(os, "geteuid") or os.geteuid() not in {0, 10001}:
        print(json.dumps({"status": "failed", "code": "operator_container_identity_required"}))
        return 2
    logging.disable(logging.CRITICAL)
    connection = None
    bot = None
    try:
        raw = sys.stdin.read(4097)
        if len(raw) > 4096:
            raise ValueError("Invalid smoke request")
        employee_id, days = read_payload(raw)
        config = load_config()
        connection = await asyncpg.connect(**config.database, timeout=5, command_timeout=5,
                                          server_settings={"application_name": "medoed-notifier-smoke",
                                                           "default_transaction_read_only": "on",
                                                           "statement_timeout": "5000"})
        bot = Bot(token=config.token, session=AiohttpSession(proxy=config.proxy, limit=1, timeout=20))
        message_id = await run_smoke(connection, bot, employee_id, days)
    except TelegramRetryAfter:
        result, code = "failed", "rate_limited_no_retry"
    except TelegramForbiddenError:
        result, code = "failed", "forbidden"
    except TelegramBadRequest:
        result, code = "failed", "bad_request"
    except TelegramUnauthorizedError:
        result, code = "failed", "unauthorized"
    except (TelegramNetworkError, TelegramServerError, TimeoutError, OSError):
        result, code = "uncertain", "transport_uncertain_no_retry"
    except (ValueError, asyncpg.PostgresError):
        result, code = "failed", "configuration_or_recipient_unavailable"
    except Exception:
        result, code = "uncertain", "unexpected_failure_no_retry"
    else:
        print(json.dumps({"status": "sent", "message_id": message_id}))
        return 0
    finally:
        if bot is not None:
            try:
                await bot.session.close()
            except Exception:
                pass  # Cleanup cannot disclose secrets or turn a known send into a retry instruction.
        if connection is not None:
            try:
                await connection.close(timeout=5)
            except Exception:
                pass
    print(json.dumps({"status": result, "code": code}))
    return 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
