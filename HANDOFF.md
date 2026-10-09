# Novel Engine — engineering handoff

This document is for the engineer or agent picking up Novel Engine. It summarizes what the project
is, its current state, how to run it, the architecture and invariants you must preserve, and exactly
what remains. Read `AGENTS.md` first (the operating contract) and the plans under `plan/`.

- Repo: `https://github.com/JakeGT/Novel-Engine`
- Branch at audit: `main` (in sync with `origin/main` at commit `827a9c2`)
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
| Phase 2 — trustworthy canon | **In progress.** Core backend work across 2B–2L is committed on PostgreSQL-canonical authority; 2A owner/operational exits and named review/UI/archive deferrals remain. See `plan/phase-2-todo.md` and `plan/phase-2-evidence.md`. |
| Phase 3+ | Planned (branch forks/overlays, world/narrative time, POV filtering, planning/prose). See `plan/09-delivery-roadmap.md`. |

**Not production-ready.** Billing, platform-funded inference, public signup, and real-time
collaboration do not exist by design. Do not describe a deferred capability as implemented
(`AGENTS.md` mandate).

**Recorded test baseline:** 426 hermetic + 61 integration tests pass; `make check` green;
`mypy backend` clean (121 files); frontend lint/typecheck/test/build green. Restricted coding-agent
sandboxes may hang on Starlette's synchronous `TestClient`; see `AGENTS.md` before diagnosing that
as an application failure.

## 3. Repository map

| Path | Responsibility |
| --- | --- |
| `backend/api/` | HTTP transport. `v1/` is canonical; legacy `/api` aliases were removed in 1A. |
| `backend/services/` | Application workflows (auth, tenancy, schema, mutation/commit, canon entity/assertion/history, media, archive, credentials, usage, projection, outbox, character). |
| `backend/domain/` | Storage-neutral domain: `canon/` (entities/propositions/facts/beliefs/events/revisions/provenance/registry/ids), `identity.py`, `tenancy.py`, `journal.py`, `operations.py`, `commit.py`, `projection.py`, `history.py`, `media.py`, `credentials.py`, `usage.py`, `jobs.py`, `schemas.py`, `schema_defs.py`, `validation.py`, `errors.py`. |
| `backend/adapters/` | Concrete ports: Neo4j (`graph.py`, `canon_writer.py`), pgvector (`vector.py`), PostgreSQL (`postgres.py`, `canon_repository.py`, `journal.py`, `identity_store.py`, `tenancy_store.py`, `schema_store.py`, `media_store.py`, `credentials.py`, `usage.py`, `rate_limit.py`, `purge.py`), OIDC (`oidc.py`), object store (`objects.py`), AI (`ai.py`), embeddings (`embedding.py`), filesystem artifacts. |
| `backend/ports.py` | Protocol definitions for every external dependency. |
| `backend/container.py` | Dependency container built from `Settings`; owns adapters and lifecycle. |
| `backend/main.py` | `create_app(settings, container)` + ASGI export; lifespan owns resources. |
| `backend/core/` | Settings, async DB engine/session, logging + redaction, security (tokens/PKCE), crypto (envelope encryption), observability (Sentry). |
| `backend/graph/` | Neo4j schema, manager, traversal, migrations (ordered runner), transitional vector search. |
| `backend/seed/` | Deterministic reference-world manifest + tenancy + schema seed. |
| `backend/devtools/` | Guarded CLIs: seed, reset, diagnostics, retention, reconcile, project, watch, archive, verify. |
| `migrations/` | Alembic environment + PostgreSQL revisions. |
| `frontend/` | TypeScript React app (Vite), OpenAPI-generated client, vitest. |
| `scripts/manual/`, `scripts/ci/` | Non-hermetic manual probes; secret scanner. |
| `tests/{unit,application,contract,integration,e2e}` | Test suites. |
| `plan/` | Product, architecture, delivery, decisions (`plan/decisions/`), per-phase evidence. |

## 4. Architecture you must preserve (ADR-012/013)

Canonical authority by datum (see `plan/decisions/012-postgres-canon-and-replayable-graph.md`):

- **PostgreSQL** owns users/sessions/workspaces/projects/memberships/security-audit; project
  schema definitions and immutable schema versions; and the **mutation workflow, operation
  mutation review decisions, commit envelopes, attempts, branch-head reservation/finalization, outbox, and
  normalized/versioned canon** (entities, facts, beliefs, events, provenance, record versions).
- **Neo4j** owns accepted-graph query state as a rebuildable projection, bounded by the committed
  PostgreSQL branch head, with durable watermarks.
- **Object storage** owns immutable media bytes; PostgreSQL owns lifecycle metadata.
- **pgvector/search/summaries** are rebuildable projections.

**Canonical commit is one PostgreSQL transaction** (`PostgresCanonRepository.apply_commit`):
lock head → run the authoritative validator in-transaction → reserve → append immutable envelope →
apply normalized canon + record versions → advance head + revision → append idempotent outbox.
Success is established only here and **never depends on Neo4j**. Graph projection is a separate,
replayable consumer (`GraphProjectionService` + `OutboxConsumer`); a projection failure produces
visible lag, never canon corruption.

