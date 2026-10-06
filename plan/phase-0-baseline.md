# Phase 0 baseline

- Captured: 2026-10-05
- Baseline commit: `557e97456d8d17345fd9b3e1117174b2ba911021`
- Implementation branch: `phase-0-stabilization`
- Purpose: static regression inventory before Phase 0 refactoring

This inventory records observable intent and known hazards. It is not an endorsement of current behavior. Importing the application performs external work, so the API and storage inventory was taken from source instead of importing `backend.main` or querying a developer database.

## Worktree ownership and data policy

- `backend/services/world_service.py` contained an unfinished user edit before Phase 0 began. It is parked for FND-008.25 and must not be overwritten or staged accidentally before that work is reconciled.
- `.opencode/` contains workstation-local commands/state, including local paths. It is ignored and is not project tooling.
- `data/` is ignored and currently contains local Chroma/Neo4j prototype state and downloaded Neo4j plugins. It was approximately 587 MB when this baseline was captured.
- The project owner confirmed that every current database record is prototype/development data. It may be reset without migration or preservation. This permission does not extend to any future deployed environment.
- Future development and tests must use migrations plus deterministic seeds. Production-like reset commands must refuse to run.

## HTTP surface

All routes except `/` are currently mounted under `/api`. There is no API version prefix, authentication, tenant/project scope, pagination, idempotency, or stable error envelope.

| Method | Path | Current intent or result |
| --- | --- | --- |
| GET | `/` | Welcome response |
| GET, POST | `/api/schema/nodes/` | List or add process-global JSON custom node types |
| DELETE | `/api/schema/nodes/{node_type}` | Remove a custom node type |
| GET, POST | `/api/schema/edges/` | List or add process-global JSON custom edge types |
| DELETE | `/api/schema/edges/{edge_type}` | Remove a custom edge type |
| POST | `/api/graph/nodes/` | Create/upsert a Neo4j entity and generate an embedding |
| GET, PUT | `/api/graph/nodes/{node_id}` | Read or update a node |
| GET | `/api/graph/nodes/{node_id}/edges` | Read incoming and outgoing edges |
| GET | `/api/graph/nodes/search/` | Name autocomplete; currently shadowed by the earlier dynamic route |
| GET | `/api/graph/nodes/entities/` | List entities by type; currently shadowed by the earlier dynamic route |
| GET, POST | `/api/graph/edges/` | Find or create a directed edge |
| DELETE | `/api/graph/edges/{edge_id}` | Delete by Neo4j internal relationship ID |
| POST | `/api/rag/query` | Chroma query plus placeholder graph/answer strings |
| POST | `/api/rag/context` | Resolve entity tags, optionally search vectors, traverse, and render context |
| POST | `/api/characters/init` | Create a minimal character anchor |
| POST | `/api/characters/generate-questions` | Generate a structured questionnaire through a live model or debug cache |
| POST | `/api/characters/process-answers` | Generate and immediately write proposed nodes and edges |

The static `/search/` and `/entities/` declarations occur after `/{node_id}` and are therefore a known route-specificity regression target. The manual scripts also call node-delete routes that do not exist.

## Graph and schema surface

Neo4j currently uses a generic `Entity` label with a unique `id` constraint and adds one domain label per node. Code declares these domain labels:

`Character`, `Location`, `Object`, `Faction`, `Concept`, `Event`, `Scene`, `Action`, `Flashback`, `Interaction`, `Story`, `Act`, `PlotArc`, `Beat`, `Theme`, `Goal`, `Conflict`, `Secret`, `WorldState`, `System`, `Pressure`, `Resource`, `Constraint`, `Law`, `Appearance`, `PersonalityProfile`, `State`, and `Belief`.

The world service also tries to use `World`, which is not declared. The JSON extension registry is `backend/config/custom_schema.json`; it contains empty node and edge lists at baseline and writes unordered sets back to the tracked file.

Code declares these relationship types:

