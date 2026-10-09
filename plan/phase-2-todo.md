# Phase 2 todo — Trustworthy canon

- Status: Implementation complete, awaiting owner acceptance (all code-completable 2B–2L work is committed; owner acceptance of the Phase 1 and Phase 2 evidence, the first clean-checkout hosted CI run, the clean-host backup/restore rehearsal, and the live Authentik round-trip are external and still open)
- Scope: CAN-002–CAN-013 and the Phase 2 portions of AST-001/AST-004/AST-008 and EXT-003–EXT-006
- Depends on: Phase 1 technical foundation and acceptance of the remaining owner/operational exit items
- Architecture: ADR-003, ADR-004, ADR-005, ADR-009, ADR-010, ADR-012 (supersedes ADR-011), ADR-013, and the proposed ADR-014/015/016 recorded while closing the phase
- Exit owner: project owner or designated technical lead

## Objective

Make project canon safe to author, review, commit, inspect, retire, export, and restore. At Phase 2
exit, entities, facts, beliefs, events, schema versions, revisions, provenance, mutation decisions,
and selected media evidence have explicit domain models and cannot bypass a recoverable canonical
commit path.

The first complete vertical workflow is:

```text
author opens a project
  -> creates or edits an entity/fact/belief/event
  -> previews the typed mutation and validation result
  -> commits against an expected root-branch head
  -> inspects the new revision and provenance
  -> exports and restores the same canonical state
```

This phase establishes trustworthy canon. It does not yet generate prose or implement full
timeline, branch-fork, POV-context, or multimodal-processing behavior.

## Phase boundary

Phase 2 includes:

- a controlled core canon vocabulary and immutable project schema versions
- safe project-scoped schema extensions compiled to runtime Pydantic and JSON Schema validators
- stable entities plus reified facts, beliefs, and events
- a root branch and monotonic system revisions for every project
- provenance and retirement rather than destructive history erasure
- a point-in-revision canon/history/provenance query boundary, immutable tags, and compensating
  revert proposals
- invariant enforcement classes and a stable evaluator seam for the Phase 2.5 story-rule workflow
- typed mutation sets, a review state machine with per-operation decisions and a high-impact
  gate, validation, optimistic concurrency, and idempotency
- an atomic PostgreSQL journal + normalized canon transaction and replayable Neo4j projection
- project-scoped entity/canon APIs and a usable world/character authoring interface
- media asset/version identity, authority, provenance, explicit links, and archive semantics
- a complete archive containing canonical history and permitted immutable originals

Phase 2 deliberately excludes:

- branch forks, inherited overlays, comparison, and merges (Phase 3)
- world-time query semantics, interval replacement, and narrative-order behavior (Phase 3)
- POV knowledge filtering and secret-aware context assembly (Phase 3)
- story hierarchy, scene plans, canonical scene documents, and prose generation (Phase 4+)
- author story-invariant authoring/runs, scene contracts, suppressions, and model critics (Phase
  2.5+); Phase 2 defines their stable enforcement boundary and serialization compatibility only
- public media upload, scanning, transcoding, captioning, transcription, segmentation, embeddings,
  similarity retrieval, or provider media delivery (Phase 4+)
- real-time co-editing or CRDTs
- background job infrastructure except the durable outbox/reconciliation seam required by commits

Phase 2 stores branch/time/visibility fields needed for compatibility where the accepted data model
requires them, but only the root branch and system-revision semantics are executable. APIs must not
claim that deferred temporal or POV rules are enforced.

## Accepted Phase 2 foundation decisions

The project owner accepted these decisions on 2026-10-08. ADR-012 supersedes ADR-011's storage
authority while carrying the other Phase 2 boundaries forward.

1. **Author edit path:** ordinary author-originated, low-impact edits may be committed
   directly after showing their validation/diff, but still create a mutation set, journal entry,
   revision, provenance, and outbox records. Model, import, bulk, destructive, schema-changing, and
   conflict-resolution proposals require a distinct human approval action.
2. **Branch/time boundary:** create one root branch per project and implement system
   revision concurrency only. Keep fork/merge and world/narrative-time evaluation in Phase 3.
3. **Canon authority:** PostgreSQL owns the immutable journal and normalized/versioned accepted
   canon in one transaction. Neo4j is an optional, rebuildable projection driven by the outbox and
   never participates in canonical commit success.
4. **Media boundary:** implement metadata, immutable version identity, explicit links,
   lifecycle, provenance, and archive behavior only. Do not expose hosted uploads until the secure
   ingestion pipeline exists.
5. **Existing generic graph API:** retain it only as an explicitly enabled local
   development/admin surface while product APIs use the mutation service. Hosted mode disables all
   mutation paths that bypass the journal.
6. **Invariant enforcement:** built-in integrity invariants are permanently blocking and
   non-suppressible. Author story invariants default to warnings and require explicit authorized
   publication to block; uncertain or extraction-dependent checks cannot reject a commit
   (ADR-013).

ADR-012 records decision 3 and carries forward ADR-011's decisions 1, 2, and 5. Decision 4 follows
ADR-010 and is restated in ADR-012 as the Phase 2 delivery boundary. ADR-013 records decision 6.

## Completion dashboard

| Workstream | Status | Depends on |
| --- | --- | --- |
| 2A. Phase 1 closeout and Phase 2 contract | In progress (ADR-012, glossary, baseline, compatibility rules recorded; owner acceptance, hosted CI run, and clean-host rehearsal are external) | Phase 1 evidence |
| 2B. Canon domain and core schema | Implemented | 2A |
| 2C. Project schema governance and compiler | Implemented | 2B |
| 2D. Revision, journal, outbox, and storage migrations | Implemented (ADR-012: normalized PostgreSQL canon + immutable journal commit in one transaction, projection watermarks; migrations 0013–0018) | 2A–2C |
| 2E. Mutation validation, canonical commit, and projection recovery | Implemented (single-PostgreSQL-transaction commit, exact published-schema validation, review state machine with transition log, per-operation decisions and immutable operation revisions, high-impact gate evaluated in-transaction, deterministic retry identity, commit telemetry, idempotent outbox consumer + replayable projector) | 2B–2D |
| 2F. Entity authoring and retirement | Implemented (canonical entity API with revision/ETag, naming policy for slugs/aliases/external ids, acknowledged retirement, guided character workflow as review proposals) | 2C–2E |
| 2G. Facts, beliefs, and events | Implemented (fact/belief/event APIs + derived truth agreement, typed relationship projection, canonical reference world with labelled proposals, close/reassert history) | 2B–2F |
| 2H. Provenance and history inspection | Implemented (revision/record history, state-at-revision, diffs, tags, evidence provenance with redaction, dependency-aware revert proposals with edit inverses, verification CLIs, projection report) | 2D–2G |
| 2I. Media identity and explicit evidence links | Implemented (asset/version identity, reviewed link roles through the mutation path, lifecycle retirement with an immutable audit, PostgreSQL coverage, reference-world asset) | 2B–2E |
| 2J. Project shell and authoring UI | Implemented (permission-aware shell; schema-derived entity forms, filters, relationships; character view with the guided proposal workflow; schema, proposal-review, history/provenance/revert, and asset views; stale-head recovery; automated accessibility checks; component and browser end-to-end tests) | 2F–2I APIs |
| 2K. Archive upgrade, restore, and rebuild | Implemented (archive v3, strict preflight, import status ledger with resume/removal, verified projection rebuild, owner authorization + security audit, bounded Phase 1 graph upgrader, clean-environment restore test) | 2C–2I |
| 2L. Security, quality, and exit evidence | Implemented (threat/auth tests, invariant matrix, property/state-machine tests, colliding-id fixtures, upgrade migration tests, metrics, performance budgets, rule-classification tests; mutation-testing tooling not adopted; clean-host rehearsal is an owner item) | all workstreams |

