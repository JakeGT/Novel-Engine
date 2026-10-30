# Delivery roadmap

## Delivery strategy

Build vertical slices in dependency order. Each phase must leave the application more usable and the repository more testable. Estimates are deliberately expressed as scope, not calendar promises; team size, deployment choice, and product answers will change elapsed time.

## Phase 0 — Stabilize the prototype

Goal: create a deterministic development baseline before expanding functionality.

Execution checklist: [`phase-0-todo.md`](phase-0-todo.md).

Work:

- choose supported Python/Node versions and add CI
- add PostgreSQL to local Compose and establish migration tooling
- split runtime/dev dependencies and produce reproducible locks
- move connection creation into FastAPI lifespan/dependency injection
- define graph, vector, AI, clock, and ID-generator ports
- standardize entity/edge DTOs and error responses
- repair route ordering and current character/world workflow defects
- move legacy tests into a marked integration suite
- add unit characterization for resolver, policies, summarizer, and schemas
- create disposable Neo4j integration fixtures
- replace implicit startup index creation with migrations
- make OpenAI and semantic embeddings optional at startup
- add safe non-production reset/reseed commands and the reference seed world

Exit criteria:

- clean checkout setup is documented and repeatable
- unit tests collect/run without PostgreSQL, Neo4j, embeddings, or API keys
- integration tests create and destroy their own database state
- no external connection or data write occurs on module import
- lint, unit tests, and frontend build run in CI
- current workflows have documented, stable responses

## Phase 1 — Identity, tenancy, distribution, and project foundation

Goal: make the stabilized prototype installable and establish invite-only hosted tenant boundaries before storing valuable manuscripts.

Work:

- documented Docker Compose self-hosted installation and upgrade path
- shared OIDC identity-provider port and secure application sessions for the portfolio deployment
- invite-only workspace membership with owner/editor/viewer authorization
- explicit local/single-user identity mode that hosted mode cannot enable
- workspace/project-scoped PostgreSQL repositories and audit records
- local secret configuration for OpenRouter and OpenAI-compatible endpoints
- model-usage normalization plus per-job token/cost estimates and caps
- backup, restore, export, and deterministic seed workflows
- offline fake-provider mode and local-model capability discovery
- contributor-facing configuration, diagnostics, and troubleshooting
- invitation, cross-tenant, key-redaction, backup, and upgrade tests

Exit criteria:

- a clean machine can install and start the supported stack from the documentation
- every hosted persistence path enforces workspace/project scope
- a portfolio identity receives no Novel Engine access without a valid invitation
- hosted mode refuses unsafe local-identity configuration
- API responses, logs, and exports never expose provider keys
- deterministic seed data boots a clean development environment
- a project survives backup/restore and complete archive export/import
- no core request depends on a billing API or project-funded inference service

## Phase 2 — Trustworthy canon

Detailed execution plan: [`phase-2-todo.md`](phase-2-todo.md).

Goal: authors can safely create and maintain isolated project canon.

Work:

- project aggregate and project-scoped repositories
- PostgreSQL-authoritative immutable journal plus normalized/versioned canon; replayable Neo4j
  projection with revision watermarks
- fixed core schema and validated custom extensions
- canonical entity/fact/belief/event models
- revision and provenance model
- canonical point-in-time/history query boundary, immutable revision tags, and compensating revert
  proposals
- invariant-evaluator seam separating permanent built-in integrity from warning-default author
  story rules
- dependency/read-model outbox conventions and visible projection watermarks
- entity CRUD/search and relationship/fact editor APIs
- media-asset identity, immutable source versions, authority/provenance, explicit domain links, and export semantics
- mutation journal and atomic commit service
- author-originated mutation review path
- project shell and world/character editors
- basic export/import with schema version

Exit criteria:

- two projects cannot read or reference each other's data
- author edits produce audited, revisioned commits
- facts and beliefs are distinct in storage and UI
- entities can be retired without losing history
- project export round-trips into a clean environment

## Phase 2.5 — Assistant-led creation

Revised 2026-10-10 (ADR-017, [`19-assistant-first-experience.md`](19-assistant-first-experience.md)).
The former Phase 2.5 (prose intake) is now Phase 2.7.

Goal: a new author goes from an idea to a reviewed, connected story bible by describing it, and
never has to learn the data model to do so.

Already built on 2026-10-10, ahead of this plan and counted here: assisted creation (describe one
thing or a whole idea → optional questions → one proposal of entries, details, and links), the
to-fill-in list, filling in any entry by interview, a starter schema on project creation, the
redesigned interface. **Not yet verified against a real model.**

Work:

