# Domain and temporal model

## Model goals

The domain model must answer seven questions without consulting generated prose:

1. What is canonically true?
2. On which branch and during which interval is it true?
3. Who believes or knows it, and with what confidence?
4. Which event or author action caused it to become true?
5. Which narrative plan does it serve?
6. What has the reader been shown at this narrative position?
7. Which authored rule or scene condition should this state satisfy?

## Top-level aggregates

### Project

Owns schema version, settings, branches, story structures, assets, and provider policy. Every durable record is project-scoped.

### Branch

Represents a line of canonical history. Fields include `id`, `project_id`, `name`, `parent_branch_id`, `fork_tick`, `head_revision`, `status`, and provenance. A branch reads inherited history through its fork point plus its own mutations.

### Canon entity

A stable identity such as Character, Location, Object, Faction, Concept, Resource, System, Law, Constraint, or Secret. Identity-defining properties change rarely. Time-varying values are facts or state records, not mutable fields on the anchor.

### Fact

A reified canonical assertion with subject, predicate, object or scalar value, branch scope, valid interval, confidence/status, and provenance. Reifying important facts makes temporal validity, contradiction detection, review, and branching tractable.

Relationships may be projected as native Neo4j edges for fast traversal, but PostgreSQL's
versioned fact identity/state and immutable journal remain authoritative. Neo4j is replayable from
committed envelopes (ADR-012).

### Belief

A character-scoped assertion about a fact or proposition. It records confidence, acquisition event, validity interval, and optionally whether it currently agrees with canon. `is_true` is a derived comparison, not the belief's identity.

### Event

Something that happened in world chronology. Events have timeline positions, participants, locations, causes, outcomes, and source scenes. Scene, action, interaction, and flashback can be event kinds rather than unrelated schemas.

### Story plan

Narrative intent organized as Story → Arc/Act → Beat → ScenePlan. It references required state, intended changes, threads, themes, reveals, and conflicts. Plans are not canon until realized by committed events.

Each scene plan may carry a typed `SceneContract`: hard preconditions, soft expectations, intended
postconditions, declared perceptions, setups, disclosures, and payoffs. Character arcs are paths
over project-defined qualitative or ordinal state dimensions, not universal psychological scores.

### Scene

An authored artefact with immutable versions. A scene version records point of view, plan, branch, timeline interval, exact text, context manifest, generation metadata, extraction result, review decisions, and committed event/mutation IDs.

### Media asset

A project-scoped image, audio, video, document, or mixed reference with immutable source versions,
authority, visibility, provenance, licence/attribution, branch/time applicability, and processing
state. Derived variants, segments, captions, transcripts, OCR, observations, and embeddings point
to the exact source version and are rebuildable. Explicit asset links connect media to entities,
scenes, plans, events, or facts with roles such as `DEPICTS`, `VOICE_OF`, `MAP_OF`, `SET_AT`, or
`EVIDENCE_FOR`.

### Mutation set

A reviewable proposal containing ordered creates, updates, closes, and links. It has statuses `draft`, `validating`, `needs_review`, `approved`, `committing`, `committed`, `rejected`, and `failed`.

### Narrated assertion and speech act

A proposition represented in narration, dialogue, thought, written communication, dream/vision, or
reported speech. Speech acts record speaker, intended listeners, explicit overhearers, evidence, and
an authored interpretation such as sincere, lie, mistaken, unreliable narration, or figurative.
They are not objective facts merely because they appear in accepted prose.

### Reader disclosure

A versioned record that a proposition was exposed at a narrative position for an intended
audience/edition, with source scene/version, exact evidence, and explicit/implied/ambiguous mode.
It supports reveal, dramatic-irony, and spoiler-safe views without claiming that every reader made
the same inference.

### Story invariant and suppression

A story invariant is a safe declarative rule over propositions and the three clocks. It is authored
policy rather than canon. New author invariants are warnings by default and may become blocking
only through an explicit authorized publication. Built-in integrity invariants are a separate,
permanently blocking class. Eligible advisory findings may be covered by versioned, scoped
suppressions; integrity failures cannot.

### Alias binding

A time-, branch-, narrative-, and POV/speaker-scoped reference from an alias or epithet to an
entity. Reviewed positive and negative resolution examples preserve author corrections for editor
suggestions, import, extraction, and deterministic checks.

## Core node types

Prefer a controlled core vocabulary:

- Project, Branch, Revision
- Character, Location, Object, Faction, Concept, Resource
- System, Law, Constraint, Pressure
- Fact, Belief, Goal, Secret
- Event
- Story, Act, PlotArc, Beat, ScenePlan, SceneContract, Scene, SceneVersion
- Thread, Conflict, Foreshadow, Payoff, Theme
- NarratedAssertion, SpeechAct, Disclosure, StoryInvariant, FindingSuppression, AliasBinding
- Source, ContextManifest, MutationSet, CanonTag
- MediaAsset, MediaAssetVersion, MediaSegment, AssetLink, DerivedMediaObservation

Appearance, personality, and state may remain structured components when they need independent temporal history. Avoid making every form section a graph node by default; use a node only when it has identity, relationships, or version history.

Media lifecycle metadata remains authoritative in PostgreSQL; graph nodes/edges are query
projections for reviewed semantic links. An image-derived appearance observation is not equivalent
to an accepted appearance fact.

## Identity rules

