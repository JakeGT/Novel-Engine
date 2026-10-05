# Phase 0 todo — Stabilize the prototype

- Status: In progress
- Scope: Foundation only
- Backlog coverage: FND-001 through FND-009, plus Phase-0 portions of QLT-001 and OPS-001
- Exit owner: project owner or designated technical lead

## Objective

Turn the current local prototype into a deterministic, testable foundation that can safely receive SaaS identity, canon, retrieval, and consistency work in later phases.

Phase 0 is complete when a clean checkout can be installed, linted, unit-tested, integration-tested against disposable PostgreSQL/Neo4j services, started without hidden side effects, reset/reseeded outside production, and exercised through stable documented API contracts without paid AI access.

## Scope boundaries

Phase 0 includes:

- reproducible runtimes, dependencies, commands, and CI
- PostgreSQL/pgvector and Neo4j development infrastructure
- explicit PostgreSQL and Neo4j migrations
- application lifecycle and dependency injection
- domain/application ports and in-memory fakes
- stable prototype DTOs and API error contracts
- repair of known prototype defects
- deterministic unit/application tests and disposable integration tests
- safe reset and reference-world seed tooling
- baseline diagnostics, health, and documentation

Phase 0 does **not** include:

- production authentication or account recovery
- memberships, production tenant authorization, subscriptions, payments, or credit ledger
- encrypted BYOK credential storage
- production job workers or the local-model companion
- final fact/belief/event/branch domain redesign
- story planner, prose editor, generation pipeline, or mutation review UI
- full consistency engine or OpenCodeReview-inspired bundle implementation
- production deployment, backup, or operational support tooling

Phase 0 may introduce `workspace_id` and `project_id` primitives/interfaces so later work does not require another foundational rewrite, but it must not pretend that production isolation exists before Phase 1 tests prove it.

## Non-negotiable engineering rules

- Preserve unrelated worktree changes and establish ownership before staging them.
- No network connection, model loading/download, database query, directory creation, or persistent write at module import time.
- Unit and application tests require no services, secrets, internet, or downloaded model.
- Integration tests own their data and never use a developer's normal `data/` directory.
- Migrations are explicit commands, idempotent where applicable, and never hidden inside ordinary application startup.
- Reset commands refuse to run in production-like environments.
- AI outputs and infrastructure driver values never leak directly into API/domain contracts.
- Each repaired defect receives a regression test.
- Do not combine package relocation with unrelated behavior changes unless characterization tests already protect that behavior.

## Completion dashboard

| Workstream | Backlog | Status | Depends on |
| --- | --- | --- | --- |
| 0A. Baseline and decisions | FND-007 | Implemented | — |
| 0B. Reproducible toolchain | FND-007 | In progress | 0A |
| 0C. Test harness and characterization | FND-006 | In progress | 0A–0B |
| 0D. PostgreSQL/Neo4j infrastructure and migrations | FND-005, FND-005A | In progress | 0A–0B |
| 0E. Application lifecycle and ports | FND-001, FND-002 | In progress | 0C–0D |
| 0F. Stable DTOs and errors | FND-003, FND-004 | In progress | 0C, 0E |
| 0G. Prototype repairs | FND-008 | Not started | 0C, 0E–0F |
| 0H. Integration fixtures and seed world | FND-006, FND-009, QLT-001 | Not started | 0D–0G |
| 0I. Diagnostics, health, and CI | OPS-001, FND-007 | Not started | 0B–0H |
| 0J. Documentation and exit audit | All Phase 0 | Not started | 0A–0I |

Update this table when a workstream enters progress or passes its acceptance gate. Detailed items remain checked only when their verification is recorded.

---

## 0A. Baseline and foundational decisions

### Repository baseline

