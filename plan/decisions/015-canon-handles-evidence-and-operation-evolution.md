# ADR-015: Entity handles, evidence references, and checksum-stable operation evolution

- Status: Accepted by the project owner on 2026-10-10 (implemented in Phase 2)
- Date: 2026-10-09
- Owners: project owner, implementer
- Related: ADR-012, `03-domain-and-temporal-model.md`, `phase-2-todo.md` 2F/2H

## Context

Three gaps shared one constraint. Entity names, slugs, aliases, and external identifiers had no
defined collision behaviour. Provenance recorded an evidence pointer only for beliefs. And adding
any field to a typed operation would have changed its serialization, so the checksum of every
previously committed envelope and archived commit would no longer recompute.

## Decision

1. **Identity is the opaque entity id; everything else is a handle.**
   - A display name is free text. A same-type duplicate among active entities is an *advisory*
     finding, never a rejection.
   - A slug is validated and unique among the **active** entities of a project branch; retiring an
     entity frees it. It is derived from the name when omitted.
   - Aliases are bounded, de-duplicated against the name and each other, searchable, and never
     unique (many characters can share an epithet). Time/POV-scoped alias bindings remain Phase 2.5.
   - An external identifier `(namespace, value)` resolves to at most one entity per project,
     active or retired, so imports de-duplicate deterministically.
   - Retiring an entity that live records still reference is allowed and reported as advisory.
2. **Every operation may cite evidence as bounded pointers.** An evidence reference is a source
   artefact id and an optional location, both restricted to identifier-shaped strings, at most ten
   per operation. Prose, captions, or media content cannot be stored in one. References to asset or
   asset-version ids must exist in the project. Evidence is stored per canonical record and
   assembled with origin and review decisions by one provenance query.
3. **Reviewer identity is need-to-know.** Any project reader may see why canon exists (origin,
   evidence, that a decision was made). Who decided, and their notes, are shown only to members
   who can write to the project; the response says when it was redacted.
4. **New operation fields are omitted from the canonical form while unset.** The canonical
   serialization that envelopes and archives checksum drops a registered later-added field when it
   is empty, so every earlier envelope keeps its exact bytes and checksum.

## Alternatives considered

### Unique display names

Rejected: real stories have two characters with one name, and names are not identity.

### Copy a quoted evidence excerpt into provenance

More convenient for display, but it duplicates manuscript text into the journal, audit, archives,
and logs, against the project's content-minimization rule.

### Version the canonical serialization

A serialization version per envelope would also preserve old checksums, but every reader would
need to carry every past serializer. Omitting unset additive fields needs no versioning as long as
additions stay optional.

## Consequences

### Positive

- Imports and the legacy upgrader de-duplicate on external identifiers instead of guessing by name.
- Every accepted change can be explained back to origin, decisions, and evidence.
- Operations can gain optional fields without invalidating history.

### Negative

- A field added later can never be *required*, and a non-empty default would have to be handled
  explicitly; the omit list in `backend/domain/operations.py` is the single place to register one.
- Upgrading a database that already held duplicate active slugs clears all but the earliest
  (migration `0020_entity_identity`); see the upgrade note in `phase-2-evidence.md`.

### Follow-up work

- Phase 2.5: scoped alias bindings and reviewed resolution examples.
- Phase 4: evidence locations for media regions and timestamps once ingestion exists.

## Validation

`tests/unit/test_entity_naming.py` (including a test pinning the exact pre-existing canonical
bytes), `tests/application/test_provenance_and_history_authz.py`,
`tests/integration/test_canon_identity_and_equivalence.py`, and
`tests/integration/test_phase_upgrade_migrations.py`.

## Amendment 2026-10-21: fields omitted per operation, and operations expanded at commit

- `retire_entity.merged_into` is registered in the omit-when-unset list like every earlier
  addition.
- `amend_event` gained `participants`, `location_ids`, `cause_ids` and `outcome_ids` (a merge
  moves every one that names the merged entry). Their names are already used by
  `create_event`, whose canonical form has always kept them even when empty, so they cannot join
  the shared omit list. A second, per-operation list (`_CANONICAL_OMIT_WHEN_NONE_BY_OP`) omits
  them only on `amend_event` and only when `None`; an explicit empty list is recorded. A unit test
  (`tests/unit/test_merge.py`) pins that earlier forms are unchanged.
- `merge_entities` is reviewed as one operation but never reaches an envelope: it is replaced
  before temporary ids are resolved by the ordinary operations it makes, worked out from the
  story at the expected head, by the same function in the mutation service and inside the commit
  transaction, which refuses the commit when the two differ (ADR-017 amendment). New temporary
  ids for the facts, beliefs and links it states are `tmp:merge-<position>-…`, so they resolve
  deterministically like any other.
- `restore_entity` is new and carries no extra fields.

## Amendment 2026-10-21 (batch 9 C)

New optional fields, each omitted from the canonical form while unset so earlier envelopes keep
their checksums: `link_asset.applies_until` and `link_asset.shows_version` (AST-001, in the
general omit list); `update_entity.type` and `amend_event.claim` (AST-EXP-017, omitted only on
those operations while `None`, since `type` and `claim` keep their values on `create_entity` and
`create_event`). A type change is validated against every current fact and belief about the entry,
counting what the same set ends (`type_does_not_fit`).
