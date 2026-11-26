# the project venv (make dev-init) when it exists, else python3 from PATH (macOS has no `python`)
PYTHON ?= $(if $(wildcard .venv/bin/python),$(CURDIR)/.venv/bin/python,python3)
.PHONY: encryption-key init up down logs build up-tunnel up-quick quick-url test migration new-module backup restore dev-init dev

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

# --- local development without Docker (macOS or Linux): backend with auto-reload + Vite dev server -----------------
dev-init: ## one-time: .venv (Python >= 3.12), npm packages, .env.dev with dev secrets and paths in ./data-dev
	@python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 12) else "Python >= 3.12 needed (macOS: brew install python@3.12)")'
	@test -d .venv || python3 -m venv .venv
	.venv/bin/pip install -q --upgrade pip
	.venv/bin/pip install -q -r core/requirements-dev.txt
	cd frontend && npm ci --no-audit --no-fund
	@mkdir -p data-dev
	@test -f .env.dev && echo ".env.dev already exists, not touching it" || ( \
	  .venv/bin/python scripts/gen_secrets.py > .env.dev && \
	  printf '%s\n' "JARVIS_COOKIE_SECURE=false" "JARVIS_DATABASE_URL=sqlite:///$(CURDIR)/data-dev/jarvis.db" \
	    "JARVIS_MODULES_DIR=$(CURDIR)/modules" "JARVIS_LOG_DIR=$(CURDIR)/data-dev/logs" \
	    "JARVIS_BACKUP_DIR=$(CURDIR)/data-dev/backups" "JARVIS_VAPID_SUBJECT=mailto:dev@example.com" >> .env.dev && \
	  echo "Created .env.dev. Setup token for the first login:" && grep '^JARVIS_SETUP_TOKEN' .env.dev )

dev: ## backend :8000 (reloads on code changes) + frontend :5173 (hot reload); open http://localhost:5173, Ctrl+C stops both
	@test -f .env.dev || (echo "run: make dev-init" && exit 1)
	@set -a; . ./.env.dev; set +a; export PYTHONPATH="$(CURDIR)/core:$(CURDIR)"; \
	trap 'kill 0' INT TERM; \
	$(PYTHON) -m uvicorn app.main:app --port 8000 --reload --reload-dir "$(CURDIR)/core/app" --reload-dir "$(CURDIR)/modules" & \
	(cd frontend && npx vite --port 5173 --strictPort) & \
	wait
