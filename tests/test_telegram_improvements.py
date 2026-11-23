from datetime import date
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from aiogram.exceptions import TelegramBadRequest, TelegramNetworkError
from aiogram.methods import SendRichMessage, SendWidget
from aiogram.types import InputRichMessage, WidgetQueryTypeData

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
async def test_widget_rejection_opens_three_expandable_weeks(monkeypatch):
    import json

    monkeypatch.setenv("ATTENDANCE_WEEK_WIDGET_PILOT_IDS", "123")
    monkeypatch.setenv("TELEGRAM_RICH_MESSAGES_ENABLED", "1")
    monkeypatch.setattr(week_widget, "get_local_date", lambda: date(2026, 11, 23))

    async def week_data(user_id, start):
        assert user_id == 1
        return json.dumps({"title": start.isoformat(), "days": [["23.11", "<Office>"]]})

    monkeypatch.setattr(week_widget, "_week_data", week_data)
    callback = SimpleNamespace(
        message=SimpleNamespace(chat=SimpleNamespace(id=123, type="private")),
        from_user=SimpleNamespace(id=123),
        answer=AsyncMock(),
    )
    bot = SimpleNamespace(
        send_widget=AsyncMock(side_effect=TelegramBadRequest(
            method=SendWidget(chat_id=123, html="<p>Widget</p>"),
            message="INPUT_MEDIA_WIDGET_NOT_ALLOWED",
        )),
        send_rich_message=AsyncMock(return_value={"message_id": 10}),
        send_message=AsyncMock(),
    )

    await week_widget.open_week_widget(callback, bot, {"id": 1})

    bot.send_rich_message.assert_awaited_once()
    params = bot.send_rich_message.await_args.kwargs
    assert params["chat_id"] == 123 and params["protect_content"]
    html = params["rich_message"].html
    assert html.count("<details") == 3
    assert "<details open><summary>2026-11-23</summary>" in html
    assert "<details><summary>2026-11-16</summary>" in html
    assert "<details><summary>2026-11-09</summary>" in html
    assert "&lt;Office&gt;" in html
    bot.send_message.assert_not_awaited()
