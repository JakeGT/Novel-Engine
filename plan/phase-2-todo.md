# Phase 2 todo — Trustworthy canon

- Status: Draft execution plan; foundation decisions accepted, implementation has not started
- Scope: CAN-002–CAN-010 and the Phase 2 portions of AST-001/AST-004/AST-008 and EXT-003–EXT-006
- Depends on: Phase 1 technical foundation and acceptance of the remaining owner/operational exit items
- Architecture: ADR-003, ADR-004, ADR-005, ADR-009, ADR-010, and ADR-011
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
- typed mutation sets, per-operation decisions, validation, optimistic concurrency, and idempotency
- a PostgreSQL journal/outbox and idempotent Neo4j application/recovery protocol
- project-scoped entity/canon APIs and a usable world/character authoring interface
- media asset/version identity, authority, provenance, explicit links, and archive semantics
- a complete archive containing canonical history and permitted immutable originals

Phase 2 deliberately excludes:

- branch forks, inherited overlays, comparison, and merges (Phase 3)
- world-time query semantics, interval replacement, and narrative-order behavior (Phase 3)
- POV knowledge filtering and secret-aware context assembly (Phase 3)
- story hierarchy, scene plans, canonical scene documents, and prose generation (Phase 4+)
- consistency runs and model critics beyond domain invariant validation (later phases)
- public media upload, scanning, transcoding, captioning, transcription, segmentation, embeddings,
  similarity retrieval, or provider media delivery (Phase 4+)
- real-time co-editing or CRDTs
- background job infrastructure except the durable outbox/reconciliation seam required by commits

Phase 2 stores branch/time/visibility fields needed for compatibility where the accepted data model
requires them, but only the root branch and system-revision semantics are executable. APIs must not
claim that deferred temporal or POV rules are enforced.

## Accepted Phase 2 foundation decisions

The project owner accepted these decisions on 2026-10-08; ADR-011 records their durable rationale
and commit/recovery protocol.

1. **Author edit path:** ordinary author-originated, low-impact edits may be committed
   directly after showing their validation/diff, but still create a mutation set, journal entry,
   revision, provenance, and outbox records. Model, import, bulk, destructive, schema-changing, and
   conflict-resolution proposals require a distinct human approval action.
2. **Branch/time boundary:** create one root branch per project and implement system
   revision concurrency only. Keep fork/merge and world/narrative-time evaluation in Phase 3.
3. **Graph authority:** Neo4j remains the canonical query state for accepted story
   records; PostgreSQL owns workflow state, idempotency, the immutable commit envelope, branch-head
   coordination, recovery, and outbox delivery. Neither store may independently report a commit as
   complete.
4. **Media boundary:** implement metadata, immutable version identity, explicit links,
   lifecycle, provenance, and archive behavior only. Do not expose hosted uploads until the secure
   ingestion pipeline exists.
5. **Existing generic graph API:** retain it only as an explicitly enabled local
   development/admin surface while product APIs use the mutation service. Hosted mode disables all
   mutation paths that bypass the journal.

ADR-011 records decisions 1–3 and 5. Decision 4 follows ADR-010 and is restated in ADR-011 as the
Phase 2 delivery boundary.

## Completion dashboard

| Workstream | Status | Depends on |
| --- | --- | --- |
| 2A. Phase 1 closeout and Phase 2 contract | In progress (ADR-011, glossary, baseline, compatibility rules recorded; owner acceptance, hosted CI run, and clean-host rehearsal are external) | Phase 1 evidence |
| 2B. Canon domain and core schema | Implemented | 2A |
| 2C. Project schema governance and compiler | Implemented | 2B |
| 2D. Revision, journal, outbox, and storage migrations | Implemented | 2A–2C |
| 2E. Mutation validation, commit saga, and recovery | In progress (typed operations, validation, saga, reconciler, fault-injection tests implemented; per-operation decisions, outbox consumer, mutation-set state machine, and telemetry deferred) | 2B–2D |
| 2F. Entity authoring and retirement | Not started | 2C–2E |
| 2G. Facts, beliefs, and events | Not started | 2B–2F |
| 2H. Provenance and history inspection | Not started | 2D–2G |
| 2I. Media identity and explicit evidence links | Not started | 2B–2E |
| 2J. Project shell and authoring UI | Not started | 2F–2I APIs |
| 2K. Archive upgrade, restore, and rebuild | Not started | 2C–2I |
| 2L. Security, quality, and exit evidence | Not started | all workstreams |

