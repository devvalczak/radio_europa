#!/bin/sh
# Development backend: uvicorn with auto-reload (code in core/app and modules), data in ./data-dev.
# Used by docker-compose.dev.yml (service api) and by `make dev-local`.
set -e
ROOT=$(cd "$(dirname "$0")/.." && pwd)
cd "$ROOT"
PYTHON=${PYTHON:-python3}
PYTHON="$PYTHON" sh dev/ensure-env.sh
set -a; . ./.env.dev; set +a
mkdir -p data-dev
export JARVIS_DATABASE_URL="sqlite:///$ROOT/data-dev/jarvis.db" JARVIS_MODULES_DIR="$ROOT/modules" \
  JARVIS_LOG_DIR="$ROOT/data-dev/logs" JARVIS_BACKUP_DIR="$ROOT/data-dev/backups" JARVIS_COOKIE_SECURE=false \
  PYTHONPATH="$ROOT/core:$ROOT"
echo "Jarvis dev: http://localhost:5173 · first login token (until an account exists): $(grep '^JARVIS_SETUP_TOKEN=' .env.dev | cut -d= -f2-)"
exec "$PYTHON" -m uvicorn app.main:app --host "${HOST:-127.0.0.1}" --port 8000 --reload \
  --reload-dir "$ROOT/core/app" --reload-dir "$ROOT/modules"
