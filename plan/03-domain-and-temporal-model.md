# Domain and temporal model

## Model goals

The domain model must answer five questions without consulting generated prose:

1. What is canonically true?
2. On which branch and during which interval is it true?
3. Who believes or knows it, and with what confidence?
4. Which event or author action caused it to become true?
5. Which narrative plan does it serve?

## Top-level aggregates

### Project

Owns schema version, settings, branches, story structures, assets, and provider policy. Every durable record is project-scoped.

### Branch

Represents a line of canonical history. Fields include `id`, `project_id`, `name`, `parent_branch_id`, `fork_tick`, `head_revision`, `status`, and provenance. A branch reads inherited history through its fork point plus its own mutations.

### Canon entity

A stable identity such as Character, Location, Object, Faction, Concept, Resource, System, Law, Constraint, or Secret. Identity-defining properties change rarely. Time-varying values are facts or state records, not mutable fields on the anchor.

### Fact

A reified canonical assertion with subject, predicate, object or scalar value, branch scope, valid interval, confidence/status, and provenance. Reifying important facts makes temporal validity, contradiction detection, review, and branching tractable.

Relationships may be projected as native Neo4j edges for fast traversal, but the fact identity and journal entry remain authoritative.

### Belief

A character-scoped assertion about a fact or proposition. It records confidence, acquisition event, validity interval, and optionally whether it currently agrees with canon. `is_true` is a derived comparison, not the belief's identity.

### Event

Something that happened in world chronology. Events have timeline positions, participants, locations, causes, outcomes, and source scenes. Scene, action, interaction, and flashback can be event kinds rather than unrelated schemas.

### Story plan

Narrative intent organized as Story → Arc/Act → Beat → ScenePlan. It references required state, intended changes, threads, themes, reveals, and conflicts. Plans are not canon until realized by committed events.

### Scene

An authored artefact with immutable versions. A scene version records point of view, plan, branch, timeline interval, exact text, context manifest, generation metadata, extraction result, review decisions, and committed event/mutation IDs.

### Mutation set

A reviewable proposal containing ordered creates, updates, closes, and links. It has statuses `draft`, `validating`, `needs_review`, `approved`, `committing`, `committed`, `rejected`, and `failed`.

## Core node types

Prefer a controlled core vocabulary:

- Project, Branch, Revision
- Character, Location, Object, Faction, Concept, Resource
- System, Law, Constraint, Pressure
- Fact, Belief, Goal, Secret
- Event
- Story, Act, PlotArc, Beat, ScenePlan, Scene, SceneVersion
- Thread, Conflict, Foreshadow, Payoff, Theme
- Source, ContextManifest, MutationSet

Appearance, personality, and state may remain structured components when they need independent temporal history. Avoid making every form section a graph node by default; use a node only when it has identity, relationships, or version history.

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

## Branch semantics

- A new branch points to a parent and fork revision/tick; it does not copy the entire graph.
- Reads overlay branch-local mutations on inherited state.
- Closing or replacing a fact creates a new revision; it does not delete history.
- Merging computes mutations relative to the common ancestor.
- Structural conflicts require author resolution; prose is never auto-merged into canon.
- Derived vector documents include project, branch, inherited source branch, and revision metadata.

## Knowledge and visibility

For a point-of-view character, context may contain:

- facts directly perceived by the character
- beliefs held by the character, including false beliefs
- public/common knowledge available by policy
- deductions explicitly recorded as beliefs
- author-only constraints in a separate non-prose instruction channel

It must not present an inaccessible secret as something the character knows. The generator may receive author-only truth only when the prompt contract clearly separates it from POV-accessible content and the workflow needs dramatic control. This choice should be configurable and covered by leakage evaluations.

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

- References cannot cross projects.
- A branch cannot inherit from itself or from a descendant.
- At one branch/tick, mutually exclusive single-valued facts cannot both be active.
- A belief holder must be a character-like entity.
- Accepted edges reference existing or co-created entities.
- A scene commit targets the branch head revision it was reviewed against.
- A scene's resulting mutations are idempotent by mutation-set ID.
- Deleting an entity used by history means retiring it, not erasing it.
- Secret visibility is deny-by-default.

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