## 2A. Phase 1 closeout and Phase 2 contract

- [ ] Obtain owner acceptance of `phase-1-evidence.md` and record the accepting commit/date.
- [ ] Run the first clean-checkout hosted CI workflow and link its immutable result.
- [ ] Complete or explicitly time-box the clean-host and coordinated backup/restore rehearsal; do
      not mislabel project archive round-tripping as a database backup.
- [x] Refresh `12-current-gap-analysis.md` from the Phase 1 implementation rather than its Phase 0
      baseline, and reconcile historical phase labels in `10-backlog.md` with this plan.
      (A "Phase 1 implementation update" section was appended to `12-current-gap-analysis.md`.)
- [x] Write and accept an ADR for canon authority, root-branch head ownership, commit saga states,
      failure recovery, and the author direct-commit/review boundary.
- [x] Freeze a Phase 2 vocabulary/glossary for entity, proposition, fact, belief, event, revision,
      mutation set, operation, decision, journal entry, projection, and provenance.
      (`plan/phase-2-glossary.md`.)
- [x] Capture the starting OpenAPI checksum, PostgreSQL/Neo4j migration heads, archive format,
      reference-seed checksum, test counts, and known deferred items. (`plan/phase-2-baseline.md`.)
- [x] Define forward/backward compatibility rules for API DTOs, archive versions, schema versions,
      prompt schemas, and Neo4j projections before adding durable records.
      (`plan/phase-2-baseline.md` "Compatibility rules".)

### 2A acceptance

- [ ] the Phase 1 evidence has an explicit owner decision
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

## 2D. Revisions, journal, outbox, and storage migrations

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
      (Scoped identity constraints model reified facts/beliefs/events; projection rebuild behavior is
      deferred to the retrieval/consistency work in Phases 3+.)
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
- [x] a branch head cannot advance twice from the same expected revision
- [x] the same commit ID/checksum cannot be reused for different operations
- [x] no canonical projection depends on Neo4j internal IDs
      (No `elementId(` in the graph layer; the legacy `GraphManager.delete_edge` still keys on `id(r)`
      but is unused by canon/projection paths and is not exposed.)

## 2E. Mutation validation, commit saga, and recovery

- [x] Define typed, ordered mutation operations for create/update/retire entity; assert/close fact;
      assert/close belief; create/amend/retire event; and create/retire explicit asset link
      (`backend/domain/operations.py`: 12 discriminated operations).
- [x] Give proposed objects temporary IDs and resolve them deterministically to durable IDs once per
      mutation set. References may target existing objects or earlier co-created operations only
      (`resolve_temporary_ids` derives durable ids from the mutation id so retries are stable;
      unresolved temporary references are rejected).
- [ ] Define mutation-set states and legal transitions separately from per-operation review
      decisions. Every transition records actor, time, reason, and correlation/idempotency IDs.
      (States and legal transitions are defined in `backend/domain/journal.py`; explicit transition
      enforcement with actor/time/reason recording is not yet wired through the service.)
- [x] Validate authorization, project/branch scope, expected head, exact schema version, operation
      shape, references, endpoint types, cardinality, lifecycle, and provenance before reservation
      (`MutationService.validate`: authorization, expected head, duplicate ids, entity existence via
      an injected lookup, registry proposition/type checks; exact published-schema-version matching
      and full cardinality/lifecycle enforcement land with 2F/2G as the entity lookup is wired).
- [x] Revalidate all mutable assumptions immediately before graph application; a previously valid
      preview is not permission to commit against a changed head or changed authorization
      (`commit` re-authorizes, re-validates, and re-reads the branch head before reserving).
- [ ] Implement per-operation accept/edit/reject decisions. Editing creates a new operation
      revision and invalidates prior validation rather than mutating accepted review evidence.
      (Mutation-level decisions are recorded; per-operation decisions and edit-as-new-revision are
      deferred to the review slice 2F/2G.)
- [x] Implement the accepted PostgreSQL/Neo4j saga with explicit recoverable states
      (`MutationService.commit`: reserve → append prepared envelope → idempotent graph apply →
      finalize/outbox).
- [x] Make Neo4j application idempotent by immutable commit/mutation IDs and reject a checksum
      mismatch. A retry must return the original result rather than duplicate nodes/edges
      (`Neo4jCanonWriter` checks the marker and raises `RevisionConflict` on checksum drift).
