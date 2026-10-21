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

## Status honesty

Do not describe planned work as built. When you change a capability, update its status line in
`plan/10-backlog.md` (one current paragraph: what is built, what is not) and its phase todo file.
Say plainly what has only been tested against the scripted or stand-in model.
