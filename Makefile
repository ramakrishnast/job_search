# JobPilot — Docker-first workflows. Everything runs in the container; only Docker is required.
# Settings (port, API key, schedule) live in .env — see .env.example.
SHELL := /bin/bash
COMPOSE ?= docker compose
PORT ?= $(shell grep -E '^PORT=' .env 2>/dev/null | cut -d= -f2 || echo 8000)
URL = http://localhost:$(or $(PORT),8000)

.PHONY: help up demo down restart logs ps discover verify-companies backup shell build \
        dev-setup dev-test dev-lint dev-run

help:  ## show targets
	@grep -E '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-18s\033[0m %s\n", $$1, $$2}'

.env:
	cp .env.example .env
	@echo ">> Created .env — set OPENAI_API_KEY (and PORT if 8000 is taken), then re-run."

data:
	mkdir -p data

build: .env  ## build the Docker image
	$(COMPOSE) build

up: .env data  ## build + start in the background
	$(COMPOSE) up -d --build
	@echo ">> JobPilot → $(URL)   (logs: make logs)"

demo: .env data  ## build, run discovery once against the seed companies, then start
	$(COMPOSE) build
	$(COMPOSE) run --rm jobpilot python -m app.cli discover --trigger demo
	$(COMPOSE) up -d
	@echo ">> JobPilot → $(URL)"

down:  ## stop and remove the container (data in ./data is kept)
	$(COMPOSE) down

restart: .env  ## restart after editing .env (e.g. new API key or port)
	$(COMPOSE) up -d --force-recreate
	@echo ">> JobPilot → $(URL)"

logs:  ## follow logs
	$(COMPOSE) logs -f --tail=100

ps:  ## container status / health
	$(COMPOSE) ps

discover: .env  ## run one discovery pass now (inside the container)
	$(COMPOSE) run --rm jobpilot python -m app.cli discover

verify-companies: .env  ## check every configured board slug against the public ATS APIs
	$(COMPOSE) run --rm jobpilot python -m app.cli verify-companies

backup:  ## consistent online SQLite backup → data/backups (keeps 14)
	$(COMPOSE) exec jobpilot python -m app.cli backup

shell:  ## shell inside the running container
	$(COMPOSE) exec jobpilot bash

# ---- development (optional, needs Python 3.11+ and Node 20+ on the host) ----
VENV ?= .venv
dev-setup:  ## [dev] create venv + install deps
	python3 -m venv $(VENV) && $(VENV)/bin/pip install -q -r backend/requirements.txt
	cd frontend && npm ci --no-audit --no-fund

dev-test:  ## [dev] backend tests
	cd backend && ../$(VENV)/bin/python -m pytest

dev-lint:  ## [dev] ruff + TypeScript typecheck
	cd backend && ../$(VENV)/bin/ruff check app tests && ../$(VENV)/bin/ruff format --check app tests
	cd frontend && npm run typecheck

dev-run:  ## [dev] backend with reload + Vite dev server on :5173
	@trap 'kill 0' EXIT; \
	(cd backend && ../$(VENV)/bin/python -m uvicorn app.main:app --reload --port 8000) & \
	(cd frontend && npm run dev) & wait