- [x] Create a Phase 0 implementation branch from current `main`, preserving and documenting the pre-existing dirty worktree.
- [x] Explicitly park the unfinished `backend/services/world_service.py` work for FND-008.25 without modifying it.
- [x] Treat `.opencode/` as user-local tooling and ignore it.
- [x] Record the pre-refactor API surface from source; defer executable OpenAPI capture until import safety permits it.
- [x] Record current graph labels, relationship types, constraints, indexes, Chroma collection metadata, and known disposable data assumptions.
- [x] Confirm in ADR-007 and the Phase 0 baseline that all current database data may be reset and has no migration guarantee.
- [x] Capture current manual workflow behavior for graph CRUD, context extraction, and character creation.
- [x] Convert each known defect in `plan/12-current-gap-analysis.md` into FND-008.1–32 before fixing it.

### Tool and architecture decisions

- [x] Select Python 3.13 as the Phase 0 runtime; add a secondary version only after the hermetic suite exists.
- [x] Select Node.js 24 LTS for the existing Vite toolchain.
- [x] Select `uv` with committed `uv.lock` as the Python dependency workflow.
- [x] Select pnpm 11 with committed `frontend/pnpm-lock.yaml`; CI enforcement remains in 0I.
- [x] Select SQLAlchemy 2 async sessions, psycopg 3, and Alembic for PostgreSQL.
- [x] Select a project-owned ordered Neo4j migration runner with applied IDs and checksums.
- [x] Select PostgreSQL 17 with a pinned pgvector 0.8-series development image and hosted-feature compatibility checks.
- [x] Select `create_app(settings, container)` plus FastAPI lifespan and dependency overrides.
- [x] Select injected UTC clock and sortable UUIDv7-compatible ID ports with deterministic test implementations.
- [x] Record the decisions in ADR-007.

### 0A acceptance

- [x] Supported runtimes and package managers are unambiguous.
- [x] Persistence/migration/application-factory decisions are recorded.
- [x] Existing user work and disposable data policy are explicitly resolved.
- [x] A baseline behavior/API inventory exists for regression comparison.

---

## 0B. Reproducible toolchain and repository hygiene

### Python

- [x] Add project metadata and supported Python version to `pyproject.toml`.
- [ ] Split runtime, development, integration, and optional local-embedding dependencies clearly.
- [ ] Remove unused dependencies only after repository-wide import/search confirmation.
- [x] Generate a reproducible Python dependency lock; commit it with P0-01.
- [ ] Ensure install works from a clean virtual environment.
- [x] Configure Ruff lint and formatting rules for the whole maintained Python tree (enforced over `backend` and `tests`; 143 tracked backend defects remain under FND-008).
- [x] Add type checking configuration and select the initial enforcement scope; do not block Phase 0 on typing every legacy module.
- [x] Add coverage configuration with separate unit/application and integration reporting.
- [x] Add a command that validates imports without connecting to services.

### Frontend

- [x] Generate the pnpm lock file; commit it with P0-01.
- [x] Confirm a clean frozen install succeeds (workstation Node 26 warned; Node 24 CI proof remains outstanding).
- [x] Confirm lint and production build succeed from the clean frozen install.
- [ ] Remove unused Vite starter assets only in a dedicated cleanup change.
- [x] Do not begin product UI or TypeScript migration unless separately scoped; Phase 0 only needs a deterministic build baseline.

### Repository commands

- [x] Make `make install-dev`, `make test`, `make lint`, `make format-check`, `make frontend-lint`, and `make frontend-build` accurate and non-fallback-masking.
- [ ] Add `make integration-test`, `make migrate`, `make seed`, `make reset-dev`, and `make check` (integration-test and check added; migrate added under 0D; seed/reset-dev depend on 0H).
- [x] Remove fallback-masking from current commands so failures propagate.
- [x] Add Windows-equivalent instructions or scripts for contributors without `make` (README instructs running the `Makefile` commands directly).
- [x] Ignore current generated/local paths, including `.opencode/`, databases, caches, builds, logs, debug responses, and credentials.
- [ ] Add a staged-secret check or equivalent CI guard.

### 0B acceptance

- [ ] Clean backend and frontend installs are reproducible.
- [ ] Lock files are committed and CI uses frozen/locked installation.
- [ ] Each documented command does exactly one predictable thing and propagates failure.
- [ ] No secret, generated database, model cache, or debug response is tracked.