`LOCATED_AT`, `PART_OF`, `OWNS`, `MEMBER_OF`, `OCCURRED_AT`, `CONTAINS`, `HAS_APPEARANCE`, `HAS_PERSONALITY`, `CURRENT_STATE`, `HAS_GOAL`, `KNOWS`, `LIKES`, `HATES`, `RELATED_TO`, `PARTICIPATED_IN`, `BELIEVES`, `KNOWS_SECRET`, `CAUSED`, `FOLLOWS`, `MOTIVATED_BY`, `HAS_ACT`, `HAS_ARC`, `HAS_THEME`, `STARTS_AT`, `CLIMAX_AT`, `INVOLVES`, `OCCURS_IN`, `AFFECTS`, `REQUIRES_STATE`, `TRIGGERS_EVENT`, `CHANGES_STATE`, `RESOLVES`, `HAS_SYSTEM`, `HAS_PRESSURE`, `REQUIRES_RESOURCE`, `FORBIDDEN_BY`, `CAUSES`, `DEPENDS_ON`, and `TRAUMATIZED_BY`.

The manager creates the uniqueness constraint and an `entity_embeddings` cosine vector index of dimension 384 during construction. It attempts Neo4j 5.15 syntax while Compose pins Neo4j 5.14, then falls back to a procedure. DDL failures are logged rather than made fatal. Edge `valid_from` and `valid_until` model fields are not persisted.

## Vector surface

- Chroma uses a persistent client at `./data/chroma`.
- Its collection is named `novel_engine`.
- The adapter provides add and text query only; there is no project filter, version contract, delete/deactivate operation, health check, or rebuild operation.
- Neo4j entity embeddings use `sentence-transformers/all-MiniLM-L6-v2`, dimension 384, according to the current vector-search implementation.
- Both vector paths initialize globally and are unsafe to inspect through normal application import.

## Manual workflow baseline

### Graph CRUD

`test_endpoints.py` expects a separately running API and persistent Neo4j. It creates a custom node type, creates/reads/updates nodes, creates a custom edge type, creates/reads edges, lists/searches nodes, then partly cleans up through direct Cypher. It expects flattened node properties on reads but raw Neo4j record nesting from entity listing. It cannot clean up nodes through the API.

### Context extraction

`test_context.py` creates a character, location, system, and two edges; submits Markdown-style entity links to `/api/rag/context`; and prints returned nodes and scores. Its attempted API cleanup uses missing routes. The current flow is explicit-tag extraction → optional semantic anchors → policy-bounded Neo4j traversal → unbudgeted text summary.

### Character creation

`test_character_creation.py` asks for a name and bio, initializes a character, requests model-generated questions, collects answers, and immediately applies a generated graph update. Its request includes name/bio fields that the API request model does not accept/use. It requires a running API, human input, persistent stores, and potentially paid model calls.

## Known-defect coverage map

The 32 concrete repair targets are enumerated as FND-008.1 through FND-008.32 in [`phase-0-todo.md`](phase-0-todo.md). They cover manager/DTO shape errors, schema/update/delete behavior, route ordering and placeholder responses, character proposal validation and ID rewriting, the parked world workflow, import-time model/vector work, and semantic threshold/version behavior. Each item requires a regression test before or with its fix.

Additional characterization targets for resolver parsing, schema order, traversal policies, summarization, identifier validation, serialization, route resolution, and fake-AI workflows are listed in workstream 0C of the same checklist.

## Baseline limitations

No live OpenAPI document, database schema dump, or model request was executed because doing so would trigger exactly the import-time side effects Phase 0 must remove. The source-derived route inventory becomes executable contract evidence once the application factory and fakes exist.

## P0-01 verification snapshot

After introducing the foundation contract:

- `uv lock --check` passes with 136 resolved Python packages.
- A clean `pnpm install --frozen-lockfile` passes after explicitly allowing only esbuild's required install script.
- Frontend ESLint and the Vite production build pass.
- Python bytecode compilation passes for `backend/`.
- Backend Ruff lint correctly fails with 144 pre-existing findings, including real undefined-name defects already tracked under FND-008; the previous Makefile fallback no longer hides them.
- Ruff format check identifies 13 legacy files. Broad autoformatting is deferred so it does not obscure characterization work or overwrite the parked world-service edit.
- Frontend checks were executed on the workstation's Node 26 and emitted the intended engine warning. Node 24 LTS remains the supported target and must be proven in CI/clean-room verification.
- `.github/workflows/foundation.yml` now enforces Python lock integrity/source compilation and the Node 24 frozen-install/lint/build path. It intentionally does not claim backend lint or test health before P0-02 isolates the legacy suite.

