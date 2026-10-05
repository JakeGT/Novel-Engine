UV_CACHE_DIR ?= $(CURDIR)/.cache/uv
PNPM_STORE_DIR ?= $(CURDIR)/.cache/pnpm-store
export UV_CACHE_DIR

.PHONY: help install install-dev lock lock-check infra-up infra-down api test lint format-check frontend-install frontend-lint frontend-build check

help:
	@echo "install          Install runtime Python dependencies"
	@echo "install-dev      Install runtime and development Python dependencies"
	@echo "lock             Refresh the Python dependency lock"
	@echo "lock-check       Verify the Python lock matches project metadata"
	@echo "infra-up         Start Neo4j"
	@echo "infra-down       Stop Neo4j"
	@echo "api              Start the FastAPI development server"
	@echo "test             Run tests that do not require live external services"
	@echo "lint             Run Python lint checks"
	@echo "format-check     Check Python formatting"
	@echo "frontend-install Install frontend dependencies"
	@echo "frontend-lint    Lint the frontend"
	@echo "frontend-build   Build the frontend"
	@echo "check            Run all local static checks and tests"

install:
	uv sync --no-dev --locked

install-dev:
	uv sync --locked

lock:
	uv lock

lock-check:
	uv lock --check

infra-up:
	docker compose up -d neo4j

infra-down:
	docker compose down

api:
	uv run --locked uvicorn backend.main:app --reload

test:
	uv run --locked pytest -m "not integration and not llm"

lint:
	uv run --locked ruff check backend

format-check:
	uv run --locked ruff format --check backend

frontend-install:
	pnpm --dir frontend install --frozen-lockfile --store-dir "$(PNPM_STORE_DIR)"

frontend-lint:
	pnpm --dir frontend run lint

frontend-build:
	pnpm --dir frontend run build

check: lock-check lint format-check test frontend-lint frontend-build
