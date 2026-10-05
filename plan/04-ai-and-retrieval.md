# AI and retrieval

## Principle

AI participates in bounded, inspectable tasks. It does not own canonical state. Each task has a typed input, typed output where practical, explicit context policy, versioned prompt, model policy, validation, and evaluation set.

## AI capabilities

Define provider-neutral ports for:

- structured completion
- long-form text generation
- embeddings
- optional image understanding
- optional critique/ranking

An operation declares capability requirements rather than a hard-coded model name. Provider configuration maps requirements to actual models and limits. OpenRouter is the primary cloud gateway; the system records the router and actual upstream model/provider when available. A separate OpenAI-compatible adapter supports approved local inference connectivity.

## Context package

A context package is a durable structure, not only a concatenated string. It contains:

- request purpose, project, branch, tick, POV, and plan references
- context policy and version
- ordered context items
- source record and revision for each item
- visibility classification
- retrieval reason and scores
- estimated/actual token count
- exclusions and truncation decisions
- rendered prompt sections and template version
- previous-prose units and their semantic, temporal, narrative, author-importance, and continuity scores
- selected author-only secrets, separately labeled from POV-accessible knowledge

This enables debugging, replay, cost analysis, and regression testing.

## Retrieval pipeline

1. **Resolve anchors.** Parse explicit entity links, scene-plan participants, POV, location, and user-selected entities.
2. **Load deterministic state.** Query branch/tick-active facts, beliefs, constraints, goals, and narrative requirements.
3. **Apply visibility.** Separate POV-accessible knowledge, public knowledge, and author-only direction.
4. **Expand structurally.** Traverse only predicates allowed by the workflow policy with depth and result caps.
5. **Expand semantically.** Search relevant summaries and previous-prose chunks, filtered by tenant/project, branch ancestry, active version, entity IDs, and visibility.
6. **Rank and deduplicate.** Combine explicit priority, graph distance, semantic relevance, narrative/world-time distance, author-assigned importance, continuity impact, unresolved threads, and diversity. Keep changing weights as metadata rather than modifying semantic vectors.
7. **Budget.** Allocate tokens by category, preserving hard constraints before stylistic examples.
8. **Render.** Build distinct sections for canon, POV beliefs, story intent, relevant history, style, and task instructions.
9. **Trace.** Persist why each item was included or excluded.

Semantic retrieval suggests candidates; it never decides truth.

## Vector document model

Each indexed item includes:

- stable document and version IDs
- project, branch, source revision, and active status
- document kind and layer
- entity and event references
- world and narrative positions
- visibility/access labels
- source hash and embedding-model version
- exact source pointer

Recommended layers:

- compact scene/event summary for continuity
- event block for semantic recall
- exact scene text for style retrieval
- preceding passages and nearby scene text for local prose continuity
- current entity or rule summary derived from canon
- planning document/thread summary

Never overwrite a version in place. Mark it inactive and index the replacement. Reindexing must be idempotent.

## Scene generation pipeline

1. Validate the scene plan and required fields.
2. Run deterministic consistency preflight and build its constraint packet.
3. Build a context package including relevant previous prose.
4. Show the author a context preview and allow pin/exclude/importance operations.
5. Resolve the autonomy/consistency profile, credential route, and credit reservation.
6. Create an immutable generation job.
7. Generate one or more drafts.
8. Store each draft separately with lineage and reconcile usage.
9. Run deterministic postflight checks, POV leakage validation, and configured model critics.
10. Let the author edit/select a draft and resolve findings.
11. Extract events, facts, beliefs, and state changes into a mutation set.
12. Resolve entity references and flag ambiguity.
13. Validate the mutation set.
14. Review or policy-approve eligible changes and commit them.

Generation and extraction should be separate calls. A prose model should not be trusted to produce the definitive state delta incidentally.

## Structured extraction

Extraction output uses operations such as:

- create entity
- assert fact
- close fact
- add or revise belief
- record event
- link participation/location/causality
- advance or annotate state
- open, progress, or resolve narrative thread

Each operation includes evidence spans from the scene, confidence, temporary references, and whether explicit author review is mandatory. Domain code resolves IDs and enforces cardinality/temporal rules.

## Consistency system

Use three layers:

1. **Deterministic rules:** impossible references, branch leakage, overlapping exclusive facts, chronology, knowledge access, missing required resources, and plan prerequisites.
2. **Graph queries:** relationship and path checks whose truth is stored explicitly.
3. **Model critic:** voice, subtle motivation, thematic alignment, and soft contradictions. Critic findings are advisory and cite supplied evidence.

The complete tier model, incremental invalidation strategy, cost profiles, previous-prose scoring, and correction workflow are specified in `13-consistency-system.md`.

Findings have severity, category, evidence, affected records, suggested resolutions, and rule/prompt version.

## Prompt management

- Store prompts as versioned files with declared inputs and outputs.
- Keep task instructions separate from retrieved user content.
- Delimit and label untrusted manuscript text to reduce prompt injection.
- Persist hashes or versions, not secret environment configuration.
- Test rendering without a provider.
- Retain a compatibility window for replaying old jobs.

## Reliability and cost

- Classify failures as transient provider, rate limit, invalid structured output, context overflow, policy violation, or domain rejection.
- Retry transient failures with bounded exponential backoff and idempotency keys.
- Repair structured output only through a bounded validation loop.
- Estimate tokens/cost before execution and expose limits to the author.
- Reserve platform credits before provider calls and reconcile actual cost idempotently; BYOK calls do not consume platform AI credits.
- Cache embeddings by content hash and embedding model.
- Allow cancellation between pipeline stages.

## Evaluation programme

Maintain versioned fixtures for:

- entity resolution and duplicate avoidance
- POV secret leakage
- temporal and branch correctness
- context relevance and omission
- structured extraction precision/recall
- contradiction detection
- character voice and scene-plan adherence
- prompt-injection resistance
- deterministic and model-assisted consistency detection by category and cost tier
- previous-prose retrieval across recency, importance, branch, and narrative-distance cases

Run deterministic contract tests on every change. Run provider evaluations explicitly, record cost/model/date, and compare against an approved baseline before prompt or model migrations.
