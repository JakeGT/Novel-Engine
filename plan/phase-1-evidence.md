# Phase 1 exit evidence

- Status: Evidence collected; project-owner acceptance and a clean-host deployment rehearsal pending
- Owner: project owner or designated technical lead
- Branch: `phase-1-stabilization` (Phase 0 merged as `8d7a599`)
- Date: 2026-10-06

This records the security and operational boundary for the invite-only deployment while
preserving a safe single-user self-hosted profile. Billing and platform-funded inference do
not exist.

## Supported runtimes

| Component | Version |
| --- | --- |
| Python | 3.13 |
| uv | 0.11.16 (locked `uv.lock`) |
| Node.js / pnpm | 24 LTS / 11.26.0 |
| PostgreSQL + pgvector | `pgvector/pgvector:0.8.0-pg17` |
| Neo4j | `neo4j:5.26.0` |

## Tests and coverage baseline

- Hermetic suite (unit + application + contract): **264 passed** (`make test`), no services,
  network, API keys, or persistent writes.
- Integration suite (disposable PostgreSQL/pgvector + `neo4j-test`): **43 passed**
  (`make integration-test`).
- Hermetic line coverage baseline: **71%** (`--cov=backend`), a regression floor only.
- Static/quality: `make check` (lock-check, lint, format, secret-scan, typecheck, hermetic
  tests, frontend lint/build) is green; `make audit` (pip-audit) runs in CI.

## Migration versions and checksums

- PostgreSQL (Alembic) head: `0009_provider_usage`
  (`0004_identity_and_sessions`, `0005_tenancy`, `0006_rate_limiter`, `0007_workspace_scope`,
  `0008_credentials`, `0009_provider_usage`).
- Neo4j ordered migration: `0001_initial_graph_schema`, checksum
  `e5ec799c5aff2b16d2019b5e00d4b9921e4e9c919e845c64cab0a88b004eba5c`.

## Clean deployment commands

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

Operational: `make diagnostics` (redacted bundle), `make retention` (purge expired ephemeral
rows), `make audit` (dependency scan). No step requires a paid model call or provider key.

## Authentik deployment runbook (no secrets)

- Host: `novel-engine.jaketalling.com` behind the portfolio reverse proxy (TLS terminated there).
- Issuer: `https://auth.jaketalling.com/application/o/novel-engine/` (exact, trailing slash).
- Application slug: `novel-engine`; client id: `h1KO202Mv2TjpI6zmXrneCQDpsyuIzDo8GYbAuDp`
  (confidential client; secret lives only in the deployment secret store).
- Scopes: `openid profile email`; ID token includes claims; `sub_mode=hashed_user_id`.
- Redirect allow-list (strict): `https://novel-engine.jaketalling.com/auth/callback`,
  `http://localhost:8000/auth/callback`, `http://localhost:5173/auth/callback`.
- Post-logout redirect: `https://novel-engine.jaketalling.com/`.
- Logout: RP-initiated; the Novel Engine host-only session is revoked locally, then the browser
  is sent to the Authentik end-session endpoint (portfolio-wide logout).

## Security matrix (implemented and tested)

- **Authentication:** OIDC authorization-code + PKCE with strict iss/aud/exp/signature/nonce
  validation (`tests/unit/test_oidc_adapter.py`); local single-user mode for self-hosting.
- **Sessions:** host-only `Secure`+`HttpOnly`+`SameSite=Lax` cookies, hashed at rest, revocation,
  and id rotation on TTL boundary or privilege change (`tests/application/test_session_rotation.py`).
- **CSRF:** server-validated double-submit token on cookie-authenticated mutations
  (`tests/contract/test_auth.py`, `test_tenancy.py`, `test_credentials.py`).
- **Authorization:** explicit owner/editor/viewer matrix centralized in `TenancyService`
  (`tests/unit/test_tenancy_policy.py`, `test_tenancy.py`).
- **Isolation:** cross-workspace **and** same-workspace cross-project isolation are enforced and
  adversarially tested — mandatory `workspace_id`+`project_id` across the graph repository, pgvector,
  the debug artifact store, and the immutable object store; cross-workspace/cross-project edges and
  reads refused; project-scoped export/import; the `JobEnvelope` contract carries the immutable scope
  (`tests/unit/test_scope_contract.py`, `tests/contract/test_workspace_scope.py`,
  `tests/integration/test_graph_repository.py`, `test_pgvector_adapter.py`, `test_object_store.py`,
  `test_job_envelope.py`). Caches and events are not implemented yet.

## Scope coverage

| Durable / derived record | Scope columns | Enforced by |
| --- | --- | --- |
| Neo4j `Entity` nodes and relationships | `workspace_id`, `project_id` properties | `GraphManager` (all reads/writes; migration-controlled constraints) |
| `vector_documents` (pgvector) | `workspace_id`, `project_id` (PK includes both + `id`) | migration `0010_vector_project_scope`, `PgVectorStore` |
| `users`, `external_identities`, `sessions`, `login_attempts` | global (identity) | identity store; not project-owned |
| `workspaces`, `memberships`, `projects`, `invitations`, `audit_events` | `workspace_id` (+ `project_id` on projects) | migration `0005_tenancy`, `PostgresTenancyStore` |
| `credentials`, `provider_usage` | `workspace_id` | migrations `0008`/`0009`; vault/usage stores |
| debug artifacts | `workspace_id`, `project_id` path namespace | `FilesystemArtifactStore` |
| product objects | `workspace_id`, `project_id` path namespace | `FilesystemObjectStore` (`ObjectStorePort`) |
| job envelope / idempotency | `workspace_id`, `project_id` fields | `JobEnvelope` (re-validated by workers before acting) |
| caches / events | — | not implemented in Phase 1; must adopt the same contract when added |
- **Credentials:** envelope-encrypted at rest (AES-256-GCM, KEK from config), metadata-only API,
  workspace-admin worker path (`tests/unit/test_crypto.py`, `tests/application/test_credentials.py`,
  `tests/integration/test_credential_vault.py`, `tests/contract/test_credentials.py`).
- **Rate limiting:** durable shared limiter for invitation acceptance
  (`tests/integration/test_rate_limiter.py`).
- **Threat/outage tests:** IdP outage, revoked-session replay, unknown callback state
  (`tests/application/test_auth_threats.py`); store degradation returns 503
  (`tests/contract/test_health.py`).
- **Redaction & hygiene:** structured-log redaction and `make secret-scan`
  (`tests/unit/test_logging_redaction.py`, `test_secret_scan.py`, `test_diagnostics.py`).

## Two-user workflow

`tests/application/test_tenancy_workflow.py` and `tests/contract/test_tenancy.py` cover invite →
authenticate → accept → project access → role change/removal → denied access, including revoked
and expired invitations, removed-inviter, and email-bound mismatch.

## Unresolved warnings, deferred items, and prerequisites

- **Deferred:** coordinated PostgreSQL/Neo4j/artefact backup tooling (procedure documented);
  clean-host self-hosted and hosted deployment rehearsals; provider-side credential validation;
  interactive confirmation thresholds; job-runner cancellation/idempotency enforcement; the live
  Authentik browser round-trip (requires the test user and a browser).
- **Warnings:** Pydantic `datetime.utcnow()` deprecation in domain `Node`/`Edge` defaults.
- **Prerequisites for Phase 2:** owner acceptance of this evidence, optional PostgreSQL RLS
  decision, and the canon mutation journal/temporal redesign.