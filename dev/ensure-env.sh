#!/bin/sh
# Creates .env.dev (development-only secrets) on first use; never overwrites it. Paths are NOT stored here:
# dev/api.sh sets them for wherever the repo is (container: /repo, Mac/Linux: the clone).
set -e
cd "$(dirname "$0")/.."
[ -f .env.dev ] && exit 0
{ "${PYTHON:-python3}" scripts/gen_secrets.py
  printf '%s\n' "JARVIS_COOKIE_SECURE=false" "JARVIS_VAPID_SUBJECT=mailto:dev@example.com"
} > .env.dev.tmp
mv .env.dev.tmp .env.dev
echo "Created .env.dev (dev secrets). First-login token: $(grep '^JARVIS_SETUP_TOKEN=' .env.dev | cut -d= -f2-)"
