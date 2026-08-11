"""Small redaction helpers for exception/log messages."""

from __future__ import annotations

import re
from typing import Any

_BOT_TOKEN_RE = re.compile(r"\b\d{6,12}:[A-Za-z0-9_-]{20,}\b")
_BOT_URL_RE = re.compile(r"(bot)\d{6,12}:[A-Za-z0-9_-]{20,}", re.IGNORECASE)


def redact_text(value: Any) -> str:
    text = str(value)
    text = _BOT_URL_RE.sub(r"\1<redacted>", text)
    return _BOT_TOKEN_RE.sub("<redacted-token>", text)