## 2A. Phase 1 closeout and Phase 2 contract

- [x] Obtain owner acceptance of `phase-1-evidence.md` and record the accepting commit/date.
      (Accepted by the owner on 2026-10-09, against commit `02ad806`.)
- [ ] Run the first clean-checkout hosted CI workflow and link its immutable result.
- [ ] Complete or explicitly time-box the clean-host and coordinated backup/restore rehearsal; do
      not mislabel project archive round-tripping as a database backup.
- [x] Refresh `12-current-gap-analysis.md` from the Phase 1 implementation rather than its Phase 0
      baseline, and reconcile historical phase labels in `10-backlog.md` with this plan.
      (A "Phase 1 implementation update" section was appended to `12-current-gap-analysis.md`.)
- [x] Write and accept the canon authority decision. ADR-012 supersedes ADR-011's cross-store saga,
      makes PostgreSQL canon authoritative, and retains the author direct-commit/review boundary.
- [x] Freeze a Phase 2 vocabulary/glossary for entity, proposition, fact, belief, event, revision,
      mutation set, operation, decision, journal entry, projection, and provenance.
      (`plan/phase-2-glossary.md`.)
- [x] Capture the starting OpenAPI checksum, PostgreSQL/Neo4j migration heads, archive format,
      reference-seed checksum, test counts, and known deferred items. (`plan/phase-2-baseline.md`.)
- [x] Define forward/backward compatibility rules for API DTOs, archive versions, schema versions,
      prompt schemas, and Neo4j projections before adding durable records.
      (`plan/phase-2-baseline.md` "Compatibility rules".)
- [x] Record the accepted story-invariant enforcement classes and cross-phase contracts for canon
      queries, reader disclosure, narrated assertions, scene contracts, aliases, suppressions,
      derived read models, MCP, and prompt caching. (ADR-013 and
      `18-author-rules-canon-queries-and-derived-views.md`.)

### 2A acceptance

- [x] the Phase 1 evidence has an explicit owner decision (accepted 2026-10-09)
- [x] the commit protocol has one accepted state diagram and one named authority per datum
- [x] every Phase 2 backlog item maps to a workstream and acceptance criterion below
- [x] deferred Phase 3+ behavior is named rather than partially implied by APIs

## 2B. Canon domain and controlled core schema

Implemented in `backend/domain/canon/` with pure invariant tests in
`tests/unit/test_canon_domain.py`.

- [x] Introduce dedicated domain modules for canon identities and value objects; do not expand the
      transport-oriented `backend.graph.schema.Node` property bag into the final domain model.
      (`backend/domain/canon/{ids,propositions,entities,assertions,events,revisions,registry,errors}.py`.)
- [x] Define opaque typed IDs for entity, predicate, fact, belief, event, branch, revision,
      mutation set, operation, schema version, provenance source, asset, and asset version.
      (`ID_PREFIXES` + `format_canon_id`/`is_canon_id`.)
- [x] Define stable `CanonEntity` identity with project scope, declared type, display name/slug,
      lifecycle (`active`/`retired`), schema version, created revision, and retired revision.
- [x] Define a shared typed proposition shape: subject, declared predicate, and exactly one typed
      object form (entity reference, string, integer/decimal, boolean, enum, quantity, or bounded
      structured value). (Discriminated `PropositionObject` union.)
- [x] Define `Fact` as an accepted objective proposition with status, root-branch scope, optional
      reserved validity fields, confidence, provenance, and creation/closure revisions.
- [x] Define `Belief` as a holder-scoped proposition with confidence and acquisition provenance.
      It supports false/uncertain beliefs without a corresponding fact; truth agreement is derived
      (`truth_agreement`), never stored.
- [x] Define `Event` with kind, participants/roles, location references, causes/outcomes, optional
      reserved world-position data, source, and revision provenance without implementing Phase 3
      chronology queries.
- [x] Define immutable `CanonRevision` and `Provenance` models. Origins cover author, model, import,
      derived, migration, and system; no raw prompts or secret content.
- [x] Define retirement/closure semantics. Referenced canon is retired or closed at a revision;
      `retire_entity` preserves the original, and `ensure_active_reference` blocks new active
      references to retired or cross-project entities.
- [x] Define a versioned core registry for entity types and predicates, including endpoint types,
      value shape, cardinality, whether temporal behavior is allowed, and visibility defaults.
      (`CORE_ENTITY_TYPES`, `CORE_PREDICATES`, `PredicateSpec`, `EntityTypeSpec`.)
- [x] Separate stable identity attributes from time-varying assertions. `CanonEntity.attributes`
      holds only schema-declared stable attributes; time-varying claims are facts.
- [x] Add pure domain tests for identifiers, value unions, proposition equality, lifecycle,
      cardinality, invalid cross-project references, and false beliefs.

### 2B acceptance

- [x] objective fact and character belief cannot be confused by type or serialization
- [x] all accepted canon carries project, root branch, schema version, revision, and provenance
- [x] unknown types/predicates/value shapes fail before persistence
- [x] retirement preserves readable history and blocks new active references unless policy allows it
- [x] no Phase 2 model invents a world timestamp when the author supplied an unknown/fuzzy value

## 2C. Project schema governance and runtime compiler

- [x] Replace the process-local/global `custom_schema.json` registry with immutable,
      workspace/project-scoped schema-version records in PostgreSQL.
      (New `schema_versions` table (migration `0012_schema_versions`) + `SchemaVersionStorePort`;
      the legacy global JSON registry remains only as a local-dev convenience for listing core
      types and is no longer a project-governance path.)
- [x] Preserve a fixed application-owned core schema and allow extensions only through a bounded
      declarative meta-schema. Never evaluate project-supplied Python, templates, regexes with
      unsafe features, Cypher, or arbitrary expressions.
      (`backend/domain/schema_defs.py`; string `format` is a vetted enum, never a user regex.)
- [x] Support the initial safe subset: bounded primitives, enums, constrained strings/numbers,
      lists, nested records, entity references, required/optional fields, endpoint constraints,
      cardinality, and reserved temporal/visibility flags.
      (Records nest to depth 3; lists carry primitives/enums/entity refs; temporal/visibility flag
      maps are reserved.)
- [x] Validate limits on definition size, nesting depth, enum/list size, identifiers, descriptions,
      and compiled JSON Schema complexity to prevent resource-exhaustion inputs.
- [x] Compile an exact schema version into internal field specifications, strict runtime Pydantic
      validators, and strict JSON Schema suitable for later model structured-output requests.
      (`backend/services/schema_compiler.py`; `CompiledSchema` + deterministic content hash.)
- [x] Cache compiled validators only by workspace/project/schema-version content hash; make cache
      eviction safe and ensure a cache hit cannot cross projects. (LRU keyed by
      `workspace\x1fproject\x1fcontent_hash`.)
- [x] Make schema publishing immutable and optimistic: edit a draft, validate compatibility, then
      publish a new version. Existing canon retains the version under which it was accepted.
- [x] Define compatibility categories (`additive`, `migration_required`, `forbidden`) and produce a
      migration/projection plan for non-additive changes; no schema publish silently rewrites canon.
      (`classify_compatibility` + `migration_plan`.)