- [x] Never return success until the graph application and PostgreSQL final state agree. Return a
      retryable operation status—not a generic success or destructive rollback—when reconciliation
      is required (`commit` succeeds only after `finalize`; `reconcile` returns a status).
- [x] Implement a reconciler that can finish or classify prepared/graph-applied/finalization-failed
      commits and requeue outbox work. Make it callable as a CLI before adding a worker
      (`backend/devtools/reconcile.py`, `make reconcile COMMIT=...`).
- [ ] Add an outbox consumer seam for derived graph/vector/search updates with retry count, source
      revision, idempotency key, and visible lag. Phase 2 may use a bounded in-process/dev runner;
      accepted canon must not depend on derived delivery completing synchronously.
      (Idempotent outbox messages are appended in the finalize transaction; the consumer/runner is
      deferred to the projection rebuild slice.)
- [ ] Record audit/telemetry for state, latency, operation counts, recovery, and outcome without
      logging canon payloads by default. (No canon payloads are logged; structured commit
      telemetry is deferred.)
- [x] Add deterministic fault-injection tests at every boundary: before/after reservation, during
      Neo4j application, after graph commit, before head finalization, and during outbox delivery
      (in-memory writer fault injection + crash-mid-apply recovery test; the integration writer test
      covers apply/marker/checksum).
- [x] Add concurrent-commit, replay, checksum mismatch, authorization-revocation, stale-schema,
      stale-head, and partial-decision tests (replay/idempotency, checksum mismatch, stale-head, and
      crash tests are present; true multi-worker concurrency relies on the journal constraints).

### 2E acceptance

- [x] every accepted canon mutation has one immutable envelope and one resulting revision
- [x] retries are idempotent and concurrent stale-head commits receive a stable conflict response
- [x] a crash at any injected boundary is recoverable without silent loss or duplicate canon
- [x] rejected operations never appear in accepted graph state
- [x] outbox failure creates visible index lag but cannot corrupt or roll back accepted canon

## 2F. Entity authoring and retirement

- [ ] Introduce project-scoped product APIs for cursor-paginated entity list/search/read, create,
      edit, link inspection, history, and retire. Require explicit expected revision/ETag on writes.
- [ ] Route every product write through the mutation service; endpoints validate transport shape,
      authorize, and delegate rather than constructing Cypher or journal rows.
- [ ] Return one stable nested entity representation across create/read/update/search/history and
      never expose raw Neo4j properties or storage identifiers.
- [ ] Define display-name collision, slug, alias, and external-identifier behavior without treating
      names as identity.
- [ ] Implement deterministic structured search by type/name/alias/status before adding richer
      hybrid retrieval in later phases.
- [ ] Make retirement fail or warn predictably for protected/referenced entities according to the
      accepted policy; preserve incoming/outgoing historical references.
- [ ] Gate or disable existing generic graph mutation endpoints in hosted mode and prove they cannot
      bypass the journal. Keep any local/admin escape hatch clearly unsafe and non-product.
- [ ] Port the guided character workflow so extracted/generated nodes remain proposals and accepted
      results commit through the same mutation path.
- [ ] Add contract/application/integration tests for permissions, project isolation, pagination,
      revision conflicts, undeclared schema, co-created links, retirement, and direct adapter calls.

### 2F acceptance

- [ ] an author can create, edit, find, inspect, and retire an entity through the canonical API
- [ ] two projects can use identical entity IDs/names without collision or reference leakage
- [ ] no hosted HTTP write path can create accepted graph state without a journal revision
- [ ] retired entities remain visible in history and exports but are excluded from active defaults

## 2G. Facts, beliefs, and events

- [ ] Add typed project-scoped APIs and application services for fact, belief, and event proposals,
      reads, history, closure/retirement, and mutation review.
- [ ] Enforce predicate endpoint/value shape and cardinality from the exact project schema version.
- [ ] Support entity-reference and scalar proposition objects without encoding references as
      unvalidated strings.
- [ ] Enforce belief-holder character compatibility and explicit acquisition/source provenance.
      Allow false, incomplete, and conflicting beliefs independently of canonical fact existence.
- [ ] Keep any truth-agreement calculation a labeled query result with compared revision, never a
      stored `is_true` field that silently goes stale.
- [ ] Validate event participants, roles, locations, causes, outcomes, and source references. Store
      unknown/fuzzy world-position input without fabricating exact chronology.
