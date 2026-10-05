.PHONY: help install install-dev infra-up infra-down api test lint format-check frontend-install frontend-lint frontend-build check

help:
	@echo "install          Install runtime Python dependencies"
	@echo "install-dev      Install runtime and development Python dependencies"
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
	python -m pip install -r requirements.txt

install-dev:
	python -m pip install -r requirements-dev.txt

infra-up:
	docker compose up -d neo4j

infra-down:
	docker compose down

api:
	python -m uvicorn backend.main:app --reload

test:
	python -m pytest -m "not integration and not llm"

lint:
	python -m ruff check backend tests 2>/dev/null || python -m ruff check backend

format-check:
	python -m ruff format --check backend tests 2>/dev/null || python -m ruff format --check backend

frontend-install:
	npm --prefix frontend install

frontend-lint:
	npm --prefix frontend run lint

frontend-build:
	npm --prefix frontend run build

check: lint format-check test frontend-lint frontend-build
