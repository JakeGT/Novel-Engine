## What this changes

<!-- In plain words: what someone using Novel Engine will notice, and why. Link the backlog
item (for example SCN-003) or the decision record in plan/decisions/ if there is one. -->

## How it was tested

<!-- Which tests were added or changed, and what you checked by hand. Say plainly what has only
run against the scripted model or the local stand-in, never a real provider. -->

## Checklist

See [CONTRIBUTING.md](../CONTRIBUTING.md) for why each of these matters.

- [ ] Words on screen are plain: no internal terms, and the change follows
      `plan/21-intuitive-design-rules.md`. Screens that changed were reviewed from screenshots,
      including at phone width.
- [ ] No author text reaches logs, error reports or exception messages (counts, timings and kinds
      of error only).
- [ ] The assistant still never changes a story on its own: anything a model produces becomes a
      proposal the author reviews.
- [ ] It works without a model, and both paths are tested: by hand, and with the (scripted)
      assistant where there is one.
- [ ] `make check` passes. If the API changed: `tests/contract/openapi_snapshot.json` and
      `frontend/src/api/schema.ts` are regenerated. If a prompt changed: its version is raised and
      its snapshot updated (`uv run python -m tests.prompts.snapshots --update`).
- [ ] If the database changed: a new migration, whose downgrade never deletes an author's words.
- [ ] Status lines in `plan/` (the backlog and the phase's todo) say what is now built and what is
      not.
- [ ] No secrets, `.env` files, databases or manuscript text are in the commits.
