PYTHON ?= python3
.PHONY: encryption-key init up down logs build up-tunnel up-quick quick-url test migration new-module backup restore

init: ## create .env with fresh secrets (never overwrites an existing .env)
	@test ! -f .env || (echo ".env already exists, not touching it" && exit 1)
	@mkdir -p data
	@cp .env.example .env
	@$(PYTHON) scripts/gen_secrets.py > .secrets.tmp 2>/dev/null || \
	  docker run --rm -v "$$PWD/scripts:/s:ro" python:3.12-slim sh -c "pip install -q cryptography >/dev/null 2>&1 && python /s/gen_secrets.py" > .secrets.tmp
	@while IFS='=' read -r k v; do sed -i "s|^$$k=.*|$$k=$$v|" .env; done < .secrets.tmp
	@rm .secrets.tmp
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
	cd core && python -m pytest -q

migration: ## new migration from model changes: make migration m="add thing"
	@rm -f .mig.db
	cd core && JARVIS_LOG_DIR= JARVIS_SECRET_KEY=$$(printf 'x%.0s' $$(seq 40)) JARVIS_DATABASE_URL=sqlite:///../.mig.db \
	  JARVIS_MODULES_DIR=$$PWD/../modules PYTHONPATH=$$PWD:$$PWD/.. \
	  sh -c 'alembic upgrade head && alembic revision --autogenerate -m "$(m)"'
	@rm -f .mig.db

new-module: ## scaffold a module: make new-module name=pogoda
	@test -n "$(name)" || (echo "usage: make new-module name=<id>" && exit 1)
	python3 scripts/new_module.py $(name)

encryption-key: ## add JARVIS_ENCRYPTION_KEY to an existing .env (needed by the mail module)
	@test -f .env || (echo "no .env: run make init" && exit 1)
	@grep -q '^JARVIS_ENCRYPTION_KEY=.\+' .env && echo "already set" || ( \
	  KEY=$$($(PYTHON) scripts/gen_secrets.py | grep '^JARVIS_ENCRYPTION_KEY=' | cut -d= -f2-) ; \
	  test -n "$$KEY" || (echo "needs python3 with 'cryptography' installed" && exit 1) ; \
	  sed -i '/^JARVIS_ENCRYPTION_KEY=/d' .env && echo "JARVIS_ENCRYPTION_KEY=$$KEY" >> .env && echo "key added to .env" )

backup: ## make a database backup now (into data/backups, same as the button in Ustawienia)
	docker compose exec core python -c "from app import backup; print(backup.create()['name'])"

restore: ## restore a backup: make restore f=data/backups/jarvis-....db.gz (stops core, keeps the old db aside)
	@test -n "$(f)" || (echo "usage: make restore f=data/backups/<file>.db.gz" && exit 1)
	docker compose stop core
	$(PYTHON) scripts/restore_backup.py "$(f)" --db data/jarvis.db
	docker compose start core
