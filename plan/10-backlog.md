# Dependency-ordered backlog

Each item is sized to be independently reviewable. IDs are stable references for issues and agent assignments. `P0` blocks later architecture; `P1` is release-one essential; `P2` is valuable but deferrable; `Deferred` is outside the current release sequence.

## Foundation

Phase 0 execution details, sub-items, dependencies, and exit evidence are maintained in [`phase-0-todo.md`](phase-0-todo.md).

- **FND-001 · P0 — Runtime lifecycle:** create Neo4j, vector, embedding, and AI clients through application lifespan and injected dependencies. Acceptance: importing `backend.main` performs no network connection or persistent write.
  - **Status:** Implemented (P0-05, commit `24f32cd`). Verified by `tests/unit/test_import_safety.py` and `tests/contract/test_lifecycle.py`.
- **FND-002 · P0 — Domain ports:** define repository, journal, vector, AI, clock, and ID interfaces with in-memory fakes. Acceptance: one application workflow runs entirely in memory.
  - **Status:** Implemented (P0-06, commit `24f32cd`; `backend/ports.py`). Verified by `tests/application/test_character_workflow.py`.
- **FND-003 · P0 — Stable DTOs:** define versioned entity/edge request and response models. Acceptance: create/read/search/update share one representation and OpenAPI examples.
  - **Status:** Implemented (P0-07, commit `576c859`). Verified by `tests/contract/test_openapi.py` and the v1 contract tests.
- **FND-004 · P0 — Error contract:** implement typed domain errors and API problem responses. Acceptance: validation, not-found, conflict, provider, and persistence cases are covered.
  - **Status:** Implemented (P0-07, commit `576c859`). Verified by `tests/contract/test_v1_errors.py`.
- **FND-005 · P0 — Migrations:** add explicit graph constraint/index migrations and version check. Acceptance: apply twice safely and verify expected schema.
  - **Status:** Implemented (P0-04, commit `24f32cd`). Verified by `tests/integration/test_neo4j_migrations.py`.
- **FND-005A · P0 — PostgreSQL foundation:** add PostgreSQL configuration, migrations, health, transaction boundary, and disposable test fixture.
  - **Status:** Implemented (P0-03, commit `24f32cd`; pgvector store added in P0-09). Verified by `tests/integration/test_postgres_*.py` and `test_pgvector_adapter.py`.
- **FND-006 · P0 — Test layout:** create deterministic unit/application suites and disposable integration fixtures. Acceptance: default tests need no services or secrets.
  - **Status:** Implemented (P0-02/P0-05, commits `24f32cd`/`c052079`; integration fixtures in P0-09). 188 hermetic + 16 integration tests.
- **FND-007 · P0 — Reproducible toolchain:** pin supported runtimes, lock dependencies, and configure CI. Acceptance: clean Linux runner passes static checks and unit tests.
  - **Status:** Implemented (P0-01/P0-11, commit `24f32cd`; full CI in the P0-11 worktree). Hosted CI run link pending first PR.
- **FND-008 · P0 — Prototype repairs:** fix property-shape mismatch, route specificity, world-service errors, character bio flow, rewritten edge targets, temporal edge persistence, and update-label behavior. Acceptance: each defect has a regression test.
  - **Status:** Partially implemented (P0-08, commit `c0476a0`; FND-008.1–22, 28–32). Deferred: FND-008.21 (atomic mutation → Phase 1) and FND-008.23–27 (world workflow → parked `world_service.py`).
- **FND-009 · P0 — Seed environment:** create deterministic PostgreSQL/Neo4j/vector/object fixtures for multiple isolated projects and the reference world, with reset/reseed commands safe only outside production.
  - **Status:** Implemented (P0-09). Two placeholder workspaces/projects support isolation fixtures without requiring production tenancy; verified by `tests/integration/test_seed_world.py` and `tests/unit/test_reset_guard.py`.

## Assistant and learning curve

Added 2026-10-10 (ADR-017, `19-assistant-first-experience.md`). These are release-one essential.

- **AST-EXP-001 · P1 — Assisted creation:** describe one thing or a whole idea, optional questions, one proposal of entries, details, and links; existing entries linked by name.
  - **Status:** Implemented 2026-10-10 (`canon_creation_workflow.py`, `/assist/*`, `AssistFlow.tsx`). Tested with a scripted model only.
- **AST-EXP-002 · P1 — To-fill-in list and fill-in interview:** bare entries are listed; the assistant fills in any entry.
  - **Status:** Implemented 2026-10-10 (`GET /entities/needs-detail`).
