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

This repository is a release-one candidate that has not been released, not a production
application. Phases 0–6 are built, with Phases 1 and 2 accepted by the owner (2026-10-09): invite-only
identity and tenancy, typed canon committed atomically in PostgreSQL behind a review gate, history
and a verified archive restore, assistant-led creation, story planning, text intake and story rules,
story time, what-ifs and point-of-view knowledge, explainable scene context, the scene studio with
background jobs, and reading scenes back into reviewed proposals. Phase 7's code-side release work
is done. Later batches added pictures (prepared, described and sent to models that can see them),
richer context, checks kept as runs and worked out only where something changed, planning help,
voice and scheduled audits, connected assistants over MCP, and a model on the author's own
computer. Still open are owner items (a licence, the first hosted CI run, a restore drill on a clean
host) and any measurement of a real model's output. The status of each capability is in
[the backlog](plan/10-backlog.md); see also [the implementation plan](plan/README.md).

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
install it with `uv sync --locked --extra embeddings` when you need search by meaning, seeding
vectors (`uv run --locked python -m backend.devtools.seed --vectors`), or real vector retrieval;
`make reindex` builds a search index with the configured model. All automated tests use
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

- PostgreSQL uses Alembic (`migrations/versions/`; the head is the newest revision there, and
  `make migrate-verify` says whether a store is at it). Keep revision ids at 32 characters or fewer. Author a new revision with `uv run alembic revision -m "..."`,
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

On a hosted site (or any install whose server is not on your computer), run the connector next to
Ollama, LM Studio or a llama.cpp server instead: `python -m connector` (standard library only) asks
the site for a short code, a workspace owner confirms it under **AI assistant**, and the assistant's
calls then go to your computer over an outbound HTTPS connection, with no port opened. See ADR-025
and [the runbook](plan/runbooks/local-connector.md). Neither local route has yet been tried against
a running local server.

For coding-agent conventions and project boundaries, read [AGENTS.md](AGENTS.md). For architecture, scope, roadmap, and open decisions, start with [plan/README.md](plan/README.md).

## Using the story from other assistants (MCP)

Assistants that speak the Model Context Protocol (Claude Desktop, Claude Code, Cursor, ChatGPT and
others) can read a story through `/mcp`. Under **People → Other assistants**, make an access key
for one story (or, hosted, every story you can read in the workspace); it is shown once with a
block of settings to paste into the assistant:

```json
{
  "mcpServers": {
    "novel-engine": {
      "type": "http",
      "url": "https://<your site>/mcp",
      "headers": { "Authorization": "Bearer nvlpat_…" }
    }
  }
}
```

The assistant can search the story bible, read an entry (or only what one character knows), the
plan, a scene's plan card and text, the timeline and a record's history, and see what waits for
review. A key reads only, unless you also let it suggest changes: then its suggestions go to
Review and change nothing until you keep them. No tool commits, approves or changes rules. Keys
last 90 days unless chosen (never over a year), are stored only as a digest, are checked on every
request, stop working when withdrawn or when their holder loses access, and count use by kind,
never content. It speaks MCP 2026-07-28 and the earlier 2025 versions over Streamable HTTP. On a
local install keep the API on 127.0.0.1. See ADR-024 and
[the runbook](plan/runbooks/connected-assistants.md).

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
- `make retention` (`RETENTION_ARGS='--days 30 --dry-run'`) purges expired sessions, login
  attempts, invitations and stale rate-limit hits, assistant leftovers after N days (finished jobs
  with no waiting draft, unused context records, notes on replaced versions), and stored picture
  files nothing refers to. It never removes a text, a version, a waiting draft, an author's decision
  or the story itself.
- `make rekey` re-locks stored provider keys under a new credential key; `make media` (picture
  upkeep), `make reindex*` (the search index), `make read-models*` (the story's ready views) and
  `make eval` (evaluation sets, no model) are described in `make help` and the runbooks in
  `plan/runbooks/`.
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
  in `deploy/hosted/README.md`; example scheduling (`deploy/hosted/systemd/`, `prune-backups.sh`) and
  copying backups off the host (`copy-off.sh`) are there too.
- Operational metrics are available in Prometheus text format at `/health/metrics` when
  `METRICS_ENABLED=true` (counts and timings only; set `METRICS_TOKEN` outside local development).

Privacy: the only outbound connections are the configured OIDC provider, the AI provider (none
when the model runs on your own computer), and opt-in Sentry. The connector on an author's computer
connects out to the site; the site never connects to it. Sentry is disabled unless a DSN is set and scrubs
request data, headers, and PII (`backend/core/observability.py`). Provider credentials are
stored envelope-encrypted and never returned by the API; logs redact `sk-`/`sk-or-` keys.

## Current limitations

- No real provider has been measured. Every assistant path (creation, planning, reading texts,
  drafting scenes, checking and auditing them, describing pictures, correction plans) is tested
  against a scripted model and a local stand-in; the quality of what a real model drafts, reads or
  notices is unmeasured.
- Media: pictures (PNG, JPEG, WebP) are prepared, can be described by a model that sees pictures,
  and are sent to one when the author chooses; sound and film are not handled, and there is no
  hosted malware scanning.
- Search by meaning needs the optional embeddings extra, which the hosted image does not install, so
  it is off there; EmbeddingGemma 2 has never been loaded or run here.
- Drafts are kept or discarded whole: no streaming, no editing inside a draft, no merging.
- The checks without a model find only what their words and records show (see each item's limits in
  `plan/10-backlog.md`); they warn and never block a draft.
- No open-source licence has been chosen yet; see `plan/phase-7-todo.md` for release items that are
  the owner's.
- The generic RAG answer endpoint is incomplete (`/api/v1/rag/query` returns 501), and the root
  `architecture.md` records the original concept; `plan/` is the maintained plan.
