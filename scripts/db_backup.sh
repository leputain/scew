#!/usr/bin/env bash
set -Eeuo pipefail

COMPOSE="${COMPOSE:-docker compose}"
DB_SERVICE="${DB_SERVICE:-db}"
POSTGRES_DB="${POSTGRES_DB:-screw}"
POSTGRES_USER="${POSTGRES_USER:-screw}"
BACKUP_DIR="${BACKUP_DIR:-backups}"

mkdir -p "$BACKUP_DIR"

timestamp="$(date +%Y%m%d_%H%M%S)"
backup_path="$BACKUP_DIR/screw_${POSTGRES_DB}_${timestamp}.sql.gz"

if ! $COMPOSE ps -q "$DB_SERVICE" >/dev/null 2>&1; then
  echo "DB service '$DB_SERVICE' is not known to docker compose" >&2
  exit 1
fi

if [ -z "$($COMPOSE ps -q "$DB_SERVICE")" ]; then
  echo "DB service '$DB_SERVICE' is not running" >&2
  exit 1
fi

echo "Creating PostgreSQL backup: $backup_path"
$COMPOSE exec -T "$DB_SERVICE" pg_dump \
  -U "$POSTGRES_USER" \
  -d "$POSTGRES_DB" \
  --format=plain \
  --no-owner \
  --no-acl \
  | gzip -9 > "$backup_path"

gzip -t "$backup_path"

if command -v sha256sum >/dev/null 2>&1; then
  sha256sum "$backup_path" > "$backup_path.sha256"
  echo "Checksum: $backup_path.sha256"
fi

echo "Backup complete: $backup_path"