- **AST-EXP-003 · P1 — Real-provider verification and evaluation set:** run the built flows against real models; labelled set for drafted entries and links; record precision, recall, duplicate rate, kept-rate. Acceptance: a versioned baseline exists and regressions fail a gate.
- **AST-EXP-004 · P1 — Stored, resumable conversations:** description, questions, answers, and proposal kept together as evidence. Acceptance: a closed session can be read from the change it produced; an open one can be resumed.
- **AST-EXP-005 · P1 — Start from a premise:** one conversation produces a starter world and open questions. Acceptance: document 19's premise measure on a new account.
- **AST-EXP-017 · P1 — The God Hand:** one conversational agent with read access to all canon and the ability to propose any change (edit, rename, end, archive, merge, re-link, restructure), reporting consequences and asking when ambiguous. Acceptance: labelled instruction set with expected operations; nothing commits without review; an accepted proposal is undone in one step. Depends on AST-EXP-003 and AST-EXP-006. Restores the "God Hand" of `architecture.md`.
- **AST-EXP-006 · P1 — Grouped review:** proposals summarised by kind, safe bulk decisions, "revise this change" by asking, pre-review check against canon. Acceptance: a 40-change proposal is decided in under ten actions without blind approval.
- **AST-EXP-007 · P1 — Link suggestions:** semantic candidates for linking existing entries, confirmed by the author. Acceptance: no automatic merge; suggestion precision recorded.
- **AST-EXP-008 · P1 — First run:** guided provider setup, first session in a real project, optional sample project. Acceptance: first accepted entry within 3 minutes.
- **AST-EXP-009 · P1 — Levels and plain language:** default visibility by level, need-based suggestions, one name mapping, a rendered-screen test for internal terms.
- **AST-EXP-010 · P1 — By-asking paths for existing features:** undo, custom types and kinds of fact, invitations (with a members page); belief editor by hand.
- **AST-EXP-011 · P1 — Session budgets and assistant threat cases:** per-session call/token budget shown before calls; injection, retention, and degraded-provider tests.
- **AST-EXP-012 · P1 — Plan records:** story, arc, beat, scene plan, and thread as canon-governed intent. Acceptance: intent never appears as fact.
- **AST-EXP-013 · P1 — Planning interview and "what happens next":** top-down, stoppable at any depth, grounded in the bible, gaps left open. Acceptance: document 19's first-plan measure; groundedness and invention rate recorded.
- **AST-EXP-014 · P1 — Outline editor and readiness in plain words:** the by-hand path for plans.
- **AST-EXP-015 · P2 — "Keep the safe ones for me":** opt-in delegation of additive, reversible changes of kinds the author has approved before; everything listed and reversible.
- **AST-EXP-018 · P1 — Pictures on entries and scenes:** an author uploads an image and links it to an entry or a scene, with a role (portrait, reference); it appears on the entry page and as the entry's picture in lists. Images only for now; bounded size and verified type; no processing, captioning, or model use (Phase 4). Acceptance: upload → link → visible on the entry; removal retires the link and keeps history; cross-tenant access is refused; works with no AI provider.
- **AST-EXP-016 · P1 — Design direction:** the interface reads as a creative tool, not a dashboard (`20-design-direction.md`).

## Open-source distribution

- **OSS-001 · P0 — Reference deployment:** one documented Docker Compose path with health checks, persistent volumes, safe network defaults, and an upgrade contract.
- **OSS-002 · P0 — Local identity mode:** a no-signup single-user mode that is explicit, safe by default, and cannot be enabled in the hosted profile.
- **OSS-003 · P0 — Provider configuration:** redacted environment/config handling for user-supplied OpenRouter keys and OpenAI-compatible local endpoints.
- **OSS-004 · P1 — Data ownership:** versioned project export/import plus tested backup, restore, and migration instructions.
- **OSS-005 · P1 — Release and community:** license, notices, contribution guide, security policy, issue templates, changelog, and reproducible release artefacts.
- **OSS-006 · P1 — Privacy contract:** opt-in telemetry only, documented outbound connections, content-free diagnostics by default, and a no-provider workflow.
- **OSS-007 · P1 — Autonomy policy:** manual/assisted/delegated workflow decisions with mandatory-review categories and audit.
- **OSS-008 · P1 — Usage limits:** per-job estimates, configurable token/cost caps, cancellation, normalized usage, and retry safeguards.