- [x] Add project-scoped schema list/read/draft/validate/publish endpoints with authorization,
      revision conflicts, stable problem responses, and audit/provenance events.
      (`backend/api/v1/schema_versions.py`; publish requires project admin; forbidden → 409.)
- [x] Remove hosted write access to the global schema endpoint. Provide a read-only core-schema
      capability endpoint or local development tool where still useful.
      (Global `POST/DELETE /schema/*` now requires local mode; hosted mode must publish project
      schema versions. Read-only listing remains available.)
- [x] Update the seed world to use published schema versions and remove its dependency on a mutable
      global JSON file. (`backend/seed/schema.py` publishes a v1 core schema for the seed project;
      `make seed` is idempotent and `make reset-dev` clears it.)
- [x] Add meta-schema property/fuzz tests, malicious definition fixtures, deterministic compilation
      snapshots, cache-isolation tests, and JSON-Schema/Pydantic agreement tests.
      (`tests/unit/test_schema_compiler.py`, `test_schema_compatibility.py`,
      `tests/application/test_schema_service.py`, `tests/integration/test_schema_store.py`,
      `tests/contract/test_schema_versions.py`.)

### 2C acceptance

- [x] two projects may define the same extension name without sharing definition or cache state
- [x] one published definition produces deterministic validators and JSON Schema after restart
      (content-addressed; cache cleared between compilations still yields the same hash/output)
- [x] historical canon remains interpretable after a newer project schema is published
      (versions are immutable and canon carries `schema_version_id`; enforcement lands with 2F)
- [x] ordinary content writes cannot create undeclared types or predicates
- [x] unsafe or pathologically complex definitions are rejected without executing user input

## 2D. Revisions, journal, outbox, canonical storage, and projection migrations

ADR-012 made PostgreSQL authoritative: normalized canon, the immutable journal commit, and
projection watermarks are implemented (migrations `0013`–`0018`). The earlier Neo4j write path is
retained only as the rebuildable projection and never participates in commit success.

- [x] Add normalized, versioned PostgreSQL tables for entities, facts, beliefs, events, provenance,
      record versions/effects, and projection watermarks. Final fact/effect columns wait for the
      accepted branch-overlay and `WorldTime` design.
      (Migration `0014_canon_storage`: `canon_entities`/`canon_facts`/`canon_beliefs`/`canon_events`/
      `canon_provenance`/`canon_record_versions`/`projection_watermarks`.)
- [x] Reserve stable ID prefixes and canonical serialization envelopes for future `StoryInvariant`,
      `SceneContract`, `NarratedAssertion`/`SpeechAct`, `Disclosure`, `AliasBinding`/
      `ResolutionExample`, `CanonTag`, `FindingSuppression`, dependency-edge, and read-model
      watermark records. Do not add inactive tables merely to reserve names.
      (`ID_PREFIXES` + `RESERVED_ID_KINDS` in `backend/domain/canon/ids.py`. The prefixes were not
      actually present before the Phase 2 closure work; they are now, with a test that they are
      distinct and unimplemented. `CanonTag` is implemented; no inactive tables were added.)
- [x] Define one PostgreSQL canon repository/unit-of-work port that reads state at a revision and
      applies a validated envelope atomically with branch head, revision, decisions, and outbox
      (`CanonRepositoryPort` + `PostgresCanonRepository.apply_commit`; `InMemoryCanonRepository` mirrors it).
- [x] Separate canonical repository ports from `CanonProjectionPort`; no application workflow may
      treat `CanonWriterPort`/Neo4j success as canon acceptance (commit uses `CanonRepositoryPort`
      only; `CanonWriterPort` is consumed solely by the projector).
- [x] Add projection cursor/watermark persistence with project, branch, revision, commit ID,
      envelope checksum, projection schema version, last error, and rebuild status
      (`projection_watermarks` + `ProjectionWatermark`).
- [x] Provide deterministic journal replay into empty normalized test state and empty Neo4j, plus
      checksum/representative-query verification
      (`GraphProjectionService.rebuild`; `make verify`; `tests/integration/test_projection.py`).

- [x] Add PostgreSQL records for root branches, branch heads/reservations, schema versions,
      mutation sets, ordered operations, review decisions, immutable commit envelopes, commit
      attempts/state transitions, and outbox messages.
      (Migration `0013_canon_journal`: `branches`, `branch_reservations`, `mutation_sets`,
      `mutation_operations`, `review_decisions`, `commit_envelopes`, `commit_attempts`,
      `canon_revisions`, `outbox_messages`; `schema_versions` arrived with 2C.)
- [x] Carry `workspace_id` and `project_id` on every row and every unique/index key that could
      otherwise collide between tenants. Add database constraints for status transitions where
      practical.
      (Every journal table carries workspace/project ownership; uniqueness keys are workspace/project
      scoped; `branches` has a partial unique index for a single root and `branch_reservations` a
      partial unique index for one active reservation per branch.)
- [x] Create exactly one root branch for every existing/new project through an idempotent migration
      and provisioning path. Use an explicit zero/genesis head, not `NULL` ambiguity.
      (`CanonJournalPort.ensure_root_branch` is idempotent and genesis head is `0` (NOT NULL DEFAULT).)
- [x] Define a monotonic per-project/root-branch system revision and optimistic expected-head
      contract. Prevent more than one active commit reservation for a branch.
      (`reserve_head` requires the exact expected revision, allocates `expected + 1`, and a partial
      unique index forbids a second active reservation; tested in unit + integration.)
- [x] Persist an immutable canonical serialization and checksum for each committed envelope,
      including operation order, exact schema version, expected/result revision, actor, provenance,
      and decision summary.
      (`commit_envelopes` stores canonical text + checksum + schema version + expected/result; the same
      commit id with a different checksum is rejected. Provenance/decision summary are stored
      alongside in `mutation_sets`/`review_decisions`/`canon_revisions`.)
- [x] Add Neo4j migrations for scoped entity, fact, belief, event, revision/application-marker, and
      projection constraints/indexes. All identities include workspace/project and branch where
      required.
      (Neo4j `0003_canon_scope_identity`: composite identity constraints for `Fact`/`Belief`/`Event`
      and `Provenance`, a `CommitMarker` uniqueness constraint, and workspace/project/branch indexes.)
- [x] Model frequent graph relationships as rebuildable projections while keeping reified fact,
      belief, and event identity/provenance inspectable.
      (Reified facts/beliefs/events are projected under the projection's own `Canon*` labels —
      Neo4j migration `0004_canon_projection_labels`, ADR-016 — and open entity-to-entity facts
      also project as typed relationships that are rebuilt from the journal.)
- [x] Add repository ports for schema, canon queries, journal/commit coordination, and outbox with
      in-memory fakes that implement the same concurrency/idempotency behavior.
      (`CanonJournalPort` + `PostgresCanonJournal` + `InMemoryCanonJournal` with matching invariants.)
- [x] Keep SQLAlchemy rows, Neo4j records, API DTOs, and domain models separate; write explicit
      mappers and repository contract tests.
      (Domain models in `backend/domain/journal.py`; row mappers in `backend/adapters/journal.py`;
      contract tests in `tests/integration/test_journal_store.py`.)
- [x] Add read-only migration verification, downgrade/roll-forward notes, and seed reset support.
      Prototype data may reset, but migrations must still be deterministic from an empty database.
      (0013 has a full `downgrade`; `clear_tenancy` truncates the journal tables; disposable
      integration databases migrate to head from empty.)

### 2D acceptance

