# Novel Engine

Novel Engine is an open-source AI-assisted fiction system designed to preserve continuity across
world facts, character beliefs, timelines, story plans, and generated scenes. It supports a local
self-hosted profile and is being built for a free, invite-only multi-tenant deployment within
`apps.jaketalling.com`.

The project combines:

- PostgreSQL for the immutable journal and normalized/versioned canonical story state
- Neo4j, pgvector, search, and summaries as rebuildable projections
- semantic retrieval for prose, summaries, and stylistic memory
- structured model workflows for authoring and extraction
- explicit provenance, temporal validity, and branch-aware state
- a review boundary between AI proposals and accepted canon
- user-controlled AI autonomy, local models or the user's own key for a major AI provider, and tiered consistency checking

This repository is a stabilized prototype, not a production application. Phase 0 is complete;
Phase 1 identity, invitation-only tenancy, scoped persistence, encrypted OpenRouter BYOK, and
distribution foundations are implemented with external rehearsals pending. Phase 2 (trustworthy
canon) is complete and accepted by the owner (2026-10-09): typed canon and immutable project
schemas, atomic PostgreSQL commits behind a review gate with per-operation decisions, evidence
provenance and history, compensating reverts, media identity, a verified archive restore, a
replayable Neo4j projection, and an authoring and review UI. Story rules, manuscript import,
temporal branching, point-of-view filtering, prose planning/generation, and production operations
remain planned. See [the implementation plan](plan/README.md) and
[the Phase 2 evidence](plan/phase-2-evidence.md) for authoritative status.

## Quick start

Prerequisites:

