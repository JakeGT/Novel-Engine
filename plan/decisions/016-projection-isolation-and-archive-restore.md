# ADR-016: Projection isolation, verified restore, and legacy archives

- Status: Proposed (implemented in Phase 2; awaiting owner acceptance with the Phase 2 evidence)
- Date: 2026-10-09
- Owners: project owner, implementer
- Related: ADR-012, ADR-010, `05-storage-and-integrity.md`, `phase-2-todo.md` 2D/2G/2K

## Context

Finishing Phase 2 against real stores exposed four problems that each needed a durable rule.

1. The canon projection and the legacy generic graph share one Neo4j database and project scope.
   The projection labelled its nodes `Entity`/`Fact`/`Belief`/`Event`, which are the labels the
   legacy graph gives its own nodes, so each store could read the other's nodes.
2. Deleting a project or workspace removed tenancy rows only: canon, journal, schema, and media
   rows and most projected nodes were left behind.
3. An archive import had no durable status, so a partial restore was indistinguishable from a
   successful project.
4. Phase 1 exported an untyped graph. Nothing said whether that could be restored as canon.

## Decision

1. **The canon projection has its own labels** (`CanonEntity`, `CanonFact`, `CanonBelief`,
   `CanonEvent`, `CanonProvenance`, `CanonAsset`) with scoped uniqueness constraints (Neo4j
   migration `0004`). Open entity-to-entity facts also project as typed relationships between
   `CanonEntity` nodes, created and removed with the fact. A predicate becomes a relationship type
   only when it is a plain identifier.
2. **A projection layout has a version.** A watermark written under another layout is treated as
   "nothing applied": readers see lag, and the next run discards and replays from the journal.
3. **Equivalence is checked, never repaired.** One service replays the journal in memory and
   compares it with normalized canon and, through ordinary product queries, with the projection.
   It reports record ids and field names only. Canon is never modified to match a derived store.
4. **Deletion is complete.** One list of project-owned tables drives project deletion, workspace
   deletion, failed-import removal, and the development reset; the projection is purged with it.
5. **A restore is recorded before it writes and verified before it is called successful.** The
   import ledger row is created after validation and target preflight and before the first durable
   write. It becomes `completed` only after every commit replays, the projection is rebuilt, and
   equivalence passes. A failed import resumes on retry or can be removed, and removal is refused
   if the project holds any canon the import did not write.
6. **Full-history export, import, and removal are owner operations** and write a durable
   security-audit record containing counts and checksums only. Import stays an operator action
   (not an HTTP upload) until the secure ingestion pipeline exists.
7. **A Phase 1 graph archive is never accepted history.** A bounded upgrader maps what fits the
   canon vocabulary into an `import`-origin *proposal* held for human approval (ADR-014) and
   reports everything it could not map with a reason. A legacy `Belief` node's stored truth flag is
   never carried into canon.

Canon has no derived vector index in Phase 2, so the graph projection is the only derived store a
restore rebuilds.

## Alternatives considered

### Distinguish projected nodes by a property instead of a label

Cheaper to migrate, but uniqueness constraints and every legacy query are label-based, so a
property cannot stop either store matching the other's nodes.

### Drop Phase 1 archive support outright

Acceptable while prototype data is disposable, but a reviewed upgrade path costs little and keeps
early users' work reachable.

### Mark an import successful when its last commit applies

Simpler, but a restore whose projection or normalized state disagrees with its journal would be
presented as healthy.

## Consequences

### Positive

- The two graph stores cannot corrupt or misread each other, and a layout change cannot leave a
  falsely "current" projection.
- Deleting a project removes its canon everywhere.
- A restore is either verified or visibly not successful.

### Negative

- Migration `0004` discards previously projected canon nodes; they are rebuilt from the journal on
  the next projection run (expected, since the projection is disposable).
- An import needs Neo4j available when a projector is configured, because verification includes it.

### Follow-up work

- Remove the legacy generic graph and its labels once nothing depends on it.
- Phase 4: rebuild and verify derived vector/search indexes through the same seam.
- Owner-run clean-host and coordinated backup/restore rehearsal (a project archive round trip is
  not a database backup).

## Validation

`tests/integration/test_canon_seed_and_purge.py`,
`tests/integration/test_canon_identity_and_equivalence.py`,
`tests/integration/test_archive_clean_environment.py`,
`tests/application/test_archive_import_ledger.py`, and `make project PROJECT_ARGS=--verify`.
