# Phase 2 exit evidence

- Status: Implemented with named deferrals; owner acceptance pending
- Scope: Canonical story-state authority (ADR-012/013), Phase 2 workstreams 2A–2L
- Owner: project owner or designated technical lead
- Date: 2026-10-09

Authoritative canon is PostgreSQL. Neo4j/pgvector/search are rebuildable projections whose
freshness is proven through durable watermarks. This document records the concrete evidence.

## Runtimes and storage

| Component | Version |
| --- | --- |
| Python | 3.13 (CI 3.13) |
| PostgreSQL + pgvector | `pgvector/pgvector:0.8.0-pg17` |
| Neo4j | `neo4j:5.26.0` |
| Node.js / pnpm | 24 LTS / 11.26.0 |

## Migration inventory

PostgreSQL (Alembic) head: **`0018_commit_integrity`**. Revisions:
`0001_initial` → `0002_vector_documents` → `0003_embedding_provenance` → `0004_identity_and_sessions`
→ `0005_tenancy` → `0006_rate_limiter` → `0007_workspace_scope` → `0008_credentials` →
`0009_provider_usage` → `0010_vector_project_scope` → `0011_active_credential_unique` →
`0012_schema_versions` → `0013_canon_journal` → `0014_canon_storage` → `0015_outbox_delivery` →
`0016_canon_tags` → `0017_media` → `0018_commit_integrity`.

Neo4j ordered migrations and checksums:
- `0001_initial_graph_schema` — `e5ec799c5aff2b16d2019b5e00d4b9921e4e9c919e845c64cab0a88b004eba5c`
- `0002_entity_scope_identity` — `a32019c1ba0092b768dc64bddb181dd73d559682b4d745a61ad9d1efcd20899e`
- `0003_canon_scope_identity` — `447e2bff20a71cc03ad55f048c3fbde789a77eed7dda79bbdc605ab332c8bb6b`

## Contract and seed

- OpenAPI snapshot: `tests/contract/openapi_snapshot.json`, exact-checked by
  `tests/contract/test_openapi.py`. SHA-256 `48d9976b2100c1506a00a7a9d98894c79e214f962810399c5a17f1da7d7f1ce0`.
- Canon archive format: `novel-engine/canon-archive` version `3`. Same-scope operations retain
  checksums; cross-project restores deliberately rewrite scoped entity references and recompute the
  affected envelope checksums. Accepted mutation/review evidence is recreated in the target journal
  before commit (`tests/application/test_canon_archive.py`).
- Reference-world seed checksum:
  `43252e91465db7d0195bc521bad124ea263bd24285e62b3d34e21ef884c5aaa6` (27 nodes, 29 edges, 2 documents,
  4 queries).

## Test counts

- Hermetic (unit + application + contract): **423 passed** (`make test`), no services/network/keys.
- Integration (disposable PostgreSQL/pgvector + `neo4j-test`): **61 passed** (`make integration-test`).
- `make check` green (ruff lint + format, secret scan, `mypy backend` clean across 121 files, frontend
  build); `make audit` (pip-audit) clean.

## Canonical authority and commit protocol

- Canonical commit is a **single PostgreSQL transaction** (`PostgresCanonRepository.apply_commit`):
  head lock → authoritative validation → reservation → immutable envelope → normalized canon
  (entities/facts/beliefs/events/provenance/record-versions) → head advance + revision → idempotent
  outbox. Success does not depend on Neo4j.
- The **authoritative validator** runs inside that transaction; blocking integrity findings roll the
  transaction back and cannot be bypassed by services, imports, or direct repository writes
  (`tests/unit/test_canon_repository.py::test_apply_commit_runs_authoritative_validation_in_tx`,
  `tests/integration/test_canon_repository.py`).
- The transaction loads the exact published project schema and validates custom entity attributes,
  predicates/endpoints, lifecycle, all operation kinds, and in-batch cardinality. Model/import/
  derived/system mutation sets require an explicit approval; retries reuse a deterministic commit
  ID, and migration `0018_commit_integrity` enforces one envelope per mutation.
