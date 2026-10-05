# Target architecture

## Recommended shape

Start as a hosted modular monolith: one FastAPI application, scalable background workers, one React application, PostgreSQL, Neo4j, object/text storage, and a vector index. Keep internal boundaries strict enough that workers, consistency analysis, or provider adapters can later move out of process without redesigning the domain.

```text
React SaaS application
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
  PostgreSQL    Neo4j    Vector index  Object/text
  journal/jobs  canon      derived       versions
```

The arrows indicate dependency direction. Domain models and policies do not import FastAPI, PostgreSQL/Neo4j/vector drivers, or an AI SDK.

## Modules

### Transport

Responsibilities:

- HTTP and event-stream endpoints
- authentication and project-scope resolution
- subscription entitlement, credit, and autonomy-policy resolution
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
- `ReserveUsageCredits`

They define transaction boundaries and call domain services through ports.

### Domain

Contains typed entities, value objects, invariants, policies, commands, and events. Important concepts include project, branch, timeline position, entity reference, fact, belief, scene plan, scene draft, mutation set, provenance, and review decision.

### Context engine

Builds a traceable, token-budgeted context package from:

- explicit entity anchors
- deterministic graph facts
- point-of-view knowledge
- narrative requirements
- semantic history
- style references

It returns structured context items plus a rendered prompt section. Retrieval and rendering are separate operations.

### AI gateway

Defines provider-neutral operations such as structured completion, text generation, embeddings, and optional vision analysis. OpenRouter is the cloud adapter; an OpenAI-compatible adapter supports an approved local-connector topology. Provider adapters translate internal requests to SDK calls. Prompts, schemas, retry policy, model selection, upstream provider identity, and usage accounting are versioned.

### Consistency engine

Builds deterministic preflight constraints, runs incremental postflight rules, and schedules bounded model critics or deep audits. Findings reference exact prose and canon revisions. See `13-consistency-system.md`.

### Review and commit

Model-extracted changes enter a staging area as a mutation set. Domain validators attach errors or warnings. The author can accept, edit, or reject changes. Only an accepted mutation set enters the canonical graph and mutation journal.

### Persistence adapters

- PostgreSQL repositories for accounts, tenancy, memberships, subscriptions, credit ledger, credentials, jobs, reviews, mutation journal, outbox, and operational audit
- Neo4j repository for canonical entities, relations, facts, beliefs, story structure, and query projections
- vector adapter for derived embeddings and chunks
- blob/text storage for exact scene versions and import/export artefacts where graph properties are unsuitable

Large immutable prose versions and imports/exports use object storage with PostgreSQL metadata. Local development may use a filesystem-compatible adapter.

## Runtime flows

### Read flow

1. Resolve user, project, branch, and timeline position.
2. Load deterministic graph state.
3. Apply knowledge and visibility policy.
4. Retrieve derived semantic candidates using graph IDs as filters.
5. rank, deduplicate, and budget context items.
6. Return structured results and an explanation trace.

### AI proposal flow

1. Create a job with immutable input references.
2. Build and persist a context manifest.
3. resolve BYOK or reserve platform credits and select a model route.
4. run deterministic consistency preflight and attach the constraint packet.
5. call the model through an adapter.
6. validate the structured response and reconcile actual usage.
7. retry only safe, classified failures without duplicate charging.
8. store output as a draft or proposed mutation set.
9. run deterministic postflight rules and policy-selected model critique.
10. present the proposal and findings for review.

### Commit flow

1. Check the proposal still targets the expected project/branch/head revision.
2. Re-run hard invariants.
3. append the mutation transaction to the journal.
4. apply canonical graph changes atomically or mark recovery required.
5. advance the branch head.
6. enqueue derived-index and summary updates.
7. publish a change event to the UI.

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
- Every request, job, object, graph record, vector record, and billing entry is tenant/project scoped.
- Every repository method requires project scope; branch/timeline scope where relevant.
- Canon writes happen only through the commit service.
- The vector store never authoritatively resolves a conflict with the graph.
- A model cannot introduce an unknown type or edge without an explicit schema-extension decision.
- Long-running AI calls return job IDs rather than holding a fragile browser request indefinitely.
- Paid jobs reserve credits before execution and reconcile actual use exactly once.
- BYOK secrets are never placed in prompts, logs, graph records, or vector metadata.
- Every stored AI artefact identifies provider, model, prompt version, schema version, and input manifest.
- API and persisted formats are versioned before the first user project becomes irreplaceable.

## Scale strategy

Do not split services pre-emptively. Extract a component only when it needs independent scaling, security isolation, or deployment. Likely first candidates are the worker and embedding/indexing pipeline. Neo4j query profiling and context budgets should precede caching or distributed architecture.
