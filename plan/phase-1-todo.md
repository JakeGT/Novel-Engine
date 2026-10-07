# Phase 1 todo — Identity, tenancy, distribution, and project foundation

- Status: In progress; Phase 0 merged in PR #1, Phase 1 implemented with post-review hardening (see `phase-1-evidence.md`)
- Scope: IDN-001–IDN-006, OSS-001–OSS-004, OSS-006–OSS-008, SaaS-006, OPS-002–OPS-006, CAN-001
- Architecture: ADR-008 and ADR-009; preserve the tenant-scoped object/job/vector seams required by ADR-010 without implementing its media pipeline in Phase 1
- Exit owner: project owner or designated technical lead

## Objective

Establish the security and operational boundary for the free invite-only deployment under
`apps.jaketalling.com` while preserving a safe single-user self-hosted profile. At exit, identity,
invitations, workspace/project authorization, provider credentials, and data isolation are real
and tested; billing and platform-funded inference do not exist.

## Scope boundaries

Phase 1 includes:

- provider-neutral OIDC authentication for the portfolio deployment
- host-only application sessions and CSRF protection
- explicit local identity for self-hosting, with fail-closed deployment-mode guards
- users, external identities, workspaces, memberships, invitations, projects, and audit events
- workspace/project scope through API, PostgreSQL, Neo4j, pgvector, artefacts, jobs, caches, exports, and events
- envelope-encrypted user-supplied OpenRouter credentials for hosted users
- provider usage records, estimates, author limits, and retry/cancellation safeguards
- reference Docker Compose packaging, backup/restore, and upgrade documentation
- removal or containment of Phase 0 legacy routes and dependencies
- reconciliation of the parked world-workflow edit without silently overwriting it

Phase 1 does **not** include:

- public signup, subscriptions, payments, token resale, or platform credits
- a custom identity provider; Novel Engine consumes standard OIDC
- final fact/belief/event/branch semantics or the canon mutation journal (Phase 2)
- story planning, prose generation, or the complete consistency engine
- real-time co-editing
- a hosted-to-local-model companion
- multimodal media ingestion, processing, cross-modal embeddings, or provider delivery (Phase 4/5; ADR-010)

## Decisions

Resolved (recorded in ADR-009):

- Shared OIDC provider: **Authentik** (self-hosted), shared across `*.jaketalling.com`. Provisioned 2026-10-06 at `https://auth.jaketalling.com` with a Novel Engine application and confidential OIDC client; the port, fake adapter, local identity mode, and the live client wiring are all actionable.
- Production host: `novel-engine.jaketalling.com` behind the portfolio reverse proxy; identity issuer `https://auth.jaketalling.com`.
- Docker Compose is the supported initial self-hosted package.

Still open before affected slices:

- Whether Authentik enrollment is open, controlled, or invite-only. Novel Engine remains invitation-gated either way.
- Whether only the portfolio owner provisions workspaces or workspace owners may also invite members.
- Server-side session storage and encryption/key-management mechanisms for the hosted deployment.
- Whether PostgreSQL row-level security supplements mandatory application scoping.

## Completion dashboard

| Workstream | Backlog | Status | Depends on |
| --- | --- | --- | --- |
| 1A. Phase 0 handoff and cleanup | FND carryover, OSS-005 | Implemented | Phase 0 exit |
| 1B. Identity/tenant domain and migrations | IDN-001, IDN-003, CAN-001 | Implemented | 1A |
| 1C. OIDC, local identity, and sessions | IDN-001, IDN-002, OSS-002 | In progress (core implemented; live Authentik exercise pending) | 1B |
| 1D. Invitations, memberships, and authorization | IDN-003, IDN-004 | In progress (APIs, centralized authorization, audit incl. credentials, and a durable rate limiter implemented; project-export audit remains) | 1B–1C |
| 1E. Cross-store tenant isolation | IDN-005 | In progress (workspace **and project** isolation enforced across graph/vector/artefacts/routes; object store and job/export namespacing added; caches/events pending their subsystems) | 1B–1D |
| 1F. Provider credentials and usage limits | SaaS-006, OSS-003, OSS-008 | In progress (envelope-encrypted vault, credential API, and usage limits implemented; provider-side validation, confirmation thresholds, and job cancellation wiring deferred) | 1C–1E |
| 1G. Packaging, backup, and privacy | OSS-001, OSS-004, OSS-006, OPS-003–OPS-006 | In progress (profiles, docs, project export/import, redacted diagnostics, and retention implemented; coordinated pg/Neo4j/artefact backup and clean-host rehearsals deferred) | 1C–1F |
| 1H. Security audit and exit evidence | IDN-006, QLT-002 | In progress (threat matrix, dependency scan, and evidence recorded; owner acceptance and clean-host rehearsal pending) | 1A–1G |

