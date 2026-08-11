from __future__ import annotations

import asyncio
import subprocess
import sys
from datetime import date, datetime
from time import time
from pathlib import Path
from collections.abc import Iterator
from types import SimpleNamespace

import pytest

from apps.attendance_bot.app.aiogram_app.keyboards import to_aiogram_markup
from apps.attendance_bot.app.aiogram_app.main import (
    AIOGRAM_FALLBACK_ROUTER,
    AIOGRAM_ROUTERS,
    create_aiogram_dispatcher,
)
from apps.attendance_bot.app.utils.poll_payload import (
    build_aiogram_poll_payload,
    build_poll_payload,
    build_poll_payload_diagnostics,
    send_aiogram_poll_with_fallback,
)
from core.screw_core.models.enums import AttendanceChoice
from core.screw_core.services.permissions import get_user_permissions


@pytest.fixture(autouse=True)
def _detach_aiogram_routers_between_tests() -> Iterator[None]:
    for router in (*AIOGRAM_ROUTERS, AIOGRAM_FALLBACK_ROUTER):
        router._parent_router = None
    yield
    for router in (*AIOGRAM_ROUTERS, AIOGRAM_FALLBACK_ROUTER):
        router._parent_router = None


def test_poll_payload_adds_bot_api_10_flags_and_media() -> None:
    payload = build_poll_payload(
        [
            {
                "key": "EVOLUTION",
                "label": "МФК «Эволюция»",
                "media": {"type": "photo", "media": "file_id"},
            }
        ],
        {
            "poll_description": "Выберите рабочий формат на сегодня",
            "poll_duration_seconds": 120,
            "poll_media": {"type": "photo", "media": "poll_file_id"},
        },
    )

    assert payload.plain_options == ["МФК «Эволюция»"]
    assert "members_only" not in payload.api_kwargs
    assert payload.api_kwargs["allows_revoting"] is True
    assert payload.api_kwargs["shuffle_options"] is False
    assert payload.api_kwargs["allow_adding_options"] is False
    assert payload.api_kwargs["description"] == "Выберите рабочий формат на сегодня"
    assert payload.api_kwargs["media"] == {"type": "photo", "media": "poll_file_id"}
    assert payload.api_kwargs["open_period"] == 120
    assert payload.is_rich is True


def test_poll_payload_converts_to_aiogram_native_options() -> None:
    payload = build_poll_payload(
        [{"key": "REMOTE", "label": "Удалёнка", "media": {"type": "photo", "media": "file_id"}}],
        {"poll_description": "desc", "poll_duration_seconds": 120},
    )

    options, plain_options, api_kwargs = build_aiogram_poll_payload(payload)

    assert options[0].text == "Удалёнка"
    assert options[0].media.type == "photo"
    assert plain_options == ["Удалёнка"]
    assert "members_only" not in api_kwargs
    assert api_kwargs["description"] == "desc"
    assert api_kwargs["open_period"] == 120


def test_poll_payload_diagnostics_reports_rich_features() -> None:
    diagnostics = build_poll_payload_diagnostics(
        [
            {
                "key": "REMOTE",
                "label": "Удалёнка",
                "persistent_id": "remote",
                "media": {"type": "photo", "media": "file_id"},
            }
        ],
        {
            "poll_description": "desc",
            "poll_duration_seconds": 120,
            "poll_media": {"type": "photo", "media": "poll_file_id"},
        },
    )

    assert diagnostics["has_description"] is True
    assert diagnostics["has_poll_media"] is True
    assert diagnostics["option_media_count"] == 1
    assert diagnostics["persistent_id_count"] == 1
    assert diagnostics["open_period"] == 120
    assert diagnostics["allow_adding_options"] is False
    assert diagnostics["allows_revoting"] is True


def test_poll_payload_adds_open_period_for_hour_long_poll() -> None:
    payload = build_poll_payload(
        [{"key": "REMOTE", "label": "Удалёнка"}],
        {"poll_duration_seconds": 3600},
    )

    assert payload.api_kwargs["open_period"] == 3600


def test_registration_approval_notice_has_start_button(monkeypatch: pytest.MonkeyPatch) -> None:
    from apps.attendance_bot.app.aiogram_app.keyboards import to_aiogram_markup
    from apps.attendance_bot.app.ui.admin_views import build_approve_notice_keyboard

    monkeypatch.delenv("OZKO_BOT_CHAT_INVITE_LINK", raising=False)
    markup = to_aiogram_markup(build_approve_notice_keyboard())

    assert markup.inline_keyboard[0][0].text == "🚀 Начать пользоваться ботом"
    assert markup.inline_keyboard[0][0].callback_data == "panel:user"


def test_registration_approval_notice_uses_url_when_bot_link_is_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    from apps.attendance_bot.app.aiogram_app.keyboards import to_aiogram_markup
    from apps.attendance_bot.app.ui.admin_views import build_approve_notice_keyboard

    monkeypatch.setenv("OZKO_BOT_CHAT_INVITE_LINK", "https://t.me/example_bot")
    markup = to_aiogram_markup(build_approve_notice_keyboard())

    assert markup.inline_keyboard[0][0].text == "🚀 Начать пользоваться ботом"
    assert markup.inline_keyboard[0][0].url == "https://t.me/example_bot?start=menu"
    assert markup.inline_keyboard[0][0].callback_data is None


def test_new_registration_notification_uses_navigation_callbacks() -> None:
    source = Path("apps/attendance_bot/app/aiogram_app/routers/start.py").read_text(encoding="utf-8")
    notify_source = source[source.index("async def _notify_invite_creator") :]

    assert 'callback_data=f"reg_view:{request_id}"' in notify_source
    assert 'callback_data="reg_list"' in notify_source
    assert 'callback_data="mgr:invites_list"' in notify_source


def test_status_calendar_weekend_days_are_selectable() -> None:
    from apps.attendance_bot.app.ui.keyboards_neutral import calendar_keyboard as neutral_calendar_keyboard

    markup = neutral_calendar_keyboard(2026, 5)
    buttons = [button for row in markup.inline_keyboard for button in row]
    saturday = next(button for button in buttons if button.callback_data == "cal:day:2026-05-16")
    sunday = next(button for button in buttons if button.callback_data == "cal:day:2026-05-17")

    assert saturday.text == "16"
    assert sunday.text == "17"


def test_day_off_does_not_block_vacation_or_sick_range_overlap() -> None:
    from core.screw_core.models.enums import DayStatusKind
    from core.screw_core.services.day_status import is_blocking_absence_range_overlap

    assert is_blocking_absence_range_overlap(DayStatusKind.DAY_OFF.value) is False
    assert is_blocking_absence_range_overlap(DayStatusKind.VACATION.value) is True
    assert is_blocking_absence_range_overlap(DayStatusKind.SICK.value) is True
    assert is_blocking_absence_range_overlap(DayStatusKind.LEAVE.value) is True


def test_manager_guards_limit_department_and_invite_scope() -> None:
    from apps.attendance_bot.app.aiogram_app.routers.admin import (
        _manager_can_manage_invite,
        _manager_can_use_department,
    )

    manager = {"id": 10, "role": "manager", "department_id": 1}
    admin = {"id": 20, "role": "admin", "department_id": None}

    assert _manager_can_use_department(manager, 1) is True
    assert _manager_can_use_department(manager, 2) is False
    assert _manager_can_use_department(admin, 2) is True

    assert _manager_can_manage_invite(manager, {"created_by": 10, "department_id": 1}) is True
    assert _manager_can_manage_invite(manager, {"created_by": 11, "department_id": 1}) is False
    assert _manager_can_manage_invite(manager, {"created_by": 10, "department_id": 2}) is False
    assert _manager_can_manage_invite(admin, {"created_by": 11, "department_id": 2}) is True


def test_manager_remove_role_uses_existing_employee_role() -> None:
    paths = [Path("apps/attendance_bot/app/aiogram_app/routers/admin_panel.py")]

    for path in paths:
        text = path.read_text(encoding="utf-8")
        assert 'role="employee"' in text
        assert 'role="user"' not in text
        assert '"new_role": "user"' not in text


def test_used_invite_is_not_rendered_as_active() -> None:
    from datetime import datetime, timedelta, timezone

    from apps.attendance_bot.app.ui.admin_panel_views import format_invite_screen, get_invite_status

    invite = {
        "department_id": 1,
        "token": "abcdef1234567890abcdef1234567890",
        "expires_at": datetime.now(timezone.utc) + timedelta(minutes=30),
        "used_by": 42,
    }

    status_text, is_active = get_invite_status(invite)
    rendered = format_invite_screen(invite, "example_bot")

    assert status_text == "⚫ Использована"
    assert is_active is False
    assert "https://t.me/example_bot?start=" not in rendered
    assert "Ссылка уже недействительна" in rendered


def test_welcome_message_introduces_joined_user_with_department(monkeypatch: pytest.MonkeyPatch) -> None:
    from apps.attendance_bot.app.aiogram_app.routers.chat_member import get_welcome_message

    monkeypatch.delenv("ATTENDANCE_WELCOME_OZKO_CONTACT", raising=False)
    monkeypatch.delenv("ATTENDANCE_WELCOME_OSB_CONTACT", raising=False)
    monkeypatch.delenv("ATTENDANCE_WORKPLACE_SETUP_URL", raising=False)
    rendered = get_welcome_message("Иванов Иван Иванович", "ОЗКО")

    assert "Иванов Иван Иванович" in rendered
    assert "ОЗКО" in rendered
    assert "Представляем нового коллегу" in rendered
    assert "уточните у администратора" in rendered


