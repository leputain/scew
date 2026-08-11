"""Runtime-neutral inline keyboard primitives.

These tiny specs intentionally expose the same ``inline_keyboard`` shape as
Telegram markups so aiogram and legacy adapters can convert them locally.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class KeyboardButtonSpec:
    text: str
    callback_data: str | None = None
    url: str | None = None
    switch_inline_query: str | None = None
    switch_inline_query_current_chat: str | None = None
    pay: bool | None = None

    def to_dict(self) -> dict[str, object]:
        data: dict[str, object] = {"text": self.text}
        for key, value in (
            ("callback_data", self.callback_data),
            ("url", self.url),
            ("switch_inline_query", self.switch_inline_query),
            ("switch_inline_query_current_chat", self.switch_inline_query_current_chat),
            ("pay", self.pay),
        ):
            if value is not None:
                data[key] = value
        return data


@dataclass(frozen=True)
class KeyboardMarkupSpec:
    inline_keyboard: list[list[KeyboardButtonSpec]]


def kb(text: str, callback_data: str | None = None, **kwargs) -> KeyboardButtonSpec:
    """Build a runtime-neutral inline button."""
    return KeyboardButtonSpec(text=text, callback_data=callback_data, **kwargs)


def km(rows: list[list[KeyboardButtonSpec]]) -> KeyboardMarkupSpec:
    """Build a runtime-neutral inline markup."""
    return KeyboardMarkupSpec(inline_keyboard=rows)
