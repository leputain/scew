"""Aiogram adapter for runtime-neutral keyboard specifications."""

from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup


def to_aiogram_markup(markup) -> InlineKeyboardMarkup | None:
    """Convert a runtime-neutral keyboard specification to aiogram markup."""
    if markup is None:
        return None

    rows = []
    for row in getattr(markup, "inline_keyboard", []) or []:
        converted_row = []
        for button in row:
            data = button.to_dict() if hasattr(button, "to_dict") else {}
            if not data and hasattr(button, "model_dump"):
                data = button.model_dump(exclude_none=True)
            converted_row.append(
                InlineKeyboardButton(
                    text=data.get("text", getattr(button, "text", "")),
                    url=data.get("url"),
                    callback_data=data.get("callback_data"),
                    switch_inline_query=data.get("switch_inline_query"),
                    switch_inline_query_current_chat=data.get("switch_inline_query_current_chat"),
                    pay=data.get("pay"),
                )
            )
        rows.append(converted_row)

    return InlineKeyboardMarkup(inline_keyboard=rows)
