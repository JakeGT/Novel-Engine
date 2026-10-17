# Dependency-ordered backlog

Each item is sized to be independently reviewable. IDs are stable references for issues and agent assignments. `P0` blocks later architecture; `P1` is release-one essential; `P2` is valuable but deferrable; `Deferred` is outside the current release sequence.

Status lines were audited against the code on 2026-10-17, after Phases 3–7. "Scripted model only" means every assistant path named has run against a scripted model or the local stand-in model, never a real provider.

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
  - **Status:** Implemented (P0-02/P0-05, commits `24f32cd`/`c052079`; integration fixtures in P0-09). 188 hermetic + 16 integration tests at the time.
- **FND-007 · P0 — Reproducible toolchain:** pin supported runtimes, lock dependencies, and configure CI. Acceptance: clean Linux runner passes static checks and unit tests.
  - **Status:** Implemented (P0-01/P0-11, commit `24f32cd`; CI extended through Phase 7 in `.github/workflows/foundation.yml`). The first hosted CI run of this branch is still an owner item (`phase-7-todo.md`).
- **FND-008 · P0 — Prototype repairs:** fix property-shape mismatch, route specificity, world-service errors, character bio flow, rewritten edge targets, temporal edge persistence, and update-label behavior. Acceptance: each defect has a regression test.
  - **Status:** Partially implemented (P0-08, commit `c0476a0`; FND-008.1–22, 28–32). FND-008.21 (atomic mutation) was met later by the Phase 2 single-transaction commit (ADR-011/012). FND-008.23–27 lapsed when the owner discarded `world_service.py` in Phase 1A.
- **FND-009 · P0 — Seed environment:** create deterministic PostgreSQL/Neo4j/vector/object fixtures for multiple isolated projects and the reference world, with reset/reseed commands safe only outside production.
  - **Status:** Implemented (P0-09). Two placeholder workspaces/projects support isolation fixtures without requiring production tenancy; verified by `tests/integration/test_seed_world.py` and `tests/unit/test_reset_guard.py`.

## Assistant and learning curve

Added 2026-10-10 (ADR-017, `19-assistant-first-experience.md`). These are release-one essential.

- **AST-EXP-001 · P1 — Assisted creation:** describe one thing or a whole idea, optional questions, one proposal of entries, details, and links; existing entries linked by name.
  - **Status:** Implemented 2026-10-10 (`canon_creation_workflow.py`, `/assist/*`, `AssistFlow.tsx`). Tested with a scripted model only.
- **AST-EXP-002 · P1 — To-fill-in list and fill-in interview:** bare entries are listed; the assistant fills in any entry.
  - **Status:** Implemented 2026-10-10 (`GET /entities/needs-detail`).
- **AST-EXP-003 · P1 — Real-provider verification and evaluation set:** run the built flows against real models; labelled set for drafted entries and links; record precision, recall, duplicate rate, kept-rate. Acceptance: a versioned baseline exists and regressions fail a gate.
  - **Status:** Closed 2026-10-10 by owner decision: verification is the owner's manual runs; the labelled set and gate were not built.
- **AST-EXP-004 · P1 — Stored, resumable conversations:** description, questions, answers, and proposal kept together as evidence. Acceptance: a closed session can be read from the change it produced; an open one can be resumed.
  - **Status:** Implemented 2026-10-10 (`/assist/sessions`), exercised with the scripted model only. Still not in project exports (checked 2026-10-17, `canon_archive.py`).
- **AST-EXP-005 · P1 — Start from a premise:** one conversation produces a starter world and open questions. Acceptance: document 19's premise measure on a new account.
  - **Status:** Partly (Phase 2.5 F): assisted creation (AST-EXP-001) accepts a whole premise and the empty project opens on "Describe your story". Not built: a dedicated premise conversation that returns open questions. The premise measure has not been taken (needs a new person and a real model).
- **AST-EXP-017 · P1 — The God Hand:** one conversational agent with read access to all canon and the ability to propose any change (edit, rename, end, archive, merge, re-link, restructure), reporting consequences and asking when ambiguous. Acceptance: labelled instruction set with expected operations; nothing commits without review; an accepted proposal is undone in one step. Depends on AST-EXP-003 and AST-EXP-006. Restores the "God Hand" of `architecture.md`.
  - **Status:** Partly (Phase 2.5 C, extended in Phase 3; `canon_change_workflow.py`, `GodHand.tsx`): entries, facts and beliefs in one reviewed proposal, questions when ambiguous, one-step undo; since Phase 3 it places events and facts in time, marks facts secret or common, and offers a what-if. Not built: merge as its own operation, changes to plan records, types or kinds of fact, retrieval for stories larger than its listing limits (200 entries, 300 facts), a persistent conversation, and the labelled instruction set. Scripted model only.
- **AST-EXP-006 · P1 — Grouped review:** proposals summarised by kind, safe bulk decisions, "revise this change" by asking, pre-review check against canon. Acceptance: a 40-change proposal is decided in under ten actions without blind approval.
  - **Status:** Implemented 2026-10-10 (Phase 2.5 B; `POST /mutations/{id}/operations/decisions`, `POST /assist/revisions`, `canon_consistency.py`). The pre-review check is structural only (duplicates, archived twins, facts already recorded); contradictions in meaning are not detected. Revise-by-asking: scripted model only.
- **AST-EXP-007 · P1 — Link suggestions:** semantic candidates for linking existing entries, confirmed by the author. Acceptance: no automatic merge; suggestion precision recorded.
  - **Status:** Partly (Phase 2.5 G; `POST /assist/links` in `canon_revise_workflow.py`): the chat model reads the listed entries and suggests facts for review; nothing is merged. Not built: embedding-based candidates (the Phase 4 vector use covers passages only), and precision is not recorded. Scripted model only.
- **AST-EXP-008 · P1 — First run:** guided provider setup, first session in a real project, optional sample project. Acceptance: first accepted entry within 3 minutes.
  - **Status:** Partly (Phase 2.5 H; `Home.tsx`, `sample_story.py`): onboarding, a prompt to connect a key, and a sample story are built. Time to first accepted entry is not measured (needs a new person).
- **AST-EXP-009 · P1 — Levels and plain language:** default visibility by level, need-based suggestions, one name mapping, a rendered-screen test for internal terms.
  - **Status:** Implemented (Phase 2.5 I, Phase 2.6, Phase 3 I; `frontend/src/lib/levels.ts`, `lib/words.ts`, `expectPlainLanguage`). Need-based suggestions exist for planning (offered at five entries) and the Level 5 triggers (what-if, secrets, times); there is no suggestion that leads to the scene studio.
- **AST-EXP-010 · P1 — By-asking paths for existing features:** undo, custom types and kinds of fact, invitations (with a members page); belief editor by hand.
  - **Status:** Implemented 2026-10-10 (Phase 2.5 J and P; `PeoplePage.tsx`, `BeliefsPanel.tsx`, restore preview from the God Hand). Each ends in a previewed action the author confirms. Scripted model only.
