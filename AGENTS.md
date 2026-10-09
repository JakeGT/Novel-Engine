# Novel Engine agent guide

This file is the operating contract for coding agents working in this repository. Read it before changing code. The authoritative product and implementation plans are indexed in `plan/README.md`.

## Mission

Novel Engine is an open-source fiction authoring system with self-hosted and free invite-only multi-tenant deployment profiles. The hosted instance belongs within `apps.jaketalling.com` and delegates authentication to a shared OIDC identity service while retaining its own invitations and authorization. Its defining constraint is continuity: generated prose and proposed world changes must respect objective canon, timeline state, branch state, and each point-of-view character's knowledge.

## Current maturity

The repository is a stabilized prototype, not a production application. Phase 0 is complete.
Phase 1 is implemented with explicit external/operational exit items remaining. Phase 2
(trustworthy canon) is complete and accepted by the owner (2026-10-09); the evidence is in
`plan/phase-2-evidence.md`. Still open and operational: the first hosted CI run and the clean-host backup/restore rehearsal (deferred by the owner until the
project is on a permanent host). ADR-014–016 are accepted. See `plan/phase-2-todo.md`.

- Application lifecycle, dependency container, ports, and adapters exist; imports perform no
  external work.
- Neo4j and PostgreSQL/pgvector schemas are migration-controlled with read-only verification.
- Stable `/api/v1` DTOs and an `application/problem+json` error contract exist; deprecated legacy
  `/api` aliases have been removed.
- PostgreSQL atomically owns the journal and normalized canon. Neo4j is a replayable,
  watermark-tracked projection under its own `Canon*` labels and never decides canonical commit
  success.
- Every canon write is a mutation set that passes a review gate and the authoritative validator
  inside one PostgreSQL transaction. Model, import, destructive, bulk, and revert changes need an
  explicit human approval; reviewers decide per operation, and an edit is a new operation revision.
- The guided character workflow produces review proposals only. The generic graph API (CRUD,
  tagged-entity context retrieval, the original character questionnaire) is a separate legacy
  store whose writes are restricted to local mode; the world workflow is parked and unfinished.
- The vector store is PostgreSQL/pgvector behind the vector port. Canon has no derived vector
  index yet.
- The React frontend covers entity, relationship, and character authoring, schema drafting and
  publishing, proposal review, history/provenance/revert, and read-only media panels, with
  component, accessibility, and browser end-to-end tests.
- OIDC/local identity, invite-only multi-tenancy, scoped storage, encrypted OpenRouter BYOK,
  object storage, and a verified, ledgered project archive restore exist.
- Story-rule authoring, manuscript import, extraction, story planning, long-form generation,
  temporal branching, point-of-view filtering, media ingestion, and production operations remain
  planned work.

Do not describe a planned capability as implemented. When changing a capability, update its
status in `plan/`.

## Source map