- IDs are opaque stable identifiers, preferably UUIDv7/ULID with readable slugs stored separately.
- User-facing names are not identifiers and may collide or change.
- Imported external identifiers live in a namespaced alias table.
- A generated proposal may use temporary IDs, resolved to durable IDs during review.
- Entity links in text use stable IDs and declared types, but type is verified against storage.

## Time model

Keep three clocks distinct:

- **World time:** chronological time inside the fiction; may be partial or symbolic.
- **Narrative order:** the order scenes are presented to the reader.
- **System revision:** the ordered commit sequence used for concurrency and audit.

Each temporal fact uses a half-open validity interval `[valid_from, valid_to)`. A timeline position should support an integer sequence for ordering plus optional authored date/calendar metadata. Unknown or fuzzy time must be representable instead of invented.

Flashbacks change narrative order, not the world time at which their events occurred.

`WorldTime` must represent ordinal, authored-calendar, relative-anchor, fuzzy interval, and unknown
forms without coercing them to civil `datetime`. `NarrativePosition` is an ordered manuscript
coordinate that may carry part/chapter/scene labels. Neither is implicitly convertible to the
other.

## Branch semantics

- A new branch points to a parent and fork revision/tick; it does not copy the entire graph.
- Reads overlay branch-local mutations on inherited state.
- Closing or replacing a fact creates a new revision; it does not delete history.
- Merging computes mutations relative to the common ancestor.
- Structural conflicts require author resolution; prose is never auto-merged into canon.
- Derived vector documents include project, branch, inherited source branch, and revision metadata.
- Asset links and observations use the same inheritance and conflict rules; alternate-branch media cannot leak into a sibling branch through vector similarity.

## Knowledge and visibility

For a point-of-view character, context may contain:

- facts directly perceived by the character
- beliefs held by the character, including false beliefs
- public/common knowledge available by policy
- deductions explicitly recorded as beliefs
- author-only constraints in a separate non-prose instruction channel

It must not present an inaccessible secret as something the character knows. The generator may receive author-only truth only when the prompt contract clearly separates it from POV-accessible content and the workflow needs dramatic control. This choice should be configurable and covered by leakage evaluations.

The same policy covers media. A portrait, map, recording, filename, caption, transcript, or visible
detail can reveal a secret even when its accompanying text is safe.

Character knowledge and reader disclosure are distinct ledgers. Deterministic belief propagation
may propose acquisitions from explicit perception, addressed speech, or overhearing, but scene
participation alone does not prove perception. Directional, context-specific trust can affect
proposed confidence. All propagated changes remain reviewable mutations.

## Provenance

Every fact, belief, event, and plan change records:

- origin type: author, import, model proposal, derived rule, or system migration
- source artefact/event/scene
- actor or job ID
- creation revision and timestamp
- prompt/model metadata when AI contributed
- confidence and review decision

Derived information also stores its derivation inputs and can be invalidated when those inputs change.

## Hard invariants

These built-in integrity invariants are permanently blocking and non-suppressible. They are not the
same as author story invariants, which default to warning behavior under ADR-013.

- References cannot cross projects.
- A branch cannot inherit from itself or from a descendant.
- At one branch/tick, mutually exclusive single-valued facts cannot both be active.
- A belief holder must be a character-like entity.
- Accepted edges reference existing or co-created entities.
- A scene commit targets the branch head revision it was reviewed against.
- A scene's resulting mutations are idempotent by mutation-set ID.
- Deleting an entity used by history means retiring it, not erasing it.
- Secret visibility is deny-by-default.
- An asset/segment cannot be linked across projects, and a derived representation cannot widen its source visibility.
- A machine-derived media observation cannot become canon without an accepted mutation carrying exact region/timestamp evidence.
- Retired, deleted, quarantined, failed, or branch/time-inapplicable media cannot enter a new executable context manifest.

## Author story invariants

Project rules use a bounded declarative AST over governed propositions, cardinality, branch/time,
narrative position, and declared exceptions. Each rule version records dependencies, examples,
scope, and enforcement mode. A deterministic violation becomes a cited finding and blocks a commit
only when that exact author rule version has explicitly enabled blocking behavior. Uncertain or
extraction-dependent results are findings only. See ADR-013 and
`18-author-rules-canon-queries-and-derived-views.md`.

## Schema extensions

Use fixed core types plus project-scoped custom types and predicates. Extensions require a name, description, property schema, allowed endpoints, cardinality, temporal behavior, visibility, and migration version. Ordinary content APIs reject undeclared schema values rather than silently adding them.

### Runtime typed-schema pipeline

Project extensions use a safe declarative schema language rather than Python source. Initially support bounded primitives, enums, constrained numbers/strings, lists, nested records, entity references, optional/required fields, relationship endpoints, cardinality, and temporal/visibility flags.

1. Validate an author's extension against a fixed meta-schema.
2. Persist an immutable project schema version in PostgreSQL and project it into Neo4j as needed.
3. Compile the version to internal field specifications and runtime Pydantic models for application validation.
4. Export strict JSON Schema for model structured-output requests through the AI adapter.
5. Validate model output against the runtime Pydantic model.
6. Resolve entity references and run domain invariants.
7. Validate again at canonical commit against the exact schema version reviewed.

Compiled models are process-local caches keyed by workspace/project/schema-version hash. They are never treated as executable user code or serialized as the durable schema. A schema change creates a new version and an explicit migration/projection plan; it never mutates the meaning of historical records silently.
