"""
Timezone helpers.

Provides a resilient tzinfo loader that falls back when IANA data is missing
(common on Windows without tzdata installed).
"""

from __future__ import annotations

from datetime import timezone, tzinfo
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from core.screw_core.utils.logging import get_logger

logger = get_logger(__name__)


def get_tzinfo(name: str, fallback: str = "UTC") -> tzinfo:
    """
    Return tzinfo for a timezone name, with a safe fallback.

    - If the requested zone is missing, try fallback.
    - If fallback is also missing, use built-in UTC.
    """
    try:
        return ZoneInfo(name)
    except ZoneInfoNotFoundError:
        logger.warning("timezone '%s' not found; falling back to '%s'", name, fallback)
        try:
            return ZoneInfo(fallback)
        except ZoneInfoNotFoundError:
            logger.warning("fallback timezone '%s' not found; using UTC", fallback)
            return timezone.utc
