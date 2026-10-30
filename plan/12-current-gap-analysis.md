# Current gap analysis

This is a point-in-time map of the prototype to the target design. It was rewritten after
Phase 0 stabilization (see `plan/phase-0-evidence.md`), and each later section supersedes the ones
above it where they differ. The newest, [State on 2026-10-21](#state-on-2026-10-21), is the current
one; the earlier sections are kept as history. Update it when a subsystem crosses from prototype to
planned architecture.

## Repository-wide

Current (post-Phase 0):

- application factory (`create_app(settings, container)`) with a dependency container and
  lifespan-owned external resources; imports perform no external work
- ports for graph, vector, embedding, AI, schema, clock, ID, artifacts, migrations, and
  database; Neo4j, pgvector, sentence-transformers, OpenAI, PostgreSQL, and system adapters
- explicit Alembic and ordered Neo4j migrations with read-only verification and checksum drift
  detection
- stable `/api/v1` DTOs and an `application/problem+json` error contract with correlation IDs
- deterministic hermetic tests (188) and disposable integration tests (16)
- reference-world seed manifest and fail-closed reset/seed commands
- structured, redacted logging; health/readiness/capabilities; CI with a disposable
  integration job

Remaining gaps to the target:

- no shared OIDC/session integration, invitations, or workspace/project scoping at persistence, cache, vector, job, or event boundaries
- no journal/outbox, encrypted hosted OpenRouter credential vault, or hosted workers
- legacy `/api` prototype routes remain as deprecated aliases pending migration/removal
- final fact/belief/event/branch domain redesign and the consistency engine are not started
- the parked world workflow (`backend/services/world_service.py`) is unreconciled

## Application lifecycle

Current:

- `backend/main.py` builds the container inside the lifespan, verifies Neo4j and PostgreSQL
  migration state in a `try/finally`, and closes resources on shutdown
- `app = create_app()` provides the conventional ASGI export without import-time side effects
- `backend/container.py` wires adapters from explicit settings

Target:

- OIDC/local identity adapters (Authentik in the portfolio deployment), invitation-derived workspace/project context, and per-request unit of work
- startup readiness that distinguishes required stores from optional providers (partially done)
- background worker lifecycle separate from the API process

## API and transport

Current:

- canonical `/api/v1` router with entity/edge/schema/context/character endpoints and a
  `rag/query` endpoint that returns 501
- `backend/api/problems.py` maps domain, validation, HTTP, AI, and unhandled errors to
  `application/problem+json`
- `CorrelationIdMiddleware` propagates/generates `X-Correlation-ID`
- legacy `/api` router still uses module singletons for graph and RAG

Target:

- remove or fully port legacy routes; enforce workspace/project scope on every endpoint
- cursor pagination, idempotency keys, and rate limiting
- streaming generation and review endpoints

## Graph, schema, and traversal

Current:

- `GraphManager` is lazy; no DDL at import or startup
- `Neo4jGraphRepository` maps Neo4j records to the stable `Node`/`Edge` domain shape and
  instruments calls
- schema registry rejects unknown types on writes, orders custom types deterministically, and
  persists sorted lists; `World` node type exists
- `build_traversal_query` is a characterized pure function; `ContextTraversal` runs on the
  repository port

Target:

- objective-fact vs character-belief separation enforced in traversal and context assembly
- branch/timeline-aware traversal with validity intervals
- pagination and streaming for large graphs

## Retrieval and vectors

Current:

- `PgVectorStore` implements the vector port over PostgreSQL/pgvector, embedding through the
  injected embedding port; the derived `vector_documents` table is rebuildable
- embeddings are optional and report honest availability/loaded state
- the transitional Chroma module and dependency have been removed; pgvector is the sole vector store

Target:

- vector port filters (by type, project, branch), deactivation, and rebuild hooks
- hybrid keyword + semantic retrieval with provenance
- rebuild tooling and index versioning

## Character and world workflows

Current:

- character workflow runs on injected ports with a full temporary→durable ID map, edge
  endpoint rewriting, and validation of unknown types, duplicates, missing targets, and
  conflicting entities
- world workflow is parked and excluded from lint/format pending owner reconciliation
  (FND-008.23–27)

Target:

- world workflow repaired and port-injected
- mutation proposals validated against branch/timeline invariants and committed atomically
  (FND-008.21, Phase 1)
- review boundary between proposals and accepted canon

## Frontend

Current:

- default Vite React scaffold with lint and production build in CI

Target:

- product UI for graph browsing, context inspection, character/world authoring, and review

## Tests and scripts

Current:

- `tests/{unit,application,contract,integration,e2e}` with documented responsibilities and
  hermetic-by-default execution
- `scripts/manual/` holds non-hermetic manual scripts excluded from pytest collection
- `scripts/ci/secret_scan.py` provides the credential guard

Target:

- coverage floors per package, mutation testing for domain invariants, and recorded
  model-quality evaluations behind the `llm` marker

## Refactor order

Phase 1 should proceed in this order without reopening Phase 0 foundations:

1. OIDC/local identity ports, invitation lifecycle, and workspace/project scope primitives
2. workspace/project enforcement at every persistence and asynchronous boundary
3. journal/outbox and unit-of-work around canon mutations
4. fact/belief/event/branch domain redesign with validity intervals
5. mutation review and atomic application (FND-008.21)
6. consistency preflight/postflight harness (ADR-006)
7. legacy `/api` route removal once v1 parity is confirmed

## Phase 1 implementation update (2026-10-08)

Phase 1 built the identity, tenancy, isolation, credential, and packaging boundary
on top of the Phase 0 foundation. This section supersedes the Phase 0 baseline
above where it differs; `plan/phase-1-evidence.md` holds the detailed evidence.

Now implemented and tested:

- provider-neutral OIDC (Authentik) login with host-only, CSRF-protected,
  server-side, rotating sessions, plus a trusted local single-user mode
- users, external identities, workspaces, memberships, invitations, projects, and
  security audit events, with centralized authorization and a durable rate limiter
- mandatory workspace/project scope across PostgreSQL, Neo4j, pgvector, and object
  storage; cross-workspace and cross-project references are refused
- envelope-encrypted OpenRouter BYOK credentials with project-write use authority,
  provider usage records and limits, and redaction
- object storage separate from debug artifacts; redacted diagnostics; retention
  purge; checksummed project export/import; dependency auditing

Still open before later phases: usage accounting completeness (latency/cost/
attempts/failures/idempotency), purge/commit journaling and recovery, live Authentik
browser validation, coordinated database backup/restore, clean-host rehearsals, and
the PostgreSQL RLS decision.

The next transition is Phase 2 (trustworthy canon): a controlled core vocabulary and
project schema versions, reified facts/beliefs/events, root-branch revisions, a
recoverable PostgreSQL journal/outbox with idempotent Neo4j application, provenance
and retirement, and media identity. ADR-012 has since reopened canonical storage/commit work so
PostgreSQL becomes authoritative and Neo4j replayable; see `plan/phase-2-todo.md`.

## Phase 2 implementation update (2026-10-09)

Phase 2 built trustworthy canon on the Phase 1 boundary. This section supersedes the paragraphs
above where they differ; `plan/phase-2-evidence.md` holds the evidence and the known limitations.

Now implemented and tested:

- PostgreSQL-authoritative canon: an immutable journal and normalized/versioned entities, facts,
  beliefs, events, provenance, and evidence, committed in one transaction (ADR-012)
- immutable, compiled project schema versions with a vocabulary endpoint for schema-derived forms
- a mutation review state machine with per-operation decisions, immutable operation revisions,
  and a high-impact gate evaluated inside the commit transaction (ADR-014, proposed)
- entity naming policy, evidence references, provenance with redaction, tags, and compensating
  reverts (ADR-015, proposed)
- a replayable Neo4j projection under its own labels, with journal/canon/projection equivalence
  checks; complete project deletion; a ledgered, verified archive restore and a bounded Phase 1
  archive upgrader (ADR-016, proposed)
- media identity, immutable versions, explicit links, and a lifecycle audit
- a guided character workflow that only ever produces review proposals
- an authoring and review UI with automated accessibility checks and browser end-to-end tests
- property/state-machine tests, upgrade-migration tests, performance budgets, and operational
  metrics

Phase 1 and Phase 2 evidence and ADR-014–016 were accepted by the owner (2026-10-09/10). Still
open: the first hosted CI run, the clean-host backup/restore rehearsal (deferred by the owner), and
the PostgreSQL RLS decision carried from Phase 1.

## After Phase 2: work done outside the written plan (2026-10-09/10)

- **Deployment.** One image (API, migrations, built UI), a hosted Compose stack, and a runbook;
  live at `novel-engine.jaketalling.com` behind Authentik. Sign-in was performed by the owner.
- **Interface.** Rebuilt around author vocabulary: landing page, library, story bible, entry
  pages with a sentence builder for facts, one-step review, history with restore, form-based
  types and fields, search palette, light and dark themes.
- **Assisted creation.** Describe → optional questions → one proposal of entries, details, and
  links; a derived to-fill-in list; the same assistant fills in any entry; a starter schema on
  project creation.

## Gaps against the assistant-first direction (ADR-017), as of 2026-10-10

Every gap below except the first and the last has since been closed or narrowed; see the next
section. Measured against [`19-assistant-first-experience.md`](19-assistant-first-experience.md):

- **Unverified:** the assistant has run only against a scripted model. Draft quality with real
  providers is unknown.
- **No assistant path yet** for: review ("revise this"), undo, custom types, invitations, story
  planning, scenes, rules, timelines, points of view, what-ifs, context, media, checks, exports.
- **No by-hand path yet** for: beliefs, members and invitations.
- **No story structure:** canon has no story, arc, beat, scene-plan, or thread records.
- **Conversations are not stored** beyond an evidence reference; they cannot be resumed.
- **Linking is by name and alias only;** there are no semantic suggestions.
- **Review does not scale:** every change is decided one at a time.
- **No first-run:** a new account lands in an empty library; provider setup is a settings page.
- **Levels are informal:** advanced screens are tucked under "Set up" and "Advanced", but nothing
  yet decides what to offer when.
- **A new author needs their own OpenRouter key** before the assistant works (owner decision).

The next transition is Phase 2.5 as revised (assistant-led creation), then 2.6 (story planning
through conversation), then 2.7 (text intake).

## State on 2026-10-21

After Phases 2.5–7 and batches 1–8. This section supersedes the paragraphs above where they differ;
the status of each capability, with what it still lacks, is in [`10-backlog.md`](10-backlog.md).

Closed since 2026-10-10:

- **Assistant paths** exist for review ("revise this"), undo, custom types, invitations, story
  planning, scenes (drafting, checks, reading back), rules, timelines and knowledge, what-ifs,
  pictures, audits and corrections (choosing what a scene's context leaves out is by hand only); **by-hand paths** for beliefs, members and invitations.
- **Story structure** (story, arc, beat, scene plan, thread) is canon-governed intent (ADR-018).
- **Conversations** are stored, resumable, kept per author and line, and archived.
- **Linking** also uses meaning when a model for search is configured; each run records how many
  suggestions the author kept.
- **Review scales**: grouped, bulk decisions; "keep the safe ones for me" by opt-in rule.
- **First run**: onboarding, a prompt to connect a key, a sample story, and starting from a premise.
- **Levels**: what is shown and offered is decided by level (`lib/levels.ts`).
- **Providers**: keys for OpenRouter and six other providers, a local OpenAI-compatible endpoint, and
  a connector for a model on the author's own computer (ADR-025).
- **Earlier repository-wide gaps**: identity, invitations and scoping at every boundary (Phase 1);
  journal, outbox and the credential vault (Phases 1–2); background workers (Phase 5); the legacy
  `/api` aliases are removed; the domain redesign and a consistency engine exist (Phases 2–5 and
  later batches); the parked world workflow was discarded by the owner (FND-008).
- **Retrieval**: vector filters, generation-versioned indexes with rebuild and rollback, hybrid
  ranking with recorded signals, and retrieval evaluation sets (CTX-004–CTX-014).
- **Frontend**: the product UI replaced the Vite scaffold (story bible, plan, studio, review,
  history, timeline, media and settings).

Still open:

- **Real models.** Every assistant path has run against the scripted model and the local stand-in
  only; quality, groundedness and critic precision with a real model are unmeasured (owner runs).
- **Owner and operational items.** A licence, the first hosted CI run, the restore drill on a clean
  host or live stack, installing scheduled backups and copy-off, the identity provider's recovery and
  MFA policy (IDN-006), and the PostgreSQL row-level security decision (deferred by ADR-009).
- **Search by meaning on the hosted site** is off: the hosted image does not install the embeddings
  extra. EmbeddingGemma 2 has never been loaded here.
- **Media**: hosted malware scanning, a remote-provider preview, and a screen for marking regions.
  Sound and film are deferred past release one by owner decision (2026-10-21).
- **Transport**: no streaming, no HTTP archive import, `/rag/query` stays `501`, and MCP has no OAuth
  sign-in.
- **Checks without a model** read only what words and records show; each backlog item lists its
  limits.
