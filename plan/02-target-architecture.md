# Target architecture

## Recommended shape

Start as a modular monolith that supports two profiles: an invite-only multi-tenant deployment under `apps.jaketalling.com`, and a trusted single-user self-hosted deployment. Both use one FastAPI application, background worker capability, one React application, PostgreSQL with pgvector, Neo4j, and object/text storage. Keep internal boundaries strict enough that workers or consistency analysis can later move out of process without redesigning the domain.

```text
React authoring application + media library
      |
      v
HTTP API / job events
      |
      v
Application workflows
  |         |        |         |            |
  v         v        v         v            v
Domain   Context  AI ports  Review/commit  Consistency
  |         |        |         |            |
  +---------+--------+---------+------------+
                  |
       Persistence interfaces
       |          |           |          |
       v          v           v          v
  PostgreSQL    Neo4j    Vector index  Object/media
  canon/jobs   projection   derived       versions
```

The arrows indicate dependency direction. Domain models and policies do not import FastAPI, PostgreSQL/Neo4j/vector drivers, or an AI SDK.

## Modules

### Transport

Responsibilities:

- HTTP and event-stream endpoints
- shared OIDC (Authentik in the portfolio deployment) or local identity plus workspace/project-scope resolution
- provider configuration, cost limits, and autonomy-policy resolution
- request validation and response serialization
- mapping known domain errors to stable problem responses

It must not construct prompts, issue Cypher, or directly call model SDKs.

### Application workflows

Use-case coordinators such as:

- `CreateCharacter`
- `PlanScene`
- `BuildSceneContext`
- `GenerateDraft`
- `ExtractSceneMutations`
- `ReviewMutationSet`
- `CommitScene`
- `CreateBranch`
- `MergeBranch`
- `RunConsistencyCheck`
- `AuthorizeModelUsage`

They define transaction boundaries and call domain services through ports.

### Domain

Contains typed entities, value objects, invariants, policies, commands, and events. Important concepts
include project, branch, world/narrative/revision positions, entity reference, fact, belief,
narrated assertion, reader disclosure, scene contract, mutation set, provenance, and review decision.

Author-defined story invariants and scene contracts use a safe declarative proposition language.
They are versioned project data, never executable project code. Built-in integrity invariants remain
separate, permanently blocking domain rules.

### Context engine

Builds a traceable, token-budgeted context package from:

- explicit entity anchors
- deterministic graph facts
- point-of-view knowledge
- narrative requirements
- semantic history
- style references
- explicitly linked and semantically relevant images, audio, and video

It returns typed text/media context parts plus rendered text sections and an exact delivery
manifest. Retrieval, authority/visibility filtering, representation fallback, and provider
rendering are separate operations. See `17-multimodal-context.md`.

### AI gateway

Defines provider-neutral operations such as structured completion, long-form generation,
multimodal embeddings, media understanding, and critique/ranking. Model routes declare accepted
modalities and media limits. OpenRouter is the cloud adapter; an OpenAI-compatible adapter supports
an approved local-connector topology. Provider adapters translate the internal discriminated
`TextPart | ImagePart | AudioPart | VideoPart` context into SDK calls or declare an explicit
derived-text fallback. Prompts, schemas, retry policy, model selection, upstream provider identity,
media delivery, and usage accounting are versioned.

### Consistency engine

Builds deterministic preflight constraints, runs incremental postflight rules, and schedules bounded model critics or deep audits. Findings reference exact prose and canon revisions. See `13-consistency-system.md`.

It also evaluates author story invariants, scene contracts, reader disclosures, scoped
suppressions, and derived voice evidence. Author invariants default to warnings; only explicitly
enabled deterministic rules may block a commit. See
`18-author-rules-canon-queries-and-derived-views.md`.

### Canon query and explanation layer

Provides one storage-neutral boundary for point-in-time state, reader/character visibility,
revision differences, provenance/blame, dependency impact, tags, and compensating revert
proposals. HTTP, UI, consistency rules, context assembly, exports, and MCP call this same
application layer. Implementations may use watermarked projections, but PostgreSQL remains the
authoritative fallback.

### Review and commit

Model-extracted changes enter a staging area as a mutation set. Domain validators attach errors or
warnings. The author can accept, edit, or reject changes. Only an accepted mutation set enters the
PostgreSQL canonical journal/state transaction; projection delivery follows through the outbox.

### Persistence adapters

- PostgreSQL repositories for external-identity mappings, workspaces, invitations, memberships,
  projects, settings, jobs, reviews, mutation journal, normalized/versioned canon, outbox, and
  operational audit; plus versioned authored rules/plans and watermarked derived read models
