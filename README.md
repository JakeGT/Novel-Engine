# Novel Engine

Novel Engine is a planned hosted AI-assisted fiction SaaS designed to preserve continuity across world facts, character beliefs, timelines, story plans, and generated scenes.

The project combines:

- Neo4j for canonical, queryable story state
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
make api
```

`uv` creates and manages `.venv` automatically. On Windows, run the commands shown in `Makefile` directly if `make` is unavailable; no manual virtual-environment activation is required.

Start the frontend separately:

```bash
make frontend-install
pnpm --dir frontend run dev
```

The API is served at `http://localhost:8000`; interactive API documentation is at `http://localhost:8000/docs`. Neo4j Browser is at `http://localhost:7474` using the credentials configured in `.env`.

## Development

```bash
make help
make lock-check
make lint
make test
make frontend-build
```

Tests that require live infrastructure or spend model tokens must be explicitly marked and are excluded from the default unit-test command.

For coding-agent conventions and project boundaries, read [AGENTS.md](AGENTS.md). For architecture, scope, roadmap, and open decisions, start with [plan/README.md](plan/README.md).

## Current limitations

- The React application is still the default Vite scaffold.
- Neo4j and ChromaDB clients are created globally and need dependency-injection boundaries.
- Existing root-level tests are live-server scripts rather than isolated automated tests.
- The world workflow and generic RAG answer endpoint are incomplete.
- The architecture document at the repository root records the original concept; `plan/` is the maintained implementation plan.