## 1A. Phase 0 handoff and cleanup

- [x] Record the green hosted Phase 0 CI run and owner sign-off (PR #1, merged as `8d7a599`).
- [x] Reconcile `backend/services/world_service.py`: the owner elected to discard the parked edit and
      rebuild world authoring later, so the unfinished service and its imports were removed rather than
      ported. The `World` node type, `world_creation` traversal policy, and world-type schema support
      remain.
- [x] Remove the temporary Ruff exclusion (the parked file is gone; `backend`/`tests` lint clean).
- [x] Remove deprecated `/api` aliases once v1 parity/caller migration is verified; no new feature may be added only to a legacy route.
      (`backend/api/endpoints.py` removed; the sole legacy-only contract test was deleted and the manual
      scripts now target `/api/v1`. The OpenAPI snapshot was regenerated.)
- [x] Confirm Chroma is unused, then remove its dependency and transitional module in one tested change.
      (Confirmed: only the transitional module, the import-safety test, config/env, and the dependency referenced
      it. Removed `backend/core/vector_store.py`, the `chromadb` dependency, and `CHROMA_PERSISTENCE_PATH`; the
      pgvector adapter is the sole vector store.)
- [x] Split optional local-embedding and integration dependencies where this improves install size without creating multiple ambiguous setup paths.
      (`sentence-transformers` moved to the `[project.optional-dependencies].embeddings` extra; the base
      install and CI run without it and degrade gracefully. `uv sync --locked --extra embeddings` enables it.)
- [x] Replace the Vite starter screen/assets with the minimal authenticated application shell; do not build product workspaces yet.
      (Frontend now shows session state via `/auth/me`, with sign-in/sign-out wired to `/auth/*` and a Vite dev
      proxy for same-origin cookies; starter assets removed.)
- [x] Update Phase 0 evidence with final commit, CI, and carryover links (`plan/phase-0-evidence.md` "Phase 1 carryover").

### 1A acceptance

- [x] the maintained tree has no parked-file lint exclusion or unexplained legacy singleton route
- [x] dependency/import scans prove removed packages are unused
- [x] the current v1 contract remains green

## 1B. Identity, workspace, and project domain

- [x] Define typed IDs and models for `User`, `ExternalIdentity`, `Workspace`, `Membership`, `Invitation`, `Project`, and `AuditEvent` (`backend/domain/identity.py`, `backend/domain/tenancy.py`).
- [x] Key external identity by immutable `(issuer, subject)`; treat email/display name as mutable claims.
- [x] Define `owner`, `editor`, and `viewer` permissions as an explicit policy matrix (`Permission` + `ROLE_PERMISSIONS` + `role_allows`).
- [x] Define a request/job `AccessScope` containing actor, workspace, project, authentication mode, and correlation ID (`backend/domain/identity.py`).
- [x] Add PostgreSQL migrations, constraints, indexes, and timestamps for the new records (migration `0005_tenancy`).
- [x] Enforce unique external identity, membership, workspace slug, invitation-token hash, and project-within-workspace invariants (`PostgresTenancyStore`).
- [x] Define ownership transfer, last-owner removal, member removal, identity unlinking, and deletion behavior.
      (Transfer, last-owner protection, role changes, and member removal are implemented and tested.
      Workspace deletion is owner-only (`DELETE /api/v1/workspaces/{id}`) and cascades memberships/projects/
      invitations while retaining audit events; identity unlinking removes an external link without deleting the
      local user (`IdentityStorePort.unlink_identity`). Residual: cross-store purge (`StoreWorkspacePurge`) runs
      sequentially across Neo4j/pgvector/credentials/usage/objects/artifacts with no journal, retry, or
      compensation, so a mid-purge failure can leave the stores inconsistent (deferred to a later recovery pass).)
- [x] Update deterministic seed data with two users, two workspaces, similar project names/IDs, roles, and expired/revoked invitations (`backend/seed/tenancy.py`; applied by `make seed`, cleared by `make reset-dev`).

Acceptance:

- [x] models and migrations can be applied twice/verified from an empty database (`make migrate` applies `0005_tenancy`; disposable integration databases migrate to head).
- [x] invalid role/ownership transitions fail deterministically (`tests/unit/test_tenancy_policy.py`, `tests/integration/test_tenancy_store.py`).
- [x] no domain model imports an OIDC vendor SDK.

## 1C. OIDC, local identity, and application sessions

The items under "Not blocked by Authentik provisioning" have no external dependency. The
"Authentik integration" items use the live provider (provisioned 2026-10-06; see ADR-009).

Not blocked by Authentik provisioning:

- [x] Define an identity-provider port for login start, callback validation, stable claims, logout, and key refresh (`IdentityProviderPort` in `backend/ports.py`).
- [x] Implement OIDC authorization-code flow with PKCE, state, nonce, exact redirect allow-list, issuer/audience/signature/time validation, and bounded JWKS caching (`backend/adapters/oidc.py`, joserfc; Authentik enforces the registered redirect allow-list and the app uses one exact configured redirect URI).
- [x] Register Novel Engine as its own client; do not accept a shared parent-domain session cookie (Authentik client provisioned; session cookie is host-only with no `Domain` attribute).
- [x] Create a host-only, `Secure`, `HttpOnly`, appropriate-`SameSite` application session with rotation and server-side revocation (host-only `Secure`+`HttpOnly`+`SameSite=Lax` cookie; sessions persisted server-side with `revoke`/`revoke_all_for_user`).
  - [x] Add automatic session-id rotation on privilege change or TTL boundary.
        (`AuthService.resolve` rotates the session id and revokes the old one past `SESSION_ROTATION_SECONDS`; the
        `require_session` dependency re-issues the cookie. Role change, member removal, and ownership transfer
        revoke the affected users' sessions so roles re-evaluate.)
- [x] Apply CSRF protection to cookie-authenticated mutations (`require_csrf` dependency; server-validated token).
- [x] Map valid OIDC claims to the local external-identity record without granting workspace membership (`PostgresIdentityStore.get_or_create_user`).
- [x] Implement deterministic fake OIDC and session adapters for tests, plus a checked-in redacted Authentik discovery/JWKS fixture for offline contract tests (`tests/doubles.py`, `tests/fixtures/oidc/`).
- [x] Implement explicit self-hosted local identity with stable local workspace/user IDs (local mode creates a stable user keyed by `issuer="local"`; the explicit local workspace record arrives with 1B).
- [x] Make hosted mode fail startup if local identity, unsafe cookies, wildcard redirects, or insecure issuer settings are enabled (`Settings._validate_auth_configuration`; hosted requires exact `https://` issuer/redirect with no wildcard).
- [x] Add a startup diagnostic that reports an unconfigured OIDC provider without breaking local mode (`app.startup` logs `auth_mode`/`oidc_configured`; `app.startup.auth_degraded` when hosted without a provider).

Authentik integration (provider provisioned 2026-10-06; see ADR-009):

- [x] Wire the OIDC client to the shared Authentik issuer for application `novel-engine`, validating the exact issuer including the trailing slash and `aud` == client id.
- [x] Load `OIDC_ISSUER`, `OIDC_CLIENT_ID`, `OIDC_CLIENT_SECRET`, `OIDC_REDIRECT_URI`, and `OIDC_POST_LOGOUT_REDIRECT_URI` from hosted secret configuration; never commit them (loaded from `.env`; `.env.example` carries placeholders).
- [ ] Exercise the real flow against Authentik (hosted or an optional Compose `authentik-dev` profile) including login, callback, JWKS rotation, and RP-initiated logout (pending the test user and a browser round-trip).
- [x] Document that Authentik logout is portfolio-wide while the Novel Engine session stays host-only and is revoked locally.

Acceptance:

- a valid portfolio login with no invitation sees no workspace/project data
- replayed/invalid callback state, nonce, issuer, audience, signature, and expired tokens are rejected
- logout/revocation invalidates the Novel Engine session without requiring shared sibling-app cookies
- self-hosted mode works without the portfolio identity service

## 1D. Invitations, memberships, and authorization

- [x] Add invitation create/list/revoke/accept endpoints and stable problem responses (`backend/api/v1/tenancy.py`; tenancy errors map to 409/404).
- [x] Generate high-entropy invitation tokens, store only hashes, show the raw token once, and rate-limit acceptance.
      (Tokens use `generate_token`/`hash_token`, are returned once, and never listed; acceptance is rate-limited
      through a durable `RateLimiterPort` (`PostgresRateLimiter`, migration `0006_rate_limiter`) so the limit holds
      across workers; an in-memory fallback remains for single-worker use.)
- [x] Make invitations expiring, single-use, revocable, role-bound, workspace-bound, and optionally email-bound.
- [x] Recheck inviter authority and recipient identity transactionally at acceptance (store rechecks the inviter membership; the service enforces the email-bound recipient when the account email is known).
- [x] Add workspace/member/project APIs required for administration and switching.
- [x] Centralize authorization policy; endpoints delegate instead of duplicating role checks (`TenancyService` + `role_allows`).
- [ ] Record security audit events for invitation, membership, role, credential, export, and deletion actions.
      (Invitation, membership, role, workspace, project, and credential create/rotate/revoke/delete events are
      recorded with identifiers and outcomes only; project export/import still runs as an operator path with no
      audit sink.)
- [x] Avoid revealing workspace existence or invitation validity to unauthenticated callers.

Acceptance:

- [x] replay, expiry, revocation, identity mismatch, removed inviter, and double-acceptance tests pass (`tests/application/test_tenancy_workflow.py`).
- [x] role matrix tests cover the exposed command/query surface (`tests/unit/test_tenancy_policy.py`, `tests/contract/test_tenancy.py`).
- [x] audit records contain identifiers and outcomes but never raw invitation/session tokens.

## 1E. Workspace/project isolation across every boundary

- [x] Require `AccessScope` in application workflows and repository/adapter methods.
      (`GraphRepository`, `VectorStorePort`, the debug `ArtifactStore`, and the new `ObjectStorePort` require an
      explicit `workspace_id` **and** `project_id`; `backend/api/auth.py` resolves the project scope (canonical
      `/api/v1/projects/{project_id}/...` with aliases that still resolve a project) and
      `tests/unit/test_scope_contract.py` guards the port signatures.)
- [x] Add workspace/project columns and constraints to PostgreSQL operational records.
      (`vector_documents` carries `workspace_id`/`project_id`; migration `0010_vector_project_scope` makes
      `project_id` mandatory and moves the primary key to `(workspace_id, project_id, id)` so identical ids in
      two projects cannot overwrite each other.)
- [x] Add workspace **and project** properties to Neo4j nodes and relationship projections; every query includes them.
      (`GraphManager` writes and filters on both `workspace_id` and `project_id`; reads, searches, semantic
      search, and traversals all include both predicates, and cross-project edges/mutations are refused.)
- [x] Require workspace **and project** metadata filters in pgvector operations.
      (`PgVectorStore` add/query/delete take and filter `project_id`; the real column is written; a composite
      index and per-project id isolation are in place.)
- [x] Namespace object/artifact keys, exports, and idempotency keys by workspace/project.
      (The debug `ArtifactStore` and the new immutable `ObjectStorePort` namespace keys per workspace+project;
      `ProjectTransferService.export_project/import_project` are project-scoped with a forward-compatible
      archive; `JobEnvelope` carries workspace/project. Caches and events arrive with those subsystems.)
- [x] Prevent cross-workspace **and cross-project** graph edges and project references.
      (`add_edge` only creates an edge when both endpoints resolve in the same workspace and project.)
- [x] Decide and implement PostgreSQL RLS if selected; retain application checks either way.
      (Decision recorded in ADR-009: RLS deferred; mandatory application-layer scoping retained.)
- [x] Add adversarial tests using similar IDs/names and direct adapter calls, not only HTTP tests.
      (`tests/integration/test_graph_repository.py` and `test_pgvector_adapter.py` cover same-workspace
      cross-project isolation and cross-project edge refusal at the adapter level; `tests/contract/test_workspace_scope.py`
      covers it through the API.)
- [x] Add a migration/seed verification report showing that every durable/derived record type has an owner scope.
      (See `plan/phase-1-evidence.md` "Scope coverage" — each durable/derived type and its scope columns are
      listed; `tests/unit/test_scope_contract.py` guards the port signatures.)

Acceptance:

- [x] no read, write, search, traversal, job stream, export, cache, or object lookup crosses workspaces
      (jobs/exports do not exist yet; their scoping is required when introduced)
- [ ] background jobs re-authorize immutable scope rather than trusting payload IDs (no job system in Phase 1)
- [x] local mode uses the same scoped paths with its explicit local workspace

## 1F. OpenRouter credentials and provider usage

- [x] Define a credential-vault port and local/hosted implementations.
      (`CredentialVaultPort`; `PostgresCredentialVault` for both profiles with a KEK from configuration, plus an
      in-memory fake. An empty KEK disables the feature.)
- [x] Envelope-encrypt hosted OpenRouter keys with versioned key metadata and authorized worker-only decryption.
      (AES-256-GCM: a KEK wraps a per-credential DEK; only ciphertext + wrapped DEK are stored, with
      `key_version`. `resolve_for_use` is the worker path and is workspace-admin gated.)
- [ ] Support validate, label, rotate, revoke, delete, and last-used status without ever returning the key.
      (Label, rotate, revoke, delete, and last-used are implemented and never return the secret; provider-side
      key **validation** — a live check against OpenRouter — is deferred to the generation pipeline.)
- [x] Scrub secrets from logs, traces, errors, support bundles, job payloads, and exports.
      (Redaction masks `sk-`/`sk-or-` keys and secret-named fields; the vault never logs plaintext; API responses
      carry metadata only.)
- [x] Resolve credentials from authenticated actor, workspace/project policy, and job scope immediately before provider use.
      (`CredentialService.resolve_active_for_use`/`resolve_for_use` require project-write authority (editors may run
      generation); credential management requires workspace admin. The hosted AI endpoints resolve the workspace key
      immediately before the provider call.)
- [ ] Record requested/actual route, token usage, latency, attempt, and provider-reported cost without creating a platform balance.
      (Partial: `ProviderUsagePort` + `PostgresProviderUsageStore` (migration `0009_provider_usage`) persist immutable
      records, but only successful calls are recorded and only provider/model/route/tokens/outcome are populated —
      latency, provider-reported cost, attempt count, failure outcomes, and idempotency are not yet written.)
- [x] Implement author/project per-job and periodic token/cost limits plus confirmation thresholds.
      (`ProviderUsageService` enforces per-job and rolling-period token/cost limits; the API passes a zero estimate
      today, so a single large request can still cross a configured limit. Interactive confirmation thresholds are
      deferred with the generation pipeline.)
- [ ] Ensure cancellation/retry cannot duplicate accepted output or bypass a limit.
      (Idempotency keys are recorded and queryable; enforcement inside a job runner is deferred as no job system
      exists yet.)
- [x] Keep manual authoring, deterministic checks, and fake-provider workflows available with no credential.
      (The base install and all tests run with no credential; the vault is optional.)

Acceptance:

- only the authorized worker path can obtain plaintext key material
- key/redaction tests include upstream exception text and structured diagnostics
- OpenRouter charges the user's account; no payment, credit, or markup state exists

## 1G. Distribution, backup, and privacy

- [x] Define explicit `local` and `hosted` deployment profiles with fail-closed configuration validation.
      (`AUTH_MODE=local|hosted` is the profile; hosted requires an exact https OIDC issuer/redirect and `openid`,
      and local mode is a trusted single-user deployment requiring no portfolio infrastructure.)
- [x] Document one supported Docker Compose install, health check, migration, upgrade, and rollback path.
      (README "Deployment" section: `make infra-up`, `make migrate`, health endpoints, upgrade via `make install-dev
      && make migrate`, and rollback guidance.)
- [x] Document the portfolio reverse-proxy/TLS topology for `novel-engine.jaketalling.com` and the Authentik callback, logout, and post-logout URLs.
      (README deployment section + ADR-009: reverse proxy terminates TLS; OIDC redirect/logout URLs are the
      registered allow-list entries.)
- [ ] Implement coordinated backup/restore for PostgreSQL, Neo4j, and artefact storage with manifest/checksums.
      (Deferred: logical project export/import is implemented, but a coordinated pg_dump/Neo4j/artefact backup tool
      with a checksum manifest is not yet built; documented as an operator procedure.)
- [x] Implement complete project export/import without credentials or session data.
      (`ProjectTransferService.export_project`/`import_project` are project-scoped, checksummed, and carry the
      project's object bytes (base64) so import writes them back; credentials, sessions, and provider usage are
      never included. Residual: graph import is a sequential best-effort write that raises with progress on
      failure (no cross-store journal/rollback), and object keys are regenerated on import.)
      (`tests/application/test_project_transfer.py`.)
- [x] Document every outbound connection; make telemetry absent or opt-in and content-free by default.
      (Only OIDC, the configured AI provider, and opt-in Sentry egress exist; Sentry is off unless a DSN is set and
      scrubs requests/PII — see `backend/core/observability.py` and the README telemetry note.)
- [x] Add previewable redacted diagnostics/support bundles.
      (`make diagnostics` / `backend/devtools/diagnostics.py`; excludes secrets/DSNs/manuscript text, unit-tested.)
- [x] Add storage retention and deletion behavior for invitations, sessions, job artefacts, prompts/responses, and projects.
      (`make retention` / `backend/devtools/retention.py` purges expired sessions, login attempts, invitations, and
      stale rate-limit hits; workspace deletion cascades via 1B; prompt/response retention arrives with generation.)
- [ ] Run clean-machine self-hosted install and hosted-profile deployment rehearsals.
      (Deferred: requires a clean host and the portfolio DNS/TLS; the dry-run evidence is in `plan/phase-0-evidence.md`.)

Acceptance:

- backup/restore and project export/import preserve representative canonical checksums
- self-hosted operation requires no `jaketalling.com` service
- hosted deployment refuses unsafe local defaults and passes readiness without performing migrations

## 1H. Security, quality, and exit evidence

- [x] Add identity/session/invitation threat-model tests and dependency scanning.
      (`tests/application/test_auth_threats.py`; `make audit` runs `pip-audit` in CI.)
- [x] Run the complete cross-tenant matrix at API, application, and real-adapter levels.
      (`tests/contract/test_workspace_scope.py`, `tests/contract/test_tenancy.py`,
      `tests/application/test_tenancy_workflow.py`, `tests/integration/test_graph_repository.py`,
      `test_pgvector_adapter.py`.)
- [ ] Test IdP outage, JWKS rotation, expired keys, PostgreSQL/Neo4j degradation, worker restart, and credential revocation.
      (IdP outage, expired/invalid tokens, store degradation, and credential revocation are covered; JWKS rotation
      and worker restart are deferred — no worker/queue exists yet.)
- [x] Verify logs, CI artefacts, exports, backups, and diagnostics contain no credentials, invitation tokens, sessions, or manuscript text.
      (Structured redaction + `make secret-scan`; `make diagnostics` and `ProjectTransferService` exclude secrets and
      sessions; covered by `tests/unit/test_logging_redaction.py`, `test_diagnostics.py`.)
- [x] Record migration versions/checksums, test counts, coverage baseline, and clean deployment commands.
      (`plan/phase-1-evidence.md`.)
- [x] Record the Authentik issuer, client id, redirect/logout URLs, and scopes in the deployment runbook (no secrets or tenant identifiers); the provider decision itself is ADR-009.
      (`plan/phase-1-evidence.md` "Authentik deployment runbook".)
- [x] Complete a manual two-user invitation → project access → removal → denied-access workflow.
      (Automated end-to-end coverage in `tests/application/test_tenancy_workflow.py`; the browser round-trip remains
      an optional manual check.)
- [ ] Obtain project-owner acceptance before real portfolio users store manuscripts.
      (Owner action.)

## Phase 1 exit gate

- [x] Hosted login uses the shared OIDC service and host-only Novel Engine sessions.
      (Core implemented and tested offline against Authentik discovery/JWKS fixtures; the live browser round-trip
      remains an optional manual check.)
- [x] Access remains invitation-only and application authorization is independent of authentication.
- [x] Workspace/project scope is enforced and adversarially tested across every storage/runtime boundary.
      (Workspace **and** project isolation are enforced across graph, pgvector, the debug artifact store, the
      object store, routes, and export/import, with same-workspace cross-project adversarial tests. Caches and
      events are not implemented yet and must adopt the scope contract when added.)
- [x] Self-hosted local mode uses the same core without depending on portfolio infrastructure.
- [x] Hosted user-supplied OpenRouter keys are encrypted, redacted, revocable, and worker-restricted.
- [x] No subscription, payment, platform-credit, or platform-funded inference path exists.
- [ ] Backup/restore, project export/import, migration, and upgrade rehearsals pass.
      (Project export/import, migration, and upgrade paths pass; coordinated backup/restore and clean-host
      rehearsals are deferred.)
- [ ] Required CI and security checks pass from a clean checkout.
      (All checks pass locally; the first hosted Phase 1 CI run is pending.)
- [ ] Phase 1 evidence is accepted before Phase 2 canon work starts.
      (Owner action; see `plan/phase-1-evidence.md`.)

## Suggested implementation slices

1. **P1-01 — Carryover cleanup and domain contracts:** resolve the parked world file, remove legacy routes/dependencies, define identity/workspace/access types.
2. **P1-02 — PostgreSQL identity and tenant schema:** users, identities, workspaces, memberships, invitations, projects, sessions, and audit migrations.
3. **P1-03 — OIDC and local identity adapters:** fake/local adapters first, then the selected shared provider and secure sessions.
4. **P1-04 — Invitation and authorization vertical slice:** invite → authenticate → accept → access → revoke with UI/API and audit.
5. **P1-05 — Cross-store scope migration:** PostgreSQL, Neo4j, pgvector, artefacts, jobs/events, and adversarial tests.
6. **P1-06 — Credential vault and provider limits:** encrypted OpenRouter BYOK plus usage/cancellation behavior.
7. **P1-07 — Distribution and recovery:** local/hosted profiles, backup/restore, export/import, privacy diagnostics.
8. **P1-08 — Exit audit:** two-user workflow, security matrix, hosted deployment rehearsal, and evidence.

Slices P1-02 and the provider-independent portions of P1-03 may proceed in parallel after P1-01. Do not add project-owned records to another store until P1-05 defines and tests that store's scope contract.
