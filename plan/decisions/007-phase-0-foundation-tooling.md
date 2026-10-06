# ADR-007: Phase 0 foundation tooling

- Status: Accepted
- Date: 2026-10-05
- Owners: project owner, implementer
- Related backlog: FND-001, FND-005, FND-005A, FND-006, FND-007

## Context

The prototype has no dependency locks, runs infrastructure work at import time, mixes schema administration with application startup, and provides commands that can hide failures. Phase 0 needs one reproducible contributor contract before behavior is refactored. The chosen stack must support a hosted multi-tenant profile later without forcing Phase 0 to implement identity or production operations.

## Decision

### Runtimes and dependency tools

- Python 3.13 is the only supported Python line during Phase 0. The project declares `>=3.13,<3.14`, pins the local selector in `.python-version`, and uses Ruff's `py313` target. Python 3.14 will be evaluated only after the database, vector, ML, and observability dependencies pass the full suite on it.
- Node.js 24 LTS is the only supported Node line during Phase 0 and is selected by `.nvmrc`. The project does not support the local Node 26 installation merely because it happens to be present on one workstation.
- `uv` owns Python resolution, environment synchronization, and command execution. `pyproject.toml` is the dependency source and `uv.lock` is committed.
- `pnpm` owns frontend resolution. The repository pins the package-manager release in `frontend/package.json`, commits `frontend/pnpm-lock.yaml`, and uses frozen installs in automation.

### Persistence and migrations

- PostgreSQL uses SQLAlchemy 2 async sessions with psycopg 3, with Alembic as the explicit schema migration tool.
- Development starts on PostgreSQL 17. The Compose implementation will pin an exact pgvector image supporting the 0.8 extension series; hosted-provider compatibility is checked before production selection. Application code must not depend on extension features absent from the selected hosted service.
- Neo4j uses a small ordered project-owned migration runner. Applied migration IDs and content checksums are stored in Neo4j. Normal application startup verifies the expected version but never applies DDL.
- The existing Neo4j/Chroma state is prototype-only and disposable. There is no compatibility promise for it. Phase 0 will replace it with reproducible migrations and seed data.

### Application construction and determinism

- FastAPI is built by `create_app(settings, container)` and retains a conventional module-level ASGI export for servers.
- External resources are created and closed through FastAPI lifespan or explicit lazy adapters, never at module import.
- Application services receive clock and ID-generator ports. Production implementations use UTC and sortable UUIDv7-compatible identifiers; tests use fixed clocks and ordered deterministic IDs.
- Settings and infrastructure dependencies are explicit and replaceable through FastAPI dependency overrides.

## Alternatives considered

### Python 3.12 as the primary runtime

It has broad package compatibility, but Python 3.13 is already installed, supported, and provides a longer useful baseline. Supporting two versions before a hermetic test suite exists would multiply uncertainty without meaningful product coverage.

### Node.js 26 Current

It is installed on the initial workstation, but production contributors and CI should use an LTS line. Node 24 is the active LTS line and satisfies Vite 7's minimum runtime.

### pip requirements files and npm

Both can work, but they were not locked in this repository. `uv` and pnpm are already available, provide deterministic lock/frozen workflows, and avoid maintaining duplicate dependency sources.

### Startup-managed database schemas

This is convenient for a prototype but makes startup mutating, obscures migration failures, and cannot provide reviewable production change history.

### Framework-global singletons

They minimize wiring but currently cause network connections, local writes, and model initialization during imports. Explicit construction is required for hermetic tests and reliable lifecycle management.

## Consequences

### Positive

- Clean checkouts have one backend and one frontend install path.
- CI and contributors resolve the same dependency graphs.
- Import safety, deterministic tests, and disposable integration environments become enforceable.
- Persistence choices support both self-hosting and the accepted invite-only multi-tenant architecture.

### Negative

- Developers using other Python or Node releases must install the selected runtimes.
- Some heavy prototype dependencies remain in the default Python environment until import boundaries allow them to become optional.
- PostgreSQL, Alembic, Neo4j migrations, the application factory, and ID/clock ports still require implementation; this ADR does not claim they exist.

### Follow-up work

- Split local embedding/model packages from the default runtime after FND-008 removes import-time loading.
- Add SQLAlchemy, psycopg, Alembic, and pgvector dependencies with the P0-03 persistence slice.
- Implement the Neo4j migration ledger and verification command.
- Add CI runtime checks that fail when Python, Node, uv, or pnpm drift from the declared contract.

## Validation

Phase 0 must prove clean locked installs, a frozen frontend install, hermetic tests, explicit migrations against disposable services, safe application import, and a green CI matrix on Python 3.13 and Node 24. Revisit a runtime or tool only when upstream support, hosted-service compatibility, or measured contributor friction gives a concrete reason.
