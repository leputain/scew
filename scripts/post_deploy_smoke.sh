#!/usr/bin/env bash
set -Eeuo pipefail

COMPOSE="${COMPOSE:-docker compose}"
LOG_SINCE="${SMOKE_LOG_SINCE:-10m}"
MAX_HEARTBEAT_AGE="${SMOKE_MAX_HEARTBEAT_AGE:-120}"

fail() {
  printf 'smoke: FAIL: %s\n' "$1" >&2
  exit 1
}

$COMPOSE config --quiet

cid=$($COMPOSE ps -q attendance_bot)
[ -n "$cid" ] || fail "attendance_bot container is missing"

state=$(docker inspect "$cid" --format '{{.State.Status}}')
health=$(docker inspect "$cid" --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}')
[ "$state" = "running" ] || fail "attendance_bot state=$state"
[ "$health" = "healthy" ] || fail "attendance_bot health=$health"

heartbeat_age=$(
  $COMPOSE exec -T attendance_bot sh -lc \
    'value=$(cat /tmp/bot_heartbeat_attendance 2>/dev/null || echo 0); echo $(( $(date +%s) - value ))'
)
[ "$heartbeat_age" -ge 0 ] 2>/dev/null || fail "heartbeat value is invalid"
[ "$heartbeat_age" -lt "$MAX_HEARTBEAT_AGE" ] || fail "heartbeat age=${heartbeat_age}s"

$COMPOSE exec -T db sh -lc \
  'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Atqc "SELECT 1"' \
  | grep -qx '1' || fail "database check failed"

runtime=$(
  $COMPOSE exec -T attendance_bot python3 -c \
    'import aiogram, importlib.util, os; assert importlib.util.find_spec("telegram") is None; print(os.getenv("SCREW_APP_VERSION", "unknown") + " aiogram=" + aiogram.__version__)'
)

telegram=$(
  $COMPOSE exec -T attendance_bot python3 - <<'PY'
import asyncio

from apps.attendance_bot.app.aiogram_app.main import create_aiogram_bot


async def main() -> None:
    bot = create_aiogram_bot()
    try:
        await bot.get_me()
        info = await bot.get_webhook_info()
        print(f"ok pending_updates={info.pending_update_count or 0}")
    finally:
        await bot.session.close()


asyncio.run(main())
PY
)

error_count=$(
  $COMPOSE logs --since "$LOG_SINCE" attendance_bot 2>&1 \
    | grep -Eic 'traceback|unhandled|critical|unauthorized|conflict|error' || true
)
[ "$error_count" -eq 0 ] || fail "runtime error patterns=$error_count since $LOG_SINCE"

printf 'smoke: OK: state=%s health=%s heartbeat=%ss runtime=%s telegram=%s errors=%s\n' \
  "$state" "$health" "$heartbeat_age" "$runtime" "$telegram" "$error_count"
