#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
WHEELHOUSE_DIR="${WHEELHOUSE_DIR:-$ROOT_DIR/wheelhouse}"

mkdir -p "$WHEELHOUSE_DIR"
python -m pip download \
  --requirement "$ROOT_DIR/requirements.lock.txt" \
  --dest "$WHEELHOUSE_DIR"

echo "Wheelhouse prepared for the current platform: $WHEELHOUSE_DIR"