- [ ] Implement current-root-revision queries and history queries. Do not expose Phase 3
      branch-overlay or point-in-world-time query parameters as if supported.
- [ ] Add safe Neo4j projections for declared high-value predicates and prove they rebuild from
      accepted reified records/journal state.
- [ ] Expand the reference world with objective truths, a secret, a false belief, an event, a
      retired entity, scalar/entity-reference facts, and deliberate invalid proposals.
- [ ] Add tests for cardinality conflicts, false beliefs, unknown dates, retired references,
      cross-project objects, invalid holders, projection equivalence, and close/reassert history.

### 2G acceptance

- [ ] the same proposition can be objectively true, falsely believed, or unknown without collapsing
      those states
- [ ] facts/beliefs/events retain complete provenance and revision history after closure
- [ ] schema cardinality and reference rules hold under concurrent commits
- [ ] query projections agree with reified accepted canon for the checked revision

## 2H. Provenance, revision history, and inspection

- [ ] Add revision list/read APIs showing actor/origin, timestamp, summary, operation/decision counts,
      source references, schema version, expected/result head, and recovery status without exposing
      secret payloads to unauthorized users.
- [ ] Add entity/fact/belief/event history APIs that map changes back to mutation operations and
      provenance sources.
- [ ] Distinguish security audit records from canon provenance: audit answers who invoked an action;
      provenance explains why a canonical record exists and from which evidence.
- [ ] Provide canonical checksum and verification commands for a project/root revision. Verification
      compares journal envelopes, graph commit markers, branch head, and representative projections.
- [ ] Provide an operator reconciliation report for missing/mismatched graph applications, stuck
      reservations, and outbox lag; never repair checksum disagreement automatically.
- [ ] Record source artefact identifiers and bounded evidence locations, not copied manuscript/media
      content, in provenance metadata.
- [ ] Add authorization/redaction tests for history, provenance, audit, and diagnostics.

### 2H acceptance

- [ ] every active or historical canonical record can identify the revision and mutation that made
      or closed it
- [ ] an operator can distinguish recoverable lag from integrity disagreement without reading story
      content in logs
- [ ] checksum verification detects tampering, missing graph state, and envelope mismatch

## 2I. Media identity and explicit evidence links

- [ ] Define project-scoped `MediaAsset`, immutable `MediaAssetVersion`, and `AssetLink` models with
      authority, visibility, provenance, source/licence/attribution, lifecycle, checksum/size/type,
      and reserved branch/time applicability.
- [ ] Persist lifecycle metadata in PostgreSQL and use opaque object-store keys. Client-visible asset
      IDs are never filesystem paths or storage credentials.
- [ ] Implement explicit reviewed link roles needed by canon (`DEPICTS`, `VOICE_OF`, `SET_AT`,
      `MAP_OF`, `EVIDENCE_FOR`, `ATTACHED_TO`) with endpoint and project constraints.
- [ ] Make asset links mutation operations so link acceptance/retirement is revisioned and audited.
      Project graph projections are rebuildable; PostgreSQL remains lifecycle authority.
- [ ] Support seed/import/operator registration and authorized metadata/link management only. Keep
      hosted upload/finalize and unsafe preview delivery disabled until Phase 4 secure ingestion.
- [ ] Preserve immutable original bytes already present through the scoped object-store port and
      verify checksum/length on registration, export, and import.
- [ ] Enforce that rejected, retired, deleted, quarantined, or cross-project assets cannot receive
      new active canonical links.
- [ ] Define deletion/retirement behavior: referenced versions retire and remain auditable; complete
      project deletion follows the existing scoped purge; ordinary APIs never orphan link history.
- [ ] Add tests for identical asset IDs across projects, opaque keys, checksum mismatch, authority,
      attribution, lifecycle transitions, link review, retirement, and export/import.

### 2I acceptance

- [ ] an immutable media version can serve as explicit evidence without becoming a fact by itself
- [ ] no Phase 2 API implies that unimplemented scanning/processing made an asset safe for hosted use
- [ ] links cannot cross projects or widen source visibility/authority
- [ ] originals and required attribution round-trip when export policy permits them

## 2J. Project shell and authoring UI

- [ ] Convert the frontend to TypeScript before product state and generated API types proliferate.
- [ ] Generate a typed client from the committed OpenAPI contract and prevent hand-maintained API
      types from becoming a second contract.
- [ ] Add routing and server-state libraries with workspace/project encoded in routes and cache keys.
      Never cache one project's response under an unscoped key.