## P0-02 verification snapshot

After isolating the deterministic suite and removing import-time external work:

- `make test` passes with 60 hermetic tests (unit markers) in under one second, with no Docker, network, API key, model download, or persistent write.
- `uv.lock` now resolves 141 packages after adding `mypy` to the development group.
- Import safety is enforced by `tests/unit/test_import_safety.py`, which imports `backend.main`, the API router, manager, traversal, vector search, vector store, and both services in a subprocess with network connections and cwd writes blocked.
- Removing import-time side effects moved Neo4j constraint/vector-index creation from `GraphManager.__init__` to the FastAPI lifespan, made the Chroma client lazy in `VectorStore`, deferred `sentence-transformers` import/model load to first use in `vector_search`, and made `CharacterService` construct `AsyncOpenAI` lazily.
- The import test caught a real defect: `CharacterService` previously required credentials at import time. `AsyncOpenAI` construction is now deferred and the client is injectable.
- A characterization test found the entity-tag regex is greedy and can span adjacent malformed fragments into a spurious ID; this is now documented rather than assumed safe.
- `make typecheck` passes for `resolver.py`, `summarizer.py`, and `schema.py`. Mypy immediately rejected `policies.py` because `semantic_top_k` is declared twice (FND-008.9, documented default 5); `policies.py` joins the type-check scope after that defect is repaired.
- Backend Ruff now reports 143 pre-existing findings (down from 144); `tests/` is Ruff-clean in check and format.
- Root manual scripts moved to `scripts/manual/` and are no longer collected by pytest. `legacy_endpoint_suite.py` is excluded from CI pending in-process replacement under 0F.
- 0C remains in progress: route-resolution, character-workflow, and world-workflow characterization wait on the application factory/injected ports (0E/0F) and the parked world-service edit (FND-008.25).

## P0-03 verification snapshot

After introducing PostgreSQL/pgvector, explicit migrations, and the settings contract:

- `uv.lock` resolves 149 packages after adding `sqlalchemy[asyncio]`, `psycopg[binary]`, `alembic`, and `pgvector`.
- Compose starts `pgvector/pgvector:pg17` and `neo4j:5.26` with health checks; `make infra-up` waits for both to be healthy using named volumes and env-interpolated credentials. A `neo4j-test` service exists under the `test` profile with separate ports (7475/7688) and volume.
- `make migrate` applies Alembic and Neo4j migrations from empty state; a second run is a no-op. `make migrate-verify` reports both stores current. Verified live against Compose: `entity_id_unique`, `entity_embeddings` (VECTOR), and the `_SchemaMigration` ledger all exist.
- Alembic `migrations/` enables the `vector` extension and creates `infrastructure_metadata`; upgrade is idempotent and downgrade to base removes the table (verified in a per-run disposable database).
- The Neo4j runner stores migration IDs/checksums in a `_SchemaMigration` ledger and raises `MigrationDriftError` on checksum mismatch; ledger checksum recorded as `e5ec799c…` for `0001_initial_graph_schema`.
- `GraphManager.create_constraints`/`create_vector_index` were removed; the FastAPI lifespan now calls `Neo4jMigrationRunner.verify()` and refuses to start when migrations are pending (verified: starts against migrated Neo4j, raises a clear error against an unmigrated instance). The 5.15+ / 5.0 fallback path was deleted in favour of the pinned 5.26 native vector index. `verify()` is strictly read-only: the ledger constraint is created only when migrations are actually applied (see the alignment repair below).
- Settings now use `SettingsConfigDict` with `APP_ENV` (`development`/`test`/`production`), PostgreSQL DSN/pool fields, embedding settings, and a production guard that rejects default credentials. `Settings(_env_file=None, ...)` supports explicit test construction.
- New hermetic tests (79 total across unit modules) cover settings validation, the unit-of-work commit/rollback boundary, engine construction without connecting, and migration planning/run/idempotency/drift. Two `integration` tests passed against disposable stores: PostgreSQL upgrade/idempotency/downgrade in a per-run database and Neo4j apply-twice/verify against the `neo4j-test` service.
- 0D remains in progress only for the CI integration job (0I): `tests/integration` is not yet wired into `.github/workflows/foundation.yml`.

