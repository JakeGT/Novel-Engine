# Product and scope

## Product thesis

Novel Engine helps a fiction author build a world, plan a story, draft scenes, and evolve canon without losing continuity. Its advantage is not generic text generation; it is the ability to explain what is true, when it is true, on which branch it is true, who knows it, and why the system supplied it to a model.

That engine is only worth having if using it is easier than not using it. The author-facing
principles (ADR-017, [`19-assistant-first-experience.md`](19-assistant-first-experience.md)) are:

1. Writing is never blocked by the tool's learning curve.
2. Everything can be done by talking to an assistant, through conversation or human-in-the-loop
   iteration; the by-hand and advanced controls remain as the alternative.
3. Advanced features are kept and revealed over time, as the story needs them.
4. Creation starts from description and interview, for the world and for the story plan.
5. The assistant never writes canon: it proposes, and the author decides.

## Primary user and distribution model

The initial user is a long-form fiction author working on one or more projects. Novel Engine is open-source-first and self-hostable, with a free invite-only deployment in the owner's portfolio under `apps.jaketalling.com`. An author can run it for personal use, call an OpenAI-compatible local model, or provide an OpenRouter API key. The core product does not require a subscription or platform credits.

The hosted profile is multi-tenant from the start: a shared portfolio identity service authenticates users, while Novel Engine owns invitations, workspaces, memberships, roles, and project authorization. There is no open self-registration into Novel Engine. The self-hosted profile supports a trusted single-user mode without changing canon semantics.

The author chooses an autonomy profile. AI may draft, analyze, retrieve, and propose changes; approval requirements depend on policy and risk. Destructive, cost-sensitive, schema-changing, secret-exposing, and conflicted-merge actions retain explicit safeguards.

## Core jobs to be done

1. Capture characters, locations, factions, objects, rules, history, and relationships without manually maintaining a brittle wiki.
2. Ask the project useful questions that expose missing or contradictory world design.
3. Plan arcs, beats, scenes, conflicts, promises, reveals, and payoffs against the current world state.
4. Assemble trustworthy context for a scene from explicit entities, current state, point-of-view knowledge, relevant history, and narrative intent.
5. Organize and retrieve character, location, object, and map pictures as scoped evidence rather than disconnected uploads. Audio and video references are deferred past release one (owner decision 2026-10-21).
6. Draft or revise prose while respecting constraints and established voice.
7. Review proposed state changes before accepting them into canon.
8. Explore alternatives on a branch without damaging the main timeline.
9. Diagnose contradictions with evidence and choose a resolution.
10. Express book-specific continuity rules and scene conditions as safe declarative checks.
11. Ask what was true, who knew it, why it was accepted, and what a proposed change would affect.
12. Export spoiler-safe bibles and use external assistants through the same scoped proposal boundary.

## Release-one workflow

Revised 2026-10-10. Release one supports a complete author loop in which every step can be done by
asking, and none requires it:

1. Sign in and describe the story in a paragraph. The assistant drafts a starter world; the author
   reviews and keeps what is right.
2. Fill in the to-do list: the assistant interviews the author about each entry that is only a
   name, and links entries together.
3. Plan the story by conversation: arcs, turning points, beats, and scenes, to whatever depth the
   author wants. Open questions stay visible as to-dos.
4. Plan a scene from a sentence. Missing point of view, place, or intent are asked for, not
   demanded.
5. See what the assistant will use to draft, in plain terms, and change it by saying so.
6. Draft the scene, write it by hand, or paste it.
7. Check it: findings appear beside the text with evidence, and "fix it" proposes a change.
8. Review the changes the scene makes to the world, grouped, and accept, amend, or skip them.
9. The story bible advances to a new version; nothing is lost and anything can be taken back.
10. Reopen any scene, entry, or version and see where it came from and why.
11. When the book needs them, reach for timelines, points of view, what-ifs, rules, and audits.

A questionnaire without the review-and-commit loop is a utility, not the engine described by the
product thesis. Equally, a review-and-commit loop that only an expert can feed is not a product an
author will keep using.

## In scope for release one