- [ ] Build a project shell with project switcher, active schema/revision indicator, navigation,
      permission state, degraded-store status, and no-provider-safe behavior.
- [ ] Build world/entity list, filters, detail, create/edit, typed property, relationship, history,
      and retirement views. Forms derive from the governed schema rather than hardcoded type bags.
- [ ] Build a character-focused view over the same entity/fact/belief/event APIs; do not create a
      parallel persistence model.
- [ ] Build schema draft/validate/publish and compatibility-report views with clear project scope.
- [ ] Build mutation preview/review UI with operation diffs, validation failures, per-operation
      decisions where required, expected/current head, and stable conflict recovery.
- [ ] Build revision/provenance inspection and basic asset metadata/link panels. Do not implement
      media upload, processing, or similarity UI in this phase.
- [ ] Clearly label draft, proposed, canonical, retired, and recovery-pending state. Never optimistically
      display an ambiguous commit as accepted.
- [ ] Preserve keyboard operation, focus/error management, accessible labels, contrast, and reduced
      motion; add automated accessibility coverage for the primary workflow.
- [ ] Add component tests with generated contract fixtures and an end-to-end browser test for the
      full Phase 2 vertical workflow.

### 2J acceptance

- [ ] an editor can complete the Phase 2 workflow without generic graph endpoints or database tools
- [ ] stale-head and validation conflicts preserve entered form data and present a recovery path
- [ ] workspace/project switches cannot display stale data from the previous scope
- [ ] the core workflow passes keyboard and automated accessibility checks

## 2K. Archive upgrade, restore, and rebuild

- [ ] Define archive format v2 with a canonical manifest, format/schema versions, project settings,
      root branch/head, schemas, entities, facts, beliefs, events, revisions, mutation/decision
      history, provenance, asset metadata/links, and permitted originals.
- [ ] Use canonical serialization and per-entry plus whole-manifest checksums. Strictly reject path
      traversal, duplicate identities, oversized/undeclared entries, checksum mismatch, invalid
      operation order, and cross-project references before durable writes.
- [ ] Retain a bounded v1 archive reader/upgrader for the Phase 1 graph/object format, or record an
      explicit owner decision to drop it while prototype data remains disposable. Never interpret a
      v1 property bag as validated v2 canon without a review/migration report.
- [ ] Make import resumable/idempotent by archive/import ID and stage it through validation before
      canonical commit. A partial import must be retryable or removable without becoming visible as
      a successful project.
- [ ] Define preserve/remap rules for stable project-owned IDs, provenance actors, schema versions,
      root branch, object keys, and external aliases when importing into another workspace/project.
- [ ] Rebuild Neo4j projections and derived indexes from accepted archive/journal data, then compare
      canonical checksums and representative queries.
- [ ] Include export/import security audit events without story content and require owner-level
      authorization for full-history exports.
- [ ] Add clean-environment round-trip tests containing false beliefs, retired records, explicit
      media links/originals, schema extensions, and complete mutation history.

### 2K acceptance

- [ ] a Phase 2 project round-trips into a clean environment with the same canonical checksum
- [ ] omitted derived data rebuilds without changing accepted canon
- [ ] corrupted or malicious archives fail before exposing a partially accepted project
- [ ] export never includes credentials, sessions, unrelated tenant data, or unapproved originals

## 2L. Security, quality, and exit evidence

- [ ] Extend the threat model for schema complexity attacks, journal replay/checksum substitution,
      cross-store partial commits, ID/reference confusion, archive bombs/path traversal, generic API
      bypass, asset key enumeration, and provenance content leakage.
- [ ] Add a critical invariant matrix covering tenant/project isolation, schema versioning,
      fact/belief separation, cardinality, retirement, optimistic heads, mutation decisions,
      idempotency, saga recovery, outbox lag, media links, and archive round-trip.
- [ ] Maintain hermetic unit/application/contract tests with in-memory ports; keep PostgreSQL/Neo4j
      behavior in disposable integration tests and browser workflows in e2e.
- [ ] Add property/state-machine tests for schema compilation, mutation transitions, revision heads,
      and recovery. Add mutation testing for the highest-risk validators if tooling is reliable.
- [ ] Expand the two-tenant deterministic reference world with intentionally colliding IDs/names and
      labeled valid/invalid canon proposals.
- [ ] Add migration tests from the supported Phase 1 schema and archive v1 policy; verify reset/seed
      and empty-database paths.
