# Current gap analysis

This is a point-in-time map of the prototype to the target design. It was rewritten after
Phase 0 stabilization (see `plan/phase-0-evidence.md`). Update it when a subsystem crosses
from prototype to planned architecture.

## Repository-wide

Current (post-Phase 0):

- application factory (`create_app(settings, container)`) with a dependency container and
  lifespan-owned external resources; imports perform no external work
- ports for graph, vector, embedding, AI, schema, clock, ID, artifacts, migrations, and
  database; Neo4j, pgvector, sentence-transformers, OpenAI, PostgreSQL, and system adapters
- explicit Alembic and ordered Neo4j migrations with read-only verification and checksum drift
  detection
- stable `/api/v1` DTOs and an `application/problem+json` error contract with correlation IDs
- deterministic hermetic tests (188) and disposable integration tests (16)
- reference-world seed manifest and fail-closed reset/seed commands
- structured, redacted logging; health/readiness/capabilities; CI with a disposable
  integration job

Remaining gaps to the target:

- no shared OIDC/session integration, invitations, or workspace/project scoping at persistence, cache, vector, job, or event boundaries
- no journal/outbox, encrypted hosted OpenRouter credential vault, or hosted workers
- legacy `/api` prototype routes remain as deprecated aliases pending migration/removal
- final fact/belief/event/branch domain redesign and the consistency engine are not started
- the parked world workflow (`backend/services/world_service.py`) is unreconciled

## Application lifecycle

Current:

- `backend/main.py` builds the container inside the lifespan, verifies Neo4j and PostgreSQL
  migration state in a `try/finally`, and closes resources on shutdown
- `app = create_app()` provides the conventional ASGI export without import-time side effects
- `backend/container.py` wires adapters from explicit settings

Target:

- OIDC/local identity adapters, invitation-derived workspace/project context, and per-request unit of work
- startup readiness that distinguishes required stores from optional providers (partially done)
- background worker lifecycle separate from the API process

## API and transport

Current:

- canonical `/api/v1` router with entity/edge/schema/context/character endpoints and a
  `rag/query` endpoint that returns 501
- `backend/api/problems.py` maps domain, validation, HTTP, AI, and unhandled errors to
  `application/problem+json`
- `CorrelationIdMiddleware` propagates/generates `X-Correlation-ID`
- legacy `/api` router still uses module singletons for graph and RAG

Target:

- remove or fully port legacy routes; enforce workspace/project scope on every endpoint
- cursor pagination, idempotency keys, and rate limiting
- streaming generation and review endpoints

## Graph, schema, and traversal

Current:

- `GraphManager` is lazy; no DDL at import or startup
- `Neo4jGraphRepository` maps Neo4j records to the stable `Node`/`Edge` domain shape and
  instruments calls
- schema registry rejects unknown types on writes, orders custom types deterministically, and
  persists sorted lists; `World` node type exists
- `build_traversal_query` is a characterized pure function; `ContextTraversal` runs on the
  repository port

Target:

- objective-fact vs character-belief separation enforced in traversal and context assembly
- branch/timeline-aware traversal with validity intervals
- pagination and streaming for large graphs

## Retrieval and vectors

Current:

- `PgVectorStore` implements the vector port over PostgreSQL/pgvector, embedding through the
  injected embedding port; the derived `vector_documents` table is rebuildable
- embeddings are optional and report honest availability/loaded state
- the legacy Chroma module remains but is unused by the container

Target:

- vector port filters (by type, project, branch), deactivation, and rebuild hooks
- hybrid keyword + semantic retrieval with provenance
- rebuild tooling and index versioning

## Character and world workflows

Current:

- character workflow runs on injected ports with a full temporary→durable ID map, edge
  endpoint rewriting, and validation of unknown types, duplicates, missing targets, and
  conflicting entities
- world workflow is parked and excluded from lint/format pending owner reconciliation
  (FND-008.23–27)

Target:

- world workflow repaired and port-injected
- mutation proposals validated against branch/timeline invariants and committed atomically
  (FND-008.21, Phase 1)
- review boundary between proposals and accepted canon

## Frontend

Current:

- default Vite React scaffold with lint and production build in CI

Target:

- product UI for graph browsing, context inspection, character/world authoring, and review

## Tests and scripts

Current:

- `tests/{unit,application,contract,integration,e2e}` with documented responsibilities and
  hermetic-by-default execution
- `scripts/manual/` holds non-hermetic manual scripts excluded from pytest collection
- `scripts/ci/secret_scan.py` provides the credential guard

Target:

- coverage floors per package, mutation testing for domain invariants, and recorded
  model-quality evaluations behind the `llm` marker

## Refactor order

Phase 1 should proceed in this order without reopening Phase 0 foundations:

1. OIDC/local identity ports, invitation lifecycle, and workspace/project scope primitives
2. workspace/project enforcement at every persistence and asynchronous boundary
3. journal/outbox and unit-of-work around canon mutations
4. fact/belief/event/branch domain redesign with validity intervals
5. mutation review and atomic application (FND-008.21)
6. consistency preflight/postflight harness (ADR-006)
7. legacy `/api` route removal once v1 parity is confirmed
