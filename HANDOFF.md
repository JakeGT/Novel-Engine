# Novel Engine — engineering handoff

This document is for the engineer or agent picking up Novel Engine. It summarizes what the project
is, its current state, how to run it, the architecture and invariants you must preserve, and exactly
what remains. Read `AGENTS.md` first (the operating contract) and the plans under `plan/`.

- Repo: `https://github.com/JakeGT/Novel-Engine`
- Branch: `phase-2-closure`, local only (not pushed). It branches from `main` at `827a9c2` and holds
  the Phase 2 closure work as a series of commits; `main` and `origin/main` are unchanged.
- Date: 2026-10-09

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
| Phase 2 — trustworthy canon | **Implementation complete, awaiting owner acceptance.** Every code-completable 2B–2L item is done; the open items are owner/operational (see §11). See `plan/phase-2-todo.md` and `plan/phase-2-evidence.md`. |
| Phase 2.5 | Next: text-first author workflow probe (manuscript import, extraction into proposals, story-rule authoring). See `plan/09-delivery-roadmap.md`. |
| Phase 3+ | Planned (branch forks/overlays, world/narrative time, POV filtering, planning/prose). See `plan/09-delivery-roadmap.md`. |

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

## 4. Architecture you must preserve (ADR-012/013, proposed ADR-014/015/016)

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
- Auth: `AUTH_MODE` (`local|hosted`), `AUTH_LOCAL_USER_ID`, `AUTH_LOCAL_WORKSPACE_ID`,
  `OIDC_ISSUER/CLIENT_ID/CLIENT_SECRET/REDIRECT_URI/POST_LOGOUT_REDIRECT_URI/SCOPES`,
  `SESSION_COOKIE_NAME`, `SESSION_TTL_SECONDS`, `SESSION_ROTATION_SECONDS`, `CSRF_COOKIE_NAME`,
  `PLATFORM_ADMIN_USER_IDS`, `ALLOW_WORKSPACE_SELF_CREATION`.
- Credentials/limits: `CREDENTIAL_KEK`, `CREDENTIAL_KEK_VERSION`, `CREDENTIAL_PREVIOUS_KEKS`,
  `PROVIDER_PER_JOB_TOKEN_LIMIT`, `PROVIDER_PER_JOB_COST_LIMIT`, `PROVIDER_PERIOD_TOKEN_LIMIT`,
  `PROVIDER_PERIOD_COST_LIMIT`, `PROVIDER_PERIOD_SECONDS`.

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

- Alembic (PostgreSQL) head: **`0022_archive_imports`** (… → `0018_commit_integrity` →
  `0019_review_workflow` → `0020_entity_identity` → `0021_media_lifecycle` →
  `0022_archive_imports`).
- Neo4j ordered migrations: `0001_initial_graph_schema`, `0002_entity_scope_identity`,
  `0003_canon_scope_identity`, `0004_canon_projection_labels` (checksums in
  `plan/phase-2-evidence.md`).
- Migrations run explicitly via `make migrate` / `make migrate-verify`; startup verifies read-only.
- Upgrade notes (duplicate slugs cleared by `0020`; projection rebuilt after Neo4j `0004`) are in
  `plan/phase-2-evidence.md`.

## 11. What remains

**Phase 2 has no remaining code work.** What is open is owner or operational:

1. Accept or reject `plan/phase-1-evidence.md`.
2. Push `phase-2-closure` and run the first clean-checkout hosted CI workflow. The `e2e-browser`
   job and the API-drift step were added in this work and have never run in hosted CI, so expect to
   fix environment details there.
3. Run, or time-box, the clean-host install and coordinated PostgreSQL/Neo4j/object-store
   backup/restore rehearsal.
4. Perform the live Authentik browser round trip.
5. Accept, amend, or reject ADR-014, ADR-015, and ADR-016 (they are implemented and `Proposed`).
6. Accept `plan/phase-2-evidence.md` and authorize the next phase.

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
