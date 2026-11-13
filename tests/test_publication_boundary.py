"""Publication checks reject private paths without opening their contents."""

from pathlib import Path

import pytest

from scripts import public_repo_audit


@pytest.mark.parametrize("relative", [
    "secrets/notifier/bot-token",
    "apps/worker/secrets/database.json",
    "secret/database.json",
    "nested/Secrets/credentials.json",
    ".env",
    "backups/synthetic.dump",
])
def test_forbidden_paths_are_reported_without_reading_material(tmp_path, monkeypatch, relative):
    path = tmp_path / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("synthetic private material", encoding="utf-8")
    monkeypatch.setattr(public_repo_audit, "ROOT", tmp_path)
    monkeypatch.setattr(public_repo_audit, "tracked_files", lambda: [path])

    def forbidden_read(self):
        raise AssertionError("A forbidden publication path must not be opened")

    monkeypatch.setattr(Path, "read_bytes", forbidden_read)
    assert public_repo_audit.audit() == [f"forbidden path: {relative}"]


def test_allowed_source_is_still_scanned_for_credentials(tmp_path, monkeypatch):
    path = tmp_path / "worker.py"
    synthetic = "ghp_" + "a" * 30
    path.write_text(f"token = {synthetic!r}\n", encoding="utf-8")
    monkeypatch.setattr(public_repo_audit, "ROOT", tmp_path)
    monkeypatch.setattr(public_repo_audit, "tracked_files", lambda: [path])
    assert public_repo_audit.audit() == ["GitHub token: worker.py"]
