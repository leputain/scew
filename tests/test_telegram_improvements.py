from datetime import date
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from aiogram.exceptions import TelegramBadRequest, TelegramNetworkError
from aiogram.methods import SendRichMessage, SendWidget
from aiogram.types import Chat, InputRichMessage, Message, WidgetQueryTypeData

from apps.attendance_bot.app.aiogram_app.routers import week_widget
from apps.attendance_bot.app.services import poll_runtime
from core.screw_core.models.enums import AttendanceChoice
from core.screw_core.services.telegram_delivery import send_system_message


@pytest.mark.asyncio
async def test_saved_poll_snapshot_controls_index_and_persistent_id():
    snapshot = [
        {"index": 0, "persistent_id": "remote-original", "choice": "REMOTE"},
        {"index": 1, "persistent_id": "office-original", "choice": "CSKA"},
    ]
    answer = SimpleNamespace(poll_id="original", option_ids=[0], option_persistent_ids=["office-original"])
    choice, _ = await poll_runtime._resolve_choice_from_poll_answer(answer, snapshot)
    assert choice == AttendanceChoice.CSKA
    answer.option_persistent_ids = []
    choice, _ = await poll_runtime._resolve_choice_from_poll_answer(answer, snapshot)
    assert choice == AttendanceChoice.REMOTE
    answer.option_persistent_ids = ["different-poll"]
    choice, _ = await poll_runtime._resolve_choice_from_poll_answer(answer, snapshot)
    assert choice is None


@pytest.mark.asyncio
@pytest.mark.parametrize("error_type", [TelegramBadRequest, TelegramNetworkError])
async def test_tracked_delivery_uses_html_only_after_definitive_rejection(monkeypatch, error_type):
    monkeypatch.setenv("TELEGRAM_RICH_MESSAGES_ENABLED", "1")
    method = SendRichMessage(chat_id=1, rich_message=InputRichMessage(html="<p>Report</p>"))
    bot = SimpleNamespace(
        send_rich_message=AsyncMock(side_effect=error_type(method=method, message="synthetic failure")),
        send_message=AsyncMock(return_value={"message_id": 1}),
    )
    kwargs = dict(chat_id=1, html_text="Report", strict_rich_fallback=True)
    if error_type is TelegramNetworkError:
        with pytest.raises(TelegramNetworkError):
            await send_system_message(bot, **kwargs)
        bot.send_message.assert_not_awaited()
    else:
        result = await send_system_message(bot, **kwargs)
        assert result.method == "html"
        bot.send_message.assert_awaited_once()


@pytest.mark.asyncio
async def test_week_widget_edits_only_the_callers_private_card(monkeypatch):
    monkeypatch.setenv("ATTENDANCE_WEEK_WIDGET_PILOT_IDS", "123")
    monkeypatch.setattr(week_widget, "get_local_date", lambda: date(2026, 11, 23))
    week_data = AsyncMock(return_value="{}")
    monkeypatch.setattr(week_widget, "_week_data", week_data)
    bot = SimpleNamespace(answer_widget_query=AsyncMock(), edit_message_widget_data=AsyncMock())
    query = SimpleNamespace(
        id="q", from_user=SimpleNamespace(id=123), chat=SimpleNamespace(id=456, type="private"),
        message_id=7, widget_id=None, type=WidgetQueryTypeData(), data="screw-week:2026-11-16",
    )

    await week_widget.update_week_widget(query, bot, {"id": 1})
    week_data.assert_not_awaited()
    bot.edit_message_widget_data.assert_not_awaited()

    query.chat.id = 123
    await week_widget.update_week_widget(query, bot, {"id": 1})
    week_data.assert_awaited_once_with(1, date(2026, 11, 16))
    bot.edit_message_widget_data.assert_awaited_once_with(chat_id=123, message_id=7, chat_data="{}")