- [x] migrations apply to an empty environment, apply idempotently where supported, and verify
      exact constraints/indexes
- [x] every durable canon/workflow record has tenant/project ownership and a declared deletion rule
      (one table list, `backend/adapters/canon_tables.py`, drives project/workspace deletion,
      failed-import removal, and the development reset; before the closure work project
      deletion left canon rows and most projected nodes behind)
- [x] a branch head cannot advance twice from the same expected revision
- [x] the same commit ID/checksum cannot be reused for different operations
- [x] no canonical projection depends on Neo4j internal IDs
      (No `elementId(` in the graph layer; the legacy `GraphManager.delete_edge` still keys on `id(r)`
      but is unused by canon/projection paths and is not exposed.)

### ADR-012 acceptance additions

- [x] PostgreSQL normalized canon and the immutable journal commit in one transaction
      (`PostgresCanonRepository.apply_commit`: head lock → in-tx validation → reservation → envelope →
      normalized canon → head/revision → outbox).
- [x] Neo4j can be deleted and rebuilt from genesis without changing canonical checksums
      (`GraphProjectionService.rebuild` + `CanonWriterPort.discard`; `tests/integration/test_projection.py`).
- [x] graph-backed reads prove their watermark satisfies the requested revision or report lag
      (`GET /projects/{id}/projection`; `OutboxConsumer.drain` lag; `make watch`).
- [x] canonical commits succeed correctly while Neo4j is unavailable (the commit path holds no Neo4j
      dependency; projection failure is recorded as visible lag, never canon corruption —
      `tests/unit/test_projection_service.py`, `tests/unit/test_outbox_consumer.py`).

## 2E. Mutation validation, canonical commit, and projection recovery

- [x] Define typed, ordered mutation operations for create/update/retire entity; assert/close fact;
      assert/close belief; create/amend/retire event; and create/retire explicit asset link
      (`backend/domain/operations.py`: 12 discriminated operations).
- [x] Give proposed objects temporary IDs and resolve them deterministically to durable IDs once per
      mutation set. References may target existing objects or earlier co-created operations only
      (`resolve_temporary_ids` derives durable ids from the mutation id so retries are stable;
      unresolved temporary references are rejected).
- [x] Define mutation-set states and legal transitions separately from per-operation review
      decisions. Every transition records actor, time, reason, and correlation/idempotency IDs.
      (`backend/domain/review.py::LEGAL_TRANSITIONS`; `mutation_transitions` is append-only and
      written by the journal and by the commit transaction; `committed`/`rejected` are terminal and
      the status set is a database CHECK constraint — migration `0019_review_workflow`, ADR-014.)
- [x] Centralize authorization, project/branch scope, exact schema version, operation shape,
      references, endpoint types, attributes, cardinality, lifecycle, provenance, and review policy
      in one authoritative validator invoked inside the canonical PostgreSQL transaction.
      (`backend/domain/validation.py::AuthoritativeValidator` runs inside
      `PostgresCanonRepository.apply_commit`; the transaction loads the exact published schema and
      enforces compiled custom types, attributes, predicates, endpoints, temporal flags, and
      cardinality. No caller can widen the allowed types.)
- [x] Split validator results into permanently blocking built-in integrity failures, explicitly
      blocking deterministic author-rule failures, and advisory findings. Phase 2 implements the
      integrity path and evaluator interface; author-rule authoring/runs arrive in Phase 2.5.
      (`ValidationSeverity` = integrity/blocking_rule/advisory; only integrity findings are emitted today.)
- [x] Prove no suppression, import, MCP/proposal transport, admin UI, or autonomy setting can bypass
      a built-in integrity invariant (validation runs inside the repository transaction, so a direct
      repository write with an invalid operation rolls back —
      `tests/unit/test_canon_repository.py::test_apply_commit_runs_authoritative_validation_in_tx`).
- [x] Revalidate every mutable assumption against the transactional head/state used for the commit;
      preview validation is advisory and cannot authorize a later write (`commit` runs the validator
      in-transaction at the locked head; `validate` is a preview only).
- [x] Implement per-operation accept/edit/reject decisions. Editing creates a new operation
      revision and invalidates prior validation rather than mutating accepted review evidence.
      (`operation_decisions` bind to an operation revision; `mutation_operation_revisions` keeps
      every revision; the committed set is the current revisions minus rejected ones, recomputed
      inside the commit transaction. Editing, re-deciding, or retargeting after approval returns
      the set to `needs_review`. There is no stored validation result to invalidate: validation is
      always recomputed, in preview and again in-transaction. `/projects/{id}/mutations` API.)
- [x] Replace the transitional PostgreSQL/Neo4j saga with ADR-012's single PostgreSQL transaction:
      validate/review → envelope + normalized canon → head/revision + outbox (`fa36e87`).
- [x] Make Neo4j application idempotent by immutable commit/mutation IDs and reject a checksum
      mismatch. A retry must return the original result rather than duplicate nodes/edges
      (`Neo4jCanonWriter` checks the marker and raises `RevisionConflict` on checksum drift).
- [x] Return canonical success after the PostgreSQL transaction, independent of Neo4j availability;
      expose projection lag separately and never label stale graph state current.
- [x] Replace commit reconciliation with a projector/replay worker that resumes from a verified
      watermark, quarantines checksum disagreement, and can rebuild one project or all projects
      (`GraphProjectionService.project_pending`/`rebuild`; `make project --lag|--rebuild`).
- [x] Add an outbox consumer seam for derived graph/vector/search updates with retry count, source
      revision, idempotency key, and visible lag (`OutboxConsumer` + `outbox_messages.attempts`/
      `last_error`; `make watch` bounded dev runner).
- [x] Record audit/telemetry for state, latency, operation counts, recovery, and outcome without
      logging canon payloads by default. (`canon.commit`/`canon.commit.failed` structured events carry
      origin/revision/operation count/latency/error type; author-direct commits record an approval
      decision with actor and reason. No canon payloads are logged.)
- [x] Replace cross-store success fault tests with PostgreSQL transaction rollback tests plus
      projector crash/retry/duplicate-delivery/rebuild tests
      (`tests/integration/test_canon_repository.py`, `tests/unit/test_projection_service.py`,
      `tests/integration/test_projection.py`).
