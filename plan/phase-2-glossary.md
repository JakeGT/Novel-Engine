# Phase 2 glossary (frozen vocabulary)

- Status: Frozen for Phase 2 implementation
- Related: ADR-003, ADR-004, ADR-011

These terms have one meaning in code, storage, API, and plan text. Reusing a term
for a different concept requires a new name and an ADR note.

| Term | Meaning |
| --- | --- |
| **Project** | The tenant-scoped unit of canon. Every canonical record carries `workspace_id` + `project_id`. |
| **Root branch** | The single executable branch per project in Phase 2 (`brn_root`). Forks/overlays are Phase 3. |
| **Genesis head** | The explicit zero head of a root branch (`revision = 0`); never represented as `NULL`. |
| **Revision** | A monotonic per-project/root-branch counter advanced by exactly one per committed mutation set. |
| **Entity** | A stable project-scoped identity with a declared type and lifecycle (`active`/`retired`). |
| **Proposition** | Subject entity id + declared predicate + exactly one typed object value. |
| **Fact** | An accepted objective proposition in the root branch (`status`: asserted/closed). |
| **Belief** | A holder-scoped proposition with confidence and acquisition provenance. May be false or uncertain; has no `is_true`. |
| **Truth agreement** | A derived comparison of a belief against accepted facts at a checked revision (`agrees`/`disagrees`/`unknown`). Never stored. |
| **Event** | A canon event with kind, participants/roles, locations, causes/outcomes, source, and provenance. World-position input is stored as supplied. |
| **Mutation set** | An ordered, validated batch of operations proposed for one project/root branch/schema version against an expected head. |
| **Operation** | One typed change inside a mutation set (e.g. create entity, assert fact, retire entity, link asset). |
| **Decision** | A per-operation human outcome (`approved`/`rejected`) required for protected operations. |
| **Journal entry** | The immutable PostgreSQL commit envelope: canonical serialization + checksum of the mutation set and its decisions. |
| **Commit attempt** | The PostgreSQL recovery record of cross-store progress (`prepared`→`graph_applying`→`graph_applied`→`committed`, or `recovery_required`/`integrity_blocked`). |
| **Outbox message** | A committed, idempotent instruction to deliver derived state (indexes, notifications). |
| **Projection** | Rebuildable derived query state in Neo4j (or pgvector) bounded by the committed PostgreSQL head. |
| **Provenance** | Why a canonical record exists: origin kind, actor/job/source identifiers, bounded source reference. Never raw prompts/secrets. |
| **Retirement** | Lifecycle transition that closes active references to a record while preserving readable history. |
| **Asset** | Project-scoped media identity; **Asset version** is an immutable byte identity in object storage. |
| **Asset link** | An explicit reviewed canon operation connecting an asset version to canon (e.g. `EVIDENCE_FOR`). |

## Identifier prefixes

`ent` entity, `prd` predicate, `fct` fact, `blf` belief, `evt` event, `brn` branch,
`rev` revision, `mut` mutation set, `opn` operation, `scv` schema version, `prv`
provenance, `ast` asset, `asv` asset version, `prj` project, `wsp` workspace.