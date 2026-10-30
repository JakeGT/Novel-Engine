# Novel Engine — engineering handoff

This document is for the engineer or agent picking up Novel Engine. It summarizes what the project
is, its current state, how to run it, the architecture and invariants you must preserve, and exactly
what remains. Read `AGENTS.md` first (the operating contract) and the plans under `plan/`.

- Repo: `https://github.com/JakeGT/Novel-Engine`
- Branch: `phase-2-closure`, local only (not pushed). It branches from `main` at `827a9c2` and holds
  the Phase 2 closure work as a series of commits; `main` and `origin/main` are unchanged.
- Date: 2026-10-17 (Phases 3–7 sections at the end)

---

## 1. What this is

Novel Engine is an open-source, continuity-first fiction authoring system. Its defining constraint
is **canon integrity**: generated prose and proposed world changes must respect objective canon,
branch/timeline state, and each point-of-view character's knowledge. The project supports a
self-hosted single-user profile and an invite-only hosted profile (`*.jaketalling.com`) that
delegates authentication to a shared OIDC provider (Authentik).

Two rules dominate every design decision:

1. **Canon truth lives in PostgreSQL.** Neo4j, pgvector, search, and summaries are disposable,
   rebuildable projections.
2. **Model output is an untrusted proposal.** Nothing enters canon without passing an authoritative
   validator and the recorded commit path.

## 2. Current status