---

## 0C. Test layout, fakes, and characterization

### Test organization

- [x] Create `tests/unit`, `tests/application`, `tests/contract`, `tests/integration`, and `tests/e2e` directories with documented responsibilities.
- [ ] Move `test_endpoints.py` into the integration suite or replace it with in-process API tests (removed from collection to `scripts/manual/legacy_endpoint_suite.py`; in-process replacement tracked under 0F).
- [x] Move interactive `test_character_creation.py`, `test_context.py`, and `run_test_query.py` into `scripts/manual/` with names that pytest will not collect.
- [ ] Mark service-dependent tests `integration` and paid/live-model tests `llm` (markers declared; no service-dependent tests exist yet).
- [x] Prevent test collection from running HTTP calls, opening databases, loading embedding models, or reading interactive input.
- [x] Add deterministic clock and ID fixtures.
- [x] Add settings factories that never read a developer's `.env` unless explicitly requested.

### Characterization tests before refactoring

- [x] Entity tag extraction: ordering, deduplication, malformed links, Unicode names, IDs containing safe punctuation, and ordinary Markdown links.
- [x] Schema registry: core/custom recognition, persistence behavior, invalid identifiers, duplicate operations, and deterministic ordering.
- [x] Context policies: default selection, allowed edges, exclusions, semantic flags, limits, and unknown context type.
- [x] Context summarizer: stable ordering, empty context, missing names, nested values; current inclusion of storage-only properties is characterized (exclusion is pending 0F).
- [x] Graph identifier validation: accepted identifiers and injection attempts.
- [x] Entity/edge DTO serialization: datetimes, empty properties, and consistent nesting; current presence of embeddings in domain dumps and naive `created_at` are characterized (exclusion and UTC normalization are pending 0F).
- [ ] API route resolution: static `search`/`entities` routes are not captured as node IDs (deferred to 0F; needs the application factory to avoid startup side effects).
- [x] Character workflow with fake graph and fake AI: init, question generation, proposal extraction, ID resolution, and validation failure (`tests/application/test_character_workflow.py`).
- [ ] World workflow with fake graph and fake AI: init and question generation, including empty/no semantic context (deferred to 0G/FND-008.25; parked worktree edit).
- [x] Application import test: importing settings, routers, services, and `backend.main` performs no external work.

### Test doubles

- [x] In-memory graph repository fake with explicit behavior, not a mock of Neo4j driver calls.
- [x] In-memory vector store fake with deterministic scoring/results supplied by the test.
- [x] Scripted AI gateway fake for structured success, invalid output, refusal, timeout, and provider error.
- [x] Fake embedding provider returning fixed-dimension deterministic vectors.
- [x] Fake clock and ordered ID generator.
- [x] Fake migration/version checker for app startup tests.

### 0C acceptance

- [x] `make test` runs without Docker, network, API keys, model downloads, or persistent writes.
- [ ] Existing intended behavior has characterization coverage before adapters are replaced.
- [x] External failures can be simulated deterministically through fakes.
- [x] Root-level manual scripts are no longer pytest tests.

---

## 0D. PostgreSQL, pgvector, Neo4j, and migrations

### Development infrastructure

- [x] Add PostgreSQL with pgvector support to Compose using a pinned supported image/version (`pgvector/pgvector:pg17`).
- [x] Keep Neo4j in Compose and pin a version compatible with required constraints and vector indexes (`neo4j:5.26`).
- [x] Remove static container names unless there is a documented reason; allow parallel project/test stacks.
- [x] Use health checks and dependency readiness rather than sleeps (`--wait` on `infra-up`).
- [x] Move development credentials into environment interpolation with safe local defaults only.
- [x] Add named/profiled development and integration-test storage so tests never touch normal development volumes.
- [x] Document ports and allow overrides to prevent local collisions.

### Settings