| Path | Responsibility |
| --- | --- |
| `backend/api/` | HTTP transport and request/response contracts (`v1/` canonical) |
| `backend/services/` | Application workflows: mutation review and commit, canon entities/assertions/history, archive, projection, equivalence, media, schema, tenancy |
| `backend/domain/` | Storage-neutral domain: canon records, operations, review policy, validation, naming |
| `backend/graph/` | Graph schema, Neo4j persistence, resolution, traversal, summarization, migrations |
| `backend/core/` | Configuration, database engine/session, structured logging/redaction |
| `backend/ports.py` | Application/domain port protocols (graph, vector, embedding, AI, clock, IDs, schema, migration, database, artifacts) |
| `backend/adapters/` | Concrete port adapters (Neo4j, pgvector, sentence-transformers, OpenAI, PostgreSQL, system clock/IDs, artifacts) |
| `backend/container.py` | Dependency container built from explicit settings; owns adapters and lifecycle |
| `backend/migrate.py` | `make migrate` CLI; Alembic plus Neo4j migration verification |
| `backend/seed/` | Versioned reference-world manifest and idempotent seed application |
| `backend/devtools/` | Guarded developer/operator CLIs: seed, reset, project, verify, watch, archive, diagnostics, retention |
| `migrations/` | Alembic environment and PostgreSQL/pgvector revisions |
| `scripts/manual/` | Non-hermetic manual scripts; never collected by pytest |
| `scripts/ci/` | CI guards such as the credential secret scanner |
| `frontend/` | React application; `frontend/e2e/` holds the Playwright browser tests |
| `scripts/e2e/` | Launcher that serves the API on a disposable database for browser tests |
| `tests/` | Automated tests: `unit`, `application`, `contract`, `integration`; `e2e` points to `frontend/e2e/` |
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
make frontend-api-check
make frontend-lint
make frontend-test
make frontend-build
make e2e
make project PROJECT_ARGS=--verify
```

Run the smallest relevant checks during iteration and `make check` before handing over a cross-cutting change. `make e2e` drives the real UI against a real API on a disposable database and is not part of `make check`; it needs the test profile running and a browser (`make frontend-e2e-install`, or `E2E_BROWSER_CHANNEL=chrome`). `make test` is hermetic (no services, network, API keys, model downloads, or persistent writes); mark tests that contact PostgreSQL or Neo4j with `integration` and tests that spend model tokens with `llm`. Start the disposable test Neo4j with `docker compose --profile test up -d --wait neo4j-test` before `make test-integration`.

Some restricted coding-agent seccomp sandboxes prevent Starlette/AnyIO's synchronous TestClient
portal from starting even though the same locked tests pass normally. In that environment, run
TestClient-bearing `make check`/integration gates through the approved normal execution path. Do
not change runtime dependencies to accommodate that sandbox artifact; the configured thread timeout
and faulthandler provide diagnostics if the behavior appears elsewhere.

Use `uv add`/`uv remove` for Python dependencies and `pnpm --dir frontend add`/`remove` for frontend dependencies. Do not restore requirements files or use unfrozen installs in CI. Refresh lock files in the same change as dependency metadata.

The Makefile keeps tool caches under ignored `.cache/` so sandboxed coding agents need write access only inside the repository. Override `UV_CACHE_DIR` or `PNPM_STORE_DIR` when a workstation or CI runner has a preferred cache location.

## Design rules

1. Keep transport, application workflow, domain logic, and persistence separate. Endpoints should validate and delegate; they should not assemble prompts or Cypher.
2. Treat PostgreSQL's immutable journal plus normalized/versioned canon tables as canonical story
   truth. Neo4j, pgvector, search, and summaries are derived and rebuildable projections.
3. Treat model output as an untrusted proposal. Validate identifiers, types, references, branch/timeline invariants, and permissions before persistence. Canon changes only through a mutation set, the review gate, and the commit transaction; never add a write path around them.
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
- Use migrations for durable schema or index changes. Keep Alembic revision ids at 32 characters or fewer. A new project-owned table must be added to `backend/adapters/canon_tables.py` so deletion and reset cover it.
- A field added to a typed operation must be optional and registered in the canonical omit list in `backend/domain/operations.py`, or every earlier envelope checksum changes (ADR-015).
- Keep prompts versioned and test their structured contracts separately from live quality evaluations.

Before handoff:

- Run relevant checks and report what was not runnable.
- Update API examples and plan status when behavior changes.
- Record a decision in `plan/decisions/` when a choice is difficult to reverse.
- Never commit `.env`, database files, model caches, debug responses, or copyrighted manuscript content.

## Definition of done

A feature is done only when its domain behavior, API contract, persistence behavior, failure handling, tests, and user-facing documentation agree. A successful model response alone is not completion.

<!-- CODEGRAPH_START -->
## CodeGraph

In repositories indexed by CodeGraph (a `.codegraph/` directory exists at the repo root), reach for it BEFORE grep/find or reading files when you need to understand or locate code:

- **MCP tool** (when available): `codegraph_explore` answers most code questions in one call — the relevant symbols' verbatim source plus the call paths between them, including dynamic-dispatch hops grep can't follow. Name a file or symbol in the query to read its current line-numbered source. If it's listed but deferred, load it by name via tool search.
- **Shell** (always works): `codegraph explore "<symbol names or question>"` prints the same output.

If there is no `.codegraph/` directory, skip CodeGraph entirely — indexing is the user's decision.
<!-- CODEGRAPH_END -->
