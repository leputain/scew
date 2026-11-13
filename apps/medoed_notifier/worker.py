"""Durable, send-only Telegram notifications with conservative ambiguous-send handling.

The worker deliberately does not import attendance startup, its DB pool, or its
configuration. It receives the existing bot token and a separate restricted DB
credential through files. No request updates, webhook changes or polling occur.
"""

from __future__ import annotations

import asyncio
import html
import json
import logging
import math
import os
import random
import signal
import time
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse
from uuid import UUID, uuid4

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

from core.screw_core.services.telegram_delivery import send_system_message
from core.screw_core.utils.logging import setup_logging

logger = logging.getLogger(__name__)
HEARTBEAT = Path("/tmp/medoed_notifier_heartbeat")
LEASE_SECONDS = 120
CLAIM_SQL = """
WITH candidate AS (
    SELECT spin_id FROM medoed.notifications
    WHERE state = 'pending' AND available_at <= clock_timestamp()
      AND created_at <= clock_timestamp() - interval '5 seconds'
    ORDER BY available_at, created_at, spin_id
    FOR UPDATE SKIP LOCKED LIMIT 1
)
UPDATE medoed.notifications AS n
SET state = 'processing', attempts = attempts + 1,
    claimed_at = clock_timestamp(), claim_token = $1, last_error_code = NULL
FROM candidate WHERE n.spin_id = candidate.spin_id
RETURNING n.spin_id, n.employee_id, n.additional_days, n.claim_token, n.attempts
"""
EXPIRE_SQL = """
UPDATE medoed.notifications
SET state = 'uncertain', claim_token = NULL, last_error_code = 'worker_interrupted'
WHERE state = 'processing'
  AND (claimed_at IS NULL OR claimed_at < clock_timestamp() - make_interval(secs => $1))
"""
SETTLE_SQL = """
UPDATE medoed.notifications
SET state = $3, claim_token = NULL, last_error_code = $4,
    available_at = COALESCE($5, available_at),
    sent_at = CASE WHEN $3 = 'sent' THEN clock_timestamp() ELSE sent_at END,
    telegram_message_id = COALESCE($6, telegram_message_id)
WHERE spin_id = $1 AND claim_token = $2 AND state = 'processing'
"""
# Table names are static code, never derived from configuration or a request.
GROUP_CLAIM_SQL = CLAIM_SQL.replace("medoed.notifications", "medoed.group_notifications").replace(
    "n.employee_id, n.additional_days", "n.employee_id, n.employee_name, n.additional_days")
GROUP_EXPIRE_SQL = EXPIRE_SQL.replace("medoed.notifications", "medoed.group_notifications")
GROUP_SETTLE_SQL = SETTLE_SQL.replace("medoed.notifications", "medoed.group_notifications")
CLAIMS = {"dm": CLAIM_SQL, "group": GROUP_CLAIM_SQL}
EXPIRATIONS = {"dm": EXPIRE_SQL, "group": GROUP_EXPIRE_SQL}
SETTLEMENTS = {"dm": SETTLE_SQL, "group": GROUP_SETTLE_SQL}


@dataclass(frozen=True, slots=True)
class GroupTarget:
    chat_id: int
    message_thread_id: int | None = None


@dataclass(frozen=True, slots=True)
class Config:
    database: dict[str, Any]
    token: str
    group_target: GroupTarget
    proxy: str | None = None
    interval: float = 1.0


