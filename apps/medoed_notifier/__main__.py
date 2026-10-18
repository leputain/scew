"""Run with ``python -m apps.medoed_notifier``; never starts Telegram polling."""

import asyncio

from apps.medoed_notifier.worker import main


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