## Invite-only identity and tenancy

- **IDN-001 · P0 — OIDC boundary:** provider-neutral authorization-code/PKCE integration, issuer/audience validation, external `(issuer, subject)` mapping, and fake development provider. The portfolio deployment uses Authentik (ADR-009); provisioning gates only the hosted wiring, not the port/fake work.
- **IDN-002 · P0 — Application sessions:** host-only secure cookies, CSRF protection, rotation, logout, expiry, revocation, and hosted-safe configuration guards.
- **IDN-003 · P0 — Workspace membership:** workspace plus owner/editor/viewer roles, least-privilege policy checks, and explicit project grants where needed.
- **IDN-004 · P0 — Invitations:** create, bind, expire, revoke, accept once, and audit workspace invitations without leaking tenant existence.
- **IDN-005 · P0 — Isolation enforcement:** workspace/project scope in PostgreSQL, Neo4j, vector, object, job, export, cache, and event adapters with adversarial tests.
- **IDN-006 · P1 — Identity operations:** recovery/admin expectations, MFA requirements for privileged users, account unlinking/deletion behavior, and runbooks for the shared portfolio IdP (Authentik).

## Deferred commercial platform

These IDs are retained for traceability from ADR-001. Identity and isolation work has moved to IDN-001–IDN-006; billing and platform-funded inference remain deferred by ADR-008.

- **SaaS-001 · Superseded — Accounts and sessions:** replaced by shared OIDC and IDN-001/IDN-002; open signup is not planned.
- **SaaS-002 · Superseded — Tenant and membership:** replaced by IDN-003/IDN-004.
- **SaaS-003 · Superseded — Isolation enforcement:** replaced by IDN-005.
- **SaaS-004 · Deferred — Subscription boundary:** plans, entitlements, subscription state, and verified idempotent webhook processing.
- **SaaS-005 · Deferred — Credit ledger:** grants, purchases, reservations, releases, reconciliations, refunds, and balance projection.
- **SaaS-006 · P1 — Hosted credential vault:** encrypted user-owned OpenRouter credential storage, rotation, revocation, worker access, and redaction; no platform funding.
- **SaaS-007 · Deferred — Hosted usage accounting:** provider cost and policy records needed by a managed service.
- **SaaS-008 · Deferred — Platform-funded jobs:** funding selection, reservation, reconciliation, and failure recovery.
- **SaaS-009 · Deferred — Hosted autonomy policy:** workspace-level constraints layered over project/user choices.
- **SaaS-010 · Deferred — Hosted abuse controls:** rate limits, quotas, anomaly alerts, and support overrides.

## Canon and schema

- **CAN-001 · P0 — Project aggregate:** introduce project ID and scope all repository methods.
- **CAN-002 · P0 — Core schema:** define typed core nodes/predicates, property schemas, endpoint constraints, and cardinality.
- **CAN-003 · P1 — Custom schema governance:** project-scoped custom types with validation and schema versions.
- **CAN-003A · P1 — Runtime typed schema compiler:** compile safe project extensions to cached JSON Schema/Pydantic validators for AI output and canon writes; never execute user code.
- **CAN-004 · P0 — Fact model:** implement reified temporal assertions plus query projections.
- **CAN-005 · P0 — Belief model:** implement holder-scoped assertions, confidence, source, and validity.
- **CAN-006 · P0 — Event model:** implement chronological events with participants, location, cause, and scene source.
- **CAN-007 · P0 — Provenance:** require actor/origin/source/revision on canon writes.
- **CAN-008 · P0 — Mutation journal:** append, validate, commit, recover, and verify checksums.
- **CAN-009 · P1 — Entity authoring API/UI:** typed list, search, create, edit, link, and retire workflows.
- **CAN-010 · P1 — Export/import:** versioned complete project archive and clean restore validation.
- **CAN-011 · P0 — Canon query/explanation boundary:** point-in-time state, revision differences,
  belief holders, and provenance through commit/decision/source evidence, shared by every transport.
- **CAN-012 · P1 — Tags and compensation:** immutable named revision pointers plus conflict-aware
  compensating revert proposals through normal review.
- **CAN-013 · P0 — Story invariant contract:** safe versioned predicate AST, dependencies, examples,
  warning default, explicit blocking publication, and separation from permanent integrity rules.

## Time, branches, and visibility

