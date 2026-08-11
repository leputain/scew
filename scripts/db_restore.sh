#!/usr/bin/env bash
set -Eeuo pipefail

COMPOSE="${COMPOSE:-docker compose}"
DB_SERVICE="${DB_SERVICE:-db}"
POSTGRES_DB="${POSTGRES_DB:-screw}"
POSTGRES_USER="${POSTGRES_USER:-screw}"
SCHEMA_NAME="${SCHEMA_NAME:-screw}"
DROP_SCHEMA="${DROP_SCHEMA:-1}"

backup_path="${1:-}"
if [ -z "$backup_path" ]; then
  echo "Usage: CONFIRM_RESTORE=YES bash scripts/db_restore.sh backups/file.sql.gz" >&2
  exit 2
fi

if [ ! -f "$backup_path" ]; then
  echo "Backup file not found: $backup_path" >&2
  exit 2
fi

if [ "${CONFIRM_RESTORE:-}" != "YES" ]; then
  cat >&2 <<EOF
Restore is destructive for database '$POSTGRES_DB' schema '$SCHEMA_NAME'.
Run explicitly:

  CONFIRM_RESTORE=YES bash scripts/db_restore.sh "$backup_path"

Optional:
  DROP_SCHEMA=0  # restore without dropping schema first
EOF
  exit 2
fi

echo "Starting DB service..."
$COMPOSE up -d "$DB_SERVICE"

if [ "$DROP_SCHEMA" = "1" ]; then
  echo "Dropping schema '$SCHEMA_NAME' before restore..."
  $COMPOSE exec -T "$DB_SERVICE" psql \
    -U "$POSTGRES_USER" \
    -d "$POSTGRES_DB" \
    -v ON_ERROR_STOP=1 \
    -c "DROP SCHEMA IF EXISTS \"$SCHEMA_NAME\" CASCADE;"
fi

echo "Restoring from: $backup_path"
case "$backup_path" in
  *.gz)
    gzip -dc "$backup_path" | $COMPOSE exec -T "$DB_SERVICE" psql \
      -U "$POSTGRES_USER" \
      -d "$POSTGRES_DB" \
      -v ON_ERROR_STOP=1
    ;;
  *)
    $COMPOSE exec -T "$DB_SERVICE" psql \
      -U "$POSTGRES_USER" \
      -d "$POSTGRES_DB" \
      -v ON_ERROR_STOP=1 < "$backup_path"
    ;;
esac

echo "Restore complete. Quick checks:"
$COMPOSE exec -T "$DB_SERVICE" psql \
  -U "$POSTGRES_USER" \
  -d "$POSTGRES_DB" \
  -v ON_ERROR_STOP=1 \
  -c "SELECT COUNT(*) AS users_count FROM screw.users;"