## P0-04/P0-05 verification snapshot

After introducing the application factory, container, ports, and adapters:

- `backend/main.py` now exposes `create_app(settings, container)` with a conventional `app = create_app()` ASGI export. The container is constructed inside the lifespan (not at import), so importing `backend.main` creates no engine or driver.
- `backend/container.py` holds a `Container` built from explicit settings with graph, vector, embedding, AI, schema-registry, clock, ID, artifacts, migration, and database ports. Adapters under `backend/adapters/` wrap Neo4j, Chroma, sentence-transformers, AsyncOpenAI, the system clock, a time-ordered ID generator, an optional filesystem artifact store, and PostgreSQL.
- `backend/ports.py` defines `GraphRepository`, `VectorStorePort`, `EmbeddingPort`, `AIGateway` (with `AIProviderError`/`AIInvalidOutputError`/`AIUsage`/`StructuredResponse`), `SchemaRegistryPort`, `Clock`, `IdGenerator`, `ArtifactStore`, `MigrationVerifier`, and `DatabasePort`.
- `CharacterService` now depends only on ports and is built by the container; debug-response caching goes through the injected artifact store (Null unless `ENABLE_DEBUG_ARTIFACTS`). The `process_answers` missing-`chapter_number` call defect was corrected and is covered by an application test.
- Health routes: `/health/live` (always alive), `/health/ready` (Neo4j migration + ping + PostgreSQL readiness/migration, 503 when degraded), `/health/capabilities` (optional embeddings/AI/vector, no secrets, no model load or disk write).
- New hermetic tests: 96 total (up from 79). Application workflow runs on in-memory fakes; contract tests cover health, lifecycle (open/close once, failure closes), and API overrides for the character and schema routes. Live verification against Compose returned `/health/ready` 200 with all checks and no `data/chroma` side effect.
- Remaining 0E gaps: the graph and RAG routes still use legacy module singletons (0F will move them onto the container and stable DTOs); the unit-of-work and vector-filter/rebuild port surface is not yet exposed through the container.
- Backend Ruff debt dropped from 143 to 109 findings; 8 legacy files still need formatting.

## Alignment repair (post-audit)

An audit of P0-03 through P0-05 found several checked claims stronger than the implementation. The following were repaired before starting 0F, each with a regression test:

- **Read-only verification (critical):** `Neo4jMigrationRunner.verify()` no longer calls `_ensure_ledger()`. Startup and readiness verification is strictly non-mutating; the ledger constraint is created only by `run()`. `tests/unit/test_neo4j_migrations.py::test_verify_is_read_only_and_never_creates_schema` fails if `verify()` issues any `CREATE`/`MERGE`.
- **No global driver at import (critical):** `backend/graph/manager.py` no longer instantiates `GraphManager` at import. `get_graph_manager()` is an `lru_cache` factory and `graph_manager` is a lazy proxy, so legacy callers (including the parked `world_service.py`) keep working without constructing a driver. The subprocess import-safety test now patches `neo4j.GraphDatabase.driver` to fail, so importing `backend.main` must not construct a driver (verified: 0 constructions).
- **Complete startup/cleanup (high):** the lifespan verifies both Neo4j and PostgreSQL migration state inside a single `try/finally`, so a failing verifier (including one that raises) still disposes resources. `Container.aclose()` now also closes the AI client via `AIGateway.aclose()`. Covered by `tests/contract/test_health.py` and `tests/contract/test_lifecycle.py`.
- **Honest capability reporting (medium):** `SentenceTransformerEmbedding` is self-contained (owns its lazy model load rather than delegating to module-global `vector_search`), `available()` only checks dependency presence, and the new `loaded()` reports whether the model is in memory. Capabilities report both. `VectorStore.health()` no longer reports success for an unopened store and never creates it; it reports whether a persisted store exists or probes an already-initialised client.
- **ADR drift (medium):** Compose now pins exact images (`pgvector/pgvector:0.8.0-pg17`, `neo4j:5.26.0`), and the production ID generator emits real RFC 9562 UUIDv7 values.
- **Checklist honesty (medium):** the 0C items claiming storage-only property and embedding exclusion were reworded to "current behavior characterized"; exclusion/UTC normalization remain pending 0F.

