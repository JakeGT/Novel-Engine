# ADR-008: Open-source core and free portfolio distribution

- Status: Proposed
- Date: 2026-10-06
- Owners: project owner
- Supersedes: commercial and release-order portions of ADR-001
- Related backlog: OSS-001–OSS-008, IDN-001–IDN-006, SaaS-001–SaaS-010

## Context

Making subscriptions, platform-funded inference, payment handling, and open public signup prerequisites would delay the writing and continuity engine that differentiates Novel Engine. Operating unrestricted hosted model inference for free would also create an uncapped cost and abuse surface. Invite-only tenant isolation remains necessary for the portfolio deployment.

The project already has useful PostgreSQL, pgvector, and Neo4j foundations. An open-source pivot does not require discarding them; it changes the primary deployment, credential, and delivery assumptions.

## Proposed decision

Make Novel Engine an open-source-first, self-hostable application with an invite-only free portfolio deployment under `apps.jaketalling.com`. The core product must work without a subscription, payment system, or project-funded inference service.

- Docker Compose is the reference deployment for contributors and independent self-hosters.
- The portfolio deployment is multi-tenant and uses shared OIDC identity plus Novel Engine-owned invitations and authorization as specified by ADR-009.
- Authors use local OpenAI-compatible inference or supply their own OpenRouter key.
- Manual authoring, import, export, deterministic consistency checks, and canon management work without paid inference.
- Project and branch scope remain mandatory. Workspace/user scope is mandatory in the hosted profile and enforced at every data boundary.
- Open self-registration is not offered. A broader public service or paid managed hosting may be added later, but it does not shape the core domain.
- The project will not promise unlimited free hosted inference. Any later hosted service must define its funding, quotas, privacy model, and operational ownership separately.

The source license is intentionally left open pending owner selection. “Free to self-host” and “open source” require an explicit OSI-approved license; a repository without one grants no general reuse rights.

## Consequences

### Positive

- Engineering effort goes first to canon, retrieval, consistency, and prose workflows.
- Local models and user-funded cloud access are natural deployment choices rather than secondary paths.
- Authors can retain direct custody of manuscripts and provider credentials.
- Contributors can run and validate the product without payment infrastructure.

### Negative

- Installation, upgrades, backups, and local resource requirements become product concerns.
- Community support and project sustainability need a model other than mandatory subscriptions.
- Shared identity and tenant isolation still add substantial work before real manuscripts can be hosted safely.
- PostgreSQL plus Neo4j is heavier than a desktop-only stack, so packaging quality matters.

## Follow-up work

1. Select an OSI-approved license before announcing an open-source release.
2. Define the supported self-hosted installation and upgrade contract.
3. Add explicit local/single-user identity and secret-storage behavior.
4. Make telemetry opt-in and document every outbound connection.
5. Select and operate the shared OIDC identity service for the portfolio deployment.
6. Define project governance, contribution policy, security reporting, and release ownership.

## Validation

The first public release is valid when a new contributor can install it from documented sources, create and export a project, use deterministic workflows without an API key, and optionally generate with either a local OpenAI-compatible endpoint or their own OpenRouter key. No request may depend on a Novel Engine-operated account, billing API, or inference balance.