def test_welcome_message_uses_runtime_contacts_without_hardcoded_private_data(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from apps.attendance_bot.app.aiogram_app.routers.chat_member import get_welcome_message

    monkeypatch.setenv("ATTENDANCE_WELCOME_OZKO_CONTACT", "@manager_one")
    monkeypatch.setenv("ATTENDANCE_WELCOME_OSB_CONTACT", "@manager_two")
    monkeypatch.setenv("ATTENDANCE_WORKPLACE_SETUP_URL", "https://intranet.example/setup")

    rendered = get_welcome_message("Петров Петр Петрович", "ОСБ")

    assert "@manager_one" in rendered
    assert "@manager_two" in rendered
    assert "https://intranet.example/setup" in rendered


def test_admin_panel_guards_are_private_chat_only() -> None:
    source = Path("apps/attendance_bot/app/aiogram_app/routers/admin_panel.py").read_text(encoding="utf-8")

    require_message = source[source.index("async def _require_admin_message") : source.index("async def _require_admin_callback")]
    require_callback = source[source.index("async def _require_admin_callback") : source.index("async def _edit_callback_message")]

    assert "_reject_if_group_message(message)" in require_message
    assert "_reject_if_group_callback(callback)" in require_callback

    state_message_handlers = [
        "user_search_input",
        "_process_office_requirement",
        "poll_option_label_input",
        "poll_option_media_input",
        "poll_skip_reason_input",
        "admin_invite_full_name_input",
        "manager_role_input",
        "birthday_input",
    ]
    for handler_name in state_message_handlers:
        start = source.index(f"async def {handler_name}(")
        next_router = source.find("\n\n@router", start + 1)
        next_def = source.find("\n\nasync def", start + 1)
        ends = [pos for pos in (next_router, next_def) if pos != -1]
        end = min(ends) if ends else len(source)
        handler_source = source[start:end]
        assert "_require_admin_message(message)" in handler_source, handler_name


def test_keyboard_spec_converts_to_aiogram_markup() -> None:
    from apps.attendance_bot.app.ui.keyboard_spec import kb, km

    markup = to_aiogram_markup(km([[kb("Открыть", "nav:open")]]))

    assert markup.inline_keyboard[0][0].text == "Открыть"
    assert markup.inline_keyboard[0][0].callback_data == "nav:open"


def test_aiogram_panel_view_helpers_do_not_import_ptb_keyboard_types() -> None:
    paths = [
        Path("apps/attendance_bot/app/ui/admin_views.py"),
        Path("apps/attendance_bot/app/ui/admin_panel_views.py"),
        Path("apps/attendance_bot/app/ui/keyboards_neutral.py"),
    ]

    matches = []
    for path in paths:
        text = path.read_text(encoding="utf-8")
        patterns = ["from telegram"]
        if path.name != "keyboards_neutral.py":
            patterns.extend(["InlineKeyboardButton", "InlineKeyboardMarkup"])
        for pattern in patterns:
            if pattern in text:
                matches.append(f"{path}:{pattern}")

    assert matches == []


def test_aiogram_routers_use_neutral_keyboards_module() -> None:
    root = Path("apps/attendance_bot/app/aiogram_app/routers")
    matches = []
    paths = [*root.glob("*.py"), Path("apps/attendance_bot/app/ui/start_views.py")]
    for path in paths:
        text = path.read_text(encoding="utf-8")
        for pattern in ("apps.attendance_bot.app.ui.keyboards import", "from_ptb_markup"):
            if pattern in text:
                matches.append(f"{path}:{pattern}")

    assert matches == []


def test_aiogram_dispatcher_import_does_not_load_telegram_package() -> None:
    code = """
import sys
from apps.attendance_bot.app.aiogram_app.main import create_aiogram_dispatcher
create_aiogram_dispatcher()
mods = [name for name in sys.modules if name == 'telegram' or name.startswith('telegram.')]
print(len(mods))
"""
    result = subprocess.run(
        [sys.executable, "-c", code],
        check=True,
        capture_output=True,
        text=True,
    )
    assert result.stdout.strip() == "0"


def test_polling_runtime_preserves_pending_updates() -> None:
    paths = [
        Path("apps/attendance_bot/app/main.py"),
        Path("apps/attendance_bot/app/aiogram_app/main.py"),
    ]

    matches = []
    for path in paths:
        if "drop_pending_updates=True" in path.read_text(encoding="utf-8"):
            matches.append(str(path))

    assert matches == []


def test_monthly_email_report_is_opt_in_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("REPORT_MONTHLY_ENABLED", raising=False)

    from apps.attendance_bot.app.config import get_report_monthly_enabled
    from apps.attendance_bot.app.scheduler import is_monthly_report_enabled

    assert get_report_monthly_enabled() is False
    assert is_monthly_report_enabled() is False


def test_flud_admin_menu_exposes_previous_month_actions() -> None:
    from apps.attendance_bot.app.ui.admin_panel_views import ACTION_LABELS, build_flud_submenu_keyboard

    markup = build_flud_submenu_keyboard()
    buttons = [button for row in markup.inline_keyboard for button in row]
    callbacks = {button.callback_data: button.text for button in buttons}

    assert callbacks["admin:flud:prev_month"] == "📆 Прошлый месяц"
    assert callbacks["admin:flud:publish_prev_month"] == "📤 Опубликовать прошлый месяц"
    assert ACTION_LABELS["admin:flud:publish_prev_month"] == "Опубликовать дайджест флудилки за прошлый месяц"


def test_monthly_stats_fast_response_seconds_keep_fractional_part() -> None:
    from apps.attendance_bot.app.jobs.monthly_stats import _format_seconds

    assert _format_seconds(0.987039) == "0.987 сек."
    assert _format_seconds(1.42) == "1.42 сек."
    assert _format_seconds(12.0) == "12 сек."
    assert _format_seconds(65.9) == "1 мин. 5 сек."


def test_birthday_notify_has_separate_flag_and_chat_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("BIRTHDAY_NOTIFY_ENABLED", raising=False)
    monkeypatch.delenv("BIRTHDAY_NOTIFY_CHAT_ID", raising=False)
    monkeypatch.delenv("BIRTHDAY_NOTIFY_MESSAGE_THREAD_ID", raising=False)
    monkeypatch.setenv("VIOLATIONS_OZKO_CHAT_ID", "-100123")

    from core.screw_core.services.notify import (
        get_birthday_notify_chat_id,
        get_birthday_notify_enabled,
        get_birthday_notify_message_thread_id,
        is_birthday_notify_message_thread_id_invalid,
    )

    assert get_birthday_notify_enabled() is False
    assert get_birthday_notify_chat_id() == -100123
    assert get_birthday_notify_message_thread_id() is None
    assert is_birthday_notify_message_thread_id_invalid() is False

    monkeypatch.setenv("BIRTHDAY_NOTIFY_ENABLED", "0")
    monkeypatch.setenv("BIRTHDAY_NOTIFY_CHAT_ID", "-100456")
    monkeypatch.setenv("BIRTHDAY_NOTIFY_MESSAGE_THREAD_ID", "4242")

    assert get_birthday_notify_enabled() is False
    assert get_birthday_notify_chat_id() == -100456
    assert get_birthday_notify_message_thread_id() == 4242
    assert is_birthday_notify_message_thread_id_invalid() is False

    monkeypatch.setenv("BIRTHDAY_NOTIFY_MESSAGE_THREAD_ID", "not-a-thread")
    assert get_birthday_notify_message_thread_id() is None
    assert is_birthday_notify_message_thread_id_invalid() is True


@pytest.mark.asyncio
async def test_system_message_uses_rich_delivery_when_available(monkeypatch: pytest.MonkeyPatch) -> None:
    from core.screw_core.services.telegram_delivery import send_system_message

    monkeypatch.setenv("TELEGRAM_RICH_MESSAGES_ENABLED", "1")
    monkeypatch.setenv("TELEGRAM_SYSTEM_REACTIONS_ENABLED", "0")

    class Sent:
        message_id = 123

    class RichBot:
        def __init__(self) -> None:
            self.rich_messages = []
            self.html_messages = []

        async def send_rich_message(self, **kwargs):
            self.rich_messages.append(kwargs)
            return Sent()

        async def send_message(self, **kwargs):
            self.html_messages.append(kwargs)
            return Sent()

    bot = RichBot()
    result = await send_system_message(
        bot,
        chat_id=-100123,
        html_text="<b>HTML fallback</b>",
        rich_html_text="<b>Rich text</b>",
        message_thread_id=456,
    )

    assert result.method == "rich"
    assert result.rich_used is True
    assert result.fallback_used is False
    assert len(bot.rich_messages) == 1
    assert bot.rich_messages[0]["rich_message"].html == "<b>Rich text</b>"
    assert bot.rich_messages[0]["message_thread_id"] == 456
    assert bot.html_messages == []


@pytest.mark.asyncio
async def test_system_message_falls_back_to_html_and_disables_link_preview(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from core.screw_core.services.telegram_delivery import send_system_message

    monkeypatch.setenv("TELEGRAM_RICH_MESSAGES_ENABLED", "1")
    monkeypatch.setenv("TELEGRAM_SYSTEM_REACTIONS_ENABLED", "0")

    class Sent:
        message_id = 124

    class FallbackBot:
        def __init__(self) -> None:
            self.html_messages = []

        async def send_rich_message(self, **_kwargs):
            raise RuntimeError("rich rejected")

        async def send_message(self, **kwargs):
            self.html_messages.append(kwargs)
            return Sent()

    bot = FallbackBot()
    result = await send_system_message(bot, chat_id=-100123, html_text="<b>safe</b>")

    assert result.method == "html"
    assert result.rich_used is False
    assert result.fallback_used is True
    assert len(bot.html_messages) == 1
    message = bot.html_messages[0]
    assert message["parse_mode"] == "HTML"
    assert getattr(message["link_preview_options"], "is_disabled") is True


@pytest.mark.asyncio
async def test_system_message_reaction_is_opt_in(monkeypatch: pytest.MonkeyPatch) -> None:
    from core.screw_core.services.telegram_delivery import send_system_message

    monkeypatch.setenv("TELEGRAM_RICH_MESSAGES_ENABLED", "0")
    monkeypatch.setenv("TELEGRAM_SYSTEM_REACTIONS_ENABLED", "1")

    class Sent:
        message_id = 125

    class ReactionBot:
        def __init__(self) -> None:
            self.reactions = []

        async def send_message(self, **_kwargs):
            return Sent()

        async def set_message_reaction(self, **kwargs):
            self.reactions.append(kwargs)
            return True

    bot = ReactionBot()
    result = await send_system_message(bot, chat_id=-100123, html_text="<b>done</b>", reaction_emoji="✅")

    assert result.reaction_set is True
    assert len(bot.reactions) == 1
    assert bot.reactions[0]["message_id"] == 125
    assert bot.reactions[0]["reaction"][0].emoji == "✅"


def test_violations_summary_escapes_html_names() -> None:
    from core.screw_core.models.enums import EventType
    from core.screw_core.services.notify import _build_violations_summary_text

    text = _build_violations_summary_text(
        date(2026, 6, 20),
        [
            {
                "event_type": EventType.NO_CHECKIN.value,
                "full_name": "Иванов <Иван> & Партнёры",
                "department_code": "OZKO",
            }
        ],
    )

    assert "📋 <b>Сводка нарушений за 20.06.2026</b>" in text
    assert "Иванов &lt;Иван&gt; &amp; Партнёры" in text
    assert "Иванов <Иван> & Партнёры" not in text


@pytest.mark.asyncio
async def test_birthday_notify_sends_to_topic_without_age_and_creates_dedup_event(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from apps.attendance_bot.app.jobs import birthday_notify
    from core.screw_core.models.enums import EventType, NotifyChannel

    monkeypatch.setenv("BIRTHDAY_NOTIFY_ENABLED", "1")
    monkeypatch.setenv("BIRTHDAY_NOTIFY_CHAT_ID", "-1001234567890")
    monkeypatch.setenv("BIRTHDAY_NOTIFY_MESSAGE_THREAD_ID", "4242")

    async def fake_birthdays(month: int, day: int, department_id: int | None = None):
        return [
            {"id": 1, "full_name": "Иванов Иван Иванович", "birthday": date(1990, month, day)},
            {"id": 2, "full_name": "Петров Петр Петрович", "birthday": date(2000, month, day)},
        ]

    async def fake_existing(_dedup_key: str):
        return None

    created_events = []

    async def fake_create_event(**kwargs):
        created_events.append(kwargs)
        return 123

    monkeypatch.setattr(birthday_notify.UsersRepo, "get_users_with_birthday_today", fake_birthdays)
    monkeypatch.setattr(birthday_notify.EventsRepo, "get_by_dedup_key", fake_existing)
    monkeypatch.setattr(birthday_notify.EventsRepo, "create_event", fake_create_event)

    class FakeBot:
        def __init__(self) -> None:
            self.messages = []

        async def send_message(self, **kwargs):
            self.messages.append(kwargs)
            return {"ok": True}

    bot = FakeBot()
    await birthday_notify.job_birthday_notify(bot)

    assert len(bot.messages) == 1
    message = bot.messages[0]
    assert message["chat_id"] == -1001234567890
    assert message["message_thread_id"] == 4242
    assert message["parse_mode"] == "HTML"
    assert "Иванов Иван Иванович" in message["text"]
    assert "Петров Петр Петрович" in message["text"]
    assert "лет" not in message["text"]
    assert "1990" not in message["text"]
    assert "2000" not in message["text"]

    assert len(created_events) == 1
    event = created_events[0]
    assert event["event_type"] == EventType.BIRTHDAY_NOTIFY_SENT
    assert event["dedup_key"].startswith("birthday_notify:")
    assert event["dedup_key"].endswith(":OZKO")
    assert event["notify_channel"] == NotifyChannel.NONE
    assert event["payload"]["count"] == 2
    assert event["payload"]["thread_configured"] is True


@pytest.mark.asyncio
async def test_birthday_notify_skips_when_dedup_event_exists(monkeypatch: pytest.MonkeyPatch) -> None:
    from apps.attendance_bot.app.jobs import birthday_notify

    monkeypatch.setenv("BIRTHDAY_NOTIFY_ENABLED", "1")
    monkeypatch.setenv("BIRTHDAY_NOTIFY_CHAT_ID", "-1001234567890")
    monkeypatch.setenv("BIRTHDAY_NOTIFY_MESSAGE_THREAD_ID", "4242")

    async def fake_birthdays(month: int, day: int, department_id: int | None = None):
        return [{"id": 1, "full_name": "Иванов Иван Иванович", "birthday": date(1990, month, day)}]

    async def fake_existing(_dedup_key: str):
        return {"id": 777}

    async def fail_create_event(**_kwargs):
        raise AssertionError("dedup event already exists")

    monkeypatch.setattr(birthday_notify.UsersRepo, "get_users_with_birthday_today", fake_birthdays)
    monkeypatch.setattr(birthday_notify.EventsRepo, "get_by_dedup_key", fake_existing)
    monkeypatch.setattr(birthday_notify.EventsRepo, "create_event", fail_create_event)

    class FakeBot:
        async def send_message(self, **_kwargs):
            raise AssertionError("message must not be sent twice")

    await birthday_notify.job_birthday_notify(FakeBot())


@pytest.mark.asyncio
async def test_birthday_notify_does_not_create_dedup_event_when_send_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from apps.attendance_bot.app.jobs import birthday_notify

    monkeypatch.setenv("BIRTHDAY_NOTIFY_ENABLED", "1")
    monkeypatch.setenv("BIRTHDAY_NOTIFY_CHAT_ID", "-1001234567890")
    monkeypatch.setenv("BIRTHDAY_NOTIFY_MESSAGE_THREAD_ID", "4242")

    async def fake_birthdays(month: int, day: int, department_id: int | None = None):
        return [{"id": 1, "full_name": "Иванов Иван Иванович", "birthday": date(1990, month, day)}]

    async def fake_existing(_dedup_key: str):
        return None

    async def fail_create_event(**_kwargs):
        raise AssertionError("dedup event must not be created after send failure")

    monkeypatch.setattr(birthday_notify.UsersRepo, "get_users_with_birthday_today", fake_birthdays)
    monkeypatch.setattr(birthday_notify.EventsRepo, "get_by_dedup_key", fake_existing)
    monkeypatch.setattr(birthday_notify.EventsRepo, "create_event", fail_create_event)

    class FailingBot:
        async def send_message(self, **_kwargs):
            raise RuntimeError("send failed")

    await birthday_notify.job_birthday_notify(FailingBot())


@pytest.mark.asyncio
async def test_birthday_notify_skips_invalid_thread_id(monkeypatch: pytest.MonkeyPatch) -> None:
    from apps.attendance_bot.app.jobs import birthday_notify

    monkeypatch.setenv("BIRTHDAY_NOTIFY_ENABLED", "1")
    monkeypatch.setenv("BIRTHDAY_NOTIFY_CHAT_ID", "-1001234567890")
    monkeypatch.setenv("BIRTHDAY_NOTIFY_MESSAGE_THREAD_ID", "bad-thread")

    async def fail_birthdays(*_args, **_kwargs):
        raise AssertionError("invalid thread id must stop before DB lookup")

    monkeypatch.setattr(birthday_notify.UsersRepo, "get_users_with_birthday_today", fail_birthdays)

    class FakeBot:
        async def send_message(self, **_kwargs):
            raise AssertionError("message must not be sent with invalid thread id")

    await birthday_notify.job_birthday_notify(FakeBot())


def test_allowed_group_chat_ids_are_loaded_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    from apps.attendance_bot.app.config import get_allowed_group_chat_ids

    for name in [
        "MAIN_GROUP_CHAT_ID",
        "ATTENDANCE_GROUP_ID",
        "VIOLATIONS_OZKO_CHAT_ID",
        "BIRTHDAY_NOTIFY_CHAT_ID",
        "POLL_SUMMARY_CHAT_ID",
        "DEPARTMENT_LISTS_CHAT_ID",
        "ATTENDANCE_ALLOWED_GROUP_IDS",
    ]:
        monkeypatch.delenv(name, raising=False)

    monkeypatch.setenv("MAIN_GROUP_CHAT_ID", "-1001")
    monkeypatch.setenv("ATTENDANCE_GROUP_ID", "-1002")
    monkeypatch.setenv("VIOLATIONS_OZKO_CHAT_ID", "not-a-chat-id")
    monkeypatch.setenv("BIRTHDAY_NOTIFY_CHAT_ID", "-1003")
    monkeypatch.setenv("POLL_SUMMARY_CHAT_ID", "-1004")
    monkeypatch.setenv("DEPARTMENT_LISTS_CHAT_ID", "-1005")
    monkeypatch.setenv("ATTENDANCE_ALLOWED_GROUP_IDS", "-1006, -1007;bad")

    assert get_allowed_group_chat_ids() == {-1001, -1002, -1003, -1004, -1005, -1006, -1007}


def test_flud_config_digest_fallback_and_allowlist(monkeypatch: pytest.MonkeyPatch) -> None:
    from apps.attendance_bot.app.config import (
        get_allowed_group_chat_ids,
        get_flud_chat_id,
        get_flud_digest_chat_id,
        get_flud_digest_message_links_enabled,
        get_flud_message_thread_id,
        get_flud_text_preview_max_chars,
        get_flud_top_limit,
        is_flud_message_thread_id_invalid,
        is_flud_enabled,
    )

    monkeypatch.setenv("FLUD_ENABLED", "1")
    monkeypatch.setenv("FLUD_CHAT_ID", "-100900")
    monkeypatch.delenv("FLUD_MESSAGE_THREAD_ID", raising=False)
    monkeypatch.setenv("VIOLATIONS_OZKO_CHAT_ID", "-100901")
    monkeypatch.delenv("FLUD_DIGEST_CHAT_ID", raising=False)
    monkeypatch.delenv("FLUD_DIGEST_MESSAGE_LINKS_ENABLED", raising=False)

    assert is_flud_enabled() is True
    assert get_flud_chat_id() == -100900
    assert get_flud_message_thread_id() is None
    assert is_flud_message_thread_id_invalid() is False
    assert get_flud_digest_chat_id() == -100901
    assert get_flud_digest_message_links_enabled() is False
    assert -100900 in get_allowed_group_chat_ids()

    monkeypatch.setenv("FLUD_DIGEST_CHAT_ID", "-100902")
    monkeypatch.setenv("FLUD_MESSAGE_THREAD_ID", "4242")
    monkeypatch.setenv("FLUD_DIGEST_MESSAGE_LINKS_ENABLED", "1")
    assert get_flud_digest_chat_id() == -100902
    assert get_flud_message_thread_id() == 4242
    assert get_flud_digest_message_links_enabled() is True

    monkeypatch.setenv("FLUD_CHAT_ID", "bad")
    monkeypatch.setenv("FLUD_MESSAGE_THREAD_ID", "bad-thread")
    monkeypatch.setenv("FLUD_TEXT_PREVIEW_MAX_CHARS", "999")
    monkeypatch.setenv("FLUD_TOP_LIMIT", "0")
    assert get_flud_chat_id() is None
    assert get_flud_message_thread_id() is None
    assert is_flud_message_thread_id_invalid() is True
    assert get_flud_text_preview_max_chars() == 120
    assert get_flud_top_limit() == 5


def test_flud_message_payload_respects_preview_privacy() -> None:
    from core.screw_core.services.flud_stats import build_flud_message_payload

    payload = build_flud_message_payload(
        chat_id=-1001,
        message_id=10,
        sent_at=datetime(2026, 6, 9),
        telegram_user_id=123,
        user_id=None,
        text="секретный текст флудилки",
        message_type="text",
        store_text_preview=False,
        source_author_hash="hash1",
    )

    assert payload["text_len"] == len("секретный текст флудилки")
    assert payload["word_count"] == 3
    assert payload["text_preview"] is None
    assert payload["has_text"] is True
    assert payload["source_author_hash"] == "hash1"

    payload_with_preview = build_flud_message_payload(
        chat_id=-1001,
        message_id=11,
        sent_at=datetime(2026, 6, 9),
        telegram_user_id=123,
        user_id=None,
        text="очень длинный текст",
        message_type="text",
        store_text_preview=True,
        preview_max_chars=8,
    )
    assert payload_with_preview["text_preview"] == "очень д…"


def test_flud_month_arg_parser_accepts_history_formats() -> None:
    from apps.attendance_bot.app.jobs.flud_digest import parse_flud_month_arg

    today = date(2026, 7, 8)

    assert parse_flud_month_arg("", today=today) == (2026, 7)
    assert parse_flud_month_arg("2026-06", today=today) == (2026, 6)
    assert parse_flud_month_arg("06.2026", today=today) == (2026, 6)
    assert parse_flud_month_arg("6", today=today) == (2026, 6)
    assert parse_flud_month_arg("июнь", today=today) == (2026, 6)
    assert parse_flud_month_arg("2026-13", today=today) is None


@pytest.mark.asyncio
async def test_flud_digest_no_data(monkeypatch: pytest.MonkeyPatch) -> None:
    from core.screw_core.services import flud_stats

    async def fake_summary(*_args, **_kwargs):
        return {"message_count": 0, "active_users_count": 0, "reply_count": 0, "reaction_count": 0}

    monkeypatch.setattr(flud_stats.FludRepo, "get_summary", fake_summary)

    text = await flud_stats.build_weekly_digest_text(date(2026, 6, 1), date(2026, 6, 7), -1001)

    assert "данных нет" in text


@pytest.mark.asyncio
async def test_flud_digest_escapes_preview_and_omits_raw_ids(monkeypatch: pytest.MonkeyPatch) -> None:
    from core.screw_core.services import flud_stats

    async def fake_summary(*_args, **_kwargs):
        return {"message_count": 3, "active_users_count": 2, "reply_count": 1, "reaction_count": 24}

    async def fake_type_counts(*_args, **_kwargs):
        return {"text": 1, "photo": 1, "sticker": 1, "voice": 0}

    async def fake_daily(*_args, **_kwargs):
        return [{"day": date(2026, 6, 7), "count": 3}]

    async def fake_hourly(*_args, **_kwargs):
        return [{"hour": 13, "count": 3}]

    async def fake_top(*_args, **_kwargs):
        return [
            {
                "author_name": "<Иванов & Co>",
                "reaction_count": 24,
                "top_reaction_type": "emoji:😂",
                "message_type": "text",
                "text_preview": "<script>мем</script>",
                "telegram_user_id": 123456789,
            }
        ]

    monkeypatch.setattr(flud_stats.FludRepo, "get_summary", fake_summary)
    monkeypatch.setattr(flud_stats.FludRepo, "get_message_type_counts", fake_type_counts)
    monkeypatch.setattr(flud_stats.FludRepo, "get_daily_activity", fake_daily)
    monkeypatch.setattr(flud_stats.FludRepo, "get_hourly_activity", fake_hourly)
    monkeypatch.setattr(flud_stats.FludRepo, "get_top_reacted_messages", fake_top)

    text = await flud_stats.build_weekly_digest_text(date(2026, 6, 1), date(2026, 6, 7), -1001, top_limit=5)

    assert "&lt;Иванов &amp; Co&gt;" in text
    assert "&lt;script&gt;мем&lt;/script&gt;" in text
    assert "123456789" not in text
    assert "Фото: 1" in text
    assert "Стикеры: 1" in text


@pytest.mark.asyncio
async def test_monthly_flud_digest_labels_caption_without_preview(monkeypatch: pytest.MonkeyPatch) -> None:
    from core.screw_core.services import flud_stats

    async def fake_summary(*_args, **_kwargs):
        return {"message_count": 3, "active_users_count": 2, "reply_count": 1, "reaction_count": 16}

    async def fake_type_counts(*_args, **_kwargs):
        return {"text": 1, "photo": 1, "sticker": 1, "voice": 0}

    async def fake_daily(*_args, **_kwargs):
        return [{"day": date(2026, 6, 7), "count": 3}]

    async def fake_hourly(*_args, **_kwargs):
        return [{"hour": 13, "count": 3}]

    async def fake_top(*_args, **_kwargs):
        return [{"author_name": "Иванов", "reaction_count": 16, "top_reaction_type": "emoji:🔥", "message_type": "photo"}]

    async def fake_media(*_args, **_kwargs):
        return [{"author_name": "Иванов", "reaction_count": 16, "top_reaction_type": "emoji:🔥", "message_type": "photo"}]

    async def fake_text(*_args, **_kwargs):
        return [{"author_name": "Иванов", "reaction_count": 10, "top_reaction_type": "emoji:😂", "message_type": "photo"}]

    monkeypatch.setattr(flud_stats.FludRepo, "get_summary", fake_summary)
    monkeypatch.setattr(flud_stats.FludRepo, "get_message_type_counts", fake_type_counts)
    monkeypatch.setattr(flud_stats.FludRepo, "get_daily_activity", fake_daily)
    monkeypatch.setattr(flud_stats.FludRepo, "get_hourly_activity", fake_hourly)
    monkeypatch.setattr(flud_stats.FludRepo, "get_top_reacted_messages", fake_top)
    monkeypatch.setattr(flud_stats.FludRepo, "get_top_media_messages", fake_media)
    monkeypatch.setattr(flud_stats.FludRepo, "get_top_text_messages", fake_text)

    text = await flud_stats.build_monthly_digest_text(2026, 6, -1001, top_limit=5)

    assert "Фраза месяца — caption без preview (фото)" in text


@pytest.mark.asyncio
async def test_monthly_flud_digest_adds_message_links_to_nominations(monkeypatch: pytest.MonkeyPatch) -> None:
    from core.screw_core.services import flud_stats

    async def fake_summary(*_args, **_kwargs):
        return {"message_count": 3, "active_users_count": 2, "reply_count": 1, "reaction_count": 16}

    async def fake_type_counts(*_args, **_kwargs):
        return {"text": 1, "photo": 1, "sticker": 1, "voice": 0}

    async def fake_daily(*_args, **_kwargs):
        return [{"day": date(2026, 6, 7), "count": 3}]

    async def fake_hourly(*_args, **_kwargs):
        return [{"hour": 13, "count": 3}]

    async def fake_top(*_args, **_kwargs):
        return [
            {
                "chat_id": -1001234567890,
                "message_id": 9001,
                "author_name": "Иванов",
                "reaction_count": 16,
                "top_reaction_type": "emoji:🔥",
                "message_type": "sticker",
            }
        ]

    async def fake_media(*_args, **_kwargs):
        return [
            {
                "chat_id": -1001234567890,
                "message_id": 9002,
                "author_name": "Иванов",
                "reaction_count": 16,
                "top_reaction_type": "emoji:🔥",
                "message_type": "photo",
            }
        ]

    async def fake_text(*_args, **_kwargs):
        return [
            {
                "chat_id": -1001234567890,
                "message_id": 9003,
                "author_name": "Иванов",
                "reaction_count": 10,
                "top_reaction_type": "emoji:😂",
                "message_type": "text",
                "text_preview": "лучший & текст",
            }
        ]

    monkeypatch.setattr(flud_stats.FludRepo, "get_summary", fake_summary)
    monkeypatch.setattr(flud_stats.FludRepo, "get_message_type_counts", fake_type_counts)
    monkeypatch.setattr(flud_stats.FludRepo, "get_daily_activity", fake_daily)
    monkeypatch.setattr(flud_stats.FludRepo, "get_hourly_activity", fake_hourly)
    monkeypatch.setattr(flud_stats.FludRepo, "get_top_reacted_messages", fake_top)
    monkeypatch.setattr(flud_stats.FludRepo, "get_top_media_messages", fake_media)
    monkeypatch.setattr(flud_stats.FludRepo, "get_top_text_messages", fake_text)

    text = await flud_stats.build_monthly_digest_text(
        2026,
        6,
        -1001234567890,
        top_limit=5,
        include_message_links=True,
        message_thread_id=4242,
    )

    assert '<a href="https://t.me/c/1234567890/4242/9002">открыть сообщение</a>' in text
    assert '<a href="https://t.me/c/1234567890/4242/9003">открыть сообщение</a>' in text
    assert "Фраза месяца — лучший &amp; текст · <a href=" in text
    assert "Картинка месяца — фото · <a href=" in text
    assert "Стикер месяца — стикер · <a href=" in text


@pytest.mark.asyncio
async def test_flud_router_collects_only_enabled_flud_chat(monkeypatch: pytest.MonkeyPatch) -> None:
    from apps.attendance_bot.app.aiogram_app.routers import flud

    monkeypatch.setenv("FLUD_ENABLED", "1")
    monkeypatch.setenv("FLUD_CHAT_ID", "-100900")
    monkeypatch.setenv("FLUD_MESSAGE_THREAD_ID", "4242")
    monkeypatch.setenv("FLUD_STORE_TEXT_PREVIEW", "0")

    async def fake_user(_telegram_id: int):
        return None

    captured = []

    async def fake_upsert(**payload):
        captured.append(payload)
        return 1

    monkeypatch.setattr(flud.UsersRepo, "get_by_telegram_id", fake_user)
    monkeypatch.setattr(flud.FludRepo, "upsert_message", fake_upsert)

    message = SimpleNamespace(
        chat=SimpleNamespace(id=-100900, type="supergroup"),
        from_user=SimpleNamespace(id=42, is_bot=False),
        message_id=55,
        message_thread_id=4242,
        date=datetime(2026, 6, 9),
        text="hello world",
        caption=None,
        photo=[],
        video=None,
        animation=None,
        sticker=None,
        voice=None,
        document=None,
        reply_to_message=SimpleNamespace(message_id=54),
    )

    await flud.collect_flud_message(message)

    assert len(captured) == 1
    assert captured[0]["chat_id"] == -100900
    assert captured[0]["message_id"] == 55
    assert captured[0]["telegram_user_id"] == 42
    assert captured[0]["text_preview"] is None
    assert captured[0]["is_reply"] is True

    message.chat.id = -100901
    await flud.collect_flud_message(message)
    assert len(captured) == 1

    message.chat.id = -100900
    message.message_thread_id = 999
    await flud.collect_flud_message(message)
    assert len(captured) == 1

    message.message_thread_id = 4242
    monkeypatch.setenv("FLUD_ENABLED", "0")
    message.chat.id = -100900
    await flud.collect_flud_message(message)
    assert len(captured) == 1


@pytest.mark.asyncio
async def test_flud_digest_publish_marks_success_and_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    from apps.attendance_bot.app.jobs import flud_digest

    monkeypatch.setenv("FLUD_CHAT_ID", "-100900")
    monkeypatch.setenv("FLUD_DIGEST_CHAT_ID", "")
    monkeypatch.setenv("VIOLATIONS_OZKO_CHAT_ID", "-100901")

    calls = []

    async def fake_create_digest_run(**kwargs):
        calls.append(("create", kwargs))
        return 10

    async def fake_mark_published(*args):
        calls.append(("published", args))

    async def fake_mark_failed(*args):
        calls.append(("failed", args))

    monkeypatch.setattr(flud_digest.FludRepo, "create_digest_run", fake_create_digest_run)
    monkeypatch.setattr(flud_digest.FludRepo, "mark_digest_published", fake_mark_published)
    monkeypatch.setattr(flud_digest.FludRepo, "mark_digest_failed", fake_mark_failed)

    class Sent:
        message_id = 777

    class OkBot:
        async def send_rich_message(self, **_kwargs):
            raise AssertionError("flud digests must use plain HTML, not sendRichMessage")

        async def send_message(self, **kwargs):
            calls.append(("send", kwargs))
            return Sent()

    success, status, chat_id = await flud_digest._publish_digest(
        OkBot(),
        digest_type="weekly",
        period_start=date(2026, 6, 1),
        period_end=date(2026, 6, 7),
        text="digest",
    )

    assert success is True
    assert status == "published"
    assert chat_id == -100901
    assert ("published", (10, -100901, 777)) in calls
    send_call = next(item for item in calls if item[0] == "send")
    assert send_call[1]["parse_mode"] == "HTML"
    assert "rich_message" not in send_call[1]

    class FailingBot:
        async def send_message(self, **_kwargs):
            raise RuntimeError("send failed")

    success, status, chat_id = await flud_digest._publish_digest(
        FailingBot(),
        digest_type="weekly",
        period_start=date(2026, 6, 1),
        period_end=date(2026, 6, 7),
        text="digest",
    )

    assert success is False
    assert "send failed" in status
    assert chat_id == -100901
    assert any(item[0] == "failed" for item in calls)


def test_flud_import_telegram_export_dry_run(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    from scripts import flud_import_telegram_export

    export_path = tmp_path / "result.json"
    export_path.write_text(
        (
            '{"messages": ['
            '{"id": 1, "type": "message", "date_unixtime": "1780934400", '
            '"from_id": "user42", "text": "hello", "reactions": [{"emoji": "😂", "count": 2}]},'
            '{"id": 2, "type": "service", "text": "skip"}'
            ']}'
        ),
        encoding="utf-8",
    )

    result = flud_import_telegram_export.main([
        "--file",
        str(export_path),
        "--chat-id",
        "-100900",
        "--dry-run",
    ])

    captured = capsys.readouterr()
    assert result == 0
    assert "parsed_messages=1" in captured.out


@pytest.mark.asyncio
async def test_security_policy_blocks_non_allowlisted_group(monkeypatch: pytest.MonkeyPatch) -> None:
    from aiogram.types import Update

    from apps.attendance_bot.app.aiogram_app.middlewares import AiogramSecurityPolicyMiddleware

    monkeypatch.setenv("ATTENDANCE_GROUP_ID", "-1001")
    event = Update.model_validate(
        {
            "update_id": 1,
            "message": {
                "message_id": 1,
                "date": int(time()),
                "chat": {"id": -1002, "type": "supergroup", "title": "blocked"},
                "from": {"id": 42, "is_bot": False, "first_name": "User"},
                "text": "/start",
            },
        }
    )

    async def handler(_event, _data):
        raise AssertionError("blocked group must not reach handler")

    assert await AiogramSecurityPolicyMiddleware()(handler, event, {}) is None


@pytest.mark.asyncio
async def test_security_policy_blocks_unknown_private_user(monkeypatch: pytest.MonkeyPatch) -> None:
    from aiogram.types import Update

    from apps.attendance_bot.app.aiogram_app.middlewares import AiogramSecurityPolicyMiddleware
    from core.screw_core.repos import users_repo

    async def fake_get_by_telegram_id(_telegram_id: int):
        return None

    monkeypatch.setattr(users_repo.UsersRepo, "get_by_telegram_id", fake_get_by_telegram_id)
    event = Update.model_validate(
        {
            "update_id": 2,
            "message": {
                "message_id": 1,
                "date": int(time()),
                "chat": {"id": 42, "type": "private"},
                "from": {"id": 42, "is_bot": False, "first_name": "User"},
                "text": "/start",
            },
        }
    )

    async def handler(_event, _data):
        raise AssertionError("unknown private user must not reach handler without invite")

    assert await AiogramSecurityPolicyMiddleware()(handler, event, {}) is None


@pytest.mark.asyncio
async def test_security_policy_allows_private_invite_and_registration_state(monkeypatch: pytest.MonkeyPatch) -> None:
    from aiogram.types import Update

    from apps.attendance_bot.app.aiogram_app.middlewares import (
        AiogramSecurityPolicyMiddleware,
        REGISTRATION_FULL_NAME_STATE,
    )
    from core.screw_core.repos import users_repo

    lookups = 0

    async def fake_get_by_telegram_id(_telegram_id: int):
        nonlocal lookups
        lookups += 1
        return None

    monkeypatch.setattr(users_repo.UsersRepo, "get_by_telegram_id", fake_get_by_telegram_id)
    middleware = AiogramSecurityPolicyMiddleware()
    calls = 0

    async def handler(_event, _data):
        nonlocal calls
        calls += 1
        return "ok"

    invite_event = Update.model_validate(
        {
            "update_id": 3,
            "message": {
                "message_id": 1,
                "date": int(time()),
                "chat": {"id": 42, "type": "private"},
                "from": {"id": 42, "is_bot": False, "first_name": "User"},
                "text": "/start abcdef123456",
            },
        }
    )
    assert await middleware(handler, invite_event, {}) == "ok"

    class FakeState:
        async def get_state(self):
            return REGISTRATION_FULL_NAME_STATE

    full_name_event = Update.model_validate(
        {
            "update_id": 4,
            "message": {
                "message_id": 2,
                "date": int(time()),
                "chat": {"id": 42, "type": "private"},
                "from": {"id": 42, "is_bot": False, "first_name": "User"},
                "text": "Иванов Иван Иванович",
            },
        }
    )
    assert await middleware(handler, full_name_event, {"state": FakeState()}) == "ok"
    assert calls == 2
    assert lookups == 2


def test_no_tg_worker_is_hidden_from_public_group_reminder() -> None:
    from apps.attendance_bot.app.services.poll_runtime import _format_non_respondent_for_group
    from core.screw_core.models.enums import UserRole

    regular = {
        "telegram_id": 111,
        "full_name": "Иванов Иван",
        "role": UserRole.EMPLOYEE.value,
    }
    no_tg = {
        "telegram_id": 222,
        "full_name": "Чунихин Дмитрий",
        "role": UserRole.NO_TG_WORKER.value,
    }

    assert _format_non_respondent_for_group(regular) == '<a href="tg://user?id=111">Иванов Иван</a>'
    assert _format_non_respondent_for_group(no_tg) is None


def test_fixed_time_violations_check_is_legacy_opt_in() -> None:
    scheduler_source = Path("apps/attendance_bot/app/scheduler.py").read_text(encoding="utf-8")

    assert "VIOLATIONS_FIXED_CHECK_ENABLED" in scheduler_source
    assert "VIOLATIONS_CHECK_ENABLED" not in scheduler_source


@pytest.mark.asyncio
async def test_post_poll_checks_preserves_department_filter(monkeypatch: pytest.MonkeyPatch) -> None:
    from core.screw_core.services import notify

    captured: dict[str, object] = {}

    async def fake_run_daily_checks(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(total_users=3, no_checkin_count=1)

    monkeypatch.setenv("VIOLATIONS_NOTIFY_ENABLED", "0")
    monkeypatch.setattr(notify.ChecksService, "run_daily_checks", fake_run_daily_checks)

    await notify.NotifyService.run_post_poll_checks(
        bot=object(),
        check_date=date(2026, 5, 14),
        department_id=1,
        poll_id=42,
    )

    assert captured["poll_id"] == 42
    assert captured["check_date"] == date(2026, 5, 14)
    assert captured["department_id"] == 1


@pytest.mark.asyncio
async def test_violations_summary_filters_ozko_department(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from core.screw_core.models.enums import EventType
    from core.screw_core.services import notify
    from core.screw_core.config.departments import OZKO_DEPARTMENT_ID

    captured: dict[str, object] = {}

    async def fake_get_pending_violations_for_chat(limit=None):
        captured["limit"] = limit
        result = []
        for idx in range(51):
            result.append(
                notify.ViolationMessage(
                    event_id=idx + 1,
                    event_type=EventType.NO_CHECKIN.value,
                    event_date=date(2026, 5, 15),
                    user_id=idx + 1,
                    full_name=f"ОЗКО {idx + 1}",
                    department_id=1,
                    department_code="OZKO",
                    text="",
                )
            )
        result.append(
            notify.ViolationMessage(
                event_id=100,
                event_type=EventType.NO_CHECKIN.value,
                event_date=date(2026, 5, 15),
                user_id=100,
                full_name="ОСБ Иванов",
                department_id=2,
                department_code="OSB",
                text="",
            )
        )
        return result

    monkeypatch.setattr(
        notify.NotifyService,
        "get_pending_violations_for_chat",
        fake_get_pending_violations_for_chat,
    )

    text, event_ids = await notify.NotifyService.build_violations_summary_for_date(
        date(2026, 5, 15),
        department_id=OZKO_DEPARTMENT_ID,
    )

    assert captured["limit"] == 10_000
    assert len(event_ids) == 51
    assert text is not None
    assert "Не отметились в опросе (51)" in text
    assert "ОЗКО (51)" in text
    assert "ОСБ (1)" not in text
    assert "ОСБ Иванов" not in text


@pytest.mark.asyncio
async def test_poll_close_summary_sends_only_ozko_data(monkeypatch: pytest.MonkeyPatch) -> None:
    from apps.attendance_bot.app.services import poll_runtime
    from core.screw_core.config.departments import OZKO_DEPARTMENT_ID
    from core.screw_core.models.enums import AttendanceChoice

    captured: dict[str, object] = {}

    async def fake_get_marks_for_date(mark_date, department_id=None):
        captured["marks_department_id"] = department_id
        assert mark_date == date(2026, 5, 15)
        if department_id == OZKO_DEPARTMENT_ID:
            return [
                {"choice": AttendanceChoice.EVOLUTION.value, "full_name": "ОЗКО Иванов"},
                {"choice": AttendanceChoice.REMOTE.value, "full_name": "ОЗКО Петров"},
            ]
        return [
            {"choice": AttendanceChoice.EVOLUTION.value, "full_name": "ОЗКО Иванов"},
            {"choice": AttendanceChoice.CSKA.value, "full_name": "ОСБ Сидоров"},
        ]

    async def fake_get_non_respondents(poll_id, department_id=None):
        captured["non_respondents_department_id"] = department_id
        assert poll_id == 42
        if department_id == OZKO_DEPARTMENT_ID:
            return [{"full_name": "ОЗКО Неответивший", "role": "employee", "telegram_id": 123}]
        return [
            {"full_name": "ОЗКО Неответивший", "role": "employee", "telegram_id": 123},
            {"full_name": "ОСБ Неответивший", "role": "employee", "telegram_id": 456},
        ]

    class FakeBot:
        def __init__(self) -> None:
            self.messages: list[str] = []

        async def send_message(self, **kwargs):
            self.messages.append(kwargs["text"])

    monkeypatch.setattr(poll_runtime, "get_poll_summary_chat_id", lambda: -100123)
    monkeypatch.setattr(poll_runtime.AttendanceRepo, "get_marks_for_date", fake_get_marks_for_date)
    monkeypatch.setattr(poll_runtime.PollsService, "get_non_respondents", fake_get_non_respondents)

    bot = FakeBot()
    result = await poll_runtime.send_poll_close_summary(
        bot,
        {
            "id": 42,
            "poll_date": date(2026, 5, 15),
            "message_id": 777,
            "chat_id": -100999,
        },
        department_id=OZKO_DEPARTMENT_ID,
    )

    joined = "\n".join(bot.messages)
    assert result is True
    assert captured["marks_department_id"] == OZKO_DEPARTMENT_ID
    assert captured["non_respondents_department_id"] == OZKO_DEPARTMENT_ID
    assert "Итоги опроса явки за <b>15.05.2026</b> — ОЗКО" in joined
    assert "Ответили: <b>2</b>" in joined
    assert "Не ответили: <b>1</b>" in joined
    assert "ОЗКО Иванов" in joined
    assert "ОЗКО Петров" in joined
    assert "ОЗКО Неответивший" in joined
    assert "ОСБ Сидоров" not in joined
    assert "ОСБ Неответивший" not in joined


@pytest.mark.asyncio
async def test_department_lists_summary_sends_ozko_then_osb(monkeypatch: pytest.MonkeyPatch) -> None:
    from apps.attendance_bot.app.services import poll_runtime
    from core.screw_core.config.departments import OSB_DEPARTMENT_ID, OZKO_DEPARTMENT_ID
    from core.screw_core.models.enums import AttendanceChoice

    async def fake_get_marks_for_date(mark_date, department_id=None):
        assert mark_date == date(2026, 5, 15)
        if department_id == OZKO_DEPARTMENT_ID:
            return [{"choice": AttendanceChoice.EVOLUTION.value, "full_name": "ОЗКО Иванов"}]
        if department_id == OSB_DEPARTMENT_ID:
            return [{"choice": AttendanceChoice.CSKA.value, "full_name": "ОСБ Сидоров"}]
        raise AssertionError(f"unexpected department_id={department_id}")

    async def fake_get_non_respondents(poll_id, department_id=None):
        assert poll_id == 42
        if department_id == OZKO_DEPARTMENT_ID:
            return [{"full_name": "ОЗКО Неответивший", "role": "employee", "telegram_id": 123}]
        if department_id == OSB_DEPARTMENT_ID:
            return [{"full_name": "ОСБ Неответивший", "role": "employee", "telegram_id": 456}]
        raise AssertionError(f"unexpected department_id={department_id}")

    async def fake_get_statuses_for_date(status_date):
        assert status_date == date(2026, 5, 15)
        return [
            {
                "kind": "VACATION",
                "full_name": "ОЗКО Отпуск",
                "end_date": date(2026, 5, 20),
            },
            {
                "kind": "SICK",
                "full_name": "ОСБ Больничный",
                "end_date": None,
            },
            {
                "kind": "DAY_OFF",
                "full_name": "Не должен попасть",
                "end_date": None,
            },
        ]

    class FakeBot:
        def __init__(self) -> None:
            self.messages: list[str] = []

        async def send_message(self, **kwargs):
            self.messages.append(kwargs["text"])

    monkeypatch.setattr(poll_runtime, "get_department_lists_chat_id", lambda: -100555)
    monkeypatch.setattr(poll_runtime.AttendanceRepo, "get_marks_for_date", fake_get_marks_for_date)
    monkeypatch.setattr(poll_runtime.PollsService, "get_non_respondents", fake_get_non_respondents)
    monkeypatch.setattr(poll_runtime.DayStatusRepo, "get_statuses_for_date", fake_get_statuses_for_date)

    bot = FakeBot()
    result = await poll_runtime.send_department_lists_summary(
        bot,
        {
            "id": 42,
            "poll_date": date(2026, 5, 15),
        },
    )

    assert result is True
    assert len(bot.messages) == 1
    text = bot.messages[0]
    assert text.startswith("Список ОЗКО на <b>15.05.2026</b>")
    assert text.index("Список ОЗКО") < text.index("Список ОСБ")
    assert "ОЗКО Иванов" in text
    assert "ОЗКО Неответивший" in text
    assert "ОСБ Сидоров" in text
    assert "ОСБ Неответивший" in text
    assert text.index("Список ОСБ") < text.index("Сегодня отсутствуют")
    assert "ОЗКО Отпуск — до 20.05.2026 включительно" in text
    assert "ОСБ Больничный" in text
    assert "Не должен попасть" not in text


@pytest.mark.asyncio
async def test_polls_repo_non_respondents_department_filter_is_optional(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from core.screw_core.repos import polls_repo

    captured: list[tuple[str, tuple[object, ...]]] = []

    class FakeConnection:
        async def fetchrow(self, _query, *args):
            assert args == (42,)
            return {
                "poll_date": date(2026, 5, 15),
                "status": "open",
                "expected_users_snapshot_at": None,
            }

        async def fetch(self, query, *args):
            captured.append((query, args))
            return []

    class FakeAcquire:
        async def __aenter__(self):
            return FakeConnection()

        async def __aexit__(self, *_args):
            return False

    monkeypatch.setattr(polls_repo, "acquire_connection", lambda: FakeAcquire())

    await polls_repo.PollsRepo.get_non_respondents(42)
    await polls_repo.PollsRepo.get_non_respondents(42, department_id=1)

    query_without_filter, args_without_filter = captured[0]
    query_with_filter, args_with_filter = captured[1]
    assert "AND u.department_id =" not in query_without_filter
    assert args_without_filter == (42, date(2026, 5, 15))
    assert "AND u.department_id = $3" in query_with_filter
    assert args_with_filter == (42, date(2026, 5, 15), 1)


def test_private_chat_guard_is_duck_typed_without_ptb_runtime_import() -> None:
    from apps.attendance_bot.app.utils.private_chat_guard import is_private_chat

    class Chat:
        type = "private"

    class Message:
        chat = Chat()

    assert is_private_chat(Message()) is True


def test_message_tracker_bad_request_detection_is_runtime_neutral() -> None:
    from apps.attendance_bot.app.utils.message_tracker import _is_bad_request_error

    BadRequestLike = type("TelegramBadRequest", (Exception,), {"__module__": "aiogram.exceptions"})
    OtherError = type("TelegramBadRequest", (Exception,), {"__module__": "custom"})

    assert _is_bad_request_error(BadRequestLike("bad")) is True
    assert _is_bad_request_error(OtherError("bad")) is False


@pytest.mark.asyncio
async def test_poll_auto_send_uses_aiogram_poll_payload_for_aiogram_bot() -> None:
    from apps.attendance_bot.app.jobs.poll_auto_send import _send_poll

    class FakeAiogramBot:
        def __init__(self) -> None:
            self.calls = []

        async def send_poll(self, **kwargs):
            self.calls.append(kwargs)
            return {"ok": True}

    bot = FakeAiogramBot()
    payload = build_poll_payload(
        [{"key": "REMOTE", "label": "Удалёнка", "media": {"type": "photo", "media": "file_id"}}],
        {"poll_description": "desc", "poll_duration_seconds": 120},
    )

    result = await _send_poll(bot, payload, chat_id=1, question="q")

    assert result == {"ok": True}
    assert "members_only" not in bot.calls[0]
    assert bot.calls[0]["description"] == "desc"
    assert bot.calls[0]["open_period"] == 120
    assert "api_kwargs" not in bot.calls[0]
    assert bot.calls[0]["options"][0].media.type == "photo"


@pytest.mark.asyncio
async def test_aiogram_poll_members_only_retry_removes_only_members_only() -> None:
    from aiogram.exceptions import TelegramBadRequest

    class FakeAiogramBot:
        def __init__(self) -> None:
            self.calls = []

        async def send_poll(self, **kwargs):
            self.calls.append(kwargs)
            if len(self.calls) == 1:
                raise TelegramBadRequest(method="sendPoll", message="poll voters can be restricted only in channel chats")
            return {"ok": True}

    bot = FakeAiogramBot()
    payload = build_poll_payload(
        [{"key": "REMOTE", "label": "Удалёнка", "media": {"type": "photo", "media": "file_id"}}],
        {"poll_description": "desc", "poll_duration_seconds": 120},
    )
    options, plain_options, _ = build_aiogram_poll_payload(payload)
    api_kwargs = dict(payload.api_kwargs)
    api_kwargs["members_only"] = True

    await send_aiogram_poll_with_fallback(
        bot,
        logger=type("Logger", (), {"warning": lambda *args, **kwargs: None})(),
        chat_id=1,
        question="q",
        options=options,
        plain_options=plain_options,
        api_kwargs=api_kwargs,
        is_anonymous=False,
        allows_multiple_answers=False,
    )

    assert len(bot.calls) == 2
    assert bot.calls[0]["members_only"] is True
    assert "members_only" not in bot.calls[1]
    assert bot.calls[1]["description"] == "desc"
    assert bot.calls[1]["options"][0].media.type == "photo"


@pytest.mark.asyncio
async def test_poll_auto_send_skips_when_group_preflight_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    from apps.attendance_bot.app.jobs import poll_auto_send
    from apps.attendance_bot.app.services.group_preflight import GroupPreflightResult

    async def fake_get_config():
        return {"auto_send_enabled": True}

    async def fake_is_skip_date(_poll_date):
        return False

    async def fake_current_poll():
        return None

    async def fake_group_preflight(_bot):
        return GroupPreflightResult(configured=True, accessible=False, chat_id=1)

    async def fail_send(*args, **kwargs):
        raise AssertionError("send_poll must not be called when group is unavailable")

    monkeypatch.setattr(poll_auto_send.PollConfigRepo, "get_config", fake_get_config)
    monkeypatch.setattr(poll_auto_send.PollConfigRepo, "is_skip_date", fake_is_skip_date)
    monkeypatch.setattr(poll_auto_send.PollsService, "get_current_poll", fake_current_poll)
    monkeypatch.setattr(poll_auto_send, "get_attendance_group_id", lambda: 1)
    monkeypatch.setattr(poll_auto_send, "check_attendance_group_access", fake_group_preflight)
    monkeypatch.setattr(poll_auto_send, "_send_poll", fail_send)

    await poll_auto_send.job_poll_auto_send(object())


@pytest.mark.asyncio
async def test_poll_answer_prefers_cached_persistent_id(monkeypatch: pytest.MonkeyPatch) -> None:
    from apps.attendance_bot.app.services import poll_runtime as poll

    poll._POLL_PERSISTENT_TO_CHOICE["poll-1"] = {"stable-remote": AttendanceChoice.REMOTE}

    class PollAnswer:
        poll_id = "poll-1"
        option_ids = [0]
        api_kwargs = {"option_persistent_ids": ["stable-remote"]}

    choice, source = await poll._resolve_choice_from_poll_answer(PollAnswer())

    assert choice == AttendanceChoice.REMOTE
    assert source == "persistent_id:stable-remote"


@pytest.mark.asyncio
async def test_poll_answer_falls_back_to_option_id(monkeypatch: pytest.MonkeyPatch) -> None:
    from apps.attendance_bot.app.services import poll_runtime as poll

    async def fake_load_config():
        return poll.LoadedPollConfig(
            payload=build_poll_payload([{"key": "CSKA", "label": "ЦСКА"}]),
            option_to_choice={1: AttendanceChoice.CSKA},
            persistent_to_choice={},
            duration_seconds=900,
            reminder_minutes=None,
        )

    monkeypatch.setattr(poll, "_load_poll_config", fake_load_config)

    class PollAnswer:
        poll_id = "poll-2"
        option_ids = [1]
        api_kwargs = {}

    choice, source = await poll._resolve_choice_from_poll_answer(PollAnswer())

    assert choice == AttendanceChoice.CSKA
    assert source == "option_id:1"


@pytest.mark.asyncio
async def test_permissions_deny_inactive_users(monkeypatch: pytest.MonkeyPatch) -> None:
    from core.screw_core.services import permissions

    async def fake_get_by_telegram_id(telegram_id: int):
        return {"id": 1, "telegram_id": telegram_id, "role": "admin", "status": "terminated"}

    monkeypatch.setattr(permissions.UsersRepo, "get_by_telegram_id", fake_get_by_telegram_id)

    result = await get_user_permissions(telegram_id=123)

    assert result.is_admin is False
    assert result.is_manager is False
    assert result.department_id is None


def test_attendance_bot_entrypoint_is_aiogram_only() -> None:
    source = Path("apps/attendance_bot/app/main.py").read_text(encoding="utf-8")

    assert "run_aiogram_runtime" in source
    assert "ATTENDANCE_BOT_RUNTIME" not in source
    assert "telegram.ext" not in source


def test_aiogram_routers_do_not_import_legacy_panel_modules() -> None:
    root = Path("apps/attendance_bot/app/aiogram_app/routers")
    forbidden = {
        "from apps.attendance_bot.app.routers",
        "from apps.attendance_bot.app.routers import start",
        "from apps.attendance_bot.app.routers import admin as",
        "from apps.attendance_bot.app.routers import poll",
        "from apps.attendance_bot.app.routers import admin_panel",
        "from apps.attendance_bot.app.routers.start_registration import",
        "from apps.attendance_bot.app.routers.admin_panel import",
    }

    matches = []
    for path in root.glob("*.py"):
        text = path.read_text(encoding="utf-8")
        for pattern in forbidden:
            if pattern in text:
                matches.append(f"{path}:{pattern}")

    assert matches == []


@pytest.mark.asyncio
async def test_attendance_lifecycle_starts_and_stops_core_services(monkeypatch: pytest.MonkeyPatch) -> None:
    from apps.attendance_bot.app import lifecycle

    lifecycle._background_tasks.clear()
    lifecycle._scheduler_instance = None

    calls = []

    class FakeTask:
        def __init__(self) -> None:
            self.cancelled = False

        def cancel(self) -> None:
            self.cancelled = True

        def __await__(self):
            async def _wait():
                raise asyncio.CancelledError

            return _wait().__await__()

    bot = object()
    scheduler = object()
    task = FakeTask()

    async def fake_create_pool() -> None:
        calls.append("create_pool")

    async def fake_check_connection() -> None:
        calls.append("check_connection")

    async def fake_bootstrap() -> None:
        calls.append("bootstrap")

    async def fake_group_preflight(bot_arg) -> None:
        calls.append(("group_preflight", bot_arg))

    async def fake_restore_auto_close(scheduler_arg, bot_arg) -> int:
        calls.append(("restore_auto_close", scheduler_arg, bot_arg))
        return 1

    async def fake_restore_reminder(scheduler_arg, bot_arg) -> int:
        calls.append(("restore_reminder", scheduler_arg, bot_arg))
        return 1

    async def fake_reschedule(scheduler_arg) -> None:
        calls.append(("reschedule_auto_send", scheduler_arg))

    async def fake_close_pool() -> None:
        calls.append("close_pool")

    monkeypatch.setattr(lifecycle, "create_pool", fake_create_pool)
    monkeypatch.setattr(lifecycle, "check_connection", fake_check_connection)
    monkeypatch.setattr(lifecycle, "bootstrap_admins_if_enabled", fake_bootstrap)
    monkeypatch.setattr(lifecycle, "check_attendance_group_access", fake_group_preflight)
    monkeypatch.setattr(lifecycle, "start_poll_reaper", lambda bot_arg: task)
    monkeypatch.setattr(lifecycle, "start_report_sender", lambda bot_arg: None)
    monkeypatch.setattr(lifecycle, "start_report_mail_scheduler", lambda: None)
    monkeypatch.setattr(lifecycle, "start_monthly_report_scheduler", lambda: None)
    monkeypatch.setattr(lifecycle, "create_scheduler", lambda: scheduler)
    monkeypatch.setattr(lifecycle, "set_bot_instance", lambda bot_arg: calls.append(("set_bot", bot_arg)))
    monkeypatch.setattr(
        lifecycle,
        "set_application_instance",
        lambda app_arg: calls.append(("set_application", app_arg)),
    )
    monkeypatch.setattr(lifecycle, "start_scheduler", lambda scheduler_arg: calls.append(("start_scheduler", scheduler_arg)))
    monkeypatch.setattr(lifecycle, "restore_poll_auto_close_jobs", fake_restore_auto_close)
    monkeypatch.setattr(lifecycle, "restore_poll_reminder_jobs", fake_restore_reminder)
    monkeypatch.setattr(lifecycle, "reschedule_poll_auto_send", fake_reschedule)
    monkeypatch.setattr(lifecycle, "shutdown_scheduler", lambda scheduler_arg: calls.append(("shutdown_scheduler", scheduler_arg)))
    monkeypatch.setattr(lifecycle, "close_pool", fake_close_pool)

    await lifecycle.attendance_startup(bot, application=None)

    assert lifecycle._scheduler_instance is scheduler
    assert lifecycle._background_tasks == [task]
    assert calls[:3] == ["create_pool", "check_connection", "bootstrap"]
    assert ("group_preflight", bot) in calls
    assert ("set_bot", bot) in calls
    assert ("start_scheduler", scheduler) in calls
    assert ("restore_auto_close", scheduler, bot) in calls
    assert ("restore_reminder", scheduler, bot) in calls
    assert ("reschedule_auto_send", scheduler) in calls

    await lifecycle.attendance_shutdown()

    assert task.cancelled is True
    assert lifecycle._scheduler_instance is None
    assert lifecycle._background_tasks == []
    assert ("shutdown_scheduler", scheduler) in calls
    assert ("set_application", None) in calls
    assert "close_pool" in calls


@pytest.mark.asyncio
async def test_group_preflight_reports_inaccessible_group(monkeypatch: pytest.MonkeyPatch) -> None:
    from apps.attendance_bot.app.services import group_preflight

    class FakeBot:
        async def get_chat(self, chat_id: int):
            raise RuntimeError("Bad Request: chat not found")

    monkeypatch.setattr(group_preflight, "get_attendance_group_id", lambda: -1001)

    result = await group_preflight.check_attendance_group_access(FakeBot())

    assert result.configured is True
    assert result.accessible is False
    assert result.chat_id == -1001
    assert "chat not found" in (result.error or "")


@pytest.mark.asyncio
async def test_group_preflight_reports_accessible_group(monkeypatch: pytest.MonkeyPatch) -> None:
    from apps.attendance_bot.app.services import group_preflight

    class Chat:
        type = "supergroup"

    class Me:
        id = 42

    class Member:
        status = "administrator"
        can_pin_messages = True

    class FakeBot:
        async def get_chat(self, chat_id: int):
            return Chat()

        async def get_me(self):
            return Me()

        async def get_chat_member(self, chat_id: int, user_id: int):
            return Member()

    monkeypatch.setattr(group_preflight, "get_attendance_group_id", lambda: -1001)

    result = await group_preflight.check_attendance_group_access(FakeBot())

    assert result.configured is True
    assert result.accessible is True
    assert result.chat_type == "supergroup"
    assert result.bot_status == "administrator"


def test_aiogram_dispatcher_smoke_builds_with_memory_storage() -> None:
    dp = create_aiogram_dispatcher()

    assert dp.storage.__class__.__name__ == "MemoryStorage"
    assert [router.name for router in dp.sub_routers] == [
        "attendance_start",
        "attendance_poll",
        "attendance_reports",
        "attendance_admin",
        "attendance_admin_panel",
        "attendance_flud",
        "attendance_chat_member",
        "attendance_user_status",
        "attendance_fallback",
    ]
