UV_CACHE_DIR ?= $(CURDIR)/.cache/uv
PNPM_STORE_DIR ?= $(CURDIR)/.cache/pnpm-store
export UV_CACHE_DIR
# pnpm run/exec verify dependencies against the configured store; keep it the one frontend-install used.
export pnpm_config_store_dir = $(PNPM_STORE_DIR)

TYPE_CHECK_TARGETS = backend

.PHONY: help install install-dev lock lock-check infra-up infra-down migrate migrate-verify verify seed reset-dev diagnostics retention reconcile project watch archive audit api test test-integration integration-test coverage-unit coverage-integration typecheck import-check lint format-check secret-scan frontend-install frontend-api-check frontend-lint frontend-typecheck frontend-test frontend-build frontend-e2e-install e2e check

help:
	@echo "install             Install runtime Python dependencies"
	@echo "install-dev         Install runtime and development Python dependencies"
	@echo "lock                Refresh the Python dependency lock"
	@echo "lock-check          Verify the Python lock matches project metadata"
	@echo "infra-up            Start PostgreSQL/pgvector and Neo4j"
	@echo "infra-down          Stop infrastructure services"
	@echo "migrate             Apply PostgreSQL and Neo4j migrations"
	@echo "migrate-verify      Verify migration state without mutating stores"
	@echo "verify              Verify canonical PostgreSQL consistency (heads/checksums/watermarks)"
	@echo "seed                Seed the deterministic reference world"
	@echo "reset-dev           Reset development stores (guarded; refuses production)"
	@echo "diagnostics         Print a redacted diagnostics/support bundle"
	@echo "retention           Purge expired sessions, attempts, invitations, and rate-limit hits"
	@echo "reconcile           Reconcile an interrupted canon commit by id"
	@echo "project             Project committed canon into the graph (PROJECT_ARGS=--lag|--rebuild)"
	@echo "watch               Drain the canonical outbox into the graph (WATCH_ARGS=--interval N|--rebuild)"
	@echo "archive             Export/import a canonical project archive (ARCHIVE_ARGS=--export PATH|--import PATH)"
	@echo "audit               Audit Python dependencies for known vulnerabilities"
	@echo "api                 Start the FastAPI development server"
	@echo "test                Run hermetic tests (unit, application, contract)"
	@echo "test-integration    Run tests marked integration against disposable services"
	@echo "coverage-unit       Hermetic test coverage report"
	@echo "coverage-integration Integration test coverage report"
	@echo "typecheck           Type-check the initial hermetic domain scope"
	@echo "import-check        Verify application imports without external work"
	@echo "lint                Run Python lint checks"
	@echo "format-check        Check Python formatting"
	@echo "secret-scan         Scan tracked files for credential shapes"
	@echo "frontend-install    Install frontend dependencies"
	@echo "frontend-api-check  Verify generated frontend API types match OpenAPI"
	@echo "frontend-lint       Lint the frontend"
	@echo "frontend-typecheck  Type-check the frontend"
	@echo "frontend-test       Run frontend component tests"
	@echo "frontend-e2e-install Install the Playwright browser used by make e2e"
	@echo "e2e                 Run the browser end-to-end workflow (needs infra + test profile)"
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

verify:
	uv run --locked python -m backend.devtools.verify

seed:
	uv run --locked python -m backend.devtools.seed

reset-dev:
	uv run --locked python -m backend.devtools.reset --force

diagnostics:
	uv run --locked python -m backend.devtools.diagnostics

retention:
	uv run --locked python -m backend.devtools.retention

reconcile:
	uv run --locked python -m backend.devtools.reconcile $(COMMIT)

project:
	uv run --locked python -m backend.devtools.project $(PROJECT_ARGS)

watch:
	uv run --locked python -m backend.devtools.watch $(WATCH_ARGS)

archive:
	uv run --locked python -m backend.devtools.archive $(ARCHIVE_ARGS)

audit:
	uv run --locked pip-audit

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

secret-scan:
	uv run --locked python -m scripts.ci.secret_scan

frontend-install:
	pnpm --dir frontend install --frozen-lockfile --store-dir "$(PNPM_STORE_DIR)"

frontend-api-check:
	@api_tmp=$$(mktemp); \
	trap 'rm -f "$$api_tmp"' EXIT; \
	pnpm --dir frontend exec openapi-typescript ../tests/contract/openapi_snapshot.json -o "$$api_tmp" || { \
		echo "openapi-typescript failed; run: make frontend-install"; \
		exit 1; \
	}; \
	cmp -s frontend/src/api/schema.ts "$$api_tmp" || { \
		echo "frontend/src/api/schema.ts is stale; run: pnpm --dir frontend run gen:api"; \
		diff -u frontend/src/api/schema.ts "$$api_tmp"; \
		exit 1; \
	}

frontend-lint:
	pnpm --dir frontend run lint

frontend-typecheck:
	pnpm --dir frontend run typecheck

frontend-test:
	pnpm --dir frontend run test

frontend-build:
	pnpm --dir frontend run build

frontend-e2e-install:
	pnpm --dir frontend exec playwright install chromium

# Drives the real UI against a real API on a disposable PostgreSQL database and
# the neo4j-test service (docker compose --profile test up -d --wait). Not part
# of `check`, which must stay hermetic. Set E2E_BROWSER_CHANNEL=chrome to use an
# installed Chrome instead of the Playwright-managed Chromium.
e2e:
	pnpm --dir frontend exec playwright test

check: lock-check lint format-check secret-scan typecheck test frontend-api-check frontend-lint frontend-typecheck frontend-test frontend-build
