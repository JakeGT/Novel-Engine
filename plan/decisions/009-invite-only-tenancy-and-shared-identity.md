# ADR-009: Invite-only tenancy and shared portfolio identity

- Status: Accepted
- Date: 2026-10-06
- Owners: project owner
- Related backlog: IDN-001–IDN-006, SaaS-002, SaaS-003

## Context

Novel Engine will be one of several applications hosted under `apps.jaketalling.com`. The hosted instance is intended as a free portfolio application, not a public-signup subscription SaaS, but it still needs multiple isolated users and collaborative project membership by invitation.

Identity should be coherent across the portfolio rather than independently implemented by every application. Novel Engine still owns application-specific authorization: being a portfolio user does not itself grant access to a Novel Engine workspace or project.

## Decision

Use a shared identity service through a standard OpenID Connect (OIDC) boundary. The likely topology is a dedicated issuer such as `auth.jaketalling.com`, with each portfolio application registered as its own OIDC client. Prefer a dedicated application host such as `novel-engine.apps.jaketalling.com`; `apps.jaketalling.com` can remain the portfolio launcher. If applications instead share one host under different paths, cookie paths are an organizational aid rather than a strong security boundary.

Novel Engine maps the stable OIDC `(issuer, subject)` pair to a local user. It owns workspaces, invitations, memberships, roles, project access, and audit records. Access to the hosted Novel Engine is invitation-only:

1. An authorized workspace member creates a time-limited, single-use invitation.
2. The recipient authenticates through the shared identity service.
3. The application verifies the invitation and binds it to the authenticated identity.
4. Acceptance creates the local membership and records an audit event.

Do not use a shared parent-domain session cookie as the cross-application identity mechanism. Each application keeps a host-only session after OIDC login, limiting the effect of a compromise in another sibling application.

Self-hosted deployments retain an explicit local single-user mode and may configure a different OIDC issuer. Hosted mode cannot enable the local identity bypass.

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
