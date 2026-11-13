"""Public safety settings must survive reconciliation with the live source."""

import pytest

from apps.attendance_bot.app import config
from apps.attendance_bot.app.jobs.monthly_stats import is_monthly_stats_enabled
from core.screw_core.config.settings import DatabaseSettings
from core.screw_core.services import notify
from scripts.flud_import_telegram_html_export import _parse_args


def test_notification_functions_require_explicit_opt_in(monkeypatch):
    for name in ("MONTHLY_STATS_ENABLED", "VIOLATIONS_NOTIFY_ENABLED", "BIRTHDAY_NOTIFY_ENABLED"):
        monkeypatch.delenv(name, raising=False)
    assert not is_monthly_stats_enabled()
    assert not notify.get_violations_notify_enabled()
    assert not notify.get_birthday_notify_enabled()


def test_database_does_not_restore_the_live_password_fallback(monkeypatch):
    monkeypatch.delenv("POSTGRES_PASSWORD", raising=False)
    with pytest.raises(ValueError, match="POSTGRES_PASSWORD"):
        DatabaseSettings()


def test_welcome_contacts_stay_configurable_and_neutral_by_default(monkeypatch):
    for name in ("ATTENDANCE_WELCOME_OZKO_CONTACT", "ATTENDANCE_WELCOME_OSB_CONTACT",
                 "ATTENDANCE_WORKPLACE_SETUP_URL"):
        monkeypatch.delenv(name, raising=False)
    assert config.get_welcome_ozko_contact() == "уточните у администратора"
    assert config.get_welcome_osb_contact() == "уточните у администратора"
    assert config.get_workplace_setup_url() == ""
    monkeypatch.setenv("ATTENDANCE_WELCOME_OZKO_CONTACT", "Synthetic contact")
    assert config.get_welcome_ozko_contact() == "Synthetic contact"


def test_export_import_has_no_deployment_topic_fallback():
    args = _parse_args(["--path", "synthetic-export", "--chat-id", "-1001234567890",
                        "--start-date", "2026-01-01", "--end-date", "2026-01-02", "--dry-run"])
    assert args.topic_root_message_id is None
