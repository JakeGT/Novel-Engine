# AI and retrieval

## Principle

AI participates in bounded, inspectable tasks. It does not own canonical state. Each task has a typed input, typed output where practical, explicit context policy, versioned prompt, model policy, validation, and evaluation set.

## AI capabilities

Define provider-neutral ports for:

- structured completion
- long-form text generation
- typed multimodal embeddings for text, images, audio, and video
- image/audio/video understanding with declared provider capabilities and limits
- optional critique/ranking

An operation declares capability requirements rather than a hard-coded model name. Provider configuration maps requirements to actual models and limits. OpenRouter is the primary cloud gateway; the system records the router and actual upstream model/provider when available. A separate OpenAI-compatible adapter supports approved local inference connectivity.

`google/embeddinggemma-2` is the preferred first local multimodal embedding candidate, subject to
the product-specific evaluation and migration gate in `17-multimodal-context.md`. The architecture
does not make one model or vector dimension authoritative.

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
- reader disclosures and unresolved setup/payoff obligations at the selected narrative position
- typed text, image, audio, and video parts with asset/segment/version pointers
- media authority, branch/time applicability, visibility, explicit-link role, and score components
- the original media or derived caption/transcript representation actually delivered to the model
- provider modality limits, exclusions, transformations, and degradation decisions

This enables debugging, replay, cost analysis, and regression testing.

## Retrieval pipeline

1. **Resolve anchors.** Parse explicit entity links, scene-plan participants, POV, location, and user-selected entities.
2. **Load deterministic state.** Query branch/tick-active facts, beliefs, constraints, goals, and narrative requirements.
3. **Apply visibility.** Separate POV-accessible knowledge, public knowledge, and author-only direction.
4. **Expand structurally.** Traverse only predicates allowed by the workflow policy with depth and result caps.
5. **Expand semantically.** Search relevant summaries and previous-prose chunks, filtered by workspace/project, branch ancestry, active version, entity IDs, and visibility.
6. **Expand across media.** Load eligible explicit asset links, then search shared-space image/audio/video segments without widening hard scope.
7. **Rank and deduplicate.** Combine explicit priority, source authority, graph distance, semantic relevance, narrative/world-time distance, author-assigned importance, continuity impact, unresolved threads, and diversity. Cluster an asset with its caption/transcript/facts so representations do not vote independently. Keep changing weights as metadata rather than modifying semantic vectors.
8. **Negotiate capabilities.** Select original media, a declared derived-text fallback, or fail before spending when the requested operation requires an unsupported modality.
9. **Budget.** Allocate text tokens, media counts/sizes/durations, and provider cost by category, preserving hard constraints before stylistic examples.
10. **Render.** Build distinct text sections and typed media parts for canon, POV beliefs, story intent, relevant history, style, and task instructions.
11. **Trace.** Persist why each item and representation was included or excluded.

Semantic retrieval suggests candidates; it never decides truth.

Rendering uses deterministic stable-to-volatile section ordering to support provider prompt
caching: policy, schema/world laws and invariant summaries, stable style/bibles, filtered canon,
scene contract/task, then recent prose and volatile evidence. Visibility and tenant boundaries are
applied before rendering; no cache prefix is shared across incompatible scope. Provider-reported
cache-read/write usage is recorded when available and `unsupported` otherwise.

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
- modality, asset/segment/version pointer, source authority, and explicit-link role where applicable
- embedding task/prefix, normalization, dimension, model revision, and representation kind

Recommended layers:

- compact scene/event summary for continuity
- event block for semantic recall
- exact scene text for style retrieval
- preceding passages and nearby scene text for local prose continuity
- current entity or rule summary derived from canon
- planning document/thread summary

Never overwrite a version in place. Mark it inactive and index the replacement. Reindexing must be idempotent.

The multimodal vector model, ingestion/indexing lifecycle, typed context-part contract, and
cross-modal evaluation set are specified in `17-multimodal-context.md`. Query and corpus embedding
operations are distinct. Incompatible embedding generations are never mixed in one search.

## Scene generation pipeline

1. Validate the scene plan and required fields.
2. Run deterministic consistency preflight and build its constraint packet.
3. Build a context package including relevant previous prose.
4. Add eligible linked/retrieved media and resolve the selected route's original-media or derived-text representation.
5. Show the author a context preview and allow pin/exclude/importance/require-original operations.
6. Resolve the autonomy/consistency profile, authorized credential route, and token/cost limit.
7. Create an immutable generation job.
8. Generate one or more drafts.
9. Store each draft separately with lineage and reconcile usage.
10. Run deterministic postflight checks, POV leakage validation, and configured model critics.
11. Let the author edit/select a draft and resolve findings.
12. Extract events, facts, beliefs, state changes, and proposed media observations into a mutation set.
13. Resolve entity references and flag ambiguity.
14. Validate the mutation set.
15. Review or policy-approve eligible changes and commit them.