@pytest.mark.asyncio
@pytest.mark.parametrize("entry, pilot_ids, native_mode, widget_calls", [
    ("user:calendar", "", "0", 0),
    ("user:calendar", "123", "1", 0),
    ("user:week_widget", "123", "1", 1),
])
async def test_personal_week_navigation_uses_original_private_message(
    monkeypatch, entry, pilot_ids, native_mode, widget_calls,
):
    import json
    from datetime import datetime, timedelta, timezone

    monkeypatch.setenv("ATTENDANCE_WEEK_WIDGET_PILOT_IDS", pilot_ids)
    monkeypatch.setenv("ATTENDANCE_NATIVE_WEEK_WIDGET_ENABLED", native_mode)
    today = date(2026, 11, 23)
    monkeypatch.setattr(week_widget, "get_local_date", lambda: today)

    async def week_data(user_id, start):
        assert user_id == 1
        return json.dumps({
            "title": start.isoformat(),
            "days": [[start.strftime("%d.%m"), "<Office>"]],
            "current": today.isoformat(),
            "previous": (start - timedelta(days=7)).isoformat(),
            "next": (start + timedelta(days=7)).isoformat(),
        })

    monkeypatch.setattr(week_widget, "_week_data", week_data)
    edit_text = AsyncMock()
    monkeypatch.setattr(Message, "edit_text", edit_text)
    callback = SimpleNamespace(
        data=entry,
        message=Message(message_id=7, date=datetime.now(timezone.utc), chat=Chat(id=123, type="private")),
        from_user=SimpleNamespace(id=123),
        answer=AsyncMock(),
    )
    bot = SimpleNamespace(send_widget=AsyncMock(side_effect=TelegramBadRequest(
        method=SendWidget(chat_id=123, html="<p>Widget</p>"),
        message="INPUT_MEDIA_WIDGET_NOT_ALLOWED",
    )))

    await week_widget.open_week_widget(callback, bot, {"id": 1})
    assert bot.send_widget.await_count == widget_calls
    edit_text.assert_awaited_once()
    args = edit_text.await_args
    assert "Пн, 23.11 · сегодня" in args.args[0]
    assert "&lt;Office&gt;" in args.args[0]
    rows = args.kwargs["reply_markup"].inline_keyboard
    assert [button.callback_data for button in rows[0]] == ["nav:ignore", "user:month", "user:my_statuses"]
    back = rows[1][0]
    assert back.callback_data == "user:week:2026-11-16"
    assert rows[2][0].style == "primary"
    assert rows[-1][0].callback_data == "panel:user"
    assert (rows[-2][0].callback_data == "user:week_widget") == (native_mode == "1")

    callback.data = back.callback_data
    await week_widget.open_week_widget(callback, bot, {"id": 1})
    assert "Пн, 16.11" in edit_text.await_args.args[0]
    assert edit_text.await_count == 2
    assert bot.send_widget.await_count == widget_calls

    callback.message = callback.message.model_copy(update={"chat": Chat(id=456, type="private")})
    await week_widget.open_week_widget(callback, bot, {"id": 1})
    assert edit_text.await_count == 2


def test_personal_menu_highlights_absence_and_keeps_voting_in_the_group():
    from apps.attendance_bot.app.aiogram_app.keyboards import to_aiogram_markup
    from apps.attendance_bot.app.ui.keyboards_neutral import (
        my_statuses_keyboard,
        user_month_navigation_keyboard,
        user_panel_keyboard,
        user_status_navigation_keyboard,
    )

    markup = to_aiogram_markup(user_panel_keyboard(is_ozko=True))
    rows = markup.inline_keyboard
    assert [button.callback_data for button in rows[0]] == ["user:today", "user:calendar"]
    assert rows[1][0].callback_data == "status:create"
    assert rows[1][0].style == "primary"
    assert [button.callback_data for button in rows[2]] == ["user:who_in_office", "user:my_stats"]
    assert rows[-2][0].callback_data == "nav:help:user"
    today = user_status_navigation_keyboard().inline_keyboard
    assert all(button.url is None for row in today for button in row)
    assert today[1][1].callback_data == "user:calendar"
    month = user_month_navigation_keyboard(2026, 11).inline_keyboard
    assert [button.callback_data for button in month[0]] == ["user:calendar", "nav:ignore", "user:my_statuses"]
    absences = my_statuses_keyboard([]).inline_keyboard
    assert [button.callback_data for button in absences[0]] == ["user:calendar", "user:month", "nav:ignore"]
    assert absences[1][0].callback_data == "status:create"
    assert absences[1][0].style == "primary"