Post-repair verification: 101 hermetic tests, 2 disposable integration tests, read-only `health/ready` 200 against Compose, no driver at import, no `data/chroma` side effect, 109 backend Ruff findings (unchanged), 7 files needing formatting.

## P0-07 (0F) verification snapshot

After introducing the stable API v1 DTO and problem contracts:

- `/api/v1` is the canonical surface (`plan/api-surface.md`). The original prototype routes remain mounted at `/api` as documented deprecated aliases; new work must use v1.
- `backend/api/v1/schemas.py` defines `Entity`, `EdgeModel`, create/update/list/search DTOs, schema-type, context, question-sheet, and graph-update DTOs. Create/read/update/search return one `{id,type,properties,created_at}` shape; embeddings and storage-only fields are excluded and `created_at` is normalized to timezone-aware UTC.
- `backend/domain/errors.py` defines validation, not-found, conflict, capability-unavailable, provider-failure, persistence, and not-implemented errors. `backend/api/problems.py` maps them, request-validation errors, HTTP exceptions, `AIError`, and unhandled exceptions to `application/problem+json` with `correlation_id`. `backend/api/middleware.py` propagates/generates `X-Correlation-ID` and stores it on `request.state`.
- `/api/v1` rejects unknown node/edge types and invalid identifiers with 400 (no silent schema mutation), rejects path/body id mismatch, validates edge endpoints, and returns 501 `not_implemented` for `/api/v1/rag/query`.
- `GraphRepository.traverse` was added to the port so `/api/v1/context` runs on injected ports rather than legacy singletons.
- Contract evidence: `tests/contract/test_v1_entities.py`, `test_v1_schema.py`, `test_v1_context.py`, `test_v1_characters.py`, `test_v1_errors.py`, and `test_openapi.py` (exact `openapi_snapshot.json`, generated without services). Route resolution (`/entities/search` before `/entities/{entity_id}`) is covered.
- Verification: 129 hermetic tests (up from 101), 2 integration tests, live v1 smoke against Compose (create 201 with no embedding leak, get/search/context 200, rag 501, legacy 200, health/ready 200), typecheck and import-check green, 109 backend Ruff findings.
- Remaining 0F gap: legacy `/api` schema routes still auto-register unknown types and the legacy routers still expose prototype shapes; migrating/removing them is 0G. The graph/RAG routes are otherwise not on the container port surface yet.

## P0-08 (0G) verification snapshot

Prototype defect repairs in committed code (FND-008.1–22, 28–32), each with a regression test:

- **Graph/schema:** `get_nodes` parameter handling fixed and flattened to the same shape as `get_node`; `_to_node` mapper characterized (`test_node_mapper.py`); node type changes rejected (v1 409, legacy 409); node deletion added across port/adapter/manager and exposed as `DELETE /api/v1/entities/{id}`; unknown/invalid types rejected without silent registration; `World` node type added; custom schema types sorted in output and on disk; duplicate `semantic_top_k` removed and traversal-policy bounds validated; traversal query extracted to `build_traversal_query` and characterized for directionality/exclusions (`test_traversal_query.py`).
- **API routing/behavior:** legacy static `/search` and `/entities` routes declared before `/{node_id}`; debug `print` removed; placeholder RAG returns 501 on both surfaces; v1 delete/update reflect real affected records.
- **Character workflow:** full temporary→durable ID map with edge-endpoint rewriting; unknown node/edge types rejected with actionable messages; duplicate normalized IDs, missing edge targets, non-character sources, and conflicting existing-entity types rejected before any write; filesystem debug cache replaced by the injected artifact store.
- **Embeddings:** deterministic `None` behavior when embeddings are disabled, covered by `DisabledEmbeddingProvider`; lazy model/collection behavior retained.
- **Explicit deferrals:** FND-008.21 (atomic mutation application → Phase 1 mutation review) and FND-008.23–27 (world workflow → parked `world_service.py` reconciliation) are documented in the checklist; edge validity is explicitly excluded from the v1 contract.
- Verification: 155 hermetic tests (up from 129), 2 integration tests, live smoke confirmed legacy route resolution (200), legacy RAG 501, v1 delete 204 → get 404, legacy unknown type 400, legacy type change 409. Type-check scope now includes `policies.py` (4 files, clean). Backend Ruff debt down from 109 to 72 findings; 3 files need formatting.
- 0G remains in progress only for the world-workflow defects blocked on the parked worktree edit.