- verify and tune assisted creation with real providers; a labelled evaluation set for drafted
  entries and links built from the reference world
- start from a premise: one conversation produces a starter world and a to-do list
- the God Hand: one agent on every screen that takes any instruction about the story and proposes
  every change it implies, including edits, renames, ending facts, archiving, and merges, with a
  report of what else is affected and one-step undo of the accepted proposal (after grouped review
  and the evaluation set exist)
- conversations are stored (description, questions, answers, proposal) and can be resumed
- link suggestions for existing entries using the derived vector index, confirmed by the author,
  never merged automatically
- grouped review: proposals summarised by kind, safe bulk decisions, "revise this change" by
  asking, and a check of drafted proposals against existing canon before review
- first-run: guided provider setup, a first session inside a real project, and an optional sample
  project (pulled forward from Phase 7)
- the level model: what is shown and offered at Levels 1–2, and the plain-language test over
  rendered screens
- by-asking paths for capabilities that exist only by hand: undo ("take it back to before…"),
  custom types and kinds of fact, inviting collaborators (with a members page)
- a by-hand editor for beliefs
- pictures on entries: upload an image and link it to an entry (a character's portrait, a place,
  an object) or, once scenes exist, to a scene; shown on the entry page and in the story bible;
  images only, size- and type-checked, stored as immutable media versions and linked through the
  existing reviewed link operation (added 2026-10-10 at the owner's request; the rest of media
  ingestion stays in Phase 4)
- session budgets and cost shown before model calls; assistant-specific threat cases

Exit criteria:

- the measures in document 19 for first entry and premise-to-linked-entries are met on new
  accounts with a real provider
- every Level 1–2 capability in the capability map has tested assistant and by-hand paths
- a plain-language instruction that changes existing canon produces one reviewable proposal that
  includes its consequences, and nothing is applied without approval
- the project is fully usable with no provider configured
- drafted-change precision, recall, duplicate rate, and kept-rate are recorded against a
  versioned baseline
- no Level 1–2 screen shows an internal term outside technical details

## Phase 2.6 — Story planning through conversation

Goal: an author builds a complete story plan by talking it through, grounded in the story bible.

Work (story structure pulled forward from Phase 5):

- story, arc, beat, and scene-plan records as canon-governed intent, distinct from fact, with
  history, proposals, and review
- the planning interview: premise and stakes → arcs → turning points → beats → scenes, stopping at
  any depth, each round ending in a reviewable proposal
- "what happens next?": two or three grounded directions with what each would change
- threads, promises, and payoffs as records; open ones shown as to-dos
- scene plans drafted from a sentence; missing point of view, place, or intent are questions, not
  errors; typed scene contracts stay optional and inferred where possible
- an outline view and editor as the by-hand path
- readiness shown in plain words ("this scene needs a place")
- evaluation fixtures for plan quality and groundedness

Exit criteria:

- a first plan (arcs and at least six beats) is accepted within the document 19 target
- a plan can be built with no provider through the outline editor
- planned intent never appears as established fact in any query or export
- gaps are reported as open items, never filled by invention
- an author who does not outline can ignore the plan entirely and is not blocked

## Phase 2.7 — Text intake (formerly Phase 2.5)

Goal: prose that already exists becomes another way into the same loop, and validates extraction
quality, entity resolution, and authored continuity rules with real text.

Work:

- paste and Markdown manuscript/scene import with immutable source versions
- explicit entity marks and @-mentions as the primary deterministic signal
- time/POV-scoped alias and epithet memory with reviewed positive/negative corrections, asked as
  one question at a time ("is 'the Fox' Mara?")
- bounded structured extraction into the grouped proposals built in Phase 2.5
- typed scene preconditions/postconditions and readiness/postflight results
- story rules written as a sentence, drafted into a declarative invariant with examples it would
  and would not catch; warning by default and explicit enablement to block (ADR-013)
- commit, provenance/blame, revision comparison, tag, and compensating revert views
- proposal-only MCP canon access if authentication is ready
- labelled extraction/entity-resolution evaluation set built from the reference world

Exit criteria:

- an author can import or paste prose, resolve its entities, review grouped proposals, and commit
  accepted canon without invoking generation
- invariant examples, scene conditions, and commit results agree deterministically
- extraction and entity-resolution precision/recall are recorded against a versioned baseline
- review telemetry demonstrates that a scene does not require blind operation-by-operation
  rubber-stamping
- every accepted change can be explained back to source evidence and review decision

## Phase 3 — Time, branches, and knowledge

Goal: canonical queries are correct for a selected branch, world time, and point of view.