| Phase | Status |
| --- | --- |
| Phase 0 — prototype stabilization | Complete (merged PR #1). See `plan/phase-0-evidence.md`. |
| Phase 1 — identity, tenancy, distribution | Implemented with external exit items pending. See `plan/phase-1-evidence.md`. |
| Phase 2 — trustworthy canon | **Complete; accepted by the owner on 2026-10-09.** Every code-completable 2B–2L item is done; the open items are owner/operational (see §11). See `plan/phase-2-todo.md` and `plan/phase-2-evidence.md`. |
| Phase 2.5 — assistant-led creation | Built (2026-10-10). See `plan/phase-2.5-todo.md`. Assistant features verified against a scripted model only. |
| Phase 2.6 — story planning | Built (2026-10-11). See `plan/phase-2.6-todo.md`. |
| Phase 2.7 — text intake | Built (2026-10-11). See `plan/phase-2.7-todo.md`. Extraction quality unmeasured. |
| Phase 3 — time, what-ifs, knowledge | Built (2026-10-17). See `plan/phase-3-todo.md` and ADR-020. Assistant paths scripted model only. |
| Phase 4 — explainable context | Built for text (2026-10-17); picture processing, search and context added in batches 6–9. Audio and video deferred past release one (owner decision 2026-10-21). See `plan/phase-4-todo.md` and ADR-021. |
| Phase 5 — scene studio | Built (2026-10-17). See `plan/phase-5-todo.md` and ADR-022. Drafting and checks run against a scripted model and a local stand-in only. |
| Phase 6 — scenes back into the story | Built (2026-10-17). See `plan/phase-6-todo.md` and ADR-023. Stand-in and scripted models only. |
| Phase 7 — release hardening | In progress. See `plan/phase-7-todo.md`. |

**Not production-ready.** Billing, platform-funded inference, public signup, and real-time
collaboration do not exist by design. Do not describe a deferred capability as implemented
(`AGENTS.md` mandate).

**Recorded test baseline (2026-10-09, `phase-2-closure`):** 581 hermetic + 87 integration tests
pass; 34 frontend component tests and 2 Playwright browser tests pass; `make check` green;
`mypy backend` clean (133 files); `make audit` clean; combined backend branch coverage 84%.
Restricted coding-agent sandboxes may hang on Starlette's synchronous `TestClient`; see `AGENTS.md`
before diagnosing that as an application failure.

## 3. Repository map

| Path | Responsibility |
| --- | --- |
| `backend/api/` | HTTP transport. `v1/` is canonical; legacy `/api` aliases were removed in 1A. |
| `backend/services/` | Application workflows (auth, tenancy, schema, mutation review/commit, canon entity/assertion/history, guided character proposals, media, archive + legacy archive upgrade, equivalence, credentials, usage, projection, outbox). |
| `backend/domain/` | Storage-neutral domain: `canon/` (entities/propositions/facts/beliefs/events/revisions/provenance/registry/ids), `identity.py`, `tenancy.py`, `journal.py`, `operations.py`, `review.py`, `archive.py`, `commit.py`, `projection.py`, `history.py`, `media.py`, `credentials.py`, `usage.py`, `jobs.py`, `schemas.py`, `schema_defs.py`, `validation.py`, `errors.py`. |
| `backend/adapters/` | Concrete ports: Neo4j (`graph.py`, `canon_writer.py`), pgvector (`vector.py`), PostgreSQL (`postgres.py`, `canon_repository.py`, `journal.py`, `identity_store.py`, `tenancy_store.py`, `schema_store.py`, `media_store.py`, `credentials.py`, `usage.py`, `rate_limit.py`, `purge.py`), OIDC (`oidc.py`), object store (`objects.py`), AI (`ai.py`), embeddings (`embedding.py`), filesystem artifacts. |
| `backend/ports.py` | Protocol definitions for every external dependency. |
| `backend/container.py` | Dependency container built from `Settings`; owns adapters and lifecycle. |
| `backend/main.py` | `create_app(settings, container)` + ASGI export; lifespan owns resources. |
| `backend/core/` | Settings, async DB engine/session, logging + redaction, security (tokens/PKCE), crypto (envelope encryption), observability (Sentry), metrics. |
| `backend/graph/` | Neo4j schema, manager, traversal, migrations (ordered runner), transitional vector search. |
| `backend/seed/` | Deterministic seeds: legacy graph manifest, tenancy, schema, and the canonical reference world (`canon.py`, committed through the mutation path). |
| `backend/devtools/` | Guarded CLIs: seed, reset, diagnostics, retention, reconcile, project, watch, archive, verify. |
| `migrations/` | Alembic environment + PostgreSQL revisions. |
| `frontend/` | TypeScript React app (Vite), OpenAPI-generated client, vitest component tests, Playwright browser tests in `frontend/e2e/`. |
| `scripts/manual/`, `scripts/ci/`, `scripts/e2e/` | Non-hermetic manual probes; secret scanner; disposable-database API launcher for browser tests. |
| `tests/{unit,application,contract,integration}` | Test suites (`tests/e2e/README.md` points to `frontend/e2e/`). |
| `plan/` | Product, architecture, delivery, decisions (`plan/decisions/`), per-phase evidence. |

## 4. Architecture you must preserve (ADR-012–016)

Canonical authority by datum (see `plan/decisions/012-postgres-canon-and-replayable-graph.md`):

- **PostgreSQL** owns users/sessions/workspaces/projects/memberships/security-audit; project
  schema definitions and immutable schema versions; and the **mutation workflow** (sets, operation
  revisions, per-operation and set-level decisions, the transition log), commit envelopes,
  branch-head reservation/finalization, outbox, **normalized/versioned canon** (entities, facts,
  beliefs, events, provenance, evidence, record versions), media identity and its lifecycle audit,
  and the archive import ledger.
- **Neo4j** owns accepted-graph query state as a rebuildable projection under its own `Canon*`
  labels, bounded by the committed PostgreSQL head, with durable, layout-versioned watermarks. The
  legacy generic graph lives in the same database under `Entity` plus a type label; the two never
  share labels.
- **Object storage** owns immutable media bytes; PostgreSQL owns lifecycle metadata.
- **pgvector/search/summaries** are rebuildable projections (canon has no vector index yet).

**Canonical commit is one PostgreSQL transaction** (`PostgresCanonRepository.apply_commit`):
lock the proposal → recompute its effective operations from stored revisions and decisions →
**review gate** → lock head → authoritative validator → reserve → append immutable envelope → apply
normalized canon, record versions, evidence → advance head + revision → outbox → state transitions.
Success is established only here and **never depends on Neo4j**.

Rules that are easy to break by accident:

- **Never add a canon write path around the mutation set.** The gate and validator live in the
  repository transaction precisely so a service, import, or new writer cannot skip them.
- **Review gate (ADR-014):** protected origins (anything but `author`/trusted `migration`),
  destructive operations, bulk changes (>25 operations), and reverts need an explicit approval.
  Approval is perishable: editing an operation, changing a decision, or retargeting returns the set
  to `needs_review`. Retire/revert endpoints take `acknowledge_high_impact`; without it the request
  is parked and answers `409 review_required`.
- **Operation evolution (ADR-015):** a field added to a typed operation must be optional and
  registered in `_CANONICAL_OMIT_WHEN_UNSET` in `backend/domain/operations.py`, otherwise every
  earlier envelope checksum changes. A test pins the exact pre-existing canonical bytes.
- **New project-owned tables** go in `backend/adapters/canon_tables.py`; that one list drives
  project/workspace deletion, failed-import removal, and the development reset.
- **Projection layout changes** need a new Neo4j migration and a bump of
  `PROJECTION_SCHEMA_VERSION`, which makes old watermarks read as lag and forces a rebuild.
- **Equivalence is checked, never repaired** (`CanonEquivalenceService`): canon is not modified to
  match a derived store.
- Metric labels and audit details carry counts and fixed vocabularies only, never ids or content.
- Alembic revision ids must be 32 characters or fewer.

Other invariants:
- One root branch per project; monotonic system revisions; optimistic `expected_revision`/`ETag`;
  at most one active reservation per branch; a mutation set yields at most one immutable envelope.
- Objective facts and character beliefs are distinct types; belief truth agreement is **derived**,
  never stored (`Belief` has no `is_true`).
- Names are handles, not identity: slugs are unique among active entities, external ids bind one
  entity per project, duplicate display names are advisory.
- Evidence is a bounded pointer (identifier shapes only), never copied content.
- Workspace/project scope is mandatory at every durable/derived boundary. Local mode has exactly
  one project; a URL naming another is 404.
- Project schema extensions are a bounded declarative meta-schema compiled to strict Pydantic +
  JSON Schema (no user code/regex/Cypher); published versions are immutable.
- Built-in integrity findings are permanently blocking. The author-rule evaluator seam exists;
  rule authoring is Phase 2.5.
- Hosted BYOK: OpenRouter keys are envelope-encrypted and only the authorized worker path resolves
  plaintext.
- The generic graph API is a local/admin-only escape hatch for writes, including the legacy
  character endpoints. Product writes go through the mutation service.

## 5. Local setup

Prerequisites: Python 3.13, Node.js 24 LTS, `uv`, pnpm 11, Docker + Compose. `.python-version` and
`.nvmrc` are authoritative.

```bash
cp .env.example .env            # then fill credentials (see §6)
make install-dev                # uv; base install excludes sentence-transformers
make frontend-install           # frozen pnpm install
make infra-up                   # PostgreSQL/pgvector + Neo4j (waits for healthy)
make migrate                    # Alembic + Neo4j migrations
make seed                       # deterministic reference worlds (idempotent)
make project                    # project committed canon into Neo4j (or `make watch`)
make api                        # FastAPI on http://localhost:8000
pnpm --dir frontend run dev     # frontend (Vite dev proxy to :8000)
```

Optional local semantic embeddings (`sentence-transformers`) are an extra:
`uv sync --locked --extra embeddings`. The base install and CI run without it and degrade
gracefully.

Useful one-off commands: `make verify` (journal consistency), `make project PROJECT_ARGS=--lag`
(projection lag), `make project PROJECT_ARGS=--verify` (journal = normalized canon = projection),
`make watch WATCH_ARGS=--interval 5` (drain outbox), `make archive ARCHIVE_ARGS="--actor USER_ID
--workspace W --project P --export /tmp/project.json"`, `make diagnostics`, `make retention`,
`make reset-dev` (guarded; refuses production or non-local stores; it passes `--force` itself, so
running it resets immediately).

The local `.env` in this checkout runs in **hosted** auth mode, so operator CLIs must name an
authorized user with `--actor` (for the seed data, `usr_seed_owner`). In local mode the UI opens
the single local project, not the seeded workspaces.

## 6. Environment variables

All variables are documented in `.env.example`; production rejects unsafe defaults. Never commit
`.env` or real secrets. Key groups:

- App: `PROJECT_NAME`, `PROJECT_VERSION`, `APP_ENV` (`development|test|production`), `LOG_LEVEL`.
- AI: `OPENAI_API_KEY`, `AI_MODEL`, `AI_PROVIDER`, `AI_BASE_URL` (hosted BYOK uses OpenRouter; the
  per-request credential/base_url come from the credential vault).
- PostgreSQL: `POSTGRES_HOST/PORT/USER/PASSWORD/DB`, pool settings.
- Embeddings/objects: `EMBEDDING_MODEL_NAME/DIMENSION`, `CUSTOM_SCHEMA_PATH` (legacy local only),
  `ENABLE_DEBUG_ARTIFACTS`, `ARTIFACT_STORE_DIR` (debug), `OBJECT_STORE_DIR` (product object store).
- Telemetry: `SENTRY_DSN_BACKEND`, `SENTRY_DSN_WEB`, `SENTRY_TRACES_SAMPLE_RATE` (opt-in; scrubbed);
  `METRICS_ENABLED`, `METRICS_TOKEN` (Prometheus text at `/health/metrics`, off by default).
- Pictures and search: `AI_IMAGE_INPUT`, `AI_IMAGE_MAX_COUNT/MAX_BYTES/TYPES`, `AI_IMAGE_PROVIDERS`
  (whether a route sees pictures); `MEDIA_*` (processing, decoding limits, `MEDIA_WORKER_SANDBOX`
  `best|require|off`, workspace quota, orphan grace); `EMBEDDING_PROVIDER`, `EMBEDDING_GEMMA_*`, `EMBEDDING_API_*`,
  `SEARCH_INDEX_CATCH_UP_MINUTES`.
- Auth: `AUTH_MODE` (`local|hosted`), `AUTH_LOCAL_USER_ID`, `AUTH_LOCAL_WORKSPACE_ID`,
  `AUTH_LOCAL_PROJECT_ID`,
  `OIDC_ISSUER/CLIENT_ID/CLIENT_SECRET/REDIRECT_URI/POST_LOGOUT_REDIRECT_URI/SCOPES`,
  `SESSION_COOKIE_NAME`, `SESSION_TTL_SECONDS`, `SESSION_ROTATION_SECONDS`, `CSRF_COOKIE_NAME`,
  `PLATFORM_ADMIN_USER_IDS`, `ALLOW_WORKSPACE_SELF_CREATION`, `ALLOW_PERSONAL_WORKSPACE`.
- Credentials/limits: `CREDENTIAL_KEK`, `CREDENTIAL_KEK_VERSION`, `CREDENTIAL_PREVIOUS_KEKS`,
  `PROVIDER_PER_JOB_TOKEN_LIMIT`, `PROVIDER_PER_JOB_COST_LIMIT`, `PROVIDER_PERIOD_TOKEN_LIMIT`,
  `PROVIDER_PERIOD_COST_LIMIT`, `PROVIDER_PERIOD_SECONDS`, `CONNECTOR_ANSWER_SECONDS` (local
  connector wait).

**Identity:** hosted mode uses Authentik (self-hosted, shared across `*.jaketalling.com`). App host
`novel-engine.jaketalling.com`; issuer `https://auth.jaketalling.com/application/o/novel-engine/`.
The application + confidential OIDC client are provisioned; redirect allow-list is strict
(`https://novel-engine.jaketalling.com/auth/callback`, `http://localhost:8000/auth/callback`,
`http://localhost:5173/auth/callback`). See `plan/decisions/009-invite-only-tenancy-and-shared-identity.md`.
Local mode (`AUTH_MODE=local`) needs no identity provider and is the self-hosted default.

**Web telemetry:** Vite only exposes `VITE_`-prefixed vars from `frontend/.env`
(`VITE_SENTRY_DSN_WEB`).

## 7. Commands (Makefile)

`install`, `install-dev`, `lock`, `lock-check`, `infra-up`, `infra-down`, `migrate`,
`migrate-verify`, `verify`, `seed`, `reset-dev`, `diagnostics`, `retention`, `reconcile`, `project`,
`watch`, `archive`, `audit`, `api`, `test`, `test-integration`, `integration-test`,
`coverage-unit`, `coverage-integration`, `typecheck` (= `mypy backend`), `import-check`, `lint`,
`format-check`, `secret-scan`, `frontend-install`, `frontend-api-check`, `frontend-lint`,
`frontend-typecheck`, `frontend-test`, `frontend-build`, `frontend-e2e-install`, `e2e`, `check`.

Use `uv add`/`uv remove` (Python) and `pnpm --dir frontend add/remove` (frontend); refresh lock
files in the same change.

## 8. Testing

- `make test` — hermetic unit/application/contract (no services, network, keys, model downloads, or
  persistent writes). 581 tests, including Hypothesis property and state-machine tests.
- `make test-integration` — disposable PostgreSQL/pgvector + `neo4j-test` (start with
  `docker compose --profile test up -d --wait neo4j-test`). 87 tests, including real concurrency,
  upgrade migrations, performance budgets, and a clean-environment archive restore.
- Frontend: `make frontend-api-check frontend-lint frontend-typecheck frontend-test frontend-build`
  (contract drift guard plus 34 vitest component tests with axe assertions).
- `make e2e` — Playwright against a real API on a disposable database (not part of `make check`).
  Install the browser once with `make frontend-e2e-install`, or set `E2E_BROWSER_CHANNEL=chrome`.
- Mark service-dependent tests `integration` and paid-model tests `llm`. Live LLM tests are
  excluded from ordinary CI.
- A property test is only evidence if it can fail: the review state machine was checked against
  deliberately broken invariants. Do the same when adding one.

## 9. API surface (canonical `/api/v1`)

`plan/api-surface.md` is the guide; `tests/contract/openapi_snapshot.json` (145 operations) is the
contract. Under `/projects/{project_id}`:

- Canon: `/entities` (+ `/search`, `/by-external-id`, `/name-collisions`, `/retire`), `/facts`,
  `/beliefs` (+ `/truth-agreement`), `/events`.
- Proposals and review: `/mutations` (+ `/validate`, `/submit`, `/approve`, `/reject`, `/retarget`,
  `/commit`, `/operations/{n}`, `/operations/{n}/decision`); `/characters/{id}/questions|proposals`.
- Schema: `/schemas` (list/validate/draft/publish) and `/schemas/vocabulary`.
- History/provenance: `/revisions`, `/revisions/{from}/diff/{to}`, `/{kind}/{id}/history`,
  `/{kind}/{id}/at-revision/{revision}`, `/provenance/{record_type}/{record_id}`, `/tags`,
  `/reverts/preview`, `/reverts`.
- Media: `/assets` (+ `/versions`, `/lifecycle`, `/links`, `/retire`), `/links`.
- Operations: `/projection`, `/access`, `/archive/export`, `/imports`.

Tenancy, credentials, and auth are unchanged from Phase 1. `/health/metrics` is operational and
deliberately outside the OpenAPI contract.

Regenerate the snapshot with
`python -c "import json;from backend.main import create_app;open('tests/contract/openapi_snapshot.json','w').write(json.dumps(create_app().openapi(),indent=2,sort_keys=True)+'\n')"`
then `pnpm --dir frontend run gen:api`.

## 10. Data & migrations

- Alembic (PostgreSQL) head: **`0063_site_search_setup`** (… → `0022_archive_imports` →
  `0023_session_id_token` → `0024_assist_sessions` → `0025_workspace_ai_settings` →
  `0026_assist_suggestions` → `0027_usage_allowance` → `0028_plan_items` →
  `0029_plan_conversations` → `0030_source_texts` → `0031_story_rules` → `0032_story_time` →
  `0033_context` → `0034_scene_studio` → `0035_scene_readings` → `0036_cached_tokens` →
  `0037_finding_decisions` → `0038_usage_controls` → `0039_check_passes` → `0040_scoped_names` → `0041_claims` →
  `0042_delegation` → `0043_access_keys` → `0044_consistency_runs` → `0045_how_long` → `0046_media_variants` →
  `0047_embedding_generations` → `0048_media_observations` → `0049_check_fingerprints` →
  `0050_read_models` → `0051_planning_help` → `0052_context_layers` → `0053_local_connector` →
  `0054_conversations` → `0055_voice_and_audits` → `0056_context_remainders` →
  `0057_check_remainders` → `0058_media_remainders` → `0059_streaming_and_routes` →
  `0060_picture_regions` → `0061_check_passes_more` → `0062_audits_and_impact` →
  `0063_site_search_setup`).
- Neo4j ordered migrations: `0001_initial_graph_schema`, `0002_entity_scope_identity`,
  `0003_canon_scope_identity`, `0004_canon_projection_labels` (checksums in
  `plan/phase-2-evidence.md`).
- Migrations run explicitly via `make migrate` / `make migrate-verify`; startup verifies read-only.
- Upgrade notes (duplicate slugs cleared by `0020`; projection rebuilt after Neo4j `0004`) are in
  `plan/phase-2-evidence.md`.

## 11. What remains

**Phase 2 has no remaining code work.** What is open is owner or operational:

1. ~~Accept or reject `plan/phase-1-evidence.md`.~~ Accepted by the owner on 2026-10-09.
2. ~~Push `phase-2-closure`.~~ Pushed and merged into `main` as PR #2 on 2026-10-30, after every
   check passed locally. The first hosted CI run is still open: on PR #2 GitHub did not start any
   job ("recent account payments have failed or your spending limit needs to be increased"), so the
   `e2e-browser` job and the API-drift step have still never run in hosted CI. Fix the account's
   billing, then re-run the workflow on `main` and expect to fix environment details there.
3. Run, or time-box, the clean-host install and coordinated PostgreSQL/Neo4j/object-store
   backup/restore rehearsal.
   **Deferred by the owner on 2026-10-10** until the project is complete and on a permanent host.
4. ~~Perform the live Authentik browser round trip.~~ Done by the owner on 2026-10-10 on the deployed site.
5. ~~Accept, amend, or reject ADR-014, ADR-015, and ADR-016.~~ Accepted by the owner on 2026-10-10.
6. ~~Accept `plan/phase-2-evidence.md`.~~ Accepted by the owner on 2026-10-09.

Known limitations of what was built are listed in `plan/phase-2-evidence.md`. The ones most likely
to matter next:

- Enum domains are not schema-governed (enum/quantity/structured fact values are entered as JSON).
- No "un-retire" operation; a revert lists retirements for manual handling.
- Stale proposals are retargeted as a whole set; record-level rebasing is Phase 3.
- The UI cannot create beliefs/events or register/link assets; hosted UI behaviour is
  component-tested only.
- The guided character workflow has only been run against a scripted model.
- Nothing runs the projector automatically in local development.

## 12. Conventions and gotchas

- Read `AGENTS.md` before changing code; it is the operating contract (design rules, change workflow,
  definition of done). **Audit-grade honesty:** claims must match implementation; only check a box
  when verified.
- Imports must never perform external work (no network, DB, model load, or writes at import time).
  `tests/unit/test_import_safety.py` enforces this.
- Never commit `.env`, databases, model caches, debug responses, or manuscript content. `make
  secret-scan` guards tracked/staged files.
- `backend/services/world_service.py` was **intentionally discarded** in 1A (owner decision to
  rebuild world authoring later). Do not resurrect it piecemeal.
- Local workstation Node may be newer than 24 (CI pins Node 24); pnpm may warn about the engine.
  Frontend dependencies were installed with a specific pnpm store; if pnpm complains about the store
  location, pass `--store-dir` or reinstall.
- Untracked local agent-tooling files (`.claude/ .codex/ .gemini/ .mcp.json GEMINI.md opencode.jsonc`)
  exist in the worktree; leave them alone.
- If the repository directory is moved, the virtualenv's entry points and the pnpm store path go
  stale: run `uv sync --locked --reinstall` and `CI=true make frontend-install`.
- `make reset-dev` is immediate and destructive for local development stores.
- `make frontend-e2e-install` lets Playwright manage `~/.cache/ms-playwright`; it may remove a
  Chromium build another project on the machine was using.
- Structured logs are redacted by default; never log canon bodies, tokens, or provider raw errors.

## 13. Suggested next steps

1. **Close Phase 2 formally:** the six owner items in §11. Hosted CI first, since it is the only
   verification that has not been run anywhere.
2. **Phase 2.5 (text-first author workflow probe):** manuscript import, explicit entity marks,
   scoped alias memory, bounded extraction into grouped proposals, and story-rule authoring. It
   builds directly on the proposal/review model, the evaluator seam, the evidence references, and
   the reference world's labelled proposals.
3. **Before Phase 3:** settle the query-store evaluation gate (PostgreSQL vs Neo4j for
   branch/time/POV queries) so a second query layer is not added by accident — see ADR-012.
4. **Cleanup worth scheduling:** remove the legacy generic graph API once nothing depends on it;
   decide whether enum domains belong in the schema meta-model.

## 14. Reference documents

- `AGENTS.md` — operating contract.
- `plan/README.md` — index of all plans.
- `plan/decisions/` — ADRs 001–016 (003 storage, 004 branches/schema, 005 POV/consistency, 007
  foundation tooling, 009 identity/tenancy, 010 media boundary, 011/012 canon authority + commit,
  013 invariants, and the proposed 014 review workflow, 015 handles/evidence/operation evolution,
  016 projection isolation and restore).
- `plan/phase-0-evidence.md`, `plan/phase-1-evidence.md`, `plan/phase-2-evidence.md` — evidence.
- `plan/phase-2-todo.md`, `plan/phase-2-baseline.md`, `plan/phase-2-glossary.md` — Phase 2 contract.
- `plan/api-surface.md` — guide to the API contract.
- `plan/12-current-gap-analysis.md` — point-in-time map to the target design.

## Hosted deployment (added 2026-10-09)

`Dockerfile` builds one image (API, migration CLI, built web UI served by the API);
`deploy/hosted/` holds the Compose stack, secret initialisation, backup script, and the ordered
runbook for the jaketalling platform. **Deployed on 2026-10-10** at `novel-engine.jaketalling.com`
(Caddy route and Authentik blueprint live in the platform repository; the owner signed in and
created canon). To ship a new version: build the image, set `NOVEL_ENGINE_VERSION` in
`deploy/hosted/.env`, `docker compose up -d --wait` (run `./backup.sh` first).
Hosted/production sites hide `/docs` and `/openapi.json`, send security headers, and read
secrets from files (`NOVEL_ENGINE_SECRETS_DIR`). `python -m backend.devtools.provision` creates
the first workspace for a user who has signed in. Development store ports now bind to 127.0.0.1.

## Frontend redesign (2026-10-10)

The owner asked for the UI to be rebuilt for a gentle learning curve. The frontend was rewritten:
landing page, library, sidebar shell with search palette, story bible, entry page with a sentence
builder for facts, one-button review ("Approve and add to canon"), history with "go back to this
version", form-based types and fields, and an AI-key page. Old addresses redirect. Server terms are
translated in `frontend/src/lib/words.ts` (entity → entry, mutation → suggested change, revision →
version, retire → archive, predicate → kind of fact). New projects are set up automatically (starter
schema), both by the API and, for older projects, by the app on first open. `GET .../revisions` now
pages back from the newest revision (it previously returned the oldest page). See
`frontend/README.md`.

## Assisted creation (2026-10-10)

The owner asked for creation to be AI-led, as in the original design. "New entry" now opens the
assistant: describe one thing or a whole idea, optionally answer generated questions, and the
assistant drafts entries, details, and linking facts as one proposal for review
(`backend/services/canon_creation_workflow.py`, `/assist/*`, `frontend/src/components/AssistFlow.tsx`).
Things that are only mentioned become bare entries; `GET /entities/needs-detail` derives a to-do
list of them, shown on the project home, and the same assistant fills in any existing entry.
Typing an entry in by hand is still one click away. Duplicate avoidance is by normalized name and
alias within a type, not semantic search. **Not verified against a real model**: tests use a
scripted model, plus a check that the output format converts to a strict JSON schema. The hosted
stack sets `AI_MODEL` (default `anthropic/claude-sonnet-5.5`, an OpenRouter id); the previous
default `gpt-4o` is not a valid OpenRouter id, so hosted AI calls could not have worked before.
The older character-only endpoints remain but the UI no longer uses them; their output model
still contains a free-form object that strict structured output rejects.

## Plan revision and second redesign (2026-10-10)

The owner directed that the product be easy to use, assistant-first, and learned gradually, and
that the interface stop looking like a generated dashboard.

- **Plan.** Audited in full and revised: `plan/audit-2026-10-10.md` (findings by document and the
  decisions needed from the owner), `plan/19-assistant-first-experience.md` (the loop, levels,
  capability map, story planning by conversation, definition of done), ADR-017 (proposed), and a
  resequenced roadmap: 2.5 assistant-led creation, 2.6 story planning through conversation, 2.7
  text intake. Read 19 before building anything author-facing.
- **Design.** `plan/20-design-direction.md`: "ink on paper". Newsreader and Instrument Sans, warm
  paper, one cinnabar accent, hairline rules instead of cards, print layouts, and a WebGL2 ink
  shader on the landing page (`InkCanvas.tsx`). WebGPU was considered and not used.
- **Open owner decisions** are listed at the end of the audit; the first (how a new author gets a
  working assistant without their own key) blocks the first-run design.

## Phase 2.5 in progress (2026-10-10)

`plan/phase-2.5-todo.md` is the live checklist. Built so far: assisted creation and fill-in, a
first version of the God Hand (`canon_change_workflow.py`, `POST /assist/changes`, `GodHand.tsx`:
one instruction → one proposal of every implied change, or questions when ambiguous), and grouped
review with keep-all/skip-all. All assistant workflows are tested with a scripted model only.
Pictures on entries (AST-EXP-018) were added to the plan at the owner's request and are not built.

Later the same day: pictures on entries (upload, portrait, removal; `Pictures.tsx`,
`POST /entities/{id}/pictures`) and a People page with invitation links (`PeoplePage.tsx`,
`InvitePage.tsx`) were added. The hosted content security policy now allows `blob:` images, because
pictures are fetched with the workspace header and shown from a local object address.

## Platform guide and first run (2026-10-10)

`platform-guide.md` (from the owner) describes the hosting platform's contract. Decisions taken
from it: Authentik is the only gate, so a newly admitted person is onboarded and creates one
personal workspace (`ALLOW_PERSONAL_WORKSPACE`); the app keeps its own visual style; sign-out
returns to the front page, which required keeping the ID token on the session (migration
`0023_session_id_token`). Hosted sessions are 12 hours. Non-security gaps against the guide were
left by the owner's decision and are listed in `plan/14-saas-platform.md`. Resend is available for
product email (sender `novel-engine@notify.jaketalling.com`); nothing sends email yet.

## Intuitive design rules (2026-10-10)

`plan/21-intuitive-design-rules.md` is binding for every screen: visible text labels on every
control, one word per thing (the vocabulary table there), recognition over recall, nothing
essential on hover, consequential actions kept apart from routine ones, undo before confirmation,
specific confirmations with the safe choice first. Shared pieces: `ConfirmDialog` and `UndoNotice`
in `components/ui.tsx`, `.danger-zone` and `.mini` in `app.css`, `roleLabel` in `lib/words.ts`,
`lib/recent.ts`. The document ends with what the first review changed and what is still open.

## Stored conversations (2026-10-10)

`assist_sessions` (migration `0024`) keeps each conversation with the assistant.
`AssistSessionService` owns the rules: open ones belong to their author alone, proposed ones are
read-only evidence for every project reader, putting one away deletes the row. The API layer
(`backend/api/v1/canon_assist.py`) opens and closes sessions around the workflow calls; the
workflows only accept the session id. Frontend: `components/Conversations.tsx`
(`OpenConversations`, `ConversationEvidence`), `resume` in the project context. The text is the
author's own writing: it is never logged, and it is not yet part of a project export.

## Providers and model choice (2026-10-10)

`backend/domain/providers.py` lists the providers (all through OpenAI-compatible endpoints) and
holds the per-request model (a context variable set in `_provider_context`, read by
`OpenAIGateway`, so workflow signatures did not change). `CredentialService` resolves the route
(`resolve_route_for_use`), stores the choice (`choose_ai`), and lists models. The gateway asks for
a schema where the provider enforces one and otherwise describes the shape in the prompt and
validates the answer (`_prompted`), falling back to that when a schema is refused. Migration
`0025`. Nothing here has been run against a real provider.

## First real model run, and setup suggestions (2026-10-10)

The owner ran assisted fill-in on the live site with a real model; see section P of
`plan/phase-2.5-todo.md` for what it showed. `backend/services/schema_suggestions.py` holds the
prompt rule, the cleaning of the model's suggestions, and `add_to_definition`. Note for tests:
`auth_client` sends the CSRF header on every request, so a GET route wrongly guarded by
`require_project_scope` passes tests and fails in a browser; read routes use `get_project_scope`.

## Sessions and losing work (2026-10-10)

Session replacement (`AuthService.resolve`) keeps the CSRF token, leaves the old id valid for
`ROTATION_GRACE_SECONDS`, and stores `rotated_from` hashed. `RotatedSessionCookieMiddleware`
delivers replacement cookies on every response, including errors. In the frontend,
`lib/reauth.ts` holds any request answered 401 (once the page has been signed in) until
`/auth/me` succeeds again, `SignedOutOverlay` explains and links to sign-in in a new tab, and
`lib/keepalive.tsx` touches the session every four minutes while the tab is visible. Consequence
to know: while someone keeps the site open, their session keeps renewing, so removing their access
in Authentik takes effect when they next have to sign in (closing the tab, or twelve hours idle),
not sooner.

## Design rules, marks, and the story bible (2026-10-10)

UI work follows the owner's shared rules in `~/Projects/Common/rules.md` (review from a
screenshot; cite rule ids). Entries carry marks (`_tags`, `_importance`, `_needs_detail`) inside
`attributes`; they are stripped before type validation (`backend/domain/validation.py`) and
never shown as details (`frontend/src/lib/marks.ts`, `components/EntryMarks.tsx`). `byPriority`
is the one ordering for entry lists. `BiblePage` has three views: index, `?view=all`, `?type=`.