- [x] Replace class-based Pydantic settings config with current settings configuration.
- [x] Introduce `APP_ENV` with explicit `development`, `test`, and `production` behavior.
- [x] Add PostgreSQL DSN/pool settings and Neo4j/vector/object/AI settings.
- [x] Validate required production settings and reject unsafe default credentials.
- [x] Keep optional AI/embedding settings optional for non-AI workflows.
- [x] Ensure settings can be instantiated explicitly in tests without global mutation.

### PostgreSQL foundation

- [x] Add async engine/session factory with bounded pool settings and clean shutdown.
- [x] Add Alembic configuration that imports metadata without importing the FastAPI app or connecting.
- [x] Create initial migration enabling required extensions, including pgvector.
- [x] Add minimal infrastructure metadata needed for migration/health verification; do not prematurely build Phase 1 account tables.
- [x] Add transaction/unit-of-work primitive and rollback tests.
- [x] Add database readiness check that is separate from liveness.
- [x] Verify upgrade from empty database and repeated current-version checks.
- [x] Verify downgrade policy or explicitly adopt tested forward-only/restore behavior.

### Neo4j migrations

- [x] Remove constraint and vector-index creation from `GraphManager.__init__` and normal application startup.
- [x] Create ordered Neo4j migrations for current uniqueness constraints and required indexes.
- [x] Store applied migration IDs/checksums and fail clearly on checksum drift.
- [x] Verify migrations against a clean disposable Neo4j instance.
- [x] Verify applying migrations twice is safe.
- [x] Add a schema/index verification command used by readiness and CI.
- [x] Keep prototype vector index migration only if its embedding dimension/provider is explicitly versioned; otherwise defer/recreate it safely.

### 0D acceptance

- [x] `make infra-up` starts healthy PostgreSQL/pgvector and Neo4j services.
- [x] `make migrate` upgrades both stores from empty state without starting the API.
- [x] Application startup verifies migration versions but does not mutate schemas.
- [x] Integration databases are disposable and isolated from developer data.
- [ ] Migration verification and idempotency tests pass in CI (tests pass locally; CI integration job is 0I).

---

## 0E. Application lifecycle, dependency injection, and ports

### Application construction

- [x] Replace module-global application construction with `create_app(...)` while retaining a conventional ASGI export.
- [x] Build an application container/dependency provider from explicit settings.
- [x] Open PostgreSQL, Neo4j, vector, embedding, and AI resources only during lifespan or first authorized use (container is built inside lifespan; adapters are lazy).
- [x] Close pools/drivers/workers deterministically during shutdown.
- [x] Make startup failure explicit for required dependencies and graceful for optional AI/embedding capabilities.
- [x] Provide FastAPI dependency overrides for tests.
- [x] Separate liveness, readiness, and capability/degradation responses.

### Ports

- [x] Define graph repository port in application/domain terms rather than Cypher/driver records.
- [ ] Define PostgreSQL unit-of-work/journal port at the minimum Phase 0 boundary (`session_scope` exists in `core/database.py` but is not yet surfaced through the container/handlers).
- [ ] Define vector port: add/upsert, query with filters, delete/deactivate, health, and rebuild hooks (add/delete/health defined; filters, upsert, and rebuild deferred to the pgvector adapter).
- [x] Define embedding port with model/version/dimension metadata.
- [x] Define AI gateway port for structured output and normalized errors/usage without binding to one SDK.
- [x] Define clock and ID-generator ports.
- [x] Define schema registry port instead of direct process-global JSON mutation.
- [x] Define migration/version and health ports where useful.

### Adapters

- [x] Wrap existing Neo4j behavior behind the graph port before significant query redesign.
- [x] Replace the global Chroma singleton with an injected transitional adapter; new target implementation is pgvector.
- [x] Move sentence-transformer initialization behind an embedding adapter and lazy lifecycle.
- [x] Move AsyncOpenAI construction behind the AI adapter; API key absence must not break non-AI startup.
- [x] Prevent debug response cache directories/files from being created during import or read-only workflows.

### 0E acceptance

