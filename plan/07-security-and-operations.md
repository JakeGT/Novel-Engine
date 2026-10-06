# Security and operations

## Threat model

Protected assets include OIDC/application sessions, invitations, OpenRouter credentials, unpublished manuscript text, private world notes, secrets within the story graph, provider request history, and project exports. Relevant threats include cross-tenant access, invitation replay, identity/session confusion across sibling portfolio apps, prompt injection from imported text, credential theft, accidental secret leakage into POV context, unsafe dynamic Cypher, excessive model spend, dependency compromise, and loss/corruption of author data.

## Security controls

### Application boundaries

- Scope every repository operation to a verified project.
- Resolve workspace and user from the validated OIDC/local session; enforce invitation-derived membership and role in application policy and repository queries.
- Use allow-listed schema identifiers; parameters cannot protect interpolated Cypher labels by themselves.
- Keep unrestricted Cypher and raw graph administration out of user-facing APIs.
- Validate uploads by size, type, extension, and content; scan when hosted.
- Apply request/body limits and timeouts.
- Return redacted errors outside development.

### AI boundaries

- Treat imported prose, web material, and retrieved notes as untrusted data, never system instructions.
- Delimit context sections and state their authority.
- Validate all structured outputs against strict schemas and domain rules.
- Do not allow model-selected tools to write canon directly.
- Apply per-project cost/rate limits and require confirmation above thresholds.
- Encrypt BYOK credentials with envelope encryption, restrict decryption to workers, support revocation/rotation, and redact request headers/logs.

### Story confidentiality

- Logs are metadata-first; manuscript content logging is opt-in and time-limited.
- Context traces may contain spoilers/secrets and inherit project access control.
- Exports are explicit, checksummed, and never uploaded implicitly.
- Hosted mode requires encryption in transit and at rest plus tenant-isolation tests.

## Authentication and tenancy

Hosted mode delegates authentication to the shared portfolio OIDC service (Authentik) and owns host-only application sessions, invitation acceptance, workspace/project membership, roles, audit, and deletion/ownership-transfer behavior. A valid portfolio identity alone grants no Novel Engine access. Local/self-hosted mode uses an explicit local identity adapter that hosted configuration refuses to enable.

Use defense in depth: application authorization, tenant-scoped repository methods, database constraints/policies where suitable, tenant metadata in Neo4j/vector/object keys, and cross-tenant integration tests.

Do not share an authentication cookie across `.jaketalling.com`. Give each application its own OIDC client, redirect URI allow-list, and host-only session cookie. Use authorization code flow with PKCE and validate issuer, audience, signature, state, nonce, and token lifetime.

Invitations are high-entropy, hashed at rest, single-use, expiring, revocable, and transactionally accepted. Audit creation, revocation, attempted acceptance, successful acceptance, role changes, and removal without logging raw tokens.

## Provider usage and limits

- Record model route, input/output/cache usage, provider-reported cost, and policy version.
- Enforce author/project token and cost limits immediately before a provider attempt.
- OpenRouter bills the user's account directly; Novel Engine has no balance, markup, purchase, or refund state.
- Cancellation, retry, timeout, and partial provider responses are explicit state transitions and cannot create duplicate accepted output.

## Background jobs

Job records contain workspace/project, initiating identity, type, immutable inputs, stage, attempts, lease/heartbeat, cancellation flag, progress, credential reference, provider usage, outputs, and classified error. Workers use idempotency keys and bounded retries. Jobs that may spend the user's provider funds check cancellation and limits immediately before provider calls.

Start with PostgreSQL-backed job/outbox coordination suitable for the invite-only hosted deployment. Adopt a dedicated broker only when throughput or distributed scheduling demands it.

## Observability

Use structured logs with correlation, project, job, workflow, prompt version, model, duration, and outcome. Hash or omit content. Metrics should cover:

- HTTP latency/error rate
- job queue time, execution time, retries, and cancellations
- provider latency, token usage, spend, and invalid-output rate
- provider route, token/cost-limit rejection, usage-record failures, and credential selection
- graph/vector query latency and result count
- context size, truncation, and retrieval source mix
- mutation validation/commit failures
- consistency finding categories
- index lag from canonical head

Add tracing across API → workflow → retrieval/provider → persistence when the basic boundaries exist.

## Health and failure modes

- Liveness checks only process health.
- Readiness checks required stores without mutating them.
- Provider outage should leave manual authoring and canonical reads operational.
- Vector outage should permit deterministic context with a visible degradation warning.
- Worker outage should queue work rather than lose it.
- Index lag is visible and repairable.
- Startup does not hide failed migrations or destructive compatibility issues.

## Deployment profiles

### Local development

FastAPI and frontend on the host; PostgreSQL and Neo4j in Compose; local vector/object adapters. Seeded development identities and the fake AI adapter are default for tests.

### Invite-only portfolio deployment

Dedicated application hostname/path behind reverse proxy/TLS, shared Authentik OIDC identity service, application instances, workers, PostgreSQL and Neo4j, vector capability, object storage, key/secret management, backups, monitoring, invitation administration, and explicit tenant isolation.

### Local inference connector

If selected, a separately authenticated companion makes outbound connections from the user's network and brokers jobs to OpenAI-compatible local endpoints. It receives only authorized job context, never platform master secrets. Its protocol, updates, revocation, and threat model require a dedicated ADR.

## Operational runbooks required before beta

- backup and restore
- failed/corrupt commit recovery
- stuck job and provider outage
- vector reindex
- schema migration failure
- project export/import
- key rotation
- data deletion
- spend spike investigation
- shared IdP outage/key rotation and invitation abuse
- suspected provider-key or tenant-data exposure
