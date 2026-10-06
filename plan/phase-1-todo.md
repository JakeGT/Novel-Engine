# Phase 1 todo — Identity, tenancy, distribution, and project foundation

- Status: Ready after Phase 0 hosted CI and owner sign-off
- Scope: IDN-001–IDN-006, OSS-001–OSS-004, OSS-006–OSS-008, SaaS-006, OPS-002–OPS-006, CAN-001
- Architecture: ADR-008 and ADR-009
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

## Decisions required before affected slices

1. Select the shared OIDC provider and its recovery/MFA administration model before 1C integration.
2. Select the production application hostname; recommended: `novel-engine.apps.jaketalling.com` with a separate identity issuer.
3. Decide whether the central identity service permits general enrollment or is itself invite-only. Novel Engine remains invitation-gated either way.
4. Decide whether only the portfolio owner provisions workspaces or workspace owners may invite members.
5. Select server-side session storage and encryption/key-management mechanisms for the hosted deployment.
6. Decide whether PostgreSQL row-level security supplements mandatory application scoping.
7. Confirm Docker Compose as the supported initial self-hosted package.

## Completion dashboard

| Workstream | Backlog | Status | Depends on |
| --- | --- | --- | --- |
| 1A. Phase 0 handoff and cleanup | FND carryover, OSS-005 | Not started | Phase 0 exit |
| 1B. Identity/tenant domain and migrations | IDN-001, IDN-003, CAN-001 | Not started | 1A |
| 1C. OIDC, local identity, and sessions | IDN-001, IDN-002, OSS-002 | Not started | 1B |
| 1D. Invitations, memberships, and authorization | IDN-003, IDN-004 | Not started | 1B–1C |
| 1E. Cross-store tenant isolation | IDN-005 | Not started | 1B–1D |
| 1F. Provider credentials and usage limits | SaaS-006, OSS-003, OSS-008 | Not started | 1C–1E |
| 1G. Packaging, backup, and privacy | OSS-001, OSS-004, OSS-006, OPS-003–OPS-006 | Not started | 1C–1F |
| 1H. Security audit and exit evidence | IDN-006, QLT-002 | Not started | 1A–1G |

## 1A. Phase 0 handoff and cleanup

- [ ] Record the green hosted Phase 0 CI run and owner sign-off.
- [ ] Reconcile `backend/services/world_service.py` as an explicit change: port it onto injected ports with tests, or preserve it outside the maintained contract until a later product slice.
- [ ] Remove the temporary Ruff exclusion only when the world-service file is reconciled.
- [ ] Remove deprecated `/api` aliases once v1 parity/caller migration is verified; no new feature may be added only to a legacy route.
- [ ] Confirm Chroma is unused, then remove its dependency and transitional module in one tested change.
- [ ] Split optional local-embedding and integration dependencies where this improves install size without creating multiple ambiguous setup paths.
- [ ] Replace the Vite starter screen/assets with the minimal authenticated application shell; do not build product workspaces yet.
- [ ] Update Phase 0 evidence with final commit, CI, and carryover links.

Acceptance:

- the maintained tree has no parked-file lint exclusion or unexplained legacy singleton route
- dependency/import scans prove removed packages are unused
- the current v1 contract remains green

## 1B. Identity, workspace, and project domain

- [ ] Define typed IDs and models for `User`, `ExternalIdentity`, `Workspace`, `Membership`, `Invitation`, `Project`, and `AuditEvent`.
- [ ] Key external identity by immutable `(issuer, subject)`; treat email/display name as mutable claims.
- [ ] Define `owner`, `editor`, and `viewer` permissions as an explicit policy matrix.
- [ ] Define a request/job `AccessScope` containing actor, workspace, project, authentication mode, and correlation ID.
- [ ] Add PostgreSQL migrations, constraints, indexes, and timestamps for the new records.
- [ ] Enforce unique external identity, membership, workspace slug, invitation-token hash, and project-within-workspace invariants.
- [ ] Define ownership transfer, last-owner removal, member removal, identity unlinking, and deletion behavior.
- [ ] Update deterministic seed data with two users, two workspaces, similar project names/IDs, roles, and expired/revoked invitations.

Acceptance:

- models and migrations can be applied twice/verified from an empty database
- invalid role/ownership transitions fail deterministically
- no domain model imports an OIDC vendor SDK

## 1C. OIDC, local identity, and application sessions

- [ ] Define an identity-provider port for login start, callback validation, stable claims, logout, and key refresh.
- [ ] Implement OIDC authorization-code flow with PKCE, state, nonce, exact redirect allow-list, issuer/audience/signature/time validation, and bounded JWKS caching.
- [ ] Register Novel Engine as its own client; do not accept a shared parent-domain session cookie.
- [ ] Create a host-only, `Secure`, `HttpOnly`, appropriate-`SameSite` application session with rotation and server-side revocation.
- [ ] Apply CSRF protection to cookie-authenticated mutations.
- [ ] Map valid OIDC claims to the local external-identity record without granting workspace membership.
- [ ] Implement deterministic fake OIDC and session adapters for tests.
- [ ] Implement explicit self-hosted local identity with stable local workspace/user IDs.
- [ ] Make hosted mode fail startup if local identity, unsafe cookies, wildcard redirects, or insecure issuer settings are enabled.

Acceptance:

- a valid portfolio login with no invitation sees no workspace/project data
- replayed/invalid callback state, nonce, issuer, audience, signature, and expired tokens are rejected
- logout/revocation invalidates the Novel Engine session without requiring shared sibling-app cookies
- self-hosted mode works without the portfolio identity service

## 1D. Invitations, memberships, and authorization