- [x] Import-side-effect test passes with network/filesystem/database calls instrumented to fail.
- [x] One representative application workflow runs entirely with in-memory fakes (character workflow).
- [ ] API tests override every external adapter cleanly (character, schema, and health routes use the container; graph and RAG routes still use legacy singletons pending 0F).
- [x] Startup/shutdown tests verify resources open and close once.
- [x] Capability health clearly distinguishes required-store failure from optional-AI degradation.

---

## 0F. Stable API DTOs and error contract

### DTOs

- [x] Introduce `/api/v1` routing while documenting any temporary compatibility aliases (`plan/api-surface.md`; legacy `/api` routes retained as deprecated aliases).
- [x] Define stable `Entity`, `Edge`, schema-type, context, question-sheet, and graph-update DTOs.
- [x] Use one nested `properties` shape consistently for create/read/update/search.
- [x] Separate domain models, persistence records, and API DTOs (domain in `backend.graph.schema`, service models in `backend.services`, external contract in `backend.api.v1.schemas`).
- [x] Exclude embeddings and internal storage metadata from ordinary API responses.
- [x] Normalize timestamps to timezone-aware UTC.
- [x] Define update semantics: full replacement versus patch; reject path/body ID mismatch.
- [x] Define list/search response shape and pagination placeholder even if prototype queries remain small.
- [x] Add examples and descriptions to generated OpenAPI.

### Errors

- [x] Define domain errors for validation, not found, conflict, unavailable capability, provider failure, and persistence failure.
- [x] Implement a consistent problem response with `type`, `title`, `status`, `code`, `detail`, `field_errors`, and `correlation_id`.
- [x] Add exception handlers that redact internal exceptions and secrets.
- [ ] Return deterministic 4xx errors for invalid schema identifiers and unknown types instead of mutating schema silently (`/api/v1` enforces this; legacy `/api` schema routes still auto-register and must be removed/migrated in 0G).
- [x] Map unavailable optional AI/embedding capability to an explicit response rather than a generic 500 (`AIError` → 502 `provider_failure`; capability degradation reported by `/health/capabilities`).

### Contract evidence

- [x] Add in-process API tests for every current endpoint's success and primary failure cases (v1 surface; legacy aliases documented as deprecated).
- [x] Save or validate an OpenAPI snapshot to catch accidental contract drift.
- [x] Document which prototype endpoints are retained, deprecated, admin-only, or placeholder-disabled (`plan/api-surface.md`).
- [x] Ensure placeholder RAG answers cannot be mistaken for generated production output (`/api/v1/rag/query` returns 501 `not_implemented`).

### 0F acceptance

- [x] Create/read/update/search return the same entity representation.
- [x] All known failures use the problem contract and correlation ID.
- [x] Static and dynamic routes resolve correctly (`/entities/search` declared before `/entities/{entity_id}`).
- [x] OpenAPI generation works with no external services.

---

## 0G. Prototype defect repairs

Each repair must have a failing regression test first or in the same change.

### Graph and schema

- [ ] **FND-008.1:** fix `get_node`/service property-shape mismatch; use explicit mapper rather than ad hoc `.get("properties")` assumptions.
- [ ] **FND-008.2:** correct `get_nodes` parameter handling and return DTOs instead of raw Neo4j records.
- [ ] **FND-008.3:** define and test node type-change behavior; relabel safely or reject type changes.
- [ ] **FND-008.4:** persist edge validity/provenance fields or explicitly remove unsupported fields from the prototype contract until implemented.
- [ ] **FND-008.5:** define node retirement/deletion and make manual/integration cleanup use an existing operation.
- [ ] **FND-008.6:** reject invalid/unknown content types unless they were created through an explicit schema operation.
- [ ] **FND-008.7:** add missing `World` type or resolve the world-anchor representation consistently.
- [ ] **FND-008.8:** return deterministic ordering for custom schema types and persisted registry output.
- [ ] **FND-008.9:** fix duplicate `semantic_top_k` declaration and validate traversal-policy bounds.
- [ ] **FND-008.10:** correct traversal depth/PageRank naming and query construction; characterize directionality and exclusions.