Generation and extraction should be separate calls. A prose model should not be trusted to produce the definitive state delta incidentally.

Narrated assertions, disclosures, aliases, and perception outcomes may also be extracted, but remain
reviewable proposals. Deterministic speech/perception policies may derive proposed belief mutations
only after their source assertions/events are reviewed.

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
- Keep stable, deterministically serialized sections before scene-specific and recent-prose
  sections so compatible providers can reuse a cacheable prefix without altering semantic order.
- Retain a compatibility window for replaying old jobs.

## Reliability and cost

- Classify failures as transient provider, rate limit, invalid structured output, context overflow, policy violation, or domain rejection.
- Retry transient failures with bounded exponential backoff and idempotency keys.
- Repair structured output only through a bounded validation loop.
- Estimate tokens/cost before execution and expose limits to the author.
- Check the user's configured limit before provider calls and record actual usage idempotently; OpenRouter charges the user's account directly.
- Cache embeddings by content hash and embedding model.
- Allow cancellation between pipeline stages.

## Evaluation programme

Maintain versioned fixtures for:

- entity resolution and duplicate avoidance
- POV secret leakage
- temporal and branch correctness
- context relevance and omission
- structured extraction precision/recall
- alias/epithet resolution, including reviewed negative examples and POV/time-dependent aliases
- narrated-assertion, reader-disclosure, and belief-propagation precision/recall
- contradiction detection
- character voice and scene-plan adherence
- deterministic voice-drift triage across sample sizes, languages, and intentional style changes
- prompt-injection resistance
- deterministic and model-assisted consistency detection by category and cost tier
- previous-prose retrieval across recency, importance, branch, and narrative-distance cases
- text-to-media, media-to-text, and media-to-media relevance with explicit-link precedence
- media branch/time/POV/tenant isolation and capability-fallback fidelity
- multimodal consistency cases with authoritative and misleading media hard negatives

Run deterministic contract tests on every change. Run provider evaluations explicitly, record cost/model/date, and compare against an approved baseline before prompt or model migrations.

## Revision 2026-10-10: the assistant's tasks

"AI participates in bounded, inspectable tasks" stands, and conversation is now one of them. Each
assistant task is bounded, versioned, schema-constrained, and ends in a proposal:

| Task | Input | Output | Status |
| --- | --- | --- | --- |
| Questions for a description | description, optional type/name, existing entries | 5–8 questions | Built |
| Draft from a description | description, answers, vocabulary, existing entries | entries, details, facts, beliefs | Built |
| Fill in an entry | the entry, what is recorded, answers | details, names, facts | Built |
| Start from a premise | a paragraph | starter world + open questions | Phase 2.5 |
| Suggest links | an entry, semantic candidates | candidate facts for confirmation | Phase 2.5 |
| Revise a proposed change | the change, an instruction | a new revision of that operation | Phase 2.5 |
| Change by instruction (God Hand) | any instruction, tool access to all canon | every implied operation of any kind + consequences + questions | Phase 2.5 |
| Planning interview | premise, bible, plan so far | arcs, beats, scene plans, open items | Phase 2.6 |
| What happens next | plan and bible | 2–3 directions with consequences | Phase 2.6 |
| Draft a rule | a sentence | invariant + caught/not-caught examples | Phase 2.7 |
| Ask canon | a question | an answer with sources | Phase 2.7 |

Rules that apply to all of them:

- Output formats must convert to a strict JSON schema; free-form objects are not accepted by
  providers (found 2026-10-10; enforced by a test for the creation format).
- The hosted default model is configured as an OpenRouter id that supports structured output.
- Model text is never trusted: every item is typed and validated, and what does not fit is
  discarded with a reason shown to the author.
- A missing field in a plan or a scene is a question to the author, not a pipeline failure.
- Duplicate avoidance today is normalized name and alias within a type. Semantic resolution is
  Phase 2.5 and produces suggestions only ("Semantic retrieval suggests candidates; it never
  decides truth" is unchanged).

Additions to the evaluation programme: question usefulness (answered rate, skipped rate),
drafted-entry and drafted-link precision/recall, duplicate rate, kept-rate of drafted changes,
plan groundedness (share of plan items that reference real entries), and invention rate (plan or
bible items not supported by anything the author wrote).
