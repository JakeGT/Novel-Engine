# Phase 2 exit evidence

- Status: **Accepted by the project owner on 2026-10-09** (recorded against commit `22955a7` on
  `phase-2-closure`). Every code-completable Phase 2 item is implemented and verified as recorded
  below. The remaining items are operational and stay open (see "Open owner items"): the hosted CI
  run, the clean-host backup/restore rehearsal, the live Authentik round trip, and the decision on
  ADR-014–016.
- Scope: Canonical story-state authority (ADR-012/013), Phase 2 workstreams 2A–2L
- Owner: project owner or designated technical lead
- Date: 2026-10-09
- Branch: `phase-2-closure` (not pushed; no hosted CI run exists for it)

Authoritative canon is PostgreSQL. Neo4j is a rebuildable projection whose freshness is proven
through durable watermarks. This document records what was run and what it showed.

## Runtimes and storage

| Component | Version |
| --- | --- |
| Python | 3.13 (CI 3.13) |
| PostgreSQL + pgvector | `pgvector/pgvector:0.8.0-pg17` |
| Neo4j | `neo4j:5.26.0` |
| Node.js / pnpm | CI pins 24 LTS / 11.26.0. The local runs below used Node 26 (pnpm warns about the engine range); nothing failed because of it. |

## Migration inventory

PostgreSQL (Alembic) head: **`0022_archive_imports`**. Phase 2 revisions:
`0012_schema_versions` → `0013_canon_journal` → `0014_canon_storage` → `0015_outbox_delivery` →
`0016_canon_tags` → `0017_media` → `0018_commit_integrity` → `0019_review_workflow` (transition
log, operation revisions, operation decisions) → `0020_entity_identity` (aliases, external ids,
active-slug uniqueness, evidence, record-version ordinal) → `0021_media_lifecycle` (immutable
lifecycle audit) → `0022_archive_imports` (import status ledger). Earlier revisions `0001`–`0011`
are Phase 0/1.

Neo4j ordered migrations and checksums:

- `0001_initial_graph_schema` — `e5ec799c5aff2b16d2019b5e00d4b9921e4e9c919e845c64cab0a88b004eba5c`
- `0002_entity_scope_identity` — `a32019c1ba0092b768dc64bddb181dd73d559682b4d745a61ad9d1efcd20899e`
- `0003_canon_scope_identity` — `447e2bff20a71cc03ad55f048c3fbde789a77eed7dda79bbdc605ab332c8bb6b`
- `0004_canon_projection_labels` — `cf8c400e8f5b7c560103d0077cc3f007c5aece124dc4cc3a9d7b7a5a032bcdd4`

`make migrate-verify` reports both stores up to date. The local development database was upgraded
in place from `0018` to `0022` with existing data and stayed usable.

### Upgrade notes

- **Duplicate slugs.** Before `0020`, slugs were free text. If two active entities shared one, the
  migration keeps the earliest and clears the others so the unique index can be built. Nothing is
  deleted, but for a cleared entity `make project PROJECT_ARGS=--verify` reports "differs in slug"
  until the author commits a new slug for it, because the journal still records the old one.
- **Projection layout.** Neo4j `0004` discards previously projected canon nodes. The projection
  schema version is now 2; the next projection run rebuilds from the journal (observed locally:
  "applied 7 commits" after the migration).

## Contract and seed

- OpenAPI snapshot: `tests/contract/openapi_snapshot.json`, 145 operations, exact-checked by
  `tests/contract/test_openapi.py`. SHA-256
  `792ec7de3b53f9fa3c8cb5b8dfb174e853467b0402f155aa7c75339c5f6f433b`. `make frontend-api-check`
  confirms the committed client types match it.
- Canon archive format: `novel-engine/canon-archive` version `3` (unchanged; commit metadata gained
  an optional `intent`). Same-scope restores keep every envelope checksum; a cross-project restore
  rewrites project-scoped entity references and recomputes the affected envelopes.
- Canon reference world (`backend/seed/canon.py`), version `2026-10-09.1`, definition checksum
  `9627b8737c69aff22d36f77c79bd6b998dd234927d16e52db2638c7f0b60d686`: 7 commits in the primary
  project and 2 in a second tenant that reuses the same ids and names; 4 valid and 21 invalid
  labelled proposals.
