# Contributing

Thank you for helping. A few things keep Novel Engine trustworthy for the people who write with it.

## Before you change code

- Read [AGENTS.md](AGENTS.md) (the working rules, for people and coding agents alike) and
  [plan/README.md](plan/README.md) (what is built, what is planned, and why).
- Larger changes start from the plan: an architecture decision record in `plan/decisions/` when a
  decision is involved, and the phase's todo file for its state.

## Rules that are not negotiable

- **The assistant never changes a story on its own.** Every model output becomes a proposal the
  author reviews.
- **An author's words never reach logs, error reports or exceptions' messages.**
- **Every capability works without a model**, by hand, and has a test of that path.
- **No secrets, `.env` files, databases or manuscript text in commits.**
- **Plain words on screen.** Interface work follows the design rules the project uses (see
  `plan/21-intuitive-design-rules.md`) and is reviewed from screenshots, not only tests.

## Running it

```bash
cp .env.example .env
make install-dev && make frontend-install
make infra-up && make migrate && make seed
make api                    # the backend
pnpm --dir frontend dev     # the frontend
```

## Checks

`make check` must pass before a pull request: lockfile, lint, formatting, secret scan, types, the
backend suite, the generated API types, and the frontend's lint, types, tests and build.
`make test-integration` and `make e2e` need the local services (see the README). A change to the
API updates `tests/contract/openapi_snapshot.json` and `frontend/src/api/schema.ts`
(`pnpm --dir frontend run gen:api`); a change to the database adds an Alembic migration with a
downgrade that never deletes an author's words; a change to a prompt's wording or answer shape
raises its version in `backend/domain/prompts.py` and updates the snapshots
(`uv run python -m tests.prompts.snapshots --update`).

## Releases

`make release` builds a reproducible set of release files from one commit into
`dist/release/novel-engine-<version>/`: the source tarball with the pinned lock files, the backend
wheel and sdist, the built frontend, CycloneDX bills of materials for Python and the frontend,
`BUILDINFO.json` and `SHA256SUMS` (check with `cd dist/release/novel-engine-<version> && sha256sum -c
SHA256SUMS`). It builds `HEAD` unless `RELEASE_ARGS="--ref <commit or tag>"` says otherwise;
`RELEASE_ARGS="--worktree"` tries uncommitted work, says so in `BUILDINFO.json` and writes to
`dist/release/novel-engine-<version>-worktree/` (that is not a release). A `--ref` is resolved to
its commit first; one starting with `-` is refused. `make release-check` builds twice and fails if
any byte differs. Every time written is fixed: the source and frontend tarballs, the bills of
materials and `BUILDINFO.json` carry the commit's time (`SOURCE_DATE_EPOCH`); `uv build` stamps the
wheel's files 1980-01-01 (the earliest a zip can hold) and the sdist's 1970-01-01. `VITE_*` and
`NODE_ENV` settings in your shell are cleared for the frontend build. So the same commit built with
the same tool versions (recorded in `BUILDINFO.json`) gives the same bytes. Paths marked
`export-ignore` in `.gitattributes` (the `.agent/` folder) and local agent-tool files are never
shipped. Nothing is tagged, signed or published by these commands; that is the maintainer's step.
See the ADR-008 amendment.

## Status honesty

Do not describe planned work as built. When you change a capability, update its status line in
`plan/10-backlog.md` (one current paragraph: what is built, what is not) and its phase todo file.
Say plainly what has only been tested against the scripted or stand-in model.