- **TMP-001 · P0 — Three-clock types:** world, narrative, and revision positions with explicit conversion limits.
- **TMP-002 · P0 — Temporal query:** active state at branch/tick using half-open intervals.
- **TMP-003 · P0 — Branch overlay:** fork, inherited reads, local mutation, and head revision.
- **TMP-004 · P1 — Branch comparison:** mutations from common ancestor and conflict categorization.
- **TMP-005 · P1 — Merge review:** author-resolved merge mutation set.
- **KNW-001 · P0 — Visibility policy:** public, private, secret, author-only, and character-accessible classification.
- **KNW-002 · P0 — Knowledge acquisition:** beliefs linked to perception/report/inference events.
- **KNW-003 · P1 — POV context gate:** deterministic filtering and leakage explanations.
- **KNW-004 · P1 — Reader disclosure ledger:** explicit/implied disclosures by narrative position,
  audience, source, and evidence with irony/reveal queries.
- **KNW-005 · P1 — Narrated assertions and speech acts:** speaker/listener/overhearer, sincerity,
  modality, visibility, and evidence without treating narration/dialogue as objective truth.
- **KNW-006 · P1 — Belief propagation proposals:** deterministic perception/report/trust policies
  that emit reviewed belief mutation sets and never write canon directly.

## Retrieval and memory

- **CTX-001 · P0 — Context manifest:** durable typed package and source trace.
- **CTX-002 · P0 — Policy engine:** versioned purpose-specific structural retrieval policies.
- **CTX-003 · P1 — Mention resolver:** parse, validate, autocomplete, and repair stable entity mentions.
- **CTX-004 · P0 — Vector port:** versioned documents, filters, health, deletion, and rebuild contract; implement pgvector first unless ADR changes it.
- **CTX-005 · P0 — Index outbox:** idempotent derived updates and lag reporting.
- **CTX-006 · P1 — Scene chunker:** summary, event-block, and full-scene layers.
- **CTX-006A · P1 — Previous-prose units:** preceding passages, nearby scene text, event blocks, summaries, voice exemplars, and thread-linked passages.
- **CTX-007 · P1 — Hybrid ranker:** explicit, graph, semantic, two-clock temporal decay, author importance, continuity impact, narrative/thread, and diversity signals.
- **CTX-008 · P1 — Token budgeter:** hard/soft categories and deterministic truncation.
- **CTX-009 · P1 — Context preview API/UI:** inspect, pin, exclude, and compare packages.
- **CTX-010 · P0 — Retrieval evaluations:** relevance, omission, leakage, and branch correctness fixtures.
- **CTX-011 · P0 — Multimodal embedding port:** typed text/image/audio/video/interleaved inputs, separate query/corpus tasks, normalized output, provenance, capability health, and deterministic fake.
- **CTX-012 · P1 — Cross-modal indexing:** generation-versioned pgvector rows for media segments, captions, transcripts, and prose; migrate away from fixed `vector(384)` without mixing spaces.
- **CTX-013 · P1 — Typed model context:** `TextPart | ImagePart | AudioPart | VideoPart`, provider capability/limit negotiation, original-versus-derived fallback, budgeting, and exact delivery manifest.
- **CTX-014 · P0 — Multimodal evaluations:** text↔media and media↔media retrieval, explicit-link precedence, 768/512 dimension comparison, hard negatives, resource use, rebuild, and leakage gates.
- **CTX-015 · P1 — Cache-compatible rendering:** deterministic stable-to-volatile context sections,
  scope-safe cache boundaries, and provider-reported cache usage.
- **CTX-016 · P1 — Alias/epithet memory:** branch/time/narrative/POV-scoped bindings and reviewed
  positive/negative resolution examples shared by editor, import, extraction, and checks.

## Multimodal assets

- **AST-001 · P0 — Asset identity and versions:** project-scoped media assets, immutable originals, authority, visibility, source/licence/attribution, branch/time applicability, provenance, and lifecycle state.
- **AST-002 · P0 — Secure object ingestion:** authorized upload/finalize, streamed hashing, signature/type/size validation, hosted scanning, opaque scoped keys, and safe preview access.
- **AST-003 · P1 — Variants and segments:** thumbnails/proxies, image regions, audio ranges, video windows/keyframes, transcript/OCR pointers, idempotent processors, and processing status.
- **AST-004 · P0 — Explicit media links:** reviewed `DEPICTS`/`VOICE_OF`/`SET_AT`/`MAP_OF`/`EVIDENCE_FOR` links that outrank similarity and obey project/branch/time/visibility.
- **AST-005 · P1 — Derived observations:** caption, transcript, OCR, speaker, and visual/audio proposals with evidence, confidence, model/processor provenance, review state, and mutation-set promotion.
- **AST-006 · P0 — Media security and privacy:** codec isolation, decompression/pixel/frame/duration quotas, metadata stripping/protection, untrusted-content delimiting, remote-provider preview, and content-free logs.
- **AST-007 · P1 — Media library and inspector:** upload/process/retry, preview, metadata, links, applicability, observation review, search, context pin/exclude, and representation controls.
- **AST-008 · P0 — Media lifecycle:** export/restore, retention, retire/delete, vector/cache/provider-upload cleanup, orphan reconciliation, and audit tombstones.

