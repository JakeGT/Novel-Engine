# ADR-009: Invite-only tenancy and shared portfolio identity

- Status: Accepted
- Date: 2026-10-06
- Owners: project owner
- Related backlog: IDN-001–IDN-006, SaaS-002, SaaS-003

## Context

Novel Engine will be one of several applications hosted under `apps.jaketalling.com`. The hosted instance is intended as a free portfolio application, not a public-signup subscription SaaS, but it still needs multiple isolated users and collaborative project membership by invitation.

Identity should be coherent across the portfolio rather than independently implemented by every application. Novel Engine still owns application-specific authorization: being a portfolio user does not itself grant access to a Novel Engine workspace or project.

## Decision

Use a shared identity service through a standard OpenID Connect (OIDC) boundary. The selected provider is **Authentik** (self-hosted), shared across the portfolio: Authentik is the identity source for every application under `*.jaketalling.com`. Novel Engine is registered as its own OIDC client and served from a dedicated host, `novel-engine.jaketalling.com`; `apps.jaketalling.com` remains the launcher. If applications instead share one host under different paths, cookie paths are an organizational aid rather than a strong security boundary.

Authentik is **provisioned** at `https://auth.jaketalling.com` (2026-10-06) with a Novel Engine application and confidential OIDC client. The provider port, fake adapter, and local identity mode are still built and tested without the live provider; the live client wiring is unblocked and tracked in Phase 1 1C. No Phase 1 slice may hard-code a specific Authentik issuer into the domain layer.

Novel Engine maps the stable OIDC `(issuer, subject)` pair to a local user. It owns workspaces, invitations, memberships, roles, project access, and audit records. Access to the hosted Novel Engine is invitation-only:

1. An authorized workspace member creates a time-limited, single-use invitation.
2. The recipient authenticates through the shared identity service.
3. The application verifies the invitation and binds it to the authenticated identity.
4. Acceptance creates the local membership and records an audit event.

Do not use a shared parent-domain session cookie as the cross-application identity mechanism. Each application keeps a host-only session after OIDC login, limiting the effect of a compromise in another sibling application.

Self-hosted deployments retain an explicit local single-user mode and may configure a different OIDC issuer. Hosted mode cannot enable the local identity bypass.

## Selected provider: Authentik

**Authentik** (self-hosted) is the chosen provider for the `*.jaketalling.com` portfolio. Expected topology:

- Authentik host / shared issuer: `https://auth.jaketalling.com`.
- Novel Engine application and OIDC provider slug: `novel-engine`.
- Application host: `https://novel-engine.jaketalling.com`.
- Issuer (must match exactly, including the trailing slash): `https://auth.jaketalling.com/application/o/novel-engine/`.
- Discovery: `<issuer>.well-known/openid-configuration`; JWKS: `<issuer>jwks/`.
- Authorization: `https://auth.jaketalling.com/application/o/authorize/`; token: `/application/o/token/`; userinfo: `/application/o/userinfo/`.
- RP-initiated logout: `<issuer>end-session/`; post-logout redirect: `https://novel-engine.jaketalling.com/`.
- Scopes: `openid profile email`; validate ID-token `aud` == the Novel Engine client id.
- Redirect URI allow-list: `https://novel-engine.jaketalling.com/auth/callback` plus an explicit localhost development URI.

Authentik provides portfolio-wide SSO, so signing out of the Authentik session may end the user's session in sibling applications. Novel Engine still keeps its own host-only session and revokes it on logout; RP-initiated logout is optional and documented as portfolio-wide.

### Provisioning status (2026-10-06)

Authentik runs at `https://auth.jaketalling.com`. A Novel Engine application (`slug: novel-engine`) and a confidential OIDC provider are configured. Issuer/endpoints are confirmed live via discovery: issuer `https://auth.jaketalling.com/application/o/novel-engine/`, JWKS `…/jwks/`, end-session `…/end-session/`.

- [x] Deploy Authentik with TLS at the shared issuer.
- [x] Create the Novel Engine application and OIDC provider (confidential client, `openid profile email`, strict redirect allow-list, signing key, frontchannel logout).
- [ ] Confirm enrollment policy (open/controlled/invite-only) and the portfolio-owner recovery/MFA policy.
- [ ] Publish DNS/TLS and the reverse-proxy route for `novel-engine.jaketalling.com`.
- [x] Supply hosted configuration through environment/secret management (`OIDC_ISSUER`, `OIDC_CLIENT_ID`, `OIDC_CLIENT_SECRET`, `OIDC_REDIRECT_URI`, `OIDC_POST_LOGOUT_REDIRECT_URI`; never committed).