`backend/domain/validation.py::AuthoritativeValidator` runs inside the commit transaction and is
non-bypassable: any repository write with an invalid operation rolls back. Findings carry a
severity (`integrity` / `blocking_rule` / `advisory`); built-in integrity is permanently blocking.
Author-rule publication is Phase 2.5 (ADR-013,
`plan/decisions/013-declarative-story-invariants.md`).

Other invariants:
- One root branch per project; monotonic system revisions; optimistic `expected_revision`/`ETag`;
  at most one active reservation per branch; a mutation set yields at most one immutable envelope
  (migration `0018_commit_integrity`).
- Objective facts and character beliefs are distinct types; belief truth agreement is **derived**,
  never stored (`Belief` has no `is_true`).
- Workspace/project scope is mandatory at every durable/derived boundary; cross-project edges and
  references are refused.
- Project schema extensions are a bounded declarative meta-schema compiled to strict Pydantic +
  JSON Schema (no user code/regex/Cypher); published versions are immutable; compatibility is
  classified `additive`/`migration_required`/`forbidden`.
- Hosted BYOK: OpenRouter keys are envelope-encrypted (AES-256-GCM, KEK + per-record DEK,
  `CREDENTIAL_PREVIOUS_KEKS` for rotation) and only the authorized worker path resolves plaintext.
- Generic graph mutation APIs are local/admin-only escape hatches; hosted mode disables bypass
  writes. Product writes go through the mutation service.

## 5. Local setup

Prerequisites: Python 3.13, Node.js 24 LTS, `uv`, pnpm 11, Docker + Compose. `.python-version` and
`.nvmrc` are authoritative.

```bash
cp .env.example .env            # then fill credentials (see §6)
make install-dev                # uv; base install excludes sentence-transformers
make frontend-install           # frozen pnpm install
make infra-up                   # PostgreSQL/pgvector + Neo4j (waits for healthy)
make migrate                    # Alembic + Neo4j migrations
make seed                       # deterministic reference world (idempotent)
make api                        # FastAPI on http://localhost:8000
pnpm --dir frontend run dev     # frontend (Vite dev proxy to :8000)
```

Optional local semantic embeddings (`sentence-transformers`) are an extra:
`uv sync --locked --extra embeddings`. The base install and CI run without it and degrade
gracefully.

Useful one-off commands: `make verify` (canonical consistency), `make project PROJECT_ARGS=--lag`
(projection lag), `make watch WATCH_ARGS=--interval 5` (drain outbox), `make archive
ARCHIVE_ARGS=--export /tmp/project.json`, `make diagnostics`, `make retention`, `make reset-dev`
(guarded; refuses production or non-local stores).

## 6. Environment variables

All variables are documented in `.env.example`; production rejects unsafe defaults. Never commit
`.env` or real secrets. Key groups:

- App: `PROJECT_NAME`, `PROJECT_VERSION`, `APP_ENV` (`development|test|production`), `LOG_LEVEL`.
- AI: `OPENAI_API_KEY`, `AI_MODEL`, `AI_PROVIDER`, `AI_BASE_URL` (hosted BYOK uses OpenRouter; the
  per-request credential/base_url come from the credential vault).
- PostgreSQL: `POSTGRES_HOST/PORT/USER/PASSWORD/DB`, pool settings.
- Embeddings/objects: `EMBEDDING_MODEL_NAME/DIMENSION`, `CUSTOM_SCHEMA_PATH` (legacy local only),
  `ENABLE_DEBUG_ARTIFACTS`, `ARTIFACT_STORE_DIR` (debug), `OBJECT_STORE_DIR` (product object store).
- Telemetry: `SENTRY_DSN_BACKEND`, `SENTRY_DSN_WEB`, `SENTRY_TRACES_SAMPLE_RATE` (opt-in; scrubbed).
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
`frontend-typecheck`, `frontend-test`, `frontend-build`, `check`.

Use `uv add`/`uv remove` (Python) and `pnpm --dir frontend add/remove` (frontend); refresh lock
files in the same change.

## 8. Testing

- `make test` — hermetic unit/application/contract (no services, network, keys, model downloads, or
  persistent writes). 426 tests.
- `make test-integration` — disposable PostgreSQL/pgvector + `neo4j-test` (start with
  `docker compose --profile test up -d --wait neo4j-test`). 61 tests.
- Frontend: `make frontend-api-check frontend-lint frontend-typecheck frontend-test frontend-build`
  (generated-contract drift guard plus vitest component test).
- Mark service-dependent tests `integration` and paid-model tests `llm`. Live LLM tests are
  excluded from ordinary CI.
- Last recorded Phase 1 coverage baseline was ~71%; remeasure it before Phase 2 exit.

## 9. API surface (canonical `/api/v1`)

- Project canon: `/projects/{project_id}/entities` (list/search/read/create/edit/retire),
  `/facts`, `/beliefs` (+ `/truth-agreement`), `/events`.
