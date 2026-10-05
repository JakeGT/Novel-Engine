# Security and operations

## Threat model

Protected assets include passwords/sessions, OpenRouter BYOK credentials, payment/subscription data, platform credits, unpublished manuscript text, private world notes, secrets within the story graph, provider request history, and project exports. Relevant threats include cross-tenant access, prompt injection from imported text, credential theft, billing fraud/double charges, accidental secret leakage into POV context, unsafe dynamic Cypher, excessive model spend, dependency compromise, and loss/corruption of author data.

## Security controls

### Application boundaries

- Scope every repository operation to a verified project.
- Resolve tenant and user from authentication; enforce membership/role both in application policy and repository queries.
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

Hosted mode is authoritative: verified accounts, secure sessions, project membership/roles, audit, abuse controls, account recovery, and deletion. Local development uses seeded test identities through an explicit development adapter, never a production authentication bypass.

Use defense in depth: application authorization, tenant-scoped repository methods, database constraints/policies where suitable, tenant metadata in Neo4j/vector/object keys, and cross-tenant integration tests.

## Billing and credits

- Maintain an append-only ledger; never mutate balances without ledger entries.
- Reserve estimated platform credits before a paid job, then reconcile or release exactly once.
- Record model route, input/output/cache usage, provider cost, markup policy version, and currency/credit conversion version.
- BYOK jobs do not consume platform inference credits but may remain subject to subscription entitlements and platform limits.
- Webhooks are verified, replay-safe, ordered/idempotent, and reconciled.
- Insufficient funds, cancellation, retry, timeout, partial provider response, and refund behavior are explicit state transitions.

## Background jobs

Job records contain tenant/project, type, immutable inputs, stage, attempts, lease/heartbeat, cancellation flag, progress, funding source/reservation, provider usage, outputs, and classified error. Workers use idempotency keys and bounded retries. Jobs that may spend money check cancellation and budget immediately before provider calls.

Start with PostgreSQL-backed job/outbox coordination suitable for the first hosted deployment. Adopt a dedicated broker only when throughput or distributed scheduling demands it.

## Observability

Use structured logs with correlation, project, job, workflow, prompt version, model, duration, and outcome. Hash or omit content. Metrics should cover:

- HTTP latency/error rate
- job queue time, execution time, retries, and cancellations
- provider latency, token usage, spend, and invalid-output rate
- credit reservations, reconciliation drift, webhook failures, and BYOK/platform routing
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

### Hosted SaaS

Reverse proxy/TLS, application instances, workers, managed PostgreSQL and Neo4j, vector capability, object storage, key/secret management, payment webhooks, backups, monitoring, and explicit tenant isolation.

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
- credit/provider reconciliation and payment webhook replay
- suspected BYOK or tenant-data exposure
