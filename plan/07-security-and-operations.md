# Security and operations

## Threat model

Protected assets include OIDC/application sessions, invitations, OpenRouter credentials,
unpublished manuscript text, private world notes, original images/audio/video, media metadata and
derived observations, secrets within the story graph, provider request history, and project
exports. Relevant threats include cross-tenant access, invitation replay, identity/session
confusion across sibling portfolio apps, prompt injection from imported text or media/OCR,
malicious/polyglot/decompression-bomb uploads, codec/parser compromise, sensitive EXIF/GPS leakage,
credential theft, accidental secret leakage into POV context, unsafe dynamic Cypher, excessive
model spend/storage, dependency compromise, and loss/corruption of author data.

## Security controls

### Application boundaries

- Scope every repository operation to a verified project.
- Resolve workspace and user from the validated OIDC/local session; enforce invitation-derived membership and role in application policy and repository queries.
- Use allow-listed schema identifiers; parameters cannot protect interpolated Cypher labels by themselves.
- Keep unrestricted Cypher and raw graph administration out of user-facing APIs.
- Validate uploads by size, type, extension, and content; scan when hosted.
- Decode/transcode media in an isolated worker with pixel/frame/duration/decompression limits and allow-listed output formats.
- Namespace object keys by verified scope, use short-lived authorized access, and never accept a client object key as authorization.
- Treat MCP as an authenticated transport over the same application services. Server-derived
  identity establishes workspace/project scope; tool arguments never establish tenant authority.
- MCP and other assistant integrations may create proposals but cannot commit canon, change
  invariant enforcement, or mint suppressions/overrides outside the ordinary authorized workflow.
- Strip or separately protect EXIF/GPS and other metadata that is not required for authored provenance.
- Apply request/body limits and timeouts.
- Return redacted errors outside development.

### AI boundaries

- Treat imported prose, web material, filenames, OCR, captions, transcripts, visible media text, and retrieved notes as untrusted data, never system instructions.
- Delimit context sections and state their authority.
- Validate all structured outputs against strict schemas and domain rules.
- Do not allow model-selected tools to write canon directly.
- Apply per-project cost/rate limits and require confirmation above thresholds.
- Encrypt BYOK credentials with envelope encryption, restrict decryption to workers, support revocation/rotation, and redact request headers/logs.
- Record whether original media or a derived fallback was sent to a remote provider; require an explicit preview and respect provider retention/deletion capabilities.

### Story confidentiality

- Logs are metadata-first; manuscript content logging is opt-in and time-limited.
- Context traces may contain spoilers/secrets and inherit project access control.
- Media previews, captions, transcripts, embeddings, perceptual derivatives, signed URLs, and context parts inherit the strictest source visibility and are excluded from content logs.
- Exports are explicit, checksummed, and never uploaded implicitly.
- Apply the same branch/revision/narrative-position/POV visibility gate to spoiler-safe exports,
  MCP results, cached context prefixes, and UI queries. Artifact sharing is narrower than project
  viewer membership and does not grant general project access.
- Scope provider prompt caches by tenant/project/visibility policy. Never trade cache reuse for
  cross-tenant, cross-POV, or secret-bearing prefix reuse.
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

Media ingestion jobs additionally record source checksum, processor/model revision, safe technical
limits, segment/variant generation, scanning result, and derived-index generation. They run outside
the API process and cannot broaden source visibility.

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
- media upload/scan/transcode/extraction/index latency and failure counts by safe media category
- original-versus-derived provider delivery and unsupported-modality rejection, without content or object URLs

Add tracing across API → workflow → retrieval/provider → persistence when the basic boundaries exist.

## Health and failure modes

- Liveness checks only process health.
- Readiness checks required stores without mutating them.
- Provider outage should leave manual authoring and canonical reads operational.
- Vector outage should permit deterministic context with a visible degradation warning.
- Media processor outage should preserve originals and text/manual workflows with a visible retryable status.
- A provider without a required modality must fail before charging or use a previewed, recorded derived-text fallback.
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

Built 2026-10-21 (SCN-002A, ADR-025): registration by the OAuth device grant (RFC 8628) confirmed by a workspace owner who sees the computer's name; a digest-only device credential rotated daily, stopped after 30 days, revocable at once and when its member leaves; an outbound HTTPS long poll (no inbound port, no redirects); only model listing and chat completions relayed; the words cleared once answered; a version floor enforced on both sides with nothing downloaded. Runbook `plan/runbooks/local-connector.md`.

## Operational runbooks required before beta

- backup and restore
- failed/corrupt commit recovery
- stuck job and provider outage
- vector reindex
- quarantined/failed media processing and multimodal reindex
- schema migration failure
- project export/import
- key rotation
- data deletion
- media/provider-upload deletion and orphan-object reconciliation
- spend spike investigation
- shared IdP outage/key rotation and invitation abuse
- suspected provider-key or tenant-data exposure

Written so far (`plan/runbooks/`, 2026-10-18, completed 2026-10-21): backup and restore (with an
example off-host copy), stuck jobs, provider outage and a revoked key, schema migration failure, a
failed project import, credential key rotation (with `backend.devtools.rekey`, which re-locks every
stored key in place), the story graph behind or wrong, a full disk, failed or corrupt commit
recovery, vector reindex (`search-index.md`), media processing and orphaned objects
(`media-processing.md`), data deletion, spend spikes, the shared identity provider (outage, key
changes, invitation abuse), and suspected exposure of keys or tenant data. None has been rehearsed
on the live site; every SQL statement in them is checked against the schema by
`tests/integration/test_runbook_queries.py`.

## Revision 2026-10-10: the in-product assistant

Additions to the threat model and operations for ADR-017:

- **Injection through the author's own text.** Descriptions, answers, and pasted prose are data.
  They cannot change the assistant's instructions, widen its tools, or cause a commit; the only
  output is a typed proposal that the validators and the review gate handle like any other.
- **Runaway sessions.** An interview is several model calls. A session has a budget in calls and
  tokens, shown to the author, enforced before each call, and counted against the existing
  per-job and per-period limits.
- **Conversation retention.** Stored conversations contain the author's story. They are
  workspace-scoped, exported and deleted with the project, excluded from logs and telemetry, and
  sent only to the provider the author configured.
- **Degraded assistant.** Provider outage, a missing key, or an exhausted budget leaves every
  by-hand path working and says plainly why the assistant is unavailable.
- **Deployment reality.** The product is live for the owner ahead of the "runbooks before beta"
  gate below. Backup is scripted; restore onto a clean host is unrehearsed (deferred by the owner
  on 2026-10-10 until the project moves to a permanent host).