## Story and scene workflows

- **STY-001 · P1 — Story hierarchy:** story, act, arc, beat, and scene-plan CRUD.
- **STY-002 · P1 — Narrative threads:** open/progress/resolve with foreshadow/payoff links.
- **STY-003 · P1 — Scene intent:** POV, location, participants, goal, conflict, rule pressure, required and intended state.
- **STY-004 · P1 — Readiness rules:** explain unmet prerequisites and suggest author-controlled bridge plans.
- **STY-005 · P1 — Scene contracts:** typed hard/soft preconditions, intended postconditions,
  perceptions, disclosures, setups/payoffs, and deterministic readiness/postflight.
- **STY-006 · P1 — Character arc paths:** project-defined qualitative/ordinal state dimensions,
  beat targets, evidence expectations, and progress queries.
- **STY-007 · P1 — Reveal planner:** reader/character irony map, premature-reveal checks, and
  reading-distance Chekhov-debt view.
- **SCN-001 · P0 — Job model:** persistence, leases, stages, progress, cancellation, retry, and usage.
- **SCN-002 · P0 — AI provider port:** structured generation, prose, typed media context, multimodal embeddings, declared modality/limit capabilities, normalized errors/usage, fake adapter, and OpenRouter adapter.
- **SCN-002A · P1 — Local inference connector:** authenticated device registration, outbound job channel, secure updates/revocation, capability discovery, and OpenAI-compatible Ollama/llama.cpp adapter.
- **SCN-003 · P1 — Prompt registry:** versioned templates, input/output schemas, rendering tests.
- **SCN-004 · P1 — Scene studio:** plan, editor, mentions, context, variants, findings, and review.
- **SCN-005 · P1 — Scene versions:** immutable text, parent lineage, context and model references.
- **SCN-006 · P1 — Generation workflow:** preview-to-job-to-draft with budget/cancellation.
- **SCN-007 · P1 — Consistency rules:** deterministic findings for time, knowledge, location, resources, and prerequisites.
- **SCN-008 · P2 — Model critic:** evidence-citing soft checks for voice and narrative intent.

## Consistency and correction

- **CON-001 · P0 — Consistency run/finding model:** scopes, revisions, profiles, stages, evidence, status, and staleness.
- **CON-002 · P0 — Constraint packet:** deterministic generation preflight for location, time, resources, rules, prerequisites, and POV knowledge.
- **CON-003 · P0 — Temporal/spatial rules:** chronology, age/duration/travel, simultaneity, and location availability.
- **CON-004 · P0 — Entity/state rules:** identity details, possession, injury/state, lifecycle, relationship, and goal continuity.
- **CON-005 · P0 — World-rule engine:** declarative requirements, ranges, incompatibilities, implications, and exceptions from governed schema.
- **CON-006 · P0 — Knowledge/leakage rules:** POV knowledge/beliefs versus isolated author truth and secret access.
- **CON-007 · P1 — Incremental dependency index:** rule/source/prose fingerprints, invalidation, cache, and stale finding behavior.
- **CON-008 · P1 — Task-specific model critics:** typed POV, motivation/voice, physical/causal, world-rule, and narrative-thread passes.
- **CON-009 · P1 — Consistency profiles:** fast/balanced/thorough/custom cost, latency, and blocking policies.
- **CON-010 · P1 — Finding review UI:** evidence comparison, dismiss rationale, manual fix, agent patch, and canon proposal actions.
- **CON-011 · P1 — Deep audit jobs:** chapter/act/project recomputation, estimates, cancellation, and correction plans.
- **CON-012 · P0 — Consistency evaluations:** labeled defects, leakage, false positives, detection/latency/cost reporting.
- **CON-013 · P0 — Deterministic bundle prototype:** implement prose review units, dependency graph, rule-specific grouping, token splitting, coverage manifest, preview, and fallbacks; benchmark against full-context and vector-only critics.
- **CON-014 · P1 — Multimodal consistency:** deterministic checks against reviewed media facts plus bounded evidence-citing image/audio/video critic bundles, region/timestamp anchors, secret leakage, and fallback-quality reporting.
- **CON-015 · P1 — Authored invariant runner:** validate examples, evaluate on commits/runs, cite
  rule versions, and block only explicitly enabled deterministic rules.