- **Replayable projection**: `GraphProjectionService` consumes committed envelopes in revision order
  into Neo4j and advances a durable `projection_watermarks` record; `OutboxConsumer.drain` marks
  delivery and reports lag. Neo4j failure produces visible lag, never canon corruption
  (`tests/unit/test_projection_service.py`, `tests/integration/test_projection.py`).

## Critical invariant matrix

| Invariant | Coverage |
| --- | --- |
| Tenant/project/workspace isolation | `tests/contract/test_workspace_scope.py`, `tests/integration/test_graph_repository.py`, `test_pgvector_adapter.py` |
| Schema versioning + compatibility | `tests/unit/test_schema_compiler.py`, `test_schema_compatibility.py`, `tests/application/test_schema_service.py` |
| Fact vs belief separation (no intrinsic belief truth) | `tests/unit/test_canon_domain.py`, `tests/contract/test_canon_assertions.py` |
| Cardinality + predicate/registry validation | `tests/contract/test_canon_assertions.py`, `tests/unit/test_canon_domain.py` |
| Entity retirement + active-reference rejection | `tests/unit/test_canon_domain.py`, `tests/unit/test_canon_repository.py`, `tests/contract/test_canon_entities.py` |
| Optimistic head / revision conflict | `tests/contract/test_canon_entities.py`, `tests/application/test_mutation_service.py`, `tests/integration/test_canon_repository.py` |
| Commit idempotency + checksum immutability | `tests/application/test_mutation_service.py`, `tests/unit/test_canon_repository.py`, `tests/integration/test_canon_repository.py` |
| Canonical-tx rollback on invalid commit | `tests/integration/test_canon_repository.py` |
| Projection replay/idempotency/rebuild + visible lag | `tests/unit/test_projection_service.py`, `tests/unit/test_outbox_consumer.py`, `tests/integration/test_projection.py` |
| Outbox lag (not corruption) | `tests/unit/test_outbox_consumer.py` |
| History / diffs / state-at-revision | `tests/unit/test_canon_history.py`, `tests/contract/test_canon_history.py` |
| Media identity + explicit evidence links | `tests/unit/test_media_service.py`, `tests/contract/test_media.py` |
| Archive round-trip + scope remapping + target preflight + tamper/traversal rejection | `tests/application/test_canon_archive.py` |
| Verification CLI (head/checksum/watermark) | `tests/unit/test_verify_cli.py` |

## Known limitations and deferrals

- **2H:** revert proposals (compensating mutation sets + intervening-conflict detection + preview)
  are not implemented; provenance source-artefact identifiers are recorded on beliefs
  (`source_ref`) but not generalized across all operations; explicit history/provenance authz/redaction
  tests remain thin.
- **2J:** the frontend is TypeScript with a client generated from the committed OpenAPI
  snapshot, React Router + TanStack Query with project-scoped cache keys, and an entity
  list/create/detail shell with history and projection status, plus a component test. Deferred:
  mutation preview/review UI, schema publish UI, media panels, automated accessibility coverage,
  and the end-to-end browser workflow.
- **2K:** no v1-archive upgrader (the graph-format `ProjectTransferService` v2 is untouched); archive
  import has deterministic retry identity but no project-level import-status/visibility ledger or
  explicit removal workflow, and it does not automatically rebuild projections or run
  representative-query comparison.
- **2L:** property/state-machine fuzzing and mutation testing are not adopted; Sentry spans/metrics
  and performance budgets are minimal; the reference world is not yet expanded with intentionally
  colliding IDs; migration tests from the full Phase-1 schema history and a clean-host rehearsal
  remain outstanding.
- **2E residue:** mutation-set state-machine enforcement, per-operation accept/edit/reject decisions,
  and commit telemetry remain deferred.

## Owner sign-off

- [ ] Project owner accepts the Phase 2 evidence and authorizes Phase 3.