## P0-09 (0H) verification snapshot

Integration fixtures, the reference seed world, and guarded reset/seed commands:

- **pgvector production adapter:** `backend/adapters/vector.py` `PgVectorStore` implements `VectorStorePort` using psycopg + pgvector, taking embeddings through the injected `EmbeddingPort` (no model load in the adapter). Alembic `0002_vector_documents` creates the derived `vector_documents` table (HNSW cosine index, dimension 384). `build_container` now uses `PgVectorStore`; `backend/core/vector_store.py` remains a transitional module and is no longer wired into the container.
- **Integration fixtures:** `tests/integration/conftest.py` creates a uniquely named PostgreSQL database per test (migrations applied automatically, dropped `WITH (FORCE)` in teardown) and connects to the separate `neo4j-test` service (entity-scoped cleanup between tests, skipped when unreachable). No development volume is touched.
- **Contract/integration evidence:** graph repository contract (`test_graph_repository.py`), pgvector adapter contract including upsert/delete/health (`test_pgvector_adapter.py`), PostgreSQL transaction commit/rollback and migration upgrade/downgrade (`test_postgres_transactions.py`, `test_postgres_migrations.py`), and API integration with real Neo4j/pgvector stores plus fake AI/embedding (`test_api_stores.py`). 16 integration tests pass twice consecutively via `make integration-test`.
- **Reference seed world:** `backend/seed/` defines a versioned manifest (`2026-10-05.1`, fixed timestamp) with a deterministic checksum (`43252e91465db7d0195bc521bad124ea263bd24285e62b3d34e21ef884c5aaa6`), 27 nodes and 29 edges. It includes two placeholder workspaces/projects, two characters, two locations, an object, a resource, a faction, a system and law, a public fact, an author-only secret, a false character belief, a flashback with narrative ordering, two branches with a conflicting fact, possession/resource and travel constraints, an unresolved thread with setup and planned payoff, prose snippets with expected entity tags, and expected query fixtures. `tests/unit/test_seed_manifest.py` and `tests/application/test_seed_apply.py` verify the definition and idempotent application; `tests/integration/test_seed_world.py` verifies against real Neo4j + PostgreSQL and reset-then-reseed.
- **Reset/seed commands:** `make seed` and `make reset-dev` (`backend/devtools/`). Reset clears Neo4j entities, pgvector rows, seed-ledger rows, and optional debug artifacts in a known order, tolerating un-migrated stores so `make reset-dev && make migrate && make seed` works from a fresh database. The guard (`backend/devtools/guard.py`) is fail-closed: production is always refused, unknown environments are refused, confirmation (`--force`) is required, and an undeclared `APP_ENV` with non-local stores is refused. `tests/unit/test_reset_guard.py` proves a missing `APP_ENV` cannot authorize a reset against remote stores.
- **Verification:** 175 hermetic tests (up from 155), 16 integration tests, seed applied twice with stable counts (27 nodes / 29 edges) and checksum, `make reset-dev && make migrate && make seed` produces the reference world, `make typecheck` and `make import-check` green, backend Ruff debt unchanged at 72 legacy findings (3 legacy files still need formatting).
- **0H residual:** query filters, deactivation, and rebuild hooks for the vector port remain deferred; the legacy Chroma module is retained but unused by the container.

## P0-11 (0I) verification snapshot

Diagnostics, health, and continuous integration:

