# Novel Engine agent guide

This file is the operating contract for coding agents working in this repository. Read it before changing code. The authoritative product and implementation plans are indexed in `plan/README.md`.

## Mission

Novel Engine is an open-source fiction authoring system with self-hosted and free invite-only multi-tenant deployment profiles. The hosted instance belongs within `apps.jaketalling.com` and delegates authentication to a shared OIDC identity service while retaining its own invitations and authorization. Its defining constraint is continuity: generated prose and proposed world changes must respect objective canon, timeline state, branch state, and each point-of-view character's knowledge.

## Current maturity

The repository is a stabilized prototype, not a production application. Phase 0
(foundation) is implemented; see `plan/phase-0-evidence.md` and the dashboard in
`plan/phase-0-todo.md`.

- Application lifecycle, dependency container, ports, and adapters exist; imports perform no
  external work.
- Neo4j and PostgreSQL/pgvector schemas are migration-controlled with read-only verification.
- Stable `/api/v1` DTOs and an `application/problem+json` error contract exist; legacy `/api`
  routes remain as deprecated aliases.
- Graph CRUD, tagged-entity context retrieval, and the character questionnaire are implemented;
  the world workflow is parked and unfinished.
- The vector store is PostgreSQL/pgvector behind the vector port; the transitional Chroma
  module has been removed.
- The React frontend is still the Vite starter.
- Story planning, generation, review, temporal branching, multi-tenancy, and production
  operations are planned work.

Do not describe a planned capability as implemented. When changing a capability, update its
status in `plan/`.

## Source map

| Path | Responsibility |
| --- | --- |
| `backend/api/` | HTTP transport and request/response contracts (`v1/` canonical, legacy deprecated) |
| `backend/services/` | Application workflows such as character and world creation |
| `backend/graph/` | Graph schema, Neo4j persistence, resolution, traversal, summarization, migrations |
| `backend/core/` | Configuration, database engine/session, structured logging/redaction |
| `backend/ports.py` | Application/domain port protocols (graph, vector, embedding, AI, clock, IDs, schema, migration, database, artifacts) |
| `backend/adapters/` | Concrete port adapters (Neo4j, pgvector, sentence-transformers, OpenAI, PostgreSQL, system clock/IDs, artifacts) |
| `backend/container.py` | Dependency container built from explicit settings; owns adapters and lifecycle |
| `backend/migrate.py` | `make migrate` CLI; Alembic plus Neo4j migration verification |
| `backend/seed/` | Versioned reference-world manifest and idempotent seed application |
| `backend/devtools/` | Guarded `seed`/`reset-dev` developer CLIs |
| `migrations/` | Alembic environment and PostgreSQL/pgvector revisions |
| `scripts/manual/` | Non-hermetic manual scripts; never collected by pytest |
| `scripts/ci/` | CI guards such as the credential secret scanner |
| `frontend/` | React application |
| `tests/` | Automated tests: `unit`, `application`, `contract`, `integration`, `e2e` |
| `plan/` | Product, architecture, delivery, and decision records |
| `data/` | Generated local databases; never commit |

Root-level `test_*.py` scripts have moved to `scripts/manual/` and are no longer collected by pytest.

## Local setup

1. Copy `.env.example` to `.env` and add local credentials.
2. Install Python 3.13, Node.js 24 LTS, `uv`, and pnpm 11. The repository's `.python-version`, `.nvmrc`, and package metadata are authoritative.
3. Run `make install-dev`; `uv` creates and manages `.venv`. Local semantic embeddings are an optional extra: `uv sync --locked --extra embeddings`.
4. Run `make frontend-install` for a frozen frontend install.
5. Run `make infra-up`; it starts PostgreSQL/pgvector and Neo4j and waits for healthy.
6. Run `make migrate` to apply Alembic and Neo4j migrations.
7. Run `make seed` to load the deterministic reference world (idempotent).
8. Run `make api`.
9. In another terminal, run `pnpm --dir frontend run dev`.