- Legacy graph reference world (unchanged): checksum
  `43252e91465db7d0195bc521bad124ea263bd24285e62b3d34e21ef884c5aaa6` (27 nodes, 29 edges).

## Test results

All run locally on 2026-10-09 against the working tree that produced this document.

| Suite | Command | Result |
| --- | --- | --- |
| Hermetic (unit + application + contract) | `make test` | **581 passed** |
| Integration (disposable PostgreSQL + `neo4j-test`) | `make test-integration` | **87 passed** |
| Frontend component | `make frontend-test` | **34 passed** (11 files) |
| Browser end to end | `make e2e` | **2 passed** (three consecutive runs) |
| Aggregate gate | `make check` | green: lock check, ruff lint + format, secret scan (409 files), `mypy backend` (133 files), hermetic tests, API drift check, frontend lint/typecheck/test/build |
| Dependency audit | `make audit` | no known vulnerabilities |

Branch coverage of `backend`: 74% hermetic, 61% integration, **84% combined** (Phase 1 baseline was
about 71%). Most uncovered lines are the operator CLIs under `backend/devtools/`.

### Run on the development stack, not only in tests

`make reset-dev` → `make migrate` → `make seed` → `make project` → `make project
PROJECT_ARGS=--verify` → `make verify`, then an owner export of the seeded project and a restore
into a second project with `make archive`:

- seed verification ok; canon heads 7 and 2;
- journal, normalized canon, and the Neo4j projection produced one identical checksum per project;
- the restore reported `projection rebuilt: True; verified: True`, the import ledger showed
  `completed stage=verified commits=7/7`, and the restored project verified;
- an export attempted as an editor, and as a user with no membership, was refused.

This run is how the projection/legacy-graph label collision below was found.

## Defects found and fixed while closing the phase

Each was a real behaviour difference, has a test that fails without the fix, and is in the commit
history of `phase-2-closure`.

| Defect | Effect before the fix | Found by |
| --- | --- | --- |
| In-transaction asset-link lookup queried `media_asset_links`, a table that does not exist | Every `link_asset` commit failed on PostgreSQL | New PostgreSQL media test |
| Event projection wrote a list of maps as a node property | Any event with participants could never be projected; the projection stopped at that commit | New equivalence test |
| The canon projection shared `Entity`/`Fact`/`Belief`/`Event` labels with the legacy graph | Each store read the other's nodes; seeding both broke the projection and polluted legacy queries | Real `make seed` + `make project` |
| Project and workspace deletion removed tenancy rows only | Canon, journal, schema, and media rows and most projected nodes survived deletion | Review of the purge path |
| Legacy character endpoints wrote model output to the graph without the local-only guard | A hosted write path outside the journal | Review while porting the workflow |
| Record history had no order within one commit | Reverts and diffs of a multi-operation commit were not deterministic | Revert work |
| Concurrent retry of one commit checked for an existing envelope before taking its lock | The second caller got an error instead of the original result | New concurrency test |
| Failed-import removal purged the whole project | It would also have deleted a schema version or asset the project already had | Self-review, then a PostgreSQL test |
| Schema validation never compared with the published version | The preview always said "additive" | Review while adding metrics |
| Local mode mixed the URL's project with the local scope's project | A request naming another project was partly served from the local one | Review before the frontend work |
| Author-direct approval was recorded after the commit transaction | Review evidence could be missing for a committed revision | Review workflow work |

## Canonical authority and commit protocol

- A commit is **one PostgreSQL transaction** (`PostgresCanonRepository.apply_commit`): lock the
  proposal → recompute its effective operations from stored revisions and decisions → **review
  gate** → lock the head → authoritative validation → reservation → immutable envelope → normalized
  canon, record versions, evidence → head advance + revision → outbox → state transitions and, for a
  low-impact direct commit, its approval record. Success never depends on Neo4j.
- The review gate and the validator both run inside that transaction, so a service, an import, or
  a direct repository caller cannot skip either (ADR-014).