## Rest of Phase 2.5 (2026-10-10)

The owner closed workstream D: he tests against the real model by hand and reports back over
time, so nothing waits on an evaluation set. What was built after that, in order:

- **Review:** edit a change with a form; keep or skip a group in one all-or-nothing request;
  a model-free check against the story when a set is opened (duplicates under another type,
  archived twins, facts already recorded); revise the set by asking the assistant; a dialog on
  commit. A new value for a one-at-a-time fact now replaces the old one instead of blocking the
  whole set.
- **Allowance:** an owner sets a daily token allowance per person (migration `0027`); it is
  shown on the Assistant page and warned about from 80%. A used-up allowance was an unhandled
  500 and is now a 429.
- **Threat cases** for the assistant are tests (`tests/contract/test_assist_threats.py`).
- **Beliefs by hand**, a **sample story**, **link suggestions**, a **shorter menu for a new
  project**, and a **plain-language check** that runs wherever the accessibility check does.
- "Change anything" points to the right page when asked for something that is not a story change.

Not done, and said so in `plan/phase-2.5-todo.md`: nothing by asking actually performs an undo,
an invitation or a setup change; no per-conversation budget or cost estimate; link suggestions
use the chat model, not embeddings, and their precision is unmeasured; contradictions in meaning
are not detected; time to first entry is not measured. Revise-by-asking, link suggestions and
the pointing-elsewhere behaviour have only ever run against the scripted model.

