# Phase 1 exit evidence

- Status: Evidence collected; project-owner acceptance, a clean-host deployment rehearsal, and the first hosted Phase 1 CI run pending
- Owner: project owner or designated technical lead
- Branch: `main` (Phase 0 merged as `8d7a599`; Phase 1 implemented on top)
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

- Hermetic suite (unit + application + contract): **317 passed** on the committed tree in this
  environment (`make test` / `make check`); no services, network, API keys, or persistent writes.
  See the deadlock note below for the reproducibility caveat.
- Integration suite (disposable PostgreSQL/pgvector + `neo4j-test`): **48 passed**
  (`make integration-test`).
- Hermetic line coverage baseline: **~71%** (`--cov=backend`), a regression floor only.
- Static/quality: `make check` (lock-check, lint, format, secret-scan, typecheck, hermetic
  tests, frontend lint/build) is green; `make audit` (pip-audit) runs in CI. `mypy` now covers
  the whole backend (`mypy backend`, 76 files), not a four-file subset.

## Migration versions and checksums

- PostgreSQL (Alembic) head: `0010_vector_project_scope`
  (`0004_identity_and_sessions`, `0005_tenancy`, `0006_rate_limiter`, `0007_workspace_scope`,
  `0008_credentials`, `0009_provider_usage`, `0010_vector_project_scope`).
- Neo4j ordered migrations: `0001_initial_graph_schema` and `0002_entity_scope_identity`
  (composite `(workspace_id, project_id, id)` entity uniqueness).

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
  project-write worker path for use and workspace-admin for management
  (`tests/unit/test_crypto.py`, `tests/application/test_credentials.py`,
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
## Post-review hardening (2026-10-06)

An external review reopened isolation/security claims. Each is now repaired with a regression
test (commits after the initial Phase 1 work):

- **Global schema authority:** schema listings require an authenticated session and mutations
  require CSRF plus platform-admin authority (`PLATFORM_ADMIN_USER_IDS`); anonymous callers can no
  longer read or mutate the shared schema.
- **Scoped entity identity:** Neo4j now uses a composite `(workspace_id, project_id, id)`
  uniqueness constraint (migration `0002_entity_scope_identity`), so the same id in two projects is
  a distinct node rather than a silent no-op with a false 201.
- **Provisioning:** hosted workspace creation requires platform-admin provisioning unless
  `ALLOW_WORKSPACE_SELF_CREATION` is enabled; local mode is trusted.
- **Deletion cascade:** workspace/project deletion purges Neo4j nodes, pgvector rows, credentials,
  usage records, object storage, and debug artifacts (`WorkspacePurgePort`), and project deletion
  is scoped to one project.
- **Invitation concurrency and email binding:** acceptance atomically claims the invitation
  (conditional `UPDATE ... RETURNING`); email-bound invitations reject accounts with no matching
  email; the rate limiter serializes per key with a transaction-scoped advisory lock.
- **Enforced BYOK pipeline:** AI endpoints require project-write authority plus CSRF; hosted calls
  resolve the workspace credential and refuse with 503 when none exists (no platform-funded
  inference); the AI port threads a per-call `api_key` and records provider usage.
- **Sentry completeness:** scrubbing removes exception frame locals, extras, and contexts, with
  `include_local_variables=False`.
- **KEK rotation:** retired KEKs (`CREDENTIAL_PREVIOUS_KEKS`) keep existing credentials decryptable
  after rotating `CREDENTIAL_KEK`.
- **Export/import:** project archives include each object's base64 bytes (import writes them back)
  and validate the whole archive before writing; graph import remains a sequential best-effort write
  (no cross-store journal/rollback) and object keys are regenerated on import.
- **Frontend:** the shell renders the nested `/auth/me` user.

A reported TestClient deadlock is **not reproducible on the committed tree**: `make check` and the
full hermetic suite complete in ~5–8 seconds here across `uv run pytest`, `python -m pytest`, closed
stdin, and file-redirected output; the cited test name
(`test_health_capabilities_include_provider_capabilities`) does not exist in this tree (the health
capabilities test is `test_health_capabilities`). Because an environment-specific hang cannot be
excluded, the gate now fails fast with diagnostics rather than hanging: Sentry is disabled whenever
pytest is running, `pytest-timeout` uses the **thread** method (the signal method does not interrupt a
protocol blocked in a native wait) with a 60s threshold, and `faulthandler_timeout = 60` dumps every
thread's stack. This converts any future hang into a labelled failure with a traceback.

## Post-review corrections (2026-10-06)

A second review reopened several claims; this pass fixed the blocking items and corrected the
overstated ones:

- **Hosted inference:** `OpenAIGateway` now builds a per-call client from the supplied key and base
  URL **before** touching the platform client, so hosted BYOK runs with no platform key; added
  `AI_PROVIDER`/`AI_BASE_URL` (OpenRouter `https://openrouter.ai/api/v1`, Ollama/llama.cpp-
  compatible), and capability reporting reflects the configured provider. Credential *use*
  (`resolve_active_for_use`) requires project-write, not workspace admin, so editors can generate.
- **Semantic retrieval:** the Neo4j vector query filters on the node returned by the index
  (`node.workspace_id`/`node.project_id`) instead of re-matching by id, so a colliding id in another
  project cannot contribute its score, and it deepens (bounded oversampling up to
  `SEMANTIC_MAX_CANDIDATES`) when a project has fewer than `top_k` scoped hits. This is a best-effort
  mitigation, **not a guarantee** — a global ANN index can still crowd a project out at the bound; an
  exact scoped path (pgvector with workspace/project filters) is the planned guarantee.
- **Export/import:** object bytes are carried, strict-base64-decoded, and verified against the recorded
  sha256/byte length on import; node `created_at` is preserved; already-present object checksums are
  skipped (idempotent retry). Graph writes remain sequential without a cross-store journal.
- **Type checking:** `mypy backend` (76 files) is clean and enforced; scope widened from four files.
- **Corrections:** the usage plan item is reopened (only successful calls recorded; latency, provider
  cost, attempts, failures, and idempotency not populated; estimates default to 0); workspace/project
  purge is documented as sequential with no journal/retry/compensation; project-export audit events
  remain absent.

**Still deferred/external:** first hosted CI run; project-owner acceptance; clean-host self-hosted
and hosted rehearsals; coordinated PostgreSQL/Neo4j/artifact backup tooling; live Authentik browser
round-trip; provider-side credential validation; interactive confirmation thresholds; job-runner
cancellation enforcement; project export/import audit events; PostgreSQL RLS decision.
