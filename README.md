# Novel Engine

Novel Engine is a planned open-source AI-assisted fiction system designed to preserve continuity across world facts, character beliefs, timelines, story plans, and generated scenes. It will support self-hosting and a free, invite-only multi-tenant deployment within `apps.jaketalling.com`.

The project combines:

- Neo4j for canonical, queryable story state
- PostgreSQL with pgvector for operational state and future vector retrieval
- semantic retrieval for prose, summaries, and stylistic memory
- structured model workflows for authoring and extraction
- explicit provenance, temporal validity, and branch-aware state
- a review boundary between AI proposals and accepted canon
- user-controlled AI autonomy, local models or user-supplied OpenRouter access, and tiered consistency checking

This repository is currently a stabilized local prototype; shared OIDC identity, invitations, multi-tenant authorization, and hosted deployment are planned but not implemented. PostgreSQL/pgvector foundations exist alongside graph CRUD, tagged-entity context retrieval, and part of the character creation workflow; the frontend and most planning/generation workflows are not yet implemented. See [the implementation plan](plan/README.md) for the target architecture and delivery sequence.

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
make seed
make api
```

`make infra-up` starts PostgreSQL/pgvector and Neo4j and waits for both to be healthy. `make migrate` applies explicit Alembic and Neo4j migrations; application startup verifies migration state but never mutates schemas. `make seed` applies the deterministic reference world (idempotent; graph always, vector documents only when embeddings are available). `make reset-dev` clears development stores first and refuses to run in production or against non-local stores. `uv` creates and manages `.venv` automatically. On Windows, run the commands shown in `Makefile` directly if `make` is unavailable; no manual virtual-environment activation is required.

Local semantic embeddings are optional. The base install is small and degrades gracefully;
install `sentence-transformers` with `uv sync --locked --extra embeddings` when you need
semantic search, `make seed --vectors`, or real vector retrieval. All automated tests use
deterministic fakes and run without it.

The full clean-checkout sequence used for Phase 0 verification is:

```bash
make install-dev
make frontend-install
make infra-up
make migrate
make reset-dev
make seed
make check
make integration-test
make api
```

### Migrations

- PostgreSQL uses Alembic (`migrations/versions/`); the head revision creates the
  `vector_documents` table. Author a new revision with `uv run alembic revision -m "..."`,
  implement `upgrade`/`downgrade`, and run `make migrate`.
- Neo4j uses the ordered runner in `backend/graph/migrations.py`. Add a `Migration` to
  `NEO4J_MIGRATIONS`; the runner records applied IDs and checksums and fails on drift.
- `make migrate-verify` checks both stores without mutating them; application startup and
  `/health/ready` perform the same read-only verification.
- Migrations never run implicitly inside application startup; `make migrate` is the explicit
  command.

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

Logs are emitted as single-line JSON with a correlation ID and are redacted by default: API keys, bearer tokens, DSN passwords, and secret-named fields are masked, and adapters log only timings, counts, and error types rather than manuscript or prompt content. Set `LOG_LEVEL=DEBUG` in `.env` for development diagnostics; debug logs may include synthetic entity IDs but still never include secrets or manuscript text.

Error monitoring is opt-in and disabled when `SENTRY_DSN_BACKEND` is empty. When enabled, Sentry events are scrubbed of request bodies, cookies, sensitive headers, and user PII before leaving the process. The web client reads `VITE_SENTRY_DSN_WEB` from `frontend/.env` (Vite only exposes `VITE_`-prefixed variables).

For coding-agent conventions and project boundaries, read [AGENTS.md](AGENTS.md). For architecture, scope, roadmap, and open decisions, start with [plan/README.md](plan/README.md).

## Deployment, backup, and privacy

Two deployment profiles are selected by `AUTH_MODE`:

- `local` (default) is a trusted single-user self-hosted install. It needs no portfolio
  identity service and uses an explicit local workspace.
- `hosted` delegates authentication to the shared Authentik OIDC provider. It fails closed
  unless `OIDC_ISSUER`/`OIDC_REDIRECT_URI` are exact `https://` URLs and `OIDC_SCOPES`
  includes `openid`. Session cookies are host-only, `Secure`, and `HttpOnly`.

The supported install is Docker Compose: `make infra-up` (PostgreSQL/pgvector + Neo4j,
health-gated), `make migrate`, `make seed`, then `make api`. Upgrade with
`make install-dev && make migrate`; roll back with `git checkout <previous-tag>` and
`make install-dev` (schema rollback uses the store's native restore, not downgrades). The
portfolio reverse proxy terminates TLS for `novel-engine.jaketalling.com`; the OIDC
redirect/logout URLs are the registered allow-list entries (see ADR-009).

Operational tooling:

- `make diagnostics` prints a redacted support bundle (versions, capability flags, safe host
  names; no secrets or manuscript text).
- `make retention` purges expired sessions, login attempts, invitations, and stale rate-limit
  hits without touching canonical data.
- Project export/import is available through `ProjectTransferService` (checksummed graph
  bundles, no credentials or sessions); a coordinated PostgreSQL/Neo4j/artefact backup
  procedure is documented but not yet automated.

Privacy: the only outbound connections are the configured OIDC provider, the AI provider
(OpenRouter/OpenAI), and opt-in Sentry. Sentry is disabled unless a DSN is set and scrubs
request data, headers, and PII (`backend/core/observability.py`). Provider credentials are
stored envelope-encrypted and never returned by the API; logs redact `sk-`/`sk-or-` keys.

## Current limitations

- The React application is still the default Vite scaffold.
- Canonical API routes live under `/api/v1` with stable DTOs and an `application/problem+json` error contract; the original prototype routes remain at `/api` as deprecated aliases pending migration.
- Character and schema routes use the injected container; graph and RAG routes still use legacy module singletons pending 0G.
- The world workflow and generic RAG answer endpoint are incomplete (`/api/v1/rag/query` returns 501).
- The architecture document at the repository root records the original concept; `plan/` is the maintained implementation plan.
