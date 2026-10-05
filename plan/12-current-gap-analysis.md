# Current gap analysis

This is a point-in-time map of the prototype to the target design. Update it when a subsystem crosses from prototype to planned architecture.

## Repository-wide

Current:

- small Python package with direct global singletons
- Python and frontend dependencies are now declared and locked by the in-progress P0-01 toolchain slice
- no CI, migrations, or formal API version
- broad line-ending churn across the worktree
- no PostgreSQL, tenant, identity, subscription, credit, credential-vault, or hosted-worker foundation

Target transition:

- stabilize line endings and tool commands
- enforce the selected runtimes and locked dependencies in CI and clean-room checks
- introduce injection seams before reorganizing packages
- introduce PostgreSQL and tenant scope before new durable product records
- add tests around current observable behavior
- migrate one vertical slice at a time

Do not start by moving every file. First make import and workflow boundaries testable; then relocate code as part of behavior changes.

## `backend/main.py`

Current:

- FastAPI app includes one router
- graph constraint creation occurs during lifespan, but the graph singleton already did work during import

Target:

- application factory with settings and dependency overrides
- infrastructure initialized/closed only in lifespan
- readiness endpoint and migration version verification
- versioned API router and consistent exception handling

## `backend/api/endpoints.py`

Current:

- graph/schema/RAG/character routes share one module
- endpoints call graph and AI services directly
- generic graph writes silently create schema types
- static node routes are declared after a dynamic `/{node_id}` route
- generic RAG answer generation is placeholder behavior

Target:

- routers by resource/workflow
- application commands/queries behind dependency injection
- versioned DTO and problem contracts
- project/branch scoping, pagination, concurrency, idempotency
- admin-only generic graph tools separated from product workflows

## `backend/graph/schema.py`

Current:

- useful initial enum vocabulary
- generic property bags
- process-global JSON custom schema registry
- edge temporal fields exist in models but are not persisted by the manager
- `World` is created by a service but is absent from `NodeType`

Target:

- domain-owned core schema and project-scoped extension schema
- declared properties, cardinality, endpoint types, time and visibility behavior
- migration/version support
- fact/belief/provenance models independent of Neo4j serialization

## `backend/graph/manager.py`

Current:

- one class combines lifecycle, migrations, commands, queries, search, and raw Cypher
- creates driver/index at import through a singleton
- catches startup failures, allowing partially degraded startup without a clear status
- inconsistent return shapes and direct driver values
- update does not reconcile labels; delete support is incomplete

Target:

- narrow project-scoped repositories implementing application ports
- separate migration/index administration
- explicit transaction and unit-of-work boundaries
- domain DTO mapping at the adapter boundary
- no raw Cypher in user-facing services

## Retrieval modules

Current:

- explicit markdown entity tags are a useful deterministic anchor
- traversal policies constrain relation sets and result counts
- semantic graph anchors use Neo4j's vector index
- score logic is heuristic; PageRank naming does not represent Graph Data Science PageRank
- no project/branch/timeline/visibility filtering
- context summary is an unbudgeted text formatter

Target:

- keep explicit entity mentions as one input format
- return a structured context manifest before rendering text
- apply deterministic visibility and temporal policy before semantic ranking
- version policies and score components
- introduce token budgets, deduplication, source traces, and evaluations

## `backend/core/vector_store.py`

Current:

- Chroma persistent client and one collection created globally
- minimal add/query wrapper
- no version lifecycle, scoping, filtering contract, deletion, or rebuild

Target:

- injected vector port with Chroma adapter
- versioned metadata contract and strict filters
- index outbox, lag, health, deletion, and deterministic rebuild
- content-hash embedding cache

## Character service

Current strengths:

- structured model outputs
- useful separation of stable anchor from appearance/personality/state/belief concepts
- explicit and semantic entity reuse attempts
- cached debug responses for development

Current defects/risks:

- graph reads are treated as nested `properties`, but the manager returns flattened properties
- init endpoint does not accept/store the bio expected by the CLI test
- post-processing rewrites node IDs without rewriting matching edge targets
- unknown output types are noticed but not rejected
- model, graph, filesystem cache, prompt assembly, and domain processing are coupled
- debug model responses are not a safe durable job/replay mechanism

Target:

- guided questionnaire as an application workflow
- provider adapter and versioned prompt
- typed proposal with temporary IDs and deterministic resolution
- review screen before mutation commit
- offline fake responses and separate live evaluations

## World service

Current:

- unfinished question-generation workflow
- undefined `explicit_ids` due to spelling mismatch and missing `json` import
- no API route or answer-to-graph flow
- duplicates character-service models and logic

Target:

- share generic guided-discovery and proposal infrastructure
- world-specific question policy/schema only
- deliver after canon/schema foundations, not as a second bespoke pipeline

## Frontend

Current:

- unmodified Vite/React starter
- no API integration, navigation, domain screens, or tests
- no signup/session, subscription, usage, BYOK, membership, or job-funding surfaces

Target transition:

1. adopt TypeScript, routing, generated API client, and server-state library
2. build account/session, subscription/usage, BYOK, and project-membership foundations
3. create project shell and error/job surfaces
4. build entity and character workflows
5. build story, consistency, and scene studio workflows
6. add branch/history and operational settings

## Tests and scripts

Current:

- endpoint tests call a separately running localhost API and persistent database
- context and character files are interactive/manual scripts but named as tests
- cleanup calls node-delete endpoints that do not exist
- the historical checked-in pytest output has been removed; there is not yet CI reporting to replace it

Target transition:

- preserve scripts under `scripts/manual/` if still useful
- create hermetic unit/application tests first
- use disposable integration infrastructure
- mark live provider evaluations explicitly
- publish current results through CI rather than checked-in output

## Refactor order

1. Characterize resolver/schema/DTO behavior.
2. Add PostgreSQL, ports, and an application factory; remove import side effects.
3. Establish tenant/project scope, identity seams, and a deterministic seed world.
4. Standardize API/domain representations.
5. Repair the existing character slice through injected fakes and repositories.
6. Add journal and fact/belief semantics.
7. Build new workflows on those foundations rather than extending current singleton services.