### API routing and behavior

- [ ] **FND-008.11:** declare static `/search` and `/entities` routes before `/{node_id}` or restructure routers to eliminate ambiguity.
- [ ] **FND-008.12:** remove debugging `print` calls and use redacted structured logging.
- [ ] **FND-008.13:** remove or return `501 Not Implemented` for placeholder RAG generation instead of returning fake success data.
- [ ] **FND-008.14:** validate source/target existence and project scope through application services, not repeated endpoint queries.
- [ ] **FND-008.15:** make delete/update responses reflect actual affected records and missing-target behavior.

### Character workflow

- [ ] **FND-008.16:** accept and persist the bio expected by question generation, with one authoritative request contract.
- [ ] **FND-008.17:** read character name/bio through the stable entity DTO.
- [ ] **FND-008.18:** build a complete temporary-ID → durable-ID map and rewrite edge endpoints after node ID normalization.
- [ ] **FND-008.19:** reject unknown LLM node/edge types with actionable validation errors.
- [ ] **FND-008.20:** validate duplicate IDs, missing targets, source constraints, and existing-entity references before any write.
- [ ] **FND-008.21:** make graph proposal application atomic/idempotent enough for the prototype or leave it explicitly uncommitted for later mutation review.
- [ ] **FND-008.22:** replace filesystem debug cache coupling with an injected optional development artefact store.

### World workflow

- [ ] **FND-008.23:** fix `explitict_ids`/`explicit_ids` mismatch.
- [ ] **FND-008.24:** add the missing JSON import or replace ad hoc serialization with the shared context renderer.
- [ ] **FND-008.25:** reconcile the unfinished world-service work already present in the worktree.
- [ ] **FND-008.26:** reuse shared question/proposal DTOs and retrieval helpers instead of duplicating character-service structures.
- [ ] **FND-008.27:** expose only the world endpoints that have complete, tested behavior; document unfinished answer processing.

### Embeddings and vector behavior

- [ ] **FND-008.28:** remove model download/loading from import and make embedding capability explicit.
- [ ] **FND-008.29:** version embedding model and dimension in vector metadata/index configuration.
- [ ] **FND-008.30:** define deterministic behavior when embeddings are disabled.
- [ ] **FND-008.31:** prevent Chroma collection creation on import; isolate it as transitional until pgvector replacement.
- [ ] **FND-008.32:** validate semantic score thresholds consistently across API and service calls.

### 0G acceptance

- [ ] Every numbered defect is repaired, deliberately deferred with a documented contract, or moved to a later backlog item with rationale.
- [ ] Every repaired defect has a deterministic regression test.
- [ ] Character and world question generation can be exercised with fake AI and no live database.
- [ ] Graph CRUD/context endpoints have stable documented success/failure behavior.

---

## 0H. Disposable integration fixtures, reset, and seed world

### Integration fixtures

- [ ] Provide a unique per-run PostgreSQL database/schema strategy.
- [ ] Provide a unique per-run Neo4j database or isolated label/project strategy appropriate to the selected edition.
- [ ] Apply migrations automatically in the integration fixture, not in application imports.
- [ ] Use pgvector through the production vector adapter in integration tests.
- [ ] Ensure teardown succeeds after test failure and leaves development volumes untouched.
- [ ] Add repository contract tests for graph and vector adapters.
- [ ] Add PostgreSQL transaction/rollback and migration tests.
- [ ] Add API integration tests with real stores and fake AI/embedding providers.

### Reference seed world

- [ ] Define a versioned seed manifest with deterministic IDs and fixed timestamps.
- [ ] Seed two placeholder workspaces/projects to support future isolation tests without implementing production auth.
- [ ] Include at least two characters, two locations, an object/resource, faction, system/rule, and relationship.
- [ ] Include one public fact, one author-only secret, and one false character belief.
- [ ] Include world events and narrative ordering that demonstrate a flashback.
- [ ] Include two branches with one intentional conflicting fact.
- [ ] Include possession/resource and travel constraints.
- [ ] Include an unresolved thread, promise/setup, and planned payoff.
- [ ] Include prose snippets and expected entity tags suitable for later retrieval/consistency evaluation.
- [ ] Store expected query/results fixtures for core graph lookups.

