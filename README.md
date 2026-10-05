# Novel Engine

Novel Engine is a planned hosted AI-assisted fiction SaaS designed to preserve continuity across world facts, character beliefs, timelines, story plans, and generated scenes.

The project combines:

- Neo4j for canonical, queryable story state
- PostgreSQL with pgvector for operational state and future vector retrieval
- semantic retrieval for prose, summaries, and stylistic memory
- structured model workflows for authoring and extraction
- explicit provenance, temporal validity, and branch-aware state
- a review boundary between AI proposals and accepted canon
- user-controlled AI autonomy, platform credits or OpenRouter BYOK, and tiered consistency checking

This repository is currently a local prototype; SaaS identity, PostgreSQL operations, billing, and hosted deployment are planned but not implemented. Graph CRUD, tagged-entity context retrieval, and part of the character creation workflow exist; the frontend and most planning/generation workflows are not yet implemented. See [the implementation plan](plan/README.md) for the accepted target architecture and delivery sequence.

## Quick start

Prerequisites:

- Python 3.13
- Node.js 24 LTS with pnpm 11
- [uv](https://docs.astral.sh/uv/) for Python environments and locked dependencies
- Docker with Compose
- an OpenAI API key for live AI workflows

```bash
cp .env.example .env
make install-dev
make infra-up
make migrate
make api
```

`make infra-up` starts PostgreSQL/pgvector and Neo4j and waits for both to be healthy. `make migrate` applies explicit Alembic and Neo4j migrations; application startup verifies migration state but never mutates schemas. `uv` creates and manages `.venv` automatically. On Windows, run the commands shown in `Makefile` directly if `make` is unavailable; no manual virtual-environment activation is required.

Start the frontend separately:

```bash
make frontend-install
pnpm --dir frontend run dev
```

The API is served at `http://localhost:8000`; canonical routes are under `/api/v1` and interactive API documentation is at `http://localhost:8000/docs`. Neo4j Browser is at `http://localhost:7474` using the credentials configured in `.env`.

## Development

```bash
make help
make lock-check
make lint
make test
make test-integration
make frontend-build
```

`make test` runs the hermetic unit/application/contract suite with no services, network, API keys, model downloads, or persistent writes. `make test-integration` targets disposable PostgreSQL and Neo4j services (start the test Neo4j with `docker compose --profile test up -d --wait neo4j-test`). Tests that spend model tokens are marked `llm` and excluded from both commands.

For coding-agent conventions and project boundaries, read [AGENTS.md](AGENTS.md). For architecture, scope, roadmap, and open decisions, start with [plan/README.md](plan/README.md).

## Current limitations

- The React application is still the default Vite scaffold.
- Canonical API routes live under `/api/v1` with stable DTOs and an `application/problem+json` error contract; the original prototype routes remain at `/api` as deprecated aliases pending migration.
- Character and schema routes use the injected container; graph and RAG routes still use legacy module singletons pending 0G.
- The world workflow and generic RAG answer endpoint are incomplete (`/api/v1/rag/query` returns 501).
- The architecture document at the repository root records the original concept; `plan/` is the maintained implementation plan.