Development does not require the live provider: contributors use the deterministic fake OIDC adapter, and may optionally run Authentik under a Compose `authentik-dev` profile. A redacted sample discovery document and JWKS fixture are checked in for offline contract tests.

## Consequences

### Positive

- One identity and sign-in experience can serve the portfolio.
- Novel Engine retains clear, testable tenant and project authorization.
- Adding another application does not require sharing its session implementation or user database.
- Invite-only access limits abuse without adding billing.

### Negative

- The identity service becomes shared infrastructure with backup, recovery, security, and availability requirements.
- Invitation acceptance must handle identity mismatch, expiry, revocation, and replay safely.
- Local development and self-hosting need a separate but contract-compatible identity path.

## Provider selection requirements

Authentik is selected; these remain the acceptance criteria it must satisfy.

- OIDC authorization-code flow with PKCE and documented logout behavior
- self-registration disablement or controlled enrollment
- stable issuer/subject identifiers and account recovery
- MFA/passkey path for administrators
- per-application clients, redirect URI allow-lists, and key rotation
- an acceptable self-hosted or managed operational burden for the whole portfolio
- export/backup and migration options that avoid locking application authorization data into the provider

## Validation

- A user with a valid portfolio identity but no invitation receives no Novel Engine tenant or project access.
- An invitation cannot be reused, accepted after expiry/revocation, or accepted for the wrong bound identity.
- Workspace/project access is enforced in API, PostgreSQL, Neo4j, vector, job, object/export, and event paths.
- Hosted mode refuses to start with the local identity bypass enabled.
- Compromise or logout of one application session does not require sibling applications to share that session cookie.

## Session and logout behavior

- Novel Engine issues its own host-only session cookie (`novel_session`), `HttpOnly`, `Secure` in
  hosted/production, and `SameSite=Lax`. It never relies on a shared `*.jaketalling.com` session
  cookie, so the application session is independent of sibling applications.
- RP-initiated logout revokes the Novel Engine session server-side first; the browser is then sent
  to Authentik's `end_session_endpoint`. Authentik logout is portfolio-wide (it ends the shared
  SSO session for every application), while the Novel Engine session is revoked locally and
  remains host-only.
- Session ids are stored only as SHA-256 hashes; CSRF uses a server-validated token exposed to the
  SPA via a non-`HttpOnly` cookie.

## Cross-store workspace scoping (1E)

- Every graph (Neo4j), vector (pgvector), and artefact operation takes an explicit `workspace_id`;
  the port protocols enforce this so a caller cannot omit scope.
- The scope field is authoritative: `add_node` writes `workspace_id`/`project_id` after applying
  caller properties, so a node's owning workspace cannot be altered through entity properties.
- Local mode uses the explicit `AUTH_LOCAL_WORKSPACE_ID` workspace. Hosted mode requires an
  `X-Workspace-Id` header naming a workspace the actor is a member of (validated before the
  request reaches a repository).
- Cross-workspace graph edges are refused: `add_edge` only creates an edge when both endpoints
  resolve within the same workspace. Cross-workspace reads, searches, traversals, and vector
  queries return nothing.
- **PostgreSQL row-level security is deferred, not selected for Phase 1.** Application-layer
  scoping is mandatory at every boundary; RLS would be defense-in-depth and can be added later
  behind the same scope contract without changing callers.

## Provider credential encryption (1F)

Hosted users may store their own OpenRouter key so inference is billed to their
own account. There is no platform balance, credit, or markup state.

- Credentials are workspace-scoped and owned by the user who created them.
- Keys are stored with envelope encryption: a key-encryption key (KEK) from
  configuration (`CREDENTIAL_KEK`, base64 32-byte) wraps a per-credential data key
  (DEK); AES-256-GCM encrypts the secret. Only ciphertext, the wrapped DEK, and
  `key_version` are persisted; the KEK is never stored in the database.
- The vault exposes metadata only. Plaintext is obtainable solely through the
  worker path (`CredentialService.resolve_for_use`), which requires project-write
  authority in hosted mode (credential management requires workspace-admin); local
  single-user mode is trusted.
- An empty `CREDENTIAL_KEK` disables credential storage; the base install and all
  deterministic tests run without any credential.
- Usage is recorded immutably (`provider_usage`) with requested/actual route, token
  counts, latency, attempt, outcome, and provider-reported cost, and is bounded by
  per-job and rolling-period token/cost limits (`ProviderUsageService`).
- Redaction masks `sk-`/`sk-or-` keys and secret-named fields in logs and errors.