- **CON-016 · P1 — Scoped suppressions:** versioned rationale/scope/expiry/dependencies, staleness,
  explicit calibration policy, and permanent exclusion of integrity failures.
- **CON-017 · P1 — Impact preview:** scratch canonical overlay plus dependency coverage for scenes,
  plans, invariants, findings, manifests, and projections.
- **CON-018 · P2 — Voice fingerprints:** language/sample-aware dialogue and narration statistics,
  advisory drift findings, and bounded critic escalation.

## Extraction and commit

- **EXT-001 · P0 — Extraction schema:** evidence-backed events/facts/beliefs/state/thread operations.
- **EXT-002 · P1 — Entity resolution:** deterministic IDs, candidate matches, and ambiguity review.
- **EXT-003 · P0 — Mutation validator:** schema, references, cardinality, time, branch, and permissions.
- **EXT-004 · P1 — Mutation review UI:** accept/edit/reject individual operations and rerun validation.
- **EXT-005 · P0 — Commit service:** optimistic head, idempotency, journal, Neo4j transaction, outbox.
- **EXT-006 · P1 — Recovery:** reconcile pending/failed commits and requeue derived updates.
- **EXT-007 · P1 — End-to-end loop:** commit scene, advance state, update threads, and inspect resulting history.
- **EXT-008 · P0 — Extraction/resolution evaluation:** labeled reference-manuscript cases for
  propositions, pronouns, aliases, epithets, narration, speech, disclosures, and review corrections.
- **EXT-009 · P1 — Text-first manuscript intake:** paste/Markdown source version, explicit marks,
  grouped/confidence-tiered extraction review, and commit without generation.

## Query projections, exports, and integrations

- **QRY-001 · P1 — Temporal read models:** outbox-built location, possession, relationship,
  timeline, reader-disclosure, and dependency views with watermarks and replay verification.
- **QRY-002 · P1 — Spoiler-safe bibles:** character/location/timeline/recap exports at branch,
  revision, narrative position, and viewpoint with artifact-specific sharing and leakage tests.
- **QRY-003 · P1 — Read-only MCP:** scoped canon/history/provenance tools over the application query
  boundary; write-like tools produce proposals only and cannot commit or change policy.

## Operations and product quality

- **OPS-001 · P0 — Structured diagnostics:** correlation IDs, redaction, workflow/job/provider metrics.
- **OPS-002 · P1 — Usage controls:** token/cost estimates, author-defined caps, confirmation thresholds, and per-job actuals.
- **OPS-003 · P1 — Backup/restore:** automated operation, verification, and runbook.
- **OPS-004 · P1 — Hosted identity operations:** OIDC/session security, invitation and role audit, recovery runbooks, and a clearly separated local identity adapter.
- **OPS-005 · P1 — Privacy controls:** retention and deletion for drafts/model artefacts/log content.
- **OPS-006 · P1 — Health/degradation:** readiness and clear graph/vector/provider/worker status.
- **OPS-007 · P1 — Media operations:** processing/index lag, safe failure telemetry, quarantine/retry/orphan runbooks, storage quotas, and processor capability diagnostics.
- **QLT-001 · P0 — Reference world:** synthetic cross-layer fixture with branches, secrets, and false beliefs.
- **QLT-002 · P1 — Release workflow E2E:** automate the complete release-one loop.
- **QLT-003 · P1 — Evaluation baseline:** approved AI/retrieval metrics and comparison report.
- **QLT-004 · P1 — Accessibility:** keyboard and automated accessibility gates for core workspaces.

## Suggested first implementation sequence

`FND-005A → FND-001 → FND-002 → FND-003 → FND-006 → FND-008 → IDN-001 → IDN-002 → IDN-003 → IDN-004 → IDN-005 → OSS-001 → OSS-003 → CAN-001 → CAN-002 → CAN-008 → QLT-001 → CON-001 → CON-002`

This sequence creates safe seams and tests before changing durable data semantics.
