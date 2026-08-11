"""Native aiogram chat_member router for welcome/farewell messages."""

from __future__ import annotations

from pathlib import Path

from aiogram import Bot, Router
from aiogram.types import ChatMemberUpdated

from apps.attendance_bot.app.config import (
    get_attendance_group_id,
    get_welcome_osb_contact,
    get_welcome_ozko_contact,
    get_workplace_setup_url,
)
from apps.attendance_bot.app.ui.keyboards_neutral import DEPT_NAMES
from core.screw_core.repos.users_repo import UsersRepo
from core.screw_core.utils.logging import get_logger

logger = get_logger(__name__)
router = Router(name="attendance_chat_member")

_TEXTS_DIR = Path(__file__).resolve().parents[2] / "ui" / "texts"
WELCOME_MESSAGE_PATH = _TEXTS_DIR / "welcome_message.md"
FAREWELL_MESSAGE_PATH = _TEXTS_DIR / "farewell_message.md"
FAREWELL_KICKED_MESSAGE_PATH = _TEXTS_DIR / "farewell_kicked_message.md"


def _load_template(path: Path, fallback: str, **values: str) -> str:
    try:
        return path.read_text(encoding="utf-8").format(**values)
    except Exception as exc:
        logger.warning("aiogram chat_member: не удалось загрузить шаблон %s: %s", path.name, exc)
        return fallback


def get_welcome_message(full_name: str, department_name: str = "—") -> str:
    workplace_setup_url = get_workplace_setup_url()
    workplace_setup_line = (
        f"\n\nИнструкция по настройке рабочего места: {workplace_setup_url}" if workplace_setup_url else ""
    )
    return _load_template(
        WELCOME_MESSAGE_PATH,
        (
            "👋 Добро пожаловать в команду!\n\n"
            f"Представляем нового коллегу:\n👤 {full_name}\n🏢 Отдел: {department_name}"
        ),
        full_name=full_name,
        department_name=department_name,
        ozko_contact=get_welcome_ozko_contact(),
        osb_contact=get_welcome_osb_contact(),
        workplace_setup_line=workplace_setup_line,
    )


def get_farewell_message(full_name: str, is_kicked: bool = False) -> str:
    if is_kicked:
        return _load_template(
            FAREWELL_KICKED_MESSAGE_PATH,
            f"👋 {full_name} был(а) исключён(а) из рабочей группы.",
            full_name=full_name,
        )
    return _load_template(
        FAREWELL_MESSAGE_PATH,
        f"👋 {full_name} покинул(а) рабочую группу.",
        full_name=full_name,
    )


@router.chat_member()
async def on_chat_member(event: ChatMemberUpdated, bot: Bot) -> None:
    target_group_id = get_attendance_group_id()
    if not target_group_id or event.chat.id != target_group_id:
        return

    old_status = event.old_chat_member.status if event.old_chat_member else "left"
    new_status = event.new_chat_member.status if event.new_chat_member else "left"

    if old_status in ("left", "kicked") and new_status in ("member", "administrator"):
        telegram_id = event.new_chat_member.user.id
        user = await UsersRepo.get_by_telegram_id(telegram_id)
        if not user:
            return

        full_name = user.get("full_name") or "коллега"
        department_name = DEPT_NAMES.get(user.get("department_id"), "—")
        try:
            await bot.send_message(chat_id=event.chat.id, text=get_welcome_message(full_name, department_name))
            logger.info("aiogram chat_member: welcome sent telegram_id=%s chat_id=%s", telegram_id, event.chat.id)
        except Exception as exc:
            logger.warning("aiogram chat_member: welcome failed telegram_id=%s: %s", telegram_id, exc)
        return

    if old_status in ("member", "administrator") and new_status in ("left", "kicked"):
        telegram_id = event.old_chat_member.user.id
        is_kicked = new_status == "kicked"
        user = await UsersRepo.get_by_telegram_id(telegram_id)
        if not user:
            return
        full_name = (user.get("full_name") if user else None) or "пользователь"
        try:
            await bot.send_message(chat_id=event.chat.id, text=get_farewell_message(full_name, is_kicked=is_kicked))
            logger.info(
                "aiogram chat_member: farewell sent telegram_id=%s chat_id=%s kicked=%s",
                telegram_id,
                event.chat.id,
                is_kicked,
            )
        except Exception as exc:
            logger.warning("aiogram chat_member: farewell failed telegram_id=%s: %s", telegram_id, exc)
