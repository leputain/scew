#!/usr/bin/env python3
"""Fail when the Git publication manifest contains high-risk private material."""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parents[1]
SELF = Path(__file__).resolve()
MAX_TRACKED_FILE_BYTES = 5 * 1024 * 1024

FORBIDDEN_PATHS = (
    re.compile(r"(^|/)\.env($|\.)"),
    re.compile(r"(^|/)backups?/", re.IGNORECASE),
    re.compile(r"\.(?:dump|sql\.gz|pem|key|p12|pfx|whl|log)$", re.IGNORECASE),
    re.compile(r"(^|/)redos-local\.repo$", re.IGNORECASE),
    re.compile(r"(^|/)\.gitlab-ci\.yml$", re.IGNORECASE),
)

CONTENT_PATTERNS = {
    "Telegram bot token": re.compile(rb"\b[0-9]{8,10}:[A-Za-z0-9_-]{30,}\b"),
    "GitHub token": re.compile(rb"\b(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,})\b"),
    "AWS access key": re.compile(rb"\bAKIA[0-9A-Z]{16}\b"),
    "private key": re.compile(rb"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    "credential in URL": re.compile(rb"https?://[^\s/:]+:[^\s/@]+@", re.IGNORECASE),
    "private network URL": re.compile(
        rb"https?://(?:10(?:\.\d{1,3}){3}|192\.168(?:\.\d{1,3}){2}|172\.(?:1[6-9]|2\d|3[01])(?:\.\d{1,3}){2})(?::\d+)?",
        re.IGNORECASE,
    ),
    "Telegram private message link": re.compile(rb"https://t\.me/c/(?!1234567890/)[0-9]{6,}"),
}


def tracked_files() -> list[Path]:
    result = subprocess.run(
        ["git", "ls-files", "-z"],
        cwd=ROOT,
        check=True,
        capture_output=True,
    )
    return [ROOT / item.decode("utf-8") for item in result.stdout.split(b"\0") if item]


def audit() -> list[str]:
    findings: list[str] = []
    for path in tracked_files():
        relative = PurePosixPath(path.relative_to(ROOT).as_posix())
        relative_text = str(relative)

        if relative_text != ".env.example":
            for pattern in FORBIDDEN_PATHS:
                if pattern.search(relative_text):
                    findings.append(f"forbidden path: {relative_text}")

        if not path.is_file():
            continue
        size = path.stat().st_size
        if size > MAX_TRACKED_FILE_BYTES:
            findings.append(f"oversized tracked file: {relative_text} ({size} bytes)")

        data = path.read_bytes()
        if b"\0" in data or path.resolve() == SELF:
            continue
        for label, pattern in CONTENT_PATTERNS.items():
            if pattern.search(data):
                findings.append(f"{label}: {relative_text}")
    return findings


def main() -> int:
    findings = audit()
    if findings:
        print("Public repository audit failed:", file=sys.stderr)
        for finding in sorted(set(findings)):
            print(f"- {finding}", file=sys.stderr)
        return 1
    print("Public repository audit: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