- **AST-EXP-011 · P1 — Session budgets and assistant threat cases:** per-session call/token budget shown before calls; injection, retention, and degraded-provider tests.
  - **Status:** Partly (Phase 2.5 K; `tests/contract/test_assist_threats.py`, `GET /assist/estimate`): threat cases are tests; a daily per-person token allowance and an estimate of what a request reads are shown before sending. Not built: a budget per conversation, an estimate of the reply, or any price in money.
- **AST-EXP-012 · P1 — Plan records:** story, arc, beat, scene plan, and thread as canon-governed intent. Acceptance: intent never appears as fact.
  - **Status:** Implemented 2026-10-11 (Phase 2.6, ADR-018; `backend/domain/canon/plans.py`, `plan_service.py`).
- **AST-EXP-013 · P1 — Planning interview and "what happens next":** top-down, stoppable at any depth, grounded in the bible, gaps left open. Acceptance: document 19's first-plan measure; groundedness and invention rate recorded.
  - **Status:** Partly 2026-10-11 (Phase 2.6 D/E; `plan_workflow.py`, `PlanAssist.tsx`): the interview and "What could happen next?" are built and bounded. Not built: the first-plan measure and any groundedness or invention-rate record. Scripted model only.
- **AST-EXP-014 · P1 — Outline editor and readiness in plain words:** the by-hand path for plans.
  - **Status:** Implemented 2026-10-11 (Phase 2.6 B/C; `PlanPage.tsx`). Reordering is by "Move up/down"; a removed plan item cannot be restored by undo.
- **AST-EXP-015 · P2 — "Keep the safe ones for me":** opt-in delegation of additive, reversible changes of kinds the author has approved before; everything listed and reversible.
  - **Status:** Not built.
- **AST-EXP-018 · P1 — Pictures on entries and scenes:** an author uploads an image and links it to an entry or a scene, with a role (portrait, reference); it appears on the entry page and as the entry's picture in lists. Images only for now; bounded size and verified type; no processing, captioning, or model use (Phase 4). Acceptance: upload → link → visible on the entry; removal retires the link and keeps history; cross-tenant access is refused; works with no AI provider.
  - **Status:** Partly 2026-10-10 (Phase 2.5 L; `media_service.add_picture`, `Pictures.tsx`): entries are done to the acceptance, including choosing the main picture and reusing one. Not built: pictures on scenes (the service links only to entries), thumbnails, and choosing an unlinked library picture.
- **AST-EXP-016 · P1 — Design direction:** the interface reads as a creative tool, not a dashboard (`20-design-direction.md`).
  - **Status:** Implemented 2026-10-10 and applied to every later screen with browser design reviews (Phases 3–6 todos). Not tested with people.

## Open-source distribution

- **OSS-001 · P0 — Reference deployment:** one documented Docker Compose path with health checks, persistent volumes, safe network defaults, and an upgrade contract.
  - **Status:** Partly (Phase 1 1G; `docker-compose.yml`, `deploy/hosted/compose.yml`, README "Deployment", `CHANGELOG.md` upgrade notes): health-gated stores on persistent volumes bound to 127.0.0.1, and an upgrade and rollback contract. The self-hosted profile runs only the stores in Compose and the server from a checkout (`make api`); the all-container stack is the hosted profile. Never rehearsed on a clean host (owner item).
- **OSS-002 · P0 — Local identity mode:** a no-signup single-user mode that is explicit, safe by default, and cannot be enabled in the hosted profile.
  - **Status:** Implemented (Phase 1 1C; `AUTH_MODE=local`, `backend/api/auth.py`).
- **OSS-003 · P0 — Provider configuration:** redacted environment/config handling for user-supplied OpenRouter keys and OpenAI-compatible local endpoints.
  - **Status:** Implemented (Phase 1 1F, Phase 2.5 O, Phase 7; `backend/domain/providers.py`, `credential_service.py`, README "Using a model on your own computer"). The local-model route has not been tried against a running Ollama or LM Studio server.
- **OSS-004 · P1 — Data ownership:** versioned project export/import plus tested backup, restore, and migration instructions.
  - **Status:** Partly (Phase 2 2K, Phase 7, 2026-10-17 complete archive; `canon_archive.py` archive v4, `deploy/hosted/backup.sh`/`restore.sh`, `tests/integration/test_release_migrations.py`, `tests/integration/test_archive_what_ifs.py`): a complete, versioned export/import is tested, including a clean-environment restore, what-ifs, conversations and readings, and a version 3 archive still restoring; hosted backup and restore scripts verify a checksum manifest. Not covered: the restore drill has not been run on a live stack (owner item).
- **OSS-005 · P1 — Release and community:** license, notices, contribution guide, security policy, issue templates, changelog, and reproducible release artefacts.
  - **Status:** Partly (Phase 7): `SECURITY.md`, `CONTRIBUTING.md` and `CHANGELOG.md` exist. Not built: a licence and notices (owner decision), issue templates, and a tagged, reproducible release artefact.
- **OSS-006 · P1 — Privacy contract:** opt-in telemetry only, documented outbound connections, content-free diagnostics by default, and a no-provider workflow.
  - **Status:** Implemented (Phase 1 1G, 2026-10-11 error-reporting fix; `backend/core/observability.py`, `backend/devtools/diagnostics.py`, README "Privacy"). The no-provider workflow is browser tested.
- **OSS-007 · P1 — Autonomy policy:** manual/assisted/delegated workflow decisions with mandatory-review categories and audit.
  - **Status:** Partly (ADR-014): manual and assisted work exist; protected origins, destructive, bulk and revert changes require explicit approval, and every decision is in the transition log. Not built: a delegated mode (AST-EXP-015).
- **OSS-008 · P1 — Usage limits:** per-job estimates, configurable token/cost caps, cancellation, normalized usage, and retry safeguards.
  - **Status:** Implemented (Phase 1 1F, Phase 2.5 K, Phase 5; `usage_service.py`, `job_runner.py`, studio estimate): per-job and period token/cost limits, a daily allowance, estimates before sending, job cancellation, usage recorded once per call, idempotency keys and capped attempts. Known limits in ADR-022: the per-job limit is checked per call, not on the job's running total; waiting-job caps can race.

## Invite-only identity and tenancy

- **IDN-001 · P0 — OIDC boundary:** provider-neutral authorization-code/PKCE integration, issuer/audience validation, external `(issuer, subject)` mapping, and fake development provider. The portfolio deployment uses Authentik (ADR-009); provisioning gates only the hosted wiring, not the port/fake work.
  - **Status:** Implemented (Phase 1 1B/1C; `backend/adapters/oidc.py`, `tests/unit/test_oidc_adapter.py`). Live Authentik round trip done by the owner 2026-10-10.
- **IDN-002 · P0 — Application sessions:** host-only secure cookies, CSRF protection, rotation, logout, expiry, revocation, and hosted-safe configuration guards.
  - **Status:** Implemented (Phase 1 1C; rotation grace added 2026-10-10; `auth_service.py`, `tests/application/test_session_rotation.py`).