### Reset and seed commands

- [ ] Implement `reset-dev` with an explicit environment guard and confirmation or force flag.
- [ ] Refuse reset if environment is production, production-like, or cannot be determined safely.
- [ ] Reset PostgreSQL, Neo4j, pgvector-derived rows, object fixtures, and development artefacts in a known order.
- [ ] Implement idempotent `seed-dev` and `seed-test` commands.
- [ ] Print exactly which environment/stores were reset and seeded.
- [ ] Add a CI test proving the production guard cannot be bypassed by missing environment variables.

### 0H acceptance

- [ ] `make integration-test` starts from clean disposable stores and passes twice consecutively.
- [ ] `make reset-dev && make migrate && make seed` creates the documented reference world.
- [ ] Seed checksums or expected record counts/queries are stable.
- [ ] No integration test or reset command can affect production or ordinary developer data accidentally.

---

## 0I. Diagnostics, health, and continuous integration

### Diagnostics

- [ ] Add structured logging configuration with timestamp, level, logger, correlation ID, and event name.
- [ ] Generate/propagate correlation IDs through API responses and error contracts.
- [ ] Redact API keys, authorization headers, DSNs/passwords, manuscript content, prompt bodies, and provider responses by default.
- [ ] Replace broad swallowed initialization exceptions with explicit health/degradation state and actionable logs.
- [ ] Add minimal timing/result-count instrumentation around database/vector/provider adapters without logging content.
- [ ] Document development debug logging and its privacy limits.

### Health

- [ ] `/health/live` checks only that the process/event loop is alive.
- [ ] `/health/ready` verifies configuration, required migration versions, PostgreSQL, and Neo4j connectivity without writes.
- [ ] `/health/capabilities` reports optional vector/embedding/AI availability and reasons for degradation without secrets.
- [ ] Test healthy, required-store-down, migration-mismatch, and optional-provider-down cases.

### CI

- [ ] Add a pull-request workflow using pinned action versions.
- [ ] Run secret scanning, dependency install from locks, Ruff, formatting check, type-check scope, unit/application tests, and frontend lint/build.
- [ ] Run integration tests with disposable PostgreSQL/pgvector and Neo4j services in a separate job.
- [ ] Cache dependencies safely without caching `.env`, databases, prompts/responses, or manuscript fixtures outside approved synthetic data.
- [ ] Upload test/coverage/OpenAPI artefacts without secrets or manuscript content.
- [ ] Add concurrency cancellation for superseded branch runs.
- [ ] Keep live LLM tests excluded from ordinary CI.
- [ ] Add a scheduled/manual dependency and live-provider evaluation workflow later; do not require it for Phase 0 completion.

### 0I acceptance

- [ ] A clean pull request passes required static, unit, frontend, and integration jobs.
- [ ] Deliberate lint/test/migration/frontend failures correctly fail CI.
- [ ] Health endpoints accurately report dependency and capability states.
- [ ] Logs and CI artefacts pass redaction/secret checks.

---

## 0J. Documentation, evidence, and exit audit

### Documentation

- [ ] Update README quick start for PostgreSQL/pgvector, Neo4j, migrations, seeding, backend, and frontend.
- [ ] Update `.env.example` with every supported setting, safe placeholders, and required/optional notes.
- [ ] Update `AGENTS.md` commands, architecture map, test categories, and Phase 0 boundaries.
- [ ] Add migration authoring and verification guidance.
- [ ] Add manual-script guidance and remove stale Windows/path references.
- [ ] Document API v1 contracts and compatibility/deprecation notes.
- [ ] Update `plan/12-current-gap-analysis.md` to reflect the actual post-Phase-0 state.
- [ ] Mark completed FND backlog items and link their pull requests/commits.