- [x] Add concurrent-commit, replay, checksum mismatch, authorization-revocation, stale-schema,
      stale-head, and partial-decision tests (replay/idempotency, checksum mismatch, stale-head,
      crash, partial-decision, and viewer-authorization tests are present, plus a Hypothesis state
      machine over every review action order; true multi-worker concurrency still relies on the
      journal's row lock and unique constraints rather than a multi-process test).

### 2E acceptance

- [x] every accepted canon mutation has one immutable envelope and one resulting revision
- [x] retries are idempotent and concurrent stale-head commits receive a stable conflict response
- [x] a crash at any canonical transaction boundary rolls back, while a projector crash resumes
      without silent loss or duplicate projection records
- [x] rejected or unapproved protected-origin operations never enter normalized canon or any projection
- [x] outbox/projector failure creates visible lag but cannot corrupt or roll back accepted canon

## 2F. Entity authoring and retirement

- [x] Introduce project-scoped product APIs for cursor-paginated entity list/search/read, create,
      edit, link inspection, history, and retire. Require explicit expected revision/ETag on writes.
      (List/search/read/create/edit/retire are implemented at `/api/v1/projects/{project_id}/entities`;
      writes require `expected_revision` and return an `ETag`. Link inspection and per-entity history
      land with 2H.)
- [x] Route every product write through the mutation service; endpoints validate transport shape,
      authorize, and delegate rather than constructing Cypher or journal rows.
      (`CanonEntityService` builds typed operations and commits through `MutationService`.)
- [x] Return one stable nested entity representation across create/read/update/search/history and
      never expose raw Neo4j properties or storage identifiers (`CanonEntityModel`).
- [x] Define display-name collision, slug, alias, and external-identifier behavior without treating
      names as identity. (`backend/domain/canon/naming.py`, ADR-015: duplicate names are advisory;
      slugs are validated, derived when omitted, and unique among active entities; aliases are
      bounded and searchable; an external identifier resolves to one entity per project. Enforced
      in the commit transaction and by a partial unique index — migration `0020_entity_identity`.)
- [x] Implement deterministic structured search by type/name/alias/status before adding richer
      hybrid retrieval in later phases. (Type filter + case-insensitive substring over name, slug,
      and aliases; `include_retired` for status; lookup by external identifier.)
- [x] Make retirement fail or warn predictably for protected/referenced entities according to the
      accepted policy; preserve incoming/outgoing historical references. (Retirement is a
      destructive operation that needs an explicit acknowledgement or review — ADR-014. Retiring
      an entity that live facts/beliefs/events/links still reference is allowed and reported as the
      advisory finding `referenced_entity`; history and references are preserved.)
- [x] Gate or disable existing generic graph mutation endpoints in hosted mode and prove they cannot
      bypass the journal. Keep any local/admin escape hatch clearly unsafe and non-product.
      (Generic entity/edge writes now require `require_local_graph_write`, platform-admin + local-only;
      the generic project-scoped surface moved to `/api/v1/graph/projects/{project_id}/...` so the canonical
      product API owns `/api/v1/projects/{project_id}/entities`.)
- [x] Port the guided character workflow so extracted/generated nodes remain proposals and accepted
      results commit through the same mutation path.
      (`backend/services/canon_character_workflow.py` + `/projects/{id}/characters/{entity_id}/
      questions|proposals`: model output becomes a `model`-origin mutation set in the review
      queue, each operation citing the questionnaire answer it came from; unmappable output is
      discarded with a reason. The legacy graph-writing character endpoints are local-only.)
- [x] Add contract/application/integration tests for permissions, project isolation, pagination,
      revision conflicts, undeclared schema, co-created links, retirement, and direct adapter calls.
      (`tests/contract/test_canon_entities.py`, `tests/contract/test_workspace_scope.py`,
      `tests/integration/test_canon_writer.py`.)

### 2F acceptance

- [x] an author can create, edit, find, inspect, and retire an entity through the canonical API
- [x] two projects can use identical entity IDs/names without collision or reference leakage
      (canonical ids are server-generated per project; isolation proven by contract tests)
- [x] no hosted HTTP write path can create accepted canon without a journal revision
- [x] retired entities remain visible in history and exports but are excluded from active defaults

## 2G. Facts, beliefs, and events

- [x] Add typed project-scoped APIs and application services for fact, belief, and event proposals,
      reads, closure/retirement, and mutation review.
      (`CanonAssertionService` + `/api/v1/projects/{id}/{facts,beliefs,events}`; per-operation review decisions
      remain 2E scope.)
- [x] Enforce predicate endpoint/value shape and cardinality from the exact project schema version.
      The authoritative transaction loads and compiles the referenced published schema, validates
      core and custom propositions, and accounts for earlier operations in the same batch.
- [x] Support entity-reference and scalar proposition objects without encoding references as
      unvalidated strings (discriminated `Proposition` union of entity-ref/string/integer/decimal/boolean/enum/
      quantity/structured objects).
- [x] Enforce belief-holder character compatibility and explicit acquisition/source provenance.
      Allow false, incomplete, and conflicting beliefs independently of canonical fact existence.
      (Holder must be an active Character; `source_ref` required; false/unknown beliefs are first-class.)
- [x] Keep any truth-agreement calculation a labeled query result with compared revision, never a
      stored `is_true` field that silently goes stale (`GET /beliefs/{id}/truth-agreement` derives from the current
      open facts; the `Belief` model has no `is_true`).
- [x] Validate event participants, roles, locations, causes, outcomes, and source references. Store
      unknown/fuzzy world-position input without fabricating exact chronology.
      (Participants/locations must resolve to active entities and roles are non-empty; locations
      must be `Location` entities; causes/outcomes resolve to active entities or events;
      `world_position` is stored verbatim as free text.)
- [x] Implement current-root-revision queries and history queries. Do not expose Phase 3
      branch-overlay or point-in-world-time query parameters as if supported.
      (Reads project the current committed head; per-revision state, diffs, and record history are
      served by the 2H history API.)
- [x] Add safe Neo4j projections for declared high-value predicates and prove they rebuild from
      accepted reified records/journal state. (Every open entity-to-entity fact projects as a typed
      relationship between `CanonEntity` nodes, created and removed with the fact; rebuilt from the
      journal and compared with canon by `CanonEquivalenceService` — ADR-016.)
- [x] Expand the reference world with objective truths, a secret, a false belief, an event, a
      retired entity, scalar/entity-reference facts, and deliberate invalid proposals.
      (`backend/seed/canon.py`, committed through the mutation path by `make seed`: truths, an
      author-only secret, false/unknown/true beliefs, a fuzzy and an unknown event date, a
      close-and-reassert history, a retired entity, a media link, and 4 valid + 21 invalid labelled
      proposals that each name the finding they must raise.)
- [x] Add tests for cardinality conflicts, false beliefs, unknown dates, retired references,
      cross-project objects, invalid holders, projection equivalence, and close/reassert history.
      (All covered: the labelled-proposal suite in `tests/application/test_canon_seed.py`, and
      projection equivalence plus close/reassert history against real PostgreSQL and Neo4j in
      `tests/integration/test_canon_identity_and_equivalence.py`.)

### 2G acceptance

- [x] the same proposition can be objectively true, falsely believed, or unknown without collapsing
      those states
- [x] facts/beliefs/events retain complete provenance and revision history after closure
      (record versions keep every assert/close in commit order; provenance assembles origin,
      decisions, and evidence; proven on PostgreSQL)
- [x] schema cardinality and reference rules hold at the locked canonical head, including within a
      single operation batch (safe per-record rebasing remains Phase 3 work)
- [x] query projections agree with reified accepted canon for the checked revision
      (`CanonEquivalenceService`; `make project PROJECT_ARGS=--verify`)

## 2H. Provenance, revision history, and inspection

- [x] Add revision list/read APIs showing actor/origin, timestamp, summary, operation/decision counts,
      source references, schema version, expected/result head, and recovery status without exposing
      secret payloads to unauthorized users.
      (`GET /revisions`, `GET /revisions/{commit_id}` now carry origin, intent, mutation id,
      decision and evidence counts, and status; the actor is withheld from read-only members.)
- [x] Add entity/fact/belief/event history APIs that map changes back to mutation operations and
      provenance sources (`GET /entities|facts|beliefs|events/{id}/history` over `canon_record_versions`).
- [x] Define and implement the storage-neutral canon query boundary for state at a canonical
      revision, revision-to-revision differences, belief holders/truth-agreement inputs, and full
      evidence provenance. Phase 3 extends the same contract with branch/world/narrative/POV
      evaluation rather than creating a second query layer.
      (`*_at_revision` for entity/fact/belief/event + `diff_revisions` + derived truth agreement +
      `record_provenance`, which assembles commit, origin, review decisions, and evidence.)
- [x] Add immutable named tags pointing to an exact root branch/revision with replacement history
      (`canon_tags`/`canon_tag_history`; `GET/POST /tags`, `DELETE /tags/{name}`, `GET /tags/{name}/history`).
- [x] Generate compensating mutation sets for eligible commit reverts, detect intervening conflicts,
      preview the diff, and require the normal validation/review/commit path. Never delete or amend
      a committed envelope. (`CanonHistoryService.propose_revert`/`commit_revert`: creations are
      removed, entity edits and event amendments are restored to their exact prior state, closed
      facts/beliefs and retired links are re-asserted as new records, records created inside the
      range collapse to one removal, same-record later changes and references from live canon
      outside the revert are reported as conflicts, and retirements are listed for manual handling
      because no un-retire operation exists. A revert is always high-impact.)
- [x] Distinguish security audit records from canon provenance: audit answers who invoked an action;
      provenance explains why a canonical record exists and from which evidence. (Tenancy
      `audit_events` vs `canon_provenance`/`canon_record_versions`.)
- [x] Provide canonical checksum and verification commands for a project/root revision. Verification
      compares journal envelopes, normalized state, branch head, projection watermarks, and
      representative queries (`make verify` → `backend.devtools.verify`).
- [x] Provide an operator projection report for lag, failed delivery, checksum mismatch, and rebuild
      status; never repair checksum disagreement by mutating canon (`GET /projects/{id}/projection`
      exposes status/last_error/failed_deliveries; `make watch`; `make project --rebuild`).
- [x] Record source artefact identifiers and bounded evidence locations, not copied manuscript/media
      content, in provenance metadata. (Every operation accepts up to ten `EvidenceRef`s whose
      fields are identifier-shaped, so content cannot be stored; kept per record in
      `canon_evidence` — ADR-015.)
- [x] Add authorization/redaction tests for history, provenance, audit, and diagnostics.
      (`tests/application/test_provenance_and_history_authz.py`: non-members are refused, another
      project is not found, viewers cannot tag/revert/review, and viewers see origin and evidence
      but not reviewer identities or notes. Audit records and metrics are asserted content-free in
      the archive and metrics tests.)

### 2H acceptance

- [x] every active or historical canonical record can identify the revision and mutation that made
      or closed it (`canon_record_versions` carries record/revision/operation/commit; exposed via the
      history and state-at-revision APIs)
- [x] an operator can distinguish recoverable lag from integrity disagreement without reading story
      content in logs (`/projection` status vs `make verify`)
- [x] checksum verification detects tampering, journal/normalized-state divergence, projection lag,
      and projection checksum mismatch (`backend.devtools.verify`)
- [x] UI/API query results agree at the same revision and explain current state through commit,
      operation, decision, source version, and evidence where recorded
- [x] tags retain their original revision meaning and revert creates new reviewed history
      (tags retain their revision; `commit_revert` applies compensating operations through the
      normal mutation/review path as a new revision)

## 2I. Media identity and explicit evidence links

- [x] Define project-scoped `MediaAsset`, immutable `MediaAssetVersion`, and `AssetLink` models with
      authority, visibility, provenance, source/licence/attribution, lifecycle, checksum/size/type,
      and reserved branch/time applicability (`backend/domain/media.py`; version is frozen).
- [x] Persist lifecycle metadata in PostgreSQL and use opaque object-store keys. Client-visible asset
      IDs are never filesystem paths or storage credentials (migration `0017_media`; `object_key` is opaque).
- [x] Implement explicit reviewed link roles needed by canon (`DEPICTS`, `VOICE_OF`, `SET_AT`,
      `MAP_OF`, `EVIDENCE_FOR`, `ATTACHED_TO`) with endpoint and project constraints (`LinkRole`).
- [x] Make asset links mutation operations so link acceptance/retirement is revisioned and audited.
      Project graph projections are rebuildable; PostgreSQL remains lifecycle authority
      (`LinkAssetOp`/`RetireAssetLinkOp` through the mutation path).
- [x] Support seed/import/operator registration and authorized metadata/link management only. Keep
      hosted upload/finalize and unsafe preview delivery disabled until Phase 4 secure ingestion.
      (`MediaService` registers/links via the journal; no upload/scan/processing endpoints exist.)
- [x] Preserve immutable original bytes already present through the scoped object-store port and
      verify checksum/length on registration, export, and import. (Registration verifies the object
      via `ObjectStorePort.stat`; the archive carries and validates object bytes/checksums.)
- [x] Enforce that rejected, retired, deleted, quarantined, or cross-project assets cannot receive
      new active canonical links (in-transaction validator requires an active asset/version).
- [x] Define deletion/retirement behavior: referenced versions retire and remain auditable; complete
      project deletion follows the existing scoped purge; ordinary APIs never orphan link history.
      (Registration, import, and retirement append an immutable `media_lifecycle_events` row in
      the same transaction — migration `0021_media_lifecycle`, `GET /assets/{id}/lifecycle`.
      Project deletion now also erases the project's canon, journal, schema, and media rows and
      its whole projection; before the closure work those rows were left behind.)
- [x] Add tests for identical asset IDs across projects, opaque keys, checksum mismatch, authority,
      attribution, lifecycle transitions, link review, retirement, and export/import
      (`tests/unit/test_media_service.py`, `tests/contract/test_media.py`,
      `tests/application/test_canon_archive.py`, and against PostgreSQL in
      `tests/integration/test_media_canon.py` and `test_canon_seed_and_purge.py`. The PostgreSQL
      test found that every `link_asset` commit had been failing on a wrong table name; fixed.)

### 2I acceptance

- [x] an immutable media version can serve as explicit evidence without becoming a fact by itself
- [x] no Phase 2 API implies that unimplemented scanning/processing made an asset safe for hosted use
- [x] links cannot cross projects or widen source visibility/authority
- [x] originals and required attribution round-trip when export policy permits them (archive v3)

## 2J. Project shell and authoring UI

- [x] Convert the frontend to TypeScript before product state and generated API types proliferate
      (`tsconfig.json`; `App.tsx`/`main.tsx`/pages).
- [x] Generate a typed client from the committed OpenAPI contract and prevent hand-maintained API
      types from becoming a second contract (`pnpm run gen:api` → `frontend/src/api/schema.ts`,
      committed; `client.ts` composes it; `make frontend-api-check` fails on generated-schema drift).
- [x] Add routing and server-state libraries with workspace/project encoded in routes and cache keys.
      Never cache one project's response under an unscoped key (React Router `/projects/:projectId`;
      TanStack Query keys include workspace + project).
