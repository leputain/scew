#!/usr/bin/env bash
set -Eeuo pipefail

COMPOSE="${COMPOSE:-docker compose}"
APP_VERSION="${APP_VERSION:-$(tr -d '\r\n' < VERSION)}"
BUILD_DATE="${BUILD_DATE:-$(date -u +%Y-%m-%dT%H:%M:%SZ)}"

if [ -z "${VCS_REF:-}" ]; then
  VCS_REF=$(
    find apps core -type f -name '*.py' -print0 \
      | sort -z \
      | xargs -0 sha256sum \
      | { cat; sha256sum requirements.lock.txt Dockerfile VERSION; } \
      | sha256sum \
      | cut -c1-16
  )
fi

export APP_VERSION BUILD_DATE VCS_REF
if [ ! -f ".env" ]; then
  echo ".env not found. Create it from .env.example and fill secrets first." >&2
  exit 2
fi

echo "Validating compose config..."
$COMPOSE config >/dev/null

echo "Building attendance_bot image locally..."
echo "Release identity: version=$APP_VERSION revision=$VCS_REF"
$COMPOSE build attendance_bot

echo "Starting database..."
$COMPOSE up -d db

echo "Starting attendance_bot (aiogram runtime)..."
$COMPOSE up -d attendance_bot

echo "Current containers:"
$COMPOSE ps

echo "Running post-deploy smoke..."
bash scripts/post_deploy_smoke.sh