## Phase 2.5 closed out (2026-10-10)

Added after the section above: asking the assistant to undo, invite or extend the setup now ends
in the real thing ready to confirm (restore preview, pre-filled invitation, suggestions to add);
an estimate of what a request reads is shown before it is sent; a picture can be made the main
one or reused from another entry; unfinished conversations expire after 30 days; lists say how an
entry is connected to the main entries, from the facts. `plan/phase-2.5-todo.md` now ends with
the roadmap's exit criteria and their state, and what was left for later phases and why. Two
criteria are not met and cannot be by building: timing a new person's first entry, and the
evaluation baseline the owner chose to replace with manual runs.

## Error reporting (2026-10-11)

Sentry was wired but off on the hosted site, and one thing was wrong: `frontend/.env` reached
the image build (`.dockerignore` only excluded the root `.env`), so the browser DSN was baked
into the deployed bundle. The site's content security policy blocked it from sending, so nothing
left, but the browser SDK (v11) collects user details, cookies, headers and bodies by default
and had no scrubbing. Now: no env file enters the build; both DSNs are secret files under
`deploy/hosted/secrets/`; the server gives the page its DSN and release at run time; the release
is the deployed commit; both sides scrub, and each was checked by sending real SDK events to a
local stand-in (`tests/unit/test_observability.py`, `frontend/e2e/error-reporting.spec.ts`). A
screen that fails to draw now shows a message and a way back instead of a blank page.

