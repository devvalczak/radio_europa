# the project venv (make dev-local-init) when it exists and runs here (a Mac venv does not run in the dev container),
# else python3 from PATH (macOS has no `python`)
PYTHON ?= $(shell .venv/bin/python -c '' >/dev/null 2>&1 && echo "$(CURDIR)/.venv/bin/python" || echo python3)
# development in Docker (docker-compose.dev.yml); files it creates in the checkout belong to you
DEV = DEV_UID=$$(id -u) DEV_GID=$$(id -g) docker compose -f docker-compose.dev.yml
.PHONY: encryption-key init up down logs build up-tunnel up-quick quick-url test migration new-module backup restore
.PHONY: dev-init dev dev-down dev-logs dev-test dev-build dev-migration dev-sh dev-local-init dev-local

init: ## create .env with fresh secrets (never overwrites an existing .env)
	@test ! -f .env || (echo ".env already exists, not touching it" && exit 1)
	@mkdir -p data
	@cp .env.example .env
	@$(PYTHON) scripts/gen_secrets.py > .secrets.tmp 2>/dev/null || \
	  docker run --rm -v "$$PWD/scripts:/s:ro" python:3.12-slim sh -c "pip install -q cryptography >/dev/null 2>&1 && python /s/gen_secrets.py" > .secrets.tmp
	@while IFS='=' read -r k v; do sed -i.bak "s|^$$k=.*|$$k=$$v|" .env; done < .secrets.tmp
	@rm -f .secrets.tmp .env.bak
	@echo "Created .env. Your one-time setup token:"; grep '^JARVIS_SETUP_TOKEN' .env

up: ## start locally on http://localhost:8080
	docker compose up -d --build

up-tunnel: ## start + named Cloudflare tunnel
	docker compose --profile tunnel up -d --build

up-quick: ## start + temporary trycloudflare.com URL
	docker compose --profile quick up -d --build

quick-url:
	@docker compose logs cloudflared-quick 2>&1 | grep -o 'https://[a-z0-9-]*\.trycloudflare\.com' | tail -1

down:
	docker compose --profile tunnel --profile quick down

logs:
	docker compose logs -f --tail=100

test: ## backend tests (needs: pip install -r core/requirements-dev.txt)
	cd core && $(PYTHON) -m pytest -q

migration: ## new migration from model changes: make migration m="add thing"
	@rm -f .mig.db
	cd core && JARVIS_LOG_DIR= JARVIS_SECRET_KEY=$$(printf 'x%.0s' $$(seq 40)) JARVIS_DATABASE_URL=sqlite:///../.mig.db \
	  JARVIS_MODULES_DIR=$$PWD/../modules PYTHONPATH=$$PWD:$$PWD/.. \
	  sh -c '$(PYTHON) -m alembic upgrade head && $(PYTHON) -m alembic revision --autogenerate -m "$(m)"'
	@rm -f .mig.db

new-module: ## scaffold a module: make new-module name=pogoda
	@test -n "$(name)" || (echo "usage: make new-module name=<id>" && exit 1)
	python3 scripts/new_module.py $(name)

encryption-key: ## add JARVIS_ENCRYPTION_KEY to an existing .env (needed by the mail module)
	@test -f .env || (echo "no .env: run make init" && exit 1)
	@grep -q '^JARVIS_ENCRYPTION_KEY=.\+' .env && echo "already set" || ( \
	  KEY=$$($(PYTHON) scripts/gen_secrets.py | grep '^JARVIS_ENCRYPTION_KEY=' | cut -d= -f2-) ; \
	  test -n "$$KEY" || (echo "needs python3 with 'cryptography' installed" && exit 1) ; \
	  sed -i.bak '/^JARVIS_ENCRYPTION_KEY=/d' .env && rm -f .env.bak && echo "JARVIS_ENCRYPTION_KEY=$$KEY" >> .env && echo "key added to .env" )

backup: ## make a database backup now (into data/backups, same as the button in Ustawienia)
	docker compose exec core python -c "from app import backup; print(backup.create()['name'])"

restore: ## restore a backup: make restore f=data/backups/jarvis-....db.gz (stops core, keeps the old db aside)
	@test -n "$(f)" || (echo "usage: make restore f=data/backups/<file>.db.gz" && exit 1)
	docker compose stop core
	$(PYTHON) scripts/restore_backup.py "$(f)" --db data/jarvis.db
	docker compose start core

# --- development in Docker: nothing but Docker on the host (macOS: Docker Desktop or OrbStack) ---------------------
dev-init: ## optional: build the dev image and create .env.dev (prints the first-login token); `make dev` does both too
	$(DEV) build
	$(DEV) run --rm --no-deps -T api sh dev/ensure-env.sh

dev: ## backend + Vite in Docker, reload on save; open http://localhost:5173 (Ctrl+C stops)
	$(DEV) up --build

dev-down: ## stop the dev containers (data in ./data-dev stays)
	$(DEV) down

dev-logs:
	$(DEV) logs -f --tail=100

dev-test: ## backend tests in the dev container
	$(DEV) run --rm --no-deps -T api sh -c "cd core && python -m pytest -q"

dev-build: ## frontend: themes check + typecheck + build, in the dev container
	$(DEV) run --rm --no-deps -T web sh dev/web.sh npm run build

dev-migration: ## new migration in the dev container: make dev-migration m="add thing"
	@test -n "$(m)" || (echo 'usage: make dev-migration m="opis"' && exit 1)
	$(DEV) run --rm --no-deps -T api make migration m="$(m)"

dev-sh: ## a shell in the dev container (python, node, git, make)
	$(DEV) run --rm --no-deps api bash

# --- development without Docker: Linux, a Mac with Python/Node, or inside the VS Code dev container --------------
dev-local-init: ## one-time on a host: .venv (Python >= 3.12) + npm packages (not needed in the dev container)
	@python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 12) else "Python >= 3.12 needed")'
	@test -d .venv || python3 -m venv .venv
	.venv/bin/pip install -q --upgrade pip
	.venv/bin/pip install -q -r core/requirements-dev.txt
	sh dev/web.sh true

dev-local: ## the same as `make dev`, without Docker: backend :8000 + Vite :5173 (Ctrl+C stops both)
	@trap 'kill 0' INT TERM; \
	PYTHON="$(PYTHON)" sh dev/api.sh & \
	sh dev/web.sh & \
	wait
