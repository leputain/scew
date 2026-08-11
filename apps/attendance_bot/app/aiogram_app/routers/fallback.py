"""Native aiogram fallback handlers."""

from __future__ import annotations

from aiogram import F, Router
from aiogram.types import CallbackQuery

from core.screw_core.utils.logging import get_logger

logger = get_logger(__name__)
router = Router(name="attendance_fallback")


def _callback_family(data: str | None) -> str:
    if not data:
        return "<empty>"
    return data.split(":", 1)[0][:32]


@router.callback_query(F.data)
async def unknown_callback(callback: CallbackQuery) -> None:
    family = _callback_family(callback.data)
    telegram_id = callback.from_user.id if callback.from_user else None
    logger.warning("aiogram fallback: unhandled callback family=%s telegram_id=%s", family, telegram_id)
    await callback.answer("Действие устарело. Откройте меню заново через /start.", show_alert=False)