def load_config(environ: dict[str, str] | None = None) -> Config:
    """Read only the three specified secret files; errors never include their values."""
    env = os.environ if environ is None else environ
    try:
        db_file = env["MEDOED_NOTIFIER_DB_CONFIG_FILE"]
        token_file = env["SCREW_BOT_TOKEN_FILE"]
        group_file = env["MEDOED_NOTIFIER_GROUP_CONFIG_FILE"]
        database = json.loads(Path(db_file).read_text(encoding="utf-8"))
        token = Path(token_file).read_text(encoding="utf-8").strip()
        group = json.loads(Path(group_file).read_text(encoding="utf-8"))
        if not isinstance(database, dict):
            raise ValueError
        required = ("host", "database", "user", "password")
        if any(not isinstance(database.get(key), str) or not database[key] or "\0" in database[key]
               for key in required):
            raise ValueError
        port = database.get("port")
        if type(port) is not int or not 1 <= port <= 65535:
            raise ValueError
        # Only supported properties are copied: the file cannot disable timeouts or change pool limits.
        database = {key: database[key] for key in (*required, "port")}
        token_id, separator, token_secret = token.partition(":")
        if not separator or not token_id.isascii() or not token_id.isdigit() or len(token_secret) < 20:
            raise ValueError
        if any(character.isspace() for character in token):
            raise ValueError
        if not isinstance(group, dict) or type(group.get("chat_id")) is not int:
            raise ValueError
        chat_id = group["chat_id"]
        thread_id = group.get("message_thread_id")
        if not -(2 ** 63) < chat_id < 0:
            raise ValueError
        if thread_id is not None and (type(thread_id) is not int or not 1 <= thread_id <= 2_147_483_647):
            raise ValueError
        # A supplied title is informational only and cannot choose/authorize a destination.
        group_target = GroupTarget(chat_id=chat_id, message_thread_id=thread_id)
        proxy = env.get("TELEGRAM_PROXY_URL", "").strip() or None
        if proxy:
            parsed = urlparse(proxy)
            if parsed.scheme not in {"http", "https", "socks4", "socks5"} or not parsed.hostname:
                raise ValueError
        interval = float(env.get("MEDOED_NOTIFIER_INTERVAL", "1"))
        if not math.isfinite(interval) or not 0.5 <= interval <= 60:
            raise ValueError
    except (KeyError, OSError, ValueError, TypeError):
        raise ValueError("Invalid notifier configuration or unreadable secret files") from None
    return Config(database=database, token=token, group_target=group_target, proxy=proxy, interval=interval)


def office_days(days: int) -> str:
    if days % 10 == 1 and days % 100 != 11:
        return f"{days} офисный день"
    if days % 10 in (2, 3, 4) and days % 100 not in (12, 13, 14):
        return f"{days} офисных дня"
    return f"{days} офисных дней"


def notification_text(spin_id: str, additional_days: int) -> str:
    """Use immutable numeric metadata, never infer a day count from an editable label."""
    if type(additional_days) is not int or not 0 <= additional_days <= 31:
        raise ValueError("Invalid additional day count")
    # This also rejects arbitrary markup/URLs in a corrupted notification identifier.
    UUID(spin_id)
    if additional_days == 0:
        result = "Дополнительных офисных дней нет. Твой обычный график остаётся прежним."
    else:
        result = f"Дополнительно к твоему обычному графику: <b>{office_days(additional_days)}</b>."
    return ("🎰 <b>Медоед сделал свой выбор!</b>\n\n"
            f"{result}\n\n"
            "Результат сохранён в журнале Медоеда.")


def group_notification_text(spin_id: str, employee_name: str, additional_days: int) -> str:
    # Reuse the immutable day-count/UUID validation without copying editable option labels.
    notification_text(spin_id, additional_days)
    if (not isinstance(employee_name, str) or not employee_name.strip() or len(employee_name) > 500
            or any(unicodedata.category(char) in {"Cc", "Zl", "Zp"} for char in employee_name)):
        raise ValueError("Invalid employee snapshot")
    name = html.escape(employee_name.strip(), quote=False)
    result = ("Дополнительных офисных дней нет." if additional_days == 0 else
              f"Дополнительно к обычному графику: <b>{office_days(additional_days)}</b>.")
    return ("🎰 <b>Медоед сделал свой выбор!</b>\n\n"
            f"Сотрудник: <b>{name}</b>\n\n{result}\n\n"
            "Результат сохранён в журнале Медоеда.")