### Exit evidence

- [ ] Record supported runtime and dependency-lock versions.
- [ ] Record unit/application/integration test counts and coverage baseline.
- [ ] Record clean setup time and exact commands used.
- [ ] Save CI run links for all required jobs.
- [ ] Save OpenAPI checksum/snapshot reference.
- [ ] Save PostgreSQL and Neo4j migration versions/checksums.
- [ ] Save reference-seed version/checksum and verification output.
- [ ] Record unresolved warnings, deferred defects, and Phase 1 prerequisites.

### Final clean-room rehearsal

- [ ] Clone into a new directory with no existing `.env`, databases, caches, or node modules.
- [ ] Configure from `.env.example` without tribal knowledge.
- [ ] Install from committed locks.
- [ ] Start infrastructure and verify health.
- [ ] Apply migrations and seed the reference world.
- [ ] Run unit and integration tests.
- [ ] Start API and generate OpenAPI.
- [ ] Install/build frontend.
- [ ] Stop infrastructure and confirm shutdown/cleanup behavior.
- [ ] Repeat migrate/seed/test to prove idempotency.

### Phase 0 exit gate

- [ ] Clean checkout setup is documented and repeatable.
- [ ] Unit/application tests require no PostgreSQL, Neo4j, embeddings, API keys, network, or persistent writes.
- [ ] Integration tests create, migrate, use, and destroy isolated test data.
- [ ] Imports perform no external connection, model load/download, or persistent write.
- [ ] Application lifecycle owns every external resource.
- [ ] PostgreSQL and Neo4j schemas are migration-controlled.
- [ ] Current graph/context/character/world prototype behavior is stable and documented.
- [ ] Known Phase 0 defects have regression tests or explicit deferrals.
- [ ] Reset/reseed is deterministic and production-safe.
- [ ] Required CI checks pass from a clean checkout.
- [ ] No real credentials, user prose, generated databases, or model responses are tracked or exposed in artefacts.
- [ ] Project owner accepts Phase 0 evidence and authorizes Phase 1.

---

## Recommended implementation slices

Keep pull requests small enough to review and revert independently.

1. **P0-01 — Baseline and toolchain decisions**
   - 0A decisions, locks, command corrections, CI skeleton without claiming green integration.
2. **P0-02 — Characterization and test isolation**
   - 0C layout, manual-script relocation, unit tests, fakes, import-side-effect test.
3. **P0-03 — PostgreSQL/pgvector development foundation**
   - Compose, settings, engine/session, Alembic, health, disposable fixture.
4. **P0-04 — Neo4j migration boundary**
   - Remove startup DDL, ordered migrations, verification, integration fixture.
5. **P0-05 — Application factory and resource lifecycle**
   - settings injection, container, lifespan, health/capabilities, no global clients.
6. **P0-06 — Ports and in-memory vertical slice**
   - graph/vector/AI/embedding/clock/ID/schema ports and one fully in-memory workflow.
7. **P0-07 — API v1 DTO and problem contracts**
   - stable representations, routers, exception mapping, OpenAPI snapshot.
8. **P0-08 — Graph/API prototype repairs**
   - FND-008.1–15 with regression tests.
9. **P0-09 — Character/world/vector prototype repairs**
   - FND-008.16–32 with fake-AI tests.
10. **P0-10 — Reference seed and safe reset**
    - deterministic manifest, reset guards, seed verification, repository contracts.
11. **P0-11 — CI integration and diagnostics**
    - real-store job, frontend build, logging/redaction, readiness/capabilities.
12. **P0-12 — Clean-room exit audit**
    - docs, rehearsal, evidence, backlog/status updates.

Parallel work is safe only where files and decisions do not overlap. P0-03 and P0-04 may proceed in parallel after P0-02. DTO work should wait until the application factory/ports establish stable ownership boundaries.

## Verification command target

By Phase 0 exit, the following should be sufficient from a configured clean checkout:

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

No command in this sequence may require a paid model call or a real provider key.