- **Structured logging and redaction:** `backend/core/logging.py` adds a JSON formatter (UTC timestamp, level, logger, event, `correlation_id`), a correlation-ID contextvar shared with the middleware, and `redact_text`/`redact_fields` for OpenAI keys, bearer tokens, DSN passwords, and secret-named fields. `configure_logging` installs one handler on the root logger; `log_event` emits structured, redacted events. `tests/unit/test_logging_redaction.py` covers detection, recursion, formatting, and the contextvar.
- **Adapter instrumentation:** a shared `instrument` decorator logs duration and result count at DEBUG and failures (error type only) at WARNING for graph and vector adapters; PostgreSQL readiness and AI generation emit timing/usage events. No manuscript, prompt, or response content is logged.
- **Explicit degradation:** embedding load failure, custom-schema load failure, semantic-search failure, traversal failure, and Neo4j ping failure now emit structured events instead of silent/swallowed f-string logs; capability state remains explicit via `available()`/`loaded()`. `/health/capabilities` reports a `reason` for each degraded optional provider.
- **Health coverage:** `tests/contract/test_health.py` now covers healthy, Neo4j-unreachable (503), PostgreSQL-down (503), migration-behind (503), optional-provider-down (still ready), and capability degradation reasons.
- **Secret scan:** `scripts/ci/secret_scan.py` (stdlib only, `make secret-scan`) scans tracked/staged files for high-signal credential shapes with a fixture/example allowlist. `tests/unit/test_secret_scan.py` proves detection, placeholder skipping, allowlisting, and non-zero exit.
- **CI:** `.github/workflows/foundation.yml` runs four jobs on PRs and pushes — `secret-scan`, `python-foundation` (lock check, compile, locked install, Ruff lint, format check, type-check scope, hermetic tests with coverage, artifact upload), `frontend-foundation` (frozen install, lint, build), and `integration-tests` (disposable `pgvector/pgvector:0.8.0-pg17` + `neo4j:5.26.0` services, migrations, integration suite). Concurrency cancels superseded runs; live LLM tests are excluded.
- **Lint/format debt cleared:** all tracked backend/test files are Ruff-clean and formatted. The parked, user-owned `backend/services/world_service.py` is explicitly excluded from Ruff pending FND-008.23–27, documented in `pyproject.toml`.
- **Verification:** 188 hermetic tests (up from 175), 16 integration tests, `make check` fully green (lock-check, lint, format-check, secret-scan, typecheck, hermetic tests, frontend lint, frontend build), `make secret-scan` clean over tracked files.
- **0I residual:** the first hosted CI run and the deliberate-failure acceptance checks remain unobserved locally; they are marked incomplete until a PR run is linked.

## P0-12 (0J) verification snapshot

Documentation, exit evidence, and the clean-room exit audit:

- **Docs:** README quick start now includes `make seed`/`make reset-dev`, the full clean-checkout sequence, and a Migrations section (Alembic + Neo4j runner authoring/verification). `.env.example` documents `LOG_LEVEL`. `AGENTS.md` reflects the stabilized maturity, source map (`seed/`, `devtools/`, `scripts/ci/`), setup, validation commands, and Phase 0 boundaries. `plan/api-surface.md` and `scripts/manual/README.md` cover API v1 deprecation and manual scripts.
- **Gap analysis and backlog:** `plan/12-current-gap-analysis.md` rewritten to the post-Phase-0 state with remaining gaps and a Phase 1 refactor order. `plan/10-backlog.md` marks FND-001–005A/006/007/009 implemented and FND-008 partially implemented with explicit deferrals.
- **Exit evidence:** `plan/phase-0-evidence.md` records runtimes, test counts (188 hermetic / 16 integration), 63% hermetic coverage baseline, OpenAPI SHA-256, PostgreSQL head and Neo4j migration checksum, seed version/checksum, redaction/secret hygiene, CI definition, and deferred defects.
- **Clean-room rehearsal:** on a copy with no `.env`, `.venv`, caches, `node_modules`, or databases, `make install-dev`, `make migrate`, `make reset-dev`, `make seed`, `make test` (188), `make test-integration` (16), OpenAPI generation (34 paths), `make import-check`, and `make frontend-install/lint/build` all passed; repeating `migrate/seed/test` reproduced identical counts and checksum.
- **Verification:** 188 hermetic tests, 16 integration tests, `make check` green, secret scan clean.
- **0J residual:** the first hosted CI run and the project-owner sign-off remain external actions.
