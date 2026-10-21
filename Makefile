PYTHON ?= python3
.PHONY: init up down logs build up-tunnel up-quick quick-url test migration new-module

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
	cd core && JARVIS_SECRET_KEY=$$(printf 'x%.0s' $$(seq 40)) JARVIS_DATABASE_URL=sqlite:///../.mig.db \
	  JARVIS_MODULES_DIR=$$PWD/../modules PYTHONPATH=$$PWD:$$PWD/.. \
	  sh -c 'alembic upgrade head && alembic revision --autogenerate -m "$(m)"'
	@rm -f .mig.db