Use `make reset-dev` to clear development stores before reseeding; it is fail-closed against
production and non-local stores.

Useful validation commands:

```text
make lock-check
make lint
make format-check
make secret-scan
make typecheck
make import-check
make test
make test-integration
make migrate-verify
make frontend-lint
make frontend-build
```

Run the smallest relevant checks during iteration and `make check` before handing over a cross-cutting change. `make test` is hermetic (no services, network, API keys, model downloads, or persistent writes); mark tests that contact PostgreSQL or Neo4j with `integration` and tests that spend model tokens with `llm`. Start the disposable test Neo4j with `docker compose --profile test up -d --wait neo4j-test` before `make test-integration`.

Use `uv add`/`uv remove` for Python dependencies and `pnpm --dir frontend add`/`remove` for frontend dependencies. Do not restore requirements files or use unfrozen installs in CI. Refresh lock files in the same change as dependency metadata.

The Makefile keeps tool caches under ignored `.cache/` so sandboxed coding agents need write access only inside the repository. Override `UV_CACHE_DIR` or `PNPM_STORE_DIR` when a workstation or CI runner has a preferred cache location.

## Design rules

1. Keep transport, application workflow, domain logic, and persistence separate. Endpoints should validate and delegate; they should not assemble prompts or Cypher.
2. Treat Neo4j as the source of canonical story truth. Vector indexes are derived and rebuildable.
3. Treat model output as an untrusted proposal. Validate identifiers, types, references, branch/timeline invariants, and permissions before persistence.
4. Preserve the distinction between objective facts and character beliefs. Never inject inaccessible truth into point-of-view context.
5. Every canon mutation must carry provenance. Temporal facts must identify branch and validity interval.
6. Prefer explicit typed request, response, and domain models. Do not silently register unknown schema types from ordinary API writes.
7. Keep provider-specific AI SDK objects behind adapters. Application services should depend on ports that can be faked in tests.
8. Make long-running or expensive operations observable and retryable. Record model, prompt version, latency, token usage, and outcome without logging secret story text by default.
9. Return one stable API representation for graph entities. Do not alternate between flattened Neo4j properties and nested domain properties.
10. Add a test for every repaired defect and every domain invariant.
11. Workspace and project scope are mandatory at every hosted persistence, job, cache, vector, object, export, and event boundary. Self-hosted local mode supplies explicit local scope through the same contracts.
12. User-funded AI operations enforce configured limits and record provider usage idempotently. Hosted OpenRouter credentials must remain encrypted/redacted and are accessed only through the credential port.
13. Run deterministic consistency preflight/postflight around generation. Model critics produce evidence-backed findings and never silently rewrite accepted prose or canon.
14. Treat original media as scoped evidence and multimodal embeddings/captions/transcripts as derived indexes. Explicit links and workspace/project/branch/time/POV gates outrank similarity; a provider fallback must never pretend a model saw media it did not receive.

## Change workflow

Before coding:

- Read the relevant plan documents and nearby tests.
- Inspect the current worktree and preserve unrelated user changes.
- State whether the change is prototype repair, planned implementation, or plan revision.
- Resolve or record any architectural decision that affects data compatibility.

While coding:

- Prefer a vertical slice with a visible acceptance criterion.
- Keep external services behind dependency injection.
- Avoid network calls and persistent database writes at module import time.
- Use migrations for durable schema or index changes.
- Keep prompts versioned and test their structured contracts separately from live quality evaluations.

Before handoff:

- Run relevant checks and report what was not runnable.
- Update API examples and plan status when behavior changes.
- Record a decision in `plan/decisions/` when a choice is difficult to reverse.
- Never commit `.env`, database files, model caches, debug responses, or copyrighted manuscript content.

## Definition of done

A feature is done only when its domain behavior, API contract, persistence behavior, failure handling, tests, and user-facing documentation agree. A successful model response alone is not completion.