- Schema versions: `/projects/{project_id}/schemas` (list/validate/draft/publish).
- History/provenance: `/revisions`, `/revisions/{commit_id}`, `/revisions/{from}/diff/{to}`,
  `/{entities|facts|beliefs|events}/{id}/history`, `/*/{id}/at-revision/{revision}`, `/tags`,
  `/reverts/preview`, `/reverts`.
- Media identity/links: `/assets`, `/assets/{id}/retire`, `/assets/{id}/versions`,
  `/assets/{id}/versions/{version_id}/retire`, `/assets/{id}/links`, `/links/{id}/retire`.
- Projection: `/projects/{project_id}/projection` (head/applied/lag/status/last_error/failed).
- Tenancy: `/workspaces`, `/workspaces/{id}/members|projects|invitations`, `/workspaces/{id}/ownership`,
  `/invitations/accept`.
- Credentials/usage and auth (`/auth/login|callback|me|logout`) as above.

`tests/contract/openapi_snapshot.json` is the committed contract; regenerate with
`python -c "import json;from backend.main import create_app;open('tests/contract/openapi_snapshot.json','w').write(json.dumps(create_app().openapi(),indent=2,sort_keys=True)+'\n')"`.
The frontend client is generated from it (`pnpm --dir frontend run gen:api`).

## 10. Data & migrations

- Alembic (PostgreSQL) head: **`0018_commit_integrity`** (chain: `0001_initial` → … → `0012_schema_versions`
  → `0013_canon_journal` → `0014_canon_storage` → `0015_outbox_delivery` → `0016_canon_tags` →
  `0017_media` → `0018_commit_integrity`).
- Neo4j ordered migrations: `0001_initial_graph_schema`, `0002_entity_scope_identity`,
  `0003_canon_scope_identity` (checksums recorded in `plan/phase-2-evidence.md`).
- Migrations run explicitly via `make migrate` / `make migrate-verify`; startup verifies read-only.

## 11. What remains (Phase 2 deferrals)

Recorded in `plan/phase-2-todo.md` and `plan/phase-2-evidence.md`. Completable work:

- **2E:** mutation-set state-machine transitions (actor/time/reason); per-operation accept/edit/reject
  decisions (edit-as-new-revision); a distinct review gate for destructive/high-impact author
  operations. Commit logs exist, but Sentry/Grafana metrics and spans remain 2L work.
- **2F:** name-collision/slug/alias/external-identifier policy; port the guided character workflow
  onto the mutation path (currently targets the generic graph port).
- **2G:** expand the reference world (truths/secret/false belief/event/constraints); safe Neo4j
  predicate projections + rebuild proof; extra cardinality/false-belief/retired-reference tests.
- **2H:** generalized provenance `source_artefact_id`; explicit history/provenance authz/redaction
  tests; dependency-aware revert conflicts and manual inverses for edit operations.
- **2I:** immutable asset/version lifecycle-change audit, a PostgreSQL media integration test, and a
  reference-world media fixture.
- **2J:** frontend filters/edit forms; character view; schema publish/compatibility view; mutation
  review UI; provenance/asset panels; accessibility automation; end-to-end browser test.
- **2K:** bounded v1 archive reader/upgrader; import-status/visibility ledger; durable security-audit
  records and application-level owner authorization; derived-index rebuild/query-equivalence proof;
  real PostgreSQL+Neo4j clean-environment round-trip test.
- **2L:** property/state-machine tests; colliding-ID reference fixtures; Phase-1→2 migration tests;
  Sentry commit metrics; performance budgets; invariant-classification tests (author rules).

External / owner-gated (not code): owner acceptance of Phase 2 evidence; first clean-checkout hosted
CI run; clean-host + coordinated backup/restore rehearsal; live Authentik browser round-trip.

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
- Structured logs are redacted by default; never log canon bodies, tokens, or provider raw errors.

## 13. Suggested next steps

1. **Phase 2 closure:** finish the bounded deferrals above (especially 2E review semantics, 2F
   character workflow, and 2J review/schema UI), then obtain owner acceptance and run the clean-host
   rehearsal.
2. **Phase 3 orientation:** branch forks/overlays/merges, world/narrative-time evaluation, and
   POV-knowledge filtering build on the same canon boundary. The planned query-store evaluation gate
   (PostgreSQL vs Neo4j for branch/time/POV queries) should be settled before adding a second query
   layer — see `plan/phase-2-todo.md` and ADR-012.
3. **Phase 2.5:** author story-invariant authoring/runs, scene contracts, suppressions, and the
   evaluator seam established by ADR-013.

## 14. Reference documents

- `AGENTS.md` — operating contract.
- `plan/README.md` — index of all plans.
- `plan/decisions/` — ADRs 001–013 (003 storage, 004 branches/schema, 005 POV/consistency, 007
  foundation tooling, 009 identity/tenancy, 010 media boundary, 011/012 canon authority + commit
  saga, 013 invariants).
- `plan/phase-0-evidence.md`, `plan/phase-1-evidence.md`, `plan/phase-2-evidence.md` — evidence.
- `plan/phase-2-todo.md`, `plan/phase-2-baseline.md`, `plan/phase-2-glossary.md` — Phase 2 contract.
- `plan/12-current-gap-analysis.md` — point-in-time map to the target design.