- [ ] Add Sentry spans/metrics and Grafana-ready counters for commit states, conflicts, recovery,
      outbox lag, schema failures, and archive operations without recording canon bodies.
- [ ] Set and measure initial performance budgets for paginated entity reads, validation, small
      commits, history queries, and representative exports; correctness remains the release gate.
- [ ] Run `make check`, integration, e2e, migration verification, dependency audit, secret scan,
      archive/restore, recovery fault matrix, and clean-host workflows from a clean checkout.
- [ ] Create `phase-2-evidence.md` with commit, migration/checksum inventory, OpenAPI checksum, test
      counts, known limitations, recovery results, archive checksum comparison, and owner sign-off.

### 2L acceptance

- [ ] all critical invariants have automated positive and adversarial coverage
- [ ] no critical/high unaccepted dependency or security finding remains
- [ ] failure injection proves the commit saga converges or stops for explicit operator action
- [ ] diagnostics and telemetry expose state without story text, secrets, or raw media
- [ ] a clean install completes the authoring → commit → history → export/restore workflow

## Suggested implementation slices

1. **P2-00 — Contract and ADR:** close/accept Phase 1, refresh the baseline, apply the five accepted
   foundation decisions and ADR-011, and freeze domain language.
2. **P2-01 — Root revision and domain kernel:** typed entity/proposition/fact/belief/event,
   provenance, core schema, root branch, genesis revision, and pure invariant tests.
3. **P2-02 — Governed schema vertical slice:** PostgreSQL versions, compiler/cache, publish API,
   seed migration, and generated form/JSON Schema proof.
4. **P2-03 — Journal skeleton:** mutation/envelope/head/outbox migrations and ports, in-memory
   implementation, transition tests, and diagnostics.
5. **P2-04 — Entity commit vertical slice:** create/edit/retire entity through preview, validation,
   journal, Neo4j application, revision history, and recovery fault injection.
6. **P2-05 — Fact/belief/event canon:** reified records, projections, schema/cardinality rules,
   false-belief fixture, history, and product APIs.
7. **P2-06 — Review and concurrency:** operation decisions, author/model/import policies,
   stale-head UX/API, checksum verification, reconciler CLI, and outbox lag.
8. **P2-07 — Evidence assets:** asset/version metadata, immutable originals already in object
   storage, explicit reviewed links, retirement, and archive integration.
9. **P2-08 — Authoring frontend:** TypeScript/client/router/query foundation, project shell,
   schema/entity/character/mutation/history flows, and accessibility tests.
10. **P2-09 — Archive and restore:** format v2, v1 policy/upgrader, staged import, projection rebuild,
    clean-environment checksum comparison, and audit.
11. **P2-10 — Exit audit:** complete invariant/fault/security/e2e matrices, clean-host rehearsal,
    evidence document, and owner acceptance.

Do not start P2-03 durable journal migrations until P2-00 settles authority and head ownership. P2-07
may begin after the domain, scope, and mutation contracts stabilize. The frontend foundation may
start after the first OpenAPI contracts are frozen, but UI completion follows working backend
vertical slices rather than mocks alone.

## Phase 2 exit gate

- [ ] core and project schema versions validate every accepted canonical record
- [ ] author edits produce audited, provenance-rich, revisioned commits through one canonical path
- [ ] model/import/high-impact proposals cannot bypass required human decisions
- [ ] objective facts and character beliefs are distinct in domain, storage, API, and UI
- [ ] entities and evidence links can be retired without erasing accepted history
- [ ] stale/concurrent commits fail predictably and retries cannot duplicate mutations
- [ ] PostgreSQL/Neo4j interruption scenarios recover without ambiguous reported success
- [ ] media identity and explicit evidence links obey project scope, authority, and lifecycle rules
- [ ] two projects cannot read, write, link, restore, or reference each other's canon/assets
- [ ] a complete project archive restores into a clean environment with the same canonical checksum
- [ ] the project shell supports entity/schema/canon review and history without generic graph tools
- [ ] Phase 2 evidence, known deferrals, and owner acceptance are recorded before Phase 3 starts

## Definition of done for each slice

A slice is complete only when domain behavior, API contracts, authorization, persistence,
migrations, recovery/failure behavior, tests, frontend behavior where applicable, observability,
archive impact, and plan status agree. A Neo4j write or successful UI happy path alone is not a
completed canon feature.