Author experience (document 19): all of this is Level 5. Defaults are one main branch, unspecified
world time, and the author's own point of view, so earlier levels never choose among them. The
assistant paths are "this happened three years before the war", "Tobin doesn't know this", and
"what if Mara refused?"; each is offered only when the story shows the problem it solves.

Work:

- world-time, narrative-order, and system-revision value objects
- temporal facts and belief intervals
- branch creation, overlay reads, comparison, and conflict model
- POV visibility/knowledge policy
- reader disclosure ledger, reveal planner, irony queries, and setup/payoff debt
- narrated assertions/speech acts plus reviewable perception/trust-based belief propagation
- secret handling and common-knowledge rules
- branch/time/POV applicability for media assets, links, and reviewed observations
- branch/timeline selector in the UI
- state/history inspectors
- location, possession, relationship, timeline, and disclosure read models with revision watermarks
- deterministic contradiction rules for time, cardinality, and knowledge
- spoiler-safe state queries and story-bible exports at narrative position/viewpoint

Exit criteria:

- reference-world invariant suite passes
- branch-local changes never leak to siblings
- false beliefs can drive POV context without being treated as canon
- flashback events preserve world chronology and narrative order
- conflicting merges require explicit resolution
- an agent may propose merge resolutions but cannot finalize a conflict without the configured human approval
- lies, mistakes, dreams, and unreliable narration remain distinct from objective facts
- reader/character knowledge differences support deterministic reveal and dramatic-irony checks
- spoiler-safe exports exclude later and inaccessible truth in leakage fixtures

## Phase 4 — Explainable context and derived memory

Goal: the author can preview and control reliable text and media scene context.

Author experience: Level 4. The assistant explains each source in a sentence and accepts "don't
use that"; pin, exclude, and budget controls are the by-hand path. Media arrives by "drop it in
and the assistant asks what it is"; authority, visibility, and scope have defaults.

Work:

- versioned context policies and context-manifest model
- deterministic graph retrieval scoped by project/branch/tick/POV
- vector adapter interface and versioned chunk model
- indexing outbox, worker, and rebuild command
- secure media ingestion lifecycle with immutable originals, variants, segments, and processing status
- provider-neutral multimodal embedding port and generation-based vector migration/rebuild
- EmbeddingGemma 2 evaluation against MiniLM and a strong text-only baseline; select 768/512 only from project metrics
- scene summaries/event blocks/full-text indexing
- image cross-modal indexing with captions as derived fallbacks; audio/video indexing and transcripts later (audio and video deferred past release one by owner decision, 2026-10-21)
- previous-prose retrieval with semantic, two-clock recency, author importance, narrative proximity, continuity impact, and thread signals
- hybrid ranking, deduplication, diversity, and token budgeting
- context preview API and context inspector UI
- stable-to-volatile deterministic context rendering for prompt-cache-compatible prefixes and
  provider-reported cache-usage accounting
- project media library and context cards showing authority, branch/time/POV scope, timestamps/regions, and delivery representation
- pin/exclude author controls
- retrieval and leakage evaluation suite

Exit criteria:

- every context item shows source and inclusion reason
- vector deletion/rebuild preserves canonical state and meets retrieval baseline
- stale index state is visible
- token budgets preserve hard constraints over soft style context
- curated POV leakage tests pass
- text-to-media and media-to-text retrieval meets the approved baseline
- incompatible vector generations cannot mix, and reindex/rollback is observable
- original media is never silently dropped when a selected model lacks the modality

## Phase 5 — Story planning, consistency, and scene studio

Goal: an author can plan and draft a scene using controlled multimodal context.

Author experience: Level 4. Story structure and scene plans moved to Phase 2.6; this phase drafts
from them. Findings appear as margin notes with "explain" and "fix" by asking; tiers, profiles,
and suppressions are the advanced view. One "Check" action states its cost before it runs.

Work:

- (story/arc/beat/scene-plan models and APIs: moved to Phase 2.6)
- typed scene contracts and project-defined character-arc state paths
- narrative threads, requirements, intended changes, reveals, and payoffs
- scene readiness checks
- async job framework, progress stream, cancellation, and budgets
- provider-neutral generation gateway
- typed text/image context parts and capability-aware OpenRouter/local adapter translation; audio/video parts stay declared only (audio and video deferred past release one by owner decision, 2026-10-21)
- OpenRouter BYOK/local-provider routing and normalized usage records
- versioned generation prompts and fake adapter
- scene editor, entity mentions, context preview, and draft variants
- immutable scene versions and lineage
- Tier 0/1 deterministic consistency preflight/postflight
- multimodal consistency checks over reviewed media facts plus evidence-citing image critic passes (audio and video deferred past release one by owner decision, 2026-10-21)
- user-triggered Tier 2 model critics, including isolated POV secret-leakage validation, with contextual recommendations and opt-in budgeted automation
- consistency findings/correction review UI and incremental invalidation
- dependency-backed canon-edit impact preview and scoped finding suppressions
- deterministic voice fingerprints with model critic escalation only for flagged passages
- deterministic dependency grouping/coverage prototype inspired by OpenCodeReview