Not verified: any event reaching the real Sentry projects, and `upload-sourcemaps.sh`. An
exception's message is sent as it is, so code must not put an author's words in one.

## Phase 2.6: story planning (2026-10-11)

A story plan now exists as its own kind of record: story, arc, beat, scene, thread. It is
governed like canon (review, history, undo) but lives in its own table and is never projected
to the graph, so a planned scene cannot be read as something that happened (ADR-018). Build
order was: records and rules (`backend/domain/canon/plans.py`, migration 0028); the by-hand
service and API (`plan_service.py`, `/plan`); the outline page (`PlanPage.tsx`); then the
assistant (`plan_workflow.py`, `/assist/plan/*`, `PlanAssist.tsx`).

`plan/phase-2.6-todo.md` has the state of each item and of the roadmap's exit criteria. The
main things not done: scene contracts, any measure of plan quality, a form editor for plan
changes on the review page, and putting a removed plan item back by undo. The assistant
workflows have only run against the scripted model.

## Phase 2.7: text intake (2026-10-11)

An author can bring in prose they have already written, and it becomes another way to fill
in the story bible. The decision record is ADR-019: a text is evidence, never canon; a story
rule is data in a fixed vocabulary, never code.

- **Texts** (`backend/domain/sources.py`, `source_service.py`, `/texts`, migration 0030):
  kept exactly as given, each change a new version whose words cannot change. Names are found
  with no model. "Who is this?" answers are remembered (`/names`).
- **Reading with the assistant** (`intake_workflow.py`, `/assist/texts/{id}/read`): one
  bounded passage; every item must quote words really in it; the result is ordinary suggested
  changes that show their passage (`FromText.tsx`).
- **Story rules** (`backend/domain/rules.py`, `rule_service.py`, `/rules`, migration 0031):
  checked by one evaluator that also runs a rule's examples and a scene's conditions.
  `RuleService` is registered as an evaluator on the commit validator and only ever returns
  warnings.
- **Scene conditions** (`scene_conditions.py`, `PUT /plan/{id}/conditions`): kept in the
  scene's details under `_conditions`.
- **Measured**: name-finding against a labelled passage, pinned in
  `tests/fixtures/intake_eval/baseline.json`. Regenerate it on purpose with
  `python -m backend.devtools.intake_eval tests/fixtures/intake_eval/lantern_coast.json`.

The assistant chain is now `… → PlanWorkflow → IntakeWorkflow → RuleWorkflow`; the container
and tests construct `RuleWorkflow`. Alembic head was `0031_story_rules` at the end of Phase 2.7.

`plan/phase-2.7-todo.md` has the state of each item and of the roadmap's exit criteria. Main
things not done: blocking rules, rules about time or belief, name memory scoped by time or
point of view, any measurement of a real model's reading, MCP access, and review telemetry.
Both assistant workflows have only run against the scripted model.


## Phase 3: time, what-ifs and knowledge (2026-10-17)

ADR-020 records the decisions; `plan/phase-3-todo.md` has the state of each item, the design
review and the roadmap's exit criteria.

- **Story time** (`backend/domain/worldtime.py`): a time is unknown, at a point, between two
  points, or relative to an event, on the story's own number line in one unit it names
  (`story_clocks`). Facts gain `starts`/`ends` and `visibility`, events a time; one new
  operation, `amend_fact`. All new operation fields are omitted from the canonical form when
  unset, so old envelope checksums are unchanged. `StoryTimeService`, `/timeline`.
- **What-ifs are branches** (`what_if_service.py`, `/what-ifs`): starting one copies every story
  table in one transaction (`PostgresCanonRepository.fork_branch`, `_FORKED_TABLES`). The working
  branch travels in the `X-Story-Branch` header, is checked in `backend/api/auth.py`, and is held
  in a context variable (`backend/domain/branching.py`) that every repository read filters by
  (`_ON_BRANCH`). The in-memory doubles keep one store per branch (`_per_branch`). Only the main
  story is projected; the outbox consumer skips what-if commits. **A new story table must be
  added to `_FORKED_TABLES` and keyed by branch**, or forking will miss or collide on it.
- **Knowledge and the reader** (`backend/domain/knowledge.py`, `knowledge_service.py`,
  `/knowledge/*`, `/reader`, `/reader/guide`, `PUT /plan/{id}/reveals|when`).
- **Record-level rebasing**: a stale proposal keeps its approval when nothing it touches
  changed (`MutationService._changed_since`); this amends ADR-014.
- **Frontend**: `TimelinePage`, `WhatIfsPage` (list and compare), `KnowledgePanel`,
  `TellDialog`, `FactDetailsDialog`, `WhenField`, `SceneReader`; `lib/whatif.ts` keeps the
  working what-if per project and `Scope.branchId` puts it in every request and cache key.
- **Docker Compose was missing on this machine** (2026-10-17): the local test databases were
  recreated with `docker run`, and the hosted stack's containers had been removed (volumes
  intact). The deploy command needs Compose back.

Not done: what-ifs of what-ifs, field-level merges, alias memory by time or viewpoint, trust,
contradictions that need prose, an assistant path for telling or reveals. Every assistant path
has only run against the scripted model.


## Phase 4: explainable scene context (2026-10-17)

ADR-021 records the decisions; `plan/phase-4-todo.md` has the state of each item.

- `backend/domain/context.py` assembles what the assistant would be given for a scene: each item
  with a reason, section, authority, words and score; stable sections first and the scene last;
  a word budget that never cuts hard items; the author's pins, exclusions and choice about secrets
  kept in the scene's details. `ContextService.for_scene`, `GET/PUT /plan/{id}/context`.
- The passage index (`text_passages`, `backend/services/passage_index.py`) keeps pointers into the
  author's writing and the entries each passage names, never the words; optional embeddings.
  `POST /texts-index/rebuild`, `python -m backend.devtools.reindex`.
- `context_manifests` keeps exactly what was given for a request; Phase 5 drafts point at it.
- Frontend: `SceneContextDialog` ("What the assistant would use"), from the plan's scene menu.

## Phase 5: the scene studio (2026-10-17)

ADR-022 records the decisions; `plan/phase-5-todo.md` has the state, the adversarial review, the
design review and the exit criteria.

- **Prose** is a text of kind `scene` linked per line of the story (`scene_manuscripts`, in
  `_FORKED_TABLES`). Versions record origin, parent, draft and manifest. Copy on write keeps full
  history (`SourceService.copy_text`); the texts page cannot change or remove a scene's text.
- **Jobs** (`jobs`, `backend/adapters/job_store.py`, `backend/services/job_runner.py`): claimed with
  `FOR UPDATE SKIP LOCKED` under a lease renewed during model calls; every write needs the lease;
  `LeaseLost` stops a worker quietly. The loop starts in the app lifespan; tests call
  `container.job_runner.run_pending()`. New work kinds register a handler with `runner.register`.
