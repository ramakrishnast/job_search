# JobPilot — one-command local workflows.
SHELL := /bin/bash
VENV ?= .venv
PY := $(VENV)/bin/python
PIP := $(VENV)/bin/pip
HOST ?= 127.0.0.1
PORT ?= 8000

.PHONY: help setup install build run dev demo discover verify-companies test lint fmt backup docker-up docker-down docker-demo clean

help:  ## show targets
	@grep -E '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-18s\033[0m %s\n", $$1, $$2}'

$(VENV)/.installed: backend/requirements.txt
	python3 -m venv $(VENV)
	$(PIP) install -q --upgrade pip
	$(PIP) install -q -r backend/requirements.txt
	@touch $@

frontend/node_modules/.installed: frontend/package-lock.json
	cd frontend && npm ci --no-audit --no-fund
	@touch $@

install: $(VENV)/.installed frontend/node_modules/.installed  ## install backend + frontend deps

.env:
	cp .env.example .env
	@echo "Created .env — add your ANTHROPIC_API_KEY to enable Claude features."

setup: install .env  ## first-time setup

build: frontend/node_modules/.installed  ## build the frontend into frontend/dist
	cd frontend && npm run build

run: setup build  ## build and start the app on http://$(HOST):$(PORT)
	cd backend && ../$(PY) -m uvicorn app.main:app --host $(HOST) --port $(PORT) --workers 1 --proxy-headers

dev: setup  ## backend with reload + Vite dev server (http://localhost:5173)
	@trap 'kill 0' EXIT; \
	(cd backend && ../$(PY) -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8000) & \
	(cd frontend && npm run dev) & wait

discover: setup  ## run discovery once against all enabled companies
	cd backend && ../$(PY) -m app.cli discover

demo: setup build  ## run discovery once against the seed companies, then start the app with real postings
	cd backend && ../$(PY) -m app.cli discover --trigger demo
	cd backend && DISCOVERY_ON_STARTUP_IF_STALE_HOURS=0 ../$(PY) -m uvicorn app.main:app --host $(HOST) --port $(PORT) --workers 1

verify-companies: setup  ## check every configured board slug against the public ATS APIs
	cd backend && ../$(PY) -m app.cli verify-companies

test: $(VENV)/.installed  ## run backend tests
	cd backend && ../$(PY) -m pytest

lint: $(VENV)/.installed frontend/node_modules/.installed  ## lint + typecheck
	cd backend && ../$(VENV)/bin/ruff check app tests && ../$(VENV)/bin/ruff format --check app tests
	cd frontend && npm run typecheck

fmt: $(VENV)/.installed
	cd backend && ../$(VENV)/bin/ruff format app tests && ../$(VENV)/bin/ruff check --fix app tests

backup: $(VENV)/.installed  ## consistent online backup of the SQLite DB into data/backups
	cd backend && ../$(PY) -m app.cli backup

docker-up: .env  ## build + start in Docker (http://127.0.0.1:8000)
	docker compose up -d --build
	@echo "JobPilot → http://127.0.0.1:8000  (logs: docker compose logs -f)"

docker-demo: .env  ## Docker: run discovery once, then start
	docker compose build
	docker compose run --rm jobpilot python -m app.cli discover --trigger demo
	docker compose up -d

docker-down:  ## stop Docker container
	docker compose down

clean:  ## remove build artifacts (keeps your data/)
	rm -rf frontend/dist backend/.pytest_cache backend/.ruff_cache