- [x] Build a project shell with project switcher, active schema/revision indicator, navigation,
      permission state, degraded-store status, and no-provider-safe behavior.
      (Workspace/project switcher, canonical head and projection lag, the caller's role from
      `GET /access` with write actions hidden for read-only members, an import banner when a
      restore is not complete, and a clear message when no AI provider is configured.)
- [x] Build world/entity list, filters, detail, create/edit, typed property, relationship, history,
      and retirement views. Forms derive from the governed schema rather than hardcoded type bags.
      (Type/retired/search filters; create and edit forms built from `GET /schemas/vocabulary` —
      typed fields for extension types, key/value attributes for core types; aliases and slug;
      advisory name-collision warning; facts in both directions with assert/close; acknowledged
      retirement. Enum, quantity, and structured fact values are entered as JSON because the
      vocabulary does not carry enum domains.)
- [x] Build a character-focused view over the same entity/fact/belief/event APIs; do not create a
      parallel persistence model. (Attributes, aliases, facts, beliefs with the derived truth
      agreement labelled as derived, events, and the guided workflow, which ends at the proposal
      to review. Beliefs and events are read-only here; they are authored through the API or a
      proposal.)
- [x] Build schema draft/validate/publish and compatibility-report views with clear project scope.
      (Version list, definition editor, validation with errors, compatibility category and
      migration plan, draft, publish.)