- The projection is a separate consumer with a versioned layout and durable watermark; failure is
  visible lag. Equivalence between journal, normalized canon, and projection is checked by
  `CanonEquivalenceService` and never repaired by changing canon (ADR-016).

## Critical invariant matrix

| Invariant | Positive and adversarial coverage |
| --- | --- |
| Tenant/project/workspace isolation, including identical ids and names in two tenants | `tests/contract/test_workspace_scope.py`, `tests/application/test_canon_seed.py`, `tests/integration/test_canon_seed_and_purge.py` |
| Local mode serves exactly one project | `tests/contract/test_project_access_and_vocabulary.py` |
| Schema versioning, compatibility, deterministic compilation, strict extension attributes | `tests/unit/test_schema_compiler.py`, `test_schema_compatibility.py`, `tests/unit/test_properties.py`, `tests/contract/test_metrics_and_schema_validation.py` |
| Fact vs belief separation; truth agreement is derived | `tests/unit/test_canon_domain.py`, `tests/contract/test_canon_assertions.py`, reference-world beliefs |
| Predicate shape, endpoint types, cardinality, intervals, references, lifecycle | 21 labelled invalid proposals in `tests/application/test_canon_seed.py` (preview and commit) |
| Naming: slug uniqueness, alias bounds, external-id binding, advisory name collisions | `tests/unit/test_entity_naming.py`, `tests/integration/test_canon_identity_and_equivalence.py` |
| Review state machine: legal transitions only, terminal states, perishable approval | `tests/unit/test_review_policy.py`, `tests/application/test_review_workflow.py`, `tests/integration/test_review_workflow.py`, Hypothesis state machine |
| Protected-origin, destructive, bulk, and revert changes need approval; no self-acknowledgement for protected origins | same, plus `tests/contract/test_canon_mutations.py` and the browser test |
| Rejected operations never reach canon; an edit invalidates decisions made on the old revision | same |
| Optimistic head; one revision per head under real concurrency | `tests/integration/test_review_workflow.py` (8 racing writers), Hypothesis head property |
| Commit idempotency and checksum immutability; earlier envelopes keep their bytes | `tests/application/test_mutation_service.py`, `tests/unit/test_entity_naming.py`, concurrent-retry test |
| Transaction rollback on any invalid commit | `tests/integration/test_canon_repository.py`, `tests/integration/test_review_workflow.py` |
| Built-in integrity cannot be suppressed; author rules warn by default and block only when explicit, deterministic, and complete | `tests/unit/test_invariant_classification.py` |
| Projection replay, idempotency, convergence after crashes, isolation from the legacy graph, stale-layout rebuild | `tests/unit/test_projection_service.py`, `tests/unit/test_properties.py`, `tests/integration/test_canon_seed_and_purge.py` |
| Journal = normalized canon = projection | `tests/unit/test_canon_equivalence.py`, `tests/integration/test_canon_identity_and_equivalence.py` |
| History, diffs, state at revision, tags | `tests/unit/test_canon_history.py`, `tests/contract/test_canon_history.py` |
| Revert: edit inverses, re-assertion, collapse, dependency and intervening conflicts | `tests/unit/test_revert_proposals.py` |
| Provenance and evidence; reviewer details redacted for read-only members; history denied outside the project | `tests/application/test_provenance_and_history_authz.py` |
| Evidence can only be a bounded pointer | same |
| Media identity, links, lifecycle; immutable lifecycle audit | `tests/unit/test_media_service.py`, `tests/integration/test_media_canon.py`, `test_canon_seed_and_purge.py` |
| Project deletion erases canon and projection and nothing else | `tests/integration/test_canon_seed_and_purge.py` |
| Archive: tamper/traversal rejection, preflight before any write, ledger, resume, exact removal, owner-only + audit, clean-environment restore | `tests/application/test_canon_archive.py`, `test_archive_import_ledger.py`, `tests/integration/test_archive_clean_environment.py` |
| Legacy archives become proposals only | `tests/application/test_archive_import_ledger.py` |
| Upgrade from Phase 1 data and from pre-review canon | `tests/integration/test_phase_upgrade_migrations.py` |
| Metrics, audit records, and equivalence reports carry no ids or content | `tests/unit/test_metrics.py`, archive and equivalence tests |
| Model output stays a proposal | `tests/contract/test_canon_characters.py` |