class Notifier:
    def __init__(self, pool: Any, bot: Any, *, group_target: GroupTarget | None = None,
                 interval: float = 1.0, heartbeat: Path = HEARTBEAT):
        self.pool = pool
        self.bot = bot
        self.interval = interval
        self.heartbeat = heartbeat
        self.group_target = group_target
        self.channels = ("dm", "group") if group_target is not None else ("dm",)
        self.channel_ready = {channel: False for channel in self.channels}
        self.paused = False

    async def preflight(self) -> None:
        """Check the real relation/column permissions without reading names or recipients."""
        await self.pool.fetch("""SELECT spin_id, employee_id, additional_days, state, attempts,
            available_at, claimed_at, claim_token, sent_at, telegram_message_id, last_error_code, created_at
            FROM medoed.notifications LIMIT 0""")
        await self.pool.fetch("SELECT id, deleted_at FROM medoed.spins LIMIT 0")
        await self.pool.fetch("SELECT user_id, telegram_id FROM medoed.notification_recipients LIMIT 0")
        if self.group_target is not None:
            await self.pool.fetch("""SELECT spin_id, employee_id, employee_name, additional_days, state, attempts,
                available_at, claimed_at, claim_token, sent_at, telegram_message_id, last_error_code, created_at
                FROM medoed.group_notifications LIMIT 0""")
            await self.pool.fetch("SELECT id FROM medoed.ozko_employees LIMIT 0")

    async def expire_interrupted(self, channel: str = "dm") -> None:
        # Expired ownership never causes an automatic resend: a crashed worker may have sent already.
        await self.pool.execute(EXPIRATIONS[channel], LEASE_SECONDS)

    async def claim(self, channel: str = "dm") -> Any:
        # A single autocommit statement commits ownership before the Telegram request.
        return await self.pool.fetchrow(CLAIMS[channel], uuid4())

    async def settle(self, item: Any, state: str, error: str | None = None,
                     *, channel: str = "dm", available_at: datetime | None = None,
                     message_id: int | None = None) -> None:
        result = await self.pool.execute(SETTLEMENTS[channel], item["spin_id"], item["claim_token"], state,
                                         error, available_at, message_id)
        if result != "UPDATE 1":
            # No second send follows an expired or superseded ownership token.
            logger.warning("medoed_notifier ownership_lost channel=%s", channel)
        else:
            logger.info("medoed_notifier delivery channel=%s state=%s code=%s", channel, state, error or "ok")

    async def deliver(self, item: Any, channel: str = "dm") -> None:
        if channel not in self.channels:
            raise ValueError("Unconfigured notification channel")
        if channel == "group":
            target = await self.pool.fetchrow("""
                SELECT s.deleted_at,
                    EXISTS(SELECT 1 FROM medoed.ozko_employees AS e WHERE e.id = $2) AS eligible
                FROM medoed.spins AS s WHERE s.id = $1
                """, item["spin_id"], item["employee_id"])
        else:
            # Resolve only the queued immutable employee ID; no surname matching or fallback recipient.
            target = await self.pool.fetchrow("""
            SELECT s.deleted_at, r.telegram_id
            FROM medoed.spins AS s
            LEFT JOIN medoed.notification_recipients AS r ON r.user_id = $2
            WHERE s.id = $1
            """, item["spin_id"], item["employee_id"])
        if target is None or target["deleted_at"] is not None:
            await self.settle(item, "cancelled", "deleted_spin", channel=channel)
            return
        if ((channel == "dm" and (type(target["telegram_id"]) is not int or target["telegram_id"] <= 0))
                or (channel == "group" and not target["eligible"])):
            await self.settle(item, "cancelled", "recipient_unavailable", channel=channel)
            return
        try:
            text = (group_notification_text(item["spin_id"], item["employee_name"], item["additional_days"])
                    if channel == "group" else notification_text(item["spin_id"], item["additional_days"]))
        except (ValueError, TypeError, AttributeError):
            await self.settle(item, "failed", "invalid_payload", channel=channel)
            return
        await self.send(item, text, channel=channel,
                        chat_id=self.group_target.chat_id if channel == "group" else target["telegram_id"],
                        message_thread_id=self.group_target.message_thread_id if channel == "group" else None)

    async def send(self, item: Any, text: str, *, channel: str, chat_id: int,
                   message_thread_id: int | None = None) -> None:
        try:
            # rich_first catches broad errors and falls back immediately; it can duplicate a timed-out send.
            result = await send_system_message(self.bot, chat_id=chat_id, html_text=text,
                                               message_thread_id=message_thread_id,
                                               delivery_mode="html", reaction_emoji=None)
        except TelegramRetryAfter as error:
            # A Bot API 429 is an explicit rejection, so a later retry is safe.
            retry_after = max(1, int(error.retry_after)) + 1 + random.random()
            available = datetime.now(timezone.utc) + timedelta(seconds=retry_after)
            await self.settle(item, "pending", "rate_limited", channel=channel, available_at=available)
        except TelegramForbiddenError:
            await self.settle(item, "failed", "forbidden", channel=channel)
        except TelegramBadRequest:
            await self.settle(item, "failed", "bad_request", channel=channel)
        except TelegramUnauthorizedError:
            await self.settle(item, "failed", "unauthorized", channel=channel)
            # Preserve other pending notifications until credentials are repaired and the worker restarted.
            self.paused = True
            logger.error("medoed_notifier delivery_paused code=unauthorized")
        except (TelegramNetworkError, TelegramServerError, TimeoutError, OSError):
            await self.settle(item, "uncertain", "transport_uncertain", channel=channel)
        except asyncio.CancelledError:
            # Shutdown midway through HTTP is ambiguous. If killed before this update, lease expiry handles it.
            try:
                await asyncio.shield(self.settle(item, "uncertain", "delivery_interrupted", channel=channel))
            finally:
                raise
        except Exception:
            # Unknown exceptions after starting the send cannot establish that Telegram rejected it.
            await self.settle(item, "uncertain", "unexpected_delivery_error", channel=channel)
        else:
            message_id = getattr(result.message, "message_id", None)
            if type(message_id) is not int or message_id <= 0:
                await self.settle(item, "uncertain", "missing_message_id", channel=channel)
                return
            await self.settle(item, "sent", channel=channel, message_id=message_id)

    async def process_channel(self, channel: str) -> bool:
        try:
            await self.expire_interrupted(channel)
            item = await self.claim(channel)
            if item is not None:
                await self.deliver(item, channel)
            return True
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.warning("medoed_notifier tick_failed channel=%s code=dependency_unavailable", channel)
            return False

    def touch_heartbeat(self) -> None:
        self.heartbeat.write_text(str(int(time.time())), encoding="ascii")

    async def channel_loop(self, channel: str, stop: asyncio.Event) -> None:
        while not stop.is_set():
            try:
                if not self.paused:
                    self.channel_ready[channel] = await self.process_channel(channel)
                    if all(self.channel_ready.values()) and not self.paused:
                        self.touch_heartbeat()
            except asyncio.CancelledError:
                raise
            except Exception:
                # SQL and transport exception messages can contain private values. Log only a fixed code.
                self.channel_ready[channel] = False
                logger.warning("medoed_notifier tick_failed channel=%s code=dependency_unavailable", channel)
            try:
                await asyncio.wait_for(stop.wait(), timeout=self.interval)
            except TimeoutError:
                pass

    async def run(self, stop: asyncio.Event) -> None:
        await self.preflight()
        # Each channel owns its loop: even the next group row can progress during a slow DM request.
        await asyncio.gather(*(self.channel_loop(channel, stop) for channel in self.channels))