- **Studio** (`backend/services/studio_service.py`, `backend/api/v1/studio.py`): state, save, keep,
  discard, estimate, `scene_draft` and `scene_review` jobs, voice notes. Deterministic checks are in
  `backend/domain/drafting.py`. Provider resolution for requests and jobs is shared
  (`backend/services/provider_route.py`). `AIGateway.generate_text` was added for prose.
- **Frontend**: `StudioPage` at `/projects/:id/plan/:planId/write`, `StudioWork`, `VoiceDialog`,
  `lib/studio.ts`; "Write this scene" on each scene in the plan.
- **Browser tests that need a model** end in `.model.spec.ts` and run with
  `playwright.model.config.ts`, whose API uses `scripts/e2e/fake_model.py` (a local stand-in with
  set answers). `make e2e` runs both configs. The database engine now hides bound values
  (`hide_parameters=True`) so a failed statement cannot log prose.

Not done: a real provider has never drafted or checked a scene; no streaming; drafts are kept or
discarded whole; media is named, not sent; consistency profiles and suppressions.

## Phase 6: scenes back into the story (2026-10-17)

ADR-023 records the decisions; `plan/phase-6-todo.md` has the state. Alembic head is
`0035_scene_readings`.

- **Reading a scene** (`backend/services/scene_reading.py`, job `scene_read`): reads the exact
  version asked about with `IntakeWorkflow.read_passage`, only the paragraphs changed since the last
  reading (`backend/domain/reading.py`), and records the reading (`scene_readings`, forked with a
  what-if). Proposals go to review. `POST /plan/{id}/studio/readings`; status on the studio.
- **Plan card from the prose** (`backend/services/scene_plan_card.py`, job `scene_plan`): entries by
  id and name, of the right kind, with a quote; the author's plan wins; one reviewed plan change.
- **See it in the scene**: `GET /texts/passage` returns `scene_id`; the review screen links to the
  studio with `?at=start-end&version=…`, which selects the words.
- The stand-in model (`scripts/e2e/fake_model.py`) answers by schema name: drafts, reviews, readings
  and plan cards.

Not done: facts read from a scene do not take its story time.

## After Phase 7: open release-one items, first batch (2026-10-18)

The backlog's status lines were audited against the code on 2026-10-17 (`plan/10-backlog.md`).
This batch built items that need no model; their status lines say what is done and what is not.

- **Retention** (OPS-005): `make retention RETENTION_ARGS='--days 30 --dry-run'` deletes finished
  jobs, unreferenced kept contexts and notes on old versions, never what the author kept.
- **Readiness** (OPS-006): `/health/ready` is `ready`, `degraded` (200; the job worker, waiting work
  or the search store) or `unavailable` (503; a required store). Watchers of the old `degraded` for a
  required store must look for `unavailable`.
- **Picture metadata** (AST-006): uploads lose EXIF/XMP/comments without decoding
  (`backend/domain/image_metadata.py`); orientation is kept; unreadable files are refused.
- **Cached tokens** (CTX-015, migration 0036): recorded on usage and jobs from each provider's
  usage format; counted as part of the prompt, never added.
- **Dismissing findings** (CON-010, CON-016, migration 0037): a studio check or note can be
  dismissed with a reason, tied to the sentence it points at; it comes back when that changes.
  Forked with what-ifs.
- **Blocking rules** (CON-015): a rule can be set to block; it is evaluated inside the commit
  transaction; only complete evaluation blocks (ADR-019 amendment).
- **Archive v4** (CAN-010): what-ifs, conversations, scene readings and dismissals travel; v3 still
  restores; a `left_out` list says what is not carried.
- **Planning depth** (STY-002, STY-003, CTX-009): thread steps ("moves along"), a scene's want,
  obstacle and outcome, and comparing today's context with a kept draft's (ADR-018/021 amendments).

A read-only review of this batch found 1 high, 4 medium and 9 low issues; all were fixed with tests
(blocking rules now compare every violation, not the first 200, and are fast inside the commit;
approval checks the change's own line of the story; restores record each conversation and rule they
write; malformed archives are refused in plain words; JPEG cleaning keeps only what a decoder needs;
readiness numbers need the metrics token on a hosted site). Known limits: a restored what-if's
rebuilt story is checked against the archive's checksums but not compared with its own journal
(`canon_equivalence.verify` covers the main story only); removing a failed import also clears that
story's studio work (waiting drafts, voice note, dismissals), as it did before for drafts.

## Open release-one items, second batch (2026-10-18)

- **Story checks** (STY-007, STY-005): a reveal shown too early, a pay-off before its set-up, a reveal
  with no earlier hint; "Promises to the reader" (still owed / kept); scene conditions, required or
  preferred, checked against story time (`backend/domain/plan_checks.py`, `scene_conditions.py`).
- **Writing** (CTX-003, AST-EXP-018): typing @ in the studio and the texts editor offers entries
  (`components/MentionTextarea.tsx`, `lib/mentions.ts`); scenes show pictures chosen from the story's
  library (`POST /plan/{id}/pictures`, `GET /pictures`; links target the plan item, ADR-010 amendment).
- **Spending** (CON-009, AST-EXP-011, OPS-002; migration 0038): checking on its own (only when I ask /
  balanced / thorough, with a daily token allowance per scene); a budget per assistant conversation;
  every request that calls a model is estimated and, above the author's or workspace's threshold,
  refused with `409 needs_confirmation` until confirmed per request (`X-Confirm-Tokens` within 1.5x
  of the estimate). OpenRouter's reported cost is recorded; no prices are invented.
- **Operations** (OPS-004, OPS-003, OSS-005, SCN-003): runbooks in `plan/runbooks/`; scheduled backups
  (`deploy/hosted/prune-backups.sh`, systemd units; a backup counts only with a complete manifest);
  issue and pull request templates; one registry of every prompt (`backend/domain/prompts.py`) with
  snapshot tests (`tests/prompts/`, update with `uv run python -m tests.prompts.snapshots --update`,
  which refuses a changed prompt without a raised version).
- **The graph follows the story by itself**: `ProjectionWorker` (started with the app) delivers each
  story's outbox to the graph, with backoff for failing stories, a per-story advisory lock across
  processes, and a watermark that only moves forward. Found and fixed: the outbox read took the
  oldest 100 messages whether delivered or not, so a story past 100 commits never projected again.
  Readiness reports `graph_worker`.

A read-only review found six medium and many low issues; all were fixed with tests (spending checks
on every model call, one automatic check per save race, the picture library per line of the story
with no orphan uploads, mention picks that never delete words, manifests written last, and the
graph worker never starved by failing stories).

## Open release-one items, third batch (2026-10-18)

- **Character arcs** (STY-006): an arc plan item holds a path of named stages (`_path` in its
  details), each placed in a scene and optionally tied to a fact; checked without a model (order,
  the tied fact at that time, long gaps); shown on the character's page and the plan; the
  viewpoint's stage goes to drafting (`backend/domain/arcs.py`, `PUT /plan/{id}/path`).
- **What this change touches** (CON-017, CON-007): computed on read from plan refs, conditions,
  reveals, threads, rules, kept drafts' contexts and the passage index; affected / may be affected /
  not checked (`backend/services/impact_service.py`, `GET /mutations/{id}/impact`,
  `GET /entities/{id}/impact`).
- **Reader's guide** (QRY-002): a page in the app (`plan/:planId/guide`) that prints, a guide at an
  earlier version (`backend/services/story_at_version.py` replays history), and a Markdown export
  whose header binds line, version, position and viewpoint. No public share link.
- **Scene time** (EXT-007): facts a scene shows starting take its story time (only kinds that can
  take one); an old value ends at the scene, a later value is kept and bounds the new one
  (`read-scene` prompt; pasted text unchanged).
- **Check passes and audits** (CON-008, CON-011; migration 0039): the scene check is five passes
  (viewpoint and secrets, continuity, the plan, voice, clarity), each its own call and prompt; an
  audit runs chosen passes over a part of the plan as one job, resumable without re-running what
  finished (`backend/services/story_audit.py`, `/projects/:id/audits/:jobId`).

A read-only review found eight medium and many low issues; all were fixed with tests (an arc stage
never carries a secret into drafts or other passes; going back to an earlier place and scenes read
out of order are timed correctly; only facts shown changing take the scene's time; audits never
repeat finished passes and the results page polls lightly; impact reads kept contexts in one go).

## Open release-one items, fourth batch (2026-10-18)

- **Scoped name memory** (CTX-016, migration 0040): a name answer can hold everywhere, in one text,
  in one what-if, from or until a scene, or when seen by one character; the narrowest wins; reviewed
  examples kept with each answer; every name reader (text reading, scene reading, plan card, studio
  checks, context) resolves for its scene and line (`/projects/:id/names`, ADR-019 amendment).
- **Claims, trust and perception** (KNW-005, KNW-006, migration 0041): something said or narrated is
  recorded as a claim (an event), never a fact, and can be promoted through review; trust per
  character (and per speaker) and who perceives an event decide which beliefs are proposed, always
  as one reviewed proposal (`backend/domain/trust.py`).