- [x] Build mutation preview/review UI with operation diffs, validation failures, per-operation
      decisions where required, expected/current head, and stable conflict recovery.
      (Queue with status filter; per-operation before/after, accept/reject with note, edit as a
      new revision; blocking vs advisory validation issues; stale banner with retarget;
      approve/reject/commit; transition log. A commit is shown as accepted only after it returns.)
- [x] Build revision/provenance inspection and basic asset metadata/link panels. Do not implement
      media upload, processing, or similarity UI in this phase. (Revision list, per-record
      provenance that says when reviewer details were withheld, revert preview and acknowledged
      commit, asset list with versions and lifecycle audit, links on an entity. Read-only for
      assets: registration and linking remain API/operator actions.)
- [x] Clearly label draft, proposed, canonical, retired, and recovery-pending state. Never
      optimistically display an ambiguous commit as accepted (entity lifecycle badges + projection
      current/lag/error status).
- [x] Preserve keyboard operation, focus/error management, accessible labels, contrast, and reduced
      motion; add automated accessibility coverage for the primary workflow. (Labelled controls,
      errors associated with fields and announced, visible focus, reduced-motion support; axe
      assertions in the component tests and WCAG 2.1 A/AA checks including contrast on five pages
      of the browser run.)
- [x] Add component tests with generated contract fixtures and an end-to-end browser test for the
      full Phase 2 vertical workflow. (34 vitest component tests with fixtures typed from the
      generated contract; `make e2e` runs a Playwright test against a real API on a disposable
      PostgreSQL database: schema publish, create, edit with stale-head recovery, provenance,
      review and commit of a model proposal, acknowledged retirement, revision list, export.)

### 2J acceptance

- [x] an editor can complete the Phase 2 workflow without generic graph endpoints or database tools
      (proven by the browser end-to-end test)
- [x] stale-head and validation conflicts preserve entered form data and present a recovery path
      (component test and the browser test, which makes the head move while a form is open)
- [x] workspace/project switches cannot display stale data from the previous scope (query keys are
      scoped by workspace + project)
- [x] the core workflow passes keyboard and automated accessibility checks (a keyboard-only
      browser test creates and retires an entity with a visible focus indicator, and axe
      WCAG 2.1 A/AA checks pass; a manual screen-reader pass has not been done)

## 2K. Archive upgrade, restore, and rebuild

- [x] Define archive format v2 with a canonical manifest, format/schema versions, project settings,
      root branch/head, schemas, entities, facts, beliefs, events, revisions, mutation/decision
      history, provenance, asset metadata/links, and permitted originals.
      (Implemented as `novel-engine/canon-archive` format v3. Accepted commits carry their source
      origin, actor, mutation metadata, and review decisions; uncommitted/rejected proposal history
      is not part of the canonical project archive.)
- [x] Use canonical serialization and per-entry plus whole-manifest checksums. Strictly reject path
      traversal, duplicate identities, oversized/undeclared entries, checksum mismatch, invalid
      operation order, and cross-project references before durable writes
      (`CanonArchiveService._validate`; media entries count toward the entry cap, decoded object
      bytes have a hard cap, and tests cover checksums, traversal, target preflight, and scoped refs.)
- [x] Retain a bounded v1 archive reader/upgrader for the Phase 1 graph/object format, or record an
      explicit owner decision to drop it while prototype data remains disposable. Never interpret a
      v1 property bag as validated v2 canon without a review/migration report.
      (`backend/services/graph_archive_upgrade.py` + `make archive ARCHIVE_ARGS=--upgrade-graph`:
      a checksum-verified, size-capped reader that files what fits the canon vocabulary as an
      `import`-origin proposal needing approval and reports everything else — ADR-016.)