- **IDN-003 · P0 — Workspace membership:** workspace plus owner/editor/viewer roles, least-privilege policy checks, and explicit project grants where needed.
  - **Status:** Implemented (Phase 1 1B/1D; `backend/domain/tenancy.py`, `tenancy_service.py`).
- **IDN-004 · P0 — Invitations:** create, bind, expire, revoke, accept once, and audit workspace invitations without leaking tenant existence.
  - **Status:** Implemented (Phase 1 1D; `tests/application/test_tenancy_workflow.py`; invitation links in `PeoplePage.tsx`, `InvitePage.tsx`).
- **IDN-005 · P0 — Isolation enforcement:** workspace/project scope in PostgreSQL, Neo4j, vector, object, job, export, cache, and event adapters with adversarial tests.
  - **Status:** Implemented (Phase 1 1E; jobs re-check access before running since Phase 5, `job_runner.py`). There are no caches or event streams to scope.
- **IDN-006 · P1 — Identity operations:** recovery/admin expectations, MFA requirements for privileged users, account unlinking/deletion behavior, and runbooks for the shared portfolio IdP (Authentik).
  - **Status:** Partly (Phase 1 1B/1H): identity unlinking, workspace deletion and the Authentik runbook (`phase-1-evidence.md`) exist. Not done: the recovery and MFA policy for privileged users (open in ADR-009; owner decision).

## Deferred commercial platform

These IDs are retained for traceability from ADR-001. Identity and isolation work has moved to IDN-001–IDN-006; billing and platform-funded inference remain deferred by ADR-008.

- **SaaS-001 · Superseded — Accounts and sessions:** replaced by shared OIDC and IDN-001/IDN-002; open signup is not planned.
- **SaaS-002 · Superseded — Tenant and membership:** replaced by IDN-003/IDN-004.
- **SaaS-003 · Superseded — Isolation enforcement:** replaced by IDN-005.
- **SaaS-004 · Deferred — Subscription boundary:** plans, entitlements, subscription state, and verified idempotent webhook processing.
  - **Status:** Not built (deferred, ADR-008).
- **SaaS-005 · Deferred — Credit ledger:** grants, purchases, reservations, releases, reconciliations, refunds, and balance projection.
  - **Status:** Not built (deferred, ADR-008).
- **SaaS-006 · P1 — Hosted credential vault:** encrypted user-owned OpenRouter credential storage, rotation, revocation, worker access, and redaction; no platform funding.
  - **Status:** Implemented (Phase 1 1F; `backend/adapters/credentials.py`, `credential_service.py`, `backend/core/crypto.py`).
- **SaaS-007 · Deferred — Hosted usage accounting:** provider cost and policy records needed by a managed service.
  - **Status:** Not built (deferred, ADR-008). Per-call usage records exist for BYOK (OSS-008).
- **SaaS-008 · Deferred — Platform-funded jobs:** funding selection, reservation, reconciliation, and failure recovery.
  - **Status:** Not built (deferred, ADR-008).
- **SaaS-009 · Deferred — Hosted autonomy policy:** workspace-level constraints layered over project/user choices.
  - **Status:** Not built (deferred, ADR-008).
- **SaaS-010 · Deferred — Hosted abuse controls:** rate limits, quotas, anomaly alerts, and support overrides.
  - **Status:** Not built (deferred, ADR-008). Invitation-acceptance rate limiting and per-person allowances exist for other reasons.

## Canon and schema

- **CAN-001 · P0 — Project aggregate:** introduce project ID and scope all repository methods.
  - **Status:** Implemented (Phase 1 1B/1E; `tests/unit/test_scope_contract.py`).
- **CAN-002 · P0 — Core schema:** define typed core nodes/predicates, property schemas, endpoint constraints, and cardinality.
  - **Status:** Implemented (Phase 2 2B; `backend/domain/canon/registry.py`).
- **CAN-003 · P1 — Custom schema governance:** project-scoped custom types with validation and schema versions.
  - **Status:** Implemented (Phase 2 2C; `schema_service.py`, `CustomizePage.tsx`). Enum domains are not schema-governed.
- **CAN-003A · P1 — Runtime typed schema compiler:** compile safe project extensions to cached JSON Schema/Pydantic validators for AI output and canon writes; never execute user code.
  - **Status:** Implemented (Phase 2 2C; `schema_compiler.py`).
- **CAN-004 · P0 — Fact model:** implement reified temporal assertions plus query projections.
  - **Status:** Implemented (Phase 2 2B/2G; story-time spans added in Phase 3; `backend/domain/canon/assertions.py`).
- **CAN-005 · P0 — Belief model:** implement holder-scoped assertions, confidence, source, and validity.
  - **Status:** Implemented (Phase 2 2G; truth agreement derived, never stored).
- **CAN-006 · P0 — Event model:** implement chronological events with participants, location, cause, and scene source.
  - **Status:** Implemented (Phase 2 2G, story time in Phase 3; `backend/domain/canon/events.py`). Scene source is by evidence pointer.
- **CAN-007 · P0 — Provenance:** require actor/origin/source/revision on canon writes.
  - **Status:** Implemented (Phase 2 2D/2H).
- **CAN-008 · P0 — Mutation journal:** append, validate, commit, recover, and verify checksums.
  - **Status:** Implemented (Phase 2 2D/2E, ADR-012; `PostgresCanonRepository.apply_commit`, `make verify`).
- **CAN-009 · P1 — Entity authoring API/UI:** typed list, search, create, edit, link, and retire workflows.
  - **Status:** Implemented (Phase 2 2F/2J, redesigned 2026-10-10; `canon_entity_service.py`, `EntryPage.tsx`, `BiblePage.tsx`).
