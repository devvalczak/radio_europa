#!/bin/sh
# Development frontend: installs npm packages when package-lock.json changed, then runs Vite (or the given command,
# e.g. `sh dev/web.sh npm run build`). Used by docker-compose.dev.yml (service web) and by `make dev-local`.
set -e
cd "$(dirname "$0")/../frontend"
if [ ! -f node_modules/.jarvis-lock ] || ! cmp -s package-lock.json node_modules/.jarvis-lock; then
  # `npm install --no-save`, not `npm ci`: in Docker node_modules is a volume mount point that npm ci cannot remove
  npm install --no-save --no-audit --no-fund
  cp package-lock.json node_modules/.jarvis-lock
fi
[ $# -gt 0 ] && exec "$@"
exec npx vite --host "${HOST:-127.0.0.1}" --port 5173 --strictPort