- [x] Make import resumable/idempotent by archive/import ID and stage it through validation before
      canonical commit. A partial import must be retryable or removable without becoming visible as
      a successful project. (`archive_imports` ledger — migration `0022_archive_imports`: recorded
      after validation and target preflight and before the first write, `completed` only after
      replay, projection rebuild, and verification; a failed import resumes on retry or is removed
      with `--remove-import`; the project's `/projection` status and `/imports` expose it.)
- [x] Define preserve/remap rules for stable project-owned IDs, provenance actors, schema versions,
      root branch, object keys, and external aliases when importing into another workspace/project
      (object keys remapped + deduped by checksum; schema versions mapped by content hash; commit ids
      are deterministic and target-scoped; entity-reference project IDs are remapped; trusted
      restore commits use migration provenance; actor/alias carry-through is recorded as-is).
- [x] Rebuild Neo4j projections and derived indexes from accepted archive/journal data, then compare
      canonical checksums and representative queries. (Import rebuilds the graph projection and
      then requires `CanonEquivalenceService` to pass: journal replay vs normalized canon vs the
      projection read through product queries. Canon has no derived vector index in Phase 2.)
- [x] Include export/import security audit events without story content and require owner-level
      authorization for full-history exports. (`export_project_as`/`import_project_as`/
      `remove_import_as` require project administration and write `audit_events` rows with counts
      and checksums only; `POST /projects/{id}/archive/export`. Import remains an operator CLI.)
- [x] Add clean-environment round-trip tests containing false beliefs, retired records, explicit
      media links/originals, schema extensions, and complete mutation history.
      (`tests/integration/test_archive_clean_environment.py` restores the reference world into a
      separate PostgreSQL database, an empty object store, and an empty graph scope. The reference
      world uses the core schema; schema-extension restore is covered in-memory by content hash.)

### 2K acceptance

- [x] a Phase 2 project round-trips into a clean environment with the same canonical checksum
      (same-scope envelope checksums are preserved; a cross-project restore rewrites
      project-scoped entity references, so those envelopes are recomputed and the restore is
      proven by journal/normalized/projection equivalence instead)
- [x] omitted derived data rebuilds without changing accepted canon (import rebuilds the projection
      and verifies it; a rebuild reproduces the same projection checksum)
- [x] corrupted or malicious archives fail before exposing a partially accepted project
- [x] export never includes credentials, sessions, unrelated tenant data, or unapproved originals
      (archive carries canon objects/bytes and media identity only)

## 2L. Security, quality, and exit evidence

- [x] Extend the threat model for schema complexity attacks, journal replay/checksum substitution,
      journal/state divergence, projection poisoning or false freshness, ID/reference confusion,
      archive bombs/path traversal, generic API
      bypass, asset key enumeration, and provenance content leakage.
      (Threat/auth tests in `tests/application/test_auth_threats.py`; the schema/archive/journal/
      projection concerns are asserted by their feature tests, catalogued in `phase-2-evidence.md`.)
- [x] Add a critical invariant matrix covering tenant/project isolation, schema versioning,
      fact/belief separation, cardinality, retirement, optimistic heads, mutation decisions,
      idempotency, transaction/projector recovery, outbox lag, media links, and archive round-trip
      ("Critical invariant matrix" in `phase-2-evidence.md`).
- [x] Include invariant-classification tests proving author rules default to warning, blocking is
      explicit, incomplete/extraction-dependent evaluation cannot block, and built-in integrity
      checks cannot be suppressed or weakened. (`InvariantEvaluator` seam in
      `backend/domain/validation.py` + `tests/unit/test_invariant_classification.py`. Rule
      *authoring* and persistence remain Phase 2.5.)
- [x] Maintain hermetic unit/application/contract tests with in-memory ports; keep PostgreSQL/Neo4j
      behavior in disposable integration tests and browser workflows in e2e.
- [x] Add property/state-machine tests for schema compilation, mutation transitions, revision heads,
      and recovery. Add mutation testing for the highest-risk validators if tooling is reliable.
      (Hypothesis: `tests/unit/test_properties.py`. No mutation-testing tool was adopted; instead
      the review state machine was run against three deliberately broken invariants and detects
      all of them.)
- [x] Expand the two-tenant deterministic reference world with intentionally colliding IDs/names and
      labeled valid/invalid canon proposals. (A second tenant's project reuses the same entity and
      fact ids, names, and slugs with different truths; isolation is asserted in memory and on
      PostgreSQL + Neo4j.)
- [x] Add migration tests from the supported Phase 1 schema and archive v1 policy; verify reset/seed
      and empty-database paths. (`tests/integration/test_phase_upgrade_migrations.py` upgrades
      real Phase 1 rows and pre-review canon to head and rolls back and forward; the archive v1
      policy is the upgrader above; reset is asserted to clear every canon table.)
- [x] Add Sentry spans/metrics and Grafana-ready counters for commit states, conflicts, recovery,
      outbox lag, schema failures, and archive operations without recording canon bodies.
      (`backend/core/metrics.py`: Prometheus text format at `/health/metrics`, off by default and
      token-protected; labels restricted to fixed vocabularies; a Sentry span around the commit
      transaction when error reporting is enabled.)
- [x] Set and measure initial performance budgets for paginated entity reads, validation, small
      commits, history queries, and representative exports; correctness remains the release gate.
      (`tests/integration/test_performance_budgets.py`; measurements in `phase-2-evidence.md`.)
- [x] Run `make check`, integration, e2e, migration verification, dependency audit, secret scan,
      archive/restore, recovery fault matrix, and clean-host workflows from a clean checkout.
      (All of these ran locally and are recorded in `phase-2-evidence.md`, including e2e and a
      real export/restore; the clean-*host* run and the first hosted CI run are owner items.)
- [x] Create `phase-2-evidence.md` with commit, migration/checksum inventory, OpenAPI checksum, test
      counts, known limitations, recovery results, archive checksum comparison, and owner sign-off
      (`plan/phase-2-evidence.md`; owner sign-off pending).

### 2L acceptance

- [x] all critical invariants have automated positive and adversarial coverage (see the invariant
      matrix in `phase-2-evidence.md`; property tests search for counterexamples)
- [x] no critical/high unaccepted dependency or security finding remains (`make audit` clean)
- [x] failure injection proves canonical transactions roll back cleanly and projections replay or
      stop for explicit operator action
- [x] diagnostics and telemetry expose state without story text, secrets, or raw media (redacted logs;
      no canon bodies logged)
- [ ] a clean install completes the authoring → commit → history → export/restore workflow
      (automated against a fresh database by the end-to-end browser test and the clean-environment
      restore test; a rehearsal on a clean *host* remains the owner's 2A item)

## Suggested implementation slices

1. **P2-00 — Contract and ADR:** close/accept Phase 1, refresh the baseline, apply the accepted
   foundation decisions plus ADR-012/ADR-013, freeze domain language, and reserve the cross-phase
   author-rule/query record contracts.
2. **P2-01 — Root revision and domain kernel:** typed entity/proposition/fact/belief/event,
   provenance, core schema, root branch, genesis revision, and pure invariant tests.
3. **P2-02 — Governed schema vertical slice:** PostgreSQL versions, compiler/cache, publish API,
   seed migration, and generated form/JSON Schema proof.
4. **P2-03 — PostgreSQL canon transaction:** journal/head/outbox plus normalized/versioned canon,
   authoritative validator, record/dependency preconditions, and transactional fault tests.
5. **P2-04 — Replayable graph projection:** idempotent outbox projector, watermarks, lag health,
   full replay/rebuild, verification, and Neo4j-unavailable commit tests.
6. **P2-05 — Fact/belief/event canon:** reified records, projections, schema/cardinality rules,
   false-belief fixture, history, and product APIs.
7. **P2-06 — Review and concurrency:** operation decisions, author/model/import policies,
   stale-head UX/API, checksum verification, reconciler CLI, and outbox lag.
8. **P2-07 — Canon reasoning:** point-in-revision query/history/provenance, immutable tags,
   compensating revert proposals, and invariant enforcement classification.
9. **P2-08 — Evidence assets:** asset/version metadata, immutable originals already in object
   storage, explicit reviewed links, retirement, and archive integration.
10. **P2-09 — Authoring frontend:** TypeScript/client/router/query foundation, project shell,
   schema/entity/character/mutation/history flows, and accessibility tests.
11. **P2-10 — Archive and restore:** format v2, v1 policy/upgrader, staged import, projection rebuild,
    clean-environment checksum comparison, and audit.
12. **P2-11 — Exit audit:** complete invariant/fault/security/e2e matrices, clean-host rehearsal,
    evidence document, and owner acceptance.

Do not start P2-03 durable journal migrations until P2-00 settles authority and head ownership. P2-07
may begin after the domain, scope, and mutation contracts stabilize. The frontend foundation may
start after the first OpenAPI contracts are frozen, but UI completion follows working backend
vertical slices rather than mocks alone.

## Phase 2 exit gate

- [x] core and project schema versions validate every accepted canonical record
      (in-transaction validator + recorded `schema_version_id`, including project-extension types
      and predicates)
- [x] author edits produce audited, provenance-rich, revisioned commits through one canonical path
- [x] model/import/high-impact proposals cannot bypass required human decisions
      (the review gate is evaluated inside the commit transaction — ADR-014)
- [x] objective facts and character beliefs are distinct in domain, storage, API, and UI
- [x] entities and evidence links can be retired without erasing accepted history
- [x] stale/concurrent commits fail predictably and retries cannot duplicate mutations
- [x] PostgreSQL commits are atomic; Neo4j interruption produces only observable, replayable
      projection lag
- [x] media identity and explicit evidence links obey project scope, authority, and lifecycle rules
      (with an immutable lifecycle audit)
- [x] two projects cannot read, write, link, restore, or reference each other's canon/assets
- [x] a complete project archive restores into a clean environment with the same canonical checksum
      (`tests/integration/test_archive_clean_environment.py`; see the 2K note on cross-project
      checksums. The clean-*host* rehearsal remains an owner item under 2A.)
- [x] the project shell supports entity/schema/canon review and history without generic graph tools
- [x] one canon query boundary supports revision state, differences, and provenance; tags and revert
      proposals preserve rather than rewrite history
- [x] built-in integrity rules are permanently blocking and author-rule contracts default to warning
      (integrity is non-suppressible; the evaluator seam classifies rule results; rule authoring is
      Phase 2.5)
- [ ] Phase 2 evidence, known deferrals, and owner acceptance are recorded before Phase 3 starts
      (`phase-2-evidence.md` records evidence + deferrals; owner acceptance is pending)

## Definition of done for each slice

A slice is complete only when domain behavior, API contracts, authorization, persistence,
migrations, recovery/failure behavior, tests, frontend behavior where applicable, observability,
archive impact, and plan status agree. A Neo4j write or successful UI happy path alone is not a
completed canon feature.