Exit criteria:

- a planned scene can be generated through an observable cancellable job
- draft lineage and exact context are inspectable
- manual editing works without an AI provider
- plan requirements and findings are visible beside the draft
- failures do not lose plans or manual prose
- deterministic consistency runs on each generation without paid inference
- provider attempts and actual usage are recorded exactly once
- context manifests reproduce whether original media or a derived fallback reached the provider

## Phase 6 — Extraction, review, and commit loop

Goal: a drafted scene can safely evolve canon.

Author experience: Level 4. Extracted changes use the grouped review from Phase 2.5; a plan can be
derived from accepted scenes for authors who do not outline.

Work:

- event/fact/belief extraction contract
- caption/visual observations may propose evidence-backed mutations through the same review contract (transcript/audio observations deferred with audio and video, 2026-10-21)
- evidence spans and entity-resolution workflow
- reuse the Phase 2.5 alias/correction memory and extraction evaluation baseline
- mutation-set validation and status machine
- per-operation accept/edit/reject UI
- optimistic head check and canonical commit
- derived indexing after commit
- consistency rerun and thread progression
- repair/recovery for partially failed commits
- end-to-end workflow evaluations

Exit criteria:

- no model operation writes canon directly
- every accepted mutation links to scene evidence and review decision
- committing twice is idempotent
- stale reviews receive a conflict rather than overwriting newer state
- full release-one workflow passes from a clean project

## Phase 7 — Open-source release hardening

Goal: make user data dependable and operation understandable.

Work:

- portfolio OIDC, session, invitation, and authorization hardening
- backup/restore automation and runbooks
- migration compatibility tests
- opt-in telemetry, cost controls, diagnostics, and privacy settings
- performance profiling and query/index tuning
- accessibility and keyboard workflow audit
- dependency/security scanning
- recovery UX (onboarding and the sample project moved to Phase 2.5)
- Tier 3 scheduled/on-demand chapter, act, and project audits
- reproducible release artefacts, upgrade notes, contributor/security policies, and license notices
- direct OpenAI-compatible local inference route and provider capability diagnostics
- media codec/scanner sandboxing, quotas, provider-retention/deletion behavior, orphan cleanup, and multimodal backup/restore hardening

Exit criteria:

- release quality gates in `08-testing-and-quality.md` pass
- recovery drills and upgrade tests pass
- known data-loss/security risks are resolved or explicitly accepted
- beta users can complete the workflow without developer assistance
- the self-hosted profile operates without a project-operated cloud dependency

## Later options

- richer branch merge/rebase tools
- generative image/audio/video creation and editing (picture retrieval/context is in release one)
- audio and video as context: upload, processing, transcripts, segments, retrieval, context parts and checks (deferred past release one by owner decision, 2026-10-21)
- richer local-model capability negotiation and offline workflows
- real-time collaborative editing, presence, and comments if not included in release one
- advanced graph/timeline visualization
- manuscript-wide revision and continuity reports
- importers for common writing tools beyond the release-one paste/Markdown intake path, including Google Docs, DOCX and LaTeX (deferred past release one by owner decision, 2026-10-21)
- delegated autonomy beyond "keep the safe ones for me" (the in-product assistant itself is Phases 2.5–2.6)
- public demo or managed multi-user hosting, with a separate funding/privacy/operations decision
- open signup or public community hosting beyond the invite-only portfolio deployment

## Dependency chain

```text
stable boundaries
  -> OIDC identity + invite-only tenancy + self-hosted packaging
    -> project-scoped canon + journal
      -> assistant-led creation + grouped review + first-run
        -> story planning through conversation
        -> text intake/extraction probe
        -> time/branch/reader+character knowledge semantics
          -> explainable text/media context + indexing
          -> planning + consistency + generation jobs
            -> extraction + review + commit
              -> open-source release hardening
```

Every phase from 2.5 onward is subject to the definition of done in document 19: an assistant
path, a by-hand path, a level, safe defaults, and tests of each.

Skipping an earlier semantic layer creates expensive ambiguity later. In particular, generation should not precede a stable review/commit model, and branching should not be bolted onto unscoped mutable graph records.