- **CAN-010 · P1 — Export/import:** versioned complete project archive and clean restore validation.
  - **Status:** Implemented (Phase 2 2K, extended through Phase 5; 2026-10-17 `canon_archive.py` format v4): strict preflight and verified, idempotent restore of the main story, plan, texts, rules, story-time unit, scene links, voice note, scene readings, dismissed findings, conversations with the assistant, and every what-if (restarted from its starting version, its own changes replayed and checked against the archive's checksums). Version 3 archives still restore. Left out on purpose and listed in the archive: drafts waiting to be kept or discarded, unsaved proposals, jobs and usage. Known limits: a restored what-if's start time is the restore time; the restore does not rebuild the writing search index for restored texts; removing a failed import does not remove restored texts or rules (as before).
- **CAN-011 · P0 — Canon query/explanation boundary:** point-in-time state, revision differences,
  belief holders, and provenance through commit/decision/source evidence, shared by every transport.
  - **Status:** Implemented (Phase 2 2H; `canon_history_service.py`, `/revisions`, `/provenance`). Story-time state at a moment added in Phase 3 (`/timeline/moment`).
- **CAN-012 · P1 — Tags and compensation:** immutable named revision pointers plus conflict-aware
  compensating revert proposals through normal review.
  - **Status:** Implemented (Phase 2 2H, Phase 2.7 G): history page names versions, shows changes since a version, and goes back as a new version through review. No screen compares two arbitrary versions (the API can).
- **CAN-013 · P0 — Story invariant contract:** safe versioned predicate AST, dependencies, examples,
  warning default, explicit blocking publication, and separation from permanent integrity rules.
  - **Status:** Partly 2026-10-11: a fixed vocabulary with versions and examples, warning only (ADR-019; `backend/domain/rules.py`). Blocking publication is not built; rules cannot speak of time or belief.

## Time, branches, and visibility

- **TMP-001 · P0 — Three-clock types:** world, narrative, and revision positions with explicit conversion limits.
  - **Status:** Implemented 2026-10-17 (Phase 3, ADR-020; `backend/domain/worldtime.py`): world time on the story's own number line, never converted to a calendar; narrative order is outline order; revisions unchanged.
- **TMP-002 · P0 — Temporal query:** active state at branch/tick using half-open intervals.
  - **Status:** Implemented 2026-10-17 (Phase 3; `story_time_service.py`): facts true for a span, state at a moment (`/timeline/moment`), per what-if.
- **TMP-003 · P0 — Branch overlay:** fork, inherited reads, local mutation, and head revision.
  - **Status:** Implemented 2026-10-17 as copy-on-start what-ifs (Phase 3, ADR-020; `what_if_service.py`, `PostgresCanonRepository.fork_branch`). Only from the main story; no what-if of a what-if.
- **TMP-004 · P1 — Branch comparison:** mutations from common ancestor and conflict categorization.
  - **Status:** Implemented 2026-10-17 (Phase 3 B): compare since the fork, conflicts per record (not per field).
- **TMP-005 · P1 — Merge review:** author-resolved merge mutation set.
  - **Status:** Implemented 2026-10-17 (Phase 3 B): bring back as one proposal; conflicts chosen per record, never merged automatically.
- **KNW-001 · P0 — Visibility policy:** public, private, secret, author-only, and character-accessible classification.
  - **Status:** Implemented 2026-10-17 (Phase 3, ADR-020; `backend/domain/knowledge.py`) as three classes, ordinary, common and secret, with deny-by-default knowledge. There are no separate private or author-only classes for facts.
- **KNW-002 · P0 — Knowledge acquisition:** beliefs linked to perception/report/inference events.
  - **Status:** Partly 2026-10-17 (Phase 3 C/D): beliefs from telling cite the telling event. Not built: perception and inference.
- **KNW-003 · P1 — POV context gate:** deterministic filtering and leakage explanations.
  - **Status:** Implemented 2026-10-17 (Phase 3 C, Phase 4, Phase 5; `knowledge_service.py`, `context_service.py`): a character's knowledge view; scene context puts facts the viewpoint character does not know in an author-only section left out by default, each labelled as not known; draft prompts keep secrets in that section (adversarially tested). Tested with a scripted model only.
- **KNW-004 · P1 — Reader disclosure ledger:** explicit/implied disclosures by narrative position,
  audience, source, and evidence with irony/reveal queries.
  - **Status:** Implemented by hand 2026-10-17 (Phase 3 E; `PUT /plan/{id}/reveals`, `/reader`): scene reveals plainly or as a hint, irony, withheld knowledge, repeats. Reveals are not read from prose.
- **KNW-005 · P1 — Narrated assertions and speech acts:** speaker/listener/overhearer, sincerity,
  modality, visibility, and evidence without treating narration/dialogue as objective truth.
  - **Status:** Partly 2026-10-17 (Phase 3 D; `TellDialog.tsx`): telling as an event with speaker, listeners and overhearers; sincerity derived from facts. Not built: narrated assertions, modality, and an assistant path for telling.
- **KNW-006 · P1 — Belief propagation proposals:** deterministic perception/report/trust policies
  that emit reviewed belief mutation sets and never write canon directly.
  - **Status:** Partly 2026-10-17 (Phase 3 D): telling gives each hearer a belief through review (overhearers at 0.6). Not built: trust and perception policies.

## Retrieval and memory

- **CTX-001 · P0 — Context manifest:** durable typed package and source trace.
  - **Status:** Implemented 2026-10-17 (Phase 4, ADR-021; `backend/domain/context.py`, `context_manifests`): every item has a reason, source, authority, words and score; drafts keep the manifest they used (Phase 5).
- **CTX-002 · P0 — Policy engine:** versioned purpose-specific structural retrieval policies.
  - **Status:** Partly 2026-10-17 (Phase 4): one versioned policy, `scene-draft/1`, assembled deterministically by branch, time and viewpoint. Other assistant workflows (God Hand, link suggestions, reading) still list the story bible up to fixed limits rather than use a policy.
- **CTX-003 · P1 — Mention resolver:** parse, validate, autocomplete, and repair stable entity mentions.
  - **Status:** Partly 2026-10-11 (Phase 2.7 B/C; `backend/domain/sources.py`, `NameQuestions.tsx`): marks and known names are found without a model, and unresolved names are repaired by "Who is this?". Not built: autocomplete while typing in the texts page or the studio.
- **CTX-004 · P0 — Vector port:** versioned documents, filters, health, deletion, and rebuild contract; implement pgvector first unless ADR changes it.
  - **Status:** Partly (Phase 0/1, Phase 4; `backend/adapters/vector.py`): pgvector with project scope, model/version/dimension provenance on each row, health and deletion; passages are rebuilt with `python -m backend.devtools.reindex`. Not built: metadata filters beyond scope, and versioned generations (one fixed `vector(384)`).
- **CTX-005 · P0 — Index outbox:** idempotent derived updates and lag reporting.
  - **Status:** Partly (Phase 2 2E, Phase 4): the graph projection consumes an idempotent outbox and reports lag (`outbox_consumer.py`, `projection_service.py`). The passage index is updated inline when a text changes, best effort, with texts not yet indexed listed (`passage_index.py`); it does not go through the outbox.
- **CTX-006 · P1 — Scene chunker:** summary, event-block, and full-scene layers.
  - **Status:** Partly 2026-10-17 (Phase 4; `backend/domain/passages.py`): texts, including scene prose, are split into passages at headings and paragraphs. Not built: summary and event-block layers.
- **CTX-006A · P1 — Previous-prose units:** preceding passages, nearby scene text, event blocks, summaries, voice exemplars, and thread-linked passages.
  - **Status:** Partly 2026-10-17 (Phase 4; `context_service.py`): passages naming the scene's people and places, a voice sample from the most recent writing, and the preceding scene's plan. Not built: the preceding scene's prose as such, event blocks, summaries, and thread-linked passages.
- **CTX-007 · P1 — Hybrid ranker:** explicit, graph, semantic, two-clock temporal decay, author importance, continuity impact, narrative/thread, and diversity signals.
  - **Status:** Partly 2026-10-17 (Phase 4): fixed scores for scene roles, pins, neighbours, plan and thread links, story-time closeness of events, and semantic similarity when embeddings are configured. Not built: author importance, continuity impact, diversity, and narrative-distance decay.
- **CTX-008 · P1 — Token budgeter:** hard/soft categories and deterministic truncation.
  - **Status:** Implemented 2026-10-17 (Phase 4; `assemble` in `backend/domain/context.py`): hard items never cut, the rest cut lowest score first, every cut listed with its reason. The budget is counted in words, not provider tokens.
- **CTX-009 · P1 — Context preview API/UI:** inspect, pin, exclude, and compare packages.
  - **Status:** Done 2026-10-17 (Phase 4; `GET/PUT /plan/{id}/context`, `SceneContext.tsx`): inspect, pin, exclude, budget and secrets, kept with the scene through review; compare the context now with the one a kept draft was written from (`GET /plan/{id}/context/kept[/{manifest_id}]`, `compare_contexts` in `backend/domain/context.py`): added, no longer given (and why), changed (words, section, reason), read only within the scene's own line of the story. Kept contexts now record each item's words and a fingerprint, except passages of the author's writing (fingerprint only); older ones compare by length. Not built: comparing two kept drafts with each other.
- **CTX-010 · P0 — Retrieval evaluations:** relevance, omission, leakage, and branch correctness fixtures.
  - **Status:** Partly (Phase 4 G): contract tests with leakage and what-if cases. Not built: a relevance or omission set; retrieval quality is unmeasured.
- **CTX-011 · P0 — Multimodal embedding port:** typed text/image/audio/video/interleaved inputs, separate query/corpus tasks, normalized output, provenance, capability health, and deterministic fake.
  - **Status:** Not built (Phase 4 I). `EmbeddingPort` takes text only.
- **CTX-012 · P1 — Cross-modal indexing:** generation-versioned pgvector rows for media segments, captions, transcripts, and prose; migrate away from fixed `vector(384)` without mixing spaces.
  - **Status:** Not built (Phase 4 I). The vector dimension is still 384.
- **CTX-013 · P1 — Typed model context:** `TextPart | ImagePart | AudioPart | VideoPart`, provider capability/limit negotiation, original-versus-derived fallback, budgeting, and exact delivery manifest.
  - **Status:** Partly (Phase 4 H): a picture linked to a scene's entries is named in context, with a note that the image is not sent; provider capabilities declare text only. Not built: media parts, negotiation and fallback.
- **CTX-014 · P0 — Multimodal evaluations:** text↔media and media↔media retrieval, explicit-link precedence, 768/512 dimension comparison, hard negatives, resource use, rebuild, and leakage gates.
  - **Status:** Not built (Phase 4 I).
- **CTX-015 · P1 — Cache-compatible rendering:** deterministic stable-to-volatile context sections,
  scope-safe cache boundaries, and provider-reported cache usage.
  - **Status:** Implemented 2026-10-17 (Phase 4 F; `render` in `backend/domain/context.py`; `usage_from` in `backend/adapters/ai.py`, migration `0036_cached_tokens`): stable-to-volatile sections with a checksum, and prompt tokens the provider read from its cache (OpenAI/OpenRouter `prompt_tokens_details.cached_tokens`, DeepSeek and Anthropic shapes) recorded on each usage record, each job and the job's API answer. Not shown in the studio yet; cache writes are not recorded; never measured against a real provider.
- **CTX-016 · P1 — Alias/epithet memory:** branch/time/narrative/POV-scoped bindings and reviewed
  positive/negative resolution examples shared by editor, import, extraction, and checks.
  - **Status:** Partly 2026-10-11 (Phase 2.7 C; `alias_bindings`, `/names`): scoped to the story or one text. Phase 3 did not add scoping by branch, time, narrative position or point of view, and it is still not built.

## Multimodal assets

- **AST-001 · P0 — Asset identity and versions:** project-scoped media assets, immutable originals, authority, visibility, source/licence/attribution, branch/time applicability, provenance, and lifecycle state.
  - **Status:** Partly (Phase 2 2I; `backend/domain/media.py`): project-scoped assets, immutable versions with checksum, source, licence, attribution, authority, visibility and lifecycle; links carry their branch (migration 0032). Not built: time applicability.
- **AST-002 · P0 — Secure object ingestion:** authorized upload/finalize, streamed hashing, signature/type/size validation, hosted scanning, opaque scoped keys, and safe preview access.
  - **Status:** Partly (Phase 2.5 L; `media_service.add_picture`): authorized picture upload, type decided from the bytes (PNG, JPEG, WebP), 8 MB limit, checksum, opaque scoped keys, sandboxed member-only serving. Not built: an upload/finalize flow, streamed hashing (the body is read whole), and hosted scanning.
- **AST-003 · P1 — Variants and segments:** thumbnails/proxies, image regions, audio ranges, video windows/keyframes, transcript/OCR pointers, idempotent processors, and processing status.
  - **Status:** Not built (Phase 4 I). No thumbnails either.
- **AST-004 · P0 — Explicit media links:** reviewed `DEPICTS`/`VOICE_OF`/`SET_AT`/`MAP_OF`/`EVIDENCE_FOR` links that outrank similarity and obey project/branch/time/visibility.
  - **Status:** Partly (Phase 2 2I; `LinkRole` in `backend/domain/media.py`): the five roles through the reviewed mutation path, scoped by project and branch. Not built: time and visibility rules on links; there is no media similarity search for them to outrank yet.
- **AST-005 · P1 — Derived observations:** caption, transcript, OCR, speaker, and visual/audio proposals with evidence, confidence, model/processor provenance, review state, and mutation-set promotion.
  - **Status:** Not built (Phase 4 I).
- **AST-006 · P0 — Media security and privacy:** codec isolation, decompression/pixel/frame/duration quotas, metadata stripping/protection, untrusted-content delimiting, remote-provider preview, and content-free logs.
  - **Status:** Partly 2026-10-17 (`backend/domain/image_metadata.py`): nothing decodes media (so no codec runs), uploads are size-limited and type-checked, logs are content-free, and uploaded pictures lose EXIF (GPS, camera, dates), XMP, IPTC, comments, text chunks and anything after the image, read from the file's structure only; the colour profile stays and the orientation is written back alone; a file whose structure cannot be followed is refused. Not built: pixel and decompression quotas, and a remote-provider preview.
- **AST-007 · P1 — Media library and inspector:** upload/process/retry, preview, metadata, links, applicability, observation review, search, context pin/exclude, and representation controls.
  - **Status:** Partly (Phase 2 2J; `MediaPage.tsx`): a read-only list of assets with versions, links and lifecycle; pictures are uploaded from entries. Not built: processing and retry, search, observation review, context controls for media, and representation controls.
- **AST-008 · P0 — Media lifecycle:** export/restore, retire/delete, vector/cache/provider-upload cleanup, orphan reconciliation, and audit tombstones.
  - **Status:** Partly (Phase 2 2I/2K): assets and bytes are exported and restored, retirement keeps an immutable lifecycle audit, and project deletion purges objects. Not built: retention rules and orphan reconciliation (an image stored before a refused link stays unlinked); there are no media vectors or provider uploads to clean up.

## Story and scene workflows

- **STY-001 · P1 — Story hierarchy:** story, act, arc, beat, and scene-plan CRUD.
  - **Status:** Implemented 2026-10-11 (Phase 2.6, ADR-018; `plan_service.py`, `PlanPage.tsx`) for story, arc, beat and scene. There is no separate act level.
- **STY-002 · P1 — Narrative threads:** open/progress/resolve with foreshadow/payoff links.
  - **Status:** Done 2026-10-17 (Phase 2.6 F; `backend/domain/threads.py`): threads opened (`sets_up`), moved along (kept in details under `_advances`, edited on the plan form) and resolved (`pays_off`); the outline returns each thread's steps in outline order and the Threads section shows them; "things the plan still needs" says when a thread opens and resolves with scenes between and none moving it along; the drafting context says when a scene moves a thread along. Not built: the assistant proposing progress steps, and a check for a thread silent over a long stretch.
- **STY-003 · P1 — Scene intent:** POV, location, participants, goal, conflict, rule pressure, required and intended state.
  - **Status:** Mostly 2026-10-17 (Phase 2.6, Phase 2.7 E; `backend/domain/scene_intent.py`): point of view, place and participants as roles; required and intended state as scene conditions (`scene_conditions.py`); the viewpoint character's goal, what stands in the way, and the outcome (yes / no / yes, but / no, and) as optional fields kept in details under `_intent`, edited on the plan form, shown on the plan card and given to the drafting context as an always-given item. Not built: rule pressure, and the assistant filling in intent (plan drafting and the plan card from prose do not set it).
- **STY-004 · P1 — Readiness rules:** explain unmet prerequisites and suggest author-controlled bridge plans.
  - **Status:** Partly (Phase 2.6 C, Phase 2.7 E): gaps and unmet scene conditions are said in plain words and never block. Not built: suggested bridge plans for an unmet prerequisite.
- **STY-005 · P1 — Scene contracts:** typed hard/soft preconditions, intended postconditions,
  perceptions, disclosures, setups/payoffs, and deterministic readiness/postflight.
  - **Status:** Partly (Phase 2.6, 2.7 E, Phase 3 E): before/after conditions with readiness and postflight, planned reveals, and thread set-ups and pay-offs. Not built: hard versus soft conditions, perceptions, and checking "after" conditions against what a scene's prose says (Phase 5 "What is not").
- **STY-006 · P1 — Character arc paths:** project-defined qualitative/ordinal state dimensions,
  beat targets, evidence expectations, and progress queries.
  - **Status:** Not built.
- **STY-007 · P1 — Reveal planner:** reader/character irony map, premature-reveal checks, and
  reading-distance Chekhov-debt view.
  - **Status:** Partly 2026-10-17 (Phase 3 E; `backend/domain/knowledge.py`): irony with the viewpoint character, withheld knowledge and repeated reveals, by hand. Not built: premature-reveal checks and the Chekhov-debt view.
- **SCN-001 · P0 — Job model:** persistence, leases, stages, progress, cancellation, retry, and usage.
  - **Status:** Implemented 2026-10-17 (Phase 5, ADR-022; `backend/domain/jobs.py`, `job_store.py`, `job_runner.py`). Integration tested on PostgreSQL.
- **SCN-002 · P0 — AI provider port:** structured generation, prose, typed media context, multimodal embeddings, declared modality/limit capabilities, normalized errors/usage, fake adapter, and OpenRouter adapter.
  - **Status:** Partly (Phase 0, Phase 2.5 O, Phase 5; `AIGateway` in `backend/ports.py`, `backend/adapters/ai.py`): structured output, prose (`generate_text`), declared capabilities, normalized errors and usage, a fake and OpenAI-compatible adapters for OpenRouter and six other providers. Not built: media context and multimodal embeddings. No streaming.
- **SCN-002A · P1 — Local inference connector:** authenticated device registration, outbound job channel, secure updates/revocation, capability discovery, and OpenAI-compatible Ollama/llama.cpp adapter.
  - **Status:** Not built. A self-hosted install can instead point `AI_BASE_URL` at Ollama or LM Studio (Phase 7); there is no connector from a hosted site to a person's own machine.
- **SCN-003 · P1 — Prompt registry:** versioned templates, input/output schemas, rendering tests.
  - **Status:** Partly: each workflow carries a prompt version id (for example `plan-draft/1`) and a typed output schema, and untrusted text is fenced (`backend/domain/prompting.py`). Not built: a central registry of templates or prompt rendering tests.
- **SCN-004 · P1 — Scene studio:** plan, editor, mentions, context, variants, findings, and review.
  - **Status:** Implemented 2026-10-17 (Phase 5, ADR-022; `backend/services/studio_service.py`, `StudioPage.tsx`). Drafts are kept or discarded whole. Tested with a scripted model and the local stand-in only.
- **SCN-005 · P1 — Scene versions:** immutable text, parent lineage, context and model references.
  - **Status:** Implemented 2026-10-17 (Phase 5; migration 0034). The screen shows who wrote each version, not yet the context behind it.
- **SCN-006 · P1 — Generation workflow:** preview-to-job-to-draft with budget/cancellation.
  - **Status:** Implemented 2026-10-17 (Phase 5; `scene_draft` job). No streaming. Tested with a scripted model and the local stand-in only.
- **SCN-007 · P1 — Consistency rules:** deterministic findings for time, knowledge, location, resources, and prerequisites.
  - **Status:** Partly 2026-10-17 (Phase 5; `check_draft` in `backend/domain/drafting.py`): new names, a secret on the page, a planned reveal missing, place or viewpoint never named, stock phrasing, length; scene conditions show readiness in the plan. Not built: time and resource checks against the prose.
- **SCN-008 · P2 — Model critic:** evidence-citing soft checks for voice and narrative intent.
  - **Status:** Implemented 2026-10-17 (Phase 5; `scene_review` job): notes in five categories, kept only if they quote the text. Tested with a scripted model only.

## Consistency and correction

- **CON-001 · P0 — Consistency run/finding model:** scopes, revisions, profiles, stages, evidence, status, and staleness.
  - **Status:** Partly 2026-10-17 (Phase 5): for one scene, model-free checks are worked out on every version and draft; the assistant's notes are stored with the version or draft they are about (`draft_findings`) and shown as about an older version when it changes. Not built: runs as records, other scopes, profiles, finding status, and staleness tracking.
- **CON-002 · P0 — Constraint packet:** deterministic generation preflight for location, time, resources, rules, prerequisites, and POV knowledge.
  - **Status:** Implemented 2026-10-17 (Phase 4, ADR-021; `ContextService.for_scene`): place, facts true at the scene's time, rules that apply, scene conditions and the viewpoint's knowledge, built deterministically before every draft. Advisory: it never blocks a draft.
- **CON-003 · P0 — Temporal/spatial rules:** chronology, age/duration/travel, simultaneity, and location availability.
  - **Status:** Partly 2026-10-17 (Phase 3 F): relative-time loops, lost anchors, spans that end before they start, and two values of a one-at-a-time kind at one moment are refused at commit. Not built: age, duration, travel, simultaneity and location availability.
- **CON-004 · P0 — Entity/state rules:** identity details, possession, injury/state, lifecycle, relationship, and goal continuity.
  - **Status:** Partly (Phase 2, Phase 3): built-in integrity refuses references to archived records and conflicting one-at-a-time values. Not built: possession, injury/state, relationship and goal continuity across scenes, or a participant after their recorded death.
- **CON-005 · P0 — World-rule engine:** declarative requirements, ranges, incompatibilities, implications, and exceptions from governed schema.
  - **Status:** Partly 2026-10-11 (Phase 2.7 F, ADR-019; `backend/domain/rules.py`): rules select entries and require has, lacks, at most or at least a kind of fact, with examples; they warn only. Not built: implications, exceptions, value ranges, and rules about time or belief.
- **CON-006 · P0 — Knowledge/leakage rules:** POV knowledge/beliefs versus isolated author truth and secret access.
  - **Status:** Partly 2026-10-17 (Phase 4, Phase 5): secrets are kept out of the viewpoint's context, and a draft paragraph naming everyone in a secret the viewpoint does not know is flagged. Not built: knowledge used before it was acquired, and checks beyond name co-occurrence.
- **CON-007 · P1 — Incremental dependency index:** rule/source/prose fingerprints, invalidation, cache, and stale finding behavior.
  - **Status:** Not built (Phase 5 "What is not"). Checks are worked out each time.
- **CON-008 · P1 — Task-specific model critics:** typed POV, motivation/voice, physical/causal, world-rule, and narrative-thread passes.
  - **Status:** Partly 2026-10-17 (Phase 5): one assistant check per scene with five categories (continuity, viewpoint, plan, voice, clarity). Not built: separate typed passes. Scripted model only.
- **CON-009 · P1 — Consistency profiles:** fast/balanced/thorough/custom cost, latency, and blocking policies.
  - **Status:** Not built (Phase 5 "What is not").
- **CON-010 · P1 — Finding review UI:** evidence comparison, dismiss rationale, manual fix, agent patch, and canon proposal actions.
  - **Status:** Partly 2026-10-17 (Phase 5; `StudioWork.tsx`, `StudioFindings.tsx`): findings and notes in the margin with the quoted words and "Show in the text"; the author fixes by editing, or dismisses a check or a note with a reason ("Intended", "Not a problem", or their own words), sees it under "Dismissed" with its reason, and can Undo (CON-016). Not built: an agent patch, and a canon proposal from a finding.
- **CON-011 · P1 — Deep audit jobs:** chapter/act/project recomputation, estimates, cancellation, and correction plans.
  - **Status:** Not built. The job queue it would use exists (SCN-001).
- **CON-012 · P0 — Consistency evaluations:** labeled defects, leakage, false positives, detection/latency/cost reporting.
  - **Status:** Not built. Leakage cases exist as contract tests only.
- **CON-013 · P0 — Deterministic bundle prototype:** implement prose review units, dependency graph, rule-specific grouping, token splitting, coverage manifest, preview, and fallbacks; benchmark against full-context and vector-only critics.
  - **Status:** Not built.
- **CON-014 · P1 — Multimodal consistency:** deterministic checks against reviewed media facts plus bounded evidence-citing image/audio/video critic bundles, region/timestamp anchors, secret leakage, and fallback-quality reporting.
  - **Status:** Not built.
- **CON-015 · P1 — Authored invariant runner:** validate examples, evaluate on commits/runs, cite
  rule versions, and block only explicitly enabled deterministic rules.
  - **Status:** Done 2026-10-17 (`rule_service.py`, `RulesPage.tsx`, migration `0037_finding_decisions`): examples are validated on save; rules are evaluated on preview and commit and cite the rule and its version. The author sets a rule to warn (the default) or block on the rules page; a blocking rule refuses approval and, inside the commit transaction, the commit, reading the line of the story through the commit's own reader. Only a complete evaluation blocks: a story larger than a rule may read, or a reader that cannot give the whole story, only warns. A rule already broken does not hold back unrelated changes. The rule vocabulary is still entries and facts now (CON-005).
- **CON-016 · P1 — Scoped suppressions:** versioned rationale/scope/expiry/dependencies, staleness,
  explicit calibration policy, and permanent exclusion of integrity failures.
  - **Status:** Partly 2026-10-17 (`backend/domain/dismissals.py`, `finding_dismissals`): studio checks and assistant notes can be dismissed per scene and line of the story (forked with a what-if), with a reason and an optional expiry (a week or a month). A dismissal holds a fingerprint of the finding's kind, subject and the sentence it points at, never the words; it lapses when that sentence changes, and comes back if the words are restored. Undo withdraws it and keeps the record. Only advisory studio findings can be dismissed; integrity checks and blocking rules cannot. Not built: dismissing findings in review, dependencies beyond the anchored sentence, and a calibration policy.
- **CON-017 · P1 — Impact preview:** scratch canonical overlay plus dependency coverage for scenes,
  plans, invariants, findings, manifests, and projections.
  - **Status:** Not built (Phase 5 "What is not"). The review page does warn about story rules a proposed change would newly break.
- **CON-018 · P2 — Voice fingerprints:** language/sample-aware dialogue and narration statistics,
  advisory drift findings, and bounded critic escalation.
  - **Status:** Partly 2026-10-17 (Phase 5): a fixed list of stock phrases is flagged. Not built: voice statistics and drift findings.

## Extraction and commit

- **EXT-001 · P0 — Extraction schema:** evidence-backed events/facts/beliefs/state/thread operations.
  - **Status:** Partly 2026-10-11 (Phase 2.7 D; `PassageDraft` in `intake_workflow.py`): entries, facts and beliefs, each quoting the passage. Not built: events, state and thread operations.
- **EXT-002 · P1 — Entity resolution:** deterministic IDs, candidate matches, and ambiguity review.
  - **Status:** Implemented 2026-10-11 (Phase 2.7 B/C/D; `backend/domain/sources.py`, `NameQuestions.tsx`): names found without a model, and a model's guesses become questions, never decisions.
- **EXT-003 · P0 — Mutation validator:** schema, references, cardinality, time, branch, and permissions.
  - **Status:** Implemented (Phase 2 2E; time and branch checks added in Phase 3).
- **EXT-004 · P1 — Mutation review UI:** accept/edit/reject individual operations and rerun validation.
  - **Status:** Implemented (Phase 2 2J, Phase 2.5 B; `ReviewDetail.tsx`). Plan changes have no form editor on the review page.
- **EXT-005 · P0 — Commit service:** optimistic head, idempotency, journal, Neo4j transaction, outbox.
  - **Status:** Implemented (Phase 2 2D/2E, ADR-011/012): one PostgreSQL transaction; Neo4j is a projection fed from the outbox.
- **EXT-006 · P1 — Recovery:** reconcile pending/failed commits and requeue derived updates.
  - **Status:** Implemented (Phase 2 2E; `backend/devtools/reconcile.py`, `outbox_consumer.py`, `make project`).
- **EXT-007 · P1 — End-to-end loop:** commit scene, advance state, update threads, and inspect resulting history.
  - **Status:** Partly 2026-10-17 (Phase 6, ADR-023; `scene_reading.py`): keep a scene version, read it into reviewed proposals with evidence back to its words, and see the result in history. Not built: updating threads from a scene, and facts read from a scene taking its story time. Tested with a scripted model and the local stand-in only.
- **EXT-008 · P0 — Extraction/resolution evaluation:** labeled reference-manuscript cases for
  propositions, pronouns, aliases, epithets, narration, speech, disclosures, and review corrections.
  - **Status:** Partly: names have a labelled passage and pinned baseline (2026-10-11, `tests/fixtures/intake_eval`). Extraction: a scorer only; no model reading measured. Unchanged through Phase 7.
- **EXT-009 · P1 — Text-first manuscript intake:** paste/Markdown source version, explicit marks,
  grouped/confidence-tiered extraction review, and commit without generation.
  - **Status:** Implemented 2026-10-11 (Phase 2.7). Reading with the assistant has only run against a scripted model.

## Query projections, exports, and integrations

- **QRY-001 · P1 — Temporal read models:** outbox-built location, possession, relationship,
  timeline, reader-disclosure, and dependency views with watermarks and replay verification.
  - **Status:** Partly 2026-10-17 (Phase 3 G, ADR-020): state at a moment, the timeline and the reader ledger are computed on read from PostgreSQL. Not built: outbox-built views with watermarks and replay verification, and a dependency view.
- **QRY-002 · P1 — Spoiler-safe bibles:** character/location/timeline/recap exports at branch,
  revision, narrative position, and viewpoint with artifact-specific sharing and leakage tests.
  - **Status:** Partly 2026-10-17 (Phase 3 G; `/reader/guide`): a spoiler-safe reader's guide up to a scene, or as a character knows it, in Markdown, in the working what-if, with leakage cases in contract tests. Not built: other formats, guides at a revision, and sharing.
- **QRY-003 · P1 — Read-only MCP:** scoped canon/history/provenance tools over the application query
  boundary; write-like tools produce proposals only and cannot commit or change policy.
  - **Status:** Not built (Phase 2.7 H). Needs authentication for machine clients first.

## Operations and product quality

- **OPS-001 · P0 — Structured diagnostics:** correlation IDs, redaction, workflow/job/provider metrics.
  - **Status:** Implemented (Phase 0 0I, extended through Phase 5; `backend/core/logging.py`, `metrics.py`, `/health/metrics`).
- **OPS-002 · P1 — Usage controls:** token/cost estimates, author-defined caps, confirmation thresholds, and per-job actuals.
  - **Status:** Partly (Phase 1 1F, Phase 2.5 K, Phase 5): token estimates before a request or draft, an owner-set daily allowance and operator limits, per-job usage on each job. Not built: estimates in money and confirmation thresholds (the author confirms every draft, at any size).
- **OPS-003 · P1 — Backup/restore:** automated operation, verification, and runbook.
  - **Status:** Partly (Phase 7; `deploy/hosted/backup.sh`, `restore.sh`, drill in `deploy/hosted/README.md`): restore refuses a backup that fails its checksum manifest. Not built: scheduled backups. Never run against a live stack or a clean host (owner item).
- **OPS-004 · P1 — Hosted identity operations:** OIDC/session security, invitation and role audit, recovery runbooks, and a clearly separated local identity adapter.
  - **Status:** Partly (Phase 1): session security, invitation, membership, role and credential audit events, and a separate local mode exist. Not done: recovery runbooks (see IDN-006).
- **OPS-005 · P1 — Privacy controls:** retention and deletion for drafts/model artefacts/log content.
  - **Status:** Implemented 2026-10-17 (`backend/devtools/retention.py`): discarded drafts are deleted, unfinished conversations expire after 30 days, logs carry no story text, and `make retention` (`RETENTION_ARGS='--days 30 --dry-run'`) purges expired sessions and invitations and, after N days, finished jobs with no waiting draft, context records no kept version or waiting draft uses, and notes on replaced versions; notes on kept or discarded drafts and words left on kept drafts go at once. Never a text, version, waiting draft or author decision. Not built: a schedule (the operator runs it) and a setting in the app.
- **OPS-006 · P1 — Health/degradation:** readiness and clear graph/vector/provider/worker status.
  - **Status:** Implemented 2026-10-17 (`backend/api/health.py`): readiness covers PostgreSQL, Neo4j and their migrations (503 `unavailable` when down) and, as optional parts that degrade to 200 `degraded` with plain reasons, the job worker (loop running, waiting count, oldest wait over five minutes) and the search store (reachable, embedding size); no addresses or credentials. Capabilities report embeddings, the AI provider and telemetry; projection lag has its own endpoint.
- **OPS-007 · P1 — Media operations:** processing/index lag, safe failure telemetry, quarantine/retry/orphan runbooks, storage quotas, and processor capability diagnostics.
  - **Status:** Not built (no media processing exists).
- **QLT-001 · P0 — Reference world:** synthetic cross-layer fixture with branches, secrets, and false beliefs.
  - **Status:** Partly (Phase 0 0H, Phase 2; `backend/seed/canon.py`): an author-only secret and false, unknown and true beliefs. Not built: branches, and Phase 3's fact visibility (Phase 3 exit criteria).
- **QLT-002 · P1 — Release workflow E2E:** automate the complete release-one loop.
  - **Status:** Implemented 2026-10-17 (Phases 2–7; `frontend/e2e/`, `scene-studio.model.spec.ts`): plan → write → draft → keep → check → read → review in a real browser against the local stand-in model. Not run with a real provider, nor yet in hosted CI (owner items).
- **QLT-003 · P1 — Evaluation baseline:** approved AI/retrieval metrics and comparison report.
  - **Status:** Partly: name-finding has a pinned baseline (Phase 2.7 I). The AI evaluation set was closed by owner decision (AST-EXP-003); no retrieval metrics exist.
- **QLT-004 · P1 — Accessibility:** keyboard and automated accessibility gates for core workspaces.
  - **Status:** Implemented 2026-10-17 (Phase 7; `e2e/accessibility-sweep.spec.ts`, axe in component tests, keyboard-only browser tests).

## Suggested first implementation sequence

`FND-005A → FND-001 → FND-002 → FND-003 → FND-006 → FND-008 → IDN-001 → IDN-002 → IDN-003 → IDN-004 → IDN-005 → OSS-001 → OSS-003 → CAN-001 → CAN-002 → CAN-008 → QLT-001 → CON-001 → CON-002`

This sequence creates safe seams and tests before changing durable data semantics.
