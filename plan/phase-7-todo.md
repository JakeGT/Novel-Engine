# Phase 7 implementation todo: release hardening

- Status: In progress (2026-10-17). Code-side items done; the remaining items are the owner's.
- Scope: `09-delivery-roadmap.md` Phase 7; release gates in `08-testing-and-quality.md`;
  `07-security-and-operations.md`.

## What exists already

- CI (`.github/workflows/foundation.yml`): secret scan, lockfile check, ruff, mypy, `pip-audit`,
  unit/contract tests, integration tests on PostgreSQL and Neo4j, the generated-types drift check,
  frontend lint/typecheck/tests/build, and the browser tests. The first hosted CI run is still an
  owner item.
- Backups for the hosted profile: `deploy/hosted/backup.sh` (PostgreSQL dump and stored objects,
  with a checksum manifest). Project archives: `make archive` (export, import, inspect).
- Migration tests: Phase 1 and pre-review data upgraded in place; Phase 2 migrations rolled back and
  forward (`tests/integration/test_phase_upgrade_migrations.py`).
- Accessibility: axe checks inside each browser test; one keyboard-only browser test.
- Diagnostics: `make diagnostics` (redacted support bundle).

## Work

- [x] Restore for the hosted profile: `deploy/hosted/restore.sh` checks every file against the
      manifest before touching anything (a damaged or incomplete backup is refused; tested locally),
      asks before replacing, restores the database and objects, and prints the graph rebuild; a
      written restore drill in `deploy/hosted/README.md`. Not run against a live stack here.
- [x] Migration tests for this release's migrations (0032–0035,
      `tests/integration/test_release_migrations.py`): writing from before survives the upgrade;
      roll back and forward twice. Found and fixed: rolling 0034 back failed when a scene existed;
      it now keeps the scene's words as an ordinary text.
- [x] Frontend dependency audit in CI and `make audit` (`pnpm audit --prod --audit-level high`), next
      to `pip-audit`. No known vulnerabilities on 2026-10-17.
- [x] CI runs the browser tests that need a model (`playwright.model.config.ts`).
- [x] An accessibility sweep (`e2e/accessibility-sweep.spec.ts`): every page of a story with something
      on it, axe in light and dark; all pass. It runs after the other browser tests (a dependent
      Playwright project), since the first workflow test needs a brand-new story. A second test opens,
      writes and saves a scene with the keyboard alone.
- [x] A local model route: a self-hosted install points `AI_BASE_URL` at Ollama or LM Studio (no key
      needed; both enforce the answer's shape, others are asked in words); documented in the README;
      `diagnostics --check-ai` asks two tiny questions with no story text and reports each by kind.
      Not tried against a running local server here.
- [x] Repository documents: `SECURITY.md`, `CONTRIBUTING.md`, `CHANGELOG.md` (release and upgrade
      notes); README limitations and `AGENTS.md` maturity brought up to date.
- [x] Recovery: a page that stops working shows "This page stopped working", that nothing saved is
      affected, and two ways on, inside the project's frame (`components/PageError.tsx`). Failed
      background work never changes the author's words (ADR-022).

## Owner decisions and owner-side items (not code)

- The open-source licence (none is in the repository; choosing one is the owner's decision).
- Reinstalling Docker Compose on the host, and the deploy itself (the hosted containers were removed;
  volumes are intact).
- The first hosted CI run and a restore drill on a clean host.
- Turning on GitHub private vulnerability reporting, which `SECURITY.md` points to.
- A real provider run of the release-one workflow (drafting, checks, reading), which the gates require.

## Exit criteria (roadmap)

- Release quality gates in `08-testing-and-quality.md` pass.
- Recovery drills and upgrade tests pass.
- Known data-loss/security risks are resolved or explicitly accepted.
- Beta users can complete the workflow without developer assistance.
- The self-hosted profile operates without a project-operated cloud dependency.
