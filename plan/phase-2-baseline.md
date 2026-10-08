# Phase 2 baseline and compatibility rules

- Status: Captured 2026-10-08 at commit `5822303` (Phase 1 hardening complete)
- Related: ADR-011, `phase-2-todo.md`

This records the starting point Phase 2 builds on, so later evidence can prove
what changed.

## Starting inventory

| Item | Value |
| --- | --- |
| PostgreSQL (Alembic) head | `0011_active_credential_unique` |
| Neo4j migrations | `0001_initial_graph_schema` (`e5ec799c5aff2b16d2019b5e00d4b9921e4e9c919e845c64cab0a88b004eba5c`), `0002_entity_scope_identity` (`a32019c1ba0092b768dc64bddb181dd73d559682b4d745a61ad9d1efcd20899e`) |
| OpenAPI snapshot SHA-256 | `8b078177b99783cd836443fb9d7ab8339bc5624e79667cd6106a34ae5ad6bf9f` |
| Reference-world seed checksum | `43252e91465db7d0195bc521bad124ea263bd24285e62b3d34e21ef884c5aaa6` (27 nodes / 29 edges / 2 documents / 4 queries) |
| Tenancy seed version | `2026-10-06.1` |
| Physical project archive format | `ARCHIVE_VERSION = "2"` (`backend/services/project_transfer.py`) |
| Hermetic tests | 329 passing |
| Integration tests | 48 passing (disposable PostgreSQL/pgvector + `neo4j-test`) |
| Type check scope | `mypy backend` (85 files, clean) |

## Compatibility rules

1. **API DTOs** — `/api/v1` is forward-compatible: additive fields only within a
   major version; removals/renames require a new version or an explicit deprecation
   window. Canon DTOs must never expose embeddings, DSNs, credentials, or secrets.
2. **Archive format** — the archive manifest carries an explicit format version and
   per-entry + whole-manifest checksums. A reader must reject unknown major versions
   and must validate every entry before any durable write. A new major version
   requires a documented upgrader from the previous major version.
3. **Schema versions** — published project schema versions are immutable; existing
   canon retains the version it was accepted under. A publish produces a
   compatibility report (`additive` / `migration_required` / `forbidden`) and never
   silently rewrites canon.
4. **Prompt schemas** — structured model-output schemas are versioned and validated
   separately from live quality evaluation; a prompt/schema change records its
   version and does not mutate stored canon without a mutation set.
5. **Neo4j projections** — projections are rebuildable and never authoritative.
   Their shape is bounded by the committed PostgreSQL head; a projection change is a
   derived-index concern, not a canon migration.

## Deferred from Phase 2 (named so APIs cannot imply them)

Branch forks/overlays/merges and world/narrative-time evaluation (Phase 3); POV
knowledge filtering (Phase 3); scene/prose generation and consistency critics
(Phase 4+); hosted media upload, scanning, transcoding, captioning, transcription,
segmentation, multimodal embeddings, and provider media delivery (Phase 4+, per
ADR-010); real-time co-editing/CRDTs; a general background job system beyond the
commit outbox/reconciler seam.