- [ ] Add invitation create/list/revoke/accept endpoints and stable problem responses.
- [ ] Generate high-entropy invitation tokens, store only hashes, show the raw token once, and rate-limit acceptance.
- [ ] Make invitations expiring, single-use, revocable, role-bound, workspace-bound, and optionally email-bound.
- [ ] Recheck inviter authority and recipient identity transactionally at acceptance.
- [ ] Add workspace/member/project APIs required for administration and switching.
- [ ] Centralize authorization policy; endpoints delegate instead of duplicating role checks.
- [ ] Record security audit events for invitation, membership, role, credential, export, and deletion actions.
- [ ] Avoid revealing workspace existence or invitation validity to unauthenticated callers.

Acceptance:

- replay, expiry, revocation, identity mismatch, removed inviter, and concurrent acceptance tests pass
- role matrix tests cover every exposed command/query
- audit records contain identifiers and outcomes but never raw invitation/session tokens

## 1E. Workspace/project isolation across every boundary

- [ ] Require `AccessScope` in application workflows and repository/adapter methods.
- [ ] Add workspace/project columns and constraints to PostgreSQL operational records.
- [ ] Add workspace/project properties to Neo4j nodes and relationship projections; every query includes them.
- [ ] Require workspace/project metadata filters in pgvector operations.
- [ ] Namespace artefact/object keys, caches, jobs, events, exports, and idempotency keys.
- [ ] Prevent cross-workspace graph edges and project references.
- [ ] Decide and implement PostgreSQL RLS if selected; retain application checks either way.
- [ ] Add adversarial tests using similar IDs/names and direct adapter calls, not only HTTP tests.
- [ ] Add a migration/seed verification report showing that every durable/derived record type has an owner scope.

Acceptance:

- no read, write, search, traversal, job stream, export, cache, or object lookup crosses workspaces
- background jobs re-authorize immutable scope rather than trusting payload IDs
- local mode uses the same scoped paths with its explicit local workspace

## 1F. OpenRouter credentials and provider usage

- [ ] Define a credential-vault port and local/hosted implementations.
- [ ] Envelope-encrypt hosted OpenRouter keys with versioned key metadata and authorized worker-only decryption.
- [ ] Support validate, label, rotate, revoke, delete, and last-used status without ever returning the key.
- [ ] Scrub secrets from logs, traces, errors, support bundles, job payloads, and exports.
- [ ] Resolve credentials from authenticated actor, workspace/project policy, and job scope immediately before provider use.
- [ ] Record requested/actual route, token usage, latency, attempt, and provider-reported cost without creating a platform balance.
- [ ] Implement author/project per-job and periodic token/cost limits plus confirmation thresholds.
- [ ] Ensure cancellation/retry cannot duplicate accepted output or bypass a limit.
- [ ] Keep manual authoring, deterministic checks, and fake-provider workflows available with no credential.

Acceptance:

- only the authorized worker path can obtain plaintext key material
- key/redaction tests include upstream exception text and structured diagnostics
- OpenRouter charges the user's account; no payment, credit, or markup state exists

## 1G. Distribution, backup, and privacy

- [ ] Define explicit `local` and `hosted` deployment profiles with fail-closed configuration validation.
- [ ] Document one supported Docker Compose install, health check, migration, upgrade, and rollback path.
- [ ] Document the portfolio reverse-proxy/TLS/OIDC callback topology.
- [ ] Implement coordinated backup/restore for PostgreSQL, Neo4j, and artefact storage with manifest/checksums.
- [ ] Implement complete project export/import without credentials or session data.
- [ ] Document every outbound connection; make telemetry absent or opt-in and content-free by default.
- [ ] Add previewable redacted diagnostics/support bundles.
- [ ] Add storage retention and deletion behavior for invitations, sessions, job artefacts, prompts/responses, and projects.
- [ ] Run clean-machine self-hosted install and hosted-profile deployment rehearsals.

Acceptance:

- backup/restore and project export/import preserve representative canonical checksums
- self-hosted operation requires no `jaketalling.com` service
- hosted deployment refuses unsafe local defaults and passes readiness without performing migrations

## 1H. Security, quality, and exit evidence

- [ ] Add identity/session/invitation threat-model tests and dependency scanning.
- [ ] Run the complete cross-tenant matrix at API, application, and real-adapter levels.
- [ ] Test IdP outage, JWKS rotation, expired keys, PostgreSQL/Neo4j degradation, worker restart, and credential revocation.
- [ ] Verify logs, CI artefacts, exports, backups, and diagnostics contain no credentials, invitation tokens, sessions, or manuscript text.
- [ ] Record migration versions/checksums, test counts, coverage baseline, and clean deployment commands.
- [ ] Record OIDC provider/configuration decision in a dedicated ADR without committing secrets or tenant identifiers.
- [ ] Complete a manual two-user invitation → project access → removal → denied-access workflow.
- [ ] Obtain project-owner acceptance before real portfolio users store manuscripts.

## Phase 1 exit gate

- [ ] Hosted login uses the shared OIDC service and host-only Novel Engine sessions.
- [ ] Access remains invitation-only and application authorization is independent of authentication.
- [ ] Workspace/project scope is enforced and adversarially tested across every storage/runtime boundary.
- [ ] Self-hosted local mode uses the same core without depending on portfolio infrastructure.
- [ ] Hosted user-supplied OpenRouter keys are encrypted, redacted, revocable, and worker-restricted.
- [ ] No subscription, payment, platform-credit, or platform-funded inference path exists.
- [ ] Backup/restore, project export/import, migration, and upgrade rehearsals pass.
- [ ] Required CI and security checks pass from a clean checkout.
- [ ] Phase 1 evidence is accepted before Phase 2 canon work starts.

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