- **Keep the safe ones for me** (AST-EXP-015, OSS-007, migration 0042): off by default; only additive,
  reversible kinds the author approved by hand three times; committed through the normal path with a
  decision naming the rule; listed with Undo under Review → Kept for you; switching off takes effect at
  once, including at commit time. Connected assistants' suggestions always wait.
- **Access keys and read-only MCP** (QRY-003, migration 0043, ADR-024): `nvlpat_` keys shown once,
  stored as SHA-256 digests, checked on every request (withdrawn when the member is removed), rate
  limited; `POST /mcp` speaks MCP (2026-07-28 and the 2025 versions) over the same services as the
  HTTP API; tools read the story (with an optional point of view and what-if); with the suggest scope,
  one tool creates a reviewed proposal of origin `machine`. Runbook `plan/runbooks/connected-assistants.md`.

A read-only security review found three medium and many low issues; all were fixed with tests
(removing a member withdraws their keys at once; switching a rule off can no longer race a commit;
point-of-view reads give only id, type and name; bodies are capped while read; machine origin is
reserved for the MCP path; malformed messages get proper errors). Migrations 0042/0043 were
renumbered before commit (no deployed database has them).

## Consistency engine, fifth batch (2026-10-18)

- **Continuity** (CON-003, CON-004; migration 0045): two places at once, journeys quicker than the
  author's travel table, age, places that do not exist yet or any more, who holds what, a scene after
  a death, conditions, and relationship changes off the page — on the timeline, in the plan and in the
  studio, warnings only (`backend/domain/continuity.py`). A whole unit of a coarse clock ("year 5")
  means some time in that unit; fractions are exact. A time that cannot be placed never makes a
  finding certain.
- **Rule engine** (CON-005, CAN-013): implications, incompatibilities, value ranges, belief checks,
  exceptions, and when a rule must hold (as recorded / every moment / at the end); old rules keep
  their meaning; a break that becomes certain is reported (and blocks if the rule blocks).
- **Knowledge** (KNW-002, CON-006, CON-001; migration 0044): how a character learned each thing
  (told, seen, worked out — `POST /knowledge/infer`), knowledge used before it was learned flagged in
  the studio and the plan, and checks kept as runs with open / dismissed / fixed and out-of-date
  ("Check history").