- Single-project and multi-project local data separation
- Core entity and relationship authoring
- Fixed core schema with controlled custom extensions
- An in-product assistant for creation, story planning, and every later capability, producing proposals only
- A by-hand path for every capability that works with no AI provider
- Guided first-run, plain-language naming, and level-based disclosure of advanced features
- Explicit entity mentions and semantic entity suggestions
- Character beliefs separated from objective facts
- Linear timeline plus branch creation and comparison
- Story hierarchy: story, arc, beat, scene plan, scene
- Context preview with inclusion reasons and token budget
- Project media library for images with explicit entity/scene links, provenance, authority, and branch/time/POV applicability (audio and video deferred past release one, owner decision 2026-10-21)
- Cross-modal retrieval and capability-aware context delivery with visible caption fallback
- One primary model provider behind an internal provider interface
- OpenRouter cloud routing plus an OpenAI-compatible local-provider path
- Structured model outputs with retry and validation
- User-configurable autonomy with recorded approvals and overrides
- Tiered consistency checks and POV secret-leakage validation
- Local provider configuration and user-supplied OpenRouter credentials
- Invite-only multi-tenancy with owner/editor/viewer workspace and project roles
- OIDC integration with the shared `apps.jaketalling.com` identity service
- A no-account single-user mode for self-hosters
- Reproducible self-hosted installation, upgrades, backup, and restore
- Export and backup of all user-authored and canonical data
- Deterministic test fixtures and offline development mode

## Explicitly deferred

- Real-time multi-author cursor-level collaboration unless explicitly selected
- Reader-facing interactive fiction runtime
- Autonomous publishing or external messaging
- Fine-tuning custom models
- Unbounded automatic canon commits without autonomy policy, audit, and protected-action review
- Arbitrary user-authored executable rules
- Unreviewed assistant, import, extraction, propagation, or MCP writes to accepted canon
- Mobile-native applications
- Marketplace or public sharing of worlds
- Generative image, music, voice-cloning, or video creation; release one consumes author-provided media as context
- Sophisticated graph visualization as the primary editor
- Subscription billing, token resale, and a platform-credit ledger
- Open public signup or free platform-funded inference

## Success measures

Product quality should be evaluated with tasks, not only model preference scores:

- An author can complete the release-one workflow without using Neo4j Browser or editing JSON.
- A new author gets a first accepted entry within 3 minutes, five linked entries from a paragraph
  within 10, and a first story plan within 30, with no documentation (document 19).
- Every capability has a tested assistant path and a tested by-hand path.
- No screen at Levels 1–3 shows an internal term outside technical details.
- A committed scene can be reconstructed with its prompt inputs, model configuration, source context, and accepted mutations.
- Point-of-view context contains no known secret leakage in the curated evaluation set.
- Branch changes never appear in another branch unless inherited or merged explicitly.
- Rebuilding derived indexes produces equivalent retrieval results within documented tolerance.
- Text-to-media and media-to-text retrieval returns applicable evidence without tenant, branch, timeline, or POV leakage.
- A new contributor can run deterministic tests without an API key.
- The system provides a useful explanation for every reported contradiction.
- A complete project can be installed, used, backed up, restored, and exported in self-hosted mode without a Novel Engine account.
- A portfolio identity without a valid Novel Engine invitation cannot access a workspace or project.
- Cross-tenant access attempts fail at API, repository, job, graph, vector, object, event, and export boundaries.
- Provider retries cannot duplicate a generation or silently exceed the author's configured cost/token limits.
- No telemetry or manuscript content leaves a self-hosted installation unless the operator enables it or invokes a configured model provider.

## Non-functional goals

- Local interactive reads: p95 under 300 ms excluding model calls.
- Context assembly: p95 under 2 seconds for a medium project after warm-up.
- No silent loss of accepted canon changes.
- All long model operations cancellable and retryable.
- Graceful operation when embeddings or a model provider are unavailable.
- Graceful, visible degradation when media processing or a selected model modality is unavailable.
- Export format documented and versioned.
- Deterministic consistency checks run on each generation without a paid model call.