async def main() -> int:
    setup_logging(logging.INFO)
    # Dependencies may include URLs/credentials in diagnostics; this worker logs fixed codes itself.
    for dependency in ("aiogram", "aiohttp", "aiohttp_socks", "asyncio", "asyncpg"):
        logging.getLogger(dependency).setLevel(logging.CRITICAL + 1)
    pool = None
    bot = None
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for signum in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(signum, stop.set)
        except NotImplementedError:  # Windows is supported for local synthetic tests.
            signal.signal(signum, lambda *_: loop.call_soon_threadsafe(stop.set))
    try:
        config = load_config()
        pool = await asyncpg.create_pool(**config.database, min_size=1, max_size=2,
                                        timeout=5, command_timeout=5,
                                        server_settings={"application_name": "medoed-notifier",
                                                         "statement_timeout": "5000",
                                                         "idle_in_transaction_session_timeout": "10000"})
        bot = Bot(token=config.token, session=AiohttpSession(proxy=config.proxy, limit=4, timeout=20))
        logger.info("medoed_notifier starting send_only=true")
        await Notifier(pool, bot, group_target=config.group_target, interval=config.interval).run(stop)
        return 0
    except asyncio.CancelledError:
        return 0
    except Exception:
        logger.error("medoed_notifier startup_failed code=configuration_or_database")
        return 1
    finally:
        if bot is not None:
            try:
                await bot.session.close()
            except Exception:
                logger.warning("medoed_notifier cleanup_failed code=telegram_session")
        if pool is not None:
            try:
                await asyncio.wait_for(pool.close(), timeout=10)
            except TimeoutError:
                pool.terminate()
            except Exception:
                logger.warning("medoed_notifier cleanup_failed code=database_pool")