- **Listing policies** (CTX-002): the God Hand, link suggestions and reading use versioned listing
  policies (`backend/domain/bible_listing.py`) instead of fixed limits; reading proposes events (with a
  time only when the passage states one) and thread steps (one plan change per reading, refused if the
  author changed the scene's plan since); the reference world exercises every layer
  (`backend/seed/story.py`).

A read-only review found two high-for-blocking and several medium issues around uncertain times,
stale plan suggestions and reading; all were fixed with tests.

## Pictures, search generations and evaluations, sixth batch (2026-10-20)

- **Preparing pictures** (AST-003, AST-006, AST-008, OPS-007; migration 0046): each picture is
  decoded in a separate `python -I` process with memory, CPU and time limits (Pillow >= 12.3,
  PNG/JPEG/WebP only, header checked before decoding) into a 256 and a 1024 WebP copy with no
  metadata. A failure says why and can be tried again; a workspace storage limit; `make media`;
  `make retention` removes stray picture files after a grace period. Runbook
  `plan/runbooks/media-processing.md`. The limits are not a filesystem or network sandbox.
- **Search generations** (CTX-004, CTX-011, CTX-012; migration 0047, which moves the old 384-number
  rows into a legacy generation and drops `vector_documents`): a typed embedding port, a
  deterministic stand-in, an EmbeddingGemma 2 adapter behind the `embeddings` extra (never loaded
  here), build beside / switch / roll back (`make reindex*`), picture search with every gate in
  the same query before ranking. Runbook `plan/runbooks/search-index.md`.
- **Pictures for the assistant** (CTX-013, AST-004, AST-005, AST-007, CON-014; migration 0048): the
  picture page (`/pictures/:id`), "Describe this picture" with the cost first, suggestions to
  accept, correct or reject, "Suggest as a detail" (a reviewed proposal), send / use its
  description / leave out, secret and story-time picture links, and a "What the pictures show"
  check pass. Whether a route sees pictures is an operator setting (`AI_IMAGE_INPUT`). Runbook
  `plan/runbooks/picture-descriptions.md`.
- **Evaluations** (CON-012, CON-013, CTX-010, CTX-014, EXT-008, QLT-003): `make eval` writes
  `var/eval/report.md`; `make eval-model` (asks first, refuses in CI) and `make eval-media` with
  `EVAL_MEDIA_ARGS` are for the owner's real models. Deterministic checks: precision 0.95, recall
  0.91 on 73 labelled cases (before the fixes below). The review-bundle prototype is
  `backend/domain/review_bundles.py`.

Everything ran against the scripted model, the local stand-in and the stand-in embedder only. A
read-only review found five medium issues (pictures found by meaning ignoring "leave out" and
story time, a picture detail making a secret detail public, captions not reaching the index,
picture decoding quality) and three check defects the evaluation surfaced ("I" in dialogue, death
words, "64 years" in a range); all fixed with tests. Known limit: the legacy project transfer
(`project_transfer.py`, not wired into the app) stores files that picture cleanup would treat as
strays.

## Context layers, incremental checks, ready views and planning help, seventh batch (2026-10-21)

- **Richer context** (CTX-004–007, migration 0052): saved texts reach the passage index through a
  waiting list read by the background worker (about two seconds; `/texts-index` shows what is
  waiting); summaries and events of earlier scenes, the end of the scene before, and passages on
  the same thread; ranking by the author's importance mark, open continuity warnings, distance and
  near-duplicates, each item saying what moved it. Model summaries are suggestions until kept, and
  belong to one line of the story.
- **Incremental checks** (CON-001, CON-007, CON-013, CON-016; migration 0049): each check records
  digests of what it read and is worked out again only when they change (`check_cache`; the checks'
  version follows their source code). Findings can be accepted as intended or superseded;
  dismissals come back when the facts behind them change; advisory findings can be dismissed in
  Review; the continuity pass is sent in groups for long scenes, confirmed again at run time if it
  grew. Runbook `plan/runbooks/incremental-checks.md`.
- **Ready views and the picture library** (QRY-001, QRY-002, AST-002, AST-007, AST-008; migration
  0050): six views per line built from the change log with watermarks and replay verification
  (`make read-models-verify`), used only when verified and current; the reader's guide in parts
  (characters, places, timeline, recap); streamed picture uploads with a digest check, finding
  pictures by words, and deleting one picture's file (owners and admins, only when shown nowhere).
  Runbook `plan/runbooks/story-views.md`.
- **Planning help** (STY-003–006, SCN-007, CON-004, CON-014; migration 0051): the assistant suggests a
  scene's goal, obstacle and outcome, and bridge scenes for an unmet condition, all as reviewed
  plan changes; shared scales such as trust; prose checks for time words against the scene before,
  things another character holds or has used up, and eye or hair colour against accepted details.

A read-only review found no high issue; medium ones (picture delete safety, checks cached across
code changes, "check again" stuck, grouped pass spend and resume, the viewpoint warning not reaching
the model, what-if summaries, prose-check false alarms) were fixed with tests. Also fixed: archives of
stories with retired or deleted pictures failed to restore (older than this batch), and the passage
waiting list could hold a text for two minutes (a `SKIP LOCKED` subquery claiming more rows than
asked). Everything ran against the scripted model and stand-ins only.

## A model on your own computer, conversations, voice and audits, eighth batch (2026-10-21)

- **Local model connector** (SCN-002A, ADR-025, migration 0053): a standard-library program
  (`uv run python -m connector --site …`) connects Ollama, LM Studio or a llama.cpp server to a
  hosted or self-hosted site through an outbound HTTPS long poll; registration is the RFC 8628
  device flow confirmed by a workspace owner or admin; credentials are stored as digests, rotate
  daily with reuse detection, and are revoked on disconnect, member removal or demotion. When the
  chosen computer is off, the assistant stops; it never falls back to a cloud key. Runbook
  `plan/runbooks/local-connector.md`.
- **Conversations** (AST-EXP-005/007/013/017, migration 0054): "Start from your story" (`/premise`)
  asks open questions and suggests entries for review; merging two entries is one reviewed, undoable
  change; "Change anything" can change the plan and keeps one conversation per author and line
  ("Clear and start again" erases it); link suggestions record how many were kept; plan
  suggestions record whether they quote the author. Archives carry only the exporting author's
  threads.
- **Voice and audits** (CON-010/011/017/018, migration 0055): voice measures without a model and a
  "How it sounds" panel; correction plans from an audit; comparing a what-if with the main story;
  scheduled audits within a limit the author sets; turning a finding into a reviewed change; what
  a change touches now covers beliefs, events, pictures and studio findings. Runbook
  `plan/runbooks/scheduled-audits.md`.
- **Operations** (OPS-004, AST-006, EXT-001): `backend/devtools/rekey.py` re-locks stored keys
  under a new key (`CREDENTIAL_PREVIOUS_KEKS` during rotation); the picture worker adds seccomp and
  Landlock where the kernel allows; readings suggest moves, possession, status and relationship
  changes, and new threads, all for review.

A read-only review found no high issue; medium ones were fixed with tests: a quadratic voice regex
that could freeze the server, connector words left after a crash, code-guess limits, credential
reuse, unlimited long polls, archive privacy of threads, three merge data-loss cases, scheduled
audit spend, and untimed readings ending facts. Decided by the owner on 2026-10-21: what-ifs stay
"set aside only", with no discard action, so their kept conversations and scheduled audits remain
until the story is deleted (a set-aside what-if's scheduled audit does not run; ADR-020 amendment). Everything ran against the scripted
model and stand-ins only; no real Ollama, LM Studio or llama.cpp server has been tried.

## Remainders, scope decisions and a docs truth audit, ninth batch (2026-10-21)

- **Owner decisions (2026-10-21):** audio and video, and Google Docs / DOCX / LaTeX import, are
  deferred past release one; what-ifs stay "set aside" only (no discard). Recorded in
  `plan/11-decisions-and-questions.md`, ADR-010, ADR-020, docs 15 and 17, and the backlog.
- **Context** (CTX-004/005/006/006A/007/012, OPS-007; migration 0056): material from scenes later
  in the story's time is left out by default (policy `scene-draft/3`) or ranked lower; story-time
  decay alongside reading order; summaries of arcs and beats (kept, or suggested and then kept);
  the endings of other recent scenes; waiting-list and media-index lag in health; an optional
  scheduled search-index catch-up (`SEARCH_INDEX_CATCH_UP_MINUTES`).
- **Checks** (CON-001/004/013/016/018, SCN-007, STY-003/004; migration 0057): height, marks and age
  as written against the story bible; quantities a character spends; dismissals for a character
  or a stretch of scenes; dismissing voice notes and each speaker's dialogue against their own;
  timings per check; more passes sent in parts; plan drafting sets intent only from the author's
  words; bridges for preferred conditions.
- **Media and data** (AST-001/002/008, QRY-001, EXT-001/007, AST-EXP-017; migration 0058): picture
  links that apply between story times and can pin a version; a two-step upload; deleting a
  picture removes what a model said about it; a page for the ready views' state with a one-at-a-time
  check; a pasted text can be read as a scene's writing; Change anything can change an entry's type
  or a fact's kind, whole or not at all; merges rewrite what characters said.
- **Docs truth audit:** every backlog status line was checked against the code and rewritten as one
  current paragraph; README, AGENTS.md, CONTRIBUTING, SECURITY, api-surface, plan/README and
  `.env.example` brought up to date.

A read-only review found one high issue (the quantity check was quadratic on the event loop; any
writer could stall the server) and several medium ones (mark and speaker checks slow, height false
alarms, retype changes cut part-way, pinned picture versions ignored by checks, the scene before
skipping the story-time filter, the history check without a lock); all were fixed with tests, and
scene checks now run off the event loop. Consistency evaluation: 161 cases, precision 1.0, recall
0.892. Everything ran against the scripted model and stand-ins only.

## Streaming, send preview, picture parts, more checks and audit notices, tenth batch (2026-10-29)

- **Drafting and models** (SCN-002/002A, CTX-013; migration 0059): drafts stream to the studio
  over `GET /jobs/{job}/stream` (server-sent events, capped, with the
  chunks stored append-only); what each model can take comes from the provider's list, OpenRouter's public list
  (`AI_MODEL_LIST`) and the operator's table (`AI_MODEL_CAPABILITIES`); the send preview and its
  limits follow the workspace's chosen model, and the request carries an `X-Confirm-Sending`
  digest of what was shown.
- **Pictures** (AST-003/005/007; migration 0060): marked parts of a picture linked to entries,
  refreshed as a job (at most 50 parts per version); words in a picture as `text_in_picture`
  observations in the search index; notes can point at a part of a picture.
- **Checks** (CON-003/004/008/009/011/014/016/017/018, STY-004/005/006, SCN-007; migration 0061):
  motivation, physical and causal, and world-rule passes; voice notes that escalate; arc stage
  suggestions; knows/seen/heard scene conditions; gap bridges; journeys and deaths read from prose
  (`backend/domain/movement.py`, linear time, off the event loop). Automatic checks and audits ask
  first, or leave out the pictures pass.
- **Audits and impact** (migration 0062): a notice when an audit finishes; a what-if lists the
  scenes it changes; dismissals travel in archives; Change impact previews a what-if's scratch
  changes with a graph.
- **Release** (OSS-005): `make release` / `make release-check` (reproducible artefacts in
  `dist/release/`; nothing tagged or published); `pyproject.toml` builds with `uv_build`.

A read-only review found one high issue (the send preview and limits used the site's default model
instead of the workspace's choice) and several medium ones (automatic checks could send pictures
without asking, limits read before capabilities were refreshed, quadratic travel and speech
scanning, unbounded streams, region refresh on the request path); all were fixed with tests.
Everything ran against the scripted model and the local stand-in only; no real provider has
streamed a draft or read a picture region.

Added 2026-10-29 (owner decision): `EMBEDDING_PROVIDER=api` makes search vectors with an
OpenAI-compatible embeddings service, so a VPS runs no model at all (chat, checks and picture
reading already went to cloud providers). Text only; every passage is sent to that service under
the operator's key, and `GET /ai/search` plus the Assistant page's "Search by meaning" card tell
authors where their writing goes. The Docker image still leaves out the `embeddings` extra, so
without this option a deployed site searches by words. ADR-010 has the decision; the runbook
`plan/runbooks/search-index.md` the steps. Tested against a stand-in service only; no real
embedding service has been called.

## Site search setup on a page (2026-10-30)

The site's search-by-meaning setup moved from operator-only settings to a page a platform
administrator fills in, following the workspace AI-key pattern. `backend/domain/embedding_providers.py`
is the provider table (OpenAI, Google Gemini, Mistral, Voyage, OpenRouter, "your own"); a single
row in `site_search_setup` (migration `0063`) holds the choice with a version bumped on every
change, and `site_search_secrets` holds the key sealed exactly like workspace credentials
(`CREDENTIAL_KEK`), included in the OPS-004 re-lock and verify tooling (`make rekey`). Sessions
gained `authenticated_at` (carried unchanged through rotation), so "signed in recently" = within 15
minutes. `backend/adapters/embedding_site.py::SiteEmbedder` is a delegating embedder that wraps the
start-up embedder and re-reads the stored setup when its version changes (at most every 10 s, at
once after a save in this process), so the vector store, the index service, `/ai/search`,
diagnostics and the CLI all follow the setup without a restart; precedence is server `api` settings
(with a key or a local address) → page setup → the server's own model → off. Saving embeds the fixed
word `lighthouse` to check, refuses a bad key/model/size with distinct codes, keeps an unreachable
service as `verified: false`, and never returns the key. Routes live in
`backend/api/v1/site_search.py` (platform admins; CSRF on writes; recent sign-in for PUT/DELETE);
`/auth/me` now reports `platform_admin`. There is only workspace-scoped audit storage, so a setup
change is recorded as the `site.search_setup_changed` log event, not an audit row.

Review and finish (2026-10-30). The work was split across DeepSeek Harness agents (backend, page,
chat-key check; then review fixes and typed answers), each in its own worktree, reviewed here before
merging. Fixes from the review: the key hint is the saved key's last four characters; store, embedder
and check calls run off the event loop (check timeout 15 s); a database error during the periodic
re-read keeps the current embedder and logs `site.search_setup_unreadable` once; a key sealed for
another provider or host is never reused, and is dropped when a local address replaces it; a 2xx
answer that is not an embedding service is `400 not_a_service`; a failed build logs
`site.search_build_failed`. "Recent sign-in" now means something under single sign-on:
`/auth/login?fresh=1` sends `prompt=login` and `max_age=0`, the callback requires `auth_time` within
5 minutes (else `/?signin=stale`, explained on the home page), and the session's `authenticated_at`
is the provider's `auth_time`, not the callback time. `return_to` takes same-site paths only (no
`//`, `\`, scheme, whitespace or control characters). The routes have typed answers, so the page
uses generated types. Workspace chat keys are also checked with their provider before they are kept
(`verified`: true/false/null; Anthropic with `x-api-key`). Without `CREDENTIAL_KEK` only the key
field is locked, so a local service can still be set. Checked against stand-ins only: the stand-in
model server now answers `/embeddings`, and `e2e/site-settings.model.spec.ts` sets, checks, builds,
uses and gives up a service end to end. No real embedding provider or identity provider has been
tried with this.
