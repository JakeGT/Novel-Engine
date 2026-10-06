# Phase 0 exit evidence

- Status: Evidence collected; clean-room rehearsal passed; hosted CI run pending
- Owner: project owner or designated technical lead
- Branch: `phase-0-stabilization`
- Date: 2026-10-05

This document records the concrete, reproducible evidence that Phase 0 stabilized the
prototype. It intentionally distinguishes verified facts from outstanding items.

## Supported runtimes and locked dependencies

| Component | Version | Source |
| --- | --- | --- |
| Python | 3.13 (verified 3.13.13) | `.python-version`, `pyproject.toml` |
| uv | 0.11.16 | `uv.lock`, CI `astral-sh/setup-uv@v10` |
| Node.js | 24 LTS | `.nvmrc`, `frontend/package.json`, CI `actions/setup-node@v7` |
| pnpm | 11.26.0 | `frontend/pnpm-lock.yaml`, CI `pnpm/action-setup@v6` |
| PostgreSQL + pgvector | `pgvector/pgvector:0.8.0-pg17` | `docker-compose.yml` |
| Neo4j | `neo4j:5.26.0` | `docker-compose.yml` |

Dependency installs are lock-enforced: `uv sync --locked` and
`pnpm install --frozen-lockfile`.

## Test counts and coverage baseline

- Hermetic suite (unit + application + contract): **188 passed** (`make test`), no
  services, network, API keys, model downloads, or persistent writes.
- Integration suite (disposable PostgreSQL/pgvector + `neo4j-test`): **16 passed**
  (`make integration-test`), run twice consecutively with isolated per-run databases.
- Hermetic line coverage baseline: **63%** (`--cov=backend`), used only as a floor to
  detect regressions, not as a completion target.
- Static checks: `make lock-check`, `make lint`, `make format-check`, `make secret-scan`,
  `make typecheck` (4 files), `make import-check`, `make frontend-lint`, `make frontend-build`
  all pass; `make check` is green locally.

## Clean setup commands

From a configured clean checkout the Phase 0 target sequence is:

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

No step requires a paid model call or a real provider key. `make seed` indexes prose
documents only when the embedding provider is available; graph seeding always runs.

## OpenAPI reference

- Canonical surface: `/api/v1`; deprecated prototype aliases remain at `/api`.
- Snapshot: `tests/contract/openapi_snapshot.json`, checked exactly by
  `tests/contract/test_openapi.py`.
- SHA-256 of the sorted, indented snapshot: `166a6304da9206ae8e6801312d8649ff88a372300b27300b88968c8876176ed4`.

## Migration versions and checksums

- PostgreSQL (Alembic) head: `0002_vector_documents` (previous: `0001_initial`).
  - `0001_initial`: enables the `vector` extension and creates `infrastructure_metadata`.
  - `0002_vector_documents`: creates the derived `vector_documents` table (dimension 384,
    HNSW cosine index) and its downgrade.
- Neo4j ordered migration: `0001_initial_graph_schema`.
  - Statements: entity `id` uniqueness constraint; `entity_embeddings` vector index
    (384 dimensions, cosine).
  - Checksum: `e5ec799c5aff2b16d2019b5e00d4b9921e4e9c919e845c64cab0a88b004eba5c`.
  - Verification (`verify()`) is read-only and never creates the ledger; drift fails startup
    and readiness.

## Reference seed

- Version: `2026-10-05.1`; fixed timestamp `2024-01-01T00:00:00Z`.
- Counts: 27 nodes, 29 edges, 2 documents, 4 expected queries, 16 node types, 16 edge types.
- Manifest checksum:
  `43252e91465db7d0195bc521bad124ea263bd24285e62b3d34e21ef884c5aaa6`.
- Applying `make seed` twice produces identical counts and checksum; `make reset-dev`
  followed by `make migrate && make seed` reproduces the world from an empty database.

## Redaction and secret hygiene

- Structured JSON logs include a correlation ID and redact API keys, bearer tokens, DSN
  passwords, and secret-named fields (`tests/unit/test_logging_redaction.py`).
- `make secret-scan` scans tracked/staged files and is clean (144 files checked at time of
  writing); `tests/unit/test_secret_scan.py` covers detection and allowlisting.
- CI uploads only `coverage.xml` and `openapi_snapshot.json`; no manuscript content or
  credentials are cached or uploaded.

## CI

- Workflow: `.github/workflows/foundation.yml` with jobs `secret-scan`,
  `python-foundation`, `frontend-foundation`, and `integration-tests`.
- Hosted run links: **pending** — no pull request has been opened against this branch yet.
- Local equivalents of every job step pass; the deliberate-failure acceptance checks are
  unverified until the first hosted run.

## Clean-room rehearsal

Performed on a copy of the working tree with no `.env`, `.venv`, caches, `node_modules`,
`data/`, or databases — only tracked/committed sources and the running Compose services:

- `make install-dev` — installed from `uv.lock` with `--locked`.
- `make migrate` — applied PostgreSQL head `0002_vector_documents`; Neo4j reported no pending
  migrations.
- `make reset-dev` — cleared the previous seed (27 entities, 0 vector documents, 4 ledger rows).
- `make seed` — applied version `2026-10-05.1`, checksum `43252e91…`, 27 nodes, 29 edges,
  `seed verification: ok`.
- `make test` — 188 hermetic tests passed; OpenAPI generated with 34 paths; `make import-check`
  printed `import ok`.
- `make frontend-install`, `make frontend-lint`, `make frontend-build` — frozen install, clean
  lint, successful production build.
- Repeating `make migrate && make seed && make test` reproduced the same counts, checksum, and
  188 passing tests, proving idempotency.
- Only warning observed: the local workstation runs Node 26 while CI pins Node 24.

## Unresolved warnings, deferred defects, and Phase 1 prerequisites

- **Warnings:** Pydantic emits a `datetime.utcnow()` deprecation warning from the domain
  `Node`/`Edge` defaults; the local workstation runs Node 26 while CI pins Node 24.
- **Deferred defects:**
  - FND-008.21 — atomic mutation application (Phase 1 mutation review).
  - FND-008.23–27 — world workflow, blocked on the parked, user-owned
    `backend/services/world_service.py`; that file is excluded from Ruff until reconciled.
  - Edge temporal validity (`valid_from`/`valid_until`) is persisted but not yet enforced
    or exposed in the v1 contract.
  - Vector port filters, deactivation, and rebuild hooks remain deferred; the legacy Chroma
    module is retained but unused by the container.
  - Legacy `/api` routes remain as deprecated aliases and must be migrated or removed.
- **Phase 1 prerequisites:** tenant/project scoping at every boundary, journal/outbox,
  credit reservation, encrypted BYOK credentials, mutation review, and the consistency
  preflight/postflight harness.