- Neo4j repository for rebuildable graph query projections with applied-revision watermarks
- vector adapter for derived embeddings and chunks
- object storage for exact scene versions, immutable media originals/variants, and import/export artefacts where graph properties are unsuitable

Large immutable prose/media versions and imports/exports use object storage with PostgreSQL
metadata. Media ingestion and embedding are idempotent background pipelines. Local development may
use a filesystem-compatible adapter.

## Runtime flows

### Read flow

1. Resolve user, project, branch, and timeline position.
2. Load deterministic canonical state from PostgreSQL; use Neo4j only when its projection watermark
   satisfies the requested revision.
3. Apply reader/character knowledge, disclosure, and visibility policy.
4. Retrieve derived semantic candidates using graph IDs as filters.
5. retrieve eligible explicitly linked and semantic media candidates.
6. rank, cluster cross-modal representations, and budget context items.
7. resolve the target model's modality support and representation fallback.
8. Return structured results and an explanation trace.

### AI proposal flow

1. Create a job with immutable input references.
2. Build and persist a context manifest.
3. resolve the configured local/OpenRouter credential, enforce the author's limits, and select a model route whose modality capabilities satisfy the job or declared fallback.
4. run deterministic consistency preflight and attach the constraint packet.
5. call the model through an adapter.
6. validate the structured response and record normalized usage.
7. retry only safe, classified failures without duplicate generation or exceeding limits.
8. store output as a draft or proposed mutation set.
9. run deterministic postflight rules and policy-selected model critique.
10. present the proposal and findings for review.

### Commit flow

1. Check the proposal still targets the expected project/branch/head revision.
2. Re-run permanently blocking built-in integrity invariants, then applicable deterministic author
   story invariants. Author rules are warnings unless an authorized user explicitly enabled
   blocking behavior for that rule version.
3. In one PostgreSQL transaction, append the immutable envelope, update normalized/versioned canon,
   advance the branch head, append the revision, and enqueue projection events.
4. Report the canonical commit after that transaction succeeds.
5. Project the committed envelope idempotently into Neo4j/vector/search and publish projection lag.
6. Publish a change event to the UI.

## Package direction

The backend should evolve toward:

```text
backend/
  api/                 # routers, dependencies, problem responses
  application/         # commands, queries, workflows, ports
  domain/              # models, invariants, policies, domain events
  infrastructure/
    graph/              # Neo4j repositories and migrations
    vector/             # vector adapters and indexing
    ai/                 # provider adapters
    jobs/               # worker and queue adapter
    journal/            # mutation journal
  prompts/              # versioned prompt templates and schemas
  settings.py
  main.py
```

This should be reached incrementally. Avoid a wholesale move before characterization tests cover existing behavior.

## Architectural constraints

- No external connection or persistent write at module import time.
- Every request, job, object, graph record, vector record, export, and event is workspace/project scoped in hosted mode. Self-hosted local mode supplies one explicit local workspace/user context through the same contracts.
- Every repository method requires project scope; branch/timeline scope where relevant.
- Canon writes happen only through the commit service.
- Built-in integrity invariants cannot be disabled, overridden, suppressed, or bypassed through an
  alternate transport. Extraction-dependent uncertainty cannot block a commit.
- MCP and other assistant transports may read scoped state and create proposals, but never commit
  canon or weaken review/invariant policy directly.
- Derived projections never authoritatively resolve a conflict with PostgreSQL canon.
- Neo4j and vector-backed reads expose or verify their applied canonical revision; stale projection
  state is never presented as current.
- A model cannot introduce an unknown type or edge without an explicit schema-extension decision.
- Long-running AI calls return job IDs rather than holding a fragile browser request indefinitely.
- Model jobs estimate usage, enforce configured limits, and record actual usage exactly once.
- BYOK secrets are never placed in prompts, logs, graph records, or vector metadata.
- Media objects are fetched only after scope re-authorization; object keys and signed URLs are never stable public identifiers.
- Explicit author/domain asset links and hard branch/time/visibility gates outrank semantic similarity.
- Embeddings, captions, transcripts, OCR, and model observations are derived and cannot write canon without mutation review.
- A provider adapter must record whether it sent original media or derived text and must not silently omit an unsupported modality.
- Every stored AI artefact identifies provider, model, prompt version, schema version, and input manifest.
- API and persisted formats are versioned before the first user project becomes irreplaceable.

## Scale strategy

Do not split services pre-emptively. Extract a component only when it needs independent scaling,
security isolation, or deployment. Likely first candidates are the worker and media
processing/embedding pipeline because codec/scanning workloads and a multimodal model should not
block API processes. Neo4j query profiling and context budgets should precede caching or
distributed architecture.
