UV_CACHE_DIR ?= $(CURDIR)/.cache/uv
PNPM_STORE_DIR ?= $(CURDIR)/.cache/pnpm-store
export UV_CACHE_DIR

TYPE_CHECK_TARGETS = backend/graph/resolver.py backend/graph/summarizer.py backend/graph/policies.py backend/graph/schema.py

.PHONY: help install install-dev lock lock-check infra-up infra-down migrate migrate-verify seed reset-dev api test test-integration integration-test coverage-unit coverage-integration typecheck import-check lint format-check frontend-install frontend-lint frontend-build check

help:
	@echo "install             Install runtime Python dependencies"
	@echo "install-dev         Install runtime and development Python dependencies"
	@echo "lock                Refresh the Python dependency lock"
	@echo "lock-check          Verify the Python lock matches project metadata"
	@echo "infra-up            Start PostgreSQL/pgvector and Neo4j"
	@echo "infra-down          Stop infrastructure services"
	@echo "migrate             Apply PostgreSQL and Neo4j migrations"
	@echo "migrate-verify      Verify migration state without mutating stores"
	@echo "seed                Seed the deterministic reference world"
	@echo "reset-dev           Reset development stores (guarded; refuses production)"
	@echo "api                 Start the FastAPI development server"
	@echo "test                Run hermetic tests (unit, application, contract)"
	@echo "test-integration    Run tests marked integration against disposable services"
	@echo "coverage-unit       Hermetic test coverage report"
	@echo "coverage-integration Integration test coverage report"
	@echo "typecheck           Type-check the initial hermetic domain scope"
	@echo "import-check        Verify application imports without external work"
	@echo "lint                Run Python lint checks"
	@echo "format-check        Check Python formatting"
	@echo "frontend-install    Install frontend dependencies"
	@echo "frontend-lint       Lint the frontend"
	@echo "frontend-build      Build the frontend"
	@echo "check               Run local static checks and hermetic tests"

install:
	uv sync --no-dev --locked

install-dev:
	uv sync --locked

lock:
	uv lock

lock-check:
	uv lock --check

infra-up:
	docker compose up -d --wait

infra-down:
	docker compose down

migrate:
	uv run --locked python -m backend.migrate

migrate-verify:
	uv run --locked python -m backend.migrate --verify

seed:
	uv run --locked python -m backend.devtools.seed

reset-dev:
	uv run --locked python -m backend.devtools.reset --force

api:
	uv run --locked uvicorn backend.main:app --reload

test:
	uv run --locked pytest -m "not integration and not llm"

test-integration:
	uv run --locked pytest -m integration

integration-test: test-integration

coverage-unit:
	COVERAGE_FILE=.coverage.unit uv run --locked pytest -m "not integration and not llm" --cov=backend --cov-report=term-missing:skip-covered

coverage-integration:
	COVERAGE_FILE=.coverage.integration uv run --locked pytest -m integration --cov=backend --cov-report=term-missing:skip-covered

typecheck:
	uv run --locked mypy $(TYPE_CHECK_TARGETS)

import-check:
	uv run --locked python -c "import backend.main; print('import ok')"

lint:
	uv run --locked ruff check backend tests

format-check:
	uv run --locked ruff format --check backend tests

frontend-install:
	pnpm --dir frontend install --frozen-lockfile --store-dir "$(PNPM_STORE_DIR)"

frontend-lint:
	pnpm --dir frontend run lint

frontend-build:
	pnpm --dir frontend run build

check: lock-check lint format-check typecheck test frontend-lint frontend-build
