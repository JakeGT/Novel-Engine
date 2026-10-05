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
