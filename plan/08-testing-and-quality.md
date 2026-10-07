# Testing and quality

## Test layers

### Domain unit tests

Fast, service-free tests for IDs, schema compilation/validation, facts, intervals, branches, belief visibility, invitation and autonomy policies, mutation validation, consistency rules, context budgeting, and state transitions. These should form the majority of the suite.

### Application workflow tests

Run use cases with fake repositories, fake clock/ID generator, and scripted AI adapter. Assert commands, events, proposals, and failure behavior without Neo4j or a network.

### Repository contract tests

Run repository behavior contracts against fakes and real PostgreSQL/Neo4j adapters. Cover tenant/project isolation, serialization, transactions/sagas, temporal queries, branch overlays, idempotency, outbox recovery, and concurrency.

### API contract tests

Use FastAPI's in-process test client with injected fakes for most cases. Verify OpenAPI, status codes, error format, validation, pagination, concurrency, and auth/project boundaries.

### Integration tests

Use disposable containers for Neo4j and any journal store. Start the application as needed. Never depend on a developer's persistent `data/` directory or an already-running server.

Media integration tests use small synthetic fixtures and disposable object storage/filesystem
roots. Codec, scan, and embedding adapters have deterministic fakes; optional real-processor tests
are separately marked and never download models during ordinary collection.

### Frontend tests

- component tests for editors and mutation review
- mocked-API workflow tests
- end-to-end happy paths against disposable services
- accessibility checks on core workflows

### AI contract and evaluation tests

Offline contract tests use recorded or synthetic structured responses. Live-provider evaluations are separately marked, opt-in, budgeted, and record provider/model/date. Do not make ordinary CI depend on nondeterministic model quality.

## Critical invariant matrix

| Area | Required tests |
| --- | --- |
| Project isolation | no read/write/reference across projects |
| Tenant isolation | no API/job/graph/vector/object/export access across tenants |
| Identity | OIDC validation, host-only sessions, local/hosted mode guard, invitation expiry/revocation/replay |
| Provider usage | limits, cancellation, retry idempotency, and actual-usage recording |
| Branches | inheritance, fork isolation, merge conflicts, head concurrency |
| Time | interval boundaries, replacement, unknown dates, flashback ordering |
| Beliefs | false belief retained, secret visibility, acquisition/expiry |
| Mutations | validation, per-operation review, atomicity, retry idempotency |
| Context | anchors, filters, ranking, budgeting, explanations, no leakage |
| Indexing | outbox retry, active versions, rebuild equivalence, stale warning |
| Media assets | upload validation, immutable versions, scoped object access, variants/segments, authority, retention, and complete deletion |
| Multimodal retrieval | explicit-link precedence, cross-modal relevance, representation clustering, embedding-generation isolation, and rebuild equivalence |
| Provider media | capability negotiation, limits, original/fallback fidelity, remote transfer preview, and unsupported-modality rejection before spend |
| AI | malformed output, refusal, timeout, rate limit, cancellation, cost cap |
| Credentials | BYOK encryption, worker-only access, rotation, deletion, and redaction |
| Autonomy | mandatory approvals cannot be bypassed by user/model/job state |
| Consistency | tier rules, incremental invalidation, evidence, staleness, override audit |
| Export | round trip, schema version, checksum, missing derived data rebuild |

## Test data

Create a compact reference world containing:

- two branches with one conflict
- a public fact and a secret
- a character with a false belief
- a location/resource constraint
- events shown out of narrative order
- a scene that produces several reviewable mutations
- an unresolved thread and planned payoff
- a selected author-only secret that must not leak into POV knowledge
- deterministic continuity defects and labeled model-critic cases
- branch/time-scoped character portraits, landscapes, objects/maps, multi-speaker audio, timestamped video, and visually similar hard negatives
- a secret-bearing asset and misleading machine observation that must not become accessible truth or canon

Seed at least two tenants with deliberately similar IDs/names so isolation tests cannot pass accidentally because records are absent.

Use deterministic IDs and a fixed clock. Keep fixtures synthetic and safe to publish.

## Quality gates

For every pull request or agent handoff:

- formatting and static lint pass
- relevant unit/workflow tests pass
- new behavior has acceptance tests
- schema/API changes update contracts and migration notes
- no secrets or generated databases are staged
- docs reflect current versus planned status

For a release candidate:

- full integration and end-to-end suite passes in a clean environment
- export/restore drill passes
- migration from the previous supported version passes
- AI evaluation metrics meet the approved baseline
- multimodal retrieval, provider-fallback fidelity, and media-consistency metrics meet the approved baseline
- security dependency scan has no unaccepted critical issue
- tenant-isolation, identity/invitation, provider-usage, and key-redaction suites pass
- manual release-one workflow is completed with a fresh project
- media export/restore/reindex/deletion and cross-tenant object-access drills pass

## Immediate test cleanup

1. Create `tests/unit`, `tests/application`, `tests/integration`, and `tests/e2e`.
2. Mark existing root endpoint tests as legacy integration tests.
3. Replace `requests` against localhost with an in-process client where possible.
4. Inject graph/vector/AI dependencies so import and test collection need no services.
5. Add characterization tests for entity tags and current graph DTOs before refactoring.
6. Keep test output in normal tooling/CI artefacts rather than committing generated reports.
7. Add CI only after the deterministic test command succeeds from a clean checkout.
