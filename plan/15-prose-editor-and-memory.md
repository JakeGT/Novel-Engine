# Prose editor and memory

## Objective

Novel Engine should support a long-running manuscript, not only isolated generated snippets. Prose remains author-owned and editable, while the engine maintains entity annotations, version lineage, context provenance, consistency findings, and canon mutations without corrupting text.

Scenes may also reference immutable project media. The structured document stores stable asset or
segment annotations, never storage URLs or embedded provider objects.

## Document hierarchy

```text
Project
  Manuscript
    Part (optional)
      Chapter
        Scene
          SceneVersion
```

World chronology and narrative order are stored separately from document order. A scene can be moved in the manuscript without changing when its events occur.

## Canonical representation

Accepted default:

- canonical structured editor document using a documented, versioned JSON schema
- immutable `SceneVersion` snapshots with parent lineage
- deterministic plain-text and Markdown projections for AI, search, diff, and export
- formatting/style metadata that is not injected into models unless relevant
- stable entity annotations stored as structured marks plus a portable text representation

Avoid storing only rendered HTML or an editor-vendor-specific opaque format. The editor schema needs migrations and round-trip fixtures.

## Editing model

- Autosave mutable working drafts with revision checks.
- Create immutable checkpoints for generation inputs, accepted edits, consistency runs, mutation extraction, and explicit author versions.
- Preserve manual edits when regenerating; generation creates a variant or patch, never replaces the live document silently.
- Use operation/diff history for local undo while immutable versions provide durable provenance.
- Represent AI corrections as reviewable patches anchored to version/range and reject them when the base is stale.
- Entity mentions survive ordinary text edits and expose broken-link repair when anchors are deleted or pasted as plain text.

For initial shared projects, optimistic concurrency and diff-based conflict review are acceptable. Real-time collaboration would require a CRDT/OT decision and editor architecture ADR.

## Long-document performance

- Edit scenes/chapters as bounded documents rather than mounting the entire novel in one browser editor.
- Virtualize manuscript navigation and lazily load prose.
- Compute plain-text projection, content hash, token count, annotations, and search/index jobs incrementally.
- Store large immutable versions in object storage; keep queryable metadata and current pointers in PostgreSQL.
- Cache recent scene versions while preserving object-store durability.

## Previous-prose memory

Index prose at multiple resolutions:

- preceding paragraphs for immediate flow
- full current/previous scene within budget
- event blocks around POV/location/time/action changes
- scene and chapter summaries
- character dialogue/voice excerpts
- passages tied to active objects, rules, secrets, promises, or threads
- captions/transcripts and explicitly linked character/location/object media relevant to the prose unit

Every unit points to exact manuscript/version/range and includes workspace/project, branch, narrative/world positions, POV, entities, visibility, content hash, embedding model, and active state.

Media memory points to exact asset/version/segment regions or timestamps and carries authority,
branch/time applicability, visibility, explicit-link role, and the embedding representation. A
portrait and its caption/reviewed facts are clustered as one evidence source during ranking.

Semantic vectors represent content meaning. Dynamic weights remain metadata:

- author importance/pin
- narrative distance
- world-time distance
- revision recency
- continuity impact
- thread/plan relevance
- POV/visibility
- branch validity
- prior retrieval usefulness and author feedback

This allows re-ranking without re-embedding. Re-embed only when source text, representation, or embedding model changes.

## Context assembly behavior

Always consider:

- the prose immediately before the generation insertion point
- the current scene plan and current scene state
- recent prose involving active characters/location/objects
- older high-impact prose linked to active facts/threads
- voice samples appropriate to speaking/POV characters
- applicable canonical/reference portraits, locations, maps, objects, performances, or recordings explicitly linked to the active scene entities

Apply hard workspace/project/branch/visibility filters first. Then rank, deduplicate overlapping ranges, allocate token budgets, and show included/excluded passages in the context inspector. Authors may pin, suppress, or adjust importance with provenance.

Apply the same rules to media and show whether the generation model receives the original asset or
a caption/transcript fallback. Requiring original media is a job capability constraint, not a soft
ranking preference.

## External editor integrations

### Google Docs

Treat as an explicit synchronization/import-export workflow unless a later ADR makes it canonical. Required design:

- stable document/scene mapping and revision token
- import conversion with formatting/annotation loss report
- export with entity annotations represented accessibly
- three-way conflict detection against last synchronized version
- explicit direction and user confirmation
- OAuth scopes limited to selected documents

### LaTeX

Support deterministic export templates and potentially import of a controlled subset. Preserve unsupported commands as source blocks or produce a loss report. LaTeX is well suited to typesetting/export but not automatically the collaborative canonical editor.

### Markdown/plain text

Provide reliable versioned import/export and sidecar metadata for entity IDs where inline syntax is undesirable. Never depend on display names alone for round-trip identity.

## Consistency integration

- An edit identifies affected ranges, entity marks, and prior dependency fingerprints.
- Cheap deterministic checks run after a debounce/checkpoint rather than every keystroke.
- Generation runs full Tier 0 preflight and Tier 1 postflight.
- Findings anchor to exact versions/ranges and become stale when dependencies change.
- Agent corrections are patches; accepted patches create a new version and rerun affected checks.

## Export and ownership

Authors can export the manuscript without AI metadata in common readable formats, plus a full fidelity project archive containing versions, annotations, provenance, plans, and canon. Identity-provider or hosted-service failure must not trap author prose. Published/exported artefacts never require Novel Engine to remain readable.

## Open implementation decisions

- structured editor framework and canonical JSON schema
- collaboration level: optimistic locking versus real-time CRDT/OT
- scene/chapter split rules and user override
- Google Docs integration priority and synchronization direction
- supported LaTeX/Markdown round-trip guarantees
- operator/user-configurable retention limits for immutable draft/generation versions