- Python 3.13
- Node.js 24 LTS with pnpm 11
- [uv](https://docs.astral.sh/uv/) for Python environments and locked dependencies
- Docker with Compose
- an OpenRouter/OpenAI-compatible API key only for live model workflows

```bash
cp .env.example .env
make install-dev
make infra-up
make migrate
make seed
make api
```

`make infra-up` starts PostgreSQL/pgvector and Neo4j and waits for both to be healthy. `make migrate` applies explicit Alembic and Neo4j migrations; application startup verifies migration state but never mutates schemas. `make seed` applies the deterministic reference worlds (idempotent): the legacy graph manifest, and the canonical reference world, which is committed through the same mutation path product writes use. Vector documents are indexed only when embeddings are available. `make reset-dev` clears development stores first and refuses to run in production or against non-local stores. `uv` creates and manages `.venv` automatically. On Windows, run the commands shown in `Makefile` directly if `make` is unavailable; no manual virtual-environment activation is required.

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

- PostgreSQL uses Alembic (`migrations/versions/`); the current head is
  `0022_archive_imports`. Keep revision ids at 32 characters or fewer. Author a new revision with `uv run alembic revision -m "..."`,
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

The frontend is TypeScript and consumes a client generated from the committed OpenAPI
snapshot (`pnpm --dir frontend run gen:api`, output committed under `frontend/src/api/`). It uses
React Router and TanStack Query with workspace/project-scoped cache keys. Validate it with
`make frontend-api-check frontend-lint frontend-typecheck frontend-test frontend-build`; the
generated client is excluded from lint, while `frontend-api-check` detects snapshot drift.
`make e2e` runs the Playwright browser tests against a real API on a disposable PostgreSQL
database (start the test profile first; install the browser once with
`make frontend-e2e-install`).

The API is served at `http://localhost:8000`; canonical routes are under `/api/v1` and interactive API documentation is at `http://localhost:8000/docs`. Neo4j Browser is at `http://localhost:7474` using the credentials configured in `.env`.

## Development

```bash
make help
make lock-check
make lint
make test
make test-integration
make frontend-build
make e2e
```

`make test` runs the hermetic unit/application/contract suite with no services, network, API keys, model downloads, or persistent writes. `make test-integration` targets disposable PostgreSQL and Neo4j services (start the test Neo4j with `docker compose --profile test up -d --wait neo4j-test`). Tests that spend model tokens are marked `llm` and excluded from both commands.

Logs are emitted as single-line JSON with a correlation ID and are redacted by default: API keys, bearer tokens, DSN passwords, and secret-named fields are masked, and adapters log only timings, counts, and error types rather than manuscript or prompt content. Set `LOG_LEVEL=DEBUG` in `.env` for development diagnostics; debug logs may include synthetic entity IDs but still never include secrets or manuscript text.

Error monitoring is opt-in. The server reports to `SENTRY_DSN_BACKEND` and hands `SENTRY_DSN_WEB` to the page it serves, so the browser needs no build-time setting; an empty value switches that side off. Events are scrubbed on both sides before they leave: no request bodies, query strings, cookies, headers that identify, user details, breadcrumbs or local variables. What remains is the stack, the route, the release, and internal ids. An exception's own message is sent, so code must never put an author's words in one. `python -m backend.core.observability` sends a test event.

Browser tests come in two sets: `playwright.config.ts` runs with no model at all, and
`playwright.model.config.ts` runs the tests named `*.model.spec.ts` against a local stand-in model
with set answers (`scripts/e2e/fake_model.py`), so drafting and checking are tested end to end
without a provider. `make e2e` runs both.

### Using a model on your own computer

A self-hosted install can use any OpenAI-compatible server instead of a provider, so nothing about
the story leaves the machine. Ollama (0.5 or later) and LM Studio both answer with the required
shape; anything else is asked for the shape in words and checked. In `.env`:

```bash
OPENAI_API_KEY=                       # not needed for a local server
AI_BASE_URL=http://localhost:11434/v1 # Ollama; LM Studio is http://localhost:1234/v1
AI_MODEL=llama3.1                     # a model you have pulled or loaded
```

From inside Docker, the host is `host.docker.internal` rather than `localhost`. Check the route
with `uv run python -m backend.devtools.diagnostics --check-ai`: it asks two tiny questions (no
story text) and says whether each worked. Smaller local models draft and read less well than large
hosted ones; the checks and the review step are the same either way.

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

- `make diagnostics` prints a redacted support bundle (versions, capability flags, the model route
  by host only; no secrets or manuscript text). `--check-ai` also checks the model answers.
- `make retention` purges expired sessions, login attempts, invitations, and stale rate-limit
  hits without touching canonical data.
- `make project` advances the Neo4j projection; `make project PROJECT_ARGS=--verify` checks that
  the journal, normalized canon, and the projection agree; `make verify` checks journal
  consistency. Neither ever changes canon.
- Canonical project export/restore is available through `make archive` (checksummed archive v4,
  including accepted history, permitted object bytes, the author's texts and rules, every what-if
  with its own changes and scene texts, scene readings, dismissed findings and conversations with
  the assistant, but never credentials or sessions). Drafts waiting to be kept or discarded are
  working material and are left out; the archive lists what it leaves out. Version 3 archives
  still restore.
  It acts as `--actor USER_ID`, requires a project administrator in a hosted deployment, and
  writes a security-audit record. A restore is recorded in a status ledger and is only marked
  complete after it has been verified; `--imports` shows the ledger and `--remove-import` erases a
  failed one. `--upgrade-graph` files a Phase 1 graph bundle as a proposal for review rather than
  restoring it. A project archive is not a database backup: the hosted profile has
  `deploy/hosted/backup.sh` (PostgreSQL and stored objects, with checksums) and
  `deploy/hosted/restore.sh` (checks the checksums before replacing anything), with a restore drill
  in `deploy/hosted/README.md`.
- Operational metrics are available in Prometheus text format at `/health/metrics` when
  `METRICS_ENABLED=true` (counts and timings only; set `METRICS_TOKEN` outside local development).

Privacy: the only outbound connections are the configured OIDC provider, the AI provider (none
when the model runs on your own computer), and opt-in Sentry. Sentry is disabled unless a DSN is set and scrubs
request data, headers, and PII (`backend/core/observability.py`). Provider credentials are
stored envelope-encrypted and never returned by the API; logs redact `sk-`/`sk-or-` keys.

## Current limitations

- No real provider has been measured. Every assistant path (creation, planning, reading texts,
  drafting scenes, checking them, filling in a plan card) is tested against a scripted model and a
  local stand-in; the quality of what a real model drafts or reads is unmeasured.
- Media: pictures are stored, linked and named in context, but no image, audio or video is processed
  or sent to a model.
- Drafts are kept or discarded whole: no streaming, no editing inside a draft, no merging.
- Consistency profiles, finding suppressions and impact preview are not built; checks are worked out
  each time.
- No open-source licence has been chosen yet; see `plan/phase-7-todo.md` for release items that are
  the owner's.
- The generic RAG answer endpoint is incomplete (`/api/v1/rag/query` returns 501), and the root
  `architecture.md` records the original concept; `plan/` is the maintained plan.
