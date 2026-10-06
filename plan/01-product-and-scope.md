# Product and scope

## Product thesis

Novel Engine helps a fiction author build a world, plan a story, draft scenes, and evolve canon without losing continuity. Its advantage is not generic text generation; it is the ability to explain what is true, when it is true, on which branch it is true, who knows it, and why the system supplied it to a model.

## Primary user and distribution model

The initial user is a long-form fiction author working on one or more projects. Novel Engine is open-source-first and self-hostable, with a free invite-only deployment in the owner's portfolio under `apps.jaketalling.com`. An author can run it for personal use, call an OpenAI-compatible local model, or provide an OpenRouter API key. The core product does not require a subscription or platform credits.

The hosted profile is multi-tenant from the start: a shared portfolio identity service authenticates users, while Novel Engine owns invitations, workspaces, memberships, roles, and project authorization. There is no open self-registration into Novel Engine. The self-hosted profile supports a trusted single-user mode without changing canon semantics.

The author chooses an autonomy profile. AI may draft, analyze, retrieve, and propose changes; approval requirements depend on policy and risk. Destructive, cost-sensitive, schema-changing, secret-exposing, and conflicted-merge actions retain explicit safeguards.

## Core jobs to be done

1. Capture characters, locations, factions, objects, rules, history, and relationships without manually maintaining a brittle wiki.
2. Ask the project useful questions that expose missing or contradictory world design.
3. Plan arcs, beats, scenes, conflicts, promises, reveals, and payoffs against the current world state.
4. Assemble trustworthy context for a scene from explicit entities, current state, point-of-view knowledge, relevant history, and narrative intent.
5. Draft or revise prose while respecting constraints and established voice.
6. Review proposed state changes before accepting them into canon.
7. Explore alternatives on a branch without damaging the main timeline.
8. Diagnose contradictions with evidence and choose a resolution.

## Release-one workflow

Release one should support a complete author loop:

1. Create a project.
2. Create and edit core world entities.
3. Create characters with appearance, personality, beliefs, goals, and initial state.
4. Plan a scene with point of view, participants, location, intent, conflict, and expected state changes.
5. Preview the exact context selected for generation.
6. Generate or paste a draft.
7. Extract events and proposed canon mutations.
8. Run consistency checks.
9. Accept, amend, or reject each mutation.
10. Commit the scene and advance project state.
11. Reopen the scene and inspect its sources, decisions, and resulting state.
12. Run or schedule deeper continuity audits and apply reviewed correction proposals.

This is the minimum coherent product. A questionnaire without the review-and-commit loop is a utility, not yet the engine described by the product thesis.

## In scope for release one

- Single-project and multi-project local data separation
- Core entity and relationship authoring
- Fixed core schema with controlled custom extensions
- Explicit entity mentions and semantic entity suggestions
- Character beliefs separated from objective facts
- Linear timeline plus branch creation and comparison
- Story hierarchy: story, arc, beat, scene plan, scene
- Context preview with inclusion reasons and token budget
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
- Mobile-native applications
- Marketplace or public sharing of worlds
- Sophisticated graph visualization as the primary editor
- Subscription billing, token resale, and a platform-credit ledger
- Open public signup or free platform-funded inference

## Success measures

Product quality should be evaluated with tasks, not only model preference scores:

- An author can complete the release-one workflow without using Neo4j Browser or editing JSON.
- A committed scene can be reconstructed with its prompt inputs, model configuration, source context, and accepted mutations.
- Point-of-view context contains no known secret leakage in the curated evaluation set.
- Branch changes never appear in another branch unless inherited or merged explicitly.
- Rebuilding derived indexes produces equivalent retrieval results within documented tolerance.
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
- Export format documented and versioned.
- Deterministic consistency checks run on each generation without a paid model call.