The Hypothesis review state machine was run against three deliberately broken invariants (approval
surviving an edit, a rejected operation committing, a terminal state reopening) and detects each.
It did not at first; the test was strengthened until it did.

## Performance budgets

Measured on a developer workstation against PostgreSQL with 300 entities, 100 facts, and 16+
revisions (`tests/integration/test_performance_budgets.py`). Budgets are about ten times the
measured median so the test is stable on a slower runner.

| Path | Measured median | Budget |
| --- | --- | --- |
| Entity page read (50 rows at offset 150) | 1.3 ms | 50 ms |
| Entity search (name/slug/alias) | 1.6 ms | 50 ms |
| Validate a 25-operation proposal | 58 ms | 600 ms |
| Small commit (one operation) | 29 ms | 300 ms |
| Record history | 1.0 ms | 50 ms |
| Revision list (20 revisions with origin/decision/evidence counts) | 90 ms | 600 ms |
| Export the project | 76 ms | 1000 ms |

These are single-user figures on a small project. Nothing here measures concurrency, large
projects, or Neo4j.

## Known limitations

These are the things that do not work or were not verified. None is a hidden gap in an item marked
complete; each is the boundary of what was built.

**Canon and review**

- Enum-valued predicates carry their allowed values on the proposition. Neither the core registry
  nor project schema extensions define an enum's domain, so the UI asks for enum, quantity, and
  structured values as JSON.
- There is no operation that reactivates a retired entity or event, so a revert lists retirements
  for manual handling.
- A stale proposal is recovered by retargeting the whole set to the current head and reviewing it
  again. Record- and dependency-level rebasing is Phase 3.
- The bulk-change threshold (25 operations) and the destructive-operation set are constants.
- Story-rule *authoring*, publication, and persistence are Phase 2.5. Phase 2 provides the
  evaluator seam and its classification only; no rule exists to run.
- No mutation-testing tool was adopted.

**Archive**

- Import is an operator CLI; there is no HTTP upload.
- The Phase 1 upgrader maps entities and entity-to-entity edges whose type is a known predicate.
  Scalar properties become attributes; everything else is reported and skipped.
- The clean-environment restore test uses the core schema. Restoring a project with schema
  extensions is covered in memory only.

**Frontend**

- Beliefs and events can be read but not created in the UI, and assets cannot be registered or
  linked there; those remain API or proposal actions.
- Hosted behaviour (project switching, the read-only viewer experience, the import banner) is
  covered by component tests. The browser test runs in local mode as the owner.
- The guided character interview is tested with a scripted model and its no-provider path. It has
  not been run against a live provider.
- Accessibility is checked by axe and a keyboard-only browser test. No manual screen-reader pass
  was done.
- In local development nothing runs the projector automatically, so the shell correctly shows the
  graph index as behind until `make project` or `make watch` runs.

**Operations**

- The `e2e-browser` CI job and the CI API-drift step were added but have never run in hosted CI.
- Metrics are off by default and no Grafana dashboard is provisioned.
- The legacy generic graph API still exists for local use; removing it is future work.
- ADR-014, ADR-015, and ADR-016 record decisions made during this work and are `Proposed`.

## Open owner items

These cannot be completed by code and are the only unchecked items in `phase-2-todo.md`.

1. ~~Accept or reject `phase-1-evidence.md`.~~ Accepted by the owner on 2026-10-09.
2. Run the first clean-checkout hosted CI workflow and link its result (push `phase-2-closure`).
3. Run, or time-box, the clean-host install and coordinated PostgreSQL/Neo4j/object-store
   backup/restore rehearsal. A project archive round trip is not a database backup.
4. Perform the live Authentik browser round trip for hosted mode.
5. Accept, amend, or reject ADR-014, ADR-015, and ADR-016.
6. ~~Accept this evidence and authorize the next phase.~~ Accepted by the owner on 2026-10-09.

## Owner sign-off

- [x] Project owner accepts the Phase 2 evidence and authorizes the next phase (2026-10-09, commit
      `22955a7`). ADR-014, ADR-015, and ADR-016 are still awaiting a decision